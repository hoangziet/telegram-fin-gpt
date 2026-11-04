"""Tests for RPO/RTO recovery metrics evaluation."""

from __future__ import annotations

import unittest

from measure_recovery import evaluate_recovery_metrics, format_duration, format_markdown_summary


class MeasureRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.params = {
            "backup_completed_at": "2026-11-03T08:00:00Z",
            "incident_occurred_at": "2026-11-03T09:30:00Z",
            "restore_requested_at": "2026-11-03T09:35:00Z",
            "restore_verified_at": "2026-11-03T10:15:30Z",
        }

    def test_duration_formatter(self) -> None:
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(125), "2m 5s")
        self.assertEqual(format_duration(3661), "1h 1m 1s")

    def test_computes_rpo_and_rto_accurately(self) -> None:
        result = evaluate_recovery_metrics(
            **self.params,
            target_rpo_minutes=120,
            target_rto_minutes=60,
        )
        self.assertEqual(result["measured_rpo_seconds"], 5400)
        self.assertEqual(result["measured_rpo_human"], "1h 30m 0s")
        self.assertEqual(result["measured_rto_seconds"], 2430)
        self.assertEqual(result["measured_rto_human"], "40m 30s")
        self.assertTrue(result["rpo_met"])
        self.assertTrue(result["rto_met"])
        self.assertTrue(result["recovery_targets_met"])

    def test_detects_target_exceeded(self) -> None:
        result = evaluate_recovery_metrics(
            **self.params,
            target_rpo_minutes=60,  # 90m actual > 60m target
            target_rto_minutes=30,  # 40m30s actual > 30m target
        )
        self.assertFalse(result["rpo_met"])
        self.assertFalse(result["rto_met"])
        self.assertFalse(result["recovery_targets_met"])

    def test_rejects_inconsistent_timestamps(self) -> None:
        with self.assertRaisesRegex(ValueError, "Incident time cannot precede"):
            evaluate_recovery_metrics(
                backup_completed_at="2026-11-03T10:00:00Z",
                incident_occurred_at="2026-11-03T09:00:00Z",
                restore_requested_at="2026-11-03T10:05:00Z",
                restore_verified_at="2026-11-03T10:20:00Z",
            )

        with self.assertRaisesRegex(ValueError, "Restore verified time cannot precede"):
            evaluate_recovery_metrics(
                backup_completed_at="2026-11-03T08:00:00Z",
                incident_occurred_at="2026-11-03T09:00:00Z",
                restore_requested_at="2026-11-03T10:05:00Z",
                restore_verified_at="2026-11-03T09:50:00Z",
            )

    def test_formats_clean_markdown_summary(self) -> None:
        result = evaluate_recovery_metrics(
            **self.params,
            target_rpo_minutes=120,
            target_rto_minutes=60,
        )
        summary = format_markdown_summary(result)
        self.assertIn("Recovery metrics evaluation (RPO / RTO)", summary)
        self.assertIn("1h 30m 0s", summary)
        self.assertIn("Target: 120m | Result: **PASSED**", summary)


if __name__ == "__main__":
    unittest.main()
