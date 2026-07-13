"""Generate candidate-specific GNPy service requests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from orchestrator.models.spectrum import SpectrumSlot


class GNPyRequestBuilderError(RuntimeError):
    """Raised when a GNPy request cannot be generated."""


class GNPyRequestBuilder:
    """Create one GNPy service request from a template."""

    def __init__(self, template_file: str | Path) -> None:
        self.template_file = Path(template_file).expanduser().resolve()

        if not self.template_file.is_file():
            raise GNPyRequestBuilderError(
                f"Template file does not exist: {self.template_file}"
            )

        try:
            with self.template_file.open("r", encoding="utf-8") as file:
                self.template_document: dict[str, Any] = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise GNPyRequestBuilderError(
                f"Cannot read template file: {self.template_file}"
            ) from exc

    def build(
        self,
        output_file: str | Path,
        request_id: str,
        source: str,
        destination: str,
        bandwidth_gbps: float,
        spectrum_slot: SpectrumSlot | None = None,
    ) -> Path:
        """
        Create a single-service GNPy request.

        When spectrum_slot is None, GNPy may perform First-Fit assignment.
        When spectrum_slot is provided, the request explicitly carries N/M.
        """

        document = copy.deepcopy(self.template_document)
        requests = document.get("path-request")

        if not isinstance(requests, list) or not requests:
            raise GNPyRequestBuilderError(
                "Template does not contain a valid 'path-request' list."
            )

        request = requests[0]

        if not isinstance(request, dict):
            raise GNPyRequestBuilderError(
                "The first path request is invalid."
            )

        request["request-id"] = str(request_id)
        request["source"] = source
        request["destination"] = destination
        request["src-tp-id"] = source
        request["dst-tp-id"] = destination

        path_constraints = request.setdefault(
            "path-constraints",
            {},
        )
        te_bandwidth = path_constraints.setdefault(
            "te-bandwidth",
            {},
        )

        te_bandwidth["path_bandwidth"] = (
            float(bandwidth_gbps) * 1_000_000_000
        )

        if spectrum_slot is None:
            te_bandwidth.pop("effective-freq-slot", None)
        else:
            te_bandwidth["effective-freq-slot"] = [
                spectrum_slot.to_gnpy_dict()
            ]

        document["path-request"] = [request]
        document.pop("synchronization", None)

        output_path = Path(output_file).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open("w", encoding="utf-8") as file:
            json.dump(document, file, indent=2)

        return output_path
