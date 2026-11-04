"""Validate a private, non-production PostgreSQL restore rehearsal plan."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

DNS_ZONE = "privatelink.postgres.database.azure.com"


def _utc_timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is not a valid ISO 8601 timestamp.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def validate_restore_plan(
    *,
    name_prefix: str,
    subscription_name: str,
    source_server_name: str,
    source_server_state: str,
    earliest_restore_time: str,
    backup: dict[str, Any],
    restore_server_name: str,
    target_exists: bool,
    vnet_id: str,
    postgres_subnet_id: str,
    private_dns_zone_id: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Validate source, recovery point, target name, and private network scope."""
    expected_source = f"{name_prefix}-test-pg"
    if source_server_name != expected_source or source_server_state != "Ready":
        raise ValueError("Restore source must be the ready FinGPT test PostgreSQL server.")
    expected_target_prefix = f"{name_prefix}-test-restore-"
    if (
        len(restore_server_name) > 63
        or not restore_server_name.startswith(expected_target_prefix)
        or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", restore_server_name) is None
    ):
        raise ValueError("Restore server name must use the dedicated test restore prefix.")
    if target_exists:
        raise ValueError("Restore target already exists; choose a new unique name.")
    if backup.get("backupType") != "Customer On-Demand":
        raise ValueError("Restore rehearsal requires a named customer on-demand backup.")

    completed = _utc_timestamp(backup.get("completedTime", ""), "Backup completion time")
    earliest = _utc_timestamp(earliest_restore_time, "Earliest restore time")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if completed < earliest or completed > current + timedelta(minutes=5):
        raise ValueError("Backup completion time is outside the source server restore window.")

    expected_vnet_suffix = f"/virtualNetworks/{name_prefix}-test-vnet"
    if not vnet_id.lower().endswith(expected_vnet_suffix.lower()):
        raise ValueError("Restore VNet must be the FinGPT test VNet.")
    expected_subnet = f"{vnet_id}/subnets/postgresql"
    if postgres_subnet_id.lower() != expected_subnet.lower():
        raise ValueError("Restore subnet must be the delegated PostgreSQL subnet in the test VNet.")
    if not private_dns_zone_id.lower().endswith(f"/privatednszones/{DNS_ZONE}".lower()):
        raise ValueError("Restore must use the FinGPT PostgreSQL private DNS zone.")

    return {
        "sourceServer": source_server_name,
        "restoreServer": restore_server_name,
        "backupName": str(backup.get("name", "")),
        "backupType": str(backup["backupType"]),
        "restoreTimeUtc": completed.isoformat(),
        "vnetId": vnet_id,
        "postgresSubnetId": postgres_subnet_id,
        "privateDnsZoneId": private_dns_zone_id,
    }


def main() -> int:
    """Validate restore-plan values supplied from Azure CLI and print the plan."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name-prefix", required=True)
    parser.add_argument("--subscription-name", required=True)
    parser.add_argument("--source-server-name", required=True)
    parser.add_argument("--source-server-state", required=True)
    parser.add_argument("--earliest-restore-time", required=True)
    parser.add_argument("--backup-json", required=True)
    parser.add_argument("--restore-server-name", required=True)
    parser.add_argument("--target-exists", action="store_true")
    parser.add_argument("--vnet-id", required=True)
    parser.add_argument("--postgres-subnet-id", required=True)
    parser.add_argument("--private-dns-zone-id", required=True)
    arguments = parser.parse_args()
    try:
        backup = json.loads(arguments.backup_json)
        if not isinstance(backup, dict):
            raise ValueError("Verified backup metadata must be a JSON object.")
        plan = validate_restore_plan(
            name_prefix=arguments.name_prefix,
            subscription_name=arguments.subscription_name,
            source_server_name=arguments.source_server_name,
            source_server_state=arguments.source_server_state,
            earliest_restore_time=arguments.earliest_restore_time,
            backup=backup,
            restore_server_name=arguments.restore_server_name,
            target_exists=arguments.target_exists,
            vnet_id=arguments.vnet_id,
            postgres_subnet_id=arguments.postgres_subnet_id,
            private_dns_zone_id=arguments.private_dns_zone_id,
        )
    except (json.JSONDecodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(plan, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
