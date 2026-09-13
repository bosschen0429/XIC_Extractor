"""Public API for XIC Inspector: SelectionRequest, ReportResult, generate_report."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from xic_extractor.diagnostics.cid_nl_explanation import (
    CidNlEvidenceLocator,
    ExplanationError,
    ExplanationUnavailableError,
    read_cid_nl_curves,
    read_cid_nl_explanation,
)
from xic_inspector.rendering.html_builder import (
    build_report_html,
    write_report_atomically,
)
from xic_inspector.selection.canonical import (
    CanonicalItem,
    Witness,
    build_canonical_ref,
)
from xic_inspector.selection.query import filter_by_attention, filter_by_query
from xic_inspector.selection.sampler import sample_stratified_sha256_v1
from xic_inspector.selection.strata import (
    classify_scoped_stratum,
)

StrategyKind = Literal["sample", "attention", "query"]
CapabilityKind = Literal["retained_curves_v1", "metadata_only"]
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
    source: CidNlEvidenceLocator
    evidence_capability: CapabilityKind
    run_total: int
    eligible_count: int
    matched_count: int
    selected_count: int
    missing_rt_count: int
    sample_ids: tuple[str, ...]


def _load_canonical_items(
    db_path: Path,
    request_id: str,
    attempt_id: str,
    normalized_sample_ids: set[str],
) -> list[CanonicalItem]:
    """Load canonical items from SQLite, building witness chains and scoped strata."""
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON;")

    try:
        # 1. Load candidates
        candidates: dict[tuple[str, str], dict[str, Any]] = {}
        for cid, sid, payload_str in connection.execute(
            "SELECT candidate_id, sample_id, payload FROM candidates"
        ):
            candidates[(cid, sid)] = json.loads(payload_str)

        # 2. Load records
        records_by_kind: dict[str, dict[str, dict[str, Any]]] = {}
        for kind, rid, payload_str in connection.execute(
            "SELECT kind, record_id, payload FROM records"
        ):
            records_by_kind.setdefault(kind, {})[rid] = json.loads(payload_str)

        # 3. Load links: (cid, sid) -> list of (kind, rid)
        links_by_cand: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for cid, sid, kind, rid in connection.execute(
            "SELECT candidate_id, sample_id, kind, record_id FROM links"
        ):
            links_by_cand.setdefault((cid, sid), []).append((kind, rid))

        # Build items
        # Priority: rows -> groups -> candidates
        rows_records = records_by_kind.get("rows", {})
        final_groups = records_by_kind.get("final_groups", {})

        group_to_rows: dict[str, list[str]] = {}
        row_items: list[CanonicalItem] = []

        for row_key_id, row_payload in rows_records.items():
            # Find group and candidate links for this row
            row_group_id = row_payload.get("group_hypothesis_id")
            if row_group_id:
                group_to_rows.setdefault(row_group_id, []).append(row_key_id)

            # Find all witnesses
            witnesses: list[Witness] = []
            item_samples: set[str] = set()
            is_att = False
            has_null = False
            has_zero = False
            is_excl = False

            # Associated cell observations / candidates
            for (cid, sid), linked in links_by_cand.items():
                if any(k == "rows" and r == row_key_id for k, r in linked):
                    item_samples.add(sid)
                    c_data = candidates.get((cid, sid), {})
                    precursor_mz = c_data.get("precursor_mz")

                    # Look for peak observations or terminal subjects
                    apex_rt = None
                    for k, r in linked:
                        if k == "terminal_subjects":
                            pass
                            # check cell decisions or edge conditions
                        if k == "cell_decisions":
                            cd = records_by_kind.get("cell_decisions", {}).get(r, {})
                            val = cd.get("quantitative_value")
                            if val is None:
                                has_null = True
                            elif val == 0:
                                has_zero = True
                            if cd.get("variant") == "unresolved" and val is None:
                                is_att = True

                    prec_mz_val = (
                        float(precursor_mz) if precursor_mz is not None else None
                    )
                    witnesses.append(
                        Witness(
                            canonical_sample_identity_id=sid,
                            candidate_id=cid,
                            group_hypothesis_id=row_group_id,
                            row_key_id=row_key_id,
                            precursor_mz=prec_mz_val,
                            rt_apex_min=apex_rt,
                            sample_stem=c_data.get("sample_stem"),
                        )
                    )

            ref, sref = build_canonical_ref("row", request_id, attempt_id, row_key_id)
            stratum = classify_scoped_stratum(
                is_attention=is_att,
                is_excluded=is_excl,
                has_null_cell=has_null,
                has_zero_cell=has_zero,
            )
            row_items.append(
                CanonicalItem(
                    kind="row",
                    request_id=request_id,
                    attempt_id=attempt_id,
                    identifier=row_key_id,
                    serialized_ref=sref,
                    sample_ids=tuple(sorted(item_samples)),
                    witnesses=tuple(witnesses),
                    stratum=stratum,
                    is_attention=is_att,
                    details=row_payload,
                )
            )

        # Groups without rows
        group_items: list[CanonicalItem] = []
        for gid, g_payload in final_groups.items():
            if gid in group_to_rows:
                continue
            witnesses = []
            item_samples = set()
            for (cid, sid), linked in links_by_cand.items():
                if any(k == "final_groups" and r == gid for k, r in linked):
                    item_samples.add(sid)
                    c_data = candidates.get((cid, sid), {})
                    witnesses.append(
                        Witness(
                            canonical_sample_identity_id=sid,
                            candidate_id=cid,
                            group_hypothesis_id=gid,
                            precursor_mz=c_data.get("precursor_mz"),
                            sample_stem=c_data.get("sample_stem"),
                        )
                    )
            ref, sref = build_canonical_ref("group", request_id, attempt_id, gid)
            group_disp = g_payload.get("disposition")
            is_att = group_disp in ("provisional", "unresolved")
            stratum = "attention" if is_att else "rest"
            group_items.append(
                CanonicalItem(
                    kind="group",
                    request_id=request_id,
                    attempt_id=attempt_id,
                    identifier=gid,
                    serialized_ref=sref,
                    sample_ids=tuple(sorted(item_samples)),
                    witnesses=tuple(witnesses),
                    stratum=stratum,
                    is_attention=is_att,
                    details=g_payload,
                )
            )

        # Candidates without groups
        cand_items: list[CanonicalItem] = []
        for (cid, sid), c_payload in candidates.items():
            linked = links_by_cand.get((cid, sid), [])
            has_group = any(
                k in ("final_groups", "initial_groups", "rows")
                for k, _ in linked
            )
            if has_group:
                continue
            ref, sref = build_canonical_ref(
                "candidate", request_id, attempt_id, cid, sample_id=sid
            )
            w = Witness(
                canonical_sample_identity_id=sid,
                candidate_id=cid,
                precursor_mz=c_payload.get("precursor_mz"),
                sample_stem=c_payload.get("sample_stem"),
            )
            stratum = (
                "excluded"
                if c_payload.get("discovery_candidate_state") == "excluded"
                else "rest"
            )
            cand_items.append(
                CanonicalItem(
                    kind="candidate",
                    request_id=request_id,
                    attempt_id=attempt_id,
                    identifier=cid,
                    serialized_ref=sref,
                    sample_ids=(sid,),
                    witnesses=(w,),
                    stratum=stratum,
                    is_attention=False,
                    details=c_payload,
                )
            )

        all_items = row_items + group_items + cand_items
        return sorted(all_items, key=lambda it: it.sort_key)
    finally:
        connection.close()


def generate_report(
    report: Path,
    selection: SelectionRequest,
    output: Path,
    *,
    expected_source: CidNlEvidenceLocator | None = None,
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
        summary = read_cid_nl_explanation(report_path)
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

    source_locator = CidNlEvidenceLocator(
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
    all_items = _load_canonical_items(report_path, req_id, att_id, normalized_set)
    run_total = len(all_items)

    # Eligible population
    if not normalized_set:
        eligible_items = []
    else:
        eligible_items = [
            it for it in all_items
            if any(s in normalized_set for s in it.sample_ids)
        ]

    eligible_count = len(eligible_items)
    selected_items: list[CanonicalItem] = []
    matched_count = 0
    missing_rt_count = 0

    if selection.strategy == "sample":
        selected_items = sample_stratified_sha256_v1(
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
    curves_data: dict[str, list[dict[str, Any]]] = {}
    if capability == "retained_curves_v1" and selected_items:
        # Load curves for selected items
        record_ids = [it.identifier for it in selected_items]
        raw_curves = read_cid_nl_curves(
            report_path, sample_ids=normalized_sample_ids, record_ids=record_ids
        )
        for c in raw_curves:
            rec_id = c.get("candidate_id") or c.get("peak_observation_content_id")
            if rec_id:
                curves_data.setdefault(rec_id, []).append(c)

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
    rendered_items = []
    for it in selected_items:
        it_curves = curves_data.get(it.identifier, [])
        # Build samples detail for table
        samples_detail = []
        for sid in it.sample_ids:
            if sid not in normalized_set:
                continue
            matching_curve = next(
                (c for c in it_curves if c.get("sample_id") == sid), None
            )
            val = None
            if matching_curve and matching_curve.get("integration"):
                val = matching_curve["integration"].get("height_raw")
            sample_stem = next(
                (
                    s["sample_stem"]
                    for s in summary_samples
                    if s.get("canonical_sample_identity_id") == sid
                ),
                sid,
            )
            decision_reason = it.details.get(
                "reason", it.details.get("disposition", "OK")
            )
            samples_detail.append(
                {
                    "sample_id": sid,
                    "sample_stem": sample_stem,
                    "status_kind": it.stratum,
                    "status_label": it.stratum.upper(),
                    "quantitative_value": val,
                    "decision_reason": decision_reason,
                }
            )

        curves_payload = []
        for c in it_curves:
            crvs = c.get("curves", {})
            integ = c.get("integration") or {}
            curves_payload.append(
                {
                    "sample_id": c.get("sample_id"),
                    "raw_rts": crvs.get("rt", []),
                    "raw_intensities": crvs.get("raw", []),
                    "processed_rts": (
                        crvs.get("rt", []) if crvs.get("smoothed") else None
                    ),
                    "processed_intensities": crvs.get("smoothed"),
                    "bound_left_rt": integ.get("rt_left_min"),
                    "bound_right_rt": integ.get("rt_right_min"),
                }
            )

        mz_val = it.witnesses[0].precursor_mz if it.witnesses else None
        rt_val = it.witnesses[0].rt_apex_min if it.witnesses else None
        obs_reason = it.details.get(
            "reason", it.details.get("disposition", "Normal observation")
        )
        rendered_items.append(
            {
                "id": it.identifier,
                "kind": it.kind,
                "title": f"{it.kind.capitalize()} {it.identifier}",
                "stratum": it.stratum,
                "badge": it.stratum,
                "precursor_mz": mz_val,
                "rt_apex_min": rt_val,
                "reason": obs_reason,
                "samples": samples_detail,
                "curves": curves_payload,
            }
        )

    payload_dict = {
        "summary": summary,
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
        title=f"XIC Inspector — {req_id or 'Report'}",
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
        write_report_atomically(html_str, output_path)
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
