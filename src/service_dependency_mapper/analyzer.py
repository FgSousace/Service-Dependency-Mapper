"""Concurrent check execution and root-cause analysis."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from service_dependency_mapper.checks import run_check
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

Checker = Callable[[Component], CheckResult]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _failed_ancestors(
    component_id: str,
    dependencies: dict[str, tuple[str, ...]],
    failed: set[str],
) -> set[str]:
    found: set[str] = set()
    to_visit = list(dependencies[component_id])
    visited: set[str] = set()
    while to_visit:
        dependency_id = to_visit.pop()
        if dependency_id in visited:
            continue
        visited.add(dependency_id)
        if dependency_id in failed:
            found.add(dependency_id)
        to_visit.extend(dependencies[dependency_id])
    return found


def analyze(
    dependency_map: DependencyMap,
    *,
    checker: Checker = run_check,
) -> AnalysisReport:
    """Run all component checks and identify root causes and impact."""

    started_at = _utc_now()
    started = time.perf_counter()
    raw_results: dict[str, CheckResult] = {}

    with ThreadPoolExecutor(
        max_workers=min(dependency_map.workers, len(dependency_map.components)),
        thread_name_prefix="sdmap",
    ) as executor:
        futures = {
            executor.submit(checker, component): component
            for component in dependency_map.components
        }
        for future in as_completed(futures):
            component = futures[future]
            try:
                raw_results[component.component_id] = future.result()
            except Exception as exc:  # A custom checker must not crash the analysis.
                raw_results[component.component_id] = CheckResult(
                    component.component_id,
                    CheckStatus.ERROR,
                    None,
                    f"Worker error: {type(exc).__name__}: {exc}",
                )

    failed = {
        component_id
        for component_id, result in raw_results.items()
        if result.status is not CheckStatus.UP
    }
    dependencies = {
        component.component_id: component.depends_on
        for component in dependency_map.components
    }

    analyzed: list[AnalyzedResult] = []
    for component in dependency_map.components:
        result = raw_results[component.component_id]
        failed_dependencies = tuple(
            sorted(_failed_ancestors(component.component_id, dependencies, failed))
        )
        if result.status is not CheckStatus.UP and not failed_dependencies:
            diagnosis = DiagnosticStatus.ROOT_CAUSE
        elif result.status is not CheckStatus.UP or failed_dependencies:
            diagnosis = DiagnosticStatus.IMPACTED
        else:
            diagnosis = DiagnosticStatus.HEALTHY
        analyzed.append(
            AnalyzedResult(
                component=component,
                result=result,
                diagnosis=diagnosis,
                failed_dependencies=failed_dependencies,
            )
        )

    unhealthy = [
        item for item in analyzed if item.diagnosis is not DiagnosticStatus.HEALTHY
    ]
    if not unhealthy:
        overall_status = OverallStatus.HEALTHY
    elif any(item.component.critical for item in unhealthy):
        overall_status = OverallStatus.DOWN
    else:
        overall_status = OverallStatus.DEGRADED

    root_causes = tuple(
        item.component.component_id
        for item in analyzed
        if item.diagnosis is DiagnosticStatus.ROOT_CAUSE
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    return AnalysisReport(
        service_name=dependency_map.service_name,
        description=dependency_map.description,
        overall_status=overall_status,
        started_at=started_at,
        completed_at=_utc_now(),
        duration_ms=duration_ms,
        results=tuple(analyzed),
        root_causes=root_causes,
    )
