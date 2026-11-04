"""Service Bus credentials follow the deployment trust boundary."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from azure.servicebus import ServiceBusClient

from fingpt.adapters.service_bus_client import create_service_bus_client


def test_local_connection_string_authentication_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINGPT_DEPLOYMENT_ENV", "development")
    client = MagicMock()
    with patch.object(ServiceBusClient, "from_connection_string", return_value=client) as connect:
        result = create_service_bus_client("local-secret")
    assert result is client
    connect.assert_called_once_with("local-secret")


def test_production_uses_managed_identity_for_configured_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINGPT_DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("SERVICE_BUS_NAMESPACE", "fingpt-prod.servicebus.windows.net")
    client = MagicMock()
    credential = MagicMock()
    with (
        patch("fingpt.adapters.service_bus_client.DefaultAzureCredential", return_value=credential),
        patch("fingpt.adapters.service_bus_client.ServiceBusClient", return_value=client) as connect,
    ):
        result = create_service_bus_client()
    assert result is client
    connect.assert_called_once_with(
        fully_qualified_namespace="fingpt-prod.servicebus.windows.net",
        credential=credential,
    )


def test_production_rejects_connection_string_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINGPT_DEPLOYMENT_ENV", "production")
    with pytest.raises(RuntimeError, match="managed identity"):
        create_service_bus_client("should-not-be-used")


def test_missing_service_bus_namespace_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINGPT_DEPLOYMENT_ENV", "production")
    monkeypatch.delenv("SERVICE_BUS_NAMESPACE", raising=False)
    with pytest.raises(RuntimeError, match="SERVICE_BUS_NAMESPACE"):
        create_service_bus_client()
