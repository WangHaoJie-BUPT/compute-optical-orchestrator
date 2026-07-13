"""
Run the GNPy adapter against the custom single-request example.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Allow the script to import the local orchestrator package.
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.gnpy_adapter import (  # noqa: E402
    GNPyAdapter,
    GNPyAdapterError,
)


def find_metric(metrics: dict, *names: str):
    """Return the first matching metric name."""

    for name in names:
        if name in metrics:
            return metrics[name]

    return None


def main() -> None:
    adapter = GNPyAdapter(project_root=PROJECT_ROOT)

    try:
        results = adapter.compute_paths(
            topology_file=(
                "topologies/gnpy_demo/"
                "meshTopologyExampleV2.xls"
            ),
            service_file=(
                "requests/gnpy_demo/"
                "services-single.json"
            ),
            equipment_file=(
                "topologies/gnpy_demo/"
                "eqpt_config.json"
            ),
            output_file=(
                "experiments/results/gnpy_adapter/"
                "single-request-result.json"
            ),
            spectrum_policy="first_fit",
            verbose=False,
        )
    except GNPyAdapterError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1) from exc

    print(f"Number of responses: {len(results)}")

    for result in results:
        gsnr = find_metric(
            result.metrics,
            "SNR-bandwidth",
            "SNR@bandwidth",
        )

        osnr = find_metric(
            result.metrics,
            "OSNR-bandwidth",
            "OSNR@bandwidth",
        )

        path_bandwidth = result.metrics.get("path_bandwidth")

        print("-" * 70)
        print(f"Request ID:       {result.response_id}")
        print(f"Feasible:         {result.feasible}")
        print(f"Route:            {' -> '.join(result.route)}")
        print(f"GSNR bandwidth:   {gsnr}")
        print(f"OSNR bandwidth:   {osnr}")
        print(f"Path bandwidth:   {path_bandwidth}")

    print("-" * 70)
    print("GNPy adapter test completed successfully.")


if __name__ == "__main__":
    main()
