"""Persist private receipt evidence and one validated total transaction preview."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from fingpt.adapters.evidence_storage import EvidenceStorageError, PrivateEvidenceStorage
from fingpt.domain.image_extraction import ImageProposal, validate_image_extraction
from fingpt.persistence.models import Draft, DraftItem, Evidence
from fingpt.services.drafts import DraftIdempotencyConflict, DraftSnapshot, _resolve_conversation, get_draft_snapshot

MAX_IMAGE_BYTES = 10 * 1024 * 1024
_IMAGE_EXTENSIONS = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
}


class ImageExtractor(Protocol):
    """Inference boundary for private receipt image extraction."""

    def extract(self, *, image: bytes, media_type: str) -> dict[str, Any]:
        """Return untrusted output for a single receipt total."""


class DuplicateImage(ValueError):
    """Raised when identical owner evidence lacks explicit duplicate consent."""


class ImageImportError(ValueError):
    """Raised when an uploaded image is not an accepted image file."""


def create_image_draft(
    database: Session,
    storage: PrivateEvidenceStorage,
    extractor: ImageExtractor,
    *,
    user_id: UUID,
    content: bytes,
    media_type: str,
    filename: str,
    idempotency_key: str,
    allow_duplicate: bool,
) -> tuple[DraftSnapshot, bool]:
    """Extract, validate, and persist one total proposal with original image evidence."""
    extension = filename.rpartition(".")[2].lower()
    if (
        len(content) > MAX_IMAGE_BYTES
        or f".{extension}" not in _IMAGE_EXTENSIONS.get(media_type, set())
        or not _matches_image_type(content, media_type)
    ):
        raise ImageImportError("Choose a valid JPEG, PNG, or WebP image up to 10 MiB.")
    digest = hashlib.sha256(content).hexdigest()
    request_hash = _request_hash(digest, allow_duplicate)
    existing = _find_by_key(database, user_id, idempotency_key)
    if existing is not None:
        return _replay(database, existing, request_hash)

    extracted = extractor.extract(image=content, media_type=media_type)
    proposal = validate_image_extraction(extracted)
    if database.get_bind().dialect.name == "postgresql":
        lock_id = int.from_bytes(hashlib.sha256(f"{user_id}:{digest}".encode()).digest()[:8], "big", signed=True)
        database.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
        existing = _find_by_key(database, user_id, idempotency_key)
        if existing is not None:
            return _replay(database, existing, request_hash)
    duplicate = database.scalar(
        select(Evidence).where(
            Evidence.user_id == user_id,
            Evidence.source_type == "image",
            Evidence.sha256 == digest,
        )
    )
    if duplicate is not None and not allow_duplicate:
        raise DuplicateImage("This image was already imported. Confirm to import it again.")

    return _persist_image_draft(
        database,
        storage,
        user_id=user_id,
        content=content,
        media_type=media_type,
        filename=filename,
        digest=digest,
        request_hash=request_hash,
        idempotency_key=idempotency_key,
        proposal=proposal,
    )


def _persist_image_draft(
    database: Session,
    storage: PrivateEvidenceStorage,
    *,
    user_id: UUID,
    content: bytes,
    media_type: str,
    filename: str,
    digest: str,
    request_hash: str,
    idempotency_key: str,
    proposal: ImageProposal,
) -> tuple[DraftSnapshot, bool]:
    """Commit evidence metadata and the single model proposal together."""
    evidence_id = uuid4()
    storage_key = storage.save(
        user_id=user_id,
        evidence_id=evidence_id,
        data=content,
        media_type=media_type,
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
        source_type="image",
        storage_key=storage_key,
        original_filename=filename,
        media_type=media_type,
        sha256=digest,
        byte_size=len(content),
    )
    item = DraftItem(
        user_id=user_id,
        draft_id=draft.id,
        item_index=0,
        status="incomplete" if proposal.missing_fields or proposal.errors else "ready",
        extracted_data=proposal.item,
        missing_fields=list(proposal.missing_fields),
        version=1,
    )
    database.add_all([draft, evidence, item])
    try:
        database.commit()
    except (IntegrityError, SQLAlchemyError):
        database.rollback()
        _delete_orphan(storage, storage_key)
        raise
    snapshot = get_draft_snapshot(database, user_id, draft.id)
    if snapshot is None:
        raise RuntimeError("A newly created image draft could not be loaded.")
    return snapshot, False


def _request_hash(digest: str, allow_duplicate: bool) -> str:
    """Fingerprint uploaded bytes and explicit duplicate consent."""
    encoded = json.dumps({"sha256": digest, "allowDuplicate": allow_duplicate}, sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _find_by_key(database: Session, user_id: UUID, key: str) -> Draft | None:
    """Find an idempotent image request scoped to its owner."""
    return database.scalar(select(Draft).where(Draft.user_id == user_id, Draft.idempotency_key == key))


def _replay(database: Session, draft: Draft, request_hash: str) -> tuple[DraftSnapshot, bool]:
    """Return exact retries and reject a changed request with the same key."""
    if draft.request_hash != request_hash:
        raise DraftIdempotencyConflict("This idempotency key was already used for different image input.")
    snapshot = get_draft_snapshot(database, draft.user_id, draft.id)
    if snapshot is None:
        raise RuntimeError("An idempotent image draft could not be loaded.")
    return snapshot, True


def _matches_image_type(content: bytes, media_type: str) -> bool:
    """Verify the declared image type against a short file signature."""
    if media_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if media_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/webp":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return False


def _delete_orphan(storage: PrivateEvidenceStorage, storage_key: str) -> None:
    """Remove image bytes when the database transaction fails."""
    try:
        storage.delete(storage_key)
    except EvidenceStorageError:
        return
