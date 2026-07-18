"""Test atomic persistent spectrum claims under concurrent plans."""

from __future__ import annotations

import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


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
from orchestrator.services.kubernetes_joint_planner import (  # noqa: E402
    KubernetesJointPlan,
)
from orchestrator.storage.database import Database  # noqa: E402


DATABASE_FILE = PROJECT_ROOT / "runtime" / "spectrum-claims-test.db"
TASK_IDS = (
    "spectrum-claim-a",
    "spectrum-claim-b",
    "spectrum-claim-disjoint",
)
SLOT = SpectrumSlot(n=-20, m=4)
SHARED_LINKS = ("Lorient-Vannes", "Vannes-Rennes")


def remove_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(DATABASE_FILE) + suffix)
        if candidate.exists():
            candidate.unlink()


def task(task_id: str) -> RuntimeTaskRequest:
    return RuntimeTaskRequest(
        task_id=task_id,
        source_access_node="trx Lorient_KMA",
        cpu_request_m=500,
        memory_request_mib=256,
        gpu_request=0,
        bandwidth_gbps=100,
        image="busybox:1.36",
        shell_command="echo safe",
    )


def plan(
    task_id: str,
    links: tuple[str, ...],
) -> KubernetesJointPlan:
    return KubernetesJointPlan(
        task_id=task_id,
        selected_site_id="site-rennes",
        selected_node_name="compute-optical-worker2",
        network_access_node="trx Rennes_STA",
        spectrum_slot=SLOT,
        fiber_links=links,
        route=("trx Lorient_KMA", "trx Rennes_STA"),
        gsnr_db=21.73,
        total_score=85.0,
    )


def claim_count(database: Database) -> int:
    with database.connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM runtime_spectrum_claims"
        ).fetchone()
    return int(row["count"])


class PersistentSpectrumClaimsTest(unittest.TestCase):
    def test_concurrent_conflict_release_and_restart(self) -> None:
        remove_database()
        database = Database(DATABASE_FILE)
        database.initialize()
        repository = RuntimeTaskRepository(database)
        repository.initialize()

        for task_id in TASK_IDS:
            repository.create_task(task(task_id))
            repository.set_state(task_id, TaskState.PLANNING)

        barrier = threading.Barrier(2)

        def commit(task_id: str) -> tuple[str, str]:
            barrier.wait(timeout=5)
            try:
                repository.save_reserved_plan(
                    plan(task_id, SHARED_LINKS)
                )
                return task_id, "committed"
            except RuntimeTaskRepositoryError as exc:
                return task_id, str(exc)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = dict(
                executor.map(commit, TASK_IDS[:2])
            )

        committed = [
            task_id
            for task_id, result in results.items()
            if result == "committed"
        ]
        rejected = [
            task_id
            for task_id, result in results.items()
            if "Persistent spectrum conflict" in result
        ]

        self.assertEqual(len(committed), 1)
        self.assertEqual(len(rejected), 1)

        winner = committed[0]
        loser = rejected[0]
        winner_record = repository.get_task(winner)
        loser_record = repository.get_task(loser)

        self.assertEqual(winner_record.state, TaskState.RESERVED)
        self.assertEqual(loser_record.state, TaskState.PLANNING)
        self.assertIsNotNone(repository.get_optical(winner))
        self.assertIsNone(repository.get_optical(loser))
        self.assertEqual(claim_count(database), len(SHARED_LINKS) * 8)

        repository.finish_release(
            task_id=winner,
            final_state=TaskState.FAILED,
            failure_reason="test release",
        )
        self.assertEqual(claim_count(database), 0)

        repository.save_reserved_plan(plan(loser, SHARED_LINKS))
        repository.save_reserved_plan(
            plan(TASK_IDS[2], ("Lorient-Brest",))
        )
        self.assertEqual(
            claim_count(database),
            (len(SHARED_LINKS) + 1) * 8,
        )

        # Initialization is also the restart-time migration/validation pass.
        restarted = RuntimeTaskRepository(Database(DATABASE_FILE))
        restarted.initialize()
        self.assertIsNotNone(restarted.get_optical(loser))
        self.assertIsNotNone(restarted.get_optical(TASK_IDS[2]))


if __name__ == "__main__":
    unittest.main()
