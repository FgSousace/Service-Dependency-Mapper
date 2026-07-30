"""Human-readable and machine-readable report rendering."""

from __future__ import annotations

import json
from typing import Any

from service_dependency_mapper.models import (
    AnalysisReport,
    AnalyzedResult,
    DiagnosticStatus,
)

_COLORS = {
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def _color(value: str, color: str, enabled: bool) -> str:
    if not enabled:
        return value
    return f"{_COLORS[color]}{value}{_COLORS['reset']}"


def _display_state(item: AnalyzedResult) -> tuple[str, str]:
    if item.diagnosis is DiagnosticStatus.ROOT_CAUSE:
        return "ROOT CAUSE", "red"
    if item.diagnosis is DiagnosticStatus.IMPACTED:
        return "IMPACTED", "yellow"
    return "HEALTHY", "green"


def _latency(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f} ms"


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def render_table(report: AnalysisReport, *, color: bool = True) -> str:
    """Render a compact terminal table without third-party dependencies."""

    headers = ("COMPONENT", "CHECK", "TARGET", "RESULT", "LATENCY", "DIAGNOSIS")
    rows: list[tuple[str, ...]] = []
    row_colors: list[str] = []
    for item in report.results:
        state, state_color = _display_state(item)
        raw_status = item.result.status.value.upper()
        rows.append(
            (
                _truncate(item.component.name, 24),
                item.component.check_type.upper(),
                _truncate(item.component.target, 34),
                raw_status,
                _latency(item.result.latency_ms),
                state,
            )
        )
        row_colors.append(state_color)

    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def format_row(row: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    overall_color = {
        "healthy": "green",
        "degraded": "yellow",
        "down": "red",
    }[report.overall_status.value]
    lines = [
        _color("Service Dependency Mapper", "cyan", color),
        f"Service: {report.service_name}",
        f"Overall: {_color(report.overall_status.value.upper(), overall_color, color)}",
        "",
        _color(format_row(headers), "bold", color),
        "  ".join("-" * width for width in widths),
    ]
    for row, row_color in zip(rows, row_colors, strict=True):
        lines.append(_color(format_row(row), row_color, color))

    lines.extend(["", f"Completed in {report.duration_ms:.2f} ms"])
    if report.root_causes:
        lines.append(
            "Root cause candidate(s): "
            + _color(", ".join(report.root_causes), "red", color)
        )
    else:
        lines.append("Root cause candidate(s): none")

    unhealthy = [
        item
        for item in report.results
        if item.diagnosis is not DiagnosticStatus.HEALTHY
    ]
    if unhealthy:
        lines.append("")
        lines.append(_color("Diagnosis", "bold", color))
        for item in unhealthy:
            dependency_note = ""
            if item.failed_dependencies:
                dependency_note = (
                    f" | failed dependencies: {', '.join(item.failed_dependencies)}"
                )
            lines.append(
                f"- {item.component.component_id}: "
                f"{item.result.message}{dependency_note}"
            )
    return "\n".join(lines) + "\n"


def report_to_dict(report: AnalysisReport) -> dict[str, Any]:
    """Convert a report to a stable JSON-compatible schema."""

    return {
        "schema_version": 1,
        "service": {
            "name": report.service_name,
            "description": report.description,
            "status": report.overall_status.value,
        },
        "execution": {
            "started_at": report.started_at,
            "completed_at": report.completed_at,
            "duration_ms": report.duration_ms,
        },
        "root_causes": list(report.root_causes),
        "components": [
            {
                "id": item.component.component_id,
                "name": item.component.name,
                "critical": item.component.critical,
                "tags": list(item.component.tags),
                "check": {
                    "type": item.component.check_type,
                    "target": item.component.target,
                    "status": item.result.status.value,
                    "latency_ms": item.result.latency_ms,
                    "message": item.result.message,
                    "details": item.result.details,
                },
                "dependencies": list(item.component.depends_on),
                "diagnosis": item.diagnosis.value,
                "failed_dependencies": list(item.failed_dependencies),
            }
            for item in report.results
        ],
    }


def render_json(report: AnalysisReport) -> str:
    """Render an indented UTF-8 JSON report."""

    return json.dumps(report_to_dict(report), indent=2, ensure_ascii=False) + "\n"
