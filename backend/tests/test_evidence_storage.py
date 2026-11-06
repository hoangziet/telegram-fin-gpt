"""Private evidence storage boundary tests."""

from __future__ import annotations

from pathlib import Path
from stat import S_IMODE
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fingpt.adapters.evidence_storage import (
    EvidenceStorageError,
    FileSystemEvidenceStorage,
    R2EvidenceStorage,
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


def test_r2_storage_uses_private_keys_and_explicit_content_type() -> None:
    client = MagicMock()
    storage = R2EvidenceStorage("account", "evidence", "access", "secret", client=client)
    key = storage.save(user_id=uuid4(), evidence_id=uuid4(), data=b"csv data", media_type="text/csv")
    assert client.put_object.call_args.kwargs["ContentType"] == "text/csv"
    assert client.put_object.call_args.kwargs["IfNoneMatch"] == "*"
    assert "https://" not in key
    client.get_object.return_value = {"Body": MagicMock(read=lambda: b"csv data")}
    assert storage.read(key) == b"csv data"
    storage.delete(key)
    client.delete_object.assert_called_once_with(Bucket="evidence", Key=key)


def test_r2_client_failure_is_wrapped() -> None:
    from botocore.exceptions import ClientError  # type: ignore[import-untyped]

    client = MagicMock()
    client.put_object.side_effect = ClientError({"Error": {"Code": "Denied", "Message": "denied"}}, "PutObject")
    storage = R2EvidenceStorage("account", "evidence", "access", "secret", client=client)
    with pytest.raises(EvidenceStorageError):
        storage.save(user_id=uuid4(), evidence_id=uuid4(), data=b"data", media_type="text/csv")
