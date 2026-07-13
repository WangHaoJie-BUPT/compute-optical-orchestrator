"""SQLite-backed controller leader lease."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from orchestrator.storage.database import Database


class ControllerLeaseRepositoryError(RuntimeError):
    """Raised when a controller lease cannot be managed."""


@dataclass(frozen=True)
class ControllerLease:
    """One persisted controller lease."""

    lease_name: str
    holder_id: str
    expires_at: float
    updated_at: float


class ControllerLeaseRepository:
    """
    Provide a lightweight single-controller lease.

    SQLite BEGIN IMMEDIATE serializes competing lease acquisitions.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS runtime_controller_leases (
            lease_name TEXT PRIMARY KEY,
            holder_id TEXT NOT NULL,
            expires_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        """

        try:
            with self.database.connection() as connection:
                connection.executescript(schema)
                connection.commit()
        except sqlite3.Error as exc:
            raise ControllerLeaseRepositoryError(
                "Failed to initialize controller lease table."
            ) from exc

    def try_acquire(
        self,
        lease_name: str,
        holder_id: str,
        lease_seconds: float,
    ) -> bool:
        """Acquire an absent, expired, or already-owned lease."""

        now = time.time()
        expires_at = now + lease_seconds

        try:
            with self.database.transaction() as connection:
                row = connection.execute(
                    """
                    SELECT
                        holder_id,
                        expires_at
                    FROM runtime_controller_leases
                    WHERE lease_name = ?
                    """,
                    (lease_name,),
                ).fetchone()

                if row is None:
                    connection.execute(
                        """
                        INSERT INTO runtime_controller_leases (
                            lease_name,
                            holder_id,
                            expires_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            lease_name,
                            holder_id,
                            expires_at,
                            now,
                        ),
                    )

                    return True

                current_holder = str(row["holder_id"])
                current_expiry = float(row["expires_at"])

                if (
                    current_holder != holder_id
                    and current_expiry > now
                ):
                    return False

                connection.execute(
                    """
                    UPDATE runtime_controller_leases
                    SET holder_id = ?,
                        expires_at = ?,
                        updated_at = ?
                    WHERE lease_name = ?
                    """,
                    (
                        holder_id,
                        expires_at,
                        now,
                        lease_name,
                    ),
                )

                return True

        except sqlite3.Error as exc:
            raise ControllerLeaseRepositoryError(
                f"Failed to acquire controller lease: {lease_name}"
            ) from exc

    def renew(
        self,
        lease_name: str,
        holder_id: str,
        lease_seconds: float,
    ) -> bool:
        """Renew a lease only when it is still owned by the caller."""

        now = time.time()

        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE runtime_controller_leases
                    SET expires_at = ?,
                        updated_at = ?
                    WHERE lease_name = ?
                      AND holder_id = ?
                    """,
                    (
                        now + lease_seconds,
                        now,
                        lease_name,
                        holder_id,
                    ),
                )

                return cursor.rowcount == 1

        except sqlite3.Error as exc:
            raise ControllerLeaseRepositoryError(
                f"Failed to renew controller lease: {lease_name}"
            ) from exc

    def release(
        self,
        lease_name: str,
        holder_id: str,
    ) -> bool:
        """Release a lease only when it belongs to the caller."""

        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """
                    DELETE FROM runtime_controller_leases
                    WHERE lease_name = ?
                      AND holder_id = ?
                    """,
                    (
                        lease_name,
                        holder_id,
                    ),
                )

                return cursor.rowcount == 1

        except sqlite3.Error as exc:
            raise ControllerLeaseRepositoryError(
                f"Failed to release controller lease: {lease_name}"
            ) from exc

    def get(
        self,
        lease_name: str,
    ) -> ControllerLease | None:
        """Return one current lease."""

        try:
            with self.database.connection() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM runtime_controller_leases
                    WHERE lease_name = ?
                    """,
                    (lease_name,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ControllerLeaseRepositoryError(
                f"Failed to read controller lease: {lease_name}"
            ) from exc

        if row is None:
            return None

        return ControllerLease(
            lease_name=str(row["lease_name"]),
            holder_id=str(row["holder_id"]),
            expires_at=float(row["expires_at"]),
            updated_at=float(row["updated_at"]),
        )
