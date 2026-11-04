"""Validate that a named PostgreSQL on-demand backup is complete and recent."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any


def verify_backup(
    payload: Any,
    backup_name: str,
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(hours=24),
) -> dict[str, str]:
    """Return safe backup metadata or raise ValueError when the gate fails."""
    records = payload.get("value", []) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("Azure returned an unexpected backup list format.")

    backup = next(
        (record for record in records if isinstance(record, dict) and record.get("name") == backup_name),
        None,
    )
    if backup is None:
        raise ValueError(f"Named backup {backup_name!r} is not available.")

    properties = backup.get("properties") or backup
    if not isinstance(properties, dict):
        raise ValueError("Azure returned invalid properties for the named backup.")
    backup_type = properties.get("backupType")
    completed_time = properties.get("completedTime")
    if backup_type != "Customer On-Demand" or not isinstance(completed_time, str):
        raise ValueError("Named backup is not a completed customer on-demand backup.")

    try:
        completed = datetime.fromisoformat(completed_time.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Azure returned an invalid backup completion timestamp.") from error
    if completed.tzinfo is None:
        raise ValueError("Azure returned a backup timestamp without a timezone.")

    current = now or datetime.now(timezone.utc)
    age = current - completed.astimezone(timezone.utc)
    if age < timedelta(minutes=-5):
        raise ValueError("Backup completion time is unexpectedly in the future.")
    if age > max_age:
        raise ValueError("Named backup is older than the 24-hour migration safety window.")

    return {
        "name": backup_name,
        "backupType": backup_type,
        "completedTime": completed.astimezone(timezone.utc).isoformat(),
    }


def main() -> int:
    """Read Azure CLI JSON from stdin and print only verified backup metadata."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Exact customer on-demand backup name")
    parser.add_argument(
        "--max-age-hours",
        type=int,
        default=24,
        help="Maximum completion age accepted by this operation (default: 24)",
    )
    arguments = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if arguments.max_age_hours < 1:
            raise ValueError("Maximum backup age must be at least one hour.")
        metadata = verify_backup(
            payload,
            arguments.name,
            max_age=timedelta(hours=arguments.max_age_hours),
        )
    except (json.JSONDecodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(metadata, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
