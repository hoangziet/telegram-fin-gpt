"""Validate owner-supplied VNet CIDRs before Bicep preview or deployment."""

from __future__ import annotations

import argparse
import ipaddress
from collections.abc import Sequence

IPv4Network = ipaddress.IPv4Network
RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def validate_network(
    vnet_cidr: str,
    aca_subnet_cidr: str,
) -> None:
    """Reject public, malformed, out-of-VNet, overlapping, or undersized CIDRs."""
    ranges = {
        "VNet": _parse_cidr("VNet", vnet_cidr),
        "Container Apps subnet": _parse_cidr("Container Apps subnet", aca_subnet_cidr),
    }
    vnet = ranges["VNet"]
    subnets = {name: network for name, network in ranges.items() if name != "VNet"}

    for name, network in ranges.items():
        if not any(network.subnet_of(private_range) for private_range in RFC1918_NETWORKS):
            raise ValueError(f"{name} must use an RFC 1918 private IPv4 range.")
    for name, network in subnets.items():
        if not network.subnet_of(vnet):
            raise ValueError(f"{name} must be inside the VNet address range.")

    subnet_items = list(subnets.items())
    for index, (first_name, first_network) in enumerate(subnet_items):
        for second_name, second_network in subnet_items[index + 1 :]:
            if first_network.overlaps(second_network):
                raise ValueError(f"{first_name} and {second_name} must not overlap.")

    if subnets["Container Apps subnet"].prefixlen > 27:
        raise ValueError("Container Apps workload-profile subnet must contain at least a /27 range.")


def _parse_cidr(name: str, value: str) -> IPv4Network:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise ValueError(f"{name} must be a valid CIDR network address.") from error
    if not isinstance(network, IPv4Network):
        raise ValueError(f"{name} must be IPv4.")
    return network


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate CIDRs from command-line flags for local and workflow use."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vnet", required=True)
    parser.add_argument("--aca-subnet", required=True)
    options = parser.parse_args(arguments)
    try:
        validate_network(
            options.vnet,
            options.aca_subnet,
        )
    except ValueError as error:
        parser.error(str(error))
    print("Network CIDRs are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
