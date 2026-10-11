from __future__ import annotations

import csv
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

from tests.test_targeted_production_workflow import production_case
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
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.peak_detection.peak_integration import find_peak_and_area
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import build_acquisition_source_identity


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


def test_complete_finalized_population_publishes_csv_excel_and_manifest(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
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
        config.output_csv,
        config.output_csv.with_name("xic_results_long.csv"),
        config.diagnostics_csv,
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
    metadata = dict(workbook["Run Metadata"].iter_rows(values_only=True))
    workbook.close()
    assert metadata["signal_processing_method"] == config.signal_processing_method
    assert (
        metadata["nl_rt_anchor_half_window_min"] == config.nl_rt_anchor_half_window_min
    )
    assert set(config.output_csv.parent.iterdir()) == {excel_path}


def test_limited_workflow_delivers_only_workbook_and_cleans_internal_files(tmp_path):
    from xic_extractor.applications.targeted.limited_identity_workflow import (
        execute_limited_identity_workflow,
    )

    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
        sample_peak_scales={"SampleA": 1.0, "SampleB": 2.5},
    )
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1, "SampleB": 2},
        excel_output_path=tmp_path / "results.xlsx",
    )
    destination = tmp_path / "delivery"
    result = execute_limited_identity_workflow(
        request,
        application=TargetedApplicationService(lambda *_args, **_kwargs: output),
        output_root=destination,
    )
    assert list(destination.iterdir()) == [destination / "results.xlsx"]
    assert not list(tmp_path.glob(".xic-limited-*"))
    assert result.final_execution.published_paths == (destination / "results.xlsx",)
    wb = load_workbook(destination / "results.xlsx", data_only=True)
    metadata = dict(wb["Run Metadata"].values)
    assert metadata["limited_identity_gate"] == "pass"
    assert "Identity Support" not in wb.sheetnames  # No empty technical tables.
    rows = list(wb["XIC Results"].values)
    records = [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]
    areas = {row["SampleName"]: row["Area"] for row in records}
    assert areas["SampleB"] == pytest.approx(areas["SampleA"] * 2.5, rel=1e-2)
    wb.close()


@pytest.mark.parametrize(
    "stop_stage", ["Auto limited support", "Auto limited expected-diff gate"]
)
def test_cancelled_limited_workflow_removes_internal_files(tmp_path, stop_stage):
    from xic_extractor.applications.targeted.limited_identity_workflow import (
        execute_limited_identity_workflow,
    )

    config, target, output = production_case(tmp_path)
    cancelled = False

    def progress(_current, _total, stage):
        nonlocal cancelled
        cancelled = stage == stop_stage

    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=tmp_path / "results.xlsx",
        progress_callback=progress,
        should_stop=lambda: cancelled,
    )
    destination = tmp_path / "delivery"
    with pytest.raises(InterruptedError):
        execute_limited_identity_workflow(
            request,
            application=TargetedApplicationService(lambda *_args, **_kwargs: output),
            output_root=destination,
        )
    assert not destination.exists()
    assert not list(tmp_path.glob(".xic-limited-*"))


def test_duplicate_declared_output_destinations_publish_nothing(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    duplicate_excel_path = (
        config.output_csv.parent
        / "equivalent-destination"
        / ".."
        / config.output_csv.name
    )
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=duplicate_excel_path,
    )
    finalized = execute_targeted_production_workflow(request, output)

    with pytest.raises(TargetedOutputBoundaryError, match="duplicate destination"):
        publish_targeted_outputs(request, finalized, output)

    assert not any(config.output_csv.parent.iterdir())


def test_publication_returns_concrete_public_result_models(tmp_path: Path) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
        sample_peak_scales={"SampleA": 1.0, "SampleB": 2.5},
    )
    request = build_targeted_application_request(
        config,
        [target],
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
    assert rendered.model_selection_result is None


@pytest.mark.parametrize(
    ("nl_status", "not_counted_reason", "count_no_ms2_as_detected"),
    [
        ("NL_FAIL", "analyte_nl_fail_requires_policy", False),
        ("NO_MS2", "analyte_missing_ms2_requires_policy", False),
        ("NO_MS2", "analyte_missing_ms2_requires_policy", True),
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
        keep_intermediate_csv=True,
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
            replace(extraction, candidate_ms2_evidence=evidence),
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
    with config.output_csv.open(newline="", encoding="utf-8-sig") as handle:
        wide_rows = {row["SampleName"]: row for row in csv.DictReader(handle)}
    with config.output_csv.with_name("xic_results_long.csv").open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        long_rows = {row["SampleName"]: row for row in csv.DictReader(handle)}
    workbook = load_workbook(excel_path, data_only=True)
    try:
        rows = workbook["XIC Results"].iter_rows(values_only=True)
        headers = next(rows)
        workbook_rows = {row[0]: dict(zip(headers, row)) for row in rows}
        sheet = workbook["XIC Results"]
        for header in ("Area", "RT", "Counted Detection", "Review", "Review Reason"):
            index = headers.index(header) + 1
            assert not any(
                dimension.hidden and dimension.min <= index <= dimension.max
                for dimension in sheet.column_dimensions.values()
            )
        for header in ("Reason", "NL Evidence", "PeakStart", "PeakEnd", "NL"):
            index = headers.index(header) + 1
            assert any(
                dimension.hidden and dimension.min <= index <= dimension.max
                for dimension in sheet.column_dimensions.values()
            )
    finally:
        workbook.close()
    assert set(wide_rows) == set(long_rows) == set(workbook_rows) == set(decisions)
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
            assert wide_rows[sample_name][f"{target.label}_{field}"] == expected_text
            assert long_rows[sample_name][field] == expected_text
            if field != "PeakWidth":
                assert workbook_rows[sample_name][field] == float(expected_text)
        for row in (long_rows[sample_name], workbook_rows[sample_name]):
            assert row["NL Evidence"] == (
                "Not observed within tolerance"
                if nl_status == "NL_FAIL"
                else "No MS2 spectrum"
            )
            assert row["Reason"].startswith("MS1 peak measured; review:")
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
        assert workbook_rows[sample_name]["Review"] == (
            "Review" if nl_status == "NL_FAIL" else "Check"
        )
        assert workbook_rows[sample_name]["Review Reason"]
        assert "PeakWidth" not in workbook_rows[sample_name]


def test_service_publishes_no_numeric_for_finalized_no_observation(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
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
    output.file_results[0].results[target.label] = replace(
        extraction,
        peak_result=find_peak_and_area(rt, trace.intensity, config, preferred_rt=1.5),
        selected_hypothesis=None,
        acquisition_trace=trace,
        physical_evaluation=evaluate_physical_peak(
            prepare_physical_signal(rt, trace.intensity, profile=profile),
            selection_subject_id=target.label,
            query_rt_min=1.5,
            request_context_rt_start_min=trace.rt_min,
            request_context_rt_end_min=trace.rt_max,
        ),
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
    with config.output_csv.open(newline="", encoding="utf-8-sig") as handle:
        wide_row = next(csv.DictReader(handle))
    with config.output_csv.with_name("xic_results_long.csv").open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        long_row = next(csv.DictReader(handle))
    workbook = load_workbook(excel_path, read_only=True, data_only=True)
    try:
        rows = workbook["XIC Results"].iter_rows(values_only=True)
        workbook_row = dict(zip(next(rows), next(rows)))
    finally:
        workbook.close()
    for field in ("RT", "Area", "PeakStart", "PeakEnd", "PeakWidth"):
        assert wide_row[f"{target.label}_{field}"] == "ND"
        assert long_row[field] == "ND"
        if field != "PeakWidth":
            assert workbook_row[field] == "ND"
    for row in (long_row, workbook_row):
        assert row["Counted Detection"] == "FALSE"
    assert long_row["Review State"] == "review_required"
    assert long_row["Projection Reason"] == "finalized_searched_no_observation"
    assert workbook_row["Review"] == "Check"
    assert workbook_row["Review Reason"]
    assert workbook_row["Reason"] == long_row["Reason"]


def test_publication_retires_equal_area_legacy_selection_authority(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)

    published = publish_targeted_outputs(request, finalized, output)

    rendered = published.output.file_results[0].results[target.label]
    assert rendered.selected_hypothesis is None
    assert rendered.selection_decision is None
    assert rendered.model_selection_result is None
    assert rendered.peak_result.candidates == ()
    assert rendered.peak_result.candidate_scores == ()


def test_neutral_loss_disabled_target_publishes_ms1_backfill(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    target = replace(
        target,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
    )
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )

    finalized = execute_targeted_production_workflow(request, output)
    published = publish_targeted_outputs(request, finalized, output)

    assert config.output_csv in published.paths
    assert config.output_csv.is_file()
    with config.output_csv.with_name("xic_results_long.csv").open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        row = next(csv.DictReader(handle))
    assert row["NL"] == ""
    assert row["NL Evidence"] == "Not requested"
    assert row["Area"] != "ND"
    assert row["Reason"].startswith("MS1 peak measured")


def test_acquisition_baseline_projection_preserves_not_counted_cells(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    extraction = output.file_results[0].results[target.label]
    projection = extraction.targeted_product_projection
    assert projection is not None
    output.file_results[0].results[target.label] = replace(
        extraction,
        targeted_product_projection=replace(
            projection,
            product_state="not_counted",
            counted_detection=False,
            projection_reason="baseline_nl_fail",
        ),
    )
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        output_projection="acquisition_baseline",
    )
    finalized = execute_targeted_production_workflow(request, output)

    publication = publish_targeted_outputs(request, finalized, output)

    with config.output_csv.with_name("xic_results_long.csv").open(
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        row = next(csv.DictReader(handle))
    assert row["Product State"] == "not_counted"
    assert row["Counted Detection"] == "FALSE"
    assert row["RT"] == f"{extraction.reported_rt:.4f}"
    assert row["Area"] == f"{extraction.reported_peak_area:.2f}"
    assert publication.output is not output


def test_workbench_summary_consumes_the_finalized_publication_view(
    tmp_path: Path,
) -> None:
    from xic_workbench.targeted import build_summary

    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
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
        target=target,
        sample_name="SampleA",
    )
    assert policy_result.targeted_product_projection is not None
    assert policy_result.targeted_product_projection.counted_detection is False
    assert (
        "analyte_nl_fail_requires_policy"
        in policy_result.targeted_product_projection.not_counted_reasons
    )
    output.file_results[0].results[target.label] = policy_result
    request = build_targeted_application_request(
        config,
        [target],
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
    with config.output_csv.with_name("xic_results_long.csv").open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        row = next(csv.DictReader(handle))
    assert row["Product State"] == "not_counted"
    assert row["Counted Detection"] == "FALSE"
    assert row["RT"] == f"{final_result.reported_rt:.4f}"
    assert row["Area"] == f"{finalized_value:.2f}"


def test_collision_leaves_existing_output_and_publishes_nothing_else(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    config.output_csv.parent.mkdir(parents=True, exist_ok=True)
    config.output_csv.write_bytes(b"existing")

    with pytest.raises(TargetedOutputBoundaryError, match="already exists"):
        publish_targeted_outputs(request, finalized, output)

    assert config.output_csv.read_bytes() == b"existing"
    assert sorted(path.name for path in config.output_csv.parent.iterdir()) == [
        config.output_csv.name
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
    config = replace(config, keep_intermediate_csv=True)
    request = build_targeted_application_request(
        config,
        [target],
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
                    target_label=target.label,
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
    expected_paths = {
        config.output_csv,
        config.output_csv.with_name("xic_results_long.csv"),
        config.diagnostics_csv,
    }
    assert set(winning.paths) == expected_paths
    assert all(path.is_file() for path in winning.paths)
    diagnostics_text = config.diagnostics_csv.read_text(encoding="utf-8-sig")
    assert (
        sum(marker in diagnostics_text for marker in ("concurrent-a", "concurrent-b"))
        == 1
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
    config = replace(config, keep_intermediate_csv=True)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    original_publish = targeted_output_boundary._publish_without_replace
    commit_count = 0
    failed = False

    def fail_second_commit(source: Path, target_path: Path) -> None:
        nonlocal commit_count, failed
        commit_count += 1
        if commit_count == 2 and not failed:
            failed = True
            raise OSError("commit failed")
        original_publish(source, target_path)

    monkeypatch.setattr(
        targeted_output_boundary,
        "_publish_without_replace",
        fail_second_commit,
    )

    with pytest.raises(TargetedOutputBoundaryError, match="rolled back"):
        publish_targeted_outputs(request, finalized, output)

    assert not any(config.output_csv.parent.iterdir())


def test_commit_cancellation_after_first_move_rolls_back_all_final_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    cancellation_requested = False
    request = build_targeted_application_request(
        config,
        [target],
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
    config = replace(config, keep_intermediate_csv=True)
    request = build_targeted_application_request(
        config,
        [target],
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
    config = replace(config, keep_intermediate_csv=True)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    original_paths = publish_targeted_outputs(request, finalized, output).paths
    original_bytes = {path: path.read_bytes() for path in original_paths}
    original_replace = Path.replace
    commit_count = 0

    def fail_second_new_output(source: Path, target_path: Path) -> Path:
        nonlocal commit_count
        if source.parent.name.startswith(".xic-targeted-publish-"):
            commit_count += 1
            if commit_count == 2:
                raise OSError("replacement failed")
        return original_replace(source, target_path)

    monkeypatch.setattr(Path, "replace", fail_second_new_output)

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
    config = replace(config, keep_intermediate_csv=True)
    request = build_targeted_application_request(
        config,
        [target],
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
            if commit_count == 2:
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
    stale_output.file_results[0].results[stale_target.label] = replace(
        stale_extraction,
        candidate_ms2_evidence=replace(
            stale_candidate,
            best_product_evidence=replace(
                stale_product,
                acquisition_source_id=stale_source.acquisition_source_id,
            ),
        ),
    )
    stale_request = build_targeted_application_request(
        stale_config,
        [stale_target],
        injection_order={"SampleA": 1},
    )
    stale_finalized = execute_targeted_production_workflow(
        stale_request,
        stale_output,
    )
    current_request = build_targeted_application_request(
        current_config,
        [current_target],
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


def test_publisher_rejects_mutated_owner_snapshot(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    completed = execute_targeted_production_workflow(request, output)
    completed.prepared.acquisition_snapshot.file_results[0].group = "mutated"

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
        injection_order={"SampleA": 1},
        should_stop=lambda: True,
    )
    monkeypatch.setattr(
        "xic_extractor.applications.targeted.production_workflow."
        "complete_prepared_targeted_production_workflow",
        lambda *_args: pytest.fail("cancelled run invoked finalization"),
    )

    execution = TargetedApplicationService(
        lambda *_args, **_kwargs: pytest.fail("cancelled run invoked acquisition")
    ).execute_application(request)

    assert execution.active_output.file_results == []
    assert execution.active_output.diagnostics == []
    assert execution.finalized_results == ()
    assert execution.published_paths == ()


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
