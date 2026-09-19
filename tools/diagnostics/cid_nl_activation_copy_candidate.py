"""Build a CID-NL activated-copy matrix candidate from expected-diff rows."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.diagnostics.cid_nl_feature_inclusion_gate import EXPECTED_DIFF_COLUMNS
from xic_extractor.tabular_io import (
    file_sha256,
    read_tsv_required,
    read_tsv_with_header,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "cid_nl_activation_copy_candidate"
DEFAULT_REVIEW_DIR = Path(
    "output/validation/cid_nl_default_activation_gallery_review",
)
DEFAULT_GATE_DIR = DEFAULT_REVIEW_DIR / "feature_inclusion_gate"
DEFAULT_EXPECTED_DIFF_CONTRACTS = (
    DEFAULT_GATE_DIR / "cid_nl_supported_candidate_expected_diff_contract.tsv",
    DEFAULT_GATE_DIR / "cid_nl_agent_resolved_expected_diff_contract.tsv",
    DEFAULT_GATE_DIR / "cid_nl_manual_resolved_expected_diff_contract.tsv",
)
DEFAULT_FORBIDDEN_TRANSITION_TSVS = (
    DEFAULT_GATE_DIR / "cid_nl_user_review_queue.tsv",
    DEFAULT_GATE_DIR / "cid_nl_agent_resolved_hold_queue.tsv",
    DEFAULT_GATE_DIR / "cid_nl_manual_resolved_hold_queue.tsv",
    DEFAULT_GATE_DIR / "cid_nl_feature_inclusion_blocked_queue.tsv",
)
DEFAULT_ALIGNMENT_DIR = Path(
    "output/discovery/cid_nl_product_ready_alignment_85raw_20260620_fix3",
)
DEFAULT_ALIGNMENT_MATRIX_TSV = DEFAULT_ALIGNMENT_DIR / "alignment_matrix.tsv"
DEFAULT_ALIGNMENT_MATRIX_IDENTITY_TSV = (
    DEFAULT_ALIGNMENT_DIR / "alignment_matrix_identity.tsv"
)
DEFAULT_OUTPUT_DIR = DEFAULT_REVIEW_DIR / "activation_copy_candidate"

IDENTITY_COLUMNS = (
    "matrix_row_index",
    "peak_hypothesis_id",
)
FORBIDDEN_TRANSITION_COLUMNS = (
    "transition_key",
)
SUMMARY_COLUMNS = (
    "schema_version",
    "validation_label",
    "activation_copy_status",
    "candidate_contract_cell_count",
    "changed_matrix_cell_count",
    "candidate_transition_count",
    "input_matrix_sha256",
    "output_matrix_sha256",
    "input_identity_sha256",
    "output_identity_sha256",
    "product_writer_changed",
    "default_quant_matrix_changed",
    "workbook_gui_changed",
    "candidate_rows_are_matrix_rows",
    "authority_statement",
)
VALUE_DELTA_COLUMNS = (
    "schema_version",
    "transition_key",
    "sample_stem",
    "source_peak_hypothesis_id",
    "successor_peak_hypothesis_id",
    "matrix_row_index",
    "source_mz",
    "source_rt",
    "successor_mz",
    "successor_rt",
    "successor_product_mz",
    "successor_neutral_loss_tag",
    "original_matrix_value",
    "activated_copy_value",
    "candidate_quant_value",
    "value_changed",
    "authority_gate",
    "product_authority_effect",
    "expected_product_effect",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        payload = build_activation_copy_candidate(
            expected_diff_contract_tsvs=tuple(
                args.expected_diff_contract_tsv or DEFAULT_EXPECTED_DIFF_CONTRACTS
            ),
            forbidden_transition_tsvs=tuple(
                args.forbidden_transition_tsv or DEFAULT_FORBIDDEN_TRANSITION_TSVS
            ),
            alignment_matrix_tsv=args.alignment_matrix_tsv,
            alignment_matrix_identity_tsv=args.alignment_matrix_identity_tsv,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"CID-NL activated-copy matrix: {payload['activated_matrix_tsv']}")
    print(f"CID-NL activated-copy delta: {payload['value_delta_tsv']}")
    print(
        "CID-NL activated-copy changed cells: "
        f"{payload['changed_matrix_cell_count']}",
    )
    if args.require_pass and payload["activation_copy_status"] != "pass":
        return 2
    return 0


def build_activation_copy_candidate(
    *,
    expected_diff_contract_tsvs: Sequence[Path],
    forbidden_transition_tsvs: Sequence[Path],
    alignment_matrix_tsv: Path,
    alignment_matrix_identity_tsv: Path,
    output_dir: Path,
) -> dict[str, Any]:
    contract_rows = _read_contract_rows(expected_diff_contract_tsvs)
    _require_no_forbidden_transitions(
        contract_rows,
        forbidden_transition_tsvs=forbidden_transition_tsvs,
    )
    matrix_header, matrix_rows = read_tsv_with_header(alignment_matrix_tsv)
    identity_rows = read_tsv_required(
        alignment_matrix_identity_tsv,
        IDENTITY_COLUMNS,
    )
    row_index_by_peak_id = _row_index_by_peak_hypothesis(identity_rows)
    patched_rows = [dict(row) for row in matrix_rows]
    value_delta_rows = _apply_contract_rows(
        patched_rows=patched_rows,
        matrix_header=matrix_header,
        row_index_by_peak_id=row_index_by_peak_id,
        contract_rows=contract_rows,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    activated_matrix_tsv = output_dir / "alignment_matrix_activated_copy.tsv"
    activated_identity_tsv = (
        output_dir / "alignment_matrix_identity_activated_copy.tsv"
    )
    value_delta_tsv = output_dir / "cid_nl_activation_copy_value_delta.tsv"
    summary_tsv = output_dir / "cid_nl_activation_copy_candidate_summary.tsv"
    summary_json = output_dir / "cid_nl_activation_copy_candidate_summary.json"

    write_tsv(activated_matrix_tsv, patched_rows, matrix_header)
    write_tsv(activated_identity_tsv, identity_rows, tuple(identity_rows[0]))
    write_tsv(value_delta_tsv, value_delta_rows, VALUE_DELTA_COLUMNS)
    summary_payload = _summary_payload(
        contract_rows=contract_rows,
        value_delta_rows=value_delta_rows,
        alignment_matrix_tsv=alignment_matrix_tsv,
        activated_matrix_tsv=activated_matrix_tsv,
        alignment_matrix_identity_tsv=alignment_matrix_identity_tsv,
        activated_identity_tsv=activated_identity_tsv,
    )
    write_tsv(summary_tsv, [summary_payload], SUMMARY_COLUMNS)
    summary_json.write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **summary_payload,
        "activated_matrix_tsv": str(activated_matrix_tsv),
        "activated_identity_tsv": str(activated_identity_tsv),
        "value_delta_tsv": str(value_delta_tsv),
        "summary_tsv": str(summary_tsv),
        "summary_json": str(summary_json),
    }


def _read_contract_rows(paths: Sequence[Path]) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in paths:
        path_rows = read_tsv_required(path, EXPECTED_DIFF_COLUMNS)
        for row in path_rows:
            key = (
                text_value(row.get("successor_peak_hypothesis_id")),
                text_value(row.get("sample_stem")),
                text_value(row.get("transition_key")),
            )
            if key in seen:
                raise ValueError(
                    "duplicate expected-diff contract row: "
                    f"{key[0]}|{key[1]}|{key[2]}",
                )
            seen.add(key)
            _validate_contract_row(row)
            rows.append(dict(row))
    if not rows:
        raise ValueError("expected-diff contract is empty")
    return tuple(rows)


def _validate_contract_row(row: Mapping[str, str]) -> None:
    if text_value(row.get("authority_gate")) != (
        "candidate_only_expected_diff_required_no_product_write"
    ):
        raise ValueError(
            "expected-diff contract row missing no-product-write authority gate: "
            f"{text_value(row.get('transition_key'))}"
        )
    if text_value(row.get("product_authority_effect")) != (
        "diagnostic_only_no_authority_change"
    ):
        raise ValueError(
            "expected-diff contract row has product authority effect: "
            f"{text_value(row.get('transition_key'))}"
        )
    for field in (
        "transition_key",
        "sample_stem",
        "successor_peak_hypothesis_id",
        "candidate_quant_value",
    ):
        if not text_value(row.get(field)):
            raise ValueError(f"expected-diff contract row missing {field}")


def _require_no_forbidden_transitions(
    contract_rows: Sequence[Mapping[str, str]],
    *,
    forbidden_transition_tsvs: Sequence[Path],
) -> None:
    contract_keys = {text_value(row.get("transition_key")) for row in contract_rows}
    forbidden_keys: set[str] = set()
    for path in forbidden_transition_tsvs:
        for row in read_tsv_required(path, FORBIDDEN_TRANSITION_COLUMNS):
            key = text_value(row.get("transition_key"))
            if key:
                forbidden_keys.add(key)
    overlap = sorted(contract_keys & forbidden_keys)
    if overlap:
        raise ValueError(
            "expected-diff contract contains forbidden review/hold/blocked "
            "transitions: "
            + ", ".join(overlap)
        )


def _row_index_by_peak_hypothesis(
    identity_rows: Sequence[Mapping[str, str]],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in identity_rows:
        peak_id = text_value(row.get("peak_hypothesis_id"))
        row_index_text = text_value(row.get("matrix_row_index"))
        if not peak_id or not row_index_text:
            continue
        if peak_id in result:
            raise ValueError(f"duplicate peak_hypothesis_id in identity: {peak_id}")
        result[peak_id] = int(row_index_text)
    return result


def _apply_contract_rows(
    *,
    patched_rows: list[dict[str, str]],
    matrix_header: Sequence[str],
    row_index_by_peak_id: Mapping[str, int],
    contract_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    sample_columns = set(matrix_header) - {"Mz", "RT"}
    value_delta_rows: list[dict[str, Any]] = []
    for row in contract_rows:
        peak_id = text_value(row.get("successor_peak_hypothesis_id"))
        sample = text_value(row.get("sample_stem"))
        if sample not in sample_columns:
            raise ValueError(f"matrix sample column not found: {sample}")
        row_index = row_index_by_peak_id.get(peak_id)
        if row_index is None:
            raise ValueError(f"successor peak hypothesis not in identity: {peak_id}")
        if row_index < 1 or row_index > len(patched_rows):
            raise ValueError(
                f"matrix_row_index out of range for {peak_id}: {row_index}"
            )
        matrix_row = patched_rows[row_index - 1]
        original = text_value(matrix_row.get(sample))
        if original:
            raise ValueError(
                "activation-copy would overwrite an existing matrix value: "
                f"{peak_id}|{sample}",
            )
        candidate_value = text_value(row.get("candidate_quant_value"))
        matrix_row[sample] = candidate_value
        value_delta_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "transition_key": row["transition_key"],
                "sample_stem": sample,
                "source_peak_hypothesis_id": row["source_peak_hypothesis_id"],
                "successor_peak_hypothesis_id": peak_id,
                "matrix_row_index": row_index,
                "source_mz": row["source_mz"],
                "source_rt": row["source_rt"],
                "successor_mz": row["successor_mz"],
                "successor_rt": row["successor_rt"],
                "successor_product_mz": row["successor_product_mz"],
                "successor_neutral_loss_tag": row["successor_neutral_loss_tag"],
                "original_matrix_value": original,
                "activated_copy_value": candidate_value,
                "candidate_quant_value": candidate_value,
                "value_changed": True,
                "authority_gate": row["authority_gate"],
                "product_authority_effect": "diagnostic_only_no_authority_change",
                "expected_product_effect": row["expected_product_effect"],
            }
        )
    return value_delta_rows


def _summary_payload(
    *,
    contract_rows: Sequence[Mapping[str, str]],
    value_delta_rows: Sequence[Mapping[str, Any]],
    alignment_matrix_tsv: Path,
    activated_matrix_tsv: Path,
    alignment_matrix_identity_tsv: Path,
    activated_identity_tsv: Path,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_label": "diagnostic_only_activated_copy_candidate",
        "activation_copy_status": "pass",
        "candidate_contract_cell_count": len(contract_rows),
        "changed_matrix_cell_count": len(value_delta_rows),
        "candidate_transition_count": len(
            {text_value(row.get("transition_key")) for row in contract_rows}
        ),
        "input_matrix_sha256": file_sha256(alignment_matrix_tsv),
        "output_matrix_sha256": file_sha256(activated_matrix_tsv),
        "input_identity_sha256": file_sha256(alignment_matrix_identity_tsv),
        "output_identity_sha256": file_sha256(activated_identity_tsv),
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "workbook_gui_changed": False,
        "candidate_rows_are_matrix_rows": False,
        "authority_statement": (
            "This is an activated-copy validation artifact only. It does not "
            "change the default matrix, ProductWriter, workbook, GUI, or "
            "Backfill authority."
        ),
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
        "--alignment-matrix-tsv",
        type=Path,
        default=DEFAULT_ALIGNMENT_MATRIX_TSV,
    )
    parser.add_argument(
        "--alignment-matrix-identity-tsv",
        type=Path,
        default=DEFAULT_ALIGNMENT_MATRIX_IDENTITY_TSV,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
