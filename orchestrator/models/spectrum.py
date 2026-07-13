"""Optical flex-grid spectrum models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, order=True)
class SpectrumSlot:
    """
    One ITU-T flexible-grid frequency slot.

    N:
        Nominal central-frequency index.
    M:
        Slot-width index.
    """

    n: int
    m: int

    def __post_init__(self) -> None:
        if self.m <= 0:
            raise ValueError("Spectrum-slot M must be greater than zero.")

    @property
    def lower_index(self) -> int:
        """Lower slot boundary in 6.25 GHz index units."""

        return self.n - self.m

    @property
    def upper_index(self) -> int:
        """Upper slot boundary in 6.25 GHz index units."""

        return self.n + self.m

    @property
    def center_frequency_thz(self) -> float:
        """Nominal central frequency in THz."""

        return 193.1 + self.n * 0.00625

    @property
    def width_ghz(self) -> float:
        """Slot width in GHz."""

        return self.m * 12.5

    @property
    def lower_frequency_thz(self) -> float:
        """Lower frequency boundary in THz."""

        return 193.1 + self.lower_index * 0.00625

    @property
    def upper_frequency_thz(self) -> float:
        """Upper frequency boundary in THz."""

        return 193.1 + self.upper_index * 0.00625

    def overlaps(self, other: "SpectrumSlot") -> bool:
        """Return whether two frequency intervals overlap."""

        return (
            self.lower_index < other.upper_index
            and other.lower_index < self.upper_index
        )

    @classmethod
    def from_gnpy_dict(cls, data: dict[str, Any]) -> "SpectrumSlot":
        """Create a slot from GNPy output fields N and M."""

        return cls(
            n=int(data["N"]),
            m=int(data["M"]),
        )

    def to_gnpy_dict(self) -> dict[str, int]:
        """Convert to the GNPy request format."""

        return {
            "N": self.n,
            "M": self.m,
        }
