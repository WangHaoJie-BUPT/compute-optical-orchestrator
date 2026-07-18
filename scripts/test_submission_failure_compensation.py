"""Test compensation after Job creation but before execution persistence."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.models.runtime_task import (  # noqa: E402
    RuntimeTaskRequest,
)
from orchestrator.models.spectrum import SpectrumSlot  # noqa: E402
from orchestrator.models.task_state import TaskState  # noqa: E402
from orchestrator.repositories.runtime_task_repository import (  # noqa: E402
    RuntimeTaskRepository,
    RuntimeTaskRepositoryError,
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


DATABASE_FILE = (
    PROJECT_ROOT
    / "runtime"
    / "submission-failure-compensation-test.db"
)
TASK_ID = "submission-failure-compensation"


class FailingSaveJobRepository(RuntimeTaskRepository):
    def save_job(
        self,
        task_id: str,
        job_name: str,
        namespace: str,
        requested_site_id: str,
    ) -> Any:
        raise RuntimeTaskRepositoryError(
            "Injected failure after Kubernetes Job creation."
        )


class FakePlanner:
    def __init__(self) -> None:
        self.optical_reserved = False

    def select(self, **_: Any) -> tuple[KubernetesJointPlan, list[Any]]:
        return (
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
            ),
            [],
        )

    def reserve_optical(self, _: KubernetesJointPlan) -> None:
        self.optical_reserved = True

    def release_optical(self, task_id: str) -> None:
        if task_id != TASK_ID or not self.optical_reserved:
            raise AssertionError("Unexpected optical release.")
        self.optical_reserved = False


class FakeKubernetesAdapter:
    namespace = "compute-optical"

    def __init__(self) -> None:
        self.job_created = False
        self.job_deleted = False
        self.created_node_name: str | None = None

    def ensure_namespace(self) -> None:
        return None

    def job_name_for_task(self, task_id: str) -> str:
        return f"co-{task_id}"

    def get_job_state(self, _: str) -> str:
        return "MISSING"

    def create_job(self, **arguments: Any) -> str:
        self.job_created = True
        self.created_node_name = arguments.get("node_name")
        return self.job_name_for_task(str(arguments["task_id"]))

    def delete_job(
        self,
        job_name: str,
        ignore_not_found: bool = True,
    ) -> None:
        if job_name != self.job_name_for_task(TASK_ID):
            raise AssertionError("Unexpected Job deletion.")
        self.job_deleted = True


def remove_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(DATABASE_FILE) + suffix)
        if candidate.exists():
            candidate.unlink()


class SubmissionFailureCompensationTest(unittest.TestCase):
    def test_job_and_optical_resources_are_compensated(self) -> None:
        remove_database()
        database = Database(DATABASE_FILE)
        database.initialize()

        repository = FailingSaveJobRepository(database)
        repository.initialize()
        event_repository = TaskEventRepository(database)
        event_repository.initialize()
        planner = FakePlanner()
        adapter = FakeKubernetesAdapter()

        service = PersistentKubernetesRuntimeService(
            repository=repository,
            event_repository=event_repository,
            planner=planner,  # type: ignore[arg-type]
            kubernetes_adapter=adapter,  # type: ignore[arg-type]
        )
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

        with self.assertRaises(PersistentKubernetesRuntimeServiceError):
            service.submit(
                task=task,
                topology_file="unused.xls",
                equipment_file="unused.json",
            )

        record = repository.get_task(TASK_ID)
        self.assertIsNotNone(record)
        self.assertEqual(record.state, TaskState.FAILED)
        self.assertIsNone(repository.get_optical(TASK_ID))
        self.assertTrue(adapter.job_created)
        self.assertTrue(adapter.job_deleted)
        self.assertFalse(planner.optical_reserved)
        self.assertEqual(
            adapter.created_node_name,
            "compute-optical-worker2",
        )


if __name__ == "__main__":
    unittest.main()
