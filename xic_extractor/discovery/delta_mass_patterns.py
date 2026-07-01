"""Pt delta-mass pattern definition and sample-local Discovery matcher.

Implements the first Delta Mass Discovery provider: ``delta_mass(pattern_id=pt)``.
The matcher consumes typed, sample-local detected MS1 features
(``xic_extractor.discovery.detected_features.DetectedFeature``) and emits
``PtPatternCandidate`` rows only when a full four-position Pt pattern
(``base``, ``pt_plus_1``, ``pt_plus_2``, ``pt_plus_4``) is found within the
hard m/z and RT gates and has local signal support. No partial or base-only
candidates are ever emitted.

See ``docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md``
Global Constraints for the binding numeric/behavioral rules this module
implements.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from xic_extractor.discovery.detected_features import (
        DeltaMassPatternSettings,
        DetectedFeature,
    )

_LOCAL_SIGNAL_SUPPORT_QUALITIES = {"GOOD", "OK", "PASS"}
_LOCAL_SIGNAL_SUPPORT_SHAPE_SCORE_MIN = 0.80
_REQUIRED_NON_BASE_SUPPORT_COUNT = 2

_BASE_LABEL = "base"
_NON_BASE_LABELS: tuple[str, ...] = ("pt_plus_1", "pt_plus_2", "pt_plus_4")


@dataclass(frozen=True)
class DeltaMassPatternPosition:
    """A single named position within a delta-mass pattern.

    ``delta_da`` is the mass shift, in Daltons, relative to the pattern's
    ``base`` position (``delta_da == 0.0`` for ``base`` itself).
    """

    label: str
    delta_da: float


@dataclass(frozen=True)
class DeltaMassPattern:
    """A named, charge/polarity-scoped delta-mass pattern definition.

    ``mz_tolerance_ppm`` and ``rt_tolerance_sec`` are the pattern's own hard
    gate tolerances so that the ``PT_PATTERN`` constant itself is a complete,
    self-describing contract independent of any caller-supplied settings
    object.
    """

    pattern_id: str
    display_name: str
    charge: int
    polarity: str
    positions: tuple[DeltaMassPatternPosition, ...]
    mz_tolerance_ppm: float
    rt_tolerance_sec: float


PT_PATTERN = DeltaMassPattern(
    pattern_id="pt",
    display_name="Pt isotope pattern",
    charge=1,
    polarity="positive",
    positions=(
        DeltaMassPatternPosition(label="base", delta_da=0.0),
        DeltaMassPatternPosition(label="pt_plus_1", delta_da=1.0021),
        DeltaMassPatternPosition(label="pt_plus_2", delta_da=2.0023),
        DeltaMassPatternPosition(label="pt_plus_4", delta_da=4.0052),
    ),
    mz_tolerance_ppm=20.0,
    rt_tolerance_sec=30.0,
)


_PPM_GATE_EPSILON = 1e-9


def is_within_ppm(
    observed_mz: float, expected_mz: float, *, tolerance_ppm: float
) -> bool:
    """Return whether ``observed_mz`` is within ``tolerance_ppm`` of ``expected_mz``.

    Uses ``expected_mz`` as the ppm denominator, per the Pt pattern Global
    Constraint (``mz_error_ppm = (observed_mz - expected_mz) / expected_mz * 1e6``).
    This helper is supporting coverage only; the matcher below inlines the
    same formula so it can retain the signed ppm error for candidate fields.

    A small epsilon is applied to the ``<=`` boundary comparison to absorb
    floating-point round-trip error when a caller constructs ``observed_mz``
    as ``expected_mz + expected_mz * tolerance_ppm * 1e-6`` (the exact-boundary
    case), which can otherwise land a few ``1e-11`` ppm outside the nominal
    tolerance purely from float arithmetic.
    """

    mz_error_ppm = (observed_mz - expected_mz) / expected_mz * 1e6
    return abs(mz_error_ppm) <= tolerance_ppm + _PPM_GATE_EPSILON


@dataclass(frozen=True)
class PtPatternCandidate:
    """A fully-matched, sample-local Pt pattern candidate.

    Field set mirrors the Pt Candidate CSV v1 contract (Task 3's CSV writer
    and Task 5's pipeline both construct/consume this same shape). The
    ``base_*`` fields describe the base position; ``pt_plus_1_*``,
    ``pt_plus_2_*``, and ``pt_plus_4_*`` describe the three non-base
    positions. The base position itself carries no per-position match fields
    (no expected/observed delta, no ratios) since it is the ratio reference.
    """

    schema_version: str
    sample_stem: str
    method_label: str
    pattern_id: str
    candidate_id: str
    candidate_state: str
    review_priority: str
    evidence_tier: str
    evidence_score: int
    base_feature_id: str
    base_mz: float
    base_rt_apex_min: float
    base_height: float
    base_area: float | None
    position_count: int
    required_position_count: int
    rt_span_sec: float
    pattern_rt_status: str
    local_signal_support_status: str
    cross_sample_support_status: str
    ratio_basis: str
    cid_nl_base_status: str
    cid_nl_base_candidate_id: str
    cid_nl_base_join_mode: str
    reason: str
    pt_plus_1_feature_id: str
    pt_plus_1_mz: float
    pt_plus_1_expected_mz: float
    pt_plus_1_mz_error_ppm: float
    pt_plus_1_delta_error_da: float
    pt_plus_1_rt_delta_sec: float
    pt_plus_1_height_ratio_to_base: float | None
    pt_plus_1_area_ratio_to_base: float | None
    pt_plus_2_feature_id: str
    pt_plus_2_mz: float
    pt_plus_2_expected_mz: float
    pt_plus_2_mz_error_ppm: float
    pt_plus_2_delta_error_da: float
    pt_plus_2_rt_delta_sec: float
    pt_plus_2_height_ratio_to_base: float | None
    pt_plus_2_area_ratio_to_base: float | None
    pt_plus_4_feature_id: str
    pt_plus_4_mz: float
    pt_plus_4_expected_mz: float
    pt_plus_4_mz_error_ppm: float
    pt_plus_4_delta_error_da: float
    pt_plus_4_rt_delta_sec: float
    pt_plus_4_height_ratio_to_base: float | None
    pt_plus_4_area_ratio_to_base: float | None


@dataclass(frozen=True)
class _PositionMatch:
    label: str
    feature: "DetectedFeature"
    expected_mz: float
    mz_error_ppm: float
    delta_error_da: float
    rt_delta_sec: float


def match_delta_mass_pattern(
    features: Iterable["DetectedFeature"],
    *,
    pattern: DeltaMassPattern | None = None,
    settings: "DeltaMassPatternSettings | None" = None,
) -> tuple[PtPatternCandidate, ...]:
    """Match sample-local detected features against ``pattern``.

    Groups ``features`` by ``sample_stem`` and, within each group, tries
    every feature as a possible ``base`` anchor (base is defined as the
    lowest m/z position — see Global Constraints). For each base candidate,
    looks up the best match for every non-base position using an m/z-bounded
    window derived from the expected m/z, filters by absolute RT delta from
    base, and requires all non-base positions plus local signal support
    before emitting a candidate. No partial/base-only rows are ever emitted.
    """

    # Local imports avoid a hard circular import at module load time: this
    # module (delta_mass_patterns) is imported by detected_features for the
    # DeltaMassPattern/PT_PATTERN default, so importing DetectedFeature /
    # DeltaMassPatternSettings back at module scope would form a cycle.
    from xic_extractor.discovery.detected_features import DeltaMassPatternSettings

    resolved_settings = settings if settings is not None else DeltaMassPatternSettings()
    resolved_pattern = pattern if pattern is not None else resolved_settings.pattern

    non_base_positions = [
        position
        for position in resolved_pattern.positions
        if position.label != _BASE_LABEL
    ]

    features_by_sample: dict[str, list["DetectedFeature"]] = {}
    for feature in features:
        features_by_sample.setdefault(feature.sample_stem, []).append(feature)

    candidates: list[PtPatternCandidate] = []
    for sample_stem, sample_features in features_by_sample.items():
        sorted_features = sorted(sample_features, key=lambda item: item.mz)
        sorted_mzs = [item.mz for item in sorted_features]
        for base in sorted_features:
            candidate = _match_base(
                sample_stem=sample_stem,
                base=base,
                sample_features=sorted_features,
                sorted_mzs=sorted_mzs,
                non_base_positions=non_base_positions,
                settings=resolved_settings,
            )
            if candidate is not None:
                candidates.append(candidate)

    candidates.sort(
        key=lambda candidate: (
            candidate.sample_stem,
            candidate.base_rt_apex_min,
            candidate.base_mz,
            candidate.candidate_id,
        )
    )
    return tuple(candidates)


def _match_base(
    *,
    sample_stem: str,
    base: "DetectedFeature",
    sample_features: list["DetectedFeature"],
    sorted_mzs: list[float],
    non_base_positions: list[DeltaMassPatternPosition],
    settings: "DeltaMassPatternSettings",
) -> PtPatternCandidate | None:
    matches: dict[str, _PositionMatch] = {}
    for position in non_base_positions:
        match = _best_position_match(
            base=base,
            position=position,
            sample_features=sample_features,
            sorted_mzs=sorted_mzs,
            mz_tolerance_ppm=settings.mz_tolerance_ppm,
            rt_tolerance_sec=settings.rt_tolerance_sec,
        )
        if match is None:
            return None
        matches[position.label] = match

    if len(matches) != len(_NON_BASE_LABELS):
        return None

    if not _has_local_signal_support(base, matches):
        return None

    rt_span_sec = _rt_span_sec(base, matches)
    if rt_span_sec > settings.rt_tolerance_sec:
        return None

    return _build_candidate(
        sample_stem=sample_stem,
        base=base,
        matches=matches,
        rt_span_sec=rt_span_sec,
        settings=settings,
    )


def _best_position_match(
    *,
    base: "DetectedFeature",
    position: DeltaMassPatternPosition,
    sample_features: list["DetectedFeature"],
    sorted_mzs: list[float],
    mz_tolerance_ppm: float,
    rt_tolerance_sec: float,
) -> _PositionMatch | None:
    expected_mz = base.mz + position.delta_da
    window_da = expected_mz * mz_tolerance_ppm * 1e-6
    lower_mz = expected_mz - window_da
    upper_mz = expected_mz + window_da

    # ``sample_features`` is sorted ascending by m/z and ``sorted_mzs`` is the
    # parallel list of its m/z values, so the [lower_mz, upper_mz] window's
    # index range can be found in O(log n) via bisect instead of scanning
    # every feature in the sample. This preserves the exact same inclusive
    # ``lower_mz <= feature.mz <= upper_mz`` semantics as the prior linear
    # scan: bisect_left(lower_mz) is the first index >= lower_mz, and
    # bisect_right(upper_mz) is one past the last index <= upper_mz.
    start_index = bisect.bisect_left(sorted_mzs, lower_mz)
    end_index = bisect.bisect_right(sorted_mzs, upper_mz)

    best: _PositionMatch | None = None
    for feature in sample_features[start_index:end_index]:
        if feature is base:
            continue
        rt_delta_sec = abs(feature.rt_apex_min - base.rt_apex_min) * 60.0
        if rt_delta_sec > rt_tolerance_sec:
            continue
        mz_error_ppm = (feature.mz - expected_mz) / expected_mz * 1e6
        candidate_match = _PositionMatch(
            label=position.label,
            feature=feature,
            expected_mz=expected_mz,
            mz_error_ppm=mz_error_ppm,
            delta_error_da=feature.mz - expected_mz,
            rt_delta_sec=rt_delta_sec,
        )
        if best is None or _is_better_match(candidate_match, best):
            best = candidate_match
    return best


def _is_better_match(candidate: _PositionMatch, current_best: _PositionMatch) -> bool:
    """Tiebreak: smallest |RT delta|, then smallest |ppm error|, then highest height."""

    candidate_key = (
        candidate.rt_delta_sec,
        abs(candidate.mz_error_ppm),
        -candidate.feature.height,
    )
    best_key = (
        current_best.rt_delta_sec,
        abs(current_best.mz_error_ppm),
        -current_best.feature.height,
    )
    return candidate_key < best_key


def _has_local_signal_support(
    base: "DetectedFeature", matches: dict[str, _PositionMatch]
) -> bool:
    def _is_supported(feature: "DetectedFeature") -> bool:
        if feature.trace_quality in _LOCAL_SIGNAL_SUPPORT_QUALITIES:
            return True
        return (
            feature.shape_score is not None
            and feature.shape_score >= _LOCAL_SIGNAL_SUPPORT_SHAPE_SCORE_MIN
        )

    if not _is_supported(base):
        return False

    supported_non_base = sum(
        1 for match in matches.values() if _is_supported(match.feature)
    )
    return supported_non_base >= _REQUIRED_NON_BASE_SUPPORT_COUNT


def _rt_span_sec(base: "DetectedFeature", matches: dict[str, _PositionMatch]) -> float:
    rt_values_min = [base.rt_apex_min] + [
        match.feature.rt_apex_min for match in matches.values()
    ]
    return (max(rt_values_min) - min(rt_values_min)) * 60.0


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    return numerator / denominator


def _build_candidate(
    *,
    sample_stem: str,
    base: "DetectedFeature",
    matches: dict[str, _PositionMatch],
    rt_span_sec: float,
    settings: "DeltaMassPatternSettings",
) -> PtPatternCandidate:
    candidate_id = (
        f"{sample_stem}|pt|base:{base.feature_id}"
        f"|mz:{base.mz:.6f}|rt:{base.rt_apex_min:.6f}"
    )

    position_fields: dict[str, object] = {}
    for label in _NON_BASE_LABELS:
        match = matches[label]
        feature = match.feature
        position_fields[f"{label}_feature_id"] = feature.feature_id
        position_fields[f"{label}_mz"] = feature.mz
        position_fields[f"{label}_expected_mz"] = match.expected_mz
        position_fields[f"{label}_mz_error_ppm"] = match.mz_error_ppm
        position_fields[f"{label}_delta_error_da"] = match.delta_error_da
        position_fields[f"{label}_rt_delta_sec"] = match.rt_delta_sec
        position_fields[f"{label}_height_ratio_to_base"] = _ratio(
            feature.height, base.height
        )
        position_fields[f"{label}_area_ratio_to_base"] = _ratio(feature.area, base.area)

    # Fully-passing pattern: every hard gate (ppm, RT, local signal support)
    # already passed by the time we reach this point, so this is treated as
    # a maximal-confidence Pt-only detection. evidence_score/evidence_tier/
    # review_priority are not locked in by any approved Task 1 test; these
    # defaults are a design choice noted in the Task 2 report.
    return PtPatternCandidate(
        schema_version="pt_pattern_candidates.v1",
        sample_stem=sample_stem,
        method_label=settings.method_label,
        pattern_id="pt",
        candidate_id=candidate_id,
        candidate_state="pt_pattern_detected",
        review_priority="HIGH",
        evidence_tier="A",
        evidence_score=90,
        base_feature_id=base.feature_id,
        base_mz=base.mz,
        base_rt_apex_min=base.rt_apex_min,
        base_height=base.height,
        base_area=base.area,
        position_count=4,
        required_position_count=4,
        rt_span_sec=rt_span_sec,
        pattern_rt_status="within_30s",
        local_signal_support_status="supported",
        cross_sample_support_status="not_evaluated",
        ratio_basis="both",
        cid_nl_base_status="not_required",
        cid_nl_base_candidate_id="",
        cid_nl_base_join_mode="not_required",
        reason="full Pt pattern within tolerance",
        **position_fields,  # type: ignore[arg-type]
    )
