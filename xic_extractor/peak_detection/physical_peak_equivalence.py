"""Compare acquired views of one sample's physical MS1 peak.

View identity is evidence, not a group/claim decision. Different query centers,
integrals, content IDs and context-dependent apices do not define new peaks.
No caller may replace missing evidence with an RT or correlation tolerance.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from xic_extractor.mass_accuracy import mz_window
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    prepare_physical_signal,
    qualified_physical_segments,
    unique_physical_segment_at_rt,
)
from xic_extractor.precursor_evidence import Ms1PrecursorObservation

if TYPE_CHECKING:
    from xic_extractor.peak_detection.observations import PeakObservationRef


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
    comparison_trace: tuple[Iterable[float], Iterable[float]] | None = None


def physical_peak_view_from_observation(peak: PeakObservationRef) -> PhysicalPeakView:
    """Project authoritative integration bounds and the original acquired trace."""
    content, provenance = peak.content, peak.generation_provenance
    integration = content.integration
    return PhysicalPeakView(
        acquisition_source_id=provenance.acquisition_source_id,
        apex_rt_min=integration.rt_apex_min,
        left_rt_min=integration.rt_left_min,
        right_rt_min=integration.rt_right_min,
        xic_mz=content.mz,
        xic_ppm_tol=provenance.query_ppm_tol,
        rt=peak.source_trace.rt,
        raw=peak.source_trace.intensity,
    )


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
    if left.acquisition_source_id != right.acquisition_source_id or max(
        left_mass[0], right_mass[0]
    ) > min(left_mass[1], right_mass[1]):
        return "distinct"
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
    if (
        apices[0] == apices[1]
        and lower < apices[0] < upper
        and overlaps[0]
        and overlaps[0] == overlaps[1]
    ):
        return "same"
    selected_traces = tuple(
        tuple(
            point
            for point in trace
            if view.left_rt_min <= point[0] <= view.right_rt_min
        )
        for trace, view in zip(traces, (left, right), strict=True)
    )
    # A shared selected lobe is sufficient evidence. Unrelated ions outside
    # these selections must not erase it merely because more context is kept.
    selected_relation = _compare_measured_lobes(selected_traces, apices)
    if selected_relation != "unresolved":
        return selected_relation
    # Integration bounds belong to quantitation, not view identity. If they
    # lack shared context around both apices, use the already acquired context
    # rather than treating the cropped selections as proof of separation.
    acquired_relation = _compare_measured_lobes(traces, apices)
    if acquired_relation != "unresolved" or not any(
        view.comparison_trace is not None for view in (left, right)
    ):
        return acquired_relation
    # Compare the original acquisition questions on one common RT envelope.
    # Batched context can be much wider: feeding all of it to baseline/lobe
    # evaluation would change the question and can split or hide the lobe.
    # The extra samples are comparison evidence, never a replacement integral.
    lower = min(trace[0][0] for trace in traces)
    upper = max(trace[-1][0] for trace in traces)
    comparison_traces = tuple(
        tuple(
            (float(t), float(v))
            for t, v in zip(
                *(view.comparison_trace or (view.rt, view.raw)), strict=True
            )
            if lower <= t <= upper
        )
        for view in (left, right)
    )
    relation = _compare_measured_lobes(
        comparison_traces, apices, require_qualified=True
    )
    if relation != "unresolved":
        return relation
    # A cropped context can join two edge-truncated tails without establishing
    # a complete peak. Reuse the wider acquired envelope when it can close the
    # owner support gate; never turn an incomplete lobe into identity proof.
    return _compare_measured_lobes(
        tuple(
            tuple(
                (float(t), float(v))
                for t, v in zip(
                    *(view.comparison_trace or (view.rt, view.raw)), strict=True
                )
            )
            for view in (left, right)
        ),
        apices,
        require_qualified=True,
    )


def _compare_measured_lobes(
    traces: tuple[tuple[tuple[float, float], ...], ...],
    apices: tuple[float, float],
    *,
    require_qualified: bool = False,
) -> Literal["same", "distinct", "unresolved"]:
    if not all(traces):
        return "unresolved"
    shared_lower = max(traces[0][0][0], traces[1][0][0])
    shared_upper = min(traces[0][-1][0], traces[1][-1][0])
    full_overlap = tuple(
        tuple(point for point in trace if shared_lower <= point[0] <= shared_upper)
        for trace in traces
    )
    if (
        len(full_overlap[0]) < 5
        or full_overlap[0] != full_overlap[1]
        or not all(
            full_overlap[0][0][0] < apex < full_overlap[0][-1][0] for apex in apices
        )
    ):
        return "unresolved"
    combined = dict((*traces[0], *traces[1]))
    rt = sorted(combined)
    prepared = prepare_physical_signal(rt, [combined[t] for t in rt])
    universe = enumerate_physical_segment_universe(prepared)
    qualified = (
        {
            s.physical_segment_id
            for s in qualified_physical_segments(prepared.prepared, universe.segments)
        }
        if require_qualified and prepared.prepared is not None
        else set()
    )
    selected = []
    for apex in apices:
        segment_id = unique_physical_segment_at_rt(universe, apex)
        if segment_id is None or (require_qualified and segment_id not in qualified):
            return "unresolved"
        selected.append(segment_id)
    return "same" if selected[0] == selected[1] else "distinct"
