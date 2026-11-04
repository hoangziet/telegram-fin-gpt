"""Read and redrive dead-letter messages through explicit operator commands."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

import click
from azure.servicebus import ServiceBusMessage, ServiceBusSubQueue
from azure.servicebus.exceptions import ServiceBusError
from flask import Flask

from fingpt.adapters.service_bus_client import create_service_bus_client


def register_service_bus_operations(app: Flask) -> None:
    """Register bounded dead-letter inspection and explicitly confirmed redrive."""

    @app.cli.group("queue")
    def queue_commands() -> None:
        """Inspect or recover Service Bus queue events."""

    @queue_commands.command("dead-letters")
    @click.option("--limit", type=click.IntRange(min=1, max=100), default=30, show_default=True)
    def list_dead_letters(limit: int) -> None:
        """List dead-letter metadata without printing message bodies."""
        connection_string, queue_name = _queue_configuration()
        try:
            rows = inspect_dead_letters(connection_string, queue_name, limit=limit)
        except ServiceBusError as error:
            raise click.ClickException("Service Bus dead-letter inspection failed.") from error
        if not rows:
            click.echo("No dead-letter messages found.")
            return
        for row in rows:
            click.echo(
                f"sequence={row['sequence']} message_id={row['message_id']} "
                f"type={row['message_type']} delivery_count={row['delivery_count']} "
                f"reason={row['reason']} enqueued={row['enqueued']}"
            )

    @queue_commands.command("replay-next")
    def replay_next_dead_letter() -> None:
        """Redrive the next dead-letter event after operator confirmation."""
        connection_string, queue_name = _queue_configuration()
        try:
            metadata = peek_first_dead_letter(connection_string, queue_name)
        except ServiceBusError as error:
            raise click.ClickException("Service Bus dead-letter inspection failed.") from error
        if metadata is None:
            click.echo("No dead-letter messages found.")
            return
        click.echo(
            f"Next message: id={metadata['message_id']} sequence={metadata['sequence']} "
            f"type={metadata['message_type']} reason={metadata['reason']}"
        )
        if not click.confirm("Redrive this message to the active queue?", default=False):
            click.echo("No message was changed.")
            return
        try:
            sequence = metadata["sequence"]
            if not isinstance(sequence, int) or isinstance(sequence, bool):
                raise click.ClickException("Dead-letter message has no valid sequence number.")
            redriven = redrive_first_dead_letter(connection_string, queue_name, expected_sequence=sequence)
        except ServiceBusError as error:
            raise click.ClickException("Service Bus dead-letter redrive failed.") from error
        if redriven is None:
            raise click.ClickException("The dead-letter queue changed before the message was received.")
        click.echo(f"Redriven message {redriven['message_id']} with its original message ID.")


def inspect_dead_letters(connection_string: str | None, queue_name: str, *, limit: int) -> list[dict[str, object]]:
    """Peek at dead-letter metadata while excluding message bodies."""
    with create_service_bus_client(connection_string) as client:
        with client.get_queue_receiver(queue_name, sub_queue=ServiceBusSubQueue.DEAD_LETTER) as receiver:
            messages = receiver.peek_messages(max_message_count=limit)
            return [_dead_letter_metadata(message) for message in messages]


def peek_first_dead_letter(connection_string: str | None, queue_name: str) -> dict[str, object] | None:
    """Inspect the next message that a redrive command would receive."""
    with create_service_bus_client(connection_string) as client:
        with client.get_queue_receiver(queue_name, sub_queue=ServiceBusSubQueue.DEAD_LETTER) as receiver:
            messages = receiver.peek_messages(max_message_count=1)
            return _dead_letter_metadata(messages[0]) if messages else None


def redrive_first_dead_letter(
    connection_string: str | None,
    queue_name: str,
    *,
    expected_sequence: int,
) -> dict[str, object] | None:
    """Copy and settle the next dead-letter message after confirmed invocation."""
    with create_service_bus_client(connection_string) as client:
        with client.get_queue_receiver(queue_name, sub_queue=ServiceBusSubQueue.DEAD_LETTER) as receiver:
            messages = receiver.receive_messages(max_message_count=1, max_wait_time=5)
            if not messages:
                return None
            message = messages[0]
            if message.sequence_number != expected_sequence:
                receiver.abandon_message(message)
                return None
            outgoing = _copy_message(message)
            try:
                with client.get_queue_sender(queue_name) as sender:
                    sender.send_messages(outgoing)
            except ServiceBusError:
                receiver.abandon_message(message)
                raise
            receiver.complete_message(message)
            return _dead_letter_metadata(message)


def _dead_letter_metadata(message: Any) -> dict[str, object]:
    """Extract bounded operational metadata and omit potentially private body data."""
    properties = message.application_properties or {}
    message_type = properties.get("message_type", properties.get(b"message_type"))
    return {
        "sequence": message.sequence_number,
        "message_id": _safe_label(message.message_id),
        "message_type": _safe_label(message_type),
        "delivery_count": message.delivery_count,
        "reason": _safe_label(message.dead_letter_reason),
        "enqueued": message.enqueued_time_utc.isoformat() if message.enqueued_time_utc else None,
    }


def _copy_message(message: Any) -> ServiceBusMessage:
    """Preserve the event body and stable identity while copying safe AMQP fields."""
    body = message.body
    if not isinstance(body, (str, bytes)):
        if isinstance(body, Iterable):
            body = b"".join(part for part in body if isinstance(part, bytes))
        else:
            body = str(body)
    return ServiceBusMessage(
        body,
        message_id=message.message_id,
        application_properties=message.application_properties,
        session_id=message.session_id,
        time_to_live=message.time_to_live,
        content_type=message.content_type,
        correlation_id=message.correlation_id,
        subject=message.subject,
        partition_key=message.partition_key,
        to=message.to,
        reply_to=message.reply_to,
        reply_to_session_id=message.reply_to_session_id,
    )


def _safe_label(value: object) -> str:
    """Convert short control-free metadata to text without exposing message content."""
    if value is None:
        return "-"
    text = str(value)
    return "".join(character for character in text if character.isprintable())[:128] or "-"


def _queue_configuration() -> tuple[str | None, str]:
    """Read queue connection settings without including secrets in errors."""
    is_production = os.environ.get("FINGPT_DEPLOYMENT_ENV", "").strip().lower() == "production"
    connection_string = None if is_production else os.environ.get("SERVICE_BUS_CONNECTION_STRING")
    queue_name = os.environ.get("SERVICE_BUS_QUEUE_NAME")
    namespace = os.environ.get("SERVICE_BUS_NAMESPACE")
    if not queue_name or (not connection_string and not namespace):
        raise click.ClickException("Service Bus namespace/connection and queue configuration are required.")
    return connection_string, queue_name
