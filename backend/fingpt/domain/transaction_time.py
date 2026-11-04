"""Explicit transaction date, clock time, and day-period handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"


class DayPeriod(StrEnum):
    """Coarse day period stored separately from an optional clock time."""

    MORNING = "morning"
    NOON = "noon"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class TransactionTimeError(ValueError):
    """Raised when transaction time data is missing or inconsistent."""


@dataclass(frozen=True, slots=True)
class TransactionTime:
    """A dated transaction with a period and optional exact local clock time."""

    transaction_date: date
    day_period: DayPeriod
    timezone: str = DEFAULT_TIMEZONE
    local_time: time | None = None

    def __post_init__(self) -> None:
        """Validate the timezone and ensure an exact time agrees with its period."""
        if isinstance(self.transaction_date, datetime) or not isinstance(self.transaction_date, date):
            raise TransactionTimeError("Transaction date must be a date without a clock time.")
        try:
            ZoneInfo(self.timezone)
        except (TypeError, ZoneInfoNotFoundError) as exc:
            raise TransactionTimeError(f"Unknown timezone: {self.timezone}") from exc

        if self.local_time is not None:
            if self.local_time.tzinfo is not None:
                raise TransactionTimeError("Clock time must be a local wall time without a timezone.")
            expected_period = period_for_clock_time(self.local_time)
            if expected_period != self.day_period:
                raise TransactionTimeError("Day period does not match the supplied clock time.")


def period_for_clock_time(local_time: time) -> DayPeriod:
    """Derive a period using the project's documented local-time boundaries."""
    clock_minutes = local_time.hour * 60 + local_time.minute
    if 5 * 60 <= clock_minutes < 11 * 60:
        return DayPeriod.MORNING
    if 11 * 60 <= clock_minutes < 14 * 60:
        return DayPeriod.NOON
    if 14 * 60 <= clock_minutes < 18 * 60:
        return DayPeriod.AFTERNOON
    return DayPeriod.EVENING


def resolve_relative_date(reference: datetime, days_ago: int, timezone: str) -> date:
    """Resolve a relative date from intake time in the owner's timezone."""
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise TransactionTimeError("Reference timestamp must be timezone-aware.")
    if isinstance(days_ago, bool) or not isinstance(days_ago, int) or days_ago < 0:
        raise TransactionTimeError("Relative date offset cannot be negative.")
    try:
        local_reference = reference.astimezone(ZoneInfo(timezone))
    except ZoneInfoNotFoundError as exc:
        raise TransactionTimeError(f"Unknown timezone: {timezone}") from exc
    return local_reference.date() - timedelta(days=days_ago)
