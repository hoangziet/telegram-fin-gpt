"""Telegram preview adapter tests."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from fingpt.adapters.telegram import TelegramBotApi
from fingpt.persistence.models import DraftItem


def test_draft_preview_offers_only_safe_actions_for_each_pending_item() -> None:
    draft_id = uuid4()
    ready = DraftItem(
        id=uuid4(),
        item_index=0,
        version=3,
        status="ready",
        extracted_data={
            "description": "bánh mì",
            "amount": "15000",
            "currency": "VND",
            "transactionDate": "2026-11-02",
            "dayPeriod": "morning",
        },
    )
    incomplete = DraftItem(
        id=uuid4(),
        item_index=1,
        version=1,
        status="incomplete",
        extracted_data={
            "description": "phở",
            "amount": "50000",
            "currency": "VND",
            "transactionDate": "2026-11-02",
            "dayPeriod": None,
        },
    )
    client = TelegramBotApi(lambda name: "test-token" if name == "telegram/token" else None)

    with patch.object(TelegramBotApi, "_call") as send_message:
        client.send_draft_preview(
            secret_name="telegram/token",
            chat_id=12345,
            draft_id=draft_id,
            items=(ready, incomplete),
        )

    payload = send_message.call_args.args[1]
    rows = payload["reply_markup"]["inline_keyboard"]
    assert [button["text"] for button in rows[0]] == ["Xác nhận khoản 1", "Hủy khoản 1"]
    assert [button["text"] for button in rows[1]] == ["Hủy khoản 2"]
    assert all(len(button["callback_data"].encode("utf-8")) <= 64 for row in rows for button in row)
