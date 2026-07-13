"""Kubernetes compute-site resource snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class KubernetesSiteResources:
    """Real-time resource snapshot for one Kubernetes compute site."""

    site_id: str
    site_name: str
    network_access_node: str
    node_name: str

    ready: bool
    schedulable: bool

    allocatable_cpu_m: int
    requested_cpu_m: int
    available_cpu_m: int

    allocatable_memory_bytes: int
    requested_memory_bytes: int
    available_memory_bytes: int

    allocatable_gpu: int
    requested_gpu: int
    available_gpu: int

    active_pod_count: int

    @property
    def allocatable_cpu_cores(self) -> float:
        return self.allocatable_cpu_m / 1000.0

    @property
    def requested_cpu_cores(self) -> float:
        return self.requested_cpu_m / 1000.0

    @property
    def available_cpu_cores(self) -> float:
        return self.available_cpu_m / 1000.0

    @property
    def allocatable_memory_gib(self) -> float:
        return self.allocatable_memory_bytes / (1024 ** 3)

    @property
    def requested_memory_gib(self) -> float:
        return self.requested_memory_bytes / (1024 ** 3)

    @property
    def available_memory_gib(self) -> float:
        return self.available_memory_bytes / (1024 ** 3)

    def can_host(
        self,
        cpu_m: int,
        memory_bytes: int,
        gpu: int = 0,
    ) -> bool:
        """Return whether the current Kubernetes snapshot can host a task."""

        return (
            self.ready
            and self.schedulable
            and self.available_cpu_m >= cpu_m
            and self.available_memory_bytes >= memory_bytes
            and self.available_gpu >= gpu
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert the snapshot to a JSON-serializable dictionary."""

        return {
            "site_id": self.site_id,
            "site_name": self.site_name,
            "network_access_node": self.network_access_node,
            "node_name": self.node_name,
            "ready": self.ready,
            "schedulable": self.schedulable,
            "active_pod_count": self.active_pod_count,
            "cpu": {
                "allocatable_m": self.allocatable_cpu_m,
                "requested_m": self.requested_cpu_m,
                "available_m": self.available_cpu_m,
                "allocatable_cores": self.allocatable_cpu_cores,
                "requested_cores": self.requested_cpu_cores,
                "available_cores": self.available_cpu_cores,
            },
            "memory": {
                "allocatable_bytes": self.allocatable_memory_bytes,
                "requested_bytes": self.requested_memory_bytes,
                "available_bytes": self.available_memory_bytes,
                "allocatable_gib": self.allocatable_memory_gib,
                "requested_gib": self.requested_memory_gib,
                "available_gib": self.available_memory_gib,
            },
            "gpu": {
                "allocatable": self.allocatable_gpu,
                "requested": self.requested_gpu,
                "available": self.available_gpu,
            },
        }
