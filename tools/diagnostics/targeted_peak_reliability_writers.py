"""Output writers for targeted peak reliability audit."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from tools.diagnostics.diagnostic_io import format_diagnostic_value
from tools.diagnostics.diagnostic_io import write_tsv as write_diagnostic_tsv
from tools.diagnostics.targeted_peak_reliability_models import (
    ROWS_COLUMNS,
    SUMMARY_COLUMNS,
    TargetedReliabilityOutputs,
    TargetedReliabilityResult,
    TargetedReliabilityRow,
    TargetedReliabilitySummary,
)


def _write_outputs(
    outputs: TargetedReliabilityOutputs,
    result: TargetedReliabilityResult,
    *,
    known_target_exceptions: Mapping[str, str],
) -> None:
    _write_tsv(outputs.rows_tsv, ROWS_COLUMNS, _row_dicts(result.rows))
    _write_tsv(
        outputs.summary_tsv,
        SUMMARY_COLUMNS,
        _summary_dicts(result.summaries),
    )
    outputs.json_path.write_text(
        json.dumps(
            _json_payload(result, known_target_exceptions=known_target_exceptions),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _write_markdown(outputs.markdown_path, result)


def _row_dicts(
    rows: Sequence[TargetedReliabilityRow],
) -> list[dict[str, object]]:
    return [
        {
            **asdict(row),
            "risk_reasons": ";".join(row.risk_reasons),
        }
        for row in rows
    ]


def _summary_dicts(
    summaries: Sequence[TargetedReliabilitySummary],
) -> list[dict[str, object]]:
    return [asdict(summary) for summary in summaries]


def _json_payload(
    result: TargetedReliabilityResult,
    *,
    known_target_exceptions: Mapping[str, str],
) -> dict[str, object]:
    return {
        "overall_status": (
            "WARN"
            if result.targeted_review_count or result.targeted_review_positive_count
            else "PASS"
        ),
        "summary": {
            "benchmark_eligible_count": result.benchmark_eligible_count,
            "targeted_review_positive_count": (result.targeted_review_positive_count),
            "targeted_review_count": result.targeted_review_count,
            "targeted_negative_count": result.targeted_negative_count,
        },
        "known_target_exceptions": dict(known_target_exceptions),
        "rows": _row_dicts(result.rows),
        "summaries": _summary_dicts(result.summaries),
    }


def _write_markdown(path: Path, result: TargetedReliabilityResult) -> None:
    overall_status = (
        "WARN"
        if result.targeted_review_count or result.targeted_review_positive_count
        else "PASS"
    )
    lines = [
        "# Targeted Peak Reliability Audit",
        "",
        f"Overall status: {overall_status}",
        "",
        (
            "| Target | Eligible | Review-positive | Review | Negative | "
            "Known exception | Top risk reasons |"
        ),
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for summary in result.summaries:
        lines.append(
            "| "
            f"{summary.target_label} | "
            f"{summary.benchmark_eligible_count} | "
            f"{summary.targeted_review_positive_count} | "
            f"{summary.targeted_review_count} | "
            f"{summary.targeted_negative_count} | "
            f"{summary.known_exception} | "
            f"{summary.top_risk_reasons} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_tsv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    write_diagnostic_tsv(
        path,
        rows,
        fieldnames,
        formatter=format_diagnostic_value,
    )
