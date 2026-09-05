"""Check CID-NL FULL COHORT-derived Discovery universe closure.

This no-RAW checker binds the current FULL COHORT-derived successor-authority
artifact to the CID-NL Discovery product claim:

- 511 successor decisions are fully partitioned.
- 147 write-authorized candidate cells are fully classified as accepted, held,
  or blocked.
- The active default activation writes exactly the 95 accepted cells.

It does not rerun RAW, create a second Discovery system, or mutate product
outputs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_cid_nl_default_product_activation import (  # noqa: E402
    DEFAULT_DOCS_DIR as DEFAULT_ACTIVATION_DOCS_DIR,
)
from scripts.build_cid_nl_default_product_activation import (  # noqa: E402
    DISCOVERY_DEFAULT_EFFECT,
    PRODUCT_AUTHORITY_SCOPE,
    validate_cid_nl_default_product_activation,
)
from scripts.check_cid_nl_discovery_full_scope_classification import (  # noqa: E402
    DEFAULT_DOCS_DIR as DEFAULT_FULL_SCOPE_DOCS_DIR,
)
from scripts.check_cid_nl_discovery_full_scope_classification import (  # noqa: E402
    check_cid_nl_discovery_full_scope_classification,
)
from scripts.check_production_acceptance_manifest import (  # noqa: E402
    REQUIRED_COLUMNS as PRODUCTION_ACCEPTANCE_COLUMNS,
)
from tools.diagnostics import (  # noqa: E402
    cid_nl_feature_inclusion_gate as feature_gate,
)
from xic_extractor.tabular_io import (  # noqa: E402
    file_sha256,
    read_tsv_required,
    text_value,
    write_tsv,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "cid_nl_default_activation_universe_closure_v1"
PRODUCT_LANE = "cid_nl_discovery"
DEFAULT_GATE_DIR = (
    ROOT
    / "output/validation/cid_nl_default_activation_gallery_review_v1"
    / "feature_inclusion_gate"
)
DEFAULT_SUCCESSOR_AUTHORITY_DIR = (
    ROOT
    / "output/validation/"
    / "cid_nl_default_activation_successor_authority_contract_v1"
)
DEFAULT_SUCCESSOR_AUTHORITY_SUMMARY_JSON = (
    DEFAULT_SUCCESSOR_AUTHORITY_DIR
    / "cid_nl_default_activation_successor_authority_summary.json"
)
DEFAULT_SUCCESSOR_AUTHORITY_DECISIONS_TSV = (
    DEFAULT_SUCCESSOR_AUTHORITY_DIR / "successor_authority_decisions.tsv"
)
DEFAULT_SUCCESSOR_AUTHORITY_MANIFEST_TSV = (
    DEFAULT_SUCCESSOR_AUTHORITY_DIR / "successor_authority_manifest.tsv"
)
DEFAULT_ACTIVATION_SUMMARY_JSON = (
    DEFAULT_ACTIVATION_DOCS_DIR / "cid_nl_default_product_activation_summary.json"
)
DEFAULT_FULL_SCOPE_SUMMARY_JSON = (
    DEFAULT_FULL_SCOPE_DOCS_DIR
    / "cid_nl_discovery_full_scope_classification_summary.json"
)
RETAINED_DOCS_DIR = (
    ROOT / "docs/superpowers/validation/cid_nl_85raw_universe_closure_v1"
)
DEFAULT_DOCS_DIR = (
    ROOT / "docs/superpowers/validation/cid_nl_full_cohort_universe_closure_v1"
)
RETAINED_SUMMARY_FILENAME = "cid_nl_85raw_universe_closure_summary.json"
RETAINED_CHECKS_FILENAME = "cid_nl_85raw_universe_closure_checks.tsv"
RETAINED_MANIFEST_FILENAME = "cid_nl_85raw_universe_closure_manifest.tsv"
RETAINED_SCHEMA_VERSION = "cid_nl_85raw_universe_closure_v1"
RETAINED_CLOSURE_SCOPE = "current_cid_nl_85raw_fix3_successor_universe"
RETAINED_INPUT_FRAGMENT = (
    "output/discovery/cid_nl_product_ready_alignment_85raw_20260620_fix3"
)
EXPECTED_FULL_COHORT_INPUT_FRAGMENT = (
    "output/discovery/cid_nl_product_ready_alignment_full_cohort_20260620_fix3"
)

SUCCESSOR_DECISION_COLUMNS = (
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
    "source_basis",
    "transition_count",
    "cell_count",
    "decision_count",
    "matrix_write_allowed",
    "default_activation_effect",
    "product_authority_effect",
    "notes",
)
EXPECTED_CHECK_IDS = (
    "default_activation_checker_pass",
    "full_scope_classification_checker_pass",
    "successor_decision_total_count",
    "successor_decision_partition",
    "successor_write_authority_flags",
    "successor_manifest_keyset_matches_decisions",
    "candidate_transition_partition_exact",
    "accepted_default_keyset_exact",
    "accepted_default_activation_count",
    "nonaccepted_authorized_count",
    "held_authorized_count",
    "blocked_authorized_count",
    "existing_successor_preserved_count",
    "omitted_no_target_count",
    "default_matrix_delta_stays_95",
    "full_cohort_input_binding",
    "raw_not_rerun",
    "no_direct_candidate_or_ms2_authority",
)
EXPECTED_MANIFEST = {
    "write_authorized_candidate_universe": {
        "source_basis": "successor_authority_decisions.write_authorized",
        "transition_count": "43",
        "cell_count": "147",
        "decision_count": "147",
        "matrix_write_allowed": "candidate_scope_only",
        "default_activation_effect": "partition_before_default_activation",
        "product_authority_effect": "no_new_authority_from_closure_checker",
    },
    "accepted_default_activation": {
        "source_basis": "expected_diff_contracts.accepted",
        "transition_count": "20",
        "cell_count": "95",
        "decision_count": "95",
        "matrix_write_allowed": "active_default_activation_scope",
        "default_activation_effect": DISCOVERY_DEFAULT_EFFECT,
        "product_authority_effect": PRODUCT_AUTHORITY_SCOPE,
    },
    "held_out_current_bundle": {
        "source_basis": "feature_inclusion_gate.hold_queues",
        "transition_count": "6",
        "cell_count": "24",
        "decision_count": "24",
        "matrix_write_allowed": "FALSE",
        "default_activation_effect": "no_default_write",
        "product_authority_effect": "no_new_authority_from_closure_checker",
    },
    "blocked_current_bundle": {
        "source_basis": "feature_inclusion_gate.blocked_queue",
        "transition_count": "17",
        "cell_count": "28",
        "decision_count": "28",
        "matrix_write_allowed": "FALSE",
        "default_activation_effect": "no_default_write",
        "product_authority_effect": "no_new_authority_from_closure_checker",
    },
    "existing_successor_context_preserved": {
        "source_basis": "successor_authority_decisions.detected_baseline",
        "transition_count": "52",
        "cell_count": "337",
        "decision_count": "337",
        "matrix_write_allowed": "FALSE",
        "default_activation_effect": "preserve_detected_baseline",
        "product_authority_effect": "no_new_authority_from_closure_checker",
    },
    "omitted_no_target_preserved": {
        "source_basis": "successor_authority_decisions.no_write_omitted",
        "transition_count": "9",
        "cell_count": "27",
        "decision_count": "27",
        "matrix_write_allowed": "FALSE",
        "default_activation_effect": "no_write_scope_removed",
        "product_authority_effect": "no_new_authority_from_closure_checker",
    },
}


def build_cid_nl_full_cohort_universe_closure(
    *,
    gate_dir: Path = DEFAULT_GATE_DIR,
    successor_summary_json: Path = DEFAULT_SUCCESSOR_AUTHORITY_SUMMARY_JSON,
    successor_decisions_tsv: Path = DEFAULT_SUCCESSOR_AUTHORITY_DECISIONS_TSV,
    successor_manifest_tsv: Path = DEFAULT_SUCCESSOR_AUTHORITY_MANIFEST_TSV,
    activation_summary_json: Path = DEFAULT_ACTIVATION_SUMMARY_JSON,
    full_scope_summary_json: Path = DEFAULT_FULL_SCOPE_SUMMARY_JSON,
    docs_dir: Path = DEFAULT_DOCS_DIR,
) -> dict[str, Any]:
    activation_summary = _read_json_object(activation_summary_json)
    full_scope_summary = _read_json_object(full_scope_summary_json)
    successor_summary = _read_json_object(successor_summary_json)
    decisions = read_tsv_required(successor_decisions_tsv, SUCCESSOR_DECISION_COLUMNS)
    successor_manifest = read_tsv_required(
        successor_manifest_tsv,
        PRODUCTION_ACCEPTANCE_COLUMNS,
    )
    gate_rows = _load_gate_rows(gate_dir)

    activation_problems = validate_cid_nl_default_product_activation(
        summary_json=activation_summary_json,
    )
    full_scope_problems = check_cid_nl_discovery_full_scope_classification()
    closure = _closure_facts(
        activation_summary=activation_summary,
        full_scope_summary=full_scope_summary,
        successor_summary=successor_summary,
        decisions=decisions,
        successor_manifest=successor_manifest,
        gate_rows=gate_rows,
        activation_problems=activation_problems,
        full_scope_problems=full_scope_problems,
    )
    checks = _check_rows(closure)
    failed = [row["check_id"] for row in checks if row["status"] != "pass"]
    if failed:
        raise ValueError(
            "CID-NL FULL COHORT universe closure failed: " + ";".join(failed)
        )

    docs_dir.mkdir(parents=True, exist_ok=True)
    checks_tsv = docs_dir / "cid_nl_full_cohort_universe_closure_checks.tsv"
    manifest_tsv = docs_dir / "cid_nl_full_cohort_universe_closure_manifest.tsv"
    summary_json = docs_dir / "cid_nl_full_cohort_universe_closure_summary.json"
    manifest_rows = _manifest_rows(closure)
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
        closure=closure,
        checks_tsv=checks_tsv,
        manifest_tsv=manifest_tsv,
        successor_summary_json=successor_summary_json,
        successor_decisions_tsv=successor_decisions_tsv,
        successor_manifest_tsv=successor_manifest_tsv,
        activation_summary_json=activation_summary_json,
        full_scope_summary_json=full_scope_summary_json,
    )
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_readme(docs_dir / "README.md", payload=payload)
    return payload


def check_cid_nl_default_activation_universe_closure(
    *,
    summary_json: Path = RETAINED_DOCS_DIR / RETAINED_SUMMARY_FILENAME,
    checks_tsv: Path = RETAINED_DOCS_DIR / RETAINED_CHECKS_FILENAME,
    manifest_tsv: Path = RETAINED_DOCS_DIR / RETAINED_MANIFEST_FILENAME,
) -> list[str]:
    problems: list[str] = []
    retained_artifact = summary_json.parent.resolve() == RETAINED_DOCS_DIR.resolve()
    expected_schema_version = (
        RETAINED_SCHEMA_VERSION if retained_artifact else SCHEMA_VERSION
    )
    expected_closure_scope = (
        RETAINED_CLOSURE_SCOPE
        if retained_artifact
        else "current_cid_nl_full_cohort_current_candidate_successor_universe"
    )
    expected_run_field = (
        "raw_or_85raw_ran" if retained_artifact else "raw_or_full_cohort_ran"
    )
    try:
        payload = _read_json_object(summary_json)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]

    expected_fields: tuple[tuple[str, object], ...] = (
        ("schema_version", expected_schema_version),
        ("status", "pass"),
        ("product_lane", PRODUCT_LANE),
        ("closure_scope", expected_closure_scope),
        ("successor_decision_count", 511),
        ("successor_authority_write_count", 147),
        ("accepted_discovery_cell_count", 95),
        ("held_candidate_cell_count", 24),
        ("blocked_candidate_cell_count", 28),
        ("nonaccepted_authorized_cell_count", 52),
        ("existing_successor_context_cell_count", 337),
        ("omitted_no_target_cell_count", 27),
        ("default_activation_cell_count", 95),
        ("default_matrix_delta_changed_cell_count", 95),
        (expected_run_field, False),
        ("product_writer_changed_by_checker", False),
        ("default_quant_matrix_changed_by_checker", False),
        ("workbook_or_gui_changed", False),
        ("selected_peak_area_or_counting_changed", False),
        ("candidate_rows_are_matrix_rows", False),
        ("cid_nl_ms2_direct_productwriter_authority", False),
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
    _check_full_cohort_summary_binding(
        payload,
        problems,
        expected_fragment=(
            RETAINED_INPUT_FRAGMENT
            if retained_artifact
            else EXPECTED_FULL_COHORT_INPUT_FRAGMENT
        ),
    )
    expected_check_ids = (
        tuple(
            "85raw_fix3_input_binding" if item == "full_cohort_input_binding" else item
            for item in EXPECTED_CHECK_IDS
        )
        if retained_artifact
        else EXPECTED_CHECK_IDS
    )
    _check_checks_tsv(checks_tsv, problems, expected_check_ids=expected_check_ids)
    _check_manifest_tsv(
        manifest_tsv,
        problems,
        expected_schema_version=expected_schema_version,
    )
    return problems


def _load_gate_rows(gate_dir: Path) -> dict[str, tuple[dict[str, str], ...]]:
    return {
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


def _closure_facts(
    *,
    activation_summary: Mapping[str, Any],
    full_scope_summary: Mapping[str, Any],
    successor_summary: Mapping[str, Any],
    decisions: Sequence[Mapping[str, str]],
    successor_manifest: Sequence[Mapping[str, str]],
    gate_rows: Mapping[str, tuple[Mapping[str, str], ...]],
    activation_problems: Sequence[str],
    full_scope_problems: Sequence[str],
) -> dict[str, Any]:
    accepted = _accepted_rows(gate_rows)
    held = gate_rows["agent_hold"] + gate_rows["manual_hold"] + gate_rows["user_review"]
    blocked = gate_rows["blocked"]
    write_decisions = _decision_rows(decisions, "write_authorized")
    detected_context = _decision_rows(decisions, "no_write_detected_baseline_preserved")
    omitted = _decision_rows(decisions, "no_write_omitted")

    write_by_transition = Counter(
        _transition_key_from_decision(row) for row in write_decisions
    )
    expected_candidate_by_transition = Counter()
    for row in accepted:
        expected_candidate_by_transition[text_value(row.get("transition_key"))] += 1
    for row in held + blocked:
        expected_candidate_by_transition[text_value(row.get("transition_key"))] += _int(
            row.get("candidate_cell_count"),
        )

    accepted_keys = {_contract_decision_key(row) for row in accepted}
    write_keys = {_decision_key(row) for row in write_decisions}
    manifest_keys = {
        (
            text_value(row.get("peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
        )
        for row in successor_manifest
    }
    write_successor_keys = {
        (
            text_value(row.get("successor_peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
        )
        for row in write_decisions
    }
    mismatched_transitions = {
        key: (write_by_transition.get(key, 0), expected_candidate_by_transition[key])
        for key in sorted(expected_candidate_by_transition)
        if write_by_transition.get(key, 0) != expected_candidate_by_transition[key]
    }
    unexpected_write_transitions = sorted(
        set(write_by_transition) - set(expected_candidate_by_transition),
    )
    missing_write_transitions = sorted(
        set(expected_candidate_by_transition) - set(write_by_transition),
    )

    held_transition_keys = _transition_keys(held)
    blocked_transition_keys = _transition_keys(blocked)
    held_authorized_count = sum(
        write_by_transition[key] for key in held_transition_keys
    )
    blocked_authorized_count = sum(
        write_by_transition[key] for key in blocked_transition_keys
    )
    successor_decision_counts = Counter(
        text_value(row.get("successor_decision")) for row in decisions
    )
    successor_summary_counts = successor_summary.get("decision_counts", {})
    matrix_delta = activation_summary.get("matrix_delta_summary", {})

    return {
        "activation_summary": activation_summary,
        "full_scope_summary": full_scope_summary,
        "successor_summary": successor_summary,
        "activation_problem_count": len(activation_problems),
        "full_scope_problem_count": len(full_scope_problems),
        "successor_decision_count": len(decisions),
        "successor_decision_counts": successor_decision_counts,
        "successor_summary_decision_counts": successor_summary_counts,
        "successor_authority_write_count": len(write_decisions),
        "successor_manifest_row_count": len(successor_manifest),
        "successor_manifest_key_mismatch_count": len(
            manifest_keys ^ write_successor_keys,
        ),
        "successor_write_flag_problem_count": _write_flag_problem_count(decisions),
        "candidate_transition_count": len(expected_candidate_by_transition),
        "write_authorized_transition_count": len(write_by_transition),
        "candidate_transition_mismatch_count": len(mismatched_transitions),
        "unexpected_write_transition_count": len(unexpected_write_transitions),
        "missing_write_transition_count": len(missing_write_transitions),
        "accepted_discovery_cell_count": len(accepted),
        "accepted_key_missing_count": len(accepted_keys - write_keys),
        "accepted_key_duplicate_count": len(accepted) - len(accepted_keys),
        "held_candidate_cell_count": _candidate_count(held),
        "blocked_candidate_cell_count": _candidate_count(blocked),
        "nonaccepted_authorized_cell_count": len(write_keys - accepted_keys),
        "held_authorized_count": held_authorized_count,
        "blocked_authorized_count": blocked_authorized_count,
        "existing_successor_context_cell_count": len(detected_context),
        "existing_successor_transition_count": len(
            {_transition_key_from_decision(row) for row in detected_context},
        ),
        "omitted_no_target_cell_count": len(omitted),
        "omitted_no_target_transition_count": len(
            {_transition_key_from_decision(row) for row in omitted},
        ),
        "default_activation_cell_count": _int(
            activation_summary.get("accepted_discovery_cell_count"),
        ),
        "default_matrix_delta_changed_cell_count": _int(
            _mapping_get(matrix_delta, "changed_cell_count"),
        ),
        "input_binding_problem_count": _input_binding_problem_count(
            activation_summary,
            successor_summary,
        ),
        "raw_or_full_cohort_ran": activation_summary.get("raw_or_full_cohort_ran")
        is True,
        "direct_authority_problem_count": _direct_authority_problem_count(
            activation_summary,
            full_scope_summary,
        ),
    }


def _check_rows(closure: Mapping[str, Any]) -> list[dict[str, Any]]:
    expected_decision_counts = {
        "no_write_detected_baseline_preserved": 337,
        "no_write_omitted": 27,
        "write_authorized": 147,
    }
    decision_counts = dict(sorted(closure["successor_decision_counts"].items()))
    return [
        _check(
            "default_activation_checker_pass",
            closure["activation_problem_count"],
            0,
            closure["activation_problem_count"] == 0,
        ),
        _check(
            "full_scope_classification_checker_pass",
            closure["full_scope_problem_count"],
            0,
            closure["full_scope_problem_count"] == 0,
        ),
        _check(
            "successor_decision_total_count",
            closure["successor_decision_count"],
            511,
            closure["successor_decision_count"] == 511,
        ),
        _check(
            "successor_decision_partition",
            json.dumps(decision_counts, sort_keys=True),
            json.dumps(expected_decision_counts, sort_keys=True),
            decision_counts == expected_decision_counts
            and closure["successor_summary_decision_counts"]
            == expected_decision_counts,
        ),
        _check(
            "successor_write_authority_flags",
            closure["successor_write_flag_problem_count"],
            0,
            closure["successor_write_flag_problem_count"] == 0,
        ),
        _check(
            "successor_manifest_keyset_matches_decisions",
            closure["successor_manifest_key_mismatch_count"],
            0,
            closure["successor_manifest_key_mismatch_count"] == 0
            and closure["successor_manifest_row_count"] == 147,
        ),
        _check(
            "candidate_transition_partition_exact",
            (
                f"mismatch={closure['candidate_transition_mismatch_count']};"
                f"missing={closure['missing_write_transition_count']};"
                f"unexpected={closure['unexpected_write_transition_count']}"
            ),
            "0",
            closure["candidate_transition_mismatch_count"] == 0
            and closure["missing_write_transition_count"] == 0
            and closure["unexpected_write_transition_count"] == 0
            and closure["candidate_transition_count"] == 43
            and closure["write_authorized_transition_count"] == 43,
        ),
        _check(
            "accepted_default_keyset_exact",
            (
                f"missing={closure['accepted_key_missing_count']};"
                f"duplicates={closure['accepted_key_duplicate_count']}"
            ),
            "0",
            closure["accepted_key_missing_count"] == 0
            and closure["accepted_key_duplicate_count"] == 0,
        ),
        _check(
            "accepted_default_activation_count",
            closure["accepted_discovery_cell_count"],
            95,
            closure["accepted_discovery_cell_count"] == 95
            and closure["default_activation_cell_count"] == 95,
        ),
        _check(
            "nonaccepted_authorized_count",
            closure["nonaccepted_authorized_cell_count"],
            52,
            closure["nonaccepted_authorized_cell_count"] == 52,
        ),
        _check(
            "held_authorized_count",
            closure["held_authorized_count"],
            24,
            closure["held_authorized_count"] == 24
            and closure["held_candidate_cell_count"] == 24,
        ),
        _check(
            "blocked_authorized_count",
            closure["blocked_authorized_count"],
            28,
            closure["blocked_authorized_count"] == 28
            and closure["blocked_candidate_cell_count"] == 28,
        ),
        _check(
            "existing_successor_preserved_count",
            closure["existing_successor_context_cell_count"],
            337,
            closure["existing_successor_context_cell_count"] == 337,
        ),
        _check(
            "omitted_no_target_count",
            closure["omitted_no_target_cell_count"],
            27,
            closure["omitted_no_target_cell_count"] == 27,
        ),
        _check(
            "default_matrix_delta_stays_95",
            closure["default_matrix_delta_changed_cell_count"],
            95,
            closure["default_matrix_delta_changed_cell_count"] == 95,
        ),
        _check(
            "full_cohort_input_binding",
            closure["input_binding_problem_count"],
            0,
            closure["input_binding_problem_count"] == 0,
        ),
        _check(
            "raw_not_rerun",
            closure["raw_or_full_cohort_ran"],
            False,
            closure["raw_or_full_cohort_ran"] is False,
        ),
        _check(
            "no_direct_candidate_or_ms2_authority",
            closure["direct_authority_problem_count"],
            0,
            closure["direct_authority_problem_count"] == 0,
        ),
    ]


def _manifest_rows(closure: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [
        _manifest_row(
            "write_authorized_candidate_universe",
            transition_count=closure["candidate_transition_count"],
            cell_count=closure["successor_authority_write_count"],
            decision_count=closure["successor_authority_write_count"],
            notes=(
                "All write-authorized successor cells are accepted, held, or blocked."
            ),
        ),
        _manifest_row(
            "accepted_default_activation",
            transition_count=20,
            cell_count=closure["accepted_discovery_cell_count"],
            decision_count=closure["accepted_discovery_cell_count"],
            notes="Only this bucket is default-active in the current product output.",
        ),
        _manifest_row(
            "held_out_current_bundle",
            transition_count=6,
            cell_count=closure["held_candidate_cell_count"],
            decision_count=closure["held_authorized_count"],
            notes="Authorized candidate cells intentionally held outside this bundle.",
        ),
        _manifest_row(
            "blocked_current_bundle",
            transition_count=17,
            cell_count=closure["blocked_candidate_cell_count"],
            decision_count=closure["blocked_authorized_count"],
            notes=(
                "Authorized candidate cells blocked by current paired-overlay review."
            ),
        ),
        _manifest_row(
            "existing_successor_context_preserved",
            transition_count=closure["existing_successor_transition_count"],
            cell_count=closure["existing_successor_context_cell_count"],
            decision_count=closure["existing_successor_context_cell_count"],
            notes=(
                "Detected successor baseline values are preserved; this can coexist "
                "with write-authorized samples in the same transition."
            ),
        ),
        _manifest_row(
            "omitted_no_target_preserved",
            transition_count=closure["omitted_no_target_transition_count"],
            cell_count=closure["omitted_no_target_cell_count"],
            decision_count=closure["omitted_no_target_cell_count"],
            notes=(
                "No safe successor target is available, so the cell remains no-write."
            ),
        ),
    ]
    return rows


def _manifest_row(
    bucket_id: str,
    *,
    transition_count: int,
    cell_count: int,
    decision_count: int,
    notes: str,
) -> dict[str, Any]:
    expected = EXPECTED_MANIFEST[bucket_id]
    return {
        "schema_version": SCHEMA_VERSION,
        "bucket_id": bucket_id,
        "source_basis": expected["source_basis"],
        "transition_count": transition_count,
        "cell_count": cell_count,
        "decision_count": decision_count,
        "matrix_write_allowed": expected["matrix_write_allowed"],
        "default_activation_effect": expected["default_activation_effect"],
        "product_authority_effect": expected["product_authority_effect"],
        "notes": notes,
    }


def _summary_payload(
    *,
    closure: Mapping[str, Any],
    checks_tsv: Path,
    manifest_tsv: Path,
    successor_summary_json: Path,
    successor_decisions_tsv: Path,
    successor_manifest_tsv: Path,
    activation_summary_json: Path,
    full_scope_summary_json: Path,
) -> dict[str, Any]:
    activation_summary = closure["activation_summary"]
    activation_inputs = activation_summary.get("input_artifacts", {})
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "validation_label": "no_raw_full_cohort_universe_closure",
        "product_lane": PRODUCT_LANE,
        "closure_scope": (
            "current_cid_nl_full_cohort_current_candidate_successor_universe"
        ),
        "strongest_evidence_tier": "FULL COHORT-derived artifact parity",
        "successor_decision_count": closure["successor_decision_count"],
        "successor_authority_write_count": closure["successor_authority_write_count"],
        "candidate_transition_count": closure["candidate_transition_count"],
        "accepted_discovery_cell_count": closure["accepted_discovery_cell_count"],
        "held_candidate_cell_count": closure["held_candidate_cell_count"],
        "blocked_candidate_cell_count": closure["blocked_candidate_cell_count"],
        "nonaccepted_authorized_cell_count": closure[
            "nonaccepted_authorized_cell_count"
        ],
        "existing_successor_context_cell_count": closure[
            "existing_successor_context_cell_count"
        ],
        "omitted_no_target_cell_count": closure["omitted_no_target_cell_count"],
        "default_activation_cell_count": closure["default_activation_cell_count"],
        "default_matrix_delta_changed_cell_count": closure[
            "default_matrix_delta_changed_cell_count"
        ],
        "raw_or_full_cohort_ran": False,
        "product_writer_changed_by_checker": False,
        "default_quant_matrix_changed_by_checker": False,
        "workbook_or_gui_changed": False,
        "selected_peak_area_or_counting_changed": False,
        "candidate_rows_are_matrix_rows": False,
        "cid_nl_ms2_direct_productwriter_authority": False,
        "release_decision": (
            "The current FULL COHORT-derived successor universe is closed for this "
            "product question: 95 default-active cells, 52 explicitly "
            "non-active authorized candidates, 337 detected-baseline preserved "
            "context cells, and 27 omitted no-target cells."
        ),
        "input_artifact_binding": {
            "expected_full_cohort_input_fragment": EXPECTED_FULL_COHORT_INPUT_FRAGMENT,
            "input_quant_matrix_tsv": _mapping_get(
                _mapping_get(activation_inputs, "input_quant_matrix_tsv"),
                "path",
            ),
            "input_matrix_identity_tsv": _mapping_get(
                _mapping_get(activation_inputs, "input_matrix_identity_tsv"),
                "path",
            ),
        },
        "artifacts": {
            "summary_json": {
                "path": (
                    "docs/superpowers/validation/"
                    "cid_nl_85raw_universe_closure_v1/"
                    "cid_nl_85raw_universe_closure_summary.json"
                ),
                "retention_decision": "keep_summary",
            },
            "checks_tsv": _artifact(checks_tsv)
            | {"retention_decision": "keep_summary"},
            "manifest_tsv": _artifact(manifest_tsv)
            | {"retention_decision": "keep_contract"},
        },
        "source_artifacts": {
            "successor_summary_json": _artifact(successor_summary_json),
            "successor_decisions_tsv": _artifact(successor_decisions_tsv),
            "successor_manifest_tsv": _artifact(successor_manifest_tsv),
            "activation_summary_json": _artifact(activation_summary_json),
            "full_scope_summary_json": _artifact(full_scope_summary_json),
            "input_quant_matrix_tsv": _artifact_from_summary(
                activation_inputs,
                "input_quant_matrix_tsv",
            ),
            "input_matrix_identity_tsv": _artifact_from_summary(
                activation_inputs,
                "input_matrix_identity_tsv",
            ),
        },
        "authority_statement": (
            "This checker adds validation evidence only. It does not change the "
            "registered CID-NL writer scope, Backfill's rule-selected membership, "
            "ProductWriter, workbook, GUI, selected peak/area, or counted "
            "detections."
        ),
    }


def _write_readme(path: Path, *, payload: Mapping[str, Any]) -> None:
    summary_relpath = _relative_or_absolute(
        path.parent / "cid_nl_full_cohort_universe_closure_summary.json",
    )
    checks_relpath = _relative_or_absolute(
        path.parent / "cid_nl_full_cohort_universe_closure_checks.tsv",
    )
    manifest_relpath = _relative_or_absolute(
        path.parent / "cid_nl_full_cohort_universe_closure_manifest.tsv",
    )
    lines = [
        "# CID-NL FULL COHORT Universe Closure v1",
        "",
        "Status: `pass`.",
        "",
        "This is a no-RAW closure gate over the current FULL COHORT-derived CID-NL "
        "successor-authority universe. It answers whether the already active "
        "95-cell default activation is the only default-output bucket in the "
        "current FULL COHORT artifact set.",
        "",
        "## Closure",
        "",
        f"- Successor decisions: `{payload['successor_decision_count']}` cells.",
        "- Write-authorized candidate universe: "
        f"`{payload['successor_authority_write_count']}` cells.",
        "- Default-active accepted cells: "
        f"`{payload['accepted_discovery_cell_count']}`.",
        "- Explicitly non-active authorized candidates: "
        f"`{payload['nonaccepted_authorized_cell_count']}` "
        "(held + blocked).",
        "- Detected-baseline context preserved: "
        f"`{payload['existing_successor_context_cell_count']}`.",
        "- Omitted no-target context preserved: "
        f"`{payload['omitted_no_target_cell_count']}`.",
        "",
        "The checker compares sample-level accepted keys, transition-level "
        "held/blocked counts, successor-authority decisions, successor authority "
        "manifest keys, default matrix delta count, and FULL COHORT current "
        "candidate input hashes.",
        "",
        "## Boundary",
        "",
        "This gate does not rerun RAW and does not create new matrix authority. "
        "It only proves the current FULL COHORT-derived artifacts are internally "
        "closed for the CID-NL Discovery product question.",
        "",
        "## Files",
        "",
        f"- Summary JSON: `{summary_relpath}`",
        f"- Checks TSV: `{checks_relpath}`",
        f"- Compact manifest: `{manifest_relpath}`",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


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


def _check_full_cohort_summary_binding(
    payload: Mapping[str, Any],
    problems: list[str],
    *,
    expected_fragment: str = EXPECTED_FULL_COHORT_INPUT_FRAGMENT,
) -> None:
    binding = payload.get("input_artifact_binding")
    if not isinstance(binding, Mapping):
        problems.append("summary input_artifact_binding missing")
        return
    for field in ("input_quant_matrix_tsv", "input_matrix_identity_tsv"):
        value = text_value(binding.get(field))
        if expected_fragment not in value:
            problems.append(
                f"summary source artifact {field} must bind to FULL COHORT "
                "current candidate"
            )


def _check_checks_tsv(
    path: Path,
    problems: list[str],
    *,
    expected_check_ids: Sequence[str] = EXPECTED_CHECK_IDS,
) -> None:
    try:
        rows = read_tsv_required(path, CHECK_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"checks_tsv: {exc}")
        return
    check_ids = [row.get("check_id", "") for row in rows]
    if len(rows) != len(expected_check_ids):
        problems.append(f"checks row count mismatch: {len(rows)}")
    missing = sorted(set(expected_check_ids) - set(check_ids))
    unexpected = sorted(set(check_ids) - set(expected_check_ids))
    duplicate_count = len(check_ids) - len(set(check_ids))
    if missing:
        problems.append("checks missing required ids: " + ";".join(missing))
    if unexpected:
        problems.append("checks unexpected ids: " + ";".join(unexpected))
    if duplicate_count:
        problems.append(f"checks duplicate id count: {duplicate_count}")
    failed = [row["check_id"] for row in rows if row.get("status") != "pass"]
    if failed:
        problems.append("failed checks: " + ";".join(failed))


def _check_manifest_tsv(
    path: Path,
    problems: list[str],
    *,
    expected_schema_version: str = SCHEMA_VERSION,
) -> None:
    try:
        rows = read_tsv_required(path, MANIFEST_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"manifest_tsv: {exc}")
        return
    if len(rows) != len(EXPECTED_MANIFEST):
        problems.append(f"manifest row count mismatch: {len(rows)}")
    rows_by_bucket = {text_value(row.get("bucket_id")): row for row in rows}
    missing = sorted(set(EXPECTED_MANIFEST) - set(rows_by_bucket))
    unexpected = sorted(set(rows_by_bucket) - set(EXPECTED_MANIFEST))
    if missing:
        problems.append("manifest missing buckets: " + ";".join(missing))
    if unexpected:
        problems.append("manifest unexpected buckets: " + ";".join(unexpected))
    for bucket_id, expected in EXPECTED_MANIFEST.items():
        row = rows_by_bucket.get(bucket_id)
        if row is None:
            continue
        for field, expected_value in expected.items():
            if text_value(row.get(field)) != expected_value:
                problems.append(f"manifest {bucket_id} {field} mismatch")
        if text_value(row.get("schema_version")) != expected_schema_version:
            problems.append(f"manifest {bucket_id} schema_version mismatch")


def _accepted_rows(
    rows: Mapping[str, tuple[Mapping[str, str], ...]],
) -> tuple[Mapping[str, str], ...]:
    return rows["primary_contract"] + rows["agent_contract"] + rows["manual_contract"]


def _decision_rows(
    rows: Sequence[Mapping[str, str]],
    decision: str,
) -> tuple[Mapping[str, str], ...]:
    return tuple(
        row for row in rows if text_value(row.get("successor_decision")) == decision
    )


def _write_flag_problem_count(rows: Sequence[Mapping[str, str]]) -> int:
    problems = 0
    for row in rows:
        decision = text_value(row.get("successor_decision"))
        write_authority = text_value(row.get("write_authority"))
        matrix_allowed = text_value(row.get("matrix_write_allowed"))
        matrix_effect = text_value(row.get("matrix_effect"))
        if decision == "write_authorized":
            if (
                write_authority != "TRUE"
                or matrix_allowed != "TRUE"
                or matrix_effect != "write_accepted_backfill"
            ):
                problems += 1
        elif write_authority != "FALSE" or matrix_allowed != "FALSE":
            problems += 1
    return problems


def _input_binding_problem_count(
    activation_summary: Mapping[str, Any],
    successor_summary: Mapping[str, Any],
) -> int:
    problems = 0
    activation_inputs = activation_summary.get("input_artifacts", {})
    successor_inputs = successor_summary.get("input_artifacts", {})
    pairs = (
        ("input_quant_matrix_tsv", "new_quant_matrix_tsv"),
        ("input_matrix_identity_tsv", "new_matrix_identity_tsv"),
    )
    for activation_key, successor_key in pairs:
        activation_artifact = _mapping_get(activation_inputs, activation_key)
        successor_artifact = _mapping_get(successor_inputs, successor_key)
        activation_path = text_value(_mapping_get(activation_artifact, "path"))
        successor_path = text_value(_mapping_get(successor_artifact, "path"))
        activation_sha = text_value(_mapping_get(activation_artifact, "sha256"))
        successor_sha = text_value(_mapping_get(successor_artifact, "sha256"))
        if EXPECTED_FULL_COHORT_INPUT_FRAGMENT not in activation_path:
            problems += 1
        if EXPECTED_FULL_COHORT_INPUT_FRAGMENT not in successor_path:
            problems += 1
        if activation_path != successor_path or activation_sha != successor_sha:
            problems += 1
    return problems


def _direct_authority_problem_count(
    activation_summary: Mapping[str, Any],
    full_scope_summary: Mapping[str, Any],
) -> int:
    problems = 0
    for payload in (activation_summary, full_scope_summary):
        for field in (
            "candidate_rows_are_matrix_rows",
            "cid_nl_ms2_direct_productwriter_authority",
            "workbook_or_gui_changed",
            "selected_peak_area_or_counting_changed",
        ):
            if payload.get(field) is True:
                problems += 1
    if activation_summary.get("backfill_writer_authority_changed") is True:
        problems += 1
    return problems


def _candidate_count(rows: Sequence[Mapping[str, str]]) -> int:
    return sum(_int(row.get("candidate_cell_count")) for row in rows)


def _transition_keys(rows: Sequence[Mapping[str, str]]) -> set[str]:
    return {
        text_value(row.get("transition_key"))
        for row in rows
        if text_value(row.get("transition_key"))
    }


def _transition_key_from_decision(row: Mapping[str, str]) -> str:
    return (
        text_value(row.get("old_peak_hypothesis_id"))
        + "->"
        + text_value(row.get("successor_peak_hypothesis_id"))
    )


def _decision_key(row: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        text_value(row.get("old_peak_hypothesis_id")),
        text_value(row.get("successor_peak_hypothesis_id")),
        text_value(row.get("sample_stem")),
    )


def _contract_decision_key(row: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        text_value(row.get("source_peak_hypothesis_id")),
        text_value(row.get("successor_peak_hypothesis_id")),
        text_value(row.get("sample_stem")),
    )


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


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "path": _relative_or_absolute(path),
        "sha256": file_sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _artifact_from_summary(payload: object, key: str) -> dict[str, Any]:
    artifact = _mapping_get(payload, key)
    if not isinstance(artifact, Mapping):
        return {"path": "", "sha256": "", "size_bytes": 0}
    return {
        "path": text_value(artifact.get("path")),
        "sha256": text_value(artifact.get("sha256")),
        "size_bytes": _int(artifact.get("size_bytes")),
    }


def _mapping_get(payload: object, key: str) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(key, "")
    return ""


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
    if isinstance(value, bool):
        return int(value)
    text = text_value(value)
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate-dir", type=Path, default=DEFAULT_GATE_DIR)
    parser.add_argument(
        "--successor-summary-json",
        type=Path,
        default=DEFAULT_SUCCESSOR_AUTHORITY_SUMMARY_JSON,
    )
    parser.add_argument(
        "--successor-decisions-tsv",
        type=Path,
        default=DEFAULT_SUCCESSOR_AUTHORITY_DECISIONS_TSV,
    )
    parser.add_argument(
        "--successor-manifest-tsv",
        type=Path,
        default=DEFAULT_SUCCESSOR_AUTHORITY_MANIFEST_TSV,
    )
    parser.add_argument(
        "--activation-summary-json",
        type=Path,
        default=DEFAULT_ACTIVATION_SUMMARY_JSON,
    )
    parser.add_argument(
        "--full-scope-summary-json",
        type=Path,
        default=DEFAULT_FULL_SCOPE_SUMMARY_JSON,
    )
    parser.add_argument("--docs-dir", type=Path)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--checks-tsv", type=Path)
    parser.add_argument("--manifest-tsv", type=Path)
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    docs_dir = args.docs_dir or (
        RETAINED_DOCS_DIR if args.check_only else DEFAULT_DOCS_DIR
    )
    if args.check_only:
        summary_json = args.summary_json or (
            docs_dir
            / (
                RETAINED_SUMMARY_FILENAME
                if docs_dir == RETAINED_DOCS_DIR
                else "cid_nl_full_cohort_universe_closure_summary.json"
            )
        )
        checks_tsv = args.checks_tsv or (
            docs_dir
            / (
                RETAINED_CHECKS_FILENAME
                if docs_dir == RETAINED_DOCS_DIR
                else "cid_nl_full_cohort_universe_closure_checks.tsv"
            )
        )
        manifest_tsv = args.manifest_tsv or (
            docs_dir
            / (
                RETAINED_MANIFEST_FILENAME
                if docs_dir == RETAINED_DOCS_DIR
                else "cid_nl_full_cohort_universe_closure_manifest.tsv"
            )
        )
        problems = check_cid_nl_default_activation_universe_closure(
            summary_json=summary_json,
            checks_tsv=checks_tsv,
            manifest_tsv=manifest_tsv,
        )
        for problem in problems:
            print(f"cid_nl_full_cohort_universe_closure_problem: {problem}")
        return 2 if problems else 0

    try:
        payload = build_cid_nl_full_cohort_universe_closure(
            gate_dir=args.gate_dir,
            successor_summary_json=args.successor_summary_json,
            successor_decisions_tsv=args.successor_decisions_tsv,
            successor_manifest_tsv=args.successor_manifest_tsv,
            activation_summary_json=args.activation_summary_json,
            full_scope_summary_json=args.full_scope_summary_json,
            docs_dir=docs_dir,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        "cid_nl_full_cohort_universe_closure_summary: "
        f"{docs_dir / 'cid_nl_full_cohort_universe_closure_summary.json'}"
    )
    print(f"cid_nl_full_cohort_universe_closure_status: {payload['status']}")
    print(
        "cid_nl_full_cohort_universe_closure_accepted_default_cells: "
        f"{payload['accepted_discovery_cell_count']}"
    )
    if args.require_pass and payload.get("status") != "pass":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
