"""Public API for XIC Inspector: SelectionRequest, ReportResult, generate_report."""

from __future__ import annotations

import hashlib
import math
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from xic_extractor.alignment_explanation.storage import (
    EXPLANATION_ARTIFACT_NAME,
)
from xic_inspector.rendering.group_colors import group_colors
from xic_inspector.rendering.html_builder import (
    build_report_html,
    write_report_atomically,
)
from xic_inspector.selection.query import filter_by_attention, filter_by_query
from xic_inspector.selection.sampler import (
    SAMPLING_ALGORITHM,
    review_case,
    sample_cross_sample_sha256,
)
from xic_inspector.sources import (
    CurveBundleLocator,
    CurveBundleSource,
    EvidenceLocator,
    ExplanationError,
    ExplanationUnavailableError,
    open_source,
)
from xic_inspector.sources.targeted_workbook import (
    TargetedWorkbookLocator,
    TargetedWorkbookSource,
)


def read_evidence_summary(path):
    """Read metadata from a supported saved-result source."""
    from xic_inspector.sources.cid_nl import read_summary

    if Path(path).suffix.lower() == ".xlsx":
        return open_source(Path(path)).summary
    return read_summary(path)


def resolve_source(path: Path) -> Path:
    """Resolve a saved evidence file or its batch/alignment output directory."""
    if not path.is_dir():
        return path
    for candidate in (
        path / EXPLANATION_ARTIFACT_NAME,
        path / "alignment" / EXPLANATION_ARTIFACT_NAME,
    ):
        if candidate.is_file():
            return candidate
    # Excel keeps "~$name.xlsx" lock files beside workbooks that are open.
    workbooks = sorted(p for p in path.glob("*.xlsx") if not p.name.startswith("~$"))
    if len(workbooks) == 1:
        return workbooks[0]
    if len(workbooks) > 1:
        raise ValueError("資料夾有多份 XLSX；請明確選擇 Targeted XLSX 檔案。")
    raise FileNotFoundError(
        f"No Targeted XLSX or {EXPLANATION_ARTIFACT_NAME} in {path}; "
        "rerun Alignment（請重新執行 Alignment 產生新輸出）。"
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
    target_ids: tuple[str, ...] = ()
    attention_only: bool = False
    mz_min: float | None = None
    mz_max: float | None = None
    rt_min: float | None = None
    rt_max: float | None = None
    offset: int = 0
    review_scope: str = "all"
    nearest_mz: float | None = None
    target_role: str | None = None

    def validate(self) -> None:
        """Validate request invariants per Issue #398 Section 4."""
        if not isinstance(self.review_scope, str) or self.review_scope not in {
            "all",
            "matrix",
            "unresolved",
            "archive",
            "candidates",
        }:
            raise InspectorError(2, "Invalid review scope")
        if self.review_scope != "all" and self.strategy != "query":
            raise InspectorError(2, "Review scope requires query selection")
        if self.strategy not in ("sample", "attention", "query"):
            raise InspectorError(2, f"Invalid strategy: {self.strategy}")

        if self.target_role is not None and (
            not isinstance(self.target_role, str)
            or self.target_role not in ("Analyte", "ISTD")
        ):
            raise InspectorError(2, "target_role must be Analyte, ISTD, or None")

        if self.nearest_mz is not None:
            if (
                isinstance(self.nearest_mz, bool)
                or not isinstance(self.nearest_mz, (int, float))
                or not math.isfinite(self.nearest_mz)
                or self.nearest_mz <= 0
            ):
                raise InspectorError(2, "nearest_mz must be a finite positive number")
            if self.strategy != "query" or self.review_scope not in {
                "matrix",
                "unresolved",
            }:
                raise InspectorError(
                    2, "Nearest m/z requires a matrix or unresolved query"
                )
            if (
                self.mz_min is not None
                or self.mz_max is not None
                or self.candidate_ids
                or self.group_ids
                or self.target_ids
                or self.target_role is not None
                or self.attention_only
            ):
                raise InspectorError(
                    2, "Nearest m/z cannot be combined with exact m/z or ID filters"
                )

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

        if not isinstance(self.attention_only, bool):
            raise InspectorError(2, "attention_only must be a boolean")

        for name, ids in (
            ("sample_ids", self.sample_ids),
            ("candidate_ids", self.candidate_ids),
            ("group_ids", self.group_ids),
            ("target_ids", self.target_ids),
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
                or bool(self.target_ids)
                or self.target_role is not None
                or self.attention_only
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
                self.nearest_mz is not None
                or self.sample_ids
                or self.candidate_ids
                or self.group_ids
                or self.target_ids
                or self.target_role is not None
                or self.attention_only
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
    source: EvidenceLocator | CurveBundleLocator | TargetedWorkbookLocator
    evidence_capability: CapabilityKind
    run_total: int
    eligible_count: int
    matched_count: int
    selected_count: int
    missing_rt_count: int
    sample_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedReport:
    """Selected owner facts shared by interactive and offline presentation."""

    source: EvidenceLocator | CurveBundleLocator | TargetedWorkbookLocator
    source_name: str
    evidence_capability: CapabilityKind
    run_total: int
    eligible_count: int
    matched_count: int
    selected_count: int
    missing_rt_count: int
    sample_ids: tuple[str, ...]
    payload: dict[str, Any]

    def render_data(self) -> dict[str, Any]:
        """Return complete presentation inputs without rendering or source I/O."""
        return {
            "title": "Inspector",
            "evidence_capability": self.evidence_capability,
            "strategy": self.payload["selection"]["strategy"],
            "run_total": self.run_total,
            "eligible_count": self.eligible_count,
            "selected_count": self.selected_count,
            "source_db_name": self.source_name,
            "payload_data": self.payload,
        }

    def to_html(self) -> str:
        return build_report_html(**self.render_data())


def prepare_report(
    report: Path,
    selection: SelectionRequest,
    *,
    expected_source: EvidenceLocator
    | CurveBundleLocator
    | TargetedWorkbookLocator
    | None = None,
    on_progress: Callable[[ReportProgress], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    _output_path: Path | None = None,
) -> PreparedReport:
    """Validate and select evidence without writing or acquiring any data."""
    selection.validate()

    report_path = Path(report).resolve()
    output_path = Path(_output_path).resolve() if _output_path is not None else None

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
        raise InspectorError(3, f"Source unavailable: {report_path}")

    try:
        source = open_source(report_path, query_only=selection.strategy == "query")
        summary = source.summary
    except ExplanationUnavailableError as exc:
        raise InspectorError(3, str(exc), cause=exc) from exc
    except ExplanationError as exc:
        raise InspectorError(4, f"Invalid source: {exc}", cause=exc) from exc

    if selection.nearest_mz is not None:
        from xic_inspector.sources.cid_nl_query import IndexedCidNlSource

        if not isinstance(source, IndexedCidNlSource):
            raise InspectorError(
                2, "Nearest m/z requires indexed Untargeted matrix evidence"
            )

    if (
        selection.target_ids
        or selection.target_role is not None
        or selection.attention_only
    ) and not isinstance(source, TargetedWorkbookSource):
        raise InspectorError(2, "Target filters require a Targeted XLSX source")

    if isinstance(source, TargetedWorkbookSource):
        summary = {
            **summary,
            "target_names": source.target_names_for_role(selection.target_role),
        }

    att_id = summary.get("processing_attempt_id", "")
    if isinstance(source, (CurveBundleSource, TargetedWorkbookSource)):
        source_locator: (
            EvidenceLocator | CurveBundleLocator | TargetedWorkbookLocator
        ) = source.locator
        if expected_source is not None and expected_source != source_locator:
            raise InspectorError(4, "Source does not match expected_source contract")
    else:
        # The SQLite source is bound to its published Matrix. Self-contained
        # workbook/bundle sources instead carry their own binding.
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
        source_locator = EvidenceLocator(
            report_path=report_path,
            matrix_path=matrix_file,
            processing_attempt_id=att_id,
        )
    if expected_source is not None and isinstance(source_locator, EvidenceLocator):
        if not isinstance(expected_source, EvidenceLocator):
            raise InspectorError(4, "Source does not match expected_source contract")
        src = source_locator
        if (
            expected_source.report_path.resolve() != src.report_path
            or expected_source.matrix_path.resolve() != src.matrix_path
            or expected_source.processing_attempt_id != src.processing_attempt_id
        ):
            raise InspectorError(4, "Source does not match expected_source contract")

    capability: CapabilityKind = summary.get(
        "plot_evidence_capability", "metadata_only"
    )
    if (
        capability == "metadata_only"
        and (
            selection.strategy == "attention"
            or selection.rt_min is not None
            or selection.rt_max is not None
        )
        and summary.get("selection_evidence_capability") != "recorded_decisions"
    ):
        raise InspectorError(
            5, "Source lacks the metadata required for attention/RT selection"
        )

    # Normalize sample scope
    summary_samples = summary.get("samples", [])
    # Colours span the whole source so a narrower selection keeps them.
    colors = group_colors(s["group"] for s in summary_samples if "group" in s)
    universe_sample_ids = {
        s["sample_key"] for s in summary_samples if "sample_key" in s
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
    if hasattr(source, "select_items"):
        try:
            (
                selected_items,
                run_total,
                eligible_count,
                matched_count,
                missing_rt_count,
            ) = source.select_items(normalized_set, selection)
        except sqlite3.Error as exc:
            raise InspectorError(4, f"Invalid query index: {exc}", cause=exc) from exc
        except ExplanationError as exc:
            raise InspectorError(
                4, f"Invalid source selection: {exc}", cause=exc
            ) from exc
    else:
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
                it
                for it in all_items
                if any(s in normalized_set for s in it.sample_ids)
            ]

        eligible_count = len(eligible_items)
        selected_items = []
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

    if selection.nearest_mz is not None:
        for item in rendered_items:
            item["mz_distance"] = source.mz_distances[item["id"]]

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
    case_counts = Counter(
        "/".join(
            review_case(
                item["kind"],
                item["stratum"],
                item["review_summary"]["observed_sample_count"],
                item["review_summary"]["scope_sample_count"],
                item["review_summary"]["origin_kinds"],
            )
        )
        for item in rendered_items
    )
    payload_dict = {
        "summary": summary,
        "selection": {
            "strategy": selection.strategy,
            "algorithm": SAMPLING_ALGORITHM if selection.strategy == "sample" else None,
            "seed": selection.seed,
            "limit": selection.limit,
            "offset": selection.offset,
            "review_scope": selection.review_scope,
            "case_counts": dict(sorted(case_counts.items())),
            "sample_ids": selection.sample_ids,
            "candidate_ids": selection.candidate_ids,
            "group_ids": selection.group_ids,
            "target_ids": selection.target_ids,
            "target_role": selection.target_role,
            "attention_only": selection.attention_only,
            "mz_min": selection.mz_min,
            "mz_max": selection.mz_max,
            "rt_min": selection.rt_min,
            "rt_max": selection.rt_max,
        },
        "samples": [
            {
                "id": s["sample_key"],
                "label": s.get("sample_stem", s["sample_key"]),
                **(
                    {"group": s["group"], "group_color": colors[s["group"]]}
                    if "group" in s
                    else {}
                ),
            }
            for s in summary_samples
            if s.get("sample_key") in normalized_set
        ],
        "items": rendered_items,
    }
    if selection.nearest_mz is not None:
        payload_dict["selection"].update(
            nearest_mz=selection.nearest_mz,
            missing_mz_count=source.missing_mz_count,
        )

    return PreparedReport(
        source=source_locator,
        source_name=report_path.name,
        evidence_capability=capability,
        run_total=run_total,
        eligible_count=eligible_count,
        matched_count=matched_count,
        selected_count=selected_count,
        missing_rt_count=missing_rt_count,
        sample_ids=normalized_sample_ids,
        payload=payload_dict,
    )


def generate_report(
    report: Path,
    selection: SelectionRequest,
    output: Path,
    *,
    expected_source: EvidenceLocator
    | CurveBundleLocator
    | TargetedWorkbookLocator
    | None = None,
    on_progress: Callable[[ReportProgress], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> ReportResult:
    """Write the same selected review as a self-contained offline report."""
    output_path = Path(output).resolve()
    prepared = prepare_report(
        report,
        selection,
        expected_source=expected_source,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
        _output_path=output_path,
    )
    html_str = prepared.to_html()

    def _check_cancel() -> None:
        if is_cancelled and is_cancelled():
            raise InspectorCancelled()

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
        source=prepared.source,
        evidence_capability=prepared.evidence_capability,
        run_total=prepared.run_total,
        eligible_count=prepared.eligible_count,
        matched_count=prepared.matched_count,
        selected_count=prepared.selected_count,
        missing_rt_count=prepared.missing_rt_count,
        sample_ids=prepared.sample_ids,
    )
