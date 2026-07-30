from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from service_dependency_mapper.cli import main as cli_main
from service_dependency_mapper.config import load_config
from service_dependency_mapper.gui import (
    DEFAULT_TEMPLATE,
    parse_timeout,
    parse_workers,
    report_summary,
    result_row,
)
from service_dependency_mapper.models import (
    AnalysisReport,
    AnalyzedResult,
    CheckResult,
    CheckStatus,
    Component,
    DiagnosticStatus,
    OverallStatus,
)


def analyzed_result() -> AnalyzedResult:
    component = Component(
        component_id="checkout_dns",
        name="Checkout DNS",
        check_type="dns",
        check={"target": "checkout.example.com"},
    )
    return AnalyzedResult(
        component=component,
        result=CheckResult(
            component_id="checkout_dns",
            status=CheckStatus.DOWN,
            latency_ms=12.345,
            message="DNS failed",
        ),
        diagnosis=DiagnosticStatus.ROOT_CAUSE,
    )


class GuiHelperTests(unittest.TestCase):
    def test_blank_timeout_uses_configuration_default(self):
        self.assertIsNone(parse_timeout("  "))

    def test_parses_positive_timeout(self):
        self.assertEqual(parse_timeout("2.5"), 2.5)

    def test_rejects_invalid_timeout(self):
        for value in ("zero", "0", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_timeout(value)

    def test_blank_workers_uses_configuration_default(self):
        self.assertIsNone(parse_workers(""))

    def test_parses_worker_count(self):
        self.assertEqual(parse_workers("12"), 12)

    def test_rejects_invalid_worker_count(self):
        for value in ("1.5", "0", "65"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_workers(value)

    def test_builds_treeview_row(self):
        row = result_row(analyzed_result())
        self.assertEqual(row[0], "Checkout DNS")
        self.assertEqual(row[3], "DOWN")
        self.assertEqual(row[4], "12.35 ms")
        self.assertEqual(row[5], "ROOT CAUSE")

    def test_builds_summary_cards(self):
        item = analyzed_result()
        report = AnalysisReport(
            service_name="Checkout",
            description="",
            overall_status=OverallStatus.DOWN,
            started_at="2026-07-30T00:00:00+00:00",
            completed_at="2026-07-30T00:00:01+00:00",
            duration_ms=15.4,
            results=(item,),
            root_causes=("checkout_dns",),
        )
        self.assertEqual(
            report_summary(report),
            {
                "overall": "DOWN",
                "root_causes": "checkout_dns",
                "components": "1",
                "duration": "15.40 ms",
            },
        )

    @patch("service_dependency_mapper.gui.launch_gui", return_value=0)
    def test_cli_gui_command_launches_desktop_interface(self, launch_gui):
        exit_code = cli_main(["gui", "examples/healthy-demo.yaml"])
        self.assertEqual(exit_code, 0)
        launch_gui.assert_called_once_with(Path("examples/healthy-demo.yaml"))

    def test_gui_template_is_a_valid_service_map(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "template.yaml")
            path.write_text(DEFAULT_TEMPLATE, encoding="utf-8")
            dependency_map = load_config(path)
        self.assertEqual(dependency_map.service_name, "My service")
        self.assertEqual(len(dependency_map.components), 5)


if __name__ == "__main__":
    unittest.main()
