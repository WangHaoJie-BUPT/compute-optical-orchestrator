"""Test authenticated asynchronous admission queue endpoints."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from orchestrator.api.application import RuntimeAPISettings  # noqa: E402
from orchestrator.api.secure_application import (  # noqa: E402
    create_secure_app,
)


API_KEY = "admission-api-test-key"


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        settings = RuntimeAPISettings(
            database_file=Path(directory) / "runtime.db",
            start_controller=False,
            admission_max_active_total=2,
            admission_max_active_per_tenant=1,
        )
        application = create_secure_app(
            settings=settings,
            api_key=API_KEY,
        )
        payload = {
            "task_id": "queued-api-task-001",
            "tenant_id": "research-team-a",
            "priority": 70,
            "profile_id": "busybox-smoke",
            "source_access_node": "trx Lorient_KMA",
            "cpu_request_m": 500,
            "memory_request_mib": 256,
            "gpu_request": 0,
            "cpu_limit_m": 1000,
            "memory_limit_mib": 512,
            "bandwidth_gbps": 100,
            "duration_seconds": 1,
        }
        headers = {"X-API-Key": API_KEY}

        with TestClient(application) as client:
            unauthorized = client.post("/api/v3/tasks", json=payload)
            if unauthorized.status_code != 401:
                raise AssertionError(unauthorized.text)

            submitted = client.post(
                "/api/v3/tasks",
                headers=headers,
                json=payload,
            )
            if submitted.status_code != 202:
                raise AssertionError(submitted.text)
            body = submitted.json()
            if body["state"] != "QUEUED":
                raise AssertionError(body)
            if body["reused_existing"]:
                raise AssertionError(body)

            duplicate = client.post(
                "/api/v3/tasks",
                headers=headers,
                json=payload,
            )
            if not duplicate.json()["reused_existing"]:
                raise AssertionError(duplicate.text)

            over_limit = client.post(
                "/api/v3/tasks",
                headers=headers,
                json={
                    **payload,
                    "task_id": "queued-api-task-002",
                },
            )
            if over_limit.status_code != 429:
                raise AssertionError(over_limit.text)

            stats = client.get(
                "/api/v3/queue/stats",
                headers=headers,
            )
            if stats.json()["active"] != 1:
                raise AssertionError(stats.text)

            listed = client.get(
                "/api/v3/queue?state=queued",
                headers=headers,
            )
            if listed.json()["count"] != 1:
                raise AssertionError(listed.text)

            cancelled = client.post(
                "/api/v3/queue/queued-api-task-001/cancel",
                headers=headers,
            )
            if cancelled.json()["state"] != "CANCELLED":
                raise AssertionError(cancelled.text)

            final_stats = client.get(
                "/api/v3/queue/stats",
                headers=headers,
            ).json()
            if final_stats["active"] != 0:
                raise AssertionError(final_stats)
            if final_stats["cancelled"] != 1:
                raise AssertionError(final_stats)

    print("Admission queue API test completed successfully.")


if __name__ == "__main__":
    main()
