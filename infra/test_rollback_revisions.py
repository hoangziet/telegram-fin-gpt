"""Tests for the Container Apps revision rollback script."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("rollback_revisions.sh")


class RollbackRevisionsTests(unittest.TestCase):
    def test_rollback_preview_succeeds_with_targets(self) -> None:
        result = self._run_script(mode="preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Rollback plan:", result.stdout)
        self.assertIn("Rollback target: api-rev-1 (will receive 100% traffic)", result.stdout)
        self.assertIn("Rollback target: worker-rev-1 (will be activated)", result.stdout)
        self.assertIn("Preview complete", result.stdout)

    def test_rollback_apply_executes_traffic_and_revision_switches(self) -> None:
        result = self._run_script(mode="apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Applying API rollback to revision api-rev-1", result.stdout)
        self.assertIn("Applying worker rollback to revision worker-rev-1", result.stdout)
        self.assertIn("Rollback applied successfully", result.stdout)

    def test_display_name_does_not_block_selected_subscription_id(self) -> None:
        result = self._run_script(mode="preview", subscription_name="Partnership DONT-USE")
        self.assertEqual(result.returncode, 0, result.stderr)

    def _run_script(
        self,
        mode: str,
        subscription_name: str = "FinGPT NonProduction",
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory)
            self._write_command(
                bin_dir / "az",
                f"""#!/usr/bin/env bash
if [[ "$*" == *"account show"* ]]; then
  if [[ "$*" == *"--query id"* ]]; then
    printf '00000000-0000-0000-0000-000000000000\\n'
  else
    printf '{subscription_name}\\n'
  fi
elif [[ "$*" == *"revision list"*"api"* ]]; then
  printf 'api-rev-1\\n'
elif [[ "$*" == *"revision list"*"worker"* ]]; then
  printf 'worker-rev-1\\n'
elif [[ "$*" == *"containerapp show"*"api"* ]]; then
  printf 'api-rev-2\\n'
elif [[ "$*" == *"containerapp show"*"worker"* ]]; then
  printf 'worker-rev-2\\n'
else
  exit 0
fi
""",
            )
            environment = os.environ | {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
                "TARGET_RESOURCE_GROUP": "fingpt-test-rg",
                "NAME_PREFIX": "fingpt",
                "ENVIRONMENT_NAME": "test",
                "MODE": mode,
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
