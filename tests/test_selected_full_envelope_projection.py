import numpy as np
import pytest

from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.selected_envelope_projection import (
    selected_envelope_evaluation_from_hypothesis,
    selected_envelope_promoted_hypothesis_from_hypothesis,
)


def test_selected_envelope_projection_requires_selected_hypothesis() -> None:
    hypothesis = _hypothesis(selected=False)

    with pytest.raises(ValueError, match="selected hypothesis"):
        selected_envelope_evaluation_from_hypothesis(
            hypothesis,
            rt_values=np.asarray([0.0, 1.0, 2.0]),
            intensity_values=np.asarray([10.0, 20.0, 10.0]),
            quantitation_context_rt_start=0.0,
            quantitation_context_rt_end=2.0,
        )


def test_selected_envelope_projection_requires_context_to_contain_resolver() -> None:
    hypothesis = _hypothesis(
        rt_left_min=4.0,
        rt_apex_min=5.0,
        rt_right_min=6.0,
    )

    with pytest.raises(ValueError, match="quantitation context"):
        selected_envelope_evaluation_from_hypothesis(
            hypothesis,
            rt_values=np.arange(11, dtype=float),
            intensity_values=np.ones(11, dtype=float),
            quantitation_context_rt_start=5.0,
            quantitation_context_rt_end=10.0,
        )


def test_selected_envelope_projection_rejects_degenerate_context() -> None:
    hypothesis = _hypothesis(
        rt_left_min=4.0,
        rt_apex_min=5.0,
        rt_right_min=6.0,
    )

    with pytest.raises(ValueError, match="quantitation context"):
        selected_envelope_evaluation_from_hypothesis(
            hypothesis,
            rt_values=np.arange(11, dtype=float),
            intensity_values=np.ones(11, dtype=float),
            quantitation_context_rt_start=4.0,
            quantitation_context_rt_end=4.0,
        )


def test_selected_envelope_projection_requires_increasing_rt_trace() -> None:
    hypothesis = _hypothesis(
        rt_left_min=1.0,
        rt_apex_min=2.0,
        rt_right_min=3.0,
    )

    with pytest.raises(ValueError, match="strictly increasing"):
        selected_envelope_evaluation_from_hypothesis(
            hypothesis,
            rt_values=np.asarray([0.0, 2.0, 1.0, 3.0]),
            intensity_values=np.ones(4, dtype=float),
            quantitation_context_rt_start=0.0,
            quantitation_context_rt_end=3.0,
        )


def test_selected_envelope_projection_requires_matching_trace_lengths() -> None:
    hypothesis = _hypothesis(
        rt_left_min=1.0,
        rt_apex_min=2.0,
        rt_right_min=3.0,
    )

    with pytest.raises(ValueError, match="same length"):
        selected_envelope_evaluation_from_hypothesis(
            hypothesis,
            rt_values=np.asarray([0.0, 1.0, 2.0, 3.0]),
            intensity_values=np.ones(3, dtype=float),
            quantitation_context_rt_start=0.0,
            quantitation_context_rt_end=3.0,
        )


def test_selected_envelope_projection_rejects_non_finite_context_intensity() -> None:
    hypothesis = _hypothesis(
        rt_left_min=1.0,
        rt_apex_min=2.0,
        rt_right_min=3.0,
    )

    with pytest.raises(ValueError, match="finite values"):
        selected_envelope_evaluation_from_hypothesis(
            hypothesis,
            rt_values=np.asarray([0.0, 1.0, 2.0, 3.0]),
            intensity_values=np.asarray([1.0, 2.0, np.nan, 1.0]),
            quantitation_context_rt_start=0.0,
            quantitation_context_rt_end=3.0,
        )


def test_selected_envelope_projection_promotes_narrowed_active_integration() -> None:
    residual = np.asarray([0, 0, 2, 10, 50, 10, 2, 0, 0], dtype=float)
    rt = np.arange(float(len(residual)), dtype=float)
    intensity = residual + 10.0
    hypothesis = _hypothesis(
        rt_left_min=1.0,
        rt_apex_min=4.0,
        rt_right_min=7.0,
    )

    promoted, evaluation = selected_envelope_promoted_hypothesis_from_hypothesis(
        hypothesis,
        rt_values=rt,
        intensity_values=intensity,
        quantitation_context_rt_start=0.0,
        quantitation_context_rt_end=8.0,
    )

    assert evaluation.row_boundary_decision == "accept_candidate"
    assert evaluation.boundary_change_class == "resolver_overwide_narrowed"
    assert promoted is not hypothesis
    assert promoted.integration.rt_left_min == pytest.approx(2.0)
    assert promoted.integration.rt_right_min == pytest.approx(6.0)
    assert promoted.integration.rt_width_min == pytest.approx(4.0)
    assert promoted.integration.integration_method == "selected_envelope_gaussian15"
    assert promoted.integration.raw_scan_indices == (2, 3, 4, 5, 6)
    assert promoted.integration.area_ms1_morphology is not None
    assert promoted.integration.ms1_morphology_area_source == (
        "gaussian15_positive_asls_residual"
    )
    assert "resolver_overwide_narrowed" in promoted.integration.boundary_sources


def test_selected_envelope_projection_keeps_original_for_conflict() -> None:
    residual = np.asarray(
        [0, 0, 2, 10, 50, 10, 2, 0, 0, 45, 80, 45, 0],
        dtype=float,
    )
    rt = np.arange(float(len(residual)), dtype=float)
    intensity = residual + 10.0
    hypothesis = _hypothesis(
        rt_left_min=3.0,
        rt_apex_min=4.0,
        rt_right_min=5.0,
    )

    promoted, evaluation = selected_envelope_promoted_hypothesis_from_hypothesis(
        hypothesis,
        rt_values=rt,
        intensity_values=intensity,
        quantitation_context_rt_start=0.0,
        quantitation_context_rt_end=12.0,
    )

    assert evaluation.row_boundary_decision == "externalize"
    assert evaluation.boundary_change_class == "context_apex_conflict"
    assert promoted is hypothesis
    assert promoted.integration.rt_left_min == pytest.approx(3.0)
    assert promoted.integration.rt_right_min == pytest.approx(5.0)


def _hypothesis(
    *,
    selected: bool = True,
    rt_left_min: float = 4.0,
    rt_apex_min: float = 5.0,
    rt_right_min: float = 6.0,
) -> PeakHypothesis:
    return PeakHypothesis(
        hypothesis_id="hypothesis-001",
        trace_group_id="trace-001",
        target_label="5-medC",
        role="Analyte",
        istd_pair="d3-5-medC",
        analysis_mode="targeted",
        resolver_mode="local_minimum",
        integration=IntegrationResult(
            rt_left_min=rt_left_min,
            rt_apex_min=rt_apex_min,
            rt_right_min=rt_right_min,
            raw_apex_rt_min=rt_apex_min,
            rt_width_min=rt_right_min - rt_left_min,
            height_raw=100.0,
            height_smoothed=95.0,
            area_raw_counts_seconds=1200.0,
        ),
        evidence=EvidenceVector(),
        audit=AuditTrail(selected=selected),
    )
