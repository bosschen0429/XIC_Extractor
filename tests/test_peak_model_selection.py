from dataclasses import replace
from pathlib import Path

from xic_extractor.peak_detection.evidence_semantics import EvidenceDecisionSemantics
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.model_selection import (
    model_select_peak_hypothesis,
    peak_hypothesis_decision_record,
)

PEAK_MODEL_SELECTION_DOC = Path("docs/product/peak-model-selection.md")


def test_characterization_map_covers_required_fixture_families() -> None:
    text = PEAK_MODEL_SELECTION_DOC.read_text(encoding="utf-8")
    required = [
        "clean single peak",
        "confidence rank",
        "role-aware RT context / ISTD",
        "strict selection RT",
        "final intensity tie-break",
        "local S/N with AsLS",
        "shape / width / morphology",
        "MS2 present / strict NL OK",
        "no MS2 / NL fail",
        "MS2 trace tie-break",
        "CWT same-apex support",
        "low-scan demotion",
        "dominant strict-NL alternative",
        "ADAP-like quality flags",
        "stale final result fallback",
        "no-candidate / no-peak fallback",
    ]

    for fixture_family in required:
        assert fixture_family in text


def test_characterization_map_documents_compatibility_coverage() -> None:
    text = PEAK_MODEL_SELECTION_DOC.read_text(encoding="utf-8")
    lower_text = text.lower()

    assert "compatibility oracle" in lower_text
    assert "successor invariant" in lower_text
    assert "same public behavior" in lower_text
    assert "tests/test_peak_scoring.py" in text
    assert "Deleting legacy scoring tests before a successor invariant" in text


def test_shadow_model_selection_reports_parity_for_legacy_selected_hypothesis() -> None:
    selected = _hypothesis(
        "selected",
        selected=True,
        confidence="HIGH",
        reason="decision: accepted",
        decision_class="accepted",
    )
    rejected = _hypothesis(
        "rejected",
        selected=False,
        confidence="LOW",
        reason="decision: review",
        decision_class="review",
    )

    result = model_select_peak_hypothesis((selected, rejected))

    assert result.selected_candidate_id == selected.hypothesis_id
    assert result.legacy_selected_candidate_id == selected.hypothesis_id
    assert result.trace_group_id == selected.trace_group_id
    assert result.decision_class == "accepted"
    assert result.selection_status == "parity"
    assert result.public_projection["confidence"] == "HIGH"
    assert result.public_projection["reason"] == "decision: accepted"
    assert result.public_projection["compatibility_labels"] == "strict_nl_ok"
    assert result.compatibility_oracle == "legacy_peak_scoring_current_oracle"
    assert result.policy_source == "selected_hypothesis_model_selection"
    assert result.evidence_comparison_policy == "limited_evidence_shadow"
    assert result.product_switch_allowed is True


def test_shadow_model_selection_blocks_no_legacy_selected_hypothesis() -> None:
    result = model_select_peak_hypothesis(
        (
            _hypothesis("first", selected=False, confidence="HIGH"),
            _hypothesis("second", selected=False, confidence="MEDIUM"),
        )
    )

    assert result.selected_candidate_id == ""
    assert result.legacy_selected_candidate_id == ""
    assert result.selection_status == "inconclusive"
    assert result.product_switch_allowed is False
    assert "missing_legacy_selected_hypothesis" in result.diff_reasons


def test_differing_successor_is_recorded_but_not_adopted() -> None:
    legacy = _hypothesis("legacy", selected=True, confidence="LOW")
    successor = _hypothesis("successor", selected=False, confidence="HIGH")

    result = model_select_peak_hypothesis((legacy, successor))

    assert result.selected_candidate_id == successor.hypothesis_id
    assert result.legacy_selected_candidate_id == legacy.hypothesis_id
    assert result.selection_status == "expected_diff"
    assert result.product_switch_allowed is False
    assert "successor_selection_not_adopted" in result.diff_reasons


def test_peak_hypothesis_decision_record_exposes_model_selection_order() -> None:
    hypothesis = _hypothesis(
        "successor",
        selected=False,
        confidence="HIGH",
        decision_class="accepted",
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
    )

    record = peak_hypothesis_decision_record(hypothesis)

    assert record.workflow == "peak_hypothesis_model_selection"
    assert record.unit_id == hypothesis.hypothesis_id
    assert record.required_evidence == (
        "peak_hypothesis",
        "integration_result",
        "evidence_vector",
        "audit_trail",
    )
    assert record.decision_class == "accepted"
    assert record.blockers == ()
    assert record.support == ("ms1_coherent", "candidate_aligned_ms2_nl")
    assert record.projection_authority == "selected_hypothesis_model_selection"
    assert [name for name, _value in record.gate] == [
        "decision_class_rank",
        "blocker_count",
    ]
    assert [name for name, _value in record.tie_break] == [
        "projected_confidence_rank",
        "negative_selection_reason_count",
        "chemical_evidence_rank",
        "selection_reference_distance",
        "legacy_selection_rank",
    ]
    assert not hasattr(record, "key")
    assert record.gate == (
        ("decision_class_rank", 0.0),
        ("blocker_count", 0.0),
    )
    assert record.tie_break == (
        ("projected_confidence_rank", 0.0),
        ("negative_selection_reason_count", -2.0),
        ("chemical_evidence_rank", 2.0),
        ("selection_reference_distance", 0.0),
        ("legacy_selection_rank", 2.0),
    )


def test_peak_hypothesis_record_ordering_ignores_legacy_score_payload() -> None:
    hypothesis = _hypothesis(
        "successor",
        selected=False,
        confidence="HIGH",
        raw_score=-999,
        decision_class="accepted",
        support_reasons=("ms1_coherent",),
    )
    adversarial = replace(
        hypothesis,
        evidence=replace(
            hypothesis.evidence,
            raw_score=999,
            support_labels=("fake_legacy_support",),
            concern_labels=("fake_legacy_concern",),
            cap_labels=("fake_legacy_cap",),
        ),
    )

    base_record = peak_hypothesis_decision_record(hypothesis)
    adversarial_record = peak_hypothesis_decision_record(adversarial)

    assert adversarial_record.gate == base_record.gate
    assert adversarial_record.tie_break == base_record.tie_break
    assert adversarial_record.support == base_record.support
    assert adversarial_record.blockers == base_record.blockers


def test_blocked_and_inconclusive_statuses_cannot_product_switch() -> None:
    legacy = _hypothesis("legacy", selected=True, confidence="HIGH")
    successor = _hypothesis("successor", selected=False, confidence="HIGH")

    blocked = model_select_peak_hypothesis(
        (legacy, successor),
        force_selection_status="blocked_diff",
        diff_reasons=("unexplained_successor_mismatch",),
    )
    inconclusive = model_select_peak_hypothesis(
        (legacy, successor),
        force_selection_status="inconclusive",
        diff_reasons=("candidate_evidence_incomplete",),
    )

    assert blocked.product_switch_allowed is False
    assert inconclusive.product_switch_allowed is False


def test_forced_parity_mismatch_cannot_product_switch() -> None:
    legacy = _hypothesis("legacy", selected=True, confidence="LOW")
    successor = _hypothesis("successor", selected=False, confidence="HIGH")

    result = model_select_peak_hypothesis(
        (legacy, successor),
        force_selection_status="parity",
    )

    assert result.selection_status == "parity"
    assert result.product_switch_allowed is False
    assert "parity_selected_candidate_mismatch" in result.diff_reasons
    assert "parity_public_projection_mismatch" in result.diff_reasons


def _hypothesis(
    suffix: str,
    *,
    selected: bool,
    confidence: str = "HIGH",
    reason: str = "decision: accepted",
    decision_class: str = "accepted",
    support_reasons: tuple[str, ...] = ("ms1_coherent",),
    conflict_reasons: tuple[str, ...] = (),
    review_reasons: tuple[str, ...] = (),
    raw_score: int | None = None,
    area: float = 1234.0,
) -> PeakHypothesis:
    return PeakHypothesis(
        hypothesis_id=f"SampleA|Analyte|{suffix}",
        trace_group_id="SampleA|Analyte|trace",
        target_label="Analyte",
        role="Analyte",
        istd_pair="ISTD",
        analysis_mode="targeted",
        resolver_mode="local_minimum",
        integration=IntegrationResult(
            rt_left_min=8.4,
            rt_apex_min=8.5,
            rt_right_min=8.6,
            raw_apex_rt_min=8.5,
            rt_width_min=0.2,
            height_raw=1200.0,
            height_smoothed=1100.0,
            area_raw_counts_seconds=area,
        ),
        evidence=EvidenceVector(
            confidence=confidence,
            raw_score=raw_score,
            reason=reason,
            decision_semantics=EvidenceDecisionSemantics(
                decision_class=decision_class,
                support_reasons=support_reasons,
                conflict_reasons=conflict_reasons,
                review_reasons=review_reasons,
                compatibility_labels=("strict_nl_ok",),
            ),
        ),
        audit=AuditTrail(
            selected=selected,
            selection_rank=1 if selected else 2,
            selection_reference_rt_min=8.5,
        ),
    )
