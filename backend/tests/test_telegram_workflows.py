"""Telegram workflow integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def test_telegram_cancel_callback_cancels_only_the_selected_draft_item(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    from fingpt.adapters.telegram import _cancellation_callback
    from fingpt.persistence.models import BotConnection, DraftItem, OutboxMessage, Transaction, WebhookReceipt
    from fingpt.services.telegram_worker import process_telegram_callback, process_telegram_text_event

    app, _, owner_id = draft_app
    app.config["TELEGRAM_WEBHOOK_SECRET_RESOLVER"] = lambda name: (
        "secret-for-tests" if name == "telegram/webhook" else None
    )
    with Session(app.extensions["database_engine"]) as database:
        database.add(
            BotConnection(
                user_id=owner_id,
                telegram_bot_id=887766556,
                secret_name="telegram/token",
                webhook_secret_name="telegram/webhook",
                allowed_telegram_user_id=345678903,
                is_enabled=True,
            )
        )
        database.commit()
    webhook = app.test_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "secret-for-tests"}
    inbound = {
        "update_id": 8021,
        "message": {
            "message_id": 1,
            "from": {"id": 345678903},
            "chat": {"id": 345678903, "type": "private"},
            "text": "Hôm qua 8h sáng ăn bánh mì 15k, xem phim 100k",
        },
    }
    assert webhook.post("/api/webhooks/telegram/887766556", json=inbound, headers=headers).status_code == 202
    with Session(app.extensions["database_engine"]) as database:
        event = database.scalar(select(OutboxMessage).where(OutboxMessage.message_type == "telegram.text.accepted"))
        assert event is not None
        draft_id = process_telegram_text_event(database, event.payload)
        assert draft_id is not None
        items = list(
            database.scalars(select(DraftItem).where(DraftItem.draft_id == draft_id).order_by(DraftItem.item_index))
        )
        assert len(items) == 2
        callback_data = _cancellation_callback(draft_id, items[0].id, items[0].version)
    callback = {
        "update_id": 8022,
        "callback_query": {
            "id": "callback-8022",
            "from": {"id": 345678903},
            "message": {
                "message_id": 2,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": 345678903, "type": "private"},
            },
            "data": callback_data,
        },
    }
    assert webhook.post("/api/webhooks/telegram/887766556", json=callback, headers=headers).status_code == 202
    with Session(app.extensions["database_engine"]) as database:
        event = database.scalar(
            select(OutboxMessage).where(OutboxMessage.message_type == "telegram.callback.accepted")
        )
        assert event is not None
        status = process_telegram_callback(database, event.payload)
        assert process_telegram_callback(database, event.payload) == status
        assert status == "Khoản đã được hủy. Không có giao dịch nào được ghi."
        receipt = database.scalar(select(WebhookReceipt).where(WebhookReceipt.external_update_id == 8022))
        assert receipt is not None and receipt.status == "processed"
        items = list(
            database.scalars(select(DraftItem).where(DraftItem.draft_id == draft_id).order_by(DraftItem.item_index))
        )
        assert [item.status for item in items] == ["cancelled", "ready"]
        assert database.scalar(select(func.count()).select_from(Transaction)) == 0
        assert (
            database.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .where(OutboxMessage.message_type == "telegram.message.send")
            )
            == 1
        )


def test_telegram_edit_command_updates_one_preview_without_writing_a_transaction(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    from fingpt.persistence.models import BotConnection, Draft, DraftItem, OutboxMessage, Transaction
    from fingpt.services.telegram_worker import process_telegram_text_event

    app, _, owner_id = draft_app
    app.config["TELEGRAM_WEBHOOK_SECRET_RESOLVER"] = lambda name: (
        "secret-for-tests" if name == "telegram/webhook" else None
    )
    bot_id = 887766559
    telegram_user_id = 345678906
    with Session(app.extensions["database_engine"]) as database:
        database.add(
            BotConnection(
                user_id=owner_id,
                telegram_bot_id=bot_id,
                secret_name="telegram/token",
                webhook_secret_name="telegram/webhook",
                allowed_telegram_user_id=telegram_user_id,
                is_enabled=True,
            )
        )
        database.commit()
    webhook = app.test_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "secret-for-tests"}

    def submit(update_id: int, message_text: str) -> None:
        response = webhook.post(
            f"/api/webhooks/telegram/{bot_id}",
            json={
                "update_id": update_id,
                "message": {
                    "message_id": update_id,
                    "from": {"id": telegram_user_id},
                    "chat": {"id": telegram_user_id, "type": "private"},
                    "text": message_text,
                },
            },
            headers=headers,
        )
        assert response.status_code == 202

    submit(8041, "Sáng hôm qua 8h ăn bánh mì 15k")
    with Session(app.extensions["database_engine"]) as database:
        accepted = database.scalar(
            select(OutboxMessage).where(OutboxMessage.deduplication_key == f"telegram:{bot_id}:8041")
        )
        assert accepted is not None
        draft_id = process_telegram_text_event(database, accepted.payload)
        assert draft_id is not None

    submit(8042, f"/sua {draft_id} 1 so_tien 200")
    with Session(app.extensions["database_engine"]) as database:
        accepted = database.scalar(
            select(OutboxMessage).where(OutboxMessage.deduplication_key == f"telegram:{bot_id}:8042")
        )
        assert accepted is not None
        assert process_telegram_text_event(database, accepted.payload) == draft_id
        assert process_telegram_text_event(database, accepted.payload) is None
        item = database.scalar(select(DraftItem).where(DraftItem.draft_id == draft_id))
        assert item is not None and item.extracted_data["amount"] == "200"
        assert item.version == 2 and item.status == "ready"
        assert database.scalar(select(func.count()).select_from(Draft)) == 1
        assert database.scalar(select(func.count()).select_from(Transaction)) == 0
        receipt_id = accepted.payload["receiptId"]
        preview = database.scalar(
            select(OutboxMessage).where(OutboxMessage.deduplication_key == f"telegram-draft-edit-preview:{receipt_id}")
        )
        assert preview is not None and preview.message_type == "telegram.draft.ready"


def test_telegram_undo_requires_a_preview_and_explicit_confirmation(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    from unittest.mock import patch

    from fingpt.adapters.telegram import TelegramBotApi, _confirmation_callback
    from fingpt.persistence.models import BotConnection, OutboxMessage, Transaction, TransactionChange, WebhookReceipt
    from fingpt.services.telegram_worker import (
        process_telegram_callback,
        process_telegram_text_event,
    )
    from fingpt.worker import _process_message

    app, _, owner_id = draft_app
    app.config["TELEGRAM_WEBHOOK_SECRET_RESOLVER"] = lambda name: (
        "secret-for-tests" if name == "telegram/webhook" else None
    )
    bot_id = 887766557
    telegram_user_id = 345678904
    with Session(app.extensions["database_engine"]) as database:
        database.add(
            BotConnection(
                user_id=owner_id,
                telegram_bot_id=bot_id,
                secret_name="telegram/token",
                webhook_secret_name="telegram/webhook",
                allowed_telegram_user_id=telegram_user_id,
                is_enabled=True,
            )
        )
        database.commit()
    webhook = app.test_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "secret-for-tests"}
    inbound = {
        "update_id": 8031,
        "message": {
            "message_id": 1,
            "from": {"id": telegram_user_id},
            "chat": {"id": telegram_user_id, "type": "private"},
            "text": "Hôm qua 8h sáng ăn bánh mì 15k",
        },
    }
    assert webhook.post(f"/api/webhooks/telegram/{bot_id}", json=inbound, headers=headers).status_code == 202
    with Session(app.extensions["database_engine"]) as database:
        accepted = database.scalar(
            select(OutboxMessage).where(OutboxMessage.deduplication_key == f"telegram:{bot_id}:8031")
        )
        assert accepted is not None
        draft_id = process_telegram_text_event(database, accepted.payload)
        assert draft_id is not None
        from fingpt.persistence.models import DraftItem

        item = database.scalar(select(DraftItem).where(DraftItem.draft_id == draft_id))
        assert item is not None
        confirm_data = _confirmation_callback(draft_id, item.id, item.version)

    def process_callback(update_id: int, callback_data: str) -> str:
        update = {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "from": {"id": telegram_user_id},
                "message": {
                    "message_id": update_id,
                    "date": int(datetime.now(UTC).timestamp()),
                    "chat": {"id": telegram_user_id, "type": "private"},
                },
                "data": callback_data,
            },
        }
        response = webhook.post(f"/api/webhooks/telegram/{bot_id}", json=update, headers=headers)
        assert response.status_code == 202
        with Session(app.extensions["database_engine"]) as database:
            event = database.scalar(
                select(OutboxMessage).where(OutboxMessage.deduplication_key == f"telegram:{bot_id}:{update_id}")
            )
            assert event is not None
            return process_telegram_callback(database, event.payload)

    assert process_callback(8032, confirm_data) == "Đã xác nhận và ghi giao dịch."
    with Session(app.extensions["database_engine"]) as database:
        transaction = database.scalar(select(Transaction))
        assert transaction is not None
        callback_receipt = database.scalar(select(WebhookReceipt).where(WebhookReceipt.external_update_id == 8032))
        assert callback_receipt is not None
        undo_reply = database.scalar(
            select(OutboxMessage).where(
                OutboxMessage.deduplication_key == f"telegram-callback-reply:{callback_receipt.id}"
            )
        )
        assert undo_reply is not None
        undo_data = undo_reply.payload["replyMarkup"]["inline_keyboard"][0][0]["callback_data"]
        assert undo_data.startswith("un.")
        assert transaction.version == 1 and transaction.is_deleted is False

    assert process_callback(8033, undo_data).startswith("Đây là bản xem trước hoàn tác")
    with Session(app.extensions["database_engine"]) as database:
        transaction = database.scalar(select(Transaction))
        preview = database.scalar(select(OutboxMessage).where(OutboxMessage.message_type == "telegram.change.preview"))
        change = database.scalar(select(TransactionChange))
        assert transaction is not None and transaction.version == 1 and transaction.is_deleted is False
        assert preview is not None and change is not None and change.status == "proposed"
        assert preview.message_type == "telegram.change.preview"
        with (
            patch.object(TelegramBotApi, "_call") as send_message,
            patch("fingpt.services.runtime_secrets.resolve_environment_secret", return_value="test-token"),
        ):
            _process_message(app.extensions["database_engine"], "telegram.change.preview", preview.payload)
        telegram_preview = send_message.call_args.args[1]
        assert "Hiện tại:" in telegram_preview["text"]
        assert "Sau khi xác nhận:" in telegram_preview["text"]
        confirm_data = telegram_preview["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        cancel_data = telegram_preview["reply_markup"]["inline_keyboard"][0][1]["callback_data"]
        assert confirm_data.startswith("tc.")
        assert cancel_data.startswith("tx.")

    assert process_callback(8034, cancel_data) == "Đã hủy bản xem trước. Giao dịch chưa được cập nhật."
    with Session(app.extensions["database_engine"]) as database:
        transaction = database.scalar(select(Transaction))
        change = database.scalar(select(TransactionChange))
        assert transaction is not None and transaction.version == 1 and transaction.is_deleted is False
        assert change is not None and change.status == "cancelled"

    assert process_callback(8035, undo_data).startswith("Đây là bản xem trước hoàn tác")
    with Session(app.extensions["database_engine"]) as database:
        bot_connection_id = database.scalar(select(BotConnection.id).where(BotConnection.telegram_bot_id == bot_id))
        assert bot_connection_id is not None
        preview = database.scalar(
            select(OutboxMessage).where(
                OutboxMessage.message_type == "telegram.change.preview",
                OutboxMessage.deduplication_key == f"telegram-undo-preview:{bot_connection_id}:8035",
            )
        )
        assert preview is not None
        with (
            patch.object(TelegramBotApi, "_call") as send_message,
            patch("fingpt.services.runtime_secrets.resolve_environment_secret", return_value="test-token"),
        ):
            _process_message(app.extensions["database_engine"], "telegram.change.preview", preview.payload)
        confirm_data = send_message.call_args.args[1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    assert process_callback(8036, confirm_data) == "Đã hoàn tác giao dịch."
    with Session(app.extensions["database_engine"]) as database:
        transaction = database.scalar(select(Transaction))
        change = database.scalar(select(TransactionChange).where(TransactionChange.status == "confirmed"))
        assert transaction is not None and transaction.version == 2 and transaction.is_deleted is True
        assert change is not None
        receipt = database.scalar(select(WebhookReceipt).where(WebhookReceipt.external_update_id == 8036))
        assert receipt is not None
        follow_up = database.scalar(
            select(OutboxMessage).where(OutboxMessage.deduplication_key == f"telegram-callback-reply:{receipt.id}")
        )
        assert follow_up is not None
        button = follow_up.payload["replyMarkup"]["inline_keyboard"][0][0]
        assert button["text"] == "Khôi phục" and button["callback_data"].startswith("un.")


def test_telegram_delivery_rejects_unapproved_inline_keyboard_shapes(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    from unittest.mock import patch

    from fingpt.adapters.telegram import TelegramBotApi
    from fingpt.persistence.models import BotConnection
    from fingpt.services.telegram_worker import PermanentMessageError, deliver_telegram_outbox_event

    app, _, owner_id = draft_app
    with Session(app.extensions["database_engine"]) as database:
        bot = BotConnection(
            user_id=owner_id,
            telegram_bot_id=887766558,
            secret_name="telegram/token",
            webhook_secret_name="telegram/webhook",
            allowed_telegram_user_id=345678905,
            is_enabled=True,
        )
        database.add(bot)
        database.commit()
        payload = {
            "botConnectionId": str(bot.id),
            "telegramChatId": 345678905,
            "text": "status",
            "replyMarkup": {"inline_keyboard": [[{"text": "unsafe link", "url": "https://example.test"}]]},
        }
        with (
            patch.object(TelegramBotApi, "_call") as send_message,
            patch("fingpt.services.runtime_secrets.resolve_environment_secret", return_value="test-token"),
            pytest.raises(PermanentMessageError),
        ):
            deliver_telegram_outbox_event(database, payload)
        send_message.assert_not_called()
