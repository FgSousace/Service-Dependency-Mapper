"""Adaptive concurrency settings for network-heavy workloads."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

MAX_WORKERS = 256
Workload = Literal["analysis", "discovery"]


def logical_processor_count() -> int:
    """Return the logical processors available to this process."""

    get_affinity = getattr(os, "sched_getaffinity", None)
    if get_affinity is not None:
        try:
            affinity_count = len(get_affinity(0))
        except OSError:
            pass
        else:
            if affinity_count > 0:
                return affinity_count
    return max(1, os.cpu_count() or 1)


def automatic_worker_count(
    workload: Workload,
    *,
    logical_processors: int | None = None,
) -> int:
    """Choose bounded concurrency for I/O-heavy discovery or active checks."""

    processors = (
        logical_processor_count() if logical_processors is None else logical_processors
    )
    if processors < 1:
        raise ValueError("Logical processor count must be at least one.")
    if workload == "discovery":
        return min(MAX_WORKERS, max(32, processors * 16))
    if workload == "analysis":
        return min(MAX_WORKERS, max(8, processors * 4))
    raise ValueError(f"Unknown workload: {workload}.")


def resolve_worker_count(
    value: object,
    *,
    workload: Workload,
    location: str = "Workers",
) -> int:
    """Resolve ``auto`` or an explicit worker count into a safe integer."""

    if isinstance(value, str) and value.strip().lower() == "auto":
        return automatic_worker_count(workload)
    if isinstance(value, bool):
        raise ValueError(f"{location} must be 'auto' or an integer from 1 to 256.")
    try:
        workers = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{location} must be 'auto' or an integer from 1 to 256."
        ) from exc
    if str(workers) != str(value).strip() or not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"{location} must be 'auto' or an integer from 1 to 256.")
    return workers


@dataclass(frozen=True, slots=True)
class DiscoveryWorkerPlan:
    """Per-stage worker limits for infrastructure discovery."""

    logical_processors: int
    requested_workers: int
    icmp_workers: int
    tcp_workers: int
    resolver_workers: int
    fingerprint_workers: int


def build_discovery_worker_plan(
    requested_workers: int | None = None,
    *,
    logical_processors: int | None = None,
) -> DiscoveryWorkerPlan:
    """Build a balanced plan that scales network I/O across available CPUs."""

    processors = (
        logical_processor_count() if logical_processors is None else logical_processors
    )
    if processors < 1:
        raise ValueError("Logical processor count must be at least one.")
    workers = (
        automatic_worker_count("discovery", logical_processors=processors)
        if requested_workers is None
        else resolve_worker_count(
            requested_workers,
            workload="discovery",
            location="Discovery workers",
        )
    )
    return DiscoveryWorkerPlan(
        logical_processors=processors,
        requested_workers=workers,
        icmp_workers=min(workers, max(8, processors * 4)),
        tcp_workers=workers,
        resolver_workers=min(workers, 64, max(4, processors * 2)),
        fingerprint_workers=min(workers, 96, max(8, processors * 4)),
    )
