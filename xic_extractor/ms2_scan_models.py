"""Pure MS2 scan data shared by acquisition and domain processing."""

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Ms2PrecursorMetadata:
    """Vendor-recorded precursor context, independent of fragment interpretation."""

    monoisotopic_mz: float | None = None
    charge: int | None = None
    isolation_width_da: float | None = None
    isolation_offset_da: float | None = None
    master_scan_number: int | None = None

    def __post_init__(self) -> None:
        for name in ("monoisotopic_mz", "isolation_width_da", "isolation_offset_da"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or (name != "isolation_offset_da" and value <= 0)
            ):
                raise ValueError(f"invalid precursor metadata {name}")
        for name in ("charge", "master_scan_number"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"invalid precursor metadata {name}")


@dataclass(frozen=True)
class Ms2Scan:
    scan_number: int
    rt: float
    precursor_mz: float
    masses: np.ndarray
    intensities: np.ndarray
    base_peak: float
    precursor_metadata: Ms2PrecursorMetadata | None = None


@dataclass(frozen=True)
class Ms2ScanEvent:
    scan: Ms2Scan | None
    parse_error: str | None
    scan_number: int


@dataclass(frozen=True)
class Ms2Window:
    """One sample's events from one requested retention-time window."""

    sample_key: str
    rt_min: float
    rt_max: float
    events: tuple[Ms2ScanEvent, ...]
