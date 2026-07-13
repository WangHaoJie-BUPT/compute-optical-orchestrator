"""Kubernetes-native compute-optical task model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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

    def __post_init__(self) -> None:
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
        )
