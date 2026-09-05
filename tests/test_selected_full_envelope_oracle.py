import numpy as np
import pytest

from xic_extractor.peak_detection.selected_envelope import (
    evaluate_selected_envelope_boundary,
)
from xic_extractor.peak_detection.selected_envelope_oracle import (
    BoundaryOracle,
    build_selected_envelope_oracle_manifest,
    compare_selected_envelope_to_oracle,
)


def _evaluation():
    residual = [0, 0, 2, 10, 30, 50, 30, 10, 2, 0, 0]
    rt = np.arange(float(len(residual)), dtype=float)
    baseline = np.full(len(residual), 10.0)
    intensity = baseline + np.asarray(residual, dtype=float)
    return evaluate_selected_envelope_boundary(
        rt,
        intensity,
        baseline,
        selected_apex_rt=5.0,
        resolver_rt_start=4.0,
        resolver_rt_end=6.0,
        quantitation_context_rt_start=0.0,
        quantitation_context_rt_end=10.0,
        selected_candidate_id="candidate-001",
        legacy_resolver_provenance="local_minimum",
    )


def test_selected_envelope_is_compared_against_manual_boundary_oracle() -> None:
    evaluation = _evaluation()
    oracle = BoundaryOracle(
        oracle_row_id="oracle-001",
        selected_candidate_id="candidate-001",
        oracle_status="expert_reviewed",
        oracle_source="manual_overlay",
        rt_start_min=2.0,
        rt_end_min=8.0,
        area_baseline_corrected=evaluation.asls_area_selected_envelope,
        shape_class="clean_single_peak",
        acceptable_boundary_delta_min=0.1,
        acceptable_area_relative_error=0.05,
        required_plot_path="plots/oracle-001.png",
    )

    comparison = compare_selected_envelope_to_oracle(evaluation, oracle)

    assert comparison.oracle_row_id == "oracle-001"
    assert comparison.selected_candidate_id == "candidate-001"
    assert comparison.oracle_status == "expert_reviewed"
    assert comparison.boundary_winner == "selected_envelope"
    assert comparison.area_winner == "selected_envelope"
    assert comparison.verdict == "selected_envelope_closer"
    assert comparison.resolver_boundary_error_min > (
        comparison.selected_envelope_boundary_error_min
    )
    assert comparison.selected_envelope_boundary_within_tolerance is True
    assert comparison.selected_envelope_area_within_tolerance is True
    assert comparison.required_plot_path == "plots/oracle-001.png"


def test_oracle_comparison_rejects_targeted_workbook_as_boundary_truth() -> None:
    evaluation = _evaluation()
    oracle = BoundaryOracle(
        oracle_row_id="targeted-control-001",
        selected_candidate_id="candidate-001",
        oracle_status="expert_reviewed",
        oracle_source="targeted_workbook_control",
        rt_start_min=2.0,
        rt_end_min=8.0,
        area_baseline_corrected=evaluation.asls_area_selected_envelope,
        shape_class="clean_single_peak",
    )

    with pytest.raises(ValueError, match="targeted workbook"):
        compare_selected_envelope_to_oracle(evaluation, oracle)


def test_benchmark_control_row_does_not_count_as_boundary_oracle() -> None:
    evaluation = _evaluation()
    oracle = BoundaryOracle(
        oracle_row_id="targeted-control-001",
        selected_candidate_id="candidate-001",
        oracle_status="benchmark_control_only",
        oracle_source="targeted_workbook_control",
        rt_start_min=2.0,
        rt_end_min=8.0,
        area_baseline_corrected=evaluation.asls_area_selected_envelope,
        shape_class="clean_single_peak",
    )

    comparison = compare_selected_envelope_to_oracle(evaluation, oracle)
    manifest = build_selected_envelope_oracle_manifest((comparison,))

    assert comparison.verdict == "benchmark_control_only"
    assert comparison.boundary_winner == "not_assessed"
    assert comparison.area_winner == "not_assessed"
    assert manifest["gate_decision"] == "defer"
    assert manifest["blocked_reasons"] == "no_boundary_oracle_rows"


def test_oracle_comparison_rejects_manual_sources_as_benchmark_only() -> None:
    evaluation = _evaluation()
    oracle = BoundaryOracle(
        oracle_row_id="manual-benchmark-001",
        selected_candidate_id="candidate-001",
        oracle_status="benchmark_control_only",
        oracle_source="manual_overlay",
        rt_start_min=2.0,
        rt_end_min=8.0,
        area_baseline_corrected=None,
        shape_class="clean_single_peak",
    )

    with pytest.raises(ValueError, match="benchmark_control_only"):
        compare_selected_envelope_to_oracle(evaluation, oracle)


def test_candidate_mismatch_is_not_compared_to_manual_oracle() -> None:
    evaluation = _evaluation()
    oracle = BoundaryOracle(
        oracle_row_id="oracle-001",
        selected_candidate_id="different-candidate",
        oracle_status="expert_reviewed",
        oracle_source="manual_overlay",
        rt_start_min=2.0,
        rt_end_min=8.0,
        area_baseline_corrected=evaluation.asls_area_selected_envelope,
        shape_class="clean_single_peak",
    )

    with pytest.raises(ValueError, match="selected_candidate_id"):
        compare_selected_envelope_to_oracle(evaluation, oracle)


def test_oracle_manifest_allows_fixture_cohort_only_after_expert_oracle_support() -> (
    None
):
    evaluation = _evaluation()
    oracle = BoundaryOracle(
        oracle_row_id="oracle-001",
        selected_candidate_id="candidate-001",
        oracle_status="expert_reviewed",
        oracle_source="expert_overlay",
        rt_start_min=2.0,
        rt_end_min=8.0,
        area_baseline_corrected=evaluation.asls_area_selected_envelope,
        shape_class="clean_single_peak",
    )

    comparison = compare_selected_envelope_to_oracle(evaluation, oracle)
    manifest = build_selected_envelope_oracle_manifest((comparison,))

    assert manifest["gate_decision"] == "promote"
    assert manifest["expert_oracle_row_count"] == "1"
    assert manifest["benchmark_control_row_count"] == "0"
    assert manifest["selected_envelope_closer_count"] == "1"
    assert manifest["blocked_reasons"] == ""
    assert manifest["next_gate"] == "changed_row_review"


def test_oracle_manifest_no_go_when_resolver_is_closer_to_truth() -> None:
    evaluation = _evaluation()
    oracle = BoundaryOracle(
        oracle_row_id="oracle-001",
        selected_candidate_id="candidate-001",
        oracle_status="expert_reviewed",
        oracle_source="expert_overlay",
        rt_start_min=4.0,
        rt_end_min=6.0,
        area_baseline_corrected=evaluation.asls_area_old_interval,
        shape_class="clean_single_peak",
    )

    comparison = compare_selected_envelope_to_oracle(evaluation, oracle)
    manifest = build_selected_envelope_oracle_manifest((comparison,))

    assert comparison.verdict == "resolver_interval_closer"
    assert manifest["gate_decision"] == "no_go"
    assert manifest["blocked_reasons"] == "resolver_interval_closer_to_oracle"
    assert manifest["next_gate"] == "stop_selected_envelope_product_path"


def test_oracle_manifest_defers_when_oracle_rows_only_tie() -> None:
    evaluation = _evaluation()
    oracle = BoundaryOracle(
        oracle_row_id="oracle-001",
        selected_candidate_id="candidate-001",
        oracle_status="expert_reviewed",
        oracle_source="expert_overlay",
        rt_start_min=3.0,
        rt_end_min=7.0,
        area_baseline_corrected=None,
        shape_class="clean_single_peak",
    )

    comparison = compare_selected_envelope_to_oracle(evaluation, oracle)
    manifest = build_selected_envelope_oracle_manifest((comparison,))

    assert comparison.verdict == "tie"
    assert manifest["gate_decision"] == "defer"
    assert manifest["blocked_reasons"] == "no_selected_envelope_oracle_support"
    assert manifest["next_gate"] == "bounded_follow_up_required"


def test_oracle_manifest_defers_when_selected_envelope_exceeds_tolerance() -> None:
    evaluation = _evaluation()
    oracle = BoundaryOracle(
        oracle_row_id="oracle-001",
        selected_candidate_id="candidate-001",
        oracle_status="expert_reviewed",
        oracle_source="expert_overlay",
        rt_start_min=2.0,
        rt_end_min=8.0,
        area_baseline_corrected=evaluation.asls_area_selected_envelope,
        shape_class="clean_single_peak",
        acceptable_boundary_delta_min=0.0,
        acceptable_area_relative_error=0.0,
    )
    shifted_oracle = BoundaryOracle(
        oracle_row_id="oracle-002",
        selected_candidate_id="candidate-001",
        oracle_status="expert_reviewed",
        oracle_source="expert_overlay",
        rt_start_min=2.05,
        rt_end_min=8.05,
        area_baseline_corrected=evaluation.asls_area_selected_envelope * 1.02,
        shape_class="clean_single_peak",
        acceptable_boundary_delta_min=0.01,
        acceptable_area_relative_error=0.01,
    )

    exact = compare_selected_envelope_to_oracle(evaluation, oracle)
    shifted = compare_selected_envelope_to_oracle(evaluation, shifted_oracle)
    manifest = build_selected_envelope_oracle_manifest((shifted,))

    assert exact.selected_envelope_boundary_within_tolerance is True
    assert exact.selected_envelope_area_within_tolerance is True
    assert shifted.verdict == "selected_envelope_closer"
    assert shifted.selected_envelope_boundary_within_tolerance is False
    assert shifted.selected_envelope_area_within_tolerance is False
    assert manifest["gate_decision"] == "defer"
    assert manifest["blocked_reasons"] == "no_selected_envelope_oracle_support"
