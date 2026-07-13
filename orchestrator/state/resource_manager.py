"""Stateful compute-resource reservation and release."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.models.resources import ComputeSite, TaskRequest


class ResourceManagerError(RuntimeError):
    """Raised when compute-resource state cannot be modified."""


@dataclass(frozen=True)
class ResourceAllocation:
    """Resources reserved for one task."""

    allocation_id: str
    task_id: str
    site_id: str
    cpu: int
    memory_gb: int
    gpu: int


class ResourceManager:
    """
    Maintain mutable available-resource state for compute sites.

    ComputeSite itself remains immutable. The resource manager stores changing
    availability and returns fresh ComputeSite snapshots to the scheduler.
    """

    def __init__(self, sites: list[ComputeSite]) -> None:
        if not sites:
            raise ResourceManagerError(
                "At least one compute site is required."
            )

        self._base_sites: dict[str, ComputeSite] = {}
        self._available: dict[str, dict[str, int]] = {}
        self._allocations: dict[str, ResourceAllocation] = {}

        for site in sites:
            if site.site_id in self._base_sites:
                raise ResourceManagerError(
                    f"Duplicate site ID: {site.site_id}"
                )

            self._base_sites[site.site_id] = site
            self._available[site.site_id] = {
                "cpu": site.cpu_available,
                "memory_gb": site.memory_gb_available,
                "gpu": site.gpu_available,
            }

    def snapshot_sites(self) -> list[ComputeSite]:
        """Return current compute-site states for scheduling."""

        snapshots: list[ComputeSite] = []

        for site_id, base_site in self._base_sites.items():
            available = self._available[site_id]

            snapshots.append(
                ComputeSite(
                    site_id=base_site.site_id,
                    name=base_site.name,
                    network_access_node=base_site.network_access_node,
                    cpu_total=base_site.cpu_total,
                    cpu_available=available["cpu"],
                    memory_gb_total=base_site.memory_gb_total,
                    memory_gb_available=available["memory_gb"],
                    gpu_total=base_site.gpu_total,
                    gpu_available=available["gpu"],
                )
            )

        return snapshots

    def reserve(
        self,
        task: TaskRequest,
        site_id: str,
    ) -> ResourceAllocation:
        """Reserve compute resources for a selected task."""

        if task.task_id in self._allocations:
            raise ResourceManagerError(
                f"Task already has an allocation: {task.task_id}"
            )

        current_site = self._get_site_snapshot(site_id)

        if not current_site.can_host(task):
            raise ResourceManagerError(
                f"Site {site_id} no longer has enough resources "
                f"for task {task.task_id}."
            )

        available = self._available[site_id]

        available["cpu"] -= task.cpu_required
        available["memory_gb"] -= task.memory_gb_required
        available["gpu"] -= task.gpu_required

        allocation = ResourceAllocation(
            allocation_id=f"alloc-{task.task_id}-{site_id}",
            task_id=task.task_id,
            site_id=site_id,
            cpu=task.cpu_required,
            memory_gb=task.memory_gb_required,
            gpu=task.gpu_required,
        )

        self._allocations[task.task_id] = allocation
        return allocation

    def release(self, task_id: str) -> ResourceAllocation:
        """Release all compute resources reserved by one task."""

        allocation = self._allocations.pop(task_id, None)

        if allocation is None:
            raise ResourceManagerError(
                f"No active allocation for task: {task_id}"
            )

        available = self._available[allocation.site_id]

        available["cpu"] += allocation.cpu
        available["memory_gb"] += allocation.memory_gb
        available["gpu"] += allocation.gpu

        return allocation

    def active_allocations(self) -> list[ResourceAllocation]:
        """Return all active allocations."""

        return list(self._allocations.values())

    def _get_site_snapshot(self, site_id: str) -> ComputeSite:
        if site_id not in self._base_sites:
            raise ResourceManagerError(
                f"Unknown compute site: {site_id}"
            )

        base_site = self._base_sites[site_id]
        available = self._available[site_id]

        return ComputeSite(
            site_id=base_site.site_id,
            name=base_site.name,
            network_access_node=base_site.network_access_node,
            cpu_total=base_site.cpu_total,
            cpu_available=available["cpu"],
            memory_gb_total=base_site.memory_gb_total,
            memory_gb_available=available["memory_gb"],
            gpu_total=base_site.gpu_total,
            gpu_available=available["gpu"],
        )
