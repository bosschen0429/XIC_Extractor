"""Compare acquired views of one sample's physical MS1 peak.

View identity is evidence, not a group/claim decision. Different query centers,
integrals, content IDs and context-dependent apices do not define new peaks.
No caller may replace missing evidence with an RT or correlation tolerance.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from xic_extractor.mass_accuracy import mz_window
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    mint_physical_peak_question,
    prepare_physical_signal,
)
from xic_extractor.precursor_evidence import Ms1PrecursorObservation


@dataclass(frozen=True, slots=True)
class PhysicalPeakView:
    """Already acquired facts; rt/raw are repeatable in-memory iterables."""

    acquisition_source_id: str
    apex_rt_min: float
    left_rt_min: float
    right_rt_min: float
    xic_mz: float
    xic_ppm_tol: float
    rt: Iterable[float]
    raw: Iterable[float]
    apex_centroids: tuple[Ms1PrecursorObservation, ...] = ()


def compare_physical_peak_views(
    left: PhysicalPeakView, right: PhysicalPeakView
) -> Literal["same", "distinct", "unresolved"]:
    """Use ion evidence first, then measured overlap and resolved lobe identity.

    ``unresolved`` is not evidence of distinct peaks and never authorizes merging.
    The same source and overlapping extraction windows are necessary, not proof.
    The lobe evaluation does not replace either authoritative integral.
    """
    left_mass = mz_window(left.xic_mz, left.xic_ppm_tol)
    right_mass = mz_window(right.xic_mz, right.xic_ppm_tol)
    lower = max(left.left_rt_min, right.left_rt_min)
    upper = min(left.right_rt_min, right.right_rt_min)
    apices = (left.apex_rt_min, right.apex_rt_min)
    if (
        left.acquisition_source_id != right.acquisition_source_id
        or max(left_mass[0], right_mass[0]) > min(left_mass[1], right_mass[1])
        or upper <= lower
    ):
        return "distinct"
    if not all(lower < apex < upper for apex in apices):
        return "unresolved"
    if left.apex_centroids or right.apex_centroids:
        scans = left.apex_centroids
        if (
            len(scans) == 3
            and scans == right.apex_centroids
            and lower <= scans[0].rt_min < apices[0]
            and scans[1].rt_min == apices[0] == apices[1]
            and apices[0] < scans[2].rt_min <= upper
            and len({scan.scan_number for scan in scans}) == 3
            and all(len(scan.centroid_mzs) == 1 for scan in scans)
        ):
            return "same"
        # Different singleton ions on the same scans are positive separation
        # evidence; missing or multiply occupied scans are merely inconclusive.
        if len(scans) == len(right.apex_centroids) == 3 and all(
            a.scan_number == b.scan_number
            and a.rt_min == b.rt_min
            and len(a.centroid_mzs) == len(b.centroid_mzs) == 1
            and a.centroid_mzs != b.centroid_mzs
            for a, b in zip(scans, right.apex_centroids, strict=True)
        ):
            return "distinct"
        return "unresolved"
    traces = tuple(
        tuple((float(t), float(v)) for t, v in zip(view.rt, view.raw, strict=True))
        for view in (left, right)
    )
    overlaps = tuple(
        tuple(point for point in trace if lower <= point[0] <= upper)
        for trace in traces
    )
    if not overlaps[0] or overlaps[0] != overlaps[1]:
        return "unresolved"
    if apices[0] == apices[1]:
        return "same"
    if len(overlaps[0]) < 5 or not all(
        overlaps[0][0][0] < apex < overlaps[0][-1][0] for apex in apices
    ):
        return "unresolved"
    shared_lower = max(traces[0][0][0], traces[1][0][0])
    shared_upper = min(traces[0][-1][0], traces[1][-1][0])
    full_overlap = tuple(
        tuple(point for point in trace if shared_lower <= point[0] <= shared_upper)
        for trace in traces
    )
    if full_overlap[0] != full_overlap[1]:
        return "unresolved"
    combined = dict((*traces[0], *traces[1]))
    rt = sorted(combined)
    prepared = prepare_physical_signal(rt, [combined[t] for t in rt])
    universe = enumerate_physical_segment_universe(prepared)
    selected = []
    for apex in apices:
        question = mint_physical_peak_question(
            prepared.projection,
            universe,
            selection_subject_id="physical-view-equivalence",
            query_rt_min=apex,
        )
        if len(question.admissible_segment_ids) != 1:
            return "unresolved"
        selected.append(question.admissible_segment_ids[0])
    return "same" if selected[0] == selected[1] else "distinct"
