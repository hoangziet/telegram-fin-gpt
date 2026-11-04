"""Tests for pre-start migration job configuration checks."""

from __future__ import annotations

import copy
import unittest

from verify_migration_job import EXPECTED_ARGS, EXPECTED_COMMAND, verify_migration_job


class VerifyMigrationJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.image = "fingpttestacr.azurecr.io/backend@sha256:" + "a" * 64
        self.job = {
            "name": "fingpt-test-migrate",
            "properties": {
                "configuration": {
                    "triggerType": "Manual",
                    "replicaRetryLimit": 0,
                    "manualTriggerConfig": {
                        "parallelism": 1,
                        "replicaCompletionCount": 1,
                    },
                },
                "template": {
                    "containers": [
                        {
                            "name": "migrate",
                            "image": self.image,
                            "command": EXPECTED_COMMAND.copy(),
                            "args": EXPECTED_ARGS.copy(),
                        }
                    ]
                },
            },
        }

    def test_accepts_expected_one_shot_job(self) -> None:
        result = verify_migration_job(self.job, self.image)
        self.assertEqual(result["image"], self.image)
        self.assertEqual(result["replicaRetryLimit"], 0)

    def test_rejects_changed_image_command_or_arguments(self) -> None:
        for path, value in [
            (("image",), self.image.replace("a" * 64, "b" * 64)),
            (("command",), ["python", "-c"]),
            (("args",), ["upgrade", "head"]),
        ]:
            with self.subTest(path=path):
                changed = copy.deepcopy(self.job)
                container = changed["properties"]["template"]["containers"][0]
                container[path[0]] = value
                with self.assertRaisesRegex(ValueError, "differs"):
                    verify_migration_job(changed, self.image)

    def test_rejects_parallel_retries_or_non_manual_trigger(self) -> None:
        for key, value in [
            ("replicaRetryLimit", 1),
            ("triggerType", "Event"),
        ]:
            with self.subTest(key=key):
                changed = copy.deepcopy(self.job)
                changed["properties"]["configuration"][key] = value
                with self.assertRaisesRegex(ValueError, "differs"):
                    verify_migration_job(changed, self.image)

        changed = copy.deepcopy(self.job)
        changed["properties"]["configuration"]["manualTriggerConfig"]["parallelism"] = 2
        with self.assertRaisesRegex(ValueError, "differs"):
            verify_migration_job(changed, self.image)

    def test_rejects_missing_or_multiple_containers(self) -> None:
        changed = copy.deepcopy(self.job)
        changed["properties"]["template"]["containers"] = []
        with self.assertRaisesRegex(ValueError, "exactly one"):
            verify_migration_job(changed, self.image)

        changed["properties"]["template"]["containers"] = [{}, {}]
        with self.assertRaisesRegex(ValueError, "exactly one"):
            verify_migration_job(changed, self.image)


if __name__ == "__main__":
    unittest.main()
