"""Immutable runtime task audit-event model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestrator.models.task_state import TaskState


@dataclass(frozen=True)
class RuntimeTaskEventRecord:
    """One immutable task lifecycle event."""

    event_id: int
    task_id: str
    event_type: str

    previous_state: TaskState | None
    new_state: TaskState | None

    source: str
    message: str | None
    details: dict[str, Any]

    created_at: str
