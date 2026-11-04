"""Service Bus dead-letter inspection and redrive tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.servicebus.exceptions import ServiceBusError

from fingpt.service_bus_operations import (
    _copy_message,
    _dead_letter_metadata,
    inspect_dead_letters,
    redrive_first_dead_letter,
)


def _received_message() -> SimpleNamespace:
    return SimpleNamespace(
        application_properties={"message_type": "telegram.text.accepted"},
        body=[b'{"receiptId":"abc","sourceText":"private financial text"}'],
        content_type="application/json",
        correlation_id="correlation-1",
        dead_letter_reason="Invalid or unsupported FinGPT event",
        delivery_count=10,
        enqueued_time_utc=datetime(2026, 11, 2, tzinfo=UTC),
        message_id="telegram:123:456",
        partition_key=None,
        reply_to=None,
        reply_to_session_id=None,
        sequence_number=789,
        session_id=None,
        subject=None,
        time_to_live=timedelta(hours=1),
        to=None,
    )


def test_dead_letter_metadata_omits_body_and_bounds_untrusted_labels() -> None:
    message = _received_message()
    summary = _dead_letter_metadata(message)
    assert summary == {
        "sequence": 789,
        "message_id": "telegram:123:456",
        "message_type": "telegram.text.accepted",
        "delivery_count": 10,
        "reason": "Invalid or unsupported FinGPT event",
        "enqueued": "2026-11-02T00:00:00+00:00",
    }
    assert "private financial text" not in str(summary)
    message.dead_letter_reason = "x\n" * 200
    reason = _dead_letter_metadata(message)["reason"]
    assert isinstance(reason, str) and len(reason) <= 128


def test_message_copy_preserves_body_and_stable_identity() -> None:
    original = _received_message()

    copied = _copy_message(original)

    assert isinstance(copied, ServiceBusMessage)
    assert copied.message_id == original.message_id
    assert copied.application_properties == original.application_properties
    assert copied.time_to_live == original.time_to_live
    assert b"private financial text" in b"".join(copied.body)


def test_inspection_uses_peek_on_dead_letter_subqueue() -> None:
    message = _received_message()
    client = MagicMock()
    client.__enter__.return_value = client
    receiver = client.get_queue_receiver.return_value.__enter__.return_value
    receiver.peek_messages.return_value = [message]
    with patch.object(ServiceBusClient, "from_connection_string", return_value=client) as connect:
        result = inspect_dead_letters("test-secret", "events", limit=7)
    connect.assert_called_once_with("test-secret")
    client.get_queue_receiver.assert_called_once()
    assert receiver.peek_messages.call_args.kwargs == {"max_message_count": 7}
    assert result[0]["sequence"] == 789


def test_redrive_copies_then_completes_and_retains_message_id() -> None:
    message = _received_message()
    client = MagicMock()
    client.__enter__.return_value = client
    receiver = client.get_queue_receiver.return_value.__enter__.return_value
    receiver.receive_messages.return_value = [message]
    sender = client.get_queue_sender.return_value.__enter__.return_value
    with patch.object(ServiceBusClient, "from_connection_string", return_value=client):
        result = redrive_first_dead_letter("test-secret", "events", expected_sequence=789)
    sent = sender.send_messages.call_args.args[0]
    assert sent.message_id == message.message_id
    assert result is not None and result["message_id"] == message.message_id
    receiver.complete_message.assert_called_once_with(message)
    receiver.abandon_message.assert_not_called()


def test_redrive_abandons_locked_message_if_send_fails() -> None:
    message = _received_message()
    client = MagicMock()
    client.__enter__.return_value = client
    receiver = client.get_queue_receiver.return_value.__enter__.return_value
    receiver.receive_messages.return_value = [message]
    sender = client.get_queue_sender.return_value.__enter__.return_value
    sender.send_messages.side_effect = ServiceBusError("failed")
    with (
        patch.object(ServiceBusClient, "from_connection_string", return_value=client),
        pytest.raises(ServiceBusError),
    ):
        redrive_first_dead_letter("test-secret", "events", expected_sequence=789)
    receiver.abandon_message.assert_called_once_with(message)
    receiver.complete_message.assert_not_called()


def test_redrive_does_not_copy_a_message_other_than_the_confirmed_sequence() -> None:
    message = _received_message()
    client = MagicMock()
    client.__enter__.return_value = client
    receiver = client.get_queue_receiver.return_value.__enter__.return_value
    receiver.receive_messages.return_value = [message]
    with patch.object(ServiceBusClient, "from_connection_string", return_value=client):
        result = redrive_first_dead_letter("test-secret", "events", expected_sequence=790)
    assert result is None
    receiver.abandon_message.assert_called_once_with(message)
    client.get_queue_sender.assert_not_called()
