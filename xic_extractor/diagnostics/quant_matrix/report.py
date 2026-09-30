from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from xic_extractor.alignment.quant_matrix_version import (
    CELL_PROVENANCE_COLUMNS,
    ROW_SUMMARY_COLUMNS,
    SOURCE_SUMMARY_COLUMNS,
)
from xic_extractor.tabular_io import (
    file_sha256,
    read_tsv_required,
    split_semicolon_labels,
    write_tsv,
)

QUANT_MATRIX_REVIEW_SCHEMA = "quant_matrix_review_report"

_AUTHORITY_NOTICE = (
    "shadow_review surface only; does not mutate quant matrix, ProductWriter, "
    "workbook, GUI, selected peak, selected area, or counted detection behavior."
)
_TRACE_NOTICE = (
    "Trace convention: gaussian smoothed trace is primary; "
    "raw trace is auxiliary."
)

QUANT_MATRIX_REVIEW_ROW_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "source_feature_family_ids",
    "sample_stem",
    "cell_status",
    "matrix_value",
    "report_authority",
    "write_authority",
    "acceptance_decision",
    "acceptance_basis",
    "truth_status",
    "prevalence_review_state",
    "prevalence_flags",
    "triggered_risk_rule_ids",
    "closure_rule_ids",
    "trace_basis",
    "boundary_status",
    "manual_negative_closure",
    "doublet_closure",
    "doublet_status",
    "reference_side",
    "doublet_allowed",
    "quant_value_source",
    "matrix_area_source",
    "decision_reason",
    "next_evidence_needed",
    "source_artifact_relpath",
    "source_artifact_sha256",
    "source_row_sha256",
    "doublet_source_relpath",
    "doublet_source_sha256",
    "manifest_sha256",
)

_MANIFEST_REVIEW_COLUMNS = (
    "peak_hypothesis_id",
    "sample_stem",
    "acceptance_decision",
    "acceptance_basis",
    "truth_status",
    "quant_value_source",
    "matrix_area_source",
    "prevalence_flags",
    "triggered_risk_rule_ids",
    "closure_rule_ids",
    "decision_reason",
    "next_evidence_needed",
    "doublet_status",
    "reference_side",
    "doublet_allowed",
    "doublet_source_relpath",
    "doublet_source_sha256",
    "source_artifact_relpath",
    "source_artifact_sha256",
    "source_row_sha256",
    "manifest_sha256",
)


def build_quant_matrix_review_report(
    *,
    quant_matrix_tsv: Path,
    cell_provenance_tsv: Path,
    row_summary_tsv: Path,
    source_summary_tsv: Path,
    output_dir: Path,
    html_path: Path | None = None,
) -> Mapping[str, Path]:
    cell_rows = read_tsv_required(cell_provenance_tsv, CELL_PROVENANCE_COLUMNS)
    row_summary_rows = read_tsv_required(row_summary_tsv, ROW_SUMMARY_COLUMNS)
    source_summary_rows = read_tsv_required(source_summary_tsv, SOURCE_SUMMARY_COLUMNS)
    source_summary = _single_row(source_summary_rows, source_summary_tsv)
    manifest_path = _resolve_source_path(
        source_summary.get("production_acceptance_manifest_tsv", ""),
        source_summary_tsv.parent,
    )
    _validate_manifest_file_hash(manifest_path, source_summary)
    manifest_rows = read_tsv_required(manifest_path, _MANIFEST_REVIEW_COLUMNS)

    row_summary_by_peak = {
        row["peak_hypothesis_id"]: row for row in row_summary_rows if row
    }
    manifest_by_cell = _manifest_by_cell(manifest_rows)
    review_rows = _review_rows(
        cell_rows=cell_rows,
        row_summary_by_peak=row_summary_by_peak,
        manifest_by_cell=manifest_by_cell,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    review_rows_tsv = output_dir / "quant_matrix_review_rows.tsv"
    summary_json = output_dir / "quant_matrix_review_summary.json"
    html_path = html_path or output_dir / "quant_matrix_review_report.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)

    write_tsv(
        review_rows_tsv,
        review_rows,
        QUANT_MATRIX_REVIEW_ROW_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    summary = _summary(
        review_rows,
        input_artifacts={
            "quant_matrix_tsv": str(quant_matrix_tsv),
            "cell_provenance_tsv": str(cell_provenance_tsv),
            "row_summary_tsv": str(row_summary_tsv),
            "source_summary_tsv": str(source_summary_tsv),
            "production_acceptance_manifest_tsv": str(manifest_path),
        },
    )
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    html_path.write_text(_render_html(review_rows, summary), encoding="utf-8")
    return {
        "review_rows": review_rows_tsv,
        "summary_json": summary_json,
        "html": html_path,
    }


def _single_row(rows: Sequence[Mapping[str, str]], path: Path) -> Mapping[str, str]:
    if len(rows) != 1:
        raise ValueError(f"{path}: expected exactly one source summary row")
    return rows[0]


def _resolve_source_path(value: str, base_dir: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _validate_manifest_file_hash(
    manifest_path: Path,
    source_summary: Mapping[str, str],
) -> None:
    expected = source_summary.get("production_acceptance_manifest_sha256", "")
    actual = file_sha256(manifest_path)
    if expected != actual:
        raise ValueError("production_acceptance_manifest_sha256 mismatch")


def _manifest_by_cell(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    result: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        key = (row.get("peak_hypothesis_id", ""), row.get("sample_stem", ""))
        if not all(key):
            continue
        if key in result:
            raise ValueError(f"duplicate manifest row: {key[0]}/{key[1]}")
        result[key] = row
    return result


def _review_rows(
    *,
    cell_rows: Sequence[Mapping[str, str]],
    row_summary_by_peak: Mapping[str, Mapping[str, str]],
    manifest_by_cell: Mapping[tuple[str, str], Mapping[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for cell in cell_rows:
        peak_hypothesis_id = cell.get("peak_hypothesis_id", "")
        sample_stem = cell.get("sample_stem", "")
        summary = row_summary_by_peak.get(peak_hypothesis_id, {})
        manifest = _manifest_for_cell(cell, manifest_by_cell)
        prevalence_flags = _first_non_empty(
            manifest.get("prevalence_flags", ""),
            summary.get("prevalence_flags", ""),
        )
        rows.append(
            {
                "schema_version": QUANT_MATRIX_REVIEW_SCHEMA,
                "peak_hypothesis_id": peak_hypothesis_id,
                "source_feature_family_ids": cell.get(
                    "source_feature_family_ids",
                    "",
                ),
                "sample_stem": sample_stem,
                "cell_status": cell.get("cell_status", ""),
                "matrix_value": cell.get("matrix_value", ""),
                "report_authority": "review_only",
                "write_authority": cell.get("write_authority", ""),
                "acceptance_decision": _first_non_empty(
                    manifest.get("acceptance_decision", ""),
                    cell.get("acceptance_decision", ""),
                ),
                "acceptance_basis": _first_non_empty(
                    manifest.get("acceptance_basis", ""),
                    cell.get("acceptance_basis", ""),
                ),
                "truth_status": _first_non_empty(
                    manifest.get("truth_status", ""),
                    cell.get("truth_status", ""),
                ),
                "prevalence_review_state": _prevalence_review_state(
                    prevalence_flags,
                    cell.get("cell_status", ""),
                ),
                "prevalence_flags": prevalence_flags,
                "triggered_risk_rule_ids": manifest.get(
                    "triggered_risk_rule_ids",
                    "",
                ),
                "closure_rule_ids": manifest.get("closure_rule_ids", ""),
                "trace_basis": _trace_basis(
                    manifest.get("quant_value_source", ""),
                    manifest.get("matrix_area_source", ""),
                    cell.get("cell_status", ""),
                ),
                "boundary_status": _boundary_status(
                    manifest.get("matrix_area_source", ""),
                    cell.get("cell_status", ""),
                ),
                "manual_negative_closure": _manual_negative_closure(
                    manifest.get("truth_status", ""),
                    cell.get("cell_status", ""),
                ),
                "doublet_closure": _doublet_closure(manifest),
                "doublet_status": manifest.get("doublet_status", ""),
                "reference_side": manifest.get("reference_side", ""),
                "doublet_allowed": manifest.get("doublet_allowed", ""),
                "quant_value_source": _first_non_empty(
                    manifest.get("quant_value_source", ""),
                    cell.get("quant_value_source", ""),
                ),
                "matrix_area_source": _first_non_empty(
                    manifest.get("matrix_area_source", ""),
                    cell.get("matrix_area_source", ""),
                ),
                "decision_reason": manifest.get("decision_reason", ""),
                "next_evidence_needed": manifest.get("next_evidence_needed", ""),
                "source_artifact_relpath": _first_non_empty(
                    manifest.get("source_artifact_relpath", ""),
                    cell.get("source_artifact_relpath", ""),
                ),
                "source_artifact_sha256": _first_non_empty(
                    manifest.get("source_artifact_sha256", ""),
                    cell.get("source_artifact_sha256", ""),
                ),
                "source_row_sha256": _first_non_empty(
                    manifest.get("source_row_sha256", ""),
                    cell.get("source_row_sha256", ""),
                ),
                "doublet_source_relpath": manifest.get("doublet_source_relpath", ""),
                "doublet_source_sha256": manifest.get("doublet_source_sha256", ""),
                "manifest_sha256": _first_non_empty(
                    manifest.get("manifest_sha256", ""),
                    cell.get("manifest_sha256", ""),
                ),
            }
        )
    return rows


def _manifest_for_cell(
    cell: Mapping[str, str],
    manifest_by_cell: Mapping[tuple[str, str], Mapping[str, str]],
) -> Mapping[str, str]:
    if cell.get("cell_status") != "accepted_backfill":
        return {}
    key = (cell.get("peak_hypothesis_id", ""), cell.get("sample_stem", ""))
    manifest = manifest_by_cell.get(key)
    if manifest is None:
        raise ValueError(
            f"manifest row missing for accepted cell: {key[0]}/{key[1]}",
        )
    _validate_manifest_join(cell, manifest)
    return manifest


def _validate_manifest_join(
    cell: Mapping[str, str],
    manifest: Mapping[str, str],
) -> None:
    label = f"{cell.get('peak_hypothesis_id', '')}/{cell.get('sample_stem', '')}"
    if cell.get("manifest_sha256") != manifest.get("manifest_sha256"):
        raise ValueError(f"{label}: manifest_sha256 mismatch")
    if cell.get("source_row_sha256") != manifest.get("source_row_sha256"):
        raise ValueError(f"{label}: source_row_sha256 mismatch")
    if manifest.get("acceptance_decision") not in {
        "accept_basic_backfill",
        "accept_strict_backfill",
    }:
        raise ValueError(f"{label}: manifest row is not authoritative")
    if (
        manifest.get("write_authority") != "TRUE"
        or manifest.get("matrix_write_allowed") != "TRUE"
        or manifest.get("shadow_only") != "FALSE"
    ):
        raise ValueError(f"{label}: manifest row is not authoritative")


def _first_non_empty(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def _prevalence_review_state(prevalence_flags: str, cell_status: str) -> str:
    if cell_status != "accepted_backfill":
        return "row_context_only" if prevalence_flags else "none"
    report_only_flags = {"low_seed_support", "high_backfill_dependency"}
    return (
        "report_only_risk"
        if report_only_flags & set(split_semicolon_labels(prevalence_flags))
        else "none"
    )


def _trace_basis(
    quant_value_source: str,
    matrix_area_source: str,
    cell_status: str,
) -> str:
    if cell_status == "detected":
        return "input_quant_matrix_detected"
    joined = f"{quant_value_source};{matrix_area_source}"
    if "gaussian_smoothed" in joined:
        return "gaussian_smoothed_trace_primary_raw_trace_auxiliary"
    return "source_reported_trace_basis"


def _boundary_status(matrix_area_source: str, cell_status: str) -> str:
    if cell_status == "detected":
        return "input_quant_matrix_detected"
    if matrix_area_source:
        return matrix_area_source
    return "not_reported"


def _manual_negative_closure(truth_status: str, cell_status: str) -> str:
    if cell_status == "detected":
        return "not_applicable"
    if truth_status == "manual_negative":
        return "manual_negative_hard_stop_present"
    return "hard_stop_not_triggered"


def _doublet_closure(manifest: Mapping[str, str]) -> str:
    if not manifest:
        return "not_applicable"
    blocked_sides = {"right", "unclear", "unresolved"}
    if manifest.get("reference_side") in blocked_sides:
        return "blocked"
    if manifest.get("doublet_allowed") == "TRUE":
        return "allowed"
    return "not_allowed"


def _summary(
    rows: Sequence[Mapping[str, str]],
    *,
    input_artifacts: Mapping[str, str],
) -> dict[str, object]:
    accepted_backfill_count = sum(
        1 for row in rows if row.get("cell_status") == "accepted_backfill"
    )
    detected_count = sum(1 for row in rows if row.get("cell_status") == "detected")
    report_only_risk_count = sum(
        1 for row in rows if row.get("prevalence_review_state") == "report_only_risk"
    )
    return {
        "schema_version": QUANT_MATRIX_REVIEW_SCHEMA,
        "validation_label": "shadow_review",
        "row_count": len(rows),
        "accepted_backfill_count": accepted_backfill_count,
        "detected_count": detected_count,
        "report_only_risk_count": report_only_risk_count,
        "input_artifacts": dict(input_artifacts),
        "authority_statement": (
            "Report only: does not mutate quant matrix, ProductWriter, "
            "workbook, GUI, selected peaks, selected areas, or counted detections."
        ),
    }


def _render_html(
    review_rows: Sequence[Mapping[str, str]],
    summary: Mapping[str, object],
) -> str:
    rows_html = "\n".join(_render_row(row) for row in review_rows)
    artifact_items = "\n".join(
        "<li><code>"
        + _escape(label)
        + "</code>: "
        + _escape(value)
        + "</li>"
        for label, value in _summary_input_artifacts(summary).items()
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <title>QuantMatrixVersion Review Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #d8dee4; padding: 6px 8px; text-align: left; }}
    th {{ background: #f6f8fa; }}
    code {{ font-family: Consolas, monospace; }}
    .notice {{ border: 1px solid #8c959f; padding: 10px 12px; margin: 12px 0; }}
  </style>
</head>
<body>
  <h1>QuantMatrixVersion Review Report</h1>
  <p class="notice">{_escape(_AUTHORITY_NOTICE)}</p>
  <p class="notice">{_escape(_TRACE_NOTICE)}</p>
  <section>
    <h2>Summary</h2>
    <ul>
      <li>accepted_backfill: {_escape(summary["accepted_backfill_count"])}</li>
      <li>detected: {_escape(summary["detected_count"])}</li>
      <li>report_only_risk: {_escape(summary["report_only_risk_count"])}</li>
    </ul>
  </section>
  <section>
    <h2>Input Artifacts</h2>
    <ul>{artifact_items}</ul>
  </section>
  <section>
    <h2>Review Rows</h2>
    <table>
      <thead>
        <tr>
          <th scope="col">peak_hypothesis_id</th>
          <th scope="col">source_feature_family_ids</th>
          <th scope="col">sample_stem</th>
          <th scope="col">cell_status</th>
          <th scope="col">prevalence_flags</th>
          <th scope="col">trace_basis</th>
          <th scope="col">manual_negative_closure</th>
          <th scope="col">doublet</th>
          <th scope="col">source</th>
          <th scope="col">manifest_sha256</th>
        </tr>
      </thead>
      <tbody>
{rows_html}
      </tbody>
    </table>
  </section>
</body>
</html>
"""


def _render_row(row: Mapping[str, str]) -> str:
    doublet = (
        f"{row.get('doublet_status', '')}; "
        f"reference={row.get('reference_side', '')}; "
        f"allowed={row.get('doublet_allowed', '')}"
    )
    source = (
        f"{row.get('source_artifact_relpath', '')}; "
        f"{row.get('source_artifact_sha256', '')}; "
        f"{row.get('doublet_source_relpath', '')}"
    )
    return (
        "        <tr>"
        f"<td>{_escape(row.get('peak_hypothesis_id', ''))}</td>"
        f"<td>{_escape(row.get('source_feature_family_ids', ''))}</td>"
        f"<td>{_escape(row.get('sample_stem', ''))}</td>"
        f"<td>{_escape(row.get('cell_status', ''))}</td>"
        f"<td>{_escape(row.get('prevalence_flags', ''))}</td>"
        f"<td>{_escape(row.get('trace_basis', ''))}</td>"
        f"<td>{_escape(_manual_negative_label(row))}</td>"
        f"<td>{_escape(doublet)}</td>"
        f"<td>{_escape(source)}</td>"
        f"<td>{_escape(row.get('manifest_sha256', ''))}</td>"
        "</tr>"
    )


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _summary_input_artifacts(summary: Mapping[str, object]) -> Mapping[str, object]:
    value = summary.get("input_artifacts", {})
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _manual_negative_label(row: Mapping[str, str]) -> str:
    closure = row.get("manual_negative_closure", "")
    if closure == "hard_stop_not_triggered":
        return "manual_negative hard stop not triggered"
    return closure
