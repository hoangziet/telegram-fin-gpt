"""Private evidence storage boundary tests."""

from __future__ import annotations

from pathlib import Path
from stat import S_IMODE
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from fingpt.adapters.evidence_storage import (
    AzureBlobEvidenceStorage,
    EvidenceStorageError,
    FileSystemEvidenceStorage,
)


def test_filesystem_storage_uses_private_generated_paths_and_permissions(tmp_path: Path) -> None:
    storage = FileSystemEvidenceStorage(tmp_path / "private")
    user_id, evidence_id = uuid4(), uuid4()
    key = storage.save(user_id=user_id, evidence_id=evidence_id, data=b"private receipt", media_type="image/jpeg")
    path = tmp_path / "private" / key

    assert storage.read(key) == b"private receipt"
    assert S_IMODE(path.stat().st_mode) == 0o600
    assert S_IMODE(path.parent.stat().st_mode) == 0o700
    with pytest.raises(EvidenceStorageError):
        storage.read("../../outside")
    storage.delete(key)
    assert not path.exists()


def test_azure_storage_uses_private_blob_keys_and_explicit_content_type() -> None:
    client = MagicMock()
    with (
        patch("fingpt.adapters.evidence_storage.DefaultAzureCredential") as credential,
        patch("fingpt.adapters.evidence_storage.BlobServiceClient", return_value=client) as service,
    ):
        storage = AzureBlobEvidenceStorage("https://private.blob.core.windows.net", "evidence")
        key = storage.save(user_id=uuid4(), evidence_id=uuid4(), data=b"csv data", media_type="text/csv")
        service.assert_called_once_with("https://private.blob.core.windows.net", credential=credential.return_value)
        client.get_blob_client.return_value.upload_blob.assert_called_once()
        kwargs = client.get_blob_client.return_value.upload_blob.call_args.kwargs
        assert kwargs["overwrite"] is False
        assert kwargs["content_settings"].content_type == "text/csv"
        assert "https://" not in key
        client.get_blob_client.return_value.download_blob.return_value.readall.return_value = b"csv data"
        assert storage.read(key) == b"csv data"
        storage.delete(key)
        client.get_blob_client.return_value.delete_blob.assert_called_once_with(delete_snapshots="include")


def test_azure_storage_rejects_non_https_endpoint() -> None:
    with pytest.raises(EvidenceStorageError):
        AzureBlobEvidenceStorage("http://storage.example", "evidence")
