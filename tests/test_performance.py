from __future__ import annotations

import unittest
from unittest.mock import patch

import service_dependency_mapper.performance as performance
from service_dependency_mapper.performance import (
    MAX_WORKERS,
    automatic_worker_count,
    build_discovery_worker_plan,
    logical_processor_count,
    resolve_worker_count,
)


class PerformanceTests(unittest.TestCase):
    def test_counts_processors_available_to_the_process(self):
        with patch.object(
            performance.os,
            "sched_getaffinity",
            return_value={0, 1, 2, 3},
            create=True,
        ):
            self.assertEqual(logical_processor_count(), 4)

    def test_discovery_auto_scales_for_network_io(self):
        self.assertEqual(
            automatic_worker_count("discovery", logical_processors=4),
            64,
        )
        self.assertEqual(
            automatic_worker_count("discovery", logical_processors=16),
            256,
        )
        self.assertEqual(
            automatic_worker_count("discovery", logical_processors=64),
            MAX_WORKERS,
        )

    def test_analysis_auto_uses_four_workers_per_logical_processor(self):
        self.assertEqual(
            automatic_worker_count("analysis", logical_processors=16),
            64,
        )

    def test_builds_stage_specific_discovery_limits(self):
        plan = build_discovery_worker_plan(
            200,
            logical_processors=16,
        )
        self.assertEqual(plan.requested_workers, 200)
        self.assertEqual(plan.icmp_workers, 64)
        self.assertEqual(plan.tcp_workers, 200)
        self.assertEqual(plan.resolver_workers, 32)
        self.assertEqual(plan.fingerprint_workers, 64)

    def test_explicit_low_limit_is_respected_by_every_stage(self):
        plan = build_discovery_worker_plan(3, logical_processors=16)
        self.assertEqual(plan.icmp_workers, 3)
        self.assertEqual(plan.tcp_workers, 3)
        self.assertEqual(plan.resolver_workers, 3)
        self.assertEqual(plan.fingerprint_workers, 3)

    @patch(
        "service_dependency_mapper.performance.logical_processor_count",
        return_value=8,
    )
    def test_resolves_auto_for_selected_workload(self, _processor_count):
        self.assertEqual(
            resolve_worker_count("auto", workload="discovery"),
            128,
        )
        self.assertEqual(
            resolve_worker_count("AUTO", workload="analysis"),
            32,
        )

    def test_rejects_invalid_worker_values(self):
        for value in (True, 0, 257, "fast", "1.5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolve_worker_count(value, workload="discovery")

    def test_rejects_invalid_processor_count(self):
        with self.assertRaises(ValueError):
            automatic_worker_count("discovery", logical_processors=0)


if __name__ == "__main__":
    unittest.main()
