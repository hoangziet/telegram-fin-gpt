"""Deterministic spending-trend aggregation; no model call lives in this module.

Every figure here comes straight from the database. `adapters/foundry_insight.py`
may phrase these numbers into a sentence later, but it never computes or changes
them, and this module never sends raw notes or evidence anywhere.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fingpt.domain.transaction_time import DEFAULT_TIMEZONE
from fingpt.persistence.models import Transaction

_ZERO = Decimal("0.0000")


def spending_trend(database: Session, user_id: UUID) -> dict[str, Any]:
    """Compare this month's and last month's owner-scoped expense totals by category."""
    today = datetime.now(UTC).astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()
    current_start = today.replace(day=1)
    current_end = _next_month(current_start)
    previous_start = _previous_month(current_start)

    current = _category_totals(database, user_id, current_start, current_end)
    previous = _category_totals(database, user_id, previous_start, current_start)

    categories = [
        {
            "category": category,
            "currency": currency,
            "currentAmount": format(current.get((category, currency), _ZERO), "f"),
            "previousAmount": format(previous.get((category, currency), _ZERO), "f"),
            "percentChange": _percent_change(
                previous.get((category, currency), _ZERO),
                current.get((category, currency), _ZERO),
            ),
        }
        for category, currency in sorted(set(current) | set(previous))
    ]
    categories.sort(key=lambda row: Decimal(str(row["currentAmount"])), reverse=True)
    return {
        "currentPeriod": current_start.strftime("%Y-%m"),
        "previousPeriod": previous_start.strftime("%Y-%m"),
        "timezone": DEFAULT_TIMEZONE,
        "categories": categories,
    }


def _category_totals(database: Session, user_id: UUID, start: date, end: date) -> dict[tuple[str, str], Decimal]:
    """Sum active expense amounts by category and currency within [start, end)."""
    rows = database.execute(
        select(Transaction.category, Transaction.currency, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == user_id,
            Transaction.is_deleted.is_(False),
            Transaction.kind == "expense",
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
        )
        .group_by(Transaction.category, Transaction.currency)
    )
    return {(category, currency): amount for category, currency, amount in rows}


def _percent_change(previous: Decimal, current: Decimal) -> float | None:
    """Return a signed percent change, or None when there is no prior baseline."""
    if previous == 0:
        return None
    return float((current - previous) / previous * 100)


def _next_month(start: date) -> date:
    """Return the first day of the month after `start`."""
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


def _previous_month(start: date) -> date:
    """Return the first day of the month before `start`."""
    return start.replace(year=start.year - 1, month=12) if start.month == 1 else start.replace(month=start.month - 1)
