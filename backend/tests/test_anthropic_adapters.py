"""Anthropic forced-tool and narrative failure boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fingpt.adapters.anthropic_image_extractor import AnthropicImageExtractor, ImageInferenceError
from fingpt.adapters.anthropic_insight import AnthropicInsight, InsightInferenceError
from fingpt.adapters.anthropic_router import AnthropicRouter, RouterInferenceError


def test_router_requires_matching_valid_tool_use() -> None:
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", name="classify_intent", input={"intent": "qa", "confidence": 0.9})],
    )
    router = AnthropicRouter("key", "model", client=client)
    assert router.classify(message="How much?", has_pending_clarification=False) == "qa"
    assert client.messages.create.call_args.kwargs["tool_choice"] == {"type": "tool", "name": "classify_intent"}
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="tool_use", content=[SimpleNamespace(type="text", text="qa")]
    )
    with pytest.raises(RouterInferenceError):
        router.classify(message="How much?", has_pending_clarification=False)


def test_image_extractor_sends_native_image_and_requires_tool() -> None:
    client = MagicMock()
    values = {
        "description": "Lunch",
        "amount": "15000",
        "currency": "VND",
        "transactionDate": None,
        "dayPeriod": None,
        "localTime": None,
        "kind": "expense",
    }
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", name="extract_receipt", input=values)]
    )
    extractor = AnthropicImageExtractor("key", "model", client=client)
    assert extractor.extract(image=b"image", media_type="image/png") == values
    request = client.messages.create.call_args.kwargs
    assert request["messages"][0]["content"][1]["source"]["data"] == "aW1hZ2U="
    assert request["tool_choice"] == {"type": "tool", "name": "extract_receipt"}
    client.messages.create.return_value = SimpleNamespace(stop_reason="tool_use", content=[])
    with pytest.raises(ImageInferenceError):
        extractor.extract(image=b"image", media_type="image/png")


def test_insight_rejects_income_mislabeling() -> None:
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Revenue increased.")]
    )
    insight = AnthropicInsight("key", "model", client=client)
    with pytest.raises(InsightInferenceError):
        insight.narrate(data={}, language="en")
