"""Test joint allocation, Kubernetes execution and automatic release."""

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
from orchestrator.models.resources import (  # noqa: E402
    ComputeSite,
    TaskRequest,
)
from orchestrator.repositories.execution_repository import (  # noqa: E402
    ExecutionRepository,
)
from orchestrator.repositories.task_repository import (  # noqa: E402
    TaskRepository,
)
from orchestrator.services.joint_allocation_service import (  # noqa: E402
    JointAllocationService,
)
from orchestrator.services.kubernetes_execution_service import (  # noqa: E402
    KubernetesExecutionService,
)
from orchestrator.services.persistent_orchestration_service import (  # noqa: E402
    PersistentOrchestrationService,
)
from orchestrator.state.optical_resource_manager import (  # noqa: E402
    OpticalResourceManager,
)
from orchestrator.state.resource_manager import (  # noqa: E402
    ResourceManager,
)
from orchestrator.storage.database import Database  # noqa: E402


DATABASE_FILE = (
    PROJECT_ROOT
    / "runtime"
    / "end-to-end-orchestrator-test.db"
)


def load_compute_sites() -> list[ComputeSite]:
    path = PROJECT_ROOT / "configs" / "compute_sites.json"

    with path.open("r", encoding="utf-8") as file:
        document = json.load(file)

    return [
        ComputeSite.from_dict(item)
        for item in document["compute_sites"]
    ]


def remove_test_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(DATABASE_FILE) + suffix)

        if candidate.exists():
            candidate.unlink()


def main() -> None:
    remove_test_database()

    task = TaskRequest(
        task_id="e2e-task-001",
        source_access_node="trx Lorient_KMA",
        cpu_required=8,
        memory_gb_required=16,
        gpu_required=0,
        bandwidth_gbps=100,
    )

    compute_manager = ResourceManager(
        load_compute_sites()
    )

    optical_manager = OpticalResourceManager(
        spectrum_lower_index=-288,
        spectrum_upper_index=480,
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

    joint_service = JointAllocationService(
        project_root=PROJECT_ROOT,
        gnpy_adapter=gnpy_adapter,
        request_builder=request_builder,
        compute_manager=compute_manager,
        optical_manager=optical_manager,
    )

    database = Database(DATABASE_FILE)
    database.initialize()

    task_repository = TaskRepository(database)

    execution_repository = ExecutionRepository(database)
    execution_repository.initialize()

    persistent_service = PersistentOrchestrationService(
        repository=task_repository,
        allocation_service=joint_service,
    )

    restored_count = persistent_service.recover()

    print(
        f"Restored allocations before test: "
        f"{restored_count}"
    )

    kubernetes_adapter = KubernetesAdapter(
        namespace="compute-optical",
        context="kind-compute-optical",
    )

    kubernetes_adapter.verify_cluster()
    kubernetes_adapter.ensure_namespace()

    job_name = kubernetes_adapter.job_name_for_task(
        task.task_id
    )

    kubernetes_adapter.delete_job(
        job_name=job_name,
        ignore_not_found=True,
    )

    execution_service = KubernetesExecutionService(
        persistent_service=persistent_service,
        task_repository=task_repository,
        execution_repository=execution_repository,
        kubernetes_adapter=kubernetes_adapter,
    )

    shell_command = """
set -eu

echo "Compute-optical task started"
echo "Task ID: ${TASK_ID}"
echo "Pod name: ${POD_NAME}"
echo "Selected Kubernetes node: ${NODE_NAME}"
echo "Expected compute site: ${EXPECTED_SITE_ID}"

sleep 3

echo "Compute-optical task completed"
""".strip()

    print()
    print("=" * 100)
    print("Starting end-to-end orchestration")

    result = execution_service.execute(
        task=task,
        topology_file=(
            "topologies/gnpy_demo/"
            "meshTopologyExampleV2.xls"
        ),
        equipment_file=(
            "topologies/gnpy_demo/"
            "eqpt_config.json"
        ),
        image="busybox:1.36",
        image_pull_policy="Never",
        shell_command=shell_command,
        timeout_seconds=120,
    )

    allocation = result.submit_result.allocation

    if allocation is None:
        raise AssertionError(
            "No joint allocation was returned."
        )

    print()
    print("=" * 100)
    print("End-to-end execution result")
    print("=" * 100)

    print(f"Task ID:          {task.task_id}")
    print(f"Selected site:    {allocation.site_id}")
    print(
        f"Spectrum:         "
        f"N={allocation.optical_allocation.slot.n}, "
        f"M={allocation.optical_allocation.slot.m}"
    )
    print(f"GSNR:             {allocation.gsnr_db:.2f} dB")
    print(f"Kubernetes Job:   {result.job_result.job_name}")
    print(f"Pod:              {result.job_result.pod_name}")
    print(f"Node:             {result.job_result.node_name}")
    print(
        f"Node site label:  "
        f"{result.job_result.node_site_label}"
    )
    print(
        f"Job succeeded:    "
        f"{result.job_result.succeeded}"
    )
    print(
        f"Final task state: "
        f"{result.final_task_record.state.value}"
    )

    print()
    print("Container logs")
    print("-" * 100)
    print(result.job_result.logs.rstrip())
    print("-" * 100)

    if not result.job_result.succeeded:
        raise AssertionError(
            "The Kubernetes Job did not succeed."
        )

    if (
        result.job_result.node_site_label
        != allocation.site_id
    ):
        raise AssertionError(
            "Kubernetes execution site differs from "
            "the joint scheduling decision."
        )

    if result.final_task_record.state.value != "RELEASED":
        raise AssertionError(
            "The task was not released after completion."
        )

    if joint_service.active_allocations():
        raise AssertionError(
            "Joint allocations were not fully released."
        )

    if optical_manager.active_allocations():
        raise AssertionError(
            "Optical allocations were not fully released."
        )

    execution_record = execution_repository.get(
        task.task_id
    )

    if execution_record is None:
        raise AssertionError(
            "Kubernetes execution was not persisted."
        )

    if execution_record.phase != "SUCCEEDED":
        raise AssertionError(
            f"Unexpected execution phase: "
            f"{execution_record.phase}"
        )

    print()
    print(
        "End-to-end orchestration test completed successfully."
    )


if __name__ == "__main__":
    main()
