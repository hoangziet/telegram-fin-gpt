"""At-least-once publication from the relational transactional outbox."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from fingpt.adapters.service_bus import MessagePublisher, MessagePublishError
from fingpt.persistence.models import OutboxMessage

MAX_PUBLISH_ATTEMPTS = 10


def dispatch_outbox_batch(database: Session, publisher: MessagePublisher, *, limit: int = 20) -> int:
    """Publish pending events and persist acknowledgement or retry state."""
    events = database.scalars(
        select(OutboxMessage)
        .where(OutboxMessage.status == "pending")
        .order_by(OutboxMessage.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    published_count = 0
    for event in events:
        try:
            publisher.publish(
                event.deduplication_key,
                {"eventType": event.message_type, **event.payload},
            )
        except MessagePublishError:
            event.attempt_count += 1
            if event.attempt_count >= MAX_PUBLISH_ATTEMPTS * (event.operator_retry_count + 1):
                event.status = "failed"
            database.commit()
            continue
        event.attempt_count += 1
        event.status = "published"
        event.published_at = datetime.now(UTC)
        database.commit()
        published_count += 1
    return published_count
