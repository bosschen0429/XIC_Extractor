import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.alignment import workflow as alignment_workflow
from xic_extractor.alignment.result.application_result import (
    FinalizedTargetApplicationResult,
)
from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.execution_runtime_observability import (
    RuntimeMetricAvailability,
    canonical_runtime_observability_ledger_bytes,
)
from xic_extractor.extraction import pipeline as extraction_pipeline
from xic_extractor.extraction import targeted_production_workflow as production_workflow
from xic_extractor.extraction.targeted_inputs import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.extraction.targeted_production_workflow import (
    execute_targeted_production_workflow,
)
from xic_extractor.extraction.targeted_service import build_targeted_application_request
from xic_extractor.extractor import ExtractionResult, FileResult, RunOutput
from xic_extractor.neutral_loss import CandidateMS2Evidence, MS2ProductEvidence
from xic_extractor.peak_detection.capture_boundary_policy import (
    promote_targeted_boundary_hypothesis,
)
from xic_extractor.peak_detection.hypotheses import build_peak_hypotheses
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedPriorContext,
    build_targeted_product_projection,
)
from xic_extractor.peak_detection.traces import Trace, targeted_trace_group
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    build_acquisition_source_identity,
)
from xic_extractor.signal_processing import find_peak_and_area


def test_retained_trace_reaches_one_finalized_target_result(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1},
        ),
        output,
    )

    assert len(finalized) == 1
    assert (
        finalized[0].completed_projection.row_binding.target.definition.target == target
    )
    assert len(finalized[0].canonical_samples) == 1
    assert finalized[0].scientific_core.cell_observation_handoff is None
    assert finalized[0].scientific_core.cell_observations == ()
    assert finalized[0].scientific_core.final_cross_sample_receipt.repair_epoch == 0
    assert finalized[0].scientific_core.repair_receipt is None


def test_nl_event_outside_exact_target_precursor_is_not_target_evidence(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
    )
    original = output.file_results[0].results[target.label]
    evidence = original.candidate_ms2_evidence
    assert evidence is not None
    product = evidence.best_product_evidence
    assert product is not None
    shifted_precursor = target.mz + 0.4
    output.file_results[0].results[target.label] = replace(
        original,
        candidate_ms2_evidence=replace(
            evidence,
            best_product_evidence=replace(
                product,
                precursor_mz=shifted_precursor,
                product_mz=shifted_precursor - product.observed_loss_da,
            ),
        ),
    )
    second = output.file_results[1].results[target.label]
    second_evidence = second.candidate_ms2_evidence
    assert second_evidence is not None
    second_product = second_evidence.best_product_evidence
    assert second_product is not None
    output.file_results[1].results[target.label] = replace(
        second,
        candidate_ms2_evidence=replace(
            second_evidence,
            best_product_evidence=replace(
                second_product,
                precursor_mz=shifted_precursor,
                product_mz=shifted_precursor - second_product.observed_loss_da,
            ),
        ),
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1, "SampleB": 2},
        ),
        output,
    )

    assert len(finalized) == 1


def test_nl_event_outside_retained_peak_finalizes_without_direct_support(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, target, output = production_case(tmp_path)
    original = output.file_results[0].results[target.label]
    selected = original.selected_hypothesis
    evidence = original.candidate_ms2_evidence
    assert selected is not None and evidence is not None
    product = evidence.best_product_evidence
    assert product is not None
    outside_rt = selected.integration.rt_right_min + 0.01
    assert outside_rt < target.rt_max
    output.file_results[0].results[target.label] = replace(
        original,
        candidate_ms2_evidence=replace(
            evidence,
            best_scan_rt=outside_rt,
            best_product_evidence=replace(product, scan_rt=outside_rt),
        ),
    )
    assessments = []
    original_assessment = production_workflow.mint_cid_nl_method_assessment_ref

    def record_assessment(**kwargs):
        assessment = original_assessment(**kwargs)
        assessments.append(assessment)
        return assessment

    monkeypatch.setattr(
        production_workflow,
        "mint_cid_nl_method_assessment_ref",
        record_assessment,
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1},
        ),
        output,
    )

    assert len(finalized) == 1
    # A local MS1 value is allowed; an outside event must not become direct support.
    assert len(assessments) == 1
    assert assessments[0].individual_support_refs == ()
    assert assessments[0].content.method_result != "SUPPORT"


def test_retained_peak_outside_target_rt_is_not_a_target_capture(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
    )
    original = output.file_results[0].results[target.label]
    selected = original.selected_hypothesis
    assert selected is not None
    outside_rt = target.rt_min - 0.1
    output.file_results[0].results[target.label] = replace(
        original,
        selected_hypothesis=replace(
            selected,
            integration=replace(
                selected.integration,
                rt_apex_min=outside_rt,
                raw_apex_rt_min=outside_rt,
            ),
        ),
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1, "SampleB": 2},
        ),
        output,
    )

    assert len(finalized) == 1
    assert len(finalized[0].canonical_samples) == 2


def test_each_nl_target_gets_distinct_runtime_attempt_and_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, target, output = production_case(tmp_path)
    second_target = replace(target, label="Analyte2")
    for file_result in output.file_results:
        original = file_result.results[target.label]
        file_result.results[second_target.label] = replace(
            original,
            target_label=second_target.label,
        )

    original_start = production_workflow.start_runtime_attempt
    started = []

    def record_start(**kwargs):
        runtime = original_start(**kwargs)
        started.append((kwargs["attempt"], runtime))
        return runtime

    monkeypatch.setattr(
        production_workflow,
        "start_runtime_attempt",
        record_start,
    )
    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target, second_target],
            injection_order={"SampleA": 1},
        ),
        output,
    )

    assert len(finalized) == 2
    assert len(started) == 2
    attempts = tuple(attempt for attempt, _runtime in started)
    assert len({attempt.processing_attempt_id for attempt in attempts}) == 2
    assert len({attempt.processing_request_id for attempt in attempts}) == 1
    manifests = tuple(runtime.manifest for _attempt, runtime in started)
    assert all(not runtime.manifest_preflight_failed for _attempt, runtime in started)
    assert all(len(manifest.work_unit_keys) == 1 for manifest in manifests)
    assert all(
        manifest.attempt is attempt for attempt, manifest in zip(attempts, manifests)
    )
    assert {manifest.work_unit_keys[0] for manifest in manifests} == {
        "targeted-workflow:"
        + result.completed_projection.row_binding.target.definition.target_definition_id
        for result in finalized
    }


def test_runtime_telemetry_marks_unobserved_rss_unsupported(monkeypatch) -> None:
    monkeypatch.setattr(
        production_workflow,
        "runtime_started_worker_keys",
        lambda _coordinator: ("worker-a", "worker-b"),
    )

    samples = production_workflow._runtime_telemetry(object())
    reason = "not_collected_by_targeted_foreground_runner"

    assert samples.event_monotonic_offsets_ns is None
    assert samples.artifact_io is None
    assert samples.unavailable_reason == reason
    assert (
        samples.process_tree_peak_rss.availability
        is RuntimeMetricAvailability.UNSUPPORTED
    )
    assert samples.process_tree_peak_rss.value is None
    assert samples.process_tree_peak_rss.reason == reason
    assert tuple(sample.worker_key for sample in samples.worker_peak_rss) == (
        "worker-a",
        "worker-b",
    )
    assert all(
        sample.availability is RuntimeMetricAvailability.UNSUPPORTED
        and sample.value is None
        and sample.reason == reason
        for sample in samples.worker_peak_rss
    )


def test_targeted_runtime_receipt_does_not_claim_uncollected_metrics_measured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, target, output = production_case(tmp_path)
    original_execute = production_workflow.execute_alignment_workflow
    terminals = []

    def capture_terminal(request, adapter):
        terminal = original_execute(request, adapter)
        terminals.append(terminal)
        return terminal

    monkeypatch.setattr(
        production_workflow,
        "execute_alignment_workflow",
        capture_terminal,
    )
    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1},
        ),
        output,
    )

    assert len(finalized) == 1
    assert len(terminals) == 1
    receipt = terminals[0].runtime_receipt
    assert receipt.supervisor.terminal_outcome.value == "succeeded"
    assert all(
        event.monotonic_offset_ns is None
        for event in receipt.observability_ledger.events
    )
    canonical = json.loads(
        canonical_runtime_observability_ledger_bytes(receipt.observability_ledger)
    )
    assert canonical["schema_version"] == "execution_runtime_observability_ledger_v2"
    metrics = {metric.name: metric for metric in receipt.observability_ledger.metrics}
    for name in (
        "artifact_read_count",
        "artifact_read_bytes",
        "artifact_write_count",
        "artifact_write_bytes",
        "coordinator_work_seconds",
        "attempt_wall_seconds",
        "stage_or_unit_elapsed_seconds",
    ):
        assert metrics[name].availability is RuntimeMetricAvailability.UNSUPPORTED
        assert metrics[name].value is None
        assert metrics[name].reason == "not_collected_by_targeted_foreground_runner"


def test_diagnostic_nl_evidence_finalizes_without_strict_support(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
    )
    original = output.file_results[0].results[target.label]
    candidate_evidence = original.candidate_ms2_evidence
    assert candidate_evidence is not None
    product_evidence = candidate_evidence.best_product_evidence
    assert product_evidence is not None
    observed_loss_da = 116.0058
    output.file_results[0].results[target.label] = replace(
        original,
        candidate_ms2_evidence=replace(
            candidate_evidence,
            nl_match=False,
            nl_status="NL_FAIL",
            strict_nl_scan_count=0,
            best_loss_ppm=50.0,
            best_product_evidence=replace(
                product_evidence,
                product_mz=product_evidence.precursor_mz - observed_loss_da,
                observed_loss_da=observed_loss_da,
                observed_loss_error_ppm=50.0,
            ),
        ),
    )
    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1, "SampleB": 2},
        ),
        output,
    )

    assert len(finalized) == 1
    assert finalized[0].completed_projection.counted_detection_count == 2
    assert finalized[0].completed_projection.no_write_count == 0


@pytest.mark.parametrize("promoted_retained", (False, True))
def test_finalization_preserves_the_acquisition_selected_hypothesis(
    tmp_path: Path,
    monkeypatch,
    promoted_retained: bool,
) -> None:
    config, target, output = production_case(tmp_path)
    rt = np.linspace(1.0, 2.0, 401)
    intensity = (
        10.0
        + 100.0 * np.exp(-((rt - 1.40) ** 2) / 0.0005)
        + 1000.0 * np.exp(-((rt - 1.62) ** 2) / 0.0005)
    )
    trace = Trace.from_arrays(
        sample_name="SampleA",
        mz=target.mz,
        rt=rt,
        intensity=intensity,
        rt_min=target.rt_min,
        rt_max=target.rt_max,
        ppm_tol=target.ppm_tol,
    )
    peak_result = find_peak_and_area(rt, intensity, config, preferred_rt=1.62)
    hypotheses = build_peak_hypotheses(
        analysis_mode="targeted",
        common_evidence_source_id="test-retained-trace",
        sample_name="SampleA",
        target_label=target.label,
        role="Analyte",
        istd_pair="",
        resolver_mode=config.resolver_mode,
        peak_result=peak_result,
        trace_group=targeted_trace_group(
            trace,
            target_label=target.label,
            resolver_mode=config.resolver_mode,
        ),
        count_no_ms2_as_detected=True,
    )
    assert len(hypotheses) >= 2
    selected = min(hypotheses, key=lambda item: item.integration.height_raw)
    expected_capture_height = selected.integration.height_smoothed
    if promoted_retained:
        selected = promote_targeted_boundary_hypothesis(
            selected,
            rt=rt,
            intensity=intensity,
            context_rt_start=float(rt[0]),
            context_rt_end=float(rt[-1]),
            morphology_window_points=15,
        ).hypothesis
        selected = replace(
            selected,
            integration=replace(
                selected.integration,
                height_smoothed=selected.integration.height_smoothed * 0.75,
            ),
        )
    original = output.file_results[0].results[target.label]
    evidence = original.candidate_ms2_evidence
    assert evidence is not None
    product = evidence.best_product_evidence
    assert product is not None
    output.file_results[0].results[target.label] = replace(
        original,
        peak_result=peak_result,
        selected_hypothesis=selected,
        acquisition_trace=trace,
        candidate_ms2_evidence=replace(
            evidence,
            best_scan_rt=selected.integration.rt_apex_min,
            best_product_evidence=replace(
                product,
                scan_rt=selected.integration.rt_apex_min,
            ),
        ),
        targeted_product_projection=build_targeted_product_projection(
            TargetedPriorContext(role="Analyte"),
            rt=selected.integration.rt_apex_min,
            area=selected.integration.area_raw_counts_seconds,
            confidence="HIGH",
            nl_status="OK",
        ),
    )
    selected_area = output.file_results[0].results[target.label].reported_peak_area

    captured = []
    original_capture = production_workflow.capture_peak_hypotheses

    def record_capture(**kwargs):
        value = original_capture(**kwargs)
        captured.append(value)
        return value

    monkeypatch.setattr(
        production_workflow,
        "capture_peak_hypotheses",
        record_capture,
    )
    monkeypatch.setattr(
        alignment_workflow,
        "capture_peak_hypotheses",
        record_capture,
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1},
        ),
        output,
    )

    assert finalized[0].completed_projection.decisions[0].value == selected_area
    assert len(captured) == 1
    assert captured[0].selected_hypothesis is not None
    captured_integration = captured[0].selected_hypothesis.integration
    assert captured_integration.rt_apex_min == selected.integration.rt_apex_min
    assert captured_integration.raw_apex_rt_min == (
        selected.integration.raw_apex_rt_min
    )
    assert captured_integration.height_raw == selected.integration.height_raw
    assert captured_integration.height_smoothed == expected_capture_height


@pytest.mark.parametrize("sample_names", [("SampleA",), ("SampleA", "SampleB")])
def test_missing_selected_peak_finalizes_as_searched_no_observation(
    tmp_path: Path,
    sample_names: tuple[str, ...],
) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=sample_names,
    )
    original = output.file_results[0].results[target.label]
    rt = np.linspace(1.0, 2.0, 101)
    flat_intensity = np.full_like(rt, 10.0)
    trace = Trace.from_arrays(
        sample_name="SampleA",
        mz=target.mz,
        rt=rt,
        intensity=flat_intensity,
        rt_min=target.rt_min,
        rt_max=target.rt_max,
        ppm_tol=target.ppm_tol,
    )
    output.file_results[0].results[target.label] = replace(
        original,
        peak_result=find_peak_and_area(
            rt,
            flat_intensity,
            config,
            preferred_rt=1.5,
        ),
        selected_hypothesis=None,
        acquisition_trace=trace,
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={name: index for index, name in enumerate(sample_names, 1)},
        ),
        output,
    )

    projection = finalized[0].completed_projection
    assert tuple(
        subject.terminal_subject.variant for subject in projection.cell_subjects
    ) == ("searched_no_observation",) + ("observed",) * (len(sample_names) - 1)
    assert projection.counted_detection_count == len(sample_names) - 1
    assert projection.no_write_count == 1


def test_assessment_handoff_is_independent_of_injection_order(tmp_path: Path) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleB": 1, "SampleA": 2},
        ),
        output,
    )

    assert finalized[0].completed_projection.counted_detection_count == 2


def test_retained_boundary_apex_rebinds_to_same_physical_raw_peak(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    original = output.file_results[0].results[target.label]
    selected = original.selected_hypothesis
    assert selected is not None
    integration = selected.integration
    shifted_apex = (integration.rt_apex_min + integration.rt_right_min) / 2
    shifted = replace(
        selected,
        integration=replace(
            integration,
            rt_apex_min=shifted_apex,
            raw_apex_rt_min=shifted_apex,
            integration_method="chrom_peak_segment_gaussian15",
        ),
    )
    evidence = original.candidate_ms2_evidence
    assert evidence is not None
    product = evidence.best_product_evidence
    assert product is not None
    output.file_results[0].results[target.label] = replace(
        original,
        selected_hypothesis=shifted,
        candidate_ms2_evidence=replace(
            evidence,
            best_scan_rt=integration.rt_apex_min,
            best_product_evidence=replace(
                product,
                scan_rt=integration.rt_apex_min,
            ),
        ),
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1},
        ),
        output,
    )

    assert finalized[0].completed_projection.counted_detection_count == 1


def test_neutral_loss_disabled_target_uses_shared_cell_decision(tmp_path: Path) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
    )
    target = replace(
        target,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
    )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1, "SampleB": 2},
        ),
        output,
    )

    assert len(finalized) == 1
    result = finalized[0]
    assert isinstance(result, FinalizedTargetApplicationResult)
    assert result.completed_projection.row_binding.target.definition.target == target
    assert len(result.completed_projection.decisions) == 2
    assert len(result.scientific_core.final_population) == 1
    assert len(result.scientific_core.final_population[0].member_subjects) == 2
    assert all(
        decision.variant == "numeric"
        for decision in result.completed_projection.decisions
    )
    decision_values = tuple(
        decision.value for decision in result.completed_projection.decisions
    )
    assert decision_values == (
        output.file_results[0].results[target.label].reported_peak_area,
        output.file_results[1].results[target.label].reported_peak_area,
    )


def test_neutral_loss_disabled_target_without_selected_ms1_is_no_write(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
    )
    target = replace(
        target,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
    )
    for file_result in output.file_results:
        original = file_result.results[target.label]
        file_result.results[target.label] = replace(
            original,
            selected_hypothesis=None,
        )

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1, "SampleB": 2},
        ),
        output,
    )

    result = finalized[0]
    assert isinstance(result, FinalizedTargetApplicationResult)
    assert len(result.completed_projection.decisions) == 2
    assert all(
        decision.variant == "searched_no_observation"
        for decision in result.completed_projection.decisions
    )
    assert all(
        decision.value is None for decision in result.completed_projection.decisions
    )


def test_neutral_loss_disabled_target_without_acquisition_trace_fails_closed(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    target = replace(
        target,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
    )
    original = output.file_results[0].results[target.label]
    output.file_results[0].results[target.label] = replace(
        original,
        selected_hypothesis=None,
        acquisition_trace=None,
    )

    with pytest.raises(RuntimeError, match="missing retained acquisition evidence"):
        execute_targeted_production_workflow(
            build_targeted_application_request(
                config,
                [target],
                injection_order={"SampleA": 1},
            ),
            output,
        )


def test_reordered_acquisition_results_bind_by_raw_sample_identity(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
    )
    output.file_results.reverse()

    finalized = execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1, "SampleB": 2},
        ),
        output,
    )

    assert [
        (sample.metadata.raw_stem, sample.metadata.sample_name)
        for sample in finalized[0].canonical_samples
    ] == [("SampleA", "SampleA"), ("SampleB", "SampleB")]


def test_crossed_acquisition_trace_sample_fails_closed(tmp_path: Path) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
    )
    extraction = output.file_results[0].results[target.label]
    trace = extraction.acquisition_trace
    assert trace is not None
    output.file_results[0].results[target.label] = replace(
        extraction,
        acquisition_trace=replace(trace, sample_name="SampleB"),
    )

    with pytest.raises(RuntimeError, match="crossed the canonical sample identity"):
        execute_targeted_production_workflow(
            build_targeted_application_request(
                config,
                [target],
                injection_order={"SampleA": 1, "SampleB": 2},
            ),
            output,
        )


def test_acquisition_boundary_stamps_ms2_source_identity(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    extraction = output.file_results[0].results[target.label]
    candidate = extraction.candidate_ms2_evidence
    assert candidate is not None
    product = candidate.best_product_evidence
    assert product is not None
    output.file_results[0].results[target.label] = replace(
        extraction,
        candidate_ms2_evidence=replace(
            candidate,
            best_product_evidence=replace(product, acquisition_source_id=""),
        ),
    )
    source = build_acquisition_source_identity(
        config.data_dir / "SampleA.raw",
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    stat = (config.data_dir / "SampleA.raw").stat()
    expectation = AcquisitionSourceExpectation(
        source=source,
        path_identity=(
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        ),
    )

    bound = extraction_pipeline._bind_acquisition_sources(
        output,
        {"SampleA": expectation},
    )

    bound_candidate = bound.file_results[0].results[target.label].candidate_ms2_evidence
    assert bound_candidate is not None
    bound_product = bound_candidate.best_product_evidence
    assert bound_product is not None
    assert bound_product.acquisition_source_id == source.acquisition_source_id


def test_unstamped_ms2_acquisition_source_fails_closed(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    extraction = output.file_results[0].results[target.label]
    candidate = extraction.candidate_ms2_evidence
    assert candidate is not None
    product = candidate.best_product_evidence
    assert product is not None
    output.file_results[0].results[target.label] = replace(
        extraction,
        candidate_ms2_evidence=replace(
            candidate,
            best_product_evidence=replace(product, acquisition_source_id=""),
        ),
    )

    with pytest.raises(RuntimeError, match="MS2 evidence crossed"):
        execute_targeted_production_workflow(
            build_targeted_application_request(
                config,
                [target],
                injection_order={"SampleA": 1},
            ),
            output,
        )


def test_crossed_ms2_acquisition_source_fails_closed(tmp_path: Path) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
    )
    first = output.file_results[0].results[target.label]
    first_candidate = first.candidate_ms2_evidence
    second_candidate = (
        output.file_results[1].results[target.label].candidate_ms2_evidence
    )
    assert first_candidate is not None
    assert second_candidate is not None
    assert second_candidate.best_product_evidence is not None
    output.file_results[0].results[target.label] = replace(
        first,
        candidate_ms2_evidence=replace(
            first_candidate,
            best_product_evidence=second_candidate.best_product_evidence,
        ),
    )

    with pytest.raises(RuntimeError, match="MS2 evidence crossed"):
        execute_targeted_production_workflow(
            build_targeted_application_request(
                config,
                [target],
                injection_order={"SampleA": 1, "SampleB": 2},
            ),
            output,
        )


def test_crossed_acquisition_result_target_fails_closed(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    extraction = output.file_results[0].results[target.label]
    output.file_results[0].results[target.label] = replace(
        extraction,
        target_label="CrossedTarget",
    )

    with pytest.raises(RuntimeError, match="crossed its declared Target identity"):
        execute_targeted_production_workflow(
            build_targeted_application_request(
                config,
                [target],
                injection_order={"SampleA": 1},
            ),
            output,
        )


def production_case(
    tmp_path: Path,
    *,
    sample_names: tuple[str, ...] = ("SampleA",),
    sample_peak_scales: dict[str, float] | None = None,
) -> tuple[ExtractionConfig, Target, RunOutput]:
    config = _config(tmp_path)
    target = _target()
    config.data_dir.mkdir()
    file_results = []
    scales = sample_peak_scales or {}
    for sample_name in sample_names:
        raw_path = config.data_dir / f"{sample_name}.raw"
        raw_path.write_bytes(sample_name.encode())
        source = build_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        scale = scales.get(sample_name, 1.0)
        rt = np.linspace(1.0, 2.0, 101)
        intensity = (10.0 + 1000.0 * np.exp(-((rt - 1.5) ** 2) / 0.002)) * scale
        trace = Trace.from_arrays(
            sample_name=sample_name,
            mz=target.mz,
            rt=rt,
            intensity=intensity,
            rt_min=target.rt_min,
            rt_max=target.rt_max,
            ppm_tol=target.ppm_tol,
        )
        peak_result = find_peak_and_area(rt, intensity, config, preferred_rt=1.5)
        hypotheses = build_peak_hypotheses(
            analysis_mode="targeted",
            common_evidence_source_id="test-retained-trace",
            sample_name=sample_name,
            target_label=target.label,
            role="Analyte",
            istd_pair="",
            resolver_mode=config.resolver_mode,
            peak_result=peak_result,
            trace_group=targeted_trace_group(
                trace,
                target_label=target.label,
                resolver_mode=config.resolver_mode,
            ),
            count_no_ms2_as_detected=True,
        )
        selected = next(item for item in hypotheses if item.audit.selected)
        file_results.append(
            FileResult(
                sample_name=sample_name,
                results={
                    target.label: ExtractionResult(
                        peak_result=peak_result,
                        nl=None,
                        candidate_ms2_evidence=CandidateMS2Evidence(
                            ms2_present=True,
                            nl_match=True,
                            nl_status="OK",
                            trigger_scan_count=1,
                            strict_nl_scan_count=1,
                            best_loss_ppm=0.0,
                            best_scan_rt=1.5,
                            best_product_base_ratio=0.5,
                            alignment_source="region",
                            best_product_evidence=MS2ProductEvidence(
                                scan_rt=1.5,
                                precursor_mz=250.0,
                                product_mz=134.0,
                                product_intensity=500.0,
                                product_base_ratio=0.5,
                                target_product_ppm=0.0,
                                observed_loss_da=116.0,
                                observed_loss_error_ppm=0.0,
                                scan_number=42,
                                acquisition_source_id=source.acquisition_source_id,
                            ),
                        ),
                        target_label=target.label,
                        role="Analyte",
                        selected_hypothesis=selected,
                        acquisition_trace=trace,
                        targeted_product_projection=build_targeted_product_projection(
                            TargetedPriorContext(role="Analyte"),
                            rt=selected.integration.rt_apex_min,
                            area=selected.integration.area_raw_counts_seconds,
                            confidence="HIGH",
                            nl_status="OK",
                        ),
                    )
                },
            )
        )
    output = RunOutput(
        file_results=file_results,
        diagnostics=[],
    )

    return config, target, output


def _config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path / "data",
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "output" / "xic_results.csv",
        diagnostics_csv=tmp_path / "output" / "xic_diagnostics.csv",
        smooth_window=9,
        smooth_polyorder=2,
        peak_rel_height=0.02,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target() -> Target:
    return Target(
        label="Analyte",
        mz=250.0,
        rt_min=1.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
    )
