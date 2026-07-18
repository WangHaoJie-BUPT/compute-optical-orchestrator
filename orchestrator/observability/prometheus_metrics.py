"""Prometheus metrics for the compute-optical orchestrator."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    make_asgi_app,
)
from prometheus_client.core import (
    GaugeMetricFamily,
    SummaryMetricFamily,
)

from orchestrator.models.task_state import TaskState
from orchestrator.models.admission import AdmissionState


ContextGetter = Callable[[], Any | None]


def _timestamp_seconds(
    value: str | None,
) -> float:
    """Convert ISO-8601 timestamp to Unix seconds."""

    if not value:
        return 0.0

    text = str(value)

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


class RuntimePrometheusCollector:
    """
    Collect metrics directly from SQLite, Kubernetes and controller state.

    The collector does not retain task-level metric labels, avoiding
    unbounded task-ID cardinality.
    """

    TERMINAL_STATES = (
        TaskState.RELEASED.value,
        TaskState.FAILED.value,
        TaskState.CANCELLED.value,
    )

    def __init__(
        self,
        context_getter: ContextGetter,
    ) -> None:
        self.context_getter = context_getter

    def describe(self) -> Iterable[Any]:
        """
        Avoid collecting during registry registration.

        The runtime context is initialized during FastAPI lifespan,
        after the metrics registry itself is created.
        """

        return ()

    def collect(self) -> Iterable[Any]:
        """Generate a fresh snapshot for every Prometheus scrape."""

        collector_up = GaugeMetricFamily(
            "compute_optical_metrics_collector_up",
            "Whether the orchestration metrics collector succeeded.",
        )

        context = self.context_getter()

        if context is None:
            collector_up.add_metric([], 0)
            yield collector_up
            return

        try:
            metrics = list(
                self._collect_runtime_metrics(context)
            )
        except Exception:
            collector_up.add_metric([], 0)
            yield collector_up
            return

        collector_up.add_metric([], 1)
        yield collector_up

        yield from metrics

    def _collect_runtime_metrics(
        self,
        context: Any,
    ) -> Iterable[Any]:
        yield from self._collect_database_metrics(
            context
        )

        yield from self._collect_controller_metrics(
            context
        )

        yield from self._collect_kubernetes_metrics(
            context
        )

    def _collect_database_metrics(
        self,
        context: Any,
    ) -> Iterable[Any]:
        with context.database.connection() as connection:
            state_rows = connection.execute(
                """
                SELECT state, COUNT(*) AS task_count
                FROM runtime_tasks
                GROUP BY state
                """
            ).fetchall()

            execution_rows = connection.execute(
                """
                SELECT phase, COUNT(*) AS execution_count
                FROM runtime_kubernetes_executions
                GROUP BY phase
                """
            ).fetchall()

            optical_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS allocation_count,
                    COALESCE(SUM(2 * m), 0) AS occupied_slot_units
                FROM runtime_optical_allocations
                """
            ).fetchone()

            event_rows = connection.execute(
                """
                SELECT event_type, COUNT(*) AS event_count
                FROM runtime_task_events
                GROUP BY event_type
                """
            ).fetchall()

            terminal_rows = connection.execute(
                """
                SELECT
                    state,
                    created_at,
                    updated_at
                FROM runtime_tasks
                WHERE state IN (?, ?, ?)
                """,
                self.TERMINAL_STATES,
            ).fetchall()

            admission_rows = connection.execute(
                """
                SELECT state, COUNT(*) AS task_count
                FROM runtime_admission_queue
                GROUP BY state
                """
            ).fetchall()

            admission_wait_row = connection.execute(
                """
                SELECT MIN(enqueued_at) AS oldest_enqueued_at,
                       COUNT(DISTINCT tenant_id) AS active_tenants
                FROM runtime_admission_queue
                WHERE state IN (?, ?)
                """,
                (
                    AdmissionState.QUEUED.value,
                    AdmissionState.DISPATCHING.value,
                ),
            ).fetchone()

        state_counts = {
            state.value: 0
            for state in TaskState
        }

        for row in state_rows:
            state_counts[str(row["state"])] = int(
                row["task_count"]
            )

        task_state_metric = GaugeMetricFamily(
            "compute_optical_tasks",
            "Number of persisted tasks in each lifecycle state.",
            labels=["state"],
        )

        for state_name in sorted(state_counts):
            task_state_metric.add_metric(
                [state_name],
                state_counts[state_name],
            )

        yield task_state_metric

        task_total_metric = GaugeMetricFamily(
            "compute_optical_tasks_total_snapshot",
            "Total number of persisted runtime tasks.",
        )

        task_total_metric.add_metric(
            [],
            sum(state_counts.values()),
        )

        yield task_total_metric

        admission_counts = {
            state.value: 0 for state in AdmissionState
        }
        for row in admission_rows:
            admission_counts[str(row["state"])] = int(
                row["task_count"]
            )

        admission_metric = GaugeMetricFamily(
            "compute_optical_admission_tasks",
            "Number of admission queue records by state.",
            labels=["state"],
        )
        for state_name in sorted(admission_counts):
            admission_metric.add_metric(
                [state_name],
                admission_counts[state_name],
            )
        yield admission_metric

        oldest_enqueued_at = (
            float(admission_wait_row["oldest_enqueued_at"])
            if (
                admission_wait_row is not None
                and admission_wait_row["oldest_enqueued_at"]
                is not None
            )
            else None
        )
        wait_metric = GaugeMetricFamily(
            "compute_optical_admission_oldest_wait_seconds",
            "Current wait time of the oldest active queued task.",
        )
        wait_metric.add_metric(
            [],
            max(time.time() - oldest_enqueued_at, 0.0)
            if oldest_enqueued_at is not None
            else 0.0,
        )
        yield wait_metric

        tenant_metric = GaugeMetricFamily(
            "compute_optical_admission_active_tenants",
            "Number of tenants with queued or dispatching tasks.",
        )
        tenant_metric.add_metric(
            [],
            int(admission_wait_row["active_tenants"] or 0)
            if admission_wait_row is not None
            else 0,
        )
        yield tenant_metric

        execution_metric = GaugeMetricFamily(
            "compute_optical_kubernetes_executions",
            "Number of Kubernetes executions by persisted phase.",
            labels=["phase"],
        )

        for row in execution_rows:
            execution_metric.add_metric(
                [str(row["phase"])],
                int(row["execution_count"]),
            )

        yield execution_metric

        optical_allocation_count = (
            int(optical_row["allocation_count"])
            if optical_row is not None
            else 0
        )

        occupied_slot_units = (
            int(optical_row["occupied_slot_units"])
            if optical_row is not None
            else 0
        )

        optical_metric = GaugeMetricFamily(
            "compute_optical_active_optical_allocations",
            "Number of currently persisted optical allocations.",
        )

        optical_metric.add_metric(
            [],
            optical_allocation_count,
        )

        yield optical_metric

        slot_metric = GaugeMetricFamily(
            "compute_optical_occupied_spectrum_slot_units",
            "Occupied flexible-grid slot units across active allocations.",
        )

        slot_metric.add_metric(
            [],
            occupied_slot_units,
        )

        yield slot_metric

        spectrum_ghz_metric = GaugeMetricFamily(
            "compute_optical_occupied_spectrum_ghz",
            "Total occupied optical spectrum width in GHz.",
        )

        # In the current GNPy model, one slot index step is 6.25 GHz.
        spectrum_ghz_metric.add_metric(
            [],
            occupied_slot_units * 6.25,
        )

        yield spectrum_ghz_metric

        event_metric = GaugeMetricFamily(
            "compute_optical_audit_events",
            "Number of persisted audit events by event type.",
            labels=["event_type"],
        )

        for row in event_rows:
            event_metric.add_metric(
                [str(row["event_type"])],
                int(row["event_count"]),
            )

        yield event_metric

        duration_groups: dict[str, list[float]] = {
            state: []
            for state in self.TERMINAL_STATES
        }

        for row in terminal_rows:
            state_name = str(row["state"])

            created_seconds = _timestamp_seconds(
                str(row["created_at"])
            )

            updated_seconds = _timestamp_seconds(
                str(row["updated_at"])
            )

            duration = max(
                updated_seconds - created_seconds,
                0.0,
            )

            duration_groups.setdefault(
                state_name,
                [],
            ).append(duration)

        duration_metric = SummaryMetricFamily(
            "compute_optical_task_duration_seconds",
            "Persisted task lifecycle duration by terminal outcome.",
            labels=["outcome"],
        )

        for outcome in sorted(duration_groups):
            values = duration_groups[outcome]

            duration_metric.add_metric(
                [outcome],
                count_value=len(values),
                sum_value=sum(values),
            )

        yield duration_metric

    def _collect_controller_metrics(
        self,
        context: Any,
    ) -> Iterable[Any]:
        thread = context.controller_thread

        controller_enabled = bool(
            context.settings.start_controller
        )

        controller_alive = bool(
            controller_enabled
            and thread is not None
            and thread.is_alive()
        )

        enabled_metric = GaugeMetricFamily(
            "compute_optical_controller_enabled",
            "Whether background reconciliation is configured.",
        )
        enabled_metric.add_metric(
            [],
            int(controller_enabled),
        )
        yield enabled_metric

        alive_metric = GaugeMetricFamily(
            "compute_optical_controller_up",
            "Whether the reconciliation controller thread is alive.",
        )
        alive_metric.add_metric(
            [],
            int(controller_alive),
        )
        yield alive_metric

        with context.status_lock:
            last_at = context.last_reconciliation_at
            last_error = context.last_controller_error
            last_result = context.last_reconciliation
            restored_allocations = (
                context.restored_allocations
            )

        error_metric = GaugeMetricFamily(
            "compute_optical_controller_last_cycle_error",
            "Whether the latest controller cycle raised an outer error.",
        )
        error_metric.add_metric(
            [],
            int(last_error is not None),
        )
        yield error_metric

        timestamp_metric = GaugeMetricFamily(
            "compute_optical_controller_last_reconciliation_timestamp_seconds",
            "Unix timestamp of the latest reconciliation cycle.",
        )
        timestamp_metric.add_metric(
            [],
            _timestamp_seconds(last_at),
        )
        yield timestamp_metric

        restored_metric = GaugeMetricFamily(
            "compute_optical_recovered_optical_allocations",
            "Optical allocations restored during API startup.",
        )
        restored_metric.add_metric(
            [],
            restored_allocations,
        )
        yield restored_metric

        scanned_metric = GaugeMetricFamily(
            "compute_optical_controller_last_scanned_tasks",
            "Tasks scanned in the latest reconciliation cycle.",
        )

        changed_metric = GaugeMetricFamily(
            "compute_optical_controller_last_changed_tasks",
            "Tasks changed in the latest reconciliation cycle.",
        )

        reconciliation_error_metric = GaugeMetricFamily(
            "compute_optical_controller_last_task_errors",
            "Per-task errors in the latest reconciliation cycle.",
        )

        scanned_metric.add_metric(
            [],
            (
                last_result.scanned_count
                if last_result is not None
                else 0
            ),
        )

        changed_metric.add_metric(
            [],
            (
                last_result.changed_count
                if last_result is not None
                else 0
            ),
        )

        reconciliation_error_metric.add_metric(
            [],
            (
                last_result.error_count
                if last_result is not None
                else 0
            ),
        )

        yield scanned_metric
        yield changed_metric
        yield reconciliation_error_metric

    def _collect_kubernetes_metrics(
        self,
        context: Any,
    ) -> Iterable[Any]:
        discovery_up = GaugeMetricFamily(
            "compute_optical_kubernetes_resource_discovery_up",
            "Whether Kubernetes site-resource discovery succeeded.",
        )

        try:
            sites = context.resource_discovery.discover()
        except Exception:
            discovery_up.add_metric([], 0)
            yield discovery_up
            return

        discovery_up.add_metric([], 1)
        yield discovery_up

        labels = [
            "site_id",
            "node_name",
        ]

        ready_metric = GaugeMetricFamily(
            "compute_optical_site_ready",
            "Whether the Kubernetes compute-site node is Ready.",
            labels=labels,
        )

        schedulable_metric = GaugeMetricFamily(
            "compute_optical_site_schedulable",
            "Whether the Kubernetes compute-site node is schedulable.",
            labels=labels,
        )

        active_pods_metric = GaugeMetricFamily(
            "compute_optical_site_active_pods",
            "Number of active Pods assigned to the compute-site node.",
            labels=labels,
        )

        cpu_allocatable_metric = GaugeMetricFamily(
            "compute_optical_site_cpu_allocatable_millicores",
            "Allocatable Kubernetes CPU in millicores.",
            labels=labels,
        )

        cpu_requested_metric = GaugeMetricFamily(
            "compute_optical_site_cpu_requested_millicores",
            "CPU requests of active Pods in millicores.",
            labels=labels,
        )

        cpu_available_metric = GaugeMetricFamily(
            "compute_optical_site_cpu_available_millicores",
            "Estimated schedulable CPU in millicores.",
            labels=labels,
        )

        memory_allocatable_metric = GaugeMetricFamily(
            "compute_optical_site_memory_allocatable_bytes",
            "Allocatable Kubernetes memory in bytes.",
            labels=labels,
        )

        memory_requested_metric = GaugeMetricFamily(
            "compute_optical_site_memory_requested_bytes",
            "Memory requests of active Pods in bytes.",
            labels=labels,
        )

        memory_available_metric = GaugeMetricFamily(
            "compute_optical_site_memory_available_bytes",
            "Estimated schedulable memory in bytes.",
            labels=labels,
        )

        gpu_allocatable_metric = GaugeMetricFamily(
            "compute_optical_site_gpu_allocatable",
            "Allocatable GPU resources.",
            labels=labels,
        )

        gpu_requested_metric = GaugeMetricFamily(
            "compute_optical_site_gpu_requested",
            "GPU resources requested by active Pods.",
            labels=labels,
        )

        gpu_available_metric = GaugeMetricFamily(
            "compute_optical_site_gpu_available",
            "Estimated schedulable GPU resources.",
            labels=labels,
        )

        for site in sites:
            label_values = [
                site.site_id,
                site.node_name,
            ]

            ready_metric.add_metric(
                label_values,
                int(site.ready),
            )

            schedulable_metric.add_metric(
                label_values,
                int(site.schedulable),
            )

            active_pods_metric.add_metric(
                label_values,
                site.active_pod_count,
            )

            cpu_allocatable_metric.add_metric(
                label_values,
                site.allocatable_cpu_m,
            )

            cpu_requested_metric.add_metric(
                label_values,
                site.requested_cpu_m,
            )

            cpu_available_metric.add_metric(
                label_values,
                site.available_cpu_m,
            )

            memory_allocatable_metric.add_metric(
                label_values,
                site.allocatable_memory_bytes,
            )

            memory_requested_metric.add_metric(
                label_values,
                site.requested_memory_bytes,
            )

            memory_available_metric.add_metric(
                label_values,
                site.available_memory_bytes,
            )

            gpu_allocatable_metric.add_metric(
                label_values,
                site.allocatable_gpu,
            )

            gpu_requested_metric.add_metric(
                label_values,
                site.requested_gpu,
            )

            gpu_available_metric.add_metric(
                label_values,
                site.available_gpu,
            )

        yield ready_metric
        yield schedulable_metric
        yield active_pods_metric

        yield cpu_allocatable_metric
        yield cpu_requested_metric
        yield cpu_available_metric

        yield memory_allocatable_metric
        yield memory_requested_metric
        yield memory_available_metric

        yield gpu_allocatable_metric
        yield gpu_requested_metric
        yield gpu_available_metric


class HTTPPrometheusMetrics:
    """In-process HTTP request metrics for one FastAPI application."""

    def __init__(
        self,
        registry: CollectorRegistry,
    ) -> None:
        self.request_counter = Counter(
            "compute_optical_http_requests",
            "HTTP requests received by the orchestration API.",
            labelnames=[
                "method",
                "route",
                "status_code",
            ],
            registry=registry,
        )

        self.request_duration = Histogram(
            "compute_optical_http_request_duration_seconds",
            "HTTP request duration in seconds.",
            labelnames=[
                "method",
                "route",
            ],
            buckets=(
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1,
                2.5,
                5,
                10,
                30,
            ),
            registry=registry,
        )

    async def observe(
        self,
        request: Request,
        call_next,
    ):
        """Measure one HTTP request without task-ID labels."""

        if request.url.path.startswith("/metrics"):
            return await call_next(request)

        started_at = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response

        finally:
            route_object = request.scope.get("route")
            route_template = getattr(
                route_object,
                "path",
                None,
            )

            if not route_template:
                route_template = "<unmatched>"

            elapsed = max(
                time.perf_counter() - started_at,
                0.0,
            )

            self.request_counter.labels(
                method=request.method,
                route=route_template,
                status_code=str(status_code),
            ).inc()

            self.request_duration.labels(
                method=request.method,
                route=route_template,
            ).observe(elapsed)


def install_prometheus_metrics(
    app: FastAPI,
) -> CollectorRegistry:
    """
    Install the custom collector, HTTP middleware and /metrics endpoint.

    A dedicated registry avoids duplicate metric registrations when
    integration tests create multiple application instances.
    """

    registry = CollectorRegistry(
        auto_describe=False,
    )

    collector = RuntimePrometheusCollector(
        context_getter=lambda: getattr(
            app.state,
            "runtime_context",
            None,
        )
    )

    registry.register(collector)

    http_metrics = HTTPPrometheusMetrics(
        registry
    )

    @app.middleware("http")
    async def prometheus_http_middleware(
        request: Request,
        call_next,
    ):
        return await http_metrics.observe(
            request,
            call_next,
        )

    metrics_app = make_asgi_app(
        registry=registry,
    )

    app.mount(
        "/metrics",
        metrics_app,
    )

    app.state.prometheus_registry = registry
    app.state.prometheus_collector = collector
    app.state.prometheus_http_metrics = http_metrics

    return registry
