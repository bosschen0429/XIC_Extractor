from dataclasses import replace

import numpy as np
import pytest

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction import peak_candidate_table
from xic_extractor.extraction.peak_candidate_audit import (
    append_peak_audit_rows,
    append_selected_envelope_diagnostic_rows_from_hypotheses,
)
from xic_extractor.extraction.peak_hypothesis_adapter import (
    build_targeted_peak_hypotheses as build_peak_hypotheses,
)
from xic_extractor.peak_detection.traces import Trace, targeted_trace_group
from xic_extractor.signal_processing import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)


def test_peak_audit_appender_reuses_one_cwt_audit_result(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        emit_peak_candidates=True,
    )
    target = Target(
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
    peak_result = PeakDetectionResult(
        status="OK",
        peak=_candidate(8.5).peak,
        n_points=7,
        max_smoothed=100.0,
        n_prominent_peaks=1,
        candidates=(_candidate(8.5),),
    )
    calls = 0

    def _fake_cwt(peak_result, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        return peak_result

    monkeypatch.setattr(
        "xic_extractor.extraction.peak_candidate_audit.add_cwt_proposals_for_audit",
        _fake_cwt,
    )
    build_calls = 0
    real_build_peak_hypotheses = peak_candidate_table.build_peak_hypotheses

    def _count_build_peak_hypotheses(**kwargs):
        nonlocal build_calls
        build_calls += 1
        return real_build_peak_hypotheses(**kwargs)

    monkeypatch.setattr(
        peak_candidate_table,
        "build_peak_hypotheses",
        _count_build_peak_hypotheses,
    )
    candidate_rows: list[dict[str, str]] = []
    boundary_rows: list[dict[str, str]] = []

    append_peak_audit_rows(
        peak_candidate_rows=candidate_rows,
        peak_candidate_boundary_rows=boundary_rows,
        config=config,
        sample_name="SampleA",
        target=target,
        peak_result=peak_result,
        candidate_ms2_builder=lambda _candidate: None,
        rt=np.asarray([8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8]),
        intensity=np.asarray([10.0, 20.0, 70.0, 100.0, 70.0, 20.0, 10.0]),
    )

    assert calls == 1
    assert build_calls == 1
    assert candidate_rows
    assert boundary_rows
    assert candidate_rows[0]["area_baseline_corrected"] != ""
    assert candidate_rows[0]["area_uncertainty"] != ""
    assert candidate_rows[0]["area_uncertainty_formula_version"] == (
        "baseline_residual_mad_v1"
    )
    assert candidate_rows[0]["baseline_residual_mad"] != ""
    assert candidate_rows[0]["area_uncertainty_noise_source"] != ""


def test_peak_audit_product_selected_marker_survives_cwt_id_change(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        emit_peak_candidates=True,
    )
    target = Target(
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
    legacy = _candidate(8.4)
    successor = _candidate(8.6)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=legacy.peak,
        n_points=11,
        max_smoothed=100.0,
        n_prominent_peaks=2,
        candidates=(legacy, successor),
    )
    rt = np.linspace(8.0, 9.0, 11)
    intensity = np.asarray(
        [10.0, 20.0, 70.0, 100.0, 70.0, 60.0, 95.0, 65.0, 20.0, 10.0, 5.0]
    )
    product_selected_hypothesis = next(
        hypothesis
        for hypothesis in build_peak_hypotheses(
            sample_name="SampleA",
            target_label=target.label,
            role="Analyte",
            istd_pair="",
            resolver_mode=config.resolver_mode,
            peak_result=peak_result,
            rt=rt,
            intensity=intensity,
        )
        if hypothesis.integration.rt_apex_min == successor.selection_apex_rt
    )
    cwt_successor = replace(
        successor,
        proposal_sources=("legacy_savgol", "centwave_cwt"),
        cwt_best_scale=4.0,
        cwt_ridge_persistence=0.5,
    )

    def _fake_cwt(peak_result, *_args, **_kwargs):
        return replace(peak_result, candidates=(legacy, cwt_successor))

    monkeypatch.setattr(
        "xic_extractor.extraction.peak_candidate_audit.add_cwt_proposals_for_audit",
        _fake_cwt,
    )
    candidate_rows: list[dict[str, str]] = []
    boundary_rows: list[dict[str, str]] = []

    append_peak_audit_rows(
        peak_candidate_rows=candidate_rows,
        peak_candidate_boundary_rows=boundary_rows,
        config=config,
        sample_name="SampleA",
        target=target,
        peak_result=peak_result,
        candidate_ms2_builder=lambda _candidate: None,
        rt=rt,
        intensity=intensity,
        product_selected_candidate_id=product_selected_hypothesis.hypothesis_id,
        product_selected_hypothesis=product_selected_hypothesis,
    )

    selected_rows = [row for row in candidate_rows if row["selected"] == "TRUE"]
    assert len(selected_rows) == 1
    assert selected_rows[0]["proposal_sources"] == "legacy_savgol;centwave_cwt"
    assert selected_rows[0]["candidate_id"] != product_selected_hypothesis.hypothesis_id
    selected_boundary_rows = [
        row
        for row in boundary_rows
        if row["candidate_id"] == selected_rows[0]["candidate_id"]
    ]
    assert selected_boundary_rows
    assert {row["selected_candidate"] for row in selected_boundary_rows} == {"TRUE"}


def test_peak_audit_appender_uses_shared_trace_group_for_audit_rows(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        emit_peak_candidates=True,
    )
    target = Target(
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
    candidate = _candidate(8.5)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=7,
        max_smoothed=100.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    trace = Trace.from_arrays(
        sample_name="SampleA",
        mz=258.1085,
        rt=[8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8],
        intensity=[10.0, 20.0, 70.0, 100.0, 70.0, 20.0, 10.0],
        rt_min=8.2,
        rt_max=8.8,
        ppm_tol=20.0,
    )

    monkeypatch.setattr(
        "xic_extractor.extraction.peak_candidate_audit.add_cwt_proposals_for_audit",
        lambda peak_result, *_args, **_kwargs: peak_result,
    )
    candidate_rows: list[dict[str, str]] = []
    boundary_rows: list[dict[str, str]] = []

    append_peak_audit_rows(
        peak_candidate_rows=candidate_rows,
        peak_candidate_boundary_rows=boundary_rows,
        config=config,
        sample_name="SampleA",
        target=target,
        peak_result=peak_result,
        candidate_ms2_builder=lambda _candidate: None,
        rt=np.asarray([8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8]),
        intensity=np.zeros(7),
        trace_group=targeted_trace_group(
            trace,
            target_label="Analyte",
            resolver_mode="legacy_savgol",
        ),
    )

    assert candidate_rows[0]["area_baseline_corrected"] != "0.00000"
    assert any(row["area_raw_counts_seconds"] != "0.00" for row in boundary_rows)


def test_boundary_only_peak_audit_does_not_build_candidate_ms2(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        emit_peak_candidates=True,
    )
    target = Target(
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
    candidate = _candidate(8.5)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=7,
        max_smoothed=100.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    calls: list[PeakCandidate] = []

    monkeypatch.setattr(
        "xic_extractor.extraction.peak_candidate_audit.add_cwt_proposals_for_audit",
        lambda peak_result, *_args, **_kwargs: peak_result,
    )
    boundary_rows: list[dict[str, str]] = []

    def _candidate_ms2_builder(candidate: PeakCandidate):
        calls.append(candidate)
        return None

    append_peak_audit_rows(
        peak_candidate_rows=None,
        peak_candidate_boundary_rows=boundary_rows,
        config=config,
        sample_name="SampleA",
        target=target,
        peak_result=peak_result,
        candidate_ms2_builder=_candidate_ms2_builder,
        rt=np.asarray([8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8]),
        intensity=np.asarray([10.0, 20.0, 70.0, 100.0, 70.0, 20.0, 10.0]),
    )

    assert calls == []
    assert boundary_rows


def test_peak_audit_appender_builds_selected_envelope_diagnostic_rows(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        emit_peak_candidates=True,
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=0.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=False,
        istd_pair="",
    )
    candidate = _candidate(5.0)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=11,
        max_smoothed=100.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    rt = np.arange(11, dtype=float)
    intensity = np.asarray([10, 10, 12, 20, 40, 60, 40, 20, 12, 10, 10], dtype=float)
    product_selected_hypothesis = build_peak_hypotheses(
        sample_name="SampleA",
        target_label=target.label,
        role="Analyte",
        istd_pair="",
        resolver_mode=config.resolver_mode,
        peak_result=peak_result,
        rt=rt,
        intensity=intensity,
    )[0]

    monkeypatch.setattr(
        "xic_extractor.extraction.peak_candidate_audit.add_cwt_proposals_for_audit",
        lambda peak_result, *_args, **_kwargs: peak_result,
    )
    candidate_rows: list[dict[str, str]] = []
    boundary_rows: list[dict[str, str]] = []
    selected_envelope_rows: list[dict[str, str]] = []

    append_peak_audit_rows(
        peak_candidate_rows=candidate_rows,
        peak_candidate_boundary_rows=boundary_rows,
        selected_envelope_diagnostic_rows=selected_envelope_rows,
        config=config,
        sample_name="SampleA",
        target=target,
        peak_result=peak_result,
        candidate_ms2_builder=lambda _candidate: None,
        rt=rt,
        intensity=intensity,
        product_selected_hypothesis=product_selected_hypothesis,
    )

    assert len(selected_envelope_rows) == 1
    row = selected_envelope_rows[0]
    assert row["sample_name"] == "SampleA"
    assert row["target_label"] == "Analyte"
    assert row["selected_candidate_id"] == product_selected_hypothesis.hypothesis_id
    assert row["selected_boundary_mode"] == "selected_full_envelope"
    assert row["legacy_resolver_provenance"] == config.resolver_mode
    assert row["quantitation_context_rt_start"] == "0.00000"
    assert row["quantitation_context_rt_end"] == "10.00000"


def test_selected_envelope_diagnostics_skip_without_exactly_one_selected_hypothesis(
    tmp_path,
) -> None:
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        emit_peak_candidates=True,
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=0.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=False,
        istd_pair="",
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=_candidate(5.0).peak,
        n_points=11,
        max_smoothed=100.0,
        n_prominent_peaks=2,
        candidates=(_candidate(4.0), _candidate(6.0)),
    )
    rt = np.arange(11, dtype=float)
    intensity = np.asarray([10, 10, 12, 20, 40, 60, 40, 20, 12, 10, 10], dtype=float)
    hypotheses = build_peak_hypotheses(
        sample_name="SampleA",
        target_label=target.label,
        role="Analyte",
        istd_pair="",
        resolver_mode=config.resolver_mode,
        peak_result=peak_result,
        rt=rt,
        intensity=intensity,
    )

    rows: list[dict[str, str]] = []
    append_selected_envelope_diagnostic_rows_from_hypotheses(
        rows,
        config,
        "SampleA",
        hypotheses,
        rt=rt,
        intensity=intensity,
    )

    selected_one = peak_candidate_table.with_product_selected_marker(
        hypotheses,
        hypotheses[0].hypothesis_id,
    )
    selected_two = tuple(
        replace(
            hypothesis,
            audit=replace(hypothesis.audit, selected=True),
        )
        for hypothesis in hypotheses
    )

    append_selected_envelope_diagnostic_rows_from_hypotheses(
        rows,
        config,
        "SampleA",
        selected_two,
        rt=rt,
        intensity=intensity,
    )
    append_selected_envelope_diagnostic_rows_from_hypotheses(
        rows,
        replace(config, emit_peak_candidates=False),
        "SampleA",
        selected_one,
        rt=rt,
        intensity=intensity,
    )

    assert rows == []


def test_selected_envelope_diagnostics_prefer_product_selected_hypothesis_boundary(
    tmp_path,
) -> None:
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        emit_peak_candidates=True,
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=_candidate(5.0).peak,
        n_points=11,
        max_smoothed=100.0,
        n_prominent_peaks=1,
        candidates=(_candidate(5.0),),
    )
    rt = np.arange(11, dtype=float)
    intensity = np.asarray([10, 10, 12, 20, 40, 60, 40, 20, 12, 10, 10], dtype=float)
    hypotheses = build_peak_hypotheses(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode=config.resolver_mode,
        peak_result=peak_result,
        rt=rt,
        intensity=intensity,
    )
    product_selected_hypothesis = replace(
        hypotheses[0],
        integration=replace(
            hypotheses[0].integration,
            rt_left_min=4.0,
            rt_right_min=8.0,
            rt_width_min=4.0,
        ),
    )
    unselected = tuple(
        replace(
            hypothesis,
            audit=replace(hypothesis.audit, selected=False, selection_rank=None),
        )
        for hypothesis in hypotheses
    )
    rows: list[dict[str, str]] = []

    append_selected_envelope_diagnostic_rows_from_hypotheses(
        rows,
        config,
        "SampleA",
        unselected,
        rt=rt,
        intensity=intensity,
        product_selected_hypothesis=product_selected_hypothesis,
    )

    assert len(rows) == 1
    assert rows[0]["selected_candidate_id"] == product_selected_hypothesis.hypothesis_id
    assert rows[0]["resolver_rt_start"] == "4.00000"
    assert rows[0]["resolver_rt_end"] == "8.00000"


def test_selected_envelope_diagnostics_reject_malformed_selected_trace(
    tmp_path,
) -> None:
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        emit_peak_candidates=True,
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=_candidate(5.0).peak,
        n_points=11,
        max_smoothed=100.0,
        n_prominent_peaks=1,
        candidates=(_candidate(5.0),),
    )
    rt = np.arange(11, dtype=float)
    intensity = np.asarray([10, 10, 12, 20, 40, 60, 40, 20, 12, 10, 10], dtype=float)
    hypotheses = build_peak_hypotheses(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode=config.resolver_mode,
        peak_result=peak_result,
        rt=rt,
        intensity=intensity,
    )
    selected = peak_candidate_table.with_product_selected_marker(
        hypotheses,
        hypotheses[0].hypothesis_id,
    )
    malformed_rt = np.asarray([0, 1, 2, 3, 5, 4, 6, 7, 8, 9, 10], dtype=float)
    rows: list[dict[str, str]] = []

    with pytest.raises(ValueError, match="strictly increasing"):
        append_selected_envelope_diagnostic_rows_from_hypotheses(
            rows,
            config,
            "SampleA",
            selected,
            rt=malformed_rt,
            intensity=intensity,
        )


def _candidate(rt: float) -> PeakCandidate:
    peak = PeakResult(
        rt=rt,
        intensity=100.0,
        intensity_smoothed=95.0,
        area=1200.0,
        peak_start=rt - 0.2,
        peak_end=rt + 0.2,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=95.0,
        selection_apex_index=3,
        raw_apex_rt=rt,
        raw_apex_intensity=100.0,
        raw_apex_index=3,
        prominence=90.0,
        proposal_sources=("legacy_savgol",),
        source_apex_rank=1,
    )
