"""Kubernetes Job adapter implemented through kubectl."""

from __future__ import annotations

import os

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any


class KubernetesAdapterError(RuntimeError):
    """Raised when a kubectl operation fails."""


@dataclass(frozen=True)
class KubernetesJobResult:
    """Final execution result of one Kubernetes Job."""

    job_name: str
    task_id: str
    site_id: str
    pod_name: str
    node_name: str
    node_site_label: str | None
    pod_phase: str
    succeeded: bool
    logs: str


class KubernetesAdapter:
    """Create, inspect, wait for, and delete Kubernetes Jobs."""

    def __init__(
        self,
        namespace: str = "compute-optical",
        context: str = "kind-compute-optical",
        kubectl_executable: str = "kubectl",
    ) -> None:
        self.namespace = namespace
        self.context = context
        self.kubectl_executable = kubectl_executable

    def verify_cluster(self) -> None:
        """Verify that kubectl can access the expected cluster."""

        self._run(
            [
                "get", "nodes", "-o", "name",
            ],
            capture_output=True,
        )

        nodes_document = self._run_json(
            [
                "get",
                "nodes",
                "-o",
                "json",
            ]
        )

        items = nodes_document.get("items")

        if not isinstance(items, list) or not items:
            raise KubernetesAdapterError(
                "The Kubernetes cluster contains no nodes."
            )

    def list_nodes(self) -> dict[str, Any]:
        """Return all Kubernetes Node objects."""

        return self._run_json(
            [
                "get",
                "nodes",
                "-o",
                "json",
            ]
        )

    def list_pods_all_namespaces(self) -> dict[str, Any]:
        """Return Pods from all namespaces."""

        return self._run_json(
            [
                "get",
                "pods",
                "--all-namespaces",
                "-o",
                "json",
            ]
        )

    def ensure_namespace(self) -> None:
        namespace_management = os.getenv(
            "KUBERNETES_NAMESPACE_MANAGEMENT",
            "auto",
        ).strip().lower()

        if namespace_management in {
            "external",
            "disabled",
            "false",
            "0",
            "no",
        }:
            return

        """Create the namespace when it does not already exist."""

        result = self._run(
            [
                "get",
                "namespace",
                self.namespace,
            ],
            check=False,
            capture_output=True,
        )

        if result.returncode == 0:
            return

        self._run(
            [
                "create",
                "namespace",
                self.namespace,
            ]
        )

    def create_job(
        self,
        task_id: str,
        site_id: str,
        image: str,
        shell_command: str,
        cpu_request: str = "100m",
        memory_request: str = "32Mi",
        cpu_limit: str = "200m",
        memory_limit: str = "64Mi",
        gpu_request: int = 0,
        gpu_resource_name: str = "nvidia.com/gpu",
        image_pull_policy: str = "Never",
    ) -> str:
        """
        Create one Kubernetes Job constrained to a compute-site label.

        The lightweight Kubernetes resource requests are currently separate
        from the simulated compute requirements in TaskRequest. They will be
        unified after Kubernetes node-resource discovery is implemented.
        """

        job_name = self.job_name_for_task(task_id)
        task_label = self.label_value(task_id)

        resource_requests: dict[str, Any] = {
            "cpu": cpu_request,
            "memory": memory_request,
        }

        resource_limits: dict[str, Any] = {
            "cpu": cpu_limit,
            "memory": memory_limit,
        }

        if gpu_request > 0:
            resource_requests[gpu_resource_name] = gpu_request
            resource_limits[gpu_resource_name] = gpu_request

        manifest: dict[str, Any] = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": job_name,
                "namespace": self.namespace,
                "labels": {
                    "app": "compute-optical-task",
                    "orchestrator-task-id": task_label,
                },
            },
            "spec": {
                "backoffLimit": 0,
                "ttlSecondsAfterFinished": 600,
                "template": {
                    "metadata": {
                        "labels": {
                            "app": "compute-optical-task",
                            "orchestrator-task-id": task_label,
                        },
                    },
                    "spec": {
                        "restartPolicy": "Never",
                        "nodeSelector": {
                            "compute-site": site_id,
                        },
                        "containers": [
                            {
                                "name": "worker",
                                "image": image,
                                "imagePullPolicy": image_pull_policy,
                                "resources": {
                                    "requests": resource_requests,
                                    "limits": resource_limits,
                                },
                                "env": [
                                    {
                                        "name": "TASK_ID",
                                        "value": task_id,
                                    },
                                    {
                                        "name": "EXPECTED_SITE_ID",
                                        "value": site_id,
                                    },
                                    {
                                        "name": "NODE_NAME",
                                        "valueFrom": {
                                            "fieldRef": {
                                                "fieldPath": "spec.nodeName",
                                            }
                                        },
                                    },
                                    {
                                        "name": "POD_NAME",
                                        "valueFrom": {
                                            "fieldRef": {
                                                "fieldPath": "metadata.name",
                                            }
                                        },
                                    },
                                ],
                                "command": [
                                    "sh",
                                    "-c",
                                    shell_command,
                                ],
                            }
                        ],
                    },
                },
            },
        }

        completed = self._run(
            [
                "create",
                "-f",
                "-",
            ],
            input_text=json.dumps(manifest),
            capture_output=True,
            check=False,
        )

        if completed.returncode != 0:
            raise KubernetesAdapterError(
                "Failed to create Kubernetes Job.\n"
                f"Job: {job_name}\n"
                f"STDOUT:\n{completed.stdout}\n"
                f"STDERR:\n{completed.stderr}"
            )

        return job_name

    def get_job_state(
        self,
        job_name: str,
    ) -> str:
        """
        Return MISSING, PENDING, RUNNING, SUCCEEDED or FAILED.
        """

        completed = self._run(
            [
                "get",
                "job",
                job_name,
                "-n",
                self.namespace,
                "-o",
                "json",
            ],
            capture_output=True,
            check=False,
        )

        if completed.returncode != 0:
            error_text = (
                f"{completed.stdout or ''}\n"
                f"{completed.stderr or ''}"
            )

            if "NotFound" in error_text:
                return "MISSING"

            raise KubernetesAdapterError(
                "Failed to inspect Kubernetes Job.\n"
                f"Job: {job_name}\n"
                f"STDOUT:\n{completed.stdout or ''}\n"
                f"STDERR:\n{completed.stderr or ''}"
            )

        try:
            document = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise KubernetesAdapterError(
                f"kubectl returned invalid Job JSON: {job_name}"
            ) from exc

        status = document.get("status", {})

        if int(status.get("succeeded", 0) or 0) > 0:
            return "SUCCEEDED"

        if int(status.get("failed", 0) or 0) > 0:
            return "FAILED"

        if int(status.get("active", 0) or 0) > 0:
            return "RUNNING"

        return "PENDING"

    def wait_for_completion(
        self,
        task_id: str,
        job_name: str,
        site_id: str,
        timeout_seconds: int = 120,
        poll_interval_seconds: float = 1.0,
    ) -> KubernetesJobResult:
        """Wait until a Job succeeds or fails."""

        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            job_document = self._run_json(
                [
                    "get",
                    "job",
                    job_name,
                    "-n",
                    self.namespace,
                    "-o",
                    "json",
                ]
            )

            status = job_document.get("status", {})

            succeeded_count = int(status.get("succeeded", 0) or 0)
            failed_count = int(status.get("failed", 0) or 0)

            if succeeded_count > 0:
                return self._build_result(
                    task_id=task_id,
                    job_name=job_name,
                    site_id=site_id,
                    succeeded=True,
                )

            if failed_count > 0:
                return self._build_result(
                    task_id=task_id,
                    job_name=job_name,
                    site_id=site_id,
                    succeeded=False,
                )

            time.sleep(poll_interval_seconds)

        raise KubernetesAdapterError(
            f"Timed out after {timeout_seconds} seconds "
            f"waiting for Job {job_name}."
        )

    def get_node_site_label(
        self,
        node_name: str,
    ) -> str | None:
        """Return the compute-site label of one node."""

        node_document = self._run_json(
            [
                "get",
                "node",
                node_name,
                "-o",
                "json",
            ]
        )

        metadata = node_document.get("metadata", {})
        labels = metadata.get("labels", {})

        value = labels.get("compute-site")

        return str(value) if value is not None else None

    def get_job_logs(
        self,
        job_name: str,
    ) -> str:
        """Return logs for the Job's Pod."""

        completed = self._run(
            [
                "logs",
                f"job/{job_name}",
                "-n",
                self.namespace,
            ],
            capture_output=True,
        )

        return completed.stdout

    def delete_job(
        self,
        job_name: str,
        ignore_not_found: bool = True,
    ) -> None:
        """Delete a Job and its controlled Pod."""

        arguments = [
            "delete",
            "job",
            job_name,
            "-n",
            self.namespace,
        ]

        if ignore_not_found:
            arguments.append("--ignore-not-found=true")

        self._run(arguments)

    def job_name_for_task(self, task_id: str) -> str:
        """Create a deterministic Kubernetes-compatible Job name."""

        normalized = re.sub(
            r"[^a-z0-9-]+",
            "-",
            task_id.lower(),
        ).strip("-")

        if not normalized:
            normalized = "task"

        digest = hashlib.sha256(
            task_id.encode("utf-8")
        ).hexdigest()[:8]

        normalized = normalized[:45].rstrip("-")

        return f"co-{normalized}-{digest}"

    @staticmethod
    def label_value(value: str) -> str:
        """Create a Kubernetes-compatible label value."""

        normalized = re.sub(
            r"[^A-Za-z0-9_.-]+",
            "-",
            value,
        ).strip("-_.")

        if not normalized:
            normalized = "task"

        return normalized[:63]

    def _build_result(
        self,
        task_id: str,
        job_name: str,
        site_id: str,
        succeeded: bool,
    ) -> KubernetesJobResult:
        pod = self._get_job_pod(job_name)

        metadata = pod.get("metadata", {})
        spec = pod.get("spec", {})
        status = pod.get("status", {})

        pod_name = str(metadata.get("name", "unknown"))
        node_name = str(spec.get("nodeName", ""))
        pod_phase = str(status.get("phase", "Unknown"))

        node_site_label = (
            self.get_node_site_label(node_name)
            if node_name
            else None
        )

        logs = self.get_job_logs(job_name)

        return KubernetesJobResult(
            job_name=job_name,
            task_id=task_id,
            site_id=site_id,
            pod_name=pod_name,
            node_name=node_name,
            node_site_label=node_site_label,
            pod_phase=pod_phase,
            succeeded=succeeded,
            logs=logs,
        )

    def _get_job_pod(
        self,
        job_name: str,
    ) -> dict[str, Any]:
        pods_document = self._run_json(
            [
                "get",
                "pods",
                "-n",
                self.namespace,
                "-l",
                f"batch.kubernetes.io/job-name={job_name}",
                "-o",
                "json",
            ]
        )

        items = pods_document.get("items")

        if not isinstance(items, list) or not items:
            raise KubernetesAdapterError(
                f"No Pod was found for Job {job_name}."
            )

        return items[0]

    def _run_json(
        self,
        arguments: list[str],
    ) -> dict[str, Any]:
        completed = self._run(
            arguments,
            capture_output=True,
        )

        try:
            document = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise KubernetesAdapterError(
                "kubectl returned invalid JSON.\n"
                f"Command output:\n{completed.stdout}"
            ) from exc

        if not isinstance(document, dict):
            raise KubernetesAdapterError(
                "kubectl JSON output must be an object."
            )

        return document

    def _run(
        self,
        arguments: list[str],
        input_text: str | None = None,
        capture_output: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            self.kubectl_executable,
            "--context",
            self.context,
            *arguments,
        ]

        try:
            completed = subprocess.run(
                command,
                input=input_text,
                text=True,
                capture_output=capture_output,
                check=False,
            )
        except OSError as exc:
            raise KubernetesAdapterError(
                f"Cannot execute kubectl: {exc}"
            ) from exc

        if check and completed.returncode != 0:
            raise KubernetesAdapterError(
                "kubectl command failed.\n"
                f"Command: {' '.join(command)}\n"
                f"Return code: {completed.returncode}\n"
                f"STDOUT:\n{completed.stdout or ''}\n"
                f"STDERR:\n{completed.stderr or ''}"
            )

        return completed
