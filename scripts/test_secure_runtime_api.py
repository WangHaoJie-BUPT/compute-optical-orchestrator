"""Test hardened profile-based API and cancellation."""

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
)
from orchestrator.api.secure_application import (  # noqa: E402
    create_secure_app,
)


DATABASE_FILE = (
    PROJECT_ROOT
    / "runtime"
    / "secure-runtime-api-test.db"
)

TASK_ID = "secure-profile-task-001"
API_KEY = "test-api-key-0123456789abcdef"


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

    job_name = adapter.job_name_for_task(TASK_ID)

    adapter.delete_job(
        job_name=job_name,
        ignore_not_found=True,
    )

    application = create_secure_app(
        settings=RuntimeAPISettings(
            database_file=DATABASE_FILE,
            controller_interval_seconds=0.5,
            controller_lease_seconds=5,
            start_controller=True,
        ),
        api_key=API_KEY,
    )

    headers = {
        "X-API-Key": API_KEY,
    }

    payload = {
        "task_id": TASK_ID,
        "profile_id": "busybox-smoke",
        "source_access_node": "trx Lorient_KMA",
        "cpu_request_m": 500,
        "memory_request_mib": 256,
        "gpu_request": 0,
        "cpu_limit_m": 1000,
        "memory_limit_mib": 512,
        "bandwidth_gbps": 100,
        "duration_seconds": 60
    }

    with TestClient(application) as client:
        print()
        print("=" * 100)
        print("1. Verify Swagger API-key scheme")

        schema = client.get("/openapi.json").json()

        scheme = (
            schema
            .get("components", {})
            .get("securitySchemes", {})
            .get("XAPIKey")
        )

        print("Security scheme:", scheme)

        if scheme is None:
            raise AssertionError(
                "Swagger API-key scheme is missing."
            )

        print()
        print("=" * 100)
        print("2. Verify authentication")

        unauthorized = client.post(
            "/api/v2/tasks",
            json=payload,
        )

        if unauthorized.status_code != 401:
            raise AssertionError(
                unauthorized.text
            )

        print("[PASS] Unauthenticated submission rejected")

        print()
        print("=" * 100)
        print("3. Verify legacy arbitrary submission is disabled")

        legacy = client.post(
            "/api/v1/tasks",
            headers=headers,
            json={
                **payload,
                "image": "arbitrary-image",
                "shell_command": "arbitrary command"
            },
        )

        print("Legacy response:", legacy.status_code, legacy.json())

        if legacy.status_code != 410:
            raise AssertionError(
                legacy.text
            )

        print()
        print("=" * 100)
        print("4. Verify arbitrary fields are rejected")

        unsafe = client.post(
            "/api/v2/tasks",
            headers=headers,
            json={
                **payload,
                "image": "attacker/image:latest",
                "shell_command": "rm -rf /"
            },
        )

        print("Unsafe response:", unsafe.status_code)

        if unsafe.status_code != 422:
            raise AssertionError(
                unsafe.text
            )

        print()
        print("=" * 100)
        print("5. List server-controlled profiles")

        profiles = client.get(
            "/api/v2/task-profiles",
            headers=headers,
        )

        print("Profiles:", profiles.json())

        if profiles.status_code != 200:
            raise AssertionError(
                profiles.text
            )

        if profiles.json()["count"] < 1:
            raise AssertionError(
                "No task profiles were returned."
            )

        print()
        print("=" * 100)
        print("6. Submit safe profile task")

        submission = client.post(
            "/api/v2/tasks",
            headers=headers,
            json=payload,
        )

        print(
            "Submission:",
            submission.status_code,
            submission.json(),
        )

        if submission.status_code != 202:
            raise AssertionError(
                submission.text
            )

        deadline = time.monotonic() + 20

        while time.monotonic() < deadline:
            job_state = adapter.get_job_state(
                job_name
            )

            if job_state in {
                "PENDING",
                "RUNNING",
            }:
                break

            time.sleep(0.5)
        else:
            raise AssertionError(
                "Kubernetes Job did not become active."
            )

        print()
        print("=" * 100)
        print("7. Cancel safe profile task")

        cancellation = client.post(
            f"/api/v2/tasks/{TASK_ID}/cancel",
            headers=headers,
        )

        print(
            "Cancellation:",
            cancellation.status_code,
            cancellation.json(),
        )

        if cancellation.status_code != 200:
            raise AssertionError(
                cancellation.text
            )

        if cancellation.json()["state"] != "CANCELLED":
            raise AssertionError(
                cancellation.json()
            )

        task_response = client.get(
            f"/api/v1/tasks/{TASK_ID}",
            headers=headers,
        )

        task_document = task_response.json()

        if task_document["state"] != "CANCELLED":
            raise AssertionError(
                task_document
            )

        if task_document["optical"] is not None:
            raise AssertionError(
                "Optical allocation was not removed."
            )

        if adapter.get_job_state(job_name) != "MISSING":
            raise AssertionError(
                "Kubernetes Job was not deleted."
            )

        print()
        print(
            "Hardened profile-based API test "
            "completed successfully."
        )


if __name__ == "__main__":
    main()
