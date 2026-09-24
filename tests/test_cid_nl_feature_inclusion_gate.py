from __future__ import annotations

import json
from pathlib import Path

from tools.diagnostics import cid_nl_feature_inclusion_gate as gate
from xic_extractor.tabular_io import read_tsv_required, write_tsv


def test_builds_feature_inclusion_gate_without_product_authority(
    tmp_path: Path,
) -> None:
    paths = _write_inputs(tmp_path)

    payload = gate.build_feature_inclusion_gate(
        differential_review_tsv=paths["differential"],
        overlay_summary_tsv=paths["overlay"],
        ai_adjudication_tsv=paths["ai"],
        decisions_tsv=paths["decisions"],
        output_dir=tmp_path / "gate",
    )

    assert payload["overall_status"] == "pass"
    assert payload["validation_label"] == "diagnostic_only"
    assert payload["candidate_cell_count"] == 17
    assert payload["supported_candidate_cell_count"] == 8
    assert payload["review_candidate_cell_count"] == 7
    assert payload["blocked_candidate_cell_count"] == 2
    assert payload["expected_diff_cell_count"] == 8
    assert payload["agent_resolved_expected_diff_cell_count"] == 4
    assert payload["agent_resolved_hold_cell_count"] == 3
    assert payload["manual_resolved_expected_diff_cell_count"] == 0
    assert payload["manual_resolved_hold_cell_count"] == 0
    assert payload["user_review_cell_count"] == 0
    assert payload["agent_resolved_expected_diff_contract_cell_count"] == 4
    assert payload["manual_resolved_expected_diff_contract_cell_count"] == 0
    assert payload["existing_successor_context_cell_count"] == 4
    assert payload["omitted_no_target_cell_count"] == 3
    assert payload["expected_diff_transition_count"] == 1
    assert payload["agent_resolved_expected_diff_transition_count"] == 1
    assert payload["agent_resolved_hold_transition_count"] == 1
    assert payload["manual_resolved_expected_diff_transition_count"] == 0
    assert payload["manual_resolved_hold_transition_count"] == 0
    assert payload["user_review_transition_count"] == 0
    assert payload["review_transition_count"] == 2
    assert payload["blocked_transition_count"] == 1
    assert payload["product_writer_changed"] is False
    assert payload["default_quant_matrix_changed"] is False
    assert payload["candidate_rows_are_matrix_rows"] is False

    summary = json.loads(
        (tmp_path / "gate" / "cid_nl_feature_inclusion_gate_summary.json")
        .read_text(encoding="utf-8")
    )
    assert summary["review_frame"] == "feature_inclusion_then_identity_authority"
    assert "separate expected-diff activation contract" in (
        summary["authority_statement"]
    )

    expected_diff = (
        tmp_path / "gate" / "cid_nl_identity_expected_diff_queue.tsv"
    ).read_text(encoding="utf-8")
    assert "FAM000001->FAM000002" in expected_diff
    assert "FAM000003->FAM000004" not in expected_diff
    assert "expected_diff_required_before_identity_authority" in expected_diff

    expected_diff_contract_path = (
        tmp_path / "gate" / "cid_nl_supported_candidate_expected_diff_contract.tsv"
    )
    expected_diff_contract_rows = read_tsv_required(
        expected_diff_contract_path,
        gate.EXPECTED_DIFF_COLUMNS,
    )
    assert len(expected_diff_contract_rows) == 8
    assert {
        row["transition_key"] for row in expected_diff_contract_rows
    } == {"FAM000001->FAM000002"}
    expected_diff_contract = expected_diff_contract_path.read_text(
        encoding="utf-8",
    )
    assert "candidate_only_expected_diff_required_no_product_write" in (
        expected_diff_contract
    )
    assert "legacy_successor_matrix_write_allowed" in expected_diff_contract
    assert "\tmatrix_write_allowed\t" not in expected_diff_contract
    assert "diagnostic_only_no_authority_change" in expected_diff_contract
    assert "Sample8" in expected_diff_contract
    assert "FAM000003->FAM000004" not in expected_diff_contract

    review_queue = (
        tmp_path / "gate" / "cid_nl_feature_inclusion_review_queue.tsv"
    ).read_text(encoding="utf-8")
    assert "FAM000003->FAM000004" in review_queue
    assert "queue_review_before_expected_diff" in review_queue
    transition_rows = read_tsv_required(
        tmp_path / "gate" / "cid_nl_feature_inclusion_gate_transitions.tsv",
        gate.GATE_TRANSITION_COLUMNS,
    )
    by_key = {row["transition_key"]: row for row in transition_rows}
    assert by_key["FAM000001->FAM000002"]["review_path"] == (
        "FAM000001->FAM000002_curves.json"
    )
    assert by_key["FAM000003->FAM000004"]["review_path"] == (
        "FAM000003->FAM000004_curves.json"
    )

    agent_expected_diff_rows = read_tsv_required(
        tmp_path
        / "gate"
        / "cid_nl_agent_resolved_expected_diff_contract.tsv",
        gate.EXPECTED_DIFF_COLUMNS,
    )
    assert len(agent_expected_diff_rows) == 4
    assert {
        row["transition_key"] for row in agent_expected_diff_rows
    } == {"FAM000003->FAM000004"}

    agent_hold_queue = (
        tmp_path / "gate" / "cid_nl_agent_resolved_hold_queue.tsv"
    ).read_text(encoding="utf-8")
    assert "FAM000010->FAM000011" in agent_hold_queue
    assert "agent_resolved_source_supported" in agent_hold_queue

    user_review_queue = (
        tmp_path / "gate" / "cid_nl_user_review_queue.tsv"
    ).read_text(encoding="utf-8")
    assert "FAM000003->FAM000004" not in user_review_queue
    assert "FAM000010->FAM000011" not in user_review_queue

    blocked_queue = (
        tmp_path / "gate" / "cid_nl_feature_inclusion_blocked_queue.tsv"
    ).read_text(encoding="utf-8")
    assert "FAM000005->FAM000006" in blocked_queue
    assert "exclude_from_current_activation_bundle" in blocked_queue


def test_guardrail_candidate_stays_out_of_expected_diff_until_review(
    tmp_path: Path,
) -> None:
    paths = _write_inputs(tmp_path, guardrail=True)

    payload = gate.build_feature_inclusion_gate(
        differential_review_tsv=paths["differential"],
        overlay_summary_tsv=paths["overlay"],
        ai_adjudication_tsv=paths["ai"],
        decisions_tsv=paths["decisions"],
        output_dir=tmp_path / "gate",
    )

    assert payload["supported_candidate_cell_count"] == 0
    assert payload["review_candidate_cell_count"] == 15
    assert payload["expected_diff_cell_count"] == 0
    assert payload["agent_resolved_expected_diff_cell_count"] == 4
    assert payload["agent_resolved_hold_cell_count"] == 3
    assert payload["manual_resolved_expected_diff_cell_count"] == 0
    assert payload["user_review_cell_count"] == 8
    review_queue = (
        tmp_path / "gate" / "cid_nl_feature_inclusion_review_queue.tsv"
    ).read_text(encoding="utf-8")
    assert "candidate_feature_inclusion_guardrail_review_required" in review_queue
    assert "identity_guardrail_review_required" in review_queue
    expected_diff = (
        tmp_path / "gate" / "cid_nl_identity_expected_diff_queue.tsv"
    ).read_text(encoding="utf-8")
    assert "FAM000001->FAM000002" not in expected_diff


def test_manual_review_resolves_guardrail_to_expected_diff_without_authority(
    tmp_path: Path,
) -> None:
    paths = _write_inputs(tmp_path, guardrail=True)
    manual = tmp_path / "manual.tsv"
    write_tsv(
        manual,
        [
            _manual_review(
                "FAM000001",
                "FAM000002",
                decision="support_successor_feature_inclusion",
                basis=(
                    "Reviewer accepts successor MS1 feature support while "
                    "leaving identity authority to expected-diff review."
                ),
            ),
        ],
        gate.MANUAL_REVIEW_REQUIRED_COLUMNS,
    )

    payload = gate.build_feature_inclusion_gate(
        differential_review_tsv=paths["differential"],
        overlay_summary_tsv=paths["overlay"],
        ai_adjudication_tsv=paths["ai"],
        decisions_tsv=paths["decisions"],
        manual_review_tsv=manual,
        output_dir=tmp_path / "gate",
    )

    assert payload["supported_candidate_cell_count"] == 0
    assert payload["expected_diff_cell_count"] == 0
    assert payload["agent_resolved_expected_diff_cell_count"] == 4
    assert payload["manual_resolved_expected_diff_cell_count"] == 8
    assert payload["manual_resolved_hold_cell_count"] == 0
    assert payload["user_review_cell_count"] == 0
    assert payload["manual_resolved_expected_diff_contract_cell_count"] == 8
    assert payload["manual_resolved_expected_diff_transition_count"] == 1

    manual_contract_rows = read_tsv_required(
        tmp_path
        / "gate"
        / "cid_nl_manual_resolved_expected_diff_contract.tsv",
        gate.EXPECTED_DIFF_COLUMNS,
    )
    assert len(manual_contract_rows) == 8
    assert {row["transition_key"] for row in manual_contract_rows} == {
        "FAM000001->FAM000002"
    }
    assert {row["product_authority_effect"] for row in manual_contract_rows} == {
        "diagnostic_only_no_authority_change"
    }

    user_review_queue = (
        tmp_path / "gate" / "cid_nl_user_review_queue.tsv"
    ).read_text(encoding="utf-8")
    assert "FAM000001->FAM000002" not in user_review_queue


def test_cli_require_pass_builds_gate(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path)

    exit_code = gate.main(
        [
            "--differential-review-tsv",
            str(paths["differential"]),
            "--overlay-summary-tsv",
            str(paths["overlay"]),
            "--ai-adjudication-tsv",
            str(paths["ai"]),
            "--decisions-tsv",
            str(paths["decisions"]),
            "--output-dir",
            str(tmp_path / "gate"),
            "--require-pass",
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "gate" / "cid_nl_feature_inclusion_gate_summary.tsv").exists()


def _write_inputs(tmp_path: Path, *, guardrail: bool = False) -> dict[str, Path]:
    differential = tmp_path / "differential.tsv"
    overlay = tmp_path / "overlay.tsv"
    ai = tmp_path / "ai.tsv"
    decisions = tmp_path / "decisions.tsv"
    write_tsv(
        differential,
        [
            _differential(
                "FAM000001",
                "FAM000002",
                write_count=8,
                preserve_count=2,
            ),
            _differential(
                "FAM000003",
                "FAM000004",
                write_count=4,
                preserve_count=1,
            ),
            _differential(
                "FAM000005",
                "FAM000006",
                write_count=2,
                preserve_count=0,
            ),
            _differential(
                "FAM000010",
                "FAM000011",
                write_count=3,
                preserve_count=0,
            ),
            _differential(
                "FAM000007",
                "FAM000008",
                write_count=0,
                preserve_count=1,
            ),
            _differential(
                "FAM000009",
                "<none>",
                write_count=0,
                preserve_count=0,
                omitted_count=3,
                readiness="no_successor_target",
            ),
        ],
        gate.DIFFERENTIAL_REQUIRED_COLUMNS,
    )
    write_tsv(
        overlay,
        [
            _overlay("FAM000001->FAM000002"),
            _overlay("FAM000003->FAM000004"),
            _overlay("FAM000005->FAM000006"),
            _overlay("FAM000010->FAM000011"),
        ],
        gate.OVERLAY_REQUIRED_COLUMNS,
    )
    write_tsv(
        ai,
        [
            _ai(
                "FAM000001->FAM000002",
                "accept_successor_identity_clear",
                guardrail_flag=(
                    "target_guardrail_300_184_source_context" if guardrail else ""
                ),
            ),
            _ai("FAM000003->FAM000004", "human_review_needed"),
            _ai("FAM000005->FAM000006", "reject_successor_identity_clear"),
            _ai(
                "FAM000010->FAM000011",
                "human_review_needed",
                successor_support="0.1",
                source_support="0.8",
            ),
        ],
        gate.AI_REQUIRED_COLUMNS,
    )
    write_tsv(
        decisions,
        [
            *_decision_rows("FAM000001", "FAM000002", 8),
            *_decision_rows("FAM000003", "FAM000004", 4),
            *_decision_rows("FAM000005", "FAM000006", 2),
            *_decision_rows("FAM000010", "FAM000011", 3),
        ],
        gate.DECISION_REQUIRED_COLUMNS,
    )
    return {
        "differential": differential,
        "overlay": overlay,
        "ai": ai,
        "decisions": decisions,
    }


def _differential(
    source: str,
    successor: str,
    *,
    write_count: int,
    preserve_count: int,
    omitted_count: int = 0,
    readiness: str = "ready_for_paired_overlay",
) -> dict[str, object]:
    return {
        "source_peak_hypothesis_id": source,
        "successor_peak_hypothesis_id": successor,
        "transition_key": f"{source}->{successor}",
        "sample_count": write_count + preserve_count + omitted_count,
        "write_authorized_count": write_count,
        "no_write_detected_baseline_preserved_count": preserve_count,
        "no_write_omitted_count": omitted_count,
        "source_mz": "243.099",
        "source_rt": "23.66",
        "source_product_mz": "127.052",
        "source_neutral_loss_tag": "DNA_dR",
        "source_identity_decision": "audit_family",
        "source_accepted_cell_count": "0",
        "successor_mz": "300.1605" if successor != "<none>" else "",
        "successor_rt": "23.35" if successor != "<none>" else "",
        "successor_product_mz": "184.113" if successor != "<none>" else "",
        "successor_neutral_loss_tag": "DNA_dR" if successor != "<none>" else "",
        "successor_identity_decision": (
            "production_family" if successor != "<none>" else ""
        ),
        "successor_accepted_cell_count": "85" if successor != "<none>" else "",
        "feature_inclusion_gate": (
            "candidate_ms1_feature_inclusion_supported"
            if write_count
            else "feature_inclusion_already_supported"
        ),
        "identity_authority_gate": (
            "replacement_merge_dedupe_requires_expected_diff"
            if write_count
            else "no_identity_authority_change_requested"
        ),
        "source_successor_relationship": (
            "source_and_successor_not_mutually_exclusive"
        ),
        "transition_type": (
            "old_to_successor" if successor != "<none>" else "no_successor_target"
        ),
        "differential_overlay_readiness": readiness,
    }


def _overlay(transition_key: str) -> dict[str, object]:
    return {
        "transition_key": transition_key,
        "status": "success",
        "review_path": f"{transition_key}_curves.json",
        "trace_data_json": f"{transition_key}.json",
        "source_trace_max_median": "10",
        "successor_trace_max_median": "100",
        "successor_to_source_median_max_ratio": "10",
        "source_nonzero_fraction": "0.1",
        "successor_nonzero_fraction": "1",
    }


def _ai(
    transition_key: str,
    decision: str,
    *,
    guardrail_flag: str = "",
    successor_support: str | None = None,
    source_support: str | None = None,
) -> dict[str, object]:
    return {
        "transition_key": transition_key,
        "ai_review_decision": decision,
        "ai_confidence": "high",
        "human_review_needed": (
            "yes" if decision == "human_review_needed" else "no"
        ),
        "ai_reason": "fixture reason",
        "product_authority_effect": "diagnostic_only_no_authority_change",
        "guardrail_flag": guardrail_flag,
        "trace_sample_count": "10",
        "successor_only_count": "8",
        "successor_dominant_count": "1",
        "source_only_count": "0",
        "source_dominant_count": "1",
        "close_count": "0",
        "none_count": "0",
        "successor_support_fraction": successor_support
        or ("0" if decision == "reject_successor_identity_clear" else "0.9"),
        "source_support_fraction": source_support
        or ("0.9" if decision == "reject_successor_identity_clear" else "0.1"),
        "status": "success",
        "png_path": f"{transition_key}.png",
        "trace_data_json": f"{transition_key}.json",
    }


def _decision_rows(
    source: str,
    successor: str,
    count: int,
) -> list[dict[str, object]]:
    return [
        {
            "old_peak_hypothesis_id": source,
            "sample_stem": f"Sample{index}",
            "successor_peak_hypothesis_id": successor,
            "successor_decision": "write_authorized",
            "write_authority": "diagnostic_candidate",
            "matrix_write_allowed": "FALSE",
            "matrix_effect": "candidate_new_value",
            "human_explanation": "fixture",
            "input_resolution_status": "resolved",
            "candidate_new_peak_hypothesis_ids": successor,
            "candidate_baseline_values": "",
            "accepted_quant_value": str(1000 + index),
        }
        for index in range(1, count + 1)
    ]


def _manual_review(
    source: str,
    successor: str,
    *,
    decision: str,
    basis: str,
) -> dict[str, object]:
    return {
        "schema_version": gate.MANUAL_REVIEW_SCHEMA_VERSION,
        "transition_key": f"{source}->{successor}",
        "source_peak_hypothesis_id": source,
        "successor_peak_hypothesis_id": successor,
        "manual_review_decision": decision,
        "manual_review_basis": basis,
        "manual_feature_inclusion_scope": "transition_level_successor_support",
        "source_peak_review_state": "not_required_for_feature_inclusion",
        "successor_peak_review_state": "reviewer_supported",
        "identity_authority_note": (
            "Manual review supports feature inclusion only; identity authority "
            "still requires expected-diff activation review."
        ),
        "product_authority_effect": "diagnostic_only_no_authority_change",
    }
