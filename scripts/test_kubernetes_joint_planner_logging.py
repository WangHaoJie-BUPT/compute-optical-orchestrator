"""Verify candidate diagnostics emitted by KubernetesJointPlanner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.models.runtime_task import RuntimeTaskRequest
from orchestrator.models.spectrum import SpectrumSlot
from orchestrator.services.kubernetes_joint_planner import (
    KubernetesCandidatePlan,
    KubernetesJointPlanner,
    KubernetesJointPlannerError,
)


class StaticCandidatePlanner(KubernetesJointPlanner):
    def __init__(
        self,
        candidates: list[KubernetesCandidatePlan],
    ) -> None:
        self.candidates = candidates

    def evaluate(self, task, topology_file, equipment_file):
        return self.candidates


def task_request() -> RuntimeTaskRequest:
    return RuntimeTaskRequest(
        task_id="candidate-log-test",
        source_access_node="trx Lorient_KMA",
        cpu_request_m=500,
        memory_request_mib=256,
        gpu_request=0,
        bandwidth_gbps=100,
        image="busybox:1.36",
        shell_command="true",
    )


def candidate(
    *,
    feasible: bool,
    reason: str,
) -> KubernetesCandidatePlan:
    slot = SpectrumSlot(n=-284, m=4) if feasible else None
    return KubernetesCandidatePlan(
        site_id="site-vannes",
        site_name="Vannes Compute Site",
        node_name="compute-optical-worker",
        network_access_node="trx Vannes_KBE",
        feasible=feasible,
        reason=reason,
        available_cpu_m=11500,
        available_memory_mib=7000,
        available_gpu=0,
        accelerators=(),
        spectrum_slot=slot,
        fiber_links=("fiber-a",) if feasible else (),
        route=("trx-a", "trx-b") if feasible else (),
        gsnr_db=24.0 if feasible else None,
        compute_score=0.9 if feasible else None,
        optical_score=0.8 if feasible else None,
        total_score=87.5 if feasible else None,
    )


class CandidateLoggingTest(unittest.TestCase):
    def test_failed_candidate_is_logged_at_warning(self) -> None:
        planner = StaticCandidatePlanner(
            [candidate(feasible=False, reason="GNPy failed")]
        )

        with self.assertLogs(
            "orchestrator.services.kubernetes_joint_planner",
            level="WARNING",
        ) as captured:
            with self.assertRaises(KubernetesJointPlannerError):
                planner.select(
                    task=task_request(),
                    topology_file=Path("topology.xls"),
                    equipment_file=Path("equipment.json"),
                )

        message = "\n".join(captured.output)
        self.assertIn("candidate-log-test", message)
        self.assertIn('"reason": "GNPy failed"', message)
        self.assertIn('"site_id": "site-vannes"', message)

    def test_selectable_candidate_is_logged_at_info(self) -> None:
        planner = StaticCandidatePlanner(
            [candidate(feasible=True, reason="Feasible")]
        )

        with self.assertLogs(
            "orchestrator.services.kubernetes_joint_planner",
            level="INFO",
        ) as captured:
            plan, _ = planner.select(
                task=task_request(),
                topology_file=Path("topology.xls"),
                equipment_file=Path("equipment.json"),
            )

        self.assertEqual(plan.selected_site_id, "site-vannes")
        self.assertIn('"total_score": 87.5', captured.output[0])


if __name__ == "__main__":
    unittest.main()
