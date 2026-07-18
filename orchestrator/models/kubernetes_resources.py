"""Kubernetes compute-site resource snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ACCELERATOR_PRODUCT_LABEL = (
    "accelerator.compute-optical.io/product"
)
ACCELERATOR_MEMORY_LABEL = (
    "accelerator.compute-optical.io/memory-mib"
)
ACCELERATOR_INTERCONNECT_LABEL = (
    "accelerator.compute-optical.io/interconnect"
)
ACCELERATOR_PARTITION_LABEL = (
    "accelerator.compute-optical.io/partition-mode"
)


@dataclass(frozen=True)
class AcceleratorInventory:
    """One Kubernetes extended accelerator resource on a node."""

    resource_name: str
    allocatable: int
    requested: int
    available: int
    product: str | None = None
    memory_mib: int | None = None
    interconnect: str | None = None
    partition_mode: str | None = None

    def matches(
        self,
        *,
        count: int,
        product: str | None = None,
        minimum_memory_mib: int | None = None,
        interconnect: str | None = None,
        partition_mode: str | None = None,
    ) -> bool:
        if self.available < count:
            return False
        if product is not None and self.product != product:
            return False
        if (
            minimum_memory_mib is not None
            and (
                self.memory_mib is None
                or self.memory_mib < minimum_memory_mib
            )
        ):
            return False
        if (
            interconnect is not None
            and self.interconnect != interconnect
        ):
            return False
        if (
            partition_mode is not None
            and self.partition_mode != partition_mode
        ):
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_name": self.resource_name,
            "allocatable": self.allocatable,
            "requested": self.requested,
            "available": self.available,
            "product": self.product,
            "memory_mib": self.memory_mib,
            "interconnect": self.interconnect,
            "partition_mode": self.partition_mode,
        }


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
    accelerators: tuple[AcceleratorInventory, ...] = ()

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
        accelerator_resource_name: str = "nvidia.com/gpu",
        accelerator_product: str | None = None,
        accelerator_memory_mib: int | None = None,
        accelerator_interconnect: str | None = None,
        accelerator_partition_mode: str | None = None,
    ) -> bool:
        """Return whether the current Kubernetes snapshot can host a task."""

        base_resources_available = (
            self.ready
            and self.schedulable
            and self.available_cpu_m >= cpu_m
            and self.available_memory_bytes >= memory_bytes
        )
        if not base_resources_available:
            return False
        if gpu == 0:
            return True

        inventory = self.accelerator_inventory(
            accelerator_resource_name
        )
        return inventory is not None and inventory.matches(
            count=gpu,
            product=accelerator_product,
            minimum_memory_mib=accelerator_memory_mib,
            interconnect=accelerator_interconnect,
            partition_mode=accelerator_partition_mode,
        )

    def accelerator_inventory(
        self,
        resource_name: str,
    ) -> AcceleratorInventory | None:
        for inventory in self.accelerators:
            if inventory.resource_name == resource_name:
                return inventory

        # Preserve compatibility with snapshots built before v12.
        if resource_name == "nvidia.com/gpu":
            return AcceleratorInventory(
                resource_name=resource_name,
                allocatable=self.allocatable_gpu,
                requested=self.requested_gpu,
                available=self.available_gpu,
            )
        return None

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
            "accelerators": [
                inventory.to_dict()
                for inventory in self.accelerators
            ],
        }
