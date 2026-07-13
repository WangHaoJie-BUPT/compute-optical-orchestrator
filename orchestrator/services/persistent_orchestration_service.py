"""Persistent and restart-safe orchestration service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.models.resources import TaskRequest
from orchestrator.models.task_state import (
    TaskRecord,
    TaskState,
)
from orchestrator.repositories.task_repository import (
    TaskRepository,
    TaskRepositoryError,
)
from orchestrator.services.joint_allocation_service import (
    CandidatePlan,
    JointAllocation,
    JointAllocationService,
    JointAllocationServiceError,
)
from orchestrator.state.optical_resource_manager import (
    OpticalResourceManagerError,
)
from orchestrator.state.resource_manager import (
    ResourceManagerError,
)


class PersistentOrchestrationServiceError(RuntimeError):
    """Raised when persistent orchestration fails."""


@dataclass(frozen=True)
class SubmitResult:
    """Result returned by idempotent task submission."""

    task_record: TaskRecord
    allocation: JointAllocation | None
    plans: tuple[CandidatePlan, ...]
    reused_existing: bool


class PersistentOrchestrationService:
    """
    Add persistence, idempotency and restart recovery to joint allocation.
    """

    def __init__(
        self,
        repository: TaskRepository,
        allocation_service: JointAllocationService,
    ) -> None:
        self.repository = repository
        self.allocation_service = allocation_service

    def recover(self) -> int:
        """
        Restore all committed resource allocations from SQLite.

        Returns the number of restored tasks.
        """

        self.repository.recover_interrupted_states()

        persisted_allocations = (
            self.repository.list_persisted_allocations()
        )

        restored_count = 0

        for persisted in persisted_allocations:
            task = persisted.task_record.task

            existing = self.allocation_service.get_allocation(
                task.task_id
            )

            if existing is not None:
                continue

            compute_allocation = None

            try:
                compute_allocation = (
                    self.allocation_service.compute_manager.reserve(
                        task=task,
                        site_id=persisted.site_id,
                    )
                )

                optical_allocation = (
                    self.allocation_service.optical_manager.reserve(
                        task_id=task.task_id,
                        links=persisted.fiber_links,
                        slot=persisted.slot,
                    )
                )

            except (
                ResourceManagerError,
                OpticalResourceManagerError,
            ) as exc:
                if compute_allocation is not None:
                    try:
                        self.allocation_service.compute_manager.release(
                            task.task_id
                        )
                    except ResourceManagerError:
                        pass

                raise PersistentOrchestrationServiceError(
                    f"Failed to restore task {task.task_id}: {exc}"
                ) from exc

            allocation = JointAllocation(
                task_id=task.task_id,
                site_id=persisted.site_id,
                compute_allocation=compute_allocation,
                optical_allocation=optical_allocation,
                gsnr_db=persisted.gsnr_db,
                total_score=persisted.total_score,
                route=persisted.route,
            )

            self.allocation_service.restore_allocation(
                allocation
            )

            restored_count += 1

        return restored_count

    def submit(
        self,
        task: TaskRequest,
        topology_file: str | Path,
        equipment_file: str | Path,
    ) -> SubmitResult:
        """
        Submit a task idempotently.

        Reusing the same task ID and identical task definition returns the
        existing record without allocating resources again.
        """

        existing = self.repository.get_task(task.task_id)

        if existing is not None:
            if existing.task != task:
                raise PersistentOrchestrationServiceError(
                    f"Task ID {task.task_id} already exists with "
                    f"different requirements."
                )

            allocation = (
                self.allocation_service.get_allocation(
                    task.task_id
                )
            )

            return SubmitResult(
                task_record=existing,
                allocation=allocation,
                plans=(),
                reused_existing=True,
            )

        inserted = self.repository.create_task(task)

        if not inserted:
            raise PersistentOrchestrationServiceError(
                f"Task was concurrently inserted: {task.task_id}"
            )

        self.repository.set_state(
            task_id=task.task_id,
            state=TaskState.PLANNING,
        )

        try:
            allocation, plans = (
                self.allocation_service.allocate(
                    task=task,
                    topology_file=topology_file,
                    equipment_file=equipment_file,
                )
            )

        except JointAllocationServiceError as exc:
            self.repository.set_state(
                task_id=task.task_id,
                state=TaskState.BLOCKED,
                failure_reason=str(exc),
            )

            record = self.repository.get_task(task.task_id)

            if record is None:
                raise PersistentOrchestrationServiceError(
                    f"Task disappeared after blocking: "
                    f"{task.task_id}"
                )

            return SubmitResult(
                task_record=record,
                allocation=None,
                plans=tuple(exc.plans),
                reused_existing=False,
            )

        try:
            self.repository.save_joint_allocation(
                allocation
            )
        except TaskRepositoryError as exc:
            # The in-memory reservation succeeded but persistence failed.
            # Compensate by releasing both resources.
            try:
                self.allocation_service.release(
                    task.task_id
                )
            except JointAllocationServiceError as release_exc:
                reason = (
                    f"Persistence failed: {exc}; "
                    f"rollback also failed: {release_exc}"
                )
            else:
                reason = (
                    f"Persistence failed and resources were rolled back: "
                    f"{exc}"
                )

            self.repository.set_state(
                task_id=task.task_id,
                state=TaskState.FAILED,
                failure_reason=reason,
            )

            raise PersistentOrchestrationServiceError(
                reason
            ) from exc

        record = self.repository.get_task(task.task_id)

        if record is None:
            raise PersistentOrchestrationServiceError(
                f"Task disappeared after allocation: "
                f"{task.task_id}"
            )

        return SubmitResult(
            task_record=record,
            allocation=allocation,
            plans=tuple(plans),
            reused_existing=False,
        )

    def release(self, task_id: str) -> TaskRecord:
        """Release both resources and persist the RELEASED state."""

        record = self.repository.get_task(task_id)

        if record is None:
            raise PersistentOrchestrationServiceError(
                f"Unknown task: {task_id}"
            )

        if record.state == TaskState.RELEASED:
            return record

        self.repository.set_state(
            task_id=task_id,
            state=TaskState.RELEASING,
        )

        try:
            self.allocation_service.release(task_id)
        except JointAllocationServiceError as exc:
            self.repository.set_state(
                task_id=task_id,
                state=TaskState.RELEASE_FAILED,
                failure_reason=str(exc),
            )

            raise PersistentOrchestrationServiceError(
                f"Resource release failed for {task_id}: {exc}"
            ) from exc

        try:
            self.repository.mark_released(task_id)
        except TaskRepositoryError as exc:
            self.repository.set_state(
                task_id=task_id,
                state=TaskState.RELEASE_FAILED,
                failure_reason=(
                    "Resources were released in memory, but the "
                    f"database update failed: {exc}"
                ),
            )

            raise PersistentOrchestrationServiceError(
                f"Database release update failed for "
                f"{task_id}: {exc}"
            ) from exc

        released_record = self.repository.get_task(task_id)

        if released_record is None:
            raise PersistentOrchestrationServiceError(
                f"Task disappeared after release: {task_id}"
            )

        return released_record
