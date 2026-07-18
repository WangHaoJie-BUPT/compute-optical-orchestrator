"""HTTP API request and response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from orchestrator.models.identifiers import TASK_ID_PATTERN
from orchestrator.models.runtime_task import RuntimeTaskRequest


class TaskSubmitRequest(BaseModel):
    """Task submitted through the orchestration API."""

    task_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=TASK_ID_PATTERN,
    )

    source_access_node: str = Field(
        min_length=1,
        max_length=256,
    )

    cpu_request_m: int = Field(gt=0)
    memory_request_mib: int = Field(gt=0)
    gpu_request: int = Field(default=0, ge=0)
    accelerator_resource_name: str = Field(
        default="nvidia.com/gpu",
        min_length=3,
        max_length=253,
    )
    accelerator_product: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    accelerator_memory_mib: int | None = Field(
        default=None,
        gt=0,
    )
    accelerator_interconnect: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    accelerator_partition_mode: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )

    bandwidth_gbps: float = Field(gt=0)

    image: str = Field(
        default="busybox:1.36",
        min_length=1,
        max_length=512,
    )

    shell_command: str = Field(
        min_length=1,
        max_length=20000,
    )

    cpu_limit_m: int | None = Field(
        default=None,
        gt=0,
    )

    memory_limit_mib: int | None = Field(
        default=None,
        gt=0,
    )

    def to_domain(self) -> RuntimeTaskRequest:
        """Convert the HTTP payload to the domain task model."""

        if (
            self.cpu_limit_m is not None
            and self.cpu_limit_m < self.cpu_request_m
        ):
            raise ValueError(
                "cpu_limit_m cannot be smaller "
                "than cpu_request_m."
            )

        if (
            self.memory_limit_mib is not None
            and self.memory_limit_mib
            < self.memory_request_mib
        ):
            raise ValueError(
                "memory_limit_mib cannot be smaller "
                "than memory_request_mib."
            )

        return RuntimeTaskRequest(
            task_id=self.task_id,
            source_access_node=self.source_access_node,
            cpu_request_m=self.cpu_request_m,
            memory_request_mib=self.memory_request_mib,
            gpu_request=self.gpu_request,
            bandwidth_gbps=self.bandwidth_gbps,
            image=self.image,
            shell_command=self.shell_command,
            cpu_limit_m=self.cpu_limit_m,
            memory_limit_mib=self.memory_limit_mib,
            accelerator_resource_name=(
                self.accelerator_resource_name
            ),
            accelerator_product=self.accelerator_product,
            accelerator_memory_mib=(
                self.accelerator_memory_mib
            ),
            accelerator_interconnect=(
                self.accelerator_interconnect
            ),
            accelerator_partition_mode=(
                self.accelerator_partition_mode
            ),
        )


class TaskSubmissionResponse(BaseModel):
    """Result returned after task submission."""

    task_id: str
    state: str
    version: int

    selected_site_id: str | None
    selected_node_name: str | None

    reused_existing: bool


class OpticalAllocationResponse(BaseModel):
    """Persisted optical allocation."""

    site_id: str
    node_name: str
    network_access_node: str

    n: int
    m: int

    fiber_links: list[str]
    route: list[str]

    gsnr_db: float
    total_score: float


class KubernetesExecutionResponse(BaseModel):
    """Kubernetes execution metadata."""

    job_name: str
    namespace: str
    requested_site_id: str

    pod_name: str | None
    node_name: str | None
    node_site_label: str | None

    phase: str
    succeeded: bool | None

    logs_available: bool


class TaskDetailResponse(BaseModel):
    """Complete task status without embedding large logs."""

    task_id: str
    state: str
    version: int

    source_access_node: str

    cpu_request_m: int
    memory_request_mib: int
    gpu_request: int
    accelerator_resource_name: str
    accelerator_product: str | None
    accelerator_memory_mib: int | None
    accelerator_interconnect: str | None
    accelerator_partition_mode: str | None

    cpu_limit_m: int
    memory_limit_mib: int

    bandwidth_gbps: float
    image: str

    selected_site_id: str | None
    selected_node_name: str | None
    failure_reason: str | None

    created_at: str
    updated_at: str

    optical: OpticalAllocationResponse | None
    execution: KubernetesExecutionResponse | None


class TaskListResponse(BaseModel):
    """Task collection response."""

    count: int
    tasks: list[TaskDetailResponse]


class HealthResponse(BaseModel):
    """Basic service health information."""

    status: str
    controller_alive: bool
    restored_allocations: int
    recovered_admission_dispatches: int
    admission_queue_active: int

    last_reconciliation_at: str | None
    last_controller_error: str | None


class ReconciliationResponse(BaseModel):
    """Result of one manually triggered controller cycle."""

    controller_id: str
    lease_acquired: bool
    scanned_count: int
    changed_count: int
    error_count: int
    events: list[dict[str, Any]]


class TaskCancellationResponse(BaseModel):
    """Result of an idempotent task cancellation request."""

    task_id: str
    state: str
    cancelled: bool
    job_deleted: bool
    message: str
