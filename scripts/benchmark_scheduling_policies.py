"""Run and persist the deterministic scheduling-policy benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.evaluation.policy_benchmark import (  # noqa: E402
    BENCHMARK_SCENARIOS,
    run_benchmark,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=int, default=1000)
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=[7, 17, 29, 43, 61],
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=sorted(BENCHMARK_SCENARIOS),
        default=sorted(BENCHMARK_SCENARIOS),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            PROJECT_ROOT
            / "experiments"
            / "results"
            / "benchmarks"
            / "scheduling-policies.json"
        ),
    )
    arguments = parser.parse_args()

    results = [
        result
        for scenario_name in arguments.scenarios
        for result in run_benchmark(
            task_count=arguments.tasks,
            seeds=tuple(arguments.seeds),
            scenario=BENCHMARK_SCENARIOS[scenario_name],
        )
    ]
    document = {
        "benchmark_type": "deterministic_synthetic_workload",
        "task_count_per_seed": arguments.tasks,
        "seeds": arguments.seeds,
        "scenarios": {
            name: {
                "cpu_capacity_scale": (
                    BENCHMARK_SCENARIOS[name].cpu_capacity_scale
                ),
                "memory_capacity_scale": (
                    BENCHMARK_SCENARIOS[name].memory_capacity_scale
                ),
                "spectrum_lower_index": (
                    BENCHMARK_SCENARIOS[name].spectrum_lower_index
                ),
                "spectrum_upper_index": (
                    BENCHMARK_SCENARIOS[name].spectrum_upper_index
                ),
                "holding_time_scale": (
                    BENCHMARK_SCENARIOS[name].holding_time_scale
                ),
            }
            for name in arguments.scenarios
        },
        "results": [item.to_dict() for item in results],
    }

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(document, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(
        "Scenario         Policy              Acceptance  Compute block  Optical block  "
        "CPU util  Spectrum util  Free runs"
    )
    print("-" * 126)

    for item in results:
        print(
            f"{item.scenario:<17}"
            f"{item.policy:<20}"
            f"{item.acceptance_rate:>10.3f}  "
            f"{item.compute_blocked:>13}  "
            f"{item.optical_blocked:>13}  "
            f"{item.mean_cpu_utilization:>8.3f}  "
            f"{item.mean_spectrum_utilization:>13.3f}  "
            f"{item.mean_free_spectrum_runs:>9.3f}"
        )

    print(f"\nSaved synthetic benchmark to {arguments.output}")


if __name__ == "__main__":
    main()
