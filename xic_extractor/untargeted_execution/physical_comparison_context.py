"""Acquire missing comparison context without changing selected peak content."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import ExitStack
from dataclasses import replace
from functools import partial
from pathlib import Path

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    BatchXICAdapter,
    TraceAcquisitionBatch,
    TraceCoordinateKind,
    mint_trace_acquisition_capability,
)
from xic_extractor.mass_accuracy import mz_window
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlPhysicalCandidateFact,
)
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.physical_peak_equivalence import (
    PhysicalPeakView,
    compare_physical_peak_views,
    physical_peak_view_from_observation,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    AcquisitionSourceIdentity,
)
from xic_extractor.raw_reader import RawFileHandle, open_raw
from xic_extractor.untargeted_execution.acquisition_cache import (
    CachedUntargetedRaw,
    acquire_untargeted_trace_batch,
)
from xic_extractor.xic_models import XICRequest

PHYSICAL_COMPARISON_INPUT_KEY = "physical_comparison_capture_requests"


def _comparison_pairs(
    views: tuple[PhysicalPeakView, ...], *, context_budget_min: float
) -> Iterator[tuple[int, int]]:
    intervals = [mz_window(view.xic_mz, view.xic_ppm_tol) for view in views]
    ordered = sorted(
        range(len(views)),
        key=lambda i: (views[i].acquisition_source_id, intervals[i][0]),
    )
    for position, left_index in enumerate(ordered):
        left = views[left_index]
        for right_position in range(position + 1, len(ordered)):
            right_index = ordered[right_position]
            right = views[right_index]
            if (
                right.acquisition_source_id != left.acquisition_source_id
                or intervals[right_index][0] > intervals[left_index][1]
            ):
                break
            if abs(left.apex_rt_min - right.apex_rt_min) <= context_budget_min:
                yield left_index, right_index


def missing_physical_comparison_requests(
    views: tuple[PhysicalPeakView, ...], *, context_budget_min: float
) -> tuple[XICRequest | None, ...]:
    """Plan only missing same-source context, never infer identity from a gap.

    The existing Alignment search budget bounds I/O, not peak equivalence.
    Each ion keeps its own mass window; an intersection XIC cannot prove that
    the original windows measured the same signal.
    """
    bounds = [(min(view.rt), max(view.rt)) for view in views]
    requests: list[XICRequest | None] = [None] * len(views)
    for left_index, right_index in _comparison_pairs(
        views, context_budget_min=context_budget_min
    ):
        left = views[left_index]
        right = views[right_index]
        shared_lower = max(bounds[left_index][0], bounds[right_index][0])
        shared_upper = min(bounds[left_index][1], bounds[right_index][1])
        if (
            shared_lower < min(left.apex_rt_min, right.apex_rt_min)
            and max(left.apex_rt_min, right.apex_rt_min) < shared_upper
        ) or compare_physical_peak_views(left, right) != "unresolved":
            continue
        lower = min(bounds[left_index][0], bounds[right_index][0])
        upper = max(bounds[left_index][1], bounds[right_index][1])
        for index in (left_index, right_index):
            view = views[index]
            old = requests[index]
            requests[index] = XICRequest(
                mz=view.xic_mz,
                ppm_tol=view.xic_ppm_tol,
                rt_min=lower if old is None else min(lower, old.rt_min),
                rt_max=upper if old is None else max(upper, old.rt_max),
            )
    return tuple(requests)


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
            coordinate.acquisition_source_id != view.acquisition_source_id
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


def acquire_missing_physical_comparison_context(
    views: tuple[PhysicalPeakView, ...],
    *,
    source: AcquisitionSourceIdentity,
    raw_supplier: Callable[[], BatchXICAdapter],
    context_budget_min: float,
) -> tuple[tuple[PhysicalPeakView, ...], TraceAcquisitionBatch | None]:
    planned = missing_physical_comparison_requests(
        views, context_budget_min=context_budget_min
    )
    requests = tuple(
        dict.fromkeys(request for request in planned if request is not None)
    )
    if not requests:
        return views, None
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="xic_extractor.untargeted_execution.physical_comparison_context",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    acquisition = acquire_untargeted_trace_batch(capability, requests, raw_supplier())
    return tuple(
        physical_view_with_context(view, acquisition.traces) for view in views
    ), acquisition


def comparison_views_from_acquisitions(
    entries: Iterable[
        tuple[tuple[PeakObservationRef, ...], TraceAcquisitionBatch | None]
    ],
) -> dict[str, PhysicalPeakView]:
    """Reuse phase-batched context at formal claim completion, not quantitation."""
    peaks: dict[str, PeakObservationRef] = {}
    contexts: dict[tuple[str, float, float], dict[str, AcquiredTrace]] = {}
    seen_batches: set[int] = set()
    for observations, batch in entries:
        peaks.update(
            (peak.content.peak_observation_content_id, peak) for peak in observations
        )
        if batch is None or id(batch) in seen_batches:
            continue
        seen_batches.add(id(batch))
        for acquired in batch.traces:
            coordinate = acquired.coordinate
            key = (
                coordinate.acquisition_source_id,
                coordinate.request.mz,
                coordinate.request.ppm_tol,
            )
            contexts.setdefault(key, {})[coordinate.trace_coordinate_id] = acquired
    views = {}
    for observation_id, peak in peaks.items():
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


class PhysicalComparisonPreparation:
    """Close missing input evidence before method-subject identity is frozen.

    The input owner supplies already verified RAW expectations. One handle is
    reused for adjacent cohorts of a source and closed on source change/exit.
    Capture scopes bind the later processing request. Acquired arrays are
    cohort-local rather than retained for the whole Discovery batch.
    """

    def __init__(
        self,
        sources: Mapping[str, tuple[Path, AcquisitionSourceExpectation]],
        *,
        dll_dir: Path,
        context_budget_min: float,
        cache_dir: Path | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self.sources = sources
        self.dll_dir = dll_dir
        self.context_budget_min = context_budget_min
        self.cache_dir = cache_dir
        self.should_cancel = should_cancel
        self.capture_requests: dict[str, XICRequest] = {}
        self.logical_request_count = 0
        self._resources = ExitStack()
        self._source_id: str | None = None
        self._raw: RawFileHandle | CachedUntargetedRaw | None = None

    def close(self) -> None:
        self._resources.close()
        self._raw = None
        self._source_id = None

    def _raw_for_source(self, source_id: str) -> RawFileHandle | CachedUntargetedRaw:
        if self._source_id != source_id:
            path, expectation = self.sources[source_id]
            self.close()
            opener = partial(
                open_raw,
                path,
                self.dll_dir,
                expected_acquisition_source=expectation.source,
                _expected_path_identity=expectation.path_identity,
            )
            if self.cache_dir is None:
                self._raw = self._resources.enter_context(opener())
            else:
                self._raw = self._resources.enter_context(
                    CachedUntargetedRaw(
                        opener, source=expectation.source, cache_dir=self.cache_dir
                    )
                )
            self._source_id = source_id
        assert self._raw is not None
        return self._raw

    def resolve(
        self, facts: tuple[CidNlPhysicalCandidateFact, ...]
    ) -> tuple[CidNlPhysicalCandidateFact, ...]:
        grouped: dict[str, list[int]] = {}
        result = list(facts)
        for index, fact in enumerate(facts):
            if fact.proof is not None:
                grouped.setdefault(fact.proof.acquisition_source_id, []).append(index)
        for source_id, indices in grouped.items():
            if self.should_cancel is not None and self.should_cancel():
                raise InterruptedError("physical comparison preparation cancelled")
            views = tuple(
                view for index in indices if (view := facts[index].proof) is not None
            )
            _, expectation = self.sources[source_id]
            resolved, acquisition = acquire_missing_physical_comparison_context(
                views,
                source=expectation.source,
                raw_supplier=partial(self._raw_for_source, source_id),
                context_budget_min=self.context_budget_min,
            )
            if acquisition is None:
                continue
            self.logical_request_count += acquisition.logical_xic_request_count
            for local, index in enumerate(indices):
                result[index] = replace(facts[index], proof=resolved[local])
            recapture: dict[int, tuple[float, float]] = {}
            for left, right in _comparison_pairs(
                resolved, context_budget_min=self.context_budget_min
            ):
                if (
                    compare_physical_peak_views(resolved[left], resolved[right])
                    == "same"
                    and compare_physical_peak_views(views[left], views[right])
                    == "unresolved"
                ):
                    lower = min(min(views[left].rt), min(views[right].rt))
                    upper = max(max(views[left].rt), max(views[right].rt))
                    for local in (left, right):
                        old = recapture.get(local, (lower, upper))
                        recapture[local] = (min(lower, old[0]), max(upper, old[1]))
            for local, (lower, upper) in sorted(recapture.items()):
                index = indices[local]
                view = resolved[local]
                self.capture_requests[facts[index].candidate_id] = XICRequest(
                    mz=view.xic_mz,
                    ppm_tol=view.xic_ppm_tol,
                    rt_min=lower,
                    rt_max=upper,
                )
        return tuple(result)
