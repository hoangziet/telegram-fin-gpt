"""Direct database-polling worker for transactional outbox events."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from fingpt.services.outbox import process_outbox_batch
from fingpt.services.telegram_worker import (
    PermanentMessageError,
    deliver_telegram_outbox_event,
    process_telegram_callback,
    process_telegram_text_event,
)


def _handlers() -> dict[str, Callable[[Session, dict[str, object]], object]]:
    """Map persisted event types to their existing idempotent handlers."""
    return {
        "telegram.text.accepted": process_telegram_text_event,
        "telegram.callback.accepted": process_telegram_callback,
        "telegram.draft.ready": deliver_telegram_outbox_event,
        "telegram.change.preview": deliver_telegram_outbox_event,
        "telegram.message.send": deliver_telegram_outbox_event,
    }


def main() -> None:
    """Poll and process pending outbox rows indefinitely."""
    database_url = _required_environment("DATABASE_URL")
    try:
        poll_interval = float(os.environ.get("FINGPT_WORKER_POLL_INTERVAL_SECONDS", "5"))
        if poll_interval <= 0 or not math.isfinite(poll_interval):
            poll_interval = 5.0
    except ValueError:
        poll_interval = 5.0
    engine = create_engine(database_url, pool_pre_ping=True)
    handlers = _handlers()
    try:
        while True:
            with Session(engine, expire_on_commit=False) as database:
                process_outbox_batch(database, handlers)
            time.sleep(poll_interval)
    finally:
        engine.dispose()


def _process_message(engine: Engine, event_type: str, payload: dict[str, object]) -> None:
    """Apply one event in a fresh session for existing direct-call workflows."""
    handler = _handlers().get(event_type)
    if handler is None:
        raise PermanentMessageError("FinGPT event type is unsupported.")
    with Session(engine, expire_on_commit=False) as database:
        handler(database, payload)


def _required_environment(name: str) -> str:
    """Load a required setting without revealing it in errors."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required worker setting {name} is missing.")
    return value


if __name__ == "__main__":
    main()
