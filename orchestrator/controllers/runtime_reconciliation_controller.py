"""Reconciliation controller for persistent runtime tasks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable

from orchestrator.adapters.kubernetes_adapter import (
    KubernetesAdapter,
    KubernetesAdapterError,
)
from orchestrator.models.task_state import TaskState
from orchestrator.repositories.controller_lease_repository import (
    ControllerLeaseRepository,
    ControllerLeaseRepositoryError,
)
from orchestrator.repositories.runtime_task_repository import (
    RuntimeTaskRepository,
)
from orchestrator.services.persistent_kubernetes_runtime_service import (
    PersistentKubernetesRuntimeService,
    PersistentKubernetesRuntimeServiceError,
)


@dataclass(frozen=True)
class ReconciliationEvent:
    """Result of reconciling one task."""

    task_id: str
    previous_state: str
    job_state: str | None
    action: str
    final_state: str | None
    error: str | None


@dataclass(frozen=True)
class ReconciliationBatchResult:
    """Result of one complete controller scan."""

    controller_id: str
    lease_acquired: bool
    scanned_count: int
    changed_count: int
    error_count: int
    events: tuple[ReconciliationEvent, ...]


class RuntimeReconciliationController:
    """Continuously reconcile SQLite state with Kubernetes state."""

    RECONCILABLE_STATES = {
        TaskState.RESERVED,
        TaskState.SUBMITTING,
        TaskState.RUNNING,
        TaskState.COMPLETED,
        TaskState.RELEASING,
        TaskState.RELEASE_FAILED,
    }

    def __init__(
        self,
        repository: RuntimeTaskRepository,
        runtime_service: PersistentKubernetesRuntimeService,
        kubernetes_adapter: KubernetesAdapter,
        lease_repository: ControllerLeaseRepository,
        controller_id: str | None = None,
        lease_name: str = "runtime-reconciliation-controller",
        lease_seconds: float = 30.0,
    ) -> None:
        self.repository = repository
        self.runtime_service = runtime_service
        self.kubernetes_adapter = kubernetes_adapter
        self.lease_repository = lease_repository

        self.controller_id = (
            controller_id
            or f"controller-{uuid.uuid4().hex[:12]}"
        )

        self.lease_name = lease_name
        self.lease_seconds = lease_seconds

    def run_once(self) -> ReconciliationBatchResult:
        """Run one leased reconciliation scan."""

        acquired = self.lease_repository.try_acquire(
            lease_name=self.lease_name,
            holder_id=self.controller_id,
            lease_seconds=self.lease_seconds,
        )

        if not acquired:
            return ReconciliationBatchResult(
                controller_id=self.controller_id,
                lease_acquired=False,
                scanned_count=0,
                changed_count=0,
                error_count=0,
                events=(),
            )

        events: list[ReconciliationEvent] = []

        try:
            tasks = self.repository.list_tasks_by_states(
                self.RECONCILABLE_STATES
            )

            for task_record in tasks:
                event = self._reconcile_task(
                    task_record.task.task_id,
                    task_record.state,
                )

                events.append(event)

                self.lease_repository.renew(
                    lease_name=self.lease_name,
                    holder_id=self.controller_id,
                    lease_seconds=self.lease_seconds,
                )

            changed_count = sum(
                event.action not in {
                    "NO_CHANGE",
                    "ERROR",
                }
                for event in events
            )

            error_count = sum(
                event.error is not None
                for event in events
            )

            return ReconciliationBatchResult(
                controller_id=self.controller_id,
                lease_acquired=True,
                scanned_count=len(tasks),
                changed_count=changed_count,
                error_count=error_count,
                events=tuple(events),
            )

        finally:
            self.lease_repository.release(
                lease_name=self.lease_name,
                holder_id=self.controller_id,
            )

    def _reconcile_task(
        self,
        task_id: str,
        previous_state: TaskState,
    ) -> ReconciliationEvent:
        try:
            if previous_state in {
                TaskState.RESERVED,
                TaskState.SUBMITTING,
            }:
                record = self.runtime_service.ensure_job(
                    task_id
                )

                execution = self.repository.get_execution(
                    task_id
                )

                job_state = (
                    self.kubernetes_adapter.get_job_state(
                        execution.job_name
                    )
                    if execution is not None
                    else None
                )

                return ReconciliationEvent(
                    task_id=task_id,
                    previous_state=previous_state.value,
                    job_state=job_state,
                    action="ENSURED_JOB",
                    final_state=record.state.value,
                    error=None,
                )

            if previous_state == TaskState.RUNNING:
                execution = self.repository.get_execution(
                    task_id
                )

                if execution is None:
                    record = self.runtime_service.ensure_job(
                        task_id
                    )

                    execution = (
                        self.repository.get_execution(
                            task_id
                        )
                    )

                    if execution is None:
                        raise (
                            PersistentKubernetesRuntimeServiceError(
                                "Execution record was not created."
                            )
                        )

                job_state = (
                    self.kubernetes_adapter.get_job_state(
                        execution.job_name
                    )
                )

                if job_state in {
                    "PENDING",
                    "RUNNING",
                }:
                    return ReconciliationEvent(
                        task_id=task_id,
                        previous_state=previous_state.value,
                        job_state=job_state,
                        action="NO_CHANGE",
                        final_state=previous_state.value,
                        error=None,
                    )

                if job_state == "MISSING":
                    final_record = (
                        self.runtime_service
                        .fail_missing_job(task_id)
                    )

                    return ReconciliationEvent(
                        task_id=task_id,
                        previous_state=previous_state.value,
                        job_state=job_state,
                        action="FAILED_MISSING_JOB",
                        final_state=(
                            final_record.state.value
                        ),
                        error=None,
                    )

                if job_state in {
                    "SUCCEEDED",
                    "FAILED",
                }:
                    result = self.runtime_service.finalize(
                        task_id=task_id,
                        timeout_seconds=15,
                    )

                    return ReconciliationEvent(
                        task_id=task_id,
                        previous_state=previous_state.value,
                        job_state=job_state,
                        action="FINALIZED_JOB",
                        final_state=(
                            result.task_record.state.value
                        ),
                        error=None,
                    )

                raise RuntimeError(
                    f"Unsupported Job state: {job_state}"
                )

            if previous_state in {
                TaskState.COMPLETED,
                TaskState.RELEASING,
                TaskState.RELEASE_FAILED,
            }:
                record = (
                    self.runtime_service.retry_release(
                        task_id
                    )
                )

                return ReconciliationEvent(
                    task_id=task_id,
                    previous_state=previous_state.value,
                    job_state=None,
                    action="RETRIED_RELEASE",
                    final_state=record.state.value,
                    error=None,
                )

            return ReconciliationEvent(
                task_id=task_id,
                previous_state=previous_state.value,
                job_state=None,
                action="NO_CHANGE",
                final_state=previous_state.value,
                error=None,
            )

        except (
            KubernetesAdapterError,
            PersistentKubernetesRuntimeServiceError,
            ControllerLeaseRepositoryError,
            RuntimeError,
        ) as exc:
            current = self.repository.get_task(task_id)

            return ReconciliationEvent(
                task_id=task_id,
                previous_state=previous_state.value,
                job_state=None,
                action="ERROR",
                final_state=(
                    current.state.value
                    if current is not None
                    else None
                ),
                error=str(exc),
            )
