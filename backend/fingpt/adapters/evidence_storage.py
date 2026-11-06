"""Private evidence storage adapters for local development and R2."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]


class EvidenceStorageError(RuntimeError):
    """Raised when private evidence bytes cannot be stored or reopened."""


class EvidenceStorageConfigurationError(RuntimeError):
    """Raised when a configured evidence backend is missing required settings."""


class PrivateEvidenceStorage(Protocol):
    """Owner-isolated private media persistence interface."""

    def save(self, *, user_id: UUID, evidence_id: UUID, data: bytes, media_type: str) -> str:
        """Persist bytes and return a private opaque storage key."""

    def read(self, storage_key: str) -> bytes:
        """Read bytes for an already owner-authorized evidence record."""

    def delete(self, storage_key: str) -> None:
        """Remove bytes after a failed database transaction or authorized deletion."""


class FileSystemEvidenceStorage:
    """Store private local-development evidence with owner-only file permissions."""

    def __init__(self, root: Path) -> None:
        """Set a private local root; create no files until the first upload."""
        self._root = root.expanduser().resolve()

    def save(self, *, user_id: UUID, evidence_id: UUID, data: bytes, media_type: str) -> str:
        """Write one immutable evidence object with mode 0600."""
        del media_type
        storage_key = _storage_key(user_id, evidence_id)
        target = self._target(storage_key)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as evidence_file:
                evidence_file.write(data)
        except OSError as error:
            raise EvidenceStorageError("Evidence could not be stored.") from error
        return storage_key

    def read(self, storage_key: str) -> bytes:
        """Read one generated-key object after rejecting path traversal."""
        try:
            return self._target(storage_key).read_bytes()
        except OSError as error:
            raise EvidenceStorageError("Evidence could not be reopened.") from error

    def delete(self, storage_key: str) -> None:
        """Delete one generated-key object without traversing outside the root."""
        try:
            self._target(storage_key).unlink(missing_ok=True)
        except OSError as error:
            raise EvidenceStorageError("Evidence could not be removed.") from error

    def _target(self, storage_key: str) -> Path:
        """Resolve only keys matching the UUID-based object layout."""
        if not _STORAGE_KEY.fullmatch(storage_key):
            raise EvidenceStorageError("Evidence storage reference is invalid.")
        target = (self._root / storage_key).resolve()
        if not target.is_relative_to(self._root):
            raise EvidenceStorageError("Evidence storage reference is invalid.")
        return target


class R2EvidenceStorage:
    """Store private evidence in a pre-provisioned R2 bucket."""

    def __init__(
        self,
        account_id: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        *,
        client: Any | None = None,
    ) -> None:
        """Configure the S3-compatible client with an injectable test transport."""
        if not all((account_id, bucket, access_key_id, secret_access_key)):
            raise EvidenceStorageError("R2 evidence storage configuration is invalid.")
        self._bucket = bucket
        self._client = (
            client
            if client is not None
            else boto3.client(
                "s3",
                endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name="auto",
            )
        )

    def save(self, *, user_id: UUID, evidence_id: UUID, data: bytes, media_type: str) -> str:
        """Create a private object under a generated owner-scoped key."""
        storage_key = _storage_key(user_id, evidence_id)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=storage_key,
                Body=data,
                ContentType=media_type,
                Metadata={"owner_id": str(user_id), "evidence_id": str(evidence_id)},
                IfNoneMatch="*",
            )
        except (ClientError, BotoCoreError) as error:
            raise EvidenceStorageError("Evidence could not be stored.") from error
        return storage_key

    def read(self, storage_key: str) -> bytes:
        """Read a validated private object without generating a public URL."""
        _validate_storage_key(storage_key)
        try:
            return bytes(self._client.get_object(Bucket=self._bucket, Key=storage_key)["Body"].read())
        except (ClientError, BotoCoreError) as error:
            raise EvidenceStorageError("Evidence could not be reopened.") from error

    def delete(self, storage_key: str) -> None:
        """Delete a validated private object."""
        _validate_storage_key(storage_key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=storage_key)
        except (ClientError, BotoCoreError) as error:
            raise EvidenceStorageError("Evidence could not be removed.") from error


def _validate_storage_key(storage_key: str) -> None:
    """Reject user-supplied object paths."""
    if not _STORAGE_KEY.fullmatch(storage_key):
        raise EvidenceStorageError("Evidence storage reference is invalid.")


_STORAGE_KEY = re.compile(r"^users/[0-9a-f-]{36}/evidence/[0-9a-f-]{36}$")


def _storage_key(user_id: UUID, evidence_id: UUID) -> str:
    """Build a stable, non-user-controlled storage path."""
    return f"users/{user_id}/evidence/{evidence_id}"


def create_evidence_storage(
    *,
    mode: str,
    local_root: str,
    r2_account_id: str | None = None,
    r2_bucket: str | None = None,
    r2_access_key_id: str | None = None,
    r2_secret_access_key: str | None = None,
) -> PrivateEvidenceStorage:
    """Create the explicit local or private R2 evidence backend."""
    if mode == "filesystem":
        return FileSystemEvidenceStorage(Path(local_root))
    if mode == "r2":
        if not (r2_account_id and r2_bucket and r2_access_key_id and r2_secret_access_key):
            raise EvidenceStorageConfigurationError("R2 evidence storage is not fully configured.")
        return R2EvidenceStorage(r2_account_id, r2_bucket, r2_access_key_id, r2_secret_access_key)
    raise EvidenceStorageConfigurationError("Evidence storage mode must be filesystem or r2.")
