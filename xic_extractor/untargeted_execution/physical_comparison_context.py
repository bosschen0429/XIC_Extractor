"""Reuse already acquired phase context for physical claim comparison."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    TraceAcquisitionBatch,
    TraceCoordinate,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
    index_physical_peaks,
)
from xic_extractor.peak_detection.physical_peak_equivalence import (
    PhysicalPeakView,
    physical_peak_view_from_observation,
)


def physical_view_with_context(
    view: PhysicalPeakView, contexts: tuple[AcquiredTrace, ...]
) -> PhysicalPeakView:
    """Use an acquired envelope of the exact ion, retaining its selection.

    These are comparison samples, not a replacement observation or integral.
    Source, ion and the original measured points bind this adapter projection.
    """
    original = tuple(zip(view.rt, view.raw, strict=True))
    candidates = []
    for acquired in contexts:
        coordinate = acquired.coordinate
        request = coordinate.request
        if (
            coordinate.capability.acquisition_source.acquisition_source_id
            != view.acquisition_source_id
            or (request.mz, request.ppm_tol) != (view.xic_mz, view.xic_ppm_tol)
            or request.rt_min > original[0][0]
            or request.rt_max < original[-1][0]
        ):
            continue
        trace = acquired.trace
        points = tuple(zip(trace.rt, trace.intensity, strict=True))
        if (
            tuple(p for p in points if original[0][0] <= p[0] <= original[-1][0])
            == original
        ):
            candidates.append(acquired)
    if not candidates:
        return view
    context = max(candidates, key=lambda item: len(item.trace.rt)).trace
    return replace(view, comparison_trace=(context.rt, context.intensity))


def comparison_views_from_acquisitions(
    entries: Iterable[tuple[tuple[PeakObservation, ...], TraceAcquisitionBatch | None]],
) -> dict[PeakObservation, PhysicalPeakView]:
    """Reuse phase-batched context at formal claim completion, not quantitation."""
    observed: list[PeakObservation] = []
    contexts: dict[tuple[str, float, float], dict[TraceCoordinate, AcquiredTrace]] = {}
    seen_batches: set[int] = set()
    for observations, batch in entries:
        observed.extend(observations)
        if batch is None or id(batch) in seen_batches:
            continue
        seen_batches.add(id(batch))
        for acquired in batch.traces:
            coordinate = acquired.coordinate
            key = (
                coordinate.capability.acquisition_source.acquisition_source_id,
                coordinate.request.mz,
                coordinate.request.ppm_tol,
            )
            contexts.setdefault(key, {})[coordinate] = acquired
    views = {}
    for observation_id, peak in index_physical_peaks(observed).items():
        view = physical_peak_view_from_observation(peak)
        views[observation_id] = physical_view_with_context(
            view,
            tuple(
                contexts.get(
                    (view.acquisition_source_id, view.xic_mz, view.xic_ppm_tol), {}
                ).values()
            ),
        )
    return views
