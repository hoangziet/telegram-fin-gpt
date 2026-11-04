"""Create Service Bus clients with local secrets or production managed identity."""

from __future__ import annotations

import os

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient


def create_service_bus_client(connection_string: str | None = None) -> ServiceBusClient:
    """Use managed identity in production and allow connection strings in development."""
    is_production = os.environ.get("FINGPT_DEPLOYMENT_ENV", "").strip().lower() == "production"
    if is_production and connection_string:
        raise RuntimeError("Production Service Bus access must use managed identity.")

    local_connection_string = connection_string or (
        os.environ.get("SERVICE_BUS_CONNECTION_STRING") if not is_production else None
    )
    if local_connection_string:
        return ServiceBusClient.from_connection_string(local_connection_string)

    namespace = os.environ.get("SERVICE_BUS_NAMESPACE")
    if not namespace:
        raise RuntimeError("SERVICE_BUS_NAMESPACE is required for managed identity access.")

    return ServiceBusClient(
        fully_qualified_namespace=namespace,
        credential=DefaultAzureCredential(),
    )
