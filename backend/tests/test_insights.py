"""Deterministic spending-trend aggregation tests; no model call is involved."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.orm import Session

from fingpt.domain.transaction_time import DEFAULT_TIMEZONE
from fingpt.services.insights import _next_month, _percent_change, _previous_month, spending_trend


def test_next_and_previous_month_roll_over_the_year_boundary() -> None:
    assert _next_month(date(2026, 12, 1)) == date(2027, 1, 1)
    assert _next_month(date(2026, 2, 1)) == date(2026, 3, 1)
    assert _previous_month(date(2026, 1, 1)) == date(2025, 12, 1)
    assert _previous_month(date(2026, 3, 1)) == date(2026, 2, 1)


def test_percent_change_has_no_baseline_when_previous_is_zero() -> None:
    assert _percent_change(Decimal(0), Decimal(100)) is None
    assert _percent_change(Decimal(100), Decimal(150)) == 50.0
    assert _percent_change(Decimal(100), Decimal(50)) == -50.0


def _record(client: FlaskClient, *, date_text: str, description: str, amount: str, category: str) -> None:
    """Create and confirm one expense transaction with an explicit category."""
    created = client.post(
        "/api/drafts",
        json={"text": f"{date_text} 8h sáng {description} {amount}"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 201
    assert created.json is not None
    draft: dict[str, Any] = created.json["draft"]
    item: dict[str, Any] = draft["items"][0]
    assert item["status"] == "ready", item

    patched = client.patch(
        f"/api/drafts/{draft['id']}/items/{item['id']}",
        json={"expectedVersion": item["version"], "category": category},
    )
    assert patched.status_code == 200
    assert patched.json is not None
    patched_item: dict[str, Any] = patched.json["item"]

    confirmed = client.post(
        f"/api/drafts/{draft['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": patched_item["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmed.status_code == 201


def test_spending_trend_compares_current_and_previous_month_by_category(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, user_id = draft_app
    today = datetime.now(UTC).astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()
    current_month_day = today.replace(day=1)
    previous_month_day = _previous_month(current_month_day)

    _record(client, date_text=current_month_day.isoformat(), description="ăn phở", amount="50000", category="Ăn uống")
    _record(client, date_text=previous_month_day.isoformat(), description="ăn phở", amount="20000", category="Ăn uống")
    _record(
        client, date_text=current_month_day.isoformat(), description="xem phim", amount="30000", category="Giải trí"
    )

    with Session(app.extensions["database_engine"]) as database:
        trend = spending_trend(database, user_id)

    assert trend["currentPeriod"] == current_month_day.strftime("%Y-%m")
    assert trend["previousPeriod"] == previous_month_day.strftime("%Y-%m")
    assert trend["timezone"] == DEFAULT_TIMEZONE

    by_category = {(row["category"], row["currency"]): row for row in trend["categories"]}
    meals = by_category[("Ăn uống", "VND")]
    assert meals["currentAmount"] == "50000.0000"
    assert meals["previousAmount"] == "20000.0000"
    assert meals["percentChange"] == 150.0

    entertainment = by_category[("Giải trí", "VND")]
    assert entertainment["currentAmount"] == "30000.0000"
    assert entertainment["previousAmount"] == "0.0000"
    assert entertainment["percentChange"] is None

    assert trend["categories"][0]["category"] == "Ăn uống"


def test_spending_trend_is_empty_without_any_transactions(draft_app: tuple[Flask, FlaskClient, UUID]) -> None:
    app, _, user_id = draft_app
    with Session(app.extensions["database_engine"]) as database:
        trend = spending_trend(database, user_id)
    assert trend["categories"] == []


def test_insight_endpoint_requires_auth_and_degrades_without_a_narrator(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, _ = draft_app
    anonymous = app.test_client()
    assert anonymous.get("/api/chat/insight").status_code == 401
    response = client.get("/api/chat/insight")
    assert response.status_code == 200
    assert response.json is not None
    assert response.json["answer"] == "Chưa đủ dữ liệu để so sánh tháng."
    assert client.get("/api/chat/insight?language=fr").status_code == 422
