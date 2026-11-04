"""Contract tests for exact transaction money values."""

from decimal import Decimal

import pytest

from fingpt.domain.money import Money, MoneyValidationError


def test_parse_vnd_without_binary_float_or_rounding() -> None:
    money = Money.parse("50000", "VND")

    assert money.amount == Decimal("50000")
    assert money.currency == "VND"
    assert money.as_database_value() == Decimal("50000")


def test_reject_float_input() -> None:
    with pytest.raises(MoneyValidationError, match="decimal string"):
        Money.parse(50000)  # type: ignore[arg-type]


def test_reject_fractional_vnd() -> None:
    with pytest.raises(MoneyValidationError, match="more precision"):
        Money.parse("10.5", "VND")


def test_reject_non_positive_amount() -> None:
    with pytest.raises(MoneyValidationError, match="greater than zero"):
        Money.parse("0", "VND")


def test_constructor_cannot_bypass_amount_validation() -> None:
    with pytest.raises(MoneyValidationError, match="greater than zero"):
        Money(amount=Decimal("-1"), currency="VND")
