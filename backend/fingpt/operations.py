"""Local operator commands for inspecting and recovering background work."""

from __future__ import annotations

from uuid import UUID

import click
from flask import Flask, current_app
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from fingpt.services.outbox_operations import list_outbox_events, retry_failed_outbox_event


def register_operations(app: Flask) -> None:
    """Register bounded, payload-redacting outbox operations commands."""

    @app.cli.group("outbox")
    def outbox_commands() -> None:
        """Inspect or recover queued background events."""

    @outbox_commands.command("list")
    @click.option("--status", type=click.Choice(["pending", "published", "failed"]))
    @click.option("--limit", type=click.IntRange(min=1, max=100), default=30, show_default=True)
    def list_events(status: str | None, limit: int) -> None:
        """List event IDs and delivery metadata; never print event payloads."""
        database = _database_session()
        try:
            events = list_outbox_events(database, status=status, limit=limit)
            for event in events:
                click.echo(
                    f"{event.id} {event.status} {event.message_type} "
                    f"attempts={event.attempt_count} operator_retries={event.operator_retry_count} "
                    f"created={event.created_at.isoformat()}"
                )
            if not events:
                click.echo("No outbox events found.")
        finally:
            database.close()

    @outbox_commands.command("retry")
    @click.argument("event_id")
    def retry_event(event_id: str) -> None:
        """Return one failed event to pending without changing its idempotency key."""
        try:
            parsed_id = UUID(event_id)
        except ValueError as error:
            raise click.ClickException("event_id must be a UUID.") from error
        database = _database_session()
        try:
            try:
                event = retry_failed_outbox_event(database, parsed_id)
            except LookupError as error:
                raise click.ClickException("Failed outbox event was not found.") from error
            click.echo(f"Requeued {event.id}; stable message key retained.")
        finally:
            database.close()


def _database_session() -> Session:
    """Open a short-lived session from the configured application engine."""
    engine: Engine | None = current_app.extensions.get("database_engine")
    if engine is None:
        raise click.ClickException("Database is unavailable.")
    return Session(engine, expire_on_commit=False)
