"""Persistent Kubernetes-native compute-optical orchestration."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from orchestrator.adapters.kubernetes_adapter import (
    KubernetesAdapter,
    KubernetesAdapterError,
    KubernetesJobResult,
)
from orchestrator.models.runtime_persistence import (
    RuntimeExecutionRecord,
    RuntimeOpticalRecord,
    RuntimeTaskRecord,
)
from orchestrator.models.runtime_task import RuntimeTaskRequest
from orchestrator.models.task_state import TaskState
from orchestrator.repositories.runtime_task_repository import (
    RuntimeTaskRepository,
    RuntimeTaskRepositoryError,
)
from orchestrator.repositories.task_event_repository import (
    TaskEventRepository,
)
from orchestrator.services.kubernetes_joint_planner import (
    KubernetesJointPlan,
    KubernetesJointPlanner,
    KubernetesJointPlannerError,
)
from orchestrator.state.optical_resource_manager import (
    OpticalResourceManagerError,
)


logger = logging.getLogger(__name__)


class PersistentKubernetesRuntimeServiceError(RuntimeError):
    """Raised when persistent runtime orchestration fails."""


@dataclass(frozen=True)
class RuntimeSubmissionResult:
    """Result of planning, reservation and Job submission."""

    task_record: RuntimeTaskRecord
    optical_record: RuntimeOpticalRecord | None
    execution_record: RuntimeExecutionRecord | None
    reused_existing: bool


@dataclass(frozen=True)
class RuntimeFinalizationResult:
    """Result of Job completion and optical release."""

    task_record: RuntimeTaskRecord
    execution_record: RuntimeExecutionRecord
    job_result: KubernetesJobResult


class PersistentKubernetesRuntimeService:
    """
    Kubernetes-native orchestration with SQLite persistence.

    Compute capacity is not reserved in a local ResourceManager.
    Kubernetes requests and the Kubernetes scheduler are the source
    of truth for compute capacity.
    """

    def __init__(
        self,
        repository: RuntimeTaskRepository,
        event_repository: TaskEventRepository,
        planner: KubernetesJointPlanner,
        kubernetes_adapter: KubernetesAdapter,
    ) -> None:
        self.repository = repository
        self.event_repository = event_repository
        self.planner = planner
        self.kubernetes_adapter = kubernetes_adapter

    def submit(
        self,
        task: RuntimeTaskRequest,
        topology_file: str | Path,
        equipment_file: str | Path,
        preselected_plan: KubernetesJointPlan | None = None,
    ) -> RuntimeSubmissionResult:
        """Plan resources, reserve spectrum and submit a Job."""

        existing = self.repository.get_task(task.task_id)

        if existing is not None:
            if existing.task != task:
                raise PersistentKubernetesRuntimeServiceError(
                    f"Task ID {task.task_id!r} already exists "
                    "with different task parameters."
                )

            return RuntimeSubmissionResult(
                task_record=existing,
                optical_record=self.repository.get_optical(
                    task.task_id
                ),
                execution_record=self.repository.get_execution(
                    task.task_id
                ),
                reused_existing=True,
            )

        if (
            preselected_plan is not None
            and preselected_plan.task_id != task.task_id
        ):
            raise PersistentKubernetesRuntimeServiceError(
                "Preselected plan task ID does not match the task."
            )

        self.repository.create_task(task)

        self.repository.set_state(
            task_id=task.task_id,
            state=TaskState.PLANNING,
        )

        try:
            if preselected_plan is None:
                plan, _ = self.planner.select(
                    task=task,
                    topology_file=topology_file,
                    equipment_file=equipment_file,
                )
            else:
                plan = preselected_plan
        except KubernetesJointPlannerError as exc:
            blocked = self.repository.set_state(
                task_id=task.task_id,
                state=TaskState.BLOCKED,
                failure_reason=str(exc),
            )

            try:
                self.event_repository.append(
                    task_id=task.task_id,
                    event_type="JOINT_PLANNING_BLOCKED",
                    source=(
                        "persistent-kubernetes-runtime-service"
                    ),
                    message=(
                        "Joint planning produced no selectable "
                        "candidate."
                    ),
                    details={
                        "failure_reason": str(exc),
                        "candidate_plans": [
                            asdict(candidate)
                            for candidate in exc.candidate_plans
                        ],
                    },
                    previous_state=TaskState.PLANNING,
                    new_state=blocked.state,
                )
            except Exception:
                logger.exception(
                    "failed to persist blocked planning audit "
                    "task_id=%s",
                    task.task_id,
                )

            raise PersistentKubernetesRuntimeServiceError(
                f"Task {task.task_id} was blocked: {exc}"
            ) from exc

        optical_reserved = False
        job_created = False

        try:
            self.planner.reserve_optical(plan)
            optical_reserved = True

            self.repository.save_reserved_plan(plan)

            self.repository.set_state(
                task_id=task.task_id,
                state=TaskState.SUBMITTING,
                selected_site_id=plan.selected_site_id,
                selected_node_name=plan.selected_node_name,
            )

            self.kubernetes_adapter.ensure_namespace()

            job_name = (
                self.kubernetes_adapter.job_name_for_task(
                    task.task_id
                )
            )

            current_job_state = (
                self.kubernetes_adapter.get_job_state(
                    job_name
                )
            )

            if current_job_state == "MISSING":
                created_job_name = (
                    self.kubernetes_adapter.create_job(
                        task_id=task.task_id,
                        site_id=plan.selected_site_id,
                        image=task.image,
                        shell_command=task.shell_command,
                        cpu_request=(
                            task.cpu_request_quantity
                        ),
                        memory_request=(
                            task.memory_request_quantity
                        ),
                        cpu_limit=task.cpu_limit_quantity,
                        memory_limit=(
                            task.memory_limit_quantity
                        ),
                        gpu_request=task.gpu_request,
                        gpu_resource_name=(
                            task.accelerator_resource_name
                        ),
                        image_pull_policy="Never",
                        node_name=plan.selected_node_name,
                        accelerator_product=(
                            task.accelerator_product
                        ),
                        accelerator_memory_mib=(
                            task.accelerator_memory_mib
                        ),
                        accelerator_interconnect=(
                            task.accelerator_interconnect
                        ),
                        accelerator_partition_mode=(
                            task.accelerator_partition_mode
                        ),
                    )
                )

                job_created = True

                if created_job_name != job_name:
                    raise (
                        PersistentKubernetesRuntimeServiceError(
                            "Kubernetes returned an unexpected "
                            f"Job name: {created_job_name}"
                        )
                    )

            task_record = self.repository.save_job(
                task_id=task.task_id,
                job_name=job_name,
                namespace=(
                    self.kubernetes_adapter.namespace
                ),
                requested_site_id=(
                    plan.selected_site_id
                ),
            )

            return RuntimeSubmissionResult(
                task_record=task_record,
                optical_record=self.repository.get_optical(
                    task.task_id
                ),
                execution_record=self.repository.get_execution(
                    task.task_id
                ),
                reused_existing=False,
            )

        except (
            KubernetesAdapterError,
            RuntimeTaskRepositoryError,
            PersistentKubernetesRuntimeServiceError,
        ) as exc:
            if job_created:
                try:
                    self.kubernetes_adapter.delete_job(
                        job_name=(
                            self.kubernetes_adapter
                            .job_name_for_task(task.task_id)
                        ),
                        ignore_not_found=True,
                    )
                except KubernetesAdapterError:
                    pass

            if optical_reserved:
                try:
                    self.planner.release_optical(
                        task.task_id
                    )
                except OpticalResourceManagerError:
                    pass

            try:
                self.repository.finish_release(
                    task_id=task.task_id,
                    final_state=TaskState.FAILED,
                    failure_reason=str(exc),
                )
            except RuntimeTaskRepositoryError:
                pass

            raise PersistentKubernetesRuntimeServiceError(
                f"Failed to submit runtime task "
                f"{task.task_id}: {exc}"
            ) from exc

    def finalize(
        self,
        task_id: str,
        timeout_seconds: int = 300,
    ) -> RuntimeFinalizationResult:
        """Wait for the Job, persist its result and release spectrum."""

        task_record = self.repository.get_task(task_id)

        if task_record is None:
            raise PersistentKubernetesRuntimeServiceError(
                f"Runtime task does not exist: {task_id}"
            )

        execution_record = self.repository.get_execution(
            task_id
        )

        if execution_record is None:
            raise PersistentKubernetesRuntimeServiceError(
                f"Task has no Kubernetes execution: {task_id}"
            )

        try:
            job_result = (
                self.kubernetes_adapter.wait_for_completion(
                    task_id=task_id,
                    job_name=execution_record.job_name,
                    site_id=(
                        execution_record.requested_site_id
                    ),
                    timeout_seconds=timeout_seconds,
                )
            )
        except KubernetesAdapterError as exc:
            raise PersistentKubernetesRuntimeServiceError(
                f"Kubernetes status is temporarily unavailable for "
                f"task {task_id}; reconciliation will retry: {exc}"
            ) from exc

        execution_phase = (
            "SUCCEEDED"
            if job_result.succeeded
            else "FAILED"
        )

        execution_record = (
            self.repository.save_execution_result(
                task_id=task_id,
                pod_name=job_result.pod_name,
                node_name=job_result.node_name,
                node_site_label=(
                    job_result.node_site_label
                ),
                phase=execution_phase,
                succeeded=job_result.succeeded,
                logs=job_result.logs,
            )
        )

        site_matches = (
            job_result.node_site_label
            == execution_record.requested_site_id
        )

        node_matches = (
            task_record.selected_node_name is None
            or job_result.node_name
            == task_record.selected_node_name
        )

        success = (
            job_result.succeeded
            and site_matches
            and node_matches
        )

        if success:
            self.repository.set_state(
                task_id=task_id,
                state=TaskState.COMPLETED,
            )

            self.repository.set_state(
                task_id=task_id,
                state=TaskState.RELEASING,
            )

            try:
                self.planner.release_optical(task_id)
            except OpticalResourceManagerError as exc:
                self.repository.set_state(
                    task_id=task_id,
                    state=TaskState.RELEASE_FAILED,
                    failure_reason=str(exc),
                )

                raise PersistentKubernetesRuntimeServiceError(
                    f"Job completed but spectrum release "
                    f"failed for {task_id}: {exc}"
                ) from exc

            final_record = self.repository.finish_release(
                task_id=task_id,
                final_state=TaskState.RELEASED,
            )

        else:
            reasons: list[str] = []

            if not job_result.succeeded:
                reasons.append(
                    f"Kubernetes Job failed: "
                    f"{job_result.job_name}"
                )

            if not site_matches:
                reasons.append(
                    "Execution site mismatch: "
                    f"expected "
                    f"{execution_record.requested_site_id}, "
                    f"received "
                    f"{job_result.node_site_label}"
                )

            if not node_matches:
                reasons.append(
                    "Execution node mismatch: "
                    f"expected {task_record.selected_node_name}, "
                    f"received {job_result.node_name}"
                )

            failure_reason = "; ".join(reasons)

            try:
                self.planner.release_optical(task_id)
            except OpticalResourceManagerError as exc:
                failure_reason = (
                    f"{failure_reason}; "
                    f"spectrum release failed: {exc}"
                )

                self.repository.set_state(
                    task_id=task_id,
                    state=TaskState.RELEASE_FAILED,
                    failure_reason=failure_reason,
                )

                raise PersistentKubernetesRuntimeServiceError(
                    f"Job failed and spectrum release also "
                    f"failed for {task_id}: {exc}"
                ) from exc

            final_record = self.repository.finish_release(
                task_id=task_id,
                final_state=TaskState.FAILED,
                failure_reason=failure_reason,
            )

        return RuntimeFinalizationResult(
            task_record=final_record,
            execution_record=execution_record,
            job_result=job_result,
        )

    def ensure_job(
        self,
        task_id: str,
    ) -> RuntimeTaskRecord:
        """
        Ensure a persisted RESERVED/SUBMITTING task has a Job.

        The Job name is deterministic, making this operation idempotent.
        """

        task_record = self.repository.get_task(task_id)

        if task_record is None:
            raise PersistentKubernetesRuntimeServiceError(
                f"Runtime task does not exist: {task_id}"
            )

        optical = self.repository.get_optical(task_id)

        if optical is None:
            raise PersistentKubernetesRuntimeServiceError(
                f"Task has no persisted optical plan: {task_id}"
            )

        job_name = (
            self.kubernetes_adapter.job_name_for_task(
                task_id
            )
        )

        job_state = self.kubernetes_adapter.get_job_state(
            job_name
        )

        if job_state == "MISSING":
            created_job_name = (
                self.kubernetes_adapter.create_job(
                    task_id=task_id,
                    site_id=optical.site_id,
                    image=task_record.task.image,
                    shell_command=(
                        task_record.task.shell_command
                    ),
                    cpu_request=(
                        task_record.task.cpu_request_quantity
                    ),
                    memory_request=(
                        task_record.task
                        .memory_request_quantity
                    ),
                    cpu_limit=(
                        task_record.task.cpu_limit_quantity
                    ),
                    memory_limit=(
                        task_record.task
                        .memory_limit_quantity
                    ),
                    gpu_request=(
                        task_record.task.gpu_request
                    ),
                    gpu_resource_name=(
                        task_record.task
                        .accelerator_resource_name
                    ),
                    image_pull_policy="Never",
                    node_name=optical.node_name,
                    accelerator_product=(
                        task_record.task.accelerator_product
                    ),
                    accelerator_memory_mib=(
                        task_record.task
                        .accelerator_memory_mib
                    ),
                    accelerator_interconnect=(
                        task_record.task
                        .accelerator_interconnect
                    ),
                    accelerator_partition_mode=(
                        task_record.task
                        .accelerator_partition_mode
                    ),
                )
            )

            if created_job_name != job_name:
                raise PersistentKubernetesRuntimeServiceError(
                    f"Unexpected Kubernetes Job name: "
                    f"{created_job_name}"
                )

        return self.repository.save_job(
            task_id=task_id,
            job_name=job_name,
            namespace=self.kubernetes_adapter.namespace,
            requested_site_id=optical.site_id,
        )

    def release_with_outcome(
        self,
        task_id: str,
        final_state: TaskState,
        failure_reason: str | None = None,
    ) -> RuntimeTaskRecord:
        """
        Release optical resources and persist a terminal outcome.

        This method is idempotent for tasks already in a release-retry
        state. If the in-memory allocation is already absent, the
        release step is treated as already completed.
        """

        if final_state not in {
            TaskState.RELEASED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }:
            raise ValueError(
                "final_state must be RELEASED, FAILED "
                "or CANCELLED."
            )

        task_record = self.repository.get_task(task_id)

        if task_record is None:
            raise PersistentKubernetesRuntimeServiceError(
                f"Runtime task does not exist: {task_id}"
            )

        self.repository.set_state(
            task_id=task_id,
            state=TaskState.RELEASING,
            failure_reason=failure_reason,
        )

        if self._has_active_optical_allocation(task_id):
            try:
                self.planner.release_optical(task_id)
            except OpticalResourceManagerError as exc:
                combined_reason = (
                    f"{failure_reason}; {exc}"
                    if failure_reason
                    else str(exc)
                )

                self.repository.set_state(
                    task_id=task_id,
                    state=TaskState.RELEASE_FAILED,
                    failure_reason=combined_reason,
                )

                raise PersistentKubernetesRuntimeServiceError(
                    f"Failed to release spectrum for "
                    f"{task_id}: {exc}"
                ) from exc

        return self.repository.finish_release(
            task_id=task_id,
            final_state=final_state,
            failure_reason=failure_reason,
        )

    def retry_release(
        self,
        task_id: str,
    ) -> RuntimeTaskRecord:
        """Retry an interrupted or failed release operation."""

        task_record = self.repository.get_task(task_id)

        if task_record is None:
            raise PersistentKubernetesRuntimeServiceError(
                f"Runtime task does not exist: {task_id}"
            )

        execution = self.repository.get_execution(task_id)

        execution_succeeded = (
            execution is not None
            and execution.succeeded is True
            and execution.node_site_label
            == execution.requested_site_id
        )

        if execution_succeeded:
            final_state = TaskState.RELEASED
            failure_reason = None
        else:
            final_state = TaskState.FAILED
            failure_reason = (
                task_record.failure_reason
                or "Task execution did not complete successfully."
            )

        return self.release_with_outcome(
            task_id=task_id,
            final_state=final_state,
            failure_reason=failure_reason,
        )

    def fail_missing_job(
        self,
        task_id: str,
    ) -> RuntimeTaskRecord:
        """Fail a RUNNING task whose Kubernetes Job disappeared."""

        return self.release_with_outcome(
            task_id=task_id,
            final_state=TaskState.FAILED,
            failure_reason=(
                "Kubernetes Job disappeared while "
                "the task was RUNNING."
            ),
        )

    def _has_active_optical_allocation(
        self,
        task_id: str,
    ) -> bool:
        """Check the in-memory OpticalResourceManager defensively."""

        active = (
            self.planner.optical_manager
            .active_allocations()
        )

        if isinstance(active, dict):
            return task_id in active

        for item in active:
            if item == task_id:
                return True

            if getattr(item, "task_id", None) == task_id:
                return True

        return False

    def recover(self) -> int:
        """
        Restore persisted optical reservations and reconcile Jobs.

        RESERVED or SUBMITTING tasks are submitted using the persisted
        task and selected site. RUNNING tasks keep running. Completed
        release transitions are finished conservatively.
        """

        recoverable = (
            self.repository.list_recoverable_tasks()
        )

        restored_count = 0

        for task_record in recoverable:
            task_id = task_record.task.task_id
            optical = self.repository.get_optical(task_id)

            if optical is None:
                self.repository.set_state(
                    task_id=task_id,
                    state=TaskState.FAILED,
                    failure_reason=(
                        "Recoverable task has no persisted "
                        "optical allocation."
                    ),
                )
                continue

            try:
                self.planner.optical_manager.reserve(
                    task_id=task_id,
                    links=optical.fiber_links,
                    slot=optical.slot,
                )
            except OpticalResourceManagerError as exc:
                self.repository.set_state(
                    task_id=task_id,
                    state=TaskState.FAILED,
                    failure_reason=(
                        "Failed to restore spectrum: "
                        f"{exc}"
                    ),
                )
                continue

            restored_count += 1

            if task_record.state in {
                TaskState.COMPLETED,
                TaskState.RELEASING,
                TaskState.RELEASE_FAILED,
            }:
                try:
                    self.planner.release_optical(task_id)

                    self.repository.finish_release(
                        task_id=task_id,
                        final_state=TaskState.RELEASED,
                    )
                except (
                    OpticalResourceManagerError,
                    RuntimeTaskRepositoryError,
                ) as exc:
                    self.repository.set_state(
                        task_id=task_id,
                        state=TaskState.RELEASE_FAILED,
                        failure_reason=str(exc),
                    )

                continue

            job_name = (
                self.kubernetes_adapter.job_name_for_task(
                    task_id
                )
            )

            job_state = (
                self.kubernetes_adapter.get_job_state(
                    job_name
                )
            )

            if task_record.state in {
                TaskState.RESERVED,
                TaskState.SUBMITTING,
            }:
                if job_state == "MISSING":
                    self.kubernetes_adapter.create_job(
                        task_id=task_id,
                        site_id=optical.site_id,
                        image=task_record.task.image,
                        shell_command=(
                            task_record.task.shell_command
                        ),
                        cpu_request=(
                            task_record.task
                            .cpu_request_quantity
                        ),
                        memory_request=(
                            task_record.task
                            .memory_request_quantity
                        ),
                        cpu_limit=(
                            task_record.task
                            .cpu_limit_quantity
                        ),
                        memory_limit=(
                            task_record.task
                            .memory_limit_quantity
                        ),
                        gpu_request=(
                            task_record.task.gpu_request
                        ),
                        gpu_resource_name=(
                            task_record.task
                            .accelerator_resource_name
                        ),
                        image_pull_policy="Never",
                        node_name=optical.node_name,
                        accelerator_product=(
                            task_record.task
                            .accelerator_product
                        ),
                        accelerator_memory_mib=(
                            task_record.task
                            .accelerator_memory_mib
                        ),
                        accelerator_interconnect=(
                            task_record.task
                            .accelerator_interconnect
                        ),
                        accelerator_partition_mode=(
                            task_record.task
                            .accelerator_partition_mode
                        ),
                    )

                self.repository.save_job(
                    task_id=task_id,
                    job_name=job_name,
                    namespace=(
                        self.kubernetes_adapter.namespace
                    ),
                    requested_site_id=optical.site_id,
                )

            elif (
                task_record.state == TaskState.RUNNING
                and job_state == "MISSING"
            ):
                try:
                    self.planner.release_optical(task_id)
                except OpticalResourceManagerError:
                    pass

                self.repository.finish_release(
                    task_id=task_id,
                    final_state=TaskState.FAILED,
                    failure_reason=(
                        "Kubernetes Job disappeared during "
                        "restart recovery."
                    ),
                )

        return restored_count
