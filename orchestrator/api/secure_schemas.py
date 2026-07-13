"""Schemas for profile-controlled task submission."""

from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class ProfileTaskSubmitRequest(BaseModel):
    """Safe submission payload without image or shell command."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(
        min_length=1,
        max_length=128,
    )

    profile_id: str = Field(
        min_length=1,
        max_length=128,
    )

    source_access_node: str = Field(
        min_length=1,
        max_length=256,
    )

    cpu_request_m: int | None = Field(
        default=None,
        gt=0,
    )

    memory_request_mib: int | None = Field(
        default=None,
        gt=0,
    )

    gpu_request: int = Field(
        default=0,
        ge=0,
    )

    cpu_limit_m: int | None = Field(
        default=None,
        gt=0,
    )

    memory_limit_mib: int | None = Field(
        default=None,
        gt=0,
    )

    bandwidth_gbps: float | None = Field(
        default=None,
        gt=0,
    )

    duration_seconds: int | None = Field(
        default=None,
        ge=1,
    )


class TaskProfileResponse(BaseModel):
    """Public task-profile metadata."""

    profile_id: str
    description: str

    default_cpu_request_m: int
    max_cpu_request_m: int

    default_memory_request_mib: int
    max_memory_request_mib: int

    max_gpu_request: int

    default_bandwidth_gbps: float
    max_bandwidth_gbps: float

    default_duration_seconds: int
    max_duration_seconds: int

    allowed_source_access_nodes: list[str]


class TaskProfileListResponse(BaseModel):
    """Available safe task profiles."""

    count: int
    profiles: list[TaskProfileResponse]


class TaskEventResponse(BaseModel):
    """One immutable task lifecycle event."""

    event_id: int
    task_id: str
    event_type: str

    previous_state: str | None
    new_state: str | None

    source: str
    message: str | None
    details: dict[str, Any]

    created_at: str


class TaskEventListResponse(BaseModel):
    """Ordered event history for one task."""

    task_id: str
    count: int
    next_after_event_id: int | None
    events: list[TaskEventResponse]
