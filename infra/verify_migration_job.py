"""Verify the reviewed Container Apps migration job before starting it."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

EXPECTED_COMMAND = ["alembic"]
EXPECTED_ARGS = ["-c", "/app/alembic.ini", "upgrade", "head"]


def verify_migration_job(job: Any, expected_image: str) -> dict[str, Any]:
    """Validate immutable image, migration command, and single-run settings."""
    if not isinstance(job, dict):
        raise ValueError("Azure returned an invalid migration job resource.")
    properties = job.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("Migration job has no properties object.")
    configuration = properties.get("configuration")
    template = properties.get("template")
    if not isinstance(configuration, dict) or not isinstance(template, dict):
        raise ValueError("Migration job is missing configuration or template.")

    containers = template.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise ValueError("Migration job must contain exactly one container.")
    container = containers[0]
    if not isinstance(container, dict):
        raise ValueError("Migration job container is invalid.")

    manual = configuration.get("manualTriggerConfig")
    if not isinstance(manual, dict):
        raise ValueError("Migration job must use manual trigger settings.")
    if (
        container.get("image") != expected_image
        or container.get("command") != EXPECTED_COMMAND
        or container.get("args") != EXPECTED_ARGS
        or configuration.get("triggerType") != "Manual"
        or configuration.get("replicaRetryLimit") != 0
        or manual.get("parallelism") != 1
        or manual.get("replicaCompletionCount") != 1
    ):
        raise ValueError("Migration job differs from the reviewed one-shot migration configuration.")

    return {
        "name": job.get("name"),
        "image": expected_image,
        "command": EXPECTED_COMMAND,
        "args": EXPECTED_ARGS,
        "triggerType": "Manual",
        "replicaRetryLimit": 0,
        "parallelism": 1,
        "replicaCompletionCount": 1,
    }


def main() -> int:
    """Read the job JSON from stdin and print only verified configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Expected immutable backend image digest")
    arguments = parser.parse_args()
    try:
        job = json.load(sys.stdin)
        result = verify_migration_job(job, arguments.image)
    except (json.JSONDecodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
