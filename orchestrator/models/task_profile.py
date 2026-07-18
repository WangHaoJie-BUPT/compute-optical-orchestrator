"""Server-controlled Kubernetes task profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestrator.models.runtime_task import RuntimeTaskRequest


class TaskProfileError(ValueError):
    """Raised when a task profile or profile request is invalid."""


@dataclass(frozen=True)
class TaskProfile:
    """A server-controlled executable task template."""

    profile_id: str
    description: str
    image: str

    allowed_source_access_nodes: tuple[str, ...]

    default_cpu_request_m: int
    max_cpu_request_m: int

    default_memory_request_mib: int
    max_memory_request_mib: int

    default_cpu_limit_m: int
    max_cpu_limit_m: int

    default_memory_limit_mib: int
    max_memory_limit_mib: int

    max_gpu_request: int

    default_bandwidth_gbps: float
    max_bandwidth_gbps: float

    default_duration_seconds: int
    max_duration_seconds: int

    command_template: str

    allowed_accelerator_resource_names: tuple[str, ...] = (
        "nvidia.com/gpu",
    )
    allowed_accelerator_products: tuple[str, ...] = ()
    allowed_accelerator_interconnects: tuple[str, ...] = ()
    allowed_accelerator_partition_modes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise TaskProfileError(
                "profile_id cannot be empty."
            )

        if not self.image:
            raise TaskProfileError(
                f"Profile {self.profile_id} has no image."
            )

        if self.default_cpu_request_m <= 0:
            raise TaskProfileError(
                "default_cpu_request_m must be positive."
            )

        if (
            self.default_cpu_request_m
            > self.max_cpu_request_m
        ):
            raise TaskProfileError(
                "Default CPU request exceeds its maximum."
            )

        if (
            self.default_memory_request_mib
            > self.max_memory_request_mib
        ):
            raise TaskProfileError(
                "Default memory request exceeds its maximum."
            )

        if (
            self.default_cpu_limit_m
            > self.max_cpu_limit_m
        ):
            raise TaskProfileError(
                "Default CPU limit exceeds its maximum."
            )

        if (
            self.default_memory_limit_mib
            > self.max_memory_limit_mib
        ):
            raise TaskProfileError(
                "Default memory limit exceeds its maximum."
            )

        if (
            self.default_bandwidth_gbps
            > self.max_bandwidth_gbps
        ):
            raise TaskProfileError(
                "Default bandwidth exceeds its maximum."
            )

        if (
            self.default_duration_seconds
            > self.max_duration_seconds
        ):
            raise TaskProfileError(
                "Default duration exceeds its maximum."
            )

    def build_task(
        self,
        *,
        task_id: str,
        source_access_node: str,
        cpu_request_m: int | None = None,
        memory_request_mib: int | None = None,
        gpu_request: int = 0,
        cpu_limit_m: int | None = None,
        memory_limit_mib: int | None = None,
        bandwidth_gbps: float | None = None,
        duration_seconds: int | None = None,
        accelerator_resource_name: str = "nvidia.com/gpu",
        accelerator_product: str | None = None,
        accelerator_memory_mib: int | None = None,
        accelerator_interconnect: str | None = None,
        accelerator_partition_mode: str | None = None,
    ) -> RuntimeTaskRequest:
        """Build a validated domain task from safe profile parameters."""

        if (
            source_access_node
            not in self.allowed_source_access_nodes
        ):
            raise TaskProfileError(
                f"Source access node {source_access_node!r} "
                f"is not allowed by profile {self.profile_id!r}."
            )

        resolved_cpu_request = (
            cpu_request_m
            if cpu_request_m is not None
            else self.default_cpu_request_m
        )

        resolved_memory_request = (
            memory_request_mib
            if memory_request_mib is not None
            else self.default_memory_request_mib
        )

        resolved_cpu_limit = (
            cpu_limit_m
            if cpu_limit_m is not None
            else self.default_cpu_limit_m
        )

        resolved_memory_limit = (
            memory_limit_mib
            if memory_limit_mib is not None
            else self.default_memory_limit_mib
        )

        resolved_bandwidth = (
            bandwidth_gbps
            if bandwidth_gbps is not None
            else self.default_bandwidth_gbps
        )

        resolved_duration = (
            duration_seconds
            if duration_seconds is not None
            else self.default_duration_seconds
        )

        if not 0 < resolved_cpu_request <= self.max_cpu_request_m:
            raise TaskProfileError(
                f"CPU request must be between 1 and "
                f"{self.max_cpu_request_m}m."
            )

        if not (
            0
            < resolved_memory_request
            <= self.max_memory_request_mib
        ):
            raise TaskProfileError(
                f"Memory request must be between 1 and "
                f"{self.max_memory_request_mib}Mi."
            )

        if not (
            resolved_cpu_request
            <= resolved_cpu_limit
            <= self.max_cpu_limit_m
        ):
            raise TaskProfileError(
                "CPU limit must be at least the CPU request "
                f"and no more than {self.max_cpu_limit_m}m."
            )

        if not (
            resolved_memory_request
            <= resolved_memory_limit
            <= self.max_memory_limit_mib
        ):
            raise TaskProfileError(
                "Memory limit must be at least the memory request "
                f"and no more than {self.max_memory_limit_mib}Mi."
            )

        if not 0 <= gpu_request <= self.max_gpu_request:
            raise TaskProfileError(
                f"GPU request cannot exceed "
                f"{self.max_gpu_request}."
            )

        accelerator_constraints = (
            accelerator_product,
            accelerator_memory_mib,
            accelerator_interconnect,
            accelerator_partition_mode,
        )
        if gpu_request == 0 and any(
            value is not None
            for value in accelerator_constraints
        ):
            raise TaskProfileError(
                "Accelerator constraints require gpu_request > 0."
            )

        if (
            gpu_request > 0
            and accelerator_resource_name
            not in self.allowed_accelerator_resource_names
        ):
            raise TaskProfileError(
                f"Accelerator resource {accelerator_resource_name!r} "
                f"is not allowed by profile {self.profile_id!r}."
            )

        for value, allowed, field_name in (
            (
                accelerator_product,
                self.allowed_accelerator_products,
                "product",
            ),
            (
                accelerator_interconnect,
                self.allowed_accelerator_interconnects,
                "interconnect",
            ),
            (
                accelerator_partition_mode,
                self.allowed_accelerator_partition_modes,
                "partition mode",
            ),
        ):
            if value is not None and allowed and value not in allowed:
                raise TaskProfileError(
                    f"Accelerator {field_name} {value!r} is not "
                    f"allowed by profile {self.profile_id!r}."
                )

        if not (
            0
            < resolved_bandwidth
            <= self.max_bandwidth_gbps
        ):
            raise TaskProfileError(
                f"Bandwidth must be positive and no more than "
                f"{self.max_bandwidth_gbps} Gbit/s."
            )

        if not (
            1
            <= resolved_duration
            <= self.max_duration_seconds
        ):
            raise TaskProfileError(
                f"Duration must be between 1 and "
                f"{self.max_duration_seconds} seconds."
            )

        command = self.command_template.replace(
            "__DURATION_SECONDS__",
            str(resolved_duration),
        )

        return RuntimeTaskRequest(
            task_id=task_id,
            source_access_node=source_access_node,
            cpu_request_m=resolved_cpu_request,
            memory_request_mib=resolved_memory_request,
            gpu_request=gpu_request,
            bandwidth_gbps=resolved_bandwidth,
            image=self.image,
            shell_command=command,
            cpu_limit_m=resolved_cpu_limit,
            memory_limit_mib=resolved_memory_limit,
            accelerator_resource_name=accelerator_resource_name,
            accelerator_product=accelerator_product,
            accelerator_memory_mib=accelerator_memory_mib,
            accelerator_interconnect=accelerator_interconnect,
            accelerator_partition_mode=(
                accelerator_partition_mode
            ),
        )

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "TaskProfile":
        """Load one profile from JSON."""

        return cls(
            profile_id=str(data["profile_id"]),
            description=str(data["description"]),
            image=str(data["image"]),
            allowed_source_access_nodes=tuple(
                str(item)
                for item in data[
                    "allowed_source_access_nodes"
                ]
            ),
            default_cpu_request_m=int(
                data["default_cpu_request_m"]
            ),
            max_cpu_request_m=int(
                data["max_cpu_request_m"]
            ),
            default_memory_request_mib=int(
                data["default_memory_request_mib"]
            ),
            max_memory_request_mib=int(
                data["max_memory_request_mib"]
            ),
            default_cpu_limit_m=int(
                data["default_cpu_limit_m"]
            ),
            max_cpu_limit_m=int(
                data["max_cpu_limit_m"]
            ),
            default_memory_limit_mib=int(
                data["default_memory_limit_mib"]
            ),
            max_memory_limit_mib=int(
                data["max_memory_limit_mib"]
            ),
            max_gpu_request=int(
                data["max_gpu_request"]
            ),
            default_bandwidth_gbps=float(
                data["default_bandwidth_gbps"]
            ),
            max_bandwidth_gbps=float(
                data["max_bandwidth_gbps"]
            ),
            default_duration_seconds=int(
                data["default_duration_seconds"]
            ),
            max_duration_seconds=int(
                data["max_duration_seconds"]
            ),
            command_template=str(
                data["command_template"]
            ),
            allowed_accelerator_resource_names=tuple(
                str(item)
                for item in data.get(
                    "allowed_accelerator_resource_names",
                    ["nvidia.com/gpu"],
                )
            ),
            allowed_accelerator_products=tuple(
                str(item)
                for item in data.get(
                    "allowed_accelerator_products",
                    [],
                )
            ),
            allowed_accelerator_interconnects=tuple(
                str(item)
                for item in data.get(
                    "allowed_accelerator_interconnects",
                    [],
                )
            ),
            allowed_accelerator_partition_modes=tuple(
                str(item)
                for item in data.get(
                    "allowed_accelerator_partition_modes",
                    [],
                )
            ),
        )
