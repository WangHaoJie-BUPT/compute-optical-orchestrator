"""Run the first compute-optical joint scheduling experiment."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.gnpy_adapter import GNPyAdapter  # noqa: E402
from orchestrator.adapters.gnpy_request_builder import (  # noqa: E402
    GNPyRequestBuilder,
)
from orchestrator.schedulers.joint_scheduler import (  # noqa: E402
    JointScheduler,
    JointSchedulerError,
)


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

    sites = scheduler.load_compute_sites(
        "configs/compute_sites.json"
    )
    task = scheduler.load_task("workloads/task-001.json")

    try:
        selected, evaluations = scheduler.schedule(
            task=task,
            sites=sites,
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
        print(f"[ERROR] {exc}")
        raise SystemExit(1) from exc

    print()
    print(f"Task: {task.task_id}")
    print("=" * 100)

    for item in evaluations:
        print(f"Site:              {item.site_id}")
        print(f"Access node:       {item.network_access_node}")
        print(f"Resource feasible: {item.resource_feasible}")
        print(f"Optical feasible:  {item.optical_feasible}")
        print(f"GSNR:              {item.gsnr_db}")
        print(f"Resource score:    {item.resource_score}")
        print(f"Total score:       {item.total_score}")
        print(f"Reason:            {item.reason}")

        if item.route:
            print(f"Route:             {' -> '.join(item.route)}")

        print("-" * 100)

    print()
    print("Selected joint scheduling decision")
    print("=" * 100)
    print(f"Site ID:     {selected.site_id}")
    print(f"Site name:   {selected.site_name}")
    print(f"Access node: {selected.network_access_node}")
    print(f"GSNR:        {selected.gsnr_db} dB")
    print(f"Score:       {selected.total_score}")
    print(f"Route:       {' -> '.join(selected.route)}")


if __name__ == "__main__":
    main()
