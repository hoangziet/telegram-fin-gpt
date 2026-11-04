"""Behavioral tests for conservative Vietnamese transaction text interpretation."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from fingpt.domain.text_interpretation import parse_text_request

INTAKE_AT = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
OWNER_TIMEZONE = "Asia/Ho_Chi_Minh"


def test_splits_several_items_and_applies_explicit_relative_date() -> None:
    result = parse_text_request(
        "Hôm qua ăn phở 50k, xem phim 100k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    assert [item.description for item in result.items] == ["phở", "xem phim"]
    assert [item.money.amount for item in result.items if item.money is not None] == [
        50_000,
        100_000,
    ]
    assert all(
        item.transaction_date is not None and item.transaction_date.isoformat() == "2026-11-01"
        for item in result.items
    )
    assert all(item.day_period is None for item in result.items)
    assert all(item.missing_fields == ("day_period", "local_time") for item in result.items)


def test_one_action_and_one_total_keep_a_comma_separated_meal_together() -> None:
    result = parse_text_request(
        "Sáng nay ăn bánh xèo, bánh căn bánh bèo 30k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    assert len(result.items) == 1
    item = result.items[0]
    assert item.description == "bánh xèo, bánh căn bánh bèo"
    assert item.money is not None and item.money.amount == 30_000
    assert item.transaction_date is not None
    assert item.day_period is not None and item.day_period.value == "morning"
    assert item.missing_fields == ("local_time",)


def test_single_amount_is_not_guessed_as_the_last_of_multiple_actions() -> None:
    result = parse_text_request(
        "Sáng nay ăn phở, uống trà 30k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    assert len(result.items) == 2
    assert all(item.money is None for item in result.items)
    assert all("amount" in item.missing_fields for item in result.items)


def test_specific_clock_time_is_retained_without_assuming_transaction_date() -> None:
    result = parse_text_request(
        "8h sáng ăn bánh mì 15k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    item = result.items[0]
    assert item.description == "bánh mì"
    assert item.money is not None and item.money.amount == 15_000
    assert item.transaction_date is None
    assert item.local_time == time(8, 0)
    assert item.day_period is not None
    assert item.day_period.value == "morning"
    assert item.missing_fields == ("transaction_date",)


def test_period_and_relative_date_complete_the_review_fields() -> None:
    result = parse_text_request(
        "Sáng hôm qua ăn phở 50k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    item = result.items[0]
    assert item.transaction_date is not None
    assert item.transaction_date.isoformat() == "2026-11-01"
    assert item.day_period is not None
    assert item.day_period.value == "morning"
    assert item.local_time is None
    assert item.missing_fields == ("local_time",)


def test_plain_amount_does_not_gain_an_assumed_thousand_multiplier() -> None:
    result = parse_text_request(
        "xem phim 100",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    assert result.items[0].money is not None
    assert result.items[0].money.amount == 100


def test_clock_time_without_period_is_retained_but_period_remains_missing() -> None:
    result = parse_text_request(
        "8h ăn bánh mì 15k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    item = result.items[0]
    assert item.local_time == time(8, 0)
    assert item.day_period is None
    assert item.missing_fields == ("transaction_date", "day_period")


def test_relative_date_uses_owner_local_intake_date() -> None:
    local_intake = datetime(2026, 11, 1, 18, 0, tzinfo=ZoneInfo(OWNER_TIMEZONE))
    result = parse_text_request(
        "Hôm qua ăn phở 50k",
        intake_at=local_intake,
        timezone=OWNER_TIMEZONE,
    )

    assert result.items[0].transaction_date is not None
    assert result.items[0].transaction_date.isoformat() == "2026-10-31"


def test_date_and_clock_digits_are_not_misread_as_money() -> None:
    result = parse_text_request(
        "Ngày 01/11/2026 8h sáng bánh mì",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    item = result.items[0]
    assert item.transaction_date is not None
    assert item.transaction_date.isoformat() == "2026-11-01"
    assert item.local_time == time(8, 0)
    assert item.description == "bánh mì"
    assert item.money is None
    assert item.missing_fields == ("amount",)


def test_bare_quantity_inside_description_is_not_misread_as_amount() -> None:
    result = parse_text_request(
        "Sáng hôm qua ăn 2 quả trứng",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    item = result.items[0]
    assert item.description == "2 quả trứng"
    assert item.money is None
    assert item.missing_fields == ("local_time", "amount")


def test_plain_list_creates_three_independent_expense_proposals() -> None:
    result = parse_text_request(
        "xem phim 100, bắp 100, nước 200",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    assert len(result.items) == 3
    assert [item.description for item in result.items] == ["xem phim", "bắp", "nước"]
    assert [item.money.amount for item in result.items if item.money is not None] == [100, 100, 200]
    assert all(item.kind.value == "expense" for item in result.items)
    assert all(item.missing_fields == ("transaction_date", "day_period", "local_time") for item in result.items)


def test_income_language_marks_a_salary_as_income() -> None:
    result = parse_text_request(
        "Hôm nay nhận lương 20tr",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    item = result.items[0]
    assert item.kind.value == "income"
    assert item.money is not None and item.money.amount == 20_000_000
    assert item.transaction_date is not None
    assert item.transaction_date.isoformat() == "2026-11-02"
    assert item.missing_fields == ("day_period", "local_time")


def test_unprefixed_explicit_calendar_date_is_recognized() -> None:
    result = parse_text_request(
        "01/11/2026 sáng ăn phở 50k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    item = result.items[0]
    assert item.transaction_date is not None
    assert item.transaction_date.isoformat() == "2026-11-01"
    assert item.description == "phở"


def test_evening_qualifier_converts_to_local_24_hour_clock() -> None:
    result = parse_text_request(
        "8h tối xem phim 100k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    item = result.items[0]
    assert item.local_time == time(20, 0)
    assert item.day_period is not None
    assert item.day_period.value == "evening"


def test_late_night_qualifier_does_not_turn_evening_into_morning() -> None:
    result = parse_text_request(
        "9h đêm xem phim 100k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    item = result.items[0]
    assert item.local_time == time(21, 0)
    assert item.day_period is not None
    assert item.day_period.value == "evening"


def test_late_morning_noon_qualifier_does_not_become_evening() -> None:
    result = parse_text_request(
        "11h trưa ăn phở 50k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    item = result.items[0]
    assert item.local_time == time(11, 0)
    assert item.day_period is not None
    assert item.day_period.value == "noon"


def test_sang_nay_recognizes_today_and_morning_period_cleanly() -> None:
    result = parse_text_request(
        "Sáng nay ăn bánh mì 15k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    assert len(result.items) == 1
    item = result.items[0]
    assert item.description == "bánh mì"
    assert item.money is not None and item.money.amount == 15_000
    assert item.transaction_date is not None
    assert item.transaction_date.isoformat() == "2026-11-02"
    assert item.day_period is not None
    assert item.day_period.value == "morning"
    assert item.missing_fields == ("local_time",)


def test_compound_relative_day_periods_and_colloquial_nay() -> None:
    result = parse_text_request(
        "Trưa nay ăn cơm tấm 35k, Tối qua ăn ốc 100k, nay uống cà phê 20k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    assert len(result.items) == 3

    item1 = result.items[0]
    assert item1.description == "cơm tấm"
    assert item1.money is not None and item1.money.amount == 35_000
    assert item1.transaction_date is not None
    assert item1.transaction_date.isoformat() == "2026-11-02"
    assert item1.day_period is not None
    assert item1.day_period.value == "noon"
    assert item1.missing_fields == ("local_time",)

    item2 = result.items[1]
    assert item2.description == "ốc"
    assert item2.money is not None and item2.money.amount == 100_000
    assert item2.transaction_date is not None
    assert item2.transaction_date.isoformat() == "2026-11-01"
    assert item2.day_period is not None
    assert item2.day_period.value == "evening"
    assert item2.missing_fields == ("local_time",)

    item3 = result.items[2]
    assert item3.description == "cà phê"
    assert item3.money is not None and item3.money.amount == 20_000
    assert item3.transaction_date is not None
    assert item3.transaction_date.isoformat() == "2026-11-02"
    assert item3.day_period is None
    assert item3.missing_fields == ("day_period", "local_time")


def test_prepositional_qua_and_demonstrative_kia_do_not_falsify_dates() -> None:
    result = parse_text_request(
        "mua qua shopee 100k, mua cái bàn kia 500k",
        intake_at=INTAKE_AT,
        timezone=OWNER_TIMEZONE,
    )

    assert len(result.items) == 2
    assert result.items[0].transaction_date is None
    assert result.items[0].description == "qua shopee"
    assert result.items[1].transaction_date is None
    assert result.items[1].description == "cái bàn kia"
