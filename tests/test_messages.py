from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.extraction.result_models import ExtractionResult
from xic_extractor.extraction.target_extraction import extract_one_target
from xic_extractor.output.messages import (
    DiagnosticRecord,
    build_diagnostic_records,
    istd_confidence_note,
)
from xic_extractor.peak_detection.models import PeakDetectionResult, PeakResult
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)


@pytest.mark.parametrize(
    "point_count, expected_reason",
    [
        (2, "insufficient_points_for_profile"),
        (21, "no_signal_above_baseline"),
    ],
)
def test_physical_unavailability_keeps_owner_reason_in_diagnostics(
    point_count: int,
    expected_reason: str,
) -> None:
    evaluation = evaluate_physical_peak(
        prepare_physical_signal(
            [8.0 + index * 0.1 for index in range(point_count)],
            [0.0] * point_count,
        ),
        selection_subject_id="WithNL",
        query_rt_min=9.0,
        request_context_rt_start_min=8.0,
        request_context_rt_end_min=10.0,
    )
    assert evaluation.state == "unavailable"
    assert evaluation.reason == expected_reason
    result = ExtractionResult(
        peak_result=PeakDetectionResult(
            status="NO_SIGNAL" if point_count == 21 else "PEAK_NOT_FOUND",
            peak=None,
            n_points=point_count,
            max_smoothed=None,
            n_prominent_peaks=0,
        ),
        nl=None,
        physical_evaluation=evaluation,
    )

    records = build_diagnostic_records("SampleA", _target("WithNL"), result, _config())

    assert len(records) == 1
    assert expected_reason in records[0].reason
    assert "prominence" not in records[0].reason


def test_build_diagnostic_records_reports_peak_and_nl_failures() -> None:
    result = ExtractionResult(
        peak_result=PeakDetectionResult(
            status="PEAK_NOT_FOUND",
            peak=None,
            n_points=8,
            max_smoothed=1234.0,
            n_prominent_peaks=0,
        ),
        nl=NLResult(
            status="NL_FAIL",
            best_ppm=80.0,
            best_scan_rt=9.12,
            valid_ms2_scan_count=2,
            parse_error_count=0,
            matched_scan_count=2,
        ),
        target_label="WithNL",
    )

    records = build_diagnostic_records("SampleA", _target("WithNL"), result, _config())

    assert [record.issue for record in records] == ["PEAK_NOT_FOUND", "NL_FAIL"]
    assert "max=1234" in records[0].reason
    assert "best observed-loss error 80.0 ppm" in records[1].reason
    assert "quadrature window at 50 ppm per ion" in records[1].reason


def test_multi_peak_diagnostic_reports_formally_selected_lower_peak() -> None:
    target = replace(
        _target("NoNL"), neutral_loss_da=None, nl_ppm_warn=None, nl_ppm_max=None
    )
    rt = np.linspace(6.0, 12.0, 1201)
    intensity = (
        1000.0 * np.exp(-0.5 * ((rt - 8.5) / 0.05) ** 2)
        + 200.0 * np.exp(-0.5 * ((rt - 9.0) / 0.05) ** 2)
        + 1500.0 * np.exp(-0.5 * ((rt - 11.0) / 0.05) ** 2)
    )

    class Raw:
        def extract_xic(self, _mz, rt_min, rt_max, _ppm_tol):
            mask = (rt >= rt_min) & (rt <= rt_max)
            return rt[mask], intensity[mask]

    results = {}
    diagnostics = []
    extract_one_target(
        Raw(),
        _config(),
        "SampleA",
        target,
        reference_rt=None,
        results=results,
        diagnostics=diagnostics,
    )
    result = results[target.label]
    assert result.reported_rt == pytest.approx(9.0, abs=0.01)
    assert result.reported_peak_intensity < 300.0
    assert [
        candidate.peak.rt for candidate in result.peak_result.candidates
    ] == pytest.approx([8.5, 9.0, 11.0], abs=0.01)
    assert result.peak_result.n_prominent_peaks == 3
    record = next(record for record in diagnostics if record.issue == "MULTI_PEAK")
    assert "3 qualified physical candidates in retained trace context" in record.reason
    assert f"selected RT {result.reported_rt:.4f} min" in record.reason
    assert "tallest" not in record.reason


def test_build_diagnostic_records_reports_selected_candidate_observed_loss() -> None:
    result = ExtractionResult(
        peak_result=PeakDetectionResult(
            status="OK",
            peak=PeakResult(
                rt=9.0,
                intensity=500.0,
                intensity_smoothed=450.0,
                area=100.0,
                peak_start=8.9,
                peak_end=9.2,
            ),
            n_points=12,
            max_smoothed=450.0,
            n_prominent_peaks=1,
        ),
        nl=NLResult("OK", 1.0, 9.1, 2, 0, 2),
        candidate_ms2_evidence=CandidateMS2Evidence(
            ms2_present=True,
            nl_match=False,
            nl_status="NL_FAIL",
            trigger_scan_count=1,
            strict_nl_scan_count=0,
            best_loss_ppm=125.0,
            best_scan_rt=9.0,
            best_product_base_ratio=0.7,
            alignment_source="region",
        ),
        target_label="WithNL",
    )

    records = build_diagnostic_records("SampleA", _target("WithNL"), result, _config())

    assert [record.issue for record in records] == ["NL_FAIL"]
    assert "selected candidate" in records[0].reason
    assert "strict observed neutral loss" in records[0].reason
    assert "best observed-loss error 125.0 ppm" in records[0].reason
    assert "expected product m/z" not in records[0].reason


def test_build_diagnostic_records_reports_product_probe_subcause() -> None:
    result = ExtractionResult(
        peak_result=PeakDetectionResult(
            status="OK",
            peak=PeakResult(
                rt=9.0,
                intensity=500.0,
                intensity_smoothed=450.0,
                area=100.0,
                peak_start=8.9,
                peak_end=9.2,
            ),
            n_points=12,
            max_smoothed=450.0,
            n_prominent_peaks=1,
        ),
        nl=NLResult("OK", 1.0, 9.1, 2, 0, 2),
        candidate_ms2_evidence=CandidateMS2Evidence(
            ms2_present=True,
            nl_match=False,
            nl_status="NL_FAIL",
            trigger_scan_count=1,
            strict_nl_scan_count=0,
            best_loss_ppm=None,
            best_scan_rt=9.0,
            best_product_base_ratio=None,
            alignment_source="region",
            diagnostic_product_absence_reason="product_outside_diagnostic_window",
            nearest_product_loss_ppm=82.3,
            nearest_product_base_ratio=0.004,
            nearest_product_mz=179.1234,
        ),
        target_label="WithNL",
    )

    records = build_diagnostic_records("SampleA", _target("WithNL"), result, _config())

    assert [record.issue for record in records] == ["NL_FAIL"]
    assert "subcause=product_outside_diagnostic_window" in records[0].reason
    assert "nearest product m/z 179.1234" in records[0].reason
    assert "nearest observed-loss error 82.3 ppm" in records[0].reason
    assert "nearest product/base ratio 0.004" in records[0].reason


def test_build_diagnostic_records_reports_istd_confidence() -> None:
    result = ExtractionResult(
        peak_result=PeakDetectionResult(
            status="OK",
            peak=PeakResult(
                rt=9.0,
                intensity=500.0,
                intensity_smoothed=450.0,
                area=100.0,
                peak_start=8.9,
                peak_end=9.2,
            ),
            n_points=12,
            max_smoothed=450.0,
            n_prominent_peaks=1,
        ),
        nl=NLResult("WARN", 18.0, 9.0, 2, 0, 2),
        target_label="ISTD",
    )

    records = build_diagnostic_records(
        "SampleA", _target("ISTD", is_istd=True), result, _config()
    )

    assert records == [
        DiagnosticRecord(
            sample_name="SampleA",
            target_label="ISTD",
            issue="ISTD_CONFIDENCE",
            reason=(
                "ISTD confidence=MEDIUM; flags=NL_WARN; "
                "MS1 peak retained because ISTD NL evidence is diagnostic support, "
                "not a hard detection gate"
            ),
        )
    ]
    assert istd_confidence_note("LOW") == "ISTD anchor was LOW"


def test_extractor_re_exports_diagnostic_issue_for_legacy_imports() -> None:
    from xic_extractor.output.messages import DiagnosticIssue

    assert DiagnosticIssue is not None


def _config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("."),
        dll_dir=Path("."),
        output_csv=Path("out.csv"),
        diagnostics_csv=Path("diag.csv"),
        smooth_window=7,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
    )


def _target(label: str, *, is_istd: bool = False) -> Target:
    return Target(
        label=label,
        mz=100.0,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=is_istd,
        istd_pair="",
    )
