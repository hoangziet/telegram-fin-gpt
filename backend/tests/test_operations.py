"""Operator CLI tests for private outbox visibility and recovery."""

from __future__ import annotations

from uuid import UUID

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.orm import Session

from fingpt.persistence.models import OutboxMessage


def test_outbox_cli_redacts_payload_and_requeues_failed_event(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app = draft_app[0]
    engine = app.extensions["database_engine"]
    with Session(engine, expire_on_commit=False) as database:
        event = OutboxMessage(
            deduplication_key="private-event-key",
            message_type="telegram.text.accepted",
            payload={"sourceText": "secret transaction contents"},
            status="failed",
            attempt_count=10,
        )
        database.add(event)
        database.commit()
        event_id = event.id

    runner = app.test_cli_runner()
    listed = runner.invoke(args=["outbox", "list", "--status", "failed"])
    assert listed.exit_code == 0
    assert str(event_id) in listed.output
    assert "telegram.text.accepted" in listed.output
    assert "secret transaction contents" not in listed.output
    assert "private-event-key" not in listed.output

    retried = runner.invoke(args=["outbox", "retry", str(event_id)])
    assert retried.exit_code == 0
    assert "stable message key retained" in retried.output
    with Session(engine) as database:
        recovered = database.get(OutboxMessage, event_id)
        assert recovered is not None
        assert recovered.status == "pending"
        assert recovered.attempt_count == 10
        assert recovered.operator_retry_count == 1


def test_outbox_cli_requires_uuid_and_failed_event(draft_app: tuple[Flask, FlaskClient, UUID]) -> None:
    app = draft_app[0]
    runner = app.test_cli_runner()
    malformed = runner.invoke(args=["outbox", "retry", "not-a-uuid"])
    assert malformed.exit_code != 0
    assert "event_id must be a UUID" in malformed.output
    missing = runner.invoke(args=["outbox", "retry", "3d734a1a-4a66-4fed-a5b1-bdb519f34b55"])
    assert missing.exit_code != 0
    assert "Failed outbox event was not found" in missing.output
