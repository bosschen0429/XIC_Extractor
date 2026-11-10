"""Output writers for the targeted ISTD benchmark diagnostic."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from xic_extractor.tabular_io import format_diagnostic_value, write_tsv

SUMMARY_COLUMNS = (
    "target_label",
    "role",
    "active_tag",
    "neutral_loss_da",
    "target_mz",
    "target_rt_min",
    "target_rt_max",
    "targeted_positive_count",
    "clean_targeted_positive_count",
    "targeted_review_positive_count",
    "targeted_review_count",
    "targeted_negative_count",
    "coverage_denominator_count",
    "targeted_total_count",
    "targeted_mean_rt",
    "candidate_match_count",
    "primary_match_count",
    "primary_feature_ids",
    "selected_feature_id",
    "untargeted_positive_count",
    "coverage_minimum",
    "paired_area_n",
    "log_area_pearson",
    "log_area_spearman",
    "family_mean_rt_delta_min",
    "sample_rt_pair_n",
    "sample_rt_median_abs_delta_min",
    "sample_rt_p95_abs_delta_min",
    "status",
    "failure_modes",
    "targeted_reliability_mode",
    "targeted_reliability_warning_modes",
    "note",
)

MATCH_COLUMNS = (
    "target_label",
    "feature_id",
    "include_in_primary_matrix",
    "group_center_mz",
    "group_center_rt",
    "group_product_mz",
    "group_observed_neutral_loss_da",
    "mz_delta_ppm",
    "rt_delta_sec",
    "product_delta_ppm",
    "loss_delta_da",
    "mass_shift_da",
    "match_type",
    "distance_score",
)


def write_benchmark_outputs(
    outputs: Any,
    *,
    summaries: Sequence[Any],
    matches: Sequence[Any],
    thresholds: Any,
) -> None:
    summary_rows = _summary_rows(summaries)
    overall_counts = _overall_counts(summaries)
    _write_tsv(outputs.summary_tsv, SUMMARY_COLUMNS, summary_rows)
    _write_tsv(outputs.matches_tsv, MATCH_COLUMNS, _match_rows(matches))
    _write_json(
        outputs.json_path,
        _json_payload(summary_rows, thresholds, overall_counts),
    )
    _write_markdown(
        outputs.markdown_path,
        summaries,
        overall_status=str(overall_counts["overall_status"]),
    )


def _summary_rows(summaries: Sequence[Any]) -> list[dict[str, object]]:
    return [
        {
            **asdict(summary),
            "active_tag": _bool_text(summary.active_tag),
            "primary_feature_ids": ";".join(summary.primary_feature_ids),
            "failure_modes": ";".join(summary.failure_modes),
            "targeted_reliability_warning_modes": ";".join(
                summary.targeted_reliability_warning_modes
            ),
        }
        for summary in summaries
    ]


def _match_rows(matches: Sequence[Any]) -> list[dict[str, object]]:
    return [asdict(match) for match in matches]


def _json_payload(
    summary_rows: Sequence[Mapping[str, object]],
    thresholds: Any,
    overall_counts: Mapping[str, object],
) -> dict[str, object]:
    return {
        **overall_counts,
        "thresholds": asdict(thresholds),
        "summaries": list(summary_rows),
    }


def _overall_counts(summaries: Sequence[Any]) -> dict[str, object]:
    fail_count = sum(summary.status == "FAIL" for summary in summaries)
    warn_count = sum(
        summary.status == "WARN" or bool(summary.targeted_reliability_warning_modes)
        for summary in summaries
    )
    active_fail_count = sum(
        summary.status == "FAIL" and summary.active_tag for summary in summaries
    )
    active_warn_count = sum(
        (summary.status == "WARN" or bool(summary.targeted_reliability_warning_modes))
        and summary.active_tag
        for summary in summaries
    )
    false_positive_tag_count = sum(
        "FALSE_POSITIVE_TAG" in summary.failure_modes for summary in summaries
    )
    return {
        "overall_status": "FAIL" if fail_count else "WARN" if warn_count else "PASS",
        "fail_count": fail_count,
        "warn_count": warn_count,
        "active_fail_count": active_fail_count,
        "active_warn_count": active_warn_count,
        "false_positive_tag_count": false_positive_tag_count,
    }


def _write_markdown(
    path: Any,
    summaries: Sequence[Any],
    *,
    overall_status: str,
) -> None:
    lines = [
        "# Targeted ISTD Benchmark",
        "",
        f"Overall status: {overall_status}",
        "",
        (
            "| Target | Active | Primary hits | Selected | Status | "
            "Failure modes | Warning modes |"
        ),
        "|---|---:|---:|---|---|---|---|",
    ]
    for summary in summaries:
        lines.append(
            "| "
            f"{summary.target_label} | "
            f"{_bool_text(summary.active_tag)} | "
            f"{summary.primary_match_count} | "
            f"{summary.selected_feature_id} | "
            f"{summary.status} | "
            f"{';'.join(summary.failure_modes)} | "
            f"{';'.join(summary.targeted_reliability_warning_modes)} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_json(path: Any, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_tsv(
    path: Any,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    write_tsv(path, rows, fieldnames, formatter=format_diagnostic_value)


def _bool_text(value: bool) -> str:
    return "TRUE" if value else "FALSE"
