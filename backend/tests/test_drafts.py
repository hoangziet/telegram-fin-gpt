"""Draft API tests for private text evidence and retry-safe previews."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fingpt.persistence.models import Draft, DraftItem, Evidence, Transaction, User

TEST_PASSWORD = "Strong draft test password! 2026"


def test_create_text_draft_previews_independent_items_and_persists_evidence(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    _, client, owner_id = draft_app
    idempotency_key = str(uuid4())

    response = client.post(
        "/api/drafts",
        json={"text": "Hôm qua ăn phở 50k, xem phim 100k"},
        headers={"Idempotency-Key": idempotency_key},
    )

    assert response.status_code == 201
    assert response.json is not None
    draft_payload = response.json["draft"]
    assert draft_payload["status"] == "open"
    assert len(draft_payload["items"]) == 2
    assert [item["description"] for item in draft_payload["items"]] == ["phở", "xem phim"]
    assert [item["amount"] for item in draft_payload["items"]] == [
        {"amount": "50000", "currency": "VND"},
        {"amount": "100000", "currency": "VND"},
    ]
    assert all(item["status"] == "incomplete" for item in draft_payload["items"])
    assert all(item["missingFields"] == ["day_period", "local_time"] for item in draft_payload["items"])

    with Session(draft_app[0].extensions["database_engine"]) as database:
        evidence = database.scalar(select(Evidence))
        assert evidence is not None
        assert evidence.user_id == owner_id
        assert evidence.source_text == "Hôm qua ăn phở 50k, xem phim 100k"
        assert database.scalar(select(func.count()).select_from(Transaction)) == 0


def test_transaction_evidence_is_reopenable_only_by_its_owner(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, _ = draft_app
    draft = client.post(
        "/api/drafts",
        json={"text": "2026-11-02 8h sáng ăn phở 50k"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert draft.status_code == 201
    assert draft.json is not None
    draft_data = draft.json["draft"]
    item = draft_data["items"][0]
    confirmation = client.post(
        f"/api/drafts/{draft_data['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": item["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmation.status_code == 201
    assert confirmation.json is not None
    transaction_id = confirmation.json["transaction"]["id"]

    evidence = client.get(f"/api/transactions/{transaction_id}/evidence")
    assert evidence.status_code == 200
    assert evidence.json is not None
    assert evidence.json["evidence"]["sourceText"] == "2026-11-02 8h sáng ăn phở 50k"

    with Session(app.extensions["database_engine"]) as database:
        hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
        database.add(User(username="another-owner", password_hash=hasher.hash(TEST_PASSWORD)))
        database.commit()
    assert client.post("/api/auth/logout").status_code == 204
    other_login = client.post(
        "/api/auth/login",
        json={"username": "another-owner", "password": TEST_PASSWORD},
    )
    assert other_login.status_code == 200
    assert other_login.json is not None
    client.environ_base["HTTP_X_CSRF_TOKEN"] = other_login.json["csrfToken"]
    assert client.get(f"/api/transactions/{transaction_id}/evidence").status_code == 404


def test_idempotent_retry_reuses_preview_and_rejects_changed_payload(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    _, client, _ = draft_app
    idempotency_key = str(uuid4())
    payload = {"text": "Ăn phở 50k"}
    headers = {"Idempotency-Key": idempotency_key}

    first_response = client.post("/api/drafts", json=payload, headers=headers)
    replay_response = client.post("/api/drafts", json=payload, headers=headers)
    conflicting_response = client.post(
        "/api/drafts",
        json={"text": "Uống trà 30k"},
        headers=headers,
    )

    assert first_response.status_code == 201
    assert first_response.json is not None
    assert replay_response.status_code == 200
    assert replay_response.json is not None
    assert replay_response.json["replayed"] is True
    assert replay_response.json["draft"]["id"] == first_response.json["draft"]["id"]
    assert conflicting_response.status_code == 409


def test_open_drafts_can_be_listed_again_after_navigation(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    _, client, _ = draft_app
    created = client.post(
        "/api/drafts",
        json={"text": "2026-11-02 8h sáng ăn phở 50k"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 201
    assert created.json is not None

    response = client.get("/api/drafts?limit=20")

    assert response.status_code == 200
    assert response.json is not None
    assert response.json["items"][0]["id"] == created.json["draft"]["id"]
    draft = response.json["items"][0]
    assert draft["evidence"]["sourceText"] == "2026-11-02 8h sáng ăn phở 50k"
    item = draft["items"][0]
    confirmation = client.post(
        f"/api/drafts/{draft['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": item["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmation.status_code == 201
    assert client.get("/api/drafts?limit=20").json == {"items": []}

    with Session(draft_app[0].extensions["database_engine"]) as database:
        assert database.scalar(select(func.count()).select_from(Draft)) == 1
        assert database.scalar(select(func.count()).select_from(DraftItem)) == 1
        assert database.scalar(select(func.count()).select_from(Evidence)) == 1


def test_draft_reads_are_private_to_the_authenticated_user(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, owner_client, _ = draft_app
    created = owner_client.post(
        "/api/drafts",
        json={"text": "Ăn phở 50k"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.json is not None
    draft_id = created.json["draft"]["id"]

    with Session(app.extensions["database_engine"]) as database:
        hasher: PasswordHasher = app.extensions["password_hasher"]
        database.add(User(username="other", password_hash=hasher.hash("Another test password! 2026")))
        database.commit()
    other_client = app.test_client()
    other_login = other_client.post(
        "/api/auth/login",
        json={"username": "other", "password": "Another test password! 2026"},
    )
    assert other_login.status_code == 200

    response = other_client.get(f"/api/drafts/{draft_id}")

    assert response.status_code == 404


def test_confirm_ready_item_creates_one_transaction_and_is_retry_safe(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, owner_id = draft_app
    created = client.post(
        "/api/drafts",
        json={"text": "Hôm qua 8h sáng ăn phở 50k"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 201
    assert created.json is not None
    draft = created.json["draft"]
    item = draft["items"][0]
    assert item["status"] == "ready"

    idempotency_key = str(uuid4())
    headers = {"Idempotency-Key": idempotency_key}
    payload = {"expectedVersion": item["version"]}
    first = client.post(f"/api/drafts/{draft['id']}/items/{item['id']}/confirm", json=payload, headers=headers)
    replay = client.post(f"/api/drafts/{draft['id']}/items/{item['id']}/confirm", json=payload, headers=headers)
    changed_replay = client.post(
        f"/api/drafts/{draft['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": item["version"] + 1},
        headers=headers,
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json is not None
    assert replay.json["replayed"] is True
    assert changed_replay.status_code == 409
    with Session(app.extensions["database_engine"]) as database:
        transaction = database.scalar(select(Transaction))
        assert transaction is not None
        assert transaction.user_id == owner_id
        assert str(transaction.amount) == "50000.0000"
        assert transaction.local_time is not None
        assert transaction.local_time.isoformat(timespec="minutes") == "08:00"
        assert transaction.day_period == "morning"
        assert database.scalar(select(func.count()).select_from(Transaction)) == 1
        from fingpt.persistence.models import TransactionRevision

        revision = database.scalar(select(TransactionRevision))
        assert revision is not None
        assert revision.action == "created"
        assert revision.after_state is not None


def test_confirmation_rejects_stale_item_version_without_writing(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, _ = draft_app
    created = client.post(
        "/api/drafts",
        json={"text": "Hôm qua 8h sáng ăn phở 50k"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.json is not None
    draft = created.json["draft"]
    item = draft["items"][0]
    response = client.post(
        f"/api/drafts/{draft['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": item["version"] + 1},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 409
    with Session(app.extensions["database_engine"]) as database:
        assert database.scalar(select(func.count()).select_from(Transaction)) == 0


def test_edit_incomplete_item_then_confirm_preserves_explicit_facts(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    _, client, _ = draft_app
    created = client.post(
        "/api/drafts",
        json={"text": "Ăn bánh mì 15k"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.json is not None
    draft = created.json["draft"]
    item = draft["items"][0]
    edit = client.patch(
        f"/api/drafts/{draft['id']}/items/{item['id']}",
        json={
            "expectedVersion": item["version"],
            "transactionDate": "2026-11-02",
            "localTime": "08:00",
        },
    )
    assert edit.status_code == 200
    assert edit.json is not None
    updated_item = edit.json["item"]
    assert updated_item["status"] == "ready"
    assert updated_item["dayPeriod"] == "morning"
    confirmed = client.post(
        f"/api/drafts/{draft['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": updated_item["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmed.status_code == 201


def test_edit_item_rejects_clock_and_day_period_mismatch(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    _, client, _ = draft_app
    created = client.post(
        "/api/drafts",
        json={"text": "Ăn bánh mì 15k"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.json is not None
    draft = created.json["draft"]
    item = draft["items"][0]
    response = client.patch(
        f"/api/drafts/{draft['id']}/items/{item['id']}",
        json={
            "expectedVersion": item["version"],
            "transactionDate": "2026-11-02",
            "localTime": "08:00",
            "dayPeriod": "evening",
        },
    )
    assert response.status_code == 422


def test_transaction_search_and_totals_use_persisted_owner_data(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    from datetime import date

    _, client, _ = draft_app
    for text in (
        f"{date.today().isoformat()} 8h sáng ăn phở 50k",
        f"{date.today().isoformat()} 8h sáng lương 100000",
    ):
        created = client.post(
            "/api/drafts",
            json={"text": text},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert created.status_code == 201
        assert created.json is not None
        draft = created.json["draft"]
        item = draft["items"][0]
        confirmed = client.post(
            f"/api/drafts/{draft['id']}/items/{item['id']}/confirm",
            json={"expectedVersion": item["version"]},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert confirmed.status_code == 201

    found = client.get("/api/transactions?q=phở&kind=expense")
    report = client.get(
        f"/api/transactions/reports/totals?from={date.today().isoformat()}&to={date.today().isoformat()}"
    )

    assert found.status_code == 200
    assert found.json is not None
    assert found.json["total"] == 1
    assert found.json["items"][0]["description"] == "phở"
    assert report.status_code == 200
    assert report.json is not None
    assert {(row["kind"], row["amount"]) for row in report.json["totals"]} == {
        ("expense", "50000.0000"),
        ("income", "100000.0000"),
    }


def test_confirmed_transaction_edit_delete_and_undo_use_durable_previews(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    _, client, _ = draft_app
    created = client.post(
        "/api/drafts",
        json={"text": "Hôm qua 8h sáng ăn phở 50k"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.json is not None
    draft = created.json["draft"]
    item = draft["items"][0]
    confirmed = client.post(
        f"/api/drafts/{draft['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": item["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmed.json is not None
    transaction_id = confirmed.json["transaction"]["id"]

    proposed = client.post(
        f"/api/transactions/{transaction_id}/changes",
        json={"action": "updated", "expectedVersion": 1, "amount": {"amount": "60000", "currency": "VND"}},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert proposed.status_code == 201
    assert proposed.json is not None
    change = proposed.json["change"]
    assert change["proposedState"]["amount"] == "60000"

    before_confirm = client.get("/api/transactions?q=phở")
    assert before_confirm.json is not None
    assert before_confirm.json["items"][0]["amount"]["amount"] == "50000.0000"
    apply_headers = {"Idempotency-Key": str(uuid4())}
    apply_payload = {"expectedVersion": change["version"]}
    applied = client.post(
        f"/api/transactions/changes/{change['id']}/confirm",
        json=apply_payload,
        headers=apply_headers,
    )
    replay = client.post(
        f"/api/transactions/changes/{change['id']}/confirm",
        json=apply_payload,
        headers=apply_headers,
    )
    assert applied.status_code == 201
    assert replay.status_code == 200

    deleted = client.post(
        f"/api/transactions/{transaction_id}/changes",
        json={"action": "deleted", "expectedVersion": 2},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert deleted.status_code == 201
    assert deleted.json is not None
    delete_change = deleted.json["change"]
    assert delete_change["proposedState"]["isDeleted"] is True
    delete_confirmed = client.post(
        f"/api/transactions/changes/{delete_change['id']}/confirm",
        json={"expectedVersion": delete_change["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert delete_confirmed.status_code == 201
    hidden = client.get("/api/transactions?q=phở")
    assert hidden.json is not None
    assert hidden.json["total"] == 0
    deleted_history = client.get("/api/transactions?q=phở&includeDeleted=true")
    assert deleted_history.json is not None
    assert deleted_history.json["total"] == 1
    assert deleted_history.json["items"][0]["isDeleted"] is True

    undo = client.post(
        f"/api/transactions/{transaction_id}/undo",
        json={"expectedVersion": 3},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert undo.status_code == 201
    assert undo.json is not None
    undo_change = undo.json["change"]
    assert undo_change["proposedState"]["isDeleted"] is False
    restored = client.post(
        f"/api/transactions/changes/{undo_change['id']}/confirm",
        json={"expectedVersion": undo_change["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert restored.status_code == 201
    visible = client.get("/api/transactions?q=phở")
    assert visible.json is not None
    assert visible.json["items"][0]["amount"]["amount"] == "60000.0000"


def test_transaction_revisions_are_available_to_the_owner_only(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    _, client, _ = draft_app
    draft = client.post(
        "/api/drafts",
        json={"text": "2026-11-02 8h sáng ăn phở 50k"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert draft.json is not None
    draft_data = draft.json["draft"]
    item = draft_data["items"][0]
    confirmed = client.post(
        f"/api/drafts/{draft_data['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": item["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmed.json is not None
    transaction_id = confirmed.json["transaction"]["id"]

    response = client.get(f"/api/transactions/{transaction_id}/revisions")

    assert response.status_code == 200
    assert response.json is not None
    assert len(response.json["items"]) == 1
    revision = response.json["items"][0]
    assert revision["revisionNumber"] == 1
    assert revision["action"] == "created"
    assert revision["beforeState"] is None
    assert revision["afterState"]["amount"] == "50000"
    assert revision["afterState"]["isDeleted"] is False
    assert client.get(f"/api/transactions/{uuid4()}/revisions").status_code == 404

    proposal_response = client.post(
        f"/api/transactions/{transaction_id}/changes",
        json={"action": "updated", "expectedVersion": 1, "amount": {"amount": "55000", "currency": "VND"}},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert proposal_response.json is not None
    proposal = proposal_response.json["change"]
    pending = client.get(f"/api/transactions/{transaction_id}/changes")
    assert pending.json is not None
    assert [row["id"] for row in pending.json["items"]] == [proposal["id"]]
    cancel_payload = {"expectedVersion": proposal["version"]}
    cancelled = client.post(f"/api/transactions/changes/{proposal['id']}/cancel", json=cancel_payload)
    replay = client.post(f"/api/transactions/changes/{proposal['id']}/cancel", json=cancel_payload)
    assert cancelled.status_code == 200
    assert cancelled.json is not None
    assert cancelled.json["change"]["status"] == "cancelled"
    assert replay.json is not None
    assert replay.json["replayed"] is True
    assert client.get(f"/api/transactions/{transaction_id}/changes").json == {"items": []}


def test_telegram_webhook_authenticates_deduplicates_and_uses_outbox(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    from fingpt.persistence.models import BotConnection, OutboxMessage, WebhookReceipt

    app, _, owner_id = draft_app
    app.config["TELEGRAM_WEBHOOK_SECRET_RESOLVER"] = lambda name: (
        "secret-for-tests" if name == "telegram/1-webhook" else None
    )
    with Session(app.extensions["database_engine"]) as database:
        database.add(
            BotConnection(
                user_id=owner_id,
                telegram_bot_id=987654321,
                secret_name="telegram/1-token",
                webhook_secret_name="telegram/1-webhook",
                allowed_telegram_user_id=123456789,
                is_enabled=True,
            )
        )
        database.commit()
    webhook = app.test_client()
    update = {
        "update_id": 7001,
        "message": {
            "message_id": 90,
            "from": {"id": 123456789},
            "chat": {"id": 123456789, "type": "private"},
            "text": "Hôm qua ăn phở 50k",
        },
    }
    headers = {"X-Telegram-Bot-Api-Secret-Token": "secret-for-tests"}
    first = webhook.post("/api/webhooks/telegram/987654321", json=update, headers=headers)
    replay = webhook.post("/api/webhooks/telegram/987654321", json=update, headers=headers)

    assert first.status_code == 202
    assert replay.status_code == 200
    assert replay.json is not None
    assert replay.json["replayed"] is True
    with Session(app.extensions["database_engine"]) as database:
        assert database.scalar(select(func.count()).select_from(WebhookReceipt)) == 1
        event = database.scalar(select(OutboxMessage))
        assert event is not None
        assert event.status == "pending"
        assert "text" not in event.payload
        assert event.payload["telegramMessageId"] == 90
        from fingpt.services.telegram_worker import process_telegram_text_event

        draft_id = process_telegram_text_event(database, event.payload)
        replayed_draft_id = process_telegram_text_event(database, event.payload)
        assert replayed_draft_id == draft_id
        assert database.scalar(select(func.count()).select_from(Draft)) == 1
        assert database.scalar(select(func.count()).select_from(Evidence)) == 1
        receipt = database.scalar(select(WebhookReceipt))
        assert receipt is not None
        assert receipt.status == "processed"
        assert database.scalar(select(func.count()).select_from(OutboxMessage)) == 2


def test_telegram_webhook_rejects_bad_secret_and_unapproved_sender(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    from fingpt.persistence.models import BotConnection

    app, _, owner_id = draft_app
    app.config["TELEGRAM_WEBHOOK_SECRET_RESOLVER"] = lambda name: (
        "secret-for-tests" if name == "telegram/1-webhook" else None
    )
    with Session(app.extensions["database_engine"]) as database:
        database.add(
            BotConnection(
                user_id=owner_id,
                telegram_bot_id=987654321,
                secret_name="telegram/1-token",
                webhook_secret_name="telegram/1-webhook",
                allowed_telegram_user_id=123456789,
                is_enabled=True,
            )
        )
        database.commit()
    webhook = app.test_client()
    update = {
        "update_id": 7002,
        "message": {
            "message_id": 91,
            "from": {"id": 999},
            "chat": {"id": 999, "type": "private"},
            "text": "ăn bánh mì 15k",
        },
    }

    bad_secret = webhook.post("/api/webhooks/telegram/987654321", json=update)
    denied_user = webhook.post(
        "/api/webhooks/telegram/987654321",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-for-tests"},
    )

    assert bad_secret.status_code == 401
    assert denied_user.status_code == 403


def test_telegram_inline_confirmation_callback_is_authorized_and_idempotent(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    from unittest.mock import patch

    from fingpt.adapters.telegram import TelegramBotApi, _confirmation_callback
    from fingpt.persistence.models import BotConnection, OutboxMessage, WebhookReceipt
    from fingpt.services.telegram_worker import (
        PermanentMessageError,
        process_telegram_callback,
        process_telegram_text_event,
    )
    from fingpt.worker import _process_message

    app, _, owner_id = draft_app
    app.config["TELEGRAM_WEBHOOK_SECRET_RESOLVER"] = lambda name: (
        "secret-for-tests" if name == "telegram/webhook" else None
    )
    with Session(app.extensions["database_engine"]) as database:
        bot = BotConnection(
            user_id=owner_id,
            telegram_bot_id=887766554,
            secret_name="telegram/token",
            webhook_secret_name="telegram/webhook",
            allowed_telegram_user_id=345678901,
            is_enabled=True,
        )
        database.add(bot)
        database.commit()
    webhook = app.test_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "secret-for-tests"}
    inbound = {
        "update_id": 8001,
        "message": {
            "message_id": 1,
            "from": {"id": 345678901},
            "chat": {"id": 345678901, "type": "private"},
            "text": "2026-11-02 8h sáng ăn bánh mì 15k",
        },
    }
    accepted = webhook.post("/api/webhooks/telegram/887766554", json=inbound, headers=headers)
    assert accepted.status_code == 202
    with Session(app.extensions["database_engine"]) as database:
        event = database.scalar(select(OutboxMessage).where(OutboxMessage.message_type == "telegram.text.accepted"))
        assert event is not None
        draft_id = process_telegram_text_event(database, event.payload)
        assert draft_id is not None
        from fingpt.persistence.models import DraftItem

        item = database.scalar(select(DraftItem).where(DraftItem.draft_id == draft_id))
        assert item is not None
        callback_data = _confirmation_callback(draft_id, item.id, item.version)
        assert len(callback_data.encode("utf-8")) <= 64
    callback_update = {
        "update_id": 8002,
        "callback_query": {
            "id": "callback-8002",
            "from": {"id": 345678901},
            "message": {
                "message_id": 2,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": 345678901, "type": "private"},
            },
            "data": callback_data,
        },
    }
    callback_response = webhook.post("/api/webhooks/telegram/887766554", json=callback_update, headers=headers)
    replay = webhook.post("/api/webhooks/telegram/887766554", json=callback_update, headers=headers)
    assert callback_response.status_code == 202
    assert replay.status_code == 200
    with Session(app.extensions["database_engine"]) as database:
        callback_event = database.scalar(
            select(OutboxMessage).where(OutboxMessage.message_type == "telegram.callback.accepted")
        )
        assert callback_event is not None
        redirected = {**callback_event.payload, "telegramChatId": 987654321}
        with pytest.raises(PermanentMessageError):
            process_telegram_callback(database, redirected)
        padded_action = {
            **callback_event.payload,
            "callbackData": f"{callback_event.payload['callbackData']}=",
        }
        with pytest.raises(PermanentMessageError):
            process_telegram_callback(database, padded_action)
        assert database.scalar(select(func.count()).select_from(Transaction)) == 0
        process_telegram_callback(database, callback_event.payload)
        receipt = database.scalar(select(WebhookReceipt).where(WebhookReceipt.external_update_id == 8002))
        assert receipt is not None
        assert receipt.status == "processed"
        assert database.scalar(select(func.count()).select_from(Transaction)) == 1
        assert database.scalar(select(func.count()).select_from(OutboxMessage)) == 4
        undo_reply = database.scalar(
            select(OutboxMessage).where(OutboxMessage.deduplication_key == f"telegram-callback-reply:{receipt.id}")
        )
        assert undo_reply is not None
        with (
            patch.object(TelegramBotApi, "_call") as send_message,
            patch("fingpt.services.runtime_secrets.resolve_environment_secret", return_value="test-token"),
        ):
            _process_message(app.extensions["database_engine"], "telegram.message.send", undo_reply.payload)
        message = send_message.call_args.args[1]
        assert message["reply_markup"]["inline_keyboard"][0][0]["text"] == "Hoàn tác"


def test_telegram_callback_older_than_24_hours_does_not_confirm(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    from datetime import UTC, datetime, timedelta

    from fingpt.adapters.telegram import _confirmation_callback
    from fingpt.persistence.models import BotConnection, DraftItem, OutboxMessage, WebhookReceipt
    from fingpt.services.telegram_worker import process_telegram_callback, process_telegram_text_event

    app, _, owner_id = draft_app
    app.config["TELEGRAM_WEBHOOK_SECRET_RESOLVER"] = lambda name: (
        "secret-for-tests" if name == "telegram/webhook" else None
    )
    with Session(app.extensions["database_engine"]) as database:
        database.add(
            BotConnection(
                user_id=owner_id,
                telegram_bot_id=887766555,
                secret_name="telegram/token",
                webhook_secret_name="telegram/webhook",
                allowed_telegram_user_id=345678902,
                is_enabled=True,
            )
        )
        database.commit()
    webhook = app.test_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "secret-for-tests"}
    inbound = {
        "update_id": 8011,
        "message": {
            "message_id": 1,
            "from": {"id": 345678902},
            "chat": {"id": 345678902, "type": "private"},
            "text": "2026-11-02 8h sáng ăn bánh mì 15k",
        },
    }
    assert webhook.post("/api/webhooks/telegram/887766555", json=inbound, headers=headers).status_code == 202
    with Session(app.extensions["database_engine"]) as database:
        event = database.scalar(select(OutboxMessage).where(OutboxMessage.message_type == "telegram.text.accepted"))
        assert event is not None
        draft_id = process_telegram_text_event(database, event.payload)
        assert draft_id is not None
        item = database.scalar(select(DraftItem).where(DraftItem.draft_id == draft_id))
        assert item is not None
        callback_data = _confirmation_callback(draft_id, item.id, item.version)
    callback = {
        "update_id": 8012,
        "callback_query": {
            "id": "callback-8012",
            "from": {"id": 345678902},
            "message": {
                "message_id": 2,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": 345678902, "type": "private"},
            },
            "data": callback_data,
        },
    }
    assert webhook.post("/api/webhooks/telegram/887766555", json=callback, headers=headers).status_code == 202
    with Session(app.extensions["database_engine"]) as database:
        event = database.scalar(
            select(OutboxMessage).where(OutboxMessage.message_type == "telegram.callback.accepted")
        )
        receipt = database.scalar(select(WebhookReceipt).where(WebhookReceipt.external_update_id == 8012))
        assert event is not None and receipt is not None
        receipt.telegram_message_at = datetime.now(UTC) - timedelta(hours=25)
        database.commit()
        process_telegram_callback(database, event.payload)
        assert database.scalar(select(func.count()).select_from(Transaction)) == 0
