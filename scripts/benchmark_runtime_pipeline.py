"""Measure live API, admission, and runtime milestone latency."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Sample:
    task_id: str
    mode: str
    submit_http_ms: float
    admission_wait_ms: float | None
    terminal_wait_ms: float | None
    final_admission_state: str | None
    final_runtime_state: str | None


def request_json(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], float]:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"{method} {path} returned HTTP {exc.code}: {detail}"
        ) from exc
    return result, (time.perf_counter() - started) * 1000


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def wait_for(
    reader: Any,
    terminal_states: set[str],
    timeout_seconds: float,
    poll_seconds: float,
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    deadline = started + timeout_seconds
    latest: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        try:
            latest = reader()
        except (ConnectionError, OSError, urllib.error.URLError) as exc:
            latest = {"transient_poll_error": str(exc)}
            time.sleep(poll_seconds)
            continue
        if str(latest.get("state")) in terminal_states:
            return latest, (time.perf_counter() - started) * 1000
        time.sleep(poll_seconds)
    raise TimeoutError(
        f"Timed out after {timeout_seconds}s; last record={latest}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("COMPUTE_OPTICAL_API_KEY"),
    )
    parser.add_argument("--mode", choices=("v2", "v3"), default="v3")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--duration-seconds", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--tenant-id", default="benchmark-tenant")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if not arguments.api_key:
        raise SystemExit("Set COMPUTE_OPTICAL_API_KEY or pass --api-key.")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    samples: list[Sample] = []
    for index in range(arguments.samples):
        task_id = f"latency-{arguments.mode}-{run_id}-{index:03d}"
        payload = {
            "task_id": task_id,
            "profile_id": "busybox-smoke",
            "source_access_node": "trx Lorient_KMA",
            "duration_seconds": arguments.duration_seconds,
        }
        if arguments.mode == "v3":
            payload.update(
                tenant_id=arguments.tenant_id,
                priority=50,
            )
            _, submit_ms = request_json(
                arguments.base_url,
                arguments.api_key,
                "POST",
                "/api/v3/tasks",
                payload,
            )
            admission, admission_ms = wait_for(
                lambda: request_json(
                    arguments.base_url,
                    arguments.api_key,
                    "GET",
                    f"/api/v3/queue/{task_id}",
                )[0],
                {"ADMITTED", "REJECTED", "CANCELLED"},
                arguments.timeout_seconds,
                arguments.poll_seconds,
            )
            final_admission = str(admission["state"])
        else:
            response, submit_ms = request_json(
                arguments.base_url,
                arguments.api_key,
                "POST",
                "/api/v2/tasks",
                payload,
            )
            admission_ms = None
            final_admission = None
            if response.get("state") not in {"RUNNING", "COMPLETED", "RELEASED"}:
                raise RuntimeError(f"Unexpected v2 response: {response}")

        runtime, terminal_ms = wait_for(
            lambda: request_json(
                arguments.base_url,
                arguments.api_key,
                "GET",
                f"/api/v1/tasks/{task_id}",
            )[0],
            {"RELEASED", "FAILED", "CANCELLED"},
            arguments.timeout_seconds,
            arguments.poll_seconds,
        )
        samples.append(
            Sample(
                task_id=task_id,
                mode=arguments.mode,
                submit_http_ms=round(submit_ms, 3),
                admission_wait_ms=(
                    round(admission_ms, 3)
                    if admission_ms is not None
                    else None
                ),
                terminal_wait_ms=round(terminal_ms, 3),
                final_admission_state=final_admission,
                final_runtime_state=str(runtime["state"]),
            )
        )

    submit_values = [sample.submit_http_ms for sample in samples]
    terminal_values = [sample.terminal_wait_ms or 0 for sample in samples]
    result = {
        "metadata": {
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "base_url": arguments.base_url,
            "mode": arguments.mode,
            "sample_count": len(samples),
            "duration_seconds": arguments.duration_seconds,
            "poll_seconds": arguments.poll_seconds,
            "definition": (
                "Client-observed wall-clock latency. Terminal wait includes "
                "workload duration and controller polling."
            ),
        },
        "summary_ms": {
            "submit_mean": round(statistics.mean(submit_values), 3),
            "submit_p50": round(percentile(submit_values, 0.50), 3),
            "submit_p95": round(percentile(submit_values, 0.95), 3),
            "terminal_mean": round(statistics.mean(terminal_values), 3),
            "terminal_p50": round(percentile(terminal_values, 0.50), 3),
            "terminal_p95": round(percentile(terminal_values, 0.95), 3),
        },
        "samples": [asdict(sample) for sample in samples],
    }
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
