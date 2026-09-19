"""Check cell-local CID-NL identity resolution for default activation.

This no-write gate narrows ambiguous canonical identity blockers from the
authority reconstruction gate. An ambiguous cell is resolved only when exactly
one candidate row already has a detected baseline value for that sample; that
state is a no-write detected-baseline supersession, not Backfill authority.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_cid_nl_default_activation_authority_reconstruction_gate import (  # noqa: E402
    evaluate_authority_reconstruction_gate,
)
from scripts.check_cid_nl_default_activation_bridge_gate import (  # noqa: E402
    DEFAULT_EXPECTED_DIFF_TSV,
    DEFAULT_MZ_TOLERANCE_DA,
    DEFAULT_NEW_MATRIX_IDENTITY_TSV,
    DEFAULT_NEW_QUANT_MATRIX_TSV,
    DEFAULT_OLD_MATRIX_IDENTITY_TSV,
    DEFAULT_PRODUCTION_ACCEPTANCE_MANIFEST_TSV,
    DEFAULT_RT_TOLERANCE_MIN,
    DEFAULT_TARGET_PREFLIGHT_SUMMARY_JSON,
)
from xic_extractor.tabular_io import (  # noqa: E402
    file_sha256,
    numeric_equal,
    read_tsv_required,
    read_tsv_with_header,
    split_semicolon_labels,
    text_value,
    write_tsv,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "output/validation/"
    / "cid_nl_default_activation_cell_local_identity_gate"
)

CELL_LOCAL_AUDIT_COLUMNS = (
    "schema_version",
    "old_peak_hypothesis_id",
    "sample_stem",
    "input_resolution_status",
    "cell_local_resolution_status",
    "authority_action",
    "matrix_effect",
    "blocker_reason",
    "candidate_new_peak_hypothesis_ids",
    "detected_candidate_peak_hypothesis_ids",
    "selected_detected_candidate_peak_hypothesis_id",
    "candidate_baseline_values",
    "candidate_coordinate_statuses",
    "accepted_quant_value",
    "source_row_sha256",
)


@dataclass(frozen=True)
class CandidateMatrixRow:
    row: Mapping[str, str]
    coordinate_valid: bool


def evaluate_cell_local_identity_gate(
    *,
    old_matrix_identity_tsv: Path,
    new_quant_matrix_tsv: Path,
    new_matrix_identity_tsv: Path,
    production_acceptance_manifest_tsv: Path,
    expected_diff_tsv: Path,
    target_preflight_summary_json: Path | None = DEFAULT_TARGET_PREFLIGHT_SUMMARY_JSON,
    mz_tolerance_da: float = DEFAULT_MZ_TOLERANCE_DA,
    rt_tolerance_min: float = DEFAULT_RT_TOLERANCE_MIN,
) -> dict[str, Any]:
    reconstruction = evaluate_authority_reconstruction_gate(
        old_matrix_identity_tsv=old_matrix_identity_tsv,
        new_quant_matrix_tsv=new_quant_matrix_tsv,
        new_matrix_identity_tsv=new_matrix_identity_tsv,
        production_acceptance_manifest_tsv=production_acceptance_manifest_tsv,
        expected_diff_tsv=expected_diff_tsv,
        target_preflight_summary_json=target_preflight_summary_json,
        mz_tolerance_da=mz_tolerance_da,
        rt_tolerance_min=rt_tolerance_min,
    )
    _header, matrix_rows = read_tsv_with_header(
        new_quant_matrix_tsv,
        required_columns=("Mz", "RT"),
    )
    identity_rows = read_tsv_required(
        new_matrix_identity_tsv,
        (
            "matrix_row_index",
            "Mz",
            "RT",
            "peak_hypothesis_id",
        ),
    )
    matrix_by_peak = _matrix_rows_by_peak(identity_rows, matrix_rows)
    audit_rows = _build_cell_local_audit_rows(
        reconstruction_rows=reconstruction["audit_rows"],
        matrix_by_peak=matrix_by_peak,
    )
    resolution_counts = _counts(
        row["cell_local_resolution_status"] for row in audit_rows
    )
    unresolved_count = sum(
        count
        for status, count in resolution_counts.items()
        if status.startswith("blocked_")
    )
    blockers: list[str] = []
    if reconstruction["candidate_replay"].get("status") != "pass":
        blockers.append(
            "reconstruction_candidate_replay_not_pass:"
            + text_value(reconstruction["candidate_replay"].get("reason"))
        )
    if unresolved_count:
        blockers.append(f"unresolved_authority_cell_count:{unresolved_count}")
    overall_status = "pass" if not blockers else "blocked"
    return {
        "schema_version": "cid_nl_default_activation_cell_local_identity_gate",
        "overall_status": overall_status,
        "readiness_label": (
            "default_activation_cell_local_identity_candidate"
            if overall_status == "pass"
            else "production_candidate_blocked"
        ),
        "product_surface_changed": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "workbook_or_gui_changed": False,
        "backfill_writer_authority_changed": False,
        "cell_local_detected_candidate_is_backfill_authority": False,
        "authority_statement": (
            "Cell-local unique detected candidates only resolve no-write "
            "detected-baseline supersession. They do not grant Backfill writer "
            "authority and cannot authorize blank writes."
        ),
        "accepted_authority_cell_count": reconstruction[
            "accepted_authority_cell_count"
        ],
        "reconstruction_unresolved_authority_cell_count": reconstruction[
            "unresolved_authority_cell_count"
        ],
        "cell_local_resolved_ambiguous_count": resolution_counts.get(
            "cell_local_unique_detected_candidate_supersession",
            0,
        ),
        "unresolved_authority_cell_count": unresolved_count,
        "resolution_counts": resolution_counts,
        "reconstruction_gate": _reconstruction_summary(reconstruction),
        "artifacts": _artifact_summaries(
            old_matrix_identity_tsv=old_matrix_identity_tsv,
            new_quant_matrix_tsv=new_quant_matrix_tsv,
            new_matrix_identity_tsv=new_matrix_identity_tsv,
            production_acceptance_manifest_tsv=production_acceptance_manifest_tsv,
            expected_diff_tsv=expected_diff_tsv,
        ),
        "blockers": blockers,
        "next_step": (
            "build_default_activation_candidate"
            if overall_status == "pass"
            else "resolve_missing_or_unresolved_ambiguous_identity"
        ),
        "audit_rows": audit_rows,
    }


def write_outputs(
    *,
    output_dir: Path,
    payload: Mapping[str, Any],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_tsv = output_dir / "cid_nl_default_activation_cell_local_identity_audit.tsv"
    summary_json = (
        output_dir / "cid_nl_default_activation_cell_local_identity_gate_summary.json"
    )
    write_tsv(
        audit_tsv,
        payload["audit_rows"],
        CELL_LOCAL_AUDIT_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    summary_payload = {
        key: value for key, value in payload.items() if key != "audit_rows"
    }
    summary_payload = {
        **summary_payload,
        "outputs": {"cell_local_audit_tsv": _relative_or_absolute(audit_tsv)},
    }
    summary_json.write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"summary_json": summary_json, "cell_local_audit_tsv": audit_tsv}


def _build_cell_local_audit_rows(
    *,
    reconstruction_rows: Sequence[Mapping[str, str]],
    matrix_by_peak: Mapping[str, CandidateMatrixRow],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in reconstruction_rows:
        sample_stem = row["sample_stem"]
        candidates = split_semicolon_labels(
            row.get("candidate_new_peak_hypothesis_ids")
        )
        candidate_values = [
            (
                candidate,
                _candidate_sample_value(matrix_by_peak.get(candidate), sample_stem),
            )
            for candidate in candidates
        ]
        coordinate_statuses = [
            (candidate, _candidate_coordinate_status(matrix_by_peak.get(candidate)))
            for candidate in candidates
        ]
        detected_candidates = [
            candidate for candidate, value in candidate_values if value
        ]
        status = _cell_local_status(
            row,
            detected_candidates,
            candidate_coordinate_statuses=tuple(
                status for _candidate, status in coordinate_statuses
            ),
        )
        selected = detected_candidates[0] if len(detected_candidates) == 1 else ""
        rows.append(
            {
                "schema_version": (
                    "cid_nl_default_activation_cell_local_identity_audit"
                ),
                "old_peak_hypothesis_id": row["old_peak_hypothesis_id"],
                "sample_stem": sample_stem,
                "input_resolution_status": row["resolution_status"],
                "cell_local_resolution_status": status,
                "authority_action": _authority_action(status),
                "matrix_effect": _matrix_effect(status),
                "blocker_reason": _blocker_reason(status, row),
                "candidate_new_peak_hypothesis_ids": ";".join(candidates),
                "detected_candidate_peak_hypothesis_ids": ";".join(
                    detected_candidates
                ),
                "selected_detected_candidate_peak_hypothesis_id": selected,
                "candidate_baseline_values": ";".join(
                    f"{candidate}={value}" for candidate, value in candidate_values
                ),
                "candidate_coordinate_statuses": ";".join(
                    f"{candidate}={status}"
                    for candidate, status in coordinate_statuses
                ),
                "accepted_quant_value": row["accepted_quant_value"],
                "source_row_sha256": row["source_row_sha256"],
            }
        )
    return rows


def _cell_local_status(
    row: Mapping[str, str],
    detected_candidates: Sequence[str],
    *,
    candidate_coordinate_statuses: Sequence[str],
) -> str:
    input_status = row["resolution_status"]
    if input_status == "blocked_identity_ambiguous":
        if any(status != "ok" for status in candidate_coordinate_statuses):
            return "blocked_ambiguous_identity_matrix_coordinate_mismatch"
        if len(detected_candidates) == 1:
            return "cell_local_unique_detected_candidate_supersession"
        if not detected_candidates:
            return "blocked_ambiguous_all_blank"
        return "blocked_ambiguous_multiple_detected_candidates"
    return input_status


def _authority_action(status: str) -> str:
    if status == "write_ready_blank":
        return "candidate_backfill_write"
    if status in {
        "superseded_by_detected_baseline",
        "cell_local_unique_detected_candidate_supersession",
    }:
        return "no_backfill_write_detected_value_already_present"
    return "none"


def _matrix_effect(status: str) -> str:
    if status == "write_ready_blank":
        return "write_accepted_backfill"
    if status in {
        "superseded_by_detected_baseline",
        "cell_local_unique_detected_candidate_supersession",
    }:
        return "preserve_detected_baseline"
    return "blocked"


def _blocker_reason(status: str, row: Mapping[str, str]) -> str:
    if not status.startswith("blocked_"):
        return ""
    if status == "blocked_ambiguous_all_blank":
        return "ambiguous_candidates_all_blank_for_sample"
    if status == "blocked_ambiguous_multiple_detected_candidates":
        return "ambiguous_candidates_multiple_detected_for_sample"
    if status == "blocked_ambiguous_identity_matrix_coordinate_mismatch":
        return "ambiguous_candidate_identity_matrix_coordinate_mismatch"
    return text_value(row.get("blocker_reason")) or status


def _candidate_sample_value(
    candidate: CandidateMatrixRow | None,
    sample_stem: str,
) -> str:
    if candidate is None or not candidate.coordinate_valid:
        return ""
    return text_value(candidate.row.get(sample_stem))


def _candidate_coordinate_status(candidate: CandidateMatrixRow | None) -> str:
    if candidate is None:
        return "missing_identity_row"
    return "ok" if candidate.coordinate_valid else "matrix_coordinate_mismatch"


def _matrix_rows_by_peak(
    identity_rows: Sequence[Mapping[str, str]],
    matrix_rows: Sequence[Mapping[str, str]],
) -> dict[str, CandidateMatrixRow]:
    rows: dict[str, CandidateMatrixRow] = {}
    for identity in identity_rows:
        peak = text_value(identity.get("peak_hypothesis_id"))
        row_index = int(text_value(identity.get("matrix_row_index"))) - 1
        if peak and 0 <= row_index < len(matrix_rows):
            matrix_row = matrix_rows[row_index]
            rows[peak] = CandidateMatrixRow(
                row=matrix_row,
                coordinate_valid=(
                    numeric_equal(identity.get("Mz"), matrix_row.get("Mz"))
                    and numeric_equal(identity.get("RT"), matrix_row.get("RT"))
                ),
            )
    return rows


def _counts(values: Sequence[str] | Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = text_value(value) or "<blank>"
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _reconstruction_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "overall_status": payload.get("overall_status", ""),
        "readiness_label": payload.get("readiness_label", ""),
        "candidate_replay": payload.get("candidate_replay", {}),
        "resolution_counts": payload.get("resolution_counts", {}),
        "unresolved_authority_cell_count": payload.get(
            "unresolved_authority_cell_count",
            0,
        ),
    }


def _artifact_summaries(**paths: Path) -> dict[str, dict[str, Any]]:
    summaries = {}
    for label, path in paths.items():
        summaries[label] = {
            "path": _relative_or_absolute(path),
            "sha256": file_sha256(path),
            "size_bytes": path.stat().st_size,
        }
    return summaries


def _relative_or_absolute(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--old-matrix-identity-tsv",
        type=Path,
        default=DEFAULT_OLD_MATRIX_IDENTITY_TSV,
    )
    parser.add_argument(
        "--new-quant-matrix-tsv",
        type=Path,
        default=DEFAULT_NEW_QUANT_MATRIX_TSV,
    )
    parser.add_argument(
        "--new-matrix-identity-tsv",
        type=Path,
        default=DEFAULT_NEW_MATRIX_IDENTITY_TSV,
    )
    parser.add_argument(
        "--production-acceptance-manifest-tsv",
        type=Path,
        default=DEFAULT_PRODUCTION_ACCEPTANCE_MANIFEST_TSV,
    )
    parser.add_argument(
        "--expected-diff-tsv",
        type=Path,
        default=DEFAULT_EXPECTED_DIFF_TSV,
    )
    parser.add_argument(
        "--target-preflight-summary-json",
        type=Path,
        default=DEFAULT_TARGET_PREFLIGHT_SUMMARY_JSON,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--mz-tolerance-da",
        type=float,
        default=DEFAULT_MZ_TOLERANCE_DA,
    )
    parser.add_argument(
        "--rt-tolerance-min",
        type=float,
        default=DEFAULT_RT_TOLERANCE_MIN,
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Return non-zero if the cell-local identity gate is blocked.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    payload = evaluate_cell_local_identity_gate(
        old_matrix_identity_tsv=args.old_matrix_identity_tsv,
        new_quant_matrix_tsv=args.new_quant_matrix_tsv,
        new_matrix_identity_tsv=args.new_matrix_identity_tsv,
        production_acceptance_manifest_tsv=args.production_acceptance_manifest_tsv,
        expected_diff_tsv=args.expected_diff_tsv,
        target_preflight_summary_json=args.target_preflight_summary_json,
        mz_tolerance_da=args.mz_tolerance_da,
        rt_tolerance_min=args.rt_tolerance_min,
    )
    outputs = write_outputs(output_dir=args.output_dir, payload=payload)
    print(f"cid_nl_default_activation_cell_local_summary: {outputs['summary_json']}")
    print(
        "cid_nl_default_activation_cell_local_audit: "
        f"{outputs['cell_local_audit_tsv']}"
    )
    print(f"cid_nl_default_activation_cell_local_status: {payload['overall_status']}")
    if args.require_pass and payload["overall_status"] != "pass":
        for blocker in payload["blockers"]:
            print(f"cid_nl_default_activation_cell_local_blocker: {blocker}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
