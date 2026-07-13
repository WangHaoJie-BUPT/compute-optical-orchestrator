"""First-stage compute and optical-network joint scheduler."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.adapters.gnpy_adapter import (
    GNPyAdapter,
    GNPyAdapterError,
)
from orchestrator.adapters.gnpy_request_builder import (
    GNPyRequestBuilder,
)
from orchestrator.models.resources import ComputeSite, TaskRequest


@dataclass
class CandidateDecision:
    """Evaluation result for one candidate compute site."""

    site_id: str
    site_name: str
    network_access_node: str
    feasible: bool
    resource_feasible: bool
    optical_feasible: bool
    gsnr_db: float | None
    resource_score: float | None
    total_score: float | None
    route: list[str]
    reason: str


class JointSchedulerError(RuntimeError):
    """Raised when no feasible joint scheduling decision can be made."""


class JointScheduler:
    """Filter compute sites and verify optical paths with GNPy."""

    def __init__(
        self,
        project_root: str | Path,
        gnpy_adapter: GNPyAdapter,
        request_builder: GNPyRequestBuilder,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.gnpy_adapter = gnpy_adapter
        self.request_builder = request_builder

    def load_compute_sites(
        self,
        file_path: str | Path,
    ) -> list[ComputeSite]:
        document = self._load_json(file_path)
        raw_sites = document.get("compute_sites")

        if not isinstance(raw_sites, list):
            raise JointSchedulerError(
                "'compute_sites' must be a JSON list."
            )

        return [ComputeSite.from_dict(item) for item in raw_sites]

    def load_task(self, file_path: str | Path) -> TaskRequest:
        return TaskRequest.from_dict(self._load_json(file_path))

    def schedule(
        self,
        task: TaskRequest,
        sites: list[ComputeSite],
        topology_file: str | Path,
        equipment_file: str | Path,
    ) -> tuple[CandidateDecision, list[CandidateDecision]]:
        """Evaluate all sites and return the highest-scoring candidate."""

        evaluations: list[CandidateDecision] = []

        for site in sites:
            if not site.can_host(task):
                evaluations.append(
                    CandidateDecision(
                        site_id=site.site_id,
                        site_name=site.name,
                        network_access_node=site.network_access_node,
                        feasible=False,
                        resource_feasible=False,
                        optical_feasible=False,
                        gsnr_db=None,
                        resource_score=None,
                        total_score=None,
                        route=[],
                        reason="Insufficient compute resources",
                    )
                )
                continue

            request_file = (
                self.project_root
                / "requests"
                / "generated"
                / f"{task.task_id}-{site.site_id}.json"
            )

            result_file = (
                self.project_root
                / "experiments"
                / "results"
                / "joint_scheduler"
                / f"{task.task_id}-{site.site_id}.json"
            )

            self.request_builder.build(
                output_file=request_file,
                request_id=f"{task.task_id}-{site.site_id}",
                source=task.source_access_node,
                destination=site.network_access_node,
                bandwidth_gbps=task.bandwidth_gbps,
            )

            try:
                optical_results = self.gnpy_adapter.compute_paths(
                    topology_file=topology_file,
                    service_file=request_file,
                    equipment_file=equipment_file,
                    output_file=result_file,
                    spectrum_policy="first_fit",
                    verbose=False,
                )
            except GNPyAdapterError as exc:
                evaluations.append(
                    CandidateDecision(
                        site_id=site.site_id,
                        site_name=site.name,
                        network_access_node=site.network_access_node,
                        feasible=False,
                        resource_feasible=True,
                        optical_feasible=False,
                        gsnr_db=None,
                        resource_score=None,
                        total_score=None,
                        route=[],
                        reason=f"GNPy execution failed: {exc}",
                    )
                )
                continue

            if not optical_results or not optical_results[0].feasible:
                evaluations.append(
                    CandidateDecision(
                        site_id=site.site_id,
                        site_name=site.name,
                        network_access_node=site.network_access_node,
                        feasible=False,
                        resource_feasible=True,
                        optical_feasible=False,
                        gsnr_db=None,
                        resource_score=None,
                        total_score=None,
                        route=[],
                        reason="No feasible optical path",
                    )
                )
                continue

            optical_result = optical_results[0]
            gsnr_value = optical_result.metrics.get("SNR-bandwidth")

            try:
                gsnr_db = float(gsnr_value)
            except (TypeError, ValueError):
                gsnr_db = 0.0

            resource_score = site.resource_score_after_allocation(task)

            # First-stage heuristic:
            # higher remaining resources and higher GSNR are preferred.
            total_score = resource_score + gsnr_db

            evaluations.append(
                CandidateDecision(
                    site_id=site.site_id,
                    site_name=site.name,
                    network_access_node=site.network_access_node,
                    feasible=True,
                    resource_feasible=True,
                    optical_feasible=True,
                    gsnr_db=gsnr_db,
                    resource_score=resource_score,
                    total_score=total_score,
                    route=optical_result.route,
                    reason="Feasible",
                )
            )

        feasible_candidates = [
            item for item in evaluations if item.feasible
        ]

        if not feasible_candidates:
            raise JointSchedulerError(
                f"No feasible compute-optical destination for {task.task_id}."
            )

        selected = max(
            feasible_candidates,
            key=lambda item: (
                item.total_score
                if item.total_score is not None
                else float("-inf")
            ),
        )

        return selected, evaluations

    def _load_json(self, file_path: str | Path) -> dict[str, Any]:
        path = Path(file_path).expanduser()

        if not path.is_absolute():
            path = self.project_root / path

        if not path.is_file():
            raise JointSchedulerError(f"File does not exist: {path}")

        try:
            with path.open("r", encoding="utf-8") as file:
                document = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise JointSchedulerError(
                f"Cannot read JSON file: {path}"
            ) from exc

        if not isinstance(document, dict):
            raise JointSchedulerError(
                f"JSON root must be an object: {path}"
            )

        return document
