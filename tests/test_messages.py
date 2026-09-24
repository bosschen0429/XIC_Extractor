from pathlib import Path

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extractor import ExtractionResult
from xic_extractor.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.output.messages import (
    DiagnosticRecord,
    build_diagnostic_records,
    istd_confidence_note,
)
from xic_extractor.signal_processing import PeakDetectionResult, PeakResult


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
    from xic_extractor.extractor import DiagnosticIssue

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
