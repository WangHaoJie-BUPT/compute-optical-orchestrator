"""Discover real Kubernetes compute resources by site."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any

from orchestrator.adapters.kubernetes_adapter import (
    KubernetesAdapter,
)
from orchestrator.models.kubernetes_resources import (
    KubernetesSiteResources,
)


class KubernetesResourceDiscoveryError(RuntimeError):
    """Raised when Kubernetes resources cannot be interpreted."""


_QUANTITY_PATTERN = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))([a-zA-Z]+)?$"
)

_BINARY_MEMORY_FACTORS = {
    "Ki": Decimal(1024),
    "Mi": Decimal(1024 ** 2),
    "Gi": Decimal(1024 ** 3),
    "Ti": Decimal(1024 ** 4),
    "Pi": Decimal(1024 ** 5),
    "Ei": Decimal(1024 ** 6),
}

_DECIMAL_MEMORY_FACTORS = {
    "": Decimal(1),
    "k": Decimal(1000),
    "K": Decimal(1000),
    "M": Decimal(1000 ** 2),
    "G": Decimal(1000 ** 3),
    "T": Decimal(1000 ** 4),
    "P": Decimal(1000 ** 5),
    "E": Decimal(1000 ** 6),
    "m": Decimal("0.001"),
}


def _parse_quantity(value: Any) -> tuple[Decimal, str]:
    text = str(value or "0").strip()
    match = _QUANTITY_PATTERN.fullmatch(text)

    if match is None:
        raise KubernetesResourceDiscoveryError(
            f"Unsupported Kubernetes quantity: {text!r}"
        )

    try:
        number = Decimal(match.group(1))
    except InvalidOperation as exc:
        raise KubernetesResourceDiscoveryError(
            f"Invalid Kubernetes quantity: {text!r}"
        ) from exc

    suffix = match.group(2) or ""
    return number, suffix


def parse_cpu_millicores(value: Any) -> int:
    """Convert a Kubernetes CPU quantity to millicores."""

    number, suffix = _parse_quantity(value)

    if suffix == "":
        millicores = number * Decimal(1000)
    elif suffix == "m":
        millicores = number
    elif suffix == "u":
        millicores = number / Decimal(1000)
    elif suffix == "n":
        millicores = number / Decimal(1_000_000)
    else:
        raise KubernetesResourceDiscoveryError(
            f"Unsupported CPU suffix: {suffix!r}"
        )

    return int(
        millicores.to_integral_value(rounding=ROUND_CEILING)
    )


def parse_memory_bytes(value: Any) -> int:
    """Convert a Kubernetes memory quantity to bytes."""

    number, suffix = _parse_quantity(value)

    if suffix in _BINARY_MEMORY_FACTORS:
        factor = _BINARY_MEMORY_FACTORS[suffix]
    elif suffix in _DECIMAL_MEMORY_FACTORS:
        factor = _DECIMAL_MEMORY_FACTORS[suffix]
    else:
        raise KubernetesResourceDiscoveryError(
            f"Unsupported memory suffix: {suffix!r}"
        )

    byte_count = number * factor

    return int(
        byte_count.to_integral_value(rounding=ROUND_CEILING)
    )


def parse_integer_resource(value: Any) -> int:
    """Convert an integer-like extended-resource quantity."""

    number, suffix = _parse_quantity(value)

    if suffix != "":
        raise KubernetesResourceDiscoveryError(
            f"Expected an integer resource, received suffix {suffix!r}."
        )

    return int(number)


class KubernetesResourceDiscovery:
    """Build site resource snapshots from Nodes and active Pods."""

    def __init__(
        self,
        kubernetes_adapter: KubernetesAdapter,
        site_mapping_file: str | Path,
        site_label: str = "compute-site",
        gpu_resource_name: str = "nvidia.com/gpu",
    ) -> None:
        self.kubernetes_adapter = kubernetes_adapter
        self.site_mapping_file = (
            Path(site_mapping_file).expanduser().resolve()
        )
        self.site_label = site_label
        self.gpu_resource_name = gpu_resource_name

        self.site_mapping = self._load_site_mapping()

    def discover(self) -> list[KubernetesSiteResources]:
        """Read Nodes and Pods and return one snapshot per compute site."""

        nodes_document = self.kubernetes_adapter.list_nodes()
        pods_document = (
            self.kubernetes_adapter.list_pods_all_namespaces()
        )

        node_items = nodes_document.get("items")
        pod_items = pods_document.get("items")

        if not isinstance(node_items, list):
            raise KubernetesResourceDiscoveryError(
                "Kubernetes Node response has no valid items list."
            )

        if not isinstance(pod_items, list):
            raise KubernetesResourceDiscoveryError(
                "Kubernetes Pod response has no valid items list."
            )

        requested_by_node: dict[str, dict[str, int]] = {}
        active_pods_by_node: dict[str, int] = {}

        for pod in pod_items:
            if not isinstance(pod, dict):
                continue

            spec = pod.get("spec", {})
            status = pod.get("status", {})

            if not isinstance(spec, dict):
                continue

            if not isinstance(status, dict):
                status = {}

            node_name = spec.get("nodeName")
            phase = str(status.get("phase", ""))

            if not node_name:
                continue

            # Completed Pods no longer reserve scheduling resources.
            if phase in {"Succeeded", "Failed"}:
                continue

            node_name = str(node_name)

            pod_request = self._calculate_pod_request(pod)

            aggregate = requested_by_node.setdefault(
                node_name,
                {
                    "cpu_m": 0,
                    "memory_bytes": 0,
                    "gpu": 0,
                },
            )

            aggregate["cpu_m"] += pod_request["cpu_m"]
            aggregate["memory_bytes"] += (
                pod_request["memory_bytes"]
            )
            aggregate["gpu"] += pod_request["gpu"]

            active_pods_by_node[node_name] = (
                active_pods_by_node.get(node_name, 0) + 1
            )

        snapshots: list[KubernetesSiteResources] = []

        for node in node_items:
            if not isinstance(node, dict):
                continue

            metadata = node.get("metadata", {})
            spec = node.get("spec", {})
            status = node.get("status", {})

            if not isinstance(metadata, dict):
                continue

            labels = metadata.get("labels", {})

            if not isinstance(labels, dict):
                continue

            site_id = labels.get(self.site_label)

            # Ignore the control plane and any unlabelled nodes.
            if site_id is None:
                continue

            site_id = str(site_id)

            if site_id not in self.site_mapping:
                raise KubernetesResourceDiscoveryError(
                    f"Node uses unknown site ID {site_id!r}."
                )

            node_name = str(metadata.get("name", ""))

            if not node_name:
                raise KubernetesResourceDiscoveryError(
                    "Kubernetes Node has no metadata.name."
                )

            if not isinstance(spec, dict):
                spec = {}

            if not isinstance(status, dict):
                status = {}

            allocatable = status.get("allocatable", {})

            if not isinstance(allocatable, dict):
                allocatable = {}

            allocatable_cpu_m = parse_cpu_millicores(
                allocatable.get("cpu", "0")
            )
            allocatable_memory_bytes = parse_memory_bytes(
                allocatable.get("memory", "0")
            )
            allocatable_gpu = parse_integer_resource(
                allocatable.get(self.gpu_resource_name, "0")
            )

            requested = requested_by_node.get(
                node_name,
                {
                    "cpu_m": 0,
                    "memory_bytes": 0,
                    "gpu": 0,
                },
            )

            ready = self._node_is_ready(status)
            unschedulable = bool(
                spec.get("unschedulable", False)
            )

            mapping = self.site_mapping[site_id]

            snapshots.append(
                KubernetesSiteResources(
                    site_id=site_id,
                    site_name=mapping["name"],
                    network_access_node=(
                        mapping["network_access_node"]
                    ),
                    node_name=node_name,
                    ready=ready,
                    schedulable=ready and not unschedulable,
                    allocatable_cpu_m=allocatable_cpu_m,
                    requested_cpu_m=requested["cpu_m"],
                    available_cpu_m=max(
                        allocatable_cpu_m
                        - requested["cpu_m"],
                        0,
                    ),
                    allocatable_memory_bytes=(
                        allocatable_memory_bytes
                    ),
                    requested_memory_bytes=(
                        requested["memory_bytes"]
                    ),
                    available_memory_bytes=max(
                        allocatable_memory_bytes
                        - requested["memory_bytes"],
                        0,
                    ),
                    allocatable_gpu=allocatable_gpu,
                    requested_gpu=requested["gpu"],
                    available_gpu=max(
                        allocatable_gpu - requested["gpu"],
                        0,
                    ),
                    active_pod_count=active_pods_by_node.get(
                        node_name,
                        0,
                    ),
                )
            )

        return sorted(
            snapshots,
            key=lambda item: item.site_id,
        )

    def _load_site_mapping(
        self,
    ) -> dict[str, dict[str, str]]:
        if not self.site_mapping_file.is_file():
            raise KubernetesResourceDiscoveryError(
                f"Site mapping does not exist: "
                f"{self.site_mapping_file}"
            )

        try:
            with self.site_mapping_file.open(
                "r",
                encoding="utf-8",
            ) as file:
                document = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise KubernetesResourceDiscoveryError(
                f"Cannot read site mapping: "
                f"{self.site_mapping_file}"
            ) from exc

        raw_sites = document.get("sites")

        if not isinstance(raw_sites, list):
            raise KubernetesResourceDiscoveryError(
                "'sites' must be a JSON list."
            )

        mapping: dict[str, dict[str, str]] = {}

        for item in raw_sites:
            if not isinstance(item, dict):
                raise KubernetesResourceDiscoveryError(
                    "Every site mapping must be an object."
                )

            site_id = str(item["site_id"])

            if site_id in mapping:
                raise KubernetesResourceDiscoveryError(
                    f"Duplicate site ID: {site_id}"
                )

            mapping[site_id] = {
                "name": str(item["name"]),
                "network_access_node": str(
                    item["network_access_node"]
                ),
            }

        return mapping

    def _calculate_pod_request(
        self,
        pod: dict[str, Any],
    ) -> dict[str, int]:
        """
        Calculate the effective scheduler request of one Pod.

        Regular container requests are summed. For init containers, the
        largest request is used. Pod overhead is added when present.
        """

        spec = pod.get("spec", {})

        if not isinstance(spec, dict):
            return {
                "cpu_m": 0,
                "memory_bytes": 0,
                "gpu": 0,
            }

        regular_total = {
            "cpu_m": 0,
            "memory_bytes": 0,
            "gpu": 0,
        }

        containers = spec.get("containers", [])

        if isinstance(containers, list):
            for container in containers:
                request = self._container_request(container)

                regular_total["cpu_m"] += request["cpu_m"]
                regular_total["memory_bytes"] += (
                    request["memory_bytes"]
                )
                regular_total["gpu"] += request["gpu"]

        init_max = {
            "cpu_m": 0,
            "memory_bytes": 0,
            "gpu": 0,
        }

        init_containers = spec.get("initContainers", [])

        if isinstance(init_containers, list):
            for container in init_containers:
                request = self._container_request(container)

                init_max["cpu_m"] = max(
                    init_max["cpu_m"],
                    request["cpu_m"],
                )
                init_max["memory_bytes"] = max(
                    init_max["memory_bytes"],
                    request["memory_bytes"],
                )
                init_max["gpu"] = max(
                    init_max["gpu"],
                    request["gpu"],
                )

        effective = {
            "cpu_m": max(
                regular_total["cpu_m"],
                init_max["cpu_m"],
            ),
            "memory_bytes": max(
                regular_total["memory_bytes"],
                init_max["memory_bytes"],
            ),
            "gpu": max(
                regular_total["gpu"],
                init_max["gpu"],
            ),
        }

        overhead = spec.get("overhead", {})

        if isinstance(overhead, dict):
            effective["cpu_m"] += parse_cpu_millicores(
                overhead.get("cpu", "0")
            )
            effective["memory_bytes"] += parse_memory_bytes(
                overhead.get("memory", "0")
            )

        return effective

    def _container_request(
        self,
        container: Any,
    ) -> dict[str, int]:
        if not isinstance(container, dict):
            return {
                "cpu_m": 0,
                "memory_bytes": 0,
                "gpu": 0,
            }

        resources = container.get("resources", {})

        if not isinstance(resources, dict):
            resources = {}

        requests = resources.get("requests", {})

        if not isinstance(requests, dict):
            requests = {}

        return {
            "cpu_m": parse_cpu_millicores(
                requests.get("cpu", "0")
            ),
            "memory_bytes": parse_memory_bytes(
                requests.get("memory", "0")
            ),
            "gpu": parse_integer_resource(
                requests.get(self.gpu_resource_name, "0")
            ),
        }

    @staticmethod
    def _node_is_ready(
        status: dict[str, Any],
    ) -> bool:
        conditions = status.get("conditions", [])

        if not isinstance(conditions, list):
            return False

        for condition in conditions:
            if not isinstance(condition, dict):
                continue

            if condition.get("type") == "Ready":
                return condition.get("status") == "True"

        return False
