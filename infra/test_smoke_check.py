"""Tests for the Azure release smoke-check shell script."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("smoke_check.sh")


class SmokeCheckTests(unittest.TestCase):
    def test_passes_when_api_and_worker_are_healthy(self) -> None:
        result = self._run_script("Healthy\tRunning")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("worker revision passed", result.stdout)

    def test_fails_when_worker_never_becomes_healthy(self) -> None:
        result = self._run_script("Unhealthy\tDegraded")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not become healthy and running", result.stderr)

    def _run_script(self, revision_state: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            binary_directory = Path(directory)
            self._write_command(
                binary_directory / "curl",
                """#!/usr/bin/env bash
if [[ "$*" == *"/" ]]; then
  printf '<!doctype html><html></html>'
fi
""",
            )
            self._write_command(
                binary_directory / "az",
                """#!/usr/bin/env bash
if [[ "$*" == *"containerapp show"* ]]; then
  printf 'worker-revision-1\\n'
else
  printf '%s\\n' "$MOCK_REVISION_STATE"
fi
""",
            )
            self._write_command(binary_directory / "sleep", "#!/usr/bin/env bash\nexit 0\n")
            environment = os.environ | {
                "PATH": f"{binary_directory}:{os.environ['PATH']}",
                "API_BASE_URL": "https://fingpt.example.test",
                "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
                "TARGET_RESOURCE_GROUP": "fingpt-test-rg",
                "NAME_PREFIX": "fingpt",
                "ENVIRONMENT_NAME": "test",
                "MOCK_REVISION_STATE": revision_state,
            }
            return subprocess.run(
                ["bash", str(SCRIPT)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

    @staticmethod
    def _write_command(path: Path, source: str) -> None:
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
