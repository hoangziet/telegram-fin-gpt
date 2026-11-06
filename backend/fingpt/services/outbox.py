"""At-least-once processing from the relational transactional outbox."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fingpt.adapters.telegram import TelegramDeliveryError
from fingpt.persistence.models import OutboxMessage
from fingpt.services.telegram_worker import PermanentMessageError

MAX_PUBLISH_ATTEMPTS = 10


def process_outbox_batch(
    database: Session,
    handlers: dict[str, Callable[[Session, dict[str, object]], object]],
    *,
    limit: int = 20,
) -> int:
    """Process pending events with row locks and persist acknowledgement or retry state."""
    events = database.scalars(
        select(OutboxMessage)
        .where(OutboxMessage.status == "pending")
        .order_by(OutboxMessage.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    processed_count = 0
    for event in events:
        handler = handlers.get(event.message_type)
        if handler is None:
            event.status = "failed"
            event.attempt_count += 1
            database.commit()
            continue
        try:
            handler(database, {"eventType": event.message_type, **event.payload})
        except PermanentMessageError:
            database.rollback()
            event.status = "failed"
            event.attempt_count += 1
            database.commit()
            continue
        except (SQLAlchemyError, TelegramDeliveryError):
            database.rollback()
            event.attempt_count += 1
            if event.attempt_count >= MAX_PUBLISH_ATTEMPTS * (event.operator_retry_count + 1):
                event.status = "failed"
            database.commit()
            continue
        event.attempt_count += 1
        event.status = "published"
        event.published_at = datetime.now(UTC)
        database.commit()
        processed_count += 1
    return processed_count
