"""Evaluate a standard-peak gate from a shift-aware calibration pack."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics.diagnostic_io import (
    format_diagnostic_value,
    read_tsv_required,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "shift_aware_standard_peak_gate_calibration"
SHIFT_AWARE_SUPPORT_CALL = "shift_aware_same_pattern_support_review_only"
OVERLAY_STANDARD_VERDICT = "ms1_shape_supports_family_backfill"

REQUIRED_COLUMNS = (
    "review_rank",
    "feature_family_id",
    "machine_shift_aware_call",
    "manual_standard_peak_call",
    "manual_backfill_authority_call",
    "min_shape_r_after_best_shift",
    "max_shape_r_after_best_shift",
    "max_abs_shift_sec",
    "family_verdict",
)

OUTPUT_COLUMNS = (
    "schema_version",
    "review_rank",
    "feature_family_id",
    "machine_shift_aware_call",
    "family_verdict",
    "standard_peak_gate_call",
    "standard_peak_gate_reasons",
    "standard_peak_gate_blockers",
    "manual_standard_peak_call",
    "manual_backfill_authority_call",
    "calibration_outcome",
    "min_shape_r_after_best_shift",
    "max_shape_r_after_best_shift",
    "max_abs_shift_sec",
    "manual_notes",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        rows, summary = evaluate_standard_peak_gate(args.manual_pack_tsv)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        rows_tsv = args.output_dir / "shift_aware_standard_peak_gate_calibration.tsv"
        summary_json = (
            args.output_dir
            / "shift_aware_standard_peak_gate_calibration_summary.json"
        )
        write_tsv(
            rows_tsv,
            rows,
            OUTPUT_COLUMNS,
            formatter=format_diagnostic_value,
            lineterminator="\n",
        )
        summary_json.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"shift-aware standard peak gate TSV: {rows_tsv}")
    print(f"shift-aware standard peak gate summary JSON: {summary_json}")
    return 0


def evaluate_standard_peak_gate(
    manual_pack_tsv: Path,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    rows = read_tsv_required(manual_pack_tsv, REQUIRED_COLUMNS)
    evaluated = tuple(_evaluate_row(row) for row in rows)
    return evaluated, _summary(evaluated)


def _evaluate_row(row: Mapping[str, str]) -> dict[str, Any]:
    gate = _gate_call(row)
    manual_label = _manual_label(row)
    machine_positive = gate["call"] == "standard_peak_gate_supported"
    return {
        "schema_version": SCHEMA_VERSION,
        "review_rank": text_value(row.get("review_rank")),
        "feature_family_id": text_value(row.get("feature_family_id")),
        "machine_shift_aware_call": text_value(row.get("machine_shift_aware_call")),
        "family_verdict": text_value(row.get("family_verdict")),
        "standard_peak_gate_call": gate["call"],
        "standard_peak_gate_reasons": ";".join(gate["reasons"]),
        "standard_peak_gate_blockers": ";".join(gate["blockers"]),
        "manual_standard_peak_call": text_value(row.get("manual_standard_peak_call")),
        "manual_backfill_authority_call": text_value(
            row.get("manual_backfill_authority_call"),
        ),
        "calibration_outcome": _outcome(
            machine_positive=machine_positive,
            manual_label=manual_label,
        ),
        "min_shape_r_after_best_shift": text_value(
            row.get("min_shape_r_after_best_shift"),
        ),
        "max_shape_r_after_best_shift": text_value(
            row.get("max_shape_r_after_best_shift"),
        ),
        "max_abs_shift_sec": text_value(row.get("max_abs_shift_sec")),
        "manual_notes": text_value(row.get("manual_notes")),
    }


def _gate_call(row: Mapping[str, str]) -> dict[str, tuple[str, ...] | str]:
    reasons: list[str] = []
    blockers: list[str] = []
    if text_value(row.get("machine_shift_aware_call")) == SHIFT_AWARE_SUPPORT_CALL:
        reasons.append("shift_aware_same_pattern_supported")
    else:
        blockers.append("shift_aware_same_pattern_not_supported")
    if text_value(row.get("family_verdict")) == OVERLAY_STANDARD_VERDICT:
        reasons.append("family_overlay_gaussian_smoothed_standard_peak_supported")
    else:
        blockers.append("family_overlay_gaussian_smoothed_peak_not_standard")
    return {
        "call": (
            "standard_peak_gate_supported"
            if not blockers
            else "standard_peak_gate_blocked"
        ),
        "reasons": tuple(reasons),
        "blockers": tuple(blockers),
    }


def _manual_label(row: Mapping[str, str]) -> str:
    manual_peak_call = text_value(row.get("manual_standard_peak_call"))
    manual_authority_call = text_value(row.get("manual_backfill_authority_call"))
    if not manual_peak_call and not manual_authority_call:
        return "unlabeled"
    if (
        manual_peak_call == "standard_peak"
        and manual_authority_call == "authorize_standard_peak_backfill"
    ):
        return "positive"
    return "negative"


def _outcome(*, machine_positive: bool, manual_label: str) -> str:
    if manual_label == "unlabeled":
        return (
            "unlabeled_machine_supported"
            if machine_positive
            else "unlabeled_machine_blocked"
        )
    manual_positive = manual_label == "positive"
    if machine_positive and manual_positive:
        return "true_positive"
    if machine_positive and not manual_positive:
        return "false_positive"
    if not machine_positive and manual_positive:
        return "false_negative"
    return "true_negative"


def _summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    outcomes = Counter(text_value(row.get("calibration_outcome")) for row in rows)
    machine_positive_count = sum(
        1
        for row in rows
        if text_value(row.get("standard_peak_gate_call"))
        == "standard_peak_gate_supported"
    )
    manual_positive_count = sum(
        1
        for row in rows
        if _manual_label(row) == "positive"
    )
    manual_negative_count = sum(
        1
        for row in rows
        if _manual_label(row) == "negative"
    )
    unlabeled_count = sum(
        1
        for row in rows
        if _manual_label(row) == "unlabeled"
    )
    true_positive = outcomes.get("true_positive", 0)
    false_positive = outcomes.get("false_positive", 0)
    false_negative = outcomes.get("false_negative", 0)
    precision = _safe_ratio(true_positive, true_positive + false_positive)
    recall = _safe_ratio(true_positive, true_positive + false_negative)
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_label": "diagnostic_only",
        "row_count": len(rows),
        "manual_positive_count": manual_positive_count,
        "manual_negative_count": manual_negative_count,
        "unlabeled_count": unlabeled_count,
        "machine_positive_count": machine_positive_count,
        "machine_negative_count": len(rows) - machine_positive_count,
        "true_positive_count": true_positive,
        "true_negative_count": outcomes.get("true_negative", 0),
        "false_positive_count": false_positive,
        "false_negative_count": false_negative,
        "unlabeled_machine_supported_count": outcomes.get(
            "unlabeled_machine_supported",
            0,
        ),
        "unlabeled_machine_blocked_count": outcomes.get(
            "unlabeled_machine_blocked",
            0,
        ),
        "precision": precision,
        "recall": recall,
        "outcome_counts": dict(sorted(outcomes.items())),
        "gate_rule": (
            "machine_shift_aware_call="
            f"{SHIFT_AWARE_SUPPORT_CALL};"
            f"family_verdict={OVERLAY_STANDARD_VERDICT}"
        ),
        "product_behavior_changed": False,
        "matrix_contract_changed": False,
    }


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manual-pack-tsv",
        type=Path,
        required=True,
        help=(
            "Historical flag name for shift_aware_backfill_calibration_pack.tsv. "
            "Rows may be manual calibration oracle rows or unlabeled "
            "machine-gate candidates."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
