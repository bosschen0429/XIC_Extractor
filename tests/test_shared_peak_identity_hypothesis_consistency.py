from __future__ import annotations

from pathlib import Path

from xic_extractor.diagnostics.shared_peak_identity_explanation import (
    hypothesis_consistency,
)


def test_hypothesis_consistency_marks_supported_candidate_ready() -> None:
    rows = hypothesis_consistency.build_hypothesis_consistency_rows(
        peak_hypothesis_selection={
            ("FAM001", "S1"): _peak_hypothesis("FAM001", "S1")
        },
        ms1_pattern_coherence_evidence={("FAM001", "S1"): _ms1_pattern("FAM001", "S1")},
        matrix_rt_drift_policy_evidence={("FAM001", "S1"): _rt_drift("FAM001", "S1")},
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): _candidate_ms2("FAM001", "S1")
        },
    )

    assert rows[0]["evidence_consistency_status"] == "consistent"
    assert rows[0]["split_readiness_status"] == "peak_hypothesis_ready"
    assert rows[0]["ms2_opportunity_status"] == "required_tag_observed"
    assert rows[0]["hypothesis_next_action"] == "no_action"


def test_hypothesis_consistency_blocks_ms1_conflict() -> None:
    rows = hypothesis_consistency.build_hypothesis_consistency_rows(
        peak_hypothesis_selection={
            ("FAM001", "S1"): _peak_hypothesis("FAM001", "S1")
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): _ms1_pattern("FAM001", "S1", status="conflict")
        },
        matrix_rt_drift_policy_evidence={("FAM001", "S1"): _rt_drift("FAM001", "S1")},
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): _candidate_ms2("FAM001", "S1")
        },
    )

    assert rows[0]["evidence_consistency_status"] == "conflict"
    assert rows[0]["split_readiness_status"] == "review_required"
    assert "ms1_pattern_conflict" in rows[0]["consistency_blockers"]
    assert rows[0]["hypothesis_next_action"] == "inspect_conflict"


def test_hypothesis_consistency_keeps_missing_nl_non_dispositive() -> None:
    rows = hypothesis_consistency.build_hypothesis_consistency_rows(
        peak_hypothesis_selection={
            ("FAM001", "S1"): _peak_hypothesis("FAM001", "S1"),
            ("FAM001", "S2"): _peak_hypothesis("FAM001", "S2"),
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): _ms1_pattern("FAM001", "S1", height="35000"),
            ("FAM001", "S2"): _ms1_pattern("FAM001", "S2", height="40000"),
        },
        matrix_rt_drift_policy_evidence={
            ("FAM001", "S1"): _rt_drift("FAM001", "S1"),
            ("FAM001", "S2"): _rt_drift("FAM001", "S2"),
        },
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): _candidate_ms2(
                "FAM001",
                "S1",
                status="not_observed",
                level="sample_boundary_no_observed_pattern",
                strict_nl_count="0",
                trigger_scan_count="3",
                trace_strength="moderate",
            ),
            ("FAM001", "S2"): _candidate_ms2("FAM001", "S2"),
        },
    )

    by_sample = {row["sample_stem"]: row for row in rows}
    s1 = by_sample["S1"]
    assert s1["ms2_opportunity_status"] == "dda_missing_nl_not_dispositive"
    assert s1["evidence_consistency_status"] == "consistent"
    assert s1["family_required_tag_status"] == "family_required_tag_observed"


def test_hypothesis_consistency_summary_blocks_split_required() -> None:
    rows = hypothesis_consistency.build_hypothesis_consistency_rows(
        peak_hypothesis_selection={
            ("FAM011810", "TumorBC2263_DNA"): _peak_hypothesis(
                "FAM011810",
                "TumorBC2263_DNA",
                status="mode_split_required",
                scope="candidate_container",
                action="require_mode_split_before_product",
                blocker="mode_split_required",
            )
        },
    )

    summary = hypothesis_consistency.build_hypothesis_consistency_summary(rows)
    assert rows[0]["evidence_consistency_status"] == "split_required"
    assert rows[0]["split_readiness_status"] == "mode_split_required"
    assert summary["consistency_gate_status"] == "blocked"
    assert summary["split_required_count"] == "1"


def test_hypothesis_consistency_writer_uses_contract_columns(tmp_path: Path) -> None:
    output = tmp_path / "hypothesis_consistency.tsv"
    rows = hypothesis_consistency.build_hypothesis_consistency_rows(
        peak_hypothesis_selection={
            ("FAM001", "S1"): _peak_hypothesis("FAM001", "S1")
        },
    )

    hypothesis_consistency.write_hypothesis_consistency_rows(output, rows)

    loaded = output.read_text(encoding="utf-8").splitlines()
    assert loaded[0].startswith("hypothesis_consistency_schema_version\t")
    assert "peak_hypothesis_status" in loaded[0]


def _peak_hypothesis(
    family_id: str,
    sample_stem: str,
    *,
    status: str = "product_candidate_core",
    scope: str = "mode_level",
    action: str = "select_mode_peak_hypothesis",
    blocker: str = "none",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "peak_hypothesis_id": f"{family_id}::mode_1",
        "peak_hypothesis_status": status,
        "product_unit_scope": scope,
        "selected_mode_id": "mode_1",
        "selected_mode_role": "tag_bearing_core",
        "selected_mode_tag_status": "tag_supported",
        "family_mode_class": "rt_mode_pure",
        "family_mode_count": "1",
        "tag_bearing_mode_count": "1",
        "product_selection_action": action,
        "product_selection_blocker": blocker,
        "reason": "unit_test_peak_hypothesis",
        "diagnostic_only": "TRUE",
    }


def _ms1_pattern(
    family_id: str,
    sample_stem: str,
    *,
    status: str = "supportive",
    height: str = "50000",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "ms1_pattern_status": status,
        "ms1_pattern_evidence_level": "trace_constellation",
        "apex_coherence_sec": "2.0",
        "boundary_overlap_score": "0.8",
        "shape_correlation_score": "0.92",
        "relative_pattern_stability_score": "0.8",
        "local_interference_score": "0.05",
        "constellation_peak_count": "3",
        "reference_peak_count": "3",
        "drift_compatible_status": "compatible",
        "cell_height": height,
        "local_window_max_intensity": height,
        "trace_max_intensity": height,
        "reason": "unit_test_ms1",
        "diagnostic_only": "TRUE",
    }


def _rt_drift(family_id: str, sample_stem: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "matrix_rt_drift_status": "rt_close",
        "drift_evidence_level": "sample_istd_aligned",
        "raw_rt_delta_sec": "0.0",
        "drift_corrected_delta_sec": "0.0",
        "matrix_shift_sec": "0.0",
        "drift_reference_count": "3",
        "drift_reference_source": "sample_istd_trend",
        "drift_compatible_status": "compatible",
        "reason": "unit_test_rt",
        "diagnostic_only": "TRUE",
    }


def _candidate_ms2(
    family_id: str,
    sample_stem: str,
    *,
    status: str = "supportive",
    level: str = "sample_candidate_aligned",
    strict_nl_count: str = "1",
    trigger_scan_count: str = "3",
    trace_strength: str = "strong",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "candidate_ms2_pattern_status": status,
        "candidate_ms2_evidence_level": level,
        "matched_neutral_loss_count": strict_nl_count,
        "raw_ms2_strict_nl_scan_count": strict_nl_count,
        "raw_ms2_trigger_scan_count": trigger_scan_count,
        "raw_ms2_trace_strength": trace_strength,
        "reason": "unit_test_ms2",
        "diagnostic_only": "TRUE",
    }
