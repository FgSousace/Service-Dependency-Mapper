from __future__ import annotations

import unittest

from service_dependency_mapper.analyzer import analyze
from service_dependency_mapper.models import (
    CheckResult,
    CheckStatus,
    Component,
    DependencyMap,
    DiagnosticStatus,
    OverallStatus,
)


def component(
    component_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    critical: bool = True,
) -> Component:
    return Component(
        component_id=component_id,
        name=component_id.upper(),
        check_type="dns",
        check={"target": f"{component_id}.example"},
        depends_on=depends_on,
        critical=critical,
    )


def service_map(*components: Component) -> DependencyMap:
    return DependencyMap(
        service_name="Test",
        description="",
        default_timeout=1,
        workers=4,
        components=components,
    )


def checker_with(statuses: dict[str, CheckStatus]):
    def checker(item: Component) -> CheckResult:
        status = statuses.get(item.component_id, CheckStatus.UP)
        return CheckResult(item.component_id, status, 1.5, status.value)

    return checker


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.map = service_map(
            component("dns"),
            component("tcp", depends_on=("dns",)),
            component("api", depends_on=("tcp",)),
        )

    def test_all_healthy(self):
        report = analyze(self.map, checker=checker_with({}))
        self.assertEqual(report.overall_status, OverallStatus.HEALTHY)
        self.assertEqual(report.root_causes, ())
        self.assertTrue(
            all(item.diagnosis is DiagnosticStatus.HEALTHY for item in report.results)
        )

    def test_first_failure_is_root_cause(self):
        report = analyze(self.map, checker=checker_with({"dns": CheckStatus.DOWN}))
        by_id = {item.component.component_id: item for item in report.results}
        self.assertEqual(report.root_causes, ("dns",))
        self.assertEqual(by_id["dns"].diagnosis, DiagnosticStatus.ROOT_CAUSE)
        self.assertEqual(by_id["tcp"].diagnosis, DiagnosticStatus.IMPACTED)
        self.assertEqual(by_id["api"].diagnosis, DiagnosticStatus.IMPACTED)

    def test_failed_downstream_is_a_symptom(self):
        report = analyze(
            self.map,
            checker=checker_with(
                {
                    "dns": CheckStatus.DOWN,
                    "tcp": CheckStatus.DOWN,
                    "api": CheckStatus.DOWN,
                }
            ),
        )
        by_id = {item.component.component_id: item for item in report.results}
        self.assertEqual(by_id["tcp"].diagnosis, DiagnosticStatus.IMPACTED)
        self.assertEqual(by_id["api"].failed_dependencies, ("dns", "tcp"))

    def test_independent_failures_are_multiple_root_causes(self):
        mapped = service_map(component("dns"), component("database"))
        report = analyze(
            mapped,
            checker=checker_with(
                {"dns": CheckStatus.DOWN, "database": CheckStatus.ERROR}
            ),
        )
        self.assertEqual(report.root_causes, ("dns", "database"))

    def test_noncritical_failure_degrades_service(self):
        mapped = service_map(component("metrics", critical=False))
        report = analyze(mapped, checker=checker_with({"metrics": CheckStatus.DOWN}))
        self.assertEqual(report.overall_status, OverallStatus.DEGRADED)

    def test_critical_impact_marks_service_down(self):
        report = analyze(self.map, checker=checker_with({"dns": CheckStatus.DOWN}))
        self.assertEqual(report.overall_status, OverallStatus.DOWN)

    def test_checker_exception_is_isolated(self):
        def broken_checker(item: Component) -> CheckResult:
            if item.component_id == "tcp":
                raise RuntimeError("boom")
            return CheckResult(item.component_id, CheckStatus.UP, 1, "ok")

        report = analyze(self.map, checker=broken_checker)
        by_id = {item.component.component_id: item for item in report.results}
        self.assertEqual(by_id["tcp"].result.status, CheckStatus.ERROR)
        self.assertIn("Worker error", by_id["tcp"].result.message)


if __name__ == "__main__":
    unittest.main()
