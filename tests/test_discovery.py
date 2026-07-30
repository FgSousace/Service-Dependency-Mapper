from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from service_dependency_mapper.cli import main
from service_dependency_mapper.config import load_config
from service_dependency_mapper.discovery import (
    DiscoveredHost,
    DiscoveredService,
    DiscoveryError,
    DiscoveryResult,
    DiscoverySettings,
    NetworkTarget,
    detect_local_networks,
    discover_infrastructure,
    discovery_to_document,
    network_targets_from_cidrs,
    parse_neighbor_table,
    render_discovery_yaml,
)


def discovery_fixture() -> DiscoveryResult:
    network = NetworkTarget(
        interface="Ethernet",
        local_address="192.168.10.20",
        network="192.168.10.0/24",
        gateway="192.168.10.1",
    )
    hosts = (
        DiscoveredHost(
            address="192.168.10.1",
            network=network.network,
            hostname="router.lan",
            mac_address="AA:BB:CC:DD:EE:01",
            ping_responded=True,
            is_gateway=True,
            is_local=False,
            services=(
                DiscoveredService(80, "http", http_status=200),
                DiscoveredService(443, "https", secure=True, http_status=401),
            ),
        ),
        DiscoveredHost(
            address="192.168.10.20",
            network=network.network,
            hostname="workstation.lan",
            mac_address="AA:BB:CC:DD:EE:20",
            ping_responded=True,
            is_gateway=False,
            is_local=True,
            services=(DiscoveredService(22, "ssh", banner="SSH-2.0-test"),),
        ),
    )
    return DiscoveryResult(
        started_at="2026-07-30T10:00:00+00:00",
        completed_at="2026-07-30T10:00:02+00:00",
        duration_ms=2000,
        networks=(network,),
        hosts=hosts,
    )


class DiscoveryParsingTests(unittest.TestCase):
    def test_parses_windows_and_linux_neighbor_rows(self):
        output = """
          192.168.10.1          aa-bb-cc-dd-ee-01     dynamic
        192.168.10.20 dev eth0 lladdr 11:22:33:44:55:66 REACHABLE
        """
        self.assertEqual(
            parse_neighbor_table(output),
            {
                "192.168.10.1": "AA:BB:CC:DD:EE:01",
                "192.168.10.20": "11:22:33:44:55:66",
            },
        )

    @patch(
        "service_dependency_mapper.discovery.platform.system", return_value="Windows"
    )
    @patch("service_dependency_mapper.discovery._windows_interface_records")
    def test_detects_and_bounds_connected_private_networks(
        self,
        records,
        _system,
    ):
        records.return_value = (
            [
                {
                    "InterfaceAlias": "Ethernet",
                    "IPAddress": "192.168.10.20",
                    "PrefixLength": 16,
                },
                {
                    "InterfaceAlias": "Public",
                    "IPAddress": "203.0.113.5",
                    "PrefixLength": 24,
                },
                {
                    "InterfaceAlias": "VPN",
                    "IPAddress": "100.64.12.4",
                    "PrefixLength": 32,
                },
            ],
            {"Ethernet": "192.168.10.1"},
            [
                {
                    "InterfaceAlias": "VPN",
                    "DestinationPrefix": "10.50.0.0/24",
                    "NextHop": "100.64.12.1",
                }
            ],
        )
        targets = detect_local_networks(max_hosts=254)
        self.assertEqual(len(targets), 3)
        by_network = {target.network: target for target in targets}
        ethernet = by_network["192.168.10.0/24"]
        self.assertEqual(ethernet.interface, "Ethernet")
        self.assertEqual(
            ethernet.original_network,
            "192.168.0.0/16",
        )
        self.assertEqual(ethernet.gateway, "192.168.10.1")
        self.assertEqual(by_network["100.64.12.4/32"].interface, "VPN")
        self.assertEqual(by_network["10.50.0.0/24"].interface, "VPN")

    def test_rejects_public_manual_network(self):
        with self.assertRaisesRegex(DiscoveryError, "private"):
            network_targets_from_cidrs(["8.8.8.0/24"])

    def test_rejects_oversized_manual_network(self):
        with self.assertRaisesRegex(DiscoveryError, "safety limit"):
            network_targets_from_cidrs(["10.0.0.0/16"], max_hosts=254)


class DiscoveryMapTests(unittest.TestCase):
    def test_generates_vendor_neutral_loadable_map(self):
        result = discovery_fixture()
        document = discovery_to_document(result)
        rendered = render_discovery_yaml(result)

        self.assertEqual(document["discovery"]["hosts"], 2)
        self.assertEqual(document["discovery"]["services"], 3)
        self.assertIn("protocol: https", rendered)
        self.assertIn("protocol: ssh", rendered)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "discovered.yaml")
            path.write_text(rendered, encoding="utf-8")
            dependency_map = load_config(path)

        self.assertEqual(dependency_map.service_name, "Discovered infrastructure")
        self.assertEqual(len(dependency_map.components), 6)
        network_node = dependency_map.components[0]
        self.assertEqual(network_node.check_type, "none")
        service_node = dependency_map.components_by_id["service_192_168_10_1_443"]
        self.assertEqual(service_node.depends_on, ("host_192_168_10_1",))

    @patch("service_dependency_mapper.discovery.fingerprint_service")
    @patch("service_dependency_mapper.discovery._resolve_hostname")
    @patch("service_dependency_mapper.discovery._tcp_is_open")
    @patch("service_dependency_mapper.discovery.read_neighbor_table")
    @patch("service_dependency_mapper.discovery._ping_host")
    def test_discovers_ping_and_arp_hosts_then_scans_services(
        self,
        ping,
        neighbors,
        tcp_open,
        resolve,
        fingerprint,
    ):
        target = NetworkTarget(
            interface="lab",
            local_address="192.168.50.1",
            network="192.168.50.0/30",
            gateway="192.168.50.2",
        )
        ping.side_effect = lambda address, _timeout: address == "192.168.50.1"
        neighbors.return_value = {"192.168.50.2": "AA:BB:CC:DD:EE:FF"}
        tcp_open.side_effect = lambda address, port, _timeout: (
            address == "192.168.50.2" and port == 22
        )
        resolve.side_effect = lambda address: (
            "gateway.lan" if address == "192.168.50.2" else "local.lan"
        )
        fingerprint.side_effect = lambda _address, port, _timeout: DiscoveredService(
            port, "ssh"
        )

        result = discover_infrastructure(
            (target,),
            settings=DiscoverySettings(
                timeout=0.01,
                workers=4,
                max_hosts_per_network=2,
                ports=(22, 80),
            ),
        )

        self.assertEqual(len(result.hosts), 2)
        by_address = {host.address: host for host in result.hosts}
        self.assertTrue(by_address["192.168.50.1"].ping_responded)
        self.assertTrue(by_address["192.168.50.2"].is_gateway)
        self.assertEqual(by_address["192.168.50.2"].services[0].port, 22)


class DiscoveryCliTests(unittest.TestCase):
    @patch(
        "service_dependency_mapper.cli.discover_infrastructure",
        return_value=discovery_fixture(),
    )
    def test_cli_writes_discovered_map(self, discover):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir, "network.yaml")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["discover", "-o", str(output)])
            dependency_map = load_config(output)

        self.assertEqual(exit_code, 0)
        self.assertEqual(dependency_map.service_name, "Discovered infrastructure")
        self.assertIn("2 host(s), 3 service(s)", stdout.getvalue())
        discover.assert_called_once()


if __name__ == "__main__":
    unittest.main()
