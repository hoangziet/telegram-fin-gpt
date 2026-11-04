"""Owner-scoped read-only chat data and explicit draft clarification rules."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fingpt.domain.text_interpretation import parse_text_request
from fingpt.domain.transaction_time import DEFAULT_TIMEZONE
from fingpt.persistence.models import DraftItem, Transaction
from fingpt.services.drafts import DraftConfirmationConflict, update_draft_item


class NoClarification(ValueError):
    """Raised when a reply contains no supported missing fact."""


def monthly_summary(database: Session, user_id: UUID) -> dict[str, Any]:
    """Supply exact totals for the current local month; never send source evidence or raw notes."""
    today = datetime.now(UTC).astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()
    start = today.replace(day=1)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    rows = database.execute(
        select(Transaction.kind, Transaction.currency, func.sum(Transaction.amount), func.count(Transaction.id))
        .where(
            Transaction.user_id == user_id,
            Transaction.is_deleted.is_(False),
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
        )
        .group_by(Transaction.kind, Transaction.currency)
    )
    return {
        "period": start.strftime("%Y-%m"),
        "timezone": DEFAULT_TIMEZONE,
        "totals": [
            {"kind": kind, "currency": currency, "amount": format(amount, "f"), "count": count}
            for kind, currency, amount, count in rows
        ],
    }


def clarify_item(
    database: Session, *, user_id: UUID, draft_id: UUID, item_id: UUID, version: int, text: str
) -> DraftItem:
    """Apply only explicit missing date/period facts; never confirm or create a transaction."""
    parsed = parse_text_request(text, intake_at=datetime.now(UTC))
    if len(parsed.items) != 1:
        raise NoClarification("Reply about one item at a time.")
    fact = parsed.items[0]
    item = database.scalar(select(DraftItem).where(DraftItem.id == item_id, DraftItem.user_id == user_id))
    if item is None or item.draft_id != draft_id:
        raise LookupError("Draft item was not found.")
    if item.version != version or item.status not in {"incomplete", "ready"}:
        raise DraftConfirmationConflict("Draft changed; reload it before replying.")
    changes: dict[str, object] = {}
    missing = set(item.missing_fields)
    if "transaction_date" in missing and fact.transaction_date:
        changes["transactionDate"] = fact.transaction_date.isoformat()
    if "day_period" in missing and fact.day_period:
        changes["dayPeriod"] = fact.day_period.value
    if "day_period" in missing and not fact.day_period:
        periods = re.findall(r"\b(morning|noon|afternoon|evening)\b", text, flags=re.IGNORECASE)
        if len(periods) == 1:
            changes["dayPeriod"] = periods[0].lower()
    if (
        fact.local_time
        and {"local_time", "localTime"}.intersection(missing)
        and item.extracted_data.get("localTime") is None
    ):
        changes["localTime"] = fact.local_time.isoformat(timespec="minutes")
    if not changes:
        raise NoClarification("Reply with the missing date, period, or clock time.")
    return update_draft_item(
        database,
        user_id=user_id,
        draft_id=draft_id,
        item_id=item_id,
        expected_version=version,
        changes=changes,
    )
