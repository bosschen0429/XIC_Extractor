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
    ratio_status: str = "unavailable"
    shape_r: float | None = None
    shape_r_threshold: float | None = None
    position_status: str = "skipped"
    raw_peak_height: float | None = None
    local_background: float | None = None
    background_noise: float | None = None
    height_above_background: float | None = None
    required_height_above_background: float | None = None
    signal_status: str = "not_required"
    lowest_status: str = "not_required"


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
    background_positions: tuple[tuple[int, int], ...] = ()


def prepare_delta_mass_candidates(
    features: Sequence[DetectedFeature],
    settings: DeltaMassPatternSettings,
    *,
    scan_interval_sec: float,
) -> DeltaMassPreparation:
    """Find ±delta partners; record height ratios without using them for admission."""
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
                    _record_ratio(row, height)
                    row.position_status = "pass"
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
            candidates.append(candidate)
    counts["layer_1_passed"] = counts["eligible_anchors"]
    counts["layer_2_passed"] = len(candidates)
    requests = []
    request_positions = []
    for index, candidate in enumerate(candidates):
        needed = {
            i
            for i, p in enumerate(candidate.positions)
            if p.partner_source == "missing"
            or p.position_label in settings.discriminating_positions
        }
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
    background_positions = []
    if settings.lowest_position:
        for index, candidate in enumerate(candidates):
            for position_index, result in enumerate(candidate.positions):
                requests.append(
                    XICRequest(
                        result.expected_mz,
                        max(
                            0,
                            candidate.base_rt_apex_min
                            - settings.background_outer_sec / 60,
                        ),
                        candidate.base_rt_apex_min + settings.background_outer_sec / 60,
                        settings.mz_tolerance_ppm,
                    )
                )
                background_positions.append((index, position_index))
    return DeltaMassPreparation(
        tuple(candidates),
        tuple(requests),
        tuple(request_positions),
        counts,
        tuple(background_positions),
    )


def complete_delta_mass_candidates(
    prepared: DeltaMassPreparation,
    traces: Sequence[XICTrace],
    settings: DeltaMassPatternSettings,
) -> tuple[DeltaMassCandidate, ...]:
    """Consume the single acquisition batch; confirm missing positions then shape."""
    # Keep native acquisition windows: cropping a wider trace by exact RT would
    # change the vendor's scan-rounded endpoints and therefore shape decisions.
    split = len(prepared.request_positions)
    trace_map = dict(zip(prepared.request_positions, traces[:split], strict=True))
    background_trace_map = dict(
        zip(prepared.background_positions, traces[split:], strict=True)
    )
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
            _record_ratio(position, candidate.base_height)
            confirmed = (
                position.height is not None
                and position.height > 0
                and position.apex_delta_sec is not None
                and abs(position.apex_delta_sec) <= position.apex_threshold_sec
                and r is not None
                and r >= settings.min_shape_r
            )
            position.position_status = "pass" if confirmed else "fail"
            if confirmed:
                position.partner_source = "raw"
            else:
                _reject(candidate, 4, "partner_not_confirmed_in_raw")
        # The measured base owns ratio evidence and apex deltas for all partners.
        if base_was_missing and candidate.base_height is not None:
            for position in candidate.positions:
                if position.apex_rt_min is not None:
                    position.apex_delta_sec = (
                        position.apex_rt_min - candidate.base_rt_apex_min
                    ) * 60
                    if abs(position.apex_delta_sec) > position.apex_threshold_sec:
                        position.position_status = "fail"
                        _reject(candidate, 4, "apex_out_of_range")
                _record_ratio(position, candidate.base_height)
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
        if candidate.status != "rejected" and settings.lowest_position:
            _apply_signal_gate(candidate, index, background_trace_map, settings)
    return prepared.candidates


def _apply_signal_gate(
    candidate: DeltaMassCandidate,
    candidate_index: int,
    traces: dict[tuple[int, int], XICTrace],
    settings: DeltaMassPatternSettings,
) -> None:
    """Compare all positions on raw, locally background-subtracted peak heights."""
    for i, position in enumerate(candidate.positions):
        trace = traces[candidate_index, i]
        # Round sub-nanosecond subtraction error at inclusive window boundaries.
        seconds = np.round((trace.rt - candidate.base_rt_apex_min) * 60, 9)
        flanks = [
            trace.intensity[
                (seconds >= -settings.background_outer_sec)
                & (seconds <= -settings.background_inner_sec)
            ],
            trace.intensity[
                (seconds >= settings.background_inner_sec)
                & (seconds <= settings.background_outer_sec)
            ],
        ]
        flanks = [values for values in flanks if len(values) >= 3]
        peak = trace.intensity[np.abs(seconds) <= settings.shape_window_sec]
        if not flanks or not len(peak):
            continue
        # A neighboring peak/tail on one side must not set the background.
        flank = min(flanks, key=lambda values: float(np.median(values)))
        background = float(np.median(flank))
        position.raw_peak_height = float(np.max(peak))
        position.local_background = background
        position.background_noise = float(
            1.4826 * np.median(np.abs(flank - background))
        )
        position.height_above_background = position.raw_peak_height - background
    lowest = next(
        p for p in candidate.positions if p.position_label == settings.lowest_position
    )
    if any(p.height_above_background is None for p in candidate.positions):
        lowest.lowest_status = "unavailable"
        lowest.signal_status = "unavailable"
        lowest.position_status = "fail"
        _reject(candidate, 5, "local_background_unavailable")
        return
    assert lowest.height_above_background is not None
    assert lowest.background_noise is not None
    lowest.required_height_above_background = max(
        settings.min_height_above_background,
        settings.min_background_sigma * lowest.background_noise,
    )
    lowest.signal_status = (
        "pass"
        if lowest.height_above_background >= lowest.required_height_above_background
        else "fail"
    )
    lowest.lowest_status = (
        "pass"
        if all(
            lowest.height_above_background <= p.height_above_background
            for p in candidate.positions
            if p.height_above_background is not None
        )
        else "fail"
    )
    if lowest.signal_status == "fail":
        lowest.position_status = "fail"
        _reject(candidate, 5, "peak_not_above_background")
    if lowest.lowest_status == "fail":
        lowest.position_status = "fail"
        _reject(candidate, 5, "lowest_position_not_lowest")


def _record_ratio(position: DeltaMassPositionResult, base_height: float | None) -> None:
    """Annotate measured abundance evidence independently of position admission."""
    if position.height is None or base_height is None or base_height <= 0:
        return
    ratio = position.height / base_height
    position.height_ratio_to_base = ratio
    position.ratio_status = (
        "below_range"
        if ratio < position.ratio_min
        else "above_range"
        if ratio > position.ratio_max
        else "within_range"
    )


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
