"""Azure AI Foundry Responses output boundary tests."""

from typing import Any

import pytest

from fingpt.adapters.foundry_image_extractor import ImageInferenceError, _extract_output


def test_responses_api_output_reads_only_assistant_structured_text() -> None:
    result = _extract_output(
        {
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"description":"Lunch","amount":"15000"}',
                        }
                    ],
                },
            ],
        }
    )

    assert result == {"description": "Lunch", "amount": "15000"}


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "failed", "output": []},
        {"status": "completed", "output": []},
        {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "not JSON"}]}],
        },
        {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '["unexpected"]'}]}],
        },
    ],
)
def test_responses_api_rejects_unusable_output(payload: dict[str, Any]) -> None:
    with pytest.raises(ImageInferenceError):
        _extract_output(payload)
