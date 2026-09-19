"""Check the CID-NL Discovery full-scope classification contract.

This is a no-RAW checker for the current CID-NL Discovery candidate universe.
It proves the 147 candidate cells are fully partitioned into accepted, held,
and blocked buckets, and that the activated 95-cell release slice is the only
default-output bucket in that universe.
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

from scripts.build_cid_nl_default_product_activation import (  # noqa: E402
    PRODUCT_AUTHORITY_SCOPE,
)
from tools.diagnostics import (
    cid_nl_feature_inclusion_gate as feature_gate,  # noqa: E402
)
from xic_extractor.tabular_io import (  # noqa: E402
    file_sha256,
    read_tsv_required,
    text_value,
    write_tsv,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "cid_nl_discovery_full_scope_classification"
PRODUCT_LANE = "cid_nl_discovery"
NO_AUTHORITY_EFFECT = "diagnostic_only_no_authority_change"
DEFAULT_GATE_DIR = (
    ROOT / "output/validation/cid_nl_default_activation_gallery_review"
    / "feature_inclusion_gate"
)
DEFAULT_ACTIVATION_SUMMARY_JSON = (
    ROOT
    / "docs/superpowers/validation/cid_nl_default_product_activation/"
    / "cid_nl_default_product_activation_summary.json"
)
DEFAULT_DOCS_DIR = (
    ROOT
    / "docs/superpowers/validation/cid_nl_discovery_full_scope_classification"
)

CHECK_COLUMNS = (
    "schema_version",
    "check_id",
    "status",
    "observed",
    "expected",
    "notes",
)
MANIFEST_COLUMNS = (
    "schema_version",
    "bucket_id",
    "product_bucket",
    "source_artifact",
    "transition_count",
    "candidate_cell_count",
    "existing_successor_context_cell_count",
    "omitted_no_target_cell_count",
    "product_effect",
    "product_authority_effect",
    "matrix_authority",
    "review_state",
)
EXPECTED_CHECK_IDS = (
    "feature_gate_status",
    "candidate_scope_count",
    "accepted_candidate_partition",
    "held_candidate_partition",
    "blocked_candidate_partition",
    "candidate_partition_complete",
    "transition_partition_complete",
    "accepted_matches_default_activation",
    "existing_successor_context_preserved",
    "omitted_no_target_preserved",
    "no_user_review_remaining",
    "no_product_authority_in_classifier",
    "accepted_contract_identity_complete",
    "hold_and_block_identity_complete",
    "bucket_overlap_absent",
    "target_300_184_source_context_preserved",
    "target_301_185_exact_pair_preserved",
    "activation_scope_unchanged",
)
EXPECTED_MANIFEST_ROWS = (
    {
        "bucket_id": "accepted_primary_supported",
        "product_bucket": "accepted_default_output_bucket",
        "source_artifact": (
            "feature_inclusion_gate/"
            "cid_nl_supported_candidate_expected_diff_contract.tsv"
        ),
        "transition_count": "14",
        "candidate_cell_count": "73",
        "existing_successor_context_cell_count": "0",
        "omitted_no_target_cell_count": "0",
        "product_effect": "write_cid_nl_discovery_default_cell_after_activation",
        "product_authority_effect": NO_AUTHORITY_EFFECT,
        "matrix_authority": "no_new_authority_from_classifier",
        "review_state": "expected_diff_contract_accepted",
    },
    {
        "bucket_id": "accepted_agent_resolved",
        "product_bucket": "accepted_default_output_bucket",
        "source_artifact": (
            "feature_inclusion_gate/"
            "cid_nl_agent_resolved_expected_diff_contract.tsv"
        ),
        "transition_count": "2",
        "candidate_cell_count": "9",
        "existing_successor_context_cell_count": "0",
        "omitted_no_target_cell_count": "0",
        "product_effect": "write_cid_nl_discovery_default_cell_after_activation",
        "product_authority_effect": NO_AUTHORITY_EFFECT,
        "matrix_authority": "no_new_authority_from_classifier",
        "review_state": "agent_resolved_expected_diff_contract_accepted",
    },
    {
        "bucket_id": "accepted_manual_resolved",
        "product_bucket": "accepted_default_output_bucket",
        "source_artifact": (
            "feature_inclusion_gate/"
            "cid_nl_manual_resolved_expected_diff_contract.tsv"
        ),
        "transition_count": "4",
        "candidate_cell_count": "13",
        "existing_successor_context_cell_count": "0",
        "omitted_no_target_cell_count": "0",
        "product_effect": "write_cid_nl_discovery_default_cell_after_activation",
        "product_authority_effect": NO_AUTHORITY_EFFECT,
        "matrix_authority": "no_new_authority_from_classifier",
        "review_state": "manual_resolved_expected_diff_contract_accepted",
    },
    {
        "bucket_id": "held_agent_resolved",
        "product_bucket": "held_out_current_bundle",
        "source_artifact": (
            "feature_inclusion_gate/cid_nl_agent_resolved_hold_queue.tsv"
        ),
        "transition_count": "6",
        "candidate_cell_count": "24",
        "existing_successor_context_cell_count": "0",
        "omitted_no_target_cell_count": "0",
        "product_effect": "no_default_write",
        "product_authority_effect": NO_AUTHORITY_EFFECT,
        "matrix_authority": "no_new_authority_from_classifier",
        "review_state": "agent_hold_current_bundle",
    },
    {
        "bucket_id": "held_manual_resolved",
        "product_bucket": "held_out_current_bundle",
        "source_artifact": (
            "feature_inclusion_gate/cid_nl_manual_resolved_hold_queue.tsv"
        ),
        "transition_count": "0",
        "candidate_cell_count": "0",
        "existing_successor_context_cell_count": "0",
        "omitted_no_target_cell_count": "0",
        "product_effect": "no_default_write",
        "product_authority_effect": NO_AUTHORITY_EFFECT,
        "matrix_authority": "no_new_authority_from_classifier",
        "review_state": "manual_hold_current_bundle",
    },
    {
        "bucket_id": "held_user_review",
        "product_bucket": "held_out_current_bundle",
        "source_artifact": "feature_inclusion_gate/cid_nl_user_review_queue.tsv",
        "transition_count": "0",
        "candidate_cell_count": "0",
        "existing_successor_context_cell_count": "0",
        "omitted_no_target_cell_count": "0",
        "product_effect": "no_default_write",
        "product_authority_effect": NO_AUTHORITY_EFFECT,
        "matrix_authority": "no_new_authority_from_classifier",
        "review_state": "user_review_required",
    },
    {
        "bucket_id": "blocked_current_overlay",
        "product_bucket": "blocked_current_bundle",
        "source_artifact": (
            "feature_inclusion_gate/"
            "cid_nl_feature_inclusion_blocked_queue.tsv"
        ),
        "transition_count": "17",
        "candidate_cell_count": "28",
        "existing_successor_context_cell_count": "0",
        "omitted_no_target_cell_count": "0",
        "product_effect": "no_default_write",
        "product_authority_effect": NO_AUTHORITY_EFFECT,
        "matrix_authority": "no_new_authority_from_classifier",
        "review_state": "blocked_by_current_overlay",
    },
    {
        "bucket_id": "existing_successor_context",
        "product_bucket": "preserved_context_no_write",
        "source_artifact": (
            "feature_inclusion_gate/"
            "cid_nl_feature_inclusion_gate_transitions.tsv"
        ),
        "transition_count": "35",
        "candidate_cell_count": "0",
        "existing_successor_context_cell_count": "337",
        "omitted_no_target_cell_count": "0",
        "product_effect": "preserve_existing_successor_context",
        "product_authority_effect": NO_AUTHORITY_EFFECT,
        "matrix_authority": "no_new_authority_from_classifier",
        "review_state": "already_detected_successor_context",
    },
    {
        "bucket_id": "omitted_no_successor_target",
        "product_bucket": "omitted_no_target_no_write",
        "source_artifact": (
            "feature_inclusion_gate/"
            "cid_nl_feature_inclusion_gate_transitions.tsv"
        ),
        "transition_count": "9",
        "candidate_cell_count": "0",
        "existing_successor_context_cell_count": "0",
        "omitted_no_target_cell_count": "27",
        "product_effect": "preserve_omitted_no_target",
        "product_authority_effect": NO_AUTHORITY_EFFECT,
        "matrix_authority": "no_new_authority_from_classifier",
        "review_state": "no_safe_successor_target",
    },
)


def build_cid_nl_discovery_full_scope_classification(
    *,
    gate_dir: Path = DEFAULT_GATE_DIR,
    activation_summary_json: Path = DEFAULT_ACTIVATION_SUMMARY_JSON,
    docs_dir: Path = DEFAULT_DOCS_DIR,
) -> dict[str, Any]:
    gate_summary = _read_json_object(
        gate_dir / "cid_nl_feature_inclusion_gate_summary.json"
    )
    activation_summary = _read_json_object(activation_summary_json)
    rows = _load_rows(gate_dir)

    checks = _check_rows(
        gate_summary=gate_summary,
        activation_summary=activation_summary,
        rows=rows,
    )
    failed = [row["check_id"] for row in checks if row["status"] != "pass"]
    if failed:
        raise ValueError(
            "CID-NL Discovery full-scope classification failed: "
            + ";".join(failed)
        )

    manifest_rows = _manifest_rows(rows)
    docs_dir.mkdir(parents=True, exist_ok=True)
    checks_tsv = docs_dir / "cid_nl_discovery_full_scope_classification_checks.tsv"
    manifest_tsv = (
        docs_dir / "cid_nl_discovery_full_scope_classification_manifest.tsv"
    )
    summary_json = docs_dir / "cid_nl_discovery_full_scope_classification_summary.json"
    write_tsv(
        checks_tsv,
        checks,
        CHECK_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    write_tsv(
        manifest_tsv,
        manifest_rows,
        MANIFEST_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )

    payload = _summary_payload(
        gate_summary=gate_summary,
        activation_summary=activation_summary,
        rows=rows,
        manifest_tsv=manifest_tsv,
        checks_tsv=checks_tsv,
    )
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def check_cid_nl_discovery_full_scope_classification(
    *,
    summary_json: Path = DEFAULT_DOCS_DIR
    / "cid_nl_discovery_full_scope_classification_summary.json",
    checks_tsv: Path = DEFAULT_DOCS_DIR
    / "cid_nl_discovery_full_scope_classification_checks.tsv",
    manifest_tsv: Path = DEFAULT_DOCS_DIR
    / "cid_nl_discovery_full_scope_classification_manifest.tsv",
) -> list[str]:
    problems: list[str] = []
    try:
        payload = _read_json_object(summary_json)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]
    expected_fields: tuple[tuple[str, object], ...] = (
        ("schema_version", SCHEMA_VERSION),
        ("status", "pass"),
        ("product_lane", PRODUCT_LANE),
        ("full_scope_kind", "cid_nl_discovery_candidate_universe"),
        ("candidate_cell_count", 147),
        ("accepted_discovery_cell_count", 95),
        ("held_candidate_cell_count", 24),
        ("blocked_candidate_cell_count", 28),
        ("existing_successor_context_cell_count", 337),
        ("omitted_no_target_cell_count", 27),
        ("user_review_cell_count", 0),
        ("default_activation_cell_count", 95),
        ("product_writer_changed", False),
        ("default_quant_matrix_changed", False),
        ("workbook_or_gui_changed", False),
        ("selected_peak_area_or_counting_changed", False),
        ("candidate_rows_are_matrix_rows", False),
        ("cid_nl_ms2_direct_productwriter_authority", False),
        ("check_only_scope", "retained_compact_artifacts_only"),
    )
    for field, expected in expected_fields:
        if payload.get(field) != expected:
            problems.append(f"summary {field} mismatch")
    _check_summary_artifact_hash(
        payload=payload,
        artifact_id="checks_tsv",
        path=checks_tsv,
        problems=problems,
    )
    _check_summary_artifact_hash(
        payload=payload,
        artifact_id="manifest_tsv",
        path=manifest_tsv,
        problems=problems,
    )

    try:
        checks = read_tsv_required(checks_tsv, CHECK_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"checks_tsv: {exc}")
    else:
        check_ids = [row.get("check_id", "") for row in checks]
        if len(checks) != len(EXPECTED_CHECK_IDS):
            problems.append(f"checks row count mismatch: {len(checks)}")
        missing = sorted(set(EXPECTED_CHECK_IDS) - set(check_ids))
        unexpected = sorted(set(check_ids) - set(EXPECTED_CHECK_IDS))
        duplicate_count = len(check_ids) - len(set(check_ids))
        if missing:
            problems.append("checks missing required ids: " + ";".join(missing))
        if unexpected:
            problems.append("checks unexpected ids: " + ";".join(unexpected))
        if duplicate_count:
            problems.append(f"checks duplicate id count: {duplicate_count}")
        failed = [row["check_id"] for row in checks if row.get("status") != "pass"]
        if failed:
            problems.append("failed checks: " + ";".join(failed))

    try:
        manifest = read_tsv_required(manifest_tsv, MANIFEST_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"manifest_tsv: {exc}")
    else:
        if len(manifest) != 9:
            problems.append(f"manifest row count mismatch: {len(manifest)}")
        if sum(_int(row.get("candidate_cell_count")) for row in manifest) != 147:
            problems.append("manifest candidate partition mismatch")
        existing_context_count = sum(
            _int(row.get("existing_successor_context_cell_count"))
            for row in manifest
        )
        if (
            existing_context_count != 337
        ):
            problems.append("manifest existing successor context mismatch")
        if sum(_int(row.get("omitted_no_target_cell_count")) for row in manifest) != 27:
            problems.append("manifest omitted no-target mismatch")
        _check_manifest_rows(manifest, problems)
    return problems


def _check_summary_artifact_hash(
    *,
    payload: Mapping[str, Any],
    artifact_id: str,
    path: Path,
    problems: list[str],
) -> None:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Mapping):
        problems.append("summary artifacts mismatch")
        return
    artifact = artifacts.get(artifact_id)
    if not isinstance(artifact, Mapping):
        problems.append(f"summary artifacts missing {artifact_id}")
        return
    expected_hash = text_value(artifact.get("sha256"))
    if not expected_hash:
        problems.append(f"summary {artifact_id} sha256 missing")
        return
    try:
        observed_hash = file_sha256(path)
    except OSError as exc:
        problems.append(f"{artifact_id} sha256 cannot read: {exc}")
        return
    if observed_hash != expected_hash:
        problems.append(f"summary {artifact_id} sha256 mismatch")


def _check_manifest_rows(
    manifest: Sequence[Mapping[str, str]],
    problems: list[str],
) -> None:
    expected_by_bucket = {
        row["bucket_id"]: row for row in EXPECTED_MANIFEST_ROWS
    }
    rows_by_bucket: dict[str, Mapping[str, str]] = {}
    duplicate_bucket_count = 0
    for row in manifest:
        bucket_id = text_value(row.get("bucket_id"))
        if bucket_id in rows_by_bucket:
            duplicate_bucket_count += 1
        rows_by_bucket[bucket_id] = row
    missing = sorted(set(expected_by_bucket) - set(rows_by_bucket))
    unexpected = sorted(set(rows_by_bucket) - set(expected_by_bucket))
    if missing:
        problems.append("manifest missing buckets: " + ";".join(missing))
    if unexpected:
        problems.append("manifest unexpected buckets: " + ";".join(unexpected))
    if duplicate_bucket_count:
        problems.append(f"manifest duplicate bucket count: {duplicate_bucket_count}")
    for bucket_id, expected in expected_by_bucket.items():
        observed = rows_by_bucket.get(bucket_id)
        if observed is None:
            continue
        for field, expected_value in expected.items():
            if text_value(observed.get(field)) != expected_value:
                problems.append(f"manifest {bucket_id} {field} mismatch")
        if text_value(observed.get("schema_version")) != SCHEMA_VERSION:
            problems.append(f"manifest {bucket_id} schema_version mismatch")


def _load_rows(gate_dir: Path) -> dict[str, tuple[dict[str, str], ...]]:
    return {
        "transitions": read_tsv_required(
            gate_dir / "cid_nl_feature_inclusion_gate_transitions.tsv",
            feature_gate.GATE_TRANSITION_COLUMNS,
        ),
        "primary_contract": read_tsv_required(
            gate_dir / "cid_nl_supported_candidate_expected_diff_contract.tsv",
            feature_gate.EXPECTED_DIFF_COLUMNS,
        ),
        "agent_contract": read_tsv_required(
            gate_dir / "cid_nl_agent_resolved_expected_diff_contract.tsv",
            feature_gate.EXPECTED_DIFF_COLUMNS,
        ),
        "manual_contract": read_tsv_required(
            gate_dir / "cid_nl_manual_resolved_expected_diff_contract.tsv",
            feature_gate.EXPECTED_DIFF_COLUMNS,
        ),
        "agent_hold": read_tsv_required(
            gate_dir / "cid_nl_agent_resolved_hold_queue.tsv",
            feature_gate.REVIEW_RESOLUTION_COLUMNS,
        ),
        "manual_hold": read_tsv_required(
            gate_dir / "cid_nl_manual_resolved_hold_queue.tsv",
            feature_gate.REVIEW_RESOLUTION_COLUMNS,
        ),
        "user_review": read_tsv_required(
            gate_dir / "cid_nl_user_review_queue.tsv",
            feature_gate.REVIEW_RESOLUTION_COLUMNS,
        ),
        "blocked": read_tsv_required(
            gate_dir / "cid_nl_feature_inclusion_blocked_queue.tsv",
            feature_gate.GATE_TRANSITION_COLUMNS,
        ),
    }


def _check_rows(
    *,
    gate_summary: Mapping[str, Any],
    activation_summary: Mapping[str, Any],
    rows: Mapping[str, tuple[Mapping[str, str], ...]],
) -> list[dict[str, Any]]:
    accepted = _accepted_rows(rows)
    held_cells = _candidate_count(rows["agent_hold"]) + _candidate_count(
        rows["manual_hold"]
    ) + _candidate_count(rows["user_review"])
    blocked_cells = _candidate_count(rows["blocked"])
    existing_context = sum(
        _int(row.get("existing_successor_context_cell_count"))
        for row in rows["transitions"]
    )
    omitted_no_target = sum(
        _int(row.get("omitted_no_target_cell_count")) for row in rows["transitions"]
    )
    accepted_transitions = _transition_count(
        rows["primary_contract"], rows["agent_contract"], rows["manual_contract"]
    )
    held_transitions = (
        len(rows["agent_hold"]) + len(rows["manual_hold"]) + len(rows["user_review"])
    )
    existing_context_transitions = sum(
        1
        for row in rows["transitions"]
        if _int(row.get("candidate_cell_count")) == 0
        and _int(row.get("existing_successor_context_cell_count")) > 0
    )
    omitted_transitions = sum(
        1
        for row in rows["transitions"]
        if _int(row.get("omitted_no_target_cell_count")) > 0
    )

    checks = [
        _check(
            "feature_gate_status",
            gate_summary.get("overall_status"),
            "pass",
            gate_summary.get("overall_status") == "pass",
        ),
        _check(
            "candidate_scope_count",
            gate_summary.get("candidate_cell_count"),
            147,
            _int(gate_summary.get("candidate_cell_count")) == 147,
        ),
        _check(
            "accepted_candidate_partition",
            len(accepted),
            95,
            len(accepted) == 95,
        ),
        _check(
            "held_candidate_partition",
            held_cells,
            24,
            held_cells == 24,
        ),
        _check(
            "blocked_candidate_partition",
            blocked_cells,
            28,
            blocked_cells == 28,
        ),
        _check(
            "candidate_partition_complete",
            len(accepted) + held_cells + blocked_cells,
            147,
            len(accepted) + held_cells + blocked_cells == 147,
        ),
        _check(
            "transition_partition_complete",
            accepted_transitions
            + held_transitions
            + len(rows["blocked"])
            + existing_context_transitions
            + omitted_transitions,
            87,
            (
                accepted_transitions
                + held_transitions
                + len(rows["blocked"])
                + existing_context_transitions
                + omitted_transitions
            )
            == 87,
        ),
        _check(
            "accepted_matches_default_activation",
            (
                f"classification={len(accepted)};"
                f"default={activation_summary.get('accepted_discovery_cell_count')}"
            ),
            "95",
            len(accepted)
            == _int(activation_summary.get("accepted_discovery_cell_count"))
            == 95,
        ),
        _check(
            "existing_successor_context_preserved",
            existing_context,
            337,
            existing_context == 337,
        ),
        _check(
            "omitted_no_target_preserved",
            omitted_no_target,
            27,
            omitted_no_target == 27,
        ),
        _check(
            "no_user_review_remaining",
            _candidate_count(rows["user_review"]),
            0,
            _candidate_count(rows["user_review"]) == 0,
        ),
        _check(
            "no_product_authority_in_classifier",
            _authority_drift_count(rows),
            0,
            _authority_drift_count(rows) == 0,
        ),
        _check(
            "accepted_contract_identity_complete",
            _accepted_contract_problem_count(accepted),
            0,
            _accepted_contract_problem_count(accepted) == 0,
        ),
        _check(
            "hold_and_block_identity_complete",
            _hold_block_problem_count(rows),
            0,
            _hold_block_problem_count(rows) == 0,
        ),
        _check(
            "bucket_overlap_absent",
            _transition_overlap_count(rows),
            0,
            _transition_overlap_count(rows) == 0,
        ),
        _check(
            "target_300_184_source_context_preserved",
            _target_guardrail_count(
                accepted,
                transition_key="FAM011440->FAM015713",
                source_mz_prefix="300.16",
                source_product_mz="184.113",
                guardrail_flag="target_guardrail_300_184_source_context",
            ),
            2,
            _target_guardrail_count(
                accepted,
                transition_key="FAM011440->FAM015713",
                source_mz_prefix="300.16",
                source_product_mz="184.113",
                guardrail_flag="target_guardrail_300_184_source_context",
            )
            == 2,
            (
                "300.1605 -> 184.113 remains source context; "
                "successor feature inclusion only"
            ),
        ),
        _check(
            "target_301_185_exact_pair_preserved",
            _target_guardrail_count(
                accepted,
                transition_key="FAM011837->FAM016144",
                source_mz_prefix="301.165",
                source_product_mz="185.116",
                guardrail_flag="target_guardrail_301_185_exact_source_context",
            ),
            1,
            _target_guardrail_count(
                accepted,
                transition_key="FAM011837->FAM016144",
                source_mz_prefix="301.165",
                source_product_mz="185.116",
                guardrail_flag="target_guardrail_301_185_exact_source_context",
            )
            == 1,
            "301.165 -> 185.116 remains its own DNA_dR source-tag context",
        ),
        _check(
            "activation_scope_unchanged",
            activation_summary.get("product_authority_scope"),
            PRODUCT_AUTHORITY_SCOPE,
            activation_summary.get("product_authority_scope")
            == PRODUCT_AUTHORITY_SCOPE,
        ),
    ]
    return checks


def _manifest_rows(
    rows: Mapping[str, tuple[Mapping[str, str], ...]],
) -> list[dict[str, Any]]:
    buckets = [
        _manifest_row(
            "accepted_primary_supported",
            "accepted_default_output_bucket",
            "feature_inclusion_gate/cid_nl_supported_candidate_expected_diff_contract.tsv",
            _transition_count(rows["primary_contract"]),
            len(rows["primary_contract"]),
            "write_cid_nl_discovery_default_cell_after_activation",
            "expected_diff_contract_accepted",
        ),
        _manifest_row(
            "accepted_agent_resolved",
            "accepted_default_output_bucket",
            "feature_inclusion_gate/cid_nl_agent_resolved_expected_diff_contract.tsv",
            _transition_count(rows["agent_contract"]),
            len(rows["agent_contract"]),
            "write_cid_nl_discovery_default_cell_after_activation",
            "agent_resolved_expected_diff_contract_accepted",
        ),
        _manifest_row(
            "accepted_manual_resolved",
            "accepted_default_output_bucket",
            "feature_inclusion_gate/cid_nl_manual_resolved_expected_diff_contract.tsv",
            _transition_count(rows["manual_contract"]),
            len(rows["manual_contract"]),
            "write_cid_nl_discovery_default_cell_after_activation",
            "manual_resolved_expected_diff_contract_accepted",
        ),
        _manifest_row(
            "held_agent_resolved",
            "held_out_current_bundle",
            "feature_inclusion_gate/cid_nl_agent_resolved_hold_queue.tsv",
            len(rows["agent_hold"]),
            _candidate_count(rows["agent_hold"]),
            "no_default_write",
            "agent_hold_current_bundle",
        ),
        _manifest_row(
            "held_manual_resolved",
            "held_out_current_bundle",
            "feature_inclusion_gate/cid_nl_manual_resolved_hold_queue.tsv",
            len(rows["manual_hold"]),
            _candidate_count(rows["manual_hold"]),
            "no_default_write",
            "manual_hold_current_bundle",
        ),
        _manifest_row(
            "held_user_review",
            "held_out_current_bundle",
            "feature_inclusion_gate/cid_nl_user_review_queue.tsv",
            len(rows["user_review"]),
            _candidate_count(rows["user_review"]),
            "no_default_write",
            "user_review_required",
        ),
        _manifest_row(
            "blocked_current_overlay",
            "blocked_current_bundle",
            "feature_inclusion_gate/cid_nl_feature_inclusion_blocked_queue.tsv",
            len(rows["blocked"]),
            _candidate_count(rows["blocked"]),
            "no_default_write",
            "blocked_by_current_overlay",
        ),
        _manifest_row(
            "existing_successor_context",
            "preserved_context_no_write",
            "feature_inclusion_gate/cid_nl_feature_inclusion_gate_transitions.tsv",
            sum(
                1
                for row in rows["transitions"]
                if _int(row.get("candidate_cell_count")) == 0
                and _int(row.get("existing_successor_context_cell_count")) > 0
            ),
            0,
            "preserve_existing_successor_context",
            "already_detected_successor_context",
            existing_successor_context_cell_count=sum(
                _int(row.get("existing_successor_context_cell_count"))
                for row in rows["transitions"]
            ),
        ),
        _manifest_row(
            "omitted_no_successor_target",
            "omitted_no_target_no_write",
            "feature_inclusion_gate/cid_nl_feature_inclusion_gate_transitions.tsv",
            sum(
                1
                for row in rows["transitions"]
                if _int(row.get("omitted_no_target_cell_count")) > 0
            ),
            0,
            "preserve_omitted_no_target",
            "no_safe_successor_target",
            omitted_no_target_cell_count=sum(
                _int(row.get("omitted_no_target_cell_count"))
                for row in rows["transitions"]
            ),
        ),
    ]
    return buckets


def _manifest_row(
    bucket_id: str,
    product_bucket: str,
    source_artifact: str,
    transition_count: int,
    candidate_cell_count: int,
    product_effect: str,
    review_state: str,
    *,
    existing_successor_context_cell_count: int = 0,
    omitted_no_target_cell_count: int = 0,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "bucket_id": bucket_id,
        "product_bucket": product_bucket,
        "source_artifact": source_artifact,
        "transition_count": transition_count,
        "candidate_cell_count": candidate_cell_count,
        "existing_successor_context_cell_count": existing_successor_context_cell_count,
        "omitted_no_target_cell_count": omitted_no_target_cell_count,
        "product_effect": product_effect,
        "product_authority_effect": NO_AUTHORITY_EFFECT,
        "matrix_authority": "no_new_authority_from_classifier",
        "review_state": review_state,
    }


def _summary_payload(
    *,
    gate_summary: Mapping[str, Any],
    activation_summary: Mapping[str, Any],
    rows: Mapping[str, tuple[Mapping[str, str], ...]],
    manifest_tsv: Path,
    checks_tsv: Path,
) -> dict[str, Any]:
    accepted = _accepted_rows(rows)
    held_cells = _candidate_count(rows["agent_hold"]) + _candidate_count(
        rows["manual_hold"]
    ) + _candidate_count(rows["user_review"])
    blocked_cells = _candidate_count(rows["blocked"])
    manifest_hash = file_sha256(manifest_tsv)
    checks_hash = file_sha256(checks_tsv)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "validation_label": "no_raw_full_scope_classification",
        "product_lane": PRODUCT_LANE,
        "full_scope_kind": "cid_nl_discovery_candidate_universe",
        "check_only_scope": "retained_compact_artifacts_only",
        "candidate_cell_count": _int(gate_summary.get("candidate_cell_count")),
        "accepted_discovery_cell_count": len(accepted),
        "held_candidate_cell_count": held_cells,
        "blocked_candidate_cell_count": blocked_cells,
        "existing_successor_context_cell_count": _int(
            gate_summary.get("existing_successor_context_cell_count")
        ),
        "omitted_no_target_cell_count": _int(
            gate_summary.get("omitted_no_target_cell_count")
        ),
        "transition_count": _int(gate_summary.get("transition_count")),
        "accepted_transition_count": _transition_count(
            rows["primary_contract"], rows["agent_contract"], rows["manual_contract"]
        ),
        "held_transition_count": (
            len(rows["agent_hold"])
            + len(rows["manual_hold"])
            + len(rows["user_review"])
        ),
        "blocked_transition_count": len(rows["blocked"]),
        "user_review_cell_count": _candidate_count(rows["user_review"]),
        "default_activation_cell_count": _int(
            activation_summary.get("accepted_discovery_cell_count")
        ),
        "default_activation_scope": activation_summary.get(
            "product_authority_scope",
            "",
        ),
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "workbook_or_gui_changed": False,
        "selected_peak_area_or_counting_changed": False,
        "candidate_rows_are_matrix_rows": False,
        "cid_nl_ms2_direct_productwriter_authority": False,
        "raw_or_full_cohort_ran": False,
        "artifacts": {
            "summary_json": {
                "path": (
                    "docs/superpowers/validation/"
                    "cid_nl_discovery_full_scope_classification/"
                    "cid_nl_discovery_full_scope_classification_summary.json"
                ),
                "retention_decision": "keep_summary",
            },
            "manifest_tsv": {
                "path": _relative_or_absolute(manifest_tsv),
                "sha256": manifest_hash,
                "retention_decision": "keep_contract",
            },
            "checks_tsv": {
                "path": _relative_or_absolute(checks_tsv),
                "sha256": checks_hash,
                "retention_decision": "keep_summary",
            },
        },
        "source_artifacts": {
            "feature_gate_summary_json": {
                "path": (
                    "output/validation/cid_nl_default_activation_gallery_review/"
                    "feature_inclusion_gate/"
                    "cid_nl_feature_inclusion_gate_summary.json"
                ),
                "retention_decision": (
                    "externalized_existing_gate_output_not_required_by_check_only"
                ),
            },
            "default_activation_summary_json": {
                "path": _relative_or_absolute(DEFAULT_ACTIVATION_SUMMARY_JSON),
                "retention_decision": "tracked_product_summary",
            },
        },
        "authority_statement": (
            "This checker classifies the current CID-NL Discovery candidate "
            "universe. It does not create ProductWriter authority. The only "
            "default-output bucket remains the separately activated 95-cell "
            "expected-diff/provenance bundle."
        ),
        "rule_statement": (
            "Accepted = expected-diff contract rows from primary, agent-resolved, "
            "and manual-resolved supported feature-inclusion buckets. Held = "
            "agent/manual/user review buckets retained outside the current bundle. "
            "Blocked = current paired overlay rejects. Existing-successor context "
            "and omitted no-target rows are preserved no-write context."
        ),
    }


def _accepted_rows(
    rows: Mapping[str, tuple[Mapping[str, str], ...]],
) -> tuple[Mapping[str, str], ...]:
    return rows["primary_contract"] + rows["agent_contract"] + rows["manual_contract"]


def _accepted_contract_problem_count(rows: Sequence[Mapping[str, str]]) -> int:
    required = (
        "transition_key",
        "sample_stem",
        "source_peak_hypothesis_id",
        "successor_peak_hypothesis_id",
        "source_mz",
        "source_rt",
        "source_product_mz",
        "source_neutral_loss_tag",
        "source_identity_decision",
        "successor_mz",
        "successor_rt",
        "successor_product_mz",
        "successor_neutral_loss_tag",
        "successor_identity_decision",
        "candidate_quant_value",
        "legacy_successor_matrix_effect",
        "legacy_successor_write_authority",
        "legacy_successor_matrix_write_allowed",
        "legacy_input_resolution_status",
        "feature_inclusion_review_status",
        "identity_authority_status",
        "authority_gate",
        "product_authority_effect",
        "expected_product_effect",
        "trace_data_json",
    )
    problems = 0
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if any(not text_value(row.get(field)) for field in required):
            problems += 1
            continue
        key = (
            text_value(row.get("transition_key")),
            text_value(row.get("successor_peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
        )
        if key in seen:
            problems += 1
        seen.add(key)
        if row.get("authority_gate") != (
            "candidate_only_expected_diff_required_no_product_write"
        ):
            problems += 1
        if row.get("product_authority_effect") != NO_AUTHORITY_EFFECT:
            problems += 1
        if row.get("expected_product_effect") != (
            "candidate_cell_expected_diff_design_only"
        ):
            problems += 1
    return problems


def _hold_block_problem_count(
    rows: Mapping[str, tuple[Mapping[str, str], ...]],
) -> int:
    problems = 0
    for row in rows["agent_hold"] + rows["manual_hold"] + rows["user_review"]:
        for field in (
            "transition_key",
            "source_peak_hypothesis_id",
            "successor_peak_hypothesis_id",
            "review_resolution_action",
            "review_resolution_status",
            "review_resolution_reason",
            "product_authority_effect",
            "png_path",
            "trace_data_json",
        ):
            if not text_value(row.get(field)):
                problems += 1
        if text_value(row.get("product_authority_effect")) != NO_AUTHORITY_EFFECT:
            problems += 1
    for row in rows["blocked"]:
        for field in (
            "transition_key",
            "source_peak_hypothesis_id",
            "successor_peak_hypothesis_id",
            "feature_inclusion_review_status",
            "identity_authority_status",
            "product_gate_action",
            "product_authority_effect",
            "review_reason",
            "png_path",
            "trace_data_json",
        ):
            if not text_value(row.get(field)):
                problems += 1
        if text_value(row.get("product_authority_effect")) != NO_AUTHORITY_EFFECT:
            problems += 1
        if text_value(row.get("product_gate_action")) != (
            "exclude_from_current_activation_bundle"
        ):
            problems += 1
    return problems


def _authority_drift_count(
    rows: Mapping[str, tuple[Mapping[str, str], ...]],
) -> int:
    count = 0
    for bucket in (
        "transitions",
        "primary_contract",
        "agent_contract",
        "manual_contract",
        "agent_hold",
        "manual_hold",
        "user_review",
        "blocked",
    ):
        for row in rows[bucket]:
            if text_value(row.get("product_authority_effect")) != NO_AUTHORITY_EFFECT:
                count += 1
    return count


def _transition_overlap_count(
    rows: Mapping[str, tuple[Mapping[str, str], ...]],
) -> int:
    accepted = _transition_keys(
        rows["primary_contract"], rows["agent_contract"], rows["manual_contract"]
    )
    held = _transition_keys(
        rows["agent_hold"],
        rows["manual_hold"],
        rows["user_review"],
    )
    blocked = _transition_keys(rows["blocked"])
    return len((accepted & held) | (accepted & blocked) | (held & blocked))


def _target_guardrail_count(
    rows: Sequence[Mapping[str, str]],
    *,
    transition_key: str,
    source_mz_prefix: str,
    source_product_mz: str,
    guardrail_flag: str,
) -> int:
    return sum(
        1
        for row in rows
        if text_value(row.get("transition_key")) == transition_key
        and text_value(row.get("source_mz")).startswith(source_mz_prefix)
        and text_value(row.get("source_product_mz")) == source_product_mz
        and text_value(row.get("source_neutral_loss_tag")) == "DNA_dR"
        and text_value(row.get("guardrail_flag")) == guardrail_flag
        and text_value(row.get("identity_authority_status"))
        == "identity_guardrail_review_required"
    )


def _candidate_count(rows: Sequence[Mapping[str, str]]) -> int:
    return sum(_int(row.get("candidate_cell_count")) for row in rows)


def _transition_count(*groups: Sequence[Mapping[str, str]]) -> int:
    return len(_transition_keys(*groups))


def _transition_keys(*groups: Sequence[Mapping[str, str]]) -> set[str]:
    return {
        text_value(row.get("transition_key"))
        for group in groups
        for row in group
        if text_value(row.get("transition_key"))
    }


def _check(
    check_id: str,
    observed: object,
    expected: object,
    ok: bool,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "check_id": check_id,
        "status": "pass" if ok else "fail",
        "observed": observed,
        "expected": expected,
        "notes": notes,
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def _relative_or_absolute(path: Path) -> str:
    try:
        return (
            path.resolve(strict=False)
            .relative_to(ROOT.resolve(strict=False))
            .as_posix()
        )
    except ValueError:
        return str(path)


def _int(value: object) -> int:
    try:
        return int(float(text_value(value)))
    except ValueError:
        return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate-dir", type=Path, default=DEFAULT_GATE_DIR)
    parser.add_argument(
        "--activation-summary-json",
        type=Path,
        default=DEFAULT_ACTIVATION_SUMMARY_JSON,
    )
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--checks-tsv", type=Path)
    parser.add_argument("--manifest-tsv", type=Path)
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check_only:
        summary_json = args.summary_json or (
            args.docs_dir / "cid_nl_discovery_full_scope_classification_summary.json"
        )
        checks_tsv = args.checks_tsv or (
            args.docs_dir / "cid_nl_discovery_full_scope_classification_checks.tsv"
        )
        manifest_tsv = args.manifest_tsv or (
            args.docs_dir / "cid_nl_discovery_full_scope_classification_manifest.tsv"
        )
        problems = check_cid_nl_discovery_full_scope_classification(
            summary_json=summary_json,
            checks_tsv=checks_tsv,
            manifest_tsv=manifest_tsv,
        )
        for problem in problems:
            print(f"cid_nl_discovery_full_scope_classification_problem: {problem}")
        return 2 if problems else 0
    try:
        payload = build_cid_nl_discovery_full_scope_classification(
            gate_dir=args.gate_dir,
            activation_summary_json=args.activation_summary_json,
            docs_dir=args.docs_dir,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        "cid_nl_discovery_full_scope_classification_summary: "
        f"{args.docs_dir / 'cid_nl_discovery_full_scope_classification_summary.json'}"
    )
    print(f"cid_nl_discovery_full_scope_classification_status: {payload['status']}")
    print(
        "cid_nl_discovery_full_scope_classification_accepted_cells: "
        f"{payload['accepted_discovery_cell_count']}"
    )
    if args.require_pass and payload.get("status") != "pass":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
