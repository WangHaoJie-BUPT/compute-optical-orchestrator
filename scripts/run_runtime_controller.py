"""Run the persistent runtime reconciliation controller."""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.gnpy_adapter import GNPyAdapter  # noqa: E402
from orchestrator.adapters.gnpy_request_builder import (  # noqa: E402
    GNPyRequestBuilder,
)
from orchestrator.adapters.kubernetes_api_adapter import (  # noqa: E402
    KubernetesApiAdapter,
)
from orchestrator.adapters.kubernetes_resource_discovery import (  # noqa: E402
    KubernetesResourceDiscovery,
)
from orchestrator.controllers.runtime_reconciliation_controller import (  # noqa: E402
    RuntimeReconciliationController,
)
from orchestrator.repositories.controller_lease_repository import (  # noqa: E402
    ControllerLeaseRepository,
)
from orchestrator.repositories.runtime_task_repository import (  # noqa: E402
    RuntimeTaskRepository,
)
from orchestrator.repositories.task_event_repository import (  # noqa: E402
    TaskEventRepository,
)
from orchestrator.services.kubernetes_joint_planner import (  # noqa: E402
    KubernetesJointPlanner,
)
from orchestrator.services.persistent_kubernetes_runtime_service import (  # noqa: E402
    PersistentKubernetesRuntimeService,
)
from orchestrator.state.optical_resource_manager import (  # noqa: E402
    OpticalResourceManager,
)
from orchestrator.storage.database import Database  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--database",
        default=str(
            PROJECT_ROOT
            / "runtime"
            / "runtime-orchestrator.db"
        ),
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    database = Database(
        Path(args.database).expanduser().resolve()
    )
    database.initialize()

    repository = RuntimeTaskRepository(database)
    repository.initialize()

    event_repository = TaskEventRepository(database)
    event_repository.initialize()

    lease_repository = ControllerLeaseRepository(
        database
    )
    lease_repository.initialize()

    adapter = KubernetesApiAdapter(
        namespace="compute-optical",
        context="kind-compute-optical",
    )
    adapter.verify_cluster()
    adapter.ensure_namespace()

    discovery = KubernetesResourceDiscovery(
        kubernetes_adapter=adapter,
        site_mapping_file=(
            PROJECT_ROOT
            / "configs"
            / "site_mapping.json"
        ),
    )

    optical_manager = OpticalResourceManager(
        spectrum_lower_index=-288,
        spectrum_upper_index=480,
    )

    planner = KubernetesJointPlanner(
        project_root=PROJECT_ROOT,
        resource_discovery=discovery,
        gnpy_adapter=GNPyAdapter(
            project_root=PROJECT_ROOT
        ),
        request_builder=GNPyRequestBuilder(
            PROJECT_ROOT
            / "requests"
            / "gnpy_demo"
            / "services-single.json"
        ),
        optical_manager=optical_manager,
    )

    runtime_service = (
        PersistentKubernetesRuntimeService(
            repository=repository,
            event_repository=event_repository,
            planner=planner,
            kubernetes_adapter=adapter,
        )
    )

    restored = runtime_service.recover()

    print(
        f"Recovered optical allocations: {restored}",
        flush=True,
    )

    controller = RuntimeReconciliationController(
        repository=repository,
        runtime_service=runtime_service,
        kubernetes_adapter=adapter,
        lease_repository=lease_repository,
    )

    stopping = False

    def stop_handler(
        signum: int,
        frame: object,
    ) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    print(
        f"Controller started: {controller.controller_id}",
        flush=True,
    )

    while not stopping:
        result = controller.run_once()

        print(
            f"lease={result.lease_acquired} "
            f"scanned={result.scanned_count} "
            f"changed={result.changed_count} "
            f"errors={result.error_count}",
            flush=True,
        )

        for event in result.events:
            if (
                event.action != "NO_CHANGE"
                or event.error is not None
            ):
                print(
                    f"task={event.task_id} "
                    f"job={event.job_state} "
                    f"action={event.action} "
                    f"state={event.final_state} "
                    f"error={event.error}",
                    flush=True,
                )

        time.sleep(max(args.interval, 0.5))

    print("Controller stopped.", flush=True)


if __name__ == "__main__":
    main()
