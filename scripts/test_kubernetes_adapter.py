"""Test Python-based Kubernetes Job submission."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.kubernetes_adapter import (  # noqa: E402
    KubernetesAdapter,
    KubernetesAdapterError,
)


TASK_ID = "k8s-adapter-task-001"
SITE_ID = "site-rennes"


def main() -> None:
    adapter = KubernetesAdapter(
        namespace="compute-optical",
        context="kind-compute-optical",
    )

    print("1. Verifying Kubernetes cluster")
    adapter.verify_cluster()
    adapter.ensure_namespace()
    print("[PASS] Cluster is accessible")

    job_name = adapter.job_name_for_task(TASK_ID)

    # Make the test repeatable.
    adapter.delete_job(
        job_name=job_name,
        ignore_not_found=True,
    )

    shell_command = """
set -eu

echo "Task started"
echo "Task ID: ${TASK_ID}"
echo "Pod name: ${POD_NAME}"
echo "Selected Kubernetes node: ${NODE_NAME}"
echo "Expected compute site: ${EXPECTED_SITE_ID}"

sleep 3

echo "Task completed"
""".strip()

    print()
    print("2. Creating Kubernetes Job")

    job_name = adapter.create_job(
        task_id=TASK_ID,
        site_id=SITE_ID,
        image="busybox:1.36",
        image_pull_policy="Never",
        shell_command=shell_command,
        cpu_request="100m",
        memory_request="32Mi",
        cpu_limit="200m",
        memory_limit="64Mi",
    )

    print(f"[CREATED] {job_name}")

    print()
    print("3. Waiting for Job completion")

    result = adapter.wait_for_completion(
        task_id=TASK_ID,
        job_name=job_name,
        site_id=SITE_ID,
        timeout_seconds=120,
    )

    print()
    print("=" * 90)
    print("Kubernetes Job result")
    print("=" * 90)
    print(f"Job name:        {result.job_name}")
    print(f"Task ID:         {result.task_id}")
    print(f"Requested site:  {result.site_id}")
    print(f"Pod name:        {result.pod_name}")
    print(f"Node name:       {result.node_name}")
    print(f"Node site label: {result.node_site_label}")
    print(f"Pod phase:       {result.pod_phase}")
    print(f"Succeeded:       {result.succeeded}")

    print()
    print("Container logs")
    print("-" * 90)
    print(result.logs.rstrip())
    print("-" * 90)

    if not result.succeeded:
        raise AssertionError(
            "Kubernetes Job did not succeed."
        )

    if result.node_site_label != SITE_ID:
        raise AssertionError(
            f"Expected site label {SITE_ID}, "
            f"received {result.node_site_label}."
        )

    if result.node_name != "compute-optical-worker2":
        raise AssertionError(
            "The Job was not scheduled to the expected Rennes node."
        )

    print()
    print(
        "Kubernetes adapter test completed successfully."
    )


if __name__ == "__main__":
    try:
        main()
    except KubernetesAdapterError as exc:
        print(f"[KUBERNETES ERROR] {exc}")
        raise SystemExit(1) from exc
