"""Build a diagnostic CID-NL feature-inclusion gate from existing review artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.tabular_io import (
    optional_float,
    optional_int,
    read_tsv_required,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "cid_nl_feature_inclusion_gate"
DEFAULT_REVIEW_DIR = Path(
    "output/validation/cid_nl_default_activation_gallery_review",
)
DEFAULT_DIFFERENTIAL_REVIEW_TSV = (
    DEFAULT_REVIEW_DIR / "cid_nl_discovery_identity_differential_review.tsv"
)
DEFAULT_OVERLAY_SUMMARY_TSV = (
    DEFAULT_REVIEW_DIR
    / "differential_overlays"
    / "cid_nl_differential_overlay_review_summary.tsv"
)
DEFAULT_AI_ADJUDICATION_TSV = (
    DEFAULT_REVIEW_DIR
    / "differential_overlays"
    / "ai_adjudication"
    / "cid_nl_differential_ai_adjudication.tsv"
)
DEFAULT_DECISIONS_TSV = Path(
    "output/validation/cid_nl_default_activation_successor_authority_contract/"
    "successor_authority_decisions.tsv",
)
DEFAULT_MANUAL_REVIEW_TSV = (
    Path("docs/superpowers/validation/cid_nl_default_activation_gallery_review")
    / "cid_nl_manual_feature_inclusion_review.tsv"
)
DEFAULT_OUTPUT_DIR = DEFAULT_REVIEW_DIR / "feature_inclusion_gate"

DIFFERENTIAL_REQUIRED_COLUMNS = (
    "source_peak_hypothesis_id",
    "successor_peak_hypothesis_id",
    "transition_key",
    "sample_count",
    "write_authorized_count",
    "no_write_detected_baseline_preserved_count",
    "no_write_omitted_count",
    "source_mz",
    "source_rt",
    "source_product_mz",
    "source_neutral_loss_tag",
    "source_identity_decision",
    "source_accepted_cell_count",
    "successor_mz",
    "successor_rt",
    "successor_product_mz",
    "successor_neutral_loss_tag",
    "successor_identity_decision",
    "successor_accepted_cell_count",
    "feature_inclusion_gate",
    "identity_authority_gate",
    "source_successor_relationship",
    "transition_type",
    "differential_overlay_readiness",
)
OVERLAY_REQUIRED_COLUMNS = (
    "transition_key",
    "status",
    "review_path",
    "trace_data_json",
    "source_trace_max_median",
    "successor_trace_max_median",
    "successor_to_source_median_max_ratio",
    "source_nonzero_fraction",
    "successor_nonzero_fraction",
)
AI_REQUIRED_COLUMNS = (
    "transition_key",
    "ai_review_decision",
    "ai_confidence",
    "human_review_needed",
    "ai_reason",
    "product_authority_effect",
    "guardrail_flag",
    "trace_sample_count",
    "successor_only_count",
    "successor_dominant_count",
    "source_only_count",
    "source_dominant_count",
    "close_count",
    "none_count",
    "successor_support_fraction",
    "source_support_fraction",
    "status",
    # Legacy adjudication input retains this field; review queues use the
    # bundle locator from OVERLAY_REQUIRED_COLUMNS instead.
    "png_path",
    "trace_data_json",
)
DECISION_REQUIRED_COLUMNS = (
    "old_peak_hypothesis_id",
    "sample_stem",
    "successor_peak_hypothesis_id",
    "successor_decision",
    "write_authority",
    "matrix_write_allowed",
    "matrix_effect",
    "human_explanation",
    "input_resolution_status",
    "candidate_new_peak_hypothesis_ids",
    "candidate_baseline_values",
    "accepted_quant_value",
)
MANUAL_REVIEW_SCHEMA_VERSION = "cid_nl_manual_feature_inclusion_review"
MANUAL_REVIEW_REQUIRED_COLUMNS = (
    "schema_version",
    "transition_key",
    "source_peak_hypothesis_id",
    "successor_peak_hypothesis_id",
    "manual_review_decision",
    "manual_review_basis",
    "manual_feature_inclusion_scope",
    "source_peak_review_state",
    "successor_peak_review_state",
    "identity_authority_note",
    "product_authority_effect",
)

GATE_TRANSITION_COLUMNS = (
    "schema_version",
    "transition_key",
    "source_peak_hypothesis_id",
    "successor_peak_hypothesis_id",
    "candidate_cell_count",
    "existing_successor_context_cell_count",
    "omitted_no_target_cell_count",
    "sample_count",
    "feature_inclusion_review_status",
    "identity_authority_status",
    "product_gate_action",
    "product_authority_effect",
    "ai_review_decision",
    "ai_confidence",
    "guardrail_flag",
    "successor_support_fraction",
    "source_support_fraction",
    "successor_to_source_median_max_ratio",
    "source_successor_relationship",
    "feature_inclusion_gate",
    "identity_authority_gate",
    "differential_overlay_readiness",
    "review_reason",
    "review_path",
    "trace_data_json",
)
EXPECTED_DIFF_COLUMNS = (
    "schema_version",
    "expected_diff_contract_status",
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
    "guardrail_flag",
    "trace_data_json",
)
REVIEW_RESOLUTION_COLUMNS = (
    "schema_version",
    "transition_key",
    "source_peak_hypothesis_id",
    "successor_peak_hypothesis_id",
    "candidate_cell_count",
    "review_resolution_action",
    "review_resolution_status",
    "review_resolution_reason",
    "successor_support_fraction",
    "source_support_fraction",
    "successor_to_source_median_max_ratio",
    "ai_review_decision",
    "ai_confidence",
    "guardrail_flag",
    "manual_review_decision",
    "manual_review_basis",
    "manual_feature_inclusion_scope",
    "source_peak_review_state",
    "successor_peak_review_state",
    "identity_authority_note",
    "product_authority_effect",
    "review_path",
    "trace_data_json",
)
SUMMARY_COLUMNS = (
    "schema_version",
    "validation_label",
    "overall_status",
    "review_frame",
    "transition_count",
    "overlay_ready_transition_count",
    "ai_adjudicated_transition_count",
    "candidate_cell_count",
    "supported_candidate_cell_count",
    "review_candidate_cell_count",
    "blocked_candidate_cell_count",
    "expected_diff_cell_count",
    "agent_resolved_expected_diff_cell_count",
    "agent_resolved_hold_cell_count",
    "manual_resolved_expected_diff_cell_count",
    "manual_resolved_hold_cell_count",
    "user_review_cell_count",
    "agent_resolved_expected_diff_contract_cell_count",
    "manual_resolved_expected_diff_contract_cell_count",
    "existing_successor_context_cell_count",
    "omitted_no_target_cell_count",
    "expected_diff_transition_count",
    "agent_resolved_expected_diff_transition_count",
    "agent_resolved_hold_transition_count",
    "manual_resolved_expected_diff_transition_count",
    "manual_resolved_hold_transition_count",
    "user_review_transition_count",
    "review_transition_count",
    "blocked_transition_count",
    "product_writer_changed",
    "default_quant_matrix_changed",
    "candidate_rows_are_matrix_rows",
    "next_product_gate",
    "authority_statement",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        payload = build_feature_inclusion_gate(
            differential_review_tsv=args.differential_review_tsv,
            overlay_summary_tsv=args.overlay_summary_tsv,
            ai_adjudication_tsv=args.ai_adjudication_tsv,
            decisions_tsv=args.decisions_tsv,
            manual_review_tsv=args.manual_review_tsv,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"CID-NL feature inclusion gate: {payload['summary_tsv']}")
    print(f"CID-NL identity expected-diff queue: {payload['expected_diff_queue_tsv']}")
    print(
        "CID-NL candidate cells: "
        f"{payload['candidate_cell_count']} "
        f"(supported {payload['supported_candidate_cell_count']}, "
        f"review {payload['review_candidate_cell_count']}, "
        f"blocked {payload['blocked_candidate_cell_count']})",
    )
    if args.require_pass and payload["overall_status"] != "pass":
        return 2
    return 0


def build_feature_inclusion_gate(
    *,
    differential_review_tsv: Path,
    overlay_summary_tsv: Path,
    ai_adjudication_tsv: Path,
    decisions_tsv: Path,
    manual_review_tsv: Path | None = None,
    output_dir: Path,
) -> dict[str, Any]:
    differential_rows = read_tsv_required(
        differential_review_tsv,
        DIFFERENTIAL_REQUIRED_COLUMNS,
    )
    overlay_by_key = _unique_by_key(
        read_tsv_required(overlay_summary_tsv, OVERLAY_REQUIRED_COLUMNS),
        "transition_key",
        overlay_summary_tsv,
    )
    ai_by_key = _unique_by_key(
        read_tsv_required(ai_adjudication_tsv, AI_REQUIRED_COLUMNS),
        "transition_key",
        ai_adjudication_tsv,
    )
    decisions = read_tsv_required(decisions_tsv, DECISION_REQUIRED_COLUMNS)
    manual_by_key = _manual_review_by_key(
        manual_review_tsv,
        transition_keys={row["transition_key"] for row in differential_rows},
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    transition_rows = [
        _gate_row(
            row,
            overlay_by_key.get(row["transition_key"], {}),
            ai_by_key.get(row["transition_key"], {}),
        )
        for row in differential_rows
    ]
    expected_diff_rows = [
        row
        for row in transition_rows
        if row["product_gate_action"] == "queue_expected_diff_design"
    ]
    review_rows = [
        row
        for row in transition_rows
        if row["product_gate_action"] == "queue_review_before_expected_diff"
    ]
    blocked_rows = [
        row
        for row in transition_rows
        if row["product_gate_action"] == "exclude_from_current_activation_bundle"
    ]
    expected_diff_contract_rows = _expected_diff_contract_rows(
        supported_transition_rows=expected_diff_rows,
        differential_rows=differential_rows,
        decisions=decisions,
    )
    review_resolution_rows = _review_resolution_rows(review_rows, manual_by_key)
    agent_expected_diff_rows = [
        row
        for row in review_resolution_rows
        if row["review_resolution_action"] == "agent_queue_expected_diff_design"
    ]
    agent_hold_rows = [
        row
        for row in review_resolution_rows
        if row["review_resolution_action"] == "agent_hold_current_bundle"
    ]
    manual_expected_diff_rows = [
        row
        for row in review_resolution_rows
        if row["review_resolution_action"] == "manual_queue_expected_diff_design"
    ]
    manual_hold_rows = [
        row
        for row in review_resolution_rows
        if row["review_resolution_action"] == "manual_hold_current_bundle"
    ]
    user_review_rows = [
        row
        for row in review_resolution_rows
        if row["review_resolution_action"] == "user_review_required"
    ]
    agent_expected_diff_contract_rows = _expected_diff_contract_rows(
        supported_transition_rows=agent_expected_diff_rows,
        differential_rows=differential_rows,
        decisions=decisions,
    )
    manual_expected_diff_contract_rows = _expected_diff_contract_rows(
        supported_transition_rows=manual_expected_diff_rows,
        differential_rows=differential_rows,
        decisions=decisions,
    )

    transition_tsv = output_dir / "cid_nl_feature_inclusion_gate_transitions.tsv"
    summary_tsv = output_dir / "cid_nl_feature_inclusion_gate_summary.tsv"
    summary_json = output_dir / "cid_nl_feature_inclusion_gate_summary.json"
    expected_diff_tsv = output_dir / "cid_nl_identity_expected_diff_queue.tsv"
    expected_diff_contract_tsv = (
        output_dir / "cid_nl_supported_candidate_expected_diff_contract.tsv"
    )
    review_queue_tsv = output_dir / "cid_nl_feature_inclusion_review_queue.tsv"
    blocked_queue_tsv = output_dir / "cid_nl_feature_inclusion_blocked_queue.tsv"
    review_resolution_tsv = output_dir / "cid_nl_review_resolution.tsv"
    agent_expected_diff_tsv = (
        output_dir / "cid_nl_agent_resolved_expected_diff_queue.tsv"
    )
    agent_expected_diff_contract_tsv = (
        output_dir / "cid_nl_agent_resolved_expected_diff_contract.tsv"
    )
    agent_hold_tsv = output_dir / "cid_nl_agent_resolved_hold_queue.tsv"
    manual_expected_diff_tsv = (
        output_dir / "cid_nl_manual_resolved_expected_diff_queue.tsv"
    )
    manual_expected_diff_contract_tsv = (
        output_dir / "cid_nl_manual_resolved_expected_diff_contract.tsv"
    )
    manual_hold_tsv = output_dir / "cid_nl_manual_resolved_hold_queue.tsv"
    user_review_tsv = output_dir / "cid_nl_user_review_queue.tsv"

    summary_payload = _summary_payload(
        transition_rows,
        ai_adjudicated_transition_count=len(ai_by_key),
        expected_diff_cell_count=len(expected_diff_contract_rows),
        agent_resolved_expected_diff_cell_count=sum(
            int(row["candidate_cell_count"]) for row in agent_expected_diff_rows
        ),
        agent_resolved_hold_cell_count=sum(
            int(row["candidate_cell_count"]) for row in agent_hold_rows
        ),
        manual_resolved_expected_diff_cell_count=sum(
            int(row["candidate_cell_count"]) for row in manual_expected_diff_rows
        ),
        manual_resolved_hold_cell_count=sum(
            int(row["candidate_cell_count"]) for row in manual_hold_rows
        ),
        user_review_cell_count=sum(
            int(row["candidate_cell_count"]) for row in user_review_rows
        ),
        agent_resolved_expected_diff_contract_cell_count=len(
            agent_expected_diff_contract_rows
        ),
        manual_resolved_expected_diff_contract_cell_count=len(
            manual_expected_diff_contract_rows
        ),
        agent_resolved_expected_diff_transition_count=len(agent_expected_diff_rows),
        agent_resolved_hold_transition_count=len(agent_hold_rows),
        manual_resolved_expected_diff_transition_count=len(manual_expected_diff_rows),
        manual_resolved_hold_transition_count=len(manual_hold_rows),
        user_review_transition_count=len(user_review_rows),
    )
    write_tsv(transition_tsv, transition_rows, GATE_TRANSITION_COLUMNS)
    write_tsv(summary_tsv, [summary_payload], SUMMARY_COLUMNS)
    write_tsv(expected_diff_tsv, expected_diff_rows, GATE_TRANSITION_COLUMNS)
    write_tsv(
        expected_diff_contract_tsv,
        expected_diff_contract_rows,
        EXPECTED_DIFF_COLUMNS,
    )
    write_tsv(review_queue_tsv, review_rows, GATE_TRANSITION_COLUMNS)
    write_tsv(blocked_queue_tsv, blocked_rows, GATE_TRANSITION_COLUMNS)
    write_tsv(review_resolution_tsv, review_resolution_rows, REVIEW_RESOLUTION_COLUMNS)
    write_tsv(
        agent_expected_diff_tsv,
        agent_expected_diff_rows,
        REVIEW_RESOLUTION_COLUMNS,
    )
    write_tsv(
        agent_expected_diff_contract_tsv,
        agent_expected_diff_contract_rows,
        EXPECTED_DIFF_COLUMNS,
    )
    write_tsv(agent_hold_tsv, agent_hold_rows, REVIEW_RESOLUTION_COLUMNS)
    write_tsv(
        manual_expected_diff_tsv,
        manual_expected_diff_rows,
        REVIEW_RESOLUTION_COLUMNS,
    )
    write_tsv(
        manual_expected_diff_contract_tsv,
        manual_expected_diff_contract_rows,
        EXPECTED_DIFF_COLUMNS,
    )
    write_tsv(manual_hold_tsv, manual_hold_rows, REVIEW_RESOLUTION_COLUMNS)
    write_tsv(user_review_tsv, user_review_rows, REVIEW_RESOLUTION_COLUMNS)
    summary_json.write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **summary_payload,
        "transition_tsv": str(transition_tsv),
        "summary_tsv": str(summary_tsv),
        "summary_json": str(summary_json),
        "expected_diff_queue_tsv": str(expected_diff_tsv),
        "expected_diff_contract_tsv": str(expected_diff_contract_tsv),
        "review_queue_tsv": str(review_queue_tsv),
        "blocked_queue_tsv": str(blocked_queue_tsv),
        "review_resolution_tsv": str(review_resolution_tsv),
        "agent_resolved_expected_diff_queue_tsv": str(agent_expected_diff_tsv),
        "agent_resolved_expected_diff_contract_tsv": str(
            agent_expected_diff_contract_tsv
        ),
        "agent_resolved_hold_queue_tsv": str(agent_hold_tsv),
        "manual_resolved_expected_diff_queue_tsv": str(manual_expected_diff_tsv),
        "manual_resolved_expected_diff_contract_tsv": str(
            manual_expected_diff_contract_tsv
        ),
        "manual_resolved_hold_queue_tsv": str(manual_hold_tsv),
        "user_review_queue_tsv": str(user_review_tsv),
    }


def _gate_row(
    differential_row: Mapping[str, str],
    overlay_row: Mapping[str, str],
    ai_row: Mapping[str, str],
) -> dict[str, Any]:
    candidate_cells = _int(differential_row, "write_authorized_count")
    existing_cells = _int(
        differential_row,
        "no_write_detected_baseline_preserved_count",
    )
    omitted_cells = _int(differential_row, "no_write_omitted_count")
    ai_decision = text_value(ai_row.get("ai_review_decision"))
    guardrail = text_value(ai_row.get("guardrail_flag"))
    feature_status, identity_status, action, reason = _classify_gate(
        candidate_cells=candidate_cells,
        existing_cells=existing_cells,
        omitted_cells=omitted_cells,
        ai_decision=ai_decision,
        guardrail_flag=guardrail,
        ai_status=text_value(ai_row.get("status")),
        overlay_status=text_value(overlay_row.get("status")),
        readiness=text_value(differential_row.get("differential_overlay_readiness")),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "transition_key": differential_row["transition_key"],
        "source_peak_hypothesis_id": differential_row["source_peak_hypothesis_id"],
        "successor_peak_hypothesis_id": differential_row[
            "successor_peak_hypothesis_id"
        ],
        "candidate_cell_count": candidate_cells,
        "existing_successor_context_cell_count": existing_cells,
        "omitted_no_target_cell_count": omitted_cells,
        "sample_count": _int(differential_row, "sample_count"),
        "feature_inclusion_review_status": feature_status,
        "identity_authority_status": identity_status,
        "product_gate_action": action,
        "product_authority_effect": "diagnostic_only_no_authority_change",
        "ai_review_decision": ai_decision,
        "ai_confidence": text_value(ai_row.get("ai_confidence")),
        "guardrail_flag": guardrail,
        "successor_support_fraction": _number_text(
            ai_row.get("successor_support_fraction")
            or overlay_row.get("successor_nonzero_fraction")
        ),
        "source_support_fraction": _number_text(
            ai_row.get("source_support_fraction")
            or overlay_row.get("source_nonzero_fraction")
        ),
        "successor_to_source_median_max_ratio": _number_text(
            overlay_row.get("successor_to_source_median_max_ratio")
        ),
        "source_successor_relationship": differential_row[
            "source_successor_relationship"
        ],
        "feature_inclusion_gate": differential_row["feature_inclusion_gate"],
        "identity_authority_gate": differential_row["identity_authority_gate"],
        "differential_overlay_readiness": differential_row[
            "differential_overlay_readiness"
        ],
        "review_reason": reason,
        "review_path": text_value(overlay_row.get("review_path")),
        "trace_data_json": text_value(
            ai_row.get("trace_data_json") or overlay_row.get("trace_data_json")
        ),
    }


def _classify_gate(
    *,
    candidate_cells: int,
    existing_cells: int,
    omitted_cells: int,
    ai_decision: str,
    guardrail_flag: str,
    ai_status: str,
    overlay_status: str,
    readiness: str,
) -> tuple[str, str, str, str]:
    if candidate_cells <= 0:
        if omitted_cells > 0 and existing_cells <= 0:
            return (
                "omitted_no_successor_target",
                "no_identity_authority_change_requested",
                "retain_omitted_no_target",
                "No successor target exists in the current diagnostic packet.",
            )
        return (
            "existing_successor_context_only",
            "no_identity_authority_change_requested",
            "retain_existing_context_no_activation",
            "No candidate feature-inclusion cells are requested for this transition.",
        )
    if readiness != "ready_for_paired_overlay":
        return (
            "candidate_feature_inclusion_not_assessable",
            "identity_review_required",
            "queue_review_before_expected_diff",
            "Candidate cells exist, but this transition is not ready for "
            "paired overlay review.",
        )
    if (
        not ai_decision
        or ai_status not in {"", "success"}
        or overlay_status not in {"", "success"}
    ):
        return (
            "candidate_feature_inclusion_not_assessable",
            "identity_review_required",
            "queue_review_before_expected_diff",
            "Candidate cells exist, but current overlay/AI evidence is "
            "missing or failed.",
        )
    if guardrail_flag:
        return (
            "candidate_feature_inclusion_guardrail_review_required",
            "identity_guardrail_review_required",
            "queue_review_before_expected_diff",
            "Target guardrail context requires explicit review before "
            "expected-diff design.",
        )
    if ai_decision == "accept_successor_identity_clear":
        return (
            "candidate_feature_inclusion_supported_by_current_overlay",
            "expected_diff_required_before_identity_authority",
            "queue_expected_diff_design",
            "Successor MS1 support is clear enough to design an "
            "expected-diff contract.",
        )
    if ai_decision == "human_review_needed":
        return (
            "candidate_feature_inclusion_review_required",
            "identity_review_required",
            "queue_review_before_expected_diff",
            "Source/successor evidence is mixed, close, or otherwise ambiguous.",
        )
    if ai_decision == "reject_successor_identity_clear":
        return (
            "candidate_feature_inclusion_not_supported_by_current_overlay",
            "identity_authority_blocked_by_current_overlay",
            "exclude_from_current_activation_bundle",
            "Current paired overlay does not support carrying this successor "
            "into the activation bundle.",
        )
    return (
        "candidate_feature_inclusion_not_assessable",
        "identity_review_required",
        "queue_review_before_expected_diff",
        f"Unrecognized AI review decision: {ai_decision}",
    )


def _summary_payload(
    rows: Sequence[Mapping[str, Any]],
    *,
    ai_adjudicated_transition_count: int,
    expected_diff_cell_count: int,
    agent_resolved_expected_diff_cell_count: int,
    agent_resolved_hold_cell_count: int,
    manual_resolved_expected_diff_cell_count: int,
    manual_resolved_hold_cell_count: int,
    user_review_cell_count: int,
    agent_resolved_expected_diff_contract_cell_count: int,
    manual_resolved_expected_diff_contract_cell_count: int,
    agent_resolved_expected_diff_transition_count: int,
    agent_resolved_hold_transition_count: int,
    manual_resolved_expected_diff_transition_count: int,
    manual_resolved_hold_transition_count: int,
    user_review_transition_count: int,
) -> dict[str, Any]:
    status_counts = Counter(
        text_value(row["feature_inclusion_review_status"]) for row in rows
    )
    action_counts = Counter(text_value(row["product_gate_action"]) for row in rows)
    candidate_cells = sum(int(row["candidate_cell_count"]) for row in rows)
    supported_cells = sum(
        int(row["candidate_cell_count"])
        for row in rows
        if row["product_gate_action"] == "queue_expected_diff_design"
    )
    review_cells = sum(
        int(row["candidate_cell_count"])
        for row in rows
        if row["product_gate_action"] == "queue_review_before_expected_diff"
    )
    blocked_cells = sum(
        int(row["candidate_cell_count"])
        for row in rows
        if row["product_gate_action"] == "exclude_from_current_activation_bundle"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_label": "diagnostic_only",
        "overall_status": "pass",
        "review_frame": "feature_inclusion_then_identity_authority",
        "transition_count": len(rows),
        "overlay_ready_transition_count": sum(
            1
            for row in rows
            if row["differential_overlay_readiness"] == "ready_for_paired_overlay"
        ),
        "ai_adjudicated_transition_count": ai_adjudicated_transition_count,
        "candidate_cell_count": candidate_cells,
        "supported_candidate_cell_count": supported_cells,
        "review_candidate_cell_count": review_cells,
        "blocked_candidate_cell_count": blocked_cells,
        "expected_diff_cell_count": expected_diff_cell_count,
        "agent_resolved_expected_diff_cell_count": (
            agent_resolved_expected_diff_cell_count
        ),
        "agent_resolved_hold_cell_count": agent_resolved_hold_cell_count,
        "manual_resolved_expected_diff_cell_count": (
            manual_resolved_expected_diff_cell_count
        ),
        "manual_resolved_hold_cell_count": manual_resolved_hold_cell_count,
        "user_review_cell_count": user_review_cell_count,
        "agent_resolved_expected_diff_contract_cell_count": (
            agent_resolved_expected_diff_contract_cell_count
        ),
        "manual_resolved_expected_diff_contract_cell_count": (
            manual_resolved_expected_diff_contract_cell_count
        ),
        "existing_successor_context_cell_count": sum(
            int(row["existing_successor_context_cell_count"]) for row in rows
        ),
        "omitted_no_target_cell_count": sum(
            int(row["omitted_no_target_cell_count"]) for row in rows
        ),
        "expected_diff_transition_count": action_counts["queue_expected_diff_design"],
        "agent_resolved_expected_diff_transition_count": (
            agent_resolved_expected_diff_transition_count
        ),
        "agent_resolved_hold_transition_count": (
            agent_resolved_hold_transition_count
        ),
        "manual_resolved_expected_diff_transition_count": (
            manual_resolved_expected_diff_transition_count
        ),
        "manual_resolved_hold_transition_count": manual_resolved_hold_transition_count,
        "user_review_transition_count": user_review_transition_count,
        "review_transition_count": action_counts["queue_review_before_expected_diff"],
        "blocked_transition_count": action_counts[
            "exclude_from_current_activation_bundle"
        ],
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "candidate_rows_are_matrix_rows": False,
        "next_product_gate": (
            "validate activated-copy candidates for supported, agent-resolved, "
            "and manual-resolved expected-diff rows; keep user-review and "
            "blocked queues out of the current activation bundle"
        ),
        "authority_statement": (
            "CID-NL/MS2 plus MS1 support can justify feature-inclusion review, "
            "but identity replacement and ProductWriter authority still require "
            "a separate expected-diff activation contract."
        ),
        "feature_inclusion_status_counts": dict(sorted(status_counts.items())),
        "product_gate_action_counts": dict(sorted(action_counts.items())),
    }


def _review_resolution_rows(
    review_rows: Sequence[Mapping[str, Any]],
    manual_by_key: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    return [_review_resolution_row(row, manual_by_key) for row in review_rows]


def _review_resolution_row(
    row: Mapping[str, Any],
    manual_by_key: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    successor_support = optional_float(row.get("successor_support_fraction")) or 0.0
    source_support = optional_float(row.get("source_support_fraction")) or 0.0
    guardrail = text_value(row.get("guardrail_flag"))
    manual_row = manual_by_key.get(text_value(row["transition_key"]), {})
    if manual_row:
        action, status, reason = _classify_manual_review_resolution(manual_row)
    else:
        action, status, reason = _classify_review_resolution(
            successor_support=successor_support,
            source_support=source_support,
            guardrail_flag=guardrail,
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "transition_key": row["transition_key"],
        "source_peak_hypothesis_id": row["source_peak_hypothesis_id"],
        "successor_peak_hypothesis_id": row["successor_peak_hypothesis_id"],
        "candidate_cell_count": row["candidate_cell_count"],
        "review_resolution_action": action,
        "review_resolution_status": status,
        "review_resolution_reason": reason,
        "successor_support_fraction": row["successor_support_fraction"],
        "source_support_fraction": row["source_support_fraction"],
        "successor_to_source_median_max_ratio": row[
            "successor_to_source_median_max_ratio"
        ],
        "ai_review_decision": row["ai_review_decision"],
        "ai_confidence": row["ai_confidence"],
        "guardrail_flag": guardrail,
        "manual_review_decision": text_value(
            manual_row.get("manual_review_decision")
        ),
        "manual_review_basis": text_value(manual_row.get("manual_review_basis")),
        "manual_feature_inclusion_scope": text_value(
            manual_row.get("manual_feature_inclusion_scope")
        ),
        "source_peak_review_state": text_value(
            manual_row.get("source_peak_review_state")
        ),
        "successor_peak_review_state": text_value(
            manual_row.get("successor_peak_review_state")
        ),
        "identity_authority_note": text_value(
            manual_row.get("identity_authority_note")
        ),
        "product_authority_effect": "diagnostic_only_no_authority_change",
        "review_path": row["review_path"],
        "trace_data_json": row["trace_data_json"],
        "feature_inclusion_review_status": row["feature_inclusion_review_status"],
        "identity_authority_status": row["identity_authority_status"],
    }


def _classify_review_resolution(
    *,
    successor_support: float,
    source_support: float,
    guardrail_flag: str,
) -> tuple[str, str, str]:
    if guardrail_flag:
        return (
            "user_review_required",
            "guardrail_review_required",
            "Target guardrail context requires explicit human/domain review.",
        )
    if successor_support >= 0.55 and source_support <= 0.2:
        return (
            "agent_queue_expected_diff_design",
            "agent_resolved_successor_supported",
            "Successor support is materially stronger than source support.",
        )
    if successor_support <= 0.25 and source_support >= 0.4:
        return (
            "agent_hold_current_bundle",
            "agent_resolved_source_supported",
            "Source support is materially stronger than successor support.",
        )
    if successor_support == 0 and source_support == 0:
        return (
            "agent_hold_current_bundle",
            "agent_resolved_no_ms1_support",
            "Neither source nor successor has enough current MS1 support.",
        )
    return (
        "user_review_required",
        "ambiguous_review_required",
        "Source/successor support is close, mixed, or below automatic thresholds.",
    )


def _classify_manual_review_resolution(
    row: Mapping[str, str],
) -> tuple[str, str, str]:
    decision = text_value(row.get("manual_review_decision"))
    basis = text_value(row.get("manual_review_basis"))
    if decision == "support_successor_feature_inclusion":
        return (
            "manual_queue_expected_diff_design",
            "manual_review_successor_supported",
            basis or "Manual review supports successor MS1 feature inclusion.",
        )
    if decision in {
        "hold_current_bundle",
        "reject_successor_feature_inclusion",
    }:
        return (
            "manual_hold_current_bundle",
            "manual_review_hold_current_bundle",
            basis or "Manual review holds this transition out of the bundle.",
        )
    if decision == "keep_user_review":
        return (
            "user_review_required",
            "manual_review_inconclusive",
            basis or "Manual review remains inconclusive.",
        )
    raise ValueError(f"unrecognized manual_review_decision: {decision}")


def _expected_diff_contract_rows(
    *,
    supported_transition_rows: Sequence[Mapping[str, Any]],
    differential_rows: Sequence[Mapping[str, str]],
    decisions: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    supported_by_key = {
        text_value(row["transition_key"]): row for row in supported_transition_rows
    }
    differential_by_key = {
        text_value(row["transition_key"]): row for row in differential_rows
    }
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        if text_value(decision.get("successor_decision")) != "write_authorized":
            continue
        transition_key = (
            f"{text_value(decision.get('old_peak_hypothesis_id'))}"
            f"->{text_value(decision.get('successor_peak_hypothesis_id'))}"
        )
        gate_row = supported_by_key.get(transition_key)
        if not gate_row:
            continue
        differential_row = differential_by_key[transition_key]
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "expected_diff_contract_status": "expected_diff_design_candidate",
                "transition_key": transition_key,
                "sample_stem": text_value(decision.get("sample_stem")),
                "source_peak_hypothesis_id": differential_row[
                    "source_peak_hypothesis_id"
                ],
                "successor_peak_hypothesis_id": differential_row[
                    "successor_peak_hypothesis_id"
                ],
                "source_mz": differential_row["source_mz"],
                "source_rt": differential_row["source_rt"],
                "source_product_mz": differential_row["source_product_mz"],
                "source_neutral_loss_tag": differential_row[
                    "source_neutral_loss_tag"
                ],
                "source_identity_decision": differential_row[
                    "source_identity_decision"
                ],
                "successor_mz": differential_row["successor_mz"],
                "successor_rt": differential_row["successor_rt"],
                "successor_product_mz": differential_row["successor_product_mz"],
                "successor_neutral_loss_tag": differential_row[
                    "successor_neutral_loss_tag"
                ],
                "successor_identity_decision": differential_row[
                    "successor_identity_decision"
                ],
                "candidate_quant_value": text_value(
                    decision.get("accepted_quant_value")
                ),
                "legacy_successor_matrix_effect": text_value(
                    decision.get("matrix_effect")
                ),
                "legacy_successor_write_authority": text_value(
                    decision.get("write_authority")
                ),
                "legacy_successor_matrix_write_allowed": text_value(
                    decision.get("matrix_write_allowed")
                ),
                "legacy_input_resolution_status": text_value(
                    decision.get("input_resolution_status")
                ),
                "feature_inclusion_review_status": gate_row[
                    "feature_inclusion_review_status"
                ],
                "identity_authority_status": gate_row["identity_authority_status"],
                "authority_gate": (
                    "candidate_only_expected_diff_required_no_product_write"
                ),
                "product_authority_effect": "diagnostic_only_no_authority_change",
                "expected_product_effect": (
                    "candidate_cell_expected_diff_design_only"
                ),
                "guardrail_flag": gate_row["guardrail_flag"],
                "trace_data_json": gate_row["trace_data_json"],
            }
        )
    supported_cell_count = sum(
        int(row["candidate_cell_count"]) for row in supported_transition_rows
    )
    if len(rows) != supported_cell_count:
        raise ValueError(
            "expected-diff contract row count does not match supported "
            f"candidate cells: {len(rows)} != {supported_cell_count}",
        )
    return rows


def _manual_review_by_key(
    path: Path | None,
    *,
    transition_keys: set[str],
) -> dict[str, Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, MANUAL_REVIEW_REQUIRED_COLUMNS)
    result: dict[str, Mapping[str, str]] = {}
    for row in rows:
        if text_value(row.get("schema_version")) != MANUAL_REVIEW_SCHEMA_VERSION:
            raise ValueError(
                f"{path}: unsupported manual review schema version: "
                f"{text_value(row.get('schema_version'))}"
            )
        if text_value(row.get("product_authority_effect")) != (
            "diagnostic_only_no_authority_change"
        ):
            raise ValueError(
                f"{path}: manual review cannot grant product authority: "
                f"{text_value(row.get('transition_key'))}"
            )
        transition_key = text_value(row.get("transition_key"))
        if transition_key not in transition_keys:
            raise ValueError(
                f"{path}: stale manual review transition: {transition_key}"
            )
        if transition_key in result:
            raise ValueError(
                f"{path}: duplicate manual review transition: {transition_key}"
            )
        result[transition_key] = row
    return result


def _unique_by_key(
    rows: Sequence[Mapping[str, str]],
    key: str,
    path: Path,
) -> dict[str, Mapping[str, str]]:
    result: dict[str, Mapping[str, str]] = {}
    for row in rows:
        value = text_value(row.get(key))
        if not value:
            raise ValueError(f"{path}: empty {key}")
        if value in result:
            raise ValueError(f"{path}: duplicate {key}: {value}")
        result[value] = row
    return result


def _int(row: Mapping[str, str], key: str) -> int:
    return optional_int(row.get(key)) or 0


def _number_text(value: object) -> str:
    parsed = optional_float(value)
    if parsed is None:
        return ""
    return f"{parsed:.6g}"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a diagnostic CID-NL feature-inclusion gate from existing "
            "differential review, overlay summary, and AI adjudication artifacts."
        ),
    )
    parser.add_argument(
        "--differential-review-tsv",
        type=Path,
        default=DEFAULT_DIFFERENTIAL_REVIEW_TSV,
    )
    parser.add_argument(
        "--overlay-summary-tsv",
        type=Path,
        default=DEFAULT_OVERLAY_SUMMARY_TSV,
    )
    parser.add_argument(
        "--ai-adjudication-tsv",
        type=Path,
        default=DEFAULT_AI_ADJUDICATION_TSV,
    )
    parser.add_argument("--decisions-tsv", type=Path, default=DEFAULT_DECISIONS_TSV)
    parser.add_argument("--manual-review-tsv", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
