"""Test optical-spectrum reservation, conflict detection and release."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.gnpy_spectrum_parser import (  # noqa: E402
    extract_fiber_links,
    extract_spectrum_slots,
)
from orchestrator.models.spectrum import SpectrumSlot  # noqa: E402
from orchestrator.state.optical_resource_manager import (  # noqa: E402
    OpticalResourceManager,
    OpticalResourceManagerError,
)


RESULT_FILE = (
    PROJECT_ROOT
    / "experiments"
    / "results"
    / "joint_scheduler"
    / "task-001-site-rennes.json"
)


def load_response() -> dict:
    if not RESULT_FILE.is_file():
        raise FileNotFoundError(
            f"GNPy result does not exist: {RESULT_FILE}"
        )

    with RESULT_FILE.open("r", encoding="utf-8") as file:
        document = json.load(file)

    responses = document.get("response")

    if not isinstance(responses, list) or not responses:
        raise ValueError("GNPy output has no valid response.")

    return responses[0]


def print_allocations(
    manager: OpticalResourceManager,
) -> None:
    print()
    print("Active optical allocations")
    print("-" * 90)

    allocations = manager.active_allocations()

    if not allocations:
        print("None")
        return

    for allocation in allocations:
        slot = allocation.slot

        print(
            f"{allocation.task_id:<18}"
            f"N={slot.n:<7}"
            f"M={slot.m:<5}"
            f"interval=[{slot.lower_index}, "
            f"{slot.upper_index})"
        )

        for link in allocation.links:
            print(f"  - {link}")

    print("-" * 90)


def main() -> None:
    response = load_response()

    links = extract_fiber_links(response)
    slots = extract_spectrum_slots(response)

    if not links:
        raise AssertionError(
            "No fiber links were extracted from the GNPy result."
        )

    if len(slots) != 1:
        raise AssertionError(
            f"Expected one spectrum slot, received: {slots}"
        )

    original_slot = slots[0]

    print("Parsed GNPy allocation")
    print("=" * 90)
    print(f"Fiber-link count: {len(links)}")
    print(
        f"Original slot: N={original_slot.n}, "
        f"M={original_slot.m}"
    )

    for link in links:
        print(f"  - {link}")

    # The example equipment range starts at index -288.
    # Index 480 corresponds to the upper boundary of the demo C-band.
    manager = OpticalResourceManager(
        spectrum_lower_index=-288,
        spectrum_upper_index=480,
    )

    print()
    print("1. Reserve task-001 on its original GNPy slot")

    allocation_1 = manager.reserve(
        task_id="task-001",
        links=links,
        slot=original_slot,
    )

    print(
        f"[RESERVED] {allocation_1.task_id}: "
        f"N={allocation_1.slot.n}, M={allocation_1.slot.m}"
    )

    print()
    print("2. Try the same slot on the same route")

    try:
        manager.reserve(
            task_id="task-conflict",
            links=links,
            slot=original_slot,
        )
    except OpticalResourceManagerError as exc:
        print(f"[EXPECTED CONFLICT] {exc}")
    else:
        raise AssertionError(
            "Overlapping spectrum was incorrectly accepted."
        )

    print()
    print("3. Search First-Fit on the same route")

    next_slot = manager.find_first_fit(
        links=links,
        m=original_slot.m,
    )

    print(
        f"First-Fit result: N={next_slot.n}, "
        f"M={next_slot.m}, "
        f"interval=[{next_slot.lower_index}, "
        f"{next_slot.upper_index})"
    )

    if next_slot != SpectrumSlot(n=-276, m=4):
        raise AssertionError(
            "Expected adjacent slot N=-276, M=4, "
            f"received {next_slot}."
        )

    manager.reserve(
        task_id="task-002",
        links=links,
        slot=next_slot,
    )

    print("[RESERVED] task-002 on the adjacent frequency slot")

    print()
    print("4. Reuse the original slot on a disjoint route")

    disjoint_links = [
        "fiber demo-disjoint-A-to-B"
    ]

    manager.reserve(
        task_id="task-003",
        links=disjoint_links,
        slot=original_slot,
    )

    print(
        "[RESERVED] task-003 reused the same slot "
        "because the route is disjoint"
    )

    print_allocations(manager)

    print()
    print("5. Release task-001")

    released = manager.release("task-001")

    print(
        f"[RELEASED] {released.task_id}: "
        f"N={released.slot.n}, M={released.slot.m}"
    )

    first_fit_after_release = manager.find_first_fit(
        links=links,
        m=original_slot.m,
    )

    print(
        f"First-Fit after release: "
        f"N={first_fit_after_release.n}, "
        f"M={first_fit_after_release.m}"
    )

    if first_fit_after_release != original_slot:
        raise AssertionError(
            "Released spectrum was not made available again."
        )

    print_allocations(manager)

    print()
    print(
        "Optical resource-manager test completed successfully."
    )


if __name__ == "__main__":
    main()
