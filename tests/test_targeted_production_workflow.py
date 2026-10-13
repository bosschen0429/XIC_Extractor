import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.alignment.completion import CaptureWorkflowArguments
from xic_extractor.alignment.result.application_result import (
    FinalizedTargetApplicationResult,
)
from xic_extractor.applications.targeted import production_workflow
from xic_extractor.applications.targeted.application import (
    build_targeted_application_request,
)
from xic_extractor.applications.targeted.production_workflow import (
    execute_targeted_production_workflow,
)
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, MS2ProductEvidence
from xic_extractor.execution._lifecycle import AttemptTerminalOutcome
from xic_extractor.execution._observability import (
    RuntimeMetricAvailability,
    canonical_runtime_observability_ledger_bytes,
)
from xic_extractor.extraction import pipeline as extraction_pipeline
from xic_extractor.extraction import (
    targeted_capture_population,
    targeted_cid_nl_assessment,
)
from xic_extractor.extraction.result_models import (
    ExtractionResult,
    FileResult,
    RunOutput,
)
from xic_extractor.extraction.targeted_inputs import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.extraction.targeted_peak_evidence import build_targeted_peak_evidence
from xic_extractor.extraction.targeted_product_projection import (
    build_targeted_product_projection,
)
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent, Ms2Window
from xic_extractor.output.targeted_product_model import (
    TargetedProductContext,
)
from xic_extractor.peak_detection import capture as capture_owner
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlCandidateInterval,
    CidNlEvaluationReceipt,
    CidNlEventQuery,
    evaluate_cid_nl_method_relations,
)
from xic_extractor.peak_detection.cid_nl_peak_support import DdaCidNlPeakSupportPolicy
from xic_extractor.peak_detection.hypotheses import build_peak_hypotheses
from xic_extractor.peak_detection.observations import (
    physical_peak_observation_integration,
)
from xic_extractor.peak_detection.peak_integration import find_peak_and_area
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.traces import Trace, targeted_trace_group
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    build_acquisition_source_identity,
)


@pytest.fixture(autouse=True)
def _placeholder_raw_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    # RAW files here are placeholders; order them by their file times.
    install_fake_raw_headers(monkeypatch)


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
    assert (
        finalized[0].scientific_core.grouping_result.partition_transition.lineage == ()
    )


def test_publication_uses_final_peak_evidence_not_stale_selected_summary(tmp_path):
    from xic_extractor.output.messages import DiagnosticRecord

    config, target, output = production_case(
        tmp_path, sample_names=("SampleA", "SampleB")
    )
    first = output.file_results[0].results[target.label]
    first_answer = first.candidate_ms2_evidence
    assert first_answer is not None and first_answer.nl_match
    second = output.file_results[1].results[target.label]
    second_answer = second.candidate_ms2_evidence
    assert second_answer is not None and second_answer.best_product_evidence is not None
    outside = second.reported_peak_end + 0.05
    second = with_method_universe(
        replace(
            second,
            candidate_ms2_evidence=replace(
                second_answer,
                best_product_evidence=replace(
                    second_answer.best_product_evidence, scan_rt=outside
                ),
            ),
        ),
        target,
    )
    # Model the stale selected summary left by a previous selected peak. The
    # complete retained population is the authority at final selection.
    output.file_results[0].results[target.label] = replace(
        first,
        candidate_ms2_evidence=second.candidate_ms2_evidence,
    )
    output.file_results[1].results[target.label] = replace(
        second,
        candidate_ms2_evidence=first_answer,
    )
    output.diagnostics.append(
        DiagnosticRecord("SampleA", target.label, "NO_MS2", "stale")
    )
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1, "SampleB": 2},
    )
    completed = execute_targeted_production_workflow(request, output)
    published = completed.publication_output(request, output)
    first_final, second_final = [
        f.results[target.label] for f in published.file_results
    ]
    assert first_final.nl_token == "OK"
    assert first_final.selected_cid_nl_supported
    assert second_final.nl_token == "NO_MS2"
    assert not second_final.selected_cid_nl_supported
    assert first_final.targeted_product_projection.legacy_evidence["nl_status"] == "OK"
    assert (
        second_final.targeted_product_projection.legacy_evidence["nl_status"]
        == "NO_MS2"
    )
    nl_records = [d for d in published.diagnostics if d.issue in {"NL_FAIL", "NO_MS2"}]
    assert [d.sample_name for d in nl_records] == ["SampleB"]
    assert "stale" not in nl_records[0].reason


def test_two_samples_reuse_exact_acquired_physical_evaluations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
        sample_peak_scales={"SampleA": 1.0, "SampleB": 2.0},
    )
    acquired = {
        file_result.sample_name: file_result.results[target.label].physical_evaluation
        for file_result in output.file_results
    }
    captured = {}
    capture_peak_hypotheses = targeted_capture_population.capture_peak_hypotheses

    def _capture(*, scope, candidate_id, trace):
        binding = next(
            item for item in scope.capture_bindings if item.candidate_id == candidate_id
        )
        captured[trace.sample_name] = binding.physical_evaluation
        return capture_peak_hypotheses(
            scope=scope,
            candidate_id=candidate_id,
            trace=trace,
        )

    monkeypatch.setattr(
        targeted_capture_population, "capture_peak_hypotheses", _capture
    )
    monkeypatch.setattr(
        capture_owner,
        "prepare_physical_signal",
        lambda *_args, **_kwargs: pytest.fail(
            "retained Targeted capture must not prepare or reintegrate"
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
    assert captured == acquired
    assert acquired["SampleA"] is not None and acquired["SampleB"] is not None
    assert acquired["SampleA"].facts is not None
    assert acquired["SampleB"].facts is not None
    assert acquired["SampleB"].facts.area_counts_seconds == pytest.approx(
        acquired["SampleA"].facts.area_counts_seconds * 2.0
    )


@pytest.mark.parametrize("ms1_only", [False, True])
def test_targeted_grouping_keeps_each_sample_selected_value(
    tmp_path: Path,
    ms1_only: bool,
) -> None:
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
        sample_peak_scales={"SampleA": 1.0, "SampleB": 2.0},
    )
    if ms1_only:
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

    result = finalized[0]
    decision_values = tuple(
        decision.value for decision in result.completed_projection.decisions
    )
    assert decision_values == (
        output.file_results[0].results[target.label].reported_peak_area,
        output.file_results[1].results[target.label].reported_peak_area,
    )
    groups = result.scientific_core.final_population
    assert groups == result.scientific_core.grouping_result.initial_population
    assert {
        member.canonical_sample_identity_id
        for group in groups
        for member in group.member_subjects
    } == {sample.identity.sample_identity_id for sample in result.canonical_samples}


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
    output.file_results[0].results[target.label] = with_method_universe(
        replace(
            original,
            candidate_ms2_evidence=replace(
                evidence,
                best_product_evidence=replace(
                    product,
                    precursor_mz=shifted_precursor,
                    product_mz=shifted_precursor - product.observed_loss_da,
                ),
            ),
        ),
        target,
    )
    second = output.file_results[1].results[target.label]
    second_evidence = second.candidate_ms2_evidence
    assert second_evidence is not None
    second_product = second_evidence.best_product_evidence
    assert second_product is not None
    output.file_results[1].results[target.label] = with_method_universe(
        replace(
            second,
            candidate_ms2_evidence=replace(
                second_evidence,
                best_product_evidence=replace(
                    second_product,
                    precursor_mz=shifted_precursor,
                    product_mz=shifted_precursor - second_product.observed_loss_da,
                ),
            ),
        ),
        target,
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
    config, target, output = production_case(
        tmp_path, sample_names=("SampleA", "SampleB")
    )
    original = output.file_results[0].results[target.label]
    selected = original.selected_hypothesis
    evidence = original.candidate_ms2_evidence
    assert selected is not None and evidence is not None
    product = evidence.best_product_evidence
    assert product is not None
    outside_rt = selected.integration.rt_right_min + 0.01
    assert outside_rt < target.rt_max
    output.file_results[0].results[target.label] = with_method_universe(
        replace(
            original,
            candidate_ms2_evidence=replace(
                evidence,
                best_scan_rt=outside_rt,
                best_product_evidence=replace(product, scan_rt=outside_rt),
            ),
        ),
        target,
    )
    assessments = []
    original_assessment = targeted_cid_nl_assessment.mint_cid_nl_method_assessment_ref

    def record_assessment(**kwargs):
        assessment = original_assessment(**kwargs)
        assessments.append(assessment)
        return assessment

    monkeypatch.setattr(
        targeted_cid_nl_assessment,
        "mint_cid_nl_method_assessment_ref",
        record_assessment,
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
    # SampleA's local MS1 value stays selected beside SampleB's direct support.
    assert len(assessments) == 2
    assert assessments[0].individual_support_refs == ()
    assert assessments[0].content.method_result != "SUPPORT"
    result = finalized[0]
    decision_values = tuple(
        decision.value for decision in result.completed_projection.decisions
    )
    assert decision_values == (
        output.file_results[0].results[target.label].reported_peak_area,
        output.file_results[1].results[target.label].reported_peak_area,
    )
    assert {
        member.canonical_sample_identity_id
        for group in result.scientific_core.final_population
        for member in group.member_subjects
    } == {sample.identity.sample_identity_id for sample in result.canonical_samples}


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
    original_execute = production_workflow.execute_runtime_attempt
    terminals = []

    def capture_terminal(*args, **kwargs):
        terminal = original_execute(*args, **kwargs)
        terminals.append(terminal)
        return terminal

    monkeypatch.setattr(
        production_workflow,
        "execute_runtime_attempt",
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
    receipt = terminals[0]
    assert receipt.supervisor.terminal_outcome.value == "succeeded"
    assert all(
        event.monotonic_offset_ns is None
        for event in receipt.observability_ledger.events
    )
    canonical = json.loads(
        canonical_runtime_observability_ledger_bytes(receipt.observability_ledger)
    )
    assert canonical["schema_version"] == "execution_runtime_observability_ledger"
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
    observed_loss_da = 115.9
    assert target.neutral_loss_da is not None
    loss_error_ppm = (
        abs(observed_loss_da - target.neutral_loss_da)
        / target.neutral_loss_da
        * 1_000_000.0
    )
    output.file_results[0].results[target.label] = with_method_universe(
        replace(
            original,
            candidate_ms2_evidence=replace(
                candidate_evidence,
                nl_match=False,
                nl_status="NL_FAIL",
                strict_nl_scan_count=0,
                best_loss_ppm=loss_error_ppm,
                best_product_evidence=replace(
                    product_evidence,
                    product_mz=product_evidence.precursor_mz - observed_loss_da,
                    observed_loss_da=observed_loss_da,
                    observed_loss_error_ppm=loss_error_ppm,
                ),
            ),
        ),
        target,
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
    assert finalized[0].completed_projection.accepted_cell_count == 2
    assert finalized[0].completed_projection.no_write_count == 0


def test_targeted_relation_uses_quadrature_for_retained_product(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    original = output.file_results[0].results[target.label]
    candidate_evidence = original.candidate_ms2_evidence
    assert candidate_evidence is not None
    product = candidate_evidence.best_product_evidence
    assert product is not None
    observed_loss_da = 116.0058
    assert target.neutral_loss_da is not None
    observed_loss_error_ppm = (
        abs(observed_loss_da - target.neutral_loss_da)
        / target.neutral_loss_da
        * 1_000_000.0
    )
    assert observed_loss_error_ppm > target.nl_ppm_max
    output.file_results[0].results[target.label] = with_method_universe(
        replace(
            original,
            candidate_ms2_evidence=replace(
                candidate_evidence,
                nl_match=False,
                nl_status="NL_FAIL",
                strict_nl_scan_count=0,
                best_loss_ppm=observed_loss_error_ppm,
                best_product_evidence=replace(
                    product,
                    product_mz=product.precursor_mz - observed_loss_da,
                    observed_loss_da=observed_loss_da,
                    observed_loss_error_ppm=observed_loss_error_ppm,
                ),
            ),
        ),
        target,
    )
    assessments = []
    original_assessment = targeted_cid_nl_assessment.mint_cid_nl_method_assessment_ref

    def record_assessment(**kwargs):
        assessment = original_assessment(**kwargs)
        assessments.append(assessment)
        return assessment

    monkeypatch.setattr(
        targeted_cid_nl_assessment,
        "mint_cid_nl_method_assessment_ref",
        record_assessment,
    )
    execute_targeted_production_workflow(
        build_targeted_application_request(
            config,
            [target],
            injection_order={"SampleA": 1},
        ),
        output,
    )
    assert len(assessments) == 1
    assert len(assessments[0].individual_support_refs) == 1
    assert assessments[0].content.method_result == "SUPPORT"


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
    output.file_results[0].results[target.label] = with_method_universe(
        replace(
            original,
            peak_result=find_peak_and_area(
                rt,
                flat_intensity,
                config,
                preferred_rt=1.5,
            ),
            selected_hypothesis=None,
            acquisition_trace=trace,
            physical_evaluation=_physical_evaluation(trace, config, query_rt_min=1.5),
        ),
        target,
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
    assert projection.accepted_cell_count == len(sample_names) - 1
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

    assert finalized[0].completed_projection.accepted_cell_count == 2


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
        rt = np.linspace(1.0, 2.0, 101)
        trace = Trace.from_arrays(
            sample_name=file_result.sample_name,
            mz=target.mz,
            rt=rt,
            intensity=np.zeros_like(rt),
            rt_min=target.rt_min,
            rt_max=target.rt_max,
            ppm_tol=target.ppm_tol,
        )
        file_result.results[target.label] = with_method_universe(
            replace(
                original,
                peak_result=find_peak_and_area(
                    rt,
                    trace.intensity,
                    config,
                    preferred_rt=1.5,
                ),
                selected_hypothesis=None,
                acquisition_trace=trace,
                physical_evaluation=_physical_evaluation(
                    trace,
                    config,
                    query_rt_min=1.5,
                ),
            ),
            target,
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
    output.file_results[0].results[target.label] = with_method_universe(
        replace(
            extraction,
            candidate_ms2_evidence=replace(
                candidate,
                best_product_evidence=replace(product, acquisition_source_id=""),
            ),
        ),
        target,
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
    output.file_results[0].results[target.label] = with_method_universe(
        replace(
            extraction,
            candidate_ms2_evidence=replace(
                candidate,
                best_product_evidence=replace(product, acquisition_source_id=""),
            ),
        ),
        target,
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
    output.file_results[0].results[target.label] = with_method_universe(
        replace(
            first,
            candidate_ms2_evidence=replace(
                first_candidate,
                best_product_evidence=second_candidate.best_product_evidence,
            ),
        ),
        target,
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
        profile = resolve_signal_processing_profile(
            signal_processing_method=config.signal_processing_method,
            signal_processing_window_points=config.signal_processing_window_points,
        )
        physical_evaluation = evaluate_physical_peak(
            prepare_physical_signal(rt, intensity, profile=profile),
            selection_subject_id=target.label,
            query_rt_min=selected.integration.rt_apex_min,
            request_context_rt_start_min=trace.rt_min,
            request_context_rt_end_min=trace.rt_max,
        )
        assert physical_evaluation.integration is not None
        selected = replace(
            selected,
            integration=physical_peak_observation_integration(physical_evaluation),
        )
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
                        physical_evaluation=physical_evaluation,
                        targeted_product_projection=build_targeted_product_projection(
                            TargetedProductContext(role="Analyte"),
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
    for file_result in output.file_results:
        file_result.results[target.label] = with_method_universe(
            file_result.results[target.label], target
        )

    return config, target, output


def with_method_universe(result: ExtractionResult, target: Target) -> ExtractionResult:
    """Complete the one-peak/one-event acquisition fixture before finalization."""
    evaluation = result.physical_evaluation
    evaluations = (
        (evaluation,)
        if evaluation is not None and evaluation.integration is not None
        else ()
    )
    policy = DdaCidNlPeakSupportPolicy(
        precursor_tolerance_da=target.mz * target.ppm_tol / 1e6,
        neutral_loss_tolerance_ppm=target.nl_ppm_max or 20.0,
    )
    candidate_evidence = result.candidate_ms2_evidence
    product = (
        None if candidate_evidence is None else candidate_evidence.best_product_evidence
    )
    products = (
        (product,)
        if product is not None
        and abs(product.precursor_mz - target.mz) <= policy.precursor_tolerance_da
        else ()
    )
    intervals = tuple(
        CidNlCandidateInterval(
            target.mz, item.integration.rt_left_min, item.integration.rt_right_min
        )
        for item in evaluations
    )
    relations = evaluate_cid_nl_method_relations(
        candidate_intervals=intervals,
        event_queries=tuple(
            CidNlEventQuery(
                item.assess_precursor_loss(
                    target.neutral_loss_da or 116.0, policy.neutral_loss_tolerance_ppm
                ),
                item.scan_rt,
                item.scan_rt,
            )
            for item in products
        ),
        policy=policy,
    )
    answers = build_targeted_peak_evidence(
        target=target,
        window=Ms2Window(
            "fixture",
            target.rt_min,
            target.rt_max,
            tuple(
                Ms2ScanEvent(
                    Ms2Scan(
                        p.scan_number or 1,
                        p.scan_rt,
                        p.scan_precursor_mz or p.precursor_mz,
                        np.array([p.product_mz]),
                        np.array([p.product_intensity]),
                        p.product_intensity / p.product_base_ratio,
                        p.precursor_metadata,
                    ),
                    None,
                    p.scan_number or 1,
                )
                for p in products
            ),
        ),
        products=products,
        relations=relations,
        candidate_apex_rts=tuple(e.integration.rt_apex_min for e in evaluations),
    )
    return replace(
        result,
        candidate_physical_evaluations=evaluations,
        cid_nl_products=products,
        cid_nl_evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=target.rt_min,
            query_rt_max=target.rt_max,
            support_policy=policy,
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=len(products),
            reason_code=None,
        ),
        cid_nl_relations=relations,
        cid_nl_candidate_evidence=answers,
        candidate_ms2_evidence=answers[0] if answers else None,
        selected_cid_nl_candidate_index=0 if evaluations else None,
    )


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


def _physical_evaluation(
    trace: Trace,
    config: ExtractionConfig,
    *,
    query_rt_min: float,
):
    profile = resolve_signal_processing_profile(
        signal_processing_method=config.signal_processing_method,
        signal_processing_window_points=config.signal_processing_window_points,
    )
    return evaluate_physical_peak(
        prepare_physical_signal(trace.rt, trace.intensity, profile=profile),
        selection_subject_id="Analyte",
        query_rt_min=query_rt_min,
        request_context_rt_start_min=trace.rt_min,
        request_context_rt_end_min=trace.rt_max,
    )


def _retained_completion_boundary(monkeypatch):
    from types import SimpleNamespace

    from xic_extractor.alignment.completion import (
        TargetWorkflowAdapter,
        TargetWorkflowRequest,
    )

    attempt = object()
    request = object.__new__(TargetWorkflowRequest)
    object.__setattr__(request, "scope", object())
    object.__setattr__(request, "processing_attempt", attempt)
    adapter = object.__new__(TargetWorkflowAdapter)
    object.__setattr__(
        adapter, "capture_inputs", (SimpleNamespace(candidate_id="c", trace=object()),)
    )
    runtime = SimpleNamespace(
        manifest=SimpleNamespace(attempt=attempt),
        history=SimpleNamespace(attempt=attempt),
    )
    monkeypatch.setattr(
        production_workflow, "validate_completion_inputs", lambda *_: object()
    )

    def run():
        return production_workflow._complete_retained_target(
            request,
            adapter,
            runtime=runtime,
            workflow_artifact=object(),
            work_unit_key="retained",
        )

    return run, runtime


@pytest.mark.parametrize("outcome", ["cancelled", "failed"])
def test_retained_runtime_terminal_never_enters_science(monkeypatch, outcome):
    from types import SimpleNamespace

    run, _ = _retained_completion_boundary(monkeypatch)
    monkeypatch.setattr(
        production_workflow,
        "execute_runtime_attempt",
        lambda *_args, **_kwargs: SimpleNamespace(
            supervisor=SimpleNamespace(terminal_outcome=AttemptTerminalOutcome(outcome))
        ),
    )
    monkeypatch.setattr(
        production_workflow,
        "complete_analysis",
        lambda *_args, **_kwargs: pytest.fail("runtime terminal entered science"),
    )
    stage = "None" if outcome == "cancelled" else "WorkflowStage.RUNTIME"
    with pytest.raises(RuntimeError, match=f"stage={stage}; reason=None"):
        run()


def test_retained_runtime_owner_exception_propagates(monkeypatch):
    run, _ = _retained_completion_boundary(monkeypatch)

    class RuntimeOwnerError(RuntimeError):
        pass

    def fail_runtime(*args, **kwargs):
        raise RuntimeOwnerError("owner phase failed")

    monkeypatch.setattr(production_workflow, "execute_runtime_attempt", fail_runtime)
    with pytest.raises(RuntimeOwnerError, match="owner phase failed"):
        run()


def test_retained_capture_failure_keeps_stage_type_and_message(monkeypatch):
    from types import SimpleNamespace

    import xic_extractor.alignment.completion as completion_owner

    run, _ = _retained_completion_boundary(monkeypatch)
    monkeypatch.setattr(
        production_workflow,
        "execute_runtime_attempt",
        lambda *_args, **_kwargs: SimpleNamespace(
            supervisor=SimpleNamespace(
                terminal_outcome=AttemptTerminalOutcome.SUCCEEDED
            )
        ),
    )

    class CaptureFailure(RuntimeError):
        pass

    error = CaptureFailure("capture broke")

    def fail_capture(**kwargs):
        raise error

    monkeypatch.setattr(completion_owner, "capture_peak_hypotheses", fail_capture)
    with pytest.raises(
        RuntimeError, match="stage=WorkflowStage.CAPTURE; reason=capture broke"
    ) as failure:
        run()
    assert failure.value.__cause__ is error
    assert type(failure.value.__cause__) is CaptureFailure


def test_retained_target_rejects_a_crossed_runtime_before_execution(monkeypatch):
    from types import SimpleNamespace

    from xic_extractor.alignment.completion import AlignmentWorkflowContractError

    run, runtime = _retained_completion_boundary(monkeypatch)
    runtime.manifest = SimpleNamespace(attempt=object())
    monkeypatch.setattr(
        production_workflow,
        "execute_runtime_attempt",
        lambda *_args, **_kwargs: pytest.fail("crossed attempt executed"),
    )
    with pytest.raises(
        AlignmentWorkflowContractError,
        match="runtime crossed the exact processing attempt",
    ):
        run()


def test_acquired_target_inputs_complete_without_runtime(tmp_path, monkeypatch):
    config, target, output = production_case(
        tmp_path,
        sample_names=("SampleA", "SampleB"),
        sample_peak_scales={"SampleA": 1.0, "SampleB": 2.0},
    )
    acquired_inputs = []
    complete = production_workflow.complete_analysis

    def retain_inputs(request, adapter, **kwargs):
        acquired_inputs.append((request, adapter))
        return complete(request, adapter, **kwargs)

    monkeypatch.setattr(production_workflow, "complete_analysis", retain_inputs)
    execute_targeted_production_workflow(
        build_targeted_application_request(
            config, [target], injection_order={"SampleA": 1, "SampleB": 2}
        ),
        output,
    )
    monkeypatch.setattr(
        production_workflow,
        "execute_runtime_attempt",
        lambda *_args, **_kwargs: pytest.fail("pure completion entered runtime"),
    )
    request, adapter = acquired_inputs[0]
    # Completed selection/integration facts are snapshots, not a claim that
    # the frozen Trace's ndarray payload is deeply immutable.
    for item in adapter.capture_inputs:
        item.trace.intensity.fill(0.0)
    for value_type in (
        type(request),
        type(adapter),
        CaptureWorkflowArguments,
        capture_owner.SampleLocalCapture,
    ):
        monkeypatch.setattr(
            value_type,
            "__post_init__",
            lambda *_args, **_kwargs: pytest.fail(
                "completion revalidated an unchanged owner-issued input"
            ),
        )
    result = complete(request, adapter)
    expected_values = tuple(
        file_result.results[target.label].reported_peak_area
        for file_result in output.file_results
    )
    assert len(set(expected_values)) == 2
    assert (
        tuple(decision.value for decision in result.completed_projection.decisions)
        == expected_values
    )


@pytest.mark.parametrize("overlap", [False, True])
@pytest.mark.parametrize("backfill_supported", [False, True])
def test_reported_peak_quality_replaces_only_peak_local_reasons(
    overlap: bool,
    backfill_supported: bool,
) -> None:
    from xic_extractor.output.targeted_product_model import TargetedProductProjection

    rt = np.linspace(0.0, 10.0, 1001)
    captured = evaluate_physical_peak(
        prepare_physical_signal(rt, 100 + 1000 * np.exp(-(((rt - 3.0) / 0.2) ** 2))),
        selection_subject_id="replaced",
        query_rt_min=3.0,
    )
    raw = 100 + (
        sum(
            height * np.exp(-0.5 * ((rt - center) / 0.65) ** 2)
            for height, center in ((1000.0, 3.0), (1500.0, 5.0), (2000.0, 7.0))
        )
        if overlap
        else 1500 * np.exp(-(((rt - 5.0) / 0.2) ** 2))
    )
    reported = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="reported",
        query_rt_min=5.0,
    )
    assert reported.state == "available"
    assert reported.requires_overlap_review is overlap
    original = TargetedProductProjection(
        "detected_flagged",
        True,
        "flagged",
        "old peak concern",
        review_reasons=(
            "trace_morphology_review",
            "physical_shoulder_overlap",
            "sample_review",
        ),
        conflict_reasons=("hard_local_quality_conflict", "sample_conflict"),
    )
    updated = production_workflow._projection_with_reported_peak_quality(
        original,
        captured=captured,
        reported=reported,
        backfill_supported=backfill_supported,
    )
    assert updated.review_reasons == (
        ("sample_review", "physical_shoulder_overlap")
        if overlap
        else ("sample_review",)
    )
    assert updated.conflict_reasons == ("sample_conflict",)
    assert original.review_reasons[0] == "trace_morphology_review"
    assert updated.counted_detection == original.counted_detection
    assert reported.integration is not None


def test_same_reported_peak_keeps_its_quality_reasons(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    result = output.file_results[0].results[target.label]
    projection = replace(
        result.targeted_product_projection,
        review_reasons=("trace_morphology_review", "sample_review"),
    )
    updated = production_workflow._projection_with_reported_peak_quality(
        projection,
        captured=result.physical_evaluation,
        reported=result.physical_evaluation,
        backfill_supported=False,
    )
    assert updated.review_reasons == projection.review_reasons
