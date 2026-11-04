"""Tests for synthetic Service Bus and Blob Storage cloud smoke checks."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from azure.core.exceptions import ResourceNotFoundError

from fingpt.cloud_smoke import smoke_test_blob_storage, smoke_test_service_bus


def test_smoke_test_service_bus_succeeds() -> None:
    mock_client = MagicMock()
    mock_sender = MagicMock()
    mock_receiver = MagicMock()

    mock_client.get_queue_sender.return_value.__enter__.return_value = mock_sender
    mock_client.get_queue_receiver.return_value.__enter__.return_value = mock_receiver

    def send_side_effect(msg: Any) -> None:
        mock_msg = MagicMock()
        mock_msg.message_id = msg.message_id
        mock_receiver.receive_messages.return_value = [mock_msg]

    mock_sender.send_messages.side_effect = send_side_effect

    result = smoke_test_service_bus(
        namespace_or_connection="test.servicebus.windows.net",
        queue_name="fingpt-events",
        client=mock_client,
    )

    assert result["verified"] is True
    assert result["message_id"].startswith("smoke-test:")
    mock_receiver.complete_message.assert_called_once()


def test_smoke_test_blob_storage_succeeds() -> None:
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_blob = MagicMock()

    mock_client.get_container_client.return_value = mock_container
    mock_container.get_blob_client.return_value = mock_blob

    mock_blob.download_blob.return_value.readall.return_value = b"synthetic-smoke-test-evidence-payload"
    mock_blob.get_blob_properties.side_effect = ResourceNotFoundError("Not found")

    result = smoke_test_blob_storage(
        account_url_or_connection="https://test.blob.core.windows.net",
        container_name="evidence",
        client=mock_client,
    )

    assert result["verified"] is True
    assert result["container"] == "evidence"
    mock_blob.upload_blob.assert_called_once()
    mock_blob.delete_blob.assert_called_once()
