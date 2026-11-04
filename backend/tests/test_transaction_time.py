"""Contract tests for explicit local transaction date and time."""

from datetime import UTC, date, datetime, time

import pytest

from fingpt.domain.transaction_time import (
    DEFAULT_TIMEZONE,
    DayPeriod,
    TransactionTime,
    TransactionTimeError,
    period_for_clock_time,
    resolve_relative_date,
)


@pytest.mark.parametrize(
    ("clock_time", "expected_period"),
    [
        (time(4, 59), DayPeriod.EVENING),
        (time(5, 0), DayPeriod.MORNING),
        (time(10, 59), DayPeriod.MORNING),
        (time(11, 0), DayPeriod.NOON),
        (time(13, 59), DayPeriod.NOON),
        (time(14, 0), DayPeriod.AFTERNOON),
        (time(17, 59), DayPeriod.AFTERNOON),
        (time(18, 0), DayPeriod.EVENING),
    ],
)
def test_clock_time_maps_to_documented_day_period(
    clock_time: time,
    expected_period: DayPeriod,
) -> None:
    assert period_for_clock_time(clock_time) == expected_period


def test_transaction_time_retains_exact_clock_time() -> None:
    transaction_time = TransactionTime(
        transaction_date=date(2026, 11, 2),
        day_period=DayPeriod.MORNING,
        local_time=time(8, 0),
    )

    assert transaction_time.local_time == time(8, 0)
    assert transaction_time.timezone == DEFAULT_TIMEZONE


def test_transaction_time_rejects_period_that_conflicts_with_clock_time() -> None:
    with pytest.raises(TransactionTimeError, match="does not match"):
        TransactionTime(
            transaction_date=date(2026, 11, 2),
            day_period=DayPeriod.NOON,
            local_time=time(8, 0),
        )


def test_relative_date_uses_intake_in_owner_timezone() -> None:
    intake_time = datetime(2026, 11, 1, 18, 30, tzinfo=UTC)

    assert resolve_relative_date(intake_time, 0, DEFAULT_TIMEZONE) == date(2026, 11, 2)


def test_relative_date_rejects_naive_intake_timestamp() -> None:
    with pytest.raises(TransactionTimeError, match="timezone-aware"):
        resolve_relative_date(datetime(2026, 11, 2, 8, 0), 0, DEFAULT_TIMEZONE)


def test_transaction_time_rejects_datetime_as_transaction_date() -> None:
    with pytest.raises(TransactionTimeError, match="date without a clock time"):
        TransactionTime(
            transaction_date=datetime(2026, 11, 2, 8, 0),
            day_period=DayPeriod.MORNING,
        )
