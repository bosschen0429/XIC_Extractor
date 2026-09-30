from __future__ import annotations

from pathlib import Path

from xic_extractor.diagnostics.shared_peak_identity_explanation import (
    machine_evidence_support,
    mode_hypothesis_assignment,
)


def test_typed_mode_hypothesis_assignment_selects_tag_bearing_irt_mode() -> None:
    rows = mode_hypothesis_assignment.build_mode_hypothesis_assignment_rows(
        rt_mode_rows=[
            _rt_mode_row("FAM011810", "S_TAG", mode_id="irt_blue_core"),
            _rt_mode_row("FAM011810", "S_DDA", mode_id="irt_blue_core"),
            _rt_mode_row(
                "FAM011810",
                "S_WRONG",
                mode_id="irt_green_core",
                role="non_tag_outlier",
                tag_status="no_tag_observed",
            ),
        ],
        candidate_ms2_pattern_rows=[
            _candidate_ms2_row("FAM011810", "S_TAG", tag_observed=True),
            _candidate_ms2_row("FAM011810", "S_DDA", dda_missing=True),
            _candidate_ms2_row("FAM011810", "S_WRONG", dda_missing=True),
        ],
        ms1_pattern_coherence_rows=[
            _ms1_row("FAM011810", "S_TAG"),
            _ms1_row("FAM011810", "S_DDA"),
            _ms1_row("FAM011810", "S_WRONG"),
        ],
        qc_ms1_pattern_reference_rows=[
            _qc_row("FAM011810", "S_TAG"),
            _qc_row("FAM011810", "S_DDA"),
            _qc_row("FAM011810", "S_WRONG"),
        ],
        matrix_rt_drift_policy_rows=[
            _rt_drift_row("FAM011810", "S_TAG"),
            _rt_drift_row("FAM011810", "S_DDA"),
            _rt_drift_row("FAM011810", "S_WRONG"),
        ],
    )

    by_sample = {row["sample_stem"]: row for row in rows}
    assert by_sample["S_TAG"]["peak_hypothesis_id"] == "FAM011810::irt_blue_core"
    assert by_sample["S_TAG"]["peak_hypothesis_status"] == "product_candidate_core"
    assert by_sample["S_DDA"]["peak_hypothesis_status"] == "product_candidate_core"
    assert by_sample["S_DDA"]["reason"] == (
        "typed_mode_hypothesis_assignment_supported_by_mode_tag_and_dda_opportunity"
    )
    assert by_sample["S_WRONG"]["peak_hypothesis_id"] == (
        "FAM011810::irt_green_core"
    )
    assert by_sample["S_WRONG"]["peak_hypothesis_status"] == (
        "cross_mode_rescue_blocked"
    )
    assert by_sample["S_WRONG"]["selected_mode_tag_status"] == "no_tag_observed"
    assert by_sample["S_WRONG"]["product_selection_blocker"] == "cross_mode_rescue"


def test_typed_assignment_keeps_raw_selected_mode_review_only() -> None:
    rows = mode_hypothesis_assignment.build_mode_hypothesis_assignment_rows(
        rt_mode_rows=[
            _rt_mode_row(
                "FAM001473",
                "S1",
                mode_id="raw_mode_1_19.27min",
                evidence_level="raw_selected_apex_modes",
            ),
        ],
        candidate_ms2_pattern_rows=[
            _candidate_ms2_row("FAM001473", "S1", tag_observed=True),
        ],
        ms1_pattern_coherence_rows=[_ms1_row("FAM001473", "S1")],
        qc_ms1_pattern_reference_rows=[_qc_row("FAM001473", "S1")],
        matrix_rt_drift_policy_rows=[_rt_drift_row("FAM001473", "S1")],
    )

    row = rows[0]
    assert row["peak_hypothesis_status"] == "raw_mode_review_only"
    assert row["product_selection_action"] == "require_raw_mode_review"
    assert row["product_selection_blocker"] == "raw_mode_review_only"
    assert row["reason"] == "raw_mode_requires_typed_irt_mode_hypothesis"


def test_typed_assignment_treats_missing_qc_as_context_not_hard_gate() -> None:
    rows = mode_hypothesis_assignment.build_mode_hypothesis_assignment_rows(
        rt_mode_rows=[
            _rt_mode_row("FAM_QC_CONTEXT", "S1", mode_id="irt_blue_core"),
        ],
        candidate_ms2_pattern_rows=[
            _candidate_ms2_row("FAM_QC_CONTEXT", "S1", tag_observed=True),
        ],
        ms1_pattern_coherence_rows=[_ms1_row("FAM_QC_CONTEXT", "S1")],
        qc_ms1_pattern_reference_rows=[],
        matrix_rt_drift_policy_rows=[_rt_drift_row("FAM_QC_CONTEXT", "S1")],
    )

    row = rows[0]
    assert row["peak_hypothesis_status"] == "product_candidate_core"
    assert row["product_selection_blocker"] == "none"
    assert row["reason"] == (
        "typed_mode_hypothesis_assignment_supported_by_mode_tag"
        "_and_sample_required_tag"
    )


def test_typed_assignment_blocks_family_without_any_required_tag() -> None:
    rows = mode_hypothesis_assignment.build_mode_hypothesis_assignment_rows(
        rt_mode_rows=[
            _rt_mode_row("FAM_NO_TAG", "S1", mode_id="irt_blue_core"),
        ],
        candidate_ms2_pattern_rows=[
            _candidate_ms2_row("FAM_NO_TAG", "S1", tag_observed=False),
        ],
        ms1_pattern_coherence_rows=[_ms1_row("FAM_NO_TAG", "S1")],
        qc_ms1_pattern_reference_rows=[_qc_row("FAM_NO_TAG", "S1")],
        matrix_rt_drift_policy_rows=[_rt_drift_row("FAM_NO_TAG", "S1")],
    )

    row = rows[0]
    assert row["peak_hypothesis_status"] == "consolidation_no_go"
    assert row["product_selection_action"] == "block_family_promotion"
    assert row["product_selection_blocker"] == "consolidation_no_go"
    assert row["reason"] == "family_required_tag_not_observed"


def test_typed_assignment_writer_matches_machine_support_loader(tmp_path: Path) -> None:
    output = tmp_path / "typed_peak_hypothesis_selection.tsv"
    rows = mode_hypothesis_assignment.build_mode_hypothesis_assignment_rows(
        rt_mode_rows=[_rt_mode_row("FAM_WRITE", "S1", mode_id="irt_blue_core")],
        candidate_ms2_pattern_rows=[
            _candidate_ms2_row("FAM_WRITE", "S1", tag_observed=True),
        ],
        ms1_pattern_coherence_rows=[_ms1_row("FAM_WRITE", "S1")],
        qc_ms1_pattern_reference_rows=[_qc_row("FAM_WRITE", "S1")],
        matrix_rt_drift_policy_rows=[_rt_drift_row("FAM_WRITE", "S1")],
    )

    mode_hypothesis_assignment.write_mode_hypothesis_assignment_rows(output, rows)

    loaded = machine_evidence_support.load_peak_hypothesis_selection(output)
    assert loaded[("FAM_WRITE", "S1")]["peak_hypothesis_status"] == (
        "product_candidate_core"
    )


def _rt_mode_row(
    family_id: str,
    sample_stem: str,
    *,
    mode_id: str,
    status: str = "mode_supported",
    role: str = "tag_bearing_core",
    tag_status: str = "tag_supported",
    family_class: str = "tag_backed_core_with_outlier_modes",
    evidence_level: str = "irt_selected_apex_modes",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "rt_mode_status": status,
        "rt_mode_evidence_level": evidence_level,
        "selected_mode_id": mode_id,
        "selected_mode_role": role,
        "selected_mode_tag_status": tag_status,
        "family_mode_class": family_class,
        "family_mode_count": "3",
        "tag_bearing_mode_count": "1",
        "selected_mode_cell_count": "2",
        "selected_mode_sample_type_counts": "Tumor:1",
        "selected_mode_status_counts": "rescued:1",
        "raw_selected_rt": "7.93",
        "normalized_selected_rt": "7.81",
        "selected_mode_raw_rt_range_min": "0.1",
        "selected_mode_normalized_rt_range_min": "0.08",
        "family_raw_rt_range_min": "2.1",
        "family_normalized_rt_range_min": "1.9",
        "reason": "unit_test_rt_mode",
        "diagnostic_only": "TRUE",
    }


def _candidate_ms2_row(
    family_id: str,
    sample_stem: str,
    *,
    tag_observed: bool = False,
    dda_missing: bool = False,
) -> dict[str, str]:
    if tag_observed:
        return {
            "feature_family_id": family_id,
            "sample_stem": sample_stem,
            "candidate_ms2_pattern_status": "supportive",
            "candidate_ms2_evidence_level": "sample_candidate_aligned",
            "raw_ms2_strict_nl_scan_count": "1",
            "matched_neutral_loss_count": "1",
            "source_matched_tag_count": "1",
            "diagnostic_only": "TRUE",
        }
    if dda_missing:
        return {
            "feature_family_id": family_id,
            "sample_stem": sample_stem,
            "candidate_ms2_pattern_status": "not_observed",
            "candidate_ms2_evidence_level": "sample_boundary_no_observed_pattern",
            "raw_ms2_trigger_scan_count": "3",
            "raw_ms2_strict_nl_scan_count": "0",
            "raw_ms2_trace_strength": "strong",
            "diagnostic_only": "TRUE",
        }
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "candidate_ms2_pattern_status": "not_observed",
        "candidate_ms2_evidence_level": "sample_boundary_no_observed_pattern",
        "raw_ms2_trigger_scan_count": "1",
        "raw_ms2_strict_nl_scan_count": "0",
        "raw_ms2_trace_strength": "weak",
        "diagnostic_only": "TRUE",
    }


def _ms1_row(family_id: str, sample_stem: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "ms1_pattern_status": "supportive",
        "ms1_pattern_evidence_level": "trace_constellation",
        "apex_coherence_sec": "1",
        "boundary_overlap_score": "0.9",
        "shape_correlation_score": "0.8",
        "relative_pattern_stability_score": "0.8",
        "local_interference_score": "0.1",
        "constellation_peak_count": "3",
        "reference_peak_count": "3",
        "drift_compatible_status": "compatible",
        "cell_height": "30000",
        "reason": "unit_test_ms1",
        "diagnostic_only": "TRUE",
    }


def _qc_row(family_id: str, sample_stem: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "qc_reference_status": "supportive",
        "qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
        "target_injection_order": "10",
        "nearest_qc_sample_stem": "QC5",
        "nearest_qc_injection_order": "11",
        "nearest_qc_injection_order_delta": "1",
        "target_apex_rt": "7.93",
        "nearest_qc_apex_rt": "7.91",
        "target_minus_qc_apex_delta_sec": "1.2",
        "target_qc_apex_abs_delta_sec": "1.2",
        "target_qc_shape_similarity": "0.8",
        "target_local_window_to_global_max_ratio": "0.5",
        "nearest_qc_local_window_to_global_max_ratio": "0.6",
        "reason": "unit_test_qc",
        "diagnostic_only": "TRUE",
    }


def _rt_drift_row(family_id: str, sample_stem: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "matrix_rt_drift_status": "drift_supported",
        "drift_evidence_level": "sample_istd_aligned",
        "raw_rt_delta_sec": "120",
        "drift_corrected_delta_sec": "2",
        "matrix_shift_sec": "118",
        "drift_reference_count": "5",
        "drift_reference_source": "istd_rt_trend",
        "drift_compatible_status": "compatible",
        "reason": "unit_test_rt_drift",
        "diagnostic_only": "TRUE",
    }
