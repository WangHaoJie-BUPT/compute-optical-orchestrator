"""Compute-resource models used by the joint scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestrator.models.identifiers import validate_task_id


@dataclass(frozen=True)
class ComputeSite:
    """One compute site connected to an optical-network access node."""

    site_id: str
    name: str
    network_access_node: str

    cpu_total: int
    cpu_available: int

    memory_gb_total: int
    memory_gb_available: int

    gpu_total: int
    gpu_available: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ComputeSite":
        return cls(
            site_id=str(data["site_id"]),
            name=str(data["name"]),
            network_access_node=str(data["network_access_node"]),
            cpu_total=int(data["cpu_total"]),
            cpu_available=int(data["cpu_available"]),
            memory_gb_total=int(data["memory_gb_total"]),
            memory_gb_available=int(data["memory_gb_available"]),
            gpu_total=int(data["gpu_total"]),
            gpu_available=int(data["gpu_available"]),
        )

    def can_host(self, task: "TaskRequest") -> bool:
        """Return whether the site has sufficient available resources."""

        return (
            self.cpu_available >= task.cpu_required
            and self.memory_gb_available >= task.memory_gb_required
            and self.gpu_available >= task.gpu_required
        )

    def resource_score_after_allocation(self, task: "TaskRequest") -> float:
        """
        Calculate a simple residual-resource score.

        This is only a first-stage heuristic and is not the final optimization
        objective.
        """

        remaining_cpu = self.cpu_available - task.cpu_required
        remaining_memory = (
            self.memory_gb_available - task.memory_gb_required
        )
        remaining_gpu = self.gpu_available - task.gpu_required

        return (
            float(remaining_cpu)
            + float(remaining_memory) / 8.0
            + float(remaining_gpu) * 10.0
        )


@dataclass(frozen=True)
class TaskRequest:
    """Compute task and associated network-bandwidth requirements."""

    task_id: str
    source_access_node: str
    cpu_required: int
    memory_gb_required: int
    gpu_required: int
    bandwidth_gbps: float

    def __post_init__(self) -> None:
        validate_task_id(self.task_id)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskRequest":
        return cls(
            task_id=str(data["task_id"]),
            source_access_node=str(data["source_access_node"]),
            cpu_required=int(data["cpu_required"]),
            memory_gb_required=int(data["memory_gb_required"]),
            gpu_required=int(data["gpu_required"]),
            bandwidth_gbps=float(data["bandwidth_gbps"]),
        )
