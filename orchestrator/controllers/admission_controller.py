"""Leased controller for durable asynchronous task admission."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from orchestrator.models.admission import (
    AdmissionRecord,
    AdmissionState,
)
from orchestrator.repositories.admission_queue_repository import (
    AdmissionQueueRepository,
    AdmissionQueueRepositoryError,
)
from orchestrator.repositories.controller_lease_repository import (
    ControllerLeaseRepository,
    ControllerLeaseRepositoryError,
)
from orchestrator.repositories.runtime_task_repository import (
    RuntimeTaskRepository,
)
from orchestrator.services.kubernetes_joint_planner import (
    KubernetesJointPlannerError,
)
from orchestrator.services.persistent_kubernetes_runtime_service import (
    PersistentKubernetesRuntimeService,
    PersistentKubernetesRuntimeServiceError,
)


@dataclass(frozen=True)
class AdmissionEvent:
    task_id: str
    tenant_id: str
    priority: int
    attempt_count: int
    action: str
    final_state: str
    error: str | None


@dataclass(frozen=True)
class AdmissionBatchResult:
    controller_id: str
    lease_acquired: bool
    claimed_count: int
    admitted_count: int
    requeued_count: int
    rejected_count: int
    events: tuple[AdmissionEvent, ...]


class AdmissionController:
    """Fairly dispatch queued tasks into the synchronous runtime."""

    def __init__(
        self,
        *,
        queue_repository: AdmissionQueueRepository,
        runtime_repository: RuntimeTaskRepository,
        runtime_service: PersistentKubernetesRuntimeService,
        lease_repository: ControllerLeaseRepository,
        topology_file: str | Path,
        equipment_file: str | Path,
        controller_id: str | None = None,
        lease_name: str = "runtime-admission-controller",
        lease_seconds: float = 300.0,
        max_dispatch_per_cycle: int = 1,
        max_transient_attempts: int = 5,
        base_backoff_seconds: float = 2.0,
        max_backoff_seconds: float = 60.0,
    ) -> None:
        if max_dispatch_per_cycle <= 0:
            raise ValueError("max_dispatch_per_cycle must be positive.")
        self.queue_repository = queue_repository
        self.runtime_repository = runtime_repository
        self.runtime_service = runtime_service
        self.lease_repository = lease_repository
        self.topology_file = Path(topology_file)
        self.equipment_file = Path(equipment_file)
        self.controller_id = (
            controller_id
            or f"admission-{uuid.uuid4().hex[:12]}"
        )
        self.lease_name = lease_name
        self.lease_seconds = lease_seconds
        self.max_dispatch_per_cycle = max_dispatch_per_cycle
        self.max_transient_attempts = max_transient_attempts
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds

    def run_once(self) -> AdmissionBatchResult:
        acquired = self.lease_repository.try_acquire(
            lease_name=self.lease_name,
            holder_id=self.controller_id,
            lease_seconds=self.lease_seconds,
        )
        if not acquired:
            return AdmissionBatchResult(
                controller_id=self.controller_id,
                lease_acquired=False,
                claimed_count=0,
                admitted_count=0,
                requeued_count=0,
                rejected_count=0,
                events=(),
            )

        events: list[AdmissionEvent] = []
        try:
            self.queue_repository.recover_stale_dispatches()
            for _ in range(self.max_dispatch_per_cycle):
                record = self.queue_repository.claim_next(
                    self.controller_id
                )
                if record is None:
                    break
                events.append(self._dispatch(record))
                self.lease_repository.renew(
                    lease_name=self.lease_name,
                    holder_id=self.controller_id,
                    lease_seconds=self.lease_seconds,
                )
        finally:
            self.lease_repository.release(
                lease_name=self.lease_name,
                holder_id=self.controller_id,
            )

        return AdmissionBatchResult(
            controller_id=self.controller_id,
            lease_acquired=True,
            claimed_count=len(events),
            admitted_count=sum(
                event.action == "ADMITTED" for event in events
            ),
            requeued_count=sum(
                event.action.startswith("REQUEUED")
                for event in events
            ),
            rejected_count=sum(
                event.action == "REJECTED" for event in events
            ),
            events=tuple(events),
        )

    def _dispatch(
        self,
        record: AdmissionRecord,
    ) -> AdmissionEvent:
        task_id = record.task.task_id
        try:
            existing = self.runtime_repository.get_task(task_id)
            if existing is not None:
                if existing.task != record.task:
                    raise PersistentKubernetesRuntimeServiceError(
                        "A runtime task already uses this ID with "
                        "different parameters."
                    )
                completed = self.queue_repository.mark_admitted(
                    task_id,
                    existing.state.value,
                )
                return self._event(
                    completed,
                    action="ADMITTED",
                    error=None,
                )

            # Probe feasibility without creating a terminal BLOCKED task.
            plan, _ = self.runtime_service.planner.select(
                task=record.task,
                topology_file=self.topology_file,
                equipment_file=self.equipment_file,
            )

            result = self.runtime_service.submit(
                task=record.task,
                topology_file=self.topology_file,
                equipment_file=self.equipment_file,
                preselected_plan=plan,
            )
            completed = self.queue_repository.mark_admitted(
                task_id,
                result.task_record.state.value,
            )
            return self._event(
                completed,
                action="ADMITTED",
                error=None,
            )

        except KubernetesJointPlannerError as exc:
            requeued = self.queue_repository.requeue(
                task_id=task_id,
                error=str(exc),
                delay_seconds=self._backoff(record.attempt_count),
            )
            return self._event(
                requeued,
                action="REQUEUED_CAPACITY",
                error=str(exc),
            )

        except (
            AdmissionQueueRepositoryError,
            ControllerLeaseRepositoryError,
            PersistentKubernetesRuntimeServiceError,
            RuntimeError,
        ) as exc:
            runtime_task = self.runtime_repository.get_task(task_id)
            if (
                runtime_task is not None
                or record.attempt_count >= self.max_transient_attempts
            ):
                rejected = self.queue_repository.reject(
                    task_id,
                    str(exc),
                )
                return self._event(
                    rejected,
                    action="REJECTED",
                    error=str(exc),
                )

            requeued = self.queue_repository.requeue(
                task_id=task_id,
                error=str(exc),
                delay_seconds=self._backoff(record.attempt_count),
            )
            return self._event(
                requeued,
                action="REQUEUED_TRANSIENT",
                error=str(exc),
            )

    def _backoff(self, attempt_count: int) -> float:
        return min(
            self.base_backoff_seconds
            * (2 ** max(attempt_count - 1, 0)),
            self.max_backoff_seconds,
        )

    @staticmethod
    def _event(
        record: AdmissionRecord,
        *,
        action: str,
        error: str | None,
    ) -> AdmissionEvent:
        if record.state == AdmissionState.DISPATCHING:
            raise RuntimeError("Admission event cannot remain dispatching.")
        return AdmissionEvent(
            task_id=record.task.task_id,
            tenant_id=record.tenant_id,
            priority=record.priority,
            attempt_count=record.attempt_count,
            action=action,
            final_state=record.state.value,
            error=error,
        )
