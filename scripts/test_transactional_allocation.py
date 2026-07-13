"""Test transactional compute and spectrum allocation."""

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
from orchestrator.services.joint_allocation_service import (  # noqa: E402
    JointAllocationService,
    JointAllocationServiceError,
)
from orchestrator.state.optical_resource_manager import (  # noqa: E402
    OpticalResourceManager,
)
from orchestrator.state.resource_manager import (  # noqa: E402
    ResourceManager,
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
        PROJECT_ROOT / "workloads" / "tasks-sequential.json"
    )

    return [
        TaskRequest.from_dict(item)
        for item in document["tasks"]
    ]


def print_plans(plans) -> None:
    print()
    print("Candidate plans")
    print("-" * 110)

    for plan in plans:
        if plan.slot is None:
            slot_text = "None"
        else:
            slot_text = (
                f"N={plan.slot.n},M={plan.slot.m}"
            )

        score_text = (
            f"{plan.total_score:.2f}"
            if plan.total_score is not None
            else "None"
        )

        print(
            f"{plan.site_id:<16}"
            f"feasible={str(plan.feasible):<7} "
            f"slot={slot_text:<16} "
            f"GSNR={str(plan.gsnr_db):<8} "
            f"score={score_text:<8} "
            f"reason={plan.reason}"
        )

    print("-" * 110)


def print_state(
    compute_manager: ResourceManager,
    optical_manager: OpticalResourceManager,
) -> None:
    print()
    print("Compute state")
    print("-" * 75)

    for site in compute_manager.snapshot_sites():
        print(
            f"{site.site_id:<16}"
            f"CPU={site.cpu_available:<5}"
            f"memory={site.memory_gb_available:<6}"
            f"GPU={site.gpu_available}"
        )

    print()
    print("Optical allocations")
    print("-" * 75)

    allocations = optical_manager.active_allocations()

    if not allocations:
        print("None")

    for allocation in allocations:
        slot = allocation.slot

        print(
            f"{allocation.task_id:<16}"
            f"N={slot.n:<6}"
            f"M={slot.m:<4}"
            f"links={len(allocation.links)}"
        )

    print("-" * 75)


def main() -> None:
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

    service = JointAllocationService(
        project_root=PROJECT_ROOT,
        gnpy_adapter=adapter,
        request_builder=builder,
        compute_manager=compute_manager,
        optical_manager=optical_manager,
    )

    tasks = load_tasks()
    blocked_tasks: list[TaskRequest] = []

    print_state(compute_manager, optical_manager)

    for task in tasks:
        print()
        print("=" * 110)
        print(f"Allocating {task.task_id}")

        try:
            allocation, plans = service.allocate(
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

            print_plans(plans)

            slot = allocation.optical_allocation.slot

            print(
                f"[COMMITTED] {task.task_id} -> "
                f"{allocation.site_id}, "
                f"N={slot.n}, M={slot.m}, "
                f"GSNR={allocation.gsnr_db:.2f} dB, "
                f"score={allocation.total_score:.2f}"
            )

        except JointAllocationServiceError as exc:
            print_plans(exc.plans)
            print(f"[BLOCKED] {exc}")
            blocked_tasks.append(task)

        print_state(compute_manager, optical_manager)

    print()
    print("=" * 110)
    print("Releasing task-002")

    try:
        released = service.release("task-002")

        print(
            f"[RELEASED] {released.task_id} from "
            f"{released.site_id}"
        )
    except JointAllocationServiceError as exc:
        print(f"[RELEASE ERROR] {exc}")

    print_state(compute_manager, optical_manager)

    if blocked_tasks:
        print()
        print("=" * 110)
        print("Retrying blocked tasks")

        for task in blocked_tasks:
            try:
                allocation, plans = service.allocate(
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

                print_plans(plans)

                slot = allocation.optical_allocation.slot

                print(
                    f"[RETRY COMMITTED] {task.task_id} -> "
                    f"{allocation.site_id}, "
                    f"N={slot.n}, M={slot.m}"
                )

            except JointAllocationServiceError as exc:
                print_plans(exc.plans)
                print(f"[RETRY BLOCKED] {exc}")

    print_state(compute_manager, optical_manager)

    print()
    print(
        "Transactional joint-allocation test completed."
    )


if __name__ == "__main__":
    main()
