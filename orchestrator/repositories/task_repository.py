"""SQLite repository for tasks and joint allocations."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from orchestrator.models.resources import TaskRequest
from orchestrator.models.spectrum import SpectrumSlot
from orchestrator.models.task_state import (
    ACTIVE_ALLOCATION_STATES,
    PersistedJointAllocation,
    TaskRecord,
    TaskState,
)
from orchestrator.services.joint_allocation_service import (
    JointAllocation,
)
from orchestrator.storage.database import Database


class TaskRepositoryError(RuntimeError):
    """Raised when persistent task data cannot be read or written."""


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""

    return datetime.now(timezone.utc).isoformat()


class TaskRepository:
    """Persist tasks and committed joint allocations."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_task(self, task: TaskRequest) -> bool:
        """
        Insert a task.

        Returns True when a new task was inserted and False when the task ID
        already existed.
        """

        timestamp = utc_now()

        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO tasks (
                        task_id,
                        source_access_node,
                        cpu_required,
                        memory_gb_required,
                        gpu_required,
                        bandwidth_gbps,
                        state,
                        selected_site,
                        failure_reason,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.source_access_node,
                        task.cpu_required,
                        task.memory_gb_required,
                        task.gpu_required,
                        task.bandwidth_gbps,
                        TaskState.PENDING.value,
                        timestamp,
                        timestamp,
                    ),
                )

                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise TaskRepositoryError(
                f"Failed to create task: {task.task_id}"
            ) from exc

    def get_task(self, task_id: str) -> TaskRecord | None:
        """Return one task or None."""

        try:
            with self.database.connection() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM tasks
                    WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise TaskRepositoryError(
                f"Failed to read task: {task_id}"
            ) from exc

        if row is None:
            return None

        return self._row_to_task_record(row)

    def list_tasks(self) -> list[TaskRecord]:
        """Return all tasks ordered by creation time."""

        try:
            with self.database.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM tasks
                    ORDER BY created_at, task_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise TaskRepositoryError(
                "Failed to list tasks."
            ) from exc

        return [
            self._row_to_task_record(row)
            for row in rows
        ]

    def set_state(
        self,
        task_id: str,
        state: TaskState,
        selected_site: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        """Update task lifecycle state."""

        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE tasks
                    SET state = ?,
                        selected_site = COALESCE(?, selected_site),
                        failure_reason = ?,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        state.value,
                        selected_site,
                        failure_reason,
                        utc_now(),
                        task_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise TaskRepositoryError(
                        f"Task does not exist: {task_id}"
                    )
        except sqlite3.Error as exc:
            raise TaskRepositoryError(
                f"Failed to update task state: {task_id}"
            ) from exc

    def save_joint_allocation(
        self,
        allocation: JointAllocation,
    ) -> None:
        """
        Atomically persist compute allocation, optical allocation and state.
        """

        compute = allocation.compute_allocation
        optical = allocation.optical_allocation

        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO compute_allocations (
                        task_id,
                        allocation_id,
                        site_id,
                        cpu,
                        memory_gb,
                        gpu
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        allocation.task_id,
                        compute.allocation_id,
                        compute.site_id,
                        compute.cpu,
                        compute.memory_gb,
                        compute.gpu,
                    ),
                )

                connection.execute(
                    """
                    INSERT INTO optical_allocations (
                        task_id,
                        allocation_id,
                        n,
                        m,
                        links_json,
                        route_json,
                        gsnr_db,
                        total_score
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        allocation.task_id,
                        optical.allocation_id,
                        optical.slot.n,
                        optical.slot.m,
                        json.dumps(
                            list(optical.links),
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            list(allocation.route),
                            ensure_ascii=False,
                        ),
                        allocation.gsnr_db,
                        allocation.total_score,
                    ),
                )

                cursor = connection.execute(
                    """
                    UPDATE tasks
                    SET state = ?,
                        selected_site = ?,
                        failure_reason = NULL,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        TaskState.RESERVED.value,
                        allocation.site_id,
                        utc_now(),
                        allocation.task_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise TaskRepositoryError(
                        f"Task does not exist: {allocation.task_id}"
                    )

        except sqlite3.IntegrityError as exc:
            raise TaskRepositoryError(
                f"Allocation already exists or is invalid: "
                f"{allocation.task_id}"
            ) from exc
        except sqlite3.Error as exc:
            raise TaskRepositoryError(
                f"Failed to persist allocation: {allocation.task_id}"
            ) from exc

    def mark_released(self, task_id: str) -> None:
        """Delete active allocations and mark the task as released."""

        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    DELETE FROM optical_allocations
                    WHERE task_id = ?
                    """,
                    (task_id,),
                )

                connection.execute(
                    """
                    DELETE FROM compute_allocations
                    WHERE task_id = ?
                    """,
                    (task_id,),
                )

                cursor = connection.execute(
                    """
                    UPDATE tasks
                    SET state = ?,
                        failure_reason = NULL,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        TaskState.RELEASED.value,
                        utc_now(),
                        task_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise TaskRepositoryError(
                        f"Task does not exist: {task_id}"
                    )
        except sqlite3.Error as exc:
            raise TaskRepositoryError(
                f"Failed to mark task released: {task_id}"
            ) from exc

    def list_persisted_allocations(
        self,
    ) -> list[PersistedJointAllocation]:
        """Return all allocations that must be restored after restart."""

        states = [
            state.value
            for state in ACTIVE_ALLOCATION_STATES
        ]
        placeholders = ", ".join("?" for _ in states)

        query = f"""
            SELECT
                t.*,

                c.allocation_id AS compute_allocation_id,
                c.site_id AS allocation_site_id,
                c.cpu AS allocation_cpu,
                c.memory_gb AS allocation_memory_gb,
                c.gpu AS allocation_gpu,

                o.allocation_id AS optical_allocation_id,
                o.n AS optical_n,
                o.m AS optical_m,
                o.links_json AS optical_links_json,
                o.route_json AS optical_route_json,
                o.gsnr_db AS optical_gsnr_db,
                o.total_score AS optical_total_score

            FROM tasks AS t

            INNER JOIN compute_allocations AS c
                ON c.task_id = t.task_id

            INNER JOIN optical_allocations AS o
                ON o.task_id = t.task_id

            WHERE t.state IN ({placeholders})
            ORDER BY t.created_at, t.task_id
        """

        try:
            with self.database.connection() as connection:
                rows = connection.execute(
                    query,
                    states,
                ).fetchall()
        except sqlite3.Error as exc:
            raise TaskRepositoryError(
                "Failed to load persisted allocations."
            ) from exc

        allocations: list[PersistedJointAllocation] = []

        for row in rows:
            record = self._row_to_task_record(row)

            try:
                links = tuple(
                    str(item)
                    for item in json.loads(
                        row["optical_links_json"]
                    )
                )

                route = tuple(
                    str(item)
                    for item in json.loads(
                        row["optical_route_json"]
                    )
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise TaskRepositoryError(
                    f"Invalid allocation JSON for "
                    f"{record.task.task_id}"
                ) from exc

            allocations.append(
                PersistedJointAllocation(
                    task_record=record,
                    compute_allocation_id=str(
                        row["compute_allocation_id"]
                    ),
                    site_id=str(
                        row["allocation_site_id"]
                    ),
                    cpu=int(row["allocation_cpu"]),
                    memory_gb=int(
                        row["allocation_memory_gb"]
                    ),
                    gpu=int(row["allocation_gpu"]),
                    optical_allocation_id=str(
                        row["optical_allocation_id"]
                    ),
                    slot=SpectrumSlot(
                        n=int(row["optical_n"]),
                        m=int(row["optical_m"]),
                    ),
                    fiber_links=links,
                    route=route,
                    gsnr_db=float(
                        row["optical_gsnr_db"]
                    ),
                    total_score=float(
                        row["optical_total_score"]
                    ),
                )
            )

        return allocations

    def recover_interrupted_states(self) -> None:
        """
        Normalize states left behind by an interrupted process.

        PLANNING without an allocation becomes FAILED.
        PLANNING with a committed allocation becomes RESERVED.
        RELEASING with allocations becomes RELEASE_FAILED.
        RELEASING without allocations becomes RELEASED.
        """

        timestamp = utc_now()

        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE tasks
                    SET state = ?,
                        failure_reason = ?,
                        updated_at = ?
                    WHERE state = ?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM compute_allocations AS c
                          WHERE c.task_id = tasks.task_id
                      )
                    """,
                    (
                        TaskState.FAILED.value,
                        "Recovered after interrupted planning.",
                        timestamp,
                        TaskState.PLANNING.value,
                    ),
                )

                connection.execute(
                    """
                    UPDATE tasks
                    SET state = ?,
                        failure_reason = NULL,
                        updated_at = ?
                    WHERE state = ?
                      AND EXISTS (
                          SELECT 1
                          FROM compute_allocations AS c
                          WHERE c.task_id = tasks.task_id
                      )
                    """,
                    (
                        TaskState.RESERVED.value,
                        timestamp,
                        TaskState.PLANNING.value,
                    ),
                )

                connection.execute(
                    """
                    UPDATE tasks
                    SET state = ?,
                        failure_reason = ?,
                        updated_at = ?
                    WHERE state = ?
                      AND EXISTS (
                          SELECT 1
                          FROM compute_allocations AS c
                          WHERE c.task_id = tasks.task_id
                      )
                    """,
                    (
                        TaskState.RELEASE_FAILED.value,
                        "Release was interrupted and requires retry.",
                        timestamp,
                        TaskState.RELEASING.value,
                    ),
                )

                connection.execute(
                    """
                    UPDATE tasks
                    SET state = ?,
                        failure_reason = NULL,
                        updated_at = ?
                    WHERE state = ?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM compute_allocations AS c
                          WHERE c.task_id = tasks.task_id
                      )
                    """,
                    (
                        TaskState.RELEASED.value,
                        timestamp,
                        TaskState.RELEASING.value,
                    ),
                )

        except sqlite3.Error as exc:
            raise TaskRepositoryError(
                "Failed to recover interrupted states."
            ) from exc

    @staticmethod
    def _row_to_task_record(
        row: Any,
    ) -> TaskRecord:
        task = TaskRequest(
            task_id=str(row["task_id"]),
            source_access_node=str(
                row["source_access_node"]
            ),
            cpu_required=int(row["cpu_required"]),
            memory_gb_required=int(
                row["memory_gb_required"]
            ),
            gpu_required=int(row["gpu_required"]),
            bandwidth_gbps=float(
                row["bandwidth_gbps"]
            ),
        )

        return TaskRecord(
            task=task,
            state=TaskState(str(row["state"])),
            selected_site=(
                str(row["selected_site"])
                if row["selected_site"] is not None
                else None
            ),
            failure_reason=(
                str(row["failure_reason"])
                if row["failure_reason"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
