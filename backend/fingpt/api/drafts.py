"""Authenticated text-draft endpoints and preview serialization."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from io import BytesIO
from typing import Any
from uuid import UUID

from flask import Blueprint, Flask, current_app, g, jsonify, request, send_file
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from werkzeug.utils import secure_filename

from fingpt.adapters.evidence_storage import EvidenceStorageError, PrivateEvidenceStorage
from fingpt.domain.money import Money, MoneyValidationError
from fingpt.domain.text_interpretation import TransactionKind
from fingpt.domain.transaction_time import DayPeriod
from fingpt.persistence.models import DraftItem
from fingpt.services.drafts import (
    ConversationNotFound,
    DraftConfirmationConflict,
    DraftIdempotencyConflict,
    DraftSnapshot,
    create_text_draft,
    get_draft_snapshot,
    list_open_draft_snapshots,
    update_draft_item,
)

DRAFT_BLUEPRINT = Blueprint("drafts", __name__, url_prefix="/api/drafts")


def register_drafts(app: Flask) -> None:
    """Register private draft creation and retrieval routes."""
    app.register_blueprint(DRAFT_BLUEPRINT)


@DRAFT_BLUEPRINT.post("")
def create_draft() -> tuple[Any, int] | Any:
    """Create a durable preview without writing any transaction."""
    request_data, error = _validated_body()
    if error is not None:
        return error
    idempotency_key = _validated_idempotency_key()
    if idempotency_key is None:
        return _error("invalid_idempotency_key", "Send a valid UUID in Idempotency-Key."), 400

    database: Session = g.database_session
    try:
        snapshot, replayed = create_text_draft(
            database,
            user_id=g.auth_user.id,
            text=request_data["text"],
            idempotency_key=idempotency_key,
            conversation_id=request_data["conversation_id"],
            intake_at=datetime.now(UTC),
        )
    except DraftIdempotencyConflict as error:
        return _error("idempotency_conflict", str(error)), 409
    except ConversationNotFound:
        return _error("conversation_not_found", "Conversation was not found."), 404
    except ValueError as error:
        return _error("invalid_draft", str(error)), 422
    except SQLAlchemyError:
        current_app.logger.warning("Draft creation failed because the database operation failed")
        database.rollback()
        return _error("service_unavailable", "Draft service is temporarily unavailable."), 503

    response_status = 200 if replayed else 201
    return jsonify(draft=_serialize_draft(snapshot), replayed=replayed), response_status


@DRAFT_BLUEPRINT.get("")
def list_open_drafts() -> tuple[Any, int] | Any:
    """Return recent owner-scoped drafts with incomplete or ready items."""
    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        return _error("invalid_query", "limit must be an integer."), 400
    if limit < 1 or limit > 50:
        return _error("invalid_query", "limit must be between 1 and 50."), 400
    database: Session = g.database_session
    try:
        snapshots = list_open_draft_snapshots(database, g.auth_user.id, limit=limit)
    except SQLAlchemyError:
        current_app.logger.warning("Draft list failed because the database operation failed")
        return _error("service_unavailable", "Draft service is temporarily unavailable."), 503
    return jsonify(items=[_serialize_draft(snapshot) for snapshot in snapshots]), 200


@DRAFT_BLUEPRINT.get("/<uuid:draft_id>")
def read_draft(draft_id: UUID) -> tuple[Any, int] | Any:
    """Return a persisted preview only when it belongs to the active user."""
    database: Session = g.database_session
    try:
        snapshot = get_draft_snapshot(database, g.auth_user.id, draft_id)
    except SQLAlchemyError:
        current_app.logger.warning("Draft read failed because the database operation failed")
        return _error("service_unavailable", "Draft service is temporarily unavailable."), 503
    if snapshot is None:
        return _error("draft_not_found", "Draft was not found."), 404
    return jsonify(draft=_serialize_draft(snapshot), replayed=False), 200


@DRAFT_BLUEPRINT.get("/<uuid:draft_id>/evidence/content")
def draft_evidence_content(draft_id: UUID) -> tuple[Any, int] | Any:
    """Stream private image or CSV evidence for an owned pending draft."""
    database: Session = g.database_session
    try:
        snapshot = get_draft_snapshot(database, g.auth_user.id, draft_id)
    except SQLAlchemyError:
        current_app.logger.warning("Draft evidence lookup failed")
        return _error("service_unavailable", "Evidence is temporarily unavailable."), 503
    if snapshot is None:
        return _error("draft_not_found", "Draft was not found."), 404
    evidence = snapshot.evidence
    if evidence.storage_key is None or evidence.source_type not in {"image", "csv"}:
        return _error("evidence_content_unavailable", "File evidence is unavailable."), 404
    if evidence.media_type not in {"image/jpeg", "image/png", "image/webp", "text/csv"}:
        return _error("evidence_unavailable", "Evidence type is unavailable."), 503
    storage: PrivateEvidenceStorage = current_app.extensions["evidence_storage"]
    try:
        content = storage.read(evidence.storage_key)
    except EvidenceStorageError:
        current_app.logger.warning("Private draft evidence could not be reopened")
        return _error("evidence_unavailable", "Evidence is unavailable."), 503
    response = send_file(
        BytesIO(content),
        mimetype=evidence.media_type,
        as_attachment=evidence.source_type == "csv",
        download_name=secure_filename(evidence.original_filename or "evidence") or "evidence",
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


def _validated_body() -> tuple[dict[str, Any], tuple[Any, int] | None]:
    """Validate the supported JSON fields before domain interpretation."""
    if not request.is_json:
        return {}, (_error("json_required", "Send a JSON request."), 415)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) - {"text", "conversationId"}:
        return {}, (_error("invalid_request", "Draft request fields are invalid."), 400)
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 8_000:
        return {}, (_error("invalid_text", "Text must contain 1–8,000 characters."), 422)
    conversation_id: UUID | None = None
    raw_conversation_id = payload.get("conversationId")
    if raw_conversation_id is not None:
        if not isinstance(raw_conversation_id, str):
            return {}, (_error("invalid_conversation_id", "Conversation ID must be a UUID."), 400)
        try:
            conversation_id = UUID(raw_conversation_id)
        except ValueError:
            return {}, (_error("invalid_conversation_id", "Conversation ID must be a UUID."), 400)
    return {"text": text, "conversation_id": conversation_id}, None


def _validated_idempotency_key() -> str | None:
    """Require a client-generated UUID so retries represent the same intent."""
    raw_key = request.headers.get("Idempotency-Key", "")
    try:
        return str(UUID(raw_key))
    except ValueError:
        return None


def _serialize_draft(snapshot: DraftSnapshot) -> dict[str, Any]:
    """Serialize only preview-safe fields, with money kept as exact strings."""
    return {
        "id": str(snapshot.draft.id),
        "conversationId": str(snapshot.conversation.id),
        "status": snapshot.draft.status,
        "version": snapshot.draft.version,
        "createdAt": snapshot.draft.created_at.isoformat(),
        "items": [_serialize_item(item) for item in snapshot.items],
        "evidence": {
            "id": str(snapshot.evidence.id),
            "sourceType": snapshot.evidence.source_type,
            "sourceText": snapshot.evidence.source_text,
            "originalFilename": snapshot.evidence.original_filename,
            "mediaType": snapshot.evidence.media_type,
            "byteSize": snapshot.evidence.byte_size,
            "sha256": snapshot.evidence.sha256,
            "createdAt": snapshot.evidence.created_at.isoformat(),
        },
    }


def _serialize_item(item: DraftItem) -> dict[str, Any]:
    """Convert persisted normalized fields into stable camelCase API values."""
    data = item.extracted_data
    amount = data["amount"]
    currency = data["currency"]
    return {
        "id": str(item.id),
        "index": item.item_index,
        "status": item.status,
        "version": item.version,
        "rowNumber": data.get("rowNumber"),
        "description": data["description"],
        "kind": data["kind"],
        "category": data.get("category", "uncategorized"),
        "amount": {"amount": amount, "currency": currency} if amount is not None else None,
        "currency": currency or "VND",
        "transactionDate": data["transactionDate"],
        "dayPeriod": data["dayPeriod"],
        "localTime": data["localTime"],
        "timezone": data["timezone"],
        "missingFields": item.missing_fields,
        "rowErrors": data.get("rowErrors", {}),
    }


def _error(code: str, message: str) -> Any:
    """Return the API's consistent machine code and user-safe message."""
    return jsonify(error={"code": code, "message": message})


@DRAFT_BLUEPRINT.post("/<uuid:draft_id>/items/<uuid:item_id>/confirm")
def confirm_item(draft_id: UUID, item_id: UUID) -> tuple[Any, int] | Any:
    """Confirm one previously previewed item after its version is checked."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"expectedVersion"}:
        return _error("invalid_request", "Send only expectedVersion as a positive integer."), 400
    expected_version = payload["expectedVersion"]
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
        return _error("invalid_request", "expectedVersion must be a positive integer."), 400
    idempotency_key = _validated_idempotency_key()
    if idempotency_key is None:
        return _error("invalid_idempotency_key", "Send a valid UUID in Idempotency-Key."), 400

    from fingpt.services.drafts import DraftItemNotReady, confirm_draft_item

    database: Session = g.database_session
    try:
        transaction, replayed = confirm_draft_item(
            database,
            user_id=g.auth_user.id,
            draft_id=draft_id,
            item_id=item_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )
    except LookupError:
        return _error("draft_item_not_found", "Draft item was not found."), 404
    except DraftConfirmationConflict as error:
        return _error("confirmation_conflict", str(error)), 409
    except DraftItemNotReady as error:
        return _error("draft_item_incomplete", str(error)), 422
    except SQLAlchemyError:
        database.rollback()
        current_app.logger.warning("Draft confirmation failed because the database operation failed")
        return _error("service_unavailable", "Confirmation service is temporarily unavailable."), 503
    return jsonify(transaction=_serialize_transaction(transaction), replayed=replayed), 200 if replayed else 201


def _serialize_transaction(transaction: Any) -> dict[str, Any]:
    """Serialize the confirmed transaction without database-only fields."""
    return {
        "id": str(transaction.id),
        "version": transaction.version,
        "draftItemId": str(transaction.draft_item_id),
        "kind": transaction.kind,
        "amount": {"amount": format(transaction.amount, "f"), "currency": transaction.currency},
        "category": transaction.category,
        "description": transaction.note,
        "transactionDate": transaction.transaction_date.isoformat(),
        "localTime": transaction.local_time.isoformat(timespec="minutes") if transaction.local_time else None,
        "dayPeriod": transaction.day_period,
        "timezone": transaction.timezone,
    }


@DRAFT_BLUEPRINT.patch("/<uuid:draft_id>/items/<uuid:item_id>")
def edit_draft_item(draft_id: UUID, item_id: UUID) -> tuple[Any, int] | Any:
    """Apply user-confirmed corrections to a draft preview, never a transaction."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("invalid_request", "Send a JSON object."), 400
    allowed = {
        "expectedVersion",
        "description",
        "kind",
        "amount",
        "transactionDate",
        "dayPeriod",
        "localTime",
        "timezone",
        "category",
    }
    if set(payload) - allowed or "expectedVersion" not in payload:
        return _error("invalid_request", "Draft item fields are invalid."), 400
    expected_version = payload["expectedVersion"]
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
        return _error("invalid_request", "expectedVersion must be a positive integer."), 400
    try:
        changes = _validated_item_changes(payload)
    except (ValueError, MoneyValidationError) as error:
        return _error("invalid_draft_item", str(error)), 422
    database: Session = g.database_session
    try:
        item = update_draft_item(
            database,
            user_id=g.auth_user.id,
            draft_id=draft_id,
            item_id=item_id,
            expected_version=expected_version,
            changes=changes,
        )
    except LookupError:
        return _error("draft_item_not_found", "Draft item was not found."), 404
    except DraftConfirmationConflict as error:
        return _error("draft_item_conflict", str(error)), 409
    except ValueError as error:
        return _error("invalid_draft_item", str(error)), 422
    except SQLAlchemyError:
        database.rollback()
        current_app.logger.warning("Draft item update failed because the database operation failed")
        return _error("service_unavailable", "Draft service is temporarily unavailable."), 503
    return jsonify(item=_serialize_item(item)), 200


def _validated_item_changes(payload: dict[str, Any]) -> dict[str, object]:
    """Convert allowed API values into validated domain facts."""
    changes: dict[str, object] = {}
    if "description" in payload:
        description = payload["description"]
        if not isinstance(description, str) or not description.strip() or len(description) > 256:
            raise ValueError("Description must contain 1–256 characters.")
        changes["description"] = description.strip()
    if "category" in payload:
        category = payload["category"]
        if not isinstance(category, str) or not category.strip() or len(category.strip()) > 128:
            raise ValueError("Category must contain 1–128 characters.")
        changes["category"] = category.strip()
    if "kind" in payload:
        changes["kind"] = TransactionKind(payload["kind"])
    if "amount" in payload:
        amount = payload["amount"]
        if not isinstance(amount, dict) or set(amount) != {"amount", "currency"}:
            raise ValueError("Amount requires decimal-string amount and currency fields.")
        if not isinstance(amount["amount"], str) or not isinstance(amount["currency"], str):
            raise ValueError("Amount and currency must be strings.")
        changes["amount"] = Money.parse(amount["amount"], amount["currency"])
    if "transactionDate" in payload:
        raw_date = payload["transactionDate"]
        if not isinstance(raw_date, str):
            raise ValueError("Transaction date must be an ISO date string.")
        changes["transactionDate"] = date.fromisoformat(raw_date).isoformat()
    if "dayPeriod" in payload:
        changes["dayPeriod"] = DayPeriod(payload["dayPeriod"])
    if "localTime" in payload:
        raw_time = payload["localTime"]
        if not isinstance(raw_time, str):
            raise ValueError("Local time must be an ISO time string or null.")
        changes["localTime"] = time.fromisoformat(raw_time).isoformat(timespec="minutes")
    if "timezone" in payload:
        timezone = payload["timezone"]
        if not isinstance(timezone, str) or not timezone:
            raise ValueError("Timezone must be a valid IANA timezone.")
        changes["timezone"] = timezone
    return changes
