"""Unit tests for the official Kubernetes Python API adapter."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

from kubernetes.client.exceptions import ApiException


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.kubernetes_adapter import (  # noqa: E402
    KubernetesAdapterError,
)
from orchestrator.adapters.kubernetes_api_adapter import (  # noqa: E402
    KubernetesApiAdapter,
)


class FakeApiClient:
    @staticmethod
    def sanitize_for_serialization(value: Any) -> Any:
        return value


class FakeCoreApi:
    def __init__(self) -> None:
        self.namespace_exists = True
        self.created_namespaces: list[dict[str, Any]] = []
        self.nodes: list[dict[str, Any]] = [
            {
                "metadata": {
                    "name": "worker-2",
                    "labels": {"compute-site": "site-rennes"},
                }
            }
        ]
        self.pods: list[dict[str, Any]] = [
            {
                "metadata": {"name": "job-pod"},
                "spec": {"nodeName": "worker-2"},
                "status": {"phase": "Succeeded"},
            }
        ]
        self.log_text = "official-client-log\n"

    def list_node(self) -> dict[str, Any]:
        return {"items": self.nodes}

    def list_pod_for_all_namespaces(self) -> dict[str, Any]:
        return {"items": self.pods}

    def read_namespace(self, name: str) -> dict[str, Any]:
        if not self.namespace_exists:
            raise ApiException(status=404, reason="Not Found")
        return {"metadata": {"name": name}}

    def create_namespace(self, body: dict[str, Any]) -> dict[str, Any]:
        self.created_namespaces.append(body)
        self.namespace_exists = True
        return body

    def read_node(self, name: str) -> dict[str, Any]:
        return next(
            node
            for node in self.nodes
            if node["metadata"]["name"] == name
        )

    def list_namespaced_pod(
        self,
        namespace: str,
        label_selector: str,
    ) -> dict[str, Any]:
        del namespace, label_selector
        return {"items": self.pods}

    def read_namespaced_pod_log(
        self,
        name: str,
        namespace: str,
    ) -> str:
        del name, namespace
        return self.log_text


class FakeBatchApi:
    def __init__(self) -> None:
        self.created_jobs: list[dict[str, Any]] = []
        self.deleted_jobs: list[str] = []
        self.status: dict[str, int] = {}
        self.job_missing = False
        self.delete_missing = False

    def create_namespaced_job(
        self,
        namespace: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        del namespace
        self.created_jobs.append(body)
        return body

    def read_namespaced_job(
        self,
        name: str,
        namespace: str,
    ) -> dict[str, Any]:
        del name, namespace
        if self.job_missing:
            raise ApiException(status=404, reason="Not Found")
        return {"status": self.status}

    def delete_namespaced_job(
        self,
        name: str,
        namespace: str,
        propagation_policy: str,
    ) -> dict[str, Any]:
        del namespace, propagation_policy
        if self.delete_missing:
            raise ApiException(status=404, reason="Not Found")
        self.deleted_jobs.append(name)
        return {"status": "Success"}


def create_adapter() -> tuple[
    KubernetesApiAdapter,
    FakeCoreApi,
    FakeBatchApi,
]:
    core = FakeCoreApi()
    batch = FakeBatchApi()
    return (
        KubernetesApiAdapter(
            namespace="compute-optical",
            context="ignored-in-injected-test",
            core_api=core,
            batch_api=batch,
            api_client=FakeApiClient(),
        ),
        core,
        batch,
    )


class KubernetesApiAdapterTest(unittest.TestCase):
    def test_discovery_and_namespace_creation(self) -> None:
        adapter, core, _ = create_adapter()
        adapter.verify_cluster()
        self.assertEqual(len(adapter.list_nodes()["items"]), 1)
        self.assertEqual(
            len(adapter.list_pods_all_namespaces()["items"]),
            1,
        )

        core.namespace_exists = False
        adapter.ensure_namespace()
        self.assertEqual(
            core.created_namespaces[0]["metadata"]["name"],
            "compute-optical",
        )

    def test_manifest_constrains_site_hostname_and_gpu(self) -> None:
        adapter, _, batch = create_adapter()
        job_name = adapter.create_job(
            task_id="official-client-task",
            site_id="site-rennes",
            node_name="worker-2",
            image="busybox:1.36",
            shell_command="echo done",
            gpu_request=2,
        )

        manifest = batch.created_jobs[0]
        self.assertEqual(manifest["metadata"]["name"], job_name)
        pod_spec = manifest["spec"]["template"]["spec"]
        self.assertEqual(
            pod_spec["nodeSelector"],
            {
                "compute-site": "site-rennes",
                "kubernetes.io/hostname": "worker-2",
            },
        )
        resources = pod_spec["containers"][0]["resources"]
        self.assertEqual(resources["requests"]["nvidia.com/gpu"], 2)
        self.assertEqual(resources["limits"]["nvidia.com/gpu"], 2)

    def test_job_state_mapping_including_404(self) -> None:
        adapter, _, batch = create_adapter()
        cases = [
            ({}, "PENDING"),
            ({"active": 1}, "RUNNING"),
            ({"succeeded": 1}, "SUCCEEDED"),
            ({"failed": 1}, "FAILED"),
        ]
        for status, expected in cases:
            with self.subTest(expected=expected):
                batch.status = status
                self.assertEqual(adapter.get_job_state("job"), expected)

        batch.job_missing = True
        self.assertEqual(adapter.get_job_state("job"), "MISSING")

    def test_deletion_is_idempotent_only_when_requested(self) -> None:
        adapter, _, batch = create_adapter()
        batch.delete_missing = True
        adapter.delete_job("missing", ignore_not_found=True)

        with self.assertRaises(KubernetesAdapterError):
            adapter.delete_job("missing", ignore_not_found=False)

    def test_wait_result_uses_actual_pod_node_and_logs(self) -> None:
        adapter, _, batch = create_adapter()
        batch.status = {"succeeded": 1}
        result = adapter.wait_for_completion(
            task_id="task-1",
            job_name="job-1",
            site_id="site-rennes",
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.pod_name, "job-pod")
        self.assertEqual(result.node_name, "worker-2")
        self.assertEqual(result.node_site_label, "site-rennes")
        self.assertEqual(result.logs, "official-client-log\n")


if __name__ == "__main__":
    unittest.main()
