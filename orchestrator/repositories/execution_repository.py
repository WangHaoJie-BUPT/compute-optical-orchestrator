"""SQLite repository for Kubernetes execution records."""

from __future__ import annotations

import sqlite3
from typing import Any

from orchestrator.models.execution import KubernetesExecutionRecord
from orchestrator.repositories.task_repository import utc_now
from orchestrator.storage.database import Database


class ExecutionRepositoryError(RuntimeError):
    """Raised when Kubernetes execution data cannot be persisted."""


class ExecutionRepository:
    """Persist Kubernetes Job execution information."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize(self) -> None:
        """Create the Kubernetes execution table."""

        schema = """
        CREATE TABLE IF NOT EXISTS kubernetes_executions (
            task_id TEXT PRIMARY KEY,
            job_name TEXT NOT NULL UNIQUE,
            namespace TEXT NOT NULL,
            requested_site TEXT NOT NULL,

            pod_name TEXT,
            node_name TEXT,
            node_site_label TEXT,

            phase TEXT NOT NULL,
            succeeded INTEGER,
            logs TEXT,

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            FOREIGN KEY (task_id)
                REFERENCES tasks(task_id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_k8s_execution_phase
            ON kubernetes_executions(phase);
        """

        try:
            with self.database.connection() as connection:
                connection.executescript(schema)
                connection.commit()
        except sqlite3.Error as exc:
            raise ExecutionRepositoryError(
                "Failed to initialize Kubernetes execution table."
            ) from exc

    def create(
        self,
        task_id: str,
        job_name: str,
        namespace: str,
        requested_site: str,
    ) -> KubernetesExecutionRecord:
        """Create an initial SUBMITTING execution record."""

        timestamp = utc_now()

        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO kubernetes_executions (
                        task_id,
                        job_name,
                        namespace,
                        requested_site,
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
                    """,
                    (
                        task_id,
                        job_name,
                        namespace,
                        requested_site,
                        "SUBMITTING",
                        timestamp,
                        timestamp,
                    ),
                )

        except sqlite3.IntegrityError as exc:
            raise ExecutionRepositoryError(
                f"Kubernetes execution already exists for task: {task_id}"
            ) from exc
        except sqlite3.Error as exc:
            raise ExecutionRepositoryError(
                f"Failed to create Kubernetes execution: {task_id}"
            ) from exc

        record = self.get(task_id)

        if record is None:
            raise ExecutionRepositoryError(
                f"Execution record disappeared after creation: {task_id}"
            )

        return record

    def update_phase(
        self,
        task_id: str,
        phase: str,
    ) -> None:
        """Update only the execution phase."""

        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE kubernetes_executions
                    SET phase = ?,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        phase,
                        utc_now(),
                        task_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise ExecutionRepositoryError(
                        f"Execution record does not exist: {task_id}"
                    )

        except sqlite3.Error as exc:
            raise ExecutionRepositoryError(
                f"Failed to update execution phase: {task_id}"
            ) from exc

    def save_result(
        self,
        task_id: str,
        pod_name: str,
        node_name: str,
        node_site_label: str | None,
        phase: str,
        succeeded: bool,
        logs: str,
    ) -> None:
        """Save the final Kubernetes execution result."""

        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE kubernetes_executions
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
                    raise ExecutionRepositoryError(
                        f"Execution record does not exist: {task_id}"
                    )

        except sqlite3.Error as exc:
            raise ExecutionRepositoryError(
                f"Failed to save execution result: {task_id}"
            ) from exc

    def get(
        self,
        task_id: str,
    ) -> KubernetesExecutionRecord | None:
        """Return one Kubernetes execution record."""

        try:
            with self.database.connection() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM kubernetes_executions
                    WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()

        except sqlite3.Error as exc:
            raise ExecutionRepositoryError(
                f"Failed to read Kubernetes execution: {task_id}"
            ) from exc

        if row is None:
            return None

        return self._row_to_record(row)

    def list_all(self) -> list[KubernetesExecutionRecord]:
        """Return all execution records."""

        try:
            with self.database.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM kubernetes_executions
                    ORDER BY created_at, task_id
                    """
                ).fetchall()

        except sqlite3.Error as exc:
            raise ExecutionRepositoryError(
                "Failed to list Kubernetes executions."
            ) from exc

        return [
            self._row_to_record(row)
            for row in rows
        ]

    @staticmethod
    def _row_to_record(
        row: Any,
    ) -> KubernetesExecutionRecord:
        succeeded_value = row["succeeded"]

        if succeeded_value is None:
            succeeded = None
        else:
            succeeded = bool(int(succeeded_value))

        return KubernetesExecutionRecord(
            task_id=str(row["task_id"]),
            job_name=str(row["job_name"]),
            namespace=str(row["namespace"]),
            requested_site=str(row["requested_site"]),
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
            succeeded=succeeded,
            logs=(
                str(row["logs"])
                if row["logs"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
