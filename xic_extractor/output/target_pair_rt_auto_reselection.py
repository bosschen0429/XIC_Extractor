from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from pathlib import Path

TARGET_PAIR_RT_AUTO_RESELECTION_HEADERS = (
    "sample_name",
    "target_label",
    "role",
    "trace_group_id",
    "previous_candidate_id",
    "selected_candidate_id",
    "selection_action",
    "selection_basis",
    "selection_status",
    "product_switch_allowed",
    "target_counted_detection",
    "expected_diff_stable_row_id",
    "evidence_comparison_policy",
    "previous_candidate_rt",
    "selected_candidate_rt",
    "paired_istd_rt",
    "pair_rt_delta_expected",
    "pair_rt_delta_observed",
    "pair_rt_delta_error",
    "paired_area_ratio_observed",
    "paired_area_ratio_reference_n",
    "paired_area_ratio_reference_min",
    "paired_area_ratio_reference_median",
    "paired_area_ratio_reference_max",
    "paired_area_ratio_status",
    "paired_area_ratio_basis",
    "paired_area_ratio_robust_status",
    "paired_area_ratio_robust_reference_min",
    "paired_area_ratio_robust_reference_median",
    "paired_area_ratio_robust_reference_max",
    "paired_area_ratio_robust_reference_mad",
    "paired_area_ratio_robust_basis",
    "calibration_source",
    "calibration_status",
    "missing_ms2_explanation",
    "role_policy",
    "gate_decision",
    "block_reason",
    "false_positive_review_status",
    "false_positive_review_reasons",
)

TARGET_PAIR_RT_AUTO_RESELECTION_SUMMARY_HEADERS = (
    "limited_evidence_shadow_count",
    "inconclusive_count",
    "blocked_diff_count",
    "shadow_auto_reselect_proposed_count",
    "auto_reselect_blocked_count",
    "changed_row_denominator",
    "false_positive_strata",
    "product_switch_allowed_true_count",
    "auto_reselected_count",
    "paired_area_ratio_within_active_count",
    "paired_area_ratio_outside_active_count",
    "paired_area_ratio_inconclusive_count",
    "false_positive_review_required_count",
    "row_approval_candidate_count",
    "product_switch_accepted_count",
)

_ALLOWED_SELECTION_ACTIONS = frozenset(
    {
        "none",
        "shadow_auto_reselect_proposed",
        "auto_reselect_blocked",
        "auto_reselected",
    }
)


def write_target_pair_rt_auto_reselection_tsv(
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
            fieldnames=TARGET_PAIR_RT_AUTO_RESELECTION_HEADERS,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            sanitized = _sanitize_row(row, TARGET_PAIR_RT_AUTO_RESELECTION_HEADERS)
            action = sanitized["selection_action"]
            if action not in _ALLOWED_SELECTION_ACTIONS:
                raise ValueError(f"invalid selection_action={action!r}")
            if action == "auto_reselected":
                if sanitized["product_switch_allowed"].upper() != "TRUE":
                    raise ValueError(
                        "auto_reselected rows must set product_switch_allowed=TRUE"
                    )
            else:
                sanitized["product_switch_allowed"] = "FALSE"
            writer.writerow(sanitized)


def write_target_pair_rt_auto_reselection_summary_tsv(
    path: Path,
    summary: Mapping[str, str],
    *,
    enabled: bool = True,
) -> None:
    if not enabled:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=TARGET_PAIR_RT_AUTO_RESELECTION_SUMMARY_HEADERS,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerow(
            _sanitize_row(summary, TARGET_PAIR_RT_AUTO_RESELECTION_SUMMARY_HEADERS)
        )


def _sanitize_row(
    row: Mapping[str, str],
    headers: Sequence[str],
) -> dict[str, str]:
    return {header: _sanitize_field(str(row.get(header, ""))) for header in headers}


def _sanitize_field(value: str) -> str:
    return " ".join(value.replace("\t", " ").splitlines())
