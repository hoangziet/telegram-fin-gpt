"""Tests for non-secret environment references."""

import pytest

from fingpt.services.runtime_secrets import resolve_environment_secret


def test_secret_reference_maps_to_runtime_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINGPT_SECRET_TELEGRAM__BOT_TOKEN", "test-token")
    assert resolve_environment_secret("telegram/bot-token") == "test-token"


def test_invalid_secret_reference_is_not_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINGPT_SECRET_TELEGRAM__BOT_TOKEN", "test-token")
    assert resolve_environment_secret("../telegram/bot-token") is None
