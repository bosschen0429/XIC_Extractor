"""Compare acquired views of one sample's physical MS1 peak.

View identity is evidence, not a group/claim decision. Different query centers,
integrals, content IDs and context-dependent apices do not define new peaks.
No caller may replace missing evidence with an RT or correlation tolerance.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Literal

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.mass_accuracy import mz_window
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    prepare_physical_signal,
    qualified_physical_segments,
    unique_physical_segment_at_rt,
)

if TYPE_CHECKING:
    from xic_extractor.peak_detection.observations import PeakObservation


_MAX_OFF_APEX_SIGNAL_DIFFERENCE = 0.05


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
    comparison_trace: tuple[Iterable[float], Iterable[float]] | None = None
    profile: SignalProcessingProfile | None = None


def physical_peak_view_from_observation(peak: PeakObservation) -> PhysicalPeakView:
    """Project authoritative integration bounds and the original acquired trace."""
    provenance = peak.generation_provenance
    integration = peak.integration
    return PhysicalPeakView(
        acquisition_source_id=provenance.acquisition_source_id,
        apex_rt_min=integration.rt_apex_min,
        left_rt_min=integration.rt_left_min,
        right_rt_min=integration.rt_right_min,
        xic_mz=peak.mz,
        xic_ppm_tol=provenance.query_ppm_tol,
        rt=peak.source_trace.rt,
        raw=peak.source_trace.intensity,
        profile=peak.signal_processing_profile,
    )


def compare_physical_peak_views(
    left: PhysicalPeakView, right: PhysicalPeakView
) -> Literal["same", "distinct", "unresolved"]:
    """Use extraction mass windows first, then measured overlap and lobe identity.

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
    # A lobe must be interpreted using the producing method, never today's
    # default. Missing or different methods do not establish lobe identity.
    if left.profile is None or left.profile != right.profile:
        return "unresolved"
    traces = tuple(
        tuple((float(t), float(v)) for t, v in zip(view.rt, view.raw, strict=True))
        for view in (left, right)
    )
    overlaps = tuple(
        tuple(point for point in trace if lower <= point[0] <= upper)
        for trace in traces
    )
    same_apex = apices[0] == apices[1] and lower < apices[0] < upper
    if same_apex and overlaps[0] and overlaps[0] == overlaps[1]:
        return "same"
    selected_traces = tuple(
        tuple(
            point
            for point in trace
            if view.left_rt_min <= point[0] <= view.right_rt_min
        )
        for trace, view in zip(traces, (left, right), strict=True)
    )
    if same_apex and _same_peak_with_off_apex_difference(selected_traces, overlaps):
        return "same"
    # A shared selected lobe is sufficient evidence. Unrelated ions outside
    # these selections must not erase it merely because more context is kept.
    selected_relation = _compare_measured_lobes(
        selected_traces, apices, profile=left.profile
    )
    if selected_relation != "unresolved":
        return selected_relation
    # Integration bounds belong to quantitation, not view identity. If they
    # lack shared context around both apices, use the already acquired context
    # rather than treating the cropped selections as proof of separation.
    acquired_relation = _compare_measured_lobes(traces, apices, profile=left.profile)
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
        comparison_traces, apices, profile=left.profile, require_qualified=True
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
        profile=left.profile,
        require_qualified=True,
    )


def _same_peak_with_off_apex_difference(
    selected: tuple[tuple[tuple[float, float], ...], ...],
    overlaps: tuple[tuple[tuple[float, float], ...], ...],
) -> bool:
    """Retain the measured peak maximum while allowing small flank differences.

    Compare acquired amplitudes, not independently normalized shapes. The
    trapezoidal difference is comparison evidence, never a replacement integral.
    """
    left, right = overlaps
    if (
        len(left) < 5
        or len(left) != len(right)
        or any(a[0] != b[0] for a, b in zip(left, right, strict=True))
        or max(v for _, v in selected[0]) != max(v for _, v in selected[1])
    ):
        return False
    differences = [abs(a[1] - b[1]) for a, b in zip(left, right, strict=True)]
    envelope = [max(a[1], b[1]) for a, b in zip(left, right, strict=True)]
    widths = [b[0] - a[0] for a, b in pairwise(left)]
    difference = math.fsum(
        (a + b) * width
        for (a, b), width in zip(pairwise(differences), widths, strict=True)
    )
    reference = math.fsum(
        (a + b) * width
        for (a, b), width in zip(pairwise(envelope), widths, strict=True)
    )
    return reference > 0 and difference <= _MAX_OFF_APEX_SIGNAL_DIFFERENCE * reference


def _compare_measured_lobes(
    traces: tuple[tuple[tuple[float, float], ...], ...],
    apices: tuple[float, float],
    *,
    profile: SignalProcessingProfile,
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
    prepared = prepare_physical_signal(rt, [combined[t] for t in rt], profile=profile)
    universe = enumerate_physical_segment_universe(prepared)
    qualified = (
        {
            segment
            for segment in qualified_physical_segments(
                prepared.prepared, universe.segments
            )
        }
        if require_qualified and prepared.prepared is not None
        else set()
    )
    selected = []
    for apex in apices:
        segment = unique_physical_segment_at_rt(universe, apex)
        if segment is None or (require_qualified and segment not in qualified):
            return "unresolved"
        selected.append(segment)
    return "same" if selected[0] == selected[1] else "distinct"
