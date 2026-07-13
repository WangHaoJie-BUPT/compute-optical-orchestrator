"""End-to-end compute-optical and Kubernetes execution service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.adapters.kubernetes_adapter import (
    KubernetesAdapter,
    KubernetesAdapterError,
    KubernetesJobResult,
)
from orchestrator.models.execution import KubernetesExecutionRecord
from orchestrator.models.resources import TaskRequest
from orchestrator.models.task_state import TaskRecord, TaskState
from orchestrator.repositories.execution_repository import (
    ExecutionRepository,
    ExecutionRepositoryError,
)
from orchestrator.repositories.task_repository import TaskRepository
from orchestrator.services.persistent_orchestration_service import (
    PersistentOrchestrationService,
    PersistentOrchestrationServiceError,
    SubmitResult,
)


class KubernetesExecutionServiceError(RuntimeError):
    """Raised when end-to-end execution fails."""


@dataclass(frozen=True)
class EndToEndExecutionResult:
    """Result of allocation, Kubernetes execution and resource release."""

    submit_result: SubmitResult
    job_result: KubernetesJobResult
    execution_record: KubernetesExecutionRecord
    final_task_record: TaskRecord


class KubernetesExecutionService:
    """
    Connect persistent joint allocation to Kubernetes Job execution.

    Successful workflow:
        PENDING -> PLANNING -> RESERVED -> SUBMITTING
        -> RUNNING -> COMPLETED -> RELEASING -> RELEASED
    """

    def __init__(
        self,
        persistent_service: PersistentOrchestrationService,
        task_repository: TaskRepository,
        execution_repository: ExecutionRepository,
        kubernetes_adapter: KubernetesAdapter,
    ) -> None:
        self.persistent_service = persistent_service
        self.task_repository = task_repository
        self.execution_repository = execution_repository
        self.kubernetes_adapter = kubernetes_adapter

    def execute(
        self,
        task: TaskRequest,
        topology_file: str | Path,
        equipment_file: str | Path,
        image: str,
        shell_command: str,
        image_pull_policy: str = "Never",
        timeout_seconds: int = 120,
    ) -> EndToEndExecutionResult:
        """Allocate resources, run a Kubernetes Job and release resources."""

        submit_result = self.persistent_service.submit(
            task=task,
            topology_file=topology_file,
            equipment_file=equipment_file,
        )

        record = submit_result.task_record

        if record.state == TaskState.BLOCKED:
            raise KubernetesExecutionServiceError(
                f"Task was blocked during joint allocation: "
                f"{record.failure_reason}"
            )

        allocation = submit_result.allocation

        if allocation is None:
            raise KubernetesExecutionServiceError(
                f"Task {task.task_id} has no active joint allocation."
            )

        existing_execution = self.execution_repository.get(
            task.task_id
        )

        if existing_execution is not None:
            raise KubernetesExecutionServiceError(
                f"Task already has a Kubernetes execution record: "
                f"{task.task_id}, phase={existing_execution.phase}"
            )

        site_id = allocation.site_id
        job_name = self.kubernetes_adapter.job_name_for_task(
            task.task_id
        )

        self.task_repository.set_state(
            task_id=task.task_id,
            state=TaskState.SUBMITTING,
            selected_site=site_id,
        )

        job_created = False

        try:
            self.kubernetes_adapter.ensure_namespace()

            created_job_name = self.kubernetes_adapter.create_job(
                task_id=task.task_id,
                site_id=site_id,
                image=image,
                shell_command=shell_command,
                cpu_request="100m",
                memory_request="32Mi",
                cpu_limit="200m",
                memory_limit="64Mi",
                image_pull_policy=image_pull_policy,
            )

            job_created = True

            if created_job_name != job_name:
                raise KubernetesExecutionServiceError(
                    f"Unexpected Job name: {created_job_name}"
                )

            self.execution_repository.create(
                task_id=task.task_id,
                job_name=job_name,
                namespace=self.kubernetes_adapter.namespace,
                requested_site=site_id,
            )

            self.execution_repository.update_phase(
                task_id=task.task_id,
                phase="RUNNING",
            )

            self.task_repository.set_state(
                task_id=task.task_id,
                state=TaskState.RUNNING,
                selected_site=site_id,
            )

            job_result = (
                self.kubernetes_adapter.wait_for_completion(
                    task_id=task.task_id,
                    job_name=job_name,
                    site_id=site_id,
                    timeout_seconds=timeout_seconds,
                )
            )

            final_execution_phase = (
                "SUCCEEDED"
                if job_result.succeeded
                else "FAILED"
            )

            self.execution_repository.save_result(
                task_id=task.task_id,
                pod_name=job_result.pod_name,
                node_name=job_result.node_name,
                node_site_label=job_result.node_site_label,
                phase=final_execution_phase,
                succeeded=job_result.succeeded,
                logs=job_result.logs,
            )

            if job_result.node_site_label != site_id:
                raise KubernetesExecutionServiceError(
                    f"Job ran on site {job_result.node_site_label}, "
                    f"but the orchestrator selected {site_id}."
                )

            if job_result.succeeded:
                self.task_repository.set_state(
                    task_id=task.task_id,
                    state=TaskState.COMPLETED,
                    selected_site=site_id,
                )

                final_task_record = (
                    self.persistent_service.release(
                        task.task_id
                    )
                )

            else:
                failure_reason = (
                    f"Kubernetes Job failed: {job_result.job_name}"
                )

                self.task_repository.set_state(
                    task_id=task.task_id,
                    state=TaskState.FAILED,
                    selected_site=site_id,
                    failure_reason=failure_reason,
                )

                # Free the allocated resources.
                self.persistent_service.release(
                    task.task_id
                )

                # Preserve FAILED as the final task outcome after release.
                self.task_repository.set_state(
                    task_id=task.task_id,
                    state=TaskState.FAILED,
                    selected_site=site_id,
                    failure_reason=failure_reason,
                )

                final_task_record = self.task_repository.get_task(
                    task.task_id
                )

                if final_task_record is None:
                    raise KubernetesExecutionServiceError(
                        f"Task disappeared after failure: "
                        f"{task.task_id}"
                    )

            execution_record = self.execution_repository.get(
                task.task_id
            )

            if execution_record is None:
                raise KubernetesExecutionServiceError(
                    f"Execution record disappeared: {task.task_id}"
                )

            return EndToEndExecutionResult(
                submit_result=submit_result,
                job_result=job_result,
                execution_record=execution_record,
                final_task_record=final_task_record,
            )

        except (
            KubernetesAdapterError,
            ExecutionRepositoryError,
            PersistentOrchestrationServiceError,
            KubernetesExecutionServiceError,
        ) as exc:
            # Stop a possibly running Job before freeing its resources.
            if job_created:
                try:
                    self.kubernetes_adapter.delete_job(
                        job_name=job_name,
                        ignore_not_found=True,
                    )
                except KubernetesAdapterError:
                    pass

            current_record = self.task_repository.get_task(
                task.task_id
            )

            if (
                current_record is not None
                and current_record.state
                not in {
                    TaskState.RELEASED,
                    TaskState.FAILED,
                }
            ):
                try:
                    self.persistent_service.release(
                        task.task_id
                    )
                except PersistentOrchestrationServiceError:
                    pass

            self.task_repository.set_state(
                task_id=task.task_id,
                state=TaskState.FAILED,
                selected_site=site_id,
                failure_reason=str(exc),
            )

            raise KubernetesExecutionServiceError(
                f"End-to-end execution failed for "
                f"{task.task_id}: {exc}"
            ) from exc
