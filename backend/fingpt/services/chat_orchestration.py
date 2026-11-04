"""Conservative backend routing between the two chat specialist agents."""

from __future__ import annotations

import re
from enum import StrEnum


class ChatIntent(StrEnum):
    """Supported specialist routes for a unified chat message."""

    QUESTION = "question"
    TRANSACTION = "transaction"
    CLARIFICATION = "clarification"


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


def resolve_chat_intent(message: str) -> ChatIntent:
    """Route explicit questions to Q&A and transaction language to draft intake.

    Ambiguous messages default to read-only Q&A. Transaction input is still
    parsed and validated by the deterministic draft service before persistence.
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
