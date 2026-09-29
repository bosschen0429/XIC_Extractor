from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

PEAK_CANDIDATE_BOUNDARY_HEADERS = (
    "sample_name",
    "group",
    "target_label",
    "target_mz",
    "role",
    "istd_pair",
    "analysis_mode",
    "resolver_mode",
    "candidate_id",
    "proposal_sources",
    "selected_candidate",
    "boundary_id",
    "boundary_sources",
    "cwt_audit_filter_reason",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "rt_width_min",
    "area_raw_counts_seconds",
    "area_baseline_corrected",
    "area_uncertainty",
    "area_uncertainty_formula_version",
    "baseline_residual_mad",
    "area_uncertainty_noise_source",
    "baseline_type",
    "baseline_score",
    "boundary_audit_score",
    "boundary_audit_rank",
    "boundary_audit_top",
    "boundary_nonoverlap_selected",
    "boundary_nonoverlap_note",
    "boundary_nonoverlap_blocker_id",
    "boundary_support_labels",
    "boundary_concern_labels",
    "scan_count",
    "area_delta_vs_candidate_interval",
    "area_ratio_vs_candidate_interval",
    "width_delta_vs_candidate_interval",
    "is_candidate_interval",
)



class FileResultWithPeakCandidateBoundaries(Protocol):
    @property
    def peak_candidate_boundary_rows(self) -> Sequence[Mapping[str, str]]: ...


def write_peak_candidate_boundaries_tsv(
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
            fieldnames=PEAK_CANDIDATE_BOUNDARY_HEADERS,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(_sanitize_row(row))


def write_peak_candidate_boundaries_for_file_results(
    path: Path,
    file_results: Sequence[FileResultWithPeakCandidateBoundaries],
    *,
    enabled: bool = True,
) -> None:
    rows: list[Mapping[str, str]] = []
    for file_result in file_results:
        rows.extend(file_result.peak_candidate_boundary_rows)
    write_peak_candidate_boundaries_tsv(path, rows, enabled=enabled)


def _sanitize_row(row: Mapping[str, str]) -> dict[str, str]:
    return {
        header: _sanitize_field(str(row.get(header, "")))
        for header in PEAK_CANDIDATE_BOUNDARY_HEADERS
    }


def _sanitize_field(value: str) -> str:
    return " ".join(value.replace("\t", " ").splitlines())
