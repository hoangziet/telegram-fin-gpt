"""Tests for direct transactional outbox processing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from fingpt.adapters.telegram import TelegramDeliveryError
from fingpt.persistence import Base
from fingpt.persistence.models import OutboxMessage
from fingpt.services.outbox import MAX_PUBLISH_ATTEMPTS, process_outbox_batch
from fingpt.services.outbox_operations import retry_failed_outbox_event
from fingpt.services.telegram_worker import PermanentMessageError


def _database() -> tuple[Session, Engine]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine), engine


def _event(message_type: str = "test.event", **kwargs: Any) -> OutboxMessage:
    return OutboxMessage(deduplication_key=f"event:{uuid4()}", message_type=message_type, payload={}, **kwargs)


def test_success_and_pending_order() -> None:
    database, engine = _database()
    earlier = _event(created_at=datetime.now(UTC) - timedelta(days=1))
    later = _event()
    done = _event(status="published")
    earlier.payload = {"order": "earlier"}
    later.payload = {"order": "later"}
    database.add_all([later, earlier, done])
    database.commit()
    seen: list[dict[str, object]] = []

    def handle(_db: Session, payload: dict[str, object]) -> None:
        seen.append(payload)

    assert process_outbox_batch(database, {"test.event": handle}) == 2
    assert seen == [
        {"eventType": "test.event", "order": "earlier"},
        {"eventType": "test.event", "order": "later"},
    ]
    assert earlier.status == later.status == "published"
    assert done.attempt_count == 0
    assert earlier.published_at is not None
    database.close()
    engine.dispose()


def test_permanent_and_unknown_fail_immediately() -> None:
    database, engine = _database()
    permanent = _event()
    unknown = _event("unknown")
    database.add_all([permanent, unknown])
    database.commit()

    def fail(_db: Session, _payload: dict[str, object]) -> None:
        raise PermanentMessageError("invalid")

    assert process_outbox_batch(database, {"test.event": fail}) == 0
    assert permanent.status == unknown.status == "failed"
    database.close()
    engine.dispose()


def test_transient_retries_then_fails_and_operator_retry() -> None:
    database, engine = _database()
    event = _event()
    database.add(event)
    database.commit()

    def fail(_db: Session, _payload: dict[str, object]) -> None:
        raise TelegramDeliveryError("temporary")

    for _ in range(MAX_PUBLISH_ATTEMPTS):
        assert process_outbox_batch(database, {"test.event": fail}) == 0
    assert event.status == "failed"
    assert event.attempt_count == MAX_PUBLISH_ATTEMPTS
    recovered = retry_failed_outbox_event(database, event.id)
    assert recovered.status == "pending"
    assert recovered.operator_retry_count == 1
    assert process_outbox_batch(database, {"test.event": lambda _db, _payload: None}) == 1
    assert recovered.status == "published"
    database.close()
    engine.dispose()


def test_pending_query_uses_skip_locked() -> None:
    from unittest.mock import MagicMock

    database = MagicMock(spec=Session)
    database.scalars.return_value.all.return_value = []
    assert process_outbox_batch(database, {}) == 0
    statement = database.scalars.call_args.args[0]
    assert statement._for_update_arg is not None
    assert statement._for_update_arg.skip_locked is True
    assert statement._limit_clause is not None
    assert statement._order_by_clauses
    assert statement._where_criteria
