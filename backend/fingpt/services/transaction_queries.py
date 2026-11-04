"""Owner-scoped search and aggregate reporting over confirmed records."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fingpt.persistence.models import Transaction


def search_transactions(
    database: Session,
    *,
    user_id: UUID,
    start_date: date | None,
    end_date: date | None,
    kind: str | None,
    query: str | None,
    include_deleted: bool = False,
    limit: int,
    offset: int,
) -> tuple[list[Transaction], int]:
    """Search visible transactions with owner and optional filter predicates."""
    statement = select(Transaction).where(Transaction.user_id == user_id)
    if not include_deleted:
        statement = statement.where(Transaction.is_deleted.is_(False))
    if start_date is not None:
        statement = statement.where(Transaction.transaction_date >= start_date)
    if end_date is not None:
        statement = statement.where(Transaction.transaction_date <= end_date)
    if kind is not None:
        statement = statement.where(Transaction.kind == kind)
    if query:
        statement = statement.where(Transaction.note.contains(query, autoescape=True))
    total = database.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = list(
        database.scalars(
            statement.order_by(Transaction.transaction_date.desc(), Transaction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total


def transaction_totals(
    database: Session,
    *,
    user_id: UUID,
    start_date: date,
    end_date: date,
    kind: str | None = None,
    query: str | None = None,
) -> list[tuple[str, str, Decimal]]:
    """Aggregate owner-owned active records matching the search filters, independent of pagination."""
    statement = (
        select(Transaction.kind, Transaction.currency, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == user_id,
            Transaction.is_deleted.is_(False),
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date,
        )
        .group_by(Transaction.kind, Transaction.currency)
        .order_by(Transaction.currency, Transaction.kind)
    )
    if kind is not None:
        statement = statement.where(Transaction.kind == kind)
    if query:
        statement = statement.where(Transaction.note.contains(query, autoescape=True))
    return [(kind, currency, total) for kind, currency, total in database.execute(statement)]
