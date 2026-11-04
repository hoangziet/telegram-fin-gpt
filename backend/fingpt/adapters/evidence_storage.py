"""Private evidence storage adapters for local development and Azure Blob Storage."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol
from uuid import UUID

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings


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


class AzureBlobEvidenceStorage:
    """Store private evidence in a pre-provisioned Azure Blob container."""

    def __init__(self, account_url: str, container_name: str) -> None:
        """Use Azure's default credential chain for managed identity or local development."""
        if not account_url.startswith("https://") or not container_name:
            raise EvidenceStorageError("Azure evidence storage configuration is invalid.")
        self._client = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        self._container_name = container_name

    def save(self, *, user_id: UUID, evidence_id: UUID, data: bytes, media_type: str) -> str:
        """Create a private blob without overwriting a previously stored object."""
        storage_key = _storage_key(user_id, evidence_id)
        try:
            self._client.get_blob_client(self._container_name, storage_key).upload_blob(
                data,
                overwrite=False,
                content_settings=ContentSettings(content_type=media_type),
                metadata={"owner_id": str(user_id), "evidence_id": str(evidence_id)},
            )
        except AzureError as error:
            raise EvidenceStorageError("Evidence could not be stored.") from error
        return storage_key

    def read(self, storage_key: str) -> bytes:
        """Download object bytes by private key without generating a public URL."""
        if not _STORAGE_KEY.fullmatch(storage_key):
            raise EvidenceStorageError("Evidence storage reference is invalid.")
        try:
            return self._client.get_blob_client(self._container_name, storage_key).download_blob().readall()
        except AzureError as error:
            raise EvidenceStorageError("Evidence could not be reopened.") from error

    def delete(self, storage_key: str) -> None:
        """Delete a stored object by validated private key."""
        if not _STORAGE_KEY.fullmatch(storage_key):
            raise EvidenceStorageError("Evidence storage reference is invalid.")
        try:
            self._client.get_blob_client(self._container_name, storage_key).delete_blob(delete_snapshots="include")
        except AzureError as error:
            raise EvidenceStorageError("Evidence could not be removed.") from error

    def close(self) -> None:
        """Release the Blob client and credential resources."""
        self._client.close()


_STORAGE_KEY = re.compile(r"^users/[0-9a-f-]{36}/evidence/[0-9a-f-]{36}$")


def _storage_key(user_id: UUID, evidence_id: UUID) -> str:
    """Build a stable, non-user-controlled storage path."""
    return f"users/{user_id}/evidence/{evidence_id}"


def create_evidence_storage(
    *,
    mode: str,
    local_root: str,
    azure_account_url: str | None,
    azure_container: str | None,
) -> PrivateEvidenceStorage:
    """Create the explicit local or private Azure evidence backend."""
    if mode == "filesystem":
        return FileSystemEvidenceStorage(Path(local_root))
    if mode == "azure":
        if not azure_account_url or not azure_container:
            raise EvidenceStorageConfigurationError("Azure evidence storage is not fully configured.")
        return AzureBlobEvidenceStorage(azure_account_url, azure_container)
    raise EvidenceStorageConfigurationError("Evidence storage mode must be filesystem or azure.")
