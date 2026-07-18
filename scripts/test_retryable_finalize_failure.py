"""Test retry semantics when Kubernetes status is temporarily unavailable."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.kubernetes_adapter import (  # noqa: E402
    KubernetesAdapterError,
)
from orchestrator.models.runtime_task import (  # noqa: E402
    RuntimeTaskRequest,
)
from orchestrator.models.spectrum import SpectrumSlot  # noqa: E402
from orchestrator.models.task_state import TaskState  # noqa: E402
from orchestrator.repositories.runtime_task_repository import (  # noqa: E402
    RuntimeTaskRepository,
)
from orchestrator.repositories.task_event_repository import (  # noqa: E402
    TaskEventRepository,
)
from orchestrator.services.kubernetes_joint_planner import (  # noqa: E402
    KubernetesJointPlan,
)
from orchestrator.services.persistent_kubernetes_runtime_service import (  # noqa: E402
    PersistentKubernetesRuntimeService,
    PersistentKubernetesRuntimeServiceError,
)
from orchestrator.storage.database import Database  # noqa: E402


DATABASE_FILE = PROJECT_ROOT / "runtime" / "retryable-finalize-test.db"
TASK_ID = "retryable-finalize-task"


class TrackingPlanner:
    def __init__(self) -> None:
        self.optical_reserved = True

    def release_optical(self, _: str) -> None:
        self.optical_reserved = False


class UnavailableKubernetesAdapter:
    namespace = "compute-optical"

    def wait_for_completion(self, **_: Any) -> None:
        raise KubernetesAdapterError("Injected Kubernetes API timeout.")


def remove_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(DATABASE_FILE) + suffix)
        if candidate.exists():
            candidate.unlink()


class RetryableFinalizeFailureTest(unittest.TestCase):
    def test_running_state_and_optical_plan_are_preserved(self) -> None:
        remove_database()
        database = Database(DATABASE_FILE)
        database.initialize()
        repository = RuntimeTaskRepository(database)
        repository.initialize()
        events = TaskEventRepository(database)
        events.initialize()

        task = RuntimeTaskRequest(
            task_id=TASK_ID,
            source_access_node="trx Lorient_KMA",
            cpu_request_m=500,
            memory_request_mib=256,
            gpu_request=0,
            bandwidth_gbps=100,
            image="busybox:1.36",
            shell_command="echo safe",
        )
        repository.create_task(task)
        repository.set_state(TASK_ID, TaskState.PLANNING)
        repository.save_reserved_plan(
            KubernetesJointPlan(
                task_id=TASK_ID,
                selected_site_id="site-rennes",
                selected_node_name="compute-optical-worker2",
                network_access_node="trx Rennes_STA",
                spectrum_slot=SpectrumSlot(n=-20, m=4),
                fiber_links=("Lorient-Vannes",),
                route=("trx Lorient_KMA", "trx Rennes_STA"),
                gsnr_db=21.73,
                total_score=85.0,
            )
        )
        repository.set_state(TASK_ID, TaskState.SUBMITTING)
        running = repository.save_job(
            task_id=TASK_ID,
            job_name="co-retryable-finalize-task",
            namespace="compute-optical",
            requested_site_id="site-rennes",
        )

        planner = TrackingPlanner()
        service = PersistentKubernetesRuntimeService(
            repository=repository,
            event_repository=events,
            planner=planner,  # type: ignore[arg-type]
            kubernetes_adapter=(
                UnavailableKubernetesAdapter()  # type: ignore[arg-type]
            ),
        )

        with self.assertRaisesRegex(
            PersistentKubernetesRuntimeServiceError,
            "reconciliation will retry",
        ):
            service.finalize(TASK_ID)

        current = repository.get_task(TASK_ID)
        self.assertIsNotNone(current)
        self.assertEqual(current.state, TaskState.RUNNING)
        self.assertEqual(current.version, running.version)
        self.assertIsNotNone(repository.get_optical(TASK_ID))
        self.assertTrue(planner.optical_reserved)


if __name__ == "__main__":
    unittest.main()
