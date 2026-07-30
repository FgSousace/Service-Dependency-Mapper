from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from service_dependency_mapper.cli import main
from service_dependency_mapper.graph import render_dot, render_mermaid
from service_dependency_mapper.models import (
    AnalysisReport,
    AnalyzedResult,
    CheckResult,
    CheckStatus,
    Component,
    DependencyMap,
    DiagnosticStatus,
    OverallStatus,
)
from service_dependency_mapper.reporting import (
    render_json,
    render_table,
    report_to_dict,
)


def fixtures():
    dns = Component(
        "dns",
        "DNS",
        "dns",
        {"target": "example.com"},
    )
    api = Component(
        "api",
        "API",
        "http",
        {"url": "https://example.com/"},
        depends_on=("dns",),
    )
    dependency_map = DependencyMap("Demo", "", 2, 2, (dns, api))
    report = AnalysisReport(
        service_name="Demo",
        description="",
        overall_status=OverallStatus.DOWN,
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
        duration_ms=10,
        results=(
            AnalyzedResult(
                dns,
                CheckResult("dns", CheckStatus.DOWN, 1, "DNS failed"),
                DiagnosticStatus.ROOT_CAUSE,
            ),
            AnalyzedResult(
                api,
                CheckResult("api", CheckStatus.UP, 2, "HTTP 200"),
                DiagnosticStatus.IMPACTED,
                ("dns",),
            ),
        ),
        root_causes=("dns",),
    )
    return dependency_map, report


VALID_YAML = """
version: 1
service:
  name: Demo
components:
  - id: dns
    check:
      type: dns
      target: example.com
"""


class ReportingGraphCliTests(unittest.TestCase):
    def test_json_schema_contains_root_cause(self):
        _, report = fixtures()
        payload = report_to_dict(report)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["root_causes"], ["dns"])
        self.assertEqual(payload["components"][1]["diagnosis"], "impacted")

    def test_render_json_has_trailing_newline(self):
        _, report = fixtures()
        output = render_json(report)
        self.assertIn('"status": "down"', output)
        self.assertTrue(output.endswith("\n"))

    def test_table_contains_diagnosis(self):
        _, report = fixtures()
        output = render_table(report, color=False)
        self.assertIn("ROOT CAUSE", output)
        self.assertIn("failed dependencies: dns", output)

    def test_mermaid_contains_dependency_edge(self):
        dependency_map, _ = fixtures()
        output = render_mermaid(dependency_map)
        self.assertIn("dns --> api", output)
        self.assertTrue(output.startswith("flowchart LR"))

    def test_dot_contains_dependency_edge(self):
        dependency_map, _ = fixtures()
        output = render_dot(dependency_map)
        self.assertIn("dns -> api;", output)
        self.assertTrue(output.startswith("digraph"))

    def test_cli_validates_configuration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "demo.yaml")
            path.write_text(VALID_YAML, encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["validate", str(path)])
        self.assertEqual(exit_code, 0)
        self.assertIn("Configuration valid", stdout.getvalue())

    def test_cli_exports_graph(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir, "demo.yaml")
            output = Path(temp_dir, "graph.mmd")
            config.write_text(VALID_YAML, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(
                    ["graph", str(config), "--format", "mermaid", "-o", str(output)]
                )
            graph = output.read_text(encoding="utf-8")
        self.assertEqual(exit_code, 0)
        self.assertIn("flowchart LR", graph)

    def test_cli_returns_two_for_invalid_configuration(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(["validate", "missing.yaml"])
        self.assertEqual(exit_code, 2)
        self.assertIn("Configuration error", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
