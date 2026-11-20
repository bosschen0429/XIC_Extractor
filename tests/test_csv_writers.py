from dataclasses import replace
from typing import Literal

import pytest

from xic_extractor.configuration.models import Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.extraction.result_assembly import build_extraction_result
from xic_extractor.extraction.result_models import ExtractionResult, FileResult
from xic_extractor.extraction.targeted_peak_selection import (
    build_production_peak_hypotheses,
    selected_peak_hypothesis,
)
from xic_extractor.output.csv_writers import (
    _long_output_rows,
)
from xic_extractor.output.schema import TARGETED_PRODUCT_PROJECTION_HEADERS
from xic_extractor.output.targeted_product_model import (
    TargetedProductProjection,
)
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.ms1_morphology import MS1_MORPHOLOGY_AREA_SOURCE


def _target(label: str, *, neutral_loss_da: float | None = 116.0474) -> Target:
    return Target(
        label=label,
        mz=258.1085,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=neutral_loss_da,
        nl_ppm_warn=20.0 if neutral_loss_da is not None else None,
        nl_ppm_max=50.0 if neutral_loss_da is not None else None,
        is_istd=False,
        istd_pair="",
    )


def _result(*, nl: NLResult | None = None) -> ExtractionResult:
    peak = PeakResult(
        rt=9.03,
        intensity=500.0,
        intensity_smoothed=450.0,
        area=123.45,
        peak_start=8.9,
        peak_end=9.2,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=9.0,
        selection_apex_intensity=450.0,
        selection_apex_index=10,
        raw_apex_rt=9.03,
        raw_apex_intensity=500.0,
        raw_apex_index=11,
        prominence=100.0,
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=12,
        max_smoothed=450.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        reason="concerns: local_sn (minor)",
        severities=((1, "local_sn"),),
    )
    return ExtractionResult(
        peak_result=peak_result,
        nl=nl,
        target_label="WithNL",
        role="Analyte",
        istd_pair="ISTD",
        reason="concerns: local_sn (minor)",
        severities=((1, "local_sn"),),
        prior_rt=None,
        prior_source="",
        quality_penalty=1,
        quality_flags=("too_broad",),
        targeted_product_projection=TargetedProductProjection(
            product_state="detected_flagged",
            counted_detection=True,
            review_state="flagged",
            projection_reason=(
                "decision: detected_flagged; review: targeted_acquired_signal_gaps"
            ),
            support_reasons=("ms1_peak_present",),
            review_reasons=("targeted_acquired_signal_gaps",),
        ),
    )


def _candidate_ms2_evidence(
    status: Literal["OK", "WARN", "NL_FAIL", "NO_MS2"],
    best_loss_ppm: float | None,
) -> CandidateMS2Evidence:
    return CandidateMS2Evidence(
        ms2_present=status != "NO_MS2",
        nl_match=status in {"OK", "WARN"},
        nl_status=status,
        trigger_scan_count=1 if status != "NO_MS2" else 0,
        strict_nl_scan_count=1 if status in {"OK", "WARN"} else 0,
        best_loss_ppm=best_loss_ppm,
        best_scan_rt=9.0 if best_loss_ppm is not None else None,
        best_product_base_ratio=0.5 if best_loss_ppm is not None else None,
        alignment_source="region" if status != "NO_MS2" else "none",
    )


def _file_result() -> FileResult:
    return FileResult(
        sample_name="SampleA",
        results={"WithNL": _result(nl=NLResult("WARN", 12.34, None, 3, 0, 2))},
        group=None,
    )


def test_row_builders_use_smoothed_rt_and_preserve_area() -> None:
    target = _target("WithNL")
    file_result = _file_result()

    long_row = _long_output_rows(file_result, [target])[0]

    assert long_row["RT"] == "9.0000"
    assert long_row["Area"] == "123.45"
    assert long_row["Int"] == "500"
    assert long_row["IntSmoothed"] == "450"
    assert "Confidence" not in long_row
    assert "Legacy Authority Status" not in long_row


def test_row_builders_report_selected_candidate_nl_before_target_window_nl() -> None:
    target = _target("WithNL")
    result = _result(nl=NLResult("OK", 1.0, 9.1, 3, 0, 3))
    result = replace(
        result,
        candidate_ms2_evidence=_candidate_ms2_evidence("NL_FAIL", 125.0),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    long_row = _long_output_rows(file_result, [target])[0]

    assert long_row["NL"] == "NL_FAIL"


def test_not_counted_product_projection_preserves_matrix_peak_values() -> None:
    target = _target("WithNL")
    result = replace(
        _result(nl=NLResult("NL_FAIL", 125.0, None, 1, 0, 1)),
        targeted_product_projection=TargetedProductProjection(
            product_state="not_counted",
            counted_detection=False,
            review_state="review_required",
            projection_reason=(
                "decision: not_counted; not_counted: "
                "identification_method_not_supported"
            ),
            support_reasons=("ms1_peak_present",),
            not_counted_reasons=("identification_method_not_supported",),
        ),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    long_row = _long_output_rows(file_result, [target])[0]

    expected_peak = {
        "RT": "9.0000",
        "Int": "500",
        "IntSmoothed": "450",
        "Area": "123.45",
        "PeakStart": "8.9000",
        "PeakEnd": "9.2000",
        "PeakWidth": "0.3000",
    }
    for suffix, value in expected_peak.items():
        assert long_row[suffix] == value
    assert long_row["NL"] == "NL_FAIL"
    assert long_row["Product State"] == "not_counted"
    assert long_row["Counted Detection"] == "FALSE"


def test_long_rows_use_targeted_product_projection() -> None:
    target = _target("WithNL")
    result = replace(
        _result(nl=NLResult("NL_FAIL", 125.0, None, 1, 0, 1)),
        reason="decision: review only, not counted; cap: VERY_LOW due to nl fail",
        targeted_product_projection=TargetedProductProjection(
            product_state="detected_flagged",
            counted_detection=True,
            review_state="flagged",
            projection_reason=(
                "decision: detected_flagged; support: paired_istd_identity; "
                "review: plausible_nl_dropout_review"
            ),
            support_reasons=("ms1_peak_present", "paired_istd_identity"),
            review_reasons=("plausible_nl_dropout_review",),
        ),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    long_row = _long_output_rows(file_result, [target])[0]

    assert long_row["NL"] == "NL_FAIL"
    assert (long_row["依據"], long_row["疑點"]) == ("ISTD 位置", "未見 NL")
    assert long_row["Product State"] == "detected_flagged"
    assert long_row["Counted Detection"] == "TRUE"
    assert long_row["Projection Review Reasons"] == "plausible_nl_dropout_review"


def test_long_row_labels_follow_not_counted_projection() -> None:
    target = _target("WithNL")
    result = replace(
        _result(nl=NLResult("OK", 125.0, 0.5, 1, 1, 1)),
        reason="decision: accepted",
        targeted_product_projection=TargetedProductProjection(
            product_state="not_counted",
            counted_detection=False,
            review_state="review_required",
            projection_reason=(
                "decision: not_counted; not_counted: missing_positive_ms1_peak"
            ),
            support_reasons=("candidate_aligned_ms2_nl",),
            not_counted_reasons=("missing_positive_ms1_peak",),
        ),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    long_row = _long_output_rows(file_result, [target])[0]

    assert long_row["Product State"] == "not_counted"
    assert long_row["Counted Detection"] == "FALSE"
    assert (long_row["依據"], long_row["疑點"]) == ("無可用 MS1 峰", "")


def test_long_labels_name_a_conflict_without_repeating_support() -> None:
    target = _target("WithNL")
    projection = TargetedProductProjection(
        product_state="ambiguous",
        counted_detection=False,
        review_state="review_required",
        projection_reason=(
            "decision: ambiguous; support: ms1_coherent, candidate_aligned_ms2_nl; "
            "conflict: ambiguous_evidence"
        ),
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=("ambiguous_evidence",),
    )
    result = replace(_result(), targeted_product_projection=projection)
    row = _long_output_rows(FileResult("SampleA", {target.label: result}), [target])[0]

    assert (row["依據"], row["疑點"]) == ("證據衝突", "")
    assert row["RT"] == "9.0000"
    assert row["Area"] == "123.45"
    assert row["Counted Detection"] == "FALSE"
    assert row["Projection Reason"] == projection.projection_reason
    assert row["Projection Support Reasons"] == "ms1_coherent; candidate_aligned_ms2_nl"
    assert row["Projection Conflict Reasons"] == "ambiguous_evidence"


def test_csv_rows_preserve_values_with_runtime_selected_hypothesis() -> None:
    target = _target("WithNL")
    legacy = _result(nl=NLResult("WARN", 12.34, None, 3, 0, 2))
    selected = selected_peak_hypothesis(
        build_production_peak_hypotheses(
            config=_Config(),
            sample_name="SampleA",
            target=target,
            peak_result=legacy.peak_result,
        )
    )
    assert selected is not None
    legacy_peak = legacy.peak_result.peak
    assert legacy_peak is not None
    selected = replace(
        selected,
        integration=replace(
            selected.integration,
            area_ms1_morphology=legacy_peak.area,
            ms1_morphology_area_source=MS1_MORPHOLOGY_AREA_SOURCE,
        ),
    )
    with_selected = build_extraction_result(
        peak_result=legacy.peak_result,
        nl_result=legacy.nl,
        candidate_ms2_evidence=legacy.candidate_ms2_evidence,
        target=target,
        candidate=legacy.peak_result.candidates[0],
        scoring_context_builder=None,
        selected_hypothesis=selected,
    )

    fallback_file = FileResult(sample_name="SampleA", results={"WithNL": legacy})
    selected_file = FileResult(
        sample_name="SampleA",
        results={"WithNL": with_selected},
    )

    selected_long = _long_output_rows(selected_file, [target])[0]
    fallback_long = _long_output_rows(fallback_file, [target])[0]
    for header, fallback_value in fallback_long.items():
        if header in {*TARGETED_PRODUCT_PROJECTION_HEADERS, "依據", "疑點"}:
            continue
        assert selected_long[header] == fallback_value
    assert fallback_long["Counted Detection"] == "TRUE"
    assert selected_long["Counted Detection"] == "FALSE"
    assert (
        selected_long["Projection Not Counted Reasons"] == "cid_nl_method_not_supported"
    )
    assert (selected_long["依據"], selected_long["疑點"]) == ("NL 偏差大", "證據單薄")
    assert selected_long["Product State"]
    assert selected_long["Counted Detection"] in {"TRUE", "FALSE"}


def test_csv_rows_project_selected_integration_values_when_present() -> None:
    target = _target("WithNL")
    result = replace(
        _result(nl=NLResult("WARN", 12.34, None, 3, 0, 2)),
        selected_hypothesis=_selected_hypothesis_with_integration(
            IntegrationResult(
                rt_left_min=8.7,
                rt_apex_min=8.95,
                rt_right_min=9.3,
                raw_apex_rt_min=8.96,
                rt_width_min=-0.42,
                height_raw=765.0,
                height_smoothed=700.0,
                area_raw_counts_seconds=9999.0,
                area_ms1_morphology=4567.89,
                ms1_morphology_area_source=MS1_MORPHOLOGY_AREA_SOURCE,
            )
        ),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    long_rows = _long_output_rows(file_result, [target])

    assert len(long_rows) == 1
    long_row = long_rows[0]
    assert long_row["RT"] == "8.9500"
    assert long_row["Area"] == "4567.89"
    assert long_row["Int"] == "765"
    assert long_row["IntSmoothed"] == "700"
    assert long_row["PeakStart"] == "8.7000"
    assert long_row["PeakEnd"] == "9.3000"
    assert long_row["PeakWidth"] == "0.4200"
    assert long_row["NL"] == "WARN_12.3ppm"
    assert (long_row["依據"], long_row["疑點"]) == ("NL 偏差大", "訊號缺口")


def test_csv_rows_preserve_no_peak_nd_projection() -> None:
    target = _target("WithNL")
    orphan_candidate = _result().peak_result.candidates[0]
    result = ExtractionResult(
        peak_result=PeakDetectionResult(
            status="NO_PEAK",
            peak=None,
            n_points=4,
            max_smoothed=0.0,
            n_prominent_peaks=0,
            candidates=(orphan_candidate,),
        ),
        nl=NLResult("NO_MS2", None, None, 0, 0, 0),
        target_label="WithNL",
        role="Analyte",
        istd_pair="",
        reason="",
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    long_row = _long_output_rows(file_result, [target])[0]

    assert long_row["RT"] == "ND"
    assert long_row["Area"] == "ND"
    assert long_row["Int"] == "ND"
    assert long_row["IntSmoothed"] == "ND"
    assert long_row["PeakStart"] == "ND"
    assert long_row["PeakEnd"] == "ND"
    assert long_row["PeakWidth"] == "ND"
    assert long_row["NL"] == "NO_MS2"


def test_not_quantifiable_row_is_blank_not_zero_or_absence():
    result = _result()
    result = replace(
        result,
        peak_result=replace(result.peak_result, peak=None, candidates=()),
        targeted_product_projection=TargetedProductProjection(
            "not_counted", False, "review_required", "finalized_not_quantifiable"
        ),
    )
    row = _long_output_rows(
        FileResult(sample_name="SampleA", results={"WithNL": result}),
        [_target("WithNL")],
    )[0]
    assert row["Area"] == ""
    assert row["RT"] == ""
    assert (row["依據"], row["疑點"]) == ("不足以定量", "")


class _Config:
    resolver_mode = "local_minimum"
    baseline_integration_method = "asls"
    count_no_ms2_as_detected = False
    ms1_morphology_smoothing_window_points = 15


def _selected_hypothesis_with_integration(
    integration: IntegrationResult,
) -> PeakHypothesis:
    return PeakHypothesis(
        hypothesis_id="SampleA|WithNL|selected",
        trace_group_id="SampleA|WithNL|targeted",
        target_label="WithNL",
        role="Analyte",
        istd_pair="ISTD",
        analysis_mode="targeted",
        resolver_mode="local_minimum",
        integration=integration,
        evidence=EvidenceVector(reason="selected spine"),
        audit=AuditTrail(selected=True),
    )


_IDENTIFIED = TargetedProductProjection(
    product_state="detected_flagged",
    counted_detection=True,
    review_state="flagged",
    projection_reason="decision: detected_flagged",
    support_reasons=("ms1_peak_present", "paired_istd_identity"),
    review_reasons=("missing_nl_support_review",),
)


@pytest.mark.parametrize(
    ("nl", "configured", "projection", "expected"),
    [
        (None, False, None, ("僅 MS1 峰", "訊號缺口")),
        (NLResult("OK", 1.0, 9.0, 1, 0, 1), True, None, ("NL 確認", "訊號缺口")),
        (
            NLResult("WARN", 25.0, 9.0, 1, 0, 1),
            True,
            None,
            ("NL 偏差大", "訊號缺口"),
        ),
        (
            NLResult("NL_FAIL", 125.0, None, 1, 0, 1),
            True,
            _IDENTIFIED,
            ("ISTD 位置", "未見 NL"),
        ),
        (
            NLResult("NO_MS2", None, None, 0, 0, 0),
            True,
            _IDENTIFIED,
            ("ISTD 位置", "無 MS2"),
        ),
    ],
)
def test_long_csv_labels_carry_the_configured_nl_observation(
    nl: NLResult | None,
    configured: bool,
    projection: TargetedProductProjection | None,
    expected: tuple[str, str],
) -> None:
    target = _target("WithNL", neutral_loss_da=116.0474 if configured else None)
    result = _result(nl=nl)
    if projection is not None:
        result = replace(result, targeted_product_projection=projection)
    row = _long_output_rows(FileResult("SampleA", {target.label: result}), [target])[0]
    assert (row["依據"], row["疑點"]) == expected
    assert row["Area"] == "123.45"


def test_long_csv_reports_file_processing_error() -> None:
    row = _long_output_rows(
        FileResult("Bad", {}, error="read failed"), [_target("WithNL")]
    )[0]
    assert (row["依據"], row["疑點"]) == ("RAW 失敗", "")
    assert row["Area"] == "ERROR"
    assert row["Counted Detection"] == "FALSE"


@pytest.mark.parametrize(
    ("group", "role", "expected"),
    [
        ("Control", "qc", "Control"),
        ("", "qc", "QC"),
        ("other", "qc", "QC"),
        ("", "sample", "Sample"),
        ("", "blank", "Blank"),
    ],
)
def test_long_output_group_uses_resolved_metadata(group, role, expected):
    from xic_extractor.sample_metadata import SampleMetadata

    target = _target("WithNL")
    result = FileResult(
        "Tumor_A",
        {target.label: _result(nl=None)},
        sample_metadata=SampleMetadata(
            "Tumor_A", "Tumor_A", group=group, sample_role=role
        ),
    )
    assert _long_output_rows(result, [target])[0]["Group"] == expected


def test_missing_metadata_does_not_guess_group_from_sample_name():
    target = _target("WithNL")
    result = FileResult("TumorQC_1", {target.label: _result(nl=None)})
    assert _long_output_rows(result, [target])[0]["Group"] == "Other"
