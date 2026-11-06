"""Worker event dispatch mapping tests."""

from fingpt.services.telegram_worker import (
    deliver_telegram_outbox_event,
    process_telegram_callback,
    process_telegram_text_event,
)
from fingpt.worker import _handlers


def test_worker_maps_all_supported_events() -> None:
    assert _handlers() == {
        "telegram.text.accepted": process_telegram_text_event,
        "telegram.callback.accepted": process_telegram_callback,
        "telegram.draft.ready": deliver_telegram_outbox_event,
        "telegram.change.preview": deliver_telegram_outbox_event,
        "telegram.message.send": deliver_telegram_outbox_event,
    }
