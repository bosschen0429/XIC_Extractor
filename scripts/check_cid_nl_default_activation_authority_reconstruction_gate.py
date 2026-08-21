"""Check CID-NL default-activation authority reconstruction readiness.

This is a no-RAW, no-ProductWriter gate. It classifies the existing rule-selected
Backfill membership against the current CID-NL alignment baseline after the
bridge gate has found a unique row match, detected-baseline supersession, or an
identity blocker. It does not write a default matrix or grant new authority.
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
    REQUIRED_MANIFEST_COLUMNS,
    evaluate_bridge_gate,
)
from xic_extractor.alignment.quant_matrix_version import (  # noqa: E402
    EXPECTED_DIFF_COLUMNS,
    build_quant_matrix_version_rows,
)
from xic_extractor.tabular_io import (  # noqa: E402
    bool_value,
    file_sha256,
    read_tsv_required,
    read_tsv_with_header,
    text_value,
    write_tsv,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "output/validation/"
    / "cid_nl_default_activation_authority_reconstruction_gate_v1"
)

ACCEPT_DECISIONS = {"accept_basic_backfill", "accept_strict_backfill"}
RECONSTRUCTION_AUDIT_COLUMNS = (
    "schema_version",
    "old_peak_hypothesis_id",
    "sample_stem",
    "resolution_status",
    "authority_action",
    "matrix_effect",
    "blocker_reason",
    "selected_new_peak_hypothesis_id",
    "candidate_new_peak_hypothesis_ids",
    "new_baseline_value",
    "accepted_quant_value",
    "expected_activated_value",
    "expected_diff_replay_action",
    "source_row_sha256",
)


def evaluate_authority_reconstruction_gate(
    *,
    old_matrix_identity_tsv: Path,
    new_quant_matrix_tsv: Path,
    new_matrix_identity_tsv: Path,
    production_acceptance_manifest_tsv: Path,
    expected_diff_tsv: Path,
    target_preflight_summary_json: Path | None = DEFAULT_TARGET_PREFLIGHT_SUMMARY_JSON,
    require_target_preflight: bool = True,
    mz_tolerance_da: float = DEFAULT_MZ_TOLERANCE_DA,
    rt_tolerance_min: float = DEFAULT_RT_TOLERANCE_MIN,
) -> dict[str, Any]:
    bridge_payload = evaluate_bridge_gate(
        old_matrix_identity_tsv=old_matrix_identity_tsv,
        new_quant_matrix_tsv=new_quant_matrix_tsv,
        new_matrix_identity_tsv=new_matrix_identity_tsv,
        production_acceptance_manifest_tsv=production_acceptance_manifest_tsv,
        expected_diff_tsv=expected_diff_tsv,
        target_preflight_summary_json=target_preflight_summary_json,
        require_target_preflight=require_target_preflight,
        mz_tolerance_da=mz_tolerance_da,
        rt_tolerance_min=rt_tolerance_min,
    )
    new_header, new_matrix_rows = read_tsv_with_header(
        new_quant_matrix_tsv,
        required_columns=("Mz", "RT"),
    )
    new_identity_rows = list(
        read_tsv_required(
            new_matrix_identity_tsv,
            (
                "matrix_row_index",
                "Mz",
                "RT",
                "peak_hypothesis_id",
                "source_feature_family_ids",
            ),
        )
    )
    manifest_rows = list(
        read_tsv_required(
            production_acceptance_manifest_tsv,
            REQUIRED_MANIFEST_COLUMNS,
        )
    )
    expected_diff_rows = list(
        read_tsv_required(expected_diff_tsv, EXPECTED_DIFF_COLUMNS),
    )
    accepted_rows = [row for row in manifest_rows if _is_authorized(row)]
    accepted_by_key = _rows_by_key(accepted_rows, "peak_hypothesis_id")
    expected_by_key = _rows_by_key(expected_diff_rows, "peak_hypothesis_id")
    audit_rows = _build_reconstruction_audit_rows(
        bridge_rows=bridge_payload["audit_rows"],
        accepted_by_key=accepted_by_key,
        expected_by_key=expected_by_key,
    )
    source_blockers = _source_blockers(
        bridge_payload=bridge_payload,
        accepted_rows=accepted_rows,
        expected_diff_rows=expected_diff_rows,
        audit_rows=audit_rows,
    )
    replay = _candidate_replay_summary(
        source_blockers=source_blockers,
        audit_rows=audit_rows,
        accepted_by_key=accepted_by_key,
        expected_by_key=expected_by_key,
        new_header=new_header,
        new_matrix_rows=new_matrix_rows,
        new_identity_rows=new_identity_rows,
    )
    resolution_counts = _counts(row["resolution_status"] for row in audit_rows)
    unresolved_count = sum(
        count
        for status, count in resolution_counts.items()
        if status.startswith("blocked_")
    )
    blockers = list(source_blockers)
    if unresolved_count:
        blockers.append(f"unresolved_authority_cell_count:{unresolved_count}")
    if replay["status"] != "pass":
        blockers.append("candidate_replay_not_pass:" + replay["reason"])
    overall_status = "pass" if not blockers else "blocked"
    return {
        "schema_version": "cid_nl_default_activation_authority_reconstruction_gate_v1",
        "overall_status": overall_status,
        "readiness_label": (
            "default_activation_authority_reconstructed_candidate"
            if overall_status == "pass"
            else "production_candidate_blocked"
        ),
        "product_surface_changed": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "workbook_or_gui_changed": False,
        "backfill_writer_authority_changed": False,
        "detected_baseline_supersession_is_backfill_authority": False,
        "authority_statement": (
            "This gate classifies the existing rule-selected Backfill membership "
            "against the new CID-NL baseline. Detected-baseline supersession "
            "is resolved "
            "as a no-write state and is not Backfill writer authority."
        ),
        "accepted_authority_cell_count": len(accepted_rows),
        "expected_diff_row_count": len(expected_diff_rows),
        "resolution_counts": resolution_counts,
        "candidate_backfill_write_count": resolution_counts.get(
            "write_ready_blank",
            0,
        ),
        "detected_baseline_superseded_count": resolution_counts.get(
            "superseded_by_detected_baseline",
            0,
        ),
        "unresolved_authority_cell_count": unresolved_count,
        "candidate_replay": replay,
        "bridge_gate": _bridge_summary(bridge_payload),
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
            else "resolve_missing_or_ambiguous_canonical_identity"
        ),
        "audit_rows": audit_rows,
    }


def write_outputs(
    *,
    output_dir: Path,
    payload: Mapping[str, Any],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_tsv = (
        output_dir / "cid_nl_default_activation_authority_reconstruction_audit.tsv"
    )
    summary_json = (
        output_dir
        / "cid_nl_default_activation_authority_reconstruction_gate_summary.json"
    )
    write_tsv(
        audit_tsv,
        payload["audit_rows"],
        RECONSTRUCTION_AUDIT_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    summary_payload = {
        key: value for key, value in payload.items() if key != "audit_rows"
    }
    summary_payload = {
        **summary_payload,
        "outputs": {"reconstruction_audit_tsv": _relative_or_absolute(audit_tsv)},
    }
    summary_json.write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"summary_json": summary_json, "reconstruction_audit_tsv": audit_tsv}


def _build_reconstruction_audit_rows(
    *,
    bridge_rows: Sequence[Mapping[str, str]],
    accepted_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    expected_by_key: Mapping[tuple[str, str], Mapping[str, str]],
) -> list[dict[str, str]]:
    audit_rows: list[dict[str, str]] = []
    for bridge in bridge_rows:
        old_peak_id = text_value(bridge.get("old_peak_hypothesis_id"))
        sample_stem = text_value(bridge.get("sample_stem"))
        key = (old_peak_id, sample_stem)
        accepted = accepted_by_key.get(key, {})
        expected = expected_by_key.get(key, {})
        resolution = _resolution_status(bridge)
        audit_rows.append(
            {
                "schema_version": (
                    "cid_nl_default_activation_authority_reconstruction_audit_v1"
                ),
                "old_peak_hypothesis_id": old_peak_id,
                "sample_stem": sample_stem,
                "resolution_status": resolution,
                "authority_action": _authority_action(resolution),
                "matrix_effect": _matrix_effect(resolution),
                "blocker_reason": _reconstruction_blocker(resolution, bridge),
                "selected_new_peak_hypothesis_id": text_value(
                    bridge.get("selected_new_peak_hypothesis_id")
                ),
                "candidate_new_peak_hypothesis_ids": text_value(
                    bridge.get("candidate_new_peak_hypothesis_ids")
                ),
                "new_baseline_value": text_value(bridge.get("new_baseline_value")),
                "accepted_quant_value": text_value(accepted.get("quant_value")),
                "expected_activated_value": text_value(
                    expected.get("activated_value")
                ),
                "expected_diff_replay_action": _expected_diff_replay_action(
                    resolution
                ),
                "source_row_sha256": text_value(accepted.get("source_row_sha256")),
            }
        )
    return audit_rows


def _resolution_status(bridge: Mapping[str, str]) -> str:
    bridge_status = text_value(bridge.get("bridge_status"))
    blocker = text_value(bridge.get("blocker_reason"))
    selected_new_peak = text_value(bridge.get("selected_new_peak_hypothesis_id"))
    new_baseline_value = text_value(bridge.get("new_baseline_value"))
    if bridge_status == "pass" and not blocker:
        return "write_ready_blank"
    if (
        blocker == "new_baseline_already_has_value"
        and selected_new_peak
        and new_baseline_value
    ):
        return "superseded_by_detected_baseline"
    if blocker == "new_identity_missing":
        return "blocked_identity_missing"
    if blocker == "new_identity_ambiguous":
        return "blocked_identity_ambiguous"
    if blocker == "new_identity_matrix_coordinate_mismatch":
        return "blocked_identity_matrix_coordinate_mismatch"
    return "blocked_other"


def _authority_action(resolution: str) -> str:
    if resolution == "write_ready_blank":
        return "candidate_backfill_write"
    if resolution == "superseded_by_detected_baseline":
        return "no_backfill_write_detected_value_already_present"
    return "none"


def _matrix_effect(resolution: str) -> str:
    if resolution == "write_ready_blank":
        return "write_accepted_backfill"
    if resolution == "superseded_by_detected_baseline":
        return "preserve_detected_baseline"
    return "blocked"


def _expected_diff_replay_action(resolution: str) -> str:
    if resolution == "write_ready_blank":
        return "include_in_candidate_replay"
    if resolution == "superseded_by_detected_baseline":
        return "exclude_detected_baseline_no_write"
    return "blocked"


def _reconstruction_blocker(
    resolution: str,
    bridge: Mapping[str, str],
) -> str:
    if resolution in {"write_ready_blank", "superseded_by_detected_baseline"}:
        return ""
    return text_value(bridge.get("blocker_reason")) or "unclassified_bridge_blocker"


def _candidate_replay_summary(
    *,
    source_blockers: Sequence[str],
    audit_rows: Sequence[Mapping[str, str]],
    accepted_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    expected_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    new_header: Sequence[str],
    new_matrix_rows: Sequence[Mapping[str, str]],
    new_identity_rows: Sequence[Mapping[str, str]],
) -> dict[str, str]:
    if source_blockers:
        return {
            "status": "not_run",
            "reason": "source_blockers_present",
            "written_backfill_count": "0",
        }
    write_ready_rows = [
        row for row in audit_rows if row["resolution_status"] == "write_ready_blank"
    ]
    if not write_ready_rows:
        return {
            "status": "pass",
            "reason": "",
            "written_backfill_count": "0",
        }
    try:
        bridged_manifest = [
            {
                **accepted_by_key[
                    (row["old_peak_hypothesis_id"], row["sample_stem"])
                ],
                "peak_hypothesis_id": row["selected_new_peak_hypothesis_id"],
                "feature_family_id": row["selected_new_peak_hypothesis_id"],
            }
            for row in write_ready_rows
        ]
        bridged_expected = [
            {
                **expected_by_key[
                    (row["old_peak_hypothesis_id"], row["sample_stem"])
                ],
                "peak_hypothesis_id": row["selected_new_peak_hypothesis_id"],
            }
            for row in write_ready_rows
        ]
        outputs = build_quant_matrix_version_rows(
            matrix_header=new_header,
            input_quant_matrix_rows=new_matrix_rows,
            input_matrix_identity_rows=new_identity_rows,
            production_acceptance_rows=bridged_manifest,
            expected_diff_rows=bridged_expected,
        )
    except (KeyError, ValueError) as exc:
        return {
            "status": "blocked",
            "reason": str(exc),
            "written_backfill_count": "0",
        }
    summary = outputs.expected_diff_summary_rows[0]
    return {
        "status": "pass",
        "reason": "",
        "written_backfill_count": summary.get("written_backfill_count", ""),
    }


def _source_blockers(
    *,
    bridge_payload: Mapping[str, Any],
    accepted_rows: Sequence[Mapping[str, str]],
    expected_diff_rows: Sequence[Mapping[str, str]],
    audit_rows: Sequence[Mapping[str, str]],
) -> list[str]:
    blockers: list[str] = []
    if len(expected_diff_rows) != len(accepted_rows):
        blockers.append(
            "expected_diff_count_mismatch:"
            f"expected={len(accepted_rows)};observed={len(expected_diff_rows)}"
        )
    if bridge_payload["target_preflight"].get("status") != "pass":
        blockers.append(
            "target_preflight_not_pass:"
            + text_value(bridge_payload["target_preflight"].get("reason"))
        )
    expected_problem_count = int(
        bridge_payload.get("expected_diff_content_problem_count", 0)
    )
    if expected_problem_count:
        blockers.append(f"expected_diff_content_problem_count:{expected_problem_count}")
    if len(audit_rows) != len(accepted_rows):
        blockers.append(
            "reconstruction_audit_count_mismatch:"
            f"accepted={len(accepted_rows)};audit={len(audit_rows)}"
        )
    return blockers


def _rows_by_key(
    rows: Sequence[Mapping[str, str]],
    peak_field: str,
) -> dict[tuple[str, str], Mapping[str, str]]:
    result: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        key = (text_value(row.get(peak_field)), text_value(row.get("sample_stem")))
        if all(key):
            result[key] = row
    return result


def _is_authorized(row: Mapping[str, str]) -> bool:
    return (
        text_value(row.get("acceptance_decision")) in ACCEPT_DECISIONS
        and bool_value(row.get("write_authority")) is True
        and bool_value(row.get("matrix_write_allowed")) is True
        and bool_value(row.get("shadow_only")) is False
    )


def _counts(values: Sequence[str] | Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = text_value(value) or "<blank>"
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _bridge_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "overall_status": payload.get("overall_status", ""),
        "readiness_label": payload.get("readiness_label", ""),
        "target_preflight": payload.get("target_preflight", {}),
        "cell_bridge_status_counts": payload.get("cell_bridge_status_counts", {}),
        "blocker_counts": payload.get("blocker_counts", {}),
        "expected_diff_content_problem_count": payload.get(
            "expected_diff_content_problem_count",
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
        help="Return non-zero if the reconstruction gate is blocked.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    payload = evaluate_authority_reconstruction_gate(
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
        "cid_nl_default_activation_authority_reconstruction_summary: "
        f"{outputs['summary_json']}"
    )
    print(
        "cid_nl_default_activation_authority_reconstruction_audit: "
        f"{outputs['reconstruction_audit_tsv']}"
    )
    print(
        "cid_nl_default_activation_authority_reconstruction_status: "
        f"{payload['overall_status']}"
    )
    if args.require_pass and payload["overall_status"] != "pass":
        for blocker in payload["blockers"]:
            print(
                "cid_nl_default_activation_authority_reconstruction_blocker: "
                f"{blocker}"
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
