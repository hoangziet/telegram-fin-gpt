"""Persist and retrieve owner-scoped transaction drafts with source evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fingpt.domain.text_interpretation import ParsedTextItem, TransactionKind, parse_text_request
from fingpt.domain.transaction_time import DEFAULT_TIMEZONE
from fingpt.persistence.models import Conversation, Draft, DraftItem, Evidence, Transaction, TransactionRevision


class DraftIdempotencyConflict(ValueError):
    """Raised when a request key is reused with different input."""


class ConversationNotFound(LookupError):
    """Raised when a requested conversation is absent or owned by another user."""


@dataclass(frozen=True, slots=True)
class DraftSnapshot:
    """User-owned aggregate required to render a draft preview."""

    draft: Draft
    conversation: Conversation
    evidence: Evidence
    items: tuple[DraftItem, ...]


def create_text_draft(
    database: Session,
    *,
    user_id: UUID,
    text: str,
    idempotency_key: str,
    conversation_id: UUID | None,
    intake_at: datetime,
    timezone: str = DEFAULT_TIMEZONE,
    evidence_id: UUID | None = None,
    conversation_channel: str = "web",
) -> tuple[DraftSnapshot, bool]:
    """Save a parsed text draft and its source atomically, without transactions."""
    request_hash = _request_hash(text, conversation_id)
    existing = _find_by_key(database, user_id, idempotency_key)
    if existing is not None:
        return _replay(database, existing, request_hash)

    parsed = parse_text_request(text, intake_at=intake_at, timezone=timezone)
    conversation = _resolve_conversation(database, user_id, conversation_id, conversation_channel)
    evidence = _resolve_text_evidence(database, user_id, text, evidence_id)
    draft = Draft(
        id=uuid4(),
        user_id=user_id,
        conversation_id=conversation.id,
        evidence_id=evidence.id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status="open",
        version=1,
    )
    if evidence_id is None:
        database.add(evidence)
        database.flush()
    database.add(draft)

    try:
        database.flush()
        database.add_all(_create_items(user_id, draft.id, parsed.items, timezone))
        database.commit()
    except IntegrityError:
        database.rollback()
        concurrent_draft = _find_by_key(database, user_id, idempotency_key)
        if concurrent_draft is None:
            raise
        return _replay(database, concurrent_draft, request_hash)

    snapshot = get_draft_snapshot(database, user_id, draft.id)
    if snapshot is None:
        raise RuntimeError("A newly created draft could not be loaded.")
    return snapshot, False


def get_draft_snapshot(database: Session, user_id: UUID, draft_id: UUID) -> DraftSnapshot | None:
    """Load all preview data with owner-scoped predicates on every query."""
    draft = database.scalar(select(Draft).where(Draft.id == draft_id, Draft.user_id == user_id))
    if draft is None:
        return None
    conversation = database.scalar(
        select(Conversation).where(Conversation.id == draft.conversation_id, Conversation.user_id == user_id)
    )
    evidence = database.scalar(select(Evidence).where(Evidence.id == draft.evidence_id, Evidence.user_id == user_id))
    items = tuple(
        database.scalars(
            select(DraftItem)
            .where(DraftItem.draft_id == draft.id, DraftItem.user_id == user_id)
            .order_by(DraftItem.item_index)
        )
    )
    if conversation is None or evidence is None:
        raise RuntimeError("Draft ownership references are inconsistent.")
    return DraftSnapshot(draft=draft, conversation=conversation, evidence=evidence, items=items)


def list_open_draft_snapshots(database: Session, user_id: UUID, *, limit: int) -> tuple[DraftSnapshot, ...]:
    """List recent owner drafts that still have at least one actionable item."""
    pending_ids = (
        select(Draft.id)
        .join(DraftItem, DraftItem.draft_id == Draft.id)
        .where(Draft.user_id == user_id, DraftItem.status.in_(("incomplete", "ready")))
        .group_by(Draft.id)
        .order_by(func.max(DraftItem.updated_at).desc())
        .limit(limit)
    )
    rows = database.execute(
        select(Draft, Conversation, Evidence, DraftItem)
        .join(Conversation, (Conversation.id == Draft.conversation_id) & (Conversation.user_id == user_id))
        .join(Evidence, (Evidence.id == Draft.evidence_id) & (Evidence.user_id == user_id))
        .join(DraftItem, (DraftItem.draft_id == Draft.id) & (DraftItem.user_id == user_id))
        .where(Draft.id.in_(pending_ids), Draft.user_id == user_id)
        .order_by(Draft.created_at.desc(), DraftItem.item_index)
    )
    grouped: dict[UUID, tuple[Draft, Conversation, Evidence, list[DraftItem]]] = {}
    for draft, conversation, evidence, item in rows:
        if draft.id not in grouped:
            grouped[draft.id] = (draft, conversation, evidence, [])
        grouped[draft.id][3].append(item)
    return tuple(
        DraftSnapshot(draft, conversation, evidence, tuple(items))
        for draft, conversation, evidence, items in grouped.values()
    )


def _request_hash(text: str, conversation_id: UUID | None) -> str:
    """Hash a canonical representation of request intent for safe replay."""
    payload = json.dumps(
        {"conversationId": str(conversation_id) if conversation_id else None, "text": text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _find_by_key(database: Session, user_id: UUID, key: str) -> Draft | None:
    """Find a draft by its owner and idempotency key."""
    return database.scalar(select(Draft).where(Draft.user_id == user_id, Draft.idempotency_key == key))


def _replay(database: Session, draft: Draft, request_hash: str) -> tuple[DraftSnapshot, bool]:
    """Return an exact retry or reject reuse of a key for different intent."""
    if draft.request_hash != request_hash:
        raise DraftIdempotencyConflict("This idempotency key was already used for different input.")
    snapshot = get_draft_snapshot(database, draft.user_id, draft.id)
    if snapshot is None:
        raise RuntimeError("An idempotent draft could not be loaded.")
    return snapshot, True


def _resolve_conversation(
    database: Session, user_id: UUID, conversation_id: UUID | None, channel: str
) -> Conversation:
    """Validate an existing conversation or create one for a new web draft."""
    if conversation_id is not None:
        conversation = database.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.channel == channel,
            )
        )
        if conversation is None:
            raise ConversationNotFound("Conversation was not found.")
        return conversation
    conversation = Conversation(
        id=uuid4(),
        user_id=user_id,
        channel="web",
        external_conversation_id=str(uuid4()),
    )
    database.add(conversation)
    database.flush()
    return conversation


def _resolve_text_evidence(database: Session, user_id: UUID, text: str, evidence_id: UUID | None) -> Evidence:
    """Reuse accepted webhook evidence or prepare evidence for a web draft."""
    if evidence_id is None:
        return _create_text_evidence(user_id, text)
    evidence = database.scalar(
        select(Evidence).where(
            Evidence.id == evidence_id,
            Evidence.user_id == user_id,
            Evidence.source_type == "text",
            Evidence.source_text == text,
        )
    )
    if evidence is None:
        raise ValueError("Source evidence was not found for this draft.")
    return evidence


def _create_text_evidence(user_id: UUID, text: str) -> Evidence:
    """Capture original text and a content digest for later review."""
    content = text.encode("utf-8")
    return Evidence(
        id=uuid4(),
        user_id=user_id,
        source_type="text",
        source_text=text,
        sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
    )


def _create_items(
    user_id: UUID,
    draft_id: UUID,
    items: tuple[ParsedTextItem, ...],
    timezone: str,
) -> list[DraftItem]:
    """Map validated domain proposals into ordered, independently reviewable rows."""
    return [
        DraftItem(
            user_id=user_id,
            draft_id=draft_id,
            item_index=index,
            status="incomplete" if item.missing_fields else "ready",
            extracted_data=_serialized_item(item, timezone),
            missing_fields=list(item.missing_fields),
            version=1,
        )
        for index, item in enumerate(items)
    ]


def _serialized_item(item: ParsedTextItem, timezone: str) -> dict[str, str | None]:
    """Store an exact, JSON-safe normalized item snapshot."""
    return {
        "description": item.description,
        "kind": item.kind.value,
        "amount": format(item.money.amount, "f") if item.money else None,
        "currency": item.money.currency if item.money else None,
        "transactionDate": item.transaction_date.isoformat() if item.transaction_date else None,
        "dayPeriod": item.day_period.value if item.day_period else None,
        "localTime": item.local_time.isoformat(timespec="minutes") if item.local_time else None,
        "timezone": timezone,
    }


class DraftConfirmationConflict(ValueError):
    """Raised when a confirmation is stale or conflicts with a prior write."""


class DraftItemNotReady(ValueError):
    """Raised when an item still needs required transaction facts."""


def cancel_draft_item(
    database: Session,
    *,
    user_id: UUID,
    draft_id: UUID,
    item_id: UUID,
    expected_version: int,
) -> tuple[DraftItem, bool]:
    """Cancel an owned, unconfirmed draft item with stale-action protection."""
    item = database.scalar(
        select(DraftItem)
        .join(Draft, Draft.id == DraftItem.draft_id)
        .where(
            Draft.id == draft_id,
            Draft.user_id == user_id,
            DraftItem.id == item_id,
            DraftItem.user_id == user_id,
        )
        .with_for_update()
    )
    if item is None:
        raise LookupError("Draft item was not found.")
    if item.status == "cancelled" and item.version == expected_version + 1:
        return item, True
    if item.version != expected_version or item.status not in {"incomplete", "ready"}:
        raise DraftConfirmationConflict("The draft item changed. Reload its preview and try again.")
    item.status = "cancelled"
    item.version += 1
    database.commit()
    return item, False


def confirm_draft_item(
    database: Session,
    *,
    user_id: UUID,
    draft_id: UUID,
    item_id: UUID,
    expected_version: int,
    idempotency_key: str,
) -> tuple[Transaction, bool]:
    """Atomically confirm one ready item with version and retry guards."""
    from datetime import date, time

    confirmation_hash = hashlib.sha256(f"{draft_id}:{item_id}:{expected_version}".encode("ascii")).hexdigest()
    prior = database.scalar(
        select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.idempotency_key == idempotency_key,
        )
    )
    if prior is not None:
        if prior.draft_item_id != item_id or prior.request_hash != confirmation_hash:
            raise DraftConfirmationConflict("This confirmation key was used for different input.")
        return prior, True

    item = database.scalar(
        select(DraftItem)
        .join(Draft, Draft.id == DraftItem.draft_id)
        .where(
            Draft.id == draft_id,
            Draft.user_id == user_id,
            DraftItem.id == item_id,
            DraftItem.user_id == user_id,
        )
        .with_for_update()
    )
    if item is None:
        raise LookupError("Draft item was not found.")
    if item.version != expected_version or item.status != "ready":
        raise DraftConfirmationConflict("The draft item changed. Reload its preview and try again.")
    if item.missing_fields:
        raise DraftItemNotReady("Complete the missing fields before confirming this item.")

    data = item.extracted_data
    row_errors = data.get("rowErrors")
    if isinstance(row_errors, dict) and row_errors:
        raise DraftItemNotReady("Resolve the imported row errors before confirming this item.")
    required = ("description", "amount", "currency", "transactionDate", "dayPeriod", "timezone")
    if any(data.get(field) is None for field in required):
        raise DraftItemNotReady("Complete the missing fields before confirming this item.")
    transaction = Transaction(
        id=uuid4(),
        user_id=user_id,
        draft_item_id=item.id,
        idempotency_key=idempotency_key,
        request_hash=confirmation_hash,
        kind=data["kind"],
        amount=Decimal(data["amount"]),
        currency=data["currency"],
        category=data.get("category") if isinstance(data.get("category"), str) else "uncategorized",
        note=data["description"],
        transaction_date=date.fromisoformat(data["transactionDate"]),
        local_time=time.fromisoformat(data["localTime"]) if data["localTime"] else None,
        day_period=data["dayPeriod"],
        timezone=data["timezone"],
        is_deleted=False,
    )
    database.add(transaction)
    item.status = "confirmed"
    item.version += 1
    after_state = _transaction_state(transaction)
    database.add(
        TransactionRevision(
            id=uuid4(),
            user_id=user_id,
            transaction_id=transaction.id,
            revision_number=1,
            action="created",
            before_state=None,
            after_state=after_state,
            actor_user_id=user_id,
        )
    )
    try:
        database.flush()
        database.commit()
    except IntegrityError:
        database.rollback()
        winner_by_key = database.scalar(
            select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.idempotency_key == idempotency_key,
            )
        )
        if winner_by_key is not None:
            if winner_by_key.draft_item_id == item_id and winner_by_key.request_hash == confirmation_hash:
                return winner_by_key, True
            raise DraftConfirmationConflict("This confirmation key was used for different input.") from None
        winner_by_item = database.scalar(
            select(Transaction).where(Transaction.user_id == user_id, Transaction.draft_item_id == item_id)
        )
        if winner_by_item is not None:
            raise DraftConfirmationConflict("The draft item has already been confirmed.") from None
        raise
    return transaction, False


def _transaction_state(transaction: Transaction) -> dict[str, str | bool | None]:
    """Return a stable audit snapshot without exposing implementation objects."""
    return {
        "kind": transaction.kind,
        "amount": format(transaction.amount, "f"),
        "currency": transaction.currency,
        "category": transaction.category,
        "note": transaction.note,
        "transactionDate": transaction.transaction_date.isoformat(),
        "localTime": transaction.local_time.isoformat(timespec="minutes") if transaction.local_time else None,
        "dayPeriod": transaction.day_period,
        "timezone": transaction.timezone,
        "isDeleted": transaction.is_deleted,
    }


def update_draft_item(
    database: Session,
    *,
    user_id: UUID,
    draft_id: UUID,
    item_id: UUID,
    expected_version: int,
    changes: dict[str, object],
    commit: bool = True,
) -> DraftItem:
    """Apply explicit corrections to a pending item with optimistic locking."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from fingpt.domain.money import Money
    from fingpt.domain.transaction_time import (
        DayPeriod,
        TransactionTime,
        TransactionTimeError,
        period_for_clock_time,
    )

    item = database.scalar(
        select(DraftItem)
        .join(Draft, Draft.id == DraftItem.draft_id)
        .where(
            Draft.id == draft_id,
            Draft.user_id == user_id,
            DraftItem.id == item_id,
            DraftItem.user_id == user_id,
        )
        .with_for_update()
    )
    if item is None:
        raise LookupError("Draft item was not found.")
    if item.version != expected_version or item.status in {"confirmed", "cancelled"}:
        raise DraftConfirmationConflict("The draft item changed. Reload its preview and try again.")

    data = dict(item.extracted_data)
    for key, value in changes.items():
        if key == "amount" and isinstance(value, Money):
            data["amount"] = format(value.amount, "f")
            data["currency"] = value.currency
        else:
            data[key] = value.value if isinstance(value, (TransactionKind, DayPeriod)) else value
    row_errors = dict(data.get("rowErrors", {}))
    error_fields = set(changes)
    if "amount" in changes:
        error_fields.add("currency")
    for field in error_fields:
        row_errors.pop(field, None)
    required: tuple[str, ...] = ("description", "amount", "currency", "transactionDate", "dayPeriod")
    if {"local_time", "localTime"}.intersection(item.missing_fields):
        required += ("localTime",)
    if all(data.get(field) is not None for field in required):
        row_errors.pop("row", None)
    data["rowErrors"] = row_errors
    local_time = time.fromisoformat(data["localTime"]) if data["localTime"] else None
    if local_time is not None and "dayPeriod" not in changes:
        data["dayPeriod"] = period_for_clock_time(local_time).value
    try:
        ZoneInfo(str(data["timezone"]))
        if local_time is not None and data["dayPeriod"] is not None:
            if period_for_clock_time(local_time) != DayPeriod(data["dayPeriod"]):
                raise TransactionTimeError("Day period does not match the supplied clock time.")
        if data["transactionDate"] is not None and data["dayPeriod"] is not None:
            TransactionTime(
                transaction_date=date.fromisoformat(data["transactionDate"]),
                day_period=DayPeriod(data["dayPeriod"]),
                timezone=data["timezone"],
                local_time=local_time,
            )
    except (ValueError, TypeError, ZoneInfoNotFoundError, TransactionTimeError) as error:
        raise ValueError("Transaction date, time, period, or timezone is invalid.") from error

    missing = [field for field in required if data.get(field) is None]
    item.extracted_data = data
    item.missing_fields = missing
    item.status = "incomplete" if missing or row_errors else "ready"
    item.version += 1
    if commit:
        database.commit()
    return item
