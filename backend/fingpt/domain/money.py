"""Exact currency amounts with validation and no implicit rounding."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_CURRENCY_SCALE = {
    "BHD": 3,
    "EUR": 2,
    "JPY": 0,
    "KWD": 3,
    "USD": 2,
    "VND": 0,
}
SUPPORTED_CURRENCIES = frozenset(_CURRENCY_SCALE)
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class MoneyValidationError(ValueError):
    """Raised when an amount or currency cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class Money:
    """A positive exact amount in a supported ISO currency."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        """Enforce the same invariants for direct construction and parsing."""
        if not isinstance(self.amount, Decimal):
            raise MoneyValidationError("Amount must be represented as Decimal.")
        if not isinstance(self.currency, str) or not _CURRENCY_PATTERN.fullmatch(self.currency):
            raise MoneyValidationError("Currency must be a three-letter uppercase code.")
        if self.currency not in _CURRENCY_SCALE:
            raise MoneyValidationError(f"Currency {self.currency} has no configured precision.")
        if not self.amount.is_finite() or self.amount <= 0:
            raise MoneyValidationError("Amount must be finite and greater than zero.")
        if self.amount.adjusted() > 14:
            raise MoneyValidationError("Amount exceeds the supported precision.")

        try:
            normalized = self.amount.quantize(Decimal(1).scaleb(-_CURRENCY_SCALE[self.currency]))
        except InvalidOperation as exc:
            raise MoneyValidationError("Amount exceeds the supported precision.") from exc
        if self.amount != normalized:
            raise MoneyValidationError(f"Amount has more precision than {self.currency} supports.")

    @classmethod
    def parse(cls, raw_amount: str, currency: str = "VND") -> Money:
        """Parse a decimal string without accepting binary floats or rounding."""
        if not isinstance(raw_amount, str) or not raw_amount.strip():
            raise MoneyValidationError("Amount must be provided as a decimal string.")

        try:
            amount = Decimal(raw_amount)
        except InvalidOperation as exc:
            raise MoneyValidationError("Amount is not a valid decimal value.") from exc

        return cls(amount=amount, currency=currency)

    def as_database_value(self) -> Decimal:
        """Return the exact decimal value for a fixed-precision database column."""
        return self.amount
