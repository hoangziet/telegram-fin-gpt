"""Tests for the pre-migration backup gate."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from verify_backup import verify_backup


class VerifyBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    def test_accepts_recent_named_customer_backup(self) -> None:
        payload = {
            "value": [
                {
                    "name": "migration-20261103",
                    "properties": {
                        "backupType": "Customer On-Demand",
                        "completedTime": "2026-11-03T11:30:00Z",
                    },
                }
            ]
        }
        result = verify_backup(payload, "migration-20261103", now=self.now)
        self.assertEqual(result["backupType"], "Customer On-Demand")
        self.assertEqual(result["completedTime"], "2026-11-03T11:30:00+00:00")

    def test_rejects_missing_or_automatic_backup(self) -> None:
        with self.assertRaisesRegex(ValueError, "not available"):
            verify_backup([], "missing", now=self.now)
        payload = [
            {
                "name": "named",
                "backupType": "Full",
                "completedTime": "2026-11-03T11:30:00Z",
            }
        ]
        with self.assertRaisesRegex(ValueError, "not a completed"):
            verify_backup(payload, "named", now=self.now)

    def test_rejects_old_future_and_timezone_free_timestamps(self) -> None:
        def payload(completed_time: str) -> list[dict[str, object]]:
            return [
                {
                    "name": "named",
                    "backupType": "Customer On-Demand",
                    "completedTime": completed_time,
                }
            ]

        with self.assertRaisesRegex(ValueError, "24-hour"):
            verify_backup(payload("2026-11-02T11:00:00Z"), "named", now=self.now)
        with self.assertRaisesRegex(ValueError, "future"):
            verify_backup(payload("2026-11-03T12:10:00Z"), "named", now=self.now)
        with self.assertRaisesRegex(ValueError, "without a timezone"):
            verify_backup(payload("2026-11-03T11:30:00"), "named", now=self.now)

    def test_accepts_nested_properties_and_exact_name(self) -> None:
        payload = [
            {
                "name": "other",
                "properties": {
                    "backupType": "Customer On-Demand",
                    "completedTime": "2026-11-03T11:30:00Z",
                },
            },
            {
                "name": "expected",
                "properties": {
                    "backupType": "Customer On-Demand",
                    "completedTime": "2026-11-03T11:30:00Z",
                },
            },
        ]
        self.assertEqual(verify_backup(payload, "expected", now=self.now)["name"], "expected")

    def test_allows_configured_retention_window_for_restore_rehearsal(self) -> None:
        payload = [
            {
                "name": "retained",
                "backupType": "Customer On-Demand",
                "completedTime": "2026-10-15T12:00:00Z",
            }
        ]
        self.assertEqual(
            verify_backup(
                payload,
                "retained",
                now=self.now,
                max_age=timedelta(days=35),
            )["name"],
            "retained",
        )


if __name__ == "__main__":
    unittest.main()
