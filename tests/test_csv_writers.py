import csv
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.handoff_spine_runtime import (
    build_production_peak_hypotheses,
    selected_peak_hypothesis,
)
from xic_extractor.extraction.result_assembly import build_extraction_result
from xic_extractor.extractor import DiagnosticRecord, ExtractionResult, FileResult
from xic_extractor.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.output.csv_writers import (
    _long_output_rows,
    _output_row,
    write_all,
    write_diagnostics_csv,
    write_long_csv,
    write_score_breakdown_csv,
    write_wide_csv,
)
from xic_extractor.output.schema import TARGETED_PRODUCT_PROJECTION_HEADERS
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedProductProjection,
)
from xic_extractor.signal_processing import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)


def _config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "output" / "xic_results.csv",
        diagnostics_csv=tmp_path / "output" / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        count_no_ms2_as_detected=True,
    )


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
        confidence="LOW",
        reason="concerns: local_sn (minor)",
        severities=((1, "local_sn"),),
    )
    return ExtractionResult(
        peak_result=peak_result,
        nl=nl,
        target_label="WithNL",
        role="Analyte",
        istd_pair="ISTD",
        confidence="LOW",
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
            projection_reason="decision: detected_flagged; review: local_sn_minor",
            support_reasons=("ms1_peak_present",),
            review_reasons=("local_sn_minor",),
            legacy_evidence={"confidence": "LOW"},
            legacy_authority_status="evidence_only",
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


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_row_builders_use_smoothed_rt_and_preserve_area() -> None:
    target = _target("WithNL")
    file_result = _file_result()

    wide_row = _output_row(file_result, [target])
    long_row = _long_output_rows(file_result, [target])[0]

    assert wide_row["WithNL_RT"] == "9.0000"
    assert wide_row["WithNL_Area"] == "123.45"
    assert wide_row["WithNL_Int"] == "500"
    assert wide_row["WithNL_IntSmoothed"] == "450"
    assert wide_row["WithNL_NL"] == "WARN_12.3ppm"
    assert long_row["RT"] == "9.0000"
    assert long_row["Area"] == "123.45"
    assert long_row["Int"] == "500"
    assert long_row["IntSmoothed"] == "450"
    assert long_row["Confidence"] == "LOW"


def test_row_builders_report_selected_candidate_nl_before_target_window_nl() -> None:
    target = _target("WithNL")
    result = _result(nl=NLResult("OK", 1.0, 9.1, 3, 0, 3))
    result = replace(
        result,
        candidate_ms2_evidence=_candidate_ms2_evidence("NL_FAIL", 125.0),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    wide_row = _output_row(file_result, [target])
    long_row = _long_output_rows(file_result, [target])[0]

    assert wide_row["WithNL_NL"] == "NL_FAIL"
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
                "decision: not_counted; not_counted: false_positive_peak"
            ),
            support_reasons=("ms1_peak_present",),
            not_counted_reasons=("false_positive_peak",),
            legacy_evidence={"confidence": "VERY_LOW", "nl_status": "NL_FAIL"},
            legacy_authority_status="evidence_only",
        ),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    wide_row = _output_row(file_result, [target])
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
        assert wide_row[f"WithNL_{suffix}"] == value
        assert long_row[suffix] == value
    assert wide_row["WithNL_NL"] == "NL_FAIL"
    assert long_row["NL"] == "NL_FAIL"
    assert long_row["Product State"] == "not_counted"
    assert long_row["Counted Detection"] == "FALSE"


def test_write_wide_and_long_csv(tmp_path: Path) -> None:
    config = _config(tmp_path)
    target = _target("WithNL")
    file_results = [_file_result()]

    write_wide_csv(config, [target], file_results)
    write_long_csv(config, [target], file_results)

    assert _read_csv(config.output_csv)[0]["WithNL_RT"] == "9.0000"
    assert _read_csv(config.output_csv.with_name("xic_results_long.csv"))[0] == {
        "SampleName": "SampleA",
        "Group": "Other",
        "Target": "WithNL",
        "Role": "Analyte",
        "ISTD Pair": "",
        "RT": "9.0000",
        "Area": "123.45",
        "NL": "WARN_12.3ppm",
        "NL Evidence": "Observed with tolerance warning",
        "Int": "500",
        "IntSmoothed": "450",
        "PeakStart": "8.9000",
        "PeakEnd": "9.2000",
        "PeakWidth": "0.3000",
        "Confidence": "LOW",
        "Reason": "MS1 peak measured; review: weak local signal-to-noise.",
        "Product State": "detected_flagged",
        "Counted Detection": "TRUE",
        "Review State": "flagged",
        "Projection Reason": "decision: detected_flagged; review: local_sn_minor",
        "Projection Support Reasons": "ms1_peak_present",
        "Projection Review Reasons": "local_sn_minor",
        "Projection Conflict Reasons": "",
        "Projection Not Counted Reasons": "",
        "Projection Exclusion Reasons": "",
        "Legacy Authority Status": "evidence_only",
        "Benchmark Eligibility State": "",
    }


def test_write_wide_csv_bytes_are_exact_and_deterministic(tmp_path: Path) -> None:
    config = _config(tmp_path)
    target = _target("WithNL")
    file_results = [
        _file_result(),
        FileResult(sample_name="FailedSample", results={}, error="read failed"),
    ]
    expected = (
        b"\xef\xbb\xbf"
        b"SampleName,WithNL_RT,WithNL_Int,WithNL_IntSmoothed,WithNL_Area,WithNL_PeakStart,"
        b"WithNL_PeakEnd,WithNL_PeakWidth,WithNL_NL\r\n"
        b"SampleA,9.0000,500,450,123.45,8.9000,9.2000,0.3000,WARN_12.3ppm\r\n"
        b"FailedSample,ERROR,ERROR,ERROR,ERROR,ERROR,ERROR,ERROR,ERROR\r\n"
    )

    write_wide_csv(config, [target], file_results)
    first = config.output_csv.read_bytes()
    write_wide_csv(config, [target], file_results)

    assert config.output_csv.name == "xic_results.csv"
    assert first == expected
    assert config.output_csv.read_bytes() == expected


def test_write_wide_csv_preserves_zero_nd_and_duplicate_sample_rows(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    target = _target("WithNL")
    zero_result = replace(
        _result(nl=NLResult("WARN", 12.34, None, 3, 0, 2)),
        selected_hypothesis=_selected_hypothesis_with_integration(
            IntegrationResult(
                rt_left_min=0.0,
                rt_apex_min=0.0,
                rt_right_min=0.0,
                raw_apex_rt_min=0.0,
                rt_width_min=0.0,
                height_raw=0.0,
                height_smoothed=0.0,
                area_raw_counts_seconds=0.0,
            )
        ),
    )
    partial_result = replace(
        zero_result,
        selected_hypothesis=_selected_hypothesis_with_integration(
            IntegrationResult(
                rt_left_min=8.9,
                rt_apex_min=9.0,
                rt_right_min=9.2,
                raw_apex_rt_min=9.03,
                rt_width_min=0.3,
                height_raw=500.0,
                height_smoothed=450.0,
                area_raw_counts_seconds=None,
            )
        ),
    )
    file_results = [
        FileResult(sample_name="DuplicateSample", results={"WithNL": zero_result}),
        FileResult(
            sample_name="DuplicateSample",
            results={"WithNL": partial_result},
        ),
    ]

    write_wide_csv(config, [target], file_results)
    rows = _read_csv(config.output_csv)

    assert [row["SampleName"] for row in rows] == [
        "DuplicateSample",
        "DuplicateSample",
    ]
    assert rows[0] == {
        "SampleName": "DuplicateSample",
        "WithNL_RT": "0.0000",
        "WithNL_Int": "0",
        "WithNL_IntSmoothed": "0",
        "WithNL_Area": "0.00",
        "WithNL_PeakStart": "0.0000",
        "WithNL_PeakEnd": "0.0000",
        "WithNL_PeakWidth": "0.0000",
        "WithNL_NL": "WARN_12.3ppm",
    }
    assert rows[1] == {
        "SampleName": "DuplicateSample",
        "WithNL_RT": "ND",
        "WithNL_Int": "ND",
        "WithNL_IntSmoothed": "ND",
        "WithNL_Area": "ND",
        "WithNL_PeakStart": "ND",
        "WithNL_PeakEnd": "ND",
        "WithNL_PeakWidth": "ND",
        "WithNL_NL": "WARN_12.3ppm",
    }


def test_write_diagnostics_and_score_breakdown_csv(tmp_path: Path) -> None:
    config = _config(tmp_path)
    file_result = _file_result()
    diagnostics = [
        DiagnosticRecord(
            sample_name="SampleA",
            target_label="WithNL",
            issue="NL_FAIL",
            reason="best match 80 ppm",
        )
    ]

    write_diagnostics_csv(config, diagnostics)
    write_score_breakdown_csv(config, [file_result])

    assert _read_csv(config.diagnostics_csv) == [
        {
            "SampleName": "SampleA",
            "Target": "WithNL",
            "Issue": "NL_FAIL",
            "Reason": "best match 80 ppm",
        }
    ]
    breakdown = _read_csv(config.output_csv.with_name("xic_score_breakdown.csv"))[0]
    assert breakdown["local_sn"] == "1"
    assert breakdown["Quality Penalty"] == "1"
    assert breakdown["Quality Flags"] == "too_broad"
    assert breakdown["Total Severity"] == "2"
    assert breakdown["Prior RT"] == "NA"


def test_score_breakdown_csv_includes_weighted_evidence_fields(tmp_path: Path) -> None:
    config = _config(tmp_path)
    result = replace(
        _result(),
        score_breakdown=(
            ("Base Score", "50"),
            ("Positive Points", "40"),
            ("Negative Points", "0"),
            ("Raw Score", "90"),
            ("Caps", ""),
            ("Final Confidence", "HIGH"),
            ("Support", "strict_nl_ok; local_sn_strong"),
            ("Concerns", ""),
        ),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    write_score_breakdown_csv(config, [file_result])

    breakdown = _read_csv(config.output_csv.with_name("xic_score_breakdown.csv"))[0]
    assert breakdown["Base Score"] == "50"
    assert breakdown["Positive Points"] == "40"
    assert breakdown["Negative Points"] == "0"
    assert breakdown["Raw Score"] == "90"
    assert breakdown["Caps"] == ""
    assert breakdown["Final Confidence"] == "HIGH"
    assert breakdown["Detection Counted"] == "TRUE"
    assert breakdown["Support"] == "strict_nl_ok; local_sn_strong"
    assert breakdown["Concerns"] == ""


def test_long_and_score_breakdown_rows_use_targeted_product_projection(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    target = _target("WithNL")
    result = replace(
        _result(nl=NLResult("NL_FAIL", 125.0, None, 1, 0, 1)),
        confidence="VERY_LOW",
        targeted_product_projection=TargetedProductProjection(
            product_state="detected_flagged",
            counted_detection=True,
            review_state="flagged",
            projection_reason=(
                "decision: detected_flagged; review: plausible_dda_nl_dropout"
            ),
            support_reasons=("ms1_peak_present",),
            review_reasons=("plausible_dda_nl_dropout",),
            legacy_evidence={"confidence": "VERY_LOW", "nl_status": "NL_FAIL"},
            legacy_authority_status="evidence_only",
        ),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    long_row = _long_output_rows(file_result, [target])[0]
    write_score_breakdown_csv(config, [file_result])

    assert long_row["NL"] == "NL_FAIL"
    assert long_row["Confidence"] == "VERY_LOW"
    assert (
        long_row["Reason"]
        == "MS1 peak measured; review: MS2/NL may be missing from DDA sampling."
    )
    assert long_row["Product State"] == "detected_flagged"
    assert long_row["Counted Detection"] == "TRUE"
    assert long_row["Projection Review Reasons"] == "plausible_dda_nl_dropout"
    breakdown = _read_csv(config.output_csv.with_name("xic_score_breakdown.csv"))[0]
    assert breakdown["Detection Counted"] == "TRUE"
    assert breakdown["Product State"] == "detected_flagged"
    assert breakdown["Projection Reason"].startswith("decision: detected_flagged")


def test_long_row_reason_prefers_projection_over_legacy_not_counted_text() -> None:
    target = _target("WithNL")
    result = replace(
        _result(nl=NLResult("NL_FAIL", 125.0, None, 1, 0, 1)),
        confidence="VERY_LOW",
        reason="decision: review only, not counted; cap: VERY_LOW due to nl fail",
        targeted_product_projection=TargetedProductProjection(
            product_state="detected_flagged",
            counted_detection=True,
            review_state="flagged",
            projection_reason=(
                "decision: detected_flagged; support: role_aware_rt_support; "
                "review: plausible_dda_nl_dropout"
            ),
            support_reasons=("role_aware_rt_support",),
            review_reasons=("plausible_dda_nl_dropout",),
            legacy_evidence={"confidence": "VERY_LOW", "nl_status": "NL_FAIL"},
            legacy_authority_status="evidence_only",
        ),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    long_row = _long_output_rows(file_result, [target])[0]

    assert long_row["Counted Detection"] == "TRUE"
    assert long_row["Product State"] == "detected_flagged"
    assert long_row["Reason"].startswith("MS1 peak measured; review:")
    assert "not counted" not in long_row["Reason"]


def test_long_row_confidence_follows_not_counted_projection() -> None:
    target = _target("WithNL")
    result = replace(
        _result(nl=NLResult("OK", 125.0, 0.5, 1, 1, 1)),
        confidence="HIGH",
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
            legacy_evidence={"confidence": "HIGH", "nl_status": "OK"},
            legacy_authority_status="evidence_only",
        ),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    long_row = _long_output_rows(file_result, [target])[0]

    assert long_row["Product State"] == "not_counted"
    assert long_row["Counted Detection"] == "FALSE"
    assert long_row["Confidence"] == "VERY_LOW"
    assert (
        long_row["Reason"]
        == "MS1 peak measured; review: no positive MS1 peak available."
    )


def test_long_reason_explains_conflict_without_repeating_support() -> None:
    target = _target("WithNL")
    projection = TargetedProductProjection(
        product_state="ambiguous",
        counted_detection=False,
        review_state="review_required",
        projection_reason=(
            "decision: ambiguous; support: ms1_coherent, candidate_aligned_ms2_nl; "
            "review: targeted_rt_review; conflict: targeted_rt_conflict"
        ),
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        review_reasons=("targeted_rt_review",),
        conflict_reasons=("targeted_rt_conflict",),
    )
    result = replace(_result(), targeted_product_projection=projection)
    row = _long_output_rows(FileResult("SampleA", {target.label: result}), [target])[0]

    assert row["Reason"] == (
        "MS1 peak measured; review: "
        "peak position conflicts with the RT reference/window."
    )
    assert row["RT"] == "9.0000"
    assert row["Area"] == "123.45"
    assert row["Counted Detection"] == "FALSE"
    assert row["Projection Reason"] == projection.projection_reason
    assert row["Projection Support Reasons"] == "ms1_coherent; candidate_aligned_ms2_nl"
    assert row["Projection Conflict Reasons"] == "targeted_rt_conflict"

    result = replace(
        result, score_breakdown=(("Concerns", "rt_centrality_borderline"),)
    )
    precise_row = _long_output_rows(
        FileResult("SampleA", {target.label: result}), [target]
    )[0]
    assert precise_row["Reason"] == (
        "MS1 peak measured; review: peak is off-centre in the RT window (borderline)."
    )


def test_long_reason_prioritizes_unresolved_boundaries_over_review_flags() -> None:
    target = _target("WithNL")
    projection = TargetedProductProjection(
        product_state="not_counted",
        counted_detection=False,
        review_state="review_required",
        projection_reason="decision: not_counted; review: legacy_confidence_review",
        review_reasons=(
            "legacy_confidence_review",
            "selected_envelope_split_supported",
        ),
        not_counted_reasons=("selected_envelope_boundary_externalize",),
    )
    result = replace(_result(), targeted_product_projection=projection)
    row = _long_output_rows(FileResult("SampleA", {target.label: result}), [target])[0]

    assert (
        row["Reason"]
        == "MS1 peak measured; review: peak/integration boundaries are unresolved."
    )
    assert row["Projection Review Reasons"] == (
        "legacy_confidence_review; selected_envelope_split_supported"
    )


def test_score_breakdown_csv_can_emit_ms2_trace_labels_without_schema_change(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    result = replace(
        _result(),
        score_breakdown=(
            ("Base Score", "50"),
            ("Positive Points", "50"),
            ("Negative Points", "8"),
            ("Raw Score", "92"),
            ("Caps", ""),
            ("Final Confidence", "HIGH"),
            ("Support", "strict_nl_ok; ms2_trace_strong; local_sn_strong"),
            ("Concerns", "ms2_trace_weak"),
        ),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    write_score_breakdown_csv(config, [file_result])

    breakdown = _read_csv(config.output_csv.with_name("xic_score_breakdown.csv"))[0]
    assert set(breakdown) >= {
        "Support",
        "Concerns",
        "Positive Points",
        "Negative Points",
        "Raw Score",
    }
    assert breakdown["Support"] == ("strict_nl_ok; ms2_trace_strong; local_sn_strong")
    assert breakdown["Concerns"] == "ms2_trace_weak"


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

    assert _output_row(selected_file, [target]) == _output_row(
        fallback_file,
        [target],
    )
    selected_long = _long_output_rows(selected_file, [target])[0]
    fallback_long = _long_output_rows(fallback_file, [target])[0]
    for header, fallback_value in fallback_long.items():
        if header in {*TARGETED_PRODUCT_PROJECTION_HEADERS, "Reason"}:
            continue
        assert selected_long[header] == fallback_value
    assert selected_long["Reason"].startswith("MS1 peak measured; review:")
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
                area_raw_counts_seconds=4567.89,
            )
        ),
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    wide_row = _output_row(file_result, [target])
    long_rows = _long_output_rows(file_result, [target])

    assert len(long_rows) == 1
    long_row = long_rows[0]
    assert list(wide_row) == [
        "SampleName",
        "WithNL_RT",
        "WithNL_Int",
        "WithNL_IntSmoothed",
        "WithNL_Area",
        "WithNL_PeakStart",
        "WithNL_PeakEnd",
        "WithNL_PeakWidth",
        "WithNL_NL",
    ]
    assert wide_row["WithNL_RT"] == "8.9500"
    assert wide_row["WithNL_Area"] == "4567.89"
    assert wide_row["WithNL_Int"] == "765"
    assert wide_row["WithNL_IntSmoothed"] == "700"
    assert wide_row["WithNL_PeakStart"] == "8.7000"
    assert wide_row["WithNL_PeakEnd"] == "9.3000"
    assert wide_row["WithNL_PeakWidth"] == "0.4200"
    assert wide_row["WithNL_NL"] == "WARN_12.3ppm"
    assert long_row["RT"] == "8.9500"
    assert long_row["Area"] == "4567.89"
    assert long_row["Int"] == "765"
    assert long_row["IntSmoothed"] == "700"
    assert long_row["PeakStart"] == "8.7000"
    assert long_row["PeakEnd"] == "9.3000"
    assert long_row["PeakWidth"] == "0.4200"
    assert long_row["NL"] == "WARN_12.3ppm"
    assert long_row["Confidence"] == "LOW"
    assert (
        long_row["Reason"] == "MS1 peak measured; review: weak local signal-to-noise."
    )


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
        confidence="",
        reason="",
    )
    file_result = FileResult(sample_name="SampleA", results={"WithNL": result})

    wide_row = _output_row(file_result, [target])
    long_row = _long_output_rows(file_result, [target])[0]

    for suffix in ("RT", "Int", "Area", "PeakStart", "PeakEnd", "PeakWidth"):
        assert wide_row[f"WithNL_{suffix}"] == "ND"
    assert wide_row["WithNL_NL"] == "NO_MS2"
    assert long_row["RT"] == "ND"
    assert long_row["Area"] == "ND"
    assert long_row["Int"] == "ND"
    assert long_row["IntSmoothed"] == "ND"
    assert long_row["PeakStart"] == "ND"
    assert long_row["PeakEnd"] == "ND"
    assert long_row["PeakWidth"] == "ND"
    assert long_row["NL"] == "NO_MS2"


def test_write_all_gates_score_breakdown(tmp_path: Path) -> None:
    config = _config(tmp_path)
    target = _target("WithNL")
    file_results = [_file_result()]

    write_all(
        config,
        [target],
        file_results,
        diagnostics=[],
        emit_score_breakdown=False,
    )
    assert config.output_csv.exists()
    assert not config.output_csv.with_name("xic_score_breakdown.csv").exists()

    write_all(
        config,
        [target],
        file_results,
        diagnostics=[],
        emit_score_breakdown=True,
    )
    assert config.output_csv.with_name("xic_score_breakdown.csv").exists()


class _Config:
    resolver_mode = "local_minimum"


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
        evidence=EvidenceVector(confidence="LOW", reason="selected spine"),
        audit=AuditTrail(selected=True, selection_rank=1),
    )


@pytest.mark.parametrize(
    ("nl", "configured", "expected"),
    [
        (None, False, "Not requested"),
        (NLResult("OK", 1.0, 9.0, 1, 0, 1), True, "Observed"),
        (NLResult("WARN", 25.0, 9.0, 1, 0, 1), True, "Observed with tolerance warning"),
        (
            NLResult("NL_FAIL", 125.0, None, 1, 0, 1),
            True,
            "Not observed within tolerance",
        ),
        (NLResult("NO_MS2", None, None, 0, 0, 0), True, "No MS2 spectrum"),
    ],
)
def test_long_csv_reports_configured_nl_observation(
    tmp_path: Path, nl: NLResult | None, configured: bool, expected: str
) -> None:
    config = _config(tmp_path)
    target = _target("WithNL", neutral_loss_da=116.0474 if configured else None)
    write_long_csv(
        config, [target], [FileResult("SampleA", {target.label: _result(nl=nl)})]
    )
    row = _read_csv(config.output_csv.with_name("xic_results_long.csv"))[0]
    assert row["NL Evidence"] == expected
    assert row["Area"] == "123.45"


def test_long_csv_reports_file_processing_error(tmp_path: Path) -> None:
    config = _config(tmp_path)
    write_long_csv(
        config, [_target("WithNL")], [FileResult("Bad", {}, error="read failed")]
    )
    row = _read_csv(config.output_csv.with_name("xic_results_long.csv"))[0]
    assert row["NL Evidence"] == "Processing error"
    assert row["Reason"] == "RAW file could not be processed."
    assert row["Area"] == "ERROR"
    assert row["Counted Detection"] == "FALSE"
