"""Test immutable task lifecycle audit events."""

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
    / "task-audit-events-test.db"
)

TASK_ID = "audit-task-001"
API_KEY = "audit-test-api-key-0123456789abcdef"


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
        job_name=adapter.job_name_for_task(TASK_ID),
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
        "bandwidth_gbps": 100,
        "duration_seconds": 3,
    }

    with TestClient(application) as client:
        print()
        print("=" * 100)
        print("1. Submit audited task")

        response = client.post(
            "/api/v2/tasks",
            headers=headers,
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

        print()
        print("=" * 100)
        print("2. Wait for terminal state")

        deadline = time.monotonic() + 90
        final_document = None

        while time.monotonic() < deadline:
            status_response = client.get(
                f"/api/v1/tasks/{TASK_ID}",
                headers=headers,
            )

            if status_response.status_code != 200:
                raise AssertionError(
                    status_response.text
                )

            final_document = status_response.json()

            print(
                "State:",
                final_document["state"],
            )

            if final_document["state"] in {
                "RELEASED",
                "FAILED",
                "CANCELLED",
            }:
                break

            time.sleep(1)

        if final_document is None:
            raise AssertionError(
                "No task response was returned."
            )

        if final_document["state"] != "RELEASED":
            raise AssertionError(
                final_document
            )

        print()
        print("=" * 100)
        print("3. Read immutable event history")

        events_response = client.get(
            f"/api/v2/tasks/{TASK_ID}/events",
            headers=headers,
        )

        if events_response.status_code != 200:
            raise AssertionError(
                events_response.text
            )

        document = events_response.json()
        events = document["events"]

        for event in events:
            print(
                f"{event['event_id']:03d} "
                f"{event['event_type']:<40} "
                f"{event['previous_state']} "
                f"-> {event['new_state']}"
            )

        if not events:
            raise AssertionError(
                "No audit events were returned."
            )

        event_ids = [
            event["event_id"]
            for event in events
        ]

        if event_ids != sorted(event_ids):
            raise AssertionError(
                "Events are not ordered by event_id."
            )

        event_types = {
            event["event_type"]
            for event in events
        }

        required_event_types = {
            "TASK_CREATED",
            "STATE_TRANSITION",
            "OPTICAL_ALLOCATION_CREATED",
            "KUBERNETES_EXECUTION_CREATED",
            "KUBERNETES_EXECUTION_PHASE_CHANGED",
            "OPTICAL_ALLOCATION_RELEASED",
        }

        missing = (
            required_event_types
            - event_types
        )

        if missing:
            raise AssertionError(
                f"Missing event types: {sorted(missing)}"
            )

        transitions = [
            (
                event["previous_state"],
                event["new_state"],
            )
            for event in events
            if event["event_type"]
            == "STATE_TRANSITION"
        ]

        required_transitions = {
            ("PENDING", "PLANNING"),
            ("PLANNING", "RESERVED"),
            ("RESERVED", "SUBMITTING"),
            ("SUBMITTING", "RUNNING"),
            ("RUNNING", "COMPLETED"),
            ("COMPLETED", "RELEASING"),
            ("RELEASING", "RELEASED"),
        }

        missing_transitions = (
            required_transitions
            - set(transitions)
        )

        if missing_transitions:
            raise AssertionError(
                "Missing state transitions: "
                f"{sorted(missing_transitions)}"
            )

        print()
        print("=" * 100)
        print("4. Verify incremental event query")

        midpoint = event_ids[len(event_ids) // 2]

        incremental_response = client.get(
            f"/api/v2/tasks/{TASK_ID}/events",
            headers=headers,
            params={
                "after_event_id": midpoint,
                "limit": 100,
            },
        )

        if incremental_response.status_code != 200:
            raise AssertionError(
                incremental_response.text
            )

        incremental_events = (
            incremental_response.json()["events"]
        )

        if any(
            event["event_id"] <= midpoint
            for event in incremental_events
        ):
            raise AssertionError(
                "Incremental query returned an old event."
            )

        print(
            f"Events after {midpoint}: "
            f"{len(incremental_events)}"
        )

        print()
        print(
            "Task audit-event test completed successfully."
        )


if __name__ == "__main__":
    main()
