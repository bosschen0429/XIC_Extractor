"""Output writers for low MS1 coverage review diagnostics."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.diagnostics.diagnostic_io import (
    format_diagnostic_value,
)
from tools.diagnostics.diagnostic_io import (
    write_tsv as write_diagnostic_tsv,
)

_format_value = format_diagnostic_value


def write_outputs(output_dir: Path, result: Mapping[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = list(result["summary"])
    detail_rows = list(result["rows"])
    apex_aware_queue = list(result["selected_apex_overlay_queue"])
    seed_aware_queue = list(result["seed_overlay_queue"])
    _write_tsv(
        output_dir / "low_ms1_assessable_coverage_summary.tsv",
        summary_rows,
        _summary_fields(),
    )
    _write_tsv(
        output_dir / "low_ms1_assessable_coverage_rows.tsv",
        detail_rows,
        _detail_fields(),
    )
    _write_tsv(
        output_dir / "low_ms1_assessable_coverage_selected_apex_overlay_queue.tsv",
        apex_aware_queue,
        _apex_aware_queue_fields(),
    )
    _write_tsv(
        output_dir / "low_ms1_assessable_coverage_seed_overlay_queue.tsv",
        seed_aware_queue,
        _seed_aware_queue_fields(),
    )
    (output_dir / "low_ms1_assessable_coverage.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_markdown(output_dir / "low_ms1_assessable_coverage.md", result)


def _write_tsv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    fieldnames: Sequence[str],
) -> None:
    write_diagnostic_tsv(
        path,
        rows,
        fieldnames,
        formatter=format_diagnostic_value,
    )


def _write_markdown(path: Path, result: Mapping[str, Any]) -> None:
    summary_rows = list(result["summary"])
    apex_aware_queue = list(result["selected_apex_overlay_queue"])
    counts = Counter(str(row["root_cause_bucket"]) for row in summary_rows)
    lines = [
        "# Low MS1 Assessable Coverage Audit",
        "",
        "## Verdict",
        "",
        f"- Reviewed families: `{len(summary_rows)}`",
        f"- Root-cause buckets: `{_format_counts(counts) or 'none'}`",
        f"- Selected-apex overlay queue: `{len(apex_aware_queue)}`",
        f"- Seed-aware overlay queue: `{len(result['seed_overlay_queue'])}`",
        "",
        "## Families",
        "",
        (
            "| Family | Bucket | Assessable | Apex in window | Zero trace in window "
            "| Event clusters | Seed evidence | Recommendation |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary_rows:
        lines.append(
            f"| `{row['feature_family_id']}` "
            f"| `{row['root_cause_bucket']}` "
            f"| {row['assessable_fraction']:.3g} "
            f"| {row['selected_apex_in_window_fraction']:.3g} "
            f"| {row['zero_trace_inside_window_fraction']:.3g} "
            f"| {row['event_cluster_count']} "
            f"| `{row['seed_evidence_bucket']}` "
            f"| {row['recommended_next_action']} |",
        )
    if not summary_rows:
        lines.append("| none |  |  |  |  |  |  |  |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _summary_fields() -> tuple[str, ...]:
    return (
        "feature_family_id",
        "family_center_mz",
        "family_center_rt",
        "rt_window_min",
        "rt_window_max",
        "event_cluster_count",
        "event_member_count",
        "identity_decision",
        "primary_evidence",
        "row_flags",
        "detected_count",
        "accepted_backfill_count",
        "detected_backfilled_count",
        "trace_cell_count",
        "selected_apex_rt_min",
        "selected_apex_rt_max",
        "assessable_count",
        "assessable_fraction",
        "trace_recomputed_assessable_fraction",
        "assessable_fraction_delta",
        "selected_apex_in_window_count",
        "selected_apex_in_window_fraction",
        "trace_recomputed_selected_apex_in_window_fraction",
        "selected_apex_in_window_fraction_delta",
        "selected_apex_outside_window_count",
        "selected_apex_outside_window_fraction",
        "zero_trace_total_count",
        "zero_trace_total_fraction",
        "zero_trace_inside_window_count",
        "zero_trace_inside_window_fraction",
        "split_supported_count",
        "split_supported_fraction",
        "status_counts",
        "region_verdict_counts",
        "detected_seed_candidate_count",
        "detected_seed_joined_count",
        "missing_detected_seed_candidate_count",
        "min_seed_evidence_score",
        "median_seed_evidence_score",
        "min_seed_event_count",
        "max_abs_nl_ppm",
        "detected_seed_precursor_mz_span",
        "detected_seed_product_mz_span",
        "seed_evidence_bucket",
        "backfill_seed_row_count",
        "backfill_seed_group_count",
        "backfill_seed_rt_span",
        "backfill_seed_rt_distribution",
        "seed_apex_far_count",
        "seed_apex_far_fraction",
        "seed_context_bucket",
        "root_cause_bucket",
        "production_interpretation",
        "recommended_next_action",
        "review_risk_score",
        "trace_summary_path",
        "alignment_reason",
    )


def _apex_aware_queue_fields() -> tuple[str, ...]:
    return (
        "feature_family_id",
        "family_center_mz",
        "family_center_rt",
        "suggested_rt_min",
        "suggested_rt_max",
        "suggested_output_prefix",
        "root_cause_bucket",
        "selected_apex_rt_min",
        "selected_apex_rt_max",
        "original_rt_min",
        "original_rt_max",
    )


def _seed_aware_queue_fields() -> tuple[str, ...]:
    return (
        "feature_family_id",
        "family_center_mz",
        "family_center_rt",
        "backfill_seed_mz",
        "backfill_seed_rt",
        "suggested_rt_min",
        "suggested_rt_max",
        "ppm",
        "rescued_cell_count",
        "suggested_output_prefix",
        "seed_context_bucket",
    )


def _detail_fields() -> tuple[str, ...]:
    return (
        "feature_family_id",
        "sample_stem",
        "status",
        "cell_area",
        "cell_height",
        "cell_apex_rt",
        "in_requested_window",
        "trace_has_signal",
        "trace_max_intensity",
        "trace_apex_rt",
        "global_trace_apex_delta_min",
        "local_window_to_global_max_ratio",
        "region_shadow_verdict",
        "source_candidate_id",
        "backfill_seed_mz",
        "backfill_seed_rt",
        "backfill_request_rt_min",
        "backfill_request_rt_max",
        "backfill_request_ppm",
        "backfill_apex_delta_sec",
        "issue_flags",
    )


def _format_counts(counter: Counter[str]) -> str:
    return ";".join(f"{key}:{counter[key]}" for key in sorted(counter))
