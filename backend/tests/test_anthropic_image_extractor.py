"""Structured receipt extraction boundary tests."""

from typing import Any

import pytest

from fingpt.adapters.anthropic_image_extractor import ImageInferenceError, _extract_output

_VALID: dict[str, Any] = {
    "description": "Lunch",
    "amount": "15000",
    "currency": "VND",
    "transactionDate": None,
    "dayPeriod": None,
    "localTime": None,
    "kind": "expense",
}


def test_structured_output_accepts_complete_receipt() -> None:
    assert _extract_output(_VALID) == _VALID


@pytest.mark.parametrize("payload", [{}, {**_VALID, "amount": 15000}, {**_VALID, "kind": "other"}])
def test_structured_output_rejects_unusable_data(payload: dict[str, Any]) -> None:
    with pytest.raises(ImageInferenceError):
        _extract_output(payload)
