"""Tests for temporary PostgreSQL restore server cleanup safeguards."""

from __future__ import annotations

import unittest

from validate_restore_cleanup import validate_cleanup_target


class ValidateRestoreCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.params = {
            "name_prefix": "fingpt",
            "subscription_name": "FinGPT NonProduction",
            "resource_group": "fingpt-test-rg",
            "server_name": "fingpt-test-restore-run-123",
            "server_exists": True,
            "server_state": "Ready",
        }

    def test_accepts_valid_test_restore_server(self) -> None:
        plan = validate_cleanup_target(**self.params)
        self.assertEqual(plan["serverName"], "fingpt-test-restore-run-123")
        self.assertEqual(plan["action"], "delete")

    def test_subscription_display_name_does_not_block_cleanup_plan(self) -> None:
        plan = validate_cleanup_target(**(self.params | {"subscription_name": "Partnership DONT-USE"}))
        self.assertEqual(plan["action"], "delete")

    def test_refuses_non_restore_servers(self) -> None:
        for name in [
            "fingpt-test-pg",
            "fingpt-prod-pg",
            "fingpt-prod-restore-run-123",
            "other-server",
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_cleanup_target(**(self.params | {"server_name": name}))

    def test_refuses_non_existent_server(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate_cleanup_target(**(self.params | {"server_exists": False}))

    def test_refuses_malformed_name_or_prefix(self) -> None:
        for prefix in ["ab", "toolongworkloadname", "bad_prefix", "UPPER"]:
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                validate_cleanup_target(**(self.params | {"name_prefix": prefix}))


if __name__ == "__main__":
    unittest.main()
