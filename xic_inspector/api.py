"""Public API for XIC Inspector: SelectionRequest, ReportResult, generate_report."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from xic_inspector.rendering.html_builder import (
    build_report_html,
    write_report_atomically,
)
from xic_inspector.selection.canonical import (
    CanonicalItem,
)
from xic_inspector.selection.query import filter_by_attention, filter_by_query
from xic_inspector.selection.sampler import (
    SAMPLING_ALGORITHM,
    review_case_key,
    sample_cross_sample_sha256,
)
from xic_inspector.sources import (
    EvidenceLocator,
    EvidenceSource,
    ExplanationError,
    ExplanationUnavailableError,
    read_evidence_summary,
)

StrategyKind = Literal["sample", "attention", "query"]
CapabilityKind = Literal["retained_curves", "metadata_only"]
ProgressStage = Literal["metadata", "selection", "curves", "render", "write"]


class InspectorError(Exception):
    """Base error for XIC Inspector with deterministic exit/failure code."""

    def __init__(self, code: int, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        if cause is not None:
            self.__cause__ = cause


class InspectorCancelled(InspectorError):
    """Raised when report generation is explicitly cancelled."""

    def __init__(self, message: str = "Report generation cancelled") -> None:
        super().__init__(code=130, message=message)


@dataclass(frozen=True, slots=True)
class SelectionRequest:
    """Immutable request specification for report selection."""

    strategy: StrategyKind = "sample"
    limit: int = 30
    seed: int = 0
    sample_ids: tuple[str, ...] = ()
    candidate_ids: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()
    mz_min: float | None = None
    mz_max: float | None = None
    rt_min: float | None = None
    rt_max: float | None = None
    offset: int = 0

    def validate(self) -> None:
        """Validate request invariants per Issue #398 Section 4."""
        if self.strategy not in ("sample", "attention", "query"):
            raise InspectorError(2, f"Invalid strategy: {self.strategy}")

        if (
            not isinstance(self.limit, int)
            or isinstance(self.limit, bool)
            or not (1 <= self.limit <= 50)
        ):
            raise InspectorError(
                2, f"Limit must be an integer between 1 and 50, got {self.limit}"
            )

        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise InspectorError(2, f"Seed must be an integer, got {self.seed}")

        if (
            not isinstance(self.offset, int)
            or isinstance(self.offset, bool)
            or self.offset < 0
        ):
            raise InspectorError(
                2, f"Offset must be an integer >= 0, got {self.offset}"
            )

        for name, ids in (
            ("sample_ids", self.sample_ids),
            ("candidate_ids", self.candidate_ids),
            ("group_ids", self.group_ids),
        ):
            for ident in ids:
                if not isinstance(ident, str) or not ident.strip():
                    raise InspectorError(
                        2, f"Empty or whitespace-only identifier in {name}"
                    )

        for name, val in (
            ("mz_min", self.mz_min),
            ("mz_max", self.mz_max),
            ("rt_min", self.rt_min),
            ("rt_max", self.rt_max),
        ):
            if val is not None:
                if (
                    not isinstance(val, (int, float))
                    or math.isnan(val)
                    or math.isinf(val)
                ):
                    raise InspectorError(
                        2, f"{name} must be a finite number, got {val}"
                    )

        if (
            self.mz_min is not None
            and self.mz_max is not None
            and self.mz_min > self.mz_max
        ):
            raise InspectorError(
                2, f"mz_min ({self.mz_min}) cannot exceed mz_max ({self.mz_max})"
            )

        if (
            self.rt_min is not None
            and self.rt_max is not None
            and self.rt_min > self.rt_max
        ):
            raise InspectorError(
                2, f"rt_min ({self.rt_min}) cannot exceed rt_max ({self.rt_max})"
            )

        if self.strategy in ("sample", "attention"):
            has_query_params = (
                bool(self.candidate_ids)
                or bool(self.group_ids)
                or self.mz_min is not None
                or self.mz_max is not None
                or self.rt_min is not None
                or self.rt_max is not None
                or self.offset != 0
            )
            if has_query_params:
                raise InspectorError(
                    2,
                    f"Parameters only apply to query strategy, not {self.strategy}",
                )

        if self.strategy == "query":
            has_filter = bool(
                self.sample_ids
                or self.candidate_ids
                or self.group_ids
                or self.mz_min is not None
                or self.mz_max is not None
                or self.rt_min is not None
                or self.rt_max is not None
            )
            if not has_filter:
                raise InspectorError(
                    2, "Query strategy requires at least one filter criterion"
                )


@dataclass(frozen=True, slots=True)
class ReportProgress:
    """Progress snapshot emitted during report generation."""

    stage: ProgressStage
    completed: int
    total: int | None


@dataclass(frozen=True, slots=True)
class ReportResult:
    """Immutable result of successful report generation."""

    output_path: Path
    source: EvidenceLocator
    evidence_capability: CapabilityKind
    run_total: int
    eligible_count: int
    matched_count: int
    selected_count: int
    missing_rt_count: int
    sample_ids: tuple[str, ...]


def generate_report(
    report: Path,
    selection: SelectionRequest,
    output: Path,
    *,
    expected_source: EvidenceLocator | None = None,
    on_progress: Callable[[ReportProgress], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> ReportResult:
    """Generate an offline, self-contained HTML review workbench report."""
    selection.validate()

    report_path = Path(report).resolve()
    output_path = Path(output).resolve()

    if report_path == output_path:
        raise InspectorError(
            6, f"Output path cannot alias source report: {output_path}"
        )

    def _check_cancel() -> None:
        if is_cancelled and is_cancelled():
            raise InspectorCancelled()

    _check_cancel()

    # Stage: Metadata
    if on_progress:
        on_progress(ReportProgress(stage="metadata", completed=0, total=1))

    if not report_path.is_file():
        raise InspectorError(3, f"Source DB unavailable: {report_path}")

    try:
        summary = read_evidence_summary(report_path)
    except ExplanationUnavailableError as exc:
        raise InspectorError(3, str(exc), cause=exc) from exc
    except ExplanationError as exc:
        raise InspectorError(
            4, f"Invalid explanation source: {exc}", cause=exc
        ) from exc

    # Validate Matrix
    matrix_path_str = summary.get("matrix_path")
    if not matrix_path_str:
        raise InspectorError(4, "Source summary missing matrix_path")

    matrix_file = Path(matrix_path_str)
    if not matrix_file.is_absolute():
        matrix_file = (report_path.parent / matrix_file).resolve()
    else:
        matrix_file = matrix_file.resolve()

    if output_path == matrix_file:
        raise InspectorError(6, f"Output path cannot alias matrix: {output_path}")

    if not matrix_file.is_file():
        raise InspectorError(4, f"Bound matrix file missing: {matrix_file}")

    with matrix_file.open("rb") as handle:
        actual_matrix_sha256 = hashlib.file_digest(handle, "sha256").hexdigest()

    expected_matrix_sha256 = summary.get("matrix_sha256")
    if actual_matrix_sha256 != expected_matrix_sha256:
        raise InspectorError(
            4,
            f"Matrix SHA-256 mismatch: expected {expected_matrix_sha256}, "
            f"got {actual_matrix_sha256}",
        )

    # Validate expected_source if provided
    schema_version = summary.get("schema_version", 1)
    req_id = summary.get("processing_request_id", "")
    att_id = summary.get("processing_attempt_id", "")

    source_locator = EvidenceLocator(
        report_path=report_path,
        matrix_path=matrix_file,
        matrix_sha256=actual_matrix_sha256,
        processing_request_id=req_id,
        processing_attempt_id=att_id,
        schema_version=schema_version,
    )

    if expected_source is not None:
        src = source_locator
        if (
            expected_source.report_path.resolve() != src.report_path
            or expected_source.matrix_path.resolve() != src.matrix_path
            or expected_source.matrix_sha256 != src.matrix_sha256
            or expected_source.processing_request_id != src.processing_request_id
            or expected_source.processing_attempt_id != src.processing_attempt_id
            or expected_source.schema_version != src.schema_version
        ):
            raise InspectorError(4, "Source does not match expected_source contract")

    capability: CapabilityKind = summary.get(
        "plot_evidence_capability", "metadata_only"
    )
    if capability == "metadata_only" and (
        selection.strategy == "attention"
        or selection.rt_min is not None
        or selection.rt_max is not None
    ):
        raise InspectorError(
            5, "Source lacks the metadata required for attention/RT selection"
        )

    # Normalize sample scope
    summary_samples = summary.get("samples", [])
    universe_sample_ids = {
        s["canonical_sample_identity_id"]
        for s in summary_samples
        if "canonical_sample_identity_id" in s
    }

    if not selection.sample_ids:
        normalized_sample_ids = tuple(sorted(universe_sample_ids))
    else:
        # Intersection with universe, deduped, sorted by UTF-8 bytes
        intersected = set(selection.sample_ids) & universe_sample_ids
        normalized_sample_ids = tuple(sorted(intersected))

    if on_progress:
        on_progress(ReportProgress(stage="metadata", completed=1, total=1))

    _check_cancel()

    # Stage: Selection
    normalized_set = set(normalized_sample_ids)
    source = EvidenceSource(report_path, req_id, att_id)
    try:
        all_items = source.load_items(normalized_set)
    except ExplanationError as exc:
        raise InspectorError(4, str(exc), cause=exc) from exc
    run_total = len(all_items)

    # Eligible population
    if not normalized_set:
        eligible_items = []
    else:
        eligible_items = [
            it for it in all_items if any(s in normalized_set for s in it.sample_ids)
        ]

    eligible_count = len(eligible_items)
    selected_items: list[CanonicalItem] = []
    matched_count = 0
    missing_rt_count = 0

    if selection.strategy == "sample":
        selected_items = sample_cross_sample_sha256(
            eligible_items, limit=selection.limit, seed=selection.seed
        )
        matched_count = eligible_count
    elif selection.strategy == "attention":
        selected_items, matched_count = filter_by_attention(
            eligible_items, limit=selection.limit
        )
    elif selection.strategy == "query":
        selected_items, matched_count, missing_rt_count = filter_by_query(
            eligible_items,
            sample_ids=selection.sample_ids,
            candidate_ids=selection.candidate_ids,
            group_ids=selection.group_ids,
            mz_min=selection.mz_min,
            mz_max=selection.mz_max,
            rt_min=selection.rt_min,
            rt_max=selection.rt_max,
            offset=selection.offset,
            limit=selection.limit,
        )

    selected_count = len(selected_items)
    if on_progress:
        on_progress(
            ReportProgress(
                stage="selection",
                completed=selected_count,
                total=eligible_count,
            )
        )

    _check_cancel()

    # Stage: Curves
    try:
        rendered_items = source.load_render_items(
            selected_items, capability=capability, check_cancel=_check_cancel
        )
    except ExplanationError as exc:
        raise InspectorError(4, str(exc), cause=exc) from exc

    if on_progress:
        on_progress(
            ReportProgress(
                stage="curves",
                completed=selected_count,
                total=selected_count,
            )
        )

    _check_cancel()

    # Stage: Render
    payload_dict = {
        "summary": summary,
        "selection": {
            "strategy": selection.strategy,
            "algorithm": SAMPLING_ALGORITHM if selection.strategy == "sample" else None,
            "seed": selection.seed,
            "limit": selection.limit,
            "offset": selection.offset,
            "case_counts": {
                "/".join(case): sum(
                    review_case_key(item) == case for item in selected_items
                )
                for case in sorted({review_case_key(item) for item in selected_items})
            },
            "sample_ids": selection.sample_ids,
            "candidate_ids": selection.candidate_ids,
            "group_ids": selection.group_ids,
            "mz_min": selection.mz_min,
            "mz_max": selection.mz_max,
            "rt_min": selection.rt_min,
            "rt_max": selection.rt_max,
        },
        "samples": [
            {
                "id": s["canonical_sample_identity_id"],
                "label": s.get("sample_stem", s["canonical_sample_identity_id"]),
            }
            for s in summary_samples
            if s.get("canonical_sample_identity_id") in normalized_set
        ],
        "items": rendered_items,
    }

    html_str = build_report_html(
        title="Inspector",
        evidence_capability=capability,
        strategy=selection.strategy,
        request_id=req_id,
        run_total=run_total,
        eligible_count=eligible_count,
        selected_count=selected_count,
        source_db_name=report_path.name,
        payload_data=payload_dict,
    )

    if on_progress:
        on_progress(ReportProgress(stage="render", completed=1, total=1))

    _check_cancel()

    # Stage: Write
    try:
        write_report_atomically(html_str, output_path, check_cancel=_check_cancel)
    except ValueError as exc:
        raise InspectorError(7, str(exc), cause=exc) from exc
    except OSError as exc:
        raise InspectorError(
            6, f"Failed writing output file: {exc}", cause=exc
        ) from exc

    if on_progress:
        on_progress(ReportProgress(stage="write", completed=1, total=1))

    return ReportResult(
        output_path=output_path,
        source=source_locator,
        evidence_capability=capability,
        run_total=run_total,
        eligible_count=eligible_count,
        matched_count=matched_count,
        selected_count=selected_count,
        missing_rt_count=missing_rt_count,
        sample_ids=normalized_sample_ids,
    )
