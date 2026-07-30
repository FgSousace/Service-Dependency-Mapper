from __future__ import annotations

import socket
import unittest
import urllib.error
from unittest.mock import patch

from service_dependency_mapper.checks import check_dns, check_http, check_tcp, run_check
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


class CheckTests(unittest.TestCase):
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
