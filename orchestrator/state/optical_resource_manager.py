"""Stateful optical-spectrum reservation and release."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.models.spectrum import SpectrumSlot


class OpticalResourceManagerError(RuntimeError):
    """Raised when optical spectrum cannot be reserved or released."""


@dataclass(frozen=True)
class OpticalAllocation:
    """One active end-to-end optical-spectrum allocation."""

    allocation_id: str
    task_id: str
    links: tuple[str, ...]
    slot: SpectrumSlot


class OpticalResourceManager:
    """
    Maintain active spectrum allocations.

    Two allocations conflict only when:

    1. Their routes share at least one fiber link.
    2. Their frequency-slot intervals overlap.
    """

    def __init__(
        self,
        spectrum_lower_index: int,
        spectrum_upper_index: int,
    ) -> None:
        if spectrum_lower_index >= spectrum_upper_index:
            raise OpticalResourceManagerError(
                "Invalid spectrum index range."
            )

        self.spectrum_lower_index = spectrum_lower_index
        self.spectrum_upper_index = spectrum_upper_index

        self._allocations: dict[str, OpticalAllocation] = {}

    def is_available(
        self,
        links: list[str] | tuple[str, ...],
        slot: SpectrumSlot,
    ) -> bool:
        """Return whether the slot is free on all specified fiber links."""

        normalized_links = set(links)

        if not normalized_links:
            return False

        if slot.lower_index < self.spectrum_lower_index:
            return False

        if slot.upper_index > self.spectrum_upper_index:
            return False

        for allocation in self._allocations.values():
            shared_links = normalized_links.intersection(
                allocation.links
            )

            if shared_links and slot.overlaps(allocation.slot):
                return False

        return True

    def find_first_fit(
        self,
        links: list[str] | tuple[str, ...],
        m: int,
    ) -> SpectrumSlot:
        """
        Find the lowest-frequency available slot.

        Candidate central-frequency index N is scanned from the lower end of
        the configured spectrum band.
        """

        if m <= 0:
            raise OpticalResourceManagerError(
                "Spectrum width index M must be greater than zero."
            )

        first_n = self.spectrum_lower_index + m
        last_n = self.spectrum_upper_index - m

        for n in range(first_n, last_n + 1):
            candidate = SpectrumSlot(n=n, m=m)

            if self.is_available(links, candidate):
                return candidate

        raise OpticalResourceManagerError(
            f"No available spectrum slot for M={m}."
        )

    def reserve(
        self,
        task_id: str,
        links: list[str] | tuple[str, ...],
        slot: SpectrumSlot,
    ) -> OpticalAllocation:
        """Reserve one spectrum slot for a task."""

        if task_id in self._allocations:
            raise OpticalResourceManagerError(
                f"Task already has an optical allocation: {task_id}"
            )

        normalized_links = tuple(dict.fromkeys(links))

        if not normalized_links:
            raise OpticalResourceManagerError(
                "Cannot reserve spectrum without fiber links."
            )

        if not self.is_available(normalized_links, slot):
            conflicting_tasks = self.find_conflicting_tasks(
                normalized_links,
                slot,
            )

            conflict_text = (
                ", ".join(conflicting_tasks)
                if conflicting_tasks
                else "spectrum outside configured range"
            )

            raise OpticalResourceManagerError(
                f"Spectrum slot N={slot.n}, M={slot.m} is unavailable; "
                f"conflict: {conflict_text}."
            )

        allocation = OpticalAllocation(
            allocation_id=f"optical-{task_id}",
            task_id=task_id,
            links=normalized_links,
            slot=slot,
        )

        self._allocations[task_id] = allocation
        return allocation

    def release(self, task_id: str) -> OpticalAllocation:
        """Release the spectrum reserved by one task."""

        allocation = self._allocations.pop(task_id, None)

        if allocation is None:
            raise OpticalResourceManagerError(
                f"No active optical allocation for task: {task_id}"
            )

        return allocation

    def active_allocations(self) -> list[OpticalAllocation]:
        """Return all active optical allocations."""

        return list(self._allocations.values())

    def find_conflicting_tasks(
        self,
        links: list[str] | tuple[str, ...],
        slot: SpectrumSlot,
    ) -> list[str]:
        """Return task IDs whose allocations conflict with the candidate."""

        normalized_links = set(links)
        conflicts: list[str] = []

        for allocation in self._allocations.values():
            shared_links = normalized_links.intersection(
                allocation.links
            )

            if shared_links and slot.overlaps(allocation.slot):
                conflicts.append(allocation.task_id)

        return conflicts
