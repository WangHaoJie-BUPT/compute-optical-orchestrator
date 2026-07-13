"""Test structured audit details for blocked joint planning."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.models.runtime_task import (  # noqa: E402
    RuntimeTaskRequest,
)
from orchestrator.models.task_state import TaskState  # noqa: E402
from orchestrator.repositories.runtime_task_repository import (  # noqa: E402
    RuntimeTaskRepository,
)
from orchestrator.repositories.task_event_repository import (  # noqa: E402
    TaskEventRepository,
)
from orchestrator.services.kubernetes_joint_planner import (  # noqa: E402
    KubernetesCandidatePlan,
    KubernetesJointPlannerError,
)
from orchestrator.services.persistent_kubernetes_runtime_service import (  # noqa: E402
    PersistentKubernetesRuntimeService,
    PersistentKubernetesRuntimeServiceError,
)
from orchestrator.storage.database import Database  # noqa: E402


DATABASE_FILE = (
    PROJECT_ROOT
    / "runtime"
    / "blocked-candidate-audit-test.db"
)

TASK_ID = "blocked-candidate-audit-task"
FAILURE_REASON = (
    f"No feasible Kubernetes and optical plan for task {TASK_ID}."
)

CANDIDATE = KubernetesCandidatePlan(
    site_id="site-rennes",
    site_name="Rennes compute site",
    node_name="compute-optical-worker",
    network_access_node="trx Rennes_KMA",
    feasible=False,
    reason=(
        "Insufficient live Kubernetes resources: "
        "available CPU=250m, memory=128.00Mi, GPU=0"
    ),
    available_cpu_m=250,
    available_memory_mib=128.0,
    available_gpu=0,
    spectrum_slot=None,
    fiber_links=(),
    route=(),
    gsnr_db=None,
    compute_score=None,
    optical_score=None,
    total_score=None,
)


class BlockingPlanner:
    """Planner stub that exposes one rejected candidate."""

    def select(
        self,
        task: RuntimeTaskRequest,
        topology_file: str | Path,
        equipment_file: str | Path,
    ) -> Any:
        raise KubernetesJointPlannerError(
            FAILURE_REASON,
            candidate_plans=[CANDIDATE],
        )


def remove_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(DATABASE_FILE) + suffix)

        if candidate.exists():
            candidate.unlink()


def main() -> None:
    remove_database()

    database = Database(DATABASE_FILE)
    database.initialize()

    repository = RuntimeTaskRepository(database)
    repository.initialize()

    event_repository = TaskEventRepository(database)
    event_repository.initialize()

    service = PersistentKubernetesRuntimeService(
        repository=repository,
        event_repository=event_repository,
        planner=BlockingPlanner(),  # type: ignore[arg-type]
        kubernetes_adapter=object(),  # type: ignore[arg-type]
    )

    task = RuntimeTaskRequest(
        task_id=TASK_ID,
        source_access_node="trx Lorient_KMA",
        cpu_request_m=1000,
        memory_request_mib=512,
        gpu_request=0,
        bandwidth_gbps=100,
        image="busybox:1.36",
        shell_command="echo unused",
    )

    try:
        service.submit(
            task=task,
            topology_file="unused-topology.xls",
            equipment_file="unused-equipment.json",
        )
    except PersistentKubernetesRuntimeServiceError as exc:
        if FAILURE_REASON not in str(exc):
            raise AssertionError(
                "Blocked submission lost its planning reason."
            ) from exc
    else:
        raise AssertionError(
            "Blocking planner did not fail submission."
        )

    record = repository.get_task(TASK_ID)

    if record is None or record.state != TaskState.BLOCKED:
        raise AssertionError(
            "Blocked submission was not persisted as BLOCKED."
        )

    blocked_events = [
        event
        for event in event_repository.list_for_task(TASK_ID)
        if event.event_type == "JOINT_PLANNING_BLOCKED"
    ]

    if len(blocked_events) != 1:
        raise AssertionError(
            "Expected exactly one JOINT_PLANNING_BLOCKED event."
        )

    event = blocked_events[0]
    details = event.details

    if details.get("failure_reason") != FAILURE_REASON:
        raise AssertionError(
            "Audit event contains the wrong failure reason."
        )

    candidate_plans = details.get("candidate_plans")

    if not isinstance(candidate_plans, list):
        raise AssertionError(
            "Audit event candidate_plans is not a list."
        )

    if len(candidate_plans) != 1:
        raise AssertionError(
            "Audit event contains the wrong candidate count."
        )

    candidate = candidate_plans[0]

    if candidate.get("site_id") != CANDIDATE.site_id:
        raise AssertionError(
            "Audit event contains the wrong candidate site."
        )

    if candidate.get("reason") != CANDIDATE.reason:
        raise AssertionError(
            "Audit event contains the wrong candidate reason."
        )

    if event.previous_state != TaskState.PLANNING:
        raise AssertionError(
            "Audit event has the wrong previous state."
        )

    if event.new_state != TaskState.BLOCKED:
        raise AssertionError(
            "Audit event has the wrong new state."
        )

    print(
        "Blocked candidate audit test completed successfully."
    )


if __name__ == "__main__":
    main()
