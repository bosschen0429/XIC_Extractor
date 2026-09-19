"""Accept or reject a CID-NL activated-copy candidate against its contract."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.diagnostics.cid_nl_activation_copy_candidate import (
    DEFAULT_ALIGNMENT_MATRIX_IDENTITY_TSV,
    DEFAULT_ALIGNMENT_MATRIX_TSV,
    DEFAULT_EXPECTED_DIFF_CONTRACTS,
    DEFAULT_FORBIDDEN_TRANSITION_TSVS,
    DEFAULT_REVIEW_DIR,
    EXPECTED_DIFF_COLUMNS,
    FORBIDDEN_TRANSITION_COLUMNS,
    VALUE_DELTA_COLUMNS,
)
from xic_extractor.tabular_io import (
    read_tsv_required,
    read_tsv_with_header,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "cid_nl_activation_copy_acceptance"
DEFAULT_ACTIVATION_COPY_DIR = DEFAULT_REVIEW_DIR / "activation_copy_candidate"
DEFAULT_ACTIVATED_MATRIX_TSV = (
    DEFAULT_ACTIVATION_COPY_DIR / "alignment_matrix_activated_copy.tsv"
)
DEFAULT_VALUE_DELTA_TSV = (
    DEFAULT_ACTIVATION_COPY_DIR / "cid_nl_activation_copy_value_delta.tsv"
)
DEFAULT_OUTPUT_DIR = DEFAULT_ACTIVATION_COPY_DIR / "acceptance"

SUMMARY_COLUMNS = (
    "schema_version",
    "validation_label",
    "acceptance_status",
    "contract_cell_count",
    "value_delta_cell_count",
    "matrix_changed_cell_count",
    "candidate_transition_count",
    "forbidden_overlap_count",
    "unexpected_matrix_change_count",
    "missing_matrix_change_count",
    "product_writer_changed",
    "default_quant_matrix_changed",
    "workbook_gui_changed",
    "candidate_rows_are_matrix_rows",
    "production_ready",
    "hard_fail_reasons",
    "next_action",
)
MATRIX_DIFF_COLUMNS = (
    "schema_version",
    "successor_peak_hypothesis_id",
    "sample_stem",
    "input_value",
    "activated_copy_value",
    "change_status",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        payload = build_activation_copy_acceptance(
            expected_diff_contract_tsvs=tuple(
                args.expected_diff_contract_tsv or DEFAULT_EXPECTED_DIFF_CONTRACTS
            ),
            forbidden_transition_tsvs=tuple(
                args.forbidden_transition_tsv or DEFAULT_FORBIDDEN_TRANSITION_TSVS
            ),
            input_alignment_matrix_tsv=args.input_alignment_matrix_tsv,
            activated_matrix_tsv=args.activated_matrix_tsv,
            alignment_matrix_identity_tsv=args.alignment_matrix_identity_tsv,
            value_delta_tsv=args.value_delta_tsv,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"CID-NL activation-copy acceptance: {payload['summary_tsv']}")
    print(f"CID-NL activation-copy matrix diff: {payload['matrix_diff_tsv']}")
    if args.require_pass and payload["acceptance_status"] != "pass":
        return 2
    return 0


def build_activation_copy_acceptance(
    *,
    expected_diff_contract_tsvs: Sequence[Path],
    forbidden_transition_tsvs: Sequence[Path],
    input_alignment_matrix_tsv: Path,
    activated_matrix_tsv: Path,
    alignment_matrix_identity_tsv: Path,
    value_delta_tsv: Path,
    output_dir: Path,
) -> dict[str, Any]:
    contract_rows = _read_contract_rows(expected_diff_contract_tsvs)
    value_delta_rows = read_tsv_required(value_delta_tsv, VALUE_DELTA_COLUMNS)
    forbidden_overlap = _forbidden_overlap(contract_rows, forbidden_transition_tsvs)
    delta_failures = _validate_value_delta(contract_rows, value_delta_rows)
    matrix_diff_rows = _matrix_diff_rows(
        input_alignment_matrix_tsv=input_alignment_matrix_tsv,
        activated_matrix_tsv=activated_matrix_tsv,
        alignment_matrix_identity_tsv=alignment_matrix_identity_tsv,
    )
    matrix_failures = _matrix_failures(contract_rows, matrix_diff_rows)
    hard_fail_reasons = tuple(
        reason
        for reason in (
            "forbidden_transition_overlap" if forbidden_overlap else "",
            *delta_failures,
            *matrix_failures,
        )
        if reason
    )
    summary = _summary_payload(
        contract_rows=contract_rows,
        value_delta_rows=value_delta_rows,
        matrix_diff_rows=matrix_diff_rows,
        forbidden_overlap_count=len(forbidden_overlap),
        hard_fail_reasons=hard_fail_reasons,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_tsv = output_dir / "cid_nl_activation_copy_acceptance_summary.tsv"
    summary_json = output_dir / "cid_nl_activation_copy_acceptance_summary.json"
    matrix_diff_tsv = output_dir / "cid_nl_activation_copy_matrix_diff.tsv"
    write_tsv(summary_tsv, [summary], SUMMARY_COLUMNS)
    write_tsv(matrix_diff_tsv, matrix_diff_rows, MATRIX_DIFF_COLUMNS)
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **summary,
        "summary_tsv": str(summary_tsv),
        "summary_json": str(summary_json),
        "matrix_diff_tsv": str(matrix_diff_tsv),
    }


def _read_contract_rows(paths: Sequence[Path]) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for path in paths:
        rows.extend(read_tsv_required(path, EXPECTED_DIFF_COLUMNS))
    if not rows:
        raise ValueError("expected-diff contract is empty")
    return tuple(rows)


def _forbidden_overlap(
    contract_rows: Sequence[Mapping[str, str]],
    forbidden_transition_tsvs: Sequence[Path],
) -> set[str]:
    contract_keys = {text_value(row.get("transition_key")) for row in contract_rows}
    forbidden: set[str] = set()
    for path in forbidden_transition_tsvs:
        for row in read_tsv_required(path, FORBIDDEN_TRANSITION_COLUMNS):
            key = text_value(row.get("transition_key"))
            if key:
                forbidden.add(key)
    return contract_keys & forbidden


def _validate_value_delta(
    contract_rows: Sequence[Mapping[str, str]],
    value_delta_rows: Sequence[Mapping[str, str]],
) -> tuple[str, ...]:
    failures: list[str] = []
    contract_keys = _contract_keys(contract_rows)
    delta_keys = _delta_keys(value_delta_rows)
    if contract_keys != delta_keys:
        failures.append("value_delta_key_set_mismatch")
    for row in value_delta_rows:
        if text_value(row.get("original_matrix_value")):
            failures.append("value_delta_original_not_blank")
        if text_value(row.get("value_changed")) != "TRUE":
            failures.append("value_delta_not_marked_changed")
        if text_value(row.get("product_authority_effect")) != (
            "diagnostic_only_no_authority_change"
        ):
            failures.append("value_delta_product_authority_changed")
    return tuple(sorted(set(failures)))


def _matrix_diff_rows(
    *,
    input_alignment_matrix_tsv: Path,
    activated_matrix_tsv: Path,
    alignment_matrix_identity_tsv: Path,
) -> list[dict[str, Any]]:
    input_header, input_rows = read_tsv_with_header(input_alignment_matrix_tsv)
    output_header, output_rows = read_tsv_with_header(activated_matrix_tsv)
    if input_header != output_header:
        raise ValueError("activated-copy matrix header differs from input matrix")
    if len(input_rows) != len(output_rows):
        raise ValueError("activated-copy matrix row count differs from input matrix")
    identity_by_index = _identity_by_index(alignment_matrix_identity_tsv)
    sample_columns = [column for column in input_header if column not in {"Mz", "RT"}]
    diff_rows: list[dict[str, Any]] = []
    for index, (input_row, output_row) in enumerate(
        zip(input_rows, output_rows, strict=True),
        start=1,
    ):
        peak_id = identity_by_index.get(index, "")
        for sample in sample_columns:
            input_value = text_value(input_row.get(sample))
            output_value = text_value(output_row.get(sample))
            if input_value == output_value:
                continue
            diff_rows.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "successor_peak_hypothesis_id": peak_id,
                    "sample_stem": sample,
                    "input_value": input_value,
                    "activated_copy_value": output_value,
                    "change_status": "changed",
                }
            )
    return diff_rows


def _matrix_failures(
    contract_rows: Sequence[Mapping[str, str]],
    matrix_diff_rows: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    failures: list[str] = []
    contract_cells = {
        (
            text_value(row.get("successor_peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
        )
        for row in contract_rows
    }
    diff_cells = {
        (
            text_value(row.get("successor_peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
        )
        for row in matrix_diff_rows
    }
    if diff_cells - contract_cells:
        failures.append("unexpected_matrix_change")
    if contract_cells - diff_cells:
        failures.append("missing_matrix_change")
    return tuple(failures)


def _summary_payload(
    *,
    contract_rows: Sequence[Mapping[str, str]],
    value_delta_rows: Sequence[Mapping[str, str]],
    matrix_diff_rows: Sequence[Mapping[str, Any]],
    forbidden_overlap_count: int,
    hard_fail_reasons: Sequence[str],
) -> dict[str, Any]:
    unexpected = 0
    missing = 0
    if "unexpected_matrix_change" in hard_fail_reasons:
        unexpected = 1
    if "missing_matrix_change" in hard_fail_reasons:
        missing = 1
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_label": "diagnostic_only_activated_copy_acceptance",
        "acceptance_status": "pass" if not hard_fail_reasons else "fail",
        "contract_cell_count": len(contract_rows),
        "value_delta_cell_count": len(value_delta_rows),
        "matrix_changed_cell_count": len(matrix_diff_rows),
        "candidate_transition_count": len(
            {text_value(row.get("transition_key")) for row in contract_rows}
        ),
        "forbidden_overlap_count": forbidden_overlap_count,
        "unexpected_matrix_change_count": unexpected,
        "missing_matrix_change_count": missing,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "workbook_gui_changed": False,
        "candidate_rows_are_matrix_rows": False,
        "production_ready": False,
        "hard_fail_reasons": ";".join(hard_fail_reasons),
        "next_action": (
            "promote_requires_explicit_adopt_gate"
            if not hard_fail_reasons
            else "review_activation_copy_acceptance_failures"
        ),
    }


def _identity_by_index(path: Path) -> dict[int, str]:
    rows = read_tsv_required(path, ("matrix_row_index", "peak_hypothesis_id"))
    result: dict[int, str] = {}
    for row in rows:
        index_text = text_value(row.get("matrix_row_index"))
        peak_id = text_value(row.get("peak_hypothesis_id"))
        if index_text and peak_id:
            result[int(index_text)] = peak_id
    return result


def _contract_keys(
    rows: Sequence[Mapping[str, str]],
) -> set[tuple[str, str, str]]:
    return {
        (
            text_value(row.get("successor_peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
            text_value(row.get("transition_key")),
        )
        for row in rows
    }


def _delta_keys(
    rows: Sequence[Mapping[str, str]],
) -> set[tuple[str, str, str]]:
    return {
        (
            text_value(row.get("successor_peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
            text_value(row.get("transition_key")),
        )
        for row in rows
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-diff-contract-tsv",
        type=Path,
        action="append",
    )
    parser.add_argument(
        "--forbidden-transition-tsv",
        type=Path,
        action="append",
    )
    parser.add_argument(
        "--input-alignment-matrix-tsv",
        type=Path,
        default=DEFAULT_ALIGNMENT_MATRIX_TSV,
    )
    parser.add_argument(
        "--activated-matrix-tsv",
        type=Path,
        default=DEFAULT_ACTIVATED_MATRIX_TSV,
    )
    parser.add_argument(
        "--alignment-matrix-identity-tsv",
        type=Path,
        default=DEFAULT_ALIGNMENT_MATRIX_IDENTITY_TSV,
    )
    parser.add_argument("--value-delta-tsv", type=Path, default=DEFAULT_VALUE_DELTA_TSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
