from __future__ import annotations

import socket
import subprocess
import unittest
import urllib.error
from unittest.mock import patch

from service_dependency_mapper.checks import (
    check_dns,
    check_http,
    check_icmp,
    check_none,
    check_tcp,
    check_tls,
    run_check,
)
from service_dependency_mapper.models import CheckStatus, Component


def make_component(check_type: str, check: dict, *, timeout: float = 1) -> Component:
    return Component(
        component_id="target",
        name="Target",
        check_type=check_type,
        check=check,
        timeout=timeout,
    )


class FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getpeername(self):
        return ("203.0.113.10", 443)


class FakeResponse:
    def __init__(self, status=200, body=b"ok", url="https://example.com/"):
        self.status = status
        self.body = body
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body

    def geturl(self):
        return self.url


class FakeTlsSocket:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getpeercert(self):
        return {"notAfter": "Jan  1 00:00:00 2099 GMT"}

    def version(self):
        return "TLSv1.3"

    def cipher(self):
        return ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)


class FakeTlsContext:
    def wrap_socket(self, _socket, *, server_hostname):
        self.server_hostname = server_hostname
        return FakeTlsSocket()


class CheckTests(unittest.TestCase):
    def test_topology_only_check_is_healthy_without_network_access(self):
        result = check_none(make_component("none", {}))
        self.assertEqual(result.status, CheckStatus.UP)
        self.assertTrue(result.details["topology_only"])

    @patch("service_dependency_mapper.checks.socket.getaddrinfo")
    def test_dns_success(self, getaddrinfo):
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0)),
        ]
        result = check_dns(make_component("dns", {"target": "example.com"}))
        self.assertEqual(result.status, CheckStatus.UP)
        self.assertEqual(result.details["addresses"], ["203.0.113.10"])

    @patch("service_dependency_mapper.checks.socket.getaddrinfo")
    def test_dns_expected_address_mismatch(self, getaddrinfo):
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0))
        ]
        result = check_dns(
            make_component(
                "dns",
                {
                    "target": "example.com",
                    "expected_addresses": ["198.51.100.5"],
                },
            )
        )
        self.assertEqual(result.status, CheckStatus.DOWN)

    @patch("service_dependency_mapper.checks.socket.getaddrinfo")
    def test_dns_resolution_failure(self, getaddrinfo):
        getaddrinfo.side_effect = socket.gaierror("not found")
        result = check_dns(make_component("dns", {"target": "invalid"}))
        self.assertEqual(result.status, CheckStatus.DOWN)
        self.assertIn("resolution failed", result.message)

    @patch("service_dependency_mapper.checks.socket.create_connection")
    def test_tcp_success(self, create_connection):
        create_connection.return_value = FakeSocket()
        result = check_tcp(make_component("tcp", {"host": "example.com", "port": 443}))
        self.assertEqual(result.status, CheckStatus.UP)
        self.assertEqual(result.details["peer"], "203.0.113.10:443")

    @patch("service_dependency_mapper.checks.socket.create_connection")
    def test_tcp_failure(self, create_connection):
        create_connection.side_effect = ConnectionRefusedError("refused")
        result = check_tcp(make_component("tcp", {"host": "example.com", "port": 443}))
        self.assertEqual(result.status, CheckStatus.DOWN)
        self.assertIn("connection failed", result.message)

    @patch("service_dependency_mapper.checks.platform.system", return_value="Windows")
    @patch("service_dependency_mapper.checks.subprocess.run")
    def test_icmp_success(self, run, _system):
        run.return_value = subprocess.CompletedProcess(["ping"], 0, "", "")
        result = check_icmp(make_component("icmp", {"target": "192.0.2.1", "count": 2}))
        self.assertEqual(result.status, CheckStatus.UP)
        self.assertEqual(result.details["packets_requested"], 2)
        self.assertIn("-n", run.call_args.args[0])

    @patch("service_dependency_mapper.checks.platform.system", return_value="Linux")
    @patch("service_dependency_mapper.checks.subprocess.run")
    def test_icmp_failure(self, run, _system):
        run.return_value = subprocess.CompletedProcess(["ping"], 1, "", "")
        result = check_icmp(make_component("icmp", {"target": "192.0.2.1", "count": 1}))
        self.assertEqual(result.status, CheckStatus.DOWN)
        self.assertIn("No successful ICMP reply", result.message)

    @patch("service_dependency_mapper.checks.time.time", return_value=1_000_000)
    @patch(
        "service_dependency_mapper.checks.ssl.cert_time_to_seconds",
        return_value=4_000_000,
    )
    @patch("service_dependency_mapper.checks.ssl.create_default_context")
    @patch("service_dependency_mapper.checks.socket.create_connection")
    def test_tls_valid_certificate(
        self,
        create_connection,
        create_default_context,
        _cert_time,
        _time,
    ):
        create_connection.return_value = FakeSocket()
        create_default_context.return_value = FakeTlsContext()
        result = check_tls(
            make_component(
                "tls",
                {
                    "host": "example.com",
                    "port": 443,
                    "min_days_remaining": 14,
                },
            )
        )
        self.assertEqual(result.status, CheckStatus.UP)
        self.assertEqual(result.details["protocol"], "TLSv1.3")

    @patch("service_dependency_mapper.checks.time.time", return_value=1_000_000)
    @patch(
        "service_dependency_mapper.checks.ssl.cert_time_to_seconds",
        return_value=1_100_000,
    )
    @patch("service_dependency_mapper.checks.ssl.create_default_context")
    @patch("service_dependency_mapper.checks.socket.create_connection")
    def test_tls_expiring_certificate(
        self,
        create_connection,
        create_default_context,
        _cert_time,
        _time,
    ):
        create_connection.return_value = FakeSocket()
        create_default_context.return_value = FakeTlsContext()
        result = check_tls(
            make_component(
                "tls",
                {
                    "host": "example.com",
                    "port": 443,
                    "min_days_remaining": 14,
                },
            )
        )
        self.assertEqual(result.status, CheckStatus.DOWN)
        self.assertIn("expires in", result.message)

    @patch("service_dependency_mapper.checks.urllib.request.urlopen")
    def test_http_success_and_content(self, urlopen):
        urlopen.return_value = FakeResponse(body=b"service is healthy")
        result = check_http(
            make_component(
                "http",
                {
                    "url": "https://example.com/",
                    "expected_status": [200],
                    "contains": "healthy",
                },
            )
        )
        self.assertEqual(result.status, CheckStatus.UP)
        self.assertEqual(result.details["status"], 200)

    @patch("service_dependency_mapper.checks.urllib.request.urlopen")
    def test_http_unexpected_status(self, urlopen):
        urlopen.return_value = FakeResponse(status=503)
        result = check_http(
            make_component(
                "http",
                {"url": "https://example.com/", "expected_status": [200]},
            )
        )
        self.assertEqual(result.status, CheckStatus.DOWN)
        self.assertIn("Unexpected HTTP status", result.message)

    @patch("service_dependency_mapper.checks.urllib.request.urlopen")
    def test_http_missing_content(self, urlopen):
        urlopen.return_value = FakeResponse(body=b"not ready")
        result = check_http(
            make_component(
                "http",
                {
                    "url": "https://example.com/",
                    "expected_status": [200],
                    "contains": "healthy",
                },
            )
        )
        self.assertEqual(result.status, CheckStatus.DOWN)
        self.assertIn("Expected content", result.message)

    @patch("service_dependency_mapper.checks.urllib.request.urlopen")
    def test_http_url_error(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("name resolution failed")
        result = check_http(
            make_component(
                "http",
                {"url": "https://example.com/", "expected_status": [200]},
            )
        )
        self.assertEqual(result.status, CheckStatus.DOWN)

    def test_dispatcher_isolates_unexpected_errors(self):
        malformed = make_component("dns", {})
        result = run_check(malformed)
        self.assertEqual(result.status, CheckStatus.ERROR)
        self.assertIn("Unexpected check error", result.message)


if __name__ == "__main__":
    unittest.main()
