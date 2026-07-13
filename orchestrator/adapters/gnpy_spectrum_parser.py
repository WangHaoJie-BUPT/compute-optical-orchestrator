"""Parse spectrum labels and fiber links from GNPy responses."""

from __future__ import annotations

from typing import Any

from orchestrator.models.spectrum import SpectrumSlot


class GNPySpectrumParserError(RuntimeError):
    """Raised when a GNPy response cannot be parsed."""


def _get_route_objects(
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return path-route-objects from one GNPy response."""

    path_properties = response.get("path-properties")

    if not isinstance(path_properties, dict):
        return []

    route_objects = path_properties.get("path-route-objects")

    if not isinstance(route_objects, list):
        raise GNPySpectrumParserError(
            "'path-route-objects' must be a list."
        )

    return route_objects


def extract_spectrum_slots(
    response: dict[str, Any],
) -> list[SpectrumSlot]:
    """
    Extract unique spectrum slots from one GNPy response.

    GNPy repeats the same label-hop after multiple route objects. Repeated
    N/M values represent the same end-to-end spectrum assignment, so they
    must be deduplicated.
    """

    unique_slots: set[SpectrumSlot] = set()

    for item in _get_route_objects(response):
        if not isinstance(item, dict):
            continue

        route_object = item.get("path-route-object")

        if not isinstance(route_object, dict):
            continue

        labels = route_object.get("label-hop")

        if not isinstance(labels, list):
            continue

        for label in labels:
            if not isinstance(label, dict):
                continue

            if "N" not in label or "M" not in label:
                continue

            unique_slots.add(
                SpectrumSlot.from_gnpy_dict(label)
            )

    return sorted(unique_slots)


def extract_fiber_links(
    response: dict[str, Any],
) -> list[str]:
    """
    Extract optical fiber elements from one GNPy response.

    For the current GNPy example topology, fiber route objects have node IDs
    beginning with 'fiber '. Amplifiers, ROADMs and transponders are excluded
    because spectrum conflicts are checked on fiber links.
    """

    links: list[str] = []

    for item in _get_route_objects(response):
        if not isinstance(item, dict):
            continue

        route_object = item.get("path-route-object")

        if not isinstance(route_object, dict):
            continue

        hop = route_object.get("num-unnum-hop")

        if not isinstance(hop, dict):
            continue

        node_id = hop.get("node-id")

        if node_id is None:
            continue

        node_name = str(node_id)

        if not node_name.startswith("fiber "):
            continue

        if node_name not in links:
            links.append(node_name)

    return links
