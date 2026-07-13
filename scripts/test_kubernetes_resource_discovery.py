"""Test Kubernetes compute-site resource discovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.kubernetes_adapter import (  # noqa: E402
    KubernetesAdapter,
)
from orchestrator.adapters.kubernetes_resource_discovery import (  # noqa: E402
    KubernetesResourceDiscovery,
)


OUTPUT_FILE = (
    PROJECT_ROOT
    / "experiments"
    / "results"
    / "kubernetes_resources"
    / "site-resource-snapshot.json"
)


def main() -> None:
    adapter = KubernetesAdapter(
        namespace="compute-optical",
        context="kind-compute-optical",
    )

    adapter.verify_cluster()

    discovery = KubernetesResourceDiscovery(
        kubernetes_adapter=adapter,
        site_mapping_file=(
            PROJECT_ROOT
            / "configs"
            / "site_mapping.json"
        ),
    )

    snapshots = discovery.discover()

    print()
    print("Kubernetes compute-site resource snapshot")
    print("=" * 112)

    print(
        f"{'Site':<16}"
        f"{'Node':<30}"
        f"{'Ready':<8}"
        f"{'CPU alloc':<12}"
        f"{'CPU req':<12}"
        f"{'CPU free':<12}"
        f"{'Mem free':<12}"
        f"{'Pods':<8}"
    )

    print("-" * 112)

    for site in snapshots:
        print(
            f"{site.site_id:<16}"
            f"{site.node_name:<30}"
            f"{str(site.ready):<8}"
            f"{site.allocatable_cpu_cores:<12.2f}"
            f"{site.requested_cpu_cores:<12.2f}"
            f"{site.available_cpu_cores:<12.2f}"
            f"{site.available_memory_gib:<12.2f}"
            f"{site.active_pod_count:<8}"
        )

    print("-" * 112)

    expected_mapping = {
        "site-vannes": "compute-optical-worker",
        "site-rennes": "compute-optical-worker2",
        "site-brest": "compute-optical-worker3",
    }

    actual_mapping = {
        site.site_id: site.node_name
        for site in snapshots
    }

    if actual_mapping != expected_mapping:
        raise AssertionError(
            "Unexpected site-to-node mapping.\n"
            f"Expected: {expected_mapping}\n"
            f"Actual:   {actual_mapping}"
        )

    if len(snapshots) != 3:
        raise AssertionError(
            f"Expected three compute sites, received {len(snapshots)}."
        )

    for site in snapshots:
        if not site.ready:
            raise AssertionError(
                f"Node is not Ready: {site.node_name}"
            )

        if not site.schedulable:
            raise AssertionError(
                f"Node is not schedulable: {site.node_name}"
            )

        if site.available_cpu_m < 0:
            raise AssertionError(
                f"Negative available CPU at {site.site_id}."
            )

        if site.available_memory_bytes < 0:
            raise AssertionError(
                f"Negative available memory at {site.site_id}."
            )

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "sites": [
                    site.to_dict()
                    for site in snapshots
                ]
            },
            file,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print(f"Saved snapshot to: {OUTPUT_FILE}")
    print(
        "Kubernetes resource-discovery test completed successfully."
    )


if __name__ == "__main__":
    main()
