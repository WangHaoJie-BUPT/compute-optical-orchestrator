"""Kubernetes-native compute-optical task model."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from orchestrator.models.identifiers import validate_task_id


_EXTENDED_RESOURCE_PATTERN = re.compile(
    r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?/[A-Za-z0-9]"
    r"(?:[-A-Za-z0-9_.]*[A-Za-z0-9])?$"
)


@dataclass(frozen=True)
class RuntimeTaskRequest:
    """
    A compute-optical task expressed using Kubernetes resource units.

    CPU uses millicores:
        1000m = 1 CPU core

    Memory uses MiB:
        1024 MiB = 1 GiB
    """

    task_id: str
    source_access_node: str

    cpu_request_m: int
    memory_request_mib: int
    gpu_request: int

    bandwidth_gbps: float

    image: str
    shell_command: str

    cpu_limit_m: int | None = None
    memory_limit_mib: int | None = None

    accelerator_resource_name: str = "nvidia.com/gpu"
    accelerator_product: str | None = None
    accelerator_memory_mib: int | None = None
    accelerator_interconnect: str | None = None
    accelerator_partition_mode: str | None = None

    def __post_init__(self) -> None:
        validate_task_id(self.task_id)

        if self.cpu_request_m <= 0:
            raise ValueError(
                "cpu_request_m must be greater than zero."
            )

        if self.memory_request_mib <= 0:
            raise ValueError(
                "memory_request_mib must be greater than zero."
            )

        if self.gpu_request < 0:
            raise ValueError(
                "gpu_request cannot be negative."
            )

        if not _EXTENDED_RESOURCE_PATTERN.fullmatch(
            self.accelerator_resource_name
        ):
            raise ValueError(
                "accelerator_resource_name must be a qualified "
                "Kubernetes extended resource name."
            )

        if (
            self.accelerator_memory_mib is not None
            and self.accelerator_memory_mib <= 0
        ):
            raise ValueError(
                "accelerator_memory_mib must be greater than zero."
            )

        constraints = (
            self.accelerator_product,
            self.accelerator_memory_mib,
            self.accelerator_interconnect,
            self.accelerator_partition_mode,
        )
        if self.gpu_request == 0 and any(
            value is not None for value in constraints
        ):
            raise ValueError(
                "Accelerator constraints require gpu_request > 0."
            )

        if self.bandwidth_gbps <= 0:
            raise ValueError(
                "bandwidth_gbps must be greater than zero."
            )

    @property
    def memory_request_bytes(self) -> int:
        """Return requested memory in bytes."""

        return self.memory_request_mib * 1024 * 1024

    @property
    def cpu_request_quantity(self) -> str:
        """Return a Kubernetes CPU quantity."""

        return f"{self.cpu_request_m}m"

    @property
    def memory_request_quantity(self) -> str:
        """Return a Kubernetes memory quantity."""

        return f"{self.memory_request_mib}Mi"

    @property
    def effective_cpu_limit_m(self) -> int:
        """Use the request as the limit when no limit is specified."""

        return (
            self.cpu_limit_m
            if self.cpu_limit_m is not None
            else self.cpu_request_m
        )

    @property
    def effective_memory_limit_mib(self) -> int:
        """Use the request as the limit when no limit is specified."""

        return (
            self.memory_limit_mib
            if self.memory_limit_mib is not None
            else self.memory_request_mib
        )

    @property
    def cpu_limit_quantity(self) -> str:
        return f"{self.effective_cpu_limit_m}m"

    @property
    def memory_limit_quantity(self) -> str:
        return f"{self.effective_memory_limit_mib}Mi"

    def to_dict(self) -> dict[str, Any]:
        """Return a complete JSON-serializable task representation."""

        return {
            "task_id": self.task_id,
            "source_access_node": self.source_access_node,
            "cpu_request_m": self.cpu_request_m,
            "memory_request_mib": self.memory_request_mib,
            "gpu_request": self.gpu_request,
            "bandwidth_gbps": self.bandwidth_gbps,
            "image": self.image,
            "shell_command": self.shell_command,
            "cpu_limit_m": self.cpu_limit_m,
            "memory_limit_mib": self.memory_limit_mib,
            "accelerator_resource_name": (
                self.accelerator_resource_name
            ),
            "accelerator_product": self.accelerator_product,
            "accelerator_memory_mib": (
                self.accelerator_memory_mib
            ),
            "accelerator_interconnect": (
                self.accelerator_interconnect
            ),
            "accelerator_partition_mode": (
                self.accelerator_partition_mode
            ),
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "RuntimeTaskRequest":
        """
        Load both new and legacy task formats.

        New format:
            cpu_request_m
            memory_request_mib
            gpu_request

        Legacy format:
            cpu_required, interpreted as CPU cores
            memory_gb_required
            gpu_required
        """

        if "cpu_request_m" in data:
            cpu_request_m = int(data["cpu_request_m"])
        else:
            cpu_request_m = int(data["cpu_required"]) * 1000

        if "memory_request_mib" in data:
            memory_request_mib = int(
                data["memory_request_mib"]
            )
        else:
            memory_request_mib = int(
                data["memory_gb_required"]
            ) * 1024

        if "gpu_request" in data:
            gpu_request = int(data["gpu_request"])
        else:
            gpu_request = int(
                data.get("gpu_required", 0)
            )

        return cls(
            task_id=str(data["task_id"]),
            source_access_node=str(
                data["source_access_node"]
            ),
            cpu_request_m=cpu_request_m,
            memory_request_mib=memory_request_mib,
            gpu_request=gpu_request,
            bandwidth_gbps=float(
                data["bandwidth_gbps"]
            ),
            image=str(
                data.get("image", "busybox:1.36")
            ),
            shell_command=str(
                data.get(
                    "shell_command",
                    "echo task started; sleep 5; "
                    "echo task completed",
                )
            ),
            cpu_limit_m=(
                int(data["cpu_limit_m"])
                if data.get("cpu_limit_m") is not None
                else None
            ),
            memory_limit_mib=(
                int(data["memory_limit_mib"])
                if data.get("memory_limit_mib") is not None
                else None
            ),
            accelerator_resource_name=str(
                data.get(
                    "accelerator_resource_name",
                    "nvidia.com/gpu",
                )
            ),
            accelerator_product=(
                str(data["accelerator_product"])
                if data.get("accelerator_product") is not None
                else None
            ),
            accelerator_memory_mib=(
                int(data["accelerator_memory_mib"])
                if data.get("accelerator_memory_mib") is not None
                else None
            ),
            accelerator_interconnect=(
                str(data["accelerator_interconnect"])
                if data.get("accelerator_interconnect") is not None
                else None
            ),
            accelerator_partition_mode=(
                str(data["accelerator_partition_mode"])
                if data.get("accelerator_partition_mode") is not None
                else None
            ),
        )
