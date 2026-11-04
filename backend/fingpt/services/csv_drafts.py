"""Durable CSV evidence and row-level transaction preview creation."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from fingpt.adapters.evidence_storage import EvidenceStorageError, PrivateEvidenceStorage
from fingpt.domain.csv_import import parse_csv_import
from fingpt.persistence.models import Draft, DraftItem, Evidence
from fingpt.services.drafts import (
    DraftIdempotencyConflict,
    DraftSnapshot,
    _resolve_conversation,
    get_draft_snapshot,
)


class DuplicateCsvFile(ValueError):
    """Raised when identical owner evidence needs explicit duplicate consent."""

    def __init__(self, existing_draft_id: UUID | None) -> None:
        """Keep only the owner's prior draft reference for an optional UI link."""
        self.existing_draft_id = existing_draft_id
        super().__init__("This CSV file was already imported. Confirm to import it again.")


def create_csv_draft(
    database: Session,
    storage: PrivateEvidenceStorage,
    *,
    user_id: UUID,
    content: bytes,
    filename: str,
    mapping: dict[str, str],
    idempotency_key: str,
    allow_duplicate: bool,
) -> tuple[DraftSnapshot, bool]:
    """Persist uploaded evidence and all mapped row previews atomically."""
    digest = hashlib.sha256(content).hexdigest()
    request_hash = _request_hash(digest, mapping, allow_duplicate)
    existing = _find_by_key(database, user_id, idempotency_key)
    if existing is not None:
        return _replay(database, existing, request_hash)

    rows = parse_csv_import(content, mapping=mapping)
    if database.get_bind().dialect.name == "postgresql":
        lock_id = int.from_bytes(hashlib.sha256(f"{user_id}:{digest}".encode()).digest()[:8], "big", signed=True)
        database.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
        existing = _find_by_key(database, user_id, idempotency_key)
        if existing is not None:
            return _replay(database, existing, request_hash)
    duplicate = database.scalar(
        select(Evidence).where(
            Evidence.user_id == user_id,
            Evidence.source_type == "csv",
            Evidence.sha256 == digest,
        )
    )
    if duplicate is not None and not allow_duplicate:
        prior_draft = database.scalar(
            select(Draft.id).where(Draft.user_id == user_id, Draft.evidence_id == duplicate.id).limit(1)
        )
        raise DuplicateCsvFile(prior_draft)

    evidence_id = uuid4()
    storage_key = storage.save(
        user_id=user_id,
        evidence_id=evidence_id,
        data=content,
        media_type="text/csv",
    )
    conversation = _resolve_conversation(database, user_id, None, "web")
    draft = Draft(
        id=uuid4(),
        user_id=user_id,
        conversation_id=conversation.id,
        evidence_id=evidence_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status="open",
        version=1,
    )
    evidence = Evidence(
        id=evidence_id,
        user_id=user_id,
        source_type="csv",
        storage_key=storage_key,
        original_filename=filename,
        media_type="text/csv",
        sha256=digest,
        byte_size=len(content),
    )
    database.add_all([draft, evidence])
    database.add_all(
        DraftItem(
            user_id=user_id,
            draft_id=draft.id,
            item_index=index,
            status="incomplete" if row.missing_fields or row.errors else "ready",
            extracted_data=row.item,
            missing_fields=list(row.missing_fields),
            version=1,
        )
        for index, row in enumerate(rows)
    )
    try:
        database.commit()
    except IntegrityError:
        database.rollback()
        _delete_orphan(storage, storage_key)
        concurrent = _find_by_key(database, user_id, idempotency_key)
        if concurrent is None:
            raise
        return _replay(database, concurrent, request_hash)
    except SQLAlchemyError:
        database.rollback()
        _delete_orphan(storage, storage_key)
        raise
    snapshot = get_draft_snapshot(database, user_id, draft.id)
    if snapshot is None:
        raise RuntimeError("A newly created CSV draft could not be loaded.")
    return snapshot, False


def _request_hash(digest: str, mapping: dict[str, str], allow_duplicate: bool) -> str:
    """Fingerprint file content and explicit import choices for safe replay."""
    canonical = json.dumps(
        {"sha256": digest, "mapping": mapping, "allowDuplicate": allow_duplicate},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find_by_key(database: Session, user_id: UUID, key: str) -> Draft | None:
    """Find an idempotent draft using the owner-scoped key."""
    return database.scalar(select(Draft).where(Draft.user_id == user_id, Draft.idempotency_key == key))


def _replay(database: Session, draft: Draft, request_hash: str) -> tuple[DraftSnapshot, bool]:
    """Return an exact import retry or reject key reuse with changed intent."""
    if draft.request_hash != request_hash:
        raise DraftIdempotencyConflict("This idempotency key was already used for different import input.")
    snapshot = get_draft_snapshot(database, draft.user_id, draft.id)
    if snapshot is None:
        raise RuntimeError("An idempotent CSV draft could not be loaded.")
    return snapshot, True


def _delete_orphan(storage: PrivateEvidenceStorage, storage_key: str) -> None:
    """Remove an uploaded object when its database transaction lost a race."""
    try:
        storage.delete(storage_key)
    except EvidenceStorageError:
        return
