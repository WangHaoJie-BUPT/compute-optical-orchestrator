"""Test automatic runtime reconciliation after restart."""

from __future__ import annotations

import json
import sys
import time
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
from orchestrator.controllers.runtime_reconciliation_controller import (  # noqa: E402
    RuntimeReconciliationController,
)
from orchestrator.models.runtime_task import RuntimeTaskRequest  # noqa: E402
from orchestrator.models.task_state import TaskState  # noqa: E402
from orchestrator.repositories.controller_lease_repository import (  # noqa: E402
    ControllerLeaseRepository,
)
from orchestrator.repositories.runtime_task_repository import (  # noqa: E402
    RuntimeTaskRepository,
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
    / "runtime-controller-test.db"
)


def remove_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(DATABASE_FILE) + suffix)

        if candidate.exists():
            candidate.unlink()


def load_task() -> RuntimeTaskRequest:
    path = (
        PROJECT_ROOT
        / "workloads"
        / "kubernetes-task-001.json"
    )

    with path.open("r", encoding="utf-8") as file:
        document = json.load(file)

    document["task_id"] = "controller-task-001"

    document["shell_command"] = """
set -eu

echo "Controller-managed task started"
echo "Task ID: ${TASK_ID}"
echo "Node: ${NODE_NAME}"
echo "Site: ${EXPECTED_SITE_ID}"

sleep 10

echo "Controller-managed task completed"
""".strip()

    return RuntimeTaskRequest.from_dict(document)


def build_runtime(
    database: Database,
    repository: RuntimeTaskRepository,
    adapter: KubernetesAdapter,
) -> tuple[
    PersistentKubernetesRuntimeService,
    OpticalResourceManager,
]:
    discovery = KubernetesResourceDiscovery(
        kubernetes_adapter=adapter,
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

    service = PersistentKubernetesRuntimeService(
        repository=repository,
        planner=planner,
        kubernetes_adapter=adapter,
    )

    return service, optical_manager


def main() -> None:
    remove_database()
    task = load_task()

    adapter = KubernetesAdapter(
        namespace="compute-optical",
        context="kind-compute-optical",
    )

    adapter.verify_cluster()
    adapter.ensure_namespace()

    job_name = adapter.job_name_for_task(task.task_id)

    adapter.delete_job(
        job_name=job_name,
        ignore_not_found=True,
    )

    database_1 = Database(DATABASE_FILE)
    database_1.initialize()

    repository_1 = RuntimeTaskRepository(database_1)
    repository_1.initialize()

    service_1, optical_manager_1 = build_runtime(
        database=database_1,
        repository=repository_1,
        adapter=adapter,
    )

    print()
    print("=" * 100)
    print("1. Submitting task")

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
        f"Submitted state: "
        f"{submission.task_record.state.value}"
    )

    if submission.task_record.state != TaskState.RUNNING:
        raise AssertionError(
            "Submitted task did not enter RUNNING."
        )

    print()
    print("=" * 100)
    print("2. Simulating controller restart")

    database_2 = Database(DATABASE_FILE)
    database_2.initialize()

    repository_2 = RuntimeTaskRepository(database_2)
    repository_2.initialize()

    service_2, optical_manager_2 = build_runtime(
        database=database_2,
        repository=repository_2,
        adapter=adapter,
    )

    restored = service_2.recover()

    print(f"Restored allocations: {restored}")

    if restored != 1:
        raise AssertionError(
            "Expected one recovered optical allocation."
        )

    lease_repository = ControllerLeaseRepository(
        database_2
    )
    lease_repository.initialize()

    controller = RuntimeReconciliationController(
        repository=repository_2,
        runtime_service=service_2,
        kubernetes_adapter=adapter,
        lease_repository=lease_repository,
        controller_id="test-controller",
        lease_name="runtime-controller-test",
        lease_seconds=10,
    )

    print()
    print("=" * 100)
    print("3. Verifying single-controller lease")

    acquired_by_other = lease_repository.try_acquire(
        lease_name="runtime-controller-test",
        holder_id="other-controller",
        lease_seconds=10,
    )

    if not acquired_by_other:
        raise AssertionError(
            "Could not acquire test lease."
        )

    blocked_result = controller.run_once()

    print(
        f"Lease acquired by test controller: "
        f"{blocked_result.lease_acquired}"
    )

    if blocked_result.lease_acquired:
        raise AssertionError(
            "Controller ignored an active foreign lease."
        )

    lease_repository.release(
        lease_name="runtime-controller-test",
        holder_id="other-controller",
    )

    print("[PASS] Duplicate controller was blocked")

    print()
    print("=" * 100)
    print("4. Running automatic reconciliation")

    final_record = None

    for iteration in range(1, 61):
        result = controller.run_once()

        print(
            f"Iteration {iteration:02d}: "
            f"scanned={result.scanned_count}, "
            f"changed={result.changed_count}, "
            f"errors={result.error_count}"
        )

        for event in result.events:
            print(
                f"  task={event.task_id}, "
                f"job={event.job_state}, "
                f"action={event.action}, "
                f"final={event.final_state}, "
                f"error={event.error}"
            )

        final_record = repository_2.get_task(
            task.task_id
        )

        if final_record is None:
            raise AssertionError(
                "Task disappeared during reconciliation."
            )

        if final_record.state in {
            TaskState.RELEASED,
            TaskState.FAILED,
        }:
            break

        time.sleep(1)

    if final_record is None:
        raise AssertionError(
            "No final task record was produced."
        )

    print()
    print("=" * 100)
    print("Final result")
    print(f"Task state: {final_record.state.value}")

    execution = repository_2.get_execution(
        task.task_id
    )

    if execution is None:
        raise AssertionError(
            "Execution record was not persisted."
        )

    print(f"Job phase: {execution.phase}")
    print(f"Job succeeded: {execution.succeeded}")
    print(f"Requested site: {execution.requested_site_id}")
    print(f"Actual site: {execution.node_site_label}")
    print(f"Node: {execution.node_name}")

    print()
    print("Container logs")
    print("-" * 100)
    print((execution.logs or "").rstrip())
    print("-" * 100)

    if final_record.state != TaskState.RELEASED:
        raise AssertionError(
            f"Expected RELEASED, received "
            f"{final_record.state.value}."
        )

    if execution.phase != "SUCCEEDED":
        raise AssertionError(
            f"Expected SUCCEEDED, received "
            f"{execution.phase}."
        )

    if execution.succeeded is not True:
        raise AssertionError(
            "Kubernetes Job did not succeed."
        )

    if (
        execution.requested_site_id
        != execution.node_site_label
    ):
        raise AssertionError(
            "Requested and actual sites differ."
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
        "Runtime reconciliation controller test "
        "completed successfully."
    )


if __name__ == "__main__":
    main()
