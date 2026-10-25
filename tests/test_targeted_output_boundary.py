from __future__ import annotations

import inspect
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import replace
from multiprocessing import get_context
from pathlib import Path
from threading import Barrier
from typing import Any

import numpy as np
import pytest
from openpyxl import load_workbook

from tests.raw_header_fakes import install_fake_raw_headers
from tests.test_targeted_production_workflow import (
    production_case,
    with_method_universe,
)
from xic_extractor.applications.targeted import (
    output_boundary as targeted_output_boundary,
)
from xic_extractor.applications.targeted.application import (
    TargetedApplicationService,
    build_targeted_application_request,
)
from xic_extractor.applications.targeted.output_boundary import (
    TargetedOutputBoundaryError,
    publish_targeted_outputs,
)
from xic_extractor.applications.targeted.production_workflow import (
    execute_targeted_production_workflow,
)
from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.extraction.result_assembly import reproject_extraction_result
from xic_extractor.extraction.targeted_inputs import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.output.excel_pipeline import _run_output_to_long_rows
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.peak_detection.peak_integration import find_peak_and_area
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import build_acquisition_source_identity


@pytest.fixture(autouse=True)
def _placeholder_raw_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    # RAW files here are placeholders; order them by their file times.
    install_fake_raw_headers(monkeypatch)


def _commit_staged_output_in_process(
    staged: str,
    final: str,
    start_barrier: Any,
) -> tuple[str, str]:
    start_barrier.wait(timeout=30)
    try:
        paths = targeted_output_boundary._commit_staged_outputs(
            {Path(staged): Path(final)}
        )
    except TargetedOutputBoundaryError as exc:
        return ("error", str(exc))
    return ("success", str(paths[0]))


@pytest.mark.parametrize(
    "with_nl,with_signal,reverse_candidates",
    [
        (True, True, False),
        (True, True, True),
        (False, True, False),
        (False, True, True),
        (True, False, False),
    ],
)
def test_formal_entry_retains_the_method_universe_and_publishes_its_decision(
    tmp_path: Path,
    monkeypatch,
    with_nl: bool,
    with_signal: bool,
    reverse_candidates: bool,
) -> None:
    from contextlib import nullcontext

    from xic_extractor.configuration.models import ExtractionConfig, Target
    from xic_extractor.extraction import (
        pipeline,
        target_extraction,
        targeted_cid_nl_assessment,
        targeted_peak_selection,
    )
    from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
    from xic_extractor.peak_detection import cid_nl_method_assessment

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    scales = {"SampleA": 1.0, "SampleB": 2.0}
    for name in scales:
        (data_dir / f"{name}.raw").write_bytes(name.encode())
    config = ExtractionConfig(
        data_dir=data_dir,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=9,
        smooth_polyorder=2,
        peak_rel_height=0.02,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        count_no_ms2_as_detected=True,
    )
    target = Target(
        label="Analyte",
        mz=250.0,
        rt_min=1.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        cid_nl_tag="dR",
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
    )

    class Raw:
        def __init__(self, scale: float) -> None:
            self.scale = scale

        def acquisition_rt_bounds(self):
            return 1.0, 2.0

        def extract_xic(self, _mz, rt_min, rt_max, _ppm):
            rt = np.linspace(rt_min, rt_max, 1201)
            intensity = 10.0 + (self.scale if with_signal else 0.0) * (
                1000.0 * np.exp(-(((rt - 1.4) / 0.025) ** 2))
                + 700.0 * np.exp(-(((rt - 1.7) / 0.025) ** 2))
            )
            return rt, intensity

        def iter_ms2_scans(self, rt_min, rt_max):
            for index, rt in enumerate((1.39, 1.4, 1.41, 1.69, 1.7, 1.71), 1):
                if rt_min <= rt <= rt_max:
                    yield Ms2ScanEvent(
                        scan=Ms2Scan(
                            scan_number=index,
                            rt=rt,
                            precursor_mz=250.0,
                            masses=np.array([133.9526 if with_nl else 135.0]),
                            intensities=np.array([500.0]),
                            base_peak=500.0,
                        ),
                        parse_error=None,
                        scan_number=index,
                    )

    monkeypatch.setattr(pipeline, "preflight_raw_reader", lambda _path: [])
    physical_candidates = targeted_peak_selection.physical_candidates_result

    def ordered_candidates(*args, **kwargs):
        result = physical_candidates(*args, **kwargs)
        return (
            replace(result, candidates=tuple(reversed(result.candidates)))
            if reverse_candidates
            else result
        )

    monkeypatch.setattr(
        targeted_peak_selection, "physical_candidates_result", ordered_candidates
    )
    monkeypatch.setattr(
        target_extraction,
        "open_raw",
        lambda path, *_args, **_kwargs: nullcontext(Raw(scales[path.stem])),
    )
    assessments = []
    relation_answers = []
    evaluate_relations = cid_nl_method_assessment.evaluate_cid_nl_method_relations

    def record_relations(**kwargs):
        answer = evaluate_relations(**kwargs)
        relation_answers.append(answer)
        return answer

    monkeypatch.setattr(
        targeted_peak_selection, "evaluate_cid_nl_method_relations", record_relations
    )
    monkeypatch.setattr(
        cid_nl_method_assessment, "evaluate_cid_nl_method_relations", record_relations
    )
    mint_assessment = targeted_cid_nl_assessment.mint_cid_nl_method_assessment_ref

    def record_assessment(**kwargs):
        result = mint_assessment(**kwargs)
        assessments.append(result)
        return result

    monkeypatch.setattr(
        targeted_cid_nl_assessment,
        "mint_cid_nl_method_assessment_ref",
        record_assessment,
    )
    workbook_path = tmp_path / "xic_results.xlsx"
    execution = TargetedApplicationService(
        runner=pipeline.run_pipeline
    ).execute_application(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1, "SampleB": 2},
            excel_output_path=workbook_path,
        )
    )

    assert len(assessments) == 2
    assert len(relation_answers) == 2
    assert all(
        len(item.candidate_peaks) == (2 if with_signal else 0) for item in assessments
    )
    assert all(
        item.content.evaluation_receipt.scanned_event_count == 6 for item in assessments
    )
    assert all(len(item.evidence_refs) == (6 if with_nl else 0) for item in assessments)
    assert all(
        item.content.method_result
        == (
            "SUPPORT"
            if with_signal and with_nl
            else "HOLD"
            if with_signal
            else "REJECT"
        )
        for item in assessments
    )
    rendered = {
        file.sample_name: file.results[target.label]
        for file in execution.active_output.file_results
    }
    if with_signal:
        assert rendered["SampleA"].reported_peak_area > 0
        assert rendered["SampleB"].reported_peak_area == pytest.approx(
            rendered["SampleA"].reported_peak_area * 2.0, rel=0.01
        )
    for result in rendered.values():
        assert result.targeted_product_projection.counted_detection is (
            with_nl and with_signal
        )
        if with_signal:
            assert result.reported_rt == pytest.approx(1.4, abs=0.01)
            # The selected method index belongs to this exact retained population,
            # even when its order differs from the peak's RT/ranking order.
            index = result.selected_cid_nl_candidate_index
            assert index is not None
            assert index == (1 if reverse_candidates else 0)
            assert (
                result.candidate_physical_evaluations[index]
                is result.physical_evaluation
            )
        else:
            assert result.selected_cid_nl_candidate_index is None
            assert result.reported_peak_area is None
            assert result.reported_rt is None
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    rows = list(workbook["XIC Results"].values)
    workbook.close()
    headers = rows[0]
    workbook_rows = {row[0]: dict(zip(headers, row, strict=True)) for row in rows[1:]}
    assert set(workbook_rows) == set(scales)
    for sample_name, result in rendered.items():
        row = workbook_rows[sample_name]
        assert row["Area"] == (
            round(result.reported_peak_area, 2) if with_signal else "ND"
        )
        assert row["RT"] == (round(result.reported_rt, 4) if with_signal else "ND")
        assert row["Counted Detection"] == (
            "TRUE" if with_nl and with_signal else "FALSE"
        )


def test_finalized_method_hold_cannot_inherit_an_acquisition_detection_claim(tmp_path):
    config, target, output = production_case(tmp_path)
    source = output.file_results[0].results[target.label]
    expected_area = source.reported_peak_area
    output.file_results[0].results[target.label] = with_method_universe(
        replace(source, candidate_ms2_evidence=None), target
    )
    # The acquisition projection still says detected_clean. It is not a
    # replacement for the completed method's observation-only hypothesis.
    assert source.targeted_product_projection.counted_detection is True
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
    )
    completed = execute_targeted_production_workflow(request, output)
    published = publish_targeted_outputs(request, completed, output)
    result = published.output.file_results[0].results[target.label]
    assert result.reported_peak_area == expected_area
    assert result.targeted_product_projection.counted_detection is False
    assert (
        "cid_nl_method_not_supported"
        in result.targeted_product_projection.not_counted_reasons
    )


def test_complete_finalized_population_publishes_only_workbook(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    excel_path = config.output_csv.with_name("xic_results.xlsx")
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=excel_path,
    )
    finalized = execute_targeted_production_workflow(request, output)

    published = publish_targeted_outputs(request, finalized, output)

    assert set(published.paths) == {
        excel_path,
    }
    assert all(path.is_file() for path in published.paths)
    assert not list(config.output_csv.parent.glob(".xic-targeted-publish-*"))


def test_workbook_only_delivery_embeds_method_settings(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    excel_path = config.output_csv.with_name("xic_results_20260903.xlsx")
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=excel_path,
    )
    finalized = execute_targeted_production_workflow(request, output)

    publish_targeted_outputs(request, finalized, output)

    workbook = load_workbook(excel_path, read_only=True, data_only=True)
    metadata = {
        row[0]: row[2] for row in workbook["Run Metadata"].iter_rows(values_only=True)
    }
    workbook.close()
    assert metadata["signal_processing_method"] == config.signal_processing_method
    assert "targeted_strategy" in metadata
    assert set(config.output_csv.parent.iterdir()) == {excel_path}


def test_publication_returns_concrete_public_result_models(tmp_path: Path) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
        sample_peak_scales={"SampleA": 1.0, "SampleB": 2.5},
    )
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1, "SampleB": 2},
    )
    finalized = execute_targeted_production_workflow(request, output)

    published = publish_targeted_outputs(request, finalized, output)

    assert len(published.output.file_results) == 2
    by_sample = {fr.sample_name: fr for fr in published.output.file_results}
    assert set(by_sample.keys()) == {"SampleA", "SampleB"}

    ext_a = by_sample["SampleA"].results[target.label]
    assert ext_a.targeted_product_projection is not None
    assert ext_a.targeted_product_projection.counted_detection is True

    ext_b = by_sample["SampleB"].results[target.label]
    assert ext_b.targeted_product_projection is not None
    assert ext_b.targeted_product_projection.counted_detection is True

    # Multi-sample attribution: distinct samples carry distinct quantitative values
    assert ext_a.reported_peak_area != ext_b.reported_peak_area
    assert ext_b.reported_peak_area == pytest.approx(
        ext_a.reported_peak_area * 2.5, rel=1e-2
    )


def test_publication_renders_the_finalized_numeric_hypothesis(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    finalized_value = finalized[0].completed_projection.decisions[0].value
    extraction = output.file_results[0].results[target.label]
    selected = extraction.selected_hypothesis
    assert selected is not None
    output.file_results[0].results[target.label] = replace(
        extraction,
        selected_hypothesis=replace(
            selected,
            integration=replace(
                selected.integration,
                area_ms1_morphology=None,
                area_raw_counts_seconds=selected.integration.area_raw_counts_seconds
                * 2,
            ),
        ),
    )

    published = publish_targeted_outputs(request, finalized, output)

    rendered = published.output.file_results[0].results[target.label]
    finalized_integration = (
        finalized[0].completed_projection.decisions[0].evidence.hypothesis.integration
    )
    assert rendered.reported_peak_area == finalized_value
    assert rendered.targeted_product_projection is not None
    assert rendered.targeted_product_projection.counted_detection is True
    assert rendered.reported_rt == finalized_integration.rt_apex_min
    assert rendered.peak is not None
    assert rendered.peak.peak_start == finalized_integration.rt_left_min
    assert rendered.peak.peak_end == finalized_integration.rt_right_min
    assert rendered.selected_hypothesis is None
    assert rendered.selection_decision is None


@pytest.mark.parametrize(
    ("nl_status", "not_counted_reason", "count_no_ms2_as_detected"),
    [
        ("NL_FAIL", "cid_nl_method_not_supported", False),
        ("NO_MS2", "cid_nl_method_not_supported", False),
        ("NO_MS2", "cid_nl_method_not_supported", True),
    ],
)
def test_service_publishes_finalized_numeric_values_without_counting_detection(
    tmp_path: Path,
    nl_status: str,
    not_counted_reason: str,
    count_no_ms2_as_detected: bool,
) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
        sample_peak_scales={"SampleA": 1.0, "SampleB": 2.5},
    )
    config = replace(
        config,
        count_no_ms2_as_detected=count_no_ms2_as_detected,
    )
    assert not target.is_istd and target.istd_pair == ""
    for file_result in output.file_results:
        extraction = file_result.results[target.label]
        evidence = extraction.candidate_ms2_evidence
        assert evidence is not None
        if nl_status == "NL_FAIL":
            product = evidence.best_product_evidence
            assert product is not None and target.neutral_loss_da is not None
            observed_loss_da = 115.9
            loss_ppm = (
                abs(observed_loss_da - target.neutral_loss_da)
                / target.neutral_loss_da
                * 1_000_000.0
            )
            evidence = replace(
                evidence,
                nl_match=False,
                nl_status="NL_FAIL",
                strict_nl_scan_count=0,
                best_loss_ppm=loss_ppm,
                best_product_evidence=replace(
                    product,
                    product_mz=product.precursor_mz - observed_loss_da,
                    observed_loss_da=observed_loss_da,
                    observed_loss_error_ppm=loss_ppm,
                ),
            )
        else:
            evidence = replace(
                evidence,
                ms2_present=False,
                nl_match=False,
                nl_status="NO_MS2",
                trigger_scan_count=0,
                strict_nl_scan_count=0,
                best_loss_ppm=None,
                best_scan_rt=None,
                best_product_base_ratio=None,
                alignment_source="none",
                best_product_evidence=None,
            )
        policy_result = reproject_extraction_result(
            with_method_universe(
                replace(extraction, candidate_ms2_evidence=evidence), target
            ),
            target=target,
            sample_name=file_result.sample_name,
        )
        projection = policy_result.targeted_product_projection
        assert projection is not None
        assert projection.counted_detection is False
        assert projection.not_counted_reasons == (not_counted_reason,)
        assert projection.conflict_reasons == projection.exclusion_reasons == ()
        assert projection.review_state == "review_required"
        file_result.results[target.label] = policy_result

    excel_path = config.output_csv.with_suffix(".xlsx")
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1, "SampleB": 2},
        excel_output_path=excel_path,
    )
    execution = TargetedApplicationService(
        lambda *_args, **_kwargs: output,
    ).execute_application(request)
    finalized = execution.finalized_results[0]
    decisions = {
        decision.subject.sample.metadata.raw_stem: decision
        for decision in finalized.completed_projection.decisions
    }
    assert set(decisions) == {"SampleA", "SampleB"}
    assert all(decision.variant == "numeric" for decision in decisions.values())
    assert decisions["SampleB"].value == pytest.approx(decisions["SampleA"].value * 2.5)
    long_rows = {
        row["SampleName"]: row
        for row in _run_output_to_long_rows(execution.active_output, [target])
    }
    workbook = load_workbook(excel_path, data_only=True)
    try:
        rows = workbook["XIC Results"].iter_rows(values_only=True)
        headers = next(rows)
        workbook_rows = {row[0]: dict(zip(headers, row, strict=True)) for row in rows}
        sheet = workbook["XIC Results"]
        for header in (
            "Area",
            "RT",
            "IntSmoothed",
            "Counted Detection",
            "Review",
            "Review Reason",
        ):
            index = headers.index(header) + 1
            assert not any(
                dimension.hidden and dimension.min <= index <= dimension.max
                for dimension in sheet.column_dimensions.values()
            )
        for header in (
            "Reason",
            "NL Evidence",
            "PeakStart",
            "PeakEnd",
            "NL",
            "Int",
            "PeakWidth",
        ):
            index = headers.index(header) + 1
            assert any(
                dimension.hidden and dimension.min <= index <= dimension.max
                for dimension in sheet.column_dimensions.values()
            )
    finally:
        workbook.close()
    assert set(long_rows) == set(workbook_rows) == set(decisions)
    rendered = {
        item.sample_name: item.results[target.label]
        for item in execution.active_output.file_results
    }
    for sample_name, decision in decisions.items():
        integration = decision.evidence.hypothesis.integration
        expected_values = {
            "RT": (integration.rt_apex_min, 4),
            "Area": (decision.value, 2),
            "PeakStart": (integration.rt_left_min, 4),
            "PeakEnd": (integration.rt_right_min, 4),
            "PeakWidth": (
                integration.rt_right_min - integration.rt_left_min,
                4,
            ),
        }
        projection = rendered[sample_name].targeted_product_projection
        assert projection is not None and projection.counted_detection is False
        assert rendered[sample_name].reported_peak_area == decision.value
        for field, (value, digits) in expected_values.items():
            expected_text = f"{value:.{digits}f}"
            assert long_rows[sample_name][field] == expected_text
            assert workbook_rows[sample_name][field] == float(expected_text)
        for row in (long_rows[sample_name], workbook_rows[sample_name]):
            assert row["NL Evidence"] == (
                "所選峰未見預期的中性丟失"
                if nl_status == "NL_FAIL"
                else "所選峰沒有對應的 MS2"
            )
            assert row["Reason"] == "未設定配對 ISTD，無法確認身分。"
            assert "Not counted" not in row["Reason"]
            assert row["Counted Detection"] == "FALSE"
        assert long_rows[sample_name]["Product State"] == "not_counted"
        assert long_rows[sample_name]["Review State"] == "review_required"
        assert (
            long_rows[sample_name]["Projection Not Counted Reasons"]
            == not_counted_reason
        )
        assert (
            long_rows[sample_name]["Projection Reason"] == projection.projection_reason
        )
        # Under CID the neutral-loss status does not set the review level.
        assert workbook_rows[sample_name]["Review"] == "Review"
        assert workbook_rows[sample_name]["Review Reason"]
        for field in ("Int", "IntSmoothed"):
            assert workbook_rows[sample_name][field] == float(
                long_rows[sample_name][field]
            )


def test_service_publishes_no_numeric_for_finalized_no_observation(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    target = replace(target, neutral_loss_da=None, nl_ppm_warn=None, nl_ppm_max=None)
    extraction = output.file_results[0].results[target.label]
    original_trace = extraction.acquisition_trace
    assert original_trace is not None
    rt = np.asarray(original_trace.rt)
    trace = Trace.from_arrays(
        sample_name="SampleA",
        mz=target.mz,
        rt=rt,
        intensity=np.zeros_like(rt),
        rt_min=target.rt_min,
        rt_max=target.rt_max,
        ppm_tol=target.ppm_tol,
    )
    profile = resolve_signal_processing_profile(
        signal_processing_method=config.signal_processing_method,
        signal_processing_window_points=config.signal_processing_window_points,
    )
    output.file_results[0].results[target.label] = with_method_universe(
        replace(
            extraction,
            peak_result=find_peak_and_area(
                rt, trace.intensity, config, preferred_rt=1.5
            ),
            selected_hypothesis=None,
            acquisition_trace=trace,
            physical_evaluation=evaluate_physical_peak(
                prepare_physical_signal(rt, trace.intensity, profile=profile),
                selection_subject_id=target.label,
                query_rt_min=1.5,
                request_context_rt_start_min=trace.rt_min,
                request_context_rt_end_min=trace.rt_max,
            ),
        ),
        target,
    )
    excel_path = config.output_csv.with_suffix(".xlsx")
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=excel_path,
    )
    execution = TargetedApplicationService(
        lambda *_args, **_kwargs: output,
    ).execute_application(request)
    decision = execution.finalized_results[0].completed_projection.decisions[0]
    assert decision.variant == "searched_no_observation"
    assert decision.value is None
    rendered = execution.active_output.file_results[0].results[target.label]
    assert rendered.reported_peak_area is None
    assert rendered.peak is None
    long_row = _run_output_to_long_rows(execution.active_output, [target])[0]
    workbook = load_workbook(excel_path, read_only=True, data_only=True)
    try:
        rows = workbook["XIC Results"].iter_rows(values_only=True)
        workbook_row = dict(zip(next(rows), next(rows), strict=True))
    finally:
        workbook.close()
    for field in ("RT", "Area", "PeakStart", "PeakEnd", "PeakWidth"):
        assert long_row[field] == "ND"
        if field != "PeakWidth":
            assert workbook_row[field] == "ND"
    for row in (long_row, workbook_row):
        assert row["Counted Detection"] == "FALSE"
    assert long_row["Review State"] == "review_required"
    assert long_row["Projection Reason"] == "finalized_searched_no_observation"
    assert workbook_row["Review"] == "Review"
    assert workbook_row["Review Reason"]
    assert workbook_row["Reason"] == long_row["Reason"]


def test_publication_retires_equal_area_legacy_selection_authority(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)

    published = publish_targeted_outputs(request, finalized, output)

    rendered = published.output.file_results[0].results[target.label]
    assert rendered.selected_hypothesis is None
    assert rendered.selection_decision is None
    assert rendered.peak_result.candidates == ()
    assert rendered.peak_result.candidate_scores == ()


def test_neutral_loss_disabled_target_publishes_ms1_backfill(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    target = replace(
        target,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
    )
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )

    finalized = execute_targeted_production_workflow(request, output)
    published = publish_targeted_outputs(request, finalized, output)

    assert published.paths == (request.excel_output_path,)
    assert request.excel_output_path.is_file()
    row = _run_output_to_long_rows(published.output, [target])[0]
    assert row["NL"] == ""
    assert row["NL Evidence"] == "未設定"
    assert row["Area"] != "ND"
    assert row["Reason"] == "有 MS1 峰；此目標不要求中性丟失。"


def test_workbench_summary_consumes_the_finalized_publication_view(
    tmp_path: Path,
) -> None:
    from xic_workbench.targeted import build_summary

    config, target, output = production_case(tmp_path)
    extraction = output.file_results[0].results[target.label]
    candidate_evidence = extraction.candidate_ms2_evidence
    assert candidate_evidence is not None
    product_evidence = candidate_evidence.best_product_evidence
    assert product_evidence is not None
    observed_loss_da = 115.9
    assert target.neutral_loss_da is not None
    observed_loss_error_ppm = (
        abs(observed_loss_da - target.neutral_loss_da)
        / target.neutral_loss_da
        * 1_000_000.0
    )
    policy_result = reproject_extraction_result(
        with_method_universe(
            replace(
                extraction,
                candidate_ms2_evidence=replace(
                    candidate_evidence,
                    nl_match=False,
                    nl_status="NL_FAIL",
                    strict_nl_scan_count=0,
                    best_loss_ppm=observed_loss_error_ppm,
                    best_product_evidence=replace(
                        product_evidence,
                        product_mz=product_evidence.precursor_mz - observed_loss_da,
                        observed_loss_da=observed_loss_da,
                        observed_loss_error_ppm=observed_loss_error_ppm,
                    ),
                ),
            ),
            target,
        ),
        target=target,
        sample_name="SampleA",
    )
    assert policy_result.targeted_product_projection is not None
    assert policy_result.targeted_product_projection.counted_detection is False
    assert (
        "cid_nl_method_not_supported"
        in policy_result.targeted_product_projection.not_counted_reasons
    )
    output.file_results[0].results[target.label] = policy_result
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    service = TargetedApplicationService(
        lambda *_args, **_kwargs: output,
    )

    execution = service.execute_application(request)
    final_output = execution.active_output
    finalized_value = (
        execution.finalized_results[0].completed_projection.decisions[0].value
    )
    final_projection = (
        final_output.file_results[0].results[target.label].targeted_product_projection
    )
    assert final_projection is not None
    assert final_projection.product_state == "not_counted"
    assert final_projection.counted_detection is False
    final_result = final_output.file_results[0].results[target.label]
    assert final_result.reported_peak_area is not None
    summary = build_summary(
        config,
        [target],
        final_output,
        config.output_csv.with_name("xic_results.xlsx"),
    )

    assert final_output is not output
    assert summary["targets"][0]["detected"] == 0
    assert summary["targets"][0]["median_area"] == finalized_value
    row = _run_output_to_long_rows(final_output, [target])[0]
    assert row["Product State"] == "not_counted"
    assert row["Counted Detection"] == "FALSE"
    assert row["RT"] == f"{final_result.reported_rt:.4f}"
    assert row["Area"] == f"{finalized_value:.2f}"


def test_collision_leaves_existing_output_and_publishes_nothing_else(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    config.output_csv.parent.mkdir(parents=True, exist_ok=True)
    request.excel_output_path.write_bytes(b"existing")

    with pytest.raises(TargetedOutputBoundaryError, match="already exists"):
        publish_targeted_outputs(request, finalized, output)

    assert request.excel_output_path.read_bytes() == b"existing"
    assert sorted(path.name for path in config.output_csv.parent.iterdir()) == [
        request.excel_output_path.name
    ]


def test_noncooperating_collision_after_precheck_is_not_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    completed = execute_targeted_production_workflow(request, output)
    original_reject = targeted_output_boundary._reject_collisions
    collided: list[Path] = []

    def create_after_precheck(paths: object) -> None:
        resolved = list(paths)
        original_reject(resolved)
        collided.append(resolved[0])
        resolved[0].write_bytes(b"outside-writer")

    monkeypatch.setattr(
        targeted_output_boundary,
        "_reject_collisions",
        create_after_precheck,
    )

    with pytest.raises(TargetedOutputBoundaryError, match="was rolled back"):
        publish_targeted_outputs(request, completed, output)

    assert len(collided) == 1
    assert collided[0].read_bytes() == b"outside-writer"
    assert [path for path in config.output_csv.parent.iterdir() if path.is_file()] == [
        collided[0]
    ]


def test_concurrent_non_replacing_publications_are_collision_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    original_commit = targeted_output_boundary._commit_staged_outputs
    commit_starts = Barrier(2)

    def synchronize_commit(*args: object, **kwargs: object) -> tuple[Path, ...]:
        commit_starts.wait(timeout=10)
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(
        targeted_output_boundary,
        "_commit_staged_outputs",
        synchronize_commit,
    )

    def publish_with_marker(marker: str) -> tuple[str, object]:
        evidence = replace(
            output,
            diagnostics=[
                DiagnosticRecord(
                    sample_name="SampleA",
                    target_label="",
                    issue="FILE_ERROR",
                    reason=marker,
                )
            ],
        )
        completed = execute_targeted_production_workflow(request, evidence)
        try:
            return ("success", publish_targeted_outputs(request, completed, evidence))
        except Exception as exc:  # noqa: BLE001 - assert the public failure below
            return ("error", exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                publish_with_marker,
                ("concurrent-a", "concurrent-b"),
            )
        )

    successes = [value for status, value in outcomes if status == "success"]
    failures = [value for status, value in outcomes if status == "error"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], TargetedOutputBoundaryError)

    winning = successes[0]
    assert isinstance(winning, targeted_output_boundary.TargetedPublication)
    expected_paths = {request.excel_output_path}
    assert set(winning.paths) == expected_paths
    assert all(path.is_file() for path in winning.paths)
    workbook = load_workbook(request.excel_output_path, read_only=True)
    worklist_text = str(list(workbook["需檢查"].values))
    workbook.close()
    assert (
        sum(marker in worklist_text for marker in ("concurrent-a", "concurrent-b")) == 1
    )
    assert not list(config.output_csv.parent.glob(".xic-targeted-publish-*"))
    assert not (config.output_csv.parent / ".xic-targeted-publish.lock").exists()


def test_concurrent_commits_lock_every_destination_parent(tmp_path: Path) -> None:
    commit_starts = Barrier(2)
    shared_final = tmp_path / "shared" / "results.xlsx"

    def commit(run_name: str) -> tuple[str, object]:
        stage_root = tmp_path / f"stage-{run_name}"
        unique_stage = stage_root / "unique.csv"
        shared_stage = stage_root / "shared.xlsx"
        unique_final = tmp_path / run_name / "unique.csv"
        stage_root.mkdir()
        unique_stage.write_text(run_name, encoding="utf-8")
        shared_stage.write_text(run_name, encoding="utf-8")
        commit_starts.wait(timeout=10)
        try:
            paths = targeted_output_boundary._commit_staged_outputs(
                {
                    unique_stage: unique_final,
                    shared_stage: shared_final,
                }
            )
            return ("success", paths)
        except Exception as exc:  # noqa: BLE001 - assert the public failure below
            return ("error", exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(commit, ("run-a", "run-b")))

    successes = [value for status, value in outcomes if status == "success"]
    failures = [value for status, value in outcomes if status == "error"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], TargetedOutputBoundaryError)

    winner = shared_final.read_text(encoding="utf-8")
    assert winner in {"run-a", "run-b"}
    assert (tmp_path / winner / "unique.csv").read_text(encoding="utf-8") == winner
    loser = "run-b" if winner == "run-a" else "run-a"
    assert not (tmp_path / loser / "unique.csv").exists()
    assert not list(tmp_path.rglob(".xic-targeted-publish.lock"))


def test_separate_process_commits_are_collision_atomic(tmp_path: Path) -> None:
    shared_final = tmp_path / "shared" / "results.csv"
    staged_paths = (tmp_path / "a.csv", tmp_path / "b.csv")
    for staged, marker in zip(staged_paths, ("process-a", "process-b"), strict=True):
        staged.write_text(marker, encoding="utf-8")

    spawn_context = get_context("spawn")
    with spawn_context.Manager() as manager:
        start_barrier = manager.Barrier(2)
        with ProcessPoolExecutor(
            max_workers=2,
            mp_context=spawn_context,
        ) as executor:
            futures = tuple(
                executor.submit(
                    _commit_staged_output_in_process,
                    str(staged),
                    str(shared_final),
                    start_barrier,
                )
                for staged in staged_paths
            )
            outcomes = tuple(future.result(timeout=45) for future in futures)

    assert [status for status, _ in outcomes].count("success") == 1
    assert [status for status, _ in outcomes].count("error") == 1
    assert shared_final.read_text(encoding="utf-8") in {"process-a", "process-b"}
    assert not (shared_final.parent / ".xic-targeted-publish.lock").exists()


def test_render_failure_publishes_no_final_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_extractor.applications.targeted.output_boundary as boundary

    config, target, output = production_case(tmp_path)
    excel_path = config.output_csv.with_name("xic_results.xlsx")
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=excel_path,
    )
    finalized = execute_targeted_production_workflow(request, output)
    monkeypatch.setattr(
        boundary,
        "write_excel_from_run_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("render failed")),
    )

    with pytest.raises(TargetedOutputBoundaryError, match="render failed"):
        publish_targeted_outputs(request, finalized, output)

    assert not any(config.output_csv.parent.iterdir())


def test_commit_failure_rolls_back_all_final_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    original_publish = targeted_output_boundary._publish_without_replace
    commit_count = 0
    failed = False

    def fail_commit(source: Path, target_path: Path) -> None:
        nonlocal commit_count, failed
        commit_count += 1
        if commit_count == 1 and not failed:
            failed = True
            raise OSError("commit failed")
        original_publish(source, target_path)

    monkeypatch.setattr(
        targeted_output_boundary,
        "_publish_without_replace",
        fail_commit,
    )

    with pytest.raises(TargetedOutputBoundaryError, match="rolled back"):
        publish_targeted_outputs(request, finalized, output)

    assert not any(config.output_csv.parent.iterdir())


def test_commit_cancellation_after_first_move_rolls_back_all_final_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    cancellation_requested = False
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
        should_stop=lambda: cancellation_requested,
    )
    finalized = execute_targeted_production_workflow(request, output)
    original_publish = targeted_output_boundary._publish_without_replace

    def cancel_after_first_commit(source: Path, target_path: Path) -> None:
        nonlocal cancellation_requested
        original_publish(source, target_path)
        cancellation_requested = True

    monkeypatch.setattr(
        targeted_output_boundary,
        "_publish_without_replace",
        cancel_after_first_commit,
    )

    publication = publish_targeted_outputs(request, finalized, output)

    assert publication.paths == ()
    assert not any(config.output_csv.parent.iterdir())


def test_commit_cancellation_after_final_move_rolls_back_output(
    tmp_path: Path,
) -> None:
    staged = tmp_path / "staged.csv"
    final = tmp_path / "final.csv"
    staged.write_text("value\n", encoding="utf-8")
    cancellation_checks = iter((False, True))

    with pytest.raises(targeted_output_boundary._TargetedPublicationCancelled):
        targeted_output_boundary._commit_staged_outputs(
            {staged: final},
            should_cancel=lambda: next(cancellation_checks),
        )

    assert staged.is_file()
    assert not final.exists()


def test_callback_exception_after_move_restores_complete_previous_set(
    tmp_path: Path,
) -> None:
    staged = (tmp_path / "stage-a.csv", tmp_path / "stage-b.csv")
    final = (tmp_path / "final-a.csv", tmp_path / "final-b.csv")
    for index, path in enumerate(staged):
        path.write_text(f"new-{index}", encoding="utf-8")
    for index, path in enumerate(final):
        path.write_text(f"old-{index}", encoding="utf-8")
    checks = 0

    def fail_after_first_move() -> bool:
        nonlocal checks
        checks += 1
        if checks == 5:
            raise RuntimeError("callback failed")
        return False

    with pytest.raises(TargetedOutputBoundaryError, match="was rolled back"):
        targeted_output_boundary._commit_staged_outputs(
            dict(zip(staged, final, strict=True)),
            replace_existing=True,
            should_cancel=fail_after_first_move,
        )

    assert [path.read_text(encoding="utf-8") for path in final] == [
        "old-0",
        "old-1",
    ]


def test_replay_replaces_its_owned_output_set(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    original_paths = publish_targeted_outputs(request, finalized, output).paths
    original_paths[0].write_bytes(b"stale replay output")

    replay_request = replace(request, replace_existing_outputs=True)
    replay_paths = publish_targeted_outputs(replay_request, finalized, output).paths

    assert replay_paths == original_paths
    assert all(path.is_file() for path in replay_paths)
    assert all(path.read_bytes() != b"stale replay output" for path in replay_paths)


def test_replay_commit_failure_restores_previous_output_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    original_paths = publish_targeted_outputs(request, finalized, output).paths
    original_bytes = {path: path.read_bytes() for path in original_paths}
    original_replace = Path.replace
    commit_count = 0

    def fail_new_output(source: Path, target_path: Path) -> Path:
        nonlocal commit_count
        if source.parent.name.startswith(".xic-targeted-publish-"):
            commit_count += 1
            if commit_count == 1:
                raise OSError("replacement failed")
        return original_replace(source, target_path)

    monkeypatch.setattr(Path, "replace", fail_new_output)

    with pytest.raises(TargetedOutputBoundaryError, match="rolled back"):
        publish_targeted_outputs(
            replace(request, replace_existing_outputs=True),
            finalized,
            output,
        )

    assert {path: path.read_bytes() for path in original_paths} == original_bytes


def test_failed_primary_restore_uses_fallback_to_restore_live_output_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    original_paths = publish_targeted_outputs(request, finalized, output).paths
    original_bytes = {path: path.read_bytes() for path in original_paths}
    original_replace = Path.replace
    commit_count = 0
    restore_failed = False

    def fail_commit_and_one_restore(source: Path, target_path: Path) -> Path:
        nonlocal commit_count, restore_failed
        if source.parent.name.startswith(".xic-targeted-publish-"):
            commit_count += 1
            if commit_count == 1:
                raise OSError("replacement failed")
        if source.parent.name == ".existing" and not restore_failed:
            restore_failed = True
            raise OSError("restore blocked")
        return original_replace(source, target_path)

    monkeypatch.setattr(Path, "replace", fail_commit_and_one_restore)

    with pytest.raises(
        TargetedOutputBoundaryError,
        match="was rolled back",
    ):
        publish_targeted_outputs(
            replace(request, replace_existing_outputs=True),
            finalized,
            output,
        )

    assert {path: path.read_bytes() for path in original_paths} == original_bytes
    assert not list(config.output_csv.parent.glob(".xic-targeted-publish-*"))


def test_failed_fallback_copy_cannot_truncate_live_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = tmp_path / "backup.csv"
    final = tmp_path / "final.csv"
    backup.write_bytes(b"complete-old-output")
    final.write_bytes(b"complete-new-output")

    def fail_replace(_source: Path, _destination: Path) -> Path:
        raise OSError("primary restore blocked")

    def fail_copy(_source: Path, destination: Path) -> None:
        destination.write_bytes(b"partial")
        raise OSError("copy interrupted")

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(targeted_output_boundary.shutil, "copy2", fail_copy)

    restored = targeted_output_boundary._restore_previous_output(backup, final)

    assert restored is False
    assert final.read_bytes() == b"complete-new-output"
    assert backup.read_bytes() == b"complete-old-output"
    assert not list(tmp_path.glob(".final.csv.rollback-*"))


def test_rollback_quarantines_new_output_when_unlink_is_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = tmp_path / "staged.csv"
    final = tmp_path / "final.csv"
    staged.write_bytes(b"recoverable-staged-output")
    final.write_bytes(b"published-output")
    original_unlink = targeted_output_boundary.os.unlink

    def deny_final_unlink(path: object, *args: object, **kwargs: object) -> None:
        if Path(path) == final:
            raise PermissionError("final is open")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(targeted_output_boundary.os, "unlink", deny_final_unlink)

    restored = targeted_output_boundary._restore_moved_output(final, staged)

    assert restored is True
    assert not final.exists()
    assert staged.read_bytes() == b"recoverable-staged-output"
    assert (tmp_path / ".staged.csv.published").read_bytes() == b"published-output"


def test_publisher_rejects_finalized_results_from_another_acquisition(
    tmp_path: Path,
) -> None:
    current_root = tmp_path / "current"
    stale_root = tmp_path / "stale"
    current_root.mkdir()
    stale_root.mkdir()
    current_config, current_target, current_output = production_case(current_root)
    stale_config, stale_target, stale_output = production_case(stale_root)
    stale_raw = stale_config.data_dir / "SampleA.raw"
    stale_raw.write_bytes(b"stale acquisition")
    stale_source = build_acquisition_source_identity(
        stale_raw,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    stale_extraction = stale_output.file_results[0].results[stale_target.label]
    stale_candidate = stale_extraction.candidate_ms2_evidence
    assert stale_candidate is not None
    stale_product = stale_candidate.best_product_evidence
    assert stale_product is not None
    stale_output.file_results[0].results[stale_target.label] = with_method_universe(
        replace(
            stale_extraction,
            candidate_ms2_evidence=replace(
                stale_candidate,
                best_product_evidence=replace(
                    stale_product,
                    acquisition_source_id=stale_source.acquisition_source_id,
                ),
            ),
        ),
        stale_target,
    )
    stale_request = build_targeted_application_request(
        stale_config,
        [stale_target],
        excel_output_path=stale_config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    stale_finalized = execute_targeted_production_workflow(
        stale_request,
        stale_output,
    )
    current_request = build_targeted_application_request(
        current_config,
        [current_target],
        excel_output_path=current_config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    with pytest.raises(
        TargetedOutputBoundaryError,
        match="crossed the acquisition evidence",
    ):
        publish_targeted_outputs(
            current_request,
            stale_finalized,
            current_output,
        )


def test_publisher_rejects_unsealed_finalized_result_tuple(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    completed = execute_targeted_production_workflow(request, output)

    with pytest.raises(
        TargetedOutputBoundaryError,
        match="exact attempt-bound completed workflow",
    ):
        publish_targeted_outputs(
            request,
            completed.finalized_results,  # type: ignore[arg-type]
            output,
        )


def test_publisher_uses_frozen_acquisition_snapshot(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    completed = execute_targeted_production_workflow(request, output)
    extraction = output.file_results[0].results[target.label]
    output.file_results[0].results[target.label] = replace(
        extraction,
        target_label="CrossedTarget",
    )
    output.file_results[0].group = "mutated-group"
    output.diagnostics.append(
        DiagnosticRecord("SampleA", target.label, "FILE_ERROR", "mutated")
    )

    publication = publish_targeted_outputs(request, completed, output)

    assert publication.output.file_results[0].results[target.label].target_label == (
        target.label
    )
    assert publication.output.file_results[0].group is None
    assert publication.output.diagnostics == []


def test_publication_replay_isolated_from_mutated_returned_view(tmp_path: Path) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
        sample_peak_scales={"SampleA": 1.0, "SampleB": 2.0},
    )
    expected_areas = {
        file_result.sample_name: file_result.results[target.label].reported_peak_area
        for file_result in output.file_results
    }
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1, "SampleB": 2},
    )
    completed = execute_targeted_production_workflow(request, output)
    publication = publish_targeted_outputs(request, completed, output)
    publication.output.file_results[0].results.clear()
    publication.output.file_results.reverse()
    publication.output.diagnostics.append(
        DiagnosticRecord("SampleA", target.label, "FILE_ERROR", "mutated")
    )

    replay = publish_targeted_outputs(
        replace(request, replace_existing_outputs=True), completed, output
    )

    assert [row.sample_name for row in replay.output.file_results] == [
        "SampleA",
        "SampleB",
    ]
    assert {
        row.sample_name: row.results[target.label].reported_peak_area
        for row in replay.output.file_results
    } == expected_areas
    assert expected_areas["SampleA"] != expected_areas["SampleB"]
    assert replay.output.diagnostics == []
    assert replay.paths == publication.paths


def test_publisher_rejects_mutated_owner_snapshot(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )
    completed = execute_targeted_production_workflow(request, output)
    completed._publication_snapshot.file_results[0].group = "mutated"

    with pytest.raises(TargetedOutputBoundaryError, match="snapshot was mutated"):
        publish_targeted_outputs(request, completed, output)


def test_publisher_rejects_reordered_acquisition_after_completion(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
    )
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1, "SampleB": 2},
    )
    completed = execute_targeted_production_workflow(request, output)
    output.file_results.reverse()

    with pytest.raises(
        TargetedOutputBoundaryError,
        match="crossed the acquisition evidence",
    ):
        publish_targeted_outputs(request, completed, output)


def test_service_accepts_raw_order_that_differs_from_canonical_order(
    tmp_path: Path,
) -> None:
    sample_names = ("SampleA", "SampleB", "SampleC", "SampleD")
    config, target, output = production_case(
        tmp_path,
        sample_names=sample_names,
    )
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={
            sample_name: index
            for index, sample_name in enumerate(sample_names, start=1)
        },
    )

    execution = TargetedApplicationService(
        lambda *_args, **_kwargs: output
    ).execute_application(request)

    assert execution.published_paths
    assert (
        tuple(
            sample.metadata.sample_name
            for sample in execution.finalized_results[0].canonical_samples
        )
        != sample_names
    )


def test_service_rejects_inputs_mutated_during_acquisition(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
    )

    def mutate_raw_then_return(*_args, **_kwargs):
        (config.data_dir / "SampleA.raw").write_bytes(b"mutated during acquisition")
        return output

    with pytest.raises(RuntimeError, match="inputs changed during acquisition"):
        TargetedApplicationService(mutate_raw_then_return).execute_application(request)

    assert not config.output_csv.exists()


def test_service_fingerprints_each_raw_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_extractor.processing_identity as identity

    sample_names = ("SampleA", "SampleB")
    config, target, output = production_case(
        tmp_path,
        sample_names=sample_names,
    )
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1, "SampleB": 2},
    )
    original_fingerprint = identity.fingerprint_file_content
    fingerprinted: list[Path] = []

    def observe_fingerprint(path: str | Path, **kwargs):
        fingerprinted.append(Path(path))
        return original_fingerprint(path, **kwargs)

    monkeypatch.setattr(identity, "fingerprint_file_content", observe_fingerprint)

    TargetedApplicationService(lambda *_args, **_kwargs: output).execute_application(
        request
    )

    assert fingerprinted == [
        config.data_dir / "SampleA.raw",
        config.data_dir / "SampleB.raw",
    ]


def test_cancellation_after_typed_completion_never_invokes_publisher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    stop_results = iter((False, False, True))
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
        should_stop=lambda: next(stop_results),
    )

    monkeypatch.setattr(
        targeted_output_boundary,
        "publish_targeted_outputs",
        lambda *_args, **_kwargs: pytest.fail("cancelled run published output"),
    )

    execution = TargetedApplicationService(
        lambda *_args, **_kwargs: output
    ).execute_application(request)

    assert execution.active_output is output
    assert execution.finalized_results
    assert execution.published_paths == ()


def test_cancellation_during_input_fingerprint_skips_acquisition_and_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 1},
        should_stop=lambda: True,
    )
    monkeypatch.setattr(
        "xic_extractor.applications.targeted.production_workflow."
        "execute_targeted_production_workflow",
        lambda *_args: pytest.fail("cancelled run invoked finalization"),
    )

    execution = TargetedApplicationService(
        lambda *_args, **_kwargs: pytest.fail("cancelled run invoked acquisition")
    ).execute_application(request)

    assert execution.active_output.file_results == []
    assert execution.active_output.diagnostics == []
    assert execution.finalized_results == ()
    assert execution.published_paths == ()


@pytest.mark.parametrize("sample_review", [False, True])
@pytest.mark.parametrize(
    "missing_pair, istd_backfill, hint_window",
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (False, False, True),
    ],
)
def test_formal_targeted_backfill_uses_confirmed_peers_and_paired_istd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_pair: bool,
    istd_backfill: bool,
    hint_window: bool,
    sample_review: bool,
) -> None:
    from contextlib import nullcontext

    from xic_extractor.configuration.models import ExtractionConfig, Target
    from xic_extractor.extraction import pipeline, target_extraction
    from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent

    data = tmp_path / "raw"
    data.mkdir()
    shifts = {
        "SampleA": 0.0,
        "SampleB": 0.02,
        "SampleC": 0.04 if istd_backfill else 0.45,
    }
    for name in shifts:
        (data / f"{name}.raw").write_bytes(name.encode())
    config = ExtractionConfig(
        data_dir=data,
        dll_dir=tmp_path,
        output_csv=tmp_path / "result.csv",
        diagnostics_csv=tmp_path / "diag.csv",
        smooth_window=9,
        smooth_polyorder=2,
        peak_rel_height=0.02,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )
    analyte = Target(
        label="Analyte",
        mz=250.0,
        rt_min=1.1,
        rt_max=1.4 if hint_window else 2.5,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        cid_nl_tag="dR",
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=istd_backfill,
        istd_pair="" if istd_backfill else "ISTD",
    )
    istd = replace(
        analyte,
        label="ISTD",
        mz=255.0,
        # Legacy geometric targets use interval midpoints as their expected RT.
        # Preserve the known 0.1 min analyte/ISTD offset in both hint variants.
        rt_min=analyte.rt_min - 0.1,
        rt_max=analyte.rt_max - 0.1,
        is_istd=True,
        istd_pair="",
    )

    class Raw:
        def __init__(self, name):
            self.name = name

        def acquisition_rt_bounds(self):
            return 0.7, 2.5

        def extract_xic(self, mz, rt_min, rt_max, _ppm):
            rt = np.linspace(rt_min, rt_max, 1801)
            center = (1.35 if mz == 255.0 else 1.45) + shifts[self.name]
            scale = {"SampleA": 1000.0, "SampleB": 2000.0, "SampleC": 3000.0}[self.name]
            signal = 10 + scale * np.exp(-(((rt - center) / 0.015) ** 2))
            if self.name == "SampleC" and mz == 250.0:
                signal += 8000.0 * np.exp(-(((rt - 2.2) / 0.015) ** 2))
            if missing_pair and self.name == "SampleC" and mz == 255.0:
                signal = np.full_like(rt, 10.0)
            return rt, signal

        def iter_ms2_scans(self, rt_min, rt_max):
            for index, mz in enumerate((250.0, 255.0), 1):
                if self.name == "SampleC" and mz == 250.0:
                    continue
                rt = (1.35 if mz == 255.0 else 1.45) + shifts[self.name]
                if rt_min <= rt <= rt_max:
                    yield Ms2ScanEvent(
                        scan=Ms2Scan(
                            scan_number=index,
                            rt=rt,
                            precursor_mz=mz,
                            masses=np.array([mz - 116.0474]),
                            intensities=np.array([500.0]),
                            base_peak=500.0,
                        ),
                        parse_error=None,
                        scan_number=index,
                    )

    monkeypatch.setattr(pipeline, "preflight_raw_reader", lambda _path: [])
    monkeypatch.setattr(
        target_extraction,
        "open_raw",
        lambda path, *_a, **_k: nullcontext(Raw(path.stem)),
    )
    # RAW enumeration order is not canonical sample-identity order.
    from xic_extractor.applications.targeted import production_workflow

    original_execute = production_workflow._execute_target
    initial_counts = []

    def execute_reverse_identity(context, *args, **kwargs):
        context = replace(
            context,
            results_by_sample=tuple(
                sorted(
                    context.results_by_sample,
                    key=lambda item: item[0].identity.sample_identity_id,
                    reverse=True,
                )
            ),
        )
        if sample_review:
            for sample, file in context.results_by_sample:
                if sample.metadata.raw_stem == "SampleC":
                    extraction = file.results["Analyte"]
                    projection = extraction.targeted_product_projection
                    file.results["Analyte"] = replace(
                        extraction,
                        targeted_product_projection=replace(
                            projection,
                            review_reasons=tuple(
                                dict.fromkeys(
                                    (*projection.review_reasons, "sample_review")
                                )
                            ),
                        ),
                    )
        if istd_backfill:
            initial_counts.extend(
                file.results["Analyte"].targeted_product_projection.counted_detection
                for sample, file in context.results_by_sample
                if sample.metadata.raw_stem == "SampleC"
            )
        return original_execute(context, *args, **kwargs)

    monkeypatch.setattr(
        production_workflow, "_execute_target", execute_reverse_identity
    )
    workbook_path = tmp_path / "backfill.xlsx"
    execution = TargetedApplicationService(pipeline.run_pipeline).execute_application(
        build_targeted_application_request(
            config,
            [analyte, istd],
            injection_order={"SampleA": 1, "SampleB": 2, "SampleC": 3},
            excel_output_path=workbook_path,
        )
    )
    finalized = next(
        r
        for r in execution.finalized_results
        if r.completed_projection.row_binding.target.definition.target.label
        == "Analyte"
    )
    decisions = finalized.scientific_core.targeted_backfill_decisions
    accepted = [d for d in decisions if d.disposition == "candidate_mapped"]
    assert len(accepted) == (0 if missing_pair else 1), (
        [(d.reason_codes, d.group_facts.direct_support_count_s) for d in decisions],
        [
            (
                f.sample_name,
                [
                    (
                        label,
                        r.targeted_product_projection.counted_detection,
                        r.reported_rt,
                    )
                    for label, r in f.results.items()
                ],
            )
            for f in execution.active_output.file_results
        ],
    )
    if accepted:
        decision = accepted[0]
        assert decision.target_cell.content.method_result == "HOLD"
        assert decision.group_facts.direct_support_count_s == 2
        assert decision.candidate_universe.localization.location.basis.startswith(
            "trajectory" if istd_backfill else "paired_istd"
        )
        assert all(
            s.sample.metadata.raw_stem != "SampleC"
            for s in decision.group_facts.group_context.supports
        )
        assert (
            decision.matched_candidate.peak_observation.content.integration.rt_apex_min
            == pytest.approx(1.49 if istd_backfill else 1.9, abs=0.005)
        )
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    values = iter(workbook["XIC Results"].values)
    headers = next(values)
    rows = [dict(zip(headers, row, strict=True)) for row in values]
    workbook.close()
    row = next(
        r for r in rows if r["SampleName"] == "SampleC" and r["Target"] == "Analyte"
    )
    # A Backfill-located analyte peak takes its identity from the counted
    # paired ISTD at the confirmed offset, even when the narrow configured
    # window truncated the first capture.
    paired_identity = not (istd_backfill or missing_pair)
    counted = istd_backfill or paired_identity
    assert row["Counted Detection"] == ("TRUE" if counted else "FALSE")
    if istd_backfill:
        assert initial_counts and not any(initial_counts)
    if not missing_pair:
        assert row["RT"] == pytest.approx(1.49 if istd_backfill else 1.9, abs=0.005)
        assert row["Area"] > 0
        candidate = accepted[0].matched_candidate.peak_observation.content.integration
        assert row["PeakStart"] == pytest.approx(candidate.rt_left_min, abs=5e-5)
        assert row["PeakEnd"] == pytest.approx(candidate.rt_right_min, abs=5e-5)
        rendered = next(
            f
            for f in execution.active_output.file_results
            if f.sample_name == "SampleC"
        ).results["Analyte"]
        assert rendered.quality_flags == ()
        assert rendered.reported_peak_start == candidate.rt_left_min
        assert rendered.reported_peak_end == candidate.rt_right_min
        assert rendered.reported_peak_area == candidate.area_ms1_morphology
        from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource

        saved = next(
            r
            for r in TargetedWorkbookSource(workbook_path).records
            if r["SampleName"] == "SampleC" and r["Target"] == "Analyte"
        )
        assert saved["Review"] == row["Review"]
        assert saved["Review Reason"] == row["Review Reason"]
        if counted and sample_review:
            assert row["Review"] == "Check"
            assert row["Review Reason"] == "證據需檢查"
            assert (
                "sample_review" in rendered.targeted_product_projection.review_reasons
            )
        elif counted:
            assert row["Review"] is None
            assert row["Review Reason"] is None
            assert row["Review Evidence"] is None
        else:
            assert "backfill" in str(row["Review Evidence"]).lower()
        assert any(
            (record.sample_name, record.target_label, record.issue)
            == ("SampleC", "Analyte", "BACKFILL_ACCEPTED")
            for record in execution.active_output.diagnostics
        )
        ref = next(
            r for r in rows if r["SampleName"] == "SampleA" and r["Target"] == "Analyte"
        )
        assert row["Area"] / ref["Area"] == pytest.approx(3.0, rel=0.03)
        assert ref["Counted Detection"] == "TRUE"


def test_paired_position_rejects_remote_nl_peak_in_workbook_and_inspector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import nullcontext

    from xic_extractor.configuration.models import ExtractionConfig
    from xic_extractor.configuration.targets import parse_target_rows
    from xic_extractor.extraction import pipeline, target_extraction
    from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
    from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource

    data = tmp_path / "raw"
    data.mkdir()
    for name in ("Near", "Far", "Spike"):
        (data / f"{name}.raw").write_bytes(name.encode())
    config = ExtractionConfig(
        data_dir=data,
        dll_dir=tmp_path,
        output_csv=tmp_path / "result.csv",
        diagnostics_csv=tmp_path / "diag.csv",
        smooth_window=9,
        smooth_polyorder=2,
        peak_rel_height=0.02,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )
    analyte, istd = parse_target_rows(
        [
            dict(
                label="Analyte",
                mz="250",
                expected_rt_min="1.5",
                cid_nl_tag="dR",
                role="analyte",
                istd_pair="ISTD",
            ),
            dict(
                label="ISTD",
                mz="255",
                expected_rt_min="1.0",
                cid_nl_tag="dR",
                role="istd",
                istd_pair="",
            ),
        ]
    )

    class Raw:
        def __init__(self, name):
            self.name = name

        def acquisition_rt_bounds(self):
            return 0.5, 2.5

        def extract_xic(self, mz, rt_min, rt_max, _ppm):
            rt = np.linspace(rt_min, rt_max, 2001)
            center = 1.0 if mz == 255.0 else 1.5
            signal = 10 + 2000 * np.exp(-(((rt - center) / 0.025) ** 2))
            if self.name == "Spike" and mz == 250.0:
                signal = np.zeros_like(rt)
                signal[np.argmin(abs(rt - 1.5))] = 2000
                signal += 1000 * np.exp(-(((rt - 2.1) / 0.025) ** 2))
            if self.name == "Far" and mz == 250.0:
                # A second, recorded-NL-supported peak wins selection over
                # the unfragmented peak at the expected paired position.
                signal += 4000 * np.exp(-(((rt - 2.1) / 0.025) ** 2))
            return rt, signal

        def iter_ms2_scans(self, rt_min, rt_max):
            for index, mz in enumerate((255.0, 250.0), 1):
                if mz == 250.0 and self.name in {"Near", "Spike"}:
                    continue
                rt = 1.0 if mz == 255.0 else 2.1
                if rt_min <= rt <= rt_max:
                    yield Ms2ScanEvent(
                        scan=Ms2Scan(
                            scan_number=index,
                            rt=rt,
                            precursor_mz=mz,
                            masses=np.array([mz - 116.0474]),
                            intensities=np.array([500.0]),
                            base_peak=500.0,
                        ),
                        parse_error=None,
                        scan_number=index,
                    )

    monkeypatch.setattr(pipeline, "preflight_raw_reader", lambda _path: [])
    monkeypatch.setattr(
        target_extraction,
        "open_raw",
        lambda path, *_a, **_k: nullcontext(Raw(path.stem)),
    )
    path = tmp_path / "paired.xlsx"
    execution = TargetedApplicationService(pipeline.run_pipeline).execute_application(
        build_targeted_application_request(
            config,
            [analyte, istd],
            injection_order={"Near": 1, "Far": 2, "Spike": 3},
            excel_output_path=path,
        )
    )
    results = {
        f.sample_name: f.results["Analyte"]
        for f in execution.active_output.file_results
    }
    near, far = results["Near"], results["Far"]
    assert far.reported_rt is None
    assert far.reported_peak_area is None
    assert far.reported_peak_start is None
    assert far.reported_peak_end is None
    assert not far.targeted_product_projection.counted_detection
    assert any(
        record.sample_name == "Far"
        and record.issue == "OFF_ISTD_POSITION"
        and "2.100" in record.reason
        for record in execution.active_output.diagnostics
    )
    assert near.reported_rt == pytest.approx(1.5, abs=0.005)
    assert near.targeted_product_projection.counted_detection
    assert "paired_istd_identity" in near.targeted_product_projection.support_reasons
    workbook = load_workbook(path, read_only=True, data_only=True)
    values = iter(workbook["XIC Results"].values)
    headers = next(values)
    rows = {
        r["SampleName"]: r
        for cells in values
        if (r := dict(zip(headers, cells, strict=True)))["Target"] == "Analyte"
    }
    workbook.close()
    assert rows["Far"]["Counted Detection"] == "FALSE"
    assert rows["Far"]["Area"] == "ND"
    assert rows["Far"]["RT"] == "ND"
    assert rows["Far"]["Review"] is None
    assert rows["Far"]["Review Reason"] is None
    assert rows["Near"]["Review"] is None
    assert rows["Spike"]["Counted Detection"] == "FALSE"
    assert rows["Spike"]["Area"] == "ND"
    assert rows["Spike"]["Review"] is None
    assert rows["Spike"]["Review Reason"] is None
    assert any(
        record.sample_name == "Spike"
        and record.issue == "PAIRED_POSITION_SUPPORT_REJECTED"
        and "insufficient_original_xic_support" in record.reason
        for record in execution.active_output.diagnostics
    )
    from xic_extractor.output.review_queue_model import review_queue_rows

    queue = review_queue_rows(
        _run_output_to_long_rows(execution.active_output, [analyte, istd]), []
    )
    assert not any(row["Target"] == "Analyte" for row in queue)
    saved = {
        r["SampleName"]: r
        for r in TargetedWorkbookSource(path).records
        if r["Target"] == "Analyte"
    }
    for sample in ("Near", "Far", "Spike"):
        assert saved[sample]["Review"] == rows[sample]["Review"]
        assert saved[sample]["Review Reason"] == rows[sample]["Review Reason"]
    for column in ("RT", "Area", "Counted Detection"):
        assert saved["Far"][column] == rows["Far"][column]


def test_cli_and_workbench_route_through_acquisition_service_boundary() -> None:
    import scripts.run_extraction as cli_module
    import xic_workbench.targeted as workbench_module

    cli_source = inspect.getsource(cli_module)
    workbench_source = inspect.getsource(workbench_module)
    assert "extractor.run" not in cli_source
    assert "extractor.run" not in workbench_source
    assert "extractor.acquire" in cli_source
    assert "extractor.acquire" in workbench_source
    assert "write_excel_from_run_output" not in cli_source
    assert "write_excel_from_run_output" not in workbench_source


def test_publication_lists_samples_in_injection_order(tmp_path: Path) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
        sample_peak_scales={"SampleA": 1.0, "SampleB": 2.5},
    )
    request = build_targeted_application_request(
        config,
        [target],
        excel_output_path=config.output_csv.with_suffix(".xlsx"),
        injection_order={"SampleA": 2, "SampleB": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)

    published = publish_targeted_outputs(request, finalized, output)

    # Readers follow the run as acquired, not the RAW file names.
    files = published.output.file_results
    assert [fr.sample_name for fr in files] == ["SampleB", "SampleA"]
    areas = [fr.results[target.label].reported_peak_area for fr in files]
    assert areas[0] == pytest.approx(areas[1] * 2.5, rel=1e-2)


def test_istd_backfill_keeps_a_drifting_target_as_one_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixed Target is one identity however far its RT drifts over the run."""
    from contextlib import nullcontext

    from xic_extractor.configuration.models import ExtractionConfig, Target
    from xic_extractor.extraction import pipeline, target_extraction
    from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent

    mz, loss = 255.0, 116.0474
    # Steady drift along injection order spans more than the grouping RT limit.
    apex = {"S1": 1.0, "S2": 1.8, "S3": 2.6, "S4": 3.4, "S5": 4.2}
    data = tmp_path / "raw"
    data.mkdir()
    for name in apex:
        (data / f"{name}.raw").write_bytes(name.encode())
    config = ExtractionConfig(
        data_dir=data,
        dll_dir=tmp_path,
        output_csv=tmp_path / "result.csv",
        diagnostics_csv=tmp_path / "diag.csv",
        smooth_window=9,
        smooth_polyorder=2,
        peak_rel_height=0.02,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )
    istd = Target(
        label="ISTD",
        mz=mz,
        rt_min=0.5,
        rt_max=4.7,
        ppm_tol=20.0,
        neutral_loss_da=loss,
        cid_nl_tag="dR",
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=True,
        istd_pair="",
    )

    class Raw:
        def __init__(self, name):
            self.name = name

        def acquisition_rt_bounds(self):
            return 0.5, 4.7

        def extract_xic(self, _mz, rt_min, rt_max, _ppm):
            rt = np.linspace(rt_min, rt_max, 2401)
            return rt, 10 + 5000.0 * np.exp(-(((rt - apex[self.name]) / 0.02) ** 2))

        def iter_ms2_scans(self, rt_min, rt_max):
            # S3 acquired no MS2 on its peak: its direct method answer is HOLD.
            rt = apex[self.name]
            if self.name != "S3" and rt_min <= rt <= rt_max:
                yield Ms2ScanEvent(
                    scan=Ms2Scan(
                        scan_number=1,
                        rt=rt,
                        precursor_mz=mz,
                        masses=np.array([mz - loss]),
                        intensities=np.array([500.0]),
                        base_peak=500.0,
                    ),
                    parse_error=None,
                    scan_number=1,
                )

    monkeypatch.setattr(pipeline, "preflight_raw_reader", lambda _path: [])
    monkeypatch.setattr(
        target_extraction,
        "open_raw",
        lambda path, *_a, **_k: nullcontext(Raw(path.stem)),
    )
    workbook_path = tmp_path / "drift.xlsx"
    execution = TargetedApplicationService(pipeline.run_pipeline).execute_application(
        build_targeted_application_request(
            config,
            [istd],
            injection_order={name: order for order, name in enumerate(apex, 1)},
            excel_output_path=workbook_path,
        )
    )
    [finalized] = execution.finalized_results
    accepted = [
        decision
        for decision in finalized.scientific_core.targeted_backfill_decisions
        if decision.disposition == "candidate_mapped"
    ]
    assert len(accepted) == 1
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    values = iter(workbook["XIC Results"].values)
    headers = next(values)
    rows = {row[0]: dict(zip(headers, row, strict=True)) for row in values}
    workbook.close()
    assert rows["S3"]["Counted Detection"] == "TRUE"
    assert rows["S3"]["RT"] == pytest.approx(2.6, abs=0.01)
