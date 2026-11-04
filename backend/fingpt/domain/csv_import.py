"""Strict, row-preserving CSV mapping for transaction import previews."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, time
from typing import Any

from fingpt.domain.money import SUPPORTED_CURRENCIES, Money, MoneyValidationError
from fingpt.domain.text_interpretation import TransactionKind
from fingpt.domain.transaction_time import DayPeriod, TransactionTime, TransactionTimeError, period_for_clock_time

MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_CSV_ROWS = 10_000
MAX_CELL_CHARACTERS = 2_048
ALLOWED_FIELDS = {
    "amount",
    "category",
    "currency",
    "dayPeriod",
    "description",
    "kind",
    "localTime",
    "transactionDate",
}
REQUIRED_FIELDS = {"amount", "description"}
_PERIOD_ALIASES = {"sang": "morning", "trua": "noon", "chieu": "afternoon", "toi": "evening"}


class CsvImportError(ValueError):
    """Raised when a CSV file or its column mapping cannot be interpreted."""


@dataclass(frozen=True, slots=True)
class CsvImportRow:
    """One source row with normalized candidate values and safe validation errors."""

    row_number: int
    item: dict[str, Any]
    missing_fields: tuple[str, ...]
    errors: tuple[str, ...]


def parse_csv_import(content: bytes, *, mapping: dict[str, str]) -> tuple[CsvImportRow, ...]:
    """Map every data row and retain invalid rows for explicit preview and correction."""
    if not content or len(content) > MAX_CSV_BYTES:
        raise CsvImportError("CSV file must contain data and be at most 5 MiB.")
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        raise CsvImportError("CSV file must use UTF-8 encoding.") from error
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames
        if not headers or any(not header or not header.strip() for header in headers):
            raise CsvImportError("CSV must have a non-empty header row.")
        normalized_headers = [header.strip() for header in headers]
        if len(set(normalized_headers)) != len(normalized_headers):
            raise CsvImportError("CSV column names must be unique.")
        if set(mapping) - ALLOWED_FIELDS or not REQUIRED_FIELDS.issubset(mapping):
            raise CsvImportError("Map amount and description, with only supported fields.")
        if len(set(mapping.values())) != len(mapping.values()):
            raise CsvImportError("Each CSV column can map to only one transaction field.")
        if any(column not in normalized_headers for column in mapping.values()):
            raise CsvImportError("A mapped CSV column is not present in the header.")
        reader.fieldnames = normalized_headers
        rows: list[CsvImportRow] = []
        for row_number, source_row in enumerate(reader, start=2):
            if row_number > MAX_CSV_ROWS + 1:
                raise CsvImportError("CSV cannot contain more than 10,000 data rows.")
            if None in source_row or any(len(value) > MAX_CELL_CHARACTERS for value in source_row.values() if value):
                rows.append(_error_row(row_number, "invalid_row_shape"))
                continue
            rows.append(_map_row(row_number, source_row, mapping))
    except csv.Error as error:
        raise CsvImportError("CSV file structure is invalid.") from error
    if not rows:
        raise CsvImportError("CSV must contain at least one data row.")
    return tuple(rows)


def _map_row(row_number: int, source: dict[str, str | None], mapping: dict[str, str]) -> CsvImportRow:
    """Validate one row without inferring absent dates or silently dropping input."""
    item: dict[str, Any] = {
        "rowNumber": row_number,
        "kind": TransactionKind.EXPENSE.value,
        "amount": None,
        "currency": "VND",
        "category": "uncategorized",
        "description": None,
        "transactionDate": None,
        "dayPeriod": None,
        "localTime": None,
        "timezone": "Asia/Ho_Chi_Minh",
    }
    errors: dict[str, str] = {}
    values = {field: (source.get(column) or "").strip() for field, column in mapping.items()}

    currency = values.get("currency", "VND").upper()
    item["currency"] = currency or "VND"
    if not currency:
        errors["currency"] = "missing_currency"
    elif currency not in SUPPORTED_CURRENCIES:
        errors["currency"] = "invalid_currency"
    raw_amount = values.get("amount", "")
    if raw_amount and currency in SUPPORTED_CURRENCIES:
        try:
            item["amount"] = format(Money.parse(raw_amount, currency).amount, "f")
        except MoneyValidationError:
            errors["amount"] = "invalid_amount"
    elif not raw_amount:
        errors["amount"] = "missing_amount"
    else:
        errors["amount"] = "invalid_amount"

    description = values.get("description", "")
    if description and len(description) <= 256:
        item["description"] = description
    elif description:
        errors["description"] = "description_too_long"
    else:
        errors["description"] = "missing_description"

    raw_kind = values.get("kind", "").lower()
    if raw_kind:
        try:
            item["kind"] = TransactionKind(raw_kind).value
        except ValueError:
            errors["kind"] = "invalid_kind"

    category = values.get("category", "")
    if category and len(category) <= 128:
        item["category"] = category
    elif category:
        errors["category"] = "category_too_long"

    raw_date = values.get("transactionDate", "")
    if raw_date:
        try:
            item["transactionDate"] = date.fromisoformat(raw_date).isoformat()
        except ValueError:
            errors["transactionDate"] = "invalid_date"
    else:
        errors["transactionDate"] = "missing_date"

    raw_time = values.get("localTime", "")
    if raw_time:
        try:
            local_time = time.fromisoformat(raw_time)
            if local_time.tzinfo is not None or local_time.second or local_time.microsecond:
                raise ValueError
            item["localTime"] = local_time.isoformat(timespec="minutes")
        except ValueError:
            errors["localTime"] = "invalid_local_time"

    raw_period = values.get("dayPeriod", "").lower()
    period_value = _PERIOD_ALIASES.get(raw_period, raw_period)
    if period_value:
        try:
            item["dayPeriod"] = DayPeriod(period_value).value
        except ValueError:
            errors["dayPeriod"] = "invalid_day_period"
    elif item["localTime"] is not None:
        item["dayPeriod"] = period_for_clock_time(time.fromisoformat(item["localTime"])).value
    else:
        errors["dayPeriod"] = "missing_day_period"

    if item["localTime"] is not None and item["dayPeriod"] is not None:
        try:
            if period_for_clock_time(time.fromisoformat(item["localTime"])) != DayPeriod(item["dayPeriod"]):
                errors["dayPeriod"] = "conflicting_time_period"
                item["dayPeriod"] = None
        except ValueError:
            errors["dayPeriod"] = "invalid_day_period"
    if item["transactionDate"] is not None and item["dayPeriod"] is not None:
        try:
            TransactionTime(
                date.fromisoformat(item["transactionDate"]),
                DayPeriod(item["dayPeriod"]),
                item["timezone"],
            )
        except (ValueError, TransactionTimeError):
            errors["transactionDate"] = "invalid_transaction_time"

    missing = tuple(
        sorted(field for field in ("amount", "description", "transactionDate", "dayPeriod") if item[field] is None)
    )
    item["rowErrors"] = errors
    return CsvImportRow(row_number, item, missing, tuple(errors.values()))


def _error_row(row_number: int, error: str) -> CsvImportRow:
    """Retain a structurally malformed source row as a correctable preview row."""
    item = {
        "rowNumber": row_number,
        "kind": "expense",
        "amount": None,
        "currency": "VND",
        "category": "uncategorized",
        "description": None,
        "transactionDate": None,
        "dayPeriod": None,
        "localTime": None,
        "timezone": "Asia/Ho_Chi_Minh",
        "rowErrors": {"row": error},
    }
    return CsvImportRow(row_number, item, ("amount", "description", "transactionDate", "dayPeriod"), (error,))
