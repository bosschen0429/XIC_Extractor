from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from tools.diagnostics import pt_pattern_trace_diagnosis as diag
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows
from xic_extractor.xic_models import XICTrace


def _candidate_row() -> dict[str, str]:
    return {
        "sample_stem": "SampleA",
        "candidate_id": "SampleA|pt|500.0000|10.0000",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "pt_plus_1_mz": "501.0020",
        "pt_plus_1_expected_mz": "501.0021",
        "pt_plus_2_mz": "502.0021",
        "pt_plus_2_expected_mz": "502.0023",
        "pt_plus_4_mz": "504.0050",
        "pt_plus_4_expected_mz": "504.0052",
    }


def _trace(intensities: list[float]) -> XICTrace:
    return XICTrace.from_arrays(
        np.array([9.98, 10.00, 10.02], dtype=float),
        np.array(intensities, dtype=float),
    )


def test_build_trace_requests_uses_expected_mz_and_base_rt_window() -> None:
    requests = diag.build_trace_requests_for_candidate(
        _candidate_row(),
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
    )

    assert [item.position_label for item in requests] == [
        "base",
        "pt_plus_1",
        "pt_plus_2",
        "pt_plus_4",
    ]
    assert requests[0].request.mz == 500.0
    assert requests[1].request.mz == 501.0021
    assert requests[0].request.rt_min == 9.5
    assert requests[0].request.rt_max == 10.5
    assert all(item.request.ppm_tol == 20.0 for item in requests)


def test_diagnose_candidate_traces_marks_coeluting_family_supported() -> None:
    traces = {
        "base": _trace([0.0, 100.0, 0.0]),
        "pt_plus_1": _trace([0.0, 40.0, 0.0]),
        "pt_plus_2": _trace([0.0, 20.0, 0.0]),
        "pt_plus_4": _trace([0.0, 10.0, 0.0]),
    }

    diagnosis = diag.diagnose_candidate_traces(
        _candidate_row(),
        traces,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
        include_trace_points=True,
    )

    assert diagnosis.summary_row["trace_support_status"] == "supported"
    assert (
        diagnosis.summary_row["combined_pt_pattern_status"]
        == "pt_pattern_detected_diagnostic"
    )
    assert diagnosis.summary_row["scan_pattern_seed_status"] == "full_pattern_seeded"
    assert diagnosis.summary_row["non_base_positions_supported"] == 3
    assert diagnosis.summary_row["max_abs_apex_delta_sec"] == 0.0
    assert math.isclose(
        diagnosis.summary_row["min_pearson_r_to_base"],
        1.0,
    )
    assert len(diagnosis.metric_rows) == 4
    assert len(diagnosis.trace_point_rows) == 12


def test_diagnose_candidate_traces_keeps_shifted_shape_in_review() -> None:
    traces = {
        "base": _trace([0.0, 100.0, 0.0]),
        "pt_plus_1": _trace([40.0, 0.0, 0.0]),
        "pt_plus_2": _trace([0.0, 20.0, 0.0]),
        "pt_plus_4": _trace([0.0, 10.0, 0.0]),
    }

    diagnosis = diag.diagnose_candidate_traces(
        _candidate_row(),
        traces,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
    )

    plus_1_metric = next(
        row for row in diagnosis.metric_rows if row["position_label"] == "pt_plus_1"
    )
    assert plus_1_metric["trace_support_status"] == "review"
    assert plus_1_metric["reason"] == "rt_supported_shape_review"
    assert diagnosis.summary_row["trace_support_status"] == "partial_support"
    assert (
        diagnosis.summary_row["combined_pt_pattern_status"]
        == "pt_pattern_candidate_partial"
    )
    assert diagnosis.summary_row["non_base_positions_supported"] == 2


def test_run_trace_diagnosis_writes_summary_metrics_and_optional_points(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row()],
        diag.REQUIRED_CANDIDATE_COLUMNS,
    )

    class FakeRaw:
        def __init__(self) -> None:
            self.requests = ()

        def __enter__(self) -> "FakeRaw":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def extract_xic_many(self, requests):
            self.requests = requests
            return tuple(
                _trace([0.0, 100.0 if index == 0 else 25.0, 0.0])
                for index, _request in enumerate(requests)
            )

    fake_raw = FakeRaw()
    monkeypatch.setattr(diag, "open_raw", lambda raw, dll_dir: fake_raw)

    outputs = diag.run_trace_diagnosis(
        candidates_csv=candidates_csv,
        raw=tmp_path / "SampleA.raw",
        dll_dir=tmp_path,
        output_dir=tmp_path / "trace_diagnosis",
        limit=1,
        offset=0,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
        write_trace_points=True,
    )

    summary_rows = read_delimited_rows(
        outputs["summary_csv"],
        required_columns=(
            "candidate_id",
            "scan_pattern_seed_status",
            "trace_support_status",
            "combined_pt_pattern_status",
        ),
        delimiter=",",
    )
    metric_rows = read_delimited_rows(
        outputs["metrics_csv"],
        required_columns=("position_label", "pearson_r_to_base"),
        delimiter=",",
    )
    trace_point_rows = read_delimited_rows(
        outputs["trace_points_csv"],
        required_columns=("position_label", "rt_min", "intensity"),
        delimiter=",",
    )

    assert len(fake_raw.requests) == 4
    assert summary_rows[0]["trace_support_status"] == "supported"
    assert (
        summary_rows[0]["combined_pt_pattern_status"]
        == "pt_pattern_detected_diagnostic"
    )
    assert len(metric_rows) == 4
    assert len(trace_point_rows) == 12
    assert outputs["sidecar_json"].is_file()
