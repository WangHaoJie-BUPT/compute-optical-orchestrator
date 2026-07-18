"""Test durable fair admission, backpressure and recovery."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.controllers.admission_controller import (  # noqa: E402
    AdmissionController,
)
from orchestrator.models.admission import AdmissionState  # noqa: E402
from orchestrator.models.runtime_task import (  # noqa: E402
    RuntimeTaskRequest,
)
from orchestrator.models.task_state import TaskState  # noqa: E402
from orchestrator.repositories.admission_queue_repository import (  # noqa: E402
    AdmissionQueueRepository,
    AdmissionQueueRepositoryError,
)
from orchestrator.repositories.controller_lease_repository import (  # noqa: E402
    ControllerLeaseRepository,
)
from orchestrator.services.kubernetes_joint_planner import (  # noqa: E402
    KubernetesJointPlannerError,
)
from orchestrator.storage.database import Database  # noqa: E402


def task(task_id: str) -> RuntimeTaskRequest:
    return RuntimeTaskRequest(
        task_id=task_id,
        source_access_node="trx Lorient_KMA",
        cpu_request_m=500,
        memory_request_mib=256,
        gpu_request=0,
        bandwidth_gbps=100,
        image="busybox:1.36",
        shell_command="echo test",
    )


class FakeRuntimeRepository:
    def __init__(self) -> None:
        self.records = {}

    def get_task(self, task_id):
        return self.records.get(task_id)


class FakePlanner:
    def __init__(self, feasible: bool = True) -> None:
        self.feasible = feasible
        self.calls = 0

    def select(self, **_arguments):
        self.calls += 1
        if not self.feasible:
            raise KubernetesJointPlannerError("No capacity.")
        return object(), []


class FakeRuntimeService:
    def __init__(self, planner: FakePlanner) -> None:
        self.planner = planner
        self.submitted = []

    def submit(self, task, **_arguments):
        self.submitted.append(task.task_id)
        return SimpleNamespace(
            task_record=SimpleNamespace(state=TaskState.RUNNING)
        )


class AdmissionQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(
            Path(self.temporary_directory.name) / "runtime.db"
        )
        self.database.initialize()
        self.repository = AdmissionQueueRepository(
            self.database,
            aging_seconds=10,
        )
        self.assertEqual(self.repository.initialize(), 0)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def enqueue(
        self,
        task_id: str,
        tenant_id: str,
        priority: int,
        per_tenant: int = 10,
    ):
        return self.repository.enqueue(
            task=task(task_id),
            tenant_id=tenant_id,
            priority=priority,
            max_active_total=20,
            max_active_per_tenant=per_tenant,
        )

    def test_fair_tenant_rotation_and_intra_tenant_priority(self) -> None:
        self.enqueue("tenant-a-low", "tenant-a", 10)
        self.enqueue("tenant-a-high", "tenant-a", 90)
        self.enqueue("tenant-b-mid", "tenant-b", 50)

        first = self.repository.claim_next("worker")
        self.assertEqual(first.task.task_id, "tenant-a-high")
        self.repository.mark_admitted(first.task.task_id, "RUNNING")

        second = self.repository.claim_next("worker")
        self.assertEqual(second.task.task_id, "tenant-b-mid")
        self.repository.mark_admitted(second.task.task_id, "RUNNING")

        third = self.repository.claim_next("worker")
        self.assertEqual(third.task.task_id, "tenant-a-low")

    def test_idempotency_backpressure_cancel_and_stats(self) -> None:
        record, reused = self.enqueue(
            "queue-idempotent",
            "tenant-a",
            50,
            per_tenant=1,
        )
        self.assertFalse(reused)
        duplicate, reused = self.enqueue(
            "queue-idempotent",
            "tenant-a",
            50,
            per_tenant=1,
        )
        self.assertTrue(reused)
        self.assertEqual(duplicate, record)

        with self.assertRaises(AdmissionQueueRepositoryError):
            self.enqueue(
                "queue-over-limit",
                "tenant-a",
                50,
                per_tenant=1,
            )

        cancelled = self.repository.cancel("queue-idempotent")
        self.assertEqual(cancelled.state, AdmissionState.CANCELLED)
        stats = self.repository.stats()
        self.assertEqual(stats.active, 0)
        self.assertEqual(stats.cancelled, 1)

    def test_priority_aging_prevents_intra_tenant_starvation(self) -> None:
        self.enqueue("old-low-priority", "tenant-a", 0)
        self.enqueue("new-high-priority", "tenant-a", 100)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE runtime_admission_queue SET enqueued_at = ? "
                "WHERE task_id = ?",
                (time.time() - 2000, "old-low-priority"),
            )
        claimed = self.repository.claim_next("worker")
        self.assertEqual(claimed.task.task_id, "old-low-priority")

    def test_interrupted_dispatch_is_recovered(self) -> None:
        self.enqueue("recovered-dispatch", "tenant-a", 50)
        claimed = self.repository.claim_next("dead-worker")
        self.assertEqual(claimed.state, AdmissionState.DISPATCHING)

        still_live = AdmissionQueueRepository(self.database)
        self.assertEqual(still_live.initialize(), 0)
        self.assertEqual(
            still_live.get("recovered-dispatch").state,
            AdmissionState.DISPATCHING,
        )

        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE runtime_admission_queue "
                "SET claim_expires_at = 0 WHERE task_id = ?",
                ("recovered-dispatch",),
            )

        self.assertEqual(still_live.recover_stale_dispatches(), 1)
        recovered = still_live.get("recovered-dispatch")
        self.assertEqual(recovered.state, AdmissionState.QUEUED)
        self.assertIsNone(recovered.claimed_by)
        self.assertIn("Recovered interrupted", recovered.last_error)

    def test_controller_admits_feasible_task(self) -> None:
        self.enqueue("admit-feasible", "tenant-a", 50)
        planner = FakePlanner(feasible=True)
        service = FakeRuntimeService(planner)
        controller = self._controller(service)
        result = controller.run_once()

        self.assertEqual(result.admitted_count, 1)
        self.assertEqual(service.submitted, ["admit-feasible"])
        record = self.repository.get("admit-feasible")
        self.assertEqual(record.state, AdmissionState.ADMITTED)
        self.assertEqual(record.runtime_state, "RUNNING")

    def test_controller_requeues_capacity_shortage(self) -> None:
        self.enqueue("wait-capacity", "tenant-a", 50)
        planner = FakePlanner(feasible=False)
        service = FakeRuntimeService(planner)
        controller = self._controller(service)
        result = controller.run_once()

        self.assertEqual(result.requeued_count, 1)
        self.assertEqual(service.submitted, [])
        record = self.repository.get("wait-capacity")
        self.assertEqual(record.state, AdmissionState.QUEUED)
        self.assertEqual(record.attempt_count, 1)
        self.assertIn("No capacity", record.last_error)
        self.assertIsNone(self.repository.claim_next("too-early"))

    def _controller(self, service):
        leases = ControllerLeaseRepository(self.database)
        leases.initialize()
        return AdmissionController(
            queue_repository=self.repository,
            runtime_repository=FakeRuntimeRepository(),  # type: ignore[arg-type]
            runtime_service=service,  # type: ignore[arg-type]
            lease_repository=leases,
            topology_file="topology.xls",
            equipment_file="equipment.json",
            controller_id="admission-test",
            base_backoff_seconds=60,
        )


if __name__ == "__main__":
    unittest.main()
