"""SQLite-backed durable admission queue."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from orchestrator.models.admission import (
    AdmissionQueueStats,
    AdmissionRecord,
    AdmissionState,
)
from orchestrator.models.runtime_task import RuntimeTaskRequest
from orchestrator.storage.database import Database


TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class AdmissionQueueRepositoryError(RuntimeError):
    """Raised when admission queue persistence fails."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AdmissionQueueRepository:
    """Persist queue state and atomically select fair candidates."""

    ACTIVE_STATES = (
        AdmissionState.QUEUED,
        AdmissionState.DISPATCHING,
    )

    def __init__(
        self,
        database: Database,
        aging_seconds: float = 60.0,
        dispatch_timeout_seconds: float = 300.0,
    ) -> None:
        if aging_seconds <= 0:
            raise ValueError("aging_seconds must be positive.")
        self.database = database
        self.aging_seconds = aging_seconds
        if dispatch_timeout_seconds <= 0:
            raise ValueError(
                "dispatch_timeout_seconds must be positive."
            )
        self.dispatch_timeout_seconds = dispatch_timeout_seconds

    def initialize(self) -> int:
        """Create queue tables and recover interrupted dispatches."""

        schema = """
        CREATE TABLE IF NOT EXISTS runtime_admission_queue (
            task_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            priority INTEGER NOT NULL,
            state TEXT NOT NULL,
            task_json TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            not_before REAL NOT NULL DEFAULT 0,
            claimed_by TEXT,
            claim_expires_at REAL,
            runtime_state TEXT,
            last_error TEXT,
            enqueued_at REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            admitted_at TEXT
        );

        CREATE TABLE IF NOT EXISTS runtime_admission_tenants (
            tenant_id TEXT PRIMARY KEY,
            last_selected_at REAL NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_admission_state_ready
            ON runtime_admission_queue(state, not_before);

        CREATE INDEX IF NOT EXISTS idx_admission_tenant_state
            ON runtime_admission_queue(tenant_id, state);
        """
        try:
            with self.database.connection() as connection:
                connection.executescript(schema)
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(runtime_admission_queue)"
                    ).fetchall()
                }
                if "claim_expires_at" not in columns:
                    connection.execute(
                        "ALTER TABLE runtime_admission_queue "
                        "ADD COLUMN claim_expires_at REAL"
                    )
                connection.commit()
        except sqlite3.Error as exc:
            raise AdmissionQueueRepositoryError(
                "Failed to initialize admission queue."
            ) from exc
        return self.recover_stale_dispatches()

    def recover_stale_dispatches(
        self,
        now: float | None = None,
    ) -> int:
        current_time = time.time() if now is None else now
        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE runtime_admission_queue
                    SET state = ?,
                        claimed_by = NULL,
                        claim_expires_at = NULL,
                        last_error = CASE
                            WHEN last_error IS NULL THEN ?
                            ELSE last_error || '; ' || ?
                        END,
                        not_before = ?,
                        updated_at = ?
                    WHERE state = ?
                      AND (
                          claim_expires_at IS NULL
                          OR claim_expires_at <= ?
                      )
                    """,
                    (
                        AdmissionState.QUEUED.value,
                        "Recovered interrupted dispatch.",
                        "Recovered interrupted dispatch.",
                        current_time,
                        utc_now(),
                        AdmissionState.DISPATCHING.value,
                        current_time,
                    ),
                )
                return int(cursor.rowcount)
        except sqlite3.Error as exc:
            raise AdmissionQueueRepositoryError(
                "Failed to recover stale admission dispatches."
            ) from exc

    def enqueue(
        self,
        *,
        task: RuntimeTaskRequest,
        tenant_id: str,
        priority: int,
        max_active_total: int,
        max_active_per_tenant: int,
    ) -> tuple[AdmissionRecord, bool]:
        if TENANT_ID_PATTERN.fullmatch(tenant_id) is None:
            raise AdmissionQueueRepositoryError(
                "tenant_id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}."
            )
        if not 0 <= priority <= 100:
            raise AdmissionQueueRepositoryError(
                "priority must be between 0 and 100."
            )
        if max_active_total <= 0 or max_active_per_tenant <= 0:
            raise ValueError("Queue limits must be positive.")

        task_json = json.dumps(
            task.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        now_epoch = time.time()
        timestamp = utc_now()
        active_values = tuple(
            state.value for state in self.ACTIVE_STATES
        )

        try:
            with self.database.transaction() as connection:
                existing = connection.execute(
                    "SELECT * FROM runtime_admission_queue "
                    "WHERE task_id = ?",
                    (task.task_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["task_json"]) != task_json
                        or str(existing["tenant_id"]) != tenant_id
                        or int(existing["priority"]) != priority
                    ):
                        raise AdmissionQueueRepositoryError(
                            f"Queue task {task.task_id!r} already exists "
                            "with different parameters."
                        )
                    return self._row_to_record(existing), True

                total_active = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM runtime_admission_queue
                        WHERE state IN (?, ?)
                        """,
                        active_values,
                    ).fetchone()[0]
                )
                if total_active >= max_active_total:
                    raise AdmissionQueueRepositoryError(
                        "Admission queue capacity is exhausted."
                    )

                tenant_active = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM runtime_admission_queue
                        WHERE tenant_id = ?
                          AND state IN (?, ?)
                        """,
                        (tenant_id, *active_values),
                    ).fetchone()[0]
                )
                if tenant_active >= max_active_per_tenant:
                    raise AdmissionQueueRepositoryError(
                        f"Tenant queue limit is exhausted: {tenant_id}."
                    )

                connection.execute(
                    """
                    INSERT INTO runtime_admission_queue (
                        task_id, tenant_id, priority, state, task_json,
                        attempt_count, not_before, claimed_by,
                        claim_expires_at,
                        runtime_state, last_error, enqueued_at,
                        created_at, updated_at, admitted_at
                    )
                    VALUES (?, ?, ?, ?, ?, 0, ?, NULL, NULL, NULL, NULL,
                            ?, ?, ?, NULL)
                    """,
                    (
                        task.task_id,
                        tenant_id,
                        priority,
                        AdmissionState.QUEUED.value,
                        task_json,
                        now_epoch,
                        now_epoch,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO runtime_admission_tenants (
                        tenant_id, last_selected_at
                    ) VALUES (?, 0)
                    """,
                    (tenant_id,),
                )
        except sqlite3.Error as exc:
            raise AdmissionQueueRepositoryError(
                f"Failed to enqueue task: {task.task_id}"
            ) from exc

        record = self.get(task.task_id)
        if record is None:
            raise AdmissionQueueRepositoryError(
                f"Queue task disappeared after enqueue: {task.task_id}"
            )
        return record, False

    def claim_next(
        self,
        worker_id: str,
        now: float | None = None,
    ) -> AdmissionRecord | None:
        current_time = time.time() if now is None else now
        try:
            with self.database.transaction() as connection:
                rows = connection.execute(
                    """
                    SELECT q.*, COALESCE(t.last_selected_at, 0)
                        AS tenant_last_selected_at
                    FROM runtime_admission_queue AS q
                    LEFT JOIN runtime_admission_tenants AS t
                        ON t.tenant_id = q.tenant_id
                    WHERE q.state = ? AND q.not_before <= ?
                    """,
                    (AdmissionState.QUEUED.value, current_time),
                ).fetchall()
                if not rows:
                    return None

                per_tenant: dict[str, list[Any]] = {}
                for row in rows:
                    per_tenant.setdefault(
                        str(row["tenant_id"]), []
                    ).append(row)

                tenant_id = min(
                    per_tenant,
                    key=lambda item: (
                        float(
                            per_tenant[item][0][
                                "tenant_last_selected_at"
                            ]
                        ),
                        min(
                            float(row["enqueued_at"])
                            for row in per_tenant[item]
                        ),
                        item,
                    ),
                )

                def task_order(row: Any) -> tuple[float, float, str]:
                    age = max(
                        current_time - float(row["enqueued_at"]),
                        0.0,
                    )
                    effective_priority = int(row["priority"]) + int(
                        age / self.aging_seconds
                    )
                    return (
                        -float(effective_priority),
                        float(row["enqueued_at"]),
                        str(row["task_id"]),
                    )

                selected = min(
                    per_tenant[tenant_id],
                    key=task_order,
                )
                timestamp = utc_now()
                cursor = connection.execute(
                    """
                    UPDATE runtime_admission_queue
                    SET state = ?, claimed_by = ?,
                        claim_expires_at = ?,
                        attempt_count = attempt_count + 1,
                        updated_at = ?
                    WHERE task_id = ? AND state = ?
                    """,
                    (
                        AdmissionState.DISPATCHING.value,
                        worker_id,
                        current_time + self.dispatch_timeout_seconds,
                        timestamp,
                        str(selected["task_id"]),
                        AdmissionState.QUEUED.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AdmissionQueueRepositoryError(
                        "Concurrent admission claim detected."
                    )
                connection.execute(
                    """
                    UPDATE runtime_admission_tenants
                    SET last_selected_at = ?
                    WHERE tenant_id = ?
                    """,
                    (current_time, tenant_id),
                )
        except sqlite3.Error as exc:
            raise AdmissionQueueRepositoryError(
                "Failed to claim an admission task."
            ) from exc

        return self.get(str(selected["task_id"]))

    def mark_admitted(
        self,
        task_id: str,
        runtime_state: str,
    ) -> AdmissionRecord:
        return self._finish(
            task_id=task_id,
            state=AdmissionState.ADMITTED,
            runtime_state=runtime_state,
            error=None,
            admitted=True,
        )

    def reject(self, task_id: str, error: str) -> AdmissionRecord:
        return self._finish(
            task_id=task_id,
            state=AdmissionState.REJECTED,
            runtime_state=None,
            error=error,
            admitted=False,
        )

    def requeue(
        self,
        task_id: str,
        error: str,
        delay_seconds: float,
    ) -> AdmissionRecord:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative.")
        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE runtime_admission_queue
                    SET state = ?, claimed_by = NULL,
                        claim_expires_at = NULL,
                        last_error = ?, not_before = ?, updated_at = ?
                    WHERE task_id = ? AND state = ?
                    """,
                    (
                        AdmissionState.QUEUED.value,
                        error,
                        time.time() + delay_seconds,
                        utc_now(),
                        task_id,
                        AdmissionState.DISPATCHING.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AdmissionQueueRepositoryError(
                        f"Queue task is not dispatching: {task_id}"
                    )
        except sqlite3.Error as exc:
            raise AdmissionQueueRepositoryError(
                f"Failed to requeue task: {task_id}"
            ) from exc
        return self._require(task_id)

    def cancel(self, task_id: str) -> AdmissionRecord:
        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE runtime_admission_queue
                    SET state = ?, claimed_by = NULL, updated_at = ?
                    WHERE task_id = ? AND state = ?
                    """,
                    (
                        AdmissionState.CANCELLED.value,
                        utc_now(),
                        task_id,
                        AdmissionState.QUEUED.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AdmissionQueueRepositoryError(
                        "Only a queued task can be cancelled."
                    )
        except sqlite3.Error as exc:
            raise AdmissionQueueRepositoryError(
                f"Failed to cancel queued task: {task_id}"
            ) from exc
        return self._require(task_id)

    def get(self, task_id: str) -> AdmissionRecord | None:
        try:
            with self.database.connection() as connection:
                row = connection.execute(
                    "SELECT * FROM runtime_admission_queue "
                    "WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise AdmissionQueueRepositoryError(
                f"Failed to read queued task: {task_id}"
            ) from exc
        return self._row_to_record(row) if row is not None else None

    def list(
        self,
        states: Iterable[AdmissionState] | None = None,
    ) -> list[AdmissionRecord]:
        values = tuple(state.value for state in states or ())
        try:
            with self.database.connection() as connection:
                if values:
                    placeholders = ", ".join("?" for _ in values)
                    rows = connection.execute(
                        "SELECT * FROM runtime_admission_queue "
                        f"WHERE state IN ({placeholders}) "
                        "ORDER BY enqueued_at, task_id",
                        values,
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT * FROM runtime_admission_queue "
                        "ORDER BY enqueued_at, task_id"
                    ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionQueueRepositoryError(
                "Failed to list admission queue."
            ) from exc
        return [self._row_to_record(row) for row in rows]

    def stats(self) -> AdmissionQueueStats:
        counts = {state: 0 for state in AdmissionState}
        try:
            with self.database.connection() as connection:
                rows = connection.execute(
                    "SELECT state, COUNT(*) AS count "
                    "FROM runtime_admission_queue GROUP BY state"
                ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionQueueRepositoryError(
                "Failed to read admission queue statistics."
            ) from exc
        for row in rows:
            counts[AdmissionState(str(row["state"]))] = int(
                row["count"]
            )
        return AdmissionQueueStats(
            queued=counts[AdmissionState.QUEUED],
            dispatching=counts[AdmissionState.DISPATCHING],
            admitted=counts[AdmissionState.ADMITTED],
            rejected=counts[AdmissionState.REJECTED],
            cancelled=counts[AdmissionState.CANCELLED],
        )

    def _finish(
        self,
        *,
        task_id: str,
        state: AdmissionState,
        runtime_state: str | None,
        error: str | None,
        admitted: bool,
    ) -> AdmissionRecord:
        timestamp = utc_now()
        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE runtime_admission_queue
                    SET state = ?, claimed_by = NULL,
                        claim_expires_at = NULL,
                        runtime_state = ?, last_error = ?,
                        admitted_at = ?, updated_at = ?
                    WHERE task_id = ? AND state = ?
                    """,
                    (
                        state.value,
                        runtime_state,
                        error,
                        timestamp if admitted else None,
                        timestamp,
                        task_id,
                        AdmissionState.DISPATCHING.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AdmissionQueueRepositoryError(
                        f"Queue task is not dispatching: {task_id}"
                    )
        except sqlite3.Error as exc:
            raise AdmissionQueueRepositoryError(
                f"Failed to finish admission task: {task_id}"
            ) from exc
        return self._require(task_id)

    def _require(self, task_id: str) -> AdmissionRecord:
        record = self.get(task_id)
        if record is None:
            raise AdmissionQueueRepositoryError(
                f"Admission task does not exist: {task_id}"
            )
        return record

    @staticmethod
    def _row_to_record(row: Any) -> AdmissionRecord:
        try:
            task_data = json.loads(str(row["task_json"]))
            task = RuntimeTaskRequest.from_dict(task_data)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AdmissionQueueRepositoryError(
                f"Invalid persisted task JSON: {row['task_id']}"
            ) from exc
        return AdmissionRecord(
            task=task,
            tenant_id=str(row["tenant_id"]),
            priority=int(row["priority"]),
            state=AdmissionState(str(row["state"])),
            attempt_count=int(row["attempt_count"]),
            not_before=float(row["not_before"]),
            claimed_by=(
                str(row["claimed_by"])
                if row["claimed_by"] is not None
                else None
            ),
            runtime_state=(
                str(row["runtime_state"])
                if row["runtime_state"] is not None
                else None
            ),
            last_error=(
                str(row["last_error"])
                if row["last_error"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            admitted_at=(
                str(row["admitted_at"])
                if row["admitted_at"] is not None
                else None
            ),
        )
