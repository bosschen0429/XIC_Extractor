from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

PEAK_CANDIDATE_HEADERS = (
    "sample_name",
    "group",
    "target_label",
    "role",
    "istd_pair",
    "analysis_mode",
    "resolver_mode",
    "candidate_id",
    "proposal_sources",
    "proposal_count",
    "source_apex_rank",
    "merge_note",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "raw_apex_rt_min",
    "rt_width_min",
    "selection_apex_intensity",
    "raw_apex_intensity",
    "prominence",
    "area_raw_counts_seconds",
    "area_baseline_corrected",
    "area_ms1_morphology",
    "ms1_morphology_area_source",
    "ms1_morphology_trace_method",
    "ms1_morphology_trace_window_points",
    "ms1_morphology_trace_effective_points",
    "area_uncertainty",
    "area_uncertainty_formula_version",
    "baseline_residual_mad",
    "area_uncertainty_noise_source",
    "quality_flags",
    "region_scan_count",
    "region_duration_min",
    "region_edge_ratio",
    "region_trace_continuity",
    "ms2_present",
    "nl_match",
    "ms2_trace_strength",
    "rt_prior_min",
    "rt_prior_sigma",
    "confidence",
    "raw_score",
    "support_labels",
    "concern_labels",
    "cap_labels",
    "reason",
    "selected",
    "selection_rank",
    "selection_reference_rt_min",
    "rejection_reason",
    "nl_status",
    "best_loss_ppm",
    "best_ms2_scan_rt_min",
    "apex_ms2_delta_min",
    "best_product_base_ratio",
    "trigger_scan_count",
    "strict_nl_scan_count",
    "ms1_peak_group_source",
    "ms1_peak_group_rt_min",
    "ms1_peak_group_rt_max",
    "ms1_peak_group_trigger_scan_count",
    "ms1_peak_group_strict_nl_scan_count",
    "ms1_peak_group_strict_nl_event_count",
    "outside_ms1_peak_group_trigger_scan_count",
    "outside_ms1_peak_group_strict_nl_scan_count",
    "ms2_alignment_source",
    "diagnostic_product_absence_reason",
    "nearest_product_loss_ppm",
    "nearest_product_base_ratio",
    "nearest_product_mz",
    "safe_merge_promotion_source",
    "safe_merge_promotion_shadow_boundary_id",
    "safe_merge_promotion_area_ratio",
    "safe_merge_promotion_selected_interval_count",
    "safe_merge_promotion_selected_interval_gap_max_min",
    "safe_merge_rejection_reason",
)



class FileResultWithPeakCandidates(Protocol):
    @property
    def peak_candidate_rows(self) -> Sequence[Mapping[str, str]]: ...


def write_peak_candidates_tsv(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    *,
    enabled: bool = True,
) -> None:
    if not enabled:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=PEAK_CANDIDATE_HEADERS,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(_sanitize_row(row))


def write_peak_candidates_for_file_results(
    path: Path,
    file_results: Sequence[FileResultWithPeakCandidates],
    *,
    enabled: bool = True,
) -> None:
    rows: list[Mapping[str, str]] = []
    for file_result in file_results:
        rows.extend(file_result.peak_candidate_rows)
    write_peak_candidates_tsv(path, rows, enabled=enabled)


def _sanitize_row(row: Mapping[str, str]) -> dict[str, str]:
    return {
        header: _sanitize_field(str(row.get(header, "")))
        for header in PEAK_CANDIDATE_HEADERS
    }


def _sanitize_field(value: str) -> str:
    return " ".join(value.replace("\t", " ").splitlines())
