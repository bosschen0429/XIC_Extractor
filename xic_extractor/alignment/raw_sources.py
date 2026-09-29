from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from xic_extractor.alignment.ms1_index_source import (
    BackfillXicBackend,
    source_for_backfill_backend,
)
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.xic_models import XICTrace


class AlignmentRawHandle(Protocol):
    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]: ...


@dataclass
class RawSourceTimingStats:
    sample_stem: str
    stage: str
    elapsed_sec: float = 0.0
    extract_xic_count: int = 0
    extract_xic_batch_count: int = 0
    raw_chromatogram_call_count: int = 0
    point_count: int = 0


class TimedRawSource:
    def __init__(
        self,
        source: AlignmentRawHandle,
        *,
        stats: RawSourceTimingStats,
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        self._source = source
        self._stats = stats
        self._timer = timer

    def extract_xic(self, mz: float, rt_min: float, rt_max: float, ppm_tol: float):
        raw_call_count_before = _raw_chromatogram_call_count(self._source)
        start = self._timer()
        try:
            rt, intensity = self._source.extract_xic(mz, rt_min, rt_max, ppm_tol)
        finally:
            self._stats.extract_xic_count += 1
            self._stats.extract_xic_batch_count += 1
            self._stats.elapsed_sec += self._timer() - start
            self._stats.raw_chromatogram_call_count += _raw_call_delta(
                raw_call_count_before,
                _raw_chromatogram_call_count(self._source),
            )
        self._stats.point_count += _trace_point_count(rt)
        return rt, intensity

    @property
    def acquisition_source(self) -> object | None:
        return getattr(self._source, "acquisition_source", None)

    def extract_xic_many(self, requests):
        requests = tuple(requests)
        if hasattr(self._source, "extract_xic_many"):
            raw_call_count_before = _raw_chromatogram_call_count(self._source)
            start = self._timer()
            traces: tuple[XICTrace, ...] = ()
            try:
                traces = tuple(self._source.extract_xic_many(requests))
            finally:
                raw_call_delta = _raw_call_delta(
                    raw_call_count_before,
                    _raw_chromatogram_call_count(self._source),
                )
                self._stats.elapsed_sec += self._timer() - start
                self._stats.extract_xic_count += len(requests)
                self._stats.extract_xic_batch_count += 1 if requests else 0
                self._stats.raw_chromatogram_call_count += raw_call_delta
                self._stats.point_count += sum(len(trace.intensity) for trace in traces)
            return traces

        traces: list[XICTrace] = []
        for request in requests:
            rt, intensity = self.extract_xic(
                request.mz,
                request.rt_min,
                request.rt_max,
                request.ppm_tol,
            )
            traces.append(XICTrace.from_arrays(rt, intensity))
        return tuple(traces)

    def scan_window_for_request(self, request):
        return self._source.scan_window_for_request(request)

    def retention_time_for_scan(self, scan_number):
        return self._source.retention_time_for_scan(scan_number)


def existing_raw_paths(
    *,
    sample_order: tuple[str, ...],
    raw_files: dict[str, Path | None],
    raw_dir: Path,
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for sample_stem in sample_order:
        raw_file = raw_files.get(sample_stem)
        candidates: list[Path] = []
        if raw_file is not None and str(raw_file):
            # Analysis members may sit in subfolders of raw_dir.
            if raw_file.is_absolute() and raw_file.resolve().is_relative_to(
                raw_dir.resolve()
            ):
                candidates.append(raw_file)
            candidates.append(raw_dir / raw_file.name)
        candidates.append(raw_dir / f"{sample_stem}.raw")
        for candidate in candidates:
            if candidate.exists():
                paths[sample_stem] = candidate
                break
    return paths


def timed_raw_sources(
    raw_sources: dict[str, AlignmentRawHandle],
    *,
    stage: str,
) -> dict[str, TimedRawSource]:
    return {
        sample_stem: TimedRawSource(
            source,
            stats=RawSourceTimingStats(sample_stem=sample_stem, stage=stage),
        )
        for sample_stem, source in raw_sources.items()
    }


def timed_backfill_sources(
    raw_sources: dict[str, AlignmentRawHandle],
    *,
    backend: BackfillXicBackend,
    stage: str,
) -> tuple[
    dict[str, TimedRawSource],
    dict[str, TimedRawSource] | None,
    tuple[RawSourceTimingStats, ...],
]:
    backfill_sources: dict[str, TimedRawSource] = {}
    validation_sources: dict[str, TimedRawSource] | None = (
        {} if backend == "ms1_index_hybrid" else None
    )
    timing_stats: list[RawSourceTimingStats] = []
    for sample_stem, source in raw_sources.items():
        stats = RawSourceTimingStats(sample_stem=sample_stem, stage=stage)
        timing_stats.append(stats)
        backfill_source = source_for_backfill_backend(source, backend)
        backfill_sources[sample_stem] = TimedRawSource(backfill_source, stats=stats)
        if validation_sources is not None:
            validation_sources[sample_stem] = TimedRawSource(source, stats=stats)
    return backfill_sources, validation_sources, tuple(timing_stats)


def record_timed_raw_sources(
    raw_sources: dict[str, TimedRawSource],
    *,
    recorder: TimingRecorder,
) -> None:
    record_raw_source_timing_stats(
        tuple(source._stats for source in raw_sources.values()),
        recorder=recorder,
    )


def record_raw_source_timing_stats(
    timing_stats: tuple[RawSourceTimingStats, ...],
    *,
    recorder: TimingRecorder,
) -> None:
    for stats in timing_stats:
        if stats.extract_xic_count == 0:
            continue
        recorder.record(
            stats.stage,
            elapsed_sec=stats.elapsed_sec,
            sample_stem=stats.sample_stem,
            metrics={
                "extract_xic_count": stats.extract_xic_count,
                "extract_xic_batch_count": stats.extract_xic_batch_count,
                "raw_chromatogram_call_count": stats.raw_chromatogram_call_count,
                "point_count": stats.point_count,
            },
        )


def _trace_point_count(trace: object) -> int:
    try:
        return len(trace)  # type: ignore[arg-type]
    except TypeError:
        return 0


def _raw_chromatogram_call_count(source: object) -> int | None:
    value = getattr(source, "raw_chromatogram_call_count", None)
    if isinstance(value, int):
        return value
    return None


def _raw_call_delta(before: int | None, after: int | None) -> int:
    if before is None or after is None:
        return 0
    return max(0, after - before)
