"""Test accelerator discovery, filtering, manifests and migration."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.kubernetes_adapter import (  # noqa: E402
    build_kubernetes_job_manifest,
)
from orchestrator.adapters.kubernetes_resource_discovery import (  # noqa: E402
    KubernetesResourceDiscovery,
)
from orchestrator.models.runtime_task import (  # noqa: E402
    RuntimeTaskRequest,
)
from orchestrator.models.task_profile import (  # noqa: E402
    TaskProfile,
    TaskProfileError,
)
from orchestrator.repositories.runtime_task_repository import (  # noqa: E402
    RuntimeTaskRepository,
)
from orchestrator.services.kubernetes_joint_planner import (  # noqa: E402
    KubernetesJointPlanner,
)
from orchestrator.storage.database import Database  # noqa: E402


class FakeKubernetesAdapter:
    def list_nodes(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "metadata": {
                        "name": "gpu-worker",
                        "labels": {
                            "compute-site": "site-gpu",
                            "accelerator.compute-optical.io/product": (
                                "NVIDIA-A100-SXM4-40GB"
                            ),
                            "accelerator.compute-optical.io/memory-mib": (
                                "40960"
                            ),
                            "accelerator.compute-optical.io/interconnect": (
                                "nvlink"
                            ),
                            "accelerator.compute-optical.io/partition-mode": (
                                "full"
                            ),
                        },
                    },
                    "spec": {"unschedulable": False},
                    "status": {
                        "allocatable": {
                            "cpu": "8",
                            "memory": "32Gi",
                            "nvidia.com/gpu": "4",
                        },
                        "conditions": [
                            {"type": "Ready", "status": "True"}
                        ],
                    },
                }
            ]
        }

    def list_pods_all_namespaces(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "spec": {
                        "nodeName": "gpu-worker",
                        "containers": [
                            {
                                "resources": {
                                    "requests": {
                                        "cpu": "500m",
                                        "memory": "1Gi",
                                        "nvidia.com/gpu": "1",
                                    }
                                }
                            }
                        ],
                    },
                    "status": {"phase": "Running"},
                }
            ]
        }


def accelerator_profile() -> TaskProfile:
    return TaskProfile.from_dict(
        {
            "profile_id": "accelerator-test",
            "description": "test",
            "image": "worker:test",
            "allowed_source_access_nodes": ["trx Lorient_KMA"],
            "default_cpu_request_m": 500,
            "max_cpu_request_m": 4000,
            "default_memory_request_mib": 512,
            "max_memory_request_mib": 8192,
            "default_cpu_limit_m": 1000,
            "max_cpu_limit_m": 8000,
            "default_memory_limit_mib": 1024,
            "max_memory_limit_mib": 16384,
            "max_gpu_request": 4,
            "default_bandwidth_gbps": 100,
            "max_bandwidth_gbps": 400,
            "default_duration_seconds": 1,
            "max_duration_seconds": 30,
            "command_template": "echo __DURATION_SECONDS__",
            "allowed_accelerator_resource_names": [
                "nvidia.com/gpu"
            ],
            "allowed_accelerator_products": [
                "NVIDIA-A100-SXM4-40GB"
            ],
            "allowed_accelerator_interconnects": ["nvlink"],
            "allowed_accelerator_partition_modes": ["full"],
        }
    )


class AcceleratorAwareSchedulingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.mapping_file = self.root / "sites.json"
        self.mapping_file.write_text(
            json.dumps(
                {
                    "sites": [
                        {
                            "site_id": "site-gpu",
                            "name": "GPU site",
                            "network_access_node": "trx Brest_KLA",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def discover_site(self):
        discovery = KubernetesResourceDiscovery(
            kubernetes_adapter=FakeKubernetesAdapter(),  # type: ignore[arg-type]
            site_mapping_file=self.mapping_file,
        )
        return discovery.discover()[0]

    def test_live_inventory_and_capability_matching(self) -> None:
        site = self.discover_site()
        inventory = site.accelerator_inventory("nvidia.com/gpu")
        self.assertIsNotNone(inventory)
        assert inventory is not None
        self.assertEqual(inventory.allocatable, 4)
        self.assertEqual(inventory.requested, 1)
        self.assertEqual(inventory.available, 3)
        self.assertEqual(inventory.memory_mib, 40960)

        self.assertTrue(
            site.can_host(
                cpu_m=1000,
                memory_bytes=2 * 1024 ** 3,
                gpu=2,
                accelerator_product="NVIDIA-A100-SXM4-40GB",
                accelerator_memory_mib=40000,
                accelerator_interconnect="nvlink",
                accelerator_partition_mode="full",
            )
        )
        self.assertFalse(
            site.can_host(
                cpu_m=1000,
                memory_bytes=2 * 1024 ** 3,
                gpu=1,
                accelerator_product="NVIDIA-H100-80GB-HBM3",
            )
        )

    def test_planner_rejects_mismatch_before_gnpy(self) -> None:
        site = self.discover_site()
        task = RuntimeTaskRequest(
            task_id="accelerator-mismatch",
            source_access_node="trx Lorient_KMA",
            cpu_request_m=500,
            memory_request_mib=512,
            gpu_request=1,
            bandwidth_gbps=100,
            image="worker:test",
            shell_command="echo test",
            accelerator_product="NVIDIA-H100-80GB-HBM3",
        )
        planner = KubernetesJointPlanner(
            project_root=self.root,
            resource_discovery=object(),  # type: ignore[arg-type]
            gnpy_adapter=object(),  # type: ignore[arg-type]
            request_builder=object(),  # type: ignore[arg-type]
            optical_manager=object(),  # type: ignore[arg-type]
        )
        candidate = planner._evaluate_site(
            task=task,
            site=site,
            topology_file="unused",
            equipment_file="unused",
        )
        self.assertFalse(candidate.feasible)
        self.assertIn(
            "Accelerator constraints do not match",
            candidate.reason,
        )
        self.assertEqual(len(candidate.accelerators), 1)

    def test_job_uses_extended_resource_and_node_affinity(self) -> None:
        manifest = build_kubernetes_job_manifest(
            namespace="compute-optical",
            task_id="ascend-task",
            site_id="site-hangzhou",
            node_name="ascend-worker",
            image="worker:test",
            shell_command="echo test",
            gpu_request=2,
            gpu_resource_name="huawei.com/Ascend910",
            accelerator_product="Ascend-910B",
            accelerator_memory_mib=65536,
            accelerator_interconnect="hccs",
            accelerator_partition_mode="full",
        )
        pod_spec = manifest["spec"]["template"]["spec"]
        resources = pod_spec["containers"][0]["resources"]
        self.assertEqual(
            resources["requests"]["huawei.com/Ascend910"],
            2,
        )
        expressions = (
            pod_spec["affinity"]["nodeAffinity"]
            ["requiredDuringSchedulingIgnoredDuringExecution"]
            ["nodeSelectorTerms"][0]["matchExpressions"]
        )
        expressions_by_key = {
            expression["key"]: expression
            for expression in expressions
        }
        self.assertEqual(
            expressions_by_key[
                "accelerator.compute-optical.io/memory-mib"
            ]["operator"],
            "Gt",
        )
        self.assertEqual(
            expressions_by_key[
                "accelerator.compute-optical.io/interconnect"
            ]["values"],
            ["hccs"],
        )

        nvidia_manifest = build_kubernetes_job_manifest(
            namespace="compute-optical",
            task_id="nvidia-task",
            site_id="site-gpu",
            image="worker:test",
            shell_command="echo test",
            gpu_request=1,
            accelerator_product="NVIDIA-A100-SXM4-40GB",
            accelerator_memory_mib=40000,
            accelerator_partition_mode="full",
        )
        terms = (
            nvidia_manifest["spec"]["template"]["spec"]
            ["affinity"]["nodeAffinity"]
            ["requiredDuringSchedulingIgnoredDuringExecution"]
            ["nodeSelectorTerms"]
        )
        self.assertEqual(len(terms), 2)
        nvidia_keys = {
            expression["key"]
            for expression in terms[1]["matchExpressions"]
        }
        self.assertIn("nvidia.com/gpu.product", nvidia_keys)
        self.assertIn("nvidia.com/gpu.memory", nvidia_keys)
        self.assertIn("nvidia.com/mig.strategy", nvidia_keys)

    def test_profile_allowlist_and_persistence_round_trip(self) -> None:
        profile = accelerator_profile()
        task = profile.build_task(
            task_id="profile-accelerator",
            source_access_node="trx Lorient_KMA",
            gpu_request=1,
            accelerator_product="NVIDIA-A100-SXM4-40GB",
            accelerator_memory_mib=40000,
            accelerator_interconnect="nvlink",
            accelerator_partition_mode="full",
        )
        with self.assertRaises(TaskProfileError):
            profile.build_task(
                task_id="profile-forbidden-product",
                source_access_node="trx Lorient_KMA",
                gpu_request=1,
                accelerator_product="NVIDIA-H100-80GB-HBM3",
            )

        database_file = self.root / "runtime.db"
        database = Database(database_file)
        database.initialize()
        repository = RuntimeTaskRepository(database)
        repository.initialize()
        repository.create_task(task)
        restored = repository.get_task(task.task_id)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.task, task)

    def test_v11_database_migrates_accelerator_columns(self) -> None:
        database_file = self.root / "legacy.db"
        connection = sqlite3.connect(database_file)
        connection.execute(
            """
            CREATE TABLE runtime_tasks (
                task_id TEXT PRIMARY KEY,
                source_access_node TEXT NOT NULL,
                cpu_request_m INTEGER NOT NULL,
                memory_request_mib INTEGER NOT NULL,
                gpu_request INTEGER NOT NULL,
                cpu_limit_m INTEGER,
                memory_limit_mib INTEGER,
                bandwidth_gbps REAL NOT NULL,
                image TEXT NOT NULL,
                shell_command TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0,
                selected_site_id TEXT,
                selected_node_name TEXT,
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO runtime_tasks VALUES (
                'legacy-task', 'trx Lorient_KMA', 500, 256, 0,
                1000, 512, 100, 'busybox:1.36', 'echo legacy',
                'RELEASED', 7, NULL, NULL, NULL,
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z'
            )
            """
        )
        connection.commit()
        connection.close()

        database = Database(database_file)
        repository = RuntimeTaskRepository(database)
        repository.initialize()
        restored = repository.get_task("legacy-task")
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(
            restored.task.accelerator_resource_name,
            "nvidia.com/gpu",
        )
        self.assertIsNone(restored.task.accelerator_product)
        self.assertEqual(restored.version, 7)


if __name__ == "__main__":
    unittest.main()
