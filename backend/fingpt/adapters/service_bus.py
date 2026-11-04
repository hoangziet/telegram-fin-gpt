"""Azure Service Bus publisher with stable message IDs and no content logging."""

from __future__ import annotations

import json
from typing import Protocol

from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.servicebus.exceptions import ServiceBusError


class MessagePublisher(Protocol):
    """Small queue boundary used by the transactional outbox dispatcher."""

    def publish(self, message_id: str, payload: dict[str, object]) -> None:
        """Publish one JSON event using its stable identifier."""


class MessagePublishError(RuntimeError):
    """Raised when the broker did not acknowledge an outbox event."""


class AzureServiceBusPublisher:
    """Synchronous Azure Service Bus queue sender."""

    def __init__(self, client: ServiceBusClient, queue_name: str) -> None:
        """Create a sender that shares the worker's configured broker client."""
        self._client = client
        self._queue_name = queue_name

    def publish(self, message_id: str, payload: dict[str, object]) -> None:
        """Send an event and wait for the broker acknowledgement."""
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            with self._client.get_queue_sender(self._queue_name) as sender:
                event_type = payload.get("eventType")
                sender.send_messages(
                    ServiceBusMessage(
                        body,
                        message_id=message_id,
                        application_properties={"message_type": event_type} if isinstance(event_type, str) else None,
                    )
                )
        except ServiceBusError as error:
            raise MessagePublishError("Service Bus did not accept the outbox event.") from error
