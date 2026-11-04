"""Minimal Telegram Bot API client for sending private draft previews."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import UUID

from fingpt.persistence.models import DraftItem, Transaction, TransactionChange


class TelegramDeliveryError(RuntimeError):
    """Raised when Telegram did not acknowledge an outbound message."""


class TelegramBotApi:
    """Send text previews with item-scoped confirmation and cancellation actions."""

    def __init__(self, secret_resolver: Callable[[str], str | None]) -> None:
        """Create the client with an injected bot-token resolver."""
        self._secret_resolver = secret_resolver

    def send_draft_preview(
        self,
        *,
        secret_name: str,
        chat_id: int,
        draft_id: UUID,
        items: tuple[DraftItem, ...],
    ) -> None:
        """Send an itemized preview and buttons for independently ready items."""
        token = self._secret_resolver(secret_name)
        if not token:
            raise TelegramDeliveryError("Telegram bot token is unavailable.")
        lines = ["Đây là bản xem trước. Chưa có khoản nào được ghi:"]
        keyboard: list[list[dict[str, str]]] = []
        for index, item in enumerate(items, start=1):
            data = item.extracted_data
            amount = data["amount"]
            money = f"{amount} {data['currency']}" if amount is not None else "thiếu số tiền"
            date_label = data["transactionDate"] or "thiếu ngày"
            period_label = data["dayPeriod"] or "thiếu buổi"
            description = data["description"] or "thiếu nội dung"
            lines.append(f"{index}. {description} — {money} — {date_label}, {period_label}")
            actions = []
            if item.status == "ready":
                actions.append(
                    {
                        "text": f"Xác nhận khoản {index}",
                        "callback_data": _confirmation_callback(draft_id, item.id, item.version),
                    }
                )
            if item.status in {"incomplete", "ready"}:
                actions.append(
                    {
                        "text": f"Hủy khoản {index}",
                        "callback_data": _cancellation_callback(draft_id, item.id, item.version),
                    }
                )
            if actions:
                keyboard.append(actions)
        message = "\n".join(lines)
        if len(message) > 3_900:
            message = message[:3_850] + "\n… Mở FinGPT để xem đầy đủ bản xem trước."
        self._call(
            token,
            {
                "chat_id": chat_id,
                "text": message,
                "reply_markup": {"inline_keyboard": keyboard} if keyboard else None,
            },
        )

    def send_text(self, *, secret_name: str, chat_id: int, text: str) -> None:
        """Send a plain-text status message with optional inline actions."""
        self.send_message(secret_name=secret_name, chat_id=chat_id, text=text)

    def send_message(
        self,
        *,
        secret_name: str,
        chat_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        """Send a bounded message and optional validated Bot API keyboard."""
        token = self._secret_resolver(secret_name)
        if not token:
            raise TelegramDeliveryError("Telegram bot token is unavailable.")
        payload: dict[str, object] = {"chat_id": chat_id, "text": text[:3_900]}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._call(token, payload)

    def send_change_preview(
        self,
        *,
        secret_name: str,
        chat_id: int,
        transaction: Transaction,
        change: TransactionChange,
    ) -> None:
        """Show the current and proposed transaction states before an undo write."""
        before = _transaction_description(
            transaction.note,
            format(transaction.amount, "f"),
            transaction.currency,
            transaction.transaction_date.isoformat(),
            transaction.day_period,
            transaction.local_time.isoformat(timespec="minutes") if transaction.local_time else None,
        )
        state = change.proposed_state
        after = _transaction_description(
            str(state["note"]) if state["note"] is not None else None,
            str(state["amount"]),
            str(state["currency"]),
            str(state["transactionDate"]),
            str(state["dayPeriod"]),
            str(state["localTime"]) if state["localTime"] is not None else None,
        )
        notice = "\nGiao dịch sẽ được ẩn khỏi sổ và báo cáo." if state["isDeleted"] else ""
        self.send_message(
            secret_name=secret_name,
            chat_id=chat_id,
            text=(
                "Xem trước hoàn tác. Chưa có thay đổi nào được ghi:\n"
                f"Hiện tại: {before}\nSau khi xác nhận: {after}{notice}"
            ),
            reply_markup={
                "inline_keyboard": [
                    [
                        {
                            "text": "Xác nhận hoàn tác",
                            "callback_data": _change_callback("tc", change.id, change.version),
                        },
                        {
                            "text": "Hủy",
                            "callback_data": _change_callback("tx", change.id, change.version),
                        },
                    ]
                ]
            },
        )

    @staticmethod
    def _call(token: str, payload: dict[str, object]) -> None:
        """Call the fixed Telegram API host and reject unsuccessful responses."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                result = json.loads(response.read())
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise TelegramDeliveryError("Telegram did not accept the outbound message.") from error
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise TelegramDeliveryError("Telegram did not accept the outbound message.")


def _confirmation_callback(draft_id: UUID, item_id: UUID, version: int) -> str:
    """Encode UUIDs compactly to fit Telegram's 64-byte callback data limit."""
    draft_token = _encode_uuid(draft_id)
    item_token = _encode_uuid(item_id)
    return f"cf.{draft_token}.{item_token}.{version:08x}"


def _cancellation_callback(draft_id: UUID, item_id: UUID, version: int) -> str:
    """Encode a versioned cancellation action within Telegram's callback limit."""
    draft_token = _encode_uuid(draft_id)
    item_token = _encode_uuid(item_id)
    return f"cx.{draft_token}.{item_token}.{version:08x}"


def _undo_callback(transaction_id: UUID, version: int) -> str:
    """Encode a version-bound undo request; the callback itself never writes."""
    return f"un.{_encode_uuid(transaction_id)}.{version:08x}"


def _change_callback(action: str, change_id: UUID, version: int) -> str:
    """Encode a version-bound confirmation or cancellation of a change preview."""
    if action not in {"tc", "tx"}:
        raise ValueError("Unsupported Telegram transaction-change action.")
    return f"{action}.{_encode_uuid(change_id)}.{version:08x}"


def _transaction_description(
    note: str | None,
    amount: str,
    currency: str,
    transaction_date: str,
    day_period: str,
    local_time: str | None,
) -> str:
    """Format a short, exact current or proposed transaction description."""
    period_labels = {"morning": "Sáng", "noon": "Trưa", "afternoon": "Chiều", "evening": "Tối"}
    details = [note or "Chưa có nội dung", f"{amount} {currency}", transaction_date, period_labels[day_period]]
    if local_time is not None:
        details.append(local_time)
    return " · ".join(details)


def _encode_uuid(value: UUID) -> str:
    """Encode a UUID as unpadded URL-safe base64."""
    from base64 import urlsafe_b64encode

    return urlsafe_b64encode(value.bytes).decode("ascii").rstrip("=")
