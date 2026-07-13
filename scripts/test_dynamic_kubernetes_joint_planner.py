"""Test joint planning with live Kubernetes compute resources."""

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
from orchestrator.models.runtime_task import (  # noqa: E402
    RuntimeTaskRequest,
)
from orchestrator.services.kubernetes_joint_planner import (  # noqa: E402
    KubernetesJointPlanner,
    KubernetesJointPlannerError,
)
from orchestrator.state.optical_resource_manager import (  # noqa: E402
    OpticalResourceManager,
    OpticalResourceManagerError,
)


TASK_FILE = (
    PROJECT_ROOT
    / "workloads"
    / "kubernetes-task-001.json"
)


def load_task() -> RuntimeTaskRequest:
    with TASK_FILE.open("r", encoding="utf-8") as file:
        document = json.load(file)

    return RuntimeTaskRequest.from_dict(document)


def print_resource_snapshot(
    title: str,
    discovery: KubernetesResourceDiscovery,
) -> dict[str, tuple[int, int, int]]:
    snapshots = discovery.discover()

    print()
    print(title)
    print("-" * 112)
    print(
        f"{'Site':<16}"
        f"{'Node':<30}"
        f"{'CPU free':<14}"
        f"{'CPU req':<14}"
        f"{'Mem free MiB':<16}"
        f"{'Pods':<8}"
    )
    print("-" * 112)

    state: dict[str, tuple[int, int, int]] = {}

    for site in snapshots:
        memory_free_mib = (
            site.available_memory_bytes
            // (1024 * 1024)
        )

        print(
            f"{site.site_id:<16}"
            f"{site.node_name:<30}"
            f"{site.available_cpu_m:<14}"
            f"{site.requested_cpu_m:<14}"
            f"{memory_free_mib:<16}"
            f"{site.active_pod_count:<8}"
        )

        state[site.site_id] = (
            site.available_cpu_m,
            site.requested_cpu_m,
            site.active_pod_count,
        )

    print("-" * 112)
    return state


def print_candidates(candidates) -> None:
    print()
    print("Joint candidate plans")
    print("-" * 130)

    for candidate in candidates:
        slot_text = (
            f"N={candidate.spectrum_slot.n},"
            f"M={candidate.spectrum_slot.m}"
            if candidate.spectrum_slot is not None
            else "None"
        )

        score_text = (
            f"{candidate.total_score:.2f}"
            if candidate.total_score is not None
            else "None"
        )

        print(
            f"{candidate.site_id:<16}"
            f"node={candidate.node_name:<30}"
            f"feasible={str(candidate.feasible):<7} "
            f"CPU={candidate.available_cpu_m:<8} "
            f"slot={slot_text:<16} "
            f"GSNR={str(candidate.gsnr_db):<8} "
            f"score={score_text:<8} "
            f"reason={candidate.reason}"
        )

    print("-" * 130)


def main() -> None:
    task = load_task()

    kubernetes_adapter = KubernetesAdapter(
        namespace="compute-optical",
        context="kind-compute-optical",
    )

    kubernetes_adapter.verify_cluster()
    kubernetes_adapter.ensure_namespace()

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

    print_resource_snapshot(
        "Kubernetes resources before planning",
        discovery,
    )

    try:
        plan, candidates = planner.select(
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
    except KubernetesJointPlannerError as exc:
        print_candidates(exc.candidate_plans)
        print(f"[BLOCKED] {exc}")
        raise SystemExit(1) from exc

    print_candidates(candidates)

    print()
    print("Selected joint plan")
    print("=" * 100)
    print(f"Task ID:        {plan.task_id}")
    print(f"Site:           {plan.selected_site_id}")
    print(f"Node:           {plan.selected_node_name}")
    print(
        f"Spectrum:       "
        f"N={plan.spectrum_slot.n}, "
        f"M={plan.spectrum_slot.m}"
    )
    print(f"GSNR:           {plan.gsnr_db:.2f} dB")
    print(f"Score:          {plan.total_score:.2f}")

    job_name = kubernetes_adapter.job_name_for_task(
        task.task_id
    )

    kubernetes_adapter.delete_job(
        job_name=job_name,
        ignore_not_found=True,
    )

    optical_reserved = False
    job_created = False

    try:
        optical_allocation = planner.reserve_optical(plan)
        optical_reserved = True

        print()
        print(
            f"[OPTICAL RESERVED] "
            f"N={optical_allocation.slot.n}, "
            f"M={optical_allocation.slot.m}"
        )

        job_name = kubernetes_adapter.create_job(
            task_id=task.task_id,
            site_id=plan.selected_site_id,
            image=task.image,
            shell_command=task.shell_command,
            cpu_request=task.cpu_request_quantity,
            memory_request=(
                task.memory_request_quantity
            ),
            cpu_limit=task.cpu_limit_quantity,
            memory_limit=task.memory_limit_quantity,
            gpu_request=task.gpu_request,
            image_pull_policy="Never",
        )

        job_created = True

        print(f"[JOB CREATED] {job_name}")

        # Wait briefly until the active Pod request becomes visible.
        resource_change_detected = False

        for _ in range(30):
            snapshots = discovery.discover()

            selected_site = next(
                site
                for site in snapshots
                if site.site_id
                == plan.selected_site_id
            )

            if (
                selected_site.requested_cpu_m
                >= task.cpu_request_m
            ):
                resource_change_detected = True
                break

            time.sleep(0.5)

        print_resource_snapshot(
            "Kubernetes resources while Job is active",
            discovery,
        )

        if not resource_change_detected:
            print(
                "[WARNING] The Job completed or was not yet "
                "visible before the resource snapshot."
            )
        else:
            print(
                "[PASS] Kubernetes Pod requests are visible "
                "in the selected site's resource snapshot."
            )

        result = kubernetes_adapter.wait_for_completion(
            task_id=task.task_id,
            job_name=job_name,
            site_id=plan.selected_site_id,
            timeout_seconds=120,
        )

        print()
        print("Kubernetes execution result")
        print("=" * 100)
        print(f"Job:             {result.job_name}")
        print(f"Pod:             {result.pod_name}")
        print(f"Node:            {result.node_name}")
        print(
            f"Requested site:  "
            f"{result.site_id}"
        )
        print(
            f"Actual site:     "
            f"{result.node_site_label}"
        )
        print(f"Succeeded:       {result.succeeded}")

        print()
        print("Container logs")
        print("-" * 100)
        print(result.logs.rstrip())
        print("-" * 100)

        if not result.succeeded:
            raise AssertionError(
                "Kubernetes Job did not succeed."
            )

        if (
            result.node_site_label
            != plan.selected_site_id
        ):
            raise AssertionError(
                "Kubernetes execution site differs from "
                "the joint planning decision."
            )

        if (
            result.node_name
            != plan.selected_node_name
        ):
            raise AssertionError(
                "Kubernetes execution node differs from "
                "the selected node."
            )

    finally:
        if optical_reserved:
            try:
                released = planner.release_optical(
                    task.task_id
                )

                print()
                print(
                    f"[OPTICAL RELEASED] "
                    f"N={released.slot.n}, "
                    f"M={released.slot.m}"
                )
            except OpticalResourceManagerError as exc:
                print(
                    f"[OPTICAL RELEASE ERROR] {exc}"
                )

    if optical_manager.active_allocations():
        raise AssertionError(
            "Optical resources were not fully released."
        )

    print_resource_snapshot(
        "Kubernetes resources after Job completion",
        discovery,
    )

    print()
    print(
        "Dynamic Kubernetes joint-planning test "
        "completed successfully."
    )


if __name__ == "__main__":
    main()
