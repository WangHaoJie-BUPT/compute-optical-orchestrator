"""Schemas for the asynchronous admission queue API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from orchestrator.api.secure_schemas import ProfileTaskSubmitRequest


class QueuedProfileTaskSubmitRequest(ProfileTaskSubmitRequest):
    tenant_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
    )
    priority: int = Field(default=50, ge=0, le=100)


class AdmissionTaskResponse(BaseModel):
    task_id: str
    tenant_id: str
    priority: int
    state: str
    attempt_count: int
    not_before: float
    claimed_by: str | None
    runtime_state: str | None
    last_error: str | None
    created_at: str
    updated_at: str
    admitted_at: str | None


class AdmissionEnqueueResponse(AdmissionTaskResponse):
    reused_existing: bool


class AdmissionTaskListResponse(BaseModel):
    count: int
    tasks: list[AdmissionTaskResponse]


class AdmissionQueueStatsResponse(BaseModel):
    queued: int
    dispatching: int
    active: int
    admitted: int
    rejected: int
    cancelled: int


class AdmissionEventResponse(BaseModel):
    task_id: str
    tenant_id: str
    priority: int
    attempt_count: int
    action: str
    final_state: str
    error: str | None


class AdmissionBatchResponse(BaseModel):
    controller_id: str
    lease_acquired: bool
    claimed_count: int
    admitted_count: int
    requeued_count: int
    rejected_count: int
    events: list[AdmissionEventResponse]
