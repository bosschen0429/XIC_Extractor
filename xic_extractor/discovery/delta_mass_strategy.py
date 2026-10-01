"""Settings and resolved strategy for Delta Mass / Pt pattern Discovery."""

from __future__ import annotations

from dataclasses import dataclass, field

from xic_extractor.discovery.delta_mass_patterns import PT_PATTERN, DeltaMassPattern
from xic_extractor.validation_primitives import require_finite_real


@dataclass(frozen=True)
class DeltaMassPatternSettings:
    """Tunable parameters for ``match_delta_mass_pattern()``.

    ``pattern`` defaults to the built-in ``PT_PATTERN`` singleton, and
    ``mz_tolerance_ppm`` / ``rt_tolerance_sec`` default to that same pattern's
    tolerances so that ``DeltaMassPatternSettings()`` and ``PT_PATTERN`` stay
    numerically consistent by construction.
    """

    pattern: DeltaMassPattern = field(default=PT_PATTERN)
    pattern_id: str = "pt"
    method_label: str = "pt_pattern"
    mz_tolerance_ppm: float = PT_PATTERN.mz_tolerance_ppm
    rt_tolerance_sec: float = PT_PATTERN.rt_tolerance_sec
    ratio_basis: str = "both"

    def __post_init__(self) -> None:
        if not self.pattern_id.strip():
            raise ValueError("pattern_id must be non-empty")
        if not self.method_label.strip():
            raise ValueError("method_label must be non-empty")
        mz_tolerance_ppm = require_finite_real(
            self.mz_tolerance_ppm,
            name="mz_tolerance_ppm",
        )
        if mz_tolerance_ppm <= 0:
            raise ValueError("mz_tolerance_ppm must be finite and > 0")
        rt_tolerance_sec = require_finite_real(
            self.rt_tolerance_sec,
            name="rt_tolerance_sec",
        )
        if rt_tolerance_sec <= 0:
            raise ValueError("rt_tolerance_sec must be finite and > 0")


@dataclass(frozen=True)
class PtPatternDiscoveryStrategy:
    """Resolved Pt pattern Discovery strategy for one run.

    ``label`` names the strategy source in error messages. ``cid_nl_composite``
    requests the CID-NL(dR) intersection join over the matched Pt candidates.
    """

    label: str
    settings: DeltaMassPatternSettings
    cid_nl_composite: bool
