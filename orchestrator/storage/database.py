"""SQLite database connection and schema management."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class DatabaseError(RuntimeError):
    """Raised when database initialization or access fails."""


class Database:
    """Manage SQLite connections and transactions."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        """Create all required tables and indexes."""

        schema = """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            source_access_node TEXT NOT NULL,
            cpu_required INTEGER NOT NULL,
            memory_gb_required INTEGER NOT NULL,
            gpu_required INTEGER NOT NULL,
            bandwidth_gbps REAL NOT NULL,

            state TEXT NOT NULL,
            selected_site TEXT,
            failure_reason TEXT,

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS compute_allocations (
            task_id TEXT PRIMARY KEY,
            allocation_id TEXT NOT NULL UNIQUE,
            site_id TEXT NOT NULL,
            cpu INTEGER NOT NULL,
            memory_gb INTEGER NOT NULL,
            gpu INTEGER NOT NULL,

            FOREIGN KEY (task_id)
                REFERENCES tasks(task_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS optical_allocations (
            task_id TEXT PRIMARY KEY,
            allocation_id TEXT NOT NULL UNIQUE,

            n INTEGER NOT NULL,
            m INTEGER NOT NULL,

            links_json TEXT NOT NULL,
            route_json TEXT NOT NULL,

            gsnr_db REAL NOT NULL,
            total_score REAL NOT NULL,

            FOREIGN KEY (task_id)
                REFERENCES tasks(task_id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_state
            ON tasks(state);

        CREATE INDEX IF NOT EXISTS idx_compute_site
            ON compute_allocations(site_id);
        """

        try:
            with self.connection() as connection:
                connection.executescript(schema)
                connection.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Failed to initialize database: {self.database_path}"
            ) from exc

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Open one configured SQLite connection."""

        connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
        )

        connection.row_factory = sqlite3.Row

        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA journal_mode = WAL")

            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open an immediate write transaction."""

        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
