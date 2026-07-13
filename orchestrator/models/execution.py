"""Persistent Kubernetes execution models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KubernetesExecutionRecord:
    """Persistent execution information for one Kubernetes Job."""

    task_id: str
    job_name: str
    namespace: str
    requested_site: str

    pod_name: str | None
    node_name: str | None
    node_site_label: str | None

    phase: str
    succeeded: bool | None
    logs: str | None

    created_at: str
    updated_at: str
