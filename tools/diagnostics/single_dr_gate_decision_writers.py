"""Output writers for the single-dR production gate decision report."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.diagnostics.diagnostic_io import write_tsv

_SUMMARY_COLUMNS = ("metric", "value")
_FAMILY_COLUMNS = (
    "feature_family_id",
    "neutral_loss_tag",
    "risk_classification",
    "rt_context",
    "include_in_primary_matrix",
    "q_detected",
    "q_rescue",
    "sample_count",
    "rescue_fraction",
    "duplicate_assigned_count",
    "row_flags",
    "promotion_state",
    "promotion_reason",
    "promotion_flags",
    "supported_rescue_count",
    "assessed_rescue_count",
    "seed_quality_status",
    "min_evidence_score",
    "min_seed_event_count",
    "max_abs_nl_ppm",
    "min_scan_support_score",
    "missing_detected_candidate_count",
    "targeted_istd_labels",
    "targeted_istd_statuses",
    "family_center_mz",
    "family_center_rt",
    "family_product_mz",
    "family_observed_neutral_loss_da",
)
_DETECTED_CELL_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "source_candidate_id",
    "area",
    "apex_rt",
    "seed_candidate_joined",
    "evidence_score",
    "seed_event_count",
    "neutral_loss_mass_error_ppm",
    "ms1_scan_support_score",
)
_GATE_CANDIDATE_COLUMNS = (
    "gate_candidate_id",
    "rule_description",
    "affected_primary_rows",
    "affected_istd_rows",
    "affected_known_target_rows",
    "affected_rows_by_reason",
    "false_positive_risk_reason",
    "recommended_action",
    "recommendation_reason",
)


def write_outputs(output_dir: Path, result: Mapping[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_tsv(
        output_dir / "single_dr_gate_decision_summary.tsv",
        result["summary"],
        fieldnames=_SUMMARY_COLUMNS,
    )
    _write_tsv(
        output_dir / "single_dr_gate_decision_families.tsv",
        result["families"],
        fieldnames=_FAMILY_COLUMNS,
    )
    _write_tsv(
        output_dir / "single_dr_gate_decision_detected_cells.tsv",
        result["detected_cells"],
        fieldnames=_DETECTED_CELL_COLUMNS,
    )
    _write_tsv(
        output_dir / "single_dr_gate_candidates.tsv",
        result["gate_candidates"],
        fieldnames=_GATE_CANDIDATE_COLUMNS,
    )
    (output_dir / "single_dr_gate_decision.json").write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "single_dr_gate_decision.md").write_text(
        _markdown(result),
        encoding="utf-8",
    )


def _write_tsv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    fieldnames: tuple[str, ...],
) -> None:
    write_tsv(path, rows, fieldnames, formatter=_format_value)


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _markdown(result: Mapping[str, Any]) -> str:
    summary = {row["metric"]: row["value"] for row in result["summary"]}
    lines = [
        "# Single-dR Production Gate Decision Report",
        "",
        f"- alignment_dir: `{result['alignment_dir']}`",
        f"- sample_count: {result['sample_count']}",
        f"- single_dr_gate_rows: {summary.get('single_dr_gate_rows', '0')}",
        f"- single_dr_primary_rows: {summary.get('single_dr_primary_rows', '0')}",
        "",
        "## Gate Candidates",
        "",
        ("| Candidate | Affected primary rows | ISTD rows | Recommended action |"),
        "|---|---:|---:|---|",
    ]
    for candidate in result["gate_candidates"]:
        lines.append(
            "| "
            f"{candidate['gate_candidate_id']} | "
            f"{candidate['affected_primary_rows']} | "
            f"{candidate['affected_istd_rows']} | "
            f"{candidate['recommended_action']} |"
        )
    lines.extend(
        [
            "",
            "## Top Families",
            "",
            "| Family | Class | q_detected | q_rescue | rescue_fraction | ISTD |",
            "|---|---|---:|---:|---:|---|",
        ],
    )
    for family in result["families"][:30]:
        lines.append(
            "| "
            f"{family['feature_family_id']} | "
            f"{family['risk_classification']} | "
            f"{family['q_detected']} | "
            f"{family['q_rescue']} | "
            f"{family['rescue_fraction']} | "
            f"{family['targeted_istd_labels']} |"
        )
    lines.append("")
    return "\n".join(lines)
