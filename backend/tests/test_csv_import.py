"""CSV import row mapping, validation, and exact-value tests."""

from __future__ import annotations

import io
import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fingpt.domain.csv_import import CsvImportError, parse_csv_import
from fingpt.persistence.models import Draft, DraftItem, Evidence, Transaction, User


class MemoryEvidenceStorage:
    """In-memory private evidence store used at the upload boundary."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def save(self, *, user_id: UUID, evidence_id: UUID, data: bytes, media_type: str) -> str:
        key = f"users/{user_id}/evidence/{evidence_id}"
        self.objects[key] = data
        return key

    def read(self, storage_key: str) -> bytes:
        return self.objects[storage_key]

    def delete(self, storage_key: str) -> None:
        self.objects.pop(storage_key, None)


class FakeImageExtractor:
    """Deterministic image extraction result for upload boundary tests."""

    def __init__(self) -> None:
        self.calls = 0
        self.result: dict[str, Any] = {
            "description": "Xem phim",
            "amount": "400",
            "currency": "VND",
            "transactionDate": "2026-11-01",
            "dayPeriod": "morning",
            "localTime": None,
            "kind": "expense",
        }

    def extract(self, *, image: bytes, media_type: str) -> dict[str, Any]:
        self.calls += 1
        assert image.startswith(b"\xff\xd8\xff")
        assert media_type == "image/jpeg"
        return self.result


def test_csv_mapping_preserves_exact_money_and_explicit_transaction_time() -> None:
    rows = parse_csv_import(
        b"date,amount,memo,type,currency,clock\n2026-11-01,12.34,Coffee,expense,USD,08:15\n",
        mapping={
            "transactionDate": "date",
            "amount": "amount",
            "description": "memo",
            "kind": "type",
            "currency": "currency",
            "localTime": "clock",
        },
    )

    assert len(rows) == 1
    assert rows[0].row_number == 2
    assert rows[0].item["amount"] == "12.34"
    assert rows[0].item["currency"] == "USD"
    assert rows[0].item["dayPeriod"] == "morning"
    assert rows[0].item["localTime"] == "08:15"
    assert rows[0].missing_fields == () and rows[0].errors == ()


def test_image_upload_returns_one_total_preview_and_reopenable_private_evidence(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, _ = draft_app
    storage = MemoryEvidenceStorage()
    extractor = FakeImageExtractor()
    app.extensions["evidence_storage"] = storage
    app.extensions["image_extractor"] = extractor
    content = b"\xff\xd8\xffsynthetic-image-bytes"
    idempotency_key = str(uuid4())
    response = client.post(
        "/api/imports/image",
        data={"file": (io.BytesIO(content), "receipt.jpg"), "allowDuplicate": "false"},
        headers={"Idempotency-Key": idempotency_key},
        content_type="multipart/form-data",
    )

    assert response.status_code == 201 and response.json is not None
    draft = response.json["draft"]
    assert len(draft["items"]) == 1
    assert draft["items"][0]["amount"] == {"amount": "400", "currency": "VND"}
    assert draft["items"][0]["description"] == "Xem phim"
    assert draft["items"][0]["status"] == "ready"
    assert draft["evidence"]["sourceType"] == "image"
    retry = client.post(
        "/api/imports/image",
        data={"file": (io.BytesIO(content), "receipt.jpg"), "allowDuplicate": "false"},
        headers={"Idempotency-Key": idempotency_key},
        content_type="multipart/form-data",
    )
    assert retry.status_code == 200 and retry.json is not None and retry.json["replayed"] is True
    reopened = client.get(f"/api/drafts/{draft['id']}/evidence/content")
    assert reopened.status_code == 200 and reopened.data == content
    assert extractor.calls == 1
    with Session(app.extensions["database_engine"]) as database:
        hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
        database.add(User(username="image-reader", password_hash=hasher.hash("Another test password! 2026")))
        database.commit()
    assert client.post("/api/auth/logout").status_code == 204
    other_login = client.post(
        "/api/auth/login",
        json={"username": "image-reader", "password": "Another test password! 2026"},
    )
    assert other_login.status_code == 200 and other_login.json is not None
    client.environ_base["HTTP_X_CSRF_TOKEN"] = other_login.json["csrfToken"]
    assert client.get(f"/api/drafts/{draft['id']}/evidence/content").status_code == 404


def test_image_upload_rejects_mime_signature_mismatch_before_model_call(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, _ = draft_app
    extractor = FakeImageExtractor()
    app.extensions["image_extractor"] = extractor
    response = client.post(
        "/api/imports/image",
        data={"file": (io.BytesIO(b"not a jpeg"), "receipt.jpg")},
        headers={"Idempotency-Key": str(uuid4())},
        content_type="multipart/form-data",
    )

    assert response.status_code == 422
    assert extractor.calls == 0


def test_image_upload_rejects_filename_type_mismatch_before_model_call(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, _ = draft_app
    extractor = FakeImageExtractor()
    app.extensions["image_extractor"] = extractor
    response = client.post(
        "/api/imports/image",
        data={"file": (io.BytesIO(b"\xff\xd8\xffvalid-signature"), "receipt.png")},
        headers={"Idempotency-Key": str(uuid4())},
        content_type="multipart/form-data",
    )

    assert response.status_code == 422
    assert extractor.calls == 0


def test_image_upload_requires_explicit_consent_to_import_duplicate(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, _ = draft_app
    app.extensions["evidence_storage"] = MemoryEvidenceStorage()
    app.extensions["image_extractor"] = FakeImageExtractor()
    content = b"\xff\xd8\xffsynthetic-image-bytes"

    def upload(key: str, allow_duplicate: bool) -> Any:
        return client.post(
            "/api/imports/image",
            data={
                "file": (io.BytesIO(content), "receipt.jpg"),
                "allowDuplicate": str(allow_duplicate).lower(),
            },
            headers={"Idempotency-Key": key},
            content_type="multipart/form-data",
        )

    assert upload(str(uuid4()), False).status_code == 201
    assert upload(str(uuid4()), False).status_code == 409
    assert upload(str(uuid4()), True).status_code == 201


def test_image_proposal_with_unknown_currency_stays_incomplete_until_user_corrects_it(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, _ = draft_app
    app.extensions["evidence_storage"] = MemoryEvidenceStorage()
    extractor = FakeImageExtractor()
    extractor.result.update({"amount": None, "currency": None})
    app.extensions["image_extractor"] = extractor
    response = client.post(
        "/api/imports/image",
        data={"file": (io.BytesIO(b"\xff\xd8\xffreceipt"), "receipt.jpg")},
        headers={"Idempotency-Key": str(uuid4())},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201 and response.json is not None
    draft = response.json["draft"]
    item = draft["items"][0]
    assert item["status"] == "incomplete"
    assert set(item["missingFields"]) == {"amount", "currency"}

    corrected = client.patch(
        f"/api/drafts/{draft['id']}/items/{item['id']}",
        json={
            "expectedVersion": item["version"],
            "description": "Xem phim",
            "amount": {"amount": "12.50", "currency": "USD"},
            "transactionDate": "2026-11-01",
            "dayPeriod": "morning",
        },
    )
    assert corrected.status_code == 200 and corrected.json is not None
    assert corrected.json["item"]["amount"] == {"amount": "12.50", "currency": "USD"}
    assert corrected.json["item"]["status"] == "ready"
    confirmed = client.post(
        f"/api/drafts/{draft['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": corrected.json["item"]["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmed.status_code == 201 and confirmed.json is not None
    transaction_id = confirmed.json["transaction"]["id"]
    evidence = client.get(f"/api/transactions/{transaction_id}/evidence/content")
    assert evidence.status_code == 200 and evidence.data == b"\xff\xd8\xffreceipt"


def test_csv_keeps_incomplete_rows_and_does_not_infer_missing_dates() -> None:
    rows = parse_csv_import(
        b"amount,description,time\n15000,Lunch,12:00\n",
        mapping={"amount": "amount", "description": "description", "localTime": "time"},
    )

    assert len(rows) == 1
    assert rows[0].item["transactionDate"] is None
    assert rows[0].item["dayPeriod"] == "noon"
    assert rows[0].item["localTime"] == "12:00"
    assert rows[0].missing_fields == ("transactionDate",)


def test_csv_reports_each_invalid_row_without_dropping_other_rows() -> None:
    rows = parse_csv_import(
        b"date,amount,description,period\n2026-11-01,10,Coffee,morning\nbad,0,,unknown\n",
        mapping={
            "transactionDate": "date",
            "amount": "amount",
            "description": "description",
            "dayPeriod": "period",
        },
    )

    assert len(rows) == 2
    assert rows[0].item["amount"] == "10"
    assert rows[0].missing_fields == ()
    assert rows[1].row_number == 3
    assert set(rows[1].errors) == {"invalid_date", "invalid_amount", "missing_description", "invalid_day_period"}
    assert rows[1].missing_fields == ("amount", "dayPeriod", "description", "transactionDate")


@pytest.mark.parametrize(
    ("content", "mapping"),
    [
        (b"\xff", {"amount": "amount", "description": "description"}),
        (b"amount,amount\n1,2\n", {"amount": "amount", "description": "description"}),
        (b"amount,description\n1,x\n", {"amount": "missing", "description": "description"}),
        (b"amount,description\n", {"amount": "amount", "description": "description"}),
    ],
)
def test_csv_rejects_unusable_file_shape(content: bytes, mapping: dict[str, str]) -> None:
    with pytest.raises(CsvImportError):
        parse_csv_import(content, mapping=mapping)


def test_csv_upload_retains_evidence_previews_errors_and_requires_duplicate_consent(
    draft_app: tuple[Flask, FlaskClient, UUID],
) -> None:
    app, client, owner_id = draft_app
    storage = MemoryEvidenceStorage()
    app.extensions["evidence_storage"] = storage
    mapping = {
        "transactionDate": "date",
        "amount": "amount",
        "description": "description",
        "dayPeriod": "period",
    }
    content = b"date,amount,description,period\n2026-11-01,15,Snack,morning\nbad,0,,unknown\n"
    request_id = str(uuid4())

    def upload(key: str, *, allow_duplicate: bool = False) -> Any:
        return client.post(
            "/api/imports/csv",
            data={
                "file": (io.BytesIO(content), "spend.csv"),
                "mapping": json.dumps(mapping),
                "allowDuplicate": str(allow_duplicate).lower(),
            },
            headers={"Idempotency-Key": key},
            content_type="multipart/form-data",
        )

    created = upload(request_id)
    assert created.status_code == 201
    assert created.json is not None
    preview = created.json["draft"]
    assert len(preview["items"]) == 2
    assert preview["items"][0]["rowNumber"] == 2
    assert preview["items"][0]["status"] == "ready"
    assert preview["items"][1]["status"] == "incomplete"
    assert preview["items"][1]["rowErrors"]["amount"] == "invalid_amount"
    evidence = preview["evidence"]
    assert evidence["sourceType"] == "csv" and evidence["originalFilename"] == "spend.csv"
    assert "storage_key" not in evidence
    preview_file = client.get(f"/api/drafts/{preview['id']}/evidence/content")
    assert preview_file.status_code == 200 and preview_file.data == content
    assert preview_file.headers["Content-Disposition"].startswith("attachment;")
    evidence_row = app.extensions["database_engine"]
    with Session(evidence_row) as database:
        item_count = database.scalar(select(func.count()).select_from(DraftItem))
        transaction_count = database.scalar(select(func.count()).select_from(Transaction))
        assert item_count == 2 and transaction_count == 0
        evidence_record = database.scalar(select(Evidence).where(Evidence.id == UUID(evidence["id"])))
        assert evidence_record is not None
        assert evidence_record.storage_key is not None
        assert storage.read(evidence_record.storage_key) == content

    ready_item = preview["items"][0]
    confirmed = client.post(
        f"/api/drafts/{preview['id']}/items/{ready_item['id']}/confirm",
        json={"expectedVersion": ready_item["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmed.status_code == 201
    assert confirmed.json is not None
    evidence_content = client.get(f"/api/transactions/{confirmed.json['transaction']['id']}/evidence/content")
    assert evidence_content.status_code == 200
    assert evidence_content.data == content
    assert evidence_content.mimetype == "text/csv"
    assert evidence_content.headers["Content-Disposition"].startswith("attachment;")
    assert evidence_content.headers["Cache-Control"] == "no-store"

    replay = upload(request_id)
    assert replay.status_code == 200 and replay.json is not None and replay.json["replayed"] is True
    duplicate = upload(str(uuid4()))
    assert duplicate.status_code == 409 and duplicate.json["error"]["code"] == "duplicate_file"
    acknowledged = upload(str(uuid4()), allow_duplicate=True)
    assert acknowledged.status_code == 201
    with Session(app.extensions["database_engine"]) as database:
        assert database.scalar(select(func.count()).select_from(Draft)) == 2


def test_csv_row_errors_block_confirmation_until_corrected(draft_app: tuple[Flask, FlaskClient, UUID]) -> None:
    app, client, _ = draft_app
    storage = MemoryEvidenceStorage()
    app.extensions["evidence_storage"] = storage
    response = client.post(
        "/api/imports/csv",
        data={
            "file": (io.BytesIO(b"date,amount,description,period\nbad,0,Lunch,unknown\n"), "rows.csv"),
            "mapping": '{"transactionDate":"date","amount":"amount","description":"description","dayPeriod":"period"}',
            "allowDuplicate": "false",
        },
        headers={"Idempotency-Key": str(uuid4())},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201 and response.json is not None
    item = response.json["draft"]["items"][0]
    confirm = client.post(
        f"/api/drafts/{response.json['draft']['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": item["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirm.status_code == 409
    corrected = client.patch(
        f"/api/drafts/{response.json['draft']['id']}/items/{item['id']}",
        json={
            "expectedVersion": item["version"],
            "amount": {"amount": "100", "currency": "VND"},
            "transactionDate": "2026-11-01",
            "dayPeriod": "morning",
        },
    )
    assert corrected.status_code == 200 and corrected.json is not None
    assert corrected.json["item"]["status"] == "ready"
    assert corrected.json["item"]["rowErrors"] == {}
    confirmed = client.post(
        f"/api/drafts/{response.json['draft']['id']}/items/{item['id']}/confirm",
        json={"expectedVersion": corrected.json["item"]["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmed.status_code == 201
    with Session(app.extensions["database_engine"]) as database:
        assert database.scalar(select(func.count()).select_from(Transaction)) == 1
