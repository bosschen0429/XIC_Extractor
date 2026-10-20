"""Read-only blast-radius report for proposed matrix identity decisions."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.machine_decision import (
    machine_decision_as_row,
    project_machine_decision,
)
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.matrix_identity import build_matrix_identity_decisions
from xic_extractor.alignment.primary_matrix_area import (
    MS1_MORPHOLOGY_PRIMARY_MATRIX_AREA_SOURCE,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.tabular_io import write_tsv

REVIEW_REQUIRED = {"feature_family_id", "include_in_primary_matrix"}
REVIEW_EVIDENCE_COLUMNS = {"family_evidence", "evidence"}
CELL_REQUIRED = {
    "feature_family_id",
    "sample_stem",
    "status",
    "area",
    "primary_matrix_area",
    "primary_matrix_area_source",
    "primary_matrix_area_reason",
    "peak_start_rt",
    "peak_end_rt",
    "rt_delta_sec",
    "reason",
}
MATRIX_IDENTITY_COLUMNS = {"feature_family_id"}
MATRIX_DOWNSTREAM_COLUMNS = {"Mz", "RT"}

OUTPUT_COLUMNS = (
    "feature_family_id",
    "current_include_in_primary_matrix",
    "proposed_include_in_primary_matrix",
    "identity_decision",
    "primary_evidence",
    "identity_reason",
    "quantifiable_detected_count",
    "quantifiable_backfill_count",
    "duplicate_assigned_count",
    "ambiguous_ms1_owner_count",
    "row_flags",
    "matrix_role",
    "evidence_tier",
    "support_reasons",
    "blockers",
    "confidence",
    "recommended_action",
    "would_change_to_audit",
    "would_change_to_production",
    "evidence_status",
    "missing_required_columns",
    "targeted_benchmark_class",
    "targeted_target_name",
    "targeted_role",
    "active_dna_istd_candidate",
)


@dataclass(frozen=True)
class _BlastRadiusInputs:
    matrix: AlignmentMatrix
    current_by_family: dict[str, Mapping[str, str]]
    cell_rows_by_family: dict[str, tuple[Mapping[str, str], ...]]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze matrix identity blast radius for alignment outputs.",
    )
    parser.add_argument("--alignment-run", type=Path, required=True)
    parser.add_argument("--benchmark-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-incomplete-summary", action="store_true")
    parser.add_argument("--require-targeted-benchmark", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run_blast_radius(
            alignment_run=args.alignment_run,
            output_dir=args.output_dir,
            benchmark_dir=args.benchmark_dir,
            allow_incomplete_summary=args.allow_incomplete_summary,
            require_targeted_benchmark=args.require_targeted_benchmark,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def run_blast_radius(
    *,
    alignment_run: Path,
    output_dir: Path,
    benchmark_dir: Path | None = None,
    allow_incomplete_summary: bool = False,
    require_targeted_benchmark: bool = False,
) -> int:
    review_rows, review_columns = _read_tsv_with_columns(
        alignment_run / "alignment_review.tsv",
    )
    cell_rows, cell_columns = _read_tsv_with_columns(
        alignment_run / "alignment_cells.tsv",
    )
    matrix_rows, matrix_columns = _read_tsv_with_columns(
        alignment_run / "alignment_matrix.tsv",
    )
    del matrix_rows
    output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_by_family = _load_benchmark_by_family(
        benchmark_dir,
        require=require_targeted_benchmark,
    )
    missing = _missing_columns(review_columns, cell_columns, matrix_columns)
    if missing:
        rows = _incomplete_rows(review_rows, missing, benchmark_by_family)
        _write_outputs(output_dir, rows, evidence_status="evidence_incomplete")
        return 0 if allow_incomplete_summary else 2

    inputs = _blast_radius_inputs_from_tsv(review_rows, cell_rows)
    decisions = build_matrix_identity_decisions(inputs.matrix, AlignmentConfig())
    output_rows: list[dict[str, object]] = []
    for family_id in sorted(decisions.rows):
        row_decision = decisions.row(family_id)
        current_row = inputs.current_by_family.get(family_id, {})
        current_include = _is_trueish(current_row.get("include_in_primary_matrix"))
        proposed_include = row_decision.include_in_primary_matrix
        benchmark = benchmark_by_family.get(family_id, {})
        projected_review_row = {
            "feature_family_id": family_id,
            "neutral_loss_tag": current_row.get("neutral_loss_tag", ""),
            "include_in_primary_matrix": proposed_include,
            "identity_decision": row_decision.identity_decision,
            "identity_confidence": row_decision.identity_confidence,
            "identity_reason": row_decision.identity_reason,
            "primary_evidence": row_decision.primary_evidence,
            "quantifiable_detected_count": (row_decision.quantifiable_detected_count),
            "quantifiable_backfill_count": row_decision.quantifiable_backfill_count,
            "accepted_backfill_count": row_decision.quantifiable_backfill_count,
            "review_backfill_count": row_decision.review_backfill_count,
            "duplicate_assigned_count": row_decision.duplicate_assigned_count,
            "ambiguous_ms1_owner_count": row_decision.ambiguous_ms1_owner_count,
            "row_flags": ";".join(row_decision.row_flags),
        }
        machine_decision = project_machine_decision(
            projected_review_row,
            inputs.cell_rows_by_family.get(family_id, ()),
        )
        output_rows.append(
            {
                "feature_family_id": family_id,
                "current_include_in_primary_matrix": current_include,
                "proposed_include_in_primary_matrix": proposed_include,
                "identity_decision": row_decision.identity_decision,
                "primary_evidence": row_decision.primary_evidence,
                "identity_reason": row_decision.identity_reason,
                "quantifiable_detected_count": (
                    row_decision.quantifiable_detected_count
                ),
                "quantifiable_backfill_count": (
                    row_decision.quantifiable_backfill_count
                ),
                "duplicate_assigned_count": row_decision.duplicate_assigned_count,
                "ambiguous_ms1_owner_count": (row_decision.ambiguous_ms1_owner_count),
                "row_flags": ";".join(row_decision.row_flags),
                **machine_decision_as_row(machine_decision),
                "would_change_to_audit": current_include and not proposed_include,
                "would_change_to_production": (
                    not current_include and proposed_include
                ),
                "evidence_status": "complete",
                "missing_required_columns": "",
                "targeted_benchmark_class": benchmark.get("benchmark_class", ""),
                "targeted_target_name": benchmark.get("target_name", ""),
                "targeted_role": benchmark.get("role", ""),
                "active_dna_istd_candidate": benchmark.get(
                    "active_dna_istd_candidate",
                    "",
                ),
            },
        )
    _write_outputs(output_dir, output_rows, evidence_status="complete")
    return 0


def _blast_radius_inputs_from_tsv(
    review_rows: Sequence[Mapping[str, str]],
    cell_rows: Sequence[Mapping[str, str]],
) -> _BlastRadiusInputs:
    clusters = []
    current_by_family: dict[str, Mapping[str, str]] = {}
    for row in review_rows:
        clusters.append(_cluster_from_review(row))
        current_by_family[row.get("feature_family_id", "")] = row

    cells = []
    sample_stems = set[str]()
    grouped_cell_rows: dict[str, list[Mapping[str, str]]] = {}
    for row in cell_rows:
        cells.append(_cell_from_row(row))
        sample_stem = row.get("sample_stem", "")
        if sample_stem:
            sample_stems.add(sample_stem)
        grouped_cell_rows.setdefault(row.get("feature_family_id", ""), []).append(row)

    return _BlastRadiusInputs(
        matrix=AlignmentMatrix(
            clusters=tuple(clusters),
            cells=tuple(cells),
            sample_order=tuple(sorted(sample_stems)),
        ),
        current_by_family=current_by_family,
        cell_rows_by_family={
            family_id: tuple(rows) for family_id, rows in grouped_cell_rows.items()
        },
    )


def _cluster_from_review(row: Mapping[str, str]) -> SimpleNamespace:
    evidence = row.get("family_evidence") or row.get("evidence") or ""
    return SimpleNamespace(
        feature_family_id=row.get("feature_family_id", ""),
        neutral_loss_tag=row.get("neutral_loss_tag", ""),
        family_center_mz=_float(row.get("family_center_mz")) or 0.0,
        family_center_rt=_float(row.get("family_center_rt")) or 0.0,
        family_product_mz=_float(row.get("family_product_mz")) or 0.0,
        family_observed_neutral_loss_da=(
            _float(row.get("family_observed_neutral_loss_da")) or 0.0
        ),
        has_anchor=_is_trueish(row.get("has_anchor")),
        event_cluster_ids=tuple(
            part for part in row.get("event_cluster_ids", "").split(";") if part
        ),
        event_member_count=int(_float(row.get("event_member_count")) or 0),
        evidence=evidence,
        review_only=_is_review_only_row(row, evidence),
    )


def _cell_from_row(row: Mapping[str, str]) -> AlignedCell:
    start = _float(row.get("peak_start_rt"))
    end = _float(row.get("peak_end_rt"))
    apex = _float(row.get("apex_rt"))
    raw_area = _float(row.get("area"))
    if apex is None and start is not None and end is not None:
        apex = (start + end) / 2.0
    return AlignedCell(
        sample_stem=row.get("sample_stem", ""),
        cluster_id=row.get("feature_family_id", ""),
        status=row.get("status", ""),  # type: ignore[arg-type]
        area=raw_area,
        apex_rt=apex,
        height=_float(row.get("height")) or 1.0,
        peak_start_rt=start,
        peak_end_rt=end,
        rt_delta_sec=_float(row.get("rt_delta_sec")),
        trace_quality=row.get("trace_quality", ""),
        scan_support_score=_float(row.get("scan_support_score")),
        source_candidate_id=row.get("source_candidate_id") or None,
        reason=row.get("reason", ""),
        selected_integration=_integration_from_cell_row(
            row,
            raw_area=raw_area,
            apex=apex,
            start=start,
            end=end,
        ),
    )


def _integration_from_cell_row(
    row: Mapping[str, str],
    *,
    raw_area: float | None,
    apex: float | None,
    start: float | None,
    end: float | None,
) -> IntegrationResult | None:
    if (
        row.get("primary_matrix_area_source", "")
        != MS1_MORPHOLOGY_PRIMARY_MATRIX_AREA_SOURCE
    ):
        return None
    primary_area = _float(row.get("primary_matrix_area"))
    if (
        primary_area is None
        or raw_area is None
        or apex is None
        or start is None
        or end is None
    ):
        return None
    return IntegrationResult(
        rt_left_min=start,
        rt_apex_min=apex,
        rt_right_min=end,
        raw_apex_rt_min=apex,
        rt_width_min=max(end - start, 0.0),
        height_raw=_float(row.get("height")) or 1.0,
        height_smoothed=_float(row.get("height")) or 1.0,
        area_raw_counts_seconds=raw_area,
        area_ms1_morphology=primary_area,
        ms1_morphology_area_source=MS1_MORPHOLOGY_PRIMARY_MATRIX_AREA_SOURCE,
        boundary_sources=("alignment_cells_tsv",),
    )


def _missing_columns(
    review_columns: set[str],
    cell_columns: set[str],
    matrix_columns: set[str],
) -> list[str]:
    missing = [
        f"alignment_review.tsv:{column}"
        for column in sorted(REVIEW_REQUIRED - review_columns)
    ]
    if not (REVIEW_EVIDENCE_COLUMNS & review_columns):
        missing.append("alignment_review.tsv:family_evidence or evidence")
    missing.extend(
        f"alignment_cells.tsv:{column}"
        for column in sorted(CELL_REQUIRED - cell_columns)
    )
    if not (
        MATRIX_IDENTITY_COLUMNS <= matrix_columns
        or MATRIX_DOWNSTREAM_COLUMNS <= matrix_columns
    ):
        missing.append("alignment_matrix.tsv:feature_family_id or Mz+RT")
    return missing


def _is_review_only_row(row: Mapping[str, str], evidence: str) -> bool:
    row_flags = _split_tokens(row.get("row_flags", ""))
    evidence_tokens = _split_tokens(evidence)
    return (
        row.get("identity_reason", "") == "review_only"
        or "review_only" in row_flags
        or "review_only" in evidence_tokens
    )


def _incomplete_rows(
    review_rows: Sequence[Mapping[str, str]],
    missing: Sequence[str],
    benchmark_by_family: Mapping[str, Mapping[str, str]],
) -> list[dict[str, object]]:
    if not review_rows:
        review_rows = ({"feature_family_id": ""},)
    rows: list[dict[str, object]] = []
    for review_row in review_rows:
        family_id = review_row.get("feature_family_id", "")
        benchmark = benchmark_by_family.get(family_id, {})
        rows.append(
            {
                "feature_family_id": family_id,
                "current_include_in_primary_matrix": review_row.get(
                    "include_in_primary_matrix",
                    "",
                ),
                "proposed_include_in_primary_matrix": "",
                "identity_decision": "",
                "primary_evidence": "",
                "identity_reason": "",
                "quantifiable_detected_count": "",
                "quantifiable_backfill_count": "",
                "duplicate_assigned_count": "",
                "ambiguous_ms1_owner_count": "",
                "row_flags": "",
                "matrix_role": "",
                "evidence_tier": "",
                "support_reasons": "",
                "blockers": "",
                "confidence": "",
                "recommended_action": "",
                "would_change_to_audit": "",
                "would_change_to_production": "",
                "evidence_status": "evidence_incomplete",
                "missing_required_columns": ";".join(missing),
                "targeted_benchmark_class": benchmark.get("benchmark_class", ""),
                "targeted_target_name": benchmark.get("target_name", ""),
                "targeted_role": benchmark.get("role", ""),
                "active_dna_istd_candidate": benchmark.get(
                    "active_dna_istd_candidate",
                    "",
                ),
            },
        )
    return rows


def _load_benchmark_by_family(
    benchmark_dir: Path | None,
    *,
    require: bool,
) -> dict[str, dict[str, str]]:
    if benchmark_dir is None:
        if require:
            raise ValueError("--benchmark-dir is required")
        return {}
    required_files = [
        benchmark_dir / "targeted_istd_benchmark_matches.tsv",
        benchmark_dir / "targeted_istd_benchmark_summary.tsv",
        benchmark_dir / "targeted_istd_benchmark.json",
    ]
    missing = [path for path in required_files if not path.exists()]
    if missing and require:
        raise FileNotFoundError(str(missing[0]))
    if missing:
        return {}
    rows, _columns = _read_tsv_with_columns(required_files[0])
    summary_rows, _summary_columns = _read_tsv_with_columns(required_files[1])
    summary_by_target = {
        _target_name(row): row for row in summary_rows if _target_name(row)
    }
    by_family: dict[str, dict[str, str]] = {}
    for row in rows:
        family_id = row.get("feature_family_id", "")
        if family_id:
            target_name = _target_name(row)
            summary = summary_by_target.get(target_name, {})
            by_family[family_id] = _normalized_benchmark_row(row, summary)
    return by_family


def _normalized_benchmark_row(
    match_row: Mapping[str, str],
    summary_row: Mapping[str, str],
) -> dict[str, str]:
    target_name = _target_name(match_row) or _target_name(summary_row)
    family_id = match_row.get("feature_family_id", "")
    selected_family_id = summary_row.get("selected_feature_id", "")
    return {
        **dict(match_row),
        "target_name": target_name,
        "role": match_row.get("role") or summary_row.get("role", ""),
        "benchmark_class": (
            match_row.get("benchmark_class")
            or summary_row.get("benchmark_class")
            or summary_row.get("status", "")
        ),
        "active_dna_istd_candidate": (
            match_row.get("active_dna_istd_candidate")
            or _selected_family_active_tag(
                family_id=family_id,
                selected_family_id=selected_family_id,
                summary_row=summary_row,
            )
        ),
    }


def _selected_family_active_tag(
    *,
    family_id: str,
    selected_family_id: str,
    summary_row: Mapping[str, str],
) -> str:
    if not family_id or family_id != selected_family_id:
        return ""
    return summary_row.get("active_dna_istd_candidate") or summary_row.get(
        "active_tag", ""
    )


def _target_name(row: Mapping[str, str]) -> str:
    return row.get("target_name") or row.get("target_label") or ""


def _write_outputs(
    output_dir: Path,
    rows: Sequence[Mapping[str, object]],
    *,
    evidence_status: str,
) -> None:
    _write_tsv(output_dir / "matrix_identity_blast_radius.tsv", OUTPUT_COLUMNS, rows)
    payload = {
        "evidence_status": evidence_status,
        "row_count": len(rows),
        "would_change_to_audit_count": sum(
            1 for row in rows if _is_trueish(row.get("would_change_to_audit"))
        ),
        "would_change_to_production_count": sum(
            1 for row in rows if _is_trueish(row.get("would_change_to_production"))
        ),
        "rows": list(rows),
    }
    (output_dir / "matrix_identity_blast_radius.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _read_tsv_with_columns(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [dict(row) for row in reader], set(reader.fieldnames or [])


def _write_tsv(
    path: Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    write_tsv(path, rows, columns, formatter=_tsv_value)


def _tsv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_trueish(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y"}


def _split_tokens(value: object) -> set[str]:
    return {
        part.strip()
        for part in str(value or "").replace(",", ";").split(";")
        if part.strip()
    }


if __name__ == "__main__":
    raise SystemExit(main())
