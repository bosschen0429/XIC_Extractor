"""Build the CID-NL default-activation successor authority contract.

This no-RAW gate converts the resolved CID-NL identity audit into a successor
ProductionAcceptanceManifest allow-list, an expected-diff packet, a full
rule-selected decision ledger, and an in-repo candidate QuantMatrixVersion replay.
It establishes a reviewable successor authority packet; it does not install a
new default ProductWriter output, workbook, GUI state, or active matrix bundle.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_quant_matrix_version import run_activation  # noqa: E402
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
from scripts.check_cid_nl_default_activation_remaining_identity_gate import (  # noqa: E402
    evaluate_remaining_identity_gate,
)
from scripts.check_production_acceptance_manifest import (  # noqa: E402
    REQUIRED_COLUMNS as PRODUCTION_ACCEPTANCE_COLUMNS,
)
from scripts.check_production_acceptance_manifest import (  # noqa: E402
    production_acceptance_manifest_sha256,
)
from xic_extractor.alignment.quant_matrix_version import (  # noqa: E402
    EXPECTED_DIFF_COLUMNS,
    EXPECTED_DIFF_SUMMARY_COLUMNS,
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


def artifact_record(path: Path, *, base_dir: Path) -> dict[str, str]:
    return {
        "path": path.resolve(strict=False).relative_to(
            base_dir.resolve(strict=False),
        ).as_posix(),
        "sha256": file_sha256(path),
    }


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "output/validation/"
    / "cid_nl_default_activation_successor_authority_contract"
)

SUCCESSOR_SCHEMA = "cid_nl_default_activation_successor_authority_contract"
DECISION_SCHEMA = "cid_nl_default_activation_successor_authority_decision"
DECISION_COLUMNS = (
    "schema_version",
    "old_peak_hypothesis_id",
    "sample_stem",
    "successor_peak_hypothesis_id",
    "successor_decision",
    "write_authority",
    "matrix_write_allowed",
    "matrix_effect",
    "default_activation_scope",
    "human_explanation",
    "input_resolution_status",
    "candidate_new_peak_hypothesis_ids",
    "detected_candidate_peak_hypothesis_ids",
    "candidate_baseline_values",
    "candidate_coordinate_statuses",
    "accepted_quant_value",
    "source_row_sha256",
)

WRITE_READY = "write_ready_blank"
DETECTED_NO_WRITE = {
    "superseded_by_detected_baseline",
    "cell_local_unique_detected_candidate_supersession",
}
SCOPE_REMOVED = {
    "scope_removed_missing_identity_no_write",
    "scope_removed_ambiguous_blank_no_write",
    "scope_removed_ambiguous_multiple_detected_no_write",
}


def build_successor_authority_contract(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    source_root: Path = ROOT,
    old_matrix_identity_tsv: Path = DEFAULT_OLD_MATRIX_IDENTITY_TSV,
    new_quant_matrix_tsv: Path = DEFAULT_NEW_QUANT_MATRIX_TSV,
    new_matrix_identity_tsv: Path = DEFAULT_NEW_MATRIX_IDENTITY_TSV,
    production_acceptance_manifest_tsv: Path = (
        DEFAULT_PRODUCTION_ACCEPTANCE_MANIFEST_TSV
    ),
    expected_diff_tsv: Path = DEFAULT_EXPECTED_DIFF_TSV,
    target_preflight_summary_json: Path | None = DEFAULT_TARGET_PREFLIGHT_SUMMARY_JSON,
    mz_tolerance_da: float = DEFAULT_MZ_TOLERANCE_DA,
    rt_tolerance_min: float = DEFAULT_RT_TOLERANCE_MIN,
) -> dict[str, Any]:
    remaining = evaluate_remaining_identity_gate(
        old_matrix_identity_tsv=old_matrix_identity_tsv,
        new_quant_matrix_tsv=new_quant_matrix_tsv,
        new_matrix_identity_tsv=new_matrix_identity_tsv,
        production_acceptance_manifest_tsv=production_acceptance_manifest_tsv,
        expected_diff_tsv=expected_diff_tsv,
        target_preflight_summary_json=target_preflight_summary_json,
        mz_tolerance_da=mz_tolerance_da,
        rt_tolerance_min=rt_tolerance_min,
    )
    source_blockers = list(remaining.get("blockers", ()))
    output_dir.mkdir(parents=True, exist_ok=True)

    old_manifest_rows = read_tsv_required(
        production_acceptance_manifest_tsv,
        PRODUCTION_ACCEPTANCE_COLUMNS,
    )
    old_manifest_by_key = _manifest_rows_by_key(old_manifest_rows)
    build = _build_successor_rows(
        remaining_rows=remaining["audit_rows"],
        old_manifest_by_key=old_manifest_by_key,
    )
    blockers = [*source_blockers, *build["blockers"]]

    authority_manifest_tsv = output_dir / "successor_authority_manifest.tsv"
    expected_diff_out_tsv = output_dir / "successor_expected_diff.tsv"
    decisions_tsv = output_dir / "successor_authority_decisions.tsv"
    write_tsv(
        authority_manifest_tsv,
        build["manifest_rows"],
        PRODUCTION_ACCEPTANCE_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    write_tsv(
        expected_diff_out_tsv,
        build["expected_diff_rows"],
        EXPECTED_DIFF_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    write_tsv(
        decisions_tsv,
        build["decision_rows"],
        DECISION_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )

    activation_outputs: Mapping[str, Path] = {}
    replay_summary: dict[str, Any] = {
        "status": "not_run",
        "reason": "blockers_present",
    }
    matrix_delta_summary: dict[str, Any] = {
        "status": "not_run",
        "reason": "blockers_present",
    }
    if not blockers:
        activation_outputs = run_activation(
            input_quant_matrix_tsv=new_quant_matrix_tsv,
            input_matrix_identity_tsv=new_matrix_identity_tsv,
            production_acceptance_manifest_tsv=authority_manifest_tsv,
            expected_diff_tsv=expected_diff_out_tsv,
            output_dir=output_dir / "candidate_quant_matrix_version",
            manifest_root=source_root,
        )
        _rewrite_source_summary_paths(activation_outputs["source_summary"])
        replay_summary = _replay_summary(activation_outputs["expected_diff_summary"])
        matrix_delta_summary = _matrix_delta_summary(
            baseline_quant_matrix_tsv=new_quant_matrix_tsv,
            candidate_quant_matrix_tsv=activation_outputs["quant_matrix"],
            matrix_identity_tsv=new_matrix_identity_tsv,
            expected_write_keys={
                (row["peak_hypothesis_id"], row["sample_stem"])
                for row in build["manifest_rows"]
            },
            expected_values={
                (row["peak_hypothesis_id"], row["sample_stem"]): row["quant_value"]
                for row in build["manifest_rows"]
            },
        )
        if replay_summary.get("status") != "pass":
            blockers.append(
                "successor_replay_not_pass:" + text_value(replay_summary.get("reason"))
            )
        if matrix_delta_summary.get("status") != "pass":
            blockers.append(
                "successor_matrix_delta_not_pass:"
                + text_value(matrix_delta_summary.get("reason"))
            )

    overall_status = "pass" if not blockers else "blocked"
    summary_json = (
        output_dir / "cid_nl_default_activation_successor_authority_summary.json"
    )
    payload = {
        "schema_version": SUCCESSOR_SCHEMA,
        "overall_status": overall_status,
        "readiness_label": (
            "cid_nl_default_activation_successor_authority_candidate"
            if overall_status == "pass"
            else "production_candidate_blocked"
        ),
        "product_surface_changed": True,
        "successor_authority_contract_built": overall_status == "pass",
        "successor_authority_write_count": len(build["manifest_rows"]),
        "successor_expected_diff_count": len(build["expected_diff_rows"]),
        "detected_baseline_no_write_count": build["detected_no_write_count"],
        "scope_removed_no_write_count": build["scope_removed_count"],
        "total_decision_count": len(build["decision_rows"]),
        "unresolved_decision_count": build["blocked_count"],
        "default_product_activation_changed": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "default_matrix_files_written": False,
        "candidate_quant_matrix_sidecar_written": bool(activation_outputs),
        "workbook_or_gui_changed": False,
        "backfill_writer_authority_changed": False,
        "cid_nl_ms2_direct_productwriter_authority": False,
        "candidate_rows_are_matrix_rows": False,
        "authority_statement": (
            "The successor authority contract is an allow-list derived from "
            "the resolved CID-NL identity gates. It authorizes only the 147 "
            "safe blank cells for a candidate activation replay, preserves "
            "detected baseline cells as no-write, and omits scope-removed cells. "
            "It is not installed as the active default ProductWriter output."
        ),
        "remaining_identity_gate": _remaining_summary(remaining),
        "decision_counts": _counts(
            row["successor_decision"] for row in build["decision_rows"]
        ),
        "replay_summary": replay_summary,
        "matrix_delta_summary": matrix_delta_summary,
        "artifacts": {
            "successor_authority_manifest_tsv": artifact_record(
                authority_manifest_tsv,
                base_dir=output_dir,
            ),
            "successor_expected_diff_tsv": artifact_record(
                expected_diff_out_tsv,
                base_dir=output_dir,
            ),
            "successor_authority_decisions_tsv": artifact_record(
                decisions_tsv,
                base_dir=output_dir,
            ),
            **{
                f"candidate_{label}": artifact_record(path, base_dir=output_dir)
                for label, path in activation_outputs.items()
            },
        },
        "input_artifacts": _artifact_summaries(
            old_matrix_identity_tsv=old_matrix_identity_tsv,
            new_quant_matrix_tsv=new_quant_matrix_tsv,
            new_matrix_identity_tsv=new_matrix_identity_tsv,
            production_acceptance_manifest_tsv=production_acceptance_manifest_tsv,
            expected_diff_tsv=expected_diff_tsv,
        ),
        "blockers": blockers,
        "next_step": (
            "human_review_and_default_activation_candidate_adoption"
            if overall_status == "pass"
            else "resolve_successor_authority_contract_blockers"
        ),
        "outputs": {
            "summary_json": _relative_or_absolute(summary_json),
            "successor_authority_manifest_tsv": _relative_or_absolute(
                authority_manifest_tsv
            ),
            "successor_expected_diff_tsv": _relative_or_absolute(
                expected_diff_out_tsv
            ),
            "successor_authority_decisions_tsv": _relative_or_absolute(decisions_tsv),
        },
    }
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _build_successor_rows(
    *,
    remaining_rows: Sequence[Mapping[str, str]],
    old_manifest_by_key: Mapping[tuple[str, str], Mapping[str, str]],
) -> dict[str, Any]:
    manifest_rows: list[dict[str, str]] = []
    expected_diff_rows: list[dict[str, str]] = []
    decision_rows: list[dict[str, str]] = []
    blockers: list[str] = []
    for row in remaining_rows:
        old_peak = row["old_peak_hypothesis_id"]
        sample = row["sample_stem"]
        status = row["remaining_identity_resolution_status"]
        old_manifest = old_manifest_by_key.get((old_peak, sample))
        if old_manifest is None:
            blockers.append(f"{old_peak}/{sample}: old manifest authority row missing")
            old_manifest = {}
        successor_peak = _successor_peak_for_status(row, blockers)
        if status == WRITE_READY and old_manifest:
            manifest_row = {
                column: old_manifest.get(column, "")
                for column in PRODUCTION_ACCEPTANCE_COLUMNS
            }
            manifest_row["peak_hypothesis_id"] = successor_peak
            manifest_row["feature_family_id"] = successor_peak
            manifest_row["decision_reason"] = (
                "cid_nl_successor_authority_contract:"
                f"old_peak={old_peak};status={status}"
            )
            manifest_row["closure_rule_ids"] = _append_token(
                manifest_row.get("closure_rule_ids", ""),
                "cid_nl_successor_authority_contract",
            )
            manifest_row["manifest_sha256"] = ""
            manifest_rows.append(manifest_row)
            expected_diff_rows.append(
                {
                    "schema_version": "quant_matrix_version_expected_diff",
                    "peak_hypothesis_id": successor_peak,
                    "sample_stem": sample,
                    "baseline_value": "",
                    "activated_value": row["accepted_quant_value"],
                    "expected_matrix_effect": "write_accepted_backfill",
                    "expected_reason": (
                        "cid_nl_successor_authority_contract:"
                        f"old_peak={old_peak}"
                    ),
                }
            )
        decision_rows.append(_decision_row(row, successor_peak=successor_peak))
    manifest_sha = production_acceptance_manifest_sha256(manifest_rows)
    for manifest_row in manifest_rows:
        manifest_row["manifest_sha256"] = manifest_sha
    return {
        "manifest_rows": manifest_rows,
        "expected_diff_rows": expected_diff_rows,
        "decision_rows": decision_rows,
        "detected_no_write_count": sum(
            1
            for row in decision_rows
            if row["successor_decision"] == "no_write_detected_baseline_preserved"
        ),
        "scope_removed_count": sum(
            1
            for row in decision_rows
            if row["successor_decision"] == "no_write_omitted"
        ),
        "blocked_count": sum(
            1
            for row in decision_rows
            if row["successor_decision"].startswith("blocked")
        ),
        "blockers": blockers,
    }


def _successor_peak_for_status(
    row: Mapping[str, str],
    blockers: list[str],
) -> str:
    status = row["remaining_identity_resolution_status"]
    label = f"{row['old_peak_hypothesis_id']}/{row['sample_stem']}"
    if status == WRITE_READY:
        candidates = split_semicolon_labels(
            row.get("candidate_new_peak_hypothesis_ids")
        )
        if len(candidates) != 1:
            blockers.append(f"{label}: write-ready row must have one successor peak")
            return ""
        expected_coordinate = f"{candidates[0]}=ok"
        coordinate_statuses = split_semicolon_labels(
            row.get("candidate_coordinate_statuses")
        )
        if expected_coordinate not in coordinate_statuses:
            blockers.append(f"{label}: write-ready successor coordinate is not ok")
            return ""
        return candidates[0]
    if status in DETECTED_NO_WRITE:
        selected = text_value(row.get("selected_detected_candidate_peak_hypothesis_id"))
        if selected:
            return selected
        candidates = split_semicolon_labels(
            row.get("candidate_new_peak_hypothesis_ids")
        )
        return candidates[0] if len(candidates) == 1 else ""
    return ""


def _decision_row(row: Mapping[str, str], *, successor_peak: str) -> dict[str, str]:
    status = row["remaining_identity_resolution_status"]
    if status == WRITE_READY:
        decision = "write_authorized"
        explanation = (
            "Safe successor row is unique and blank for this sample; this cell is "
            "included in the successor write allow-list."
        )
        write_authority = "TRUE"
        matrix_write_allowed = "TRUE"
    elif status in DETECTED_NO_WRITE:
        decision = "no_write_detected_baseline_preserved"
        explanation = (
            "The successor baseline already has a detected value; Backfill must "
            "not overwrite it."
        )
        write_authority = "FALSE"
        matrix_write_allowed = "FALSE"
    elif status in SCOPE_REMOVED:
        decision = "no_write_omitted"
        explanation = (
            "No single safe successor write target exists; this legacy claim is "
            "omitted from the successor activation candidate."
        )
        write_authority = "FALSE"
        matrix_write_allowed = "FALSE"
    else:
        decision = "blocked_unresolved"
        explanation = "Unrecognized or blocked identity state; no authority granted."
        write_authority = "FALSE"
        matrix_write_allowed = "FALSE"
    return {
        "schema_version": DECISION_SCHEMA,
        "old_peak_hypothesis_id": row["old_peak_hypothesis_id"],
        "sample_stem": row["sample_stem"],
        "successor_peak_hypothesis_id": successor_peak,
        "successor_decision": decision,
        "write_authority": write_authority,
        "matrix_write_allowed": matrix_write_allowed,
        "matrix_effect": row["matrix_effect"],
        "default_activation_scope": row["default_activation_scope"],
        "human_explanation": explanation,
        "input_resolution_status": status,
        "candidate_new_peak_hypothesis_ids": row["candidate_new_peak_hypothesis_ids"],
        "detected_candidate_peak_hypothesis_ids": row[
            "detected_candidate_peak_hypothesis_ids"
        ],
        "candidate_baseline_values": row["candidate_baseline_values"],
        "candidate_coordinate_statuses": row["candidate_coordinate_statuses"],
        "accepted_quant_value": row["accepted_quant_value"],
        "source_row_sha256": row["source_row_sha256"],
    }


def _matrix_delta_summary(
    *,
    baseline_quant_matrix_tsv: Path,
    candidate_quant_matrix_tsv: Path,
    matrix_identity_tsv: Path,
    expected_write_keys: set[tuple[str, str]],
    expected_values: Mapping[tuple[str, str], str],
) -> dict[str, Any]:
    baseline_header, baseline_rows = read_tsv_with_header(baseline_quant_matrix_tsv)
    candidate_header, candidate_rows = read_tsv_with_header(candidate_quant_matrix_tsv)
    if baseline_header != candidate_header:
        return {"status": "blocked", "reason": "matrix_header_mismatch"}
    if len(baseline_rows) != len(candidate_rows):
        return {"status": "blocked", "reason": "matrix_row_count_mismatch"}
    identity_rows = read_tsv_required(
        matrix_identity_tsv,
        ("matrix_row_index", "peak_hypothesis_id"),
    )
    peak_by_index = {
        int(row["matrix_row_index"]): row["peak_hypothesis_id"]
        for row in identity_rows
    }
    sample_columns = tuple(
        column for column in baseline_header if column not in {"Mz", "RT"}
    )
    changed_keys: set[tuple[str, str]] = set()
    unexpected_values: list[str] = []
    for row_index, (baseline, candidate) in enumerate(
        zip(baseline_rows, candidate_rows, strict=True),
        start=1,
    ):
        peak = peak_by_index.get(row_index, "")
        for sample in sample_columns:
            if text_value(baseline.get(sample)) == text_value(candidate.get(sample)):
                continue
            key = (peak, sample)
            changed_keys.add(key)
            expected_value = expected_values.get(key, "")
            if not numeric_equal(candidate.get(sample), expected_value):
                unexpected_values.append(f"{peak}/{sample}")
    missing_writes = sorted(expected_write_keys - changed_keys)
    unexpected_writes = sorted(changed_keys - expected_write_keys)
    if missing_writes or unexpected_writes or unexpected_values:
        return {
            "status": "blocked",
            "reason": "matrix_delta_mismatch",
            "changed_cell_count": len(changed_keys),
            "missing_writes": [f"{peak}/{sample}" for peak, sample in missing_writes],
            "unexpected_writes": [
                f"{peak}/{sample}" for peak, sample in unexpected_writes
            ],
            "unexpected_values": unexpected_values,
        }
    return {
        "status": "pass",
        "reason": "",
        "changed_cell_count": len(changed_keys),
        "expected_write_count": len(expected_write_keys),
        "unexpected_write_count": 0,
        "missing_write_count": 0,
    }


def _replay_summary(expected_diff_summary_tsv: Path) -> dict[str, str]:
    rows = read_tsv_required(expected_diff_summary_tsv, EXPECTED_DIFF_SUMMARY_COLUMNS)
    if len(rows) != 1:
        return {
            "status": "blocked",
            "reason": "expected_diff_summary_row_count_mismatch",
        }
    row = rows[0]
    status = "pass" if row.get("acceptance_status") == "pass" else "blocked"
    return {
        "status": status,
        "reason": "" if status == "pass" else "expected_diff_summary_not_pass",
        "expected_diff_count": row.get("expected_diff_count", ""),
        "written_backfill_count": row.get("written_backfill_count", ""),
        "unused_expected_diff_count": row.get("unused_expected_diff_count", ""),
    }


def _manifest_rows_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    result: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        key = (row.get("peak_hypothesis_id", ""), row.get("sample_stem", ""))
        if all(key):
            result[key] = row
    return result


def _remaining_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "overall_status": payload.get("overall_status", ""),
        "readiness_label": payload.get("readiness_label", ""),
        "resolution_counts": payload.get("resolution_counts", {}),
        "candidate_backfill_write_count": payload.get(
            "candidate_backfill_write_count",
            0,
        ),
        "detected_baseline_no_write_count": payload.get(
            "detected_baseline_no_write_count",
            0,
        ),
        "scope_removed_no_write_count": payload.get("scope_removed_no_write_count", 0),
        "unresolved_authority_cell_count": payload.get(
            "unresolved_authority_cell_count",
            0,
        ),
    }


def _counts(values: Sequence[str] | Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = text_value(value) or "<blank>"
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _append_token(value: str, token: str) -> str:
    tokens = split_semicolon_labels(value)
    if token not in tokens:
        tokens.append(token)
    return ";".join(tokens)


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
        return path.resolve(strict=False).relative_to(
            ROOT.resolve(strict=False),
        ).as_posix()
    except ValueError:
        return str(path)


def _rewrite_source_summary_paths(source_summary_tsv: Path) -> None:
    rows = read_tsv_required(source_summary_tsv, ("schema_version",))
    if len(rows) != 1:
        raise ValueError("source_summary must contain exactly one row")
    row = dict(rows[0])
    for field in (
        "input_quant_matrix_tsv",
        "input_matrix_identity_tsv",
        "production_acceptance_manifest_tsv",
        "expected_diff_tsv",
    ):
        value = row.get(field, "")
        if value:
            row[field] = _portable_relpath(Path(value), source_summary_tsv.parent)
    write_tsv(
        source_summary_tsv,
        [row],
        tuple(row),
        extrasaction="raise",
        lineterminator="\n",
    )


def _portable_relpath(path: Path, base_dir: Path) -> str:
    try:
        return Path(
            os.path.relpath(
                path.resolve(strict=False),
                base_dir.resolve(strict=False),
            )
        ).as_posix()
    except ValueError:
        return str(path)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--source-root", type=Path, default=ROOT)
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
        help="Return non-zero if the successor authority contract is blocked.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    payload = build_successor_authority_contract(
        output_dir=args.output_dir,
        source_root=args.source_root,
        old_matrix_identity_tsv=args.old_matrix_identity_tsv,
        new_quant_matrix_tsv=args.new_quant_matrix_tsv,
        new_matrix_identity_tsv=args.new_matrix_identity_tsv,
        production_acceptance_manifest_tsv=args.production_acceptance_manifest_tsv,
        expected_diff_tsv=args.expected_diff_tsv,
        target_preflight_summary_json=args.target_preflight_summary_json,
        mz_tolerance_da=args.mz_tolerance_da,
        rt_tolerance_min=args.rt_tolerance_min,
    )
    print(
        "cid_nl_default_activation_successor_authority_summary: "
        f"{payload['outputs']['summary_json']}"
    )
    print(
        "cid_nl_default_activation_successor_authority_status: "
        f"{payload['overall_status']}"
    )
    if args.require_pass and payload["overall_status"] != "pass":
        for blocker in payload["blockers"]:
            print(f"cid_nl_default_activation_successor_authority_blocker: {blocker}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
