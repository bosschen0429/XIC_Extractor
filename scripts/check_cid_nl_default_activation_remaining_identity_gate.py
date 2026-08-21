"""Resolve remaining CID-NL default-activation identity blockers.

This no-RAW, no-ProductWriter gate consumes the cell-local identity gate and
turns the final known terminal blockers into explicit no-write scope removals.
It does not choose among ambiguous candidates, does not write ProductWriter
outputs, and does not grant Backfill writer authority.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
from scripts.check_cid_nl_default_activation_cell_local_identity_gate import (  # noqa: E402
    evaluate_cell_local_identity_gate,
)
from xic_extractor.tabular_io import (  # noqa: E402
    file_sha256,
    read_tsv_required,
    split_semicolon_labels,
    text_value,
    write_tsv,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "output/validation/"
    / "cid_nl_default_activation_remaining_identity_gate_v1"
)

RESOLVED_CELL_LOCAL_STATUSES = {
    "write_ready_blank",
    "superseded_by_detected_baseline",
    "cell_local_unique_detected_candidate_supersession",
}
SCOPE_REMOVAL_STATUSES = {
    "blocked_identity_missing": "scope_removed_missing_identity_no_write",
    "blocked_ambiguous_all_blank": "scope_removed_ambiguous_blank_no_write",
    "blocked_ambiguous_multiple_detected_candidates": (
        "scope_removed_ambiguous_multiple_detected_no_write"
    ),
}

REMAINING_IDENTITY_AUDIT_COLUMNS = (
    "schema_version",
    "old_peak_hypothesis_id",
    "sample_stem",
    "input_cell_local_resolution_status",
    "remaining_identity_resolution_status",
    "authority_action",
    "matrix_effect",
    "default_activation_scope",
    "scope_removal_reason",
    "candidate_new_peak_hypothesis_ids",
    "detected_candidate_peak_hypothesis_ids",
    "selected_detected_candidate_peak_hypothesis_id",
    "candidate_baseline_values",
    "candidate_coordinate_statuses",
    "new_identity_exact_peak_hypothesis_ids",
    "new_identity_source_feature_family_peak_hypothesis_ids",
    "accepted_quant_value",
    "source_row_sha256",
)


def evaluate_remaining_identity_gate(
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
    cell_local = evaluate_cell_local_identity_gate(
        old_matrix_identity_tsv=old_matrix_identity_tsv,
        new_quant_matrix_tsv=new_quant_matrix_tsv,
        new_matrix_identity_tsv=new_matrix_identity_tsv,
        production_acceptance_manifest_tsv=production_acceptance_manifest_tsv,
        expected_diff_tsv=expected_diff_tsv,
        target_preflight_summary_json=target_preflight_summary_json,
        mz_tolerance_da=mz_tolerance_da,
        rt_tolerance_min=rt_tolerance_min,
    )
    new_identity_rows = read_tsv_required(
        new_matrix_identity_tsv,
        (
            "peak_hypothesis_id",
            "source_feature_family_ids",
        ),
    )
    identity_context = _build_identity_context(new_identity_rows)
    audit_rows = _build_remaining_identity_audit_rows(
        cell_local_rows=cell_local["audit_rows"],
        identity_context=identity_context,
    )
    resolution_counts = _counts(
        row["remaining_identity_resolution_status"] for row in audit_rows
    )
    unresolved_count = sum(
        count
        for status, count in resolution_counts.items()
        if status.startswith("blocked_")
    )
    source_blockers = [
        blocker
        for blocker in cell_local.get("blockers", [])
        if not text_value(blocker).startswith("unresolved_authority_cell_count:")
    ]
    blockers = list(source_blockers)
    if unresolved_count:
        blockers.append(f"unresolved_authority_cell_count:{unresolved_count}")
    if len(audit_rows) != cell_local.get("accepted_authority_cell_count"):
        blockers.append(
            "remaining_identity_audit_count_mismatch:"
            f"accepted={cell_local.get('accepted_authority_cell_count')};"
            f"audit={len(audit_rows)}"
        )
    overall_status = "pass" if not blockers else "blocked"
    scope_removed_count = sum(
        count
        for status, count in resolution_counts.items()
        if status.startswith("scope_removed_")
    )
    detected_no_write_count = (
        resolution_counts.get("superseded_by_detected_baseline", 0)
        + resolution_counts.get("cell_local_unique_detected_candidate_supersession", 0)
    )
    return {
        "schema_version": "cid_nl_default_activation_remaining_identity_gate_v1",
        "overall_status": overall_status,
        "readiness_label": (
            "default_activation_remaining_identity_resolved_candidate"
            if overall_status == "pass"
            else "production_candidate_blocked"
        ),
        "product_surface_changed": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "workbook_or_gui_changed": False,
        "backfill_writer_authority_changed": False,
        "scope_removal_is_backfill_authority": False,
        "candidate_rows_are_matrix_rows": False,
        "default_activation_candidate_built": False,
        "default_activation_candidate_requires_separate_expected_diff_contract": True,
        "authority_statement": (
            "This gate resolves the final blocked CID-NL default-activation "
            "cells only as explicit no-write scope removals from a future "
            "activation candidate. It does not delete the current product-ready "
            "default bundle, choose ambiguous candidates, write ProductWriter "
            "outputs, or grant Backfill writer authority."
        ),
        "accepted_authority_cell_count": cell_local["accepted_authority_cell_count"],
        "input_unresolved_authority_cell_count": cell_local[
            "unresolved_authority_cell_count"
        ],
        "remaining_cells_resolved_count": scope_removed_count,
        "scope_removed_no_write_count": scope_removed_count,
        "candidate_backfill_write_count": resolution_counts.get(
            "write_ready_blank",
            0,
        ),
        "detected_baseline_no_write_count": detected_no_write_count,
        "unresolved_authority_cell_count": unresolved_count,
        "resolution_counts": resolution_counts,
        "cell_local_gate": _cell_local_summary(cell_local),
        "activation_candidate_contract": {
            "candidate_backfill_write_count": resolution_counts.get(
                "write_ready_blank",
                0,
            ),
            "detected_baseline_no_write_count": detected_no_write_count,
            "scope_removed_no_write_count": scope_removed_count,
            "total_classified_authority_cell_count": len(audit_rows),
            "unresolved_authority_cell_count": unresolved_count,
            "product_outputs_written": False,
        },
        "artifacts": _artifact_summaries(
            old_matrix_identity_tsv=old_matrix_identity_tsv,
            new_quant_matrix_tsv=new_quant_matrix_tsv,
            new_matrix_identity_tsv=new_matrix_identity_tsv,
            production_acceptance_manifest_tsv=production_acceptance_manifest_tsv,
            expected_diff_tsv=expected_diff_tsv,
        ),
        "blockers": blockers,
        "next_step": (
            "build_default_activation_candidate_expected_diff_contract"
            if overall_status == "pass"
            else "resolve_remaining_identity_gate_blockers"
        ),
        "audit_rows": audit_rows,
    }


def write_outputs(
    *,
    output_dir: Path,
    payload: Mapping[str, Any],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_tsv = output_dir / "cid_nl_default_activation_remaining_identity_audit.tsv"
    summary_json = (
        output_dir / "cid_nl_default_activation_remaining_identity_gate_summary.json"
    )
    write_tsv(
        audit_tsv,
        payload["audit_rows"],
        REMAINING_IDENTITY_AUDIT_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    summary_payload = {
        key: value for key, value in payload.items() if key != "audit_rows"
    }
    summary_payload = {
        **summary_payload,
        "outputs": {"remaining_identity_audit_tsv": _relative_or_absolute(audit_tsv)},
    }
    summary_json.write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"summary_json": summary_json, "remaining_identity_audit_tsv": audit_tsv}


def _build_remaining_identity_audit_rows(
    *,
    cell_local_rows: Sequence[Mapping[str, str]],
    identity_context: Mapping[str, dict[str, tuple[str, ...]]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in cell_local_rows:
        old_peak = row["old_peak_hypothesis_id"]
        input_status = row["cell_local_resolution_status"]
        status = _remaining_identity_status(input_status)
        context = identity_context.get(old_peak, {})
        rows.append(
            {
                "schema_version": (
                    "cid_nl_default_activation_remaining_identity_audit_v1"
                ),
                "old_peak_hypothesis_id": old_peak,
                "sample_stem": row["sample_stem"],
                "input_cell_local_resolution_status": input_status,
                "remaining_identity_resolution_status": status,
                "authority_action": _authority_action(status),
                "matrix_effect": _matrix_effect(status),
                "default_activation_scope": _default_activation_scope(status),
                "scope_removal_reason": _scope_removal_reason(status),
                "candidate_new_peak_hypothesis_ids": row[
                    "candidate_new_peak_hypothesis_ids"
                ],
                "detected_candidate_peak_hypothesis_ids": row[
                    "detected_candidate_peak_hypothesis_ids"
                ],
                "selected_detected_candidate_peak_hypothesis_id": row[
                    "selected_detected_candidate_peak_hypothesis_id"
                ],
                "candidate_baseline_values": row["candidate_baseline_values"],
                "candidate_coordinate_statuses": row["candidate_coordinate_statuses"],
                "new_identity_exact_peak_hypothesis_ids": ";".join(
                    context.get("exact", ())
                ),
                "new_identity_source_feature_family_peak_hypothesis_ids": ";".join(
                    context.get("source", ())
                ),
                "accepted_quant_value": row["accepted_quant_value"],
                "source_row_sha256": row["source_row_sha256"],
            }
        )
    return rows


def _remaining_identity_status(input_status: str) -> str:
    if input_status in RESOLVED_CELL_LOCAL_STATUSES:
        return input_status
    if input_status in SCOPE_REMOVAL_STATUSES:
        return SCOPE_REMOVAL_STATUSES[input_status]
    return "blocked_unresolved_cell_local_status"


def _authority_action(status: str) -> str:
    if status == "write_ready_blank":
        return "candidate_backfill_write"
    if status in {
        "superseded_by_detected_baseline",
        "cell_local_unique_detected_candidate_supersession",
    }:
        return "no_backfill_write_detected_value_already_present"
    if status.startswith("scope_removed_"):
        return "no_backfill_write_legacy_claim_removed_from_candidate_scope"
    return "none"


def _matrix_effect(status: str) -> str:
    if status == "write_ready_blank":
        return "write_accepted_backfill"
    if status in {
        "superseded_by_detected_baseline",
        "cell_local_unique_detected_candidate_supersession",
    }:
        return "preserve_detected_baseline"
    if status.startswith("scope_removed_"):
        return "no_write_scope_removed"
    return "blocked"


def _default_activation_scope(status: str) -> str:
    if status == "write_ready_blank":
        return "candidate_write_scope"
    if status in {
        "superseded_by_detected_baseline",
        "cell_local_unique_detected_candidate_supersession",
    }:
        return "candidate_no_write_detected_baseline_scope"
    if status.startswith("scope_removed_"):
        return "removed_from_candidate_scope"
    return "blocked"


def _scope_removal_reason(status: str) -> str:
    if status == "scope_removed_missing_identity_no_write":
        return "no_safe_new_identity_bridge_for_legacy_authority_cell"
    if status == "scope_removed_ambiguous_blank_no_write":
        return "ambiguous_candidates_all_blank_no_safe_write_target"
    if status == "scope_removed_ambiguous_multiple_detected_no_write":
        return "ambiguous_candidates_multiple_detected_no_canonical_choice"
    return ""


def _build_identity_context(
    identity_rows: Sequence[Mapping[str, str]],
) -> dict[str, dict[str, tuple[str, ...]]]:
    exact: dict[str, list[str]] = {}
    source: dict[str, list[str]] = {}
    for row in identity_rows:
        peak = text_value(row.get("peak_hypothesis_id"))
        if peak:
            exact.setdefault(peak, []).append(peak)
        for source_id in split_semicolon_labels(row.get("source_feature_family_ids")):
            if source_id and peak:
                source.setdefault(source_id, []).append(peak)
    keys = set(exact) | set(source)
    return {
        key: {
            "exact": tuple(dict.fromkeys(exact.get(key, []))),
            "source": tuple(dict.fromkeys(source.get(key, []))),
        }
        for key in keys
    }


def _counts(values: Sequence[str] | Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = text_value(value) or "<blank>"
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _cell_local_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "overall_status": payload.get("overall_status", ""),
        "readiness_label": payload.get("readiness_label", ""),
        "resolution_counts": payload.get("resolution_counts", {}),
        "reconstruction_gate": payload.get("reconstruction_gate", {}),
        "cell_local_resolved_ambiguous_count": payload.get(
            "cell_local_resolved_ambiguous_count",
            0,
        ),
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
        help="Return non-zero if the remaining-identity gate is blocked.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    payload = evaluate_remaining_identity_gate(
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
    print(
        "cid_nl_default_activation_remaining_identity_summary: "
        f"{outputs['summary_json']}"
    )
    print(
        "cid_nl_default_activation_remaining_identity_audit: "
        f"{outputs['remaining_identity_audit_tsv']}"
    )
    print(
        "cid_nl_default_activation_remaining_identity_status: "
        f"{payload['overall_status']}"
    )
    if args.require_pass and payload["overall_status"] != "pass":
        for blocker in payload["blockers"]:
            print(f"cid_nl_default_activation_remaining_identity_blocker: {blocker}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
