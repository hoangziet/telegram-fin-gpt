"""Tests for restore rehearsal target and recovery-point safeguards."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from validate_restore_plan import validate_restore_plan


class ValidateRestorePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.arguments = {
            "name_prefix": "fingpt",
            "subscription_name": "FinGPT NonProduction",
            "source_server_name": "fingpt-test-pg",
            "source_server_state": "Ready",
            "earliest_restore_time": "2026-10-30T00:00:00Z",
            "backup": {
                "name": "restore-backup",
                "backupType": "Customer On-Demand",
                "completedTime": "2026-11-03T10:00:00Z",
            },
            "restore_server_name": "fingpt-test-restore-run-42",
            "target_exists": False,
            "vnet_id": (
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Network/virtualNetworks/fingpt-test-vnet"
            ),
            "postgres_subnet_id": (
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Network/virtualNetworks/fingpt-test-vnet/subnets/postgresql"
            ),
            "private_dns_zone_id": (
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Network/privateDnsZones/"
                "privatelink.postgres.database.azure.com"
            ),
            "now": datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc),
        }

    def test_accepts_private_test_restore_plan(self) -> None:
        plan = validate_restore_plan(**self.arguments)
        self.assertEqual(plan["sourceServer"], "fingpt-test-pg")
        self.assertEqual(plan["restoreTimeUtc"], "2026-11-03T10:00:00+00:00")

    def test_subscription_display_name_does_not_block_plan(self) -> None:
        plan = validate_restore_plan(**(self.arguments | {"subscription_name": "Partnership DONT-USE"}))
        self.assertEqual(plan["sourceServer"], "fingpt-test-pg")

    def test_refuses_non_test_server(self) -> None:
        for values in [
            {"source_server_name": "fingpt-prod-pg"},
            {"source_server_state": "Stopped"},
        ]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_restore_plan(**(self.arguments | values))

    def test_refuses_existing_or_unscoped_target(self) -> None:
        for values in [
            {"target_exists": True},
            {"restore_server_name": "fingpt-prod-restore-run-42"},
            {"restore_server_name": "bad/name"},
            {"postgres_subnet_id": "/subscriptions/other/subnets/public"},
            {"private_dns_zone_id": "/subscriptions/sub/privateDnsZones/public.example"},
        ]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_restore_plan(**(self.arguments | values))

    def test_refuses_backup_outside_source_restore_window(self) -> None:
        for values in [
            {"earliest_restore_time": "2026-11-03T11:00:00Z"},
            {
                "backup": self.arguments["backup"]
                | {"completedTime": "2026-11-03T13:00:00Z"}
            },
            {"backup": self.arguments["backup"] | {"backupType": "Full"}},
        ]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_restore_plan(**(self.arguments | values))


if __name__ == "__main__":
    unittest.main()
