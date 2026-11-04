"""Tests for preview/apply deployment input binding."""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from deployment_fingerprint import deployment_fingerprint


class DeploymentFingerprintTests(unittest.TestCase):
    def test_is_stable_for_same_inputs(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LOCATION": "southeastasia",
                "PASSWORD": "secret",
                "FINGPT_DEPLOYMENT_FINGERPRINT_KEY": "a" * 64,
            },
            clear=True,
        ):
            expected = deployment_fingerprint(["LOCATION", "PASSWORD"])
            self.assertEqual(expected, deployment_fingerprint(["PASSWORD", "LOCATION"]))

    def test_changes_when_any_input_changes(self) -> None:
        with patch.dict(
            os.environ,
            {"LOCATION": "southeastasia", "FINGPT_DEPLOYMENT_FINGERPRINT_KEY": "a" * 64},
            clear=True,
        ):
            first = deployment_fingerprint(["LOCATION"])
        with patch.dict(
            os.environ,
            {"LOCATION": "eastasia", "FINGPT_DEPLOYMENT_FINGERPRINT_KEY": "a" * 64},
            clear=True,
        ):
            second = deployment_fingerprint(["LOCATION"])
        self.assertNotEqual(first, second)

    def test_changes_when_fingerprint_key_changes(self) -> None:
        with patch.dict(
            os.environ,
            {"LOCATION": "southeastasia", "FINGPT_DEPLOYMENT_FINGERPRINT_KEY": "a" * 64},
            clear=True,
        ):
            first = deployment_fingerprint(["LOCATION"])
        with patch.dict(
            os.environ,
            {"LOCATION": "southeastasia", "FINGPT_DEPLOYMENT_FINGERPRINT_KEY": "b" * 64},
            clear=True,
        ):
            second = deployment_fingerprint(["LOCATION"])
        self.assertNotEqual(first, second)

    def test_rejects_missing_and_duplicate_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "LOCATION is missing"):
            with patch.dict(
                os.environ, {"FINGPT_DEPLOYMENT_FINGERPRINT_KEY": "a" * 64}, clear=True
            ):
                deployment_fingerprint(["LOCATION"])
        with self.assertRaisesRegex(ValueError, "unique"):
            deployment_fingerprint(["LOCATION", "LOCATION"])

    def test_cli_prints_only_a_digest(self) -> None:
        script = Path(__file__).with_name("deployment_fingerprint.py")
        result = subprocess.run(
            ["python3", str(script), "PASSWORD"],
            check=True,
            capture_output=True,
            text=True,
            env={
                "PASSWORD": "high-entropy-secret-value",
                "FINGPT_DEPLOYMENT_FINGERPRINT_KEY": "b" * 64,
            },
        )
        self.assertRegex(result.stdout, re.compile(r"^[a-f0-9]{64}\n$"))
        self.assertNotIn("high-entropy-secret-value", result.stdout)


if __name__ == "__main__":
    unittest.main()
