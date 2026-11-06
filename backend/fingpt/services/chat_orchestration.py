"""Backend chat orchestration: route one message to its specialist agent.

The optional LLM router classifies intent only; it never creates, edits, or
confirms a transaction, and any inference failure or invalid output falls back
to the deterministic classifier below. Transaction intake still always goes
through deterministic parsing (`domain/text_interpretation.py`) and an explicit,
version-checked user confirmation — nothing here can skip that.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from fingpt.adapters.anthropic_insight import InsightInferenceError
from fingpt.adapters.anthropic_router import RouterInferenceError


class _Router(Protocol):
    """Structural type for anything that can classify one chat message."""

    def classify(self, *, message: str, has_pending_clarification: bool) -> str: ...


class _Narrator(Protocol):
    """Structural type for anything that can phrase one spending comparison."""

    def narrate(self, *, data: dict[str, Any], language: str) -> str: ...


class ChatIntent(StrEnum):
    """Supported specialist routes for a unified chat message."""

    QUESTION = "question"
    TRANSACTION = "transaction"
    CLARIFICATION = "clarification"
    INSIGHT = "insight"


_QUESTION_SIGNAL = re.compile(
    r"\?|^\s*(what|how|why|when|where|who|which|is|are|do|does|did|can|could|would|should|explain|define)\b"
    r"|\b(bao nhiêu|là gì|tại sao|như thế nào|có thể|có nên|vì sao|khi nào|ở đâu)\b",
    re.IGNORECASE,
)
_SUMMARY_SIGNAL = re.compile(
    r"\b(tổng|tháng này|tháng trước|dòng tiền|chi tiêu|thu nhập|expenses?|income|spending|this month)\b",
    re.IGNORECASE,
)
_TRANSACTION_SIGNAL = re.compile(
    r"\b(add|record|log|spent|paid|bought|purchased|earned|received salary)\b"
    r"|\b(ghi|nhập|lưu)(?:\s+(?:lại|giao dịch|khoản))?\b"
    r"|\b(chi(?=\s+(?:\d|tiền|khoản))|mua|ăn|uống|trả tiền|đổ xăng|nạp|nhận tiền|lương)\b",
    re.IGNORECASE,
)

_ROUTER_INTENT_MAP: dict[str, ChatIntent] = {
    "transaction": ChatIntent.TRANSACTION,
    "qa": ChatIntent.QUESTION,
    "insight": ChatIntent.INSIGHT,
    "clarification": ChatIntent.CLARIFICATION,
}


def resolve_chat_intent(message: str) -> ChatIntent:
    """Route explicit questions to Q&A and transaction language to draft intake.

    Ambiguous messages default to read-only Q&A. Transaction input is still
    parsed and validated by the deterministic draft service before persistence.
    This is the resilience fallback used when no LLM router is configured, or
    when the router is unavailable or returns an unsupported label.
    """
    if is_explicit_question(message):
        return ChatIntent.QUESTION
    if _TRANSACTION_SIGNAL.search(message):
        return ChatIntent.TRANSACTION
    if _SUMMARY_SIGNAL.search(message):
        return ChatIntent.QUESTION
    return ChatIntent.QUESTION


def is_explicit_question(message: str) -> bool:
    """Identify direct question phrasing before interpreting draft follow-ups."""
    return bool(_QUESTION_SIGNAL.search(message))


class ChatOrchestrator:
    """Pick one specialist per message; degrade to the deterministic router on any failure."""

    def __init__(self, router: _Router | None) -> None:
        """Hold an optional LLM router; `None` means deterministic-only routing."""
        self._router = router

    def route(self, message: str, *, has_pending_clarification: bool = False) -> ChatIntent:
        """Classify with the LLM router when configured, else the deterministic fallback."""
        if self._router is not None:
            try:
                label = self._router.classify(message=message, has_pending_clarification=has_pending_clarification)
            except RouterInferenceError:
                return resolve_chat_intent(message)
            intent = _ROUTER_INTENT_MAP.get(label)
            if intent is not None:
                return intent
        return resolve_chat_intent(message)


class InsightSpecialist:
    """Read-only spending-trend narration; has no reference to any write path."""

    def __init__(
        self,
        narrator: _Narrator | None,
        spending_trend: Callable[[Session, UUID], dict[str, Any]],
    ) -> None:
        """Hold a deterministic aggregator and an optional narrator for phrasing."""
        self._narrator = narrator
        self._spending_trend = spending_trend

    def narrate(self, database: Session, user_id: UUID, *, language: str) -> str:
        """Phrase this month vs. last month's spending change; never writes anything."""
        trend = self._spending_trend(database, user_id)
        if self._narrator is not None:
            try:
                return self._narrator.narrate(data=trend, language=language)
            except InsightInferenceError:
                pass
        return _template_sentence(trend, language)


def _template_sentence(trend: dict[str, Any], language: str) -> str:
    """Produce a safe, deterministic sentence when no narrator is configured or it fails."""
    categories = trend.get("categories", [])
    top = next((row for row in categories if row.get("percentChange") is not None), None)
    if top is None:
        return "Not enough history yet to compare months." if language == "en" else "Chưa đủ dữ liệu để so sánh tháng."
    change = top["percentChange"]
    percent = abs(round(change))
    if language == "en":
        direction = "up" if change >= 0 else "down"
        return f"{top['category']} spending is {direction} {percent}% from last month."
    direction = "tăng" if change >= 0 else "giảm"
    return f"Chi tiêu {top['category']} {direction} {percent}% so với tháng trước."
