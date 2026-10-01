from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.loader import load_config
from xic_extractor.diagnostics.targeted_ms1_shape_identity import (
    build_targeted_ms1_shape_identity_rows,
    write_targeted_ms1_shape_identity_tsv,
)
from xic_extractor.diagnostics.targeted_ms1_shape_identity_support_builder import (
    DEFAULT_CANDIDATE_SEARCH_HALF_WINDOW_MIN,
    DEFAULT_MIN_REFERENCE_POINTS,
    LONG_CSV_REQUIRED_COLUMNS,
    TargetedMs1ShapeTraceRequest,
    build_targeted_ms1_shape_identity_candidates_from_traces,
    build_targeted_ms1_shape_identity_support_plan,
)
from xic_extractor.raw_reader import open_raw
from xic_extractor.tabular_io import read_delimited_rows
from xic_extractor.xic_models import XICRequest, XICTrace

TraceLoader = Callable[[TargetedMs1ShapeTraceRequest], XICTrace]


@dataclass(frozen=True)
class TargetedMs1ShapeIdentitySupportOutputs:
    evidence_tsv: Path
    candidate_count: int
    evidence_row_count: int
    trace_request_count: int


def run_build_targeted_ms1_shape_identity_supports(
    *,
    long_csv: Path,
    raw_dir: Path,
    dll_dir: Path,
    config_dir: Path,
    output_tsv: Path,
    target_names: Sequence[str] = (),
    min_reference_points: int = DEFAULT_MIN_REFERENCE_POINTS,
    candidate_search_half_window_min: float = (
        DEFAULT_CANDIDATE_SEARCH_HALF_WINDOW_MIN
    ),
    min_own_max_similarity: float = 0.80,
    trace_loader: TraceLoader | None = None,
) -> TargetedMs1ShapeIdentitySupportOutputs:
    rows = read_delimited_rows(
        long_csv,
        required_columns=LONG_CSV_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    _config, targets = load_config(
        config_dir,
        settings_overrides={
            "data_dir": str(raw_dir),
            "dll_dir": str(dll_dir),
        },
    )
    plan = build_targeted_ms1_shape_identity_support_plan(
        rows,
        targets=targets,
        target_names=target_names,
        min_reference_points=min_reference_points,
    )
    traces = (
        _load_traces(plan.trace_requests, raw_dir=raw_dir, dll_dir=dll_dir)
        if trace_loader is None
        else _load_traces_with_loader(plan.trace_requests, trace_loader)
    )
    candidates = build_targeted_ms1_shape_identity_candidates_from_traces(
        plan,
        targets=targets,
        traces=traces,
        candidate_search_half_window_min=candidate_search_half_window_min,
    )
    evidence_rows = build_targeted_ms1_shape_identity_rows(
        candidates,
        min_own_max_similarity=min_own_max_similarity,
    )
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    write_targeted_ms1_shape_identity_tsv(output_tsv, evidence_rows)
    return TargetedMs1ShapeIdentitySupportOutputs(
        evidence_tsv=output_tsv,
        candidate_count=len(plan.candidates),
        evidence_row_count=len(evidence_rows),
        trace_request_count=len(plan.trace_requests),
    )


def _load_traces_with_loader(
    requests: Sequence[TargetedMs1ShapeTraceRequest],
    trace_loader: TraceLoader,
) -> dict[tuple[str, str], XICTrace]:
    return {request.key: trace_loader(request) for request in requests}


def _load_traces(
    requests: Sequence[TargetedMs1ShapeTraceRequest],
    *,
    raw_dir: Path,
    dll_dir: Path,
) -> dict[tuple[str, str], XICTrace]:
    grouped: dict[str, list[TargetedMs1ShapeTraceRequest]] = defaultdict(list)
    for request in requests:
        grouped[request.sample_name].append(request)

    traces: dict[tuple[str, str], XICTrace] = {}
    for sample_name in sorted(grouped):
        raw_path = raw_dir / f"{sample_name}.raw"
        if not raw_path.is_file():
            raise ValueError(f"{raw_path}: RAW file does not exist")
        sample_requests = grouped[sample_name]
        with open_raw(raw_path, dll_dir) as raw:
            extracted = raw.extract_xic_many(
                tuple(
                    XICRequest(
                        mz=request.mz,
                        rt_min=request.rt_min,
                        rt_max=request.rt_max,
                        ppm_tol=request.ppm_tol,
                    )
                    for request in sample_requests
                )
            )
        for request, trace in zip(sample_requests, extracted, strict=True):
            traces[request.key] = trace
    return traces
