"""Validate untrusted single-total receipt extraction into a draft proposal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any

from fingpt.domain.money import SUPPORTED_CURRENCIES, Money, MoneyValidationError
from fingpt.domain.text_interpretation import TransactionKind
from fingpt.domain.transaction_time import DayPeriod, TransactionTime, TransactionTimeError, period_for_clock_time

TIMEZONE = "Asia/Ho_Chi_Minh"
_FIELDS = {
    "amount",
    "currency",
    "dayPeriod",
    "description",
    "kind",
    "localTime",
    "transactionDate",
}


class ImageExtractionError(ValueError):
    """Raised when model output has an unsupported shape."""


@dataclass(frozen=True, slots=True)
class ImageProposal:
    """One normalized transaction total with safe validation errors."""

    item: dict[str, Any]
    missing_fields: tuple[str, ...]
    errors: dict[str, str]


def validate_image_extraction(values: dict[str, Any]) -> ImageProposal:
    """Validate model output without filling missing dates or transaction facts."""
    if set(values) != _FIELDS:
        raise ImageExtractionError("Image extraction returned an invalid field set.")
    if any(value is not None and not isinstance(value, str) for value in values.values()):
        raise ImageExtractionError("Image extraction fields must be strings or null.")

    errors: dict[str, str] = {}
    item: dict[str, Any] = {
        "description": None,
        "kind": "expense",
        "amount": None,
        "currency": None,
        "category": "uncategorized",
        "transactionDate": None,
        "dayPeriod": None,
        "localTime": None,
        "timezone": TIMEZONE,
    }

    description = values["description"]
    if isinstance(description, str) and description.strip() and len(description.strip()) <= 256:
        item["description"] = description.strip()
    elif description is not None:
        errors["description"] = "invalid_description"

    raw_kind = values["kind"]
    if raw_kind is not None:
        try:
            item["kind"] = TransactionKind(raw_kind).value
        except ValueError:
            errors["kind"] = "invalid_kind"

    raw_currency = values["currency"]
    currency = raw_currency.upper() if isinstance(raw_currency, str) else None
    item["currency"] = currency
    raw_amount = values["amount"]
    if currency not in SUPPORTED_CURRENCIES:
        if currency is not None:
            errors["currency"] = "invalid_currency"
    elif raw_amount is not None:
        try:
            item["amount"] = format(Money.parse(raw_amount, currency).amount, "f")
        except MoneyValidationError:
            errors["amount"] = "invalid_amount"

    raw_date = values["transactionDate"]
    if raw_date is not None:
        try:
            item["transactionDate"] = date.fromisoformat(raw_date).isoformat()
        except ValueError:
            errors["transactionDate"] = "invalid_date"

    raw_time = values["localTime"]
    if raw_time is not None:
        try:
            parsed_time = time.fromisoformat(raw_time)
            if parsed_time.tzinfo is not None or parsed_time.second or parsed_time.microsecond:
                raise ValueError
            item["localTime"] = parsed_time.isoformat(timespec="minutes")
        except ValueError:
            errors["localTime"] = "invalid_local_time"

    raw_period = values["dayPeriod"]
    if raw_period is not None:
        try:
            item["dayPeriod"] = DayPeriod(raw_period).value
        except ValueError:
            errors["dayPeriod"] = "invalid_day_period"
    elif item["localTime"] is not None:
        item["dayPeriod"] = period_for_clock_time(time.fromisoformat(item["localTime"])).value

    if item["localTime"] is not None and item["dayPeriod"] is not None:
        clock_period = period_for_clock_time(time.fromisoformat(item["localTime"]))
        if clock_period != DayPeriod(item["dayPeriod"]):
            errors["dayPeriod"] = "conflicting_time_period"
            item["dayPeriod"] = None
    if item["transactionDate"] is not None and item["dayPeriod"] is not None:
        try:
            TransactionTime(date.fromisoformat(item["transactionDate"]), DayPeriod(item["dayPeriod"]), TIMEZONE)
        except (ValueError, TransactionTimeError):
            errors["transactionDate"] = "invalid_transaction_time"

    missing_fields = tuple(
        sorted(
            field
            for field in ("amount", "currency", "description", "transactionDate", "dayPeriod")
            if item[field] is None
        )
    )
    item["rowErrors"] = errors
    return ImageProposal(item, missing_fields, errors)
