"""Immutable values for one CID-NL chromatographic support event."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupport,
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.peak_detection.observations import PeakObservation

RELATIONSHIP = "strict_nl_scans_within_one_physical_ms1_peak"


class CidNlChromatographicSupportContractError(ValueError):
    """Raised when scan support cannot form one physical-peak event."""


@dataclass(frozen=True, slots=True)
class DdaCidNlScanSupportMember:
    """One exact DDA scan relation retained inside the aggregate event."""

    support: DdaCidNlPeakSupport
    configured_neutral_loss_da: float | None = None


@dataclass(frozen=True, slots=True)
class CidNlChromatographicSupport:
    """One physical MS1 support event backed by one or more exact DDA scans."""

    raw_stem: str
    peak_observation: PeakObservation
    candidate_peaks: tuple[PeakObservation, ...]
    acquisition_mode: str
    policy: DdaCidNlPeakSupportPolicy
    scan_supports: tuple[DdaCidNlScanSupportMember, ...]
    strict_nl_scan_count: int
    chromatographic_support_event_count: int
    relationship: str


def _member_sort_key(
    member: DdaCidNlScanSupportMember,
) -> tuple[int, float]:
    return (
        member.support.dda_scan_number,
        member.configured_neutral_loss_da or 0.0,
    )


__all__ = [
    "CidNlChromatographicSupport",
    "CidNlChromatographicSupportContractError",
    "DdaCidNlScanSupportMember",
    "RELATIONSHIP",
]
