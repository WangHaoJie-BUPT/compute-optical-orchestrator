"""Persistent admission-queue domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from orchestrator.models.runtime_task import RuntimeTaskRequest


class AdmissionState(str, Enum):
    QUEUED = "QUEUED"
    DISPATCHING = "DISPATCHING"
    ADMITTED = "ADMITTED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class AdmissionRecord:
    task: RuntimeTaskRequest
    tenant_id: str
    priority: int
    state: AdmissionState
    attempt_count: int
    not_before: float
    claimed_by: str | None
    runtime_state: str | None
    last_error: str | None
    created_at: str
    updated_at: str
    admitted_at: str | None


@dataclass(frozen=True)
class AdmissionQueueStats:
    queued: int
    dispatching: int
    admitted: int
    rejected: int
    cancelled: int

    @property
    def active(self) -> int:
        return self.queued + self.dispatching
