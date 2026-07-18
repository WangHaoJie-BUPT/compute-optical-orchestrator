"""Test task identifier validation at API and domain boundaries."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.api.schemas import TaskSubmitRequest  # noqa: E402
from orchestrator.api.secure_schemas import (  # noqa: E402
    ProfileTaskSubmitRequest,
)
from orchestrator.models.resources import TaskRequest  # noqa: E402
from orchestrator.models.runtime_task import (  # noqa: E402
    RuntimeTaskRequest,
)


INVALID_TASK_IDS = (
    "../escape",
    "nested/task",
    "\\windows-path",
    ".hidden",
    "-leading-dash",
    "contains a space",
    "a" * 129,
)


class TaskIdentifierValidationTest(unittest.TestCase):
    def test_valid_identifier_is_accepted(self) -> None:
        identifier = "Task_2026.07-17"

        request = RuntimeTaskRequest(
            task_id=identifier,
            source_access_node="trx Lorient_KMA",
            cpu_request_m=500,
            memory_request_mib=256,
            gpu_request=0,
            bandwidth_gbps=100,
            image="busybox:1.36",
            shell_command="echo safe",
        )

        self.assertEqual(request.task_id, identifier)

    def test_runtime_model_rejects_unsafe_identifiers(self) -> None:
        for identifier in INVALID_TASK_IDS:
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    RuntimeTaskRequest(
                        task_id=identifier,
                        source_access_node="trx Lorient_KMA",
                        cpu_request_m=500,
                        memory_request_mib=256,
                        gpu_request=0,
                        bandwidth_gbps=100,
                        image="busybox:1.36",
                        shell_command="echo safe",
                    )

    def test_legacy_model_rejects_unsafe_identifiers(self) -> None:
        with self.assertRaises(ValueError):
            TaskRequest(
                task_id="../../legacy-escape",
                source_access_node="trx Lorient_KMA",
                cpu_required=1,
                memory_gb_required=1,
                gpu_required=0,
                bandwidth_gbps=100,
            )

    def test_api_schemas_reject_unsafe_identifiers(self) -> None:
        for identifier in INVALID_TASK_IDS:
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValidationError):
                    ProfileTaskSubmitRequest(
                        task_id=identifier,
                        profile_id="busybox-smoke",
                        source_access_node="trx Lorient_KMA",
                    )

                with self.assertRaises(ValidationError):
                    TaskSubmitRequest(
                        task_id=identifier,
                        source_access_node="trx Lorient_KMA",
                        cpu_request_m=500,
                        memory_request_mib=256,
                        bandwidth_gbps=100,
                        shell_command="echo safe",
                    )


if __name__ == "__main__":
    unittest.main()
