"""Kubernetes adapter implemented with the official Python client."""

from __future__ import annotations

import os
import time
from typing import Any

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from orchestrator.adapters.kubernetes_adapter import (
    KubernetesAdapter,
    KubernetesAdapterError,
    KubernetesJobResult,
    build_kubernetes_job_manifest,
)
from orchestrator.adapters.kubernetes_client_loader import (
    KubernetesClientConfigurationError,
    load_kubernetes_client_configuration,
)


class KubernetesApiAdapter(KubernetesAdapter):
    """Implement the runtime adapter contract without subprocesses."""

    def __init__(
        self,
        namespace: str = "compute-optical",
        context: str = "kind-compute-optical",
        core_api: Any | None = None,
        batch_api: Any | None = None,
        api_client: Any | None = None,
    ) -> None:
        super().__init__(namespace=namespace, context=context)

        if core_api is None or batch_api is None or api_client is None:
            try:
                self.credential_source = (
                    load_kubernetes_client_configuration(context)
                )
            except KubernetesClientConfigurationError as exc:
                raise KubernetesAdapterError(str(exc)) from exc
        else:
            self.credential_source = "injected"

        self.core_api = core_api or client.CoreV1Api()
        self.batch_api = batch_api or client.BatchV1Api()
        self.api_client = api_client or client.ApiClient()

    def verify_cluster(self) -> None:
        nodes = self.list_nodes().get("items")
        if not isinstance(nodes, list) or not nodes:
            raise KubernetesAdapterError(
                "The Kubernetes cluster contains no nodes."
            )

    def list_nodes(self) -> dict[str, Any]:
        try:
            response = self.core_api.list_node()
        except ApiException as exc:
            self._raise_api_error("list Kubernetes Nodes", exc)
        return self._serialize_document(response, "NodeList")

    def list_pods_all_namespaces(self) -> dict[str, Any]:
        try:
            response = self.core_api.list_pod_for_all_namespaces()
        except ApiException as exc:
            self._raise_api_error("list Kubernetes Pods", exc)
        return self._serialize_document(response, "PodList")

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

        try:
            self.core_api.read_namespace(self.namespace)
            return
        except ApiException as exc:
            if exc.status != 404:
                self._raise_api_error(
                    f"read Namespace {self.namespace}", exc
                )

        body = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": self.namespace},
        }
        try:
            self.core_api.create_namespace(body=body)
        except ApiException as exc:
            # A concurrent creator may win after the read returned 404.
            if exc.status != 409:
                self._raise_api_error(
                    f"create Namespace {self.namespace}", exc
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
        node_name: str | None = None,
        accelerator_product: str | None = None,
        accelerator_memory_mib: int | None = None,
        accelerator_interconnect: str | None = None,
        accelerator_partition_mode: str | None = None,
    ) -> str:
        job_name = self.job_name_for_task(task_id)
        body = build_kubernetes_job_manifest(
            namespace=self.namespace,
            task_id=task_id,
            site_id=site_id,
            image=image,
            shell_command=shell_command,
            cpu_request=cpu_request,
            memory_request=memory_request,
            cpu_limit=cpu_limit,
            memory_limit=memory_limit,
            gpu_request=gpu_request,
            gpu_resource_name=gpu_resource_name,
            image_pull_policy=image_pull_policy,
            node_name=node_name,
            accelerator_product=accelerator_product,
            accelerator_memory_mib=accelerator_memory_mib,
            accelerator_interconnect=accelerator_interconnect,
            accelerator_partition_mode=accelerator_partition_mode,
        )
        try:
            self.batch_api.create_namespaced_job(
                namespace=self.namespace,
                body=body,
            )
        except ApiException as exc:
            self._raise_api_error(
                f"create Kubernetes Job {job_name}", exc
            )
        return job_name

    def get_job_state(self, job_name: str) -> str:
        try:
            response = self.batch_api.read_namespaced_job(
                name=job_name,
                namespace=self.namespace,
            )
        except ApiException as exc:
            if exc.status == 404:
                return "MISSING"
            self._raise_api_error(
                f"inspect Kubernetes Job {job_name}", exc
            )

        document = self._serialize_document(response, "Job")
        status = document.get("status") or {}
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
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            state = self.get_job_state(job_name)
            if state == "SUCCEEDED":
                return self._build_result(
                    task_id=task_id,
                    job_name=job_name,
                    site_id=site_id,
                    succeeded=True,
                )
            if state == "FAILED":
                return self._build_result(
                    task_id=task_id,
                    job_name=job_name,
                    site_id=site_id,
                    succeeded=False,
                )
            if state == "MISSING":
                raise KubernetesAdapterError(
                    f"Job disappeared while waiting: {job_name}."
                )
            time.sleep(poll_interval_seconds)

        raise KubernetesAdapterError(
            f"Timed out after {timeout_seconds} seconds "
            f"waiting for Job {job_name}."
        )

    def get_node_site_label(self, node_name: str) -> str | None:
        try:
            response = self.core_api.read_node(name=node_name)
        except ApiException as exc:
            self._raise_api_error(
                f"read Kubernetes Node {node_name}", exc
            )
        document = self._serialize_document(response, "Node")
        labels = (document.get("metadata") or {}).get("labels") or {}
        value = labels.get("compute-site")
        return str(value) if value is not None else None

    def get_job_logs(self, job_name: str) -> str:
        pod = self._get_job_pod(job_name)
        pod_name = str((pod.get("metadata") or {}).get("name") or "")
        if not pod_name:
            raise KubernetesAdapterError(
                f"Pod for Job {job_name} has no name."
            )
        try:
            logs = self.core_api.read_namespaced_pod_log(
                name=pod_name,
                namespace=self.namespace,
            )
        except ApiException as exc:
            self._raise_api_error(
                f"read logs for Pod {pod_name}", exc
            )
        return str(logs or "")

    def delete_job(
        self,
        job_name: str,
        ignore_not_found: bool = True,
    ) -> None:
        try:
            self.batch_api.delete_namespaced_job(
                name=job_name,
                namespace=self.namespace,
                propagation_policy="Background",
            )
        except ApiException as exc:
            if ignore_not_found and exc.status == 404:
                return
            self._raise_api_error(
                f"delete Kubernetes Job {job_name}", exc
            )

    def _build_result(
        self,
        task_id: str,
        job_name: str,
        site_id: str,
        succeeded: bool,
    ) -> KubernetesJobResult:
        pod = self._get_job_pod(job_name)
        metadata = pod.get("metadata") or {}
        spec = pod.get("spec") or {}
        status = pod.get("status") or {}
        pod_name = str(metadata.get("name") or "unknown")
        node_name = str(spec.get("nodeName") or "")
        return KubernetesJobResult(
            job_name=job_name,
            task_id=task_id,
            site_id=site_id,
            pod_name=pod_name,
            node_name=node_name,
            node_site_label=(
                self.get_node_site_label(node_name)
                if node_name
                else None
            ),
            pod_phase=str(status.get("phase") or "Unknown"),
            succeeded=succeeded,
            logs=self.get_job_logs(job_name),
        )

    def _get_job_pod(self, job_name: str) -> dict[str, Any]:
        try:
            response = self.core_api.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=(
                    f"batch.kubernetes.io/job-name={job_name}"
                ),
            )
        except ApiException as exc:
            self._raise_api_error(
                f"list Pods for Job {job_name}", exc
            )
        document = self._serialize_document(response, "PodList")
        items = document.get("items")
        if not isinstance(items, list) or not items:
            raise KubernetesAdapterError(
                f"No Pod was found for Job {job_name}."
            )
        return items[0]

    def _serialize_document(
        self,
        value: Any,
        resource_kind: str,
    ) -> dict[str, Any]:
        document = self.api_client.sanitize_for_serialization(value)
        if not isinstance(document, dict):
            raise KubernetesAdapterError(
                f"Kubernetes {resource_kind} response must be an object."
            )
        return document

    @staticmethod
    def _raise_api_error(operation: str, exc: ApiException) -> None:
        detail = exc.body or exc.reason or str(exc)
        raise KubernetesAdapterError(
            f"Failed to {operation} (HTTP {exc.status}): {detail}"
        ) from exc
