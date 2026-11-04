"""Queue worker for transactional outbox delivery and Telegram updates."""

from __future__ import annotations

import json
import os

from azure.servicebus.exceptions import ServiceBusError
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fingpt.adapters.service_bus import AzureServiceBusPublisher, MessagePublishError
from fingpt.adapters.service_bus_client import create_service_bus_client
from fingpt.adapters.telegram import TelegramDeliveryError
from fingpt.services.outbox import dispatch_outbox_batch
from fingpt.services.telegram_worker import (
    PermanentMessageError,
    deliver_telegram_outbox_event,
    process_telegram_callback,
    process_telegram_text_event,
)


def main() -> None:
    """Run the outbox dispatcher and peek-lock queue consumer indefinitely."""
    database_url = _required_environment("DATABASE_URL")
    queue_name = _required_environment("SERVICE_BUS_QUEUE_NAME")
    engine = create_engine(database_url, pool_pre_ping=True)
    connection_string = os.environ.get("SERVICE_BUS_CONNECTION_STRING")
    client = create_service_bus_client(connection_string)
    publisher = AzureServiceBusPublisher(client, queue_name)
    try:
        with client.get_queue_receiver(queue_name, max_wait_time=5) as receiver:
            while True:
                with Session(engine, expire_on_commit=False) as database:
                    dispatch_outbox_batch(database, publisher)
                messages = receiver.receive_messages(max_message_count=10, max_wait_time=5)
                for message in messages:
                    try:
                        payload = json.loads(str(message))
                        if not isinstance(payload, dict):
                            raise PermanentMessageError("Queue event must be a JSON object.")
                        event_type = payload.get("eventType")
                        if not isinstance(event_type, str):
                            raise PermanentMessageError("FinGPT event type is invalid.")
                        _process_message(engine, event_type, payload)
                    except (json.JSONDecodeError, PermanentMessageError):
                        receiver.dead_letter_message(message, reason="Invalid or unsupported FinGPT event")
                    except (SQLAlchemyError, MessagePublishError, TelegramDeliveryError, ServiceBusError):
                        receiver.abandon_message(message)
                    else:
                        receiver.complete_message(message)
    finally:
        client.close()
        engine.dispose()


def _process_message(engine: Engine, event_type: str, payload: dict[str, object]) -> None:
    """Apply one supported queue event in a fresh database session."""
    with Session(engine, expire_on_commit=False) as database:
        if event_type == "telegram.text.accepted":
            process_telegram_text_event(database, payload)
        elif event_type == "telegram.callback.accepted":
            process_telegram_callback(database, payload)
        elif event_type in {
            "telegram.draft.ready",
            "telegram.change.preview",
            "telegram.message.send",
        }:
            deliver_telegram_outbox_event(database, payload)
        else:
            raise PermanentMessageError("FinGPT queue message type is unsupported.")


def _required_environment(name: str) -> str:
    """Load a required setting without revealing it in errors."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required worker setting {name} is missing.")
    return value


if __name__ == "__main__":
    main()
