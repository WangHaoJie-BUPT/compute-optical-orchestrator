"""Run the secure API and a separate Prometheus metrics listener."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from prometheus_client import make_asgi_app  # noqa: E402

from orchestrator.api.application import (  # noqa: E402
    RuntimeAPISettings,
)
from orchestrator.api.secure_application import (  # noqa: E402
    create_secure_app,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the compute-optical orchestration API "
            "and its metrics listener."
        )
    )

    parser.add_argument(
        "--database",
        default=str(
            PROJECT_ROOT
            / "runtime"
            / "runtime-orchestrator.db"
        ),
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Business API bind address.",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Business API port.",
    )

    parser.add_argument(
        "--metrics-host",
        default="127.0.0.1",
        help="Prometheus metrics bind address.",
    )

    parser.add_argument(
        "--metrics-port",
        type=int,
        default=9100,
        help="Prometheus metrics port.",
    )

    parser.add_argument(
        "--controller-interval",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--admission-aging-seconds",
        type=float,
        default=60.0,
    )
    parser.add_argument(
        "--admission-max-active-total",
        type=int,
        default=1000,
    )
    parser.add_argument(
        "--admission-dispatch-timeout-seconds",
        type=float,
        default=300.0,
    )
    parser.add_argument(
        "--admission-lease-seconds",
        type=float,
        default=300.0,
    )
    parser.add_argument(
        "--admission-max-active-per-tenant",
        type=int,
        default=100,
    )
    parser.add_argument(
        "--admission-max-dispatch-per-cycle",
        type=int,
        default=1,
    )

    return parser.parse_args()


def create_metrics_export_app(
    registry: Any,
) -> FastAPI:
    """
    Create a metrics-only ASGI application.

    It contains no task-submission, cancellation or query routes.
    """

    metrics_application = FastAPI(
        title="Compute-Optical Metrics",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    metrics_application.mount(
        "/metrics",
        make_asgi_app(registry=registry),
    )

    @metrics_application.get("/healthz")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
        }

    return metrics_application


def wait_for_server_start(
    server: uvicorn.Server,
    thread: threading.Thread,
    timeout_seconds: float = 10.0,
) -> None:
    """Fail clearly when the secondary metrics listener cannot start."""

    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        if server.started:
            return

        if not thread.is_alive():
            raise RuntimeError(
                "Metrics server stopped before startup completed."
            )

        time.sleep(0.1)

    raise RuntimeError(
        "Timed out waiting for the metrics server to start."
    )


def main() -> None:
    args = parse_args()

    api_key = os.environ.get(
        "COMPUTE_OPTICAL_API_KEY",
        "",
    )

    if len(api_key) < 16:
        raise SystemExit(
            "COMPUTE_OPTICAL_API_KEY must be set "
            "and contain at least 16 characters."
        )

    settings = RuntimeAPISettings(
        database_file=Path(args.database),
        controller_interval_seconds=(
            args.controller_interval
        ),
        admission_aging_seconds=(
            args.admission_aging_seconds
        ),
        admission_max_active_total=(
            args.admission_max_active_total
        ),
        admission_dispatch_timeout_seconds=(
            args.admission_dispatch_timeout_seconds
        ),
        admission_lease_seconds=(
            args.admission_lease_seconds
        ),
        admission_max_active_per_tenant=(
            args.admission_max_active_per_tenant
        ),
        admission_max_dispatch_per_cycle=(
            args.admission_max_dispatch_per_cycle
        ),
    )

    application = create_secure_app(
        settings=settings,
        api_key=api_key,
    )

    registry = getattr(
        application.state,
        "prometheus_registry",
        None,
    )

    if registry is None:
        raise SystemExit(
            "Prometheus registry is not installed "
            "on the secure application."
        )

    metrics_application = create_metrics_export_app(
        registry
    )

    metrics_config = uvicorn.Config(
        app=metrics_application,
        host=args.metrics_host,
        port=args.metrics_port,
        workers=1,
        log_level="warning",
        access_log=False,
    )

    metrics_server = uvicorn.Server(
        metrics_config
    )

    metrics_thread = threading.Thread(
        target=metrics_server.run,
        name="prometheus-metrics-listener",
        daemon=True,
    )

    metrics_thread.start()

    try:
        wait_for_server_start(
            metrics_server,
            metrics_thread,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    print(
        "Prometheus metrics listener: "
        f"http://{args.metrics_host}:"
        f"{args.metrics_port}/metrics/",
        flush=True,
    )

    api_config = uvicorn.Config(
        app=application,
        host=args.host,
        port=args.port,
        workers=1,
        log_level="info",
        access_log=True,
    )

    api_server = uvicorn.Server(
        api_config
    )

    try:
        api_server.run()
    finally:
        metrics_server.should_exit = True
        metrics_thread.join(timeout=15)


if __name__ == "__main__":
    main()
