from pathlib import Path

import pytest

from xic_extractor.alignment.backfill_evidence_projection import (
    BACKFILL_PROJECTION_COLUMNS,
    PRODUCT_AUTHORITY_SCOPE_FIELD,
    PRODUCT_AUTHORITY_SOURCE_FIELD,
    PRODUCT_AUTHORITY_STATUS_FIELD,
    PRODUCT_AUTHORIZED_SCOPE,
    PRODUCT_AUTHORIZED_STATUS,
    load_candidate_ms2_pattern_rows,
    project_backfill_evidence_to_cells,
)
from xic_extractor.alignment.promotion_policy import (
    ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
    BACKFILL_HYPOTHESIS_BLOCKED_REASON,
    BACKFILL_MS1_PATTERN_BLOCKED_REASON,
    classify_backfill_promotion,
    evidence_from_tsv_rows,
)


def test_backfill_projection_maps_sidecar_evidence_into_supported_rescue_cell() -> None:
    rows = project_backfill_evidence_to_cells(
        cell_rows=[
            _cell_row("FAM_DROPOUT", "S1", status="detected"),
            _cell_row(
                "FAM_DROPOUT",
                "S2",
                status="rescued",
                rt_delta_sec="120",
            ),
        ],
        candidate_ms2_pattern_rows=[
            {
                "feature_family_id": "FAM_DROPOUT",
                "sample_stem": "S1",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "5",
                "raw_ms2_strict_nl_scan_count": "2",
                "raw_ms2_trace_strength": "strong",
                "diagnostic_only": "FALSE",
            },
            {
                "feature_family_id": "FAM_DROPOUT",
                "sample_stem": "S2",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "3",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "moderate",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            },
        ],
        ms1_pattern_coherence_rows=[
            {
                "feature_family_id": "FAM_DROPOUT",
                "sample_stem": "S2",
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "trace_constellation",
                "reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
        matrix_rt_drift_policy_rows=[
            {
                "feature_family_id": "FAM_DROPOUT",
                "sample_stem": "S2",
                "matrix_rt_drift_status": "drift_supported",
                "drift_evidence_level": "matrix_reference_aligned",
                "drift_corrected_delta_sec": "20",
                "drift_compatible_status": "compatible",
                "reason": "istd_drift_explains_rt_delta",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
    )

    rescued = _row(rows, "FAM_DROPOUT", "S2")
    assert rescued["backfill_ms1_pattern_status"] == "supportive"
    assert rescued["backfill_ms1_pattern_evidence_level"] == "trace_constellation"
    assert rescued["backfill_matrix_rt_drift_status"] == "drift_supported"
    assert rescued["backfill_drift_evidence_level"] == "matrix_reference_aligned"
    assert rescued["backfill_drift_corrected_delta_sec"] == "20"
    assert rescued["backfill_candidate_ms2_pattern_status"] == "supportive"
    assert (
        rescued["backfill_candidate_ms2_evidence_level"]
        == "sample_candidate_aligned"
    )
    assert rescued["backfill_ms2_trigger_scan_count"] == "3"
    assert rescued["backfill_strict_nl_scan_count"] == "1"
    assert rescued["backfill_ms2_trace_strength"] == "moderate"
    assert rescued["backfill_dda_missing_nl_policy_status"] == ""
    assert rescued["backfill_family_ms2_required_tag_status"] == ""
    assert "ms1_pattern_coherence" in rescued["backfill_evidence_reason"]
    assert ANCHOR_OWN_MAX_MS1_SUPPORT_REASON in rescued["backfill_evidence_reason"]
    assert "candidate_ms2_pattern" in rescued["backfill_evidence_reason"]

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_rescue_count": "1",
        },
        [rescued],
        seed_quality=None,
        sample_count=3,
    )
    assert evidence.cells[0].supported_for_backfill

    detected = _row(rows, "FAM_DROPOUT", "S1")
    assert all(detected[column] == "" for column in BACKFILL_PROJECTION_COLUMNS)


def test_legacy_ms1_pattern_constellation_is_context_not_product_support() -> None:
    rows = project_backfill_evidence_to_cells(
        cell_rows=[
            _cell_row("FAM_LEGACY", "S1", status="detected"),
            _cell_row("FAM_LEGACY", "S2", status="rescued"),
        ],
        candidate_ms2_pattern_rows=[
            {
                "feature_family_id": "FAM_LEGACY",
                "sample_stem": "S2",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "5",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "strong",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
        ms1_pattern_coherence_rows=[
            {
                "feature_family_id": "FAM_LEGACY",
                "sample_stem": "S2",
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "sample_constellation",
                "reason": "gaussian15_ms1_pattern_coherent",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
    )

    rescued = _row(rows, "FAM_LEGACY", "S2")
    assert "gaussian15_ms1_pattern_coherent" in rescued["backfill_evidence_reason"]

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_rescue_count": "1",
        },
        [rescued],
        seed_quality=None,
        sample_count=3,
    )

    assert not evidence.cells[0].supported_for_backfill
    assert evidence.cells[0].backfill_identity_block_reason == (
        BACKFILL_MS1_PATTERN_BLOCKED_REASON
    )


def test_anchor_reason_requires_trace_constellation_level() -> None:
    rows = project_backfill_evidence_to_cells(
        cell_rows=[
            _cell_row("FAM_WRONG_LEVEL", "S1", status="detected"),
            _cell_row("FAM_WRONG_LEVEL", "S2", status="rescued"),
        ],
        candidate_ms2_pattern_rows=[
            {
                "feature_family_id": "FAM_WRONG_LEVEL",
                "sample_stem": "S2",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "5",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "strong",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
        ms1_pattern_coherence_rows=[
            {
                "feature_family_id": "FAM_WRONG_LEVEL",
                "sample_stem": "S2",
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "sample_constellation",
                "reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
    )

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_rescue_count": "1",
        },
        [_row(rows, "FAM_WRONG_LEVEL", "S2")],
        seed_quality=None,
        sample_count=3,
    )

    assert not evidence.cells[0].supported_for_backfill
    assert evidence.cells[0].backfill_identity_block_reason == (
        BACKFILL_MS1_PATTERN_BLOCKED_REASON
    )


def test_diagnostic_only_ms1_pattern_is_not_product_support() -> None:
    rows = project_backfill_evidence_to_cells(
        cell_rows=[
            _cell_row("FAM_DIAGNOSTIC_MS1", "S1", status="detected"),
            _cell_row("FAM_DIAGNOSTIC_MS1", "S2", status="rescued"),
        ],
        candidate_ms2_pattern_rows=[
            {
                "feature_family_id": "FAM_DIAGNOSTIC_MS1",
                "sample_stem": "S2",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "5",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "strong",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
        ms1_pattern_coherence_rows=[
            {
                "feature_family_id": "FAM_DIAGNOSTIC_MS1",
                "sample_stem": "S2",
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "trace_constellation",
                "reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
                "diagnostic_only": "TRUE",
            }
        ],
    )

    rescued = _row(rows, "FAM_DIAGNOSTIC_MS1", "S2")
    assert rescued["backfill_ms1_pattern_status"] == ""
    assert ANCHOR_OWN_MAX_MS1_SUPPORT_REASON not in (
        rescued["backfill_evidence_reason"]
    )

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_rescue_count": "1",
        },
        [rescued],
        seed_quality=None,
        sample_count=3,
    )

    assert not evidence.cells[0].supported_for_backfill
    assert evidence.cells[0].backfill_identity_block_reason == (
        BACKFILL_MS1_PATTERN_BLOCKED_REASON
    )


def test_diagnostic_only_qc_reference_is_not_product_ms1_support() -> None:
    rows = project_backfill_evidence_to_cells(
        cell_rows=[
            _cell_row("FAM_DIAGNOSTIC_QC", "S1", status="detected"),
            _cell_row("FAM_DIAGNOSTIC_QC", "S2", status="rescued"),
        ],
        candidate_ms2_pattern_rows=[
            {
                "feature_family_id": "FAM_DIAGNOSTIC_QC",
                "sample_stem": "S2",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "5",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "strong",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
        qc_ms1_pattern_reference_rows=[
            {
                "feature_family_id": "FAM_DIAGNOSTIC_QC",
                "sample_stem": "S2",
                "qc_reference_status": "supportive",
                "qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
                "reason": "local_qc_overlay_supports_peak",
                "diagnostic_only": "TRUE",
            }
        ],
    )

    rescued = _row(rows, "FAM_DIAGNOSTIC_QC", "S2")
    assert rescued["backfill_qc_reference_status"] == ""

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_rescue_count": "1",
        },
        [rescued],
        seed_quality=None,
        sample_count=3,
    )

    assert not evidence.cells[0].supported_for_backfill
    assert evidence.cells[0].backfill_identity_block_reason == (
        BACKFILL_MS1_PATTERN_BLOCKED_REASON
    )


def test_product_authorized_qc_reference_does_not_replace_anchor_ms1() -> None:
    rows = project_backfill_evidence_to_cells(
        cell_rows=[
            _cell_row("FAM_QC_ONLY", "S1", status="detected"),
            _cell_row("FAM_QC_ONLY", "S2", status="rescued"),
        ],
        candidate_ms2_pattern_rows=[
            {
                "feature_family_id": "FAM_QC_ONLY",
                "sample_stem": "S2",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "5",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "strong",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
        qc_ms1_pattern_reference_rows=[
            {
                "feature_family_id": "FAM_QC_ONLY",
                "sample_stem": "S2",
                "qc_reference_status": "supportive",
                "qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
                "reason": "local_qc_overlay_supports_peak",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
    )

    rescued = _row(rows, "FAM_QC_ONLY", "S2")
    assert rescued["backfill_qc_reference_status"] == "supportive"

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_rescue_count": "1",
        },
        [rescued],
        seed_quality=None,
        sample_count=3,
    )

    assert not evidence.cells[0].supported_for_backfill
    assert evidence.cells[0].backfill_identity_block_reason == (
        BACKFILL_MS1_PATTERN_BLOCKED_REASON
    )


def test_ms1_projection_drops_stale_anchor_reason_before_current_sidecar() -> None:
    rows = project_backfill_evidence_to_cells(
        cell_rows=[
            _cell_row("FAM_STALE", "S1", status="detected"),
            {
                **_cell_row("FAM_STALE", "S2", status="rescued"),
                "backfill_ms1_pattern_status": "supportive",
                "backfill_ms1_pattern_evidence_level": "trace_constellation",
                "backfill_evidence_reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
            },
        ],
        candidate_ms2_pattern_rows=[
            {
                "feature_family_id": "FAM_STALE",
                "sample_stem": "S2",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "5",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "strong",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
        ms1_pattern_coherence_rows=[
            {
                "feature_family_id": "FAM_STALE",
                "sample_stem": "S2",
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "trace_constellation",
                "reason": "gaussian15_ms1_pattern_coherent",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
    )

    rescued = _row(rows, "FAM_STALE", "S2")
    assert ANCHOR_OWN_MAX_MS1_SUPPORT_REASON not in (
        rescued["backfill_evidence_reason"]
    )
    assert "gaussian15_ms1_pattern_coherent" in rescued["backfill_evidence_reason"]

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_rescue_count": "1",
        },
        [rescued],
        seed_quality=None,
        sample_count=3,
    )

    assert not evidence.cells[0].supported_for_backfill
    assert evidence.cells[0].backfill_identity_block_reason == (
        BACKFILL_MS1_PATTERN_BLOCKED_REASON
    )


def test_diagnostic_only_candidate_ms2_does_not_block_ms1_product_support() -> None:
    rows = project_backfill_evidence_to_cells(
        cell_rows=[
            _cell_row("FAM_DIAGNOSTIC_MS2", "S1", status="detected"),
            {
                **_cell_row("FAM_DIAGNOSTIC_MS2", "S2", status="rescued"),
                "backfill_candidate_ms2_pattern_status": "supportive",
                "backfill_candidate_ms2_evidence_level": "sample_candidate_aligned",
                "backfill_dda_missing_nl_policy_status": "not_dispositive",
                "backfill_family_ms2_required_tag_status": "observed_in_family",
            },
        ],
        candidate_ms2_pattern_rows=[
            {
                "feature_family_id": "FAM_DIAGNOSTIC_MS2",
                "sample_stem": "S2",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "5",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "strong",
                "diagnostic_only": "TRUE",
            },
            {
                "feature_family_id": "FAM_DIAGNOSTIC_MS2",
                "sample_stem": "S1",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "5",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "strong",
                "diagnostic_only": "TRUE",
            },
        ],
        ms1_pattern_coherence_rows=[
            {
                "feature_family_id": "FAM_DIAGNOSTIC_MS2",
                "sample_stem": "S2",
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "trace_constellation",
                "reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
    )

    rescued = _row(rows, "FAM_DIAGNOSTIC_MS2", "S2")
    assert rescued["backfill_candidate_ms2_pattern_status"] == ""
    assert rescued["backfill_dda_missing_nl_policy_status"] == ""
    assert rescued["backfill_family_ms2_required_tag_status"] == ""

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_rescue_count": "1",
        },
        [rescued],
        seed_quality=None,
        sample_count=3,
    )

    assert not evidence.cells[0].ms2_context_supported
    assert evidence.cells[0].supported_for_backfill
    assert evidence.cells[0].backfill_identity_block_reason == ""


def test_backfill_projection_is_fail_closed_when_sidecars_are_missing() -> None:
    rows = project_backfill_evidence_to_cells(
        cell_rows=[
            {
                **_cell_row("FAM_MISSING", "S2", status="rescued"),
                "backfill_ms1_pattern_status": "supportive",
                "backfill_ms1_pattern_evidence_level": "trace_constellation",
                "backfill_candidate_ms2_pattern_status": "supportive",
                "backfill_candidate_ms2_evidence_level": "sample_candidate_aligned",
                "backfill_evidence_reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
            }
        ]
    )

    row = rows[0]
    assert all(column in row for column in BACKFILL_PROJECTION_COLUMNS)
    assert all(row[column] == "" for column in BACKFILL_PROJECTION_COLUMNS)

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_rescue_count": "1",
        },
        rows,
        seed_quality=None,
        sample_count=3,
    )
    assert not evidence.cells[0].supported_for_backfill
    assert evidence.cells[0].backfill_identity_block_reason != ""


def test_generic_review_rescue_count_requires_owner_rehydration() -> None:
    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "quantifiable_detected_count": "2",
            "quantifiable_rescue_count": "0",
            "review_rescue_count": "1",
        },
        [_cell_row("FAM_REVIEW", "S2", status="rescued")],
        seed_quality=None,
        sample_count=3,
    )

    decision = classify_backfill_promotion(evidence)

    assert evidence.held_rescue_count == 0
    assert evidence.unrehydrated_review_rescue_count == 1
    assert decision.state == "unavailable"
    assert decision.reason == "backfill_owner_rehydration_unavailable"


def test_naked_backfill_support_fields_without_authority_do_not_promote() -> None:
    row = {
        **_cell_row("FAM_NAKED", "S2", status="rescued"),
        "backfill_ms1_pattern_status": "supportive",
        "backfill_ms1_pattern_evidence_level": "trace_constellation",
        "backfill_candidate_ms2_pattern_status": "supportive",
        "backfill_candidate_ms2_evidence_level": "sample_candidate_aligned",
        "backfill_evidence_reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
    }

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_rescue_count": "1",
        },
        [row],
        seed_quality=None,
        sample_count=3,
    )

    assert not evidence.cells[0].same_peak_ms1_pattern_supported
    assert not evidence.cells[0].ms2_context_supported
    assert not evidence.cells[0].supported_for_backfill
    assert evidence.cells[0].backfill_identity_block_reason == (
        BACKFILL_MS1_PATTERN_BLOCKED_REASON
    )


def test_same_peak_support_does_not_override_duplicate_loser_claim() -> None:
    row = {
        **_cell_row("FAM_DUP_LOSER", "S2", status="rescued"),
        "group_claim_state": "duplicate_loser",
        "claim_winner_group_hypothesis_id": "FAM_WINNER",
        "claim_source_group_hypothesis_id": "FAM_DUP_LOSER",
        "backfill_ms1_pattern_status": "supportive",
        "backfill_ms1_pattern_evidence_level": "trace_constellation",
        "backfill_evidence_reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
        "backfill_ms1_product_authority_status": PRODUCT_AUTHORIZED_STATUS,
        "backfill_ms1_product_authority_scope": PRODUCT_AUTHORIZED_SCOPE,
        "backfill_ms1_product_authority_source": "unit_test_reviewed_allowlist",
    }

    evidence = evidence_from_tsv_rows(
        {
            "neutral_loss_tag": "DNA_dR",
            "primary_evidence": "owner_complete_link",
            "detected_count": "2",
            "accepted_rescue_count": "1",
        },
        [row],
        seed_quality=None,
        sample_count=3,
    )

    assert evidence.cells[0].same_peak_ms1_pattern_supported
    assert not evidence.cells[0].supported_for_backfill
    assert (
        evidence.cells[0].backfill_identity_block_reason
        == BACKFILL_HYPOTHESIS_BLOCKED_REASON
    )


def test_candidate_ms2_pattern_loader_requires_diagnostic_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate_ms2_pattern.tsv"
    path.write_text(
        "\t".join(
            (
                "feature_family_id",
                "sample_stem",
                "candidate_ms2_pattern_status",
                "candidate_ms2_evidence_level",
            )
        )
        + "\n"
        + "\t".join(
            ("FAM_MISSING_SCHEMA", "S2", "supportive", "sample_candidate_aligned")
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required columns"):
        load_candidate_ms2_pattern_rows(path)


def test_diagnostic_false_without_product_authority_is_not_product_support() -> None:
    rows = project_backfill_evidence_to_cells(
        cell_rows=[
            _cell_row("FAM_NO_AUTHORITY", "S1", status="detected"),
            _cell_row("FAM_NO_AUTHORITY", "S2", status="rescued"),
        ],
        candidate_ms2_pattern_rows=[
            {
                "feature_family_id": "FAM_NO_AUTHORITY",
                "sample_stem": "S2",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "5",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "strong",
                "diagnostic_only": "FALSE",
            }
        ],
        ms1_pattern_coherence_rows=[
            {
                "feature_family_id": "FAM_NO_AUTHORITY",
                "sample_stem": "S2",
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "trace_constellation",
                "reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
                "diagnostic_only": "FALSE",
            }
        ],
    )

    rescued = _row(rows, "FAM_NO_AUTHORITY", "S2")
    assert rescued["backfill_ms1_pattern_status"] == ""
    assert rescued["backfill_candidate_ms2_pattern_status"] == ""
    assert rescued["backfill_evidence_reason"] == ""


def test_duplicate_product_sidecar_key_is_rejected() -> None:
    sidecar_row = {
        "feature_family_id": "FAM_DUP_AUTH",
        "sample_stem": "S2",
        "ms1_pattern_status": "supportive",
        "ms1_pattern_evidence_level": "trace_constellation",
        "reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
        "diagnostic_only": "FALSE",
        **_product_authority(),
    }

    with pytest.raises(
        ValueError,
        match="duplicate ms1_pattern_coherence backfill evidence sidecar key",
    ):
        project_backfill_evidence_to_cells(
            cell_rows=[_cell_row("FAM_DUP_AUTH", "S2", status="rescued")],
            ms1_pattern_coherence_rows=[sidecar_row, sidecar_row],
        )


def _cell_row(
    family_id: str,
    sample_stem: str,
    *,
    status: str,
    rt_delta_sec: str = "30",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "status": status,
        "primary_matrix_area": "123",
        "apex_rt": "10.0",
        "height": "40000",
        "peak_start_rt": "9.8",
        "peak_end_rt": "10.2",
        "rt_delta_sec": rt_delta_sec,
        "scan_support_score": "0.8",
    }


def _row(
    rows: tuple[dict[str, str], ...],
    family_id: str,
    sample_stem: str,
) -> dict[str, str]:
    return next(
        row
        for row in rows
        if row["feature_family_id"] == family_id and row["sample_stem"] == sample_stem
    )


def _product_authority() -> dict[str, str]:
    return {
        PRODUCT_AUTHORITY_STATUS_FIELD: PRODUCT_AUTHORIZED_STATUS,
        PRODUCT_AUTHORITY_SCOPE_FIELD: PRODUCT_AUTHORIZED_SCOPE,
        PRODUCT_AUTHORITY_SOURCE_FIELD: "unit_test_reviewed_allowlist",
    }
