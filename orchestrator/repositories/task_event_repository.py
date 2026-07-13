"""SQLite repository for immutable runtime task events."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from orchestrator.models.runtime_event import (
    RuntimeTaskEventRecord,
)
from orchestrator.models.task_state import TaskState
from orchestrator.storage.database import Database


class TaskEventRepositoryError(RuntimeError):
    """Raised when task audit events cannot be managed."""


def utc_now() -> str:
    """Return current UTC time in ISO-8601 form."""

    return datetime.now(timezone.utc).isoformat()


class TaskEventRepository:
    """
    Persist immutable lifecycle events.

    SQLite triggers capture task states, optical allocations and
    Kubernetes execution phases in the same transaction as the source
    database change.
    """

    def __init__(
        self,
        database: Database,
    ) -> None:
        self.database = database

    def initialize(self) -> None:
        """Create the event table, indexes and audit triggers."""

        schema = """
        CREATE TABLE IF NOT EXISTS runtime_task_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,

            event_type TEXT NOT NULL,

            previous_state TEXT,
            new_state TEXT,

            source TEXT NOT NULL,
            message TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',

            created_at TEXT NOT NULL,

            FOREIGN KEY (task_id)
                REFERENCES runtime_tasks(task_id)
                ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS
            idx_runtime_task_events_task_id
        ON runtime_task_events(task_id, event_id);

        CREATE INDEX IF NOT EXISTS
            idx_runtime_task_events_type
        ON runtime_task_events(event_type, event_id);

        CREATE TRIGGER IF NOT EXISTS
            trg_runtime_task_created
        AFTER INSERT ON runtime_tasks
        BEGIN
            INSERT INTO runtime_task_events (
                task_id,
                event_type,
                previous_state,
                new_state,
                source,
                message,
                details_json,
                created_at
            )
            VALUES (
                NEW.task_id,
                'TASK_CREATED',
                NULL,
                NEW.state,
                'database-trigger',
                'Runtime task created.',
                '{}',
                NEW.created_at
            );
        END;

        CREATE TRIGGER IF NOT EXISTS
            trg_runtime_task_state_changed
        AFTER UPDATE OF state ON runtime_tasks
        WHEN OLD.state IS NOT NEW.state
        BEGIN
            INSERT INTO runtime_task_events (
                task_id,
                event_type,
                previous_state,
                new_state,
                source,
                message,
                details_json,
                created_at
            )
            VALUES (
                NEW.task_id,
                'STATE_TRANSITION',
                OLD.state,
                NEW.state,
                'database-trigger',
                'Runtime task state changed.',
                json_object(
                    'selected_site_id',
                    NEW.selected_site_id,
                    'selected_node_name',
                    NEW.selected_node_name,
                    'failure_reason',
                    NEW.failure_reason
                ),
                NEW.updated_at
            );
        END;

        CREATE TRIGGER IF NOT EXISTS
            trg_runtime_optical_created
        AFTER INSERT ON runtime_optical_allocations
        BEGIN
            INSERT INTO runtime_task_events (
                task_id,
                event_type,
                previous_state,
                new_state,
                source,
                message,
                details_json,
                created_at
            )
            VALUES (
                NEW.task_id,
                'OPTICAL_ALLOCATION_CREATED',
                NULL,
                NULL,
                'database-trigger',
                'Optical route and spectrum reserved.',
                json_object(
                    'site_id',
                    NEW.site_id,
                    'node_name',
                    NEW.node_name,
                    'network_access_node',
                    NEW.network_access_node,
                    'n',
                    NEW.n,
                    'm',
                    NEW.m,
                    'gsnr_db',
                    NEW.gsnr_db,
                    'total_score',
                    NEW.total_score
                ),
                NEW.created_at
            );
        END;

        CREATE TRIGGER IF NOT EXISTS
            trg_runtime_optical_deleted
        AFTER DELETE ON runtime_optical_allocations
        BEGIN
            INSERT INTO runtime_task_events (
                task_id,
                event_type,
                previous_state,
                new_state,
                source,
                message,
                details_json,
                created_at
            )
            VALUES (
                OLD.task_id,
                'OPTICAL_ALLOCATION_RELEASED',
                NULL,
                NULL,
                'database-trigger',
                'Optical route and spectrum released.',
                json_object(
                    'site_id',
                    OLD.site_id,
                    'node_name',
                    OLD.node_name,
                    'n',
                    OLD.n,
                    'm',
                    OLD.m
                ),
                strftime(
                    '%Y-%m-%dT%H:%M:%fZ',
                    'now'
                )
            );
        END;

        CREATE TRIGGER IF NOT EXISTS
            trg_runtime_execution_created
        AFTER INSERT ON runtime_kubernetes_executions
        BEGIN
            INSERT INTO runtime_task_events (
                task_id,
                event_type,
                previous_state,
                new_state,
                source,
                message,
                details_json,
                created_at
            )
            VALUES (
                NEW.task_id,
                'KUBERNETES_EXECUTION_CREATED',
                NULL,
                NULL,
                'database-trigger',
                'Kubernetes Job execution created.',
                json_object(
                    'job_name',
                    NEW.job_name,
                    'namespace',
                    NEW.namespace,
                    'requested_site_id',
                    NEW.requested_site_id,
                    'phase',
                    NEW.phase
                ),
                NEW.created_at
            );
        END;

        CREATE TRIGGER IF NOT EXISTS
            trg_runtime_execution_phase_changed
        AFTER UPDATE OF phase
        ON runtime_kubernetes_executions
        WHEN OLD.phase IS NOT NEW.phase
        BEGIN
            INSERT INTO runtime_task_events (
                task_id,
                event_type,
                previous_state,
                new_state,
                source,
                message,
                details_json,
                created_at
            )
            VALUES (
                NEW.task_id,
                'KUBERNETES_EXECUTION_PHASE_CHANGED',
                NULL,
                NULL,
                'database-trigger',
                'Kubernetes execution phase changed.',
                json_object(
                    'previous_phase',
                    OLD.phase,
                    'new_phase',
                    NEW.phase,
                    'job_name',
                    NEW.job_name,
                    'pod_name',
                    NEW.pod_name,
                    'node_name',
                    NEW.node_name,
                    'node_site_label',
                    NEW.node_site_label,
                    'succeeded',
                    NEW.succeeded
                ),
                NEW.updated_at
            );
        END;
        """

        try:
            with self.database.connection() as connection:
                connection.executescript(schema)

                # Existing tasks predate the audit subsystem. Create one
                # baseline event without inventing unavailable history.
                connection.execute(
                    """
                    INSERT INTO runtime_task_events (
                        task_id,
                        event_type,
                        previous_state,
                        new_state,
                        source,
                        message,
                        details_json,
                        created_at
                    )
                    SELECT
                        task.task_id,
                        'AUDIT_BASELINE',
                        NULL,
                        task.state,
                        'audit-migration',
                        'Audit tracking enabled for an existing task.',
                        json_object(
                            'selected_site_id',
                            task.selected_site_id,
                            'selected_node_name',
                            task.selected_node_name
                        ),
                        task.updated_at
                    FROM runtime_tasks AS task
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM runtime_task_events AS event
                        WHERE event.task_id = task.task_id
                    )
                    """
                )

                connection.commit()

        except sqlite3.Error as exc:
            raise TaskEventRepositoryError(
                "Failed to initialize task-event auditing."
            ) from exc

    def append(
        self,
        *,
        task_id: str,
        event_type: str,
        source: str,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        previous_state: TaskState | None = None,
        new_state: TaskState | None = None,
    ) -> RuntimeTaskEventRecord:
        """Append an explicit application-level event."""

        if not event_type:
            raise ValueError(
                "event_type cannot be empty."
            )

        if not source:
            raise ValueError(
                "source cannot be empty."
            )

        created_at = utc_now()

        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO runtime_task_events (
                        task_id,
                        event_type,
                        previous_state,
                        new_state,
                        source,
                        message,
                        details_json,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        event_type,
                        (
                            previous_state.value
                            if previous_state is not None
                            else None
                        ),
                        (
                            new_state.value
                            if new_state is not None
                            else None
                        ),
                        source,
                        message,
                        json.dumps(
                            details or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        created_at,
                    ),
                )

                event_id = int(cursor.lastrowid)

        except sqlite3.IntegrityError as exc:
            raise TaskEventRepositoryError(
                f"Task does not exist: {task_id}"
            ) from exc
        except sqlite3.Error as exc:
            raise TaskEventRepositoryError(
                f"Failed to append event for task: {task_id}"
            ) from exc

        record = self.get(event_id)

        if record is None:
            raise TaskEventRepositoryError(
                f"Event disappeared after insertion: {event_id}"
            )

        return record

    def get(
        self,
        event_id: int,
    ) -> RuntimeTaskEventRecord | None:
        """Return one event by its numeric ID."""

        try:
            with self.database.connection() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM runtime_task_events
                    WHERE event_id = ?
                    """,
                    (event_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise TaskEventRepositoryError(
                f"Failed to read task event: {event_id}"
            ) from exc

        if row is None:
            return None

        return self._row_to_record(row)

    def list_for_task(
        self,
        task_id: str,
        *,
        limit: int = 200,
        after_event_id: int | None = None,
    ) -> list[RuntimeTaskEventRecord]:
        """Return events in ascending creation order."""

        if not 1 <= limit <= 1000:
            raise ValueError(
                "limit must be between 1 and 1000."
            )

        try:
            with self.database.connection() as connection:
                if after_event_id is None:
                    rows = connection.execute(
                        """
                        SELECT *
                        FROM runtime_task_events
                        WHERE task_id = ?
                        ORDER BY event_id ASC
                        LIMIT ?
                        """,
                        (
                            task_id,
                            limit,
                        ),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT *
                        FROM runtime_task_events
                        WHERE task_id = ?
                          AND event_id > ?
                        ORDER BY event_id ASC
                        LIMIT ?
                        """,
                        (
                            task_id,
                            after_event_id,
                            limit,
                        ),
                    ).fetchall()

        except sqlite3.Error as exc:
            raise TaskEventRepositoryError(
                f"Failed to list events for task: {task_id}"
            ) from exc

        return [
            self._row_to_record(row)
            for row in rows
        ]

    @staticmethod
    def _row_to_record(
        row: Any,
    ) -> RuntimeTaskEventRecord:
        try:
            details = json.loads(
                str(row["details_json"])
            )
        except json.JSONDecodeError:
            details = {
                "raw_details": str(
                    row["details_json"]
                )
            }

        if not isinstance(details, dict):
            details = {
                "value": details,
            }

        previous_value = row["previous_state"]
        new_value = row["new_state"]

        return RuntimeTaskEventRecord(
            event_id=int(row["event_id"]),
            task_id=str(row["task_id"]),
            event_type=str(row["event_type"]),
            previous_state=(
                TaskState(str(previous_value))
                if previous_value is not None
                else None
            ),
            new_state=(
                TaskState(str(new_value))
                if new_value is not None
                else None
            ),
            source=str(row["source"]),
            message=(
                str(row["message"])
                if row["message"] is not None
                else None
            ),
            details=details,
            created_at=str(row["created_at"]),
        )
