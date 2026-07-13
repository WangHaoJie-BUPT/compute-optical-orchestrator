"""
GNPy CLI adapter.

This module isolates the compute-network orchestrator from the details of the
GNPy command-line interface. The orchestrator provides topology, equipment,
and service-request files, while the adapter returns structured path results.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class OpticalPathResult:
    """Structured result returned for one optical service request."""

    response_id: str
    feasible: bool
    route: list[str]
    metrics: dict[str, Any]
    raw_response: dict[str, Any]


class GNPyAdapterError(RuntimeError):
    """Raised when GNPy execution or result parsing fails."""


class GNPyAdapter:
    """Execute GNPy path requests and convert the JSON output."""

    def __init__(
        self,
        project_root: str | Path,
        executable: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()

        if executable is not None:
            self.executable = str(Path(executable).expanduser())
        else:
            # Prefer the CLI installed beside the current Python interpreter.
            # This normally resolves to .venv/bin/gnpy-path-request.
            venv_cli = Path(sys.executable).with_name("gnpy-path-request")

            if venv_cli.exists():
                self.executable = str(venv_cli)
            else:
                system_cli = shutil.which("gnpy-path-request")

                if system_cli is None:
                    raise GNPyAdapterError(
                        "Cannot find gnpy-path-request. "
                        "Activate the GNPy virtual environment first."
                    )

                self.executable = system_cli

    def compute_paths(
        self,
        topology_file: str | Path,
        service_file: str | Path,
        equipment_file: str | Path,
        output_file: str | Path,
        spectrum_policy: str | None = "first_fit",
        verbose: bool = False,
    ) -> list[OpticalPathResult]:
        """
        Run GNPy and return one OpticalPathResult per response.

        Parameters
        ----------
        topology_file:
            GNPy topology file in JSON, XLS, or XLSX format.
        service_file:
            GNPy service-request file.
        equipment_file:
            GNPy equipment-library JSON file.
        output_file:
            Location at which GNPy should save its JSON response.
        spectrum_policy:
            Spectrum assignment policy. Use None to omit spectrum assignment.
        verbose:
            Add the GNPy -v option when True.
        """

        topology_path = self._resolve_path(topology_file)
        service_path = self._resolve_path(service_file)
        equipment_path = self._resolve_path(equipment_file)
        output_path = self._resolve_path(output_file)

        self._require_file(topology_path, "Topology")
        self._require_file(service_path, "Service request")
        self._require_file(equipment_path, "Equipment configuration")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        command = [self.executable]

        if verbose:
            command.append("-v")

        if spectrum_policy:
            command.extend(["-sa", spectrum_policy])

        command.extend(
            [
                "-e",
                str(equipment_path),
                "-o",
                str(output_path),
                str(topology_path),
                str(service_path),
            ]
        )

        completed = subprocess.run(
            command,
            cwd=self.project_root,
            text=True,
            capture_output=True,
            check=False,
        )

        if completed.returncode != 0:
            raise GNPyAdapterError(
                "GNPy execution failed.\n"
                f"Command: {' '.join(command)}\n"
                f"Return code: {completed.returncode}\n"
                f"STDOUT:\n{completed.stdout}\n"
                f"STDERR:\n{completed.stderr}"
            )

        if not output_path.exists():
            raise GNPyAdapterError(
                f"GNPy finished but did not create output file: {output_path}"
            )

        try:
            with output_path.open("r", encoding="utf-8") as file:
                result_document = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise GNPyAdapterError(
                f"Cannot read GNPy output JSON: {output_path}"
            ) from exc

        return self._parse_results(result_document)

    def _resolve_path(self, path: str | Path) -> Path:
        """Resolve a path relative to the project root."""

        resolved = Path(path).expanduser()

        if not resolved.is_absolute():
            resolved = self.project_root / resolved

        return resolved.resolve()

    @staticmethod
    def _require_file(path: Path, description: str) -> None:
        """Check that an input file exists."""

        if not path.is_file():
            raise GNPyAdapterError(f"{description} file does not exist: {path}")

    def _parse_results(
        self,
        document: dict[str, Any],
    ) -> list[OpticalPathResult]:
        """Convert the GNPy response document into Python objects."""

        responses = document.get("response")

        if not isinstance(responses, list):
            raise GNPyAdapterError(
                "Invalid GNPy output: top-level 'response' must be a list."
            )

        return [self._parse_single_response(item) for item in responses]

    @staticmethod
    def _parse_single_response(
        response: dict[str, Any],
    ) -> OpticalPathResult:
        """Parse one GNPy path response."""

        response_id = str(response.get("response-id", "unknown"))
        path_properties = response.get("path-properties")

        # Successful results normally contain path-properties.
        feasible = isinstance(path_properties, dict)

        if not feasible:
            return OpticalPathResult(
                response_id=response_id,
                feasible=False,
                route=[],
                metrics={},
                raw_response=response,
            )

        metrics: dict[str, Any] = {}

        for metric in path_properties.get("path-metric", []):
            if not isinstance(metric, dict):
                continue

            metric_type = metric.get("metric-type")
            metric_value = metric.get("accumulative-value")

            if metric_type is not None:
                metrics[str(metric_type)] = metric_value

        route: list[str] = []

        for item in path_properties.get("path-route-objects", []):
            if not isinstance(item, dict):
                continue

            route_object = item.get("path-route-object", {})
            if not isinstance(route_object, dict):
                continue

            hop = route_object.get("num-unnum-hop", {})
            if not isinstance(hop, dict):
                continue

            node_id = hop.get("node-id")

            if node_id is not None:
                node_name = str(node_id)

                # Avoid repeated adjacent entries.
                if not route or route[-1] != node_name:
                    route.append(node_name)

        return OpticalPathResult(
            response_id=response_id,
            feasible=True,
            route=route,
            metrics=metrics,
            raw_response=response,
        )
