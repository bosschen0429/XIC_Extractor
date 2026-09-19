from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from xic_extractor.alignment.production_candidate_gate import (
    PRODUCTION_CANDIDATE_GATE_COLUMNS,
    TIER2_CRITERIA,
    TIER2_DIAGNOSTIC_CRITERIA,
    TIER2_SUPPORT_COMPONENT,
    TIER2_TRACE_EVIDENCE_COLUMNS,
    evaluate_production_candidate_gate,
    load_tier2_trace_evidence,
    production_candidate_gate_as_row,
    source_context_for_artifacts,
    summarize_gate_decisions,
    tier2_candidate_subset_signature,
)


def test_direct_review_row_tier2_token_does_not_promote_without_sidecar(
    tmp_path: Path,
) -> None:
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    decision = evaluate_production_candidate_gate(
        _review_row(
            flags=(
                "single_detected_seed;provisional_retention_candidate;"
                "skip_expensive_evidence"
            ),
            detected=1,
            rescued=2,
            independent_support=TIER2_SUPPORT_COMPONENT,
        ),
        _cell_rows(detected=1, rescued=2),
        source_context=source_context_for_artifacts(
            review_path=review_path,
            cell_path=cell_path,
            matrix_path=matrix_path,
        ),
    )

    assert decision.candidate_gate_status == "keep_provisional"
    assert decision.recommended_action == "keep_provisional"
    assert decision.evidence_tier == 1
    assert decision.support_components == ()
    assert decision.tier2_evidence_available is False
    assert "missing_positive_tier2_support" in decision.challenge_blockers
    assert decision.candidate_confidence == "review"


def test_valid_tier2_sidecar_support_tracks_candidate(tmp_path: Path) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(tmp_path)

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "production_candidate"
    assert decision.recommended_action == "track_candidate"
    assert decision.evidence_tier == 2
    assert decision.support_components == (TIER2_SUPPORT_COMPONENT,)
    assert decision.dependent_context == (
        "owner_backfill_context",
        "family_ms1_context",
        "rescued_cell_scan_support_distribution",
        "selected_boundary_local_apex_consistency",
        "rescued_cell_rt_coherence",
    )
    assert decision.challenge_blockers == ()
    assert decision.tier2_evidence_available is True
    assert decision.candidate_confidence == "medium"


def test_retention_candidate_without_positive_tier2_support_stays_provisional(
    tmp_path: Path,
) -> None:
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    decision = evaluate_production_candidate_gate(
        _review_row(
            flags="single_detected_seed;provisional_retention_candidate",
            detected=1,
            rescued=1,
        ),
        _cell_rows(detected=1, rescued=1, scan_support_score="0.3"),
        source_context=source_context_for_artifacts(
            review_path=review_path,
            cell_path=cell_path,
            matrix_path=matrix_path,
        ),
    )

    assert decision.candidate_gate_status == "keep_provisional"
    assert decision.recommended_action == "keep_provisional"
    assert decision.evidence_tier == 1
    assert decision.support_components == ()
    assert decision.dependent_context == (
        "owner_backfill_context",
        "family_ms1_context",
        "selected_boundary_local_apex_consistency",
    )
    assert decision.tier2_evidence_available is False
    assert "missing_positive_tier2_support" in decision.challenge_blockers
    assert decision.candidate_confidence == "review"


def test_neighboring_interference_forces_audit(tmp_path: Path) -> None:
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    decision = evaluate_production_candidate_gate(
        _review_row(
            flags="single_detected_seed;provisional_retention_candidate",
            detected=1,
            rescued=1,
        ),
        _cell_rows(
            detected=1,
            rescued=1,
            region_review_reason="neighboring_ms1_interference",
        ),
        source_context=source_context_for_artifacts(
            review_path=review_path,
            cell_path=cell_path,
            matrix_path=matrix_path,
        ),
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.recommended_action == "review"
    assert "neighboring_interference_challenge" in decision.challenge_blockers
    assert decision.candidate_confidence == "review"


def test_low_scan_support_forces_audit(tmp_path: Path) -> None:
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    decision = evaluate_production_candidate_gate(
        _review_row(
            flags="single_detected_seed;provisional_retention_candidate",
            detected=1,
            rescued=1,
        ),
        _cell_rows(detected=1, rescued=1, scan_support_score="0.1"),
        source_context=source_context_for_artifacts(
            review_path=review_path,
            cell_path=cell_path,
            matrix_path=matrix_path,
        ),
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.recommended_action == "review"
    assert "low_assessable_coverage_challenge" in decision.challenge_blockers
    assert "missing_positive_tier2_support" in decision.challenge_blockers


def test_missing_rescued_cell_evidence_blocks_tier2_candidate(
    tmp_path: Path,
) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(tmp_path)

    decision = evaluate_production_candidate_gate(
        review_row,
        (),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.recommended_action == "review"
    assert decision.evidence_tier == 2
    assert decision.support_components == (TIER2_SUPPORT_COMPONENT,)
    assert "missing_rescued_cell_evidence" in decision.challenge_blockers


def test_fractional_counts_are_not_valid_candidate_eligibility(
    tmp_path: Path,
) -> None:
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    decision = evaluate_production_candidate_gate(
        _review_row(
            flags="single_detected_seed;provisional_retention_candidate",
            detected="1.5",
            rescued=1,
            independent_support=TIER2_SUPPORT_COMPONENT,
        ),
        _cell_rows(detected=1, rescued=1),
        source_context=source_context_for_artifacts(
            review_path=review_path,
            cell_path=cell_path,
            matrix_path=matrix_path,
        ),
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.recommended_action == "review"
    assert decision.tier2_evidence_available is False
    assert decision.support_components == ()
    assert decision.challenge_blockers == ("not_retention_candidate",)


def test_dependent_or_provenance_only_support_tokens_do_not_promote(
    tmp_path: Path,
) -> None:
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    decision = evaluate_production_candidate_gate(
        _review_row(
            flags="single_detected_seed;provisional_retention_candidate",
            detected=1,
            rescued=2,
            independent_support="owner_backfill_context;rescued_cell_rt_coherence",
        ),
        _cell_rows(detected=1, rescued=2),
        source_context=source_context_for_artifacts(
            review_path=review_path,
            cell_path=cell_path,
            matrix_path=matrix_path,
        ),
    )

    assert decision.candidate_gate_status == "keep_provisional"
    assert decision.support_components == ()
    assert decision.tier2_evidence_available is False
    assert "missing_positive_tier2_support" in decision.challenge_blockers


def test_unknown_support_token_does_not_promote(tmp_path: Path) -> None:
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    decision = evaluate_production_candidate_gate(
        _review_row(
            flags="single_detected_seed;provisional_retention_candidate",
            detected=1,
            rescued=2,
            independent_support="unreviewed_tier2_placeholder",
        ),
        _cell_rows(detected=1, rescued=2),
        source_context=source_context_for_artifacts(
            review_path=review_path,
            cell_path=cell_path,
            matrix_path=matrix_path,
        ),
    )

    assert decision.candidate_gate_status == "keep_provisional"
    assert decision.support_components == ()
    assert decision.tier2_evidence_available is False
    assert "missing_positive_tier2_support" in decision.challenge_blockers


def test_review_only_exact_token_excludes_but_rescue_only_review_does_not(
    tmp_path: Path,
) -> None:
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    context = source_context_for_artifacts(
        review_path=review_path,
        cell_path=cell_path,
        matrix_path=matrix_path,
    )

    review_only = evaluate_production_candidate_gate(
        _review_row(
            identity_reason="review_only",
            flags="single_detected_seed;provisional_retention_candidate",
            detected=1,
            rescued=1,
        ),
        _cell_rows(detected=1, rescued=1),
        source_context=context,
    )
    rescue_only_review = evaluate_production_candidate_gate(
        _review_row(
            flags=(
                "single_detected_seed;provisional_retention_candidate;"
                "rescue_only_review"
            ),
            detected=1,
            rescued=1,
        ),
        _cell_rows(detected=1, rescued=1),
        source_context=context,
    )

    assert review_only.candidate_gate_status == "audit"
    assert review_only.recommended_action == "review"
    assert "review_only" in review_only.challenge_blockers
    assert "review_only" not in rescue_only_review.challenge_blockers
    assert rescue_only_review.candidate_gate_status == "keep_provisional"
    assert "missing_positive_tier2_support" in rescue_only_review.challenge_blockers


def test_non_retention_candidate_is_reported_without_tier2_promotion(
    tmp_path: Path,
) -> None:
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    decision = evaluate_production_candidate_gate(
        _review_row(flags="single_detected_seed", detected=1, rescued=1),
        _cell_rows(detected=1, rescued=1),
        source_context=source_context_for_artifacts(
            review_path=review_path,
            cell_path=cell_path,
            matrix_path=matrix_path,
        ),
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.recommended_action == "review"
    assert decision.evidence_tier == 1
    assert decision.tier2_evidence_available is False
    assert decision.challenge_blockers == ("not_retention_candidate",)


def test_gate_row_has_stable_columns_and_hashes(tmp_path: Path) -> None:
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    decision = evaluate_production_candidate_gate(
        _review_row(
            flags="single_detected_seed;provisional_retention_candidate",
            detected=1,
            rescued=1,
        ),
        _cell_rows(detected=1, rescued=1),
        source_context=source_context_for_artifacts(
            review_path=review_path,
            cell_path=cell_path,
            matrix_path=matrix_path,
        ),
    )

    row = production_candidate_gate_as_row(decision)

    assert tuple(row) == PRODUCTION_CANDIDATE_GATE_COLUMNS
    assert row["source_review_artifact"] == str(review_path)
    assert row["source_cell_artifact"] == str(cell_path)
    assert row["source_matrix_artifact"] == str(matrix_path)
    assert len(row["source_review_sha256"]) == 64
    assert row["source_review_sha256"].isupper()
    assert len(row["source_cell_sha256"]) == 64
    assert row["source_cell_sha256"].isupper()
    assert len(row["source_matrix_sha256"]) == 64
    assert row["source_matrix_sha256"].isupper()


def test_summarize_gate_decisions_reports_diagnostic_only_counts(
    tmp_path: Path,
) -> None:
    production_evidence, review_row, context = _load_tier2_fixture(tmp_path)
    production_candidate = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=context,
        tier2_evidence=production_evidence,
    )
    keep_provisional = evaluate_production_candidate_gate(
        _review_row(
            flags="single_detected_seed;provisional_retention_candidate",
            detected=1,
            rescued=1,
        ),
        _cell_rows(detected=1, rescued=1),
        source_context=context,
    )
    audit = evaluate_production_candidate_gate(
        _review_row(
            flags="single_detected_seed;provisional_retention_candidate",
            detected=1,
            rescued=1,
        ),
        _cell_rows(
            detected=1,
            rescued=1,
            region_review_reason="neighboring_ms1_interference",
        ),
        source_context=context,
    )

    summary = summarize_gate_decisions((production_candidate, keep_provisional, audit))

    assert summary["readiness_label"] == "diagnostic_only"
    assert summary["production_candidate_count"] == 1
    assert summary["keep_provisional_count"] == 1
    assert summary["audit_count"] == 1
    assert summary["excluded_count"] == 0
    assert summary["production_ready"] is False
    assert summary["matrix_contract_changed"] is False


def test_stale_tier2_source_hash_blocks_support(tmp_path: Path) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        source_review_sha256="0" * 64,
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.support_components == ()
    assert "source_hash_mismatch" in decision.challenge_blockers


def test_unknown_tier2_criteria_version_blocks_support(tmp_path: Path) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        criteria_version="future_criteria",
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert "criteria_version_not_allowlisted" in decision.challenge_blockers


def test_inconclusive_tier2_sidecar_does_not_promote(tmp_path: Path) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        evidence_status="inconclusive",
        support_component="",
        challenge_blockers="metric_unavailable",
        raw_trace_reread_status="inconclusive",
        coherence_status="inconclusive",
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.support_components == ()
    assert "metric_unavailable" in decision.challenge_blockers


def test_tier2_hard_challenge_blocker_prevents_support(tmp_path: Path) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        challenge_blockers="neighbor_interference",
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.support_components == ()
    assert "neighbor_interference" in decision.challenge_blockers


def test_mismatched_tier2_family_id_does_not_promote(tmp_path: Path) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        sidecar_family_id="FAM999",
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.support_components == ()
    assert "tier2_feature_family_id_mismatch" in decision.challenge_blockers


@pytest.mark.parametrize(
    ("overrides", "expected_blocker"),
    (
        (
            {"producer_version": "unsupported_raw_trace_reread_tier2"},
            "producer_version_not_recognized",
        ),
        ({"source_raw_manifest_sha256": "0" * 64}, "raw_manifest_hash_mismatch"),
        (
            {"source_candidate_subset_sha256": "0" * 64},
            "candidate_subset_hash_mismatch",
        ),
        ({"source_candidate_subset_count": "999"}, "candidate_subset_hash_mismatch"),
        ({"support_component": ""}, "missing_positive_tier2_support"),
        ({"raw_trace_reread_status": "fail"}, "raw_trace_reread_not_pass"),
        ({"coherence_status": "fail"}, "rescued_coherence_not_pass"),
        ({"scan_support_score": ""}, "metric_unavailable"),
    ),
)
def test_invalid_tier2_sidecar_fields_do_not_promote(
    tmp_path: Path,
    overrides: dict[str, str],
    expected_blocker: str,
) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        **cast(Any, overrides),
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.support_components == ()
    assert expected_blocker in decision.challenge_blockers


def test_blank_tier2_neighbor_interference_requires_not_assessed_context(
    tmp_path: Path,
) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        row_overrides={"neighbor_interference_ratio": "", "dependent_context": ""},
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.support_components == ()
    assert "neighbor_interference_unassessed" in decision.challenge_blockers


@pytest.mark.parametrize(
    ("row_overrides", "expected_blocker"),
    (
        ({"seed_apex_rt": ""}, "metric_unavailable"),
        ({"tier2_apex_rt": ""}, "metric_unavailable"),
        ({"boundary_start_rt": ""}, "metric_unavailable"),
        ({"boundary_end_rt": ""}, "metric_unavailable"),
        ({"source_expected_sample_count": ""}, "missing_valid_tier2_provenance"),
        ({"raw_reader_runtime": ""}, "missing_valid_tier2_provenance"),
        ({"python_executable": ""}, "missing_valid_tier2_provenance"),
        ({"dll_dir": ""}, "missing_valid_tier2_provenance"),
        ({"producer_command": ""}, "missing_valid_tier2_provenance"),
        ({"generated_at_utc": ""}, "missing_valid_tier2_provenance"),
    ),
)
def test_required_tier2_full_schema_values_do_not_promote(
    tmp_path: Path,
    row_overrides: dict[str, str],
    expected_blocker: str,
) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        row_overrides=row_overrides,
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.support_components == ()
    assert expected_blocker in decision.challenge_blockers


@pytest.mark.parametrize(
    ("row_overrides", "expected_blocker"),
    (
        ({"trace_scan_count": "4"}, "metric_unavailable"),
        ({"scan_support_score": "0.10"}, "low_scan_support"),
        ({"scan_support_score": "0.30"}, "weak_scan_support"),
        ({"apex_delta_sec": "31.0"}, "apex_delta_exceeds_threshold"),
        ({"boundary_width_sec": "0.0"}, "boundary_width_out_of_range"),
        ({"boundary_width_sec": "181.0"}, "boundary_width_out_of_range"),
        ({"neighbor_interference_ratio": "0.34"}, "neighbor_interference"),
        ({"rescued_cell_count_supported": "0"}, "rescued_cell_support_low"),
        (
            {"rescued_cell_count_checked": "4", "rescued_cell_count_supported": "1"},
            "rescued_cell_support_low",
        ),
        ({"rescued_apex_rt_span_sec": "22.0"}, "rescued_apex_span_wide"),
        ({"rescued_boundary_overlap_min": "0.49"}, "rescued_boundary_overlap_low"),
    ),
)
def test_tier2_threshold_failures_do_not_promote(
    tmp_path: Path,
    row_overrides: dict[str, str],
    expected_blocker: str,
) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        row_overrides=row_overrides,
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.support_components == ()
    assert expected_blocker in decision.challenge_blockers


def test_tier2_sidecar_missing_metric_column_is_rejected(
    tmp_path: Path,
) -> None:
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    review_row = _review_row(
        flags="single_detected_seed;provisional_retention_candidate",
        detected=1,
        rescued=2,
    )
    source_context = source_context_for_artifacts(
        review_path=review_path,
        cell_path=cell_path,
        matrix_path=matrix_path,
    )
    raw_manifest_path = _write_raw_manifest(tmp_path)
    sidecar_path = _write_tier2_sidecar(
        tmp_path,
        review_rows=(review_row,),
        source_context=source_context,
        raw_manifest_path=raw_manifest_path,
        evidence_status="validated",
        support_component=TIER2_SUPPORT_COMPONENT,
        raw_trace_reread_status="pass",
        coherence_status="pass",
        sidecar_columns=TIER2_TRACE_EVIDENCE_COLUMNS,
        omitted_columns=("trace_scan_count",),
    )

    with pytest.raises(ValueError, match="trace_scan_count"):
        load_tier2_trace_evidence(
            sidecar_path=sidecar_path,
            raw_manifest_path=raw_manifest_path,
            candidate_rows=(review_row,),
            source_context=source_context,
        )


def test_diagnostic_tier2_sidecar_is_diagnostic_only(tmp_path: Path) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        criteria_version=TIER2_DIAGNOSTIC_CRITERIA,
        producer_version="raw_trace_reread_tier2",
        evidence_status="inconclusive",
        support_component="",
        raw_trace_reread_status="pass",
        coherence_status="inconclusive",
        challenge_blockers="tier2_diagnostic_only",
        dependent_context=(
            "neighbor_interference_not_assessed;"
            "raw_trace_reread;"
            "rescued_coherence"
        ),
        row_overrides=_diagnostic_row_overrides(),
        sidecar_columns=TIER2_TRACE_EVIDENCE_COLUMNS,
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.evidence_tier == 1
    assert decision.support_components == ()
    assert decision.tier2_evidence_available is False
    assert "tier2_diagnostic_only" in decision.challenge_blockers


@pytest.mark.parametrize("criteria", (TIER2_CRITERIA, TIER2_DIAGNOSTIC_CRITERIA))
def test_sidecar_requires_complete_metric_columns(
    tmp_path: Path, criteria: str,
) -> None:
    with pytest.raises(ValueError, match="trace_apex_intensity"):
        _load_tier2_fixture(
            tmp_path,
            criteria_version=criteria,
            producer_version="raw_trace_reread_tier2",
            evidence_status="inconclusive",
            support_component="",
            raw_trace_reread_status="pass",
            coherence_status="inconclusive",
            row_overrides=_diagnostic_row_overrides(),
            sidecar_columns=TIER2_TRACE_EVIDENCE_COLUMNS,
            omitted_columns=("trace_apex_intensity",),
        )


def test_diagnostic_gate_adds_diagnostic_only_blocker_when_row_omits_it(
    tmp_path: Path,
) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        criteria_version=TIER2_DIAGNOSTIC_CRITERIA,
        producer_version="raw_trace_reread_tier2",
        evidence_status="inconclusive",
        support_component="",
        raw_trace_reread_status="pass",
        coherence_status="inconclusive",
        challenge_blockers="",
        dependent_context=(
            "neighbor_interference_not_assessed;"
            "raw_trace_reread;"
            "rescued_coherence"
        ),
        row_overrides=_diagnostic_row_overrides(),
        sidecar_columns=TIER2_TRACE_EVIDENCE_COLUMNS,
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.support_components == ()
    assert "tier2_diagnostic_only" in decision.challenge_blockers


def test_diagnostic_validated_support_spoof_cannot_promote(tmp_path: Path) -> None:
    evidence, review_row, source_context = _load_tier2_fixture(
        tmp_path,
        criteria_version=TIER2_DIAGNOSTIC_CRITERIA,
        producer_version="raw_trace_reread_tier2",
        evidence_status="validated",
        support_component=TIER2_SUPPORT_COMPONENT,
        raw_trace_reread_status="pass",
        coherence_status="pass",
        challenge_blockers="",
        dependent_context=(
            "neighbor_interference_not_assessed;"
            "raw_trace_reread;"
            "rescued_coherence"
        ),
        row_overrides=_diagnostic_row_overrides(),
        sidecar_columns=TIER2_TRACE_EVIDENCE_COLUMNS,
    )

    decision = evaluate_production_candidate_gate(
        review_row,
        _cell_rows(detected=1, rescued=2),
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.evidence_tier == 1
    assert decision.support_components == ()
    assert decision.tier2_evidence_available is False
    assert "tier2_diagnostic_only" in decision.challenge_blockers


def _diagnostic_row_overrides() -> dict[str, str]:
    return {
        "scan_availability_score": "1.0",
        "trace_apex_intensity": "1000",
        "trace_baseline_noise": "10",
        "trace_signal_to_noise_proxy": "20",
        "trace_apex_prominence_score": "0.75",
        "scan_support_basis": "scan_count_only",
        "seed_rescued_boundary_overlap_min": "0.8",
        "rescued_pairwise_boundary_overlap_min": "0.85",
        "family_consensus_boundary_overlap_min": "0.82",
        "seed_rescued_apex_span_sec": "6",
        "rescued_only_apex_span_sec": "3",
        "neighbor_interference_status": "not_assessed",
    }


def _load_tier2_fixture(
    tmp_path: Path,
    *,
    evidence_status: str = "validated",
    support_component: str = TIER2_SUPPORT_COMPONENT,
    criteria_version: str = "tier2_trace_identity_rescued_coherence",
    producer_version: str = "raw_trace_reread_tier2",
    challenge_blockers: str = "",
    dependent_context: str = "",
    raw_trace_reread_status: str = "pass",
    coherence_status: str = "pass",
    source_review_sha256: str | None = None,
    source_raw_manifest_sha256: str | None = None,
    source_candidate_subset_sha256: str | None = None,
    source_candidate_subset_count: str | None = None,
    scan_support_score: str = "0.80",
    row_overrides: dict[str, str] | None = None,
    sidecar_family_id: str = "FAM001",
    omitted_columns: tuple[str, ...] = (),
    sidecar_columns: tuple[str, ...] | None = None,
):
    review_path, cell_path, matrix_path = _write_sources(tmp_path)
    review_row = _review_row(
        flags="single_detected_seed;provisional_retention_candidate",
        detected=1,
        rescued=2,
    )
    source_context = source_context_for_artifacts(
        review_path=review_path,
        cell_path=cell_path,
        matrix_path=matrix_path,
    )
    raw_manifest_path = _write_raw_manifest(tmp_path)
    sidecar_path = _write_tier2_sidecar(
        tmp_path,
        review_rows=(review_row,),
        source_context=source_context,
        raw_manifest_path=raw_manifest_path,
        feature_family_id=sidecar_family_id,
        evidence_status=evidence_status,
        support_component=support_component,
        criteria_version=criteria_version,
        producer_version=producer_version,
        challenge_blockers=challenge_blockers,
        dependent_context=dependent_context,
        raw_trace_reread_status=raw_trace_reread_status,
        coherence_status=coherence_status,
        source_review_sha256=source_review_sha256,
        source_raw_manifest_sha256=source_raw_manifest_sha256,
        source_candidate_subset_sha256=source_candidate_subset_sha256,
        source_candidate_subset_count=source_candidate_subset_count,
        scan_support_score=scan_support_score,
        row_overrides=row_overrides,
        omitted_columns=omitted_columns,
        sidecar_columns=sidecar_columns,
    )
    evidence_by_family = load_tier2_trace_evidence(
        sidecar_path=sidecar_path,
        raw_manifest_path=raw_manifest_path,
        candidate_rows=(review_row,),
        source_context=source_context,
    )
    evidence = evidence_by_family[sidecar_family_id]
    return evidence, review_row, source_context


def _write_raw_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "alignment_tier2_raw_manifest.tsv"
    path.write_text(
        (
            "sample_stem\traw_file_path\traw_file_size_bytes\t"
            "raw_file_mtime_utc\traw_reader_runtime\tpython_executable\tdll_dir\n"
            "S001\tC:\\Xcalibur\\data\\S001.raw\t123\t"
            "2026-05-29T00:00:00Z\tpythonnet\t.venv\\Scripts\\python.exe\t"
            "C:\\Xcalibur\\system\\programs\n"
        ),
        encoding="utf-8",
    )
    return path


def _write_tier2_sidecar(
    tmp_path: Path,
    *,
    review_rows: tuple[dict[str, str], ...],
    source_context,
    raw_manifest_path: Path,
    evidence_status: str,
    support_component: str,
    feature_family_id: str = "FAM001",
    criteria_version: str = "tier2_trace_identity_rescued_coherence",
    producer_version: str = "raw_trace_reread_tier2",
    challenge_blockers: str = "",
    dependent_context: str = "",
    raw_trace_reread_status: str = "pass",
    coherence_status: str = "pass",
    source_review_sha256: str | None = None,
    source_raw_manifest_sha256: str | None = None,
    source_candidate_subset_sha256: str | None = None,
    source_candidate_subset_count: str | None = None,
    scan_support_score: str = "0.80",
    omitted_columns: tuple[str, ...] = (),
    row_overrides: dict[str, str] | None = None,
    sidecar_columns: tuple[str, ...] | None = None,
) -> Path:
    subset = tier2_candidate_subset_signature(review_rows)
    row = {
        "feature_family_id": feature_family_id,
        "evidence_status": evidence_status,
        "support_component": support_component,
        "criteria_version": criteria_version,
        "producer_version": producer_version,
        "raw_trace_reread_status": raw_trace_reread_status,
        "seed_apex_rt": "8.000",
        "tier2_apex_rt": "8.100",
        "apex_delta_sec": "6.0",
        "scan_support_score": scan_support_score,
        "trace_scan_count": "8",
        "boundary_start_rt": "7.950",
        "boundary_end_rt": "8.050",
        "boundary_width_sec": "6.0",
        "neighbor_interference_ratio": "0.10",
        "rescued_cell_count_checked": "2",
        "rescued_cell_count_supported": "2",
        "rescued_apex_rt_span_sec": "6.0",
        "rescued_boundary_overlap_min": "0.80",
        "coherence_status": coherence_status,
        "challenge_blockers": challenge_blockers,
        "dependent_context": dependent_context,
        "source_alignment_review_sha256": (
            source_review_sha256 or source_context.review_sha256
        ),
        "source_alignment_cells_sha256": source_context.cell_sha256,
        "source_raw_manifest_sha256": (
            source_raw_manifest_sha256 or _sha256_file(raw_manifest_path)
        ),
        "source_candidate_subset_sha256": (
            source_candidate_subset_sha256 or subset.sha256
        ),
        "source_candidate_subset_count": (
            source_candidate_subset_count or str(subset.count)
        ),
        "source_expected_sample_count": "8",
        "raw_reader_runtime": "pythonnet",
        "python_executable": ".venv\\Scripts\\python.exe",
        "dll_dir": "C:\\Xcalibur\\system\\programs",
        "producer_command": "synthetic-test-fixture",
        "generated_at_utc": "2026-05-29T00:00:00Z",
    }
    row.update(_diagnostic_row_overrides())
    if row_overrides:
        row.update(row_overrides)
    columns = [
        column
        for column in (sidecar_columns or tuple(row))
        if column not in omitted_columns
    ]
    path = tmp_path / "alignment_tier2_trace_evidence.tsv"
    path.write_text(
        "\n".join(("\t".join(columns), "\t".join(row[column] for column in columns)))
        + "\n",
        encoding="utf-8",
    )
    return path


def _review_row(
    *,
    flags: str,
    detected: object,
    rescued: object,
    decision: str = "provisional_discovery",
    identity_reason: str = "insufficient_detected_identity_support",
    duplicate: object = 0,
    ambiguous: object = 0,
    independent_support: str = "",
) -> dict[str, str]:
    return {
        "feature_family_id": "FAM001",
        "neutral_loss_tag": "DNA_dR",
        "include_in_primary_matrix": "FALSE",
        "identity_decision": decision,
        "identity_confidence": "review",
        "identity_reason": identity_reason,
        "primary_evidence": "owner_complete_link",
        "quantifiable_detected_count": str(detected),
        "quantifiable_rescue_count": str(rescued),
        "review_rescue_count": "0",
        "accepted_rescue_count": str(rescued),
        "duplicate_assigned_count": str(duplicate),
        "ambiguous_ms1_owner_count": str(ambiguous),
        "row_flags": flags,
        "family_evidence": "owner_complete_link;owner_count=1",
        "independent_tier2_support_components": independent_support,
    }


def _cell_rows(
    *,
    detected: int,
    rescued: int,
    scan_support_score: str = "0.8",
    region_review_reason: str = "",
) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for index in range(detected):
        rows.append(_cell_row(index, "detected", "1000", "0.8", ""))
    for index in range(detected, detected + rescued):
        rows.append(
            _cell_row(
                index,
                "rescued",
                "500",
                scan_support_score,
                region_review_reason,
            )
        )
    return tuple(rows)


def _cell_row(
    index: int,
    status: str,
    area: str,
    scan_support_score: str,
    region_review_reason: str,
) -> dict[str, str]:
    return {
        "feature_family_id": "FAM001",
        "sample_stem": f"S{index + 1:03d}",
        "status": status,
        "area": area,
        "apex_rt": "8.00",
        "height": "100",
        "peak_start_rt": "7.95",
        "peak_end_rt": "8.05",
        "rt_delta_sec": "0.0",
        "trace_quality": "owner_backfill" if status == "rescued" else "clean",
        "scan_support_score": scan_support_score,
        "reason": status,
        "region_review_reason": region_review_reason,
    }


def _write_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    review_path = tmp_path / "alignment_review.tsv"
    cell_path = tmp_path / "alignment_cells.tsv"
    matrix_path = tmp_path / "alignment_matrix.tsv"
    review_path.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    cell_path.write_text(
        "feature_family_id\tsample_stem\nFAM001\tS001\n",
        encoding="utf-8",
    )
    matrix_path.write_text("feature_family_id\nFAM_PRIMARY\n", encoding="utf-8")
    return review_path, cell_path, matrix_path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()
