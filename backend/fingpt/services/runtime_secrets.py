"""Runtime environment lookup for secret-store references."""

from __future__ import annotations

import os
import re

_SECRET_NAME = re.compile(r"^[a-zA-Z0-9]+(?:[-/][a-zA-Z0-9]+)*$")


def resolve_environment_secret(secret_name: str) -> str | None:
    """Resolve a secret reference through a deterministic runtime env name."""
    if not _SECRET_NAME.fullmatch(secret_name):
        return None
    suffix = secret_name.upper().replace("/", "__").replace("-", "_")
    return os.environ.get(f"FINGPT_SECRET_{suffix}")
