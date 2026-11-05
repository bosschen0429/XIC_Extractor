from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace

import pytest

from xic_extractor.alignment.primary_matrix_area import (
    ASLS_PRIMARY_MATRIX_AREA_SOURCE,
    MISSING_MS1_MORPHOLOGY_AREA,
    primary_matrix_area_from_integration,
)
from xic_extractor.extraction.result_models import ExtractionResult
from xic_extractor.output.schema import LONG_COLUMNS
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.models import PeakDetectionResult


def test_targeted_area_column_and_reported_peak_area_use_ms1_morphology_area() -> None:
    area_column = next(column for column in LONG_COLUMNS if column.name == "Area")
    assert (
        area_column.description
        == "Gaussian15-smoothed positive AsLS residual area (counts·s)"
    )

    integration = IntegrationResult(
        rt_left_min=1.0,
        rt_apex_min=1.2,
        rt_right_min=1.4,
        raw_apex_rt_min=1.2,
        rt_width_min=0.4,
        height_raw=1000.0,
        height_smoothed=950.0,
        area_raw_counts_seconds=1200.0,
        area_baseline_corrected=800.0,
        baseline_type="asls",
        area_ms1_morphology=900.0,
        ms1_morphology_area_source="gaussian15_positive_asls_residual",
    )
    result = ExtractionResult(
        peak_result=PeakDetectionResult(
            status="OK",
            peak=None,
            n_points=5,
            max_smoothed=950.0,
            n_prominent_peaks=1,
        ),
        nl=None,
        selected_hypothesis=_hypothesis(integration),
    )

    assert result.reported_peak_area == pytest.approx(900.0)


@pytest.mark.parametrize(
    "area_source",
    [
        "gaussian15_positive_asls_residual",
        "trapezoid_counts_seconds_on_canonical_signal",
        "trapezoid_counts_seconds_on_observed_censored_signal",
    ],
)
def test_alignment_primary_matrix_area_preserves_accepted_area_source(
    area_source: str,
) -> None:
    integration = IntegrationResult(
        rt_left_min=1.0,
        rt_apex_min=1.2,
        rt_right_min=1.4,
        raw_apex_rt_min=1.2,
        rt_width_min=0.4,
        height_raw=1000.0,
        height_smoothed=950.0,
        area_raw_counts_seconds=1200.0,
        area_baseline_corrected=800.0,
        baseline_type="asls",
        area_ms1_morphology=900.0,
        ms1_morphology_area_source=area_source,
    )

    decision = primary_matrix_area_from_integration(integration)

    assert decision.value == pytest.approx(900.0)
    assert decision.source == area_source
    assert decision.reason == ""


@pytest.mark.parametrize(
    "area", [None, 0.0, -1.0, float("nan"), float("inf"), True, "900"]
)
def test_alignment_primary_matrix_area_rejects_invalid_observed_censored_area(
    area: object,
) -> None:
    decision = primary_matrix_area_from_integration(
        SimpleNamespace(
            baseline_type="",
            area_baseline_corrected=None,
            area_ms1_morphology=area,
            ms1_morphology_area_source=(
                "trapezoid_counts_seconds_on_observed_censored_signal"
            ),
        )
    )

    assert decision.value is None
    assert decision.source == ""
    assert decision.reason == MISSING_MS1_MORPHOLOGY_AREA


def test_alignment_primary_matrix_area_rejects_unknown_positive_area_source() -> None:
    decision = primary_matrix_area_from_integration(
        SimpleNamespace(
            baseline_type="",
            area_baseline_corrected=None,
            area_ms1_morphology=900.0,
            ms1_morphology_area_source="unknown_signal",
        )
    )

    assert decision.value is None
    assert decision.source == ""
    assert decision.reason == MISSING_MS1_MORPHOLOGY_AREA


def test_alignment_primary_matrix_area_retires_asls_legacy_fallback() -> None:
    integration = IntegrationResult(
        rt_left_min=1.0,
        rt_apex_min=1.2,
        rt_right_min=1.4,
        raw_apex_rt_min=1.2,
        rt_width_min=0.4,
        height_raw=1000.0,
        height_smoothed=950.0,
        area_raw_counts_seconds=1200.0,
        area_baseline_corrected=800.0,
        baseline_type="asls",
    )

    decision = primary_matrix_area_from_integration(integration)

    assert decision.value is None
    assert decision.source == ""
    assert decision.reason == MISSING_MS1_MORPHOLOGY_AREA
    assert ASLS_PRIMARY_MATRIX_AREA_SOURCE == "asls_baseline_corrected"


def test_integration_result_carries_ms1_morphology_fields() -> None:
    field_names = {field.name for field in fields(IntegrationResult)}

    assert {"rt_left_min", "rt_apex_min", "rt_right_min"} <= field_names
    assert "area_ms1_morphology" in field_names
    assert "ms1_morphology_area_source" in field_names
    assert "resolver_rt_start_min" not in field_names
    assert "envelope_rt_start_min" not in field_names
    assert "quantitation_context_rt_start_min" not in field_names
    assert "boundary_change_class" not in field_names


def _hypothesis(integration: IntegrationResult) -> PeakHypothesis:
    return PeakHypothesis(
        hypothesis_id="hypothesis-fe0",
        trace_group_id="SampleA|Analyte|local_minimum",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        analysis_mode="targeted",
        resolver_mode="local_minimum",
        integration=integration,
        evidence=EvidenceVector(),
        audit=AuditTrail(selected=True),
    )
