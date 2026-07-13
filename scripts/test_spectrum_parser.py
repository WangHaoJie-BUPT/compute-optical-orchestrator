"""Test extraction of N and M from a GNPy response."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.adapters.gnpy_spectrum_parser import (  # noqa: E402
    extract_spectrum_slots,
)
from orchestrator.models.spectrum import SpectrumSlot  # noqa: E402


RESULT_FILE = (
    PROJECT_ROOT
    / "experiments"
    / "results"
    / "gnpy_custom"
    / "explicit-slot-result.json"
)


def main() -> None:
    if not RESULT_FILE.is_file():
        raise FileNotFoundError(
            f"GNPy result does not exist: {RESULT_FILE}"
        )

    with RESULT_FILE.open("r", encoding="utf-8") as file:
        document = json.load(file)

    responses = document.get("response")

    if not isinstance(responses, list) or not responses:
        raise ValueError(
            "GNPy result does not contain a valid response."
        )

    slots = extract_spectrum_slots(responses[0])

    print(f"Unique spectrum slots: {len(slots)}")
    print("-" * 70)

    for slot in slots:
        print(f"N:                       {slot.n}")
        print(f"M:                       {slot.m}")
        print(
            f"Central frequency:       "
            f"{slot.center_frequency_thz:.6f} THz"
        )
        print(f"Slot width:              {slot.width_ghz:.2f} GHz")
        print(
            f"Index interval:          "
            f"[{slot.lower_index}, {slot.upper_index})"
        )
        print(
            f"Frequency interval:      "
            f"[{slot.lower_frequency_thz:.6f}, "
            f"{slot.upper_frequency_thz:.6f}) THz"
        )
        print("-" * 70)

    expected_slot = SpectrumSlot(n=-276, m=4)

    if slots != [expected_slot]:
        raise AssertionError(
            f"Expected {[expected_slot]}, but received {slots}."
        )

    print("Spectrum parsing test completed successfully.")


if __name__ == "__main__":
    main()
