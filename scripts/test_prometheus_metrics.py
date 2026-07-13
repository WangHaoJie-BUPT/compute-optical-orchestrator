"""Test Prometheus metrics for the orchestration runtime."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from prometheus_client.parser import (  # noqa: E402
    text_string_to_metric_families,
)

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
    / "prometheus-metrics-test.db"
)

TASK_ID = "metrics-task-001"
API_KEY = "metrics-test-api-key-0123456789abcdef"


def remove_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(
            str(DATABASE_FILE) + suffix
        )

        if candidate.exists():
            candidate.unlink()


def parse_samples(
    text: str,
) -> list[Any]:
    samples: list[Any] = []

    for family in text_string_to_metric_families(
        text
    ):
        samples.extend(family.samples)

    return samples


def find_sample(
    samples: list[Any],
    name: str,
    required_labels: dict[str, str] | None = None,
) -> Any | None:
    labels = required_labels or {}

    for sample in samples:
        if sample.name != name:
            continue

        if all(
            sample.labels.get(key) == value
            for key, value in labels.items()
        ):
            return sample

    return None


def require_value(
    samples: list[Any],
    name: str,
    required_labels: dict[str, str] | None = None,
) -> float:
    sample = find_sample(
        samples,
        name,
        required_labels,
    )

    if sample is None:
        raise AssertionError(
            f"Metric sample not found: "
            f"{name}, labels={required_labels}"
        )

    return float(sample.value)


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
        "duration_seconds": 4,
    }

    with TestClient(application) as client:
        print()
        print("=" * 100)
        print("1. Verify metrics endpoint is available")

        metrics_response = client.get(
            "/metrics/"
        )

        print(
            "Metrics status:",
            metrics_response.status_code,
        )

        if metrics_response.status_code != 200:
            raise AssertionError(
                metrics_response.text
            )

        initial_samples = parse_samples(
            metrics_response.text
        )

        collector_up = require_value(
            initial_samples,
            "compute_optical_metrics_collector_up",
        )

        discovery_up = require_value(
            initial_samples,
            (
                "compute_optical_"
                "kubernetes_resource_discovery_up"
            ),
        )

        if collector_up != 1:
            raise AssertionError(
                "Prometheus collector is not healthy."
            )

        if discovery_up != 1:
            raise AssertionError(
                "Kubernetes resource discovery failed."
            )

        site_ready_samples = [
            sample
            for sample in initial_samples
            if sample.name
            == "compute_optical_site_ready"
        ]

        print(
            "Discovered site metrics:",
            len(site_ready_samples),
        )

        if len(site_ready_samples) != 3:
            raise AssertionError(
                "Expected metrics for three compute sites."
            )

        print()
        print("=" * 100)
        print("2. Submit task and inspect active metrics")

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

        active_metrics = client.get(
            "/metrics/"
        )

        if active_metrics.status_code != 200:
            raise AssertionError(
                active_metrics.text
            )

        active_samples = parse_samples(
            active_metrics.text
        )

        running_count = require_value(
            active_samples,
            "compute_optical_tasks",
            {
                "state": "RUNNING",
            },
        )

        optical_count = require_value(
            active_samples,
            (
                "compute_optical_"
                "active_optical_allocations"
            ),
        )

        occupied_spectrum = require_value(
            active_samples,
            (
                "compute_optical_"
                "occupied_spectrum_ghz"
            ),
        )

        print("RUNNING tasks:", running_count)
        print("Optical allocations:", optical_count)
        print("Occupied spectrum GHz:", occupied_spectrum)

        if running_count < 1:
            raise AssertionError(
                "RUNNING task metric was not updated."
            )

        if optical_count < 1:
            raise AssertionError(
                "Active optical allocation metric is zero."
            )

        if occupied_spectrum <= 0:
            raise AssertionError(
                "Occupied spectrum metric is invalid."
            )

        print()
        print("=" * 100)
        print("3. Wait for controller finalization")

        deadline = time.monotonic() + 90
        final_document = None

        while time.monotonic() < deadline:
            response = client.get(
                f"/api/v1/tasks/{TASK_ID}",
                headers=headers,
            )

            if response.status_code != 200:
                raise AssertionError(
                    response.text
                )

            final_document = response.json()

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
                "Task status was not returned."
            )

        if final_document["state"] != "RELEASED":
            raise AssertionError(
                final_document
            )

        print()
        print("=" * 100)
        print("4. Verify terminal and released metrics")

        final_metrics = client.get(
            "/metrics/"
        )

        if final_metrics.status_code != 200:
            raise AssertionError(
                final_metrics.text
            )

        final_samples = parse_samples(
            final_metrics.text
        )

        released_count = require_value(
            final_samples,
            "compute_optical_tasks",
            {
                "state": "RELEASED",
            },
        )

        final_optical_count = require_value(
            final_samples,
            (
                "compute_optical_"
                "active_optical_allocations"
            ),
        )

        succeeded_execution_count = require_value(
            final_samples,
            (
                "compute_optical_"
                "kubernetes_executions"
            ),
            {
                "phase": "SUCCEEDED",
            },
        )

        transition_event_count = require_value(
            final_samples,
            "compute_optical_audit_events",
            {
                "event_type": "STATE_TRANSITION",
            },
        )

        duration_count = require_value(
            final_samples,
            (
                "compute_optical_"
                "task_duration_seconds_count"
            ),
            {
                "outcome": "RELEASED",
            },
        )

        controller_up = require_value(
            final_samples,
            "compute_optical_controller_up",
        )

        print("RELEASED tasks:", released_count)
        print(
            "Active optical allocations:",
            final_optical_count,
        )
        print(
            "SUCCEEDED executions:",
            succeeded_execution_count,
        )
        print(
            "STATE_TRANSITION events:",
            transition_event_count,
        )
        print(
            "Completed duration observations:",
            duration_count,
        )
        print("Controller up:", controller_up)

        if released_count != 1:
            raise AssertionError(
                "RELEASED task metric is incorrect."
            )

        if final_optical_count != 0:
            raise AssertionError(
                "Optical allocation metric was not released."
            )

        if succeeded_execution_count != 1:
            raise AssertionError(
                "SUCCEEDED execution metric is incorrect."
            )

        if transition_event_count < 7:
            raise AssertionError(
                "State-transition event metric is incomplete."
            )

        if duration_count != 1:
            raise AssertionError(
                "Task-duration metric is incorrect."
            )

        if controller_up != 1:
            raise AssertionError(
                "Controller thread is not reported as up."
            )

        print()
        print("=" * 100)
        print("5. Verify HTTP request metrics")

        # This second scrape includes HTTP requests completed
        # before the scrape began.
        http_metrics = client.get(
            "/metrics/"
        )

        http_samples = parse_samples(
            http_metrics.text
        )

        task_get_count = require_value(
            http_samples,
            (
                "compute_optical_"
                "http_requests_total"
            ),
            {
                "method": "GET",
                "route": "/api/v1/tasks/{task_id}",
                "status_code": "200",
            },
        )

        print(
            "Task status GET requests:",
            task_get_count,
        )

        if task_get_count < 1:
            raise AssertionError(
                "HTTP request counter was not updated."
            )

        print()
        print(
            "Prometheus metrics test completed successfully."
        )


if __name__ == "__main__":
    main()
