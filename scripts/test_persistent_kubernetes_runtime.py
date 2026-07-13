"""Test persistent Kubernetes-native orchestration and recovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.gnpy_adapter import GNPyAdapter  # noqa: E402
from orchestrator.adapters.gnpy_request_builder import (  # noqa: E402
    GNPyRequestBuilder,
)
from orchestrator.adapters.kubernetes_adapter import (  # noqa: E402
    KubernetesAdapter,
)
from orchestrator.adapters.kubernetes_resource_discovery import (  # noqa: E402
    KubernetesResourceDiscovery,
)
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
    KubernetesJointPlanner,
)
from orchestrator.services.persistent_kubernetes_runtime_service import (  # noqa: E402
    PersistentKubernetesRuntimeService,
)
from orchestrator.state.optical_resource_manager import (  # noqa: E402
    OpticalResourceManager,
)
from orchestrator.storage.database import Database  # noqa: E402


DATABASE_FILE = (
    PROJECT_ROOT
    / "runtime"
    / "persistent-kubernetes-runtime-test.db"
)

TASK_FILE = (
    PROJECT_ROOT
    / "workloads"
    / "kubernetes-task-001.json"
)


def remove_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(
            str(DATABASE_FILE) + suffix
        )

        if candidate.exists():
            candidate.unlink()


def load_task() -> RuntimeTaskRequest:
    with TASK_FILE.open("r", encoding="utf-8") as file:
        document = json.load(file)

    # Use a separate ID and a longer command for recovery testing.
    document["task_id"] = "persistent-runtime-task-001"

    document["shell_command"] = """
set -eu

echo "Persistent runtime task started"
echo "Task ID: ${TASK_ID}"
echo "Pod: ${POD_NAME}"
echo "Node: ${NODE_NAME}"
echo "Site: ${EXPECTED_SITE_ID}"

sleep 15

echo "Persistent runtime task completed"
""".strip()

    return RuntimeTaskRequest.from_dict(document)


def build_service(
    database: Database,
    repository: RuntimeTaskRepository,
    kubernetes_adapter: KubernetesAdapter,
) -> tuple[
    PersistentKubernetesRuntimeService,
    OpticalResourceManager,
]:
    discovery = KubernetesResourceDiscovery(
        kubernetes_adapter=kubernetes_adapter,
        site_mapping_file=(
            PROJECT_ROOT
            / "configs"
            / "site_mapping.json"
        ),
    )

    gnpy_adapter = GNPyAdapter(
        project_root=PROJECT_ROOT,
    )

    request_builder = GNPyRequestBuilder(
        PROJECT_ROOT
        / "requests"
        / "gnpy_demo"
        / "services-single.json"
    )

    optical_manager = OpticalResourceManager(
        spectrum_lower_index=-288,
        spectrum_upper_index=480,
    )

    planner = KubernetesJointPlanner(
        project_root=PROJECT_ROOT,
        resource_discovery=discovery,
        gnpy_adapter=gnpy_adapter,
        request_builder=request_builder,
        optical_manager=optical_manager,
    )

    event_repository = TaskEventRepository(database)
    event_repository.initialize()

    service = PersistentKubernetesRuntimeService(
        repository=repository,
        event_repository=event_repository,
        planner=planner,
        kubernetes_adapter=kubernetes_adapter,
    )

    return service, optical_manager


def main() -> None:
    remove_database()

    task = load_task()

    kubernetes_adapter = KubernetesAdapter(
        namespace="compute-optical",
        context="kind-compute-optical",
    )

    kubernetes_adapter.verify_cluster()
    kubernetes_adapter.ensure_namespace()

    job_name = (
        kubernetes_adapter.job_name_for_task(
            task.task_id
        )
    )

    kubernetes_adapter.delete_job(
        job_name=job_name,
        ignore_not_found=True,
    )

    database_1 = Database(DATABASE_FILE)
    database_1.initialize()

    repository_1 = RuntimeTaskRepository(database_1)
    repository_1.initialize()

    service_1, optical_manager_1 = build_service(
        database=database_1,
        repository=repository_1,
        kubernetes_adapter=kubernetes_adapter,
    )

    print()
    print("=" * 100)
    print("1. Submitting persistent runtime task")

    submission = service_1.submit(
        task=task,
        topology_file=(
            "topologies/gnpy_demo/"
            "meshTopologyExampleV2.xls"
        ),
        equipment_file=(
            "topologies/gnpy_demo/"
            "eqpt_config.json"
        ),
    )

    print(
        f"Task state:      "
        f"{submission.task_record.state.value}"
    )
    print(
        f"Selected site:   "
        f"{submission.task_record.selected_site_id}"
    )
    print(
        f"Selected node:   "
        f"{submission.task_record.selected_node_name}"
    )
    print(
        f"Reused existing: "
        f"{submission.reused_existing}"
    )

    if submission.task_record.state != TaskState.RUNNING:
        raise AssertionError(
            "Task did not enter RUNNING state."
        )

    if len(optical_manager_1.active_allocations()) != 1:
        raise AssertionError(
            "The first service did not reserve spectrum."
        )

    duplicate = service_1.submit(
        task=task,
        topology_file=(
            "topologies/gnpy_demo/"
            "meshTopologyExampleV2.xls"
        ),
        equipment_file=(
            "topologies/gnpy_demo/"
            "eqpt_config.json"
        ),
    )

    if not duplicate.reused_existing:
        raise AssertionError(
            "Duplicate submission was not detected."
        )

    print("[PASS] Duplicate submission reused existing task")

    print()
    print("=" * 100)
    print("2. Simulating orchestrator restart")

    database_2 = Database(DATABASE_FILE)
    database_2.initialize()

    repository_2 = RuntimeTaskRepository(database_2)
    repository_2.initialize()

    service_2, optical_manager_2 = build_service(
        database=database_2,
        repository=repository_2,
        kubernetes_adapter=kubernetes_adapter,
    )

    restored_count = service_2.recover()

    print(
        f"Restored optical allocations: "
        f"{restored_count}"
    )

    if restored_count != 1:
        raise AssertionError(
            "Expected one restored optical allocation."
        )

    if len(optical_manager_2.active_allocations()) != 1:
        raise AssertionError(
            "Spectrum was not restored after restart."
        )

    recovered_task = repository_2.get_task(
        task.task_id
    )

    if recovered_task is None:
        raise AssertionError(
            "Recovered task does not exist."
        )

    print(
        f"Recovered state:  "
        f"{recovered_task.state.value}"
    )

    print()
    print("=" * 100)
    print("3. Waiting for the original Kubernetes Job")

    finalization = service_2.finalize(
        task_id=task.task_id,
        timeout_seconds=120,
    )

    print(
        f"Final state:      "
        f"{finalization.task_record.state.value}"
    )
    print(
        f"Job succeeded:    "
        f"{finalization.job_result.succeeded}"
    )
    print(
        f"Requested site:   "
        f"{finalization.execution_record.requested_site_id}"
    )
    print(
        f"Actual site:      "
        f"{finalization.execution_record.node_site_label}"
    )
    print(
        f"Node:             "
        f"{finalization.execution_record.node_name}"
    )

    print()
    print("Container logs")
    print("-" * 100)
    print(finalization.job_result.logs.rstrip())
    print("-" * 100)

    if (
        finalization.task_record.state
        != TaskState.RELEASED
    ):
        raise AssertionError(
            "Task did not reach RELEASED."
        )

    if not finalization.job_result.succeeded:
        raise AssertionError(
            "Kubernetes Job did not succeed."
        )

    if (
        finalization.execution_record.node_site_label
        != finalization.execution_record
        .requested_site_id
    ):
        raise AssertionError(
            "The actual site differs from the selected site."
        )

    if optical_manager_2.active_allocations():
        raise AssertionError(
            "Optical resources were not released."
        )

    if repository_2.get_optical(task.task_id) is not None:
        raise AssertionError(
            "Persisted optical allocation was not deleted."
        )

    print()
    print(
        "Persistent Kubernetes runtime recovery "
        "test completed successfully."
    )


if __name__ == "__main__":
    main()
