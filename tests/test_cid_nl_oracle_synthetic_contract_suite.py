from __future__ import annotations

import csv
from pathlib import Path

from xic_extractor.discovery.cid_nl_oracle_verdict import (
    aggregate_synthetic_metrics,
    synthetic_contract_violation,
)

ROOT = Path(__file__).resolve().parents[1]
SUITE = (
    ROOT
    / "docs/superpowers/validation/cid_nl_oracle_packet_preflight/"
    "cid_nl_oracle_synthetic_contract_suite.tsv"
)


def _rows() -> list[dict[str, str]]:
    with SUITE.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_synthetic_suite_covers_required_contract_classes() -> None:
    rows = _rows()
    assert len(rows) == 25

    classes = {row["scenario_class"] for row in rows}
    assert {
        "clean_observed_peak",
        "no_peak_absent",
        "noise_selected_as_value",
        "neighbor_ownership_ambiguous",
        "ambiguous_selected_as_value",
        "truncated_machine_boundary",
        "wrong_apex_outside_boundary",
        "insufficient_area_retention",
        "targeted_prior_with_local_peak",
        "targeted_prior_without_local_peak",
        "unassessable_hold",
        "unassessable_selected_as_value",
        "failed_collapsed_to_absent",
        "replay_is_deterministic",
        "replay_lineage_mismatch",
        "hidden_duplicate_not_scientific_weight",
        "tested_tracer_defines_reference",
        "nl132_control_outside_nl116",
        "observed_collapsed_to_missing",
        "absent_held_instead_of_completed",
        "missing_collapsed_to_absent",
        "observed_policy_hold",
        "failed_state_hold",
    } <= classes


def test_synthetic_suite_expected_outcomes_follow_contract() -> None:
    for row in _rows():
        violation = synthetic_contract_violation(row)
        if row["expected_contract_status"] == "pass":
            assert violation is None, row["synthetic_case_id"]
            assert row["expected_gate_id"] == "none"
        else:
            assert violation == row["expected_gate_id"], row["synthetic_case_id"]


def test_synthetic_suite_keeps_non_authoritative_rows_out_of_denominators() -> None:
    rows = {row["synthetic_case_id"]: row for row in _rows()}

    assert rows["SYN013"]["observation_state"] == "unassessable"
    assert rows["SYN013"]["projection_outcome"] == "hold"
    assert rows["SYN018"]["analysis_role"] == "repeatability_only"
    assert rows["SYN020"]["analysis_role"] == "supplemental_descriptive"
    assert rows["SYN020"]["expected_claim_boundary"] == "method_scope_exclusion_only"


def test_synthetic_metrics_exclude_duplicates_and_supplemental_rows() -> None:
    summary = aggregate_synthetic_metrics(_rows())

    assert summary.primary_case_count == 10
    assert summary.proposal_positive_count == 4
    assert summary.proposal_correct_count == 4
    assert summary.reference_positive_count == 5
    assert summary.reference_recalled_count == 4
    assert summary.hidden_duplicate_count == 1
    assert summary.supplemental_count == 1


def test_synthetic_metrics_ignore_author_supplied_answer_flags() -> None:
    rows = _rows()
    baseline = aggregate_synthetic_metrics(rows)
    mutated = [
        {
            **row,
            "include_in_primary_metrics": (
                "FALSE"
                if row["include_in_primary_metrics"] == "TRUE"
                else "TRUE"
            ),
            "proposal_positive": "FALSE",
            "reference_positive_eligible": "FALSE",
            "machine_correct": "FALSE",
            "expected_contract_status": "pass",
        }
        for row in rows
    ]

    assert aggregate_synthetic_metrics(mutated) == baseline
