"""Immutable values for one CID-NL chromatographic support event."""

from __future__ import annotations

import math
from dataclasses import dataclass

from xic_extractor.peak_detection.cid_nl_evidence import (
    ACQUISITION_MODE,
)
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

    def __post_init__(self) -> None:
        if self.configured_neutral_loss_da is not None and (
            isinstance(self.configured_neutral_loss_da, bool)
            or not isinstance(self.configured_neutral_loss_da, (float, int))
            or not math.isfinite(self.configured_neutral_loss_da)
            or self.configured_neutral_loss_da <= 0
        ):
            raise CidNlChromatographicSupportContractError(
                "configured loss must be positive and finite"
            )


@dataclass(frozen=True, slots=True)
class CidNlChromatographicSupport:
    """One physical MS1 support event backed by one or more exact DDA scans."""

    sample_key: str
    peak_observation: PeakObservation
    candidate_peaks: tuple[PeakObservation, ...]
    acquisition_mode: str
    policy: DdaCidNlPeakSupportPolicy
    scan_supports: tuple[DdaCidNlScanSupportMember, ...]
    strict_nl_scan_count: int
    chromatographic_support_event_count: int
    relationship: str

    def __post_init__(self) -> None:
        if self.acquisition_mode != ACQUISITION_MODE:
            raise CidNlChromatographicSupportContractError(
                f"acquisition_mode must be {ACQUISITION_MODE!r}"
            )
        if not isinstance(self.policy, DdaCidNlPeakSupportPolicy):
            raise CidNlChromatographicSupportContractError(
                "policy must be owner-normalized DDA CID-NL support policy"
            )
        if not isinstance(self.scan_supports, tuple) or not self.scan_supports:
            raise CidNlChromatographicSupportContractError(
                "missing strict DDA scan support"
            )
        if any(
            not isinstance(member, DdaCidNlScanSupportMember)
            for member in self.scan_supports
        ):
            raise CidNlChromatographicSupportContractError(
                "scan_supports must contain typed DDA scan support members"
            )
        canonical_members = tuple(sorted(self.scan_supports, key=_member_sort_key))
        if self.scan_supports != canonical_members:
            raise CidNlChromatographicSupportContractError(
                "scan_supports must be canonical-sorted"
            )
        scan_numbers = tuple(
            member.support.dda_scan_number for member in self.scan_supports
        )
        loss_values = tuple(
            member.configured_neutral_loss_da for member in self.scan_supports
        )
        if any(value is None for value in loss_values) and not all(
            value is None for value in loss_values
        ):
            raise CidNlChromatographicSupportContractError(
                "composite scan support loss dimensions must be complete"
            )
        event_keys = tuple(zip(scan_numbers, loss_values, strict=True))
        if len(set(event_keys)) != len(event_keys):
            raise CidNlChromatographicSupportContractError(
                "duplicate DDA scan support cannot multiply one event"
            )
        if self.strict_nl_scan_count != len(set(scan_numbers)):
            raise CidNlChromatographicSupportContractError(
                "strict_nl_scan_count mismatches exact scan support"
            )
        if self.chromatographic_support_event_count != 1:
            raise CidNlChromatographicSupportContractError(
                "one physical MS1 peak must form exactly one support event"
            )
        if self.relationship != RELATIONSHIP:
            raise CidNlChromatographicSupportContractError(
                f"relationship must be {RELATIONSHIP!r}"
            )


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
