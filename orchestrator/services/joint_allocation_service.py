"""Transactional compute and optical-spectrum allocation service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.adapters.gnpy_adapter import (
    GNPyAdapter,
    GNPyAdapterError,
)
from orchestrator.adapters.gnpy_request_builder import (
    GNPyRequestBuilder,
    GNPyRequestBuilderError,
)
from orchestrator.adapters.gnpy_spectrum_parser import (
    GNPySpectrumParserError,
    extract_fiber_links,
    extract_spectrum_slots,
)
from orchestrator.models.resources import ComputeSite, TaskRequest
from orchestrator.models.spectrum import SpectrumSlot
from orchestrator.state.optical_resource_manager import (
    OpticalAllocation,
    OpticalResourceManager,
    OpticalResourceManagerError,
)
from orchestrator.state.resource_manager import (
    ResourceAllocation,
    ResourceManager,
    ResourceManagerError,
)


@dataclass(frozen=True)
class CandidatePlan:
    """Evaluation result for one compute-site candidate."""

    site_id: str
    site_name: str
    feasible: bool
    reason: str

    slot: SpectrumSlot | None
    fiber_links: tuple[str, ...]
    route: tuple[str, ...]

    gsnr_db: float | None
    resource_score: float | None
    total_score: float | None


@dataclass(frozen=True)
class JointAllocation:
    """Committed compute and optical allocation."""

    task_id: str
    site_id: str

    compute_allocation: ResourceAllocation
    optical_allocation: OpticalAllocation

    gsnr_db: float
    total_score: float
    route: tuple[str, ...]


class JointAllocationServiceError(RuntimeError):
    """Raised when a joint allocation cannot be completed."""

    def __init__(
        self,
        message: str,
        plans: list[CandidatePlan] | None = None,
    ) -> None:
        super().__init__(message)
        self.plans = plans or []


class JointAllocationService:
    """
    Plan and commit compute-optical allocations.

    Workflow:
    1. Check compute resources.
    2. Ask GNPy for a candidate route and required slot width M.
    3. Search an available N/M using the optical resource manager.
    4. Re-run GNPy with the explicit N/M to verify QoT.
    5. Reserve compute and optical resources transactionally.
    """

    def __init__(
        self,
        project_root: str | Path,
        gnpy_adapter: GNPyAdapter,
        request_builder: GNPyRequestBuilder,
        compute_manager: ResourceManager,
        optical_manager: OpticalResourceManager,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.gnpy_adapter = gnpy_adapter
        self.request_builder = request_builder
        self.compute_manager = compute_manager
        self.optical_manager = optical_manager

        self._allocations: dict[str, JointAllocation] = {}

    def evaluate(
        self,
        task: TaskRequest,
        topology_file: str | Path,
        equipment_file: str | Path,
    ) -> list[CandidatePlan]:
        """Evaluate all current compute-site candidates."""

        plans: list[CandidatePlan] = []

        for site in self.compute_manager.snapshot_sites():
            plans.append(
                self._evaluate_site(
                    task=task,
                    site=site,
                    topology_file=topology_file,
                    equipment_file=equipment_file,
                )
            )

        return plans

    def allocate(
        self,
        task: TaskRequest,
        topology_file: str | Path,
        equipment_file: str | Path,
    ) -> tuple[JointAllocation, list[CandidatePlan]]:
        """Evaluate candidates and commit the best feasible allocation."""

        if task.task_id in self._allocations:
            raise JointAllocationServiceError(
                f"Task is already allocated: {task.task_id}"
            )

        plans = self.evaluate(
            task=task,
            topology_file=topology_file,
            equipment_file=equipment_file,
        )

        feasible_plans = [
            plan for plan in plans
            if plan.feasible
            and plan.slot is not None
            and plan.total_score is not None
        ]

        if not feasible_plans:
            raise JointAllocationServiceError(
                f"No feasible compute-optical allocation for "
                f"{task.task_id}.",
                plans=plans,
            )

        selected = max(
            feasible_plans,
            key=lambda item: item.total_score,
        )

        compute_allocation: ResourceAllocation | None = None

        try:
            compute_allocation = self.compute_manager.reserve(
                task=task,
                site_id=selected.site_id,
            )

            optical_allocation = self.optical_manager.reserve(
                task_id=task.task_id,
                links=selected.fiber_links,
                slot=selected.slot,
            )

        except (
            ResourceManagerError,
            OpticalResourceManagerError,
        ) as exc:
            # Roll back compute reservation if optical reservation failed.
            if compute_allocation is not None:
                try:
                    self.compute_manager.release(task.task_id)
                except ResourceManagerError:
                    pass

            raise JointAllocationServiceError(
                f"Transactional reservation failed for "
                f"{task.task_id}: {exc}",
                plans=plans,
            ) from exc

        allocation = JointAllocation(
            task_id=task.task_id,
            site_id=selected.site_id,
            compute_allocation=compute_allocation,
            optical_allocation=optical_allocation,
            gsnr_db=float(selected.gsnr_db or 0.0),
            total_score=float(selected.total_score),
            route=selected.route,
        )

        self._allocations[task.task_id] = allocation

        return allocation, plans

    def release(self, task_id: str) -> JointAllocation:
        """Release both compute and optical resources."""

        allocation = self._allocations.get(task_id)

        if allocation is None:
            raise JointAllocationServiceError(
                f"No active joint allocation for task: {task_id}"
            )

        try:
            self.optical_manager.release(task_id)
            self.compute_manager.release(task_id)
        except (
            OpticalResourceManagerError,
            ResourceManagerError,
        ) as exc:
            raise JointAllocationServiceError(
                f"Failed to release joint allocation "
                f"{task_id}: {exc}"
            ) from exc

        del self._allocations[task_id]
        return allocation

    def active_allocations(self) -> list[JointAllocation]:
        """Return all committed joint allocations."""

        return list(self._allocations.values())

    def get_allocation(
        self,
        task_id: str,
    ) -> JointAllocation | None:
        """Return one active in-memory allocation."""

        return self._allocations.get(task_id)

    def restore_allocation(
        self,
        allocation: JointAllocation,
    ) -> None:
        """
        Register an allocation restored from persistent storage.

        The corresponding compute and optical resource managers must already
        contain the restored reservations before this method is called.
        """

        if allocation.task_id in self._allocations:
            raise JointAllocationServiceError(
                f"Task is already restored: {allocation.task_id}"
            )

        self._allocations[allocation.task_id] = allocation

    def _evaluate_site(
        self,
        task: TaskRequest,
        site: ComputeSite,
        topology_file: str | Path,
        equipment_file: str | Path,
    ) -> CandidatePlan:
        """Evaluate one site through discovery and explicit-slot QoT checks."""

        if not site.can_host(task):
            return self._failed_plan(
                site,
                "Insufficient compute resources",
            )

        prefix = f"{task.task_id}-{site.site_id}"

        discovery_request = (
            self.project_root
            / "requests"
            / "generated"
            / f"{prefix}-discovery.json"
        )
        discovery_result = (
            self.project_root
            / "experiments"
            / "results"
            / "transactional"
            / f"{prefix}-discovery.json"
        )

        try:
            self.request_builder.build(
                output_file=discovery_request,
                request_id=f"{prefix}-discovery",
                source=task.source_access_node,
                destination=site.network_access_node,
                bandwidth_gbps=task.bandwidth_gbps,
                spectrum_slot=None,
            )

            discovery_responses = self.gnpy_adapter.compute_paths(
                topology_file=topology_file,
                service_file=discovery_request,
                equipment_file=equipment_file,
                output_file=discovery_result,
                spectrum_policy="first_fit",
                verbose=False,
            )

            if (
                not discovery_responses
                or not discovery_responses[0].feasible
            ):
                return self._failed_plan(
                    site,
                    "GNPy could not discover a feasible path",
                )

            discovery = discovery_responses[0]

            fiber_links = extract_fiber_links(
                discovery.raw_response
            )
            discovered_slots = extract_spectrum_slots(
                discovery.raw_response
            )

            if not fiber_links:
                return self._failed_plan(
                    site,
                    "No fiber links were extracted from the path",
                )

            if len(discovered_slots) != 1:
                return self._failed_plan(
                    site,
                    "GNPy did not return exactly one spectrum slot",
                )

            required_m = discovered_slots[0].m

            candidate_slot = self.optical_manager.find_first_fit(
                links=fiber_links,
                m=required_m,
            )

            explicit_request = (
                self.project_root
                / "requests"
                / "generated"
                / f"{prefix}-explicit.json"
            )
            explicit_result = (
                self.project_root
                / "experiments"
                / "results"
                / "transactional"
                / f"{prefix}-explicit.json"
            )

            self.request_builder.build(
                output_file=explicit_request,
                request_id=f"{prefix}-explicit",
                source=task.source_access_node,
                destination=site.network_access_node,
                bandwidth_gbps=task.bandwidth_gbps,
                spectrum_slot=candidate_slot,
            )

            verified_responses = self.gnpy_adapter.compute_paths(
                topology_file=topology_file,
                service_file=explicit_request,
                equipment_file=equipment_file,
                output_file=explicit_result,
                spectrum_policy=None,
                verbose=False,
            )

            if (
                not verified_responses
                or not verified_responses[0].feasible
            ):
                return self._failed_plan(
                    site,
                    "Explicit spectrum slot failed GNPy validation",
                )

            verified = verified_responses[0]

            verified_slots = extract_spectrum_slots(
                verified.raw_response
            )
            verified_links = extract_fiber_links(
                verified.raw_response
            )

            if verified_slots != [candidate_slot]:
                return self._failed_plan(
                    site,
                    "GNPy did not preserve the requested N/M",
                )

            if not verified_links:
                return self._failed_plan(
                    site,
                    "Verified path contains no fiber links",
                )

            # Recheck availability using the route produced by the explicit
            # spectrum verification run.
            if not self.optical_manager.is_available(
                verified_links,
                candidate_slot,
            ):
                return self._failed_plan(
                    site,
                    "Spectrum became unavailable on verified route",
                )

            gsnr_value = verified.metrics.get(
                "SNR-bandwidth"
            )

            try:
                gsnr_db = float(gsnr_value)
            except (TypeError, ValueError):
                return self._failed_plan(
                    site,
                    "GNPy response has no valid GSNR value",
                )

            resource_score = (
                site.resource_score_after_allocation(task)
            )
            total_score = resource_score + gsnr_db

            return CandidatePlan(
                site_id=site.site_id,
                site_name=site.name,
                feasible=True,
                reason="Feasible",
                slot=candidate_slot,
                fiber_links=tuple(verified_links),
                route=tuple(verified.route),
                gsnr_db=gsnr_db,
                resource_score=resource_score,
                total_score=total_score,
            )

        except (
            GNPyAdapterError,
            GNPyRequestBuilderError,
            GNPySpectrumParserError,
            OpticalResourceManagerError,
        ) as exc:
            return self._failed_plan(
                site,
                str(exc),
            )

    @staticmethod
    def _failed_plan(
        site: ComputeSite,
        reason: str,
    ) -> CandidatePlan:
        return CandidatePlan(
            site_id=site.site_id,
            site_name=site.name,
            feasible=False,
            reason=reason,
            slot=None,
            fiber_links=(),
            route=(),
            gsnr_db=None,
            resource_score=None,
            total_score=None,
        )
