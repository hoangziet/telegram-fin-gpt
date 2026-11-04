"""Validate safeguards before deleting a temporary PostgreSQL restore server."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


def validate_cleanup_target(
    *,
    name_prefix: str,
    subscription_name: str,
    resource_group: str,
    server_name: str,
    server_exists: bool,
    server_state: str,
) -> dict[str, Any]:
    """Ensure the target is strictly a temporary test-restore server and not production or source."""
    if not re.fullmatch(r"^[a-z0-9]{3,12}$", name_prefix):
        raise ValueError("Workload name prefix must be 3-12 lowercase alphanumeric characters.")

    if not re.fullmatch(r"^[A-Za-z0-9._()-]{1,90}$", resource_group):
        raise ValueError("Resource group name is invalid.")

    expected_prefix = f"{name_prefix}-test-restore-"
    if not server_name.startswith(expected_prefix):
        raise ValueError(f"Target server name must begin with '{expected_prefix}'.")

    if (
        len(server_name) > 63
        or not re.fullmatch(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", server_name)
    ):
        raise ValueError("Target server name is not a valid Azure PostgreSQL server name.")

    # Guard against accidental deletion of source or production servers
    prohibited_names = {
        f"{name_prefix}-test-pg",
        f"{name_prefix}-prod-pg",
        f"{name_prefix}-dev-pg",
    }
    if server_name in prohibited_names or "-prod-" in server_name:
        raise ValueError("Target is a protected persistent server and cannot be deleted by restore cleanup.")

    if not server_exists:
        raise ValueError(f"Server '{server_name}' does not exist in resource group '{resource_group}'.")

    return {
        "subscriptionName": subscription_name,
        "resourceGroup": resource_group,
        "serverName": server_name,
        "serverState": server_state,
        "action": "delete",
    }


def main() -> int:
    """Validate cleanup parameters and emit sanitized JSON delete plan."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name-prefix", required=True)
    parser.add_argument("--subscription-name", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--server-exists", action="store_true")
    parser.add_argument("--server-state", required=True)

    args = parser.parse_args()
    try:
        plan = validate_cleanup_target(
            name_prefix=args.name_prefix,
            subscription_name=args.subscription_name,
            resource_group=args.resource_group,
            server_name=args.server_name,
            server_exists=args.server_exists,
            server_state=args.server_state,
        )
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(json.dumps(plan, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
