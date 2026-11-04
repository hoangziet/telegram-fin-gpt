"""Network parameter validation tests for the Azure template."""

from __future__ import annotations

import unittest

from validate_network import validate_network


class ValidateNetworkTests(unittest.TestCase):
    def test_accepts_private_nested_non_overlapping_ranges(self) -> None:
        validate_network(
            "10.40.0.0/16",
            "10.40.0.0/23",
        )

    def test_rejects_non_private_networks(self) -> None:
        with self.assertRaisesRegex(ValueError, "RFC 1918"):
            validate_network(
                "8.8.0.0/16",
                "8.8.0.0/23",
            )

    def test_rejects_subnet_outside_vnet(self) -> None:
        with self.assertRaisesRegex(ValueError, "inside the VNet"):
            validate_network(
                "10.40.0.0/16",
                "10.41.0.0/23",
            )

    def test_enforces_container_apps_workload_profile_minimum(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least a /27"):
            validate_network(
                "10.40.0.0/16",
                "10.40.0.0/28",
            )


if __name__ == "__main__":
    unittest.main()
