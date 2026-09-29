from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from pathlib import Path

PEAK_CANDIDATE_BOUNDARY_SUMMARY_HEADERS = (
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
    "top_boundary_id",
    "top_boundary_sources",
    "top_boundary_audit_score",
    "top_boundary_audit_rank",
    "top_boundary_support_labels",
    "top_boundary_concern_labels",
    "nonoverlap_selected",
    "nonoverlap_note",
    "nonoverlap_blocker_id",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "rt_width_min",
    "area_raw_counts_seconds",
    "area_baseline_corrected",
    "area_ratio_vs_candidate_interval",
    "width_delta_vs_candidate_interval",
    "is_candidate_interval",
)



def write_peak_candidate_boundary_summary_tsv(
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
            fieldnames=PEAK_CANDIDATE_BOUNDARY_SUMMARY_HEADERS,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(_sanitize_row(row))


def _sanitize_row(row: Mapping[str, str]) -> dict[str, str]:
    return {
        header: _sanitize_field(str(row.get(header, "")))
        for header in PEAK_CANDIDATE_BOUNDARY_SUMMARY_HEADERS
    }


def _sanitize_field(value: str) -> str:
    return " ".join(value.replace("\t", " ").splitlines())
