"""Owner-scoped transaction search and report endpoints."""

from __future__ import annotations

from datetime import date, time
from io import BytesIO
from typing import Any
from uuid import UUID

from flask import Blueprint, Flask, current_app, g, jsonify, request, send_file
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from werkzeug.utils import secure_filename

from fingpt.adapters.evidence_storage import EvidenceStorageError, PrivateEvidenceStorage
from fingpt.domain.money import Money, MoneyValidationError
from fingpt.domain.text_interpretation import TransactionKind
from fingpt.domain.transaction_time import DayPeriod
from fingpt.persistence.models import (
    Draft,
    DraftItem,
    Evidence,
    Transaction,
    TransactionChange,
    TransactionRevision,
)
from fingpt.services.drafts import DraftConfirmationConflict
from fingpt.services.transaction_changes import (
    cancel_change,
    confirm_change,
    create_change_proposal,
    get_change,
)
from fingpt.services.transaction_queries import search_transactions, transaction_totals

TRANSACTION_BLUEPRINT = Blueprint("transactions", __name__, url_prefix="/api/transactions")


def register_transactions(app: Flask) -> None:
    """Register read-only transaction query endpoints."""
    app.register_blueprint(TRANSACTION_BLUEPRINT)


@TRANSACTION_BLUEPRINT.get("")
def list_transactions() -> tuple[Any, int] | Any:
    """Search the active user's confirmed transaction history."""
    try:
        start_date = _query_date("from")
        end_date = _query_date("to")
        kind = request.args.get("kind")
        query = request.args.get("q", "").strip()
        include_deleted = request.args.get("includeDeleted", "false")
        if include_deleted not in {"true", "false"}:
            raise ValueError("includeDeleted must be true or false.")
        limit = _bounded_integer("limit", default=50, minimum=1, maximum=100)
        offset = _bounded_integer("offset", default=0, minimum=0, maximum=10_000)
    except ValueError as error:
        return jsonify(error={"code": "invalid_query", "message": str(error)}), 400
    if start_date and end_date and start_date > end_date:
        return jsonify(error={"code": "invalid_query", "message": "from must not be after to."}), 400
    if kind not in {None, "income", "expense"}:
        return jsonify(error={"code": "invalid_query", "message": "kind must be income or expense."}), 400
    if len(query) > 128:
        return jsonify(error={"code": "invalid_query", "message": "q must be 128 characters or fewer."}), 400

    database: Session = g.database_session
    try:
        items, total = search_transactions(
            database,
            user_id=g.auth_user.id,
            start_date=start_date,
            end_date=end_date,
            kind=kind,
            query=query or None,
            include_deleted=include_deleted == "true",
            limit=limit,
            offset=offset,
        )
    except SQLAlchemyError:
        current_app.logger.warning("Transaction search failed because the database operation failed")
        return jsonify(error={"code": "service_unavailable", "message": "Search is temporarily unavailable."}), 503
    return jsonify(
        items=[_serialize_transaction(item) for item in items], total=total, limit=limit, offset=offset
    ), 200


@TRANSACTION_BLUEPRINT.get("/reports/totals")
def report_totals() -> tuple[Any, int] | Any:
    """Return income and expense totals grouped by currency for a date range."""
    try:
        start_date = _query_date("from", required=True)
        end_date = _query_date("to", required=True)
        kind = request.args.get("kind")
        query = request.args.get("q", "").strip()
    except ValueError as error:
        return jsonify(error={"code": "invalid_query", "message": str(error)}), 400
    assert start_date is not None and end_date is not None
    if start_date > end_date:
        return jsonify(error={"code": "invalid_query", "message": "from must not be after to."}), 400
    if (end_date - start_date).days > 366:
        return jsonify(error={"code": "invalid_query", "message": "Report range must not exceed 367 days."}), 400
    if kind not in {None, "income", "expense"} or len(query) > 128:
        return jsonify(error={"code": "invalid_query", "message": "Invalid kind or search query."}), 400
    database: Session = g.database_session
    try:
        totals = transaction_totals(
            database,
            user_id=g.auth_user.id,
            start_date=start_date,
            end_date=end_date,
            kind=kind,
            query=query or None,
        )
    except SQLAlchemyError:
        current_app.logger.warning("Transaction report failed because the database operation failed")
        return jsonify(error={"code": "service_unavailable", "message": "Report is temporarily unavailable."}), 503
    return jsonify(
        fromDate=start_date.isoformat(),
        toDate=end_date.isoformat(),
        totals=[
            {"kind": kind, "currency": currency, "amount": format(amount, "f")} for kind, currency, amount in totals
        ],
    ), 200


@TRANSACTION_BLUEPRINT.get("/<uuid:transaction_id>/evidence")
def transaction_evidence(transaction_id: UUID) -> tuple[Any, int] | Any:
    """Reopen evidence for an owned transaction without exposing storage keys."""
    database: Session = g.database_session
    transaction = database.scalar(
        select(Transaction).where(Transaction.id == transaction_id, Transaction.user_id == g.auth_user.id)
    )
    if transaction is None:
        return jsonify(error={"code": "transaction_not_found", "message": "Transaction was not found."}), 404
    draft_item = database.scalar(
        select(DraftItem).where(DraftItem.id == transaction.draft_item_id, DraftItem.user_id == g.auth_user.id)
    )
    if draft_item is None:
        current_app.logger.error("Transaction evidence references are inconsistent")
        return jsonify(error={"code": "evidence_unavailable", "message": "Evidence is unavailable."}), 503
    draft = database.scalar(select(Draft).where(Draft.id == draft_item.draft_id, Draft.user_id == g.auth_user.id))
    if draft is None:
        current_app.logger.error("Transaction evidence references are inconsistent")
        return jsonify(error={"code": "evidence_unavailable", "message": "Evidence is unavailable."}), 503
    evidence = database.scalar(
        select(Evidence).where(Evidence.id == draft.evidence_id, Evidence.user_id == g.auth_user.id)
    )
    if evidence is None:
        return jsonify(error={"code": "evidence_unavailable", "message": "Evidence is unavailable."}), 503
    return jsonify(
        evidence={
            "id": str(evidence.id),
            "sourceType": evidence.source_type,
            "sourceText": evidence.source_text,
            "originalFilename": evidence.original_filename,
            "mediaType": evidence.media_type,
            "byteSize": evidence.byte_size,
            "sha256": evidence.sha256,
            "createdAt": evidence.created_at.isoformat(),
        }
    ), 200


@TRANSACTION_BLUEPRINT.get("/<uuid:transaction_id>/evidence/content")
def transaction_evidence_content(transaction_id: UUID) -> tuple[Any, int] | Any:
    """Stream private file evidence only after resolving it through an owned transaction."""
    database: Session = g.database_session
    evidence = _owned_transaction_evidence(database, g.auth_user.id, transaction_id)
    if evidence is None:
        return jsonify(error={"code": "transaction_not_found", "message": "Transaction was not found."}), 404
    if evidence.storage_key is None or evidence.source_type not in {"image", "csv"}:
        return jsonify(error={"code": "evidence_content_unavailable", "message": "File evidence is unavailable."}), 404
    storage: PrivateEvidenceStorage = current_app.extensions["evidence_storage"]
    try:
        content = storage.read(evidence.storage_key)
    except EvidenceStorageError:
        current_app.logger.warning("Private transaction evidence could not be reopened")
        return jsonify(error={"code": "evidence_unavailable", "message": "Evidence is unavailable."}), 503
    media_type = evidence.media_type
    if media_type not in {"image/jpeg", "image/png", "image/webp", "text/csv"}:
        return jsonify(error={"code": "evidence_unavailable", "message": "Evidence type is unavailable."}), 503
    filename = secure_filename(evidence.original_filename or "evidence") or "evidence"
    response = send_file(
        BytesIO(content),
        mimetype=media_type,
        as_attachment=evidence.source_type == "csv",
        download_name=filename,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


def _owned_transaction_evidence(database: Session, user_id: UUID, transaction_id: UUID) -> Evidence | None:
    """Resolve a transaction's evidence without exposing cross-owner relationships."""
    transaction = database.scalar(
        select(Transaction).where(Transaction.id == transaction_id, Transaction.user_id == user_id)
    )
    if transaction is None:
        return None
    draft_item = database.scalar(
        select(DraftItem).where(DraftItem.id == transaction.draft_item_id, DraftItem.user_id == user_id)
    )
    if draft_item is None:
        return None
    draft = database.scalar(select(Draft).where(Draft.id == draft_item.draft_id, Draft.user_id == user_id))
    if draft is None:
        return None
    return database.scalar(select(Evidence).where(Evidence.id == draft.evidence_id, Evidence.user_id == user_id))


@TRANSACTION_BLUEPRINT.get("/<uuid:transaction_id>/revisions")
def transaction_revisions(transaction_id: UUID) -> tuple[Any, int] | Any:
    """Return immutable revision snapshots for an owned transaction."""
    database: Session = g.database_session
    transaction = database.scalar(
        select(Transaction.id).where(Transaction.id == transaction_id, Transaction.user_id == g.auth_user.id)
    )
    if transaction is None:
        return jsonify(error={"code": "transaction_not_found", "message": "Transaction was not found."}), 404
    revisions = database.scalars(
        select(TransactionRevision)
        .where(
            TransactionRevision.transaction_id == transaction_id,
            TransactionRevision.user_id == g.auth_user.id,
        )
        .order_by(TransactionRevision.revision_number.desc())
    ).all()
    return jsonify(
        items=[
            {
                "id": str(revision.id),
                "revisionNumber": revision.revision_number,
                "action": revision.action,
                "beforeState": revision.before_state,
                "afterState": revision.after_state,
                "createdAt": revision.created_at.isoformat(),
            }
            for revision in revisions
        ]
    ), 200


@TRANSACTION_BLUEPRINT.get("/<uuid:transaction_id>/changes")
def list_transaction_changes(transaction_id: UUID) -> tuple[Any, int] | Any:
    """List pending proposals so an owner can resume or cancel a preview."""
    database: Session = g.database_session
    owned = database.scalar(
        select(Transaction.id).where(Transaction.id == transaction_id, Transaction.user_id == g.auth_user.id)
    )
    if owned is None:
        return jsonify(error={"code": "transaction_not_found", "message": "Transaction was not found."}), 404
    try:
        limit = _bounded_integer("limit", default=20, minimum=1, maximum=100)
        offset = _bounded_integer("offset", default=0, minimum=0, maximum=10_000)
    except ValueError as error:
        return jsonify(error={"code": "invalid_query", "message": str(error)}), 400
    changes = database.scalars(
        select(TransactionChange)
        .where(
            TransactionChange.transaction_id == transaction_id,
            TransactionChange.user_id == g.auth_user.id,
            TransactionChange.status == "proposed",
        )
        .order_by(TransactionChange.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return jsonify(items=[_serialize_change(change) for change in changes]), 200


def _query_date(name: str, *, required: bool = False) -> date | None:
    """Parse an optional ISO date query parameter."""
    raw_value = request.args.get(name)
    if raw_value is None:
        if required:
            raise ValueError(f"{name} is required as an ISO date.")
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date.") from error


def _bounded_integer(name: str, *, default: int, minimum: int, maximum: int) -> int:
    """Parse a bounded integer query parameter."""
    raw_value = request.args.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer.") from error
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _serialize_transaction(transaction: Transaction) -> dict[str, Any]:
    """Return stable public transaction data and exclude internal hashes."""
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
        "isDeleted": transaction.is_deleted,
    }


@TRANSACTION_BLUEPRINT.post("/<uuid:transaction_id>/changes")
def propose_change(transaction_id: UUID) -> tuple[Any, int] | Any:
    """Create a durable preview for an edit or soft delete."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or "expectedVersion" not in payload or "action" not in payload:
        return jsonify(error={"code": "invalid_request", "message": "action and expectedVersion are required."}), 400
    action = payload["action"]
    expected_version = payload["expectedVersion"]
    if action not in {"updated", "deleted"}:
        return jsonify(error={"code": "invalid_request", "message": "action must be updated or deleted."}), 400
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
        return jsonify(error={"code": "invalid_request", "message": "expectedVersion must be positive."}), 400
    key = _idempotency_key()
    if key is None:
        return jsonify(error={"code": "invalid_idempotency_key", "message": "Send a UUID idempotency key."}), 400
    allowed_fields = (
        {"action", "expectedVersion"}
        if action == "deleted"
        else {
            "action",
            "expectedVersion",
            "description",
            "kind",
            "amount",
            "category",
            "transactionDate",
            "dayPeriod",
            "localTime",
            "timezone",
        }
    )
    if set(payload) - allowed_fields or (action == "updated" and set(payload) <= {"action", "expectedVersion"}):
        return jsonify(error={"code": "invalid_request", "message": "Change fields are invalid."}), 400
    try:
        changes = _validated_change_fields(payload)
        proposal, replayed = create_change_proposal(
            g.database_session,
            user_id=g.auth_user.id,
            transaction_id=transaction_id,
            expected_version=expected_version,
            action=action,
            changes=changes,
            idempotency_key=key,
        )
    except (ValueError, MoneyValidationError) as error:
        return jsonify(error={"code": "invalid_change", "message": str(error)}), 422
    except LookupError:
        return jsonify(error={"code": "transaction_not_found", "message": "Transaction was not found."}), 404
    except DraftConfirmationConflict as error:
        return jsonify(error={"code": "change_conflict", "message": str(error)}), 409
    except SQLAlchemyError:
        current_app.logger.warning("Transaction change proposal failed because the database operation failed")
        return jsonify(error={"code": "service_unavailable", "message": "Proposal service is unavailable."}), 503
    return jsonify(change=_serialize_change(proposal), replayed=replayed), 200 if replayed else 201


@TRANSACTION_BLUEPRINT.post("/<uuid:transaction_id>/undo")
def propose_undo(transaction_id: UUID) -> tuple[Any, int] | Any:
    """Preview an inverse operation from the latest immutable revision."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"expectedVersion"}:
        return jsonify(error={"code": "invalid_request", "message": "Send expectedVersion."}), 400
    expected_version = payload["expectedVersion"]
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
        return jsonify(error={"code": "invalid_request", "message": "expectedVersion must be positive."}), 400
    key = _idempotency_key()
    if key is None:
        return jsonify(error={"code": "invalid_idempotency_key", "message": "Send a UUID idempotency key."}), 400
    try:
        proposal, replayed = create_change_proposal(
            g.database_session,
            user_id=g.auth_user.id,
            transaction_id=transaction_id,
            expected_version=expected_version,
            action="restored",
            changes={},
            idempotency_key=key,
        )
    except ValueError as error:
        return jsonify(error={"code": "undo_conflict", "message": str(error)}), 409
    except LookupError:
        return jsonify(error={"code": "transaction_not_found", "message": "Transaction was not found."}), 404
    except SQLAlchemyError:
        current_app.logger.warning("Undo proposal failed because the database operation failed")
        return jsonify(error={"code": "service_unavailable", "message": "Undo service is unavailable."}), 503
    return jsonify(change=_serialize_change(proposal), replayed=replayed), 200 if replayed else 201


@TRANSACTION_BLUEPRINT.get("/changes/<uuid:change_id>")
def get_change_preview(change_id: UUID) -> tuple[Any, int] | Any:
    """Read a proposed change only for the user who created it."""
    proposal = get_change(g.database_session, g.auth_user.id, change_id)
    if proposal is None:
        return jsonify(error={"code": "change_not_found", "message": "Change proposal was not found."}), 404
    return jsonify(change=_serialize_change(proposal)), 200


@TRANSACTION_BLUEPRINT.post("/changes/<uuid:change_id>/confirm")
def confirm_proposed_change(change_id: UUID) -> tuple[Any, int] | Any:
    """Apply an explicitly confirmed edit/delete/undo proposal."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"expectedVersion"}:
        return jsonify(error={"code": "invalid_request", "message": "Send expectedVersion."}), 400
    expected_version = payload["expectedVersion"]
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
        return jsonify(error={"code": "invalid_request", "message": "expectedVersion must be positive."}), 400
    key = _idempotency_key()
    if key is None:
        return jsonify(error={"code": "invalid_idempotency_key", "message": "Send a UUID idempotency key."}), 400
    try:
        transaction, proposal, replayed = confirm_change(
            g.database_session,
            user_id=g.auth_user.id,
            change_id=change_id,
            expected_version=expected_version,
            confirmation_key=key,
        )
    except LookupError:
        return jsonify(error={"code": "change_not_found", "message": "Change proposal was not found."}), 404
    except DraftConfirmationConflict as error:
        return jsonify(error={"code": "change_conflict", "message": str(error)}), 409
    except SQLAlchemyError:
        g.database_session.rollback()
        current_app.logger.warning("Transaction change confirmation failed because the database operation failed")
        return jsonify(error={"code": "service_unavailable", "message": "Confirmation service is unavailable."}), 503
    return jsonify(
        transaction=_serialize_transaction(transaction),
        change=_serialize_change(proposal),
        replayed=replayed,
    ), 200 if replayed else 201


@TRANSACTION_BLUEPRINT.post("/changes/<uuid:change_id>/cancel")
def cancel_proposed_change(change_id: UUID) -> tuple[Any, int] | Any:
    """Cancel a pending preview idempotently at its current version."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"expectedVersion"}:
        return jsonify(error={"code": "invalid_request", "message": "Send expectedVersion."}), 400
    expected_version = payload["expectedVersion"]
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
        return jsonify(error={"code": "invalid_request", "message": "expectedVersion must be positive."}), 400
    try:
        change, replayed = cancel_change(
            g.database_session,
            user_id=g.auth_user.id,
            change_id=change_id,
            expected_version=expected_version,
        )
    except LookupError:
        return jsonify(error={"code": "change_not_found", "message": "Change proposal was not found."}), 404
    except DraftConfirmationConflict as error:
        return jsonify(error={"code": "change_conflict", "message": str(error)}), 409
    except SQLAlchemyError:
        g.database_session.rollback()
        current_app.logger.warning("Transaction change cancellation failed because the database operation failed")
        return jsonify(error={"code": "service_unavailable", "message": "Cancellation service is unavailable."}), 503
    return jsonify(change=_serialize_change(change), replayed=replayed), 200


def _idempotency_key() -> str | None:
    """Parse a required request idempotency UUID."""
    try:
        return str(UUID(request.headers.get("Idempotency-Key", "")))
    except ValueError:
        return None


def _validated_change_fields(payload: dict[str, Any]) -> dict[str, object]:
    """Normalize user-supplied transaction corrections to domain values."""
    fields: dict[str, object] = {}
    if "description" in payload:
        value = payload["description"]
        if not isinstance(value, str) or len(value) > 2_000:
            raise ValueError("Description must be 2,000 characters or fewer.")
        fields["description"] = value
    if "kind" in payload:
        fields["kind"] = TransactionKind(payload["kind"])
    if "amount" in payload:
        value = payload["amount"]
        if not isinstance(value, dict) or set(value) != {"amount", "currency"}:
            raise ValueError("Amount requires decimal-string amount and currency fields.")
        if not isinstance(value["amount"], str) or not isinstance(value["currency"], str):
            raise ValueError("Amount and currency must be strings.")
        fields["amount"] = Money.parse(value["amount"], value["currency"])
    if "category" in payload:
        value = payload["category"]
        if not isinstance(value, str):
            raise ValueError("Category must be text.")
        fields["category"] = value.strip()
    if "transactionDate" in payload:
        value = payload["transactionDate"]
        if not isinstance(value, str):
            raise ValueError("Transaction date must be an ISO date.")
        fields["transactionDate"] = date.fromisoformat(value).isoformat()
    if "dayPeriod" in payload:
        fields["dayPeriod"] = DayPeriod(payload["dayPeriod"])
    if "localTime" in payload:
        value = payload["localTime"]
        if value is not None and not isinstance(value, str):
            raise ValueError("Local time must be an ISO time or null.")
        fields["localTime"] = time.fromisoformat(value).isoformat(timespec="minutes") if value else None
    if "timezone" in payload:
        value = payload["timezone"]
        if not isinstance(value, str):
            raise ValueError("Timezone must be text.")
        fields["timezone"] = value
    return fields


def _serialize_change(change: TransactionChange) -> dict[str, Any]:
    """Serialize a durable change preview and its optimistic version."""
    return {
        "id": str(change.id),
        "transactionId": str(change.transaction_id),
        "action": change.action,
        "status": change.status,
        "expectedTransactionVersion": change.expected_transaction_version,
        "version": change.version,
        "proposedState": change.proposed_state,
    }
