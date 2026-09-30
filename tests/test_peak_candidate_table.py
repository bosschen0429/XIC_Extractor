import csv
from dataclasses import replace
from pathlib import Path

import numpy as np

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence
from xic_extractor.extraction.peak_candidate_table import (
    append_peak_candidate_rows,
    build_peak_candidate_rows,
    build_peak_candidate_rows_from_hypotheses,
    candidate_audit_id,
    with_product_selected_marker,
)
from xic_extractor.extraction.peak_hypothesis_adapter import (
    build_targeted_peak_hypotheses as build_peak_hypotheses,
)
from xic_extractor.output.peak_candidates import (
    PEAK_CANDIDATE_HEADERS,
    write_peak_candidates_tsv,
)
from xic_extractor.peak_detection.baseline import integrate_asls_baseline
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.scoring_models import ScoringContext
from xic_extractor.peak_detection.traces import Trace, targeted_trace_group
from xic_extractor.signal_processing import (
    PeakCandidate,
    PeakCandidateScore,
    PeakDetectionResult,
    PeakResult,
)

_PRE_NL_DIAGNOSTIC_HEADERS = (
    "sample_name",
    "group",
    "target_label",
    "role",
    "istd_pair",
    "analysis_mode",
    "resolver_mode",
    "candidate_id",
    "proposal_sources",
    "proposal_count",
    "source_apex_rank",
    "merge_note",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "raw_apex_rt_min",
    "rt_width_min",
    "selection_apex_intensity",
    "raw_apex_intensity",
    "prominence",
    "area_raw_counts_seconds",
    "area_baseline_corrected",
    "area_ms1_morphology",
    "ms1_morphology_area_source",
    "ms1_morphology_trace_method",
    "ms1_morphology_trace_window_points",
    "ms1_morphology_trace_effective_points",
    "area_uncertainty",
    "area_uncertainty_formula_version",
    "baseline_residual_mad",
    "area_uncertainty_noise_source",
    "quality_flags",
    "region_scan_count",
    "region_duration_min",
    "region_edge_ratio",
    "region_trace_continuity",
    "ms2_present",
    "nl_match",
    "ms2_trace_strength",
    "rt_prior_min",
    "rt_prior_sigma",
    "confidence",
    "raw_score",
    "support_labels",
    "concern_labels",
    "cap_labels",
    "reason",
    "selected",
    "selection_rank",
    "selection_reference_rt_min",
    "rejection_reason",
)

_NL_DIAGNOSTIC_HEADERS = (
    "nl_status",
    "best_loss_ppm",
    "best_ms2_scan_rt_min",
    "apex_ms2_delta_min",
    "best_product_base_ratio",
    "trigger_scan_count",
    "strict_nl_scan_count",
)

_MS1_PEAK_GROUP_MS2_HEADERS = (
    "ms1_peak_group_source",
    "ms1_peak_group_rt_min",
    "ms1_peak_group_rt_max",
    "ms1_peak_group_trigger_scan_count",
    "ms1_peak_group_strict_nl_scan_count",
    "ms1_peak_group_strict_nl_event_count",
    "outside_ms1_peak_group_trigger_scan_count",
    "outside_ms1_peak_group_strict_nl_scan_count",
    "ms2_alignment_source",
    "diagnostic_product_absence_reason",
    "nearest_product_loss_ppm",
    "nearest_product_base_ratio",
    "nearest_product_mz",
)

_SAFE_MERGE_PROVENANCE_HEADERS = (
    "safe_merge_promotion_source",
    "safe_merge_promotion_shadow_boundary_id",
    "safe_merge_promotion_area_ratio",
    "safe_merge_promotion_selected_interval_count",
    "safe_merge_promotion_selected_interval_gap_max_min",
    "safe_merge_rejection_reason",
)


def test_peak_candidate_headers_append_nl_diagnostics_without_reordering() -> None:
    assert PEAK_CANDIDATE_HEADERS[: len(_PRE_NL_DIAGNOSTIC_HEADERS)] == (
        _PRE_NL_DIAGNOSTIC_HEADERS
    )
    nl_start = len(_PRE_NL_DIAGNOSTIC_HEADERS)
    nl_end = nl_start + len(_NL_DIAGNOSTIC_HEADERS)
    assert PEAK_CANDIDATE_HEADERS[nl_start:nl_end] == _NL_DIAGNOSTIC_HEADERS
    ms1_group_end = nl_end + len(_MS1_PEAK_GROUP_MS2_HEADERS)
    assert PEAK_CANDIDATE_HEADERS[nl_end:ms1_group_end] == (
        _MS1_PEAK_GROUP_MS2_HEADERS
    )
    assert PEAK_CANDIDATE_HEADERS[ms1_group_end:] == (
        _SAFE_MERGE_PROVENANCE_HEADERS
    )


def test_candidate_id_is_deterministic() -> None:
    candidate = _candidate(
        8.1234567,
        left=8.001234,
        right=8.654321,
        proposal_sources=("legacy_savgol",),
    )

    first = candidate_audit_id(
        sample_name="SampleA",
        target_label="Analyte",
        resolver_mode="legacy_savgol",
        candidate=candidate,
    )
    second = candidate_audit_id(
        sample_name="SampleA",
        target_label="Analyte",
        resolver_mode="legacy_savgol",
        candidate=candidate,
    )

    assert first == second
    assert first == (
        "SampleA|Analyte|legacy_savgol|legacy_savgol|"
        "8.12346|8.00123|8.65432"
    )


def test_build_rows_marks_selected_and_rejected_candidates() -> None:
    selected = _candidate(8.5, area=5000.0, proposal_sources=("legacy_savgol",))
    rejected = _candidate(8.9, area=900.0, proposal_sources=("local_minimum",))
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=20,
        max_smoothed=3000.0,
        n_prominent_peaks=2,
        candidates=(selected, rejected),
        candidate_scores=(
            _score(selected, raw_score=92, confidence="HIGH"),
            _score(rejected, raw_score=58, confidence="LOW"),
        ),
        selection_reference_rt=8.45,
    )

    rows = build_peak_candidate_rows(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="ISTD",
        resolver_mode="region_first_safe_merge",
        peak_result=result,
        candidate_ms2_evidence={
            selected: _ms2_evidence(nl_match=True),
            rejected: _ms2_evidence(nl_match=False),
        },
    )

    assert [row["selected"] for row in rows] == ["TRUE", "FALSE"]
    assert rows[0]["selection_rank"] == "1"
    assert rows[0]["proposal_sources"] == "legacy_savgol"
    assert rows[0]["ms2_present"] == "TRUE"
    assert rows[0]["nl_match"] == "TRUE"
    assert rows[0]["nl_status"] == "OK"
    assert rows[0]["best_loss_ppm"] == "1.00000"
    assert rows[0]["best_ms2_scan_rt_min"] == "8.50000"
    assert rows[0]["apex_ms2_delta_min"] == "0.00000"
    assert rows[0]["best_product_base_ratio"] == "0.30000"
    assert rows[0]["trigger_scan_count"] == "1"
    assert rows[0]["strict_nl_scan_count"] == "1"
    assert rows[0]["ms1_peak_group_source"] == "gaussian15_ms1_peak_group"
    assert rows[0]["ms1_peak_group_rt_min"] == "8.40000"
    assert rows[0]["ms1_peak_group_rt_max"] == "8.60000"
    assert rows[0]["ms1_peak_group_trigger_scan_count"] == "1"
    assert rows[0]["ms1_peak_group_strict_nl_scan_count"] == "1"
    assert rows[0]["ms1_peak_group_strict_nl_event_count"] == "1"
    assert rows[0]["outside_ms1_peak_group_trigger_scan_count"] == "0"
    assert rows[0]["outside_ms1_peak_group_strict_nl_scan_count"] == "0"
    assert rows[0]["ms2_alignment_source"] == "region"
    assert rows[0]["diagnostic_product_absence_reason"] == ""
    assert rows[0]["nearest_product_loss_ppm"] == "1.00000"
    assert rows[0]["nearest_product_base_ratio"] == "0.30000"
    assert rows[0]["raw_score"] == "92"
    assert rows[0]["support_labels"] == "strict_nl_ok;shape_clean"
    assert rows[1]["proposal_sources"] == "local_minimum"
    assert rows[1]["rejection_reason"] == "lower_confidence"
    assert rows[1]["nl_match"] == "FALSE"
    assert rows[1]["nl_status"] == "NL_FAIL"
    assert rows[1]["strict_nl_scan_count"] == "0"
    assert rows[1]["diagnostic_product_absence_reason"] == "no_product_peak"
    assert rows[1]["concern_labels"] == "nl_fail"


def test_build_rows_ranks_selected_candidate_first_after_selection_demotes_higher_score(
) -> None:
    selected = _candidate(8.7, area=50_000.0, proposal_sources=("local_minimum",))
    rejected = _candidate(8.5, area=2_000.0, proposal_sources=("local_minimum",))
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=20,
        max_smoothed=3000.0,
        n_prominent_peaks=2,
        candidates=(rejected, selected),
        candidate_scores=(
            _score(rejected, raw_score=110, confidence="MEDIUM"),
            _score(selected, raw_score=37, confidence="VERY_LOW"),
        ),
        selection_reference_rt=8.5,
    )

    rows = build_peak_candidate_rows(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="ISTD",
        resolver_mode="local_minimum",
        peak_result=result,
    )

    rejected_row = next(row for row in rows if row["rt_apex_min"] == "8.50000")
    selected_row = next(row for row in rows if row["rt_apex_min"] == "8.70000")
    assert rejected_row["selected"] == "FALSE"
    assert rejected_row["selection_rank"] == "2"
    assert selected_row["selected"] == "TRUE"
    assert selected_row["selection_rank"] == "1"


def test_build_rows_formats_missing_candidate_ms2_diagnostics_as_blank() -> None:
    selected = _candidate(8.5, proposal_sources=("legacy_savgol",))
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=20,
        max_smoothed=3000.0,
        n_prominent_peaks=1,
        candidates=(selected,),
    )

    row = build_peak_candidate_rows(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="legacy_savgol",
        peak_result=result,
    )[0]

    assert {header: row[header] for header in _NL_DIAGNOSTIC_HEADERS} == {
        header: "" for header in _NL_DIAGNOSTIC_HEADERS
    }
    assert {header: row[header] for header in _SAFE_MERGE_PROVENANCE_HEADERS} == {
        header: "" for header in _SAFE_MERGE_PROVENANCE_HEADERS
    }


def test_build_rows_exposes_safe_merge_promotion_provenance() -> None:
    selected = _candidate(
        8.5,
        proposal_sources=("local_minimum",),
        safe_merge_promotion_source="adjacent_wis_local_minimum_merge",
        safe_merge_promotion_shadow_boundary_id="left;right",
        safe_merge_promotion_area_ratio=1.07008,
        safe_merge_promotion_selected_interval_count=2,
        safe_merge_promotion_selected_interval_gap_max_min=0.04144,
    )
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=20,
        max_smoothed=3000.0,
        n_prominent_peaks=1,
        candidates=(selected,),
    )

    row = build_peak_candidate_rows(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="region_first_safe_merge",
        peak_result=result,
    )[0]

    assert row["safe_merge_promotion_source"] == (
        "adjacent_wis_local_minimum_merge"
    )
    assert row["safe_merge_promotion_shadow_boundary_id"] == "left;right"
    assert row["safe_merge_promotion_area_ratio"] == "1.07008"
    assert row["safe_merge_promotion_selected_interval_count"] == "2"
    assert row["safe_merge_promotion_selected_interval_gap_max_min"] == "0.04144"


def test_build_rows_exposes_safe_merge_rejection_reason() -> None:
    selected = _candidate(
        8.5,
        proposal_sources=("local_minimum",),
        safe_merge_rejection_reason="gap_exceeds_safe_merge_max",
    )
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=20,
        max_smoothed=3000.0,
        n_prominent_peaks=1,
        candidates=(selected,),
    )

    row = build_peak_candidate_rows(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="region_first_safe_merge",
        peak_result=result,
    )[0]

    assert row["safe_merge_rejection_reason"] == "gap_exceeds_safe_merge_max"


def test_build_rows_can_emit_baseline_corrected_audit_area() -> None:
    selected = _candidate(8.2, left=8.0, right=8.4, proposal_sources=("legacy_savgol",))
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=5,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(selected,),
    )

    rows = build_peak_candidate_rows(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="legacy_savgol",
        peak_result=result,
        rt=np.asarray([8.0, 8.1, 8.2, 8.3, 8.4]),
        intensity=np.asarray([10.0, 25.0, 50.0, 35.0, 20.0]),
    )

    expected = integrate_asls_baseline(
        np.asarray([10.0, 25.0, 50.0, 35.0, 20.0]),
        np.asarray([8.0, 8.1, 8.2, 8.3, 8.4]),
        0,
        5,
    )
    assert rows[0]["area_baseline_corrected"] == (
        f"{expected.area_baseline_corrected:.5f}"
    )
    assert rows[0]["area_ms1_morphology"] != ""
    assert (
        rows[0]["ms1_morphology_area_source"]
        == "gaussian15_positive_asls_residual"
    )
    assert rows[0]["ms1_morphology_trace_method"] == "gaussian_15"
    assert rows[0]["ms1_morphology_trace_window_points"] == "15"
    assert rows[0]["ms1_morphology_trace_effective_points"] != ""
    assert rows[0]["area_uncertainty"] != ""
    assert rows[0]["area_uncertainty_formula_version"] == (
        "baseline_residual_mad"
    )
    assert rows[0]["baseline_residual_mad"] != ""
    assert rows[0]["area_uncertainty_noise_source"] != ""


def test_build_rows_prefers_shared_trace_group_arrays() -> None:
    selected = _candidate(8.2, left=8.0, right=8.4, proposal_sources=("legacy_savgol",))
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=5,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(selected,),
    )
    trace = Trace.from_arrays(
        sample_name="SampleA",
        mz=258.1085,
        rt=[8.0, 8.1, 8.2, 8.3, 8.4],
        intensity=[10.0, 25.0, 50.0, 35.0, 20.0],
        rt_min=8.0,
        rt_max=8.4,
        ppm_tol=20.0,
    )

    rows = build_peak_candidate_rows(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="legacy_savgol",
        peak_result=result,
        rt=np.asarray([8.0, 8.1, 8.2, 8.3, 8.4]),
        intensity=np.zeros(5),
        trace_group=targeted_trace_group(
            trace,
            target_label="Analyte",
            resolver_mode="legacy_savgol",
        ),
    )

    expected = integrate_asls_baseline(
        np.asarray([10.0, 25.0, 50.0, 35.0, 20.0]),
        np.asarray([8.0, 8.1, 8.2, 8.3, 8.4]),
        0,
        5,
    )
    assert rows[0]["area_baseline_corrected"] == (
        f"{expected.area_baseline_corrected:.5f}"
    )
    assert rows[0]["area_ms1_morphology"] != ""


def test_build_rows_from_hypotheses_projects_spine_without_legacy_result() -> None:
    row = build_peak_candidate_rows_from_hypotheses(
        sample_name="SampleA",
        group="Tumor",
        hypotheses=(
            PeakHypothesis(
                hypothesis_id="hypothesis-row-id",
                trace_group_id="SampleA|Analyte|spine",
                target_label="Analyte",
                role="ISTD",
                istd_pair="Analyte_IS",
                analysis_mode="targeted",
                resolver_mode="spine",
                integration=IntegrationResult(
                    rt_left_min=8.1,
                    rt_apex_min=8.3,
                    rt_right_min=8.5,
                    raw_apex_rt_min=8.3,
                    rt_width_min=0.4,
                    height_raw=100.0,
                    height_smoothed=95.0,
                    area_raw_counts_seconds=1234.5,
                    area_baseline_corrected=999.0,
                ),
                evidence=EvidenceVector(
                    confidence="HIGH",
                    raw_score=87,
                    support_labels=("strict_nl_ok",),
                    reason="spine decision",
                    ms2_present=True,
                    nl_match=True,
                ),
                audit=AuditTrail(
                    proposal_sources=("hypothesis_source",),
                    selected=True,
                    selection_rank=1,
                ),
            ),
        ),
    )[0]

    assert row["sample_name"] == "SampleA"
    assert row["group"] == "Tumor"
    assert row["candidate_id"] == "hypothesis-row-id"
    assert row["proposal_sources"] == "hypothesis_source"
    assert row["selected"] == "TRUE"
    assert row["selection_rank"] == "1"
    assert row["area_baseline_corrected"] == "999.00000"
    assert row["area_ms1_morphology"] == ""
    assert row["support_labels"] == "strict_nl_ok"
    assert row["reason"] == "spine decision"


def test_product_selected_marker_replaces_legacy_selected_candidate() -> None:
    legacy = _candidate_table_hypothesis("legacy", selected=True, rank=1)
    successor = _candidate_table_hypothesis("successor", selected=False, rank=2)

    rows = build_peak_candidate_rows_from_hypotheses(
        sample_name="SampleA",
        hypotheses=with_product_selected_marker(
            (legacy, successor),
            successor.hypothesis_id,
        ),
    )

    selected_by_id = {row["candidate_id"]: row["selected"] for row in rows}
    rank_by_id = {row["candidate_id"]: row["selection_rank"] for row in rows}
    assert selected_by_id == {"legacy": "FALSE", "successor": "TRUE"}
    assert rank_by_id["legacy"] == ""
    assert rank_by_id["successor"] == "1"


def test_product_selected_marker_projects_across_cwt_audit_id_change() -> None:
    legacy = _candidate_table_hypothesis("legacy", selected=True, rank=1)
    successor = replace(
        _candidate_table_hypothesis("successor", selected=False, rank=2),
        integration=IntegrationResult(
            rt_left_min=8.6,
            rt_apex_min=8.7,
            rt_right_min=8.8,
            raw_apex_rt_min=8.7,
            rt_width_min=0.2,
            height_raw=1200.0,
            height_smoothed=1100.0,
            area_raw_counts_seconds=4321.0,
        ),
    )
    cwt_merged_successor = replace(
        successor,
        hypothesis_id="successor-with-cwt-audit",
        audit=replace(
            successor.audit,
            proposal_sources=("legacy_savgol", "centwave_cwt"),
        ),
    )

    rows = build_peak_candidate_rows_from_hypotheses(
        sample_name="SampleA",
        hypotheses=with_product_selected_marker(
            (legacy, cwt_merged_successor),
            successor.hypothesis_id,
            selected_hypothesis=successor,
        ),
    )

    selected_by_id = {row["candidate_id"]: row["selected"] for row in rows}
    assert selected_by_id == {
        "legacy": "FALSE",
        "successor-with-cwt-audit": "TRUE",
    }


def test_targeted_handoff_spine_writes_peak_candidate_contract_row(
    tmp_path: Path,
) -> None:
    selected = _candidate(
        8.5,
        left=8.3,
        right=8.7,
        area=1234.5,
        proposal_sources=("legacy_savgol", "centwave_cwt"),
    )
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=5,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(selected,),
        candidate_scores=(_score(selected, raw_score=97, confidence="HIGH"),),
        selection_reference_rt=8.5,
    )
    trace = Trace.from_arrays(
        sample_name="SampleA",
        mz=258.1085,
        rt=[8.3, 8.4, 8.5, 8.6, 8.7],
        intensity=[10.0, 25.0, 50.0, 35.0, 20.0],
        rt_min=8.3,
        rt_max=8.7,
        ppm_tol=20.0,
        source="synthetic_contract",
    )
    trace_group = targeted_trace_group(
        trace,
        target_label="Analyte",
        resolver_mode="region_first_safe_merge",
        expected_rt_min=8.5,
        role="ISTD",
        istd_pair="Analyte_IS",
    )
    ms2_evidence = {selected: _ms2_evidence(nl_match=True)}

    hypotheses = build_peak_hypotheses(
        sample_name="SampleA",
        target_label="Analyte",
        role="ISTD",
        istd_pair="Analyte_IS",
        resolver_mode="region_first_safe_merge",
        peak_result=result,
        candidate_ms2_evidence=ms2_evidence,
        trace_group=trace_group,
    )
    assert len(hypotheses) == 1
    hypothesis = hypotheses[0]
    assert hypothesis.trace_group_id == "SampleA|Analyte|region_first_safe_merge"
    assert hypothesis.audit.selected is True
    assert hypothesis.evidence.raw_score == 97
    assert hypothesis.evidence.support_labels == ("strict_nl_ok", "shape_clean")
    assert hypothesis.integration.area_baseline_corrected is not None

    rows = build_peak_candidate_rows(
        sample_name="SampleA",
        target_label="Analyte",
        role="ISTD",
        istd_pair="Analyte_IS",
        resolver_mode="region_first_safe_merge",
        peak_result=result,
        candidate_ms2_evidence=ms2_evidence,
        trace_group=trace_group,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["sample_name"] == "SampleA"
    assert row["target_label"] == "Analyte"
    assert row["role"] == "ISTD"
    assert row["istd_pair"] == "Analyte_IS"
    assert row["analysis_mode"] == "targeted"
    assert row["resolver_mode"] == "region_first_safe_merge"
    assert row["candidate_id"] == candidate_audit_id(
        sample_name="SampleA",
        target_label="Analyte",
        resolver_mode="region_first_safe_merge",
        candidate=selected,
    )
    assert row["selected"] == "TRUE"
    assert row["selection_rank"] == "1"
    assert row["proposal_sources"] == "legacy_savgol;centwave_cwt"
    assert row["confidence"] == "HIGH"
    assert row["raw_score"] == "97"
    assert row["support_labels"] == "strict_nl_ok;shape_clean"
    assert row["reason"] == "decision: high"
    assert row["ms2_present"] == "TRUE"
    assert row["nl_match"] == "TRUE"
    assert row["nl_status"] == "OK"
    assert row["best_ms2_scan_rt_min"] == "8.50000"
    assert row["apex_ms2_delta_min"] == "0.00000"
    assert row["rt_left_min"] == "8.30000"
    assert row["rt_apex_min"] == "8.50000"
    assert row["rt_right_min"] == "8.70000"
    assert row["area_raw_counts_seconds"] == "1234.50"
    assert row["area_baseline_corrected"] != ""
    assert row["area_ms1_morphology"] != ""

    path = tmp_path / "peak_candidates.tsv"
    write_peak_candidates_tsv(path, rows)

    with path.open(newline="", encoding="utf-8-sig") as handle:
        read_back = list(csv.DictReader(handle, delimiter="\t"))

    assert len(read_back) == 1
    for key, value in row.items():
        assert read_back[0][key] == value


def test_hypothesis_integration_uses_configured_ms1_morphology_window() -> None:
    selected = _candidate(
        8.5,
        left=8.1,
        right=8.9,
        area=1234.5,
        proposal_sources=("legacy_savgol",),
    )
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=9,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(selected,),
        selection_reference_rt=8.5,
    )

    hypotheses = build_peak_hypotheses(
        sample_name="SampleA",
        target_label="Analyte",
        role="ISTD",
        istd_pair="Analyte_IS",
        resolver_mode="region_first_safe_merge",
        peak_result=result,
        rt=np.linspace(8.1, 8.9, 9),
        intensity=np.array([10.0, 18.0, 35.0, 80.0, 120.0, 80.0, 35.0, 18.0, 10.0]),
        ms1_morphology_smoothing_window_points=7,
    )

    integration = hypotheses[0].integration
    assert integration.ms1_morphology_trace_method == "gaussian_15"
    assert integration.ms1_morphology_trace_window_points == 7
    assert integration.ms1_morphology_trace_effective_points == 7
    assert integration.area_ms1_morphology is not None


def test_append_rows_rescores_same_apex_cwt_audit_support(
    tmp_path: Path,
    monkeypatch,
) -> None:
    selected = _candidate(8.5, proposal_sources=("legacy_savgol",))
    cwt_selected = replace(
        selected,
        proposal_sources=("legacy_savgol", "centwave_cwt"),
        cwt_best_scale=4.0,
        cwt_ridge_persistence=0.5,
    )
    cwt_only = replace(
        _candidate(8.9, proposal_sources=("centwave_cwt",)),
        cwt_best_scale=4.0,
        cwt_ridge_persistence=0.5,
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=20,
        max_smoothed=3000.0,
        n_prominent_peaks=1,
        candidates=(selected,),
        selection_reference_rt=8.5,
    )

    def _fake_cwt(
        peak_result: PeakDetectionResult,
        *_args: object,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        return replace(
            peak_result,
            candidates=(cwt_selected, cwt_only),
        )

    monkeypatch.setattr(
        "xic_extractor.extraction.peak_candidate_table.add_cwt_proposals_for_audit",
        _fake_cwt,
    )

    rows: list[dict[str, str]] = []
    append_peak_candidate_rows(
        rows,
        _config(tmp_path),
        "SampleA",
        _target(),
        peak_result,
        lambda _candidate: None,
        rt=np.linspace(8.0, 9.0, 20),
        intensity=np.asarray(
            [
                10.0,
                11.0,
                15.0,
                25.0,
                80.0,
                300.0,
                800.0,
                1200.0,
                850.0,
                350.0,
                100.0,
                30.0,
                15.0,
                12.0,
                10.0,
                10.0,
                10.0,
                10.0,
                10.0,
                10.0,
            ]
        ),
        scoring_context_builder=_scoring_context,
    )

    selected_rows = [row for row in rows if row["selected"] == "TRUE"]
    cwt_only_rows = [
        row for row in rows if row["proposal_sources"] == "centwave_cwt"
    ]
    assert len(selected_rows) == 1
    assert "cwt_same_apex_support" in selected_rows[0]["support_labels"]
    assert "cwt_same_apex_support" not in cwt_only_rows[0]["support_labels"]


def test_write_peak_candidates_tsv_serializes_rows_safely(tmp_path: Path) -> None:
    path = tmp_path / "peak_candidates.tsv"
    row = build_peak_candidate_rows(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="legacy_savgol",
        peak_result=PeakDetectionResult(
            status="OK",
            peak=_candidate(8.5).peak,
            n_points=20,
            max_smoothed=3000.0,
            n_prominent_peaks=1,
            candidates=(_candidate(8.5),),
        ),
    )[0]
    row["reason"] = "line1\nline2\twith tab"
    row["trace_group_id"] = "debug-only-internal-id"
    row.pop("cap_labels")

    write_peak_candidates_tsv(path, [row])

    text = path.read_text(encoding="utf-8-sig")
    assert "line1 line2 with tab" in text
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert reader.fieldnames == list(PEAK_CANDIDATE_HEADERS)
        assert "trace_group_id" not in reader.fieldnames
        rows = list(reader)
    assert rows[0]["reason"] == "line1 line2 with tab"
    assert rows[0]["cap_labels"] == ""


def test_disabled_writer_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "peak_candidates.tsv"

    write_peak_candidates_tsv(path, [{"sample_name": "SampleA"}], enabled=False)

    assert not path.exists()


def _config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="region_first_safe_merge",
        emit_peak_candidates=True,
    )


def _target() -> Target:
    return Target(
        label="Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=9.0,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=False,
        istd_pair="",
    )


def _scoring_context(candidate: PeakCandidate) -> ScoringContext:
    return ScoringContext(
        rt_array=np.linspace(8.0, 9.0, 20),
        intensity_array=np.asarray(
            [
                10.0,
                11.0,
                15.0,
                25.0,
                80.0,
                300.0,
                800.0,
                1200.0,
                850.0,
                350.0,
                100.0,
                30.0,
                15.0,
                12.0,
                10.0,
                10.0,
                10.0,
                10.0,
                10.0,
                10.0,
            ]
        ),
        apex_index=candidate.selection_apex_index,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=candidate.selection_apex_rt,
        rt_prior_sigma=0.1,
        rt_min=8.0,
        rt_max=9.0,
        dirty_matrix=False,
    )


def _candidate(
    rt: float,
    *,
    left: float | None = None,
    right: float | None = None,
    area: float = 1000.0,
    proposal_sources: tuple[str, ...] = ("legacy_savgol",),
    safe_merge_promotion_source: str = "",
    safe_merge_promotion_shadow_boundary_id: str = "",
    safe_merge_promotion_area_ratio: float | None = None,
    safe_merge_promotion_selected_interval_count: int | None = None,
    safe_merge_promotion_selected_interval_gap_max_min: float | None = None,
    safe_merge_rejection_reason: str = "",
) -> PeakCandidate:
    peak_start = rt - 0.2 if left is None else left
    peak_end = rt + 0.2 if right is None else right
    peak = PeakResult(
        rt=rt,
        intensity=1200.0,
        intensity_smoothed=1100.0,
        area=area,
        peak_start=peak_start,
        peak_end=peak_end,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=1100.0,
        selection_apex_index=7,
        raw_apex_rt=rt,
        raw_apex_intensity=1200.0,
        raw_apex_index=7,
        prominence=700.0,
        proposal_sources=proposal_sources,
        source_apex_rank=1,
        safe_merge_promotion_source=safe_merge_promotion_source,
        safe_merge_promotion_shadow_boundary_id=(
            safe_merge_promotion_shadow_boundary_id
        ),
        safe_merge_promotion_area_ratio=safe_merge_promotion_area_ratio,
        safe_merge_promotion_selected_interval_count=(
            safe_merge_promotion_selected_interval_count
        ),
        safe_merge_promotion_selected_interval_gap_max_min=(
            safe_merge_promotion_selected_interval_gap_max_min
        ),
        safe_merge_rejection_reason=safe_merge_rejection_reason,
    )


def _candidate_table_hypothesis(
    hypothesis_id: str,
    *,
    selected: bool,
    rank: int,
) -> PeakHypothesis:
    return PeakHypothesis(
        hypothesis_id=hypothesis_id,
        trace_group_id="SampleA|Analyte|trace",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        analysis_mode="targeted",
        resolver_mode="region_first_safe_merge",
        integration=IntegrationResult(
            rt_left_min=8.4,
            rt_apex_min=8.5,
            rt_right_min=8.6,
            raw_apex_rt_min=8.5,
            rt_width_min=0.2,
            height_raw=1200.0,
            height_smoothed=1100.0,
            area_raw_counts_seconds=1234.0,
        ),
        evidence=EvidenceVector(confidence="HIGH", reason="decision: accepted"),
        audit=AuditTrail(selected=selected, selection_rank=rank),
    )


def _score(
    candidate: PeakCandidate,
    *,
    raw_score: int,
    confidence: str,
) -> PeakCandidateScore:
    return PeakCandidateScore(
        candidate=candidate,
        confidence=confidence,
        reason=f"decision: {confidence.lower()}",
        raw_score=raw_score,
        support_labels=("strict_nl_ok", "shape_clean"),
        concern_labels=() if confidence == "HIGH" else ("nl_fail",),
        cap_labels=() if confidence == "HIGH" else ("nl_fail_cap",),
    )


def _ms2_evidence(*, nl_match: bool) -> CandidateMS2Evidence:
    return CandidateMS2Evidence(
        ms2_present=True,
        nl_match=nl_match,
        nl_status="OK" if nl_match else "NL_FAIL",
        trigger_scan_count=1,
        strict_nl_scan_count=1 if nl_match else 0,
        best_loss_ppm=1.0 if nl_match else None,
        best_scan_rt=8.5,
        best_product_base_ratio=0.3,
        alignment_source="region",
        diagnostic_product_absence_reason="" if nl_match else "no_product_peak",
        nearest_product_loss_ppm=1.0 if nl_match else None,
        nearest_product_base_ratio=0.3 if nl_match else None,
        nearest_product_mz=151.0 if nl_match else None,
        ms1_peak_group_source="gaussian15_ms1_peak_group",
        ms1_peak_group_rt_min=8.4,
        ms1_peak_group_rt_max=8.6,
        ms1_peak_group_trigger_scan_count=1,
        ms1_peak_group_strict_nl_scan_count=1 if nl_match else 0,
        ms1_peak_group_strict_nl_event_count=1 if nl_match else 0,
        outside_ms1_peak_group_trigger_scan_count=0,
        outside_ms1_peak_group_strict_nl_scan_count=0,
    )
