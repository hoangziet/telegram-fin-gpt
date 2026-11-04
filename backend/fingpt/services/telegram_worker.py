"""Idempotent Telegram text-event processing for queue consumers."""

from __future__ import annotations

import re
from base64 import b64decode
from datetime import UTC, datetime, time, timedelta
from typing import TypeGuard
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from fingpt.persistence.models import BotConnection, Conversation, Evidence, OutboxMessage, WebhookReceipt
from fingpt.services.drafts import create_text_draft

EDIT_COMMAND = re.compile(
    r"^/sua\s+([0-9a-fA-F-]{36})\s+([1-9][0-9]{0,2})\s+(mo_ta|so_tien|ngay|buoi|gio|loai)\s+(.+)$"
)

TELEGRAM_DRAFT_NAMESPACE = UUID("8feaa03e-e3e8-4ebc-9996-3d588f9d7341")
CALLBACK_MAX_AGE = timedelta(hours=24)
ITEM_CALLBACK = re.compile(r"(cf|cx)\.([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{22})\.([0-9a-f]{8})")
CHANGE_CALLBACK = re.compile(r"(un|tc|tx)\.([A-Za-z0-9_-]{22})\.([0-9a-f]{8})")


class PermanentMessageError(ValueError):
    """Raised when a queued event cannot be repaired by delivery retries."""


def process_telegram_text_event(database: Session, payload: dict[str, object]) -> UUID | None:
    """Create one durable draft per accepted update and queue its preview notice."""
    receipt_id = _payload_uuid(payload, "receiptId")
    user_id = _payload_uuid(payload, "userId")
    conversation_id = _payload_uuid(payload, "conversationId")
    evidence_id = _payload_uuid(payload, "evidenceId")
    bot_id = _payload_uuid(payload, "botConnectionId")
    receipt = database.scalar(select(WebhookReceipt).where(WebhookReceipt.id == receipt_id))
    if receipt is None:
        raise PermanentMessageError("Webhook receipt does not exist.")
    evidence = database.scalar(
        select(Evidence).where(
            Evidence.id == evidence_id,
            Evidence.user_id == user_id,
            Evidence.source_type == "text",
        )
    )
    conversation = database.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.channel == "telegram",
        )
    )
    bot = database.scalar(
        select(BotConnection).where(
            BotConnection.id == bot_id,
            BotConnection.user_id == user_id,
            BotConnection.is_enabled.is_(True),
        )
    )
    if evidence is None or evidence.source_text is None or conversation is None or bot is None:
        raise PermanentMessageError("Telegram event references are invalid.")
    edit_match = EDIT_COMMAND.fullmatch(evidence.source_text.strip())
    if edit_match is not None or evidence.source_text.strip().startswith("/sua"):
        return _process_telegram_draft_edit(database, receipt, payload, user_id, bot_id, edit_match)
    accepted_at = receipt.received_at
    if accepted_at.tzinfo is None:
        accepted_at = accepted_at.replace(tzinfo=UTC)
    draft, _ = create_text_draft(
        database,
        user_id=user_id,
        text=evidence.source_text,
        idempotency_key=str(uuid5(TELEGRAM_DRAFT_NAMESPACE, f"{bot_id}:{receipt.external_update_id}")),
        conversation_id=conversation.id,
        intake_at=accepted_at,
        evidence_id=evidence.id,
        conversation_channel="telegram",
    )
    receipt.status = "processed"
    receipt.processed_at = datetime.now(UTC)
    preview_key = f"telegram-preview:{receipt.id}"
    if database.scalar(select(OutboxMessage.id).where(OutboxMessage.deduplication_key == preview_key)) is None:
        database.add(
            OutboxMessage(
                deduplication_key=preview_key,
                message_type="telegram.draft.ready",
                payload={
                    "botConnectionId": str(bot.id),
                    "telegramChatId": payload["telegramChatId"],
                    "draftId": str(draft.draft.id),
                    "userId": str(user_id),
                },
            )
        )
    database.commit()
    return draft.draft.id


def parse_telegram_edit_value(field: str, raw_value: str, currency: str) -> dict[str, object]:
    """Convert one explicit Telegram correction into domain-validated fields."""
    from fingpt.domain.money import Money
    from fingpt.domain.text_interpretation import TransactionKind
    from fingpt.domain.transaction_time import DayPeriod

    value = raw_value.strip()
    if not value or len(value) > 256:
        raise ValueError("Correction value must contain 1–256 characters.")
    if field == "mo_ta":
        return {"description": value}
    if field == "so_tien":
        return {"amount": Money.parse(value, currency)}
    if field == "ngay":
        return {"transactionDate": value}
    if field == "buoi":
        periods = {"sang": "morning", "trua": "noon", "chieu": "afternoon", "toi": "evening"}
        return {"dayPeriod": DayPeriod(periods.get(value.lower(), value.lower()))}
    if field == "gio":
        return {"localTime": time.fromisoformat(value)}
    if field == "loai":
        return {"kind": TransactionKind(value.lower())}
    raise ValueError("Unsupported Telegram edit field.")


def _process_telegram_draft_edit(
    database: Session,
    receipt: WebhookReceipt,
    payload: dict[str, object],
    user_id: UUID,
    bot_id: UUID,
    match: re.Match[str] | None,
) -> UUID | None:
    """Correct one owned pending item and queue its refreshed preview."""
    from fingpt.persistence.models import Draft, DraftItem
    from fingpt.services.drafts import DraftConfirmationConflict, update_draft_item

    if receipt.status == "processed":
        return None
    if match is None:
        _queue_edit_status(database, receipt, payload, "Cú pháp: /sua <draft-id> <khoản> <trường> <giá trị>")
        return None
    try:
        draft_id = UUID(match[1])
        item_number = int(match[2])
        draft = database.scalar(
            select(Draft)
            .join(Conversation, Conversation.id == Draft.conversation_id)
            .where(
                Draft.id == draft_id,
                Draft.user_id == user_id,
                Conversation.user_id == user_id,
                Conversation.channel == "telegram",
                Conversation.external_conversation_id == str(payload.get("telegramChatId")),
            )
        )
        item = database.scalar(
            select(DraftItem).where(
                DraftItem.draft_id == draft_id,
                DraftItem.user_id == user_id,
                DraftItem.item_index == item_number - 1,
            )
        )
        if draft is None or item is None:
            _queue_edit_status(
                database,
                receipt,
                payload,
                "Không tìm thấy khoản cần sửa trong cuộc trò chuyện này.",
            )
            return None
        changes = parse_telegram_edit_value(match[3], match[4], str(item.extracted_data.get("currency") or "VND"))
        update_draft_item(
            database,
            user_id=user_id,
            draft_id=draft_id,
            item_id=item.id,
            expected_version=item.version,
            changes=changes,
            commit=False,
        )
    except DraftConfirmationConflict:
        status = "Khoản đã được xử lý. Gửi lại nội dung để xem bản mới."
        _queue_edit_status(database, receipt, payload, status)
        return None
    except (ValueError, LookupError):
        _queue_edit_status(
            database,
            receipt,
            payload,
            "Giá trị sửa không hợp lệ; giao dịch chưa được ghi.",
        )
        return None

    event_key = f"telegram-draft-edit-preview:{receipt.id}"
    if database.scalar(select(OutboxMessage.id).where(OutboxMessage.deduplication_key == event_key)) is None:
        database.add(
            OutboxMessage(
                deduplication_key=event_key,
                message_type="telegram.draft.ready",
                payload={
                    "botConnectionId": str(bot_id),
                    "telegramChatId": payload["telegramChatId"],
                    "draftId": str(draft_id),
                    "userId": str(user_id),
                },
            )
        )
    receipt.status = "processed"
    receipt.processed_at = datetime.now(UTC)
    database.commit()
    return draft_id


def _queue_edit_status(database: Session, receipt: WebhookReceipt, payload: dict[str, object], message: str) -> None:
    """Queue a safe correction status and consume the command receipt."""
    bot_id = _payload_uuid(payload, "botConnectionId")
    chat_id = payload.get("telegramChatId")
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        raise PermanentMessageError("Telegram correction destination is invalid.")
    event_key = f"telegram-draft-edit-status:{receipt.id}"
    if database.scalar(select(OutboxMessage.id).where(OutboxMessage.deduplication_key == event_key)) is None:
        database.add(
            OutboxMessage(
                deduplication_key=event_key,
                message_type="telegram.message.send",
                payload={"botConnectionId": str(bot_id), "telegramChatId": chat_id, "text": message},
            )
        )
    receipt.status = "processed"
    receipt.processed_at = datetime.now(UTC)
    database.commit()


def _payload_uuid(payload: dict[str, object], key: str) -> UUID:
    """Read a required UUID reference from an untrusted queue message."""
    value = payload.get(key)
    if not isinstance(value, str):
        raise PermanentMessageError("Telegram event reference is invalid.")
    try:
        return UUID(value)
    except ValueError as error:
        raise PermanentMessageError("Telegram event reference is invalid.") from error


def process_telegram_callback(database: Session, payload: dict[str, object]) -> str:
    """Confirm a ready draft item from one authorized inline-button callback."""
    receipt_id = _payload_uuid(payload, "receiptId")
    bot_id = _payload_uuid(payload, "botConnectionId")
    user_id = _payload_uuid(payload, "userId")
    receipt = database.scalar(select(WebhookReceipt).where(WebhookReceipt.id == receipt_id))
    bot = database.scalar(
        select(BotConnection).where(
            BotConnection.id == bot_id,
            BotConnection.user_id == user_id,
            BotConnection.is_enabled.is_(True),
        )
    )
    if receipt is None or bot is None or receipt.bot_connection_id != bot.id:
        raise PermanentMessageError("Telegram callback is not authorized.")
    telegram_user_id = payload.get("telegramUserId")
    telegram_chat_id = payload.get("telegramChatId")
    if (
        not isinstance(telegram_user_id, int)
        or isinstance(telegram_user_id, bool)
        or telegram_user_id != bot.allowed_telegram_user_id
        or not isinstance(telegram_chat_id, int)
        or isinstance(telegram_chat_id, bool)
        or telegram_chat_id != telegram_user_id
    ):
        raise PermanentMessageError("Telegram callback is not authorized.")
    callback_data = payload.get("callbackData")
    if not isinstance(callback_data, str) or len(callback_data) > 64:
        raise PermanentMessageError("Telegram callback data is invalid.")
    item_match = ITEM_CALLBACK.fullmatch(callback_data)
    change_match = CHANGE_CALLBACK.fullmatch(callback_data)
    if item_match is None and change_match is None:
        raise PermanentMessageError("Telegram callback action is unsupported.")
    try:
        if item_match is not None:
            resource_id = UUID(bytes=b64decode(item_match[2] + "==", altchars=b"-_", validate=True))
            item_id = UUID(bytes=b64decode(item_match[3] + "==", altchars=b"-_", validate=True))
            expected_version = int(item_match[4], 16)
        else:
            assert change_match is not None
            resource_id = UUID(bytes=b64decode(change_match[2] + "==", altchars=b"-_", validate=True))
            item_id = None
            expected_version = int(change_match[3], 16)
    except (ValueError, TypeError) as error:
        raise PermanentMessageError("Telegram callback action is invalid.") from error
    update_id = payload.get("updateId")
    callback_query_id = payload.get("callbackQueryId")
    if (
        not isinstance(update_id, int)
        or isinstance(update_id, bool)
        or update_id < 0
        or receipt.external_update_id != update_id
        or not isinstance(callback_query_id, str)
        or not callback_query_id
        or len(callback_query_id) > 128
    ):
        raise PermanentMessageError("Telegram callback reference is invalid.")
    reply_markup = None
    change_preview_id = None
    if item_match is not None and item_id is not None:
        draft_id = resource_id
        if item_match[1] == "cf":
            status_text, reply_markup = _confirm_callback(
                database, receipt, payload, draft_id, item_id, expected_version, update_id
            )
        else:
            status_text = _cancel_callback(database, receipt, payload, draft_id, item_id, expected_version)
    elif change_match is not None and change_match[1] == "un":
        status_text, change_preview_id = _propose_undo_callback(
            database, receipt, payload, resource_id, expected_version, update_id
        )
    elif change_match is not None and change_match[1] == "tc":
        status_text, reply_markup = _confirm_change_callback(
            database, receipt, payload, resource_id, expected_version, update_id
        )
    elif change_match is not None:
        status_text = _cancel_change_callback(database, receipt, payload, resource_id, expected_version)
    else:
        raise PermanentMessageError("Telegram callback action is unsupported.")
    receipt.status = "processed"
    receipt.processed_at = datetime.now(UTC)
    reply_key = (
        f"telegram-undo-preview:{bot.id}:{update_id}" if change_preview_id else f"telegram-callback-reply:{receipt.id}"
    )
    if database.scalar(select(OutboxMessage.id).where(OutboxMessage.deduplication_key == reply_key)) is None:
        message_payload: dict[str, object] = {
            "botConnectionId": str(bot.id),
            "telegramChatId": telegram_chat_id,
            "text": status_text,
        }
        message_type = "telegram.message.send"
        if reply_markup is not None:
            message_payload["replyMarkup"] = reply_markup
        if change_preview_id is not None:
            message_type = "telegram.change.preview"
            message_payload["changeId"] = str(change_preview_id)
            message_payload["changeVersion"] = expected_version
            message_payload["userId"] = str(user_id)
        database.add(
            OutboxMessage(
                deduplication_key=reply_key,
                message_type=message_type,
                payload=message_payload,
            )
        )
    database.commit()
    return status_text


def _confirm_callback(
    database: Session,
    receipt: WebhookReceipt,
    payload: dict[str, object],
    draft_id: UUID,
    item_id: UUID,
    expected_version: int,
    update_id: int,
) -> tuple[str, dict[str, object] | None]:
    """Reject expired previews before transaction confirmation."""
    if _callback_is_expired(receipt):
        return "Nút xem trước đã hết hạn. Hãy gửi lại nội dung để xem bản mới.", None

    from fingpt.services.drafts import DraftConfirmationConflict, DraftItemNotReady, confirm_draft_item

    try:
        transaction, _ = confirm_draft_item(
            database,
            user_id=_payload_uuid(payload, "userId"),
            draft_id=draft_id,
            item_id=item_id,
            expected_version=expected_version,
            idempotency_key=str(uuid5(TELEGRAM_DRAFT_NAMESPACE, f"callback:{payload['botConnectionId']}:{update_id}")),
        )
    except DraftItemNotReady:
        return "Khoản này còn thiếu thông tin nên chưa được ghi.", None
    except DraftConfirmationConflict:
        return (
            "Bản xem trước đã thay đổi hoặc được xử lý. Hãy gửi lại để xem bản mới.",
            None,
        )
    from fingpt.adapters.telegram import _undo_callback

    return "Đã xác nhận và ghi giao dịch.", {
        "inline_keyboard": [
            [{"text": "Hoàn tác", "callback_data": _undo_callback(transaction.id, transaction.version)}]
        ]
    }


def _cancel_callback(
    database: Session,
    receipt: WebhookReceipt,
    payload: dict[str, object],
    draft_id: UUID,
    item_id: UUID,
    expected_version: int,
) -> str:
    """Cancel only the selected pending item while retaining sibling proposals."""
    if _callback_is_expired(receipt):
        return "Nút xem trước đã hết hạn. Hãy gửi lại nội dung để xem bản mới."

    from fingpt.services.drafts import DraftConfirmationConflict, cancel_draft_item

    try:
        cancel_draft_item(
            database,
            user_id=_payload_uuid(payload, "userId"),
            draft_id=draft_id,
            item_id=item_id,
            expected_version=expected_version,
        )
    except (DraftConfirmationConflict, LookupError):
        status = "Khoản này đã thay đổi hoặc được xử lý."
        status += " Hãy gửi lại nội dung để xem bản mới."
        return status
    return "Khoản đã được hủy. Không có giao dịch nào được ghi."


def _propose_undo_callback(
    database: Session,
    receipt: WebhookReceipt,
    payload: dict[str, object],
    transaction_id: UUID,
    expected_version: int,
    update_id: int,
) -> tuple[str, UUID | None]:
    """Create a durable undo preview without mutating the confirmed transaction."""
    if _callback_is_expired(receipt):
        return "Nút hoàn tác đã hết hạn. Hãy mở giao dịch trên FinGPT để xem lại.", None
    from fingpt.services.drafts import DraftConfirmationConflict
    from fingpt.services.transaction_changes import create_change_proposal

    user_id = _payload_uuid(payload, "userId")
    bot_id = _payload_uuid(payload, "botConnectionId")
    try:
        change, _ = create_change_proposal(
            database,
            user_id=user_id,
            transaction_id=transaction_id,
            expected_version=expected_version,
            action="restored",
            changes={},
            idempotency_key=str(uuid5(TELEGRAM_DRAFT_NAMESPACE, f"undo:{bot_id}:{update_id}")),
        )
    except (DraftConfirmationConflict, LookupError):
        return "Giao dịch đã thay đổi. Hãy mở giao dịch trên FinGPT để xem trạng thái mới.", None
    return "Đây là bản xem trước hoàn tác. Chưa có thay đổi nào được ghi.", change.id


def _confirm_change_callback(
    database: Session,
    receipt: WebhookReceipt,
    payload: dict[str, object],
    change_id: UUID,
    expected_version: int,
    update_id: int,
) -> tuple[str, dict[str, object] | None]:
    """Apply an explicitly confirmed undo proposal with a stable retry key."""
    if _callback_is_expired(receipt):
        return "Nút xác nhận hoàn tác đã hết hạn. Giao dịch chưa được cập nhật.", None
    from fingpt.services.drafts import DraftConfirmationConflict
    from fingpt.services.transaction_changes import confirm_change

    try:
        transaction, _, _ = confirm_change(
            database,
            user_id=_payload_uuid(payload, "userId"),
            change_id=change_id,
            expected_version=expected_version,
            confirmation_key=str(
                uuid5(
                    TELEGRAM_DRAFT_NAMESPACE,
                    f"undo-confirm:{payload['botConnectionId']}:{update_id}",
                )
            ),
        )
    except (DraftConfirmationConflict, LookupError):
        return "Bản xem trước đã thay đổi hoặc hết hạn. Giao dịch chưa được cập nhật.", None
    from fingpt.adapters.telegram import _undo_callback

    action_label = "Khôi phục" if transaction.is_deleted else "Hoàn tác"
    return "Đã hoàn tác giao dịch.", {
        "inline_keyboard": [
            [{"text": action_label, "callback_data": _undo_callback(transaction.id, transaction.version)}]
        ]
    }


def _cancel_change_callback(
    database: Session,
    receipt: WebhookReceipt,
    payload: dict[str, object],
    change_id: UUID,
    expected_version: int,
) -> str:
    """Cancel a Telegram undo proposal while leaving the transaction unchanged."""
    if _callback_is_expired(receipt):
        return "Nút hủy đã hết hạn. Giao dịch chưa được cập nhật."
    from fingpt.services.drafts import DraftConfirmationConflict
    from fingpt.services.transaction_changes import cancel_change

    try:
        cancel_change(
            database,
            user_id=_payload_uuid(payload, "userId"),
            change_id=change_id,
            expected_version=expected_version,
        )
    except (DraftConfirmationConflict, LookupError):
        return "Bản xem trước đã thay đổi hoặc hết hạn. Giao dịch chưa được cập nhật."
    return "Đã hủy bản xem trước. Giao dịch chưa được cập nhật."


def _callback_is_expired(receipt: WebhookReceipt) -> bool:
    """Expire buttons from the original Telegram message, including queued callbacks."""
    message_at = receipt.telegram_message_at or receipt.received_at
    if message_at.tzinfo is None:
        message_at = message_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - message_at > CALLBACK_MAX_AGE


def _is_supported_reply_markup(value: object) -> TypeGuard[dict[str, object]]:
    """Allow only our versioned callback buttons from untrusted queue payloads."""
    if not isinstance(value, dict) or set(value) != {"inline_keyboard"}:
        return False
    rows = value["inline_keyboard"]
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        return False
    for row in rows:
        if not isinstance(row, list) or not row or len(row) > 8:
            return False
        for button in row:
            if not isinstance(button, dict) or set(button) != {"text", "callback_data"}:
                return False
            label = button["text"]
            data = button["callback_data"]
            if not isinstance(label, str) or not label or len(label) > 64:
                return False
            if not isinstance(data, str) or len(data) > 64:
                return False
            if ITEM_CALLBACK.fullmatch(data) is None and CHANGE_CALLBACK.fullmatch(data) is None:
                return False
    return True


def deliver_telegram_outbox_event(database: Session, payload: dict[str, object]) -> None:
    """Send a persisted preview or status reply using the configured bot secret."""
    from fingpt.adapters.telegram import TelegramBotApi
    from fingpt.persistence.models import Draft, Transaction
    from fingpt.services.drafts import get_draft_snapshot
    from fingpt.services.runtime_secrets import resolve_environment_secret
    from fingpt.services.transaction_changes import get_change

    bot_id = _payload_uuid(payload, "botConnectionId")
    bot = database.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.is_enabled.is_(True)))
    chat_id = payload.get("telegramChatId")
    if (
        bot is None
        or bot.allowed_telegram_user_id is None
        or not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
        or chat_id != bot.allowed_telegram_user_id
    ):
        raise PermanentMessageError("Telegram delivery references are invalid.")
    client = TelegramBotApi(resolve_environment_secret)
    if "draftId" in payload:
        draft_id = _payload_uuid(payload, "draftId")
        owner_id = _payload_uuid(payload, "userId")
        draft_row = database.scalar(select(Draft).where(Draft.id == draft_id, Draft.user_id == owner_id))
        snapshot = get_draft_snapshot(database, owner_id, draft_id)
        if draft_row is None or snapshot is None or bot.user_id != owner_id:
            raise PermanentMessageError("Telegram draft preview is not available.")
        client.send_draft_preview(
            secret_name=bot.secret_name,
            chat_id=chat_id,
            draft_id=draft_id,
            items=snapshot.items,
        )
        return
    if "changeId" in payload:
        change_id = _payload_uuid(payload, "changeId")
        owner_id = _payload_uuid(payload, "userId")
        version = payload.get("changeVersion")
        change = get_change(database, owner_id, change_id)
        transaction = None
        if change is not None:
            transaction = database.scalar(
                select(Transaction).where(
                    Transaction.id == change.transaction_id,
                    Transaction.user_id == owner_id,
                )
            )
        if (
            bot.user_id != owner_id
            or not isinstance(version, int)
            or isinstance(version, bool)
            or change is None
            or change.status != "proposed"
            or change.version != version
            or transaction is None
            or transaction.version != change.expected_transaction_version
        ):
            client.send_text(
                secret_name=bot.secret_name,
                chat_id=chat_id,
                text="Bản xem trước đã thay đổi hoặc hết hạn. Hãy mở lại FinGPT.",
            )
            return
        client.send_change_preview(
            secret_name=bot.secret_name, chat_id=chat_id, transaction=transaction, change=change
        )
        return
    text = payload.get("text")
    if not isinstance(text, str):
        raise PermanentMessageError("Telegram status message is invalid.")
    reply_markup = payload.get("replyMarkup")
    if reply_markup is not None:
        if not _is_supported_reply_markup(reply_markup):
            raise PermanentMessageError("Telegram reply keyboard is invalid.")
    client.send_message(secret_name=bot.secret_name, chat_id=chat_id, text=text, reply_markup=reply_markup)
