"""Run fault-oriented tests and emit an auditable JSON result."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

LOCAL_SCENARIOS = {
    "job_created_database_failed_compensation": "test_submission_failure_compensation.py",
    "release_finalize_failure_retry": "test_retryable_finalize_failure.py",
    "concurrent_spectrum_collision": "test_persistent_spectrum_claims.py",
    "stale_writer_cas_rejection": "test_runtime_state_versioning.py",
    "admission_retry_claim_and_fairness": "test_admission_queue.py",
    "kubernetes_client_boundary_failures": "test_kubernetes_api_adapter_unit.py",
}

CLUSTER_SCENARIOS = {
    "controller_restart_and_duplicate_lease": "test_runtime_reconciliation_controller.py",
    "official_kubernetes_client_integration": "test_kubernetes_api_adapter_integration.py",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--include-cluster",
        action="store_true",
        default=(
            os.environ.get("FAULT_INJECTION_INCLUDE_CLUSTER")
            == "1"
        ),
        help="Include scenarios that require the reference kind cluster.",
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    scenarios = dict(LOCAL_SCENARIOS)
    if arguments.include_cluster:
        scenarios.update(CLUSTER_SCENARIOS)

    results = []
    failures = 0
    temporary_directory = (
        PROJECT_ROOT / "runtime" / "fault-injection-tmp"
    )
    temporary_directory.mkdir(parents=True, exist_ok=True)
    child_environment = os.environ.copy()
    for variable in ("TEMP", "TMP", "TMPDIR"):
        child_environment[variable] = str(temporary_directory)

    for scenario, filename in scenarios.items():
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / filename)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
            env=child_environment,
        )
        duration = time.perf_counter() - started
        passed = completed.returncode == 0
        failures += 0 if passed else 1
        results.append(
            {
                "scenario": scenario,
                "test_script": filename,
                "passed": passed,
                "duration_seconds": round(duration, 3),
                "return_code": completed.returncode,
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-2000:],
            }
        )
        print(
            f"[{'PASS' if passed else 'FAIL'}] {scenario} "
            f"({duration:.2f}s)"
        )

    report = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "scope": "local-and-cluster" if arguments.include_cluster else "local",
        "scenario_count": len(results),
        "failure_count": failures,
        "results": results,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
