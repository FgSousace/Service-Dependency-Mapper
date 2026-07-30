from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from service_dependency_mapper.config import (
    ConfigError,
    load_config,
    topological_order,
)

VALID_CONFIG = """
version: 1
service:
  name: Test service
  description: Test map
defaults:
  timeout: 2
  workers: 3
components:
  - id: dns
    name: DNS
    check:
      type: dns
      target: example.com
  - id: tcp
    name: TCP
    depends_on: [dns]
    check:
      type: tcp
      host: example.com
      port: 443
  - id: http
    name: HTTP
    depends_on: [tcp]
    check:
      type: http
      url: https://example.com/
      expected_status: [200, 204]
"""


class ConfigTests(unittest.TestCase):
    def load_text(self, content: str, **kwargs):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "service.yaml")
            path.write_text(content, encoding="utf-8")
            return load_config(path, **kwargs)

    def test_loads_valid_configuration(self):
        service_map = self.load_text(VALID_CONFIG)
        self.assertEqual(service_map.service_name, "Test service")
        self.assertEqual(len(service_map.components), 3)
        self.assertEqual(service_map.workers, 3)

    def test_defaults_component_name_to_id(self):
        service_map = self.load_text(VALID_CONFIG.replace("    name: DNS\n", ""))
        self.assertEqual(service_map.components[0].name, "dns")

    def test_applies_runtime_overrides(self):
        service_map = self.load_text(
            VALID_CONFIG, timeout_override=9, workers_override=2
        )
        self.assertEqual(service_map.default_timeout, 9)
        self.assertTrue(all(item.timeout == 9 for item in service_map.components))
        self.assertEqual(service_map.workers, 2)

    def test_component_timeout_overrides_default(self):
        content = VALID_CONFIG.replace(
            "      target: example.com\n",
            "      target: example.com\n      timeout: 7\n",
            1,
        )
        service_map = self.load_text(content)
        self.assertEqual(service_map.components[0].timeout, 7)
        self.assertEqual(service_map.components[1].timeout, 2)

    def test_rejects_unknown_version(self):
        with self.assertRaisesRegex(ConfigError, "version must be 1"):
            self.load_text(VALID_CONFIG.replace("version: 1", "version: 2"))

    def test_rejects_duplicate_component_id(self):
        content = VALID_CONFIG.replace("  - id: tcp", "  - id: dns", 1)
        with self.assertRaisesRegex(ConfigError, "Duplicate component id"):
            self.load_text(content)

    def test_rejects_unknown_dependency(self):
        content = VALID_CONFIG.replace("depends_on: [dns]", "depends_on: [missing]")
        with self.assertRaisesRegex(ConfigError, "unknown dependencies"):
            self.load_text(content)

    def test_rejects_self_dependency(self):
        content = VALID_CONFIG.replace("depends_on: [dns]", "depends_on: [tcp]")
        with self.assertRaisesRegex(ConfigError, "cannot depend on itself"):
            self.load_text(content)

    def test_rejects_dependency_cycle(self):
        content = VALID_CONFIG.replace(
            "    check:\n      type: dns",
            "    depends_on: [http]\n    check:\n      type: dns",
            1,
        )
        with self.assertRaisesRegex(ConfigError, "Dependency cycle detected"):
            self.load_text(content)

    def test_rejects_invalid_tcp_port(self):
        with self.assertRaisesRegex(ConfigError, "port must be an integer"):
            self.load_text(VALID_CONFIG.replace("port: 443", "port: 70000"))

    def test_rejects_invalid_http_url(self):
        with self.assertRaisesRegex(ConfigError, "must use http"):
            self.load_text(
                VALID_CONFIG.replace("https://example.com/", "ftp://example.com/")
            )

    def test_returns_dependency_first_order(self):
        service_map = self.load_text(VALID_CONFIG)
        self.assertEqual(topological_order(service_map), ("dns", "tcp", "http"))

    def test_loads_vendor_neutral_icmp_and_tls_checks(self):
        content = (
            VALID_CONFIG
            + """
  - id: gateway_ping
    check:
      type: icmp
      target: 192.0.2.1
      count: 2
  - id: certificate
    depends_on: [dns]
    check:
      type: tls
      host: example.com
"""
        )
        service_map = self.load_text(content)
        by_id = service_map.components_by_id
        self.assertEqual(by_id["gateway_ping"].check["count"], 2)
        self.assertEqual(by_id["certificate"].check["port"], 443)
        self.assertEqual(by_id["certificate"].check["min_days_remaining"], 14)

    def test_rejects_invalid_icmp_count(self):
        content = VALID_CONFIG.replace(
            "type: dns\n      target: example.com",
            "type: icmp\n      target: 192.0.2.1\n      count: 20",
            1,
        )
        with self.assertRaisesRegex(ConfigError, "count must be an integer"):
            self.load_text(content)


if __name__ == "__main__":
    unittest.main()
