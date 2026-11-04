"""Synthetic cloud smoke checks for Service Bus and Evidence Blob Storage."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.storage.blob import BlobServiceClient


def smoke_test_service_bus(
    *,
    namespace_or_connection: str,
    queue_name: str,
    credential: Any | None = None,
    client: ServiceBusClient | None = None,
) -> dict[str, Any]:
    """Send and receive a synthetic, non-financial test message and settle it."""
    test_id = f"smoke-test:{uuid4()}"
    test_payload = json.dumps(
        {
            "smokeTest": True,
            "generatedAt": datetime.now(UTC).isoformat(),
            "testId": test_id,
        }
    ).encode("utf-8")

    message = ServiceBusMessage(
        body=test_payload,
        message_id=test_id,
        application_properties={"message_type": "smoke_test.synthetic"},
    )

    def _execute(sb_client: ServiceBusClient) -> dict[str, Any]:
        with sb_client.get_queue_sender(queue_name) as sender:
            sender.send_messages(message)

        with sb_client.get_queue_receiver(queue_name, max_wait_time=15) as receiver:
            messages = receiver.receive_messages(max_message_count=1, max_wait_time=10)
            if not messages:
                raise RuntimeError("No message received from smoke-test queue.")
            target = messages[0]
            received_id = str(target.message_id)
            if received_id != test_id:
                receiver.abandon_message(target)
                raise RuntimeError(f"Unexpected message received: expected {test_id}, got {received_id}")
            receiver.complete_message(target)

        return {
            "verified": True,
            "message_id": received_id,
            "queue_name": queue_name,
        }

    if client is not None:
        return _execute(client)

    if namespace_or_connection.startswith("Endpoint=") or ";" in namespace_or_connection:
        with ServiceBusClient.from_connection_string(namespace_or_connection) as sb_client:
            return _execute(sb_client)
    else:
        cred = credential or DefaultAzureCredential()
        with ServiceBusClient(namespace_or_connection, credential=cred) as sb_client:
            return _execute(sb_client)


def smoke_test_blob_storage(
    *,
    account_url_or_connection: str,
    container_name: str,
    credential: Any | None = None,
    client: BlobServiceClient | None = None,
) -> dict[str, Any]:
    """Upload a synthetic test file, verify download, delete it, and confirm deletion."""
    test_blob_name = f"smoke-test/{uuid4()}.txt"
    test_content = b"synthetic-smoke-test-evidence-payload"

    def _execute(blob_client: BlobServiceClient) -> dict[str, Any]:
        container = blob_client.get_container_client(container_name)
        blob = container.get_blob_client(test_blob_name)

        blob.upload_blob(test_content, overwrite=True)
        downloaded = blob.download_blob().readall()
        if downloaded != test_content:
            raise RuntimeError("Downloaded blob content did not match uploaded smoke payload.")

        blob.delete_blob()
        try:
            blob.get_blob_properties()
            raise RuntimeError("Blob still exists after deletion.")
        except ResourceNotFoundError:
            pass

        return {
            "verified": True,
            "container": container_name,
            "blob_path": test_blob_name,
        }

    if client is not None:
        return _execute(client)

    if account_url_or_connection.startswith("DefaultEndpointsProtocol=") or ";" in account_url_or_connection:
        blob_client = BlobServiceClient.from_connection_string(account_url_or_connection)
        return _execute(blob_client)
    else:
        cred = credential or DefaultAzureCredential()
        blob_client = BlobServiceClient(account_url_or_connection, credential=cred)
        return _execute(blob_client)


def main() -> int:
    """CLI entrypoint for running synthetic cloud service smoke tests."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-service-bus", action="store_true")
    parser.add_argument("--test-storage", action="store_true")
    parser.add_argument("--sb-namespace", default=os.environ.get("SERVICE_BUS_NAMESPACE"))
    parser.add_argument("--sb-queue", default=os.environ.get("SERVICE_BUS_QUEUE_NAME", "fingpt-events"))
    parser.add_argument("--storage-account-url", default=os.environ.get("FINGPT_EVIDENCE_BLOB_ACCOUNT_URL"))
    parser.add_argument("--storage-container", default=os.environ.get("FINGPT_EVIDENCE_BLOB_CONTAINER", "evidence"))
    args = parser.parse_args()

    results: dict[str, Any] = {}
    success = True

    if args.test_service_bus:
        if not args.sb_namespace:
            print("SERVICE_BUS_NAMESPACE or --sb-namespace is required.", file=sys.stderr)
            return 1
        try:
            results["service_bus"] = smoke_test_service_bus(
                namespace_or_connection=args.sb_namespace,
                queue_name=args.sb_queue,
            )
        except Exception as error:
            results["service_bus"] = {"verified": False, "error": str(error)}
            success = False

    if args.test_storage:
        if not args.storage_account_url:
            print("FINGPT_EVIDENCE_BLOB_ACCOUNT_URL or --storage-account-url is required.", file=sys.stderr)
            return 1
        try:
            results["storage"] = smoke_test_blob_storage(
                account_url_or_connection=args.storage_account_url,
                container_name=args.storage_container,
            )
        except Exception as error:
            results["storage"] = {"verified": False, "error": str(error)}
            success = False

    print(json.dumps(results, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
