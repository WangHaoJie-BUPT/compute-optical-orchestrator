"""Hardened runtime API with API-key authentication and profiles."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from orchestrator.api.application import (
    RuntimeAPISettings,
    create_app as create_base_app,
    get_context,
)
from orchestrator.api.admission_schemas import (
    AdmissionBatchResponse,
    AdmissionEnqueueResponse,
    AdmissionEventResponse,
    AdmissionQueueStatsResponse,
    AdmissionTaskListResponse,
    AdmissionTaskResponse,
    QueuedProfileTaskSubmitRequest,
)
from orchestrator.api.schemas import (
    TaskCancellationResponse,
    TaskSubmissionResponse,
)
from orchestrator.api.secure_schemas import (
    ProfileTaskSubmitRequest,
    TaskEventListResponse,
    TaskEventResponse,
    TaskProfileListResponse,
    TaskProfileResponse,
)
from orchestrator.api.console import mount_console
from orchestrator.models.task_profile import TaskProfileError
from orchestrator.models.admission import (
    AdmissionRecord,
    AdmissionState,
)
from orchestrator.observability.prometheus_metrics import (
    install_prometheus_metrics,
)
from orchestrator.models.task_state import TaskState
from orchestrator.repositories.runtime_task_repository import (
    RuntimeTaskRepositoryError,
)
from orchestrator.repositories.admission_queue_repository import (
    AdmissionQueueRepositoryError,
)
from orchestrator.services.persistent_kubernetes_runtime_service import (
    PersistentKubernetesRuntimeServiceError,
)
from orchestrator.services.task_profile_registry import (
    TaskProfileRegistry,
    TaskProfileRegistryError,
)


def admission_task_response(
    record: AdmissionRecord,
) -> AdmissionTaskResponse:
    return AdmissionTaskResponse(
        task_id=record.task.task_id,
        tenant_id=record.tenant_id,
        priority=record.priority,
        state=record.state.value,
        attempt_count=record.attempt_count,
        not_before=record.not_before,
        claimed_by=record.claimed_by,
        runtime_state=record.runtime_state,
        last_error=record.last_error,
        created_at=record.created_at,
        updated_at=record.updated_at,
        admitted_at=record.admitted_at,
    )


def create_secure_app(
    settings: RuntimeAPISettings,
    api_key: str,
    profile_file: str | Path | None = None,
) -> FastAPI:
    """Create the hardened single-process orchestration API."""

    if len(api_key) < 16:
        raise ValueError(
            "The API key must contain at least 16 characters."
        )

    registry = TaskProfileRegistry(
        profile_file
        or (
            settings.project_root
            / "configs"
            / "task_profiles.json"
        )
    )

    app = create_base_app(settings)
    app.state.task_profile_registry = registry

    @app.middleware("http")
    async def security_middleware(
        request: Request,
        call_next,
    ):
        path = request.url.path

        protected = (
            path.startswith("/api/v1/")
            or path.startswith("/api/v2/")
            or path.startswith("/api/v3/")
        )

        if protected:
            supplied_key = request.headers.get(
                "X-API-Key"
            )

            authenticated = (
                supplied_key is not None
                and secrets.compare_digest(
                    supplied_key,
                    api_key,
                )
            )

            if not authenticated:
                return JSONResponse(
                    status_code=(
                        status.HTTP_401_UNAUTHORIZED
                    ),
                    content={
                        "detail": (
                            "Missing or invalid X-API-Key."
                        )
                    },
                    headers={
                        "WWW-Authenticate": "API-Key",
                    },
                )

        # The legacy endpoint accepted arbitrary image and command.
        if (
            request.method == "POST"
            and path == "/api/v1/tasks"
        ):
            return JSONResponse(
                status_code=status.HTTP_410_GONE,
                content={
                    "detail": (
                        "Arbitrary image/command submission has "
                        "been disabled. Use POST /api/v2/tasks."
                    )
                },
            )

        return await call_next(request)

    def build_profile_task(payload: Any):
        profile = registry.get(payload.profile_id)
        return profile.build_task(
            task_id=payload.task_id,
            source_access_node=payload.source_access_node,
            cpu_request_m=payload.cpu_request_m,
            memory_request_mib=payload.memory_request_mib,
            gpu_request=payload.gpu_request,
            cpu_limit_m=payload.cpu_limit_m,
            memory_limit_mib=payload.memory_limit_mib,
            bandwidth_gbps=payload.bandwidth_gbps,
            duration_seconds=payload.duration_seconds,
            accelerator_resource_name=(
                payload.accelerator_resource_name
            ),
            accelerator_product=payload.accelerator_product,
            accelerator_memory_mib=(
                payload.accelerator_memory_mib
            ),
            accelerator_interconnect=(
                payload.accelerator_interconnect
            ),
            accelerator_partition_mode=(
                payload.accelerator_partition_mode
            ),
        )

    @app.get(
        "/api/v2/task-profiles",
        response_model=TaskProfileListResponse,
        tags=["safe tasks"],
    )
    def list_task_profiles() -> TaskProfileListResponse:
        profiles = registry.list_profiles()

        return TaskProfileListResponse(
            count=len(profiles),
            profiles=[
                TaskProfileResponse(
                    profile_id=profile.profile_id,
                    description=profile.description,
                    default_cpu_request_m=(
                        profile.default_cpu_request_m
                    ),
                    max_cpu_request_m=(
                        profile.max_cpu_request_m
                    ),
                    default_memory_request_mib=(
                        profile.default_memory_request_mib
                    ),
                    max_memory_request_mib=(
                        profile.max_memory_request_mib
                    ),
                    max_gpu_request=(
                        profile.max_gpu_request
                    ),
                    allowed_accelerator_resource_names=list(
                        profile.allowed_accelerator_resource_names
                    ),
                    allowed_accelerator_products=list(
                        profile.allowed_accelerator_products
                    ),
                    allowed_accelerator_interconnects=list(
                        profile.allowed_accelerator_interconnects
                    ),
                    allowed_accelerator_partition_modes=list(
                        profile.allowed_accelerator_partition_modes
                    ),
                    default_bandwidth_gbps=(
                        profile.default_bandwidth_gbps
                    ),
                    max_bandwidth_gbps=(
                        profile.max_bandwidth_gbps
                    ),
                    default_duration_seconds=(
                        profile.default_duration_seconds
                    ),
                    max_duration_seconds=(
                        profile.max_duration_seconds
                    ),
                    allowed_source_access_nodes=list(
                        profile.allowed_source_access_nodes
                    ),
                )
                for profile in profiles
            ],
        )

    @app.post(
        "/api/v3/tasks",
        response_model=AdmissionEnqueueResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["admission queue"],
    )
    def enqueue_profile_task(
        payload: QueuedProfileTaskSubmitRequest,
        request: Request,
    ) -> AdmissionEnqueueResponse:
        context = get_context(request)
        try:
            task = build_profile_task(payload)
        except (
            TaskProfileError,
            TaskProfileRegistryError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

        try:
            record, reused = context.admission_repository.enqueue(
                task=task,
                tenant_id=payload.tenant_id,
                priority=payload.priority,
                max_active_total=(
                    context.settings.admission_max_active_total
                ),
                max_active_per_tenant=(
                    context.settings
                    .admission_max_active_per_tenant
                ),
            )
        except AdmissionQueueRepositoryError as exc:
            message = str(exc)
            response_status = (
                status.HTTP_429_TOO_MANY_REQUESTS
                if (
                    "capacity is exhausted" in message
                    or "queue limit is exhausted" in message
                )
                else status.HTTP_409_CONFLICT
            )
            raise HTTPException(
                status_code=response_status,
                detail=message,
            ) from exc

        response = admission_task_response(record)
        return AdmissionEnqueueResponse(
            **response.model_dump(),
            reused_existing=reused,
        )

    @app.get(
        "/api/v3/queue/stats",
        response_model=AdmissionQueueStatsResponse,
        tags=["admission queue"],
    )
    def admission_queue_stats(
        request: Request,
    ) -> AdmissionQueueStatsResponse:
        stats = get_context(request).admission_repository.stats()
        return AdmissionQueueStatsResponse(
            queued=stats.queued,
            dispatching=stats.dispatching,
            active=stats.active,
            admitted=stats.admitted,
            rejected=stats.rejected,
            cancelled=stats.cancelled,
        )

    @app.get(
        "/api/v3/queue",
        response_model=AdmissionTaskListResponse,
        tags=["admission queue"],
    )
    def list_admission_queue(
        request: Request,
        state_filter: str | None = Query(
            default=None,
            alias="state",
        ),
    ) -> AdmissionTaskListResponse:
        states = None
        if state_filter is not None:
            try:
                states = [AdmissionState(state_filter.upper())]
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Unknown admission state: "
                        f"{state_filter}"
                    ),
                ) from exc
        records = get_context(request).admission_repository.list(
            states
        )
        return AdmissionTaskListResponse(
            count=len(records),
            tasks=[
                admission_task_response(record)
                for record in records
            ],
        )

    @app.post(
        "/api/v3/queue/dispatch",
        response_model=AdmissionBatchResponse,
        tags=["admission queue"],
    )
    def dispatch_admission_queue(
        request: Request,
    ) -> AdmissionBatchResponse:
        context = get_context(request)
        with context.operation_lock:
            result = context.admission_controller.run_once()
        return AdmissionBatchResponse(
            controller_id=result.controller_id,
            lease_acquired=result.lease_acquired,
            claimed_count=result.claimed_count,
            admitted_count=result.admitted_count,
            requeued_count=result.requeued_count,
            rejected_count=result.rejected_count,
            events=[
                AdmissionEventResponse(
                    task_id=event.task_id,
                    tenant_id=event.tenant_id,
                    priority=event.priority,
                    attempt_count=event.attempt_count,
                    action=event.action,
                    final_state=event.final_state,
                    error=event.error,
                )
                for event in result.events
            ],
        )

    @app.get(
        "/api/v3/queue/{task_id}",
        response_model=AdmissionTaskResponse,
        tags=["admission queue"],
    )
    def get_admission_task(
        task_id: str,
        request: Request,
    ) -> AdmissionTaskResponse:
        record = get_context(request).admission_repository.get(
            task_id
        )
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Queued task does not exist: {task_id}",
            )
        return admission_task_response(record)

    @app.post(
        "/api/v3/queue/{task_id}/cancel",
        response_model=AdmissionTaskResponse,
        tags=["admission queue"],
    )
    def cancel_admission_task(
        task_id: str,
        request: Request,
    ) -> AdmissionTaskResponse:
        try:
            record = (
                get_context(request)
                .admission_repository.cancel(task_id)
            )
        except AdmissionQueueRepositoryError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return admission_task_response(record)

    @app.post(
        "/api/v2/tasks",
        response_model=TaskSubmissionResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["safe tasks"],
    )
    def submit_profile_task(
        payload: ProfileTaskSubmitRequest,
        request: Request,
    ) -> TaskSubmissionResponse:
        context = get_context(request)

        try:
            profile = registry.get(payload.profile_id)

            task = profile.build_task(
                task_id=payload.task_id,
                source_access_node=(
                    payload.source_access_node
                ),
                cpu_request_m=payload.cpu_request_m,
                memory_request_mib=(
                    payload.memory_request_mib
                ),
                gpu_request=payload.gpu_request,
                cpu_limit_m=payload.cpu_limit_m,
                memory_limit_mib=(
                    payload.memory_limit_mib
                ),
                bandwidth_gbps=(
                    payload.bandwidth_gbps
                ),
                duration_seconds=(
                    payload.duration_seconds
                ),
                accelerator_resource_name=(
                    payload.accelerator_resource_name
                ),
                accelerator_product=(
                    payload.accelerator_product
                ),
                accelerator_memory_mib=(
                    payload.accelerator_memory_mib
                ),
                accelerator_interconnect=(
                    payload.accelerator_interconnect
                ),
                accelerator_partition_mode=(
                    payload.accelerator_partition_mode
                ),
            )

        except (
            TaskProfileError,
            TaskProfileRegistryError,
        ) as exc:
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

    @app.post(
        "/api/v1/tasks/{task_id}/cancel",
        response_model=TaskCancellationResponse,
        tags=["tasks"],
    )
    @app.post(
        "/api/v2/tasks/{task_id}/cancel",
        response_model=TaskCancellationResponse,
        tags=["safe tasks"],
    )
    def cancel_task(
        task_id: str,
        request: Request,
    ) -> TaskCancellationResponse:
        context = get_context(request)

        with context.operation_lock:
            task_record = context.repository.get_task(
                task_id
            )

            if task_record is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        f"Task does not exist: {task_id}"
                    ),
                )

            if task_record.state in {
                TaskState.RELEASED,
                TaskState.FAILED,
                TaskState.CANCELLED,
            }:
                return TaskCancellationResponse(
                    task_id=task_id,
                    state=task_record.state.value,
                    cancelled=False,
                    job_deleted=False,
                    message=(
                        "Task is already in a terminal state."
                    ),
                )

            if task_record.state in {
                TaskState.COMPLETED,
                TaskState.RELEASING,
                TaskState.RELEASE_FAILED,
            }:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Task execution has already completed "
                        "and resource release is in progress."
                    ),
                )

            execution = (
                context.repository.get_execution(
                    task_id
                )
            )

            job_deleted = False

            try:
                if execution is not None:
                    job_state = (
                        context.kubernetes_adapter
                        .get_job_state(
                            execution.job_name
                        )
                    )

                    if job_state in {
                        "SUCCEEDED",
                        "FAILED",
                    }:
                        result = (
                            context.runtime_service.finalize(
                                task_id=task_id,
                                timeout_seconds=15,
                            )
                        )

                        return TaskCancellationResponse(
                            task_id=task_id,
                            state=(
                                result.task_record
                                .state.value
                            ),
                            cancelled=False,
                            job_deleted=False,
                            message=(
                                "The Kubernetes Job had already "
                                "finished; its real result was finalized."
                            ),
                        )

                    if job_state != "MISSING":
                        context.kubernetes_adapter.delete_job(
                            job_name=execution.job_name,
                            ignore_not_found=True,
                        )
                        job_deleted = True

                    context.repository.mark_execution_cancelled(
                        task_id
                    )

                final_record = (
                    context.runtime_service
                    .release_with_outcome(
                        task_id=task_id,
                        final_state=TaskState.CANCELLED,
                        failure_reason=(
                            "Task cancelled through the API."
                        ),
                    )
                )

            except (
                PersistentKubernetesRuntimeServiceError,
                RuntimeTaskRepositoryError,
            ) as exc:
                raise HTTPException(
                    status_code=(
                        status.HTTP_500_INTERNAL_SERVER_ERROR
                    ),
                    detail=(
                        f"Failed to cancel task "
                        f"{task_id}: {exc}"
                    ),
                ) from exc

            return TaskCancellationResponse(
                task_id=task_id,
                state=final_record.state.value,
                cancelled=True,
                job_deleted=job_deleted,
                message="Task cancelled successfully.",
            )

    @app.get(
        "/api/v2/tasks/{task_id}/events",
        response_model=TaskEventListResponse,
        tags=["safe tasks"],
    )
    def list_task_events(
        task_id: str,
        request: Request,
        limit: int = Query(
            default=200,
            ge=1,
            le=1000,
        ),
        after_event_id: int | None = Query(
            default=None,
            ge=0,
        ),
    ) -> TaskEventListResponse:
        """Return the immutable lifecycle history of one task."""

        context = get_context(request)

        task_record = context.repository.get_task(
            task_id
        )

        if task_record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task does not exist: {task_id}",
            )

        records = (
            context.event_repository.list_for_task(
                task_id,
                limit=limit,
                after_event_id=after_event_id,
            )
        )

        events = [
            TaskEventResponse(
                event_id=record.event_id,
                task_id=record.task_id,
                event_type=record.event_type,
                previous_state=(
                    record.previous_state.value
                    if record.previous_state is not None
                    else None
                ),
                new_state=(
                    record.new_state.value
                    if record.new_state is not None
                    else None
                ),
                source=record.source,
                message=record.message,
                details=record.details,
                created_at=record.created_at,
            )
            for record in records
        ]

        return TaskEventListResponse(
            task_id=task_id,
            count=len(events),
            next_after_event_id=(
                events[-1].event_id
                if events
                else after_event_id
            ),
            events=events,
        )

    install_prometheus_metrics(app)
    mount_console(
        app,
        settings.project_root / "frontend" / "dist",
    )

    def custom_openapi():
        if app.openapi_schema is not None:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )

        components = schema.setdefault(
            "components",
            {},
        )

        security_schemes = components.setdefault(
            "securitySchemes",
            {},
        )

        security_schemes["XAPIKey"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
        }

        paths = schema.get("paths", {})

        # Hide the unsafe legacy submission operation.
        legacy_path = paths.get("/api/v1/tasks")

        if isinstance(legacy_path, dict):
            legacy_path.pop("post", None)

        for path, path_item in paths.items():
            if not (
                path.startswith("/api/v1/")
                or path.startswith("/api/v2/")
                or path.startswith("/api/v3/")
            ):
                continue

            if not isinstance(path_item, dict):
                continue

            for method in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
            }:
                operation = path_item.get(method)

                if isinstance(operation, dict):
                    operation["security"] = [
                        {
                            "XAPIKey": [],
                        }
                    ]

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi

    return app
