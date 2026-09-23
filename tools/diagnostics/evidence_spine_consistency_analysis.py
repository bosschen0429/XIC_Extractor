from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Mapping, Sequence

from tools.diagnostics.evidence_spine_consistency_models import (
    AlignmentCell,
    ConsistencyRow,
    ConsistencySummary,
    TargetedCandidate,
    TargetedShadow,
)

SampleRtCellIndex = dict[str, tuple[tuple[float, ...], tuple[AlignmentCell, ...]]]


def _build_rows(
    targeted: tuple[TargetedCandidate, ...],
    *,
    target_mz: Mapping[tuple[str, str, str], float],
    shadows: Mapping[tuple[str, str], TargetedShadow],
    alignment_cells: tuple[AlignmentCell, ...],
    target_labels: tuple[str, ...],
    include_istd: bool,
    match_ppm: float,
    match_rt_min: float,
) -> list[ConsistencyRow]:
    focus = set(target_labels)
    cells_by_sample = _sample_rt_cell_index(alignment_cells)

    rows: list[ConsistencyRow] = []
    for candidate in targeted:
        if candidate.target_label not in focus and not (
            include_istd and candidate.role == "ISTD"
        ):
            continue
        mz = target_mz.get(
            (candidate.sample, candidate.target_label, candidate.candidate_id)
        )
        shadow = shadows.get((candidate.sample, candidate.target_label))
        match = _best_alignment_match(
            candidate,
            target_mz=mz,
            cells=_candidate_cells_for_rt_window(
                cells_by_sample,
                sample=candidate.sample,
                rt=candidate.rt,
                match_rt_min=match_rt_min,
            ),
            match_ppm=match_ppm,
            match_rt_min=match_rt_min,
        )
        rows.append(_consistency_row(candidate, mz=mz, shadow=shadow, match=match))
    return rows


def _sample_rt_cell_index(
    alignment_cells: Sequence[AlignmentCell],
) -> SampleRtCellIndex:
    grouped: dict[str, list[AlignmentCell]] = {}
    for cell in alignment_cells:
        if cell.status not in {"detected", "backfilled"}:
            continue
        if cell.mz is None or cell.rt is None:
            continue
        grouped.setdefault(cell.sample, []).append(cell)
    index: SampleRtCellIndex = {}
    for sample, cells in grouped.items():
        ordered = tuple(sorted(cells, key=lambda cell: cell.rt or 0.0))
        index[sample] = (
            tuple(cell.rt or 0.0 for cell in ordered),
            ordered,
        )
    return index


def _candidate_cells_for_rt_window(
    cells_by_sample: SampleRtCellIndex,
    *,
    sample: str,
    rt: float | None,
    match_rt_min: float,
) -> tuple[AlignmentCell, ...]:
    if rt is None:
        return ()
    rts, cells = cells_by_sample.get(sample, ((), ()))
    left = bisect_left(rts, rt - match_rt_min)
    right = bisect_right(rts, rt + match_rt_min)
    return cells[left:right]


def _best_alignment_match(
    candidate: TargetedCandidate,
    *,
    target_mz: float | None,
    cells: Sequence[AlignmentCell],
    match_ppm: float,
    match_rt_min: float,
) -> AlignmentCell | None:
    if target_mz is None or candidate.rt is None:
        return None
    candidates: list[tuple[int, float, float, str, AlignmentCell]] = []
    for cell in cells:
        if cell.mz is None or cell.rt is None:
            continue
        ppm = _ppm(cell.mz, target_mz)
        rt_delta = abs(cell.rt - candidate.rt)
        if ppm <= match_ppm and rt_delta <= match_rt_min:
            candidates.append(
                (
                    _primary_consolidation_rank(cell),
                    rt_delta,
                    ppm,
                    cell.family_id,
                    cell,
                )
            )
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[:4])[4]


def _primary_consolidation_rank(cell: AlignmentCell) -> int:
    return 0 if cell.reason.startswith("primary family consolidation") else 1


def _consistency_row(
    candidate: TargetedCandidate,
    *,
    mz: float | None,
    shadow: TargetedShadow | None,
    match: AlignmentCell | None,
) -> ConsistencyRow:
    targeted_region_verdict = "" if shadow is None else shadow.shadow_verdict
    targeted_mixture = "" if shadow is None else shadow.local_mixture_diagnostic
    reasons = _mismatch_reasons(candidate, mz=mz, shadow=shadow, match=match)
    return ConsistencyRow(
        sample=candidate.sample,
        target_label=candidate.target_label,
        role=candidate.role,
        targeted_candidate_id=candidate.candidate_id,
        untargeted_family_id="" if match is None else match.family_id,
        target_mz=mz,
        untargeted_family_mz=None if match is None else match.mz,
        mz_delta_ppm=(
            None
            if match is None or mz is None or match.mz is None
            else _ppm(match.mz, mz)
        ),
        trace_scan_count=candidate.scan_count,
        rt_window_min=_format_rt_window(candidate.left, candidate.right),
        targeted_selected_rt=candidate.rt,
        untargeted_selected_rt=None if match is None else match.rt,
        rt_delta_min=None
        if match is None or candidate.rt is None or match.rt is None
        else match.rt - candidate.rt,
        targeted_boundary_start=candidate.left,
        targeted_boundary_end=candidate.right,
        untargeted_boundary_start=None if match is None else match.left,
        untargeted_boundary_end=None if match is None else match.right,
        boundary_delta_start_min=None
        if match is None or candidate.left is None or match.left is None
        else match.left - candidate.left,
        boundary_delta_end_min=None
        if match is None or candidate.right is None or match.right is None
        else match.right - candidate.right,
        targeted_area=candidate.area,
        untargeted_area=None if match is None else match.area,
        area_ratio_untargeted_to_targeted=_ratio(
            None if match is None else match.area,
            candidate.area,
        ),
        baseline_corrected_area_available=candidate.baseline_area is not None,
        targeted_region_verdict=targeted_region_verdict,
        untargeted_region_verdict="" if match is None else match.region_verdict,
        targeted_local_mixture_verdict=targeted_mixture,
        untargeted_local_mixture_verdict=""
        if match is None
        else match.local_mixture_diagnostic,
        mismatch_reason=";".join(reasons),
    )


def _mismatch_reasons(
    candidate: TargetedCandidate,
    *,
    mz: float | None,
    shadow: TargetedShadow | None,
    match: AlignmentCell | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if mz is None:
        reasons.append("target_mz_unavailable")
    if match is None:
        reasons.append("no_alignment_mz_rt_match")
        return tuple(reasons)
    if candidate.left is not None and match.left is not None:
        if abs(match.left - candidate.left) > 0.10:
            reasons.append("boundary_start_delta_gt_0.10")
    if candidate.right is not None and match.right is not None:
        if abs(match.right - candidate.right) > 0.10:
            reasons.append("boundary_end_delta_gt_0.10")
    area_ratio = _ratio(match.area, candidate.area)
    if area_ratio is not None and (area_ratio < 0.5 or area_ratio > 2.0):
        reasons.append("area_ratio_outside_2x")
    if shadow is not None and shadow.shadow_verdict and match.region_verdict:
        if shadow.shadow_verdict != match.region_verdict:
            reasons.append("region_verdict_mismatch")
    if (
        shadow is not None
        and shadow.local_mixture_diagnostic
        and match.local_mixture_diagnostic
        and shadow.local_mixture_diagnostic != match.local_mixture_diagnostic
    ):
        reasons.append("local_mixture_mismatch")
    if not reasons:
        reasons.append("consistent")
    return tuple(reasons)


def _summarize(
    rows: Sequence[ConsistencyRow],
    *,
    target_labels: tuple[str, ...],
) -> ConsistencySummary:
    reason_counter: Counter[str] = Counter()
    matched = 0
    consistent = 0
    missing = 0
    istd_rows = 0
    for row in rows:
        if row.untargeted_family_id:
            matched += 1
        else:
            missing += 1
        if row.mismatch_reason == "consistent":
            consistent += 1
        if row.role == "ISTD":
            istd_rows += 1
        for reason in row.mismatch_reason.split(";"):
            reason_counter[reason] += 1
    return ConsistencySummary(
        rows_checked=len(rows),
        matched_rows=matched,
        consistent_rows=consistent,
        mismatch_rows=len(rows) - consistent,
        missing_alignment_rows=missing,
        focused_target_labels=";".join(target_labels),
        included_istd_rows=istd_rows,
        mismatch_reason_counts=_format_counter(reason_counter),
    )


def _ppm(observed: float, expected: float) -> float:
    if expected == 0:
        return math.inf
    return abs(observed - expected) / expected * 1_000_000.0


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _format_rt_window(left: float | None, right: float | None) -> str:
    if left is None or right is None:
        return ""
    return f"{left:.6g}-{right:.6g}"


def _format_counter(counter: Counter[str]) -> str:
    return ";".join(f"{key}:{counter[key]}" for key in sorted(counter))
