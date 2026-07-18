"""Persistent models for Kubernetes-native orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.models.runtime_task import RuntimeTaskRequest
from orchestrator.models.spectrum import SpectrumSlot
from orchestrator.models.task_state import TaskState


@dataclass(frozen=True)
class RuntimeTaskRecord:
    """Persistent record of one Kubernetes-native task."""

    task: RuntimeTaskRequest
    state: TaskState

    selected_site_id: str | None
    selected_node_name: str | None
    failure_reason: str | None

    version: int

    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RuntimeOpticalRecord:
    """Persisted selected optical path and spectrum allocation."""

    task_id: str
    site_id: str
    node_name: str
    network_access_node: str

    slot: SpectrumSlot
    fiber_links: tuple[str, ...]
    route: tuple[str, ...]

    gsnr_db: float
    total_score: float
    created_at: str


@dataclass(frozen=True)
class RuntimeExecutionRecord:
    """Persisted Kubernetes Job execution information."""

    task_id: str
    job_name: str
    namespace: str
    requested_site_id: str

    pod_name: str | None
    node_name: str | None
    node_site_label: str | None

    phase: str
    succeeded: bool | None
    logs: str | None

    created_at: str
    updated_at: str
