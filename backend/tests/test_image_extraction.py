"""Validation tests for untrusted receipt model output."""

from fingpt.domain.image_extraction import ImageExtractionError, validate_image_extraction


def test_image_proposal_keeps_one_total_and_explicit_time() -> None:
    result = validate_image_extraction(
        {
            "description": "Xem phim",
            "amount": "400",
            "currency": "VND",
            "transactionDate": "2026-10-05",
            "dayPeriod": None,
            "localTime": "08:00",
            "kind": "expense",
        }
    )

    assert result.item["amount"] == "400"
    assert result.item["description"] == "Xem phim"
    assert result.item["dayPeriod"] == "morning"
    assert result.item["localTime"] == "08:00"
    assert result.missing_fields == ()
    assert result.errors == {}


def test_image_proposal_keeps_missing_date_unfilled() -> None:
    result = validate_image_extraction(
        {
            "description": "Ăn trưa",
            "amount": "15000",
            "currency": "VND",
            "transactionDate": None,
            "dayPeriod": None,
            "localTime": None,
            "kind": "expense",
        }
    )

    assert result.item["transactionDate"] is None
    assert result.missing_fields == ("dayPeriod", "transactionDate")


def test_image_proposal_rejects_invalid_money_without_trusting_model() -> None:
    result = validate_image_extraction(
        {
            "description": "Receipt total",
            "amount": "12.345",
            "currency": "USD",
            "transactionDate": "2026-10-05",
            "dayPeriod": "morning",
            "localTime": None,
            "kind": "expense",
        }
    )

    assert result.item["amount"] is None
    assert result.errors["amount"] == "invalid_amount"
    assert "amount" in result.missing_fields


def test_image_proposal_rejects_unexpected_fields() -> None:
    values = {
        "description": "Xem phim",
        "amount": "400",
        "currency": "VND",
        "transactionDate": "2026-10-05",
        "dayPeriod": "morning",
        "localTime": None,
        "kind": "expense",
        "sql": "DROP TABLE transactions",
    }

    try:
        validate_image_extraction(values)
    except ImageExtractionError:
        pass
    else:
        raise AssertionError("unexpected model properties must be rejected")
