"""Orchestrator routing: LLM router happy paths and deterministic fallback."""

from __future__ import annotations

from typing import Any

import pytest

from fingpt.adapters.anthropic_insight import InsightInferenceError, _extract_text
from fingpt.adapters.anthropic_router import RouterInferenceError
from fingpt.services.chat_orchestration import ChatIntent, ChatOrchestrator, InsightSpecialist


class StubRouter:
    """A router stub that returns a fixed label or raises a fixed error."""

    def __init__(self, *, label: str | None = None, error: Exception | None = None) -> None:
        self._label = label
        self._error = error
        self.calls: list[tuple[str, bool]] = []

    def classify(self, *, message: str, has_pending_clarification: bool) -> str:
        self.calls.append((message, has_pending_clarification))
        if self._error is not None:
            raise self._error
        assert self._label is not None
        return self._label


def test_orchestrator_uses_router_label_when_configured() -> None:
    router = StubRouter(label="insight")
    orchestrator = ChatOrchestrator(router)
    assert orchestrator.route("So sánh chi tiêu tháng này với tháng trước") is ChatIntent.INSIGHT
    assert router.calls == [("So sánh chi tiêu tháng này với tháng trước", False)]


def test_orchestrator_passes_pending_clarification_hint_to_router() -> None:
    router = StubRouter(label="clarification")
    orchestrator = ChatOrchestrator(router)
    assert orchestrator.route("sáng hôm qua", has_pending_clarification=True) is ChatIntent.CLARIFICATION
    assert router.calls == [("sáng hôm qua", True)]


def test_orchestrator_falls_back_to_regex_when_router_errors() -> None:
    router = StubRouter(error=RouterInferenceError("down"))
    orchestrator = ChatOrchestrator(router)
    assert orchestrator.route("Ăn phở 50k") is ChatIntent.TRANSACTION
    assert orchestrator.route("How much did I spend this month?") is ChatIntent.QUESTION


def test_orchestrator_falls_back_to_regex_on_unsupported_label() -> None:
    router = StubRouter(label="unsupported-label")
    orchestrator = ChatOrchestrator(router)
    assert orchestrator.route("Ăn phở 50k") is ChatIntent.TRANSACTION


def test_orchestrator_is_deterministic_without_a_configured_router() -> None:
    orchestrator = ChatOrchestrator(None)
    assert orchestrator.route("Ăn phở 50k") is ChatIntent.TRANSACTION
    assert orchestrator.route("How much did I spend this month?") is ChatIntent.QUESTION


class StubNarrator:
    """A narrator stub that returns a fixed sentence or raises a fixed error."""

    def __init__(self, *, sentence: str | None = None, error: Exception | None = None) -> None:
        self._sentence = sentence
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def narrate(self, *, data: dict[str, Any], language: str) -> str:
        self.calls.append({"data": data, "language": language})
        if self._error is not None:
            raise self._error
        assert self._sentence is not None
        return self._sentence


_TREND = {
    "currentPeriod": "2026-11",
    "previousPeriod": "2026-10",
    "timezone": "Asia/Ho_Chi_Minh",
    "categories": [
        {
            "category": "Ăn uống",
            "currency": "VND",
            "currentAmount": "50000",
            "previousAmount": "20000",
            "percentChange": 150.0,
        },
    ],
}


@pytest.mark.parametrize(
    "sentence",
    [
        "Doanh Thu tăng 150%.",
        "THU NHẬP tăng 150%.",
        "Revenue is up 150%.",
        "INCOME is up 150%.",
        "Monthly income, up 150%.",
    ],
)
def test_insight_rejects_income_labels(sentence: str) -> None:
    with pytest.raises(InsightInferenceError, match="mislabeled spending"):
        _extract_text(sentence)


@pytest.mark.parametrize(
    "sentence",
    ["Chi tiêu tăng 150%.", "Spending is up 150%.", "Spending for Incomer is up 150%."],
)
def test_insight_accepts_spending_without_income_word(sentence: str) -> None:
    assert _extract_text(sentence) == sentence


def test_insight_specialist_uses_narrator_output_when_available() -> None:
    narrator = StubNarrator(sentence="Chi tiêu Ăn uống tăng 150% so với tháng trước.")
    specialist = InsightSpecialist(narrator, lambda _database, _user_id: _TREND)
    answer = specialist.narrate(object(), object(), language="vi")  # type: ignore[arg-type]
    assert answer == "Chi tiêu Ăn uống tăng 150% so với tháng trước."
    assert narrator.calls == [{"data": _TREND, "language": "vi"}]


def test_insight_specialist_falls_back_to_template_when_narrator_fails() -> None:
    narrator = StubNarrator(error=InsightInferenceError("down"))
    specialist = InsightSpecialist(narrator, lambda _database, _user_id: _TREND)
    answer = specialist.narrate(object(), object(), language="en")  # type: ignore[arg-type]
    assert answer == "Ăn uống spending is up 150% from last month."


def test_insight_specialist_falls_back_to_template_without_a_configured_narrator() -> None:
    specialist = InsightSpecialist(None, lambda _database, _user_id: _TREND)
    assert specialist.narrate(object(), object(), language="vi") == "Chi tiêu Ăn uống tăng 150% so với tháng trước."  # type: ignore[arg-type]


def test_insight_specialist_template_handles_no_comparable_category() -> None:
    empty_trend: dict[str, Any] = {"categories": []}
    specialist = InsightSpecialist(None, lambda _database, _user_id: empty_trend)
    assert specialist.narrate(object(), object(), language="en") == "Not enough history yet to compare months."  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("transaction", ChatIntent.TRANSACTION),
        ("qa", ChatIntent.QUESTION),
        ("insight", ChatIntent.INSIGHT),
        ("clarification", ChatIntent.CLARIFICATION),
    ],
)
def test_router_label_maps_to_the_expected_chat_intent(label: str, expected: ChatIntent) -> None:
    router = StubRouter(label=label)
    orchestrator = ChatOrchestrator(router)
    assert orchestrator.route("irrelevant") is expected
