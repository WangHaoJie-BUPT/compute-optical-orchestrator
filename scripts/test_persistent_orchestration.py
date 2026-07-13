"""Test SQLite persistence, idempotency and restart recovery."""

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
from orchestrator.models.resources import (  # noqa: E402
    ComputeSite,
    TaskRequest,
)
from orchestrator.repositories.task_repository import (  # noqa: E402
    TaskRepository,
)
from orchestrator.services.joint_allocation_service import (  # noqa: E402
    JointAllocationService,
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
    / "persistent-orchestrator-test.db"
)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_sites() -> list[ComputeSite]:
    document = load_json(
        PROJECT_ROOT / "configs" / "compute_sites.json"
    )

    return [
        ComputeSite.from_dict(item)
        for item in document["compute_sites"]
    ]


def load_tasks() -> list[TaskRequest]:
    document = load_json(
        PROJECT_ROOT
        / "workloads"
        / "tasks-sequential.json"
    )

    return [
        TaskRequest.from_dict(item)
        for item in document["tasks"]
    ]


def build_runtime():
    compute_manager = ResourceManager(load_sites())

    optical_manager = OpticalResourceManager(
        spectrum_lower_index=-288,
        spectrum_upper_index=480,
    )

    adapter = GNPyAdapter(
        project_root=PROJECT_ROOT,
    )

    builder = GNPyRequestBuilder(
        PROJECT_ROOT
        / "requests"
        / "gnpy_demo"
        / "services-single.json"
    )

    allocation_service = JointAllocationService(
        project_root=PROJECT_ROOT,
        gnpy_adapter=adapter,
        request_builder=builder,
        compute_manager=compute_manager,
        optical_manager=optical_manager,
    )

    database = Database(DATABASE_FILE)
    database.initialize()

    repository = TaskRepository(database)

    persistent_service = PersistentOrchestrationService(
        repository=repository,
        allocation_service=allocation_service,
    )

    return (
        compute_manager,
        optical_manager,
        allocation_service,
        repository,
        persistent_service,
    )


def compute_snapshot(manager: ResourceManager) -> tuple:
    return tuple(
        (
            site.site_id,
            site.cpu_available,
            site.memory_gb_available,
            site.gpu_available,
        )
        for site in manager.snapshot_sites()
    )


def optical_snapshot(
    manager: OpticalResourceManager,
) -> tuple:
    return tuple(
        sorted(
            (
                allocation.task_id,
                allocation.slot.n,
                allocation.slot.m,
                allocation.links,
            )
            for allocation in manager.active_allocations()
        )
    )


def print_state(
    title: str,
    compute_manager: ResourceManager,
    optical_manager: OpticalResourceManager,
    repository: TaskRepository,
) -> None:
    print()
    print("=" * 100)
    print(title)

    print()
    print("Tasks")
    print("-" * 100)

    for record in repository.list_tasks():
        print(
            f"{record.task.task_id:<14}"
            f"state={record.state.value:<16}"
            f"site={str(record.selected_site):<16}"
            f"reason={record.failure_reason}"
        )

    print()
    print("Compute resources")
    print("-" * 100)

    for site in compute_manager.snapshot_sites():
        print(
            f"{site.site_id:<16}"
            f"CPU={site.cpu_available:<5}"
            f"memory={site.memory_gb_available:<6}"
            f"GPU={site.gpu_available}"
        )

    print()
    print("Optical allocations")
    print("-" * 100)

    allocations = optical_manager.active_allocations()

    if not allocations:
        print("None")

    for allocation in allocations:
        print(
            f"{allocation.task_id:<16}"
            f"N={allocation.slot.n:<6}"
            f"M={allocation.slot.m:<4}"
            f"links={len(allocation.links)}"
        )


def main() -> None:
    # Use a clean database for this repeatable test.
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(DATABASE_FILE) + suffix)

        if candidate.exists():
            candidate.unlink()

    tasks = load_tasks()
    task_001 = tasks[0]
    task_002 = tasks[1]

    print("Starting first orchestrator process")

    (
        compute_1,
        optical_1,
        allocation_service_1,
        repository_1,
        service_1,
    ) = build_runtime()

    restored = service_1.recover()
    print(f"Initially restored allocations: {restored}")

    result_1 = service_1.submit(
        task=task_001,
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
        f"[SUBMITTED] {task_001.task_id}: "
        f"state={result_1.task_record.state.value}, "
        f"site={result_1.task_record.selected_site}"
    )

    result_2 = service_1.submit(
        task=task_002,
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
        f"[SUBMITTED] {task_002.task_id}: "
        f"state={result_2.task_record.state.value}, "
        f"site={result_2.task_record.selected_site}"
    )

    print_state(
        "State before duplicate submission",
        compute_1,
        optical_1,
        repository_1,
    )

    compute_before_duplicate = compute_snapshot(compute_1)
    optical_before_duplicate = optical_snapshot(optical_1)

    duplicate = service_1.submit(
        task=task_001,
        topology_file=(
            "topologies/gnpy_demo/"
            "meshTopologyExampleV2.xls"
        ),
        equipment_file=(
            "topologies/gnpy_demo/"
            "eqpt_config.json"
        ),
    )

    print()
    print(
        f"[DUPLICATE] {task_001.task_id}: "
        f"reused_existing={duplicate.reused_existing}, "
        f"state={duplicate.task_record.state.value}"
    )

    if compute_snapshot(compute_1) != compute_before_duplicate:
        raise AssertionError(
            "Duplicate submission changed compute resources."
        )

    if optical_snapshot(optical_1) != optical_before_duplicate:
        raise AssertionError(
            "Duplicate submission changed optical resources."
        )

    print(
        "Duplicate submission did not consume resources again."
    )

    expected_compute_state = compute_snapshot(compute_1)
    expected_optical_state = optical_snapshot(optical_1)

    print()
    print("=" * 100)
    print("Simulating process restart")

    # New in-memory managers and services, same SQLite database.
    (
        compute_2,
        optical_2,
        allocation_service_2,
        repository_2,
        service_2,
    ) = build_runtime()

    restored = service_2.recover()

    print(
        f"Restored allocations after restart: {restored}"
    )

    if compute_snapshot(compute_2) != expected_compute_state:
        raise AssertionError(
            "Compute state was not restored correctly."
        )

    if optical_snapshot(optical_2) != expected_optical_state:
        raise AssertionError(
            "Optical state was not restored correctly."
        )

    print_state(
        "State restored from SQLite",
        compute_2,
        optical_2,
        repository_2,
    )

    print()
    print("=" * 100)
    print("Releasing task-002 after restart")

    released = service_2.release(task_002.task_id)

    print(
        f"[RELEASED] {task_002.task_id}: "
        f"state={released.state.value}"
    )

    print_state(
        "State after persistent release",
        compute_2,
        optical_2,
        repository_2,
    )

    print()
    print(
        "Persistent orchestration test completed successfully."
    )


if __name__ == "__main__":
    main()
