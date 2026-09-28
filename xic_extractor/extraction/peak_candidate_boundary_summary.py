from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from xic_extractor.output.peak_candidate_boundary_summary import (
    write_peak_candidate_boundary_summary_tsv,
)

PeakCandidateBoundarySummaryRow = dict[str, str]


class FileResultWithPeakCandidateBoundarySummary(Protocol):
    @property
    def peak_candidate_boundary_rows(self) -> Sequence[Mapping[str, str]]: ...


def build_peak_candidate_boundary_summary_rows(
    boundary_rows: Sequence[Mapping[str, str]],
) -> list[PeakCandidateBoundarySummaryRow]:
    return [
        _summary_row(row)
        for row in boundary_rows
        if row.get("boundary_audit_top") == "TRUE"
    ]


def _summary_row(row: Mapping[str, str]) -> PeakCandidateBoundarySummaryRow:
    return {
        "sample_name": row.get("sample_name", ""),
        "group": row.get("group", ""),
        "target_label": row.get("target_label", ""),
        "target_mz": row.get("target_mz", ""),
        "role": row.get("role", ""),
        "istd_pair": row.get("istd_pair", ""),
        "analysis_mode": row.get("analysis_mode", ""),
        "resolver_mode": row.get("resolver_mode", ""),
        "candidate_id": row.get("candidate_id", ""),
        "proposal_sources": row.get("proposal_sources", ""),
        "selected_candidate": row.get("selected_candidate", ""),
        "top_boundary_id": row.get("boundary_id", ""),
        "top_boundary_sources": row.get("boundary_sources", ""),
        "top_boundary_audit_score": row.get("boundary_audit_score", ""),
        "top_boundary_audit_rank": row.get("boundary_audit_rank", ""),
        "top_boundary_support_labels": row.get("boundary_support_labels", ""),
        "top_boundary_concern_labels": row.get("boundary_concern_labels", ""),
        "nonoverlap_selected": row.get("boundary_nonoverlap_selected", ""),
        "nonoverlap_note": row.get("boundary_nonoverlap_note", ""),
        "nonoverlap_blocker_id": row.get("boundary_nonoverlap_blocker_id", ""),
        "rt_left_min": row.get("rt_left_min", ""),
        "rt_apex_min": row.get("rt_apex_min", ""),
        "rt_right_min": row.get("rt_right_min", ""),
        "rt_width_min": row.get("rt_width_min", ""),
        "area_raw_counts_seconds": row.get("area_raw_counts_seconds", ""),
        "area_baseline_corrected": row.get("area_baseline_corrected", ""),
        "area_ratio_vs_candidate_interval": row.get(
            "area_ratio_vs_candidate_interval",
            "",
        ),
        "width_delta_vs_candidate_interval": row.get(
            "width_delta_vs_candidate_interval",
            "",
        ),
        "is_candidate_interval": row.get("is_candidate_interval", ""),
    }


def write_peak_candidate_boundary_summary_for_file_results(
    path: Path,
    file_results: Sequence[FileResultWithPeakCandidateBoundarySummary],
    *,
    enabled: bool = True,
) -> None:
    boundary_rows: list[Mapping[str, str]] = []
    for file_result in file_results:
        boundary_rows.extend(file_result.peak_candidate_boundary_rows)
    write_peak_candidate_boundary_summary_tsv(
        path,
        build_peak_candidate_boundary_summary_rows(boundary_rows),
        enabled=enabled,
    )
