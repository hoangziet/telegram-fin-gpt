"""Durable preview, confirmation, and undo for existing transactions."""

from __future__ import annotations

import hashlib
import json
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fingpt.domain.money import Money
from fingpt.domain.text_interpretation import TransactionKind
from fingpt.domain.transaction_time import DayPeriod, TransactionTime, period_for_clock_time
from fingpt.persistence.models import Transaction, TransactionChange, TransactionRevision
from fingpt.services.drafts import DraftConfirmationConflict


def create_change_proposal(
    database: Session,
    *,
    user_id: UUID,
    transaction_id: UUID,
    expected_version: int,
    action: str,
    changes: dict[str, object],
    idempotency_key: str,
) -> tuple[TransactionChange, bool]:
    """Persist a fully validated proposal without changing the transaction."""
    request_hash = _proposal_hash(transaction_id, expected_version, action, changes)
    existing = database.scalar(
        select(TransactionChange).where(
            TransactionChange.user_id == user_id,
            TransactionChange.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise DraftConfirmationConflict("This proposal key was used for different input.")
        return existing, True

    transaction = database.scalar(
        select(Transaction).where(Transaction.id == transaction_id, Transaction.user_id == user_id)
    )
    if transaction is None:
        raise LookupError("Transaction was not found.")
    if transaction.version != expected_version:
        raise DraftConfirmationConflict("The transaction changed. Reload it before proposing an update.")
    if transaction.is_deleted and action != "restored":
        raise DraftConfirmationConflict("A deleted transaction cannot be changed.")
    proposed = _state(transaction)
    if action == "deleted":
        proposed["isDeleted"] = True
    elif action == "restored":
        proposed = _undo_state(database, transaction, proposed)
        action = "deleted" if proposed["isDeleted"] else "restored"
    else:
        _apply_changes(proposed, changes)
        _validate_state(proposed)
    proposal = TransactionChange(
        id=uuid4(),
        user_id=user_id,
        transaction_id=transaction.id,
        action=action,
        expected_transaction_version=expected_version,
        proposed_state=proposed,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    database.add(proposal)
    try:
        database.commit()
    except IntegrityError:
        database.rollback()
        winner = database.scalar(
            select(TransactionChange).where(
                TransactionChange.user_id == user_id,
                TransactionChange.idempotency_key == idempotency_key,
            )
        )
        if winner is None:
            raise
        if winner.request_hash != request_hash:
            raise DraftConfirmationConflict("This proposal key was used for different input.") from None
        return winner, True
    return proposal, False


def confirm_change(
    database: Session,
    *,
    user_id: UUID,
    change_id: UUID,
    expected_version: int,
    confirmation_key: str,
) -> tuple[Transaction, TransactionChange, bool]:
    """Apply a confirmed proposal with stale-state, audit, and retry guards."""
    prior_confirmation = database.scalar(
        select(TransactionChange).where(
            TransactionChange.user_id == user_id,
            TransactionChange.confirmation_key == confirmation_key,
        )
    )
    if prior_confirmation is not None and prior_confirmation.id != change_id:
        raise DraftConfirmationConflict("This confirmation key was used for another proposal.")
    change = database.scalar(
        select(TransactionChange)
        .where(TransactionChange.id == change_id, TransactionChange.user_id == user_id)
        .with_for_update()
    )
    if change is None:
        raise LookupError("Change proposal was not found.")
    confirmation_hash = hashlib.sha256(f"{change.id}:{expected_version}".encode("ascii")).hexdigest()
    if change.status == "confirmed":
        if change.confirmation_key == confirmation_key and change.confirmation_hash == confirmation_hash:
            transaction = database.scalar(
                select(Transaction).where(
                    Transaction.id == change.transaction_id,
                    Transaction.user_id == user_id,
                )
            )
            if transaction is None:
                raise RuntimeError("Confirmed transaction no longer exists.")
            return transaction, change, True
        raise DraftConfirmationConflict("This proposal was already confirmed with different input.")
    if change.status != "proposed" or change.version != expected_version:
        raise DraftConfirmationConflict("The proposal changed. Reload it before confirming.")
    transaction = database.scalar(
        select(Transaction)
        .where(Transaction.id == change.transaction_id, Transaction.user_id == user_id)
        .with_for_update()
    )
    if transaction is None:
        raise LookupError("Transaction was not found.")
    if transaction.version != change.expected_transaction_version:
        change.status = "stale"
        database.commit()
        raise DraftConfirmationConflict("The transaction changed after this preview was created.")

    before = _state(transaction)
    _apply_state(transaction, change.proposed_state)
    transaction.version += 1
    revision_number = (
        database.scalar(
            select(func.max(TransactionRevision.revision_number)).where(
                TransactionRevision.transaction_id == transaction.id,
                TransactionRevision.user_id == user_id,
            )
        )
        or 0
    )
    database.add(
        TransactionRevision(
            id=uuid4(),
            user_id=user_id,
            transaction_id=transaction.id,
            revision_number=revision_number + 1,
            action=change.action,
            before_state=before,
            after_state=_state(transaction),
            actor_user_id=user_id,
        )
    )
    change.status = "confirmed"
    change.confirmation_key = confirmation_key
    change.confirmation_hash = confirmation_hash
    change.version += 1
    database.commit()
    return transaction, change, False


def get_change(database: Session, user_id: UUID, change_id: UUID) -> TransactionChange | None:
    """Load a change proposal only for its owner."""
    return database.scalar(
        select(TransactionChange).where(
            TransactionChange.id == change_id,
            TransactionChange.user_id == user_id,
        )
    )


def cancel_change(
    database: Session,
    *,
    user_id: UUID,
    change_id: UUID,
    expected_version: int,
) -> tuple[TransactionChange, bool]:
    """Cancel an owned preview without changing its transaction."""
    change = database.scalar(
        select(TransactionChange)
        .where(TransactionChange.id == change_id, TransactionChange.user_id == user_id)
        .with_for_update()
    )
    if change is None:
        raise LookupError("Change proposal was not found.")
    if change.status == "cancelled" and change.version == expected_version + 1:
        return change, True
    if change.status != "proposed" or change.version != expected_version:
        raise DraftConfirmationConflict("The proposal changed. Reload it before cancelling.")
    change.status = "cancelled"
    change.version += 1
    database.commit()
    return change, False


def _proposal_hash(transaction_id: UUID, version: int, action: str, changes: dict[str, object]) -> str:
    """Fingerprint proposal intent, including exact validated field values."""
    normalized: dict[str, object] = {}
    for key, value in changes.items():
        normalized[key] = str(value.amount) + ":" + value.currency if isinstance(value, Money) else str(value)
    payload = json.dumps(
        {"transactionId": str(transaction_id), "version": version, "action": action, "changes": normalized},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _apply_changes(state: dict[str, object], changes: dict[str, object]) -> None:
    """Apply normalized user edits to a full transaction snapshot."""
    names = {
        "description": "note",
        "kind": "kind",
        "amount": "amount",
        "category": "category",
        "transactionDate": "transactionDate",
        "dayPeriod": "dayPeriod",
        "localTime": "localTime",
        "timezone": "timezone",
    }
    for key, value in changes.items():
        target = names[key]
        if key == "amount" and isinstance(value, Money):
            state["amount"] = format(value.amount, "f")
            state["currency"] = value.currency
        elif key == "kind" and isinstance(value, TransactionKind):
            state[target] = value.value
        elif key == "dayPeriod" and isinstance(value, DayPeriod):
            state[target] = value.value
        elif key == "localTime" and "dayPeriod" not in changes and value is not None:
            state[target] = value
            state["dayPeriod"] = period_for_clock_time(time.fromisoformat(str(value))).value
        else:
            state[target] = value


def _validate_state(state: dict[str, object]) -> None:
    """Revalidate complete transaction facts after applying proposal edits."""
    amount = Money.parse(str(state["amount"]), str(state["currency"]))
    kind = TransactionKind(str(state["kind"]))
    category = state["category"]
    note = state["note"]
    if not isinstance(category, str) or not category.strip() or len(category) > 128:
        raise ValueError("Category must contain 1–128 characters.")
    if note is not None and (not isinstance(note, str) or len(note) > 2_000):
        raise ValueError("Description must be 2,000 characters or fewer.")
    state["amount"] = format(amount.amount, "f")
    state["kind"] = kind.value
    transaction_time = TransactionTime(
        transaction_date=date.fromisoformat(str(state["transactionDate"])),
        day_period=DayPeriod(str(state["dayPeriod"])),
        timezone=str(state["timezone"]),
        local_time=time.fromisoformat(str(state["localTime"])) if state["localTime"] else None,
    )
    state["transactionDate"] = transaction_time.transaction_date.isoformat()


def _undo_state(
    database: Session,
    transaction: Transaction,
    current: dict[str, object],
) -> dict[str, object]:
    """Build an inverse proposal from the latest immutable revision."""
    latest = database.scalar(
        select(TransactionRevision)
        .where(
            TransactionRevision.transaction_id == transaction.id,
            TransactionRevision.user_id == transaction.user_id,
        )
        .order_by(TransactionRevision.revision_number.desc())
        .limit(1)
    )
    if latest is None:
        raise DraftConfirmationConflict("This transaction has no revision to undo.")
    if latest.before_state is not None:
        return dict(latest.before_state)
    restored = dict(current)
    restored["isDeleted"] = True
    return restored


def _state(transaction: Transaction) -> dict[str, object]:
    """Serialize an exact transaction state for immutable history."""
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


def _apply_state(transaction: Transaction, state: dict[str, object]) -> None:
    """Copy a validated immutable snapshot onto a transaction row."""
    transaction.kind = str(state["kind"])
    transaction.amount = Decimal(str(state["amount"]))
    transaction.currency = str(state["currency"])
    transaction.category = str(state["category"])
    transaction.note = str(state["note"]) if state["note"] is not None else None
    transaction.transaction_date = date.fromisoformat(str(state["transactionDate"]))
    transaction.local_time = time.fromisoformat(str(state["localTime"])) if state["localTime"] else None
    transaction.day_period = str(state["dayPeriod"])
    transaction.timezone = str(state["timezone"])
    transaction.is_deleted = bool(state["isDeleted"])
