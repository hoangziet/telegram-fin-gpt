"""Dated, owner-scoped transaction filters and unpaginated analysis totals."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from fingpt.persistence.models import Transaction


def _body(response: TestResponse) -> dict[str, Any]:
    """Narrow a Flask test response's optional JSON body for indexing."""
    assert response.json is not None
    body: dict[str, Any] = response.json
    return body


def _record(client: FlaskClient, message: str) -> str:
    created = client.post("/api/drafts", json={"text": message}, headers={"Idempotency-Key": str(uuid4())})
    assert created.status_code == 201
    draft = _body(created)["draft"]
    item = draft["items"][0]
    assert item["status"] == "ready"
    confirmed = client.post(
        f"/api/drafts/{draft['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": item["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmed.status_code == 201
    return str(_body(confirmed)["transaction"]["id"])


def test_date_kind_search_pagination_and_summary_follow_same_filters(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, _ = draft_app
    first_id = _record(client, "2026-10-15 8h sáng ăn phở 50k")
    _record(client, "2026-10-15 12h trưa ăn phở 30k")
    _record(client, "2026-10-16 9h sáng nhận lương 100k")
    _record(client, "2026-09-15 7h sáng ăn phở 20k")

    base = "/api/transactions?from=2026-10-01&to=2026-10-31&kind=expense&q=phở"
    page = client.get(f"{base}&limit=1&offset=0")
    next_page = client.get(f"{base}&limit=1&offset=1")
    assert page.status_code == 200
    assert _body(page)["total"] == _body(next_page)["total"] == 2
    assert _body(page)["items"][0]["id"] != _body(next_page)["items"][0]["id"]

    totals_url = "/api/transactions/reports/totals?from=2026-10-01&to=2026-10-31&kind=expense&q=phở"
    totals = client.get(totals_url)
    assert totals.status_code == 200
    assert _body(totals)["totals"] == [{"kind": "expense", "currency": "VND", "amount": "80000.0000"}]
    assert _body(client.get(totals_url.replace("kind=expense", "kind=income")))["totals"] == []
    assert _body(client.get(totals_url.replace("q=phở", "q=no-match")))["totals"] == []

    with Session(app.extensions["database_engine"]) as database:
        transaction = database.scalar(select(Transaction).where(Transaction.id == UUID(first_id)))
        assert transaction is not None
        transaction.is_deleted = True
        database.commit()
    assert _body(client.get(f"{base}&includeDeleted=true"))["total"] == 2
    assert _body(client.get(base))["total"] == 1
    assert _body(client.get(totals_url))["totals"][0]["amount"] == "30000.0000"


def test_summary_rejects_unsupported_filters(draft_app: tuple[Flask, FlaskClient, UUID]) -> None:
    _, client, _ = draft_app
    url = "/api/transactions/reports/totals?from=2026-10-01&to=2026-10-31"
    assert client.get(f"{url}&kind=unknown").status_code == 400
    assert client.get(f"{url}&q={'x' * 129}").status_code == 400
