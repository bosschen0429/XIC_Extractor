"""Check the CID-NL default-activation ID bridge gate.

This is a no-RAW, no-ProductWriter gate. It tests whether the current
rule-selected Backfill membership can be bridged from the tracked default activation
identity space onto a newer CID-NL alignment identity space without ambiguity,
without overwriting detected values, and with expected-diff closure.
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

from xic_extractor.alignment.quant_matrix_version import (  # noqa: E402
    EXPECTED_DIFF_COLUMNS,
    EXPECTED_DIFF_SCHEMA,
    build_quant_matrix_version_rows,
)
from xic_extractor.tabular_io import (  # noqa: E402
    bool_value,
    file_sha256,
    numeric_equal,
    optional_float,
    read_tsv_required,
    read_tsv_with_header,
    text_value,
    write_tsv,
)

ROOT = Path(__file__).resolve().parents[1]
OLD_INPUT_DIR = ROOT / "docs/superpowers/validation/quant_matrix_real_bundle/inputs"
NEW_ALIGNMENT_DIR = (
    ROOT / "output/discovery/cid_nl_product_ready_alignment_85raw_20260620_fix3"
)
DEFAULT_OLD_MATRIX_IDENTITY_TSV = OLD_INPUT_DIR / "alignment_matrix_identity.tsv"
DEFAULT_NEW_QUANT_MATRIX_TSV = NEW_ALIGNMENT_DIR / "alignment_matrix.tsv"
DEFAULT_NEW_MATRIX_IDENTITY_TSV = NEW_ALIGNMENT_DIR / "alignment_matrix_identity.tsv"
DEFAULT_PRODUCTION_ACCEPTANCE_MANIFEST_TSV = (
    OLD_INPUT_DIR / "production_acceptance_manifest.tsv"
)
DEFAULT_EXPECTED_DIFF_TSV = OLD_INPUT_DIR / "expected_diff.tsv"
DEFAULT_TARGET_PREFLIGHT_SUMMARY_JSON = (
    ROOT
    / "docs/superpowers/validation/cid_nl_default_activation_preflight/"
    / "cid_nl_default_activation_preflight_summary.json"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "output/validation/cid_nl_default_activation_bridge_gate"
)

DEFAULT_MZ_TOLERANCE_DA = 0.02
DEFAULT_RT_TOLERANCE_MIN = 0.2

ACCEPT_DECISIONS = {"accept_basic_backfill", "accept_strict_backfill"}

REQUIRED_IDENTITY_COLUMNS = (
    "matrix_row_index",
    "Mz",
    "RT",
    "peak_hypothesis_id",
    "source_feature_family_ids",
)
REQUIRED_MANIFEST_COLUMNS = (
    "peak_hypothesis_id",
    "sample_stem",
    "feature_family_id",
    "acceptance_decision",
    "write_authority",
    "matrix_write_allowed",
    "shadow_only",
    "quant_value",
    "source_row_sha256",
)
BRIDGE_AUDIT_COLUMNS = (
    "schema_version",
    "old_peak_hypothesis_id",
    "sample_stem",
    "bridge_status",
    "blocker_reason",
    "candidate_new_peak_hypothesis_ids",
    "selected_new_peak_hypothesis_id",
    "old_mz",
    "old_rt",
    "new_mz",
    "new_rt",
    "mz_delta_da",
    "rt_delta_min",
    "new_baseline_value",
    "accepted_quant_value",
    "source_row_sha256",
)


@dataclass(frozen=True)
class BridgeCandidate:
    peak_hypothesis_id: str
    matrix_row_index: int
    mz: str
    rt: str
    mz_delta_da: float
    rt_delta_min: float


@dataclass(frozen=True)
class PeakBridge:
    old_peak_hypothesis_id: str
    old_mz: str
    old_rt: str
    status: str
    blocker_reason: str
    candidates: tuple[BridgeCandidate, ...]
    selected: BridgeCandidate | None


def evaluate_bridge_gate(
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
    old_identity_rows = list(
        read_tsv_required(old_matrix_identity_tsv, REQUIRED_IDENTITY_COLUMNS),
    )
    new_header, new_matrix_rows = read_tsv_with_header(
        new_quant_matrix_tsv,
        required_columns=("Mz", "RT"),
    )
    new_identity_rows = list(
        read_tsv_required(new_matrix_identity_tsv, REQUIRED_IDENTITY_COLUMNS),
    )
    manifest_rows = list(
        read_tsv_required(
            production_acceptance_manifest_tsv,
            REQUIRED_MANIFEST_COLUMNS,
        ),
    )
    expected_diff_rows = list(
        read_tsv_required(expected_diff_tsv, EXPECTED_DIFF_COLUMNS),
    )

    accepted_rows = [row for row in manifest_rows if _is_authorized(row)]
    old_identity_by_peak = {
        text_value(row.get("peak_hypothesis_id")): row for row in old_identity_rows
    }
    peak_bridges = _build_peak_bridges(
        old_peak_ids=sorted({row["peak_hypothesis_id"] for row in accepted_rows}),
        old_identity_by_peak=old_identity_by_peak,
        new_identity_rows=new_identity_rows,
        mz_tolerance_da=mz_tolerance_da,
        rt_tolerance_min=rt_tolerance_min,
        new_matrix_row_count=len(new_matrix_rows),
    )
    audit_rows = _build_audit_rows(
        accepted_rows=accepted_rows,
        peak_bridges=peak_bridges,
        new_matrix_rows=new_matrix_rows,
    )
    audit_status_counts = _counts(row["bridge_status"] for row in audit_rows)
    blocker_counts = _counts(
        row["blocker_reason"] for row in audit_rows if row["blocker_reason"]
    )
    target_preflight = _target_preflight_status(
        target_preflight_summary_json,
        require_target_preflight=require_target_preflight,
    )
    expected_diff_problems = _expected_diff_content_problems(
        accepted_rows=accepted_rows,
        expected_diff_rows=expected_diff_rows,
    )
    bridge_blockers = _bridge_blockers(
        accepted_rows=accepted_rows,
        expected_diff_rows=expected_diff_rows,
        audit_rows=audit_rows,
        target_preflight=target_preflight,
        expected_diff_problems=expected_diff_problems,
    )
    activation_replay = _activation_replay_summary(
        blockers=bridge_blockers,
        new_header=new_header,
        new_matrix_rows=new_matrix_rows,
        new_identity_rows=new_identity_rows,
        accepted_rows=accepted_rows,
        expected_diff_rows=expected_diff_rows,
        peak_bridges=peak_bridges,
    )
    overall_status = (
        "pass"
        if not bridge_blockers and activation_replay["status"] == "pass"
        else "blocked"
    )
    return {
        "schema_version": "cid_nl_default_activation_bridge_gate",
        "overall_status": overall_status,
        "readiness_label": (
            "default_activation_bridge_candidate"
            if overall_status == "pass"
            else "production_candidate_blocked"
        ),
        "product_surface_changed": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "workbook_or_gui_changed": False,
        "backfill_writer_authority_changed": False,
        "authority_statement": (
            "This gate does not grant ProductWriter/default matrix authority. "
            "It only checks whether the existing rule-selected Backfill membership "
            "can bridge cleanly onto the new CID-NL alignment identity."
        ),
        "mz_tolerance_da": mz_tolerance_da,
        "rt_tolerance_min": rt_tolerance_min,
        "accepted_authority_cell_count": len(accepted_rows),
        "expected_diff_row_count": len(expected_diff_rows),
        "accepted_peak_hypothesis_id_count": len(
            {row["peak_hypothesis_id"] for row in accepted_rows}
        ),
        "peak_bridge_status_counts": _counts(
            bridge.status for bridge in peak_bridges.values()
        ),
        "cell_bridge_status_counts": audit_status_counts,
        "blocker_counts": blocker_counts,
        "target_preflight": target_preflight,
        "expected_diff_content_problem_count": len(expected_diff_problems),
        "expected_diff_content_problems": expected_diff_problems[:20],
        "activation_replay": activation_replay,
        "artifacts": _artifact_summaries(
            old_matrix_identity_tsv=old_matrix_identity_tsv,
            new_quant_matrix_tsv=new_quant_matrix_tsv,
            new_matrix_identity_tsv=new_matrix_identity_tsv,
            production_acceptance_manifest_tsv=production_acceptance_manifest_tsv,
            expected_diff_tsv=expected_diff_tsv,
        ),
        "blockers": bridge_blockers,
        "next_step": (
            "build_default_activation_candidate"
            if overall_status == "pass"
            else "define_stronger_canonical_identity_bridge_or_regenerate_authority"
        ),
        "audit_rows": audit_rows,
    }


def write_outputs(
    *,
    output_dir: Path,
    payload: Mapping[str, Any],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_tsv = output_dir / "cid_nl_default_activation_bridge_audit.tsv"
    summary_json = output_dir / "cid_nl_default_activation_bridge_gate_summary.json"
    write_tsv(
        audit_tsv,
        payload["audit_rows"],
        BRIDGE_AUDIT_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    summary_payload = {
        key: value for key, value in payload.items() if key != "audit_rows"
    }
    summary_payload = {
        **summary_payload,
        "outputs": {"bridge_audit_tsv": _relative_or_absolute(audit_tsv)},
    }
    summary_json.write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"summary_json": summary_json, "bridge_audit_tsv": audit_tsv}


def _build_peak_bridges(
    *,
    old_peak_ids: Sequence[str],
    old_identity_by_peak: Mapping[str, Mapping[str, str]],
    new_identity_rows: Sequence[Mapping[str, str]],
    mz_tolerance_da: float,
    rt_tolerance_min: float,
    new_matrix_row_count: int,
) -> dict[str, PeakBridge]:
    bridges: dict[str, PeakBridge] = {}
    for old_peak_id in old_peak_ids:
        old_identity = old_identity_by_peak.get(old_peak_id)
        if old_identity is None:
            bridges[old_peak_id] = PeakBridge(
                old_peak_id,
                "",
                "",
                "blocked",
                "old_identity_missing",
                (),
                None,
            )
            continue
        old_mz = optional_float(old_identity.get("Mz"))
        old_rt = optional_float(old_identity.get("RT"))
        if old_mz is None or old_rt is None:
            bridges[old_peak_id] = PeakBridge(
                old_peak_id,
                old_identity.get("Mz", ""),
                old_identity.get("RT", ""),
                "blocked",
                "old_identity_invalid_mz_rt",
                (),
                None,
            )
            continue
        candidates = tuple(
            _candidate_from_identity(
                row,
                old_mz=old_mz,
                old_rt=old_rt,
                new_matrix_row_count=new_matrix_row_count,
            )
            for row in new_identity_rows
            if _within_tolerance(
                row,
                old_mz=old_mz,
                old_rt=old_rt,
                mz_tolerance_da=mz_tolerance_da,
                rt_tolerance_min=rt_tolerance_min,
            )
        )
        if len(candidates) == 1:
            bridges[old_peak_id] = PeakBridge(
                old_peak_id,
                old_identity.get("Mz", ""),
                old_identity.get("RT", ""),
                "pass",
                "",
                candidates,
                candidates[0],
            )
        elif not candidates:
            bridges[old_peak_id] = PeakBridge(
                old_peak_id,
                old_identity.get("Mz", ""),
                old_identity.get("RT", ""),
                "blocked",
                "new_identity_missing",
                (),
                None,
            )
        else:
            bridges[old_peak_id] = PeakBridge(
                old_peak_id,
                old_identity.get("Mz", ""),
                old_identity.get("RT", ""),
                "blocked",
                "new_identity_ambiguous",
                candidates,
                None,
            )
    return bridges


def _build_audit_rows(
    *,
    accepted_rows: Sequence[Mapping[str, str]],
    peak_bridges: Mapping[str, PeakBridge],
    new_matrix_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    audit_rows: list[dict[str, str]] = []
    for accepted in accepted_rows:
        old_peak_id = text_value(accepted.get("peak_hypothesis_id"))
        sample_stem = text_value(accepted.get("sample_stem"))
        bridge = peak_bridges[old_peak_id]
        selected = bridge.selected
        new_baseline_value = ""
        status = bridge.status
        blocker = bridge.blocker_reason
        if selected is not None:
            matrix_row = new_matrix_rows[selected.matrix_row_index - 1]
            new_baseline_value = text_value(matrix_row.get(sample_stem))
            mz_matches = numeric_equal(matrix_row.get("Mz"), selected.mz)
            rt_matches = numeric_equal(matrix_row.get("RT"), selected.rt)
            if not mz_matches or not rt_matches:
                status = "blocked"
                blocker = "new_identity_matrix_coordinate_mismatch"
            elif new_baseline_value:
                status = "blocked"
                blocker = "new_baseline_already_has_value"
        audit_rows.append(
            {
                "schema_version": "cid_nl_default_activation_bridge_audit",
                "old_peak_hypothesis_id": old_peak_id,
                "sample_stem": sample_stem,
                "bridge_status": status,
                "blocker_reason": blocker,
                "candidate_new_peak_hypothesis_ids": ";".join(
                    candidate.peak_hypothesis_id for candidate in bridge.candidates
                ),
                "selected_new_peak_hypothesis_id": (
                    selected.peak_hypothesis_id if selected else ""
                ),
                "old_mz": bridge.old_mz,
                "old_rt": bridge.old_rt,
                "new_mz": selected.mz if selected else "",
                "new_rt": selected.rt if selected else "",
                "mz_delta_da": (
                    f"{selected.mz_delta_da:.6g}" if selected else ""
                ),
                "rt_delta_min": (
                    f"{selected.rt_delta_min:.6g}" if selected else ""
                ),
                "new_baseline_value": new_baseline_value,
                "accepted_quant_value": accepted.get("quant_value", ""),
                "source_row_sha256": accepted.get("source_row_sha256", ""),
            }
        )
    return audit_rows


def _bridge_blockers(
    *,
    accepted_rows: Sequence[Mapping[str, str]],
    expected_diff_rows: Sequence[Mapping[str, str]],
    audit_rows: Sequence[Mapping[str, str]],
    target_preflight: Mapping[str, Any],
    expected_diff_problems: Sequence[str],
) -> list[str]:
    blockers: list[str] = []
    if len(expected_diff_rows) != len(accepted_rows):
        blockers.append(
            "expected_diff_count_mismatch:"
            f"expected={len(accepted_rows)};observed={len(expected_diff_rows)}",
        )
    if target_preflight.get("status") != "pass":
        blockers.append(
            "target_preflight_not_pass:"
            + text_value(target_preflight.get("reason")),
        )
    if expected_diff_problems:
        blockers.append(
            f"expected_diff_content_problem_count:{len(expected_diff_problems)}",
        )
    status_counts = _counts(row["bridge_status"] for row in audit_rows)
    if status_counts.get("blocked", 0):
        blockers.append(f"blocked_bridge_cell_count:{status_counts['blocked']}")
    return blockers


def _activation_replay_summary(
    *,
    blockers: Sequence[str],
    new_header: Sequence[str],
    new_matrix_rows: Sequence[Mapping[str, str]],
    new_identity_rows: Sequence[Mapping[str, str]],
    accepted_rows: Sequence[Mapping[str, str]],
    expected_diff_rows: Sequence[Mapping[str, str]],
    peak_bridges: Mapping[str, PeakBridge],
) -> dict[str, str]:
    if blockers:
        return {
            "status": "not_run",
            "reason": "bridge_blockers_present",
            "written_backfill_count": "0",
        }
    bridged_manifest = [
        {
            **row,
            "peak_hypothesis_id": peak_bridges[row["peak_hypothesis_id"]]
            .selected.peak_hypothesis_id,  # type: ignore[union-attr]
            "feature_family_id": peak_bridges[row["peak_hypothesis_id"]]
            .selected.peak_hypothesis_id,  # type: ignore[union-attr]
        }
        for row in accepted_rows
    ]
    expected_by_key = {
        (row["peak_hypothesis_id"], row["sample_stem"]): row
        for row in expected_diff_rows
    }
    bridged_expected = [
        {
            **expected_by_key[(row["peak_hypothesis_id"], row["sample_stem"])],
            "peak_hypothesis_id": peak_bridges[row["peak_hypothesis_id"]]
            .selected.peak_hypothesis_id,  # type: ignore[union-attr]
        }
        for row in accepted_rows
    ]
    try:
        outputs = build_quant_matrix_version_rows(
            matrix_header=new_header,
            input_quant_matrix_rows=new_matrix_rows,
            input_matrix_identity_rows=new_identity_rows,
            production_acceptance_rows=bridged_manifest,
            expected_diff_rows=bridged_expected,
        )
    except ValueError as exc:
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


def _expected_diff_content_problems(
    *,
    accepted_rows: Sequence[Mapping[str, str]],
    expected_diff_rows: Sequence[Mapping[str, str]],
) -> list[str]:
    problems: list[str] = []
    expected_by_key: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in expected_diff_rows:
        key = (
            text_value(row.get("peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
        )
        if key in expected_by_key:
            problems.append(f"{key[0]}/{key[1]}: duplicate expected-diff key")
        expected_by_key[key] = row
    accepted_keys = {
        (text_value(row.get("peak_hypothesis_id")), text_value(row.get("sample_stem")))
        for row in accepted_rows
    }
    extra_keys = sorted(set(expected_by_key) - accepted_keys)
    missing_keys = sorted(accepted_keys - set(expected_by_key))
    problems.extend(f"{key[0]}/{key[1]}: extra expected-diff row" for key in extra_keys)
    problems.extend(
        f"{key[0]}/{key[1]}: missing expected-diff row" for key in missing_keys
    )
    for accepted in accepted_rows:
        key = (
            text_value(accepted.get("peak_hypothesis_id")),
            text_value(accepted.get("sample_stem")),
        )
        expected = expected_by_key.get(key)
        if expected is None:
            continue
        label = f"{key[0]}/{key[1]}"
        if expected.get("schema_version") != EXPECTED_DIFF_SCHEMA:
            problems.append(f"{label}: expected-diff schema_version mismatch")
        if expected.get("expected_matrix_effect") != "write_accepted_backfill":
            problems.append(f"{label}: expected-diff effect mismatch")
        if text_value(expected.get("baseline_value")):
            problems.append(f"{label}: expected-diff baseline must be blank")
        if not numeric_equal(
            expected.get("activated_value"),
            accepted.get("quant_value"),
        ):
            problems.append(f"{label}: expected-diff activated_value mismatch")
    return problems


def _target_preflight_status(
    path: Path | None,
    *,
    require_target_preflight: bool,
) -> dict[str, Any]:
    if path is None:
        return {"status": "not_required", "reason": ""}
    if not path.exists():
        return {
            "status": "blocked" if require_target_preflight else "not_found",
            "reason": "target_preflight_summary_missing",
            "path": _relative_or_absolute(path),
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    status = (
        "pass"
        if payload.get("target_alignment_evidence_status") == "pass"
        else "blocked"
    )
    return {
        "status": status,
        "reason": "" if status == "pass" else "target_alignment_evidence_not_pass",
        "path": _relative_or_absolute(path),
        "overall_status": payload.get("overall_status", ""),
        "target_alignment_evidence_status": payload.get(
            "target_alignment_evidence_status",
            "",
        ),
    }


def _candidate_from_identity(
    row: Mapping[str, str],
    *,
    old_mz: float,
    old_rt: float,
    new_matrix_row_count: int,
) -> BridgeCandidate:
    row_index = int(text_value(row.get("matrix_row_index")))
    if row_index < 1 or row_index > new_matrix_row_count:
        raise ValueError(f"new matrix_row_index out of range: {row_index}")
    mz = optional_float(row.get("Mz"))
    rt = optional_float(row.get("RT"))
    if mz is None or rt is None:
        raise ValueError("new identity row has invalid Mz/RT")
    return BridgeCandidate(
        peak_hypothesis_id=text_value(row.get("peak_hypothesis_id")),
        matrix_row_index=row_index,
        mz=text_value(row.get("Mz")),
        rt=text_value(row.get("RT")),
        mz_delta_da=abs(mz - old_mz),
        rt_delta_min=abs(rt - old_rt),
    )


def _within_tolerance(
    row: Mapping[str, str],
    *,
    old_mz: float,
    old_rt: float,
    mz_tolerance_da: float,
    rt_tolerance_min: float,
) -> bool:
    mz = optional_float(row.get("Mz"))
    rt = optional_float(row.get("RT"))
    return (
        mz is not None
        and rt is not None
        and abs(mz - old_mz) <= mz_tolerance_da
        and abs(rt - old_rt) <= rt_tolerance_min
    )


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
        help="Return non-zero if the bridge gate is blocked.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    payload = evaluate_bridge_gate(
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
    print(f"cid_nl_default_activation_bridge_summary: {outputs['summary_json']}")
    print(f"cid_nl_default_activation_bridge_audit: {outputs['bridge_audit_tsv']}")
    print(f"cid_nl_default_activation_bridge_status: {payload['overall_status']}")
    if args.require_pass and payload["overall_status"] != "pass":
        for blocker in payload["blockers"]:
            print(f"cid_nl_default_activation_bridge_blocker: {blocker}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
