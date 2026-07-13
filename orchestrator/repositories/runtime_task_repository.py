"""SQLite repository for Kubernetes-native orchestration."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from orchestrator.models.runtime_persistence import (
    RuntimeExecutionRecord,
    RuntimeOpticalRecord,
    RuntimeTaskRecord,
)
from orchestrator.models.runtime_task import RuntimeTaskRequest
from orchestrator.models.spectrum import SpectrumSlot
from orchestrator.models.task_state import TaskState
from orchestrator.services.kubernetes_joint_planner import (
    KubernetesJointPlan,
)
from orchestrator.storage.database import Database


class RuntimeTaskRepositoryError(RuntimeError):
    """Raised when runtime orchestration state cannot be persisted."""


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""

    return datetime.now(timezone.utc).isoformat()


class RuntimeTaskRepository:
    """Persist Kubernetes-native tasks, plans and executions."""

    RECOVERABLE_STATES = {
        TaskState.RESERVED,
        TaskState.SUBMITTING,
        TaskState.RUNNING,
        TaskState.COMPLETED,
        TaskState.RELEASING,
        TaskState.RELEASE_FAILED,
    }

    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize(self) -> None:
        """Create Kubernetes-native orchestration tables."""

        schema = """
        CREATE TABLE IF NOT EXISTS runtime_tasks (
            task_id TEXT PRIMARY KEY,
            source_access_node TEXT NOT NULL,

            cpu_request_m INTEGER NOT NULL,
            memory_request_mib INTEGER NOT NULL,
            gpu_request INTEGER NOT NULL,

            cpu_limit_m INTEGER,
            memory_limit_mib INTEGER,

            bandwidth_gbps REAL NOT NULL,
            image TEXT NOT NULL,
            shell_command TEXT NOT NULL,

            state TEXT NOT NULL,
            selected_site_id TEXT,
            selected_node_name TEXT,
            failure_reason TEXT,

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runtime_optical_allocations (
            task_id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL,
            node_name TEXT NOT NULL,
            network_access_node TEXT NOT NULL,

            n INTEGER NOT NULL,
            m INTEGER NOT NULL,

            fiber_links_json TEXT NOT NULL,
            route_json TEXT NOT NULL,

            gsnr_db REAL NOT NULL,
            total_score REAL NOT NULL,
            created_at TEXT NOT NULL,

            FOREIGN KEY (task_id)
                REFERENCES runtime_tasks(task_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS runtime_kubernetes_executions (
            task_id TEXT PRIMARY KEY,
            job_name TEXT NOT NULL UNIQUE,
            namespace TEXT NOT NULL,
            requested_site_id TEXT NOT NULL,

            pod_name TEXT,
            node_name TEXT,
            node_site_label TEXT,

            phase TEXT NOT NULL,
            succeeded INTEGER,
            logs TEXT,

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            FOREIGN KEY (task_id)
                REFERENCES runtime_tasks(task_id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_runtime_tasks_state
            ON runtime_tasks(state);

        CREATE INDEX IF NOT EXISTS idx_runtime_execution_phase
            ON runtime_kubernetes_executions(phase);
        """

        try:
            with self.database.connection() as connection:
                connection.executescript(schema)
                connection.commit()
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                "Failed to initialize runtime orchestration tables."
            ) from exc

    def create_task(
        self,
        task: RuntimeTaskRequest,
    ) -> RuntimeTaskRecord:
        """Create a task in PENDING state."""

        timestamp = utc_now()

        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO runtime_tasks (
                        task_id,
                        source_access_node,
                        cpu_request_m,
                        memory_request_mib,
                        gpu_request,
                        cpu_limit_m,
                        memory_limit_mib,
                        bandwidth_gbps,
                        image,
                        shell_command,
                        state,
                        selected_site_id,
                        selected_node_name,
                        failure_reason,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, NULL, NULL, NULL, ?, ?
                    )
                    """,
                    (
                        task.task_id,
                        task.source_access_node,
                        task.cpu_request_m,
                        task.memory_request_mib,
                        task.gpu_request,
                        task.cpu_limit_m,
                        task.memory_limit_mib,
                        task.bandwidth_gbps,
                        task.image,
                        task.shell_command,
                        TaskState.PENDING.value,
                        timestamp,
                        timestamp,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RuntimeTaskRepositoryError(
                f"Runtime task already exists: {task.task_id}"
            ) from exc
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                f"Failed to create runtime task: {task.task_id}"
            ) from exc

        record = self.get_task(task.task_id)

        if record is None:
            raise RuntimeTaskRepositoryError(
                f"Task disappeared after creation: {task.task_id}"
            )

        return record

    def get_task(
        self,
        task_id: str,
    ) -> RuntimeTaskRecord | None:
        """Return one runtime task."""

        try:
            with self.database.connection() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM runtime_tasks
                    WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                f"Failed to read runtime task: {task_id}"
            ) from exc

        if row is None:
            return None

        return self._row_to_task(row)

    def list_tasks(self) -> list[RuntimeTaskRecord]:
        """Return all runtime tasks."""

        try:
            with self.database.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM runtime_tasks
                    ORDER BY created_at, task_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                "Failed to list runtime tasks."
            ) from exc

        return [
            self._row_to_task(row)
            for row in rows
        ]

    def list_tasks_by_states(
        self,
        states: set[TaskState] | tuple[TaskState, ...] | list[TaskState],
    ) -> list[RuntimeTaskRecord]:
        """Return runtime tasks whose state is in the supplied set."""

        ordered_states = sorted(
            set(states),
            key=lambda state: state.value,
        )

        if not ordered_states:
            return []

        placeholders = ",".join(
            "?"
            for _ in ordered_states
        )

        values = tuple(
            state.value
            for state in ordered_states
        )

        try:
            with self.database.connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT *
                    FROM runtime_tasks
                    WHERE state IN ({placeholders})
                    ORDER BY created_at, task_id
                    """,
                    values,
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                "Failed to list runtime tasks by state."
            ) from exc

        return [
            self._row_to_task(row)
            for row in rows
        ]

    def list_recoverable_tasks(
        self,
    ) -> list[RuntimeTaskRecord]:
        """Return tasks that may still hold optical resources."""

        placeholders = ",".join(
            "?"
            for _ in self.RECOVERABLE_STATES
        )

        state_values = tuple(
            state.value
            for state in self.RECOVERABLE_STATES
        )

        try:
            with self.database.connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT *
                    FROM runtime_tasks
                    WHERE state IN ({placeholders})
                    ORDER BY created_at, task_id
                    """,
                    state_values,
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                "Failed to list recoverable runtime tasks."
            ) from exc

        return [
            self._row_to_task(row)
            for row in rows
        ]

    def set_state(
        self,
        task_id: str,
        state: TaskState,
        selected_site_id: str | None = None,
        selected_node_name: str | None = None,
        failure_reason: str | None = None,
    ) -> RuntimeTaskRecord:
        """Update task state and optional scheduling metadata."""

        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE runtime_tasks
                    SET state = ?,
                        selected_site_id =
                            COALESCE(?, selected_site_id),
                        selected_node_name =
                            COALESCE(?, selected_node_name),
                        failure_reason = ?,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        state.value,
                        selected_site_id,
                        selected_node_name,
                        failure_reason,
                        utc_now(),
                        task_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise RuntimeTaskRepositoryError(
                        f"Runtime task does not exist: {task_id}"
                    )
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                f"Failed to update runtime task state: {task_id}"
            ) from exc

        record = self.get_task(task_id)

        if record is None:
            raise RuntimeTaskRepositoryError(
                f"Task disappeared after state update: {task_id}"
            )

        return record

    def save_reserved_plan(
        self,
        plan: KubernetesJointPlan,
    ) -> RuntimeTaskRecord:
        """Persist the optical plan and transition to RESERVED atomically."""

        timestamp = utc_now()

        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO runtime_optical_allocations (
                        task_id,
                        site_id,
                        node_name,
                        network_access_node,
                        n,
                        m,
                        fiber_links_json,
                        route_json,
                        gsnr_db,
                        total_score,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        site_id = excluded.site_id,
                        node_name = excluded.node_name,
                        network_access_node =
                            excluded.network_access_node,
                        n = excluded.n,
                        m = excluded.m,
                        fiber_links_json =
                            excluded.fiber_links_json,
                        route_json = excluded.route_json,
                        gsnr_db = excluded.gsnr_db,
                        total_score = excluded.total_score
                    """,
                    (
                        plan.task_id,
                        plan.selected_site_id,
                        plan.selected_node_name,
                        plan.network_access_node,
                        plan.spectrum_slot.n,
                        plan.spectrum_slot.m,
                        json.dumps(list(plan.fiber_links)),
                        json.dumps(list(plan.route)),
                        plan.gsnr_db,
                        plan.total_score,
                        timestamp,
                    ),
                )

                cursor = connection.execute(
                    """
                    UPDATE runtime_tasks
                    SET state = ?,
                        selected_site_id = ?,
                        selected_node_name = ?,
                        failure_reason = NULL,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        TaskState.RESERVED.value,
                        plan.selected_site_id,
                        plan.selected_node_name,
                        timestamp,
                        plan.task_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise RuntimeTaskRepositoryError(
                        f"Runtime task does not exist: {plan.task_id}"
                    )
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                f"Failed to persist selected plan: {plan.task_id}"
            ) from exc

        record = self.get_task(plan.task_id)

        if record is None:
            raise RuntimeTaskRepositoryError(
                f"Task disappeared after plan persistence: "
                f"{plan.task_id}"
            )

        return record

    def get_optical(
        self,
        task_id: str,
    ) -> RuntimeOpticalRecord | None:
        """Return one persisted optical allocation."""

        try:
            with self.database.connection() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM runtime_optical_allocations
                    WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                f"Failed to read optical allocation: {task_id}"
            ) from exc

        if row is None:
            return None

        return RuntimeOpticalRecord(
            task_id=str(row["task_id"]),
            site_id=str(row["site_id"]),
            node_name=str(row["node_name"]),
            network_access_node=str(
                row["network_access_node"]
            ),
            slot=SpectrumSlot(
                n=int(row["n"]),
                m=int(row["m"]),
            ),
            fiber_links=tuple(
                json.loads(row["fiber_links_json"])
            ),
            route=tuple(
                json.loads(row["route_json"])
            ),
            gsnr_db=float(row["gsnr_db"]),
            total_score=float(row["total_score"]),
            created_at=str(row["created_at"]),
        )

    def save_job(
        self,
        task_id: str,
        job_name: str,
        namespace: str,
        requested_site_id: str,
    ) -> RuntimeTaskRecord:
        """
        Persist a Kubernetes Job and transition the task to RUNNING.

        UPSERT is intentional: recovery can rediscover a deterministic
        Job name after a crash between Job creation and database commit.
        """

        timestamp = utc_now()

        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO runtime_kubernetes_executions (
                        task_id,
                        job_name,
                        namespace,
                        requested_site_id,
                        pod_name,
                        node_name,
                        node_site_label,
                        phase,
                        succeeded,
                        logs,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        ?, ?, ?, ?,
                        NULL, NULL, NULL,
                        ?, NULL, NULL,
                        ?, ?
                    )
                    ON CONFLICT(task_id) DO UPDATE SET
                        job_name = excluded.job_name,
                        namespace = excluded.namespace,
                        requested_site_id =
                            excluded.requested_site_id,
                        phase = excluded.phase,
                        updated_at = excluded.updated_at
                    """,
                    (
                        task_id,
                        job_name,
                        namespace,
                        requested_site_id,
                        "RUNNING",
                        timestamp,
                        timestamp,
                    ),
                )

                cursor = connection.execute(
                    """
                    UPDATE runtime_tasks
                    SET state = ?,
                        failure_reason = NULL,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        TaskState.RUNNING.value,
                        timestamp,
                        task_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise RuntimeTaskRepositoryError(
                        f"Runtime task does not exist: {task_id}"
                    )
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                f"Failed to persist Kubernetes Job: {task_id}"
            ) from exc

        record = self.get_task(task_id)

        if record is None:
            raise RuntimeTaskRepositoryError(
                f"Task disappeared after Job persistence: {task_id}"
            )

        return record

    def save_execution_result(
        self,
        task_id: str,
        pod_name: str,
        node_name: str,
        node_site_label: str | None,
        phase: str,
        succeeded: bool,
        logs: str,
    ) -> RuntimeExecutionRecord:
        """Persist the final Kubernetes execution result."""

        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE runtime_kubernetes_executions
                    SET pod_name = ?,
                        node_name = ?,
                        node_site_label = ?,
                        phase = ?,
                        succeeded = ?,
                        logs = ?,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        pod_name,
                        node_name,
                        node_site_label,
                        phase,
                        int(succeeded),
                        logs,
                        utc_now(),
                        task_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise RuntimeTaskRepositoryError(
                        f"Execution record does not exist: {task_id}"
                    )
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                f"Failed to persist execution result: {task_id}"
            ) from exc

        record = self.get_execution(task_id)

        if record is None:
            raise RuntimeTaskRepositoryError(
                f"Execution disappeared after update: {task_id}"
            )

        return record

    def mark_execution_cancelled(
        self,
        task_id: str,
    ) -> RuntimeExecutionRecord:
        """Mark an existing Kubernetes execution as cancelled."""

        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE runtime_kubernetes_executions
                    SET phase = ?,
                        succeeded = ?,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        "CANCELLED",
                        0,
                        utc_now(),
                        task_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise RuntimeTaskRepositoryError(
                        f"Execution record does not exist: {task_id}"
                    )

        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                f"Failed to cancel execution record: {task_id}"
            ) from exc

        record = self.get_execution(task_id)

        if record is None:
            raise RuntimeTaskRepositoryError(
                f"Execution disappeared after cancellation: "
                f"{task_id}"
            )

        return record

    def get_execution(
        self,
        task_id: str,
    ) -> RuntimeExecutionRecord | None:
        """Return one Kubernetes execution record."""

        try:
            with self.database.connection() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM runtime_kubernetes_executions
                    WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                f"Failed to read execution record: {task_id}"
            ) from exc

        if row is None:
            return None

        succeeded_value = row["succeeded"]

        return RuntimeExecutionRecord(
            task_id=str(row["task_id"]),
            job_name=str(row["job_name"]),
            namespace=str(row["namespace"]),
            requested_site_id=str(
                row["requested_site_id"]
            ),
            pod_name=(
                str(row["pod_name"])
                if row["pod_name"] is not None
                else None
            ),
            node_name=(
                str(row["node_name"])
                if row["node_name"] is not None
                else None
            ),
            node_site_label=(
                str(row["node_site_label"])
                if row["node_site_label"] is not None
                else None
            ),
            phase=str(row["phase"]),
            succeeded=(
                bool(int(succeeded_value))
                if succeeded_value is not None
                else None
            ),
            logs=(
                str(row["logs"])
                if row["logs"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def finish_release(
        self,
        task_id: str,
        final_state: TaskState,
        failure_reason: str | None = None,
    ) -> RuntimeTaskRecord:
        """Delete optical persistence and store the final task state."""

        if final_state not in {
            TaskState.RELEASED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }:
            raise ValueError(
                "final_state must be RELEASED, FAILED "
                "or CANCELLED."
            )

        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    DELETE FROM runtime_optical_allocations
                    WHERE task_id = ?
                    """,
                    (task_id,),
                )

                cursor = connection.execute(
                    """
                    UPDATE runtime_tasks
                    SET state = ?,
                        failure_reason = ?,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        final_state.value,
                        failure_reason,
                        utc_now(),
                        task_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise RuntimeTaskRepositoryError(
                        f"Runtime task does not exist: {task_id}"
                    )
        except sqlite3.Error as exc:
            raise RuntimeTaskRepositoryError(
                f"Failed to finish task release: {task_id}"
            ) from exc

        record = self.get_task(task_id)

        if record is None:
            raise RuntimeTaskRepositoryError(
                f"Task disappeared after release: {task_id}"
            )

        return record

    @staticmethod
    def _row_to_task(
        row: Any,
    ) -> RuntimeTaskRecord:
        task = RuntimeTaskRequest(
            task_id=str(row["task_id"]),
            source_access_node=str(
                row["source_access_node"]
            ),
            cpu_request_m=int(row["cpu_request_m"]),
            memory_request_mib=int(
                row["memory_request_mib"]
            ),
            gpu_request=int(row["gpu_request"]),
            bandwidth_gbps=float(
                row["bandwidth_gbps"]
            ),
            image=str(row["image"]),
            shell_command=str(row["shell_command"]),
            cpu_limit_m=(
                int(row["cpu_limit_m"])
                if row["cpu_limit_m"] is not None
                else None
            ),
            memory_limit_mib=(
                int(row["memory_limit_mib"])
                if row["memory_limit_mib"] is not None
                else None
            ),
        )

        return RuntimeTaskRecord(
            task=task,
            state=TaskState(str(row["state"])),
            selected_site_id=(
                str(row["selected_site_id"])
                if row["selected_site_id"] is not None
                else None
            ),
            selected_node_name=(
                str(row["selected_node_name"])
                if row["selected_node_name"] is not None
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
