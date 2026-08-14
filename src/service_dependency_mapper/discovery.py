"""Vendor-neutral discovery of local IPv4 infrastructure and TCP services."""

from __future__ import annotations

import ipaddress
import json
import math
import platform
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from service_dependency_mapper.performance import (
    automatic_worker_count,
    build_discovery_worker_plan,
)

PRIVATE_RANGES = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)

# The initial probes are intentionally generic and cover common infrastructure
# protocols. They are not tied to any monitoring vendor or application product.
HOST_DISCOVERY_PORTS = (
    22,
    53,
    80,
    135,
    139,
    389,
    443,
    445,
    1433,
    1883,
    2375,
    3000,
    3306,
    3389,
    5432,
    5985,
    6379,
    6443,
    7001,
    8000,
    8008,
    8080,
    8081,
    8088,
    8443,
    8888,
    9000,
    9090,
    9200,
    9443,
    10000,
    25565,
    27017,
)

_COMMON_SCAN_PORTS = (
    20,
    21,
    22,
    23,
    25,
    37,
    43,
    49,
    53,
    70,
    79,
    80,
    81,
    88,
    110,
    111,
    119,
    135,
    139,
    143,
    161,
    179,
    389,
    427,
    443,
    445,
    465,
    500,
    514,
    515,
    548,
    554,
    587,
    623,
    631,
    636,
    873,
    902,
    989,
    990,
    993,
    995,
    1080,
    1194,
    2222,
    1433,
    1521,
    1723,
    1883,
    2049,
    2375,
    2376,
    3000,
    3001,
    3128,
    3268,
    3269,
    3306,
    3389,
    4000,
    4443,
    5000,
    5060,
    5357,
    5432,
    5672,
    5900,
    5985,
    5986,
    6379,
    6443,
    7000,
    7001,
    7474,
    8000,
    8008,
    8080,
    8081,
    8088,
    8123,
    8443,
    8500,
    8686,
    8883,
    8888,
    8983,
    9000,
    9001,
    9042,
    9090,
    9100,
    9200,
    9300,
    9418,
    9443,
    9999,
    10000,
    11211,
    15672,
    25565,
    27017,
)

# Every well-known TCP port is checked on a host once the host is found, with
# additional high ports commonly used by infrastructure services.
DEFAULT_SCAN_PORTS = tuple(sorted(set(range(1, 1025)).union(_COMMON_SCAN_PORTS)))
ALL_TCP_PORTS = tuple(range(1, 65536))

PORT_PROTOCOLS = {
    20: "ftp-data",
    21: "ftp",
    22: "ssh",
    23: "telnet",
    25: "smtp",
    37: "time",
    43: "whois",
    49: "tacacs",
    53: "dns",
    70: "gopher",
    79: "finger",
    80: "http",
    81: "http-alt",
    88: "kerberos",
    110: "pop3",
    111: "rpcbind",
    119: "nntp",
    135: "ms-rpc",
    139: "netbios",
    143: "imap",
    161: "snmp",
    179: "bgp",
    389: "ldap",
    427: "slp",
    443: "https",
    445: "smb",
    465: "smtps",
    500: "isakmp",
    514: "shell/syslog",
    515: "printer",
    548: "afp",
    554: "rtsp",
    587: "smtp-submission",
    623: "ipmi",
    631: "ipp",
    636: "ldaps",
    873: "rsync",
    902: "management",
    989: "ftps-data",
    990: "ftps",
    993: "imaps",
    995: "pop3s",
    1080: "socks",
    1194: "vpn",
    1433: "sql",
    1521: "database",
    1723: "vpn",
    1883: "mqtt",
    2049: "nfs",
    2375: "container-api",
    2376: "container-api-tls",
    3000: "http-alt",
    3128: "http-proxy",
    3268: "directory",
    3269: "directory-tls",
    3306: "database",
    3389: "rdp",
    4443: "https-alt",
    5000: "http-alt",
    5060: "sip",
    5357: "web-services",
    5432: "database",
    5672: "amqp",
    5900: "vnc",
    5985: "winrm-http",
    5986: "winrm-https",
    6379: "cache",
    6443: "https-api",
    7001: "http-alt",
    8000: "http-alt",
    8008: "http-alt",
    8080: "http-proxy",
    8081: "http-alt",
    8088: "http-alt",
    8443: "https-alt",
    8883: "mqtt-tls",
    8888: "http-alt",
    9000: "management",
    9042: "database",
    9090: "http-alt",
    9100: "printer",
    9200: "http-api",
    9300: "cluster",
    9418: "git",
    9443: "https-alt",
    11211: "cache",
    27017: "database",
}

HTTP_PORTS = {
    80,
    81,
    3000,
    3128,
    5000,
    5357,
    5985,
    7001,
    8000,
    8008,
    8080,
    8081,
    8088,
    8888,
    9090,
    9200,
}
HTTPS_PORTS = {443, 2376, 4443, 5986, 6443, 8443, 9443}
BANNER_PORTS = {21, 22, 23, 25, 110, 119, 143, 587}
SECURE_PROTOCOLS = {
    "container-api-tls",
    "directory-tls",
    "ftps",
    "ftps-data",
    "https",
    "https-alt",
    "imaps",
    "ldaps",
    "mqtt-tls",
    "pop3s",
    "smtps",
    "winrm-https",
}

ProgressCallback = Callable[[str, int, int, str], None]


class DiscoveryError(RuntimeError):
    """Raised when discovery cannot determine or scan a safe local network."""


@dataclass(frozen=True, slots=True)
class NetworkTarget:
    """An automatically detected network or explicitly selected IPv4 target."""

    interface: str
    local_address: str | None
    network: str
    gateway: str | None = None
    original_network: str | None = None
    exhaustive: bool = False


@dataclass(frozen=True, slots=True)
class DiscoveredService:
    """A generic TCP service found on a host."""

    port: int
    protocol: str
    secure: bool = False
    http_status: int | None = None
    banner: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredHost:
    """A reachable host found during active and passive discovery."""

    address: str
    network: str
    hostname: str | None
    mac_address: str | None
    ping_responded: bool
    is_gateway: bool
    is_local: bool
    services: tuple[DiscoveredService, ...]

    @property
    def display_name(self) -> str:
        """Return a readable, product-neutral host label."""

        if self.hostname and self.hostname != self.address:
            return f"{self.hostname} ({self.address})"
        if self.is_gateway:
            return f"Gateway ({self.address})"
        return f"Host {self.address}"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Complete result of one infrastructure discovery run."""

    started_at: str
    completed_at: str
    duration_ms: float
    networks: tuple[NetworkTarget, ...]
    hosts: tuple[DiscoveredHost, ...]
    warnings: tuple[str, ...] = ()
    worker_count: int = 1
    logical_processors: int = 1
    exhaustive_addresses: tuple[str, ...] = ()

    @property
    def service_count(self) -> int:
        """Return the number of discovered TCP services."""

        return sum(len(host.services) for host in self.hosts)


@dataclass(frozen=True, slots=True)
class DiscoverySettings:
    """Safe bounds and concurrency settings for a discovery run."""

    timeout: float = 0.3
    workers: int = field(default_factory=lambda: automatic_worker_count("discovery"))
    max_hosts_per_network: int = 1022
    ports: tuple[int, ...] = DEFAULT_SCAN_PORTS
    exhaustive_ports: tuple[int, ...] = ALL_TCP_PORTS

    def __post_init__(self) -> None:
        if self.timeout <= 0:
            raise ValueError("Discovery timeout must be greater than zero.")
        if not 1 <= self.workers <= 256:
            raise ValueError("Discovery workers must be between 1 and 256.")
        if not 1 <= self.max_hosts_per_network <= 4094:
            raise ValueError("Maximum hosts per network must be between 1 and 4094.")
        if not self.ports or any(not 1 <= port <= 65535 for port in self.ports):
            raise ValueError("Discovery ports must be integers from 1 to 65535.")
        if not self.exhaustive_ports or any(
            not 1 <= port <= 65535 for port in self.exhaustive_ports
        ):
            raise ValueError(
                "Exhaustive discovery ports must be integers from 1 to 65535."
            )


def _decode_command_output(value: bytes | str | None) -> str:
    """Decode redirected command output without relying on Windows ANSI pages."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _run_command(command: list[str], timeout: float = 8) -> str:
    """Run a fixed system inventory command and return its output."""

    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    )
    completed = subprocess.run(
        command,
        capture_output=True,
        timeout=timeout,
        check=False,
        creationflags=creation_flags,
    )
    if completed.returncode != 0:
        return ""
    return _decode_command_output(completed.stdout)


def _as_json_list(value: str | None) -> list[dict[str, Any]]:
    if not value or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _windows_interface_records() -> tuple[
    list[dict[str, Any]],
    dict[str, str],
    list[dict[str, Any]],
]:
    utf8_output = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    )
    address_script = utf8_output + (
        "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Where-Object { $_.AddressState -eq 'Preferred' -and "
        "$_.IPAddress -notlike '127.*' } | "
        "Select-Object InterfaceAlias,IPAddress,PrefixLength | "
        "ConvertTo-Json -Compress"
    )
    route_script = utf8_output + (
        "Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Sort-Object RouteMetric | "
        "Select-Object InterfaceAlias,DestinationPrefix,NextHop | "
        "ConvertTo-Json -Compress"
    )
    addresses = _as_json_list(
        _run_command(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                address_script,
            ]
        )
    )
    routes = _as_json_list(
        _run_command(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                route_script,
            ]
        )
    )
    gateways: dict[str, str] = {}
    for item in routes:
        if (
            item.get("DestinationPrefix") == "0.0.0.0/0"
            and item.get("InterfaceAlias")
            and item.get("NextHop")
            and item.get("NextHop") != "0.0.0.0"
        ):
            gateways.setdefault(
                str(item["InterfaceAlias"]),
                str(item["NextHop"]),
            )
    return addresses, gateways, routes


def _linux_interface_records() -> tuple[
    list[dict[str, Any]],
    dict[str, str],
    list[dict[str, Any]],
]:
    records: list[dict[str, Any]] = []
    for interface in _as_json_list(
        _run_command(["ip", "-j", "-4", "addr", "show", "up"])
    ):
        name = str(interface.get("ifname", "unknown"))
        address_info = interface.get("addr_info", [])
        if not isinstance(address_info, list):
            continue
        for address in address_info:
            if not isinstance(address, dict) or address.get("family") != "inet":
                continue
            records.append(
                {
                    "InterfaceAlias": name,
                    "IPAddress": address.get("local"),
                    "PrefixLength": address.get("prefixlen"),
                }
            )

    gateways: dict[str, str] = {}
    routes = _as_json_list(_run_command(["ip", "-j", "-4", "route", "show"]))
    for route in routes:
        interface = route.get("dev")
        gateway = route.get("gateway")
        if route.get("dst") == "default" and interface and gateway:
            gateways.setdefault(str(interface), str(gateway))
    return records, gateways, routes


def _generic_interface_records() -> tuple[
    list[dict[str, Any]],
    dict[str, str],
    list[dict[str, Any]],
]:
    records: list[dict[str, Any]] = []
    try:
        resolved = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        resolved = []
    for index, item in enumerate(resolved):
        address = item[4][0]
        records.append(
            {
                "InterfaceAlias": f"interface-{index + 1}",
                "IPAddress": address,
                "PrefixLength": 24,
            }
        )

    # A UDP connect does not send data, but lets the OS select the primary route.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
        records.append(
            {
                "InterfaceAlias": "primary",
                "IPAddress": address,
                "PrefixLength": 24,
            }
        )
    except OSError:
        pass
    return records, {}, []


def _is_private_address(address: ipaddress.IPv4Address) -> bool:
    return any(address in network for network in PRIVATE_RANGES)


def _is_private_network(network: ipaddress.IPv4Network) -> bool:
    return any(network.subnet_of(private) for private in PRIVATE_RANGES)


def _bounded_network(
    address: ipaddress.IPv4Address,
    prefix_length: int,
    max_hosts: int,
) -> tuple[ipaddress.IPv4Network, str | None]:
    original = ipaddress.ip_network(f"{address}/{prefix_length}", strict=False)
    if max(1, original.num_addresses - 2) <= max_hosts:
        return original, None

    bounded_prefix = prefix_length
    while bounded_prefix < 30:
        bounded_prefix += 1
        candidate = ipaddress.ip_network(f"{address}/{bounded_prefix}", strict=False)
        if max(1, candidate.num_addresses - 2) <= max_hosts:
            return candidate, str(original)
    return ipaddress.ip_network(f"{address}/30", strict=False), str(original)


def detect_local_networks(max_hosts: int = 1022) -> tuple[NetworkTarget, ...]:
    """Detect connected private IPv4 networks without requiring administrator access."""

    system = platform.system()
    try:
        if system == "Windows":
            records, gateways, routes = _windows_interface_records()
        elif system == "Linux":
            records, gateways, routes = _linux_interface_records()
        else:
            records, gateways, routes = _generic_interface_records()
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        records, gateways, routes = _generic_interface_records()

    if not records:
        records, gateways, routes = _generic_interface_records()

    targets: list[NetworkTarget] = []
    seen: set[str] = set()
    for record in records:
        try:
            address = ipaddress.ip_address(str(record.get("IPAddress")))
            prefix = int(record.get("PrefixLength", 24))
        except (ValueError, TypeError):
            continue
        if not isinstance(address, ipaddress.IPv4Address):
            continue
        if (
            address.is_loopback
            or address.is_link_local
            or not _is_private_address(address)
        ):
            continue
        if not 1 <= prefix <= 32:
            continue

        network, original = _bounded_network(address, prefix, max_hosts)
        network_text = str(network)
        if network_text in seen:
            continue
        seen.add(network_text)
        interface = str(record.get("InterfaceAlias") or "unknown")
        gateway = gateways.get(interface)
        if gateway:
            try:
                if ipaddress.ip_address(gateway) not in network:
                    gateway = None
            except ValueError:
                gateway = None
        targets.append(
            NetworkTarget(
                interface=interface,
                local_address=str(address),
                network=network_text,
                gateway=gateway,
                original_network=original,
            )
        )

    addresses_by_interface: dict[str, list[ipaddress.IPv4Address]] = {}
    for record in records:
        try:
            address = ipaddress.ip_address(str(record.get("IPAddress")))
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv4Address):
            interface = str(record.get("InterfaceAlias") or "unknown")
            addresses_by_interface.setdefault(interface, []).append(address)

    for route in routes:
        destination = route.get("DestinationPrefix", route.get("dst"))
        if not destination or destination == "default":
            continue
        try:
            network = ipaddress.ip_network(str(destination), strict=False)
        except ValueError:
            continue
        if not isinstance(network, ipaddress.IPv4Network):
            continue
        if network.prefixlen == 0 or not _is_private_network(network):
            continue

        host_count = max(1, network.num_addresses - 2)
        original: str | None = None
        if host_count > max_hosts:
            anchor = next(network.hosts(), network.network_address)
            network, original = _bounded_network(
                anchor,
                network.prefixlen,
                max_hosts,
            )
        network_text = str(network)
        if network_text in seen:
            continue
        seen.add(network_text)

        interface = str(route.get("InterfaceAlias", route.get("dev", "routed-network")))
        local_address = next(
            (
                str(address)
                for address in addresses_by_interface.get(interface, [])
                if address in network
            ),
            None,
        )
        next_hop = route.get("NextHop", route.get("gateway"))
        gateway: str | None = None
        if next_hop and str(next_hop) != "0.0.0.0":
            try:
                candidate = ipaddress.ip_address(str(next_hop))
                if candidate in network:
                    gateway = str(candidate)
            except ValueError:
                pass
        targets.append(
            NetworkTarget(
                interface=interface,
                local_address=local_address,
                network=network_text,
                gateway=gateway,
                original_network=original,
            )
        )

    if not targets:
        raise DiscoveryError(
            "No connected private IPv4 network was found. "
            "Connect to a LAN or VPN and try again."
        )
    return tuple(
        sorted(
            targets,
            key=lambda item: int(ipaddress.ip_network(item.network).network_address),
        )
    )


def network_targets_from_cidrs(
    cidrs: Iterable[str],
    *,
    max_hosts: int = 1022,
) -> tuple[NetworkTarget, ...]:
    """Validate explicit IP addresses or private CIDRs."""

    targets: list[NetworkTarget] = []
    seen: set[str] = set()
    for raw in cidrs:
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError as exc:
            raise DiscoveryError(f"Invalid network '{raw}': {exc}") from exc
        if not isinstance(network, ipaddress.IPv4Network):
            raise DiscoveryError(f"Only IPv4 discovery is supported: {raw}.")
        authorized_public_host = (
            network.prefixlen == 32 and network.network_address.is_global
        )
        if not _is_private_network(network) and not authorized_public_host:
            raise DiscoveryError(
                "CIDR discovery is limited to private or carrier-grade NAT "
                f"ranges; only an exact public host may be targeted: {raw}."
            )
        host_count = max(1, network.num_addresses - 2)
        if host_count > max_hosts:
            raise DiscoveryError(
                f"Network {network} contains {host_count} hosts; "
                f"the configured safety limit is {max_hosts}."
            )
        network_text = str(network)
        if network_text in seen:
            continue
        seen.add(network_text)
        targets.append(
            NetworkTarget(
                interface="manual",
                local_address=None,
                network=network_text,
                exhaustive=network.prefixlen == 32,
            )
        )
    if not targets:
        raise DiscoveryError("At least one private IPv4 network is required.")
    return tuple(targets)


def _merge_network_targets(
    primary: tuple[NetworkTarget, ...],
    additional: tuple[NetworkTarget, ...],
) -> tuple[NetworkTarget, ...]:
    """Add targets that are not already covered by a selected network."""

    merged = list(primary)
    selected_networks = [
        ipaddress.ip_network(target.network, strict=False) for target in primary
    ]
    for target in additional:
        candidate = ipaddress.ip_network(target.network, strict=False)
        if any(candidate.subnet_of(selected) for selected in selected_networks):
            continue
        merged.append(target)
        selected_networks.append(candidate)
    return tuple(merged)


def _target_addresses(targets: Iterable[NetworkTarget]) -> set[str]:
    """Return addresses explicitly marked for exhaustive scanning."""

    addresses: set[str] = set()
    for target in targets:
        if not target.exhaustive:
            continue
        network = ipaddress.ip_network(target.network, strict=False)
        addresses.update(str(address) for address in network.hosts())
    return addresses


_MAC_PATTERN = re.compile(
    r"(?P<ip>\d{1,3}(?:\.\d{1,3}){3}).*?"
    r"(?P<mac>[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})"
)


def parse_neighbor_table(output: str) -> dict[str, str]:
    """Parse common Windows, Linux, and macOS ARP/neighbor table formats."""

    neighbors: dict[str, str] = {}
    for line in output.splitlines():
        match = _MAC_PATTERN.search(line)
        if not match:
            continue
        try:
            address = ipaddress.ip_address(match.group("ip"))
        except ValueError:
            continue
        if not isinstance(address, ipaddress.IPv4Address):
            continue
        mac = match.group("mac").replace("-", ":").upper()
        if mac == "00:00:00:00:00:00":
            continue
        neighbors[str(address)] = mac
    return neighbors


def read_neighbor_table() -> dict[str, str]:
    """Read the operating system's passive IPv4 neighbor cache."""

    commands = (
        [["arp", "-a"]]
        if platform.system() == "Windows"
        else [["ip", "neigh", "show"], ["arp", "-an"]]
    )
    for command in commands:
        try:
            output = _run_command(command)
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            continue
        neighbors = parse_neighbor_table(output)
        if neighbors:
            return neighbors
    return {}


def _ping_host(address: str, timeout: float) -> bool:
    system = platform.system()
    if system == "Windows":
        command = ["ping", "-n", "1", "-w", str(max(1, int(timeout * 1000))), address]
    elif system == "Darwin":
        command = ["ping", "-c", "1", "-W", str(max(1, int(timeout * 1000))), address]
    else:
        command = ["ping", "-c", "1", "-W", str(max(1, math.ceil(timeout))), address]
    try:
        creation_flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if system == "Windows" else 0
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout + 1,
            check=False,
            creationflags=creation_flags,
        )
        return completed.returncode == 0
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False


def _tcp_is_open(address: str, port: int, timeout: float) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout)
            return probe.connect_ex((address, port)) == 0
    except OSError:
        return False


def _clean_banner(value: bytes) -> str | None:
    text = value.decode("utf-8", errors="replace")
    text = " ".join(text.replace("\x00", " ").split())
    return text[:180] or None


def _probe_http(
    address: str,
    port: int,
    *,
    secure: bool,
    timeout: float,
) -> tuple[int | None, str | None]:
    try:
        raw_socket = socket.create_connection((address, port), timeout=timeout)
        connection: socket.socket | ssl.SSLSocket = raw_socket
        if secure:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            connection = context.wrap_socket(raw_socket, server_hostname=address)
        with connection:
            connection.settimeout(timeout)
            request = (
                f"HEAD / HTTP/1.0\r\nHost: {address}\r\n"
                "User-Agent: Service-Dependency-Mapper/1.2\r\n\r\n"
            )
            connection.sendall(request.encode("ascii"))
            response = connection.recv(2048)
    except (OSError, ssl.SSLError):
        return None, None

    banner = _clean_banner(response)
    if not banner:
        return None, None
    match = re.match(r"HTTP/\d(?:\.\d)?\s+(\d{3})", banner, re.IGNORECASE)
    status = int(match.group(1)) if match else None
    server = re.search(r"(?:^|\s)Server:\s*([^\r\n]+)", banner, re.IGNORECASE)
    if server:
        banner = f"HTTP {status or '?'}; Server: {server.group(1).strip()[:100]}"
    elif status:
        banner = f"HTTP {status}"
    return status, banner


def fingerprint_service(address: str, port: int, timeout: float) -> DiscoveredService:
    """Identify a service generically from its standard port and safe banner probes."""

    protocol = PORT_PROTOCOLS.get(port)
    if protocol is None:
        try:
            protocol = socket.getservbyport(port, "tcp")
        except OSError:
            protocol = "tcp"

    secure = port in HTTPS_PORTS or protocol in SECURE_PROTOCOLS
    http_status: int | None = None
    banner: str | None = None
    if port in HTTP_PORTS or port in HTTPS_PORTS:
        http_status, banner = _probe_http(
            address,
            port,
            secure=port in HTTPS_PORTS,
            timeout=max(timeout, 0.5),
        )
        if http_status is not None:
            protocol = "https" if port in HTTPS_PORTS else "http"
            secure = port in HTTPS_PORTS
    elif port in BANNER_PORTS:
        try:
            with socket.create_connection(
                (address, port), timeout=timeout
            ) as connection:
                connection.settimeout(timeout)
                banner = _clean_banner(connection.recv(512))
        except OSError:
            pass

    return DiscoveredService(
        port=port,
        protocol=protocol,
        secure=secure,
        http_status=http_status,
        banner=banner,
    )


def _resolve_hostname(address: str) -> str | None:
    try:
        hostname = socket.gethostbyaddr(address)[0].rstrip(".")
    except (socket.herror, socket.gaierror, OSError):
        return None
    return hostname or None


def _emit_progress(
    callback: ProgressCallback | None,
    stage: str,
    completed: int,
    total: int,
    message: str,
) -> None:
    if callback is not None:
        callback(stage, completed, total, message)


def _cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _run_parallel(
    function: Callable[[Any], Any],
    items: Iterable[Any],
    *,
    total: int,
    workers: int,
    progress: ProgressCallback | None,
    stage: str,
    message: str,
    cancel_event: threading.Event | None,
) -> list[Any]:
    if total <= 0:
        return []
    effective_workers = max(1, min(workers, total))
    results: list[Any] = []
    completed = 0
    progress_interval = max(25, math.ceil(total / 500))
    iterator = iter(items)
    with ThreadPoolExecutor(
        max_workers=effective_workers,
        thread_name_prefix="sdmap-discovery",
    ) as pool:
        pending: set[Future[Any]] = set()
        for _ in range(min(total, effective_workers * 4)):
            try:
                item = next(iterator)
            except StopIteration:
                break
            pending.add(pool.submit(function, item))

        while pending:
            if _cancelled(cancel_event):
                for future in pending:
                    future.cancel()
                raise DiscoveryError("Discovery was cancelled.")
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                completed += 1
                # One failed probe must not abort the entire inventory.
                with suppress(Exception):
                    value = future.result()
                    if value is not None:
                        results.append(value)
                try:
                    item = next(iterator)
                except StopIteration:
                    continue
                pending.add(pool.submit(function, item))
            if (
                completed == 1
                or completed == total
                or completed % progress_interval == 0
            ):
                _emit_progress(progress, stage, completed, total, message)
    return results


def discover_infrastructure(
    networks: tuple[NetworkTarget, ...] | None = None,
    *,
    additional_targets: tuple[NetworkTarget, ...] = (),
    settings: DiscoverySettings | None = None,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> DiscoveryResult:
    """Discover connected private networks, hosts, and generic TCP services."""

    settings = settings or DiscoverySettings()
    worker_plan = build_discovery_worker_plan(settings.workers)
    started_clock = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    if networks is None:
        try:
            primary_targets = detect_local_networks(settings.max_hosts_per_network)
        except DiscoveryError:
            if not additional_targets:
                raise
            primary_targets = ()
    else:
        primary_targets = networks
    targets = _merge_network_targets(primary_targets, additional_targets)
    if not targets:
        raise DiscoveryError("No private IPv4 networks were selected.")
    exhaustive_addresses = _target_addresses((*primary_targets, *additional_targets))

    address_network: dict[str, str] = {}
    for target in targets:
        network = ipaddress.ip_network(target.network)
        for address in network.hosts():
            address_network.setdefault(str(address), target.network)
    addresses = sorted(
        address_network, key=lambda value: int(ipaddress.ip_address(value))
    )
    if not addresses:
        raise DiscoveryError(
            "The selected networks do not contain usable host addresses."
        )

    _emit_progress(
        progress,
        "performance",
        0,
        len(addresses),
        (
            f"Using {worker_plan.tcp_workers} I/O workers across "
            f"{worker_plan.logical_processors} logical processors…"
        ),
    )
    ping_results = _run_parallel(
        lambda address: (address, _ping_host(address, settings.timeout)),
        addresses,
        total=len(addresses),
        workers=worker_plan.icmp_workers,
        progress=progress,
        stage="hosts",
        message="Probing hosts with ICMP…",
        cancel_event=cancel_event,
    )
    ping_alive = {address for address, alive in ping_results if alive}

    neighbors = read_neighbor_table()
    local_addresses = {
        target.local_address for target in targets if target.local_address
    }
    gateways = {target.gateway for target in targets if target.gateway}
    alive = (
        ping_alive
        | (set(neighbors) & set(addresses))
        | local_addresses
        | gateways
        | exhaustive_addresses
    )

    unknown_addresses = [address for address in addresses if address not in alive]
    discovery_ports = sorted(set(HOST_DISCOVERY_PORTS) & set(settings.ports))
    discovery_tasks = [
        (address, port) for address in unknown_addresses for port in discovery_ports
    ]
    initial_open = _run_parallel(
        lambda item: (
            (item[0], item[1])
            if _tcp_is_open(item[0], item[1], settings.timeout)
            else None
        ),
        discovery_tasks,
        total=len(discovery_tasks),
        workers=worker_plan.tcp_workers,
        progress=progress,
        stage="hosts",
        message="Finding hosts that block ICMP…",
        cancel_event=cancel_event,
    )
    open_ports: dict[str, set[int]] = {}
    for address, port in initial_open:
        alive.add(address)
        open_ports.setdefault(address, set()).add(port)

    sorted_alive = sorted(alive, key=lambda value: int(ipaddress.ip_address(value)))

    def ports_for(address: str) -> tuple[int, ...]:
        if address in exhaustive_addresses:
            return settings.exhaustive_ports
        return settings.ports

    remaining_total = sum(
        sum(port not in open_ports.get(address, set()) for port in ports_for(address))
        for address in sorted_alive
    )
    remaining_tasks = (
        (address, port)
        for address in sorted_alive
        for port in ports_for(address)
        if port not in open_ports.get(address, set())
    )
    service_message = "Scanning generic TCP services…"
    if exhaustive_addresses:
        service_message = (
            "Scanning all TCP ports on explicitly targeted server addresses…"
        )
    scanned_ports = _run_parallel(
        lambda item: (
            (item[0], item[1])
            if _tcp_is_open(item[0], item[1], settings.timeout)
            else None
        ),
        remaining_tasks,
        total=remaining_total,
        workers=worker_plan.tcp_workers,
        progress=progress,
        stage="services",
        message=service_message,
        cancel_event=cancel_event,
    )
    for address, port in scanned_ports:
        open_ports.setdefault(address, set()).add(port)

    hostname_results = _run_parallel(
        lambda address: (address, _resolve_hostname(address)),
        sorted_alive,
        total=len(sorted_alive),
        workers=worker_plan.resolver_workers,
        progress=progress,
        stage="identity",
        message="Resolving host names…",
        cancel_event=cancel_event,
    )
    hostnames = dict(hostname_results)

    service_tasks = [
        (address, port)
        for address in sorted_alive
        for port in sorted(open_ports.get(address, set()))
    ]
    fingerprint_results = _run_parallel(
        lambda item: (
            item[0],
            fingerprint_service(item[0], item[1], settings.timeout),
        ),
        service_tasks,
        total=len(service_tasks),
        workers=worker_plan.fingerprint_workers,
        progress=progress,
        stage="fingerprint",
        message="Identifying discovered services…",
        cancel_event=cancel_event,
    )
    services_by_host: dict[str, list[DiscoveredService]] = {}
    for address, service in fingerprint_results:
        services_by_host.setdefault(address, []).append(service)

    hosts = tuple(
        DiscoveredHost(
            address=address,
            network=address_network.get(address, targets[0].network),
            hostname=hostnames.get(address),
            mac_address=neighbors.get(address),
            ping_responded=address in ping_alive,
            is_gateway=address in gateways,
            is_local=address in local_addresses,
            services=tuple(
                sorted(services_by_host.get(address, []), key=lambda item: item.port)
            ),
        )
        for address in sorted_alive
    )

    warnings = tuple(
        f"{target.interface}: {target.original_network} was limited to "
        f"{target.network} ({settings.max_hosts_per_network}-host safety limit)."
        for target in targets
        if target.original_network
    ) + tuple(
        f"Explicit target {address} was scanned across "
        f"{len(settings.exhaustive_ports)} TCP ports."
        for address in sorted(
            exhaustive_addresses,
            key=lambda value: int(ipaddress.ip_address(value)),
        )
    )
    completed_at = datetime.now(timezone.utc).isoformat()
    duration_ms = round((time.perf_counter() - started_clock) * 1000, 2)
    _emit_progress(
        progress,
        "complete",
        len(hosts),
        len(hosts),
        (
            f"Found {len(hosts)} hosts and "
            f"{sum(len(item.services) for item in hosts)} services."
        ),
    )
    return DiscoveryResult(
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        networks=targets,
        hosts=hosts,
        warnings=warnings,
        worker_count=worker_plan.tcp_workers,
        logical_processors=worker_plan.logical_processors,
        exhaustive_addresses=tuple(
            sorted(
                exhaustive_addresses,
                key=lambda value: int(ipaddress.ip_address(value)),
            )
        ),
    )


def _safe_id(prefix: str, value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return f"{prefix}_{normalized}"


def network_component_id(network: str) -> str:
    """Return the stable component id used for a discovered network."""

    return _safe_id("network", network)


def host_component_id(address: str) -> str:
    """Return the stable component id used for a discovered host."""

    return _safe_id("host", address)


def service_component_id(address: str, port: int) -> str:
    """Return the stable component id used for a discovered TCP service."""

    return _safe_id("service", f"{address}_{port}")


def discovery_to_document(result: DiscoveryResult) -> dict[str, Any]:
    """Convert a discovery result into a version 1 dependency-map document."""

    components: list[dict[str, Any]] = []
    network_ids: dict[str, str] = {}
    for network in result.networks:
        network_id = network_component_id(network.network)
        network_ids[network.network] = network_id
        tags = ["discovered", "network", f"interface:{network.interface}"]
        if network.original_network:
            tags.append(f"bounded-from:{network.original_network}")
        if network.exhaustive:
            tags.append("explicit-target")
        components.append(
            {
                "id": network_id,
                "name": f"Network {network.network}",
                "critical": False,
                "check": {"type": "none"},
                "tags": tags,
            }
        )

    for host in result.hosts:
        host_id = host_component_id(host.address)
        host_tags = ["discovered", "host", f"ip:{host.address}"]
        if host.mac_address:
            host_tags.append(f"mac:{host.mac_address}")
        if host.hostname:
            host_tags.append(f"hostname:{host.hostname}")
        if host.is_gateway:
            host_tags.append("gateway")
        if host.is_local:
            host_tags.append("local")
        if host.address in result.exhaustive_addresses:
            host_tags.append("explicit-target")

        if host.ping_responded:
            host_check: dict[str, Any] = {
                "type": "icmp",
                "target": host.address,
                "count": 1,
            }
        elif host.services:
            host_check = {
                "type": "tcp",
                "host": host.address,
                "port": host.services[0].port,
            }
        else:
            host_check = {"type": "none"}

        components.append(
            {
                "id": host_id,
                "name": host.display_name,
                "critical": False,
                "depends_on": [network_ids[host.network]],
                "check": host_check,
                "tags": host_tags,
            }
        )

        for service in host.services:
            service_id = service_component_id(host.address, service.port)
            service_tags = [
                "discovered",
                "service",
                f"protocol:{service.protocol}",
                f"port:{service.port}",
            ]
            if service.secure:
                service_tags.append("secure")
            if service.http_status is not None:
                service_tags.append(f"http-status:{service.http_status}")
            components.append(
                {
                    "id": service_id,
                    "name": f"{service.protocol.upper()} :{service.port}",
                    "critical": False,
                    "depends_on": [host_id],
                    "check": {
                        "type": "tcp",
                        "host": host.address,
                        "port": service.port,
                    },
                    "tags": service_tags,
                }
            )

    return {
        "version": 1,
        "service": {
            "name": "Discovered infrastructure",
            "description": (
                "Vendor-neutral map generated from detected networks and "
                "explicit IPv4 targets."
            ),
        },
        "defaults": {"timeout": 2, "workers": "auto"},
        "discovery": {
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "duration_ms": result.duration_ms,
            "networks": [network.network for network in result.networks],
            "hosts": len(result.hosts),
            "services": result.service_count,
            "exhaustive_addresses": list(result.exhaustive_addresses),
            "performance": {
                "workers": result.worker_count,
                "logical_processors": result.logical_processors,
            },
            "warnings": list(result.warnings),
            "inventory": [
                {
                    "address": host.address,
                    "network": host.network,
                    "hostname": host.hostname,
                    "mac_address": host.mac_address,
                    "ping_responded": host.ping_responded,
                    "is_gateway": host.is_gateway,
                    "is_local": host.is_local,
                    "services": [
                        {
                            "port": service.port,
                            "protocol": service.protocol,
                            "secure": service.secure,
                            "http_status": service.http_status,
                            "banner": service.banner,
                        }
                        for service in host.services
                    ],
                }
                for host in result.hosts
            ],
        },
        "components": components,
    }


def render_discovery_yaml(result: DiscoveryResult) -> str:
    """Render a discovery result as a loadable YAML dependency map."""

    return yaml.safe_dump(
        discovery_to_document(result),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


def write_discovery_map(result: DiscoveryResult, destination: str | Path) -> Path:
    """Write a discovered dependency map and return its resolved path."""

    output = Path(destination).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_discovery_yaml(result), encoding="utf-8")
    return output.resolve()


def default_discovery_output() -> Path:
    """Return a timestamped, non-overwriting map path for the desktop GUI."""

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return (
        Path.home()
        / "Documents"
        / "Service Dependency Mapper"
        / "maps"
        / f"discovered-infrastructure-{timestamp}.yaml"
    )
