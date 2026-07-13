"""End-to-end test of the persistent orchestration HTTP API."""

from __future__ import annotations

import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from orchestrator.adapters.kubernetes_adapter import (  # noqa: E402
    KubernetesAdapter,
)
from orchestrator.api.application import (  # noqa: E402
    RuntimeAPISettings,
    create_app,
)


DATABASE_FILE = (
    PROJECT_ROOT
    / "runtime"
    / "runtime-api-test.db"
)

TASK_ID = "runtime-api-task-001"


def remove_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(
            str(DATABASE_FILE) + suffix
        )

        if candidate.exists():
            candidate.unlink()


def main() -> None:
    remove_database()

    adapter = KubernetesAdapter(
        namespace="compute-optical",
        context="kind-compute-optical",
    )

    adapter.verify_cluster()

    adapter.delete_job(
        job_name=adapter.job_name_for_task(
            TASK_ID
        ),
        ignore_not_found=True,
    )

    settings = RuntimeAPISettings(
        database_file=DATABASE_FILE,
        controller_interval_seconds=0.5,
        controller_lease_seconds=5,
        start_controller=True,
    )

    application = create_app(settings)

    payload = {
        "task_id": TASK_ID,
        "source_access_node": "trx Lorient_KMA",
        "cpu_request_m": 500,
        "memory_request_mib": 256,
        "gpu_request": 0,
        "cpu_limit_m": 1000,
        "memory_limit_mib": 512,
        "bandwidth_gbps": 100,
        "image": "busybox:1.36",
        "shell_command": (
            "set -eu\n"
            "echo \"API-managed task started\"\n"
            "echo \"Task ID: ${TASK_ID}\"\n"
            "echo \"Node: ${NODE_NAME}\"\n"
            "echo \"Site: ${EXPECTED_SITE_ID}\"\n"
            "sleep 8\n"
            "echo \"API-managed task completed\""
        ),
    }

    with TestClient(application) as client:
        print()
        print("=" * 100)
        print("1. Health and readiness")

        health = client.get("/healthz")
        print("Health:", health.json())

        if health.status_code != 200:
            raise AssertionError(
                health.text
            )

        readiness = client.get("/readyz")
        print("Readiness:", readiness.json())

        if readiness.status_code != 200:
            raise AssertionError(
                readiness.text
            )

        print()
        print("=" * 100)
        print("2. Submit task")

        response = client.post(
            "/api/v1/tasks",
            json=payload,
        )

        print(
            "Submission:",
            response.status_code,
            response.json(),
        )

        if response.status_code != 202:
            raise AssertionError(
                response.text
            )

        submission = response.json()

        if submission["state"] != "RUNNING":
            raise AssertionError(
                f"Unexpected initial state: "
                f"{submission['state']}"
            )

        print()
        print("=" * 100)
        print("3. Verify idempotent duplicate submission")

        duplicate = client.post(
            "/api/v1/tasks",
            json=payload,
        )

        print(
            "Duplicate:",
            duplicate.status_code,
            duplicate.json(),
        )

        if duplicate.status_code != 202:
            raise AssertionError(
                duplicate.text
            )

        if (
            duplicate.json()["reused_existing"]
            is not True
        ):
            raise AssertionError(
                "Duplicate submission was not reused."
            )

        print()
        print("=" * 100)
        print("4. Poll task until terminal state")

        final_task = None
        deadline = time.monotonic() + 90

        while time.monotonic() < deadline:
            status_response = client.get(
                f"/api/v1/tasks/{TASK_ID}"
            )

            if status_response.status_code != 200:
                raise AssertionError(
                    status_response.text
                )

            final_task = status_response.json()

            print(
                "State:",
                final_task["state"],
                "Execution:",
                (
                    final_task["execution"]["phase"]
                    if final_task["execution"]
                    else None
                ),
            )

            if final_task["state"] in {
                "RELEASED",
                "FAILED",
            }:
                break

            time.sleep(1)

        if final_task is None:
            raise AssertionError(
                "No task response was received."
            )

        if final_task["state"] != "RELEASED":
            raise AssertionError(
                f"Expected RELEASED, received "
                f"{final_task['state']}."
            )

        if final_task["optical"] is not None:
            raise AssertionError(
                "Optical persistence was not removed."
            )

        execution = final_task["execution"]

        if execution is None:
            raise AssertionError(
                "Execution was not persisted."
            )

        if execution["phase"] != "SUCCEEDED":
            raise AssertionError(
                execution
            )

        if (
            execution["requested_site_id"]
            != execution["node_site_label"]
        ):
            raise AssertionError(
                "Requested and actual sites differ."
            )

        print()
        print("=" * 100)
        print("5. Read task logs")

        logs_response = client.get(
            f"/api/v1/tasks/{TASK_ID}/logs"
        )

        print(logs_response.text)

        if logs_response.status_code != 200:
            raise AssertionError(
                logs_response.text
            )

        if (
            "API-managed task completed"
            not in logs_response.text
        ):
            raise AssertionError(
                "Expected task log was not found."
            )

        print()
        print("=" * 100)
        print("6. Read Kubernetes site resources")

        sites_response = client.get(
            "/api/v1/sites"
        )

        sites_document = sites_response.json()

        print(
            "Site count:",
            sites_document["count"],
        )

        if sites_response.status_code != 200:
            raise AssertionError(
                sites_response.text
            )

        if sites_document["count"] != 3:
            raise AssertionError(
                sites_document
            )

        print()
        print(
            "Runtime orchestration API test "
            "completed successfully."
        )


if __name__ == "__main__":
    main()
