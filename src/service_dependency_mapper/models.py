"""Core data models used by the dependency mapper."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckStatus(str, Enum):
    """Raw result returned by a health check."""

    UP = "up"
    DOWN = "down"
    ERROR = "error"


class DiagnosticStatus(str, Enum):
    """Meaning of a component result in the dependency graph."""

    HEALTHY = "healthy"
    ROOT_CAUSE = "root_cause"
    IMPACTED = "impacted"


class OverallStatus(str, Enum):
    """Aggregated state of the monitored service."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class Component:
    """A single checkable component and its declared dependencies."""

    component_id: str
    name: str
    check_type: str
    check: dict[str, Any]
    depends_on: tuple[str, ...] = ()
    critical: bool = True
    timeout: float = 3.0
    tags: tuple[str, ...] = ()

    @property
    def target(self) -> str:
        """Return a compact target suitable for terminal reports."""

        if self.check_type == "dns":
            return str(self.check["target"])
        if self.check_type == "tcp":
            return f"{self.check['host']}:{self.check['port']}"
        return str(self.check["url"])


@dataclass(frozen=True, slots=True)
class DependencyMap:
    """Validated service definition loaded from YAML."""

    service_name: str
    description: str
    default_timeout: float
    workers: int
    components: tuple[Component, ...]

    @property
    def components_by_id(self) -> dict[str, Component]:
        """Return components indexed by their stable identifier."""

        return {component.component_id: component for component in self.components}


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Raw result of one DNS, TCP, or HTTP check."""

    component_id: str
    status: CheckStatus
    latency_ms: float | None
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AnalyzedResult:
    """Check result enriched with dependency-graph diagnosis."""

    component: Component
    result: CheckResult
    diagnosis: DiagnosticStatus
    failed_dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """Complete result returned to reporters and the CLI."""

    service_name: str
    description: str
    overall_status: OverallStatus
    started_at: str
    completed_at: str
    duration_ms: float
    results: tuple[AnalyzedResult, ...]
    root_causes: tuple[str, ...]
