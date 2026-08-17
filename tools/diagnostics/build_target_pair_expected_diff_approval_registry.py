from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics.diagnostic_io import read_tsv_required, write_tsv
from xic_extractor.peak_detection.model_selection import expected_diff_stable_row_id
from xic_extractor.peak_detection.model_selection_approval_registry import (
    EXPECTED_DIFF_APPROVAL_REGISTRY_HEADERS,
)

REQUIRED_TARGET_PAIR_REVIEW_COLUMNS = (
    "sample_name",
    "target_label",
    "previous_candidate_id",
    "selected_candidate_id",
    "selection_action",
    "selection_basis",
    "selection_status",
    "expected_diff_stable_row_id",
    "evidence_comparison_policy",
    "previous_candidate_rt",
    "selected_candidate_rt",
    "paired_istd_rt",
    "paired_area_ratio_observed",
    "paired_area_ratio_status",
    "missing_ms2_explanation",
    "false_positive_review_status",
    "false_positive_review_reasons",
)

DEFAULT_PUBLIC_OUTPUTS = (
    "candidate table selected marker",
    "selected rt",
    "area",
    "boundary",
    "confidence",
    "reason",
    "final matrix value",
)

DEFAULT_EVIDENCE_SOURCES = (
    "ms1_trace",
    "role_aware_rt",
    "paired_area_ratio",
    "manual_eic_review",
)


def build_expected_diff_approval_registry_rows(
    review_rows: Sequence[Mapping[str, str]],
    *,
    approved_rows: Iterable[tuple[str, str]],
    validation_tier: str = "manual_eic_ms2_review",
    reviewer_role: str = "mass_spectrometry_reviewer",
) -> list[dict[str, str]]:
    rows_by_key = _unique_review_rows_by_key(review_rows)
    out: list[dict[str, str]] = []
    for sample_name, target_label in approved_rows:
        key = (sample_name, target_label)
        row = rows_by_key.get(key)
        if row is None:
            raise ValueError(
                f"approved row {sample_name!r}::{target_label!r} not found"
            )
        _validate_row_approval_candidate(row)
        evidence_summary = _evidence_summary(row)
        out.append(
            {
                "stable_row_id": row["expected_diff_stable_row_id"],
                "sample_name": row["sample_name"],
                "target_label": row["target_label"],
                "legacy_selected_candidate_id": row["previous_candidate_id"],
                "successor_selected_candidate_id": row["selected_candidate_id"],
                "final_label": "expected_diff",
                "reviewer_verdict": "approved",
                "validation_tier": validation_tier,
                "public_outputs_touched": ";".join(DEFAULT_PUBLIC_OUTPUTS),
                "matrix_value_impact": "area_value_changed",
                "evidence_sources": ";".join(DEFAULT_EVIDENCE_SOURCES),
                "evidence_summary": evidence_summary,
                "reviewer_role": reviewer_role,
            }
        )
    return out


def write_expected_diff_approval_registry_from_target_pair_review(
    *,
    review_tsv: Path,
    output_tsv: Path,
    approved_rows: Iterable[tuple[str, str]],
    validation_tier: str = "manual_eic_ms2_review",
    reviewer_role: str = "mass_spectrometry_reviewer",
) -> Path:
    review_rows = read_tsv_required(
        review_tsv,
        REQUIRED_TARGET_PAIR_REVIEW_COLUMNS,
    )
    approval_rows = build_expected_diff_approval_registry_rows(
        review_rows,
        approved_rows=approved_rows,
        validation_tier=validation_tier,
        reviewer_role=reviewer_role,
    )
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        output_tsv,
        approval_rows,
        EXPECTED_DIFF_APPROVAL_REGISTRY_HEADERS,
        lineterminator="\n",
    )
    return output_tsv


def _unique_review_rows_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    out: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        key = (row.get("sample_name", ""), row.get("target_label", ""))
        if key in out:
            raise ValueError(
                f"duplicate target-pair review row for {key[0]!r}::{key[1]!r}"
            )
        out[key] = row
    return out


def _validate_row_approval_candidate(row: Mapping[str, str]) -> None:
    label = f"{row.get('sample_name', '')}::{row.get('target_label', '')}"
    if row.get("false_positive_review_status") != "row_approval_candidate":
        raise ValueError(
            f"{label}: approval requires false_positive_review_status="
            "row_approval_candidate"
        )
    if row.get("selection_action") != "shadow_auto_reselect_proposed":
        raise ValueError(
            f"{label}: approval requires selection_action="
            "shadow_auto_reselect_proposed"
        )
    if row.get("selection_status") != "expected_diff":
        raise ValueError(f"{label}: approval requires selection_status=expected_diff")
    if row.get("paired_area_ratio_status") != "within_robust_range":
        raise ValueError(
            f"{label}: approval requires paired_area_ratio_status="
            "within_robust_range"
        )
    if not row.get("selected_candidate_rt", "").strip():
        raise ValueError(f"{label}: approval requires selected_candidate_rt")
    if not row.get("previous_candidate_id", "").strip():
        raise ValueError(f"{label}: approval requires previous_candidate_id")
    if not row.get("selected_candidate_id", "").strip():
        raise ValueError(f"{label}: approval requires selected_candidate_id")
    if row.get("previous_candidate_id") == row.get("selected_candidate_id"):
        raise ValueError(f"{label}: approval requires a changed selected candidate")
    expected_stable_id = expected_diff_stable_row_id(
        legacy_selected_candidate_id=row["previous_candidate_id"],
        successor_selected_candidate_id=row["selected_candidate_id"],
    )
    if row.get("expected_diff_stable_row_id") != expected_stable_id:
        raise ValueError(f"{label}: expected_diff_stable_row_id mismatch")


def _evidence_summary(row: Mapping[str, str]) -> str:
    parts = [
        "Manual EIC review approved target-pair expected-diff switch.",
        f"RT {row.get('previous_candidate_rt', '')} -> "
        f"{row.get('selected_candidate_rt', '')}.",
        f"Paired ISTD RT {row.get('paired_istd_rt', '')}.",
        f"Paired area ratio {row.get('paired_area_ratio_observed', '')} "
        f"({row.get('paired_area_ratio_status', '')}).",
    ]
    ms2 = row.get("missing_ms2_explanation", "")
    if ms2:
        parts.append(f"MS2/NL state recorded as {ms2}, not standalone authority.")
    reasons = row.get("false_positive_review_reasons", "")
    if reasons:
        parts.append(f"Review reasons: {reasons}.")
    return " ".join(part for part in parts if part)


def _parse_approved_row(value: str) -> tuple[str, str]:
    sample_name, separator, target_label = value.partition("::")
    if not separator or not sample_name.strip() or not target_label.strip():
        raise argparse.ArgumentTypeError(
            "--approved-row must be formatted as SAMPLE::TARGET"
        )
    return sample_name.strip(), target_label.strip()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build an expected-diff approval registry from explicitly approved "
            "target-pair RT review rows."
        )
    )
    parser.add_argument("--target-pair-review-tsv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--approved-row",
        action="append",
        type=_parse_approved_row,
        required=True,
        help="Explicit approved row formatted as SAMPLE::TARGET. Repeatable.",
    )
    parser.add_argument(
        "--validation-tier",
        default="manual_eic_ms2_review",
        choices=("targeted_benchmark", "8raw", "manual_eic_ms2_review"),
    )
    parser.add_argument(
        "--reviewer-role",
        default="mass_spectrometry_reviewer",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        output = write_expected_diff_approval_registry_from_target_pair_review(
            review_tsv=args.target_pair_review_tsv,
            output_tsv=args.output,
            approved_rows=tuple(args.approved_row),
            validation_tier=args.validation_tier,
            reviewer_role=args.reviewer_role,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
