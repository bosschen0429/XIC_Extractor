from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from pathlib import Path

PEAK_REGION_SELECTION_SHADOW_HEADERS = (
    "sample_name",
    "group",
    "target_label",
    "target_mz",
    "role",
    "istd_pair",
    "analysis_mode",
    "resolver_mode",
    "current_candidate_id",
    "current_boundary_id",
    "shadow_boundary_id",
    "current_rt_left_min",
    "current_rt_apex_min",
    "current_rt_right_min",
    "current_area_raw_counts_seconds",
    "shadow_rt_left_min",
    "shadow_rt_apex_min",
    "shadow_rt_right_min",
    "shadow_area_raw_counts_seconds",
    "shadow_status",
    "shadow_verdict",
    "decision_status",
    "decision_class",
    "product_action",
    "selected_candidate_id",
    "selected_boundary_id",
    "alternate_boundary_ids",
    "evidence_sources",
    "support_reasons",
    "conflict_reasons",
    "audit_reason",
    "promotion_reason",
    "baseline_method",
    "merge_suggestion_source",
    "score_delta",
    "area_ratio",
    "current_scan_count",
    "shadow_scan_count",
    "selected_interval_count",
    "selected_interval_gap_max_min",
    "selected_interval_total_score",
    "best_single_boundary_score",
    "support_labels",
    "concern_labels",
    "local_mixture_diagnostic",
    "local_mixture_reason",
    "review_reason",
)


PEAK_REGION_SELECTION_SHADOW_SUMMARY_HEADERS = (
    "sample_name",
    "target_label",
    "target_mz",
    "role",
    "resolver_mode",
    "current_rt_apex_min",
    "shadow_rt_apex_min",
    "current_area_raw_counts_seconds",
    "shadow_area_raw_counts_seconds",
    "shadow_status",
    "shadow_verdict",
    "decision_status",
    "decision_class",
    "product_action",
    "selected_candidate_id",
    "selected_boundary_id",
    "alternate_boundary_ids",
    "evidence_sources",
    "support_reasons",
    "conflict_reasons",
    "audit_reason",
    "promotion_reason",
    "baseline_method",
    "merge_suggestion_source",
    "score_delta",
    "area_ratio",
    "current_scan_count",
    "shadow_scan_count",
    "selected_interval_count",
    "selected_interval_gap_max_min",
    "selected_interval_total_score",
    "best_single_boundary_score",
    "local_mixture_diagnostic",
    "local_mixture_reason",
    "review_reason",
)


PEAK_REGION_SELECTION_BLAST_RADIUS_HEADERS = (
    "total_rows",
    "rows_that_would_change",
    "istd_rows_that_would_change",
    "affected_target_labels",
    "area_ratio_min",
    "area_ratio_median",
    "area_ratio_max",
)



def write_peak_region_selection_shadow_tsv(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    *,
    enabled: bool = True,
) -> None:
    _write_tsv(path, rows, PEAK_REGION_SELECTION_SHADOW_HEADERS, enabled=enabled)


def write_peak_region_selection_shadow_summary_tsv(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    *,
    enabled: bool = True,
) -> None:
    _write_tsv(
        path,
        rows,
        PEAK_REGION_SELECTION_SHADOW_SUMMARY_HEADERS,
        enabled=enabled,
    )


def write_peak_region_selection_blast_radius_tsv(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    *,
    enabled: bool = True,
) -> None:
    _write_tsv(
        path,
        rows,
        PEAK_REGION_SELECTION_BLAST_RADIUS_HEADERS,
        enabled=enabled,
    )


def _write_tsv(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    headers: Sequence[str],
    *,
    enabled: bool,
) -> None:
    if not enabled:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=headers,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(_sanitize_row(row, headers))


def _sanitize_row(
    row: Mapping[str, str],
    headers: Sequence[str],
) -> dict[str, str]:
    return {
        header: _sanitize_field(str(row.get(header, "")))
        for header in headers
    }


def _sanitize_field(value: str) -> str:
    return " ".join(value.replace("\t", " ").splitlines())
