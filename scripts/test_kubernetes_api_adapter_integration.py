"""Run one real kind Job through the official Kubernetes client."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.kubernetes_api_adapter import (  # noqa: E402
    KubernetesApiAdapter,
)


TASK_ID = "official-api-adapter-task-001"
SITE_ID = "site-rennes"
NODE_NAME = "compute-optical-worker2"


def main() -> None:
    adapter = KubernetesApiAdapter(
        namespace="compute-optical",
        context="kind-compute-optical",
    )
    adapter.verify_cluster()
    adapter.ensure_namespace()
    job_name = adapter.job_name_for_task(TASK_ID)
    adapter.delete_job(job_name, ignore_not_found=True)

    try:
        created_name = adapter.create_job(
            task_id=TASK_ID,
            site_id=SITE_ID,
            node_name=NODE_NAME,
            image="busybox:1.36",
            image_pull_policy="Never",
            shell_command=(
                "echo task=${TASK_ID}; "
                "echo pod=${POD_NAME}; "
                "echo node=${NODE_NAME}; "
                "echo site=${EXPECTED_SITE_ID}"
            ),
        )
        if created_name != job_name:
            raise AssertionError("Created Job name is not deterministic.")

        result = adapter.wait_for_completion(
            task_id=TASK_ID,
            job_name=job_name,
            site_id=SITE_ID,
            timeout_seconds=120,
        )
        if not result.succeeded:
            raise AssertionError("Official-client Job failed.")
        if result.node_name != NODE_NAME:
            raise AssertionError(
                f"Expected node {NODE_NAME}, received {result.node_name}."
            )
        if result.node_site_label != SITE_ID:
            raise AssertionError(
                f"Expected site {SITE_ID}, "
                f"received {result.node_site_label}."
            )
        if f"node={NODE_NAME}" not in result.logs:
            raise AssertionError("Expected node is absent from Pod logs.")
        print(
            "Official Kubernetes API integration test passed: "
            f"job={job_name}, pod={result.pod_name}, "
            f"node={result.node_name}."
        )
    finally:
        adapter.delete_job(job_name, ignore_not_found=True)


if __name__ == "__main__":
    main()
