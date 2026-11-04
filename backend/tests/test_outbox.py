"""Tests for retryable transactional outbox delivery."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from fingpt.adapters.service_bus import MessagePublishError
from fingpt.persistence import Base
from fingpt.persistence.models import OutboxMessage
from fingpt.services.outbox import MAX_PUBLISH_ATTEMPTS, dispatch_outbox_batch
from fingpt.services.outbox_operations import retry_failed_outbox_event


class RecordingPublisher:
    """Deterministic broker fake for outbox state tests."""

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.message_ids: list[str] = []
        self.events: list[dict[str, object]] = []

    def publish(self, message_id: str, payload: dict[str, object]) -> None:
        """Record acknowledged sends or simulate bounded broker failures."""
        if self.failures:
            self.failures -= 1
            raise MessagePublishError("send failed")
        self.message_ids.append(message_id)
        self.events.append(payload)


def _database() -> tuple[Session, Engine]:
    """Create a disposable in-memory database and its session."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine), engine


def test_dispatch_marks_acknowledged_outbox_events_published() -> None:
    database, engine = _database()
    event = OutboxMessage(
        deduplication_key=f"event:{uuid4()}",
        message_type="test.event",
        payload={"reference": "id-only"},
    )
    database.add(event)
    database.commit()
    publisher = RecordingPublisher()

    sent = dispatch_outbox_batch(database, publisher)

    database.refresh(event)
    assert sent == 1
    assert event.status == "published"
    assert event.attempt_count == 1
    assert publisher.message_ids == [event.deduplication_key]
    database.close()
    engine.dispose()


def test_dispatch_marks_events_failed_after_bounded_retries() -> None:
    database, engine = _database()
    event = OutboxMessage(
        deduplication_key=f"event:{uuid4()}",
        message_type="test.event",
        payload={"reference": "id-only"},
    )
    database.add(event)
    database.commit()
    publisher = RecordingPublisher(failures=MAX_PUBLISH_ATTEMPTS)

    for _ in range(MAX_PUBLISH_ATTEMPTS):
        assert dispatch_outbox_batch(database, publisher) == 0

    database.refresh(event)
    assert event.status == "failed"
    assert event.attempt_count == MAX_PUBLISH_ATTEMPTS
    database.close()
    engine.dispose()


def test_dispatch_uses_event_type_and_stable_message_identifier() -> None:
    database, engine = _database()
    event = OutboxMessage(
        deduplication_key=f"event:{uuid4()}",
        message_type="telegram.text.accepted",
        payload={"receiptId": str(uuid4())},
    )
    database.add(event)
    database.commit()
    publisher = RecordingPublisher()
    assert dispatch_outbox_batch(database, publisher) == 1
    assert publisher.message_ids == [event.deduplication_key]
    assert publisher.events[0]["eventType"] == "telegram.text.accepted"
    database.close()
    engine.dispose()


def test_operator_retry_preserves_attempt_history_and_stable_delivery_key() -> None:
    database, engine = _database()
    event = OutboxMessage(
        deduplication_key=f"event:{uuid4()}",
        message_type="telegram.text.accepted",
        payload={"receiptId": str(uuid4())},
        status="failed",
        attempt_count=MAX_PUBLISH_ATTEMPTS,
    )
    database.add(event)
    database.commit()
    event_id = event.id
    delivery_key = event.deduplication_key

    recovered = retry_failed_outbox_event(database, event_id)

    assert recovered.status == "pending"
    assert recovered.attempt_count == MAX_PUBLISH_ATTEMPTS
    assert recovered.operator_retry_count == 1
    assert recovered.deduplication_key == delivery_key
    publisher = RecordingPublisher()
    assert dispatch_outbox_batch(database, publisher) == 1
    assert publisher.message_ids == [delivery_key]
    assert recovered.status == "published"
    database.close()
    engine.dispose()


def test_operator_retry_refuses_pending_or_missing_events() -> None:
    database, engine = _database()
    event = OutboxMessage(
        deduplication_key=f"event:{uuid4()}",
        message_type="test.event",
        payload={},
    )
    database.add(event)
    database.commit()

    import pytest

    with pytest.raises(LookupError):
        retry_failed_outbox_event(database, event.id)
    with pytest.raises(LookupError):
        retry_failed_outbox_event(database, uuid4())
    database.close()
    engine.dispose()
