"""Validate and durably accept authorized Telegram text updates."""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fingpt.persistence.models import BotConnection, Conversation, Evidence, OutboxMessage, WebhookReceipt


class TelegramUpdateRejected(ValueError):
    """Raised when an update is not an allowed text message for the bot owner."""


def accept_text_update(
    database: Session,
    *,
    telegram_bot_id: int,
    update: dict[str, object],
    telegram_user_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
    text: str,
) -> tuple[WebhookReceipt, bool]:
    """Store evidence and an outbox reference atomically, without raw text in events."""
    update_id = update.get("update_id")
    if not isinstance(update_id, int) or isinstance(update_id, bool) or update_id < 0:
        raise TelegramUpdateRejected("Telegram update ID is invalid.")
    bot = database.scalar(
        select(BotConnection).where(
            BotConnection.telegram_bot_id == telegram_bot_id,
            BotConnection.is_enabled.is_(True),
            BotConnection.allowed_telegram_user_id == telegram_user_id,
        )
    )
    if bot is None:
        raise TelegramUpdateRejected("Telegram update is not authorized.")
    existing = database.scalar(
        select(WebhookReceipt).where(
            WebhookReceipt.bot_connection_id == bot.id,
            WebhookReceipt.external_update_id == update_id,
        )
    )
    if existing is not None:
        return existing, True
    conversation = database.scalar(
        select(Conversation).where(
            Conversation.user_id == bot.user_id,
            Conversation.channel == "telegram",
            Conversation.external_conversation_id == str(telegram_chat_id),
        )
    )
    if conversation is None:
        conversation = Conversation(
            id=uuid4(),
            user_id=bot.user_id,
            channel="telegram",
            external_conversation_id=str(telegram_chat_id),
        )
        database.add(conversation)
        database.flush()
    content = text.encode("utf-8")
    evidence = Evidence(
        id=uuid4(),
        user_id=bot.user_id,
        source_type="text",
        source_text=text,
        sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
    )
    receipt = WebhookReceipt(id=uuid4(), bot_connection_id=bot.id, external_update_id=update_id, status="accepted")
    event_id = f"telegram:{telegram_bot_id}:{update_id}"
    outbox = OutboxMessage(
        id=uuid4(),
        deduplication_key=event_id,
        message_type="telegram.text.accepted",
        payload={
            "receiptId": str(receipt.id),
            "botConnectionId": str(bot.id),
            "userId": str(bot.user_id),
            "conversationId": str(conversation.id),
            "evidenceId": str(evidence.id),
            "telegramUserId": telegram_user_id,
            "telegramChatId": telegram_chat_id,
            "telegramMessageId": telegram_message_id,
        },
    )
    database.add_all([evidence, receipt, outbox])
    try:
        database.commit()
    except IntegrityError:
        database.rollback()
        concurrent_receipt = database.scalar(
            select(WebhookReceipt).where(
                WebhookReceipt.bot_connection_id == bot.id,
                WebhookReceipt.external_update_id == update_id,
            )
        )
        if concurrent_receipt is None:
            raise
        return concurrent_receipt, True
    return receipt, False


def accept_callback_update(
    database: Session,
    *,
    telegram_bot_id: int,
    update: dict[str, object],
    telegram_user_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
    callback_message_at: datetime,
    callback_query_id: str,
    callback_data: str,
) -> tuple[WebhookReceipt, bool]:
    """Queue an authorized Telegram button callback exactly once."""
    update_id = update.get("update_id")
    if not isinstance(update_id, int) or isinstance(update_id, bool) or update_id < 0:
        raise TelegramUpdateRejected("Telegram update ID is invalid.")
    if not callback_query_id or len(callback_query_id) > 128 or len(callback_data) > 64:
        raise TelegramUpdateRejected("Telegram callback data is invalid.")
    if callback_message_at.tzinfo is None or callback_message_at.utcoffset() is None:
        raise TelegramUpdateRejected("Telegram callback message time is invalid.")
    bot = database.scalar(
        select(BotConnection).where(
            BotConnection.telegram_bot_id == telegram_bot_id,
            BotConnection.is_enabled.is_(True),
            BotConnection.allowed_telegram_user_id == telegram_user_id,
        )
    )
    if bot is None:
        raise TelegramUpdateRejected("Telegram callback is not authorized.")
    existing = database.scalar(
        select(WebhookReceipt).where(
            WebhookReceipt.bot_connection_id == bot.id,
            WebhookReceipt.external_update_id == update_id,
        )
    )
    if existing is not None:
        return existing, True
    receipt = WebhookReceipt(
        id=uuid4(),
        bot_connection_id=bot.id,
        external_update_id=update_id,
        status="accepted",
        telegram_message_at=callback_message_at,
    )
    outbox = OutboxMessage(
        id=uuid4(),
        deduplication_key=f"telegram:{telegram_bot_id}:{update_id}",
        message_type="telegram.callback.accepted",
        payload={
            "receiptId": str(receipt.id),
            "botConnectionId": str(bot.id),
            "userId": str(bot.user_id),
            "telegramUserId": telegram_user_id,
            "telegramChatId": telegram_chat_id,
            "telegramMessageId": telegram_message_id,
            "callbackQueryId": callback_query_id,
            "callbackData": callback_data,
            "updateId": update_id,
        },
    )
    database.add_all([receipt, outbox])
    try:
        database.commit()
    except IntegrityError:
        database.rollback()
        concurrent_receipt = database.scalar(
            select(WebhookReceipt).where(
                WebhookReceipt.bot_connection_id == bot.id,
                WebhookReceipt.external_update_id == update_id,
            )
        )
        if concurrent_receipt is None:
            raise
        return concurrent_receipt, True
    return receipt, False
