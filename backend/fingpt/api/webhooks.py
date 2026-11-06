"""Authenticated Telegram webhook ingress."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, Flask, current_app, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fingpt.persistence.models import BotConnection
from fingpt.services.runtime_secrets import resolve_environment_secret
from fingpt.services.telegram_ingress import (
    TelegramUpdateRejected,
    accept_callback_update,
    accept_text_update,
)

WEBHOOK_BLUEPRINT = Blueprint("webhooks", __name__, url_prefix="/api/webhooks/telegram")


def register_webhooks(app: Flask) -> None:
    """Register Telegram webhook ingress without user-session authentication."""
    app.register_blueprint(WEBHOOK_BLUEPRINT)


@WEBHOOK_BLUEPRINT.post("/<int:telegram_bot_id>")
def telegram_webhook(telegram_bot_id: int) -> tuple[Any, int] | Any:
    """Verify Telegram's webhook token and durably queue one text update."""
    engine = current_app.extensions["database_engine"]
    if engine is None:
        return _error("service_unavailable", "Dịch vụ webhook không khả dụng."), 503
    database = Session(engine, expire_on_commit=False)
    try:
        bot = database.scalar(
            select(BotConnection).where(
                BotConnection.telegram_bot_id == telegram_bot_id,
                BotConnection.is_enabled.is_(True),
            )
        )
        if bot is None:
            return _error("not_found", "Không tìm thấy điểm cuối webhook."), 404
        resolver: Callable[[str], str | None] = current_app.config.get(
            "TELEGRAM_WEBHOOK_SECRET_RESOLVER", resolve_environment_secret
        )
        expected = resolver(bot.webhook_secret_name) if bot.webhook_secret_name is not None else None
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not expected or not hmac.compare_digest(expected, provided):
            return _error("unauthorized", "Xác thực webhook thất bại."), 401
        request.max_content_length = 16_384
        if not request.is_json:
            return _error("json_required", "Gửi bản cập nhật Telegram dạng JSON."), 415
        update = request.get_json(silent=True)
        if not isinstance(update, dict):
            return _error("invalid_update", "Bản cập nhật Telegram không hợp lệ."), 400
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            receipt, replayed = _accept_callback(database, telegram_bot_id, update, callback)
            return jsonify(status="accepted", receiptId=str(receipt.id), replayed=replayed), 200 if replayed else 202
        message = update.get("message")
        if not isinstance(message, dict):
            return jsonify(status="ignored"), 200
        text = message.get("text")
        chat = message.get("chat")
        sender = message.get("from")
        message_id = message.get("message_id")
        if not isinstance(text, str) or not text.strip() or len(text) > 8_000:
            return _error("unsupported_message", "Chỉ chấp nhận tin nhắn văn bản tối đa 8.000 ký tự."), 422
        if not isinstance(sender, dict):
            return _error("invalid_update", "Thông tin người gửi Telegram không hợp lệ."), 400
        if not _is_private_chat(chat, sender):
            return _error("invalid_update", "Chỉ hỗ trợ trò chuyện riêng trên Telegram."), 400
        if not isinstance(chat, dict):
            return _error("invalid_update", "Thông tin cuộc trò chuyện Telegram không hợp lệ."), 400
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            return _error("invalid_update", "ID tin nhắn Telegram không hợp lệ."), 400
        receipt, replayed = accept_text_update(
            database,
            telegram_bot_id=telegram_bot_id,
            update=update,
            telegram_user_id=sender["id"],
            telegram_chat_id=chat["id"],
            telegram_message_id=message_id,
            text=text,
        )
        return jsonify(status="accepted", receiptId=str(receipt.id), replayed=replayed), 200 if replayed else 202
    except TelegramUpdateRejected:
        return _error("not_authorized", "Người dùng Telegram không có quyền truy cập."), 403
    except SQLAlchemyError:
        database.rollback()
        current_app.logger.warning("Telegram webhook could not persist the update")
        return _error("service_unavailable", "Dịch vụ webhook tạm thời không khả dụng."), 503
    finally:
        database.close()


def _error(code: str, message: str) -> Any:
    """Return a concise safe webhook error."""
    return jsonify(error={"code": code, "message": message})


def _accept_callback(
    database: Session,
    telegram_bot_id: int,
    update: dict[str, object],
    callback: dict[str, object],
) -> tuple[Any, bool]:
    """Validate Telegram callback metadata and enqueue its opaque action."""
    callback_id = callback.get("id")
    callback_data = callback.get("data")
    sender = callback.get("from")
    message = callback.get("message")
    if not isinstance(sender, dict) or not isinstance(message, dict):
        raise TelegramUpdateRejected("Telegram callback metadata is invalid.")
    chat = message.get("chat")
    message_id = message.get("message_id")
    message_timestamp = message.get("date")
    if not _is_private_chat(chat, sender):
        raise TelegramUpdateRejected("Only private Telegram chats are supported.")
    if not isinstance(chat, dict):
        raise TelegramUpdateRejected("Telegram callback chat is invalid.")
    if not isinstance(message_id, int) or isinstance(message_id, bool):
        raise TelegramUpdateRejected("Telegram callback message is invalid.")
    if not isinstance(message_timestamp, int) or isinstance(message_timestamp, bool) or message_timestamp <= 0:
        raise TelegramUpdateRejected("Telegram callback message time is invalid.")
    try:
        callback_message_at = datetime.fromtimestamp(message_timestamp, UTC)
    except (OSError, OverflowError, ValueError) as error:
        raise TelegramUpdateRejected("Telegram callback message time is invalid.") from error
    if not isinstance(callback_id, str) or not isinstance(callback_data, str):
        raise TelegramUpdateRejected("Telegram callback action is invalid.")
    return accept_callback_update(
        database,
        telegram_bot_id=telegram_bot_id,
        update=update,
        telegram_user_id=sender["id"],
        telegram_chat_id=chat["id"],
        telegram_message_id=message_id,
        callback_message_at=callback_message_at,
        callback_query_id=callback_id,
        callback_data=callback_data,
    )


def _is_private_chat(chat: object, sender: dict[str, object]) -> bool:
    """Require one-to-one chats so private finance previews never enter groups."""
    if not isinstance(chat, dict):
        return False
    chat_id = chat.get("id")
    user_id = sender.get("id")
    return (
        chat.get("type") == "private"
        and isinstance(chat_id, int)
        and not isinstance(chat_id, bool)
        and isinstance(user_id, int)
        and not isinstance(user_id, bool)
        and chat_id == user_id
    )
