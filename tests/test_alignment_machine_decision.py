from __future__ import annotations

from xic_extractor.alignment.machine_decision import project_machine_decision
from xic_extractor.alignment.promotion_policy import BACKFILL_HYPOTHESIS_BLOCKED_REASON


def test_generic_review_backfill_is_unavailable_not_primary_use() -> None:
    review_row = _review_row(
        include="TRUE",
        decision="production_family",
        confidence="high",
        reason="owner_complete_link",
        primary_evidence="legacy_unrehydrated_primary_evidence",
        detected=2,
        rescued=0,
    )
    review_row["review_backfill_count"] = "1"

    vector = project_machine_decision(
        review_row,
        _cell_rows(detected=2, rescued=1),
    )

    assert vector.matrix_role == "audit"
    assert vector.recommended_action == "review"
    assert vector.blockers == ("backfill_owner_rehydration_unavailable",)
    assert "ms1_backfill_supported" not in vector.support_reasons
    assert "rt_coherent" not in vector.support_reasons


def test_wrong_hypothesis_cell_blocker_is_exposed_in_machine_decision() -> None:
    vector = project_machine_decision(
        _review_row(
            include="FALSE",
            decision="provisional_discovery",
            confidence="review",
            reason="cell_evidence_supported_backfill",
            primary_evidence="owner_complete_link",
            detected=2,
            rescued=1,
            flags="backfill_cell_evidence_required;backfill_rescue_review_only",
        ),
        _cell_rows(
            detected=2,
            rescued=1,
            rescue_group_claim_state="duplicate_loser",
        ),
    )

    assert BACKFILL_HYPOTHESIS_BLOCKED_REASON in vector.blockers
    assert "low_ms1_assessable_coverage_blocked" not in vector.blockers
    assert vector.matrix_role == "audit"
    assert vector.recommended_action == "review"


def test_rescue_only_row_projects_to_exclude_with_explicit_blocker() -> None:
    vector = project_machine_decision(
        _review_row(
            include="FALSE",
            decision="audit_family",
            confidence="review",
            reason="rescue_only_blocked",
            primary_evidence="owner_complete_link",
            detected=0,
            rescued=2,
            flags="rescue_only;rescue_only_review",
        ),
        _cell_rows(detected=0, rescued=2),
    )

    assert vector.matrix_role == "excluded"
    assert vector.recommended_action == "exclude"
    assert vector.blockers == ("rescue_only_blocked", "rescue_only")


def test_ambiguous_owner_row_projects_to_audit_review() -> None:
    vector = project_machine_decision(
        _review_row(
            include="FALSE",
            decision="audit_family",
            confidence="review",
            reason="ambiguous_only",
            primary_evidence="none",
            detected=0,
            rescued=0,
            ambiguous=2,
            flags="ambiguous_only;ambiguous_ms1_owner_pressure;zero_present",
        ),
        _cell_rows(detected=0, rescued=0, ambiguous=2),
    )

    assert vector.matrix_role == "audit"
    assert vector.recommended_action == "review"
    assert vector.blockers == (
        "ambiguous_only",
        "ambiguous_ms1_owner_pressure",
        "zero_present",
    )


def test_area_only_rescue_does_not_claim_ms1_backfill_support() -> None:
    vector = project_machine_decision(
        _review_row(
            include="FALSE",
            decision="provisional_discovery",
            confidence="review",
            reason="insufficient_detected_identity_support",
            primary_evidence="owner_complete_link",
            detected=1,
            rescued=2,
            flags="single_detected_seed",
        ),
        _cell_rows(
            detected=1,
            rescued=2,
            rescue_trace_quality="owner_backfill",
            rescue_scan_support_score="",
        ),
    )

    assert vector.matrix_role == "audit"
    assert vector.recommended_action == "review"
    assert "ms1_backfill_supported" not in vector.support_reasons
    assert "low_ms1_assessable_coverage_blocked" in vector.blockers


def test_neighboring_interference_rescue_does_not_claim_ms1_backfill_support() -> None:
    vector = project_machine_decision(
        _review_row(
            include="FALSE",
            decision="provisional_discovery",
            confidence="review",
            reason="insufficient_detected_identity_support",
            primary_evidence="owner_complete_link",
            detected=1,
            rescued=2,
            flags="single_detected_seed",
        ),
        _cell_rows(
            detected=1,
            rescued=2,
            rescue_region_review_reason="neighboring_ms1_interference",
        ),
    )

    assert vector.matrix_role == "audit"
    assert vector.recommended_action == "review"
    assert "ms1_backfill_supported" not in vector.support_reasons
    assert "neighboring_ms1_interference_blocked" in vector.blockers


def test_low_scan_support_rescue_does_not_claim_ms1_backfill_support() -> None:
    vector = project_machine_decision(
        _review_row(
            include="FALSE",
            decision="provisional_discovery",
            confidence="review",
            reason="insufficient_detected_identity_support",
            primary_evidence="owner_complete_link",
            detected=1,
            rescued=2,
            flags="single_detected_seed",
        ),
        _cell_rows(detected=1, rescued=2, rescue_scan_support_score="0.1"),
    )

    assert vector.matrix_role == "audit"
    assert vector.recommended_action == "review"
    assert "ms1_backfill_supported" not in vector.support_reasons
    assert "low_ms1_assessable_coverage_blocked" in vector.blockers


def test_generic_provisional_label_gets_explicit_missing_support_blocker() -> None:
    vector = project_machine_decision(
        _review_row(
            include="FALSE",
            decision="provisional_discovery",
            confidence="review",
            reason="",
            primary_evidence="single_sample_local_owner",
            detected=1,
            rescued=0,
        ),
        _cell_rows(detected=1, rescued=0),
    )

    assert vector.matrix_role == "provisional"
    assert vector.recommended_action == "keep_provisional"
    assert vector.blockers == (
        "single_detected_seed",
        "insufficient_identity_support",
    )


def _review_row(
    *,
    include: str,
    decision: str,
    confidence: str,
    reason: str,
    primary_evidence: str,
    detected: int,
    rescued: int,
    duplicate: int = 0,
    ambiguous: int = 0,
    flags: str = "",
) -> dict[str, str]:
    return {
        "feature_family_id": "FAM001",
        "include_in_primary_matrix": include,
        "identity_decision": decision,
        "identity_confidence": confidence,
        "identity_reason": reason,
        "primary_evidence": primary_evidence,
        "neutral_loss_tag": "DNA_dR",
        "quantifiable_detected_count": str(detected),
        "quantifiable_backfill_count": str(rescued),
        "review_backfill_count": "0",
        "accepted_backfill_count": str(rescued),
        "duplicate_assigned_count": str(duplicate),
        "ambiguous_ms1_owner_count": str(ambiguous),
        "row_flags": flags,
    }


def _cell_rows(
    *,
    detected: int,
    rescued: int,
    duplicate: int = 0,
    ambiguous: int = 0,
    rescue_trace_quality: str = "clean",
    rescue_scan_support_score: str = "0.8",
    rescue_reason: str = "backfilled",
    rescue_region_review_reason: str = "",
    rescue_rt_delta_sec: str = "0.0",
    rescue_group_claim_state: str = "",
) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for index in range(detected):
        rows.append(_cell_row(index, "detected", "1000"))
    for index in range(detected, detected + rescued):
        rows.append(
            _cell_row(
                index,
                "backfilled",
                "500",
                trace_quality=rescue_trace_quality,
                scan_support_score=rescue_scan_support_score,
                reason=rescue_reason,
                region_review_reason=rescue_region_review_reason,
                rt_delta_sec=rescue_rt_delta_sec,
                group_claim_state=rescue_group_claim_state,
            ),
        )
    for index in range(detected + rescued, detected + rescued + duplicate):
        rows.append(_cell_row(index, "duplicate_assigned", ""))
    for index in range(
        detected + rescued + duplicate,
        detected + rescued + duplicate + ambiguous,
    ):
        rows.append(_cell_row(index, "ambiguous_ms1_owner", ""))
    return tuple(rows)


def _cell_row(
    index: int,
    status: str,
    area: str,
    *,
    trace_quality: str | None = None,
    scan_support_score: str | None = None,
    reason: str | None = None,
    region_review_reason: str = "",
    rt_delta_sec: str = "0.0",
    group_claim_state: str = "",
) -> dict[str, str]:
    row = {
        "feature_family_id": "FAM001",
        "sample_stem": f"S{index + 1:03d}",
        "status": status,
        "area": area,
        "apex_rt": "8.0" if area else "",
        "height": "100" if area else "",
        "peak_start_rt": "7.95" if area else "",
        "peak_end_rt": "8.05" if area else "",
        "rt_delta_sec": rt_delta_sec if area else "",
        "trace_quality": (
            trace_quality
            if trace_quality is not None
            else ("clean" if area else status)
        ),
        "scan_support_score": (
            scan_support_score
            if scan_support_score is not None
            else ("0.8" if area else "")
        ),
        "reason": reason if reason is not None else status,
        "region_review_reason": region_review_reason,
        "group_claim_state": group_claim_state,
    }
    return row
