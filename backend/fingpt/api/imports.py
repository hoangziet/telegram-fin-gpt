"""Authenticated file-import preview routes."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from flask import Blueprint, Flask, current_app, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from werkzeug.utils import secure_filename

from fingpt.adapters.evidence_storage import EvidenceStorageError, PrivateEvidenceStorage
from fingpt.adapters.foundry_image_extractor import ImageInferenceError
from fingpt.domain.csv_import import MAX_CSV_BYTES, CsvImportError
from fingpt.domain.image_extraction import ImageExtractionError
from fingpt.persistence.models import DraftItem
from fingpt.services.csv_drafts import DuplicateCsvFile, create_csv_draft
from fingpt.services.drafts import DraftIdempotencyConflict, DraftSnapshot
from fingpt.services.image_drafts import (
    MAX_IMAGE_BYTES,
    DuplicateImage,
    ImageImportError,
    create_image_draft,
)

IMPORT_BLUEPRINT = Blueprint("imports", __name__, url_prefix="/api/imports")
MAX_MULTIPART_BYTES = MAX_CSV_BYTES + 64 * 1024
MAX_IMAGE_MULTIPART_BYTES = MAX_IMAGE_BYTES + 64 * 1024


def register_imports(app: Flask) -> None:
    """Register authenticated file-import preview routes."""
    app.register_blueprint(IMPORT_BLUEPRINT)


@IMPORT_BLUEPRINT.post("/csv")
def upload_csv() -> tuple[Any, int] | Any:
    """Validate, privately store, and preview CSV rows without writing transactions."""
    request.max_content_length = MAX_MULTIPART_BYTES
    if request.mimetype != "multipart/form-data":
        return _error("multipart_required", "Tải tệp CSV lên bằng biểu mẫu multipart."), 415
    file = request.files.get("file")
    if file is None or not file.filename:
        return _error("file_required", "Chọn một tệp CSV."), 400
    filename = secure_filename(file.filename)
    if not filename or len(filename) > 255 or not filename.lower().endswith(".csv"):
        return _error("invalid_filename", "Chọn tệp có phần mở rộng .csv."), 422
    raw_mapping = request.form.get("mapping")
    try:
        mapping = _parse_mapping(raw_mapping)
        allow_duplicate = _parse_boolean(request.form.get("allowDuplicate", "false"))
        idempotency_key = str(UUID(request.headers.get("Idempotency-Key", "")))
    except (ValueError, TypeError) as error:
        return _error("invalid_request", str(error)), 400
    content = file.stream.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        return _error("file_too_large", "Tệp CSV không được lớn hơn 5 MiB."), 413

    database: Session = g.database_session
    storage: PrivateEvidenceStorage = current_app.extensions["evidence_storage"]
    try:
        snapshot, replayed = create_csv_draft(
            database,
            storage,
            user_id=g.auth_user.id,
            content=content,
            filename=filename,
            mapping=mapping,
            idempotency_key=idempotency_key,
            allow_duplicate=allow_duplicate,
        )
    except DuplicateCsvFile as error:
        database.rollback()
        return jsonify(
            error={"code": "duplicate_file", "message": str(error)},
            existingDraftId=str(error.existing_draft_id) if error.existing_draft_id else None,
        ), 409
    except DraftIdempotencyConflict as error:
        database.rollback()
        return _error("idempotency_conflict", str(error)), 409
    except CsvImportError as error:
        database.rollback()
        return _error("invalid_csv", str(error)), 422
    except EvidenceStorageError:
        database.rollback()
        current_app.logger.warning("CSV evidence storage failed")
        return _error("evidence_unavailable", "Không thể lưu chứng từ CSV."), 503
    except SQLAlchemyError:
        database.rollback()
        current_app.logger.warning("CSV import preview could not be persisted")
        return _error("service_unavailable", "Nhập CSV tạm thời không khả dụng."), 503
    return jsonify(draft=_serialize_csv_draft(snapshot), replayed=replayed), 200 if replayed else 201


@IMPORT_BLUEPRINT.post("/image")
def upload_image() -> tuple[Any, int] | Any:
    """Extract one receipt total and return an owner-reviewable draft."""
    request.max_content_length = MAX_IMAGE_MULTIPART_BYTES
    if request.mimetype != "multipart/form-data":
        return _error("multipart_required", "Tải ảnh lên bằng biểu mẫu multipart."), 415
    file = request.files.get("file")
    if file is None or not file.filename:
        return _error("file_required", "Chọn một tệp ảnh."), 400
    filename = secure_filename(file.filename)
    if not filename or len(filename) > 255:
        return _error("invalid_filename", "Chọn tên tệp ảnh hợp lệ."), 422
    try:
        idempotency_key = str(UUID(request.headers.get("Idempotency-Key", "")))
        allow_duplicate = _parse_boolean(request.form.get("allowDuplicate", "false"))
    except (ValueError, TypeError) as error:
        return _error("invalid_request", str(error)), 400
    content = file.stream.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        return _error("file_too_large", "Ảnh không được lớn hơn 10 MiB."), 413

    extractor = current_app.extensions.get("image_extractor")
    if extractor is None:
        return _error("image_extraction_unavailable", "Trích xuất ảnh chưa được cấu hình."), 503
    database: Session = g.database_session
    storage: PrivateEvidenceStorage = current_app.extensions["evidence_storage"]
    try:
        snapshot, replayed = create_image_draft(
            database,
            storage,
            extractor,
            user_id=g.auth_user.id,
            content=content,
            media_type=file.mimetype or "",
            filename=filename,
            idempotency_key=idempotency_key,
            allow_duplicate=allow_duplicate,
        )
    except DuplicateImage as error:
        database.rollback()
        return _error("duplicate_image", str(error)), 409
    except DraftIdempotencyConflict as error:
        database.rollback()
        return _error("idempotency_conflict", str(error)), 409
    except ImageImportError as error:
        database.rollback()
        return _error("invalid_image", str(error)), 422
    except (ImageExtractionError, ImageInferenceError):
        database.rollback()
        current_app.logger.warning("Receipt image extraction was unavailable or malformed")
        return _error("image_extraction_failed", "Không thể trích xuất ảnh hóa đơn."), 503
    except EvidenceStorageError:
        database.rollback()
        current_app.logger.warning("Image evidence storage failed")
        return _error("evidence_unavailable", "Không thể lưu chứng từ ảnh."), 503
    except SQLAlchemyError:
        database.rollback()
        current_app.logger.warning("Image draft could not be persisted")
        return _error("service_unavailable", "Nhập ảnh tạm thời không khả dụng."), 503
    return jsonify(draft=_serialize_image_draft(snapshot), replayed=replayed), 200 if replayed else 201


def _parse_mapping(value: str | None) -> dict[str, str]:
    """Decode a bounded JSON object mapping domain fields to CSV headers."""
    if value is None or len(value) > 4_096:
        raise ValueError("Send a CSV column mapping.")
    parsed = json.loads(value)
    if not isinstance(parsed, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in parsed.items()
    ):
        raise ValueError("CSV column mapping must contain field-to-column strings.")
    return parsed


def _parse_boolean(value: str) -> bool:
    """Accept explicit lower-case form booleans only."""
    if value not in {"true", "false"}:
        raise ValueError("allowDuplicate must be true or false.")
    return value == "true"


def _serialize_csv_draft(snapshot: DraftSnapshot) -> dict[str, object]:
    """Return an owner-authorized row preview and safe evidence metadata."""
    evidence = snapshot.evidence
    return {
        "id": str(snapshot.draft.id),
        "status": snapshot.draft.status,
        "version": snapshot.draft.version,
        "conversationId": str(snapshot.conversation.id),
        "items": [_serialize_csv_item(item) for item in snapshot.items],
        "evidence": {
            "id": str(evidence.id),
            "sourceType": evidence.source_type,
            "sourceText": None,
            "originalFilename": evidence.original_filename,
            "mediaType": evidence.media_type,
            "byteSize": evidence.byte_size,
            "sha256": evidence.sha256,
            "createdAt": evidence.created_at.isoformat(),
        },
    }


def _serialize_csv_item(item: DraftItem) -> dict[str, object]:
    """Render mapped row fields, stable source row number, and field errors."""
    values = item.extracted_data
    return {
        "id": str(item.id),
        "index": item.item_index,
        "rowNumber": values.get("rowNumber"),
        "status": item.status,
        "version": item.version,
        "description": values.get("description"),
        "kind": values.get("kind"),
        "amount": {"amount": values.get("amount"), "currency": values.get("currency")}
        if values.get("amount") is not None
        else None,
        "currency": values.get("currency"),
        "category": values.get("category"),
        "transactionDate": values.get("transactionDate"),
        "dayPeriod": values.get("dayPeriod"),
        "localTime": values.get("localTime"),
        "timezone": values.get("timezone"),
        "missingFields": item.missing_fields,
        "rowErrors": values.get("rowErrors", {}),
    }


def _serialize_image_draft(snapshot: DraftSnapshot) -> dict[str, object]:
    """Return a single-item image preview and private evidence metadata."""
    evidence = snapshot.evidence
    item = snapshot.items[0]
    values = item.extracted_data
    return {
        "id": str(snapshot.draft.id),
        "status": snapshot.draft.status,
        "version": snapshot.draft.version,
        "conversationId": str(snapshot.conversation.id),
        "items": [
            {
                "id": str(item.id),
                "index": item.item_index,
                "rowNumber": None,
                "status": item.status,
                "version": item.version,
                "description": values.get("description"),
                "kind": values.get("kind"),
                "amount": {"amount": values.get("amount"), "currency": values.get("currency")}
                if values.get("amount") is not None
                else None,
                "currency": values.get("currency"),
                "category": values.get("category"),
                "transactionDate": values.get("transactionDate"),
                "dayPeriod": values.get("dayPeriod"),
                "localTime": values.get("localTime"),
                "timezone": values.get("timezone"),
                "missingFields": item.missing_fields,
                "rowErrors": values.get("rowErrors", {}),
            }
        ],
        "evidence": {
            "id": str(evidence.id),
            "sourceType": evidence.source_type,
            "sourceText": None,
            "originalFilename": evidence.original_filename,
            "mediaType": evidence.media_type,
            "byteSize": evidence.byte_size,
            "sha256": evidence.sha256,
            "createdAt": evidence.created_at.isoformat(),
        },
    }


def _error(code: str, message: str) -> Any:
    """Return a compact upload-boundary error."""
    return jsonify(error={"code": code, "message": message})
