"""Operator visibility and controlled recovery for durable outbox events."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from fingpt.persistence.models import OutboxMessage


def list_outbox_events(database: Session, *, status: str | None, limit: int) -> tuple[OutboxMessage, ...]:
    """Return event metadata without exposing payloads or financial content."""
    query = select(OutboxMessage).order_by(OutboxMessage.created_at.desc()).limit(limit)
    if status is not None:
        query = query.where(OutboxMessage.status == status)
    return tuple(database.scalars(query))


def retry_failed_outbox_event(database: Session, event_id: UUID) -> OutboxMessage:
    """Requeue one failed event while retaining its attempt history and message ID."""
    event = database.scalar(select(OutboxMessage).where(OutboxMessage.id == event_id).with_for_update())
    if event is None or event.status != "failed":
        raise LookupError("Failed outbox event was not found.")
    event.operator_retry_count += 1
    event.status = "pending"
    event.published_at = None
    database.commit()
    return event
