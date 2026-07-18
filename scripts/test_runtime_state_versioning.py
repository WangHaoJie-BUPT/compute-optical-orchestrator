"""Test runtime state transitions, version CAS, and schema migration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.models.runtime_task import (  # noqa: E402
    RuntimeTaskRequest,
)
from orchestrator.models.task_state import TaskState  # noqa: E402
from orchestrator.repositories.runtime_task_repository import (  # noqa: E402
    RuntimeTaskRepository,
    RuntimeTaskRepositoryError,
)
from orchestrator.storage.database import Database  # noqa: E402


DATABASE_FILE = PROJECT_ROOT / "runtime" / "state-versioning-test.db"
TASK_ID = "state-versioning-task"


def remove_database() -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(DATABASE_FILE) + suffix)
        if candidate.exists():
            candidate.unlink()


def create_legacy_runtime_table(database: Database) -> None:
    """Create the pre-version runtime_tasks schema."""

    with database.connection() as connection:
        connection.execute(
            """
            CREATE TABLE runtime_tasks (
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
            )
            """
        )
        connection.commit()


class RuntimeStateVersioningTest(unittest.TestCase):
    def test_migration_transitions_and_stale_version(self) -> None:
        remove_database()
        database = Database(DATABASE_FILE)
        create_legacy_runtime_table(database)

        repository = RuntimeTaskRepository(database)
        repository.initialize()

        with database.connection() as connection:
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(runtime_tasks)"
                ).fetchall()
            }

        self.assertIn("version", columns)

        task = RuntimeTaskRequest(
            task_id=TASK_ID,
            source_access_node="trx Lorient_KMA",
            cpu_request_m=500,
            memory_request_mib=256,
            gpu_request=0,
            bandwidth_gbps=100,
            image="busybox:1.36",
            shell_command="echo safe",
        )
        created = repository.create_task(task)
        self.assertEqual(created.state, TaskState.PENDING)
        self.assertEqual(created.version, 0)

        planning = repository.set_state(
            task_id=TASK_ID,
            state=TaskState.PLANNING,
            expected_version=created.version,
        )
        self.assertEqual(planning.version, 1)

        with self.assertRaisesRegex(
            RuntimeTaskRepositoryError,
            "Stale runtime task version",
        ):
            repository.set_state(
                task_id=TASK_ID,
                state=TaskState.BLOCKED,
                expected_version=created.version,
            )

        with self.assertRaisesRegex(
            RuntimeTaskRepositoryError,
            "Invalid runtime state transition",
        ):
            repository.set_state(
                task_id=TASK_ID,
                state=TaskState.RUNNING,
                expected_version=planning.version,
            )

        unchanged = repository.get_task(TASK_ID)
        self.assertIsNotNone(unchanged)
        self.assertEqual(unchanged.state, TaskState.PLANNING)
        self.assertEqual(unchanged.version, 1)

        blocked = repository.set_state(
            task_id=TASK_ID,
            state=TaskState.BLOCKED,
            expected_version=planning.version,
        )
        self.assertEqual(blocked.version, 2)


if __name__ == "__main__":
    unittest.main()
