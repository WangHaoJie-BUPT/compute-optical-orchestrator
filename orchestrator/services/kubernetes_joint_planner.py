"""Joint planning using real Kubernetes compute resources and GNPy."""

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
from orchestrator.adapters.kubernetes_resource_discovery import (
    KubernetesResourceDiscovery,
    KubernetesResourceDiscoveryError,
)
from orchestrator.models.kubernetes_resources import (
    KubernetesSiteResources,
)
from orchestrator.models.runtime_task import (
    RuntimeTaskRequest,
)
from orchestrator.models.spectrum import SpectrumSlot
from orchestrator.state.optical_resource_manager import (
    OpticalAllocation,
    OpticalResourceManager,
    OpticalResourceManagerError,
)


@dataclass(frozen=True)
class KubernetesCandidatePlan:
    """Evaluation result for one Kubernetes compute site."""

    site_id: str
    site_name: str
    node_name: str
    network_access_node: str

    feasible: bool
    reason: str

    available_cpu_m: int
    available_memory_mib: float
    available_gpu: int

    spectrum_slot: SpectrumSlot | None
    fiber_links: tuple[str, ...]
    route: tuple[str, ...]

    gsnr_db: float | None

    compute_score: float | None
    optical_score: float | None
    total_score: float | None


@dataclass(frozen=True)
class KubernetesJointPlan:
    """Selected Kubernetes and optical-network plan."""

    task_id: str
    selected_site_id: str
    selected_node_name: str
    network_access_node: str

    spectrum_slot: SpectrumSlot
    fiber_links: tuple[str, ...]
    route: tuple[str, ...]

    gsnr_db: float
    total_score: float


class KubernetesJointPlannerError(RuntimeError):
    """Raised when no real-resource joint plan can be produced."""

    def __init__(
        self,
        message: str,
        candidate_plans: list[
            KubernetesCandidatePlan
        ] | None = None,
    ) -> None:
        super().__init__(message)
        self.candidate_plans = candidate_plans or []


class KubernetesJointPlanner:
    """
    Select a Kubernetes compute site using live resources and GNPy.

    Kubernetes is the source of truth for compute capacity.
    OpticalResourceManager is the source of truth for spectrum occupancy.
    """

    def __init__(
        self,
        project_root: str | Path,
        resource_discovery: KubernetesResourceDiscovery,
        gnpy_adapter: GNPyAdapter,
        request_builder: GNPyRequestBuilder,
        optical_manager: OpticalResourceManager,
    ) -> None:
        self.project_root = (
            Path(project_root).expanduser().resolve()
        )
        self.resource_discovery = resource_discovery
        self.gnpy_adapter = gnpy_adapter
        self.request_builder = request_builder
        self.optical_manager = optical_manager

    def evaluate(
        self,
        task: RuntimeTaskRequest,
        topology_file: str | Path,
        equipment_file: str | Path,
    ) -> list[KubernetesCandidatePlan]:
        """Evaluate all live Kubernetes compute sites."""

        try:
            sites = self.resource_discovery.discover()
        except KubernetesResourceDiscoveryError as exc:
            raise KubernetesJointPlannerError(
                f"Cannot discover Kubernetes resources: {exc}"
            ) from exc

        return [
            self._evaluate_site(
                task=task,
                site=site,
                topology_file=topology_file,
                equipment_file=equipment_file,
            )
            for site in sites
        ]

    def select(
        self,
        task: RuntimeTaskRequest,
        topology_file: str | Path,
        equipment_file: str | Path,
    ) -> tuple[
        KubernetesJointPlan,
        list[KubernetesCandidatePlan],
    ]:
        """Evaluate candidates and select the highest-scoring plan."""

        candidates = self.evaluate(
            task=task,
            topology_file=topology_file,
            equipment_file=equipment_file,
        )

        feasible = [
            candidate
            for candidate in candidates
            if candidate.feasible
            and candidate.spectrum_slot is not None
            and candidate.gsnr_db is not None
            and candidate.total_score is not None
        ]

        if not feasible:
            raise KubernetesJointPlannerError(
                f"No feasible Kubernetes and optical plan "
                f"for task {task.task_id}.",
                candidate_plans=candidates,
            )

        selected = max(
            feasible,
            key=lambda candidate: candidate.total_score,
        )

        plan = KubernetesJointPlan(
            task_id=task.task_id,
            selected_site_id=selected.site_id,
            selected_node_name=selected.node_name,
            network_access_node=(
                selected.network_access_node
            ),
            spectrum_slot=selected.spectrum_slot,
            fiber_links=selected.fiber_links,
            route=selected.route,
            gsnr_db=float(selected.gsnr_db),
            total_score=float(selected.total_score),
        )

        return plan, candidates

    def reserve_optical(
        self,
        plan: KubernetesJointPlan,
    ) -> OpticalAllocation:
        """Reserve the selected optical path and frequency slot."""

        return self.optical_manager.reserve(
            task_id=plan.task_id,
            links=plan.fiber_links,
            slot=plan.spectrum_slot,
        )

    def release_optical(
        self,
        task_id: str,
    ) -> OpticalAllocation:
        """Release a task's optical allocation."""

        return self.optical_manager.release(task_id)

    def _evaluate_site(
        self,
        task: RuntimeTaskRequest,
        site: KubernetesSiteResources,
        topology_file: str | Path,
        equipment_file: str | Path,
    ) -> KubernetesCandidatePlan:
        """Evaluate one Kubernetes site."""

        available_memory_mib = (
            site.available_memory_bytes
            / (1024 * 1024)
        )

        if not site.ready:
            return self._failed_plan(
                site,
                "Kubernetes node is not Ready",
            )

        if not site.schedulable:
            return self._failed_plan(
                site,
                "Kubernetes node is unschedulable",
            )

        if not site.can_host(
            cpu_m=task.cpu_request_m,
            memory_bytes=task.memory_request_bytes,
            gpu=task.gpu_request,
        ):
            reason = (
                "Insufficient live Kubernetes resources: "
                f"available CPU={site.available_cpu_m}m, "
                f"memory={available_memory_mib:.2f}Mi, "
                f"GPU={site.available_gpu}"
            )

            return self._failed_plan(
                site,
                reason,
            )

        prefix = f"{task.task_id}-{site.site_id}"

        discovery_request = (
            self.project_root
            / "requests"
            / "generated"
            / f"{prefix}-k8s-discovery.json"
        )

        discovery_result = (
            self.project_root
            / "experiments"
            / "results"
            / "kubernetes_joint"
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

            discovery_responses = (
                self.gnpy_adapter.compute_paths(
                    topology_file=topology_file,
                    service_file=discovery_request,
                    equipment_file=equipment_file,
                    output_file=discovery_result,
                    spectrum_policy="first_fit",
                    verbose=False,
                )
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
                    "No fiber links were extracted",
                )

            if len(discovered_slots) != 1:
                return self._failed_plan(
                    site,
                    "GNPy did not return exactly one slot",
                )

            required_m = discovered_slots[0].m

            candidate_slot = (
                self.optical_manager.find_first_fit(
                    links=fiber_links,
                    m=required_m,
                )
            )

            explicit_request = (
                self.project_root
                / "requests"
                / "generated"
                / f"{prefix}-k8s-explicit.json"
            )

            explicit_result = (
                self.project_root
                / "experiments"
                / "results"
                / "kubernetes_joint"
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

            verified_responses = (
                self.gnpy_adapter.compute_paths(
                    topology_file=topology_file,
                    service_file=explicit_request,
                    equipment_file=equipment_file,
                    output_file=explicit_result,
                    spectrum_policy=None,
                    verbose=False,
                )
            )

            if (
                not verified_responses
                or not verified_responses[0].feasible
            ):
                return self._failed_plan(
                    site,
                    "Explicit frequency slot failed QoT validation",
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

            if not self.optical_manager.is_available(
                verified_links,
                candidate_slot,
            ):
                return self._failed_plan(
                    site,
                    "Spectrum became unavailable",
                )

            gsnr_value = verified.metrics.get(
                "SNR-bandwidth"
            )

            try:
                gsnr_db = float(gsnr_value)
            except (TypeError, ValueError):
                return self._failed_plan(
                    site,
                    "GNPy returned no valid GSNR",
                )

            compute_score = self._compute_score(
                site=site,
                task=task,
            )

            optical_score = max(
                min(gsnr_db / 30.0, 1.0),
                0.0,
            )

            # Score is normalized to a 0-100 scale.
            total_score = 100.0 * (
                0.75 * compute_score
                + 0.25 * optical_score
            )

            return KubernetesCandidatePlan(
                site_id=site.site_id,
                site_name=site.site_name,
                node_name=site.node_name,
                network_access_node=(
                    site.network_access_node
                ),
                feasible=True,
                reason="Feasible",
                available_cpu_m=site.available_cpu_m,
                available_memory_mib=(
                    available_memory_mib
                ),
                available_gpu=site.available_gpu,
                spectrum_slot=candidate_slot,
                fiber_links=tuple(verified_links),
                route=tuple(verified.route),
                gsnr_db=gsnr_db,
                compute_score=compute_score,
                optical_score=optical_score,
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
    def _compute_score(
        site: KubernetesSiteResources,
        task: RuntimeTaskRequest,
    ) -> float:
        """
        Calculate normalized residual-resource score.

        A higher value means more resources remain after placement.
        """

        cpu_remaining_ratio = (
            site.available_cpu_m
            - task.cpu_request_m
        ) / max(site.allocatable_cpu_m, 1)

        memory_remaining_ratio = (
            site.available_memory_bytes
            - task.memory_request_bytes
        ) / max(site.allocatable_memory_bytes, 1)

        if task.gpu_request == 0:
            gpu_remaining_ratio = 1.0
        else:
            gpu_remaining_ratio = (
                site.available_gpu
                - task.gpu_request
            ) / max(site.allocatable_gpu, 1)

        cpu_remaining_ratio = max(
            min(cpu_remaining_ratio, 1.0),
            0.0,
        )

        memory_remaining_ratio = max(
            min(memory_remaining_ratio, 1.0),
            0.0,
        )

        gpu_remaining_ratio = max(
            min(gpu_remaining_ratio, 1.0),
            0.0,
        )

        return (
            0.50 * cpu_remaining_ratio
            + 0.35 * memory_remaining_ratio
            + 0.15 * gpu_remaining_ratio
        )

    @staticmethod
    def _failed_plan(
        site: KubernetesSiteResources,
        reason: str,
    ) -> KubernetesCandidatePlan:
        return KubernetesCandidatePlan(
            site_id=site.site_id,
            site_name=site.site_name,
            node_name=site.node_name,
            network_access_node=(
                site.network_access_node
            ),
            feasible=False,
            reason=reason,
            available_cpu_m=site.available_cpu_m,
            available_memory_mib=(
                site.available_memory_bytes
                / (1024 * 1024)
            ),
            available_gpu=site.available_gpu,
            spectrum_slot=None,
            fiber_links=(),
            route=(),
            gsnr_db=None,
            compute_score=None,
            optical_score=None,
            total_score=None,
        )
