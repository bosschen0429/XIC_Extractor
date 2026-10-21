"""Pure, sample-local delta-mass hypotheses; acquisition stays with the caller."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from xic_extractor.discovery.delta_mass_strategy import DeltaMassPatternSettings
from xic_extractor.discovery.detected_features import DetectedFeature
from xic_extractor.mass_accuracy import mz_window, signed_ppm_error
from xic_extractor.xic_models import XICRequest, XICTrace


@dataclass
class DeltaMassPositionResult:
    position_label: str
    delta_da: float
    expected_mz: float
    ratio_min: float
    ratio_max: float
    apex_threshold_sec: float
    partner_source: str = "missing"
    feature_id: str | None = None
    observed_mz: float | None = None
    mz_error_ppm: float | None = None
    apex_rt_min: float | None = None
    apex_delta_sec: float | None = None
    height: float | None = None
    height_ratio_to_base: float | None = None
    shape_r: float | None = None
    shape_r_threshold: float | None = None
    position_status: str = "skipped"


@dataclass
class DeltaMassCandidate:
    sample_stem: str
    method_label: str
    pattern_id: str
    candidate_id: str
    anchor_position: str
    anchor_feature_id: str
    base_mz: float
    base_rt_apex_min: float
    base_height: float | None
    positions: tuple[DeltaMassPositionResult, ...]
    status: str = "candidate"
    rejected_at_layer: int | None = None
    reject_reason: str = ""
    cid_nl_base_status: str = ""
    cid_nl_base_candidate_id: str = ""
    cid_nl_base_join_mode: str = ""


@dataclass
class DeltaMassPreparation:
    candidates: tuple[DeltaMassCandidate, ...]
    requests: tuple[XICRequest, ...] = ()
    request_positions: tuple[tuple[int, int], ...] = ()
    counts: dict[str, int] = field(default_factory=dict)


def prepare_delta_mass_candidates(
    features: Sequence[DetectedFeature],
    settings: DeltaMassPatternSettings,
    *,
    scan_interval_sec: float,
    noise_level: float,
) -> DeltaMassPreparation:
    """Find ±delta partners from admitted detector features, then prune ratios."""
    threshold = max(
        settings.apex_floor_sec, settings.scan_interval_multiplier * scan_interval_sec
    )
    ordered = sorted(features, key=lambda f: f.mz)
    masses = [f.mz for f in ordered]
    candidates: list[DeltaMassCandidate] = []
    seen: set[tuple[str, tuple[str | None, ...]]] = set()
    counts = {
        "eligible_anchors": 0,
        "layer_1_rejected": 0,
        "layer_2_rejected": 0,
        "layer_2_deduplicated": 0,
        "layer_3_rejected": 0,
    }
    for anchor in features:
        if anchor.trace_quality != "OK":
            counts["layer_1_rejected"] += 1
            continue
        counts["eligible_anchors"] += 1
        for role in settings.positions:
            if role.label not in settings.anchor_positions:
                continue
            base_mz = anchor.mz - role.delta_da
            if base_mz <= 0:
                counts["layer_2_rejected"] += 1
                continue
            matches: list[DetectedFeature | None] = []
            used_features = {anchor.feature_id}
            reference_rt = anchor.rt_apex_min
            for position in settings.positions:
                expected = base_mz + position.delta_da
                lower_mz, upper_mz = mz_window(expected, settings.mz_tolerance_ppm)
                options = [
                    f
                    for f in ordered[
                        bisect_left(masses, lower_mz) : bisect_right(masses, upper_mz)
                    ]
                    if f.sample_stem == anchor.sample_stem
                    and f.feature_id not in used_features
                    and abs(f.rt_apex_min - reference_rt) * 60 <= threshold
                ]
                match = (
                    anchor
                    if position.label == role.label
                    else min(
                        options,
                        key=lambda f: (
                            abs(f.mz - expected),
                            abs(f.rt_apex_min - anchor.rt_apex_min),
                            f.feature_id,
                        ),
                        default=None,
                    )
                )
                matches.append(match)
                if match is not None:
                    used_features.add(match.feature_id)
                    if position.delta_da == 0:
                        base_mz = match.mz
                        reference_rt = match.rt_apex_min
            if sum(f is not None for f in matches) < 2:
                counts["layer_2_rejected"] += 1
                continue
            key = (
                anchor.sample_stem,
                tuple(f.feature_id if f else None for f in matches),
            )
            if key in seen:
                counts["layer_2_deduplicated"] += 1
                continue
            seen.add(key)
            base = matches[0]
            height = base.height if base else None
            lower_height = max(
                f.height / p.ratio_max
                for p, f in zip(settings.positions, matches, strict=True)
                if f is not None
            )
            upper_height = min(
                f.height / p.ratio_min
                for p, f in zip(settings.positions, matches, strict=True)
                if f is not None
            )
            rt = base.rt_apex_min if base else anchor.rt_apex_min
            results = []
            for position, match in zip(settings.positions, matches, strict=True):
                row = DeltaMassPositionResult(
                    position.label,
                    position.delta_da,
                    base_mz + position.delta_da,
                    position.ratio_min,
                    position.ratio_max,
                    threshold,
                )
                if match:
                    row.partner_source = "anchor" if match is anchor else "table"
                    row.feature_id = match.feature_id
                    row.observed_mz = match.mz
                    row.mz_error_ppm = signed_ppm_error(match.mz, row.expected_mz)
                    row.apex_rt_min = match.rt_apex_min
                    row.apex_delta_sec = (match.rt_apex_min - rt) * 60
                    row.height = match.height
                    row.height_ratio_to_base = (
                        match.height / height if height is not None else None
                    )
                    ratio_ok = (
                        (
                            position.ratio_min
                            <= row.height_ratio_to_base
                            <= position.ratio_max
                        )
                        if row.height_ratio_to_base is not None
                        else lower_height <= upper_height
                    )
                    row.position_status = "pass" if ratio_ok else "fail"
                results.append(row)
            candidate = DeltaMassCandidate(
                anchor.sample_stem,
                settings.method_label,
                settings.pattern_id,
                f"{anchor.sample_stem}|delta|{len(candidates) + 1}",
                role.label,
                anchor.feature_id,
                base.mz if base else base_mz,
                rt,
                height,
                tuple(results),
            )
            if any(p.position_status == "fail" for p in results):
                candidate.status = "rejected"
                candidate.rejected_at_layer = 3
                candidate.reject_reason = "ratio_out_of_range"
                counts["layer_3_rejected"] += 1
            candidates.append(candidate)
    counts["layer_1_passed"] = counts["eligible_anchors"]
    counts["layer_2_passed"] = len(candidates)
    requests = []
    request_positions = []
    for index, candidate in enumerate(candidates):
        if candidate.status == "rejected":
            for p in candidate.positions:
                if p.partner_source == "missing":
                    p.partner_source = "not_requested"
            continue
        needed = {
            i
            for i, p in enumerate(candidate.positions)
            if p.partner_source == "missing"
            or p.position_label in settings.discriminating_positions
        }
        height_upper = (
            candidate.base_height
            if candidate.base_height is not None
            else min(
                p.height / p.ratio_min
                for p in candidate.positions
                if p.height is not None
            )
        )
        below_noise = [
            i
            for i in needed
            if candidate.positions[i].partner_source == "missing"
            and height_upper * candidate.positions[i].ratio_max < noise_level
        ]
        if below_noise:
            candidate.status = "rejected"
            candidate.rejected_at_layer = 3
            candidate.reject_reason = "expected_below_noise"
            counts["layer_3_rejected"] += 1
            for p in candidate.positions:
                if p.partner_source == "missing":
                    p.partner_source = "not_requested"
            continue
        if needed:
            needed.add(0)
            if candidate.positions[0].partner_source == "missing":
                needed.add(
                    next(
                        i
                        for i, p in enumerate(candidate.positions)
                        if p.partner_source == "anchor"
                    )
                )
        for position_index in sorted(needed):
            p = candidate.positions[position_index]
            requests.append(
                XICRequest(
                    p.expected_mz,
                    max(0, candidate.base_rt_apex_min - settings.shape_window_sec / 60),
                    candidate.base_rt_apex_min + settings.shape_window_sec / 60,
                    settings.mz_tolerance_ppm,
                )
            )
            request_positions.append((index, position_index))
    return DeltaMassPreparation(
        tuple(candidates), tuple(requests), tuple(request_positions), counts
    )


def complete_delta_mass_candidates(
    prepared: DeltaMassPreparation,
    traces: Sequence[XICTrace],
    settings: DeltaMassPatternSettings,
) -> tuple[DeltaMassCandidate, ...]:
    """Consume the single acquisition batch; confirm missing positions then shape."""
    trace_map = dict(zip(prepared.request_positions, traces, strict=True))
    for index, candidate in enumerate(prepared.candidates):
        if candidate.status == "rejected":
            continue
        base_trace = trace_map.get((index, 0))
        base_was_missing = candidate.positions[0].partner_source == "missing"
        anchor_index = next(
            i for i, p in enumerate(candidate.positions) if p.partner_source == "anchor"
        )
        for position_index, position in enumerate(candidate.positions):
            if position.partner_source != "missing":
                continue
            trace = trace_map[index, position_index]
            reference = (
                trace_map[index, anchor_index] if position_index == 0 else base_trace
            )
            r = _shape_correlation(reference, trace)
            position.shape_r = r
            position.shape_r_threshold = settings.min_shape_r
            if trace.intensity.size:
                apex_index = int(np.argmax(trace.intensity))
                position.apex_rt_min = float(trace.rt[apex_index])
                position.apex_delta_sec = (
                    position.apex_rt_min - candidate.base_rt_apex_min
                ) * 60
                position.height = float(trace.intensity[apex_index])
            if (
                position_index == 0
                and position.height
                and position.apex_rt_min is not None
            ):
                candidate.base_height = position.height
                candidate.base_rt_apex_min = position.apex_rt_min
            if position.height is not None and candidate.base_height is not None:
                position.height_ratio_to_base = position.height / candidate.base_height
            confirmed = (
                position.height is not None
                and position.height > 0
                and position.apex_delta_sec is not None
                and abs(position.apex_delta_sec) <= position.apex_threshold_sec
                and r is not None
                and r >= settings.min_shape_r
                and position.height_ratio_to_base is not None
                and position.ratio_min
                <= position.height_ratio_to_base
                <= position.ratio_max
            )
            position.position_status = "pass" if confirmed else "fail"
            if confirmed:
                position.partner_source = "raw"
            else:
                _reject(candidate, 4, "partner_not_confirmed_in_raw")
        # A missing base was only bounded by table ratios before acquisition.
        # Its measured height/apex now own ratios/deltas, including table partners.
        if base_was_missing and candidate.base_height is not None:
            for position in candidate.positions:
                if position.apex_rt_min is not None:
                    position.apex_delta_sec = (
                        position.apex_rt_min - candidate.base_rt_apex_min
                    ) * 60
                    if abs(position.apex_delta_sec) > position.apex_threshold_sec:
                        position.position_status = "fail"
                        _reject(candidate, 4, "apex_out_of_range")
                if position.height is not None:
                    position.height_ratio_to_base = (
                        position.height / candidate.base_height
                    )
                    if (
                        not position.ratio_min
                        <= position.height_ratio_to_base
                        <= position.ratio_max
                    ):
                        position.position_status = "fail"
                        _reject(candidate, 4, "ratio_out_of_range")
        if candidate.status == "rejected":
            continue
        for position_index, position in enumerate(candidate.positions):
            if position.position_label not in settings.discriminating_positions:
                continue
            position.shape_r = _shape_correlation(
                base_trace, trace_map[index, position_index]
            )
            position.shape_r_threshold = settings.min_shape_r
            if position.shape_r is None or position.shape_r < settings.min_shape_r:
                position.position_status = "fail"
                _reject(candidate, 5, "discriminating_shape_below_threshold")
    return prepared.candidates


def _reject(candidate: DeltaMassCandidate, layer: int, reason: str) -> None:
    if candidate.status != "rejected":
        candidate.status = "rejected"
        candidate.rejected_at_layer = layer
        candidate.reject_reason = reason


def _shape_correlation(base: XICTrace | None, other: XICTrace) -> float | None:
    if base is None or len(base.rt) < 3 or len(other.rt) < 3:
        return None
    mask = (base.rt >= other.rt[0]) & (base.rt <= other.rt[-1])
    left = base.intensity[mask]
    right = np.interp(base.rt[mask], other.rt, other.intensity)
    if len(left) < 3 or np.std(left) == 0 or np.std(right) == 0:
        return None
    correlation = float(np.corrcoef(left, right)[0, 1])
    return correlation if np.isfinite(correlation) else None
