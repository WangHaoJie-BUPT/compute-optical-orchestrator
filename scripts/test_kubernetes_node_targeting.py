"""Test exact-node constraints in generated Kubernetes Jobs."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.kubernetes_adapter import (  # noqa: E402
    KubernetesAdapter,
)


class CapturingKubernetesAdapter(KubernetesAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.manifest: dict[str, Any] | None = None

    def _run(
        self,
        arguments: list[str],
        input_text: str | None = None,
        capture_output: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if input_text is not None:
            self.manifest = json.loads(input_text)

        return subprocess.CompletedProcess(
            args=arguments,
            returncode=0,
            stdout="created",
            stderr="",
        )


class KubernetesNodeTargetingTest(unittest.TestCase):
    def test_selected_node_is_encoded_in_job_selector(self) -> None:
        adapter = CapturingKubernetesAdapter()

        adapter.create_job(
            task_id="node-targeting-task",
            site_id="site-rennes",
            node_name="compute-optical-worker2",
            image="busybox:1.36",
            shell_command="echo safe",
        )

        self.assertIsNotNone(adapter.manifest)
        selector = (
            adapter.manifest["spec"]["template"]["spec"]
            ["nodeSelector"]
        )

        self.assertEqual(selector["compute-site"], "site-rennes")
        self.assertEqual(
            selector["kubernetes.io/hostname"],
            "compute-optical-worker2",
        )

    def test_node_selector_remains_optional_for_legacy_callers(self) -> None:
        adapter = CapturingKubernetesAdapter()

        adapter.create_job(
            task_id="site-targeting-task",
            site_id="site-rennes",
            image="busybox:1.36",
            shell_command="echo safe",
        )

        selector = (
            adapter.manifest["spec"]["template"]["spec"]
            ["nodeSelector"]
        )

        self.assertEqual(selector, {"compute-site": "site-rennes"})


if __name__ == "__main__":
    unittest.main()
