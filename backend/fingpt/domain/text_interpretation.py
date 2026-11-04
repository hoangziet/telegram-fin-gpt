"""Conservative extraction of explicit transaction facts from Vietnamese text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from fingpt.domain.money import Money, MoneyValidationError
from fingpt.domain.transaction_time import (
    DEFAULT_TIMEZONE,
    DayPeriod,
    period_for_clock_time,
    resolve_relative_date,
)

_DATE_PATTERN = re.compile(
    r"\b(?P<relative>"
    r"(?:(?:sáng|trưa|chiều|tối|đêm)\s+)?(?:hôm|bữa)\s+(?:nay|qua|kia)|"
    r"(?:sáng|trưa|chiều|tối|đêm)\s+(?:nay|qua|kia)|"
    r"(?<!tháng )(?<!tháng\s\s)(?<!năm )nay"
    r")\b|"
    r"\b(?P<iso>\d{4}-\d{2}-\d{2})\b|"
    r"\b(?:ngày\s+)?(?P<local>\d{1,2}/\d{1,2}/\d{4})\b",
    re.IGNORECASE,
)
_TIME_PATTERN = re.compile(
    r"(?<!\d)(?P<hour>\d{1,2})(?:\s*:\s*(?P<colon_minute>\d{2})|"
    r"\s*h\s*(?P<h_minute>\d{1,2})?|\s+giờ(?:\s*(?P<word_minute>\d{1,2}))?)"
    r"\s*(?P<qualifier>sáng|trưa|chiều|tối|đêm)?\b",
    re.IGNORECASE,
)
_PERIOD_PATTERN = re.compile(r"\b(sáng|trưa|chiều|tối|đêm)\b", re.IGNORECASE)
_AMOUNT_PATTERN = re.compile(
    r"(?<!\w)(?P<number>\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*"
    r"(?P<unit>triệu|tr|nghìn|ngàn|k|đ|vnd)?(?!\w)",
    re.IGNORECASE,
)
_ITEM_SEPARATOR = re.compile(r"\n|;|,(?=\s*[^\d\s,])")
_ACTION_WORDS = re.compile(r"\b(ăn|uống|mua|đi|thanh toán|trả tiền|chi)\b", re.IGNORECASE)
_INCOME_WORDS = re.compile(r"\b(lương|thu nhập|nhận tiền|được trả|hoàn tiền|lãi)\b", re.IGNORECASE)


class TransactionKind(StrEnum):
    """Supported cash-flow direction for a proposal."""

    EXPENSE = "expense"
    INCOME = "income"


@dataclass(frozen=True, slots=True)
class ParsedTextItem:
    """One independently reviewable item; missing facts remain explicit."""

    source_text: str
    description: str | None
    money: Money | None
    kind: TransactionKind
    transaction_date: date | None
    day_period: DayPeriod | None
    local_time: time | None
    missing_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedTextRequest:
    """Parsed message with no persistence or transaction side effects."""

    items: tuple[ParsedTextItem, ...]


def parse_text_request(
    text: str,
    *,
    intake_at: datetime,
    timezone: str = DEFAULT_TIMEZONE,
) -> ParsedTextRequest:
    """Split text items and extract only explicit money and time information."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Transaction text is required.")
    if len(text) > 8_000:
        raise ValueError("Transaction text exceeds the 8,000-character limit.")

    segments = tuple(part.strip() for part in _ITEM_SEPARATOR.split(text) if part.strip())
    parsed_amounts = tuple(_parse_money(segment) for segment in segments)
    amount_count = sum(money is not None for money, _ in parsed_amounts)
    suppress_amount_index: int | None = None
    if len(segments) > 1 and amount_count == 1:
        if len(tuple(_ACTION_WORDS.finditer(text))) == 1:
            segments = (text.strip(),)
        else:
            suppress_amount_index = next(index for index, (money, _) in enumerate(parsed_amounts) if money is not None)
    if not segments or len(segments) > 100:
        raise ValueError("A message must contain between 1 and 100 transaction items.")

    shared_date = _shared_date(text, intake_at, timezone)
    shared_period, shared_time = _shared_time(text)
    items = tuple(
        _parse_item(
            part,
            intake_at,
            timezone,
            shared_date,
            shared_period,
            shared_time,
            suppress_amount=index == suppress_amount_index,
        )
        for index, part in enumerate(segments)
    )
    return ParsedTextRequest(items=items)


def _shared_date(value: str, intake_at: datetime, timezone: str) -> date | None:
    """Use a message date only when it contains one unambiguous date expression."""
    matches = tuple(_DATE_PATTERN.finditer(value))
    if len(matches) != 1:
        return None
    match = matches[0]
    relative = match.group("relative")
    if relative is not None:
        relative_normalized = relative.casefold()
        if relative_normalized.endswith("nay"):
            days_ago = 0
        elif relative_normalized.endswith("qua"):
            days_ago = 1
        elif relative_normalized.endswith("kia"):
            days_ago = 2
        else:
            return None
        return resolve_relative_date(intake_at, days_ago, timezone)
    raw_date = match.group("iso") or match.group("local")
    if raw_date is None:
        return None
    return date.fromisoformat(raw_date) if "-" in raw_date else _parse_local_date(raw_date)


def _parse_local_date(value: str) -> date:
    """Parse the explicit Vietnamese day/month/year format."""
    day, month, year = (int(part) for part in value.split("/"))
    return date(year, month, day)


def _shared_time(value: str) -> tuple[DayPeriod | None, time | None]:
    """Extract one shared period or clock time, rejecting ambiguous multiples."""
    matches = tuple(_TIME_PATTERN.finditer(value))
    if len(matches) == 1:
        local_time, derived_period = _parse_clock(matches[0])
        if local_time is not None:
            return derived_period, local_time
    if matches:
        return None, None
    periods = tuple(_PERIOD_PATTERN.finditer(value))
    if not periods:
        return None, None
    unique_periods = {_period_from_word(period.group(1)) for period in periods}
    if len(unique_periods) != 1:
        return None, None
    return next(iter(unique_periods)), None


def _parse_clock(match: re.Match[str]) -> tuple[time | None, DayPeriod | None]:
    """Parse explicit clock syntax and derive periods only when unambiguous."""
    hour = int(match.group("hour"))
    minute = int(match.group("colon_minute") or match.group("h_minute") or match.group("word_minute") or 0)
    qualifier = match.group("qualifier")
    if minute > 59 or hour > 23:
        return None, None
    if qualifier:
        hour = _qualify_hour(hour, qualifier.casefold())
    elif 1 <= hour <= 12:
        return time(hour, minute), None
    local_time = time(hour, minute)
    return local_time, period_for_clock_time(local_time)


def _qualify_hour(hour: int, qualifier: str) -> int:
    """Resolve a 12-hour Vietnamese clock qualifier to local 24-hour time."""
    if qualifier == "sáng":
        return hour % 12
    if qualifier == "trưa":
        return 12 if hour in {0, 12} else hour + 12 if 1 <= hour <= 3 else hour
    if qualifier in {"chiều", "tối"}:
        return hour + 12 if 1 <= hour < 12 else hour
    if hour in {0, 12}:
        return 0
    return hour if hour <= 5 else hour + 12


def _period_from_word(value: str) -> DayPeriod:
    """Map an explicit Vietnamese period word to the stored enum."""
    return {
        "sáng": DayPeriod.MORNING,
        "trưa": DayPeriod.NOON,
        "chiều": DayPeriod.AFTERNOON,
        "tối": DayPeriod.EVENING,
        "đêm": DayPeriod.EVENING,
    }[value.casefold()]


def _parse_item(
    source_text: str,
    intake_at: datetime,
    timezone: str,
    shared_date: date | None,
    shared_period: DayPeriod | None,
    shared_time: time | None,
    *,
    suppress_amount: bool = False,
) -> ParsedTextItem:
    """Extract one item and leave unrecognized or absent facts for review."""
    local_date = _shared_date(source_text, intake_at, timezone) or shared_date
    local_period, local_time = _shared_time(source_text)
    period = local_period or shared_period
    clock_time = local_time or shared_time
    money, amount_span = _parse_money(source_text)
    if suppress_amount:
        money = None
    description = _clean_description(source_text, amount_span)
    missing = tuple(
        field
        for field, is_missing in (
            ("transaction_date", local_date is None),
            ("day_period", period is None),
            ("local_time", clock_time is None),
            ("amount", money is None),
            ("description", description is None),
        )
        if is_missing
    )
    kind = TransactionKind.INCOME if _INCOME_WORDS.search(description or "") else TransactionKind.EXPENSE
    return ParsedTextItem(
        source_text=source_text,
        description=description,
        money=money,
        kind=kind,
        transaction_date=local_date,
        day_period=period,
        local_time=clock_time,
        missing_fields=missing,
    )


def _parse_money(value: str) -> tuple[Money | None, tuple[int, int] | None]:
    """Parse the last explicit numeric amount, preserving literal units."""
    date_and_time_spans = tuple(
        match.span() for pattern in (_DATE_PATTERN, _TIME_PATTERN) for match in pattern.finditer(value)
    )
    matches = tuple(
        match
        for match in _AMOUNT_PATTERN.finditer(value)
        if not any(match.start() < end and match.end() > start for start, end in date_and_time_spans)
        and (match.group("unit") is not None or _is_terminal_amount(value, match))
    )
    if not matches:
        return None, None
    match = matches[-1]
    raw_number = match.group("number")
    unit = (match.group("unit") or "").casefold()
    normalized_number = _normalize_number(raw_number)
    multiplier = Decimal(1_000_000 if unit in {"triệu", "tr"} else 1_000 if unit in {"k", "nghìn", "ngàn"} else 1)
    try:
        money = Money(amount=Decimal(normalized_number) * multiplier, currency="VND")
    except (InvalidOperation, MoneyValidationError):
        return None, match.span()
    return money, match.span()


def _is_terminal_amount(value: str, match: re.Match[str]) -> bool:
    """Accept a unitless amount only when it ends the transaction description."""
    trailing_text = value[match.end() :]
    for pattern in (_DATE_PATTERN, _TIME_PATTERN, _PERIOD_PATTERN):
        trailing_text = pattern.sub(" ", trailing_text)
    return not trailing_text.strip(" \t\r\n.,;:-–—")


def _normalize_number(value: str) -> str:
    """Normalize Vietnamese thousands separators and decimal notation."""
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", value):
        return value.replace(".", "").replace(",", "")
    if "." in value and "," in value:
        decimal_separator = "." if value.rfind(".") > value.rfind(",") else ","
        thousands_separator = "," if decimal_separator == "." else "."
        return value.replace(thousands_separator, "").replace(decimal_separator, ".")
    if "," in value:
        return value.replace(",", ".")
    return value


def _clean_description(value: str, amount_span: tuple[int, int] | None) -> str | None:
    """Remove recognized date/time/amount syntax and common action words."""
    if amount_span is not None:
        value = value[: amount_span[0]] + " " + value[amount_span[1] :]
    value = _DATE_PATTERN.sub(" ", value)
    value = _TIME_PATTERN.sub(" ", value)
    value = _PERIOD_PATTERN.sub(" ", value)
    value = _ACTION_WORDS.sub(" ", value)
    normalized = re.sub(r"\A[\s\-–—:,.]+|[\s\-–—:,.]+\Z", "", " ".join(value.split()))
    return normalized or None
