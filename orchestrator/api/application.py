"""FastAPI application for the compute-optical orchestrator."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import PlainTextResponse

from orchestrator.adapters.gnpy_adapter import GNPyAdapter
from orchestrator.adapters.gnpy_request_builder import (
    GNPyRequestBuilder,
)
from orchestrator.adapters.kubernetes_adapter import (
    KubernetesAdapter,
    KubernetesAdapterError,
)
from orchestrator.adapters.kubernetes_api_adapter import (
    KubernetesApiAdapter,
)
from orchestrator.adapters.kubernetes_resource_discovery import (
    KubernetesResourceDiscovery,
    KubernetesResourceDiscoveryError,
)
from orchestrator.api.schemas import (
    HealthResponse,
    KubernetesExecutionResponse,
    OpticalAllocationResponse,
    ReconciliationResponse,
    TaskDetailResponse,
    TaskListResponse,
    TaskSubmissionResponse,
    TaskSubmitRequest,
)
from orchestrator.controllers.runtime_reconciliation_controller import (
    ReconciliationBatchResult,
    RuntimeReconciliationController,
)
from orchestrator.controllers.admission_controller import (
    AdmissionBatchResult,
    AdmissionController,
)
from orchestrator.models.runtime_persistence import (
    RuntimeExecutionRecord,
    RuntimeOpticalRecord,
    RuntimeTaskRecord,
)
from orchestrator.models.task_state import TaskState
from orchestrator.repositories.controller_lease_repository import (
    ControllerLeaseRepository,
)
from orchestrator.repositories.admission_queue_repository import (
    AdmissionQueueRepository,
)
from orchestrator.repositories.runtime_task_repository import (
    RuntimeTaskRepository,
    RuntimeTaskRepositoryError,
)
from orchestrator.repositories.task_event_repository import (
    TaskEventRepository,
)
from orchestrator.services.kubernetes_joint_planner import (
    KubernetesJointPlanner,
)
from orchestrator.services.persistent_kubernetes_runtime_service import (
    PersistentKubernetesRuntimeService,
    PersistentKubernetesRuntimeServiceError,
)
from orchestrator.state.optical_resource_manager import (
    OpticalResourceManager,
)
from orchestrator.storage.database import Database


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RuntimeAPISettings:
    """Runtime settings for the API process."""

    project_root: Path = PROJECT_ROOT

    database_file: Path = (
        PROJECT_ROOT
        / "runtime"
        / "runtime-orchestrator.db"
    )

    kubernetes_context: str = "kind-compute-optical"
    kubernetes_namespace: str = "compute-optical"

    controller_interval_seconds: float = 2.0
    controller_lease_seconds: float = 30.0

    start_controller: bool = True

    admission_aging_seconds: float = 60.0
    admission_dispatch_timeout_seconds: float = 300.0
    admission_lease_seconds: float = 300.0
    admission_max_active_total: int = 1000
    admission_max_active_per_tenant: int = 100
    admission_max_dispatch_per_cycle: int = 1

    topology_file: Path = (
        PROJECT_ROOT
        / "topologies"
        / "gnpy_demo"
        / "meshTopologyExampleV2.xls"
    )

    equipment_file: Path = (
        PROJECT_ROOT
        / "topologies"
        / "gnpy_demo"
        / "eqpt_config.json"
    )

    request_template_file: Path = (
        PROJECT_ROOT
        / "requests"
        / "gnpy_demo"
        / "services-single.json"
    )

    site_mapping_file: Path = (
        PROJECT_ROOT
        / "configs"
        / "site_mapping.json"
    )


@dataclass
class RuntimeAPIContext:
    """Shared mutable runtime owned by one API process."""

    settings: RuntimeAPISettings

    database: Database
    repository: RuntimeTaskRepository
    event_repository: TaskEventRepository
    lease_repository: ControllerLeaseRepository
    admission_repository: AdmissionQueueRepository

    kubernetes_adapter: KubernetesAdapter
    resource_discovery: KubernetesResourceDiscovery

    optical_manager: OpticalResourceManager
    runtime_service: PersistentKubernetesRuntimeService
    controller: RuntimeReconciliationController
    admission_controller: AdmissionController

    operation_lock: threading.RLock = field(
        default_factory=threading.RLock
    )

    status_lock: threading.Lock = field(
        default_factory=threading.Lock
    )

    stop_event: threading.Event = field(
        default_factory=threading.Event
    )

    controller_thread: threading.Thread | None = None

    restored_allocations: int = 0
    last_reconciliation_at: str | None = None
    last_controller_error: str | None = None
    last_reconciliation: ReconciliationBatchResult | None = None
    recovered_admission_dispatches: int = 0
    last_admission: AdmissionBatchResult | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_runtime_context(
    settings: RuntimeAPISettings,
) -> RuntimeAPIContext:
    """Construct the complete shared runtime."""

    database_file = (
        settings.database_file.expanduser().resolve()
    )
    database_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    database = Database(database_file)
    database.initialize()

    repository = RuntimeTaskRepository(database)
    repository.initialize()

    event_repository = TaskEventRepository(database)
    event_repository.initialize()

    lease_repository = ControllerLeaseRepository(
        database
    )
    lease_repository.initialize()

    admission_repository = AdmissionQueueRepository(
        database,
        aging_seconds=settings.admission_aging_seconds,
        dispatch_timeout_seconds=(
            settings.admission_dispatch_timeout_seconds
        ),
    )
    recovered_admission_dispatches = (
        admission_repository.initialize()
    )

    kubernetes_adapter = KubernetesApiAdapter(
        namespace=settings.kubernetes_namespace,
        context=settings.kubernetes_context,
    )

    kubernetes_adapter.verify_cluster()
    kubernetes_adapter.ensure_namespace()

    resource_discovery = KubernetesResourceDiscovery(
        kubernetes_adapter=kubernetes_adapter,
        site_mapping_file=settings.site_mapping_file,
    )

    optical_manager = OpticalResourceManager(
        spectrum_lower_index=-288,
        spectrum_upper_index=480,
    )

    planner = KubernetesJointPlanner(
        project_root=settings.project_root,
        resource_discovery=resource_discovery,
        gnpy_adapter=GNPyAdapter(
            project_root=settings.project_root,
        ),
        request_builder=GNPyRequestBuilder(
            settings.request_template_file,
        ),
        optical_manager=optical_manager,
    )

    runtime_service = (
        PersistentKubernetesRuntimeService(
            repository=repository,
            event_repository=event_repository,
            planner=planner,
            kubernetes_adapter=kubernetes_adapter,
        )
    )

    controller = RuntimeReconciliationController(
        repository=repository,
        runtime_service=runtime_service,
        kubernetes_adapter=kubernetes_adapter,
        lease_repository=lease_repository,
        lease_seconds=(
            settings.controller_lease_seconds
        ),
    )

    admission_controller = AdmissionController(
        queue_repository=admission_repository,
        runtime_repository=repository,
        runtime_service=runtime_service,
        lease_repository=lease_repository,
        topology_file=settings.topology_file,
        equipment_file=settings.equipment_file,
        lease_seconds=settings.admission_lease_seconds,
        max_dispatch_per_cycle=(
            settings.admission_max_dispatch_per_cycle
        ),
    )

    context = RuntimeAPIContext(
        settings=settings,
        database=database,
        repository=repository,
        event_repository=event_repository,
        lease_repository=lease_repository,
        admission_repository=admission_repository,
        kubernetes_adapter=kubernetes_adapter,
        resource_discovery=resource_discovery,
        optical_manager=optical_manager,
        runtime_service=runtime_service,
        controller=controller,
        admission_controller=admission_controller,
        recovered_admission_dispatches=(
            recovered_admission_dispatches
        ),
    )

    context.restored_allocations = (
        runtime_service.recover()
    )

    return context


def controller_loop(
    context: RuntimeAPIContext,
) -> None:
    """Run reconciliation until shutdown is requested."""

    interval = max(
        context.settings.controller_interval_seconds,
        0.5,
    )

    while not context.stop_event.is_set():
        try:
            # Submission, recovery and reconciliation all touch the
            # same in-memory OpticalResourceManager.
            with context.operation_lock:
                admission_result = (
                    context.admission_controller.run_once()
                )
                result = context.controller.run_once()

            with context.status_lock:
                context.last_reconciliation = result
                context.last_admission = admission_result
                context.last_reconciliation_at = utc_now()
                context.last_controller_error = None

        except Exception as exc:
            # The thread remains alive so a temporary Kubernetes or
            # database failure does not permanently stop reconciliation.
            with context.status_lock:
                context.last_controller_error = (
                    f"{type(exc).__name__}: {exc}"
                )
                context.last_reconciliation_at = utc_now()

        context.stop_event.wait(interval)


def start_controller(
    context: RuntimeAPIContext,
) -> None:
    """Start the background reconciliation thread."""

    if not context.settings.start_controller:
        return

    thread = threading.Thread(
        target=controller_loop,
        args=(context,),
        name="runtime-reconciliation-controller",
        daemon=True,
    )

    context.controller_thread = thread
    thread.start()


def stop_controller(
    context: RuntimeAPIContext,
) -> None:
    """Request controller shutdown and wait briefly."""

    context.stop_event.set()

    thread = context.controller_thread

    if thread is not None and thread.is_alive():
        thread.join(timeout=15)


def get_context(
    request: Request,
) -> RuntimeAPIContext:
    context = getattr(
        request.app.state,
        "runtime_context",
        None,
    )

    if context is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Runtime context is not initialized.",
        )

    return context


def optical_response(
    record: RuntimeOpticalRecord | None,
) -> OpticalAllocationResponse | None:
    if record is None:
        return None

    return OpticalAllocationResponse(
        site_id=record.site_id,
        node_name=record.node_name,
        network_access_node=record.network_access_node,
        n=record.slot.n,
        m=record.slot.m,
        fiber_links=list(record.fiber_links),
        route=list(record.route),
        gsnr_db=record.gsnr_db,
        total_score=record.total_score,
    )


def execution_response(
    record: RuntimeExecutionRecord | None,
) -> KubernetesExecutionResponse | None:
    if record is None:
        return None

    return KubernetesExecutionResponse(
        job_name=record.job_name,
        namespace=record.namespace,
        requested_site_id=(
            record.requested_site_id
        ),
        pod_name=record.pod_name,
        node_name=record.node_name,
        node_site_label=record.node_site_label,
        phase=record.phase,
        succeeded=record.succeeded,
        logs_available=bool(record.logs),
    )


def task_response(
    context: RuntimeAPIContext,
    record: RuntimeTaskRecord,
) -> TaskDetailResponse:
    task = record.task

    return TaskDetailResponse(
        task_id=task.task_id,
        state=record.state.value,
        version=record.version,
        source_access_node=task.source_access_node,
        cpu_request_m=task.cpu_request_m,
        memory_request_mib=task.memory_request_mib,
        gpu_request=task.gpu_request,
        accelerator_resource_name=(
            task.accelerator_resource_name
        ),
        accelerator_product=task.accelerator_product,
        accelerator_memory_mib=(
            task.accelerator_memory_mib
        ),
        accelerator_interconnect=(
            task.accelerator_interconnect
        ),
        accelerator_partition_mode=(
            task.accelerator_partition_mode
        ),
        cpu_limit_m=task.effective_cpu_limit_m,
        memory_limit_mib=(
            task.effective_memory_limit_mib
        ),
        bandwidth_gbps=task.bandwidth_gbps,
        image=task.image,
        selected_site_id=record.selected_site_id,
        selected_node_name=record.selected_node_name,
        failure_reason=record.failure_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
        optical=optical_response(
            context.repository.get_optical(
                task.task_id
            )
        ),
        execution=execution_response(
            context.repository.get_execution(
                task.task_id
            )
        ),
    )


def batch_result_response(
    result: ReconciliationBatchResult,
) -> ReconciliationResponse:
    return ReconciliationResponse(
        controller_id=result.controller_id,
        lease_acquired=result.lease_acquired,
        scanned_count=result.scanned_count,
        changed_count=result.changed_count,
        error_count=result.error_count,
        events=[
            {
                "task_id": event.task_id,
                "previous_state": event.previous_state,
                "job_state": event.job_state,
                "action": event.action,
                "final_state": event.final_state,
                "error": event.error,
            }
            for event in result.events
        ],
    )


def create_app(
    settings: RuntimeAPISettings | None = None,
) -> FastAPI:
    """Create one single-process orchestration API."""

    resolved_settings = (
        settings
        if settings is not None
        else RuntimeAPISettings()
    )

    @asynccontextmanager
    async def lifespan(
        app: FastAPI,
    ) -> Iterator[None]:
        context = build_runtime_context(
            resolved_settings
        )

        app.state.runtime_context = context

        start_controller(context)

        try:
            yield
        finally:
            stop_controller(context)

    app = FastAPI(
        title="Compute-Optical Orchestrator API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get(
        "/healthz",
        response_model=HealthResponse,
        tags=["system"],
    )
    def health(
        request: Request,
    ) -> HealthResponse:
        context = get_context(request)

        thread = context.controller_thread
        queue_stats = context.admission_repository.stats()

        with context.status_lock:
            return HealthResponse(
                status="ok",
                controller_alive=(
                    bool(
                        thread is not None
                        and thread.is_alive()
                    )
                    if context.settings.start_controller
                    else True
                ),
                restored_allocations=(
                    context.restored_allocations
                ),
                recovered_admission_dispatches=(
                    context.recovered_admission_dispatches
                ),
                admission_queue_active=queue_stats.active,
                last_reconciliation_at=(
                    context.last_reconciliation_at
                ),
                last_controller_error=(
                    context.last_controller_error
                ),
            )

    @app.get(
        "/readyz",
        tags=["system"],
    )
    def readiness(
        request: Request,
    ) -> dict[str, Any]:
        context = get_context(request)

        try:
            context.kubernetes_adapter.verify_cluster()
            sites = (
                context.resource_discovery.discover()
            )
        except (
            KubernetesAdapterError,
            KubernetesResourceDiscoveryError,
        ) as exc:
            raise HTTPException(
                status_code=(
                    status.HTTP_503_SERVICE_UNAVAILABLE
                ),
                detail=str(exc),
            ) from exc

        return {
            "status": "ready",
            "kubernetes_context": (
                context.settings.kubernetes_context
            ),
            "compute_site_count": len(sites),
        }

    @app.post(
        "/api/v1/tasks",
        response_model=TaskSubmissionResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["tasks"],
    )
    def submit_task(
        payload: TaskSubmitRequest,
        request: Request,
    ) -> TaskSubmissionResponse:
        context = get_context(request)

        try:
            task = payload.to_domain()
        except ValueError as exc:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                detail=str(exc),
            ) from exc

        existing = context.repository.get_task(
            task.task_id
        )

        if (
            existing is not None
            and existing.task != task
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Task ID {task.task_id!r} already "
                    "exists with different parameters."
                ),
            )

        try:
            with context.operation_lock:
                result = context.runtime_service.submit(
                    task=task,
                    topology_file=(
                        context.settings.topology_file
                    ),
                    equipment_file=(
                        context.settings.equipment_file
                    ),
                )

        except PersistentKubernetesRuntimeServiceError as exc:
            current = context.repository.get_task(
                task.task_id
            )

            response_status = (
                status.HTTP_409_CONFLICT
                if (
                    current is not None
                    and current.state
                    == TaskState.BLOCKED
                )
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            )

            raise HTTPException(
                status_code=response_status,
                detail={
                    "task_id": task.task_id,
                    "state": (
                        current.state.value
                        if current is not None
                        else None
                    ),
                    "error": str(exc),
                },
            ) from exc

        return TaskSubmissionResponse(
            task_id=result.task_record.task.task_id,
            state=result.task_record.state.value,
            version=result.task_record.version,
            selected_site_id=(
                result.task_record.selected_site_id
            ),
            selected_node_name=(
                result.task_record.selected_node_name
            ),
            reused_existing=result.reused_existing,
        )

    @app.get(
        "/api/v1/tasks",
        response_model=TaskListResponse,
        tags=["tasks"],
    )
    def list_tasks(
        request: Request,
        state_filter: str | None = Query(
            default=None,
            alias="state",
        ),
    ) -> TaskListResponse:
        context = get_context(request)

        records = context.repository.list_tasks()

        if state_filter is not None:
            normalized = state_filter.upper()

            try:
                requested_state = TaskState(normalized)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Unknown task state: "
                        f"{state_filter}"
                    ),
                ) from exc

            records = [
                record
                for record in records
                if record.state == requested_state
            ]

        responses = [
            task_response(context, record)
            for record in records
        ]

        return TaskListResponse(
            count=len(responses),
            tasks=responses,
        )

    @app.get(
        "/api/v1/tasks/{task_id}",
        response_model=TaskDetailResponse,
        tags=["tasks"],
    )
    def get_task(
        task_id: str,
        request: Request,
    ) -> TaskDetailResponse:
        context = get_context(request)

        record = context.repository.get_task(task_id)

        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task does not exist: {task_id}",
            )

        return task_response(context, record)

    @app.get(
        "/api/v1/tasks/{task_id}/logs",
        response_class=PlainTextResponse,
        tags=["tasks"],
    )
    def get_task_logs(
        task_id: str,
        request: Request,
    ) -> PlainTextResponse:
        context = get_context(request)

        task = context.repository.get_task(task_id)

        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task does not exist: {task_id}",
            )

        execution = (
            context.repository.get_execution(task_id)
        )

        if execution is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The task does not yet have a "
                    "Kubernetes execution."
                ),
            )

        if execution.logs is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Execution logs are not available yet."
                ),
            )

        return PlainTextResponse(
            execution.logs,
            media_type="text/plain",
        )

    @app.get(
        "/api/v1/sites",
        tags=["resources"],
    )
    def list_sites(
        request: Request,
    ) -> dict[str, Any]:
        context = get_context(request)

        try:
            sites = context.resource_discovery.discover()
        except KubernetesResourceDiscoveryError as exc:
            raise HTTPException(
                status_code=(
                    status.HTTP_503_SERVICE_UNAVAILABLE
                ),
                detail=str(exc),
            ) from exc

        return {
            "count": len(sites),
            "sites": [
                site.to_dict()
                for site in sites
            ],
        }

    @app.post(
        "/api/v1/controller/reconcile",
        response_model=ReconciliationResponse,
        tags=["controller"],
    )
    def reconcile_once(
        request: Request,
    ) -> ReconciliationResponse:
        context = get_context(request)

        with context.operation_lock:
            result = context.controller.run_once()

        with context.status_lock:
            context.last_reconciliation = result
            context.last_reconciliation_at = utc_now()
            context.last_controller_error = None

        return batch_result_response(result)

    return app


app = create_app()
