"""Create a non-reversible fingerprint for deployment inputs across CI jobs."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys


def deployment_fingerprint(field_names: list[str]) -> str:
    """Hash a canonical mapping of named environment values without exposing them."""
    if not field_names or len(field_names) != len(set(field_names)):
        raise ValueError("Provide a non-empty list of unique environment variable names.")

    values: dict[str, str] = {}
    for name in field_names:
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", name) is None:
            raise ValueError("Deployment fingerprint fields must be uppercase variable names.")
        value = os.environ.get(name)
        if value is None:
            raise ValueError(f"Required deployment input {name} is missing.")
        values[name] = value

    key_hex = os.environ.get("FINGPT_DEPLOYMENT_FINGERPRINT_KEY", "")
    if re.fullmatch(r"[a-f0-9]{64}", key_hex) is None:
        raise ValueError("Fingerprint key must be configured as 64 lowercase hex characters.")

    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(bytes.fromhex(key_hex), canonical.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()


def main() -> int:
    """Print the fingerprint only; never print the underlying deployment values."""
    try:
        print(deployment_fingerprint(sys.argv[1:]))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
