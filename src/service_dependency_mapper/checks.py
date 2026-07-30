"""Network health-check implementations."""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from service_dependency_mapper.models import CheckResult, CheckStatus, Component

CheckFunction = Callable[[Component], CheckResult]


def _latency_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def check_dns(component: Component) -> CheckResult:
    """Resolve a hostname and optionally verify expected addresses."""

    started = time.perf_counter()
    target = str(component.check["target"])
    try:
        records = socket.getaddrinfo(target, None)
        addresses = sorted({record[4][0] for record in records})
        expected = set(component.check.get("expected_addresses", []))
        if expected and expected.isdisjoint(addresses):
            return CheckResult(
                component.component_id,
                CheckStatus.DOWN,
                _latency_ms(started),
                "Resolved addresses do not match the expected set.",
                {"addresses": addresses, "expected_addresses": sorted(expected)},
            )
        return CheckResult(
            component.component_id,
            CheckStatus.UP,
            _latency_ms(started),
            f"Resolved {len(addresses)} unique address(es).",
            {"addresses": addresses},
        )
    except socket.gaierror as exc:
        return CheckResult(
            component.component_id,
            CheckStatus.DOWN,
            _latency_ms(started),
            f"DNS resolution failed: {exc}",
        )
    except OSError as exc:
        return CheckResult(
            component.component_id,
            CheckStatus.ERROR,
            _latency_ms(started),
            f"DNS check error: {exc}",
        )


def check_tcp(component: Component) -> CheckResult:
    """Attempt a TCP connection within the configured timeout."""

    started = time.perf_counter()
    host = str(component.check["host"])
    port = int(component.check["port"])
    try:
        with socket.create_connection((host, port), timeout=component.timeout) as sock:
            peer = sock.getpeername()
        return CheckResult(
            component.component_id,
            CheckStatus.UP,
            _latency_ms(started),
            "TCP connection established.",
            {"peer": f"{peer[0]}:{peer[1]}"},
        )
    except TimeoutError as exc:
        return CheckResult(
            component.component_id,
            CheckStatus.DOWN,
            _latency_ms(started),
            f"TCP connection timed out: {exc}",
        )
    except OSError as exc:
        return CheckResult(
            component.component_id,
            CheckStatus.DOWN,
            _latency_ms(started),
            f"TCP connection failed: {exc}",
        )


def check_http(component: Component) -> CheckResult:
    """Request an HTTP endpoint and validate status and optional content."""

    started = time.perf_counter()
    url = str(component.check["url"])
    method = str(component.check.get("method", "GET"))
    expected_status = set(component.check.get("expected_status", [200]))
    expected_content = component.check.get("contains")
    max_read_bytes = 1_048_576
    request = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": "Service-Dependency-Mapper/1.0"},
    )

    try:
        try:
            response: Any = urllib.request.urlopen(request, timeout=component.timeout)
        except urllib.error.HTTPError as exc:
            response = exc

        with response:
            status = int(response.status)
            body = b""
            if method != "HEAD" and expected_content is not None:
                body = response.read(max_read_bytes)
            final_url = response.geturl()

        details = {"status": status, "final_url": final_url}
        if status not in expected_status:
            return CheckResult(
                component.component_id,
                CheckStatus.DOWN,
                _latency_ms(started),
                f"Unexpected HTTP status {status}.",
                {**details, "expected_status": sorted(expected_status)},
            )

        if expected_content is not None:
            decoded = body.decode("utf-8", errors="replace")
            if str(expected_content) not in decoded:
                return CheckResult(
                    component.component_id,
                    CheckStatus.DOWN,
                    _latency_ms(started),
                    "Expected content was not found in the response.",
                    details,
                )

        return CheckResult(
            component.component_id,
            CheckStatus.UP,
            _latency_ms(started),
            f"HTTP status {status} matched.",
            details,
        )
    except TimeoutError as exc:
        return CheckResult(
            component.component_id,
            CheckStatus.DOWN,
            _latency_ms(started),
            f"HTTP request timed out: {exc}",
        )
    except urllib.error.URLError as exc:
        return CheckResult(
            component.component_id,
            CheckStatus.DOWN,
            _latency_ms(started),
            f"HTTP request failed: {exc.reason}",
        )
    except OSError as exc:
        return CheckResult(
            component.component_id,
            CheckStatus.ERROR,
            _latency_ms(started),
            f"HTTP check error: {exc}",
        )


CHECKS: dict[str, CheckFunction] = {
    "dns": check_dns,
    "tcp": check_tcp,
    "http": check_http,
}


def run_check(component: Component) -> CheckResult:
    """Dispatch a validated component to its check implementation."""

    try:
        return CHECKS[component.check_type](component)
    except Exception as exc:  # Defensive isolation between worker threads.
        return CheckResult(
            component.component_id,
            CheckStatus.ERROR,
            None,
            f"Unexpected check error: {type(exc).__name__}: {exc}",
        )
