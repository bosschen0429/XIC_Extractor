"""Diagnostic-only shadow policy report for retained backfill cells."""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from xic_extractor.diagnostics.backfill_decision_explanation import (
    BackfillDecisionExplanation,
    decision_explanation,
)
from xic_extractor.diagnostics.backfill_overlay import (
    selected_overlay_row as select_backfill_overlay_row,
)
from xic_extractor.diagnostics.diagnostic_io import (
    file_sha256,
    optional_float,
    read_tsv_required,
    rows_by_text_field,
    split_semicolon_labels,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "backfill_shadow_policy"
SUPPORT_OVERLAY_VERDICT = "ms1_shape_supports_family_backfill"
_URL_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_DANGEROUS_SCHEMES = {"javascript", "data", "vbscript"}

ShadowPolicyDecision = Literal[
    "fill_now",
    "would_fill_under_ms1_rt_policy",
    "needs_ms1_same_peak_evidence",
    "blocked",
]

BACKFILL_SHADOW_POLICY_COLUMNS = (
    "schema_version",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
    "current_product_cell_state",
    "shadow_policy_decision",
    "decision_reason",
    "production_gap",
    "diagnostic_authority",
    "seed_mz",
    "seed_rt",
    "seed_rt_window",
    "detected_cell_count",
    "rescued_cell_count",
    "cell_status",
    "primary_matrix_area_source",
    "rt_delta_sec",
    "evidence_gate_status",
    "overlay_family_verdict",
    "own_max_shape_supported_fraction",
    "absolute_trace_apex_cluster_fraction",
    "support_components",
    "blockers",
    "missing_evidence",
    "backfill_evidence_reason",
    "candidate_ms2_product_authority_status",
    "overlay_png_path",
)

CELL_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "primary_matrix_area",
    "primary_matrix_area_source",
    "gap_fill_state",
)
GATE_REQUIRED_COLUMNS = (
    "feature_family_id",
    "seed_group_id",
    "seed_group_basis",
    "seed_mz",
    "seed_rt",
    "suggested_rt_min",
    "suggested_rt_max",
    "evidence_gate_status",
    "detected_cell_count",
    "rescued_cell_count",
    "support_components",
    "challenge_blockers",
    "missing_evidence",
    "overlay_family_verdict",
    "overlay_png_path",
    "seed_source_samples",
)
OVERLAY_REQUIRED_COLUMNS = (
    "feature_family_id",
    "family_verdict",
    "png_path",
)


@dataclass(frozen=True)
class BackfillShadowPolicyIndex:
    rows: tuple[dict[str, str], ...]
    summary: dict[str, object]


@dataclass(frozen=True)
class BackfillShadowPolicyOutputs:
    tsv: Path
    json: Path
    html: Path


def run_backfill_shadow_policy_report(
    *,
    alignment_cells_tsv: Path,
    retained_gate_tsv: Path,
    output_dir: Path,
    alignment_matrix_tsv: Path | None = None,
    overlay_batch_summary_tsvs: Sequence[Path] = (),
    source_run_id: str = "",
) -> BackfillShadowPolicyOutputs:
    cell_rows = read_tsv_required(alignment_cells_tsv, CELL_REQUIRED_COLUMNS)
    gate_rows = read_tsv_required(retained_gate_tsv, GATE_REQUIRED_COLUMNS)
    overlay_rows: list[dict[str, str]] = []
    for path in overlay_batch_summary_tsvs:
        overlay_rows.extend(read_tsv_required(path, OVERLAY_REQUIRED_COLUMNS))
    matrix_sha256 = (
        file_sha256(alignment_matrix_tsv)
        if alignment_matrix_tsv is not None and alignment_matrix_tsv.exists()
        else ""
    )
    index = build_backfill_shadow_policy_index(
        cell_rows=cell_rows,
        retained_gate_rows=gate_rows,
        overlay_rows=overlay_rows,
        source_run_id=source_run_id,
        source_cell_sha256=file_sha256(alignment_cells_tsv),
        source_gate_sha256=file_sha256(retained_gate_tsv),
        source_matrix_sha256=matrix_sha256,
        source_overlay_artifacts=tuple(
            str(path) for path in overlay_batch_summary_tsvs
        ),
    )
    return write_backfill_shadow_policy_outputs(output_dir, index)


def build_backfill_shadow_policy_index(
    *,
    cell_rows: Iterable[Mapping[str, str]],
    retained_gate_rows: Iterable[Mapping[str, str]],
    overlay_rows: Iterable[Mapping[str, str]] = (),
    source_run_id: str = "",
    source_cell_sha256: str = "",
    source_gate_sha256: str = "",
    source_matrix_sha256: str = "",
    source_overlay_artifacts: Sequence[str] = (),
) -> BackfillShadowPolicyIndex:
    cells_by_key = {
        (
            text_value(row.get("feature_family_id")),
            text_value(row.get("sample_stem")),
        ): row
        for row in cell_rows
        if text_value(row.get("feature_family_id"))
        and text_value(row.get("sample_stem"))
    }
    overlays_by_family = _group_by_family(overlay_rows)
    rows: list[dict[str, str]] = []
    for gate_row in retained_gate_rows:
        family_id = text_value(gate_row.get("feature_family_id"))
        seed_group_id = text_value(gate_row.get("seed_group_id"))
        if not family_id or not seed_group_id:
            continue
        overlay_row = _selected_overlay_row(
            overlays_by_family.get(family_id, ()),
            seed_group_id=seed_group_id,
        )
        for sample in split_semicolon_labels(gate_row.get("seed_source_samples")):
            cell = cells_by_key.get((family_id, sample), {})
            rows.append(
                _shadow_row(
                    gate_row=gate_row,
                    cell=cell,
                    sample_stem=sample,
                    overlay_row=overlay_row,
                ),
            )

    rows = sorted(rows, key=_row_sort_key)
    summary = _summary(
        rows,
        source_run_id=source_run_id,
        source_cell_sha256=source_cell_sha256,
        source_gate_sha256=source_gate_sha256,
        source_matrix_sha256=source_matrix_sha256,
        source_overlay_artifacts=source_overlay_artifacts,
    )
    return BackfillShadowPolicyIndex(rows=tuple(rows), summary=summary)


def write_backfill_shadow_policy_outputs(
    output_dir: Path,
    index: BackfillShadowPolicyIndex,
) -> BackfillShadowPolicyOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = output_dir / "backfill_shadow_policy_cells.tsv"
    json_path = output_dir / "backfill_shadow_policy_summary.json"
    html_path = output_dir / "backfill_shadow_policy_report.html"
    write_tsv(
        tsv_path,
        index.rows,
        BACKFILL_SHADOW_POLICY_COLUMNS,
        lineterminator="\n",
    )
    json_path.write_text(
        json.dumps(index.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    html_path.write_text(_render_html(index), encoding="utf-8")
    return BackfillShadowPolicyOutputs(tsv=tsv_path, json=json_path, html=html_path)


def _shadow_row(
    *,
    gate_row: Mapping[str, str],
    cell: Mapping[str, str],
    sample_stem: str,
    overlay_row: Mapping[str, str],
) -> dict[str, str]:
    current_state = _current_product_cell_state(cell)
    explanation = _shadow_decision(
        gate_row=gate_row,
        cell=cell,
        overlay_row=overlay_row,
        current_product_cell_state=current_state,
    )
    rt_start = text_value(gate_row.get("suggested_rt_min"))
    rt_end = text_value(gate_row.get("suggested_rt_max"))
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_family_id": text_value(gate_row.get("feature_family_id")),
        "seed_group_id": text_value(gate_row.get("seed_group_id")),
        "sample_stem": sample_stem,
        "current_product_cell_state": current_state,
        "shadow_policy_decision": explanation.decision,
        "decision_reason": explanation.reason_text,
        "production_gap": explanation.production_gap,
        "diagnostic_authority": "diagnostic_only",
        "seed_mz": text_value(gate_row.get("seed_mz")),
        "seed_rt": text_value(gate_row.get("seed_rt")),
        "seed_rt_window": f"{rt_start}-{rt_end}" if rt_start or rt_end else "",
        "detected_cell_count": text_value(gate_row.get("detected_cell_count")),
        "rescued_cell_count": text_value(gate_row.get("rescued_cell_count")),
        "cell_status": text_value(cell.get("status")),
        "primary_matrix_area_source": text_value(
            cell.get("primary_matrix_area_source"),
        ),
        "rt_delta_sec": text_value(cell.get("rt_delta_sec")),
        "evidence_gate_status": text_value(gate_row.get("evidence_gate_status")),
        "overlay_family_verdict": text_value(gate_row.get("overlay_family_verdict")),
        "own_max_shape_supported_fraction": text_value(
            overlay_row.get("absolute_own_max_shape_supported_fraction"),
        ),
        "absolute_trace_apex_cluster_fraction": text_value(
            overlay_row.get("absolute_trace_apex_cluster_fraction"),
        ),
        "support_components": text_value(gate_row.get("support_components")),
        "blockers": text_value(gate_row.get("challenge_blockers")),
        "missing_evidence": text_value(gate_row.get("missing_evidence")),
        "backfill_evidence_reason": text_value(cell.get("backfill_evidence_reason")),
        "candidate_ms2_product_authority_status": text_value(
            cell.get("backfill_candidate_ms2_product_authority_status"),
        ),
        "overlay_png_path": (
            text_value(overlay_row.get("png_path"))
            or text_value(gate_row.get("overlay_png_path"))
        ),
    }


def _shadow_decision(
    *,
    gate_row: Mapping[str, str],
    cell: Mapping[str, str],
    overlay_row: Mapping[str, str],
    current_product_cell_state: str,
) -> BackfillDecisionExplanation:
    if current_product_cell_state == "filled_now":
        return decision_explanation("fill_now", "product_already_writes_rescue")
    if text_value(cell.get("status")) != "backfilled":
        return decision_explanation("blocked", "rescued_cell_missing_or_not_rescued")
    if optional_float(gate_row.get("detected_cell_count")) in (None, 0.0):
        return decision_explanation("blocked", "detected_seed_missing")
    missing = split_semicolon_labels(gate_row.get("missing_evidence"))
    if "missing_seed_provenance" in missing:
        return decision_explanation("blocked", "missing_seed_provenance")
    blockers = split_semicolon_labels(gate_row.get("challenge_blockers"))
    if blockers:
        return decision_explanation("blocked", "visual_conflict_or_review_required")
    if missing:
        return decision_explanation("blocked", "missing_evidence")
    if (
        text_value(gate_row.get("evidence_gate_status")) == "visual_support"
        and text_value(gate_row.get("overlay_family_verdict"))
        == SUPPORT_OVERLAY_VERDICT
    ):
        own_max_fraction = optional_float(
            overlay_row.get("absolute_own_max_shape_supported_fraction"),
        )
        if own_max_fraction is None:
            return decision_explanation("blocked", "own_max_shape_metric_missing")
        if own_max_fraction <= 0.5:
            return decision_explanation(
                "blocked",
                "own_max_shape_at_or_below_threshold",
            )
        return decision_explanation(
            "would_fill_under_ms1_rt_policy",
            "ms1_rt_shadow_supported",
        )
    return decision_explanation(
        "needs_ms1_same_peak_evidence",
        "ms1_rt_evidence_inconclusive_or_unlinked",
        production_gap="needs_ms1_same_peak_evidence",
    )


def _current_product_cell_state(cell: Mapping[str, str]) -> str:
    if text_value(cell.get("status")) != "backfilled":
        return "not_rescued"
    if text_value(cell.get("gap_fill_state")) == "gap_fill_rescued":
        return "filled_now"
    return "review_only"

def _summary(
    rows: Sequence[Mapping[str, str]],
    *,
    source_run_id: str,
    source_cell_sha256: str,
    source_gate_sha256: str,
    source_matrix_sha256: str,
    source_overlay_artifacts: Sequence[str],
) -> dict[str, object]:
    decision_counts = Counter(row["shadow_policy_decision"] for row in rows)
    production_gap_counts = Counter(
        row["production_gap"] for row in rows if row["production_gap"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_label": "diagnostic_only",
        "source_run_id": source_run_id,
        "row_count": len(rows),
        "family_count": len({row["feature_family_id"] for row in rows}),
        "decision_counts": dict(sorted(decision_counts.items())),
        "production_gap_counts": dict(sorted(production_gap_counts.items())),
        "matrix_contract_changed": False,
        "product_behavior_changed": False,
        "source_cell_sha256": source_cell_sha256,
        "source_gate_sha256": source_gate_sha256,
        "source_matrix_sha256": source_matrix_sha256,
        "source_overlay_artifacts": ";".join(source_overlay_artifacts),
    }


def _render_html(index: BackfillShadowPolicyIndex) -> str:
    rows_html = "\n".join(_render_html_row(row) for row in index.rows)
    summary = index.summary
    row_count = _h(summary.get("row_count"))
    family_count = _h(summary.get("family_count"))
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <title>Backfill MS1+RT Shadow Policy</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --text: #14202b;
      --muted: #526171;
      --line: #cbd7e4;
      --panel: #ffffff;
      --accent: #1d6fa5;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 "Segoe UI", Arial, sans-serif;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 24px 48px;
    }}
    h1 {{
      margin: 0 0 14px;
      font-size: 24px;
    }}
    .summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 16px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-left: 4px solid var(--accent);
      border-radius: 6px;
      padding: 9px 12px;
      min-width: 130px;
    }}
    .metric b {{
      display: block;
      font-size: 16px;
    }}
    .note {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      color: var(--muted);
      margin-bottom: 16px;
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
    }}
    table {{
      width: 100%;
      min-width: 1040px;
      border-collapse: collapse;
    }}
    th, td {{
      border-bottom: 1px solid #e2e9f0;
      padding: 8px 10px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #e8eef5;
      font-weight: 700;
    }}
    .muted {{
      color: var(--muted);
      font-size: 12px;
    }}
    .badge {{
      display: inline-block;
      border: 1px solid var(--line);
      border-left: 4px solid var(--accent);
      border-radius: 5px;
      padding: 2px 6px;
      font-weight: 700;
      background: #fff;
    }}
  </style>
</head>
<body>
<main>
  <h1>Backfill MS1+RT shadow policy</h1>
  <div class="summary">
    <div class="metric"><span>rows</span><b>{row_count}</b></div>
    <div class="metric"><span>families</span><b>{family_count}</b></div>
    <div class="metric"><span>authority</span><b>diagnostic_only</b></div>
    <div class="metric"><span>product changed</span><b>FALSE</b></div>
  </div>
  <div class="note">
    這份報表只評估如果採用 MS1 own-max + RT 的 shadow policy，
    哪些 rescued cells 會成為候選；它不修改 alignment matrix、
    cells、review TSV、workbook 或 production decisions。
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>family</th>
          <th>sample</th>
          <th>current</th>
          <th>shadow decision</th>
          <th>reason / gap</th>
          <th>seed RT</th>
          <th>own-max</th>
          <th>evidence</th>
          <th>overlay</th>
        </tr>
      </thead>
      <tbody>
{rows_html}
      </tbody>
    </table>
  </div>
</main>
</body>
</html>
"""


def _render_html_row(row: Mapping[str, str]) -> str:
    overlay = text_value(row.get("overlay_png_path"))
    overlay_href = _safe_href(overlay)
    if overlay_href:
        overlay_html = f'<a href="{_attr(overlay_href)}">PNG</a>'
    elif overlay:
        overlay_html = (
            f'<span class="muted" title="{_attr(_remove_control_chars(overlay))}">'
            "none</span>"
        )
    else:
        overlay_html = '<span class="muted">none</span>'
    reason = text_value(row.get("decision_reason"))
    gap = text_value(row.get("production_gap"))
    blockers = text_value(row.get("blockers"))
    missing = text_value(row.get("missing_evidence"))
    evidence = blockers or missing or text_value(row.get("support_components"))
    family = _h(row.get("feature_family_id"))
    seed_group = _h(row.get("seed_group_id"))
    sample = _h(row.get("sample_stem"))
    current = _h(row.get("current_product_cell_state"))
    decision = _h(row.get("shadow_policy_decision"))
    seed_rt = _h(row.get("seed_rt"))
    seed_window = _h(row.get("seed_rt_window"))
    own_max = _h(row.get("own_max_shape_supported_fraction"))
    apex_cluster = _h(row.get("absolute_trace_apex_cluster_fraction"))
    return f"""        <tr>
          <td><b>{family}</b><div class="muted">{seed_group}</div></td>
          <td>{sample}</td>
          <td>{current}</td>
          <td><span class="badge">{decision}</span></td>
          <td>{_h(reason)}<div class="muted">{_h(gap)}</div></td>
          <td>{seed_rt}<div class="muted">{seed_window}</div></td>
          <td>{own_max}<div class="muted">apex {apex_cluster}</div></td>
          <td>{_h(evidence)}</td>
          <td>{overlay_html}</td>
        </tr>"""


def _selected_overlay_row(
    rows: Sequence[Mapping[str, str]],
    *,
    seed_group_id: str,
) -> Mapping[str, str]:
    return select_backfill_overlay_row(
        rows,
        seed_group_id=seed_group_id,
        support_verdict=SUPPORT_OVERLAY_VERDICT,
    )


def _group_by_family(
    rows: Iterable[Mapping[str, str]],
) -> dict[str, tuple[Mapping[str, str], ...]]:
    return rows_by_text_field(rows, "feature_family_id")


def _row_sort_key(row: Mapping[str, str]) -> tuple[int, str, str, str]:
    priority = {
        "would_fill_under_ms1_rt_policy": 0,
        "needs_ms1_same_peak_evidence": 1,
        "blocked": 2,
        "fill_now": 3,
    }.get(text_value(row.get("shadow_policy_decision")), 9)
    return (
        priority,
        text_value(row.get("feature_family_id")),
        text_value(row.get("seed_group_id")),
        text_value(row.get("sample_stem")),
    )


def _h(value: object) -> str:
    return html.escape(text_value(value))


def _attr(value: object) -> str:
    return html.escape(text_value(value), quote=True)


def _safe_href(value: object) -> str:
    sanitized = _remove_control_chars(text_value(value))
    scheme = _detected_url_scheme(sanitized)
    if scheme in _DANGEROUS_SCHEMES:
        return ""
    return sanitized


def _detected_url_scheme(value: str) -> str:
    compact = "".join(ch for ch in text_value(value) if ord(ch) > 32)
    match = _URL_SCHEME_RE.match(compact)
    if not match:
        return ""
    scheme = match.group(1).lower()
    if len(scheme) == 1 and len(compact) >= 3 and compact[1:3] in {":\\", ":/"}:
        return ""
    return scheme


def _remove_control_chars(value: str) -> str:
    return "".join(ch for ch in value if ord(ch) >= 32)
