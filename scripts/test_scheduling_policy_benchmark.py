"""Test deterministic scheduling-policy benchmark invariants."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.evaluation.policy_benchmark import (  # noqa: E402
    BENCHMARK_SCENARIOS,
    run_benchmark,
)


class SchedulingPolicyBenchmarkTest(unittest.TestCase):
    def test_results_are_deterministic_and_well_formed(self) -> None:
        first = run_benchmark(task_count=100, seeds=(7, 17))
        second = run_benchmark(task_count=100, seeds=(7, 17))

        self.assertEqual(first, second)
        self.assertEqual(
            {item.policy for item in first},
            {
                "joint_weighted",
                "compute_first",
                "optical_first",
                "random_feasible",
            },
        )

        for result in first:
            self.assertEqual(result.scenario, "balanced")
            self.assertEqual(
                result.accepted
                + result.compute_blocked
                + result.optical_blocked,
                result.offered,
            )
            self.assertGreaterEqual(result.acceptance_rate, 0.0)
            self.assertLessEqual(result.acceptance_rate, 1.0)
            self.assertGreaterEqual(result.mean_cpu_utilization, 0.0)
            self.assertLessEqual(result.mean_cpu_utilization, 1.0)

    def test_stress_scenarios_are_executable(self) -> None:
        for scenario_name in ("compute_stress", "spectrum_stress"):
            results = run_benchmark(
                task_count=50,
                seeds=(7,),
                scenario=BENCHMARK_SCENARIOS[scenario_name],
            )
            self.assertEqual(
                {item.scenario for item in results},
                {scenario_name},
            )


if __name__ == "__main__":
    unittest.main()
