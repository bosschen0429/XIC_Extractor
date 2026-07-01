"""Sample-local detected MS1 feature model for Delta Mass / Pt pattern Discovery.

``DetectedFeature`` is the typed input the Pt pattern matcher in
``xic_extractor.discovery.delta_mass_patterns`` consumes. ``DeltaMassPatternSettings``
carries the tunable matcher parameters (pattern, ppm tolerance, RT tolerance,
ratio basis) with defaults that resolve to the built-in ``PT_PATTERN``.

This module intentionally has no runtime dependency on
``xic_extractor.discovery.delta_mass_patterns`` other than importing the
``DeltaMassPattern`` type and ``PT_PATTERN`` constant for the settings default,
mirroring the existing ``models.py`` / ``evidence_config.py`` split in this
package.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from xic_extractor.discovery.delta_mass_patterns import PT_PATTERN, DeltaMassPattern

DETECTED_FEATURES_COLUMNS = (
    "sample_stem",
    "feature_id",
    "source_feature_row_id",
    "mz",
    "rt_apex_min",
    "rt_start_min",
    "rt_end_min",
    "height",
    "area",
    "trace_quality",
    "shape_score",
    "source",
)


@dataclass(frozen=True)
class DetectedFeature:
    """A single sample-local detected MS1 feature.

    ``source_feature_row_id`` is an optional pointer back to the originating
    row (e.g. an existing CID-NL candidate's ``ms1_feature_row_id``) used by
    later composite/intersection strategies; it is not required for Pt-only
    matching.
    """

    sample_stem: str
    feature_id: str
    source_feature_row_id: str | None
    mz: float
    rt_apex_min: float
    rt_start_min: float | None
    rt_end_min: float | None
    height: float
    area: float | None
    trace_quality: str
    shape_score: float | None
    source: str


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
