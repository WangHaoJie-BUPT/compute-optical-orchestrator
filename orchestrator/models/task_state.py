"""Persistent task-state and allocation models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from orchestrator.models.resources import TaskRequest
from orchestrator.models.spectrum import SpectrumSlot


class TaskState(str, Enum):
    """Lifecycle states of one orchestrated task."""

    PENDING = "PENDING"
    PLANNING = "PLANNING"
    RESERVED = "RESERVED"
    SUBMITTING = "SUBMITTING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"

    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    RELEASING = "RELEASING"
    RELEASED = "RELEASED"
    RELEASE_FAILED = "RELEASE_FAILED"


ACTIVE_ALLOCATION_STATES = frozenset(
    {
        TaskState.RESERVED,
        TaskState.SUBMITTING,
        TaskState.RUNNING,
        TaskState.COMPLETED,
        TaskState.RELEASING,
        TaskState.RELEASE_FAILED,
    }
)


@dataclass(frozen=True)
class TaskRecord:
    """Persistent record of one task."""

    task: TaskRequest
    state: TaskState
    selected_site: str | None
    failure_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PersistedJointAllocation:
    """Joint allocation reconstructed from SQLite."""

    task_record: TaskRecord

    compute_allocation_id: str
    site_id: str
    cpu: int
    memory_gb: int
    gpu: int

    optical_allocation_id: str
    slot: SpectrumSlot
    fiber_links: tuple[str, ...]
    route: tuple[str, ...]
    gsnr_db: float
    total_score: float
