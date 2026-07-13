"""Test stateful scheduling, reservation, blocking, and release."""

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
from orchestrator.models.resources import TaskRequest  # noqa: E402
from orchestrator.schedulers.joint_scheduler import (  # noqa: E402
    JointScheduler,
    JointSchedulerError,
)
from orchestrator.state.resource_manager import (  # noqa: E402
    ResourceManager,
    ResourceManagerError,
)


def load_tasks(file_path: Path) -> list[TaskRequest]:
    with file_path.open("r", encoding="utf-8") as file:
        document = json.load(file)

    raw_tasks = document.get("tasks")

    if not isinstance(raw_tasks, list):
        raise ValueError("'tasks' must be a list.")

    return [TaskRequest.from_dict(item) for item in raw_tasks]


def print_resource_state(manager: ResourceManager) -> None:
    print()
    print("Current compute-resource state")
    print("-" * 84)
    print(
        f"{'Site':<18}"
        f"{'CPU available':<16}"
        f"{'Memory available':<20}"
        f"{'GPU available':<15}"
    )
    print("-" * 84)

    for site in manager.snapshot_sites():
        print(
            f"{site.site_id:<18}"
            f"{site.cpu_available:<16}"
            f"{site.memory_gb_available:<20}"
            f"{site.gpu_available:<15}"
        )

    print("-" * 84)


def schedule_and_reserve(
    scheduler: JointScheduler,
    manager: ResourceManager,
    task: TaskRequest,
) -> bool:
    print()
    print("=" * 100)
    print(f"Scheduling task: {task.task_id}")
    print(
        f"Requirements: CPU={task.cpu_required}, "
        f"memory={task.memory_gb_required} GB, "
        f"GPU={task.gpu_required}, "
        f"bandwidth={task.bandwidth_gbps} Gbit/s"
    )

    try:
        selected, evaluations = scheduler.schedule(
            task=task,
            sites=manager.snapshot_sites(),
            topology_file=(
                "topologies/gnpy_demo/"
                "meshTopologyExampleV2.xls"
            ),
            equipment_file=(
                "topologies/gnpy_demo/"
                "eqpt_config.json"
            ),
        )
    except JointSchedulerError as exc:
        print(f"[BLOCKED] {exc}")
        return False

    print()
    print("Candidate results:")

    for item in evaluations:
        status = "feasible" if item.feasible else "infeasible"

        print(
            f"  {item.site_id:<16}"
            f"{status:<12}"
            f"GSNR={str(item.gsnr_db):<8}"
            f"score={str(item.total_score):<10}"
            f"reason={item.reason}"
        )

    try:
        allocation = manager.reserve(
            task=task,
            site_id=selected.site_id,
        )
    except ResourceManagerError as exc:
        print(f"[RESERVATION FAILED] {exc}")
        return False

    print()
    print(
        f"[ACCEPTED] {task.task_id} -> {selected.site_id}, "
        f"allocation={allocation.allocation_id}"
    )
    print(f"Selected GSNR: {selected.gsnr_db} dB")
    print(f"Selected score: {selected.total_score}")

    print_resource_state(manager)
    return True


def main() -> None:
    gnpy_adapter = GNPyAdapter(project_root=PROJECT_ROOT)

    request_builder = GNPyRequestBuilder(
        PROJECT_ROOT
        / "requests"
        / "gnpy_demo"
        / "services-single.json"
    )

    scheduler = JointScheduler(
        project_root=PROJECT_ROOT,
        gnpy_adapter=gnpy_adapter,
        request_builder=request_builder,
    )

    initial_sites = scheduler.load_compute_sites(
        "configs/compute_sites.json"
    )

    manager = ResourceManager(initial_sites)

    tasks = load_tasks(
        PROJECT_ROOT
        / "workloads"
        / "tasks-sequential.json"
    )

    print_resource_state(manager)

    blocked_tasks: list[TaskRequest] = []

    for task in tasks:
        accepted = schedule_and_reserve(
            scheduler=scheduler,
            manager=manager,
            task=task,
        )

        if not accepted:
            blocked_tasks.append(task)

    print()
    print("=" * 100)
    print("Active allocations")

    for allocation in manager.active_allocations():
        print(
            f"{allocation.task_id} -> {allocation.site_id}: "
            f"CPU={allocation.cpu}, "
            f"memory={allocation.memory_gb} GB, "
            f"GPU={allocation.gpu}"
        )

    # Simulate task-002 completing.
    print()
    print("=" * 100)
    print("Simulating completion of task-002")

    try:
        released = manager.release("task-002")
        print(
            f"[RELEASED] {released.task_id} from "
            f"{released.site_id}"
        )
    except ResourceManagerError as exc:
        print(f"[RELEASE FAILED] {exc}")

    print_resource_state(manager)

    # Retry previously blocked tasks after resource release.
    if blocked_tasks:
        print()
        print("=" * 100)
        print("Retrying blocked tasks after resource release")

        for task in blocked_tasks:
            schedule_and_reserve(
                scheduler=scheduler,
                manager=manager,
                task=task,
            )


if __name__ == "__main__":
    main()
