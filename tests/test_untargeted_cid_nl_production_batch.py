from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.cid_nl_completion as cid_nl_completion_owner
import xic_extractor.alignment.result.application_result as application_result_owner
import xic_extractor.alignment.untargeted_finalized_matrix as finalized_matrix
import xic_extractor.discovery.processing_run_spec_adapter as processing_spec_adapter
import xic_extractor.processing_identity as identity_owner
import xic_extractor.raw_reader as raw_reader_owner
import xic_extractor.untargeted_execution.alignment as alignment_adapter
import xic_extractor.untargeted_execution.partition.request as input_owner
from tests.cid_nl_raw_fixture import (
    _multi_tag_settings,
    _peak_config,
    _settings,
)
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_untargeted_cid_nl_batch_runtime import _nearby_subject_candidates
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _current_candidates,
    _discovery_receipts,
    _FakeRaw,
    _install_runtime_raw,
    _install_runtime_raws,
)
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    complete_dense_partition,
    execute_dense_partition,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.production_decisions import ProductionDecisionSet
from xic_extractor.alignment.result.scientific_alignment import (
    ObservedSearchSubject,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
from xic_extractor.discovery.models import (
    MS1_FEATURE_BACKED_STATES,
    DiscoveryCandidate,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec_with_expectations,
)
from xic_extractor.execution._lifecycle import AttemptTerminalOutcome
from xic_extractor.execution._observability import RuntimeMetricAvailability
from xic_extractor.execution.process_memory import (
    ProcessMemoryPeak,
    ProcessMemorySample,
    ProcessTreeMemorySampler,
)
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.pipeline_outputs import (
    alignment_metadata,
    output_paths,
    write_outputs_atomic,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlPhysicalCandidateFact,
    CidNlPhysicalProof,
    group_cid_nl_physical_subject_units,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.execution import (
    execute_untargeted_plan,
)
from xic_extractor.untargeted_execution.execution_result import (
    CompletedUntargetBatchWorkflowResult,
)
from xic_extractor.untargeted_execution.input_planning import (
    prepare_eager_untargeted_plan,
)
from xic_extractor.untargeted_execution.partition import models as partition_models
from xic_extractor.untargeted_execution.partition import workers as partition_workers
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedPartitionError,
    UntargetedPartitionJob,
    build_sample_jobs,
)
from xic_extractor.untargeted_execution.partition_composition import (
    compose_untargeted_partition,
)


def _physical_subject_facts(candidates, records):
    facts = []
    for candidate in candidates:
        record = records.get(candidate.candidate_id)
        selected = None if record is None else record.evaluation.facts
        proof = None
        if record is not None and selected is not None:
            trace = record.acquired_trace.trace
            proof = CidNlPhysicalProof(
                acquisition_source_id=record.work_item.acquisition_source_id,
                apex_rt_min=selected.apex_rt_min,
                left_rt_min=selected.left_rt_min,
                right_rt_min=selected.right_rt_min,
                xic_mz=record.work_item.xic_mz,
                xic_ppm_tol=record.work_item.xic_ppm_tol,
                rt=trace.rt,
                raw=trace.intensity,
            )
        facts.append(
            CidNlPhysicalCandidateFact(
                candidate_id=candidate.candidate_id,
                physical_mz=candidate.physical_mz,
                configured_neutral_loss_da=candidate.configured_neutral_loss_da,
                proof=proof,
            )
        )
    return tuple(facts)


def _assert_partition_composition_parity(
    batch,
    runtime_result,
    completed,
    *,
    alignment_config,
    known_istd_reference=None,
    subject_worksets=None,
):
    from xic_extractor.alignment.result.application_result import (
        UntargetBatchApplicationResultRequest,
        finalize_alignment_result,
    )
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject_batch,
    )

    parts = []
    subject_worksets = subject_worksets or tuple(
        (subject,) for subject in batch.subject_batch.subjects
    )
    for subjects in subject_worksets:
        subject_ids = {subject.method_subject_id for subject in subjects}
        partition = replace(
            batch,
            subject_batch=mint_untargeted_cid_nl_method_subject_batch(
                subjects=subjects,
                candidate_memberships=tuple(
                    item
                    for item in batch.subject_batch.candidate_memberships
                    if item[1] in subject_ids
                ),
            ),
            jobs=tuple(
                job
                for job in batch.jobs
                if job.method_subject.method_subject_id in subject_ids
            ),
        )
        results = tuple(
            result
            for result in runtime_result.sample_results
            if result.job.method_subject.method_subject_id in subject_ids
        )
        with pytest.raises(RuntimeError, match="sample/subject results"):
            compose_untargeted_partition(
                partition,
                results[:-1],
                alignment_config=alignment_config,
                known_istd_reference=known_istd_reference,
            )
        parts.append(
            compose_untargeted_partition(
                partition,
                results,
                alignment_config=alignment_config,
                known_istd_reference=known_istd_reference,
            )
        )
    original = completed.application_result
    candidate_order = {
        candidate_id: index
        for index, candidate_id in enumerate(original.scope.declared_candidate_ids)
    }
    exclusions = tuple(
        sorted(
            (item for part in parts for item in part.excluded_candidates),
            key=lambda item: candidate_order[item.candidate_id],
        )
    )
    result = finalize_alignment_result(
        UntargetBatchApplicationResultRequest(
            scope=original.scope,
            processing_request=original.processing_request,
            processing_attempt=original.processing_attempt,
            children=tuple(child for part in parts for child in part.children),
            excluded_candidates=exclusions,
        )
    )
    recomposed = replace(completed, application_result=result)
    assert project_completed_untarget_batch_writer_inputs(
        recomposed.application_result
    ).matrix == (
        project_completed_untarget_batch_writer_inputs(
            completed.application_result
        ).matrix
    )
    assert result.excluded_candidates == original.excluded_candidates
    assert result.physical_observation_ids == original.physical_observation_ids
    assert all(
        child.processing_request is original.processing_request
        for child in result.children
    )
    assert all(
        left is right
        for child in result.children
        for left, right in zip(
            child.canonical_samples, original.canonical_samples, strict=True
        )
    )
    assert set(item for part in parts for item in part.method_assessments) == (
        set(completed.method_assessments)
    )
    assert tuple(
        group.group_hypothesis_id for part in parts for group in part.initial_groups
    ) == tuple(group.group_hypothesis_id for group in completed.initial_groups)
    assert tuple(edge for part in parts for edge in part.initial_edges) == (
        completed.initial_edges
    )
    assert {
        binding.candidate_id for part in parts for binding in part.capture_bindings
    } == {binding.candidate_id for binding in original.scope.capture_bindings}


def _assert_jobs_match_direct_construction(batch) -> None:

    context = batch.context
    memberships = dict(batch.subject_batch.candidate_memberships)
    candidate_keys = {
        (sample.identity.sample_identity_id, memberships[candidate.candidate_id])
        for sample in context.samples
        for candidate in context.candidates_by_sample[sample.metadata.raw_stem]
        if candidate.candidate_id in memberships
    }
    expected = tuple(
        UntargetedPartitionJob(
            processing_owners=context.processing_owners,
            sample=sample,
            method_subject=subject,
            candidates=context.candidates_by_sample[sample.metadata.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[
                sample.metadata.raw_stem
            ],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
            dll_dir=batch.jobs[0].dll_dir,
            work_unit_key=(
                "untargeted-cid-nl:"
                f"{sample.metadata.raw_stem}:{subject.method_subject_id}"
            ),
            signal_processing_profile=context.signal_processing_profile,
            subject_partition=batch.jobs[0].subject_partition,
            acquisition_source_expectation=(
                context.acquisition_sources_by_sample.get(sample.metadata.raw_stem)
                if batch.jobs[0].subject_partition is not None
                else None
            ),
            physical_records=tuple(
                record
                for _candidate_id, record in sorted(
                    context.physical_records_by_candidate.items()
                )
                if record.work_item.sample_stem == sample.metadata.raw_stem
            ),
            acquisition_cache_dir=batch.jobs[0].acquisition_cache_dir,
        )
        for sample in context.samples
        for subject in batch.subject_batch.subjects
        if not batch.sample_job_templates
        or (sample.identity.sample_identity_id, subject.method_subject_id)
        in candidate_keys
    )
    assert batch.jobs == expected
    assert tuple(job._request_subjects for job in batch.jobs) == tuple(
        job._request_subjects for job in expected
    )
    memberships = dict(batch.subject_batch.candidate_memberships)
    for job, direct_job in zip(batch.jobs, expected, strict=True):
        records = tuple(
            record
            for record in job.physical_records
            if memberships.get(record.candidate_id)
            == job.method_subject.method_subject_id
        )
        selected = partition_models._request_subject_physical_records(job)
        assert selected == records
        assert all(left is right for left, right in zip(selected, records, strict=True))
        assert partition_models._request_subject_physical_records(direct_job) == records


def _assert_single_unsupported_exclusion(
    completed, runtime_result, batch, tmp_path: Path, *, populated: bool
) -> None:
    from copy import copy

    from xic_extractor.alignment.result.application_result import (
        ApplicationResultContractError,
        UntargetBatchApplicationResultRequest,
    )
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        is_unsearched_cid_nl_discovery_assessment,
        mint_cid_nl_method_assessment_ref,
    )
    from xic_extractor.processing_identity import (
        build_processing_request_identity,
        issue_processing_attempt,
    )

    result = completed.application_result
    exclusions = result.excluded_candidates
    expected_candidate_ids = {
        candidate_id
        for candidate_id, subject_id in batch.subject_batch.candidate_memberships
        if subject_id in {item.method_subject_id for item in exclusions}
    }
    assert len(exclusions) == len(expected_candidate_ids)
    subject_ids = {item.method_subject_id for item in exclusions}
    assert len(subject_ids) == 1
    retained = tuple(
        sorted(
            (
                item.effective_assessment
                for item in runtime_result.sample_results
                if item.job.method_subject.method_subject_id in subject_ids
            ),
            key=lambda ref: ref.content.canonical_sample_identity_id,
        )
    )
    assert len(retained) == 2
    assert all(
        item.reason_code == "cid_nl_requirement_not_supported"
        and not item.composite_assessments
        and item.method_assessments == retained
        and all(
            left is right
            for left, right in zip(item.method_assessments, retained, strict=True)
        )
        for item in exclusions
    )
    assert {item.candidate_id for item in exclusions} == expected_candidate_ids
    assert len(result.children) == int(populated)
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    destination = tmp_path / "single-unsupported-matrix.tsv"
    write_alignment_matrix_tsv(
        destination, writer.matrix, production_decisions=writer.production_decisions
    )
    with destination.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert {"Support", "Hold"}.issubset(reader.fieldnames)
        rows = list(reader)
    assert len(rows) == int(populated)
    if populated:
        assert all(float(rows[0][stem]) > 0 for stem in ("Support", "Hold"))
    else:
        assert not writer.matrix.clusters and not writer.matrix.cells

    def validate(exclusion):
        return UntargetBatchApplicationResultRequest(
            scope=result.scope,
            processing_request=result.processing_request,
            processing_attempt=result.processing_attempt,
            children=result.children,
            excluded_candidates=(exclusion, *exclusions[1:]),
        )

    first = exclusions[0]
    own_assessment = next(
        assessment
        for assessment in retained
        if assessment.content.canonical_sample_identity_id
        == first.canonical_sample_identity_id
    )
    unsearched = next(
        (
            assessment
            for assessment in retained
            if is_unsearched_cid_nl_discovery_assessment(assessment)
        ),
        None,
    )
    if unsearched is not None:
        assert any(
            assessment.content.technical_state == "evaluated"
            and assessment.content.method_result in {"HOLD", "REJECT"}
            for assessment in retained
        )
        with pytest.raises(ApplicationResultContractError):
            replace(
                first,
                canonical_sample_identity_id=(
                    unsearched.content.canonical_sample_identity_id
                ),
            )
        with pytest.raises(ApplicationResultContractError):
            replace(
                first,
                discovery_candidate_state="review_only_orphan_nl",
                method_assessments=(unsearched,),
            )
    # A sample with no Discovery input for this subject needs no placeholder
    # assessment. Sparse execution retains all input-bearing evidence only.
    sparse_proof = tuple(
        assessment
        for assessment in retained
        if not is_unsearched_cid_nl_discovery_assessment(assessment)
    )
    validate(replace(first, method_assessments=sparse_proof))
    incomplete_proofs = [(own_assessment, own_assessment)]
    if len(sparse_proof) > 1:
        incomplete_proofs.append((own_assessment,))
    for proof in incomplete_proofs:
        with pytest.raises(
            ApplicationResultContractError, match="input sample coverage"
        ):
            validate(replace(first, method_assessments=proof))
    for substitution in (
        {"method_assessments": ()},
        {"composite_assessments": retained},
        {"method_assessments": (), "composite_assessments": retained},
        {"reason_code": "composite_requirement_not_supported"},
        {"reason_code": "discovery_candidate_not_search_eligible"},
    ):
        with pytest.raises(ApplicationResultContractError):
            replace(first, **substitution)
    crossed = copy(first)
    object.__setattr__(crossed, "composite_assessments", retained)
    with pytest.raises(ApplicationResultContractError):
        validate(crossed)
    if populated:
        supported = next(
            item.effective_assessment
            for item in runtime_result.sample_results
            if item.effective_assessment.content.method_result == "SUPPORT"
        )
        with pytest.raises(ApplicationResultContractError):
            replace(first, method_assessments=(supported, retained[1]))
        with pytest.raises(ApplicationResultContractError):
            replace(
                first,
                method_subject_id=supported.content.method_subject.method_subject_id,
                method_assessments=(supported,),
            )

    sample = next(
        sample
        for sample in batch.context.samples
        if sample.identity.sample_identity_id
        == retained[0].content.canonical_sample_identity_id
    )
    receipt = replace(retained[0].content.evaluation_receipt, scanned_event_count=0)
    for state in ("failed", "not_evaluated", "unsupported"):
        technical = mint_cid_nl_method_assessment_ref(
            processing_owners=batch.context.processing_owners,
            sample=sample,
            method_subject=retained[0].content.method_subject,
            detector_invocation_id=f"test:{state}",
            evaluation_receipt=replace(
                receipt,
                search_completed=False,
                reason_code=f"test_{state}",
                acquisition_supported=state != "unsupported",
            ),
            technical_state=state,
        )
        with pytest.raises(ApplicationResultContractError):
            replace(first, method_assessments=(technical, retained[1]))
    retry_owners = replace(
        batch.context.processing_owners,
        attempt=issue_processing_attempt(
            result.processing_request, retry_of=result.processing_attempt
        ),
    )
    retry_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=retry_owners,
        sample=sample,
        method_subject=retained[0].content.method_subject,
        detector_invocation_id="test:retry",
        evaluation_receipt=receipt,
    )
    with pytest.raises(ApplicationResultContractError, match="owner binding"):
        validate(replace(first, method_assessments=(retry_assessment, retained[1])))
    import json

    request = result.processing_request
    other_request = build_processing_request_identity(
        acquisition_sources=request.acquisition_sources,
        acquisition_bindings=request.acquisition_bindings,
        scientific_input_schema_version=request.scientific_input_schema_version,
        processing_algorithm_version=request.processing_algorithm_version + ":other",
        processing_contract_version=request.processing_contract_version,
        scientific_inputs=json.loads(request.normalized_scientific_inputs_json),
    )
    other_owners = replace(
        batch.context.processing_owners,
        request=other_request,
        attempt=issue_processing_attempt(other_request),
    )
    foreign_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=other_owners,
        sample=sample,
        method_subject=retained[0].content.method_subject,
        detector_invocation_id="test:foreign-request",
        evaluation_receipt=receipt,
    )
    with pytest.raises(ApplicationResultContractError, match="owner binding"):
        validate(replace(first, method_assessments=(foreign_assessment, retained[1])))


def _assert_composite_no_input_exclusion_coverage(
    completed, runtime_result, batch
) -> None:
    from xic_extractor.alignment.result.application_result import (
        ApplicationResultContractError,
        UntargetBatchApplicationResultRequest,
    )
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        is_unsearched_cid_nl_discovery_assessment,
        mint_cid_nl_method_assessment_ref,
    )

    result = completed.application_result
    exclusions = result.excluded_candidates
    assert exclusions
    assert all(
        exclusion.reason_code == "composite_requirement_not_supported"
        and exclusion.composite_assessments
        and not exclusion.method_assessments
        for exclusion in exclusions
    )
    first = exclusions[0]
    result_by_sample_id = {
        item.effective_assessment.content.canonical_sample_identity_id: item
        for item in runtime_result.sample_results
        if item.job.method_subject.method_subject_id == first.method_subject_id
    }
    no_input_by_sample_id = {}
    for sample_id, sample_result in result_by_sample_id.items():
        assessment = sample_result.effective_assessment
        no_input_by_sample_id[sample_id] = mint_cid_nl_method_assessment_ref(
            processing_owners=batch.context.processing_owners,
            sample=sample_result.job.sample,
            method_subject=assessment.content.method_subject,
            detector_invocation_id=f"test:composite-no-input:{sample_id}",
            evaluation_receipt=replace(
                assessment.content.evaluation_receipt,
                acquisition_supported=True,
                search_completed=False,
                scanned_event_count=0,
                reason_code="no_search_eligible_discovery_input",
            ),
            technical_state="not_evaluated",
        )
    assert all(
        is_unsearched_cid_nl_discovery_assessment(assessment)
        for assessment in no_input_by_sample_id.values()
    )

    peer_sample_id = next(
        sample_id
        for sample_id in result_by_sample_id
        if sample_id != first.canonical_sample_identity_id
    )
    mixed_proof = tuple(
        no_input_by_sample_id[peer_sample_id]
        if assessment.content.canonical_sample_identity_id == peer_sample_id
        else assessment
        for assessment in first.composite_assessments
    )
    mixed = replace(first, composite_assessments=mixed_proof)
    UntargetBatchApplicationResultRequest(
        scope=result.scope,
        processing_request=result.processing_request,
        processing_attempt=result.processing_attempt,
        children=result.children,
        excluded_candidates=(mixed, *exclusions[1:]),
    )

    own_no_input_proof = tuple(
        no_input_by_sample_id[first.canonical_sample_identity_id]
        if assessment.content.canonical_sample_identity_id
        == first.canonical_sample_identity_id
        else assessment
        for assessment in first.composite_assessments
    )
    with pytest.raises(ApplicationResultContractError):
        replace(first, composite_assessments=own_no_input_proof)
    with pytest.raises(ApplicationResultContractError):
        replace(
            first,
            composite_assessments=tuple(
                no_input_by_sample_id[assessment.content.canonical_sample_identity_id]
                for assessment in first.composite_assessments
            ),
        )


@pytest.mark.parametrize(
    ("peak", "expected_availability", "expected_value", "expected_reason"),
    (
        (
            ProcessMemoryPeak(
                rss_bytes=123456,
                private_bytes=654321,
                availability="measured",
            ),
            RuntimeMetricAvailability.MEASURED,
            123456,
            None,
        ),
        (
            ProcessMemoryPeak(
                availability="unavailable",
                reason="process memory sampler unavailable",
            ),
            RuntimeMetricAvailability.UNSUPPORTED,
            None,
            "process memory sampler unavailable",
        ),
    ),
)
def test_alignment_runtime_telemetry_preserves_process_memory_peak(
    monkeypatch: pytest.MonkeyPatch,
    peak: ProcessMemoryPeak,
    expected_availability: RuntimeMetricAvailability,
    expected_value: int | None,
    expected_reason: str | None,
) -> None:
    monkeypatch.setattr(
        alignment_adapter,
        "runtime_started_worker_keys",
        lambda _coordinator: (),
    )

    samples = alignment_adapter._coarse_runtime_telemetry(object(), peak)

    assert samples.event_monotonic_offsets_ns is None
    assert samples.artifact_io is None
    assert samples.unavailable_reason == (
        "not_collected_by_alignment_foreground_runner"
    )
    assert samples.process_tree_peak_rss.availability is expected_availability
    assert samples.process_tree_peak_rss.value == expected_value
    assert samples.process_tree_peak_rss.reason == expected_reason


def test_writer_projection_rejects_duplicate_physical_presentation_fact() -> None:
    facts = {"physical-1": (500.0, 8.0)}

    with pytest.raises(ValueError, match="duplicate physical identity"):
        finalized_matrix._add_presentation_fact(
            facts,
            physical_id="physical-1",
            mz=500.0,
            rt=8.0,
        )


def _expected_retained_curve(
    *,
    kind,
    sample_id,
    candidate_id,
    trace,
    evaluation,
    request_id,
    attempt_id,
    observation=None,
    method_subject_id=None,
):
    # Acceptance oracle starts with the original owner values, never a reader
    # result or a re-executed signal-processing calculation.
    signal = evaluation.retained_prepared_signal if evaluation is not None else None
    provenance = {
        "processing_request_id": request_id,
        "processing_attempt_id": attempt_id,
    }
    if method_subject_id is not None:
        provenance["method_subject_id"] = method_subject_id
    if evaluation is not None:
        provenance.update(
            trace_content_id=evaluation.question.trace_content_id,
            prepared_signal_id=evaluation.prepared.prepared_signal_id,
            profile_id=evaluation.prepared.profile_id,
            physical_state=evaluation.state,
            physical_reason=evaluation.reason,
        )
    if signal is not None:
        provenance.update(
            profile_id=signal.profile.profile_id,
            profile_version=signal.profile.schema_version,
            profile=asdict(signal.profile),
        )
    provenance["acquisition_source_id"] = trace.acquisition_source_id
    if observation is not None:
        generation = observation.generation_provenance
        for name in (
            "full_trace_sha256",
            "selection_trace_sha256",
            "selected_trace_indices",
            "resolver_mode",
            "proposal_sources",
        ):
            provenance[name] = getattr(generation, name)
        provenance["observation_artifact_id"] = (
            observation.artifact.evidence_artifact_id
        )
    integration = (
        observation.content.integration
        if observation is not None
        else evaluation.integration
    )
    value = {
        "schema_version": "curve_payload",
        "kind": kind,
        "sample_id": sample_id,
        "candidate_id": candidate_id,
        "peak_observation_content_id": observation.content.peak_observation_content_id
        if observation is not None
        else None,
        "trace_coordinate": {
            "mz": trace.mz,
            "rt_min": trace.rt_min,
            "rt_max": trace.rt_max,
            "ppm_tol": trace.ppm_tol,
        },
        "curves": {
            "rt": list(trace.rt),
            "raw": list(trace.intensity),
            "baseline": list(signal.baseline) if signal is not None else None,
            "smoothed": list(signal.smoothed) if signal is not None else None,
            "positive": list(signal.positive_signal) if signal is not None else None,
        },
        "integration": asdict(integration) if integration is not None else None,
        "provenance": provenance,
    }
    return json.loads(json.dumps(value))


def _expected_capture_curve(evidence):
    return _expected_retained_curve(
        kind="backfill_observation"
        if evidence.capture_stage == "method_backfill"
        else evidence.capture_stage,
        sample_id=evidence.canonical_sample_identity_id,
        candidate_id=evidence.candidate_id,
        trace=evidence.trace,
        evaluation=evidence.physical_evaluation,
        request_id=evidence.processing_request_id,
        attempt_id=evidence.processing_attempt_id,
        observation=evidence.observation_ref,
        method_subject_id=evidence.method_subject_id,
    )


def _assert_fresh_process_curve_parity(path, expected, expected_links=None):
    expected_links = expected_links or {}
    script = """
import json, sys
from xic_extractor.peak_detection import evidence_facts

def forbidden(*args, **kwargs):
    raise AssertionError('readonly evidence must not execute scientific preparation')
for name in (
    'prepare_physical_signal', 'evaluate_physical_peak',
    'rehydrate_physical_peak_evaluation',
):
    setattr(evidence_facts, name, forbidden)
from xic_extractor.diagnostics.alignment_explanation import (
    read_cid_nl_curves,
    read_cid_nl_explanation,
)
curves = read_cid_nl_curves(sys.argv[1])
links = {
    candidate: [
        record['payload_id'] for record in read_cid_nl_explanation(
            sys.argv[1], candidate_id=candidate
        )['records'].get('capture_evidence', [])
    ]
    for candidate in json.loads(sys.argv[2])
}
print(json.dumps({'curves': curves, 'links': links}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), json.dumps(list(expected_links))],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    actual = json.loads(result.stdout)

    def normalize(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    by_payload_id = {item.pop("payload_id"): item for item in actual["curves"]}
    assert len(by_payload_id) == len(actual["curves"])
    assert {normalize(item) for item in by_payload_id.values()} == {
        normalize(item) for item in expected
    }
    for candidate_id, source_values in expected_links.items():
        linked = {
            normalize(by_payload_id[identity])
            for identity in actual["links"][candidate_id]
        }
        assert {normalize(item) for item in source_values} <= linked


@pytest.mark.parametrize(
    (
        "group_sizes",
        "method_mode",
        "nearby",
        "family_context",
        "family_precursor_delta",
    ),
    (
        ((2,), "single", False, False, 0.0),
        ((1, 1), "single", False, False, 0.0),
        ((2, 2), "single", False, False, 0.0),
        pytest.param((2,), "single", True, False, 0.0, id="nearby-single"),
        pytest.param((2,), "union", True, False, 0.0, id="nearby-or-different-tags"),
        pytest.param(
            (2,), "intersection", True, False, 0.0, id="nearby-and-incomplete"
        ),
        pytest.param(
            (2,), "single", False, True, 0.0, id="overlapping-family-one-peak"
        ),
        pytest.param(
            (4,),
            "single",
            False,
            True,
            0.0065,
            id="overlapping-family-across-fixed-subject-boundary",
        ),
    ),
)
@pytest.mark.parametrize("indexed_input", (False, True), ids=("eager", "indexed"))
def test_profile_replay_closes_persisted_discovery_through_runtime_and_writer(
    tmp_path: Path,
    monkeypatch,
    group_sizes: tuple[int, ...],
    method_mode,
    nearby,
    family_context,
    family_precursor_delta,
    indexed_input,
) -> None:
    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.discovery.csv_reader import (
        read_owner_bound_discovery_candidates_csv,
    )
    from xic_extractor.discovery.grouping import group_discovery_seeds
    from xic_extractor.discovery.ms1_backfill import (
        _backfill_ms1_candidates_with_acquisition,
    )
    from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
    from xic_extractor.discovery.physical_fact_persistence import (
        write_discovery_physical_fact_sidecar,
    )
    from xic_extractor.discovery.processing_run_spec_adapter import (
        compile_discovery_processing_run_spec,
    )
    from xic_extractor.processing_identity import IdentityContractError

    separate_groups = len(group_sizes) == 2
    sample_stems = tuple(f"S{index + 1}" for index in range(sum(group_sizes)))
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in sample_stems)
    for path in paths:
        path.write_bytes(path.stem.encode())
    # Observed RAW coordinates: acquisition bounds do not land on scan RTs.
    center_rt = 13.263299286133334
    precursor_mz = 307.1023254394531
    settings = replace(
        _settings() if method_mode == "single" else _multi_tag_settings(),
        neutral_loss_profile=None,
        tag_combine_mode=method_mode,
        rt_min=13.1,
        rt_max=17.4 if separate_groups or family_precursor_delta else 13.4,
    )
    peak_config = _peak_config(tmp_path)
    profile = SignalProcessingProfile(window_points=15)
    if family_context:
        settings = replace(
            settings,
            neutral_loss_profile=None,
            seed_rt_gap_min=0.01,
            product_mz_tolerance_ppm=1.0,
            rt_min=12.8,
        )
    spec = compile_discovery_processing_run_spec(
        paths, discovery_settings=settings, signal_processing_profile=profile
    )

    source_raws = {}
    opened_raws = _install_runtime_raws(
        monkeypatch, sources_by_path=source_raws, tmp_path=tmp_path
    )
    receipts = {}
    csvs = {}
    candidates = {}
    physical_facts = {}
    source_records = {}
    family_evaluations = {}
    for path, source in zip(paths, spec.acquisition_inputs, strict=True):
        import numpy as np

        sample_offset = (
            4.0
            if (
                (separate_groups and sample_stems.index(path.stem) >= group_sizes[0])
                or (family_precursor_delta and sample_stems.index(path.stem) >= 1)
            )
            else 0.0
        )
        sample_center_rt = center_rt + sample_offset
        sample_index = sample_stems.index(path.stem)
        sample_mz = precursor_mz + (
            (0.0, 0.0058, family_precursor_delta, 0.03)[sample_index]
            if family_precursor_delta
            else sample_index * 0.00030517578125
            if nearby
            else 0.0
        )
        loss_index = sample_index if method_mode != "single" else 0
        raw = _FakeRaw(
            (8.0,),
            scan_precursor_mz=sample_mz,
            neutral_loss_das=(
                settings.neutral_loss_profiles[loss_index].neutral_loss_da,
            ),
            acquisition_source=source.acquisition_source,
        )
        raw.rt = np.linspace(12.517828061866668, 13.92044642215, 521) + sample_offset
        if family_precursor_delta:
            sample_center_rt = round(float(raw.rt[276]), 4)
            raw.rt[276] = sample_center_rt
        else:
            raw.rt[int(np.argmin(np.abs(raw.rt - sample_center_rt)))] = sample_center_rt
        raw.intensity = (
            1000.0
            * np.exp(
                -0.5
                * ((raw.rt - sample_center_rt) / (0.15 if family_context else 0.035))
                ** 2
            )
            + 250.0
        ) * (1.0 + sample_index * 0.4 if nearby else 1.0)
        raw.scans = tuple(
            replace(
                scan,
                rt=sample_center_rt,
                scan_number=int(np.argmin(np.abs(raw.rt - sample_center_rt))) + 1,
            )
            for scan in raw.scans
        )
        if family_context:
            query_rts = (
                (sample_center_rt,)
                if family_precursor_delta
                else (sample_center_rt - 0.25, sample_center_rt + 0.05)
            )
            scan_indexes = (0,) if family_precursor_delta else (0, 1)
            if family_precursor_delta:
                for query_rt in query_rts:
                    raw.rt[int(np.argmin(np.abs(raw.rt - query_rt)))] = query_rt
            raw.scans = tuple(
                replace(
                    raw.scans[0],
                    rt=query_rts[index],
                    scan_number=(int(np.argmin(np.abs(raw.rt - query_rts[index]))) + 1),
                    # Separate chemical hypotheses exercise two retained wrappers.
                    precursor_mz=sample_mz,
                    masses=(
                        raw.scans[0].masses
                        if family_precursor_delta
                        else raw.scans[0].masses + index * 0.0003
                    ),
                )
                for index in scan_indexes
            )
        source_raws[path] = raw
        with partition_workers.open_raw(
            path, tmp_path, expected_acquisition_source=source.acquisition_source
        ) as discovery_raw:
            seeds = collect_strict_nl_seeds(
                discovery_raw, raw_file=path, settings=settings
            )
            discovery = _backfill_ms1_candidates_with_acquisition(
                discovery_raw,
                group_discovery_seeds(seeds, settings=settings),
                acquisition_source=source.acquisition_source,
                run_spec_id=spec.run_spec_id,
                settings=settings,
                peak_config=peak_config,
                signal_processing_profile=profile,
            )
        folder = tmp_path / path.stem
        folder.mkdir()
        persisted_candidates = discovery.candidates
        if indexed_input:
            from xic_extractor.discovery.models import build_discovery_candidate_id

            # Audit-only observations deliberately precede the real feature in
            # m/z. They must not choose its query center or demand a curve load.
            audit = replace(
                persisted_candidates[0],
                candidate_id=build_discovery_candidate_id(
                    sample_stem=path.stem,
                    best_ms2_scan_id=persisted_candidates[0].best_ms2_scan_id,
                    precursor_mz=persisted_candidates[0].precursor_mz - 0.001,
                    product_mz=persisted_candidates[0].product_mz,
                ),
                precursor_mz=persisted_candidates[0].precursor_mz - 0.001,
                precursor_mz_basis="product_plus_neutral_loss",
                ms1_peak_found=False,
                ms1_feature_row_id="",
                discovery_candidate_state="review_only_orphan_nl",
            )
            persisted_candidates = (*persisted_candidates, audit)
        receipts.update(
            _discovery_receipts(folder, (path,), {path.stem: persisted_candidates})
        )
        csvs[path.stem] = folder / f"{path.stem}.owner-candidates.csv"
        write_discovery_physical_fact_sidecar(
            folder / "discovery_physical_facts.json",
            run_spec=spec,
            acquisition_input=source,
            work_manifest=discovery.physical_work_manifest,
            records=discovery.physical_evaluations,
        )
        candidates[path.stem] = tuple(
            candidate
            for candidate in read_owner_bound_discovery_candidates_csv(
                csvs[path.stem], receipt=receipts[path.stem]
            )
            if candidate.ms1_peak_found
        )
        source_records.update(
            (record.candidate_id, record) for record in discovery.physical_evaluations
        )
        if family_context:
            expected_candidate_count = 1 if family_precursor_delta else 2
            assert len(candidates[path.stem]) == expected_candidate_count
            assert all(candidate.ms1_peak_found for candidate in candidates[path.stem])
            if not family_precursor_delta:
                left, right = discovery.physical_evaluations
                assert left.work_item.xic_rt_max > right.work_item.xic_rt_min
            rt_min = min(
                record.work_item.xic_rt_min for record in discovery.physical_evaluations
            )
            rt_max = max(
                record.work_item.xic_rt_max for record in discovery.physical_evaluations
            )
            # The vendor boundary includes the end scan returned for the request,
            # which may lie just beyond the requested RT upper bound.
            vendor = opened_raws[path][0]
            start = vendor.ScanNumberFromRetentionTime(rt_min) - 1
            end = vendor.ScanNumberFromRetentionTime(rt_max)
            prepared = prepare_physical_signal(
                raw.rt[start:end], raw.intensity[start:end], profile=profile
            )
            for record in discovery.physical_evaluations:
                family_evaluations[record.candidate_id] = evaluate_physical_peak(
                    prepared,
                    selection_subject_id=record.evaluation.question.selection_subject_id,
                    query_rt_min=record.evaluation.question.query_rt_min,
                    request_context_rt_start_min=rt_min,
                    request_context_rt_end_min=rt_max,
                )
            assert (
                len(
                    {
                        family_evaluations[
                            record.candidate_id
                        ].facts.physical_segment_id
                        for record in discovery.physical_evaluations
                        if family_evaluations[record.candidate_id].facts is not None
                    }
                )
                == 1
            )
            assert all(
                family_evaluations[record.candidate_id].facts is not None
                for record in discovery.physical_evaluations
            )
            continue
        (record,) = discovery.physical_evaluations
        assert record.evaluation.facts is not None
        assert (
            record.evaluation.question.context_rt_start_min
            != record.work_item.xic_rt_min
        )
        assert (
            record.evaluation.question.context_rt_end_min != record.work_item.xic_rt_max
        )
        physical_facts[path.stem] = record.evaluation.facts
    if family_precursor_delta:
        proof_units = group_cid_nl_physical_subject_units(
            _physical_subject_facts(
                tuple(
                    candidate
                    for sample_candidates in candidates.values()
                    for candidate in sample_candidates
                ),
                source_records,
            ),
            composition=None,
            precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        )
        proof_debug = tuple(
            (
                candidate.candidate_id,
                candidate.configured_neutral_loss_da,
                source_records[candidate.candidate_id].work_item.acquisition_source_id,
                source_records[candidate.candidate_id].evaluation.facts.apex_rt_min,
                source_records[candidate.candidate_id].evaluation.facts.left_rt_min,
                source_records[candidate.candidate_id].evaluation.facts.right_rt_min,
            )
            for sample_candidates in candidates.values()
            for candidate in sample_candidates
        )
        assert all(
            len(
                {
                    source_records[candidate.candidate_id].evaluation.facts.apex_rt_min
                    for candidate in sample_candidates
                }
            )
            == 1
            for sample_candidates in candidates.values()
        )
        assert sorted(len(unit_candidates) for _, unit_candidates in proof_units) == [
            1,
            1,
            1,
            1,
        ], proof_debug
    discovery_batch = DiscoveryBatchInput(
        sample_order=sample_stems,
        candidate_csvs=csvs,
        raw_files={path.stem: path for path in paths},
        review_csvs={path.stem: None for path in paths},
        candidate_receipts=receipts,
    )
    captured_batches = []
    captured_peaks = []
    from xic_extractor.peak_detection import capture as capture_owner

    capture_peak = capture_owner.capture_peak_hypotheses
    capture_reacquired = capture_owner.capture_reacquired_physical_peak

    def retain_capture(**kwargs):
        capture = capture_peak(**kwargs)
        captured_peaks.append(capture)
        return capture

    monkeypatch.setattr(capture_owner, "capture_peak_hypotheses", retain_capture)

    def retain_reacquired_capture(**kwargs):
        capture = capture_reacquired(**kwargs)
        captured_peaks.append(capture)
        return capture

    monkeypatch.setattr(
        capture_owner, "capture_reacquired_physical_peak", retain_reacquired_capture
    )
    from xic_extractor.untargeted_execution import execution as bounded_owner

    materialize_batch = bounded_owner.materialize_untargeted_partition

    def retain_batch(*args, **kwargs):
        batch = materialize_batch(*args, **kwargs)
        captured_batches.append(batch)
        return batch

    monkeypatch.setattr(
        bounded_owner, "materialize_untargeted_partition", retain_batch
    )
    from xic_extractor.alignment import (
        cid_nl_predecessor_cells as predecessor_cell_owner,
    )
    from xic_extractor.alignment import completion as workflow_owner
    from xic_extractor.alignment import grouping_bootstrap as bootstrap_owner
    from xic_extractor.alignment.projection.untarget import post_cell as post_cell_owner
    from xic_extractor.alignment.result import application_result as result_owner
    from xic_extractor.peak_detection import observations as observation_owner
    from xic_extractor.untargeted_execution import partition_composition as production

    mint_captured = production.mint_captured_peak_observation
    mint_rechecks = []
    mint_args = []

    def record_captured_mint(capture, *args, **kwargs):
        counts = {"prepare": 0, "evaluate": 0}
        real_capture_prepare = capture_owner.prepare_physical_signal
        real_capture_evaluate = capture_owner.evaluate_physical_peak
        real_observation_prepare = observation_owner.prepare_physical_signal
        real_observation_evaluate = observation_owner.evaluate_physical_peak

        def count_capture_prepare(*call_args, **call_kwargs):
            counts["prepare"] += 1
            return real_capture_prepare(*call_args, **call_kwargs)

        def count_capture_evaluate(*call_args, **call_kwargs):
            counts["evaluate"] += 1
            return real_capture_evaluate(*call_args, **call_kwargs)

        def count_observation_prepare(*call_args, **call_kwargs):
            counts["prepare"] += 1
            return real_observation_prepare(*call_args, **call_kwargs)

        def count_observation_evaluate(*call_args, **call_kwargs):
            counts["evaluate"] += 1
            return real_observation_evaluate(*call_args, **call_kwargs)

        with monkeypatch.context() as mint_patch:
            mint_patch.setattr(
                capture_owner, "prepare_physical_signal", count_capture_prepare
            )
            mint_patch.setattr(
                capture_owner, "evaluate_physical_peak", count_capture_evaluate
            )
            mint_patch.setattr(
                observation_owner,
                "prepare_physical_signal",
                count_observation_prepare,
            )
            mint_patch.setattr(
                observation_owner,
                "evaluate_physical_peak",
                count_observation_evaluate,
            )
            result = mint_captured(capture, *args, **kwargs)
        mint_args.append((capture, args, kwargs))
        mint_rechecks.append(counts)
        return result

    monkeypatch.setattr(
        production, "mint_captured_peak_observation", record_captured_mint
    )

    from xic_extractor.alignment.cell_decision import owner as decision_owner
    from xic_extractor.alignment.projection.untarget import pre_cell as pre_cell_owner

    projection_builds = []
    decision_rechecks = []
    build_subjects = pre_cell_owner._build_cell_subjects
    validate_decision = decision_owner.validate_production_cell_decision

    def record_subject_build(**kwargs):
        result = build_subjects(**kwargs)
        projection_builds.append(result)
        return result

    def record_decision_recheck(value):
        decision_rechecks.append(value)
        return validate_decision(value)

    monkeypatch.setattr(pre_cell_owner, "_build_cell_subjects", record_subject_build)
    monkeypatch.setattr(
        decision_owner, "validate_production_cell_decision", record_decision_recheck
    )

    completion_checks = {"scientific": [], "post_cell": []}
    scientific_check = (
        result_owner._validate_finalized_scientific_alignment_completion_receipt
    )

    def record_scientific_check(value):
        completion_checks["scientific"].append(value)
        return scientific_check(value)

    post_cell_check = post_cell_owner.validate_untarget_post_cell_completion_receipt

    def record_post_cell_check(value):
        completion_checks["post_cell"].append(value)
        return post_cell_check(value)

    monkeypatch.setattr(
        result_owner,
        "_validate_finalized_scientific_alignment_completion_receipt",
        record_scientific_check,
    )
    monkeypatch.setattr(
        post_cell_owner,
        "validate_untarget_post_cell_completion_receipt",
        record_post_cell_check,
    )

    bind_capture = capture_owner.bind_owner_backed_capture_hypothesis
    evaluate_physical = observation_owner.evaluate_physical_peak
    binding_rechecks = []
    bound_captures = []

    def record_binding(capture, **kwargs):
        rechecks = []

        def record_evaluation(*args, **evaluation_kwargs):
            rechecks.append(True)
            return evaluate_physical(*args, **evaluation_kwargs)

        with monkeypatch.context() as binding_patch:
            for owner in (capture_owner, observation_owner):
                binding_patch.setattr(
                    owner, "evaluate_physical_peak", record_evaluation
                )
            bound = bind_capture(capture, **kwargs)
        binding_rechecks.append(len(rechecks))
        bound_captures.append((capture, kwargs))
        return bound

    for owner in (production, workflow_owner):
        monkeypatch.setattr(
            owner, "bind_owner_backed_capture_hypothesis", record_binding
        )

    hypothesis_universe_compositions = []
    compose_hypothesis_universe = (
        bootstrap_owner.compose_peak_hypotheses_from_assessment_universe
    )

    def count_hypothesis_universe(owner_name):
        def counted(**kwargs):
            assessment = kwargs["assessment"]
            hypothesis_universe_compositions.append(
                (
                    owner_name,
                    kwargs["sample"].identity.sample_identity_id,
                    assessment.artifact.content_identity.artifact_content_id,
                )
            )
            return compose_hypothesis_universe(**kwargs)

        return counted

    for owner_name, owner in (
        ("production", production),
        ("bootstrap", bootstrap_owner),
        ("cell", predecessor_cell_owner),
    ):
        monkeypatch.setattr(
            owner,
            "compose_peak_hypotheses_from_assessment_universe",
            count_hypothesis_universe(owner_name),
        )

    retained_contexts = []
    bootstrap_contexts = []
    retained_initial_edges = []
    consumed_contexts = []
    retained_captures = []
    consumed_captures = []
    retained_candidate_batches = []
    retained_cell_batches = []
    build_initial_context = production.build_initial_cid_nl_cross_sample_groups
    regroup_initial_context = production.regroup_cid_nl_cross_sample_groups
    compose_predecessor_cells = cid_nl_completion_owner.compose_cid_nl_predecessor_cells
    finalize_scientific = workflow_owner.finalize_scientific_alignment

    def retain_initial_context(**kwargs):
        grouping_scope, context, retained_candidates = build_initial_context(**kwargs)
        bootstrap_contexts.append(context)
        retained_captures.extend(kwargs["captures"])
        retained_candidate_batches.append(retained_candidates)
        return grouping_scope, context, retained_candidates

    def retain_regrouped_context(**kwargs):
        grouping_scope, context = regroup_initial_context(**kwargs)
        retained_contexts.append(context)
        return grouping_scope, context

    def retain_predecessor_cells(**kwargs):
        cells = compose_predecessor_cells(**kwargs)
        retained_cell_batches.append(cells)
        return cells

    def retain_scientific_captures(**kwargs):
        consumed_contexts.append(kwargs["grouping_result"])
        consumed_captures.extend(kwargs["captures"])
        return finalize_scientific(**kwargs)

    monkeypatch.setattr(
        production,
        "build_initial_cid_nl_cross_sample_groups",
        retain_initial_context,
    )
    monkeypatch.setattr(
        production,
        "regroup_cid_nl_cross_sample_groups",
        retain_regrouped_context,
    )
    monkeypatch.setattr(
        cid_nl_completion_owner,
        "compose_cid_nl_predecessor_cells",
        retain_predecessor_cells,
    )
    monkeypatch.setattr(
        workflow_owner, "finalize_scientific_alignment", retain_scientific_captures
    )

    runtime_results = []
    runtime_execute = bounded_owner.execute_partition

    def checked_runtime(*args, **kwargs):
        result = runtime_execute(*args, **kwargs)
        assert result.succeeded, (
            tuple(
                settlement.failure_detail.message
                for settlement in result.worker_batch.settlements
                if settlement.failure_detail is not None
            ),
            tuple(
                (
                    sample_stem,
                    candidate.candidate_id,
                    candidate.seed_scan_ids,
                    candidate.rt_seed_min,
                    candidate.rt_seed_max,
                )
                for sample_stem, sample_candidates in candidates.items()
                for candidate in sample_candidates
            ),
        )
        if not kwargs.get("initial_only"):
            runtime_results.append(result)
        if family_context:
            for sample_result in result.sample_results:
                adapter = sample_result.adapter_result
                stem = sample_result.job.sample.metadata.raw_stem
                expected_source_candidate_ids = {
                    candidate.candidate_id
                    for candidate in candidates[stem]
                    if dict(
                        captured_batches[0].subject_batch.candidate_memberships
                    ).get(candidate.candidate_id)
                    == sample_result.job.method_subject.method_subject_id
                }
                assert (
                    set(adapter.source_candidate_ids) == expected_source_candidate_ids
                )
                assert (
                    captured_batches[0].context.physical_records_by_candidate
                    == source_records
                )
                expected_capture_candidate_ids = {
                    candidate.candidate_id
                    for candidate in candidates[stem]
                    if (
                        candidate.candidate_id in expected_source_candidate_ids
                        and candidate.discovery_candidate_state
                        in MS1_FEATURE_BACKED_STATES
                    )
                }
                expected_trace_count = 1 if expected_capture_candidate_ids else 0
                if expected_trace_count:
                    assert adapter.acquisition is not None
                    # One shared integration trace plus non-authoritative
                    # elution context, still acquired in one initial sample batch.
                    assert len(adapter.elution_contexts) == 1
                    expected_acquisition_count = expected_trace_count + 1
                    assert len(adapter.acquisition.traces) == expected_acquisition_count
                    assert adapter.acquisition.adapter_chromatogram_call_count == 1
                else:
                    assert adapter.acquisition is None
                assert (
                    len(adapter.assessment_input.assessment.candidate_peaks)
                    == expected_trace_count
                )
                from xic_extractor.alignment.hypothesis_owner_adapter import (
                    compose_peak_hypotheses_from_assessment_universe,
                )

                hypotheses = compose_peak_hypotheses_from_assessment_universe(
                    processing_owners=sample_result.job.processing_owners,
                    sample=sample_result.job.sample,
                    assessment=adapter.assessment_input.assessment,
                )
                assert len(hypotheses) == expected_trace_count
                assert {capture.candidate_id for capture in adapter.captures} == set(
                    expected_capture_candidate_ids
                )
                for capture in adapter.captures:
                    expected = family_evaluations[capture.candidate_id]
                    if family_precursor_delta:
                        assert capture.physical_evaluation.facts is not None
                        assert expected.facts is not None
                        assert (
                            capture.physical_evaluation.facts.apex_rt_min
                            == expected.facts.apex_rt_min
                        )
                    else:
                        assert capture.physical_evaluation.facts == expected.facts
        return result

    monkeypatch.setattr(bounded_owner, "execute_partition", checked_runtime)
    preparation_fingerprints = []
    runtime_fingerprints = []
    preparation_fingerprint = identity_owner.fingerprint_file_content
    fingerprint = raw_reader_owner.fingerprint_file_content

    def count_preparation_fingerprint(path, **kwargs):
        preparation_fingerprints.append(path)
        return preparation_fingerprint(path, **kwargs)

    def count_runtime_fingerprint(path):
        runtime_fingerprints.append(path)
        return fingerprint(path)

    monkeypatch.setattr(
        raw_reader_owner, "fingerprint_file_content", count_runtime_fingerprint
    )
    monkeypatch.setattr(
        identity_owner, "fingerprint_file_content", count_preparation_fingerprint
    )
    from xic_extractor.discovery.input_source import DiscoveryInputSource

    completed = alignment_adapter.complete_untargeted_alignment(
        discovery_batch=discovery_batch,
        candidates_by_sample=(
            DiscoveryInputSource(discovery_batch) if indexed_input else candidates
        ),
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "alignment",
        settings=settings,
        peak_config=peak_config,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        signal_processing_profile=profile,
        timing_recorder=TimingRecorder.disabled(
            "alignment",
            process_memory_sampler=ProcessTreeMemorySampler(
                sample_reader=lambda: ProcessMemorySample(123456, 654321, "measured"),
            ),
        ),
    )
    composed_universes = [item[1:] for item in hypothesis_universe_compositions]
    assert len(composed_universes) == len(set(composed_universes)), (
        {
            owner_name: sum(
                item[0] == owner_name for item in hypothesis_universe_compositions
            )
            for owner_name in {item[0] for item in hypothesis_universe_compositions}
        },
        hypothesis_universe_compositions,
    )
    retained_hypotheses_by_id = {
        candidate.peak_hypothesis_id: candidate.peak_hypothesis
        for candidates in retained_candidate_batches
        for candidate in candidates
    }
    assert all(
        capture.selected_hypothesis is None
        or retained_hypotheses_by_id[capture.selected_hypothesis.hypothesis_id]
        == capture.selected_hypothesis
        for capture in retained_captures
    )
    assert all(
        retained_hypotheses_by_id[support.hypothesis.hypothesis_id]
        == support.hypothesis
        for cells in retained_cell_batches
        for cell in cells
        for support in cell.group_context.supports
    )
    # Owner-produced arrays survive both sidecar and runtime partition handoff.
    retained_discovery = tuple(completed.discovery_physical_records)
    assert {item.candidate_id for item in retained_discovery} == set(source_records)
    for retained in retained_discovery:
        source = source_records[retained.candidate_id]
        assert (
            retained.evaluation.retained_prepared_signal
            == source.evaluation.retained_prepared_signal
        )
        assert retained.rt == source.rt
        assert retained.raw == source.raw
        assert retained.candidate_links == source.candidate_links
    persisted_capture_evidence = tuple(completed.capture_evidence)
    runtime_captures = tuple(
        evidence
        for result in runtime_results
        for sample_result in result.sample_results
        for evidence in (
            *sample_result.adapter_result.capture_evidence,
            *(
                item
                for attempt in sample_result.backfill_attempts
                for item in attempt.capture_evidence
            ),
        )
    )
    assert len(persisted_capture_evidence) == len(runtime_captures)
    assert persisted_capture_evidence
    for retained, source in zip(
        persisted_capture_evidence, runtime_captures, strict=True
    ):
        assert (
            retained.canonical_sample_identity_id == source.canonical_sample_identity_id
        )
        assert retained.candidate_id == source.candidate_id
        assert retained.capture_stage == source.capture_stage
        assert retained.physical_evaluation is not None
        assert source.physical_evaluation is not None
        assert (
            retained.physical_evaluation.retained_prepared_signal
            == source.physical_evaluation.retained_prepared_signal
        )
        assert retained.observation_ref == source.observation_ref
        if retained.observation_ref is not None:
            assert retained.observation_ref.capture_stage == retained.capture_stage
            assert (
                retained.observation_ref.physical_evaluation
                == retained.physical_evaluation
            )

    expected_curves = []
    expected_curve_links = {}
    application = completed.application_result
    samples_by_stem = {
        sample.metadata.raw_stem: sample for sample in application.canonical_samples
    }
    for record in retained_discovery:
        work = record.work_item
        source_trace = SimpleNamespace(
            rt=record.rt,
            intensity=record.raw,
            mz=work.xic_mz,
            rt_min=work.xic_rt_min,
            rt_max=work.xic_rt_max,
            ppm_tol=work.xic_ppm_tol,
            acquisition_source_id=work.acquisition_source_id,
        )
        expected = _expected_retained_curve(
            kind="discovery_physical",
            candidate_id=record.candidate_id,
            sample_id=samples_by_stem[work.sample_stem].identity.sample_identity_id,
            trace=source_trace,
            evaluation=record.evaluation,
            request_id=application.processing_request.processing_request_id,
            attempt_id=application.processing_attempt.processing_attempt_id,
        )
        expected_curves.append(expected)
        for cid in {link.candidate_id for link in record.candidate_links} or {
            record.candidate_id
        }:
            expected_curve_links.setdefault(cid, []).append(expected)
    for evidence in persisted_capture_evidence:
        expected = _expected_capture_curve(evidence)
        expected_curves.append(expected)
        if evidence.candidate_id is not None:
            expected_curve_links.setdefault(evidence.candidate_id, []).append(expected)

    def close_and_verify_persisted_curves():
        completed.close()
        _assert_fresh_process_curve_parity(
            tmp_path / "producer-evidence.sqlite", expected_curves, expected_curve_links
        )

    assert Counter(preparation_fingerprints) == Counter(
        tmp_path / f"{stem}.synthetic" for stem in discovery_batch.sample_order
    ), "formal preparation must fully fingerprint each RAW source once"
    assert runtime_fingerprints == [], (
        "RAW opens must reuse the input owner's source verification",
        runtime_fingerprints,
    )
    marker = OwnerPathRuntimeMarker(
        tmp_path / "alignment" / ".cid_nl_runtime.json",
        "untargeted-cid-nl-alignment",
    ).read_optional()
    assert marker is not None and marker.observability_ledger_bytes is not None
    persisted_ledger = json.loads(marker.observability_ledger_bytes)
    assert persisted_ledger["schema_version"] == (
        "execution_runtime_observability_ledger"
    )
    assert all(
        event["monotonic_offset_ns"] is None for event in persisted_ledger["events"]
    )
    unavailable_metric_names = {
        "artifact_read_count",
        "artifact_read_bytes",
        "artifact_write_count",
        "artifact_write_bytes",
        "coordinator_work_seconds",
        "attempt_wall_seconds",
        "stage_or_unit_elapsed_seconds",
    }
    unavailable_metrics = tuple(
        metric
        for metric in persisted_ledger["metrics"]
        if metric["name"] in unavailable_metric_names
    )
    assert {metric["name"] for metric in unavailable_metrics} == (
        unavailable_metric_names
    )
    assert all(
        metric["availability"] == "unsupported"
        and metric["value"] is None
        and metric["reason"] == "not_collected_by_alignment_foreground_runner"
        for metric in unavailable_metrics
    )
    rss = [
        metric
        for metric in persisted_ledger["metrics"]
        if metric["name"] == "process_tree_peak_rss_bytes"
    ]
    assert len(rss) == 1
    assert rss[0]["availability"] == "measured"
    assert rss[0]["value"] == 123456
    _assert_jobs_match_direct_construction(captured_batches[0])
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix_path = tmp_path / "canonical-matrix.tsv"
    write_alignment_matrix_tsv(
        matrix_path, writer.matrix, production_decisions=writer.production_decisions
    )
    with matrix_path.open(encoding="utf-8", newline="") as handle:
        matrix_rows = tuple(csv.DictReader(handle, delimiter="\t"))
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        write_cid_nl_explanation,
    )

    write_cid_nl_explanation(
        tmp_path / "producer-evidence.sqlite",
        full_audit=True,
        completed=completed,
        writer_inputs=writer,
        candidates_by_sample=candidates,
        matrix_path=matrix_path,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )
    assert len(retained_contexts) == len(completed.application_result.children)
    assert len(retained_contexts) == len(consumed_contexts)
    # Frozen grouping crosses the spool before Backfill. Its complete scientific
    # content must survive; Python object identity is not a persisted contract.
    assert all(
        retained == consumed
        for retained, consumed in zip(retained_contexts, consumed_contexts, strict=True)
    )
    # Bootstrap captures cross a disk handoff; preserve content, not addresses.
    for retained, consumed in zip(retained_captures, consumed_captures, strict=True):
        assert retained.candidate_id == consumed.candidate_id
        assert retained.selected_hypothesis == consumed.selected_hypothesis
        assert list(retained.trace.rt) == list(consumed.trace.rt)
        assert list(retained.trace.intensity) == list(consumed.trace.intensity)
    for context, child in zip(
        retained_contexts, completed.application_result.children, strict=True
    ):
        assert child.scientific_core.final_population == context.final_population
        assert child.scientific_core.lineage == ()
        assert child.scientific_core.grouping_result == context
    assert tuple(completed.initial_edges) == tuple(retained_initial_edges)
    children = completed.application_result.children
    # Completed owner answers flow through projection, spool and writer once.
    assert len(projection_builds) == len(children)
    assert not decision_rechecks
    assert not completion_checks["scientific"]
    # Post-cell checks close each owner result before spool publication.
    expected_projections = tuple(child.completed_projection for child in children)
    assert tuple(completion_checks["post_cell"]) == expected_projections
    assert len({id(value) for value in completion_checks["post_cell"]}) == len(
        completion_checks["post_cell"]
    )
    # Binding consumes the closed capture; it must not repeat physical selection.
    assert not any(binding_rechecks)
    if method_mode != "intersection":
        assert binding_rechecks
        assert mint_rechecks
        assert mint_args
    assert len(mint_rechecks) == len(mint_args)
    assert all(counts == {"prepare": 0, "evaluate": 0} for counts in mint_rechecks)
    if family_context:
        assert len(matrix_rows) == 1
        if family_precursor_delta:
            batch = captured_batches[0]
            assert len(batch.subject_batch.subjects) == 3
            assert len(children) == 2
            assert sorted(
                len(child.scientific_core.scope.evidence_providers)
                for child in children
            ) == [1, 2]
            child = next(
                child
                for child in children
                if len(child.scientific_core.scope.evidence_providers) == 2
            )
            assert tuple(
                provider.method_subject_id
                for provider in child.scientific_core.scope.evidence_providers
            ) != tuple(
                subject.method_subject_id for subject in batch.subject_batch.subjects
            )
            assert sorted(
                len(group.member_subjects)
                for group in child.scientific_core.final_population
            ) == [1, 2]
            sample_id_by_stem = {
                sample.metadata.raw_stem: sample.identity.sample_identity_id
                for sample in child.scientific_core.canonical_samples
            }
            joint_group = next(
                group
                for group in child.scientific_core.final_population
                if len(group.member_subjects) == 2
            )
            assert {
                member.canonical_sample_identity_id
                for member in joint_group.member_subjects
            } == {sample_id_by_stem["S2"], sample_id_by_stem["S3"]}
            joint_final_cell = next(
                cell
                for cell in child.scientific_core.cell_observations
                if cell.group_hypothesis_id == joint_group.group_hypothesis_id
                and cell.canonical_sample_identity_id == sample_id_by_stem["S3"]
            )
            (joint_cell,) = joint_final_cell.predecessor_cells
            assert any(
                support.assessment is joint_cell.method_assessment
                and support.sample.identity.sample_identity_id
                == sample_id_by_stem["S3"]
                for support in joint_cell.group_context.supports
            )
            assert all(
                assessment.content.reason_codes
                != ("no_search_eligible_discovery_input",)
                for assessment in completed.method_assessments
            )
            assert len(writer.matrix.clusters) == 3
            assert len(writer.matrix.cells) == 3 * len(sample_stems)
            (matrix_row,) = matrix_rows
            assert matrix_row["S1"] == ""
            assert matrix_row["S2"]
            assert matrix_row["S3"]
            # The fixture XIC ignores m/z, so S4's group-window trace holds a
            # complete peak without CID-NL: an MS1-only Backfill value.
            (s4_cell,) = (
                cell
                for cell in child.scientific_core.cell_observations
                if cell.group_hypothesis_id == joint_group.group_hypothesis_id
                and cell.canonical_sample_identity_id == sample_id_by_stem["S4"]
            )
            assert s4_cell.evidence_states == ("ms1_only_backfill_observation",)
            assert matrix_row["S4"]
        else:
            assert len(writer.matrix.cells) == len(sample_stems)
        close_and_verify_persisted_curves()
        return
    if nearby:
        assert len(matrix_rows) == (0 if method_mode == "intersection" else 1)
        batch = captured_batches[0]
        assert len(batch.subject_batch.subjects) == 1
        for result in runtime_results[0].sample_results:
            stem = result.job.sample.metadata.raw_stem
            (candidate,) = batch.context.candidates_by_sample[stem]
            record = batch.context.physical_records_by_candidate[candidate.candidate_id]
            assert record.work_item.precursor_mz == candidate.precursor_mz
            assert record.acquired_trace.coordinate.request.mz == candidate.precursor_mz
            assert record.evaluation.facts == physical_facts[stem]
            assert result.effective_assessment.content.method_result == (
                "HOLD" if method_mode == "intersection" else "SUPPORT"
            )
        if method_mode == "intersection":
            assert writer.matrix.cells == ()
            assert writer.matrix.clusters == ()
            assert all(
                "incomplete_composite_requirement"
                in result.effective_assessment.content.reason_codes
                for result in runtime_results[0].sample_results
            )
            _assert_composite_no_input_exclusion_coverage(
                completed, runtime_results[0], batch
            )
            close_and_verify_persisted_curves()
            return
        if method_mode == "union":
            (child,) = completed.application_result.children
            assert sorted(
                len(group.member_subjects)
                for group in child.scientific_core.final_population
            ) == [2]
            selected_cells = tuple(
                cell for cell in writer.matrix.cells if cell.selected_integration
            )
            assert {cell.sample_stem for cell in selected_cells} == set(sample_stems)
            for cell in selected_cells:
                facts = physical_facts[cell.sample_stem]
                assert cell.area == facts.area_counts_seconds
                assert cell.selected_integration is not None
                assert cell.selected_integration.rt_apex_min == facts.apex_rt_min
            close_and_verify_persisted_curves()
            return
    assert len(writer.matrix.cells) == len(group_sizes) * len(sample_stems)
    for cell in writer.matrix.cells:
        if cell.selected_integration is None:
            assert separate_groups
            assert not writer.production_decisions.cell(
                cell.cluster_id, cell.sample_stem
            ).write_matrix_value
            continue
        facts = physical_facts[cell.sample_stem]
        assert cell.area == facts.area_counts_seconds
        assert cell.selected_integration is not None
        assert cell.selected_integration.area_raw_counts_seconds != cell.area
        assert cell.selected_integration.rt_apex_min == facts.apex_rt_min
        assert cell.selected_integration.height_smoothed == facts.height
        assert facts.physical_segment_id in cell.selected_integration.boundary_sources
    expected_row_count = sum(size > 1 for size in group_sizes)
    assert len(matrix_rows) == expected_row_count
    for sample_stem, facts in physical_facts.items():
        sample_values = [
            float(row[sample_stem]) for row in matrix_rows if row[sample_stem]
        ]
        assert sample_values == pytest.approx(
            [facts.area_counts_seconds] if expected_row_count else []
        )
    if nearby:
        assert len({facts.fact_content_id for facts in physical_facts.values()}) == 2
        assert len({cell.source_candidate_id for cell in writer.matrix.cells}) == 2
        assert len(matrix_rows) == 1
        close_and_verify_persisted_curves()
        return
    if separate_groups:
        (child,) = completed.application_result.children
        scientific = child.scientific_core
        assert len(scientific.final_population) == 2
        assert len(scientific.cell_observations) == 2 * len(sample_stems)
        # Each sample's SUPPORT belongs to its own group. A group with K direct
        # supports revisits the other samples' windows, which hold no peak;
        # below K the other samples stay unsearched outside-group cells.
        revisited = min(group_sizes) >= 2
        assert sum(
            cell.evidence_states
            == (("unavailable",) if revisited else ("not_searched",))
            for cell in scientific.cell_observations
        ) == len(sample_stems)
        assert all(
            sum(bool(row[stem]) for stem in sample_stems) == 2 for row in matrix_rows
        )
        assert all(
            result.adapter_result.assessment_input.assessment.content.method_result
            == "SUPPORT"
            for result in runtime_results[0].sample_results
        )
        assert (
            any(
                source.content.outside_group is not None
                for cell in scientific.cell_observations
                for source in cell.predecessor_cells
            )
            != revisited
        )
        close_and_verify_persisted_curves()
        return

    from xic_extractor.peak_detection.observations import (
        PeakObservationContractError,
        mint_peak_observation_ref,
        physical_peak_observation_integration,
    )

    capture = captured_peaks[0]
    evaluation = capture.physical_evaluation
    assert evaluation is not None
    integration = physical_peak_observation_integration(evaluation)
    prepared = prepare_physical_signal(
        capture.trace.rt, capture.trace.intensity, profile=profile
    )
    assert prepared.prepared is not None
    observation_args = dict(
        processing_owners=captured_batches[0].jobs[0].processing_owners,
        sample=captured_batches[0].jobs[0].sample,
        trace=capture.trace,
        integration=integration,
        selection_intensity=prepared.prepared.positive_signal,
        resolver_mode=peak_config.resolver_mode,
        proposal_sources=("canonical_physical_segment",),
        physical_evaluation=evaluation,
        signal_processing_profile=profile,
    )
    for substitution in (
        {"signal_processing_profile": SignalProcessingProfile(window_points=21)},
        {"trace": replace(capture.trace, intensity=capture.trace.intensity + 1.0)},
        {
            "integration": replace(
                integration,
                area_baseline_corrected=integration.area_baseline_corrected + 1.0,
            )
        },
    ):
        with pytest.raises((PeakObservationContractError, ValueError)):
            mint_peak_observation_ref(**(observation_args | substitution))

    bound_capture, binding_args = bound_captures[0]
    changed_source = capture_peak(
        scope=bound_capture.scope,
        candidate_id=bound_capture.candidate_id,
        trace=replace(bound_capture.trace, source="different-capture-source"),
    )
    with pytest.raises(capture_owner.SampleLocalCaptureError):
        bind_capture(changed_source, **binding_args)
    from xic_extractor.peak_detection.search_scope import SearchScopeContractError

    changed_answer = evaluate_physical_peak(
        prepared,
        selection_subject_id="substituted-selection-subject",
        query_rt_min=evaluation.question.query_rt_min,
        request_context_rt_start_min=capture.trace.rt_min,
        request_context_rt_end_min=capture.trace.rt_max,
    )
    with pytest.raises(SearchScopeContractError, match="answer is not bound"):
        replace(
            capture.scope,
            capture_bindings=tuple(
                replace(binding, physical_evaluation=changed_answer)
                if binding.candidate_id == capture.candidate_id
                else binding
                for binding in capture.scope.capture_bindings
            ),
        )
    batch = captured_batches[0]
    from xic_extractor.peak_detection.cid_nl_peak_support import (
        DdaCidNlPeakSupportPolicy,
    )
    from xic_extractor.processing_identity import resolve_request_acquisition_source
    from xic_extractor.untargeted_execution.partition.cid_nl_acquisition import (
        CidNlAcquisitionError,
        backfill_untargeted_cid_nl_sample_subject,
    )

    job = batch.jobs[0]
    recovery_raw = partition_workers.open_raw(
        paths[0],
        tmp_path,
        expected_acquisition_source=resolve_request_acquisition_source(
            processing_owners=job.processing_owners, sample=job.sample
        ),
    )
    recovery_args = dict(
        processing_owners=job.processing_owners,
        sample=job.sample,
        method_subject=job.method_subject,
        group_center_rt=center_rt,
        group_center_mz=job.method_subject.precursor_mz,
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01, neutral_loss_tolerance_ppm=10.0
        ),
        peak_config=peak_config,
        raw=recovery_raw,
        settings=settings,
        signal_processing_profile=profile,
    )
    recovered = backfill_untargeted_cid_nl_sample_subject(**recovery_args)
    (observation,) = recovered.assessment.candidate_peaks
    acquired = recovered.acquisition.traces[0]
    recovery_prepared = prepare_physical_signal(
        acquired.trace.rt, acquired.trace.intensity, profile=profile
    )
    assert observation.capture_stage == "method_backfill"
    assert observation.physical_evaluation is not None
    assert (
        observation.physical_evaluation.retained_prepared_signal
        == recovery_prepared.prepared
    )
    (retained_recovery,) = recovered.capture_evidence
    assert retained_recovery.observation_ref is observation
    assert retained_recovery.physical_evaluation is observation.physical_evaluation
    assert tuple(retained_recovery.trace.rt) == tuple(acquired.trace.rt)
    assert tuple(retained_recovery.trace.intensity) == tuple(acquired.trace.intensity)
    integ = observation.content.integration
    physical_facts = observation.physical_evaluation.facts
    assert physical_facts is not None
    assert integ.area_baseline_corrected == physical_facts.area_counts_seconds
    assert integ.height_smoothed == physical_facts.height
    assert recovered.assessment.content.method_result == "HOLD"
    with pytest.raises(
        capture_owner.SampleLocalCaptureError, match="question is not request-bound"
    ):
        backfill_untargeted_cid_nl_sample_subject(
            **(
                recovery_args
                | {
                    "signal_processing_profile": SignalProcessingProfile(
                        window_points=21
                    )
                }
            )
        )
    with pytest.raises(
        CidNlAcquisitionError, match="lost its request-bound profile"
    ):
        backfill_untargeted_cid_nl_sample_subject(
            **(recovery_args | {"signal_processing_profile": None})
        )
    original_intensity = source_raws[paths[0]].intensity
    recovery_evaluations = []
    evaluate_recovery = capture_owner.evaluate_physical_peak

    def retain_recovery_evaluation(*args, **kwargs):
        evaluation = evaluate_recovery(*args, **kwargs)
        recovery_evaluations.append(evaluation)
        return evaluation

    monkeypatch.setattr(
        capture_owner,
        "evaluate_physical_peak",
        retain_recovery_evaluation,
    )
    try:
        for offsets, reason in (
            ((0,), "insufficient_original_xic_support"),
            ((-2, 2), "insufficient_original_xic_continuity"),
        ):
            isolated = original_intensity * 0.0
            for offset in offsets:
                isolated[len(isolated) // 2 + offset] = float(max(original_intensity))
            source_raws[paths[0]].intensity = isolated
            unavailable = backfill_untargeted_cid_nl_sample_subject(**recovery_args)
            assert unavailable.assessment.candidate_peaks == ()
            assert unavailable.assessment.content.method_result == "HOLD"
            assert unavailable.backfill_input is None
            assert recovery_evaluations[-1].reason == reason
            assert recovery_evaluations[-1].facts is None
            assert recovery_evaluations[-1].integration is None
            assert recovery_evaluations[-1].question.selection_policy_version == (
                "sample_local_background_qualified_peak_in_context"
            )

        source_raws[paths[0]].intensity = original_intensity * 0.0
        unavailable = backfill_untargeted_cid_nl_sample_subject(**recovery_args)
        assert unavailable.assessment.candidate_peaks == ()
        assert unavailable.assessment.content.method_result == "HOLD"
        assert unavailable.backfill_input is None
        (retained_unavailable,) = unavailable.capture_evidence
        assert retained_unavailable.observation_ref is None
        assert retained_unavailable.physical_evaluation.state == "unavailable"
        signal = retained_unavailable.physical_evaluation.retained_prepared_signal
        assert signal is None  # The owner skips preparation for an all-zero trace.
        assert len(retained_unavailable.trace.rt) > 0
        assert all(value == 0.0 for value in retained_unavailable.trace.intensity)

        # The external vendor can return an empty chromatogram for a valid
        # native scan window. Keep the acquisition and scientific owners live.
        vendor = opened_raws[paths[0]][-1]
        vendor_chromatogram = vendor.GetChromatogramData

        def empty_vendor_chromatogram(*args):
            data = vendor_chromatogram(*args)
            data.PositionsArray = [[] for _ in data.PositionsArray]
            data.IntensitiesArray = [[] for _ in data.IntensitiesArray]
            return data

        with monkeypatch.context() as empty_vendor:
            empty_vendor.setattr(
                vendor, "GetChromatogramData", empty_vendor_chromatogram
            )
            empty = backfill_untargeted_cid_nl_sample_subject(**recovery_args)
        assert empty.assessment.candidate_peaks == ()
        assert empty.assessment.content.method_result == "HOLD"
        assert empty.backfill_input is None
        assert empty.acquisition.traces[0].coordinate.trace_point_count == 0
        (retained_empty,) = empty.capture_evidence
        assert retained_empty.observation_ref is None
        assert retained_empty.physical_evaluation.state == "unavailable"
        assert retained_empty.physical_evaluation.reason == "no_signal_above_baseline"
        assert retained_empty.physical_evaluation.integration is None
        assert retained_empty.physical_evaluation.retained_prepared_signal is None
        assert len(retained_empty.trace.rt) == len(retained_empty.trace.intensity) == 0

    finally:
        source_raws[paths[0]].intensity = original_intensity
        recovery_raw.__exit__(None, None, None)

    # The actual recovery owner ran above under this request and attempt.
    # Exercise its production sink directly, without altering the completed
    # scientific population or manufacturing a new recovery identity.
    from xic_extractor.diagnostics.alignment_explanation import ExplanationReportWriter
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        _emit_curve_payload,
    )

    recovery_path = tmp_path / "recovery-evidence.sqlite"
    recovery_sources = (retained_recovery, retained_unavailable, retained_empty)
    with ExplanationReportWriter(recovery_path) as sink:
        for evidence in recovery_sources:
            _emit_curve_payload(
                sink,
                evidence.observation_ref,
                evidence.canonical_sample_identity_id,
                kind="backfill_observation",
                trace=evidence.trace,
                evaluation=evidence.physical_evaluation,
                provenance={
                    "processing_request_id": evidence.processing_request_id,
                    "processing_attempt_id": evidence.processing_attempt_id,
                    "method_subject_id": evidence.method_subject_id,
                },
            )
        sink.finalize(
            {
                "processing_request_id": (
                    job.processing_owners.request.processing_request_id
                ),
                "processing_attempt_id": (
                    job.processing_owners.attempt.processing_attempt_id
                ),
                "matrix_path": str(matrix_path),
            }
        )
    _assert_fresh_process_curve_parity(
        recovery_path, [_expected_capture_curve(item) for item in recovery_sources]
    )
    from xic_extractor.untargeted_execution.partition.models import (
        UntargetedPartitionError,
    )

    with pytest.raises(
        UntargetedPartitionError, match="profile is not request-bound"
    ):
        replace(
            batch.jobs[0],
            signal_processing_profile=SignalProcessingProfile(window_points=21),
        )
    first_job = batch.jobs[0]
    sample_inputs = {
        item.name: getattr(first_job, item.name)
        for item in fields(first_job)
        if item.init and item.name not in {"method_subject", "work_unit_key"}
    }
    subject_work_units = tuple(
        (job.method_subject, job.work_unit_key)
        for job in batch.jobs
        if job.sample == first_job.sample
    )
    for changed, message in (
        ({"signal_processing_profile": None}, "lost its profile"),
        (
            {"signal_processing_profile": SignalProcessingProfile(window_points=21)},
            "profile is not request-bound",
        ),
        ({"physical_records": ()}, "candidate coverage changed"),
    ):
        with pytest.raises(UntargetedPartitionError, match=message):
            build_sample_jobs(
                **(sample_inputs | changed), subject_work_units=subject_work_units
            )
    if not indexed_input:
        wrong_records = dict(batch.context.physical_records_by_candidate)
        keys = tuple(wrong_records)
        wrong_records[keys[0]] = wrong_records[keys[1]]
        with pytest.raises(IdentityContractError, match="crossed candidate"):
            alignment_adapter.prepare_eager_untargeted_plan(
                raw_paths=paths,
                sample_metadata=tuple(
                    sample.metadata for sample in batch.context.samples
                ),
                settings=settings,
                peak_config=peak_config,
                candidates_by_sample=batch.context.candidates_by_sample,
                discovery_receipts_by_sample=receipts,
                dll_dir=tmp_path,
                sample_identity_namespace="test:physical-replay",
                signal_processing_profile=profile,
                physical_records_by_candidate=wrong_records,
            )
    close_and_verify_persisted_curves()


@pytest.mark.parametrize(
    ("mz", "rt"),
    (
        (float("nan"), 8.0),
        (500.0, float("inf")),
        (True, 8.0),
        (500.0, False),
    ),
)
def test_writer_projection_rejects_invalid_presentation_coordinates(
    mz: float,
    rt: float,
) -> None:
    with pytest.raises(ValueError, match="finite owner-backed"):
        finalized_matrix._add_presentation_fact(
            {},
            physical_id="physical-1",
            mz=mz,
            rt=rt,
        )


def test_writer_projection_keeps_distinct_repaired_facts_with_shared_signal() -> None:
    rt = tuple(7.0 + 0.01 * index for index in range(101))
    raw = tuple(
        1.0 + 100.0 * math.exp(-0.5 * ((index - 50) / 4.0) ** 2) for index in range(101)
    )
    prepared = prepare_physical_signal(
        rt,
        raw,
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    evaluations = tuple(
        evaluate_physical_peak(
            prepared,
            selection_subject_id=subject_id,
            query_rt_min=7.5,
        )
        for subject_id in ("repair-subject-a", "repair-subject-b")
    )
    assert evaluations[0].prepared.prepared_signal_id == (
        evaluations[1].prepared.prepared_signal_id
    )
    assert evaluations[0].facts is not None
    assert evaluations[1].facts is not None
    assert evaluations[0].facts.fact_content_id != evaluations[1].facts.fact_content_id

    repaired_observations = tuple(
        SimpleNamespace(
            selected_for_output=True,
            facts=SimpleNamespace(physical_evaluation=evaluation),
            cell=SimpleNamespace(
                backfill_seed_mz=500.0 + index,
                apex_rt=evaluation.facts.apex_rt_min,
            ),
        )
        for index, evaluation in enumerate(evaluations)
    )
    observation = SimpleNamespace(
        group=SimpleNamespace(member_subjects=()),
        direct_observations=(),
        repaired_observations=repaired_observations,
    )

    _mz_values, _rt_values, physical_ids = finalized_matrix._presentation_facts(
        (observation,)
    )

    assert set(physical_ids) == {
        evaluations[0].facts.fact_content_id,
        evaluations[1].facts.fact_content_id,
    }
    assert len(physical_ids) == 2


def _subject_proof_record(
    candidate: DiscoveryCandidate,
    *,
    source: str = "source-1",
    apex: float = 7.5,
    bounds: tuple[float, float] = (7.0, 8.0),
    rt: tuple[float, ...] = (7.0, 7.5, 8.0),
    raw: tuple[float, ...] = (1.0, 10.0, 1.0),
    xic_mz: float | None = None,
    xic_ppm: float = 100.0,
):
    return SimpleNamespace(
        work_item=SimpleNamespace(
            acquisition_source_id=source,
            xic_mz=candidate.precursor_mz if xic_mz is None else xic_mz,
            xic_ppm_tol=xic_ppm,
        ),
        evaluation=SimpleNamespace(
            facts=SimpleNamespace(
                apex_rt_min=apex,
                left_rt_min=bounds[0],
                right_rt_min=bounds[1],
            )
        ),
        acquired_trace=SimpleNamespace(trace=SimpleNamespace(rt=rt, intensity=raw)),
    )


@pytest.mark.parametrize(
    ("scenario", "masses", "expected_units"),
    (
        (
            "far",
            (500.0, 750.0),
            ((0,), (1,)),
        ),
        (
            "different-signal",
            (500.0, 500.006),
            ((0,), (1,)),
        ),
        (
            "different-apex",
            (500.0, 500.006),
            ((0,), (1,)),
        ),
        (
            "bridge",
            (500.0, 500.015, 500.03),
            ((0, 1), (2,)),
        ),
    ),
    ids=(
        "far-mass-identical-signal",
        "different-signal",
        "different-apex-unproven",
        "common-center-bridge",
    ),
)
def test_physical_subject_units_require_complete_common_proof(
    tmp_path: Path,
    scenario: str,
    masses: tuple[float, ...],
    expected_units: tuple[tuple[int, ...], ...],
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"physical-subject-units:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=20.0
    )
    peak_config = _peak_config(tmp_path)
    _raw, base_candidates = _nearby_subject_candidates(
        raw_path, settings=settings, peak_config=peak_config
    )
    candidates = tuple(
        replace(
            base_candidates[0],
            candidate_id=f"subject-proof-{index}",
            best_ms2_scan_id=100 + index,
            precursor_mz=mz,
            product_mz=mz - 116.0474,
            configured_neutral_loss_da=116.0474,
        )
        for index, mz in enumerate(masses)
    )
    records = {
        candidate.candidate_id: _subject_proof_record(
            candidate,
            xic_mz=500.015 if scenario == "bridge" else None,
        )
        for candidate in candidates
    }
    if scenario == "different-signal":
        records[candidates[1].candidate_id] = _subject_proof_record(
            candidates[1], raw=(1.0, 11.0, 1.0)
        )
    elif scenario == "different-apex":
        records[candidates[1].candidate_id] = _subject_proof_record(
            candidates[1], apex=7.51
        )
    input_facts = _physical_subject_facts(candidates, records)
    if scenario == "far":

        class TraceMustStayLazy:
            def __iter__(self):
                raise AssertionError("far-mass candidates must not read trace arrays")

        assert all(fact.proof is not None for fact in input_facts)
        input_facts = tuple(
            replace(
                fact,
                proof=replace(
                    fact.proof,
                    rt=TraceMustStayLazy(),
                    raw=TraceMustStayLazy(),
                ),
            )
            for fact in input_facts
        )

    units = group_cid_nl_physical_subject_units(
        input_facts,
        composition=None,
        precursor_tolerance_ppm=20.0,
    )

    candidate_index = {
        candidate.candidate_id: index for index, candidate in enumerate(candidates)
    }
    assert (
        tuple(
            tuple(candidate_index[candidate_id] for candidate_id in unit)
            for _requirement, unit in units
        )
        == expected_units
    )


def test_subject_planning_uses_physical_mass_without_rewriting_seed_receipts(
    tmp_path: Path,
) -> None:
    from xic_extractor.untargeted_execution.input_planning import (
        prepare_untargeted_subject_membership,
    )

    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"physical-ion-planning:S1")
    settings = replace(_settings(), neutral_loss_profile=None)
    _, candidates = _nearby_subject_candidates(
        raw_path, settings=settings, peak_config=_peak_config(tmp_path)
    )
    inferred = replace(
        candidates[0],
        precursor_mz=445.2140,
        product_mz=445.2140 - 116.0474,
        ms1_apex_mz=445.2071,
        precursor_mz_basis="product_plus_neutral_loss",
    )
    subjects = prepare_untargeted_subject_membership(
        candidates_by_sample={"S1": (inferred,)}, settings=settings
    )
    assert len(subjects.subjects) == 1
    assert subjects.subjects[0].precursor_mz == 445.2071
    assert inferred.precursor_mz == 445.2140
    assert inferred.product_mz == 445.2140 - 116.0474

    selected = replace(inferred, ms1_apex_mz=None, precursor_mz_basis="scan_precursor")
    fallback = prepare_untargeted_subject_membership(
        candidates_by_sample={"S1": (selected,)}, settings=settings
    )
    assert fallback.subjects[0].precursor_mz == 445.2140


def test_physical_subject_units_keep_unproven_candidates_singleton(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"unproven-subject-units:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=20.0
    )
    _raw, base_candidates = _nearby_subject_candidates(
        raw_path, settings=settings, peak_config=_peak_config(tmp_path)
    )
    candidates = tuple(
        replace(
            base_candidates[0],
            candidate_id=f"unproven-{index}",
            best_ms2_scan_id=100 + index,
        )
        for index in range(2)
    )

    units = group_cid_nl_physical_subject_units(
        _physical_subject_facts(
            candidates,
            {candidates[0].candidate_id: _subject_proof_record(candidates[0])},
        ),
        composition=None,
        precursor_tolerance_ppm=20.0,
    )

    assert sorted(len(unit) for _requirement, unit in units) == [1, 1]


def test_proven_subject_unit_is_not_split_by_lower_fixed_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xic_extractor.untargeted_execution import input_planning as planning_owner

    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"lower-anchor-subject-unit:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=20.0
    )
    _raw, base_candidates = _nearby_subject_candidates(
        raw_path, settings=settings, peak_config=_peak_config(tmp_path)
    )
    masses = (499.995, 500.0, 500.015)
    candidates = tuple(
        replace(
            base_candidates[0],
            candidate_id=f"lower-anchor-{index}",
            best_ms2_scan_id=100 + index,
            precursor_mz=mz,
            product_mz=mz - 116.0474,
            configured_neutral_loss_da=116.0474,
        )
        for index, mz in enumerate(masses)
    )
    records = {
        candidates[0].candidate_id: _subject_proof_record(
            candidates[0], apex=7.25, raw=(1.0, 5.0, 1.0), xic_mz=500.0
        ),
        **{
            candidate.candidate_id: _subject_proof_record(candidate, xic_mz=500.0)
            for candidate in candidates[1:]
        },
    }
    captured = {}

    class ExpectedStop(Exception):
        pass

    def capture_subject_batch(**kwargs):
        captured["subject_batch"] = kwargs["method_subject"]
        raise ExpectedStop

    monkeypatch.setattr(
        planning_owner,
        'build_partition_context',
        capture_subject_batch,
    )
    with pytest.raises(ExpectedStop):
        planning_owner.prepare_eager_untargeted_plan(
            raw_paths=(raw_path,),
            sample_metadata=(),
            settings=settings,
            peak_config=_peak_config(tmp_path),
            candidates_by_sample={"S1": candidates},
            discovery_receipts_by_sample={},
            dll_dir=tmp_path,
            sample_identity_namespace="test:lower-anchor",
            physical_records_by_candidate=records,
        )

    subject_batch = captured["subject_batch"]
    memberships = dict(subject_batch.candidate_memberships)
    assert (
        memberships[candidates[0].candidate_id]
        != memberships[candidates[1].candidate_id]
    )
    assert (
        memberships[candidates[1].candidate_id]
        == memberships[candidates[2].candidate_id]
    )
    assert len(subject_batch.subjects) == 2


def test_alignment_subject_worksets_preserve_only_compatible_overlap() -> None:
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        CidNlComposition,
        CidNlMethodSubjectContractError,
        cid_nl_alignment_subject_worksets,
        mint_untargeted_cid_nl_method_subject,
        validate_cid_nl_alignment_subject_workset,
    )

    first = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    overlapping = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.015,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    disconnected = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.05,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    different_loss = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.005,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=132.0423,
    )
    composite = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.01,
        precursor_tolerance_ppm=20.0,
        composition=CidNlComposition(
            tags=(("DNA_dR", 116.0474), ("RNA_R", 132.0423)),
            combine_mode="union",
        ),
    )

    assert cid_nl_alignment_subject_worksets(
        (first, overlapping, disconnected, different_loss, composite)
    ) == (
        (first, overlapping),
        (disconnected,),
        (different_loss,),
        (composite,),
    )
    # A confirmed identity's search envelope must be compared together before
    # partition dispatch, even if two resolver windows do not directly overlap.
    assert cid_nl_alignment_subject_worksets(
        (first, overlapping, disconnected, different_loss, composite),
        comparison_mz_intervals=((499.99, 500.06),),
    ) == (
        (first, overlapping, disconnected),
        (different_loss,),
        (composite,),
    )
    with pytest.raises(
        CidNlMethodSubjectContractError,
        match="not overlap-connected",
    ):
        validate_cid_nl_alignment_subject_workset((first, disconnected))
    with pytest.raises(
        CidNlMethodSubjectContractError,
        match="cannot cross configured neutral losses",
    ):
        validate_cid_nl_alignment_subject_workset((first, different_loss))
    with pytest.raises(
        CidNlMethodSubjectContractError,
        match="scalar Untargeted",
    ):
        validate_cid_nl_alignment_subject_workset((first, composite))
    with pytest.raises(
        CidNlMethodSubjectContractError,
        match="unique method subject identities",
    ):
        validate_cid_nl_alignment_subject_workset((first, first))


def test_completed_empty_recovery_preserves_each_compatible_source_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stems = ("SupportA1", "SupportA2", "SupportB1", "SupportB2", "Missing")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(f"compatible-source-subject:{raw_path.stem}".encode())
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=20.0
    )
    peak_config = _peak_config(tmp_path)
    source_raws = {}
    selected_candidates = {}
    for raw_path, precursor_mz in zip(
        raw_paths[:4], (500.0, 500.0, 500.0105, 500.0105), strict=True
    ):
        source_raw = _FakeRaw(
            (7.98,),
            scan_precursor_mz=precursor_mz,
            neutral_loss_das=(116.0474,),
        )
        candidates = _current_candidates(
            source_raw,
            raw_file=raw_path,
            settings=settings,
            peak_config=peak_config,
        )
        source_raws[raw_path] = source_raw
        selected_candidates[raw_path.stem] = candidates[0]
    missing_raw = _FakeRaw((7.98,))
    missing_raw.intensity[:] = 0.0
    source_raws[raw_paths[4]] = missing_raw
    candidates_by_sample = {stem: (selected_candidates[stem],) for stem in stems[:4]}
    candidates_by_sample["Missing"] = ()
    batch = build_dense_partition(
        raw_paths=raw_paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(stems, start=1)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:compatible-source-subject",
    )
    assert len(batch.subject_batch.subjects) == 2
    assert (
        len({subject.method_subject_id for subject in batch.subject_batch.subjects})
        == 2
    )
    _install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )
    alignment_config = AlignmentConfig(
        anchor_min_seed_events=1,
        product_mz_tolerance_ppm=30.0,
    )
    runtime = execute_dense_partition(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "compatible-source-subject.runtime.json",
            "compatible-source-subject",
        ),
        alignment_config=alignment_config,
    )
    assert runtime.succeeded
    completed = complete_dense_partition(
        batch,
        runtime,
        alignment_config=alignment_config,
    )

    (child,) = completed.application_result.children
    (group,) = child.scientific_core.final_population
    source_subject_ids = {
        source.method_assessment.content.method_subject.method_subject_id
        for cell in child.scientific_core.cell_observations
        for source in cell.predecessor_cells
        if source.content.method_result == "SUPPORT"
    }
    assert source_subject_ids == {
        subject.method_subject_id for subject in batch.subject_batch.subjects
    }
    position = next(
        position
        for position in (
            child.scientific_core.cell_observation_handoff.coverage.epoch.positions
        )
        if position.sample.metadata.raw_stem == "Missing"
        and position.group_identity.group_hypothesis_id == group.group_hypothesis_id
    )
    assert position.conservative_no_request_proof is not None
    assert position.conservative_no_request_proof.variant == "completed_backfill_empty"


@pytest.mark.parametrize(
    ("masses", "losses", "ppm", "centers"),
    (
        ((500.0, 500.004), (116.0474, 116.0474), 20.0, (500.0, 500.0)),
        ((500.0, 500.02), (116.0474, 116.0474), 20.0, (500.0, 500.02)),
        (
            (500.0, 500.008, 500.016),
            (116.0474,) * 3,
            20.0,
            (500.0, 500.0, 500.016),
        ),
        (
            (512.0, 512.0078125),
            (116.0474, 116.0474),
            15.2587890625,
            (512.0, 512.0),
        ),
        ((500.0, 500.0), (116.0474, 132.0423), 20.0, (500.0, 500.0)),
    ),
    ids=("nearby", "outside", "fixed-anchor", "inclusive-bound", "scalar-losses"),
)
def test_production_batch_preserves_bounded_subject_membership(
    tmp_path: Path, masses, losses, ppm, centers
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=ppm
    )
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )

    candidates = tuple(
        replace(
            candidates[0],
            candidate_id=f"{raw_path.stem}:membership-{index}",
            best_ms2_scan_id=index + 100,
            precursor_mz=mz,
            product_mz=mz - loss,
            configured_neutral_loss_da=loss,
        )
        for index, (mz, loss) in enumerate(zip(masses, losses, strict=True))
    )
    arguments = dict(
        raw_paths=(raw_path,),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="study_sample",
            ),
        ),
        settings=settings,
        peak_config=peak_config,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-batch",
    )

    batch = build_dense_partition(
        **arguments, candidates_by_sample={"S1": candidates}
    )
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject,
    )

    expected_memberships = tuple(
        sorted(
            (
                candidate.candidate_id,
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=center,
                    precursor_tolerance_ppm=ppm,
                    neutral_loss_da=loss,
                ).method_subject_id,
            )
            for candidate, center, loss in zip(candidates, centers, losses, strict=True)
        )
    )
    assert batch.subject_batch.candidate_memberships == expected_memberships
    assert len(batch.subject_batch.subjects) == len(
        set(zip(centers, losses, strict=True))
    )
    permuted = build_dense_partition(
        **arguments, candidates_by_sample={"S1": tuple(reversed(candidates))}
    )
    assert (
        permuted.subject_batch.request_payload()
        == batch.subject_batch.request_payload()
    )
    assert tuple(candidate.precursor_mz for candidate in candidates) == masses
    assert tuple(job.method_subject for job in batch.jobs) == (
        batch.subject_batch.subjects
    )
    assert all(
        job.candidates is batch.context.candidates_by_sample["S1"] for job in batch.jobs
    )
    _assert_jobs_match_direct_construction(batch)


def _source_verification_inputs(tmp_path: Path):
    path = tmp_path / "S1.synthetic"
    path.write_bytes(b"source-verification:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    raw, candidates = _nearby_subject_candidates(
        path, settings=settings, peak_config=peak_config
    )
    return (
        path,
        raw,
        dict(
            raw_paths=(path,),
            sample_metadata=(
                SampleMetadata(
                    sample_name="S1",
                    raw_stem="S1",
                    injection_order=1,
                    sample_role="study_sample",
                ),
            ),
            settings=settings,
            peak_config=peak_config,
            candidates_by_sample={"S1": candidates},
            discovery_receipts_by_sample=_discovery_receipts(
                tmp_path, (path,), {"S1": candidates}
            ),
            dll_dir=tmp_path,
            sample_identity_namespace="study:source-verification",
        ),
    )


def test_production_reuses_source_verification_across_runtime_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, raw, arguments = _source_verification_inputs(tmp_path)
    fingerprints = []
    fingerprint = identity_owner.fingerprint_file_content

    def count_fingerprint(source, **kwargs):
        fingerprints.append(source)
        return fingerprint(source, **kwargs)

    monkeypatch.setattr(identity_owner, "fingerprint_file_content", count_fingerprint)
    monkeypatch.setattr(raw_reader_owner, "fingerprint_file_content", count_fingerprint)
    plan = prepare_eager_untargeted_plan(**arguments)
    opened = _install_runtime_raws(
        monkeypatch, sources_by_path={path: raw}, tmp_path=tmp_path
    )
    marker = OwnerPathRuntimeMarker(tmp_path / "runtime.json", "source")
    execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "out",
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=marker,
        alignment_config=AlignmentConfig(),
        max_partition_jobs=1,
    )
    snapshot = marker.read_optional()
    assert snapshot is not None
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert len(opened[path]) == len(plan.subject_batch.subjects) > 1
    assert fingerprints == [path]


def test_production_rejects_source_change_during_expectation_mint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _raw, arguments = _source_verification_inputs(tmp_path)
    build_source = input_owner.build_acquisition_source_identity

    def change_after_fingerprint(source_path, **kwargs):
        source = build_source(source_path, **kwargs)
        path.write_bytes(b"changed-after-fingerprint")
        return source

    monkeypatch.setattr(
        input_owner, "build_acquisition_source_identity", change_after_fingerprint
    )
    with pytest.raises(identity_owner.IdentityContractError, match="changed during"):
        prepare_eager_untargeted_plan(**arguments)


def test_reused_run_spec_source_rejects_change_before_plan(
    tmp_path: Path,
) -> None:
    path, _raw, arguments = _source_verification_inputs(tmp_path)
    _spec, expectations = compile_discovery_processing_run_spec_with_expectations(
        (path,),
        discovery_settings=arguments["settings"],
    )
    path.write_bytes(b"changed-between-preparation-owners")

    with pytest.raises(identity_owner.IdentityContractError, match="changed after"):
        prepare_eager_untargeted_plan(
            **arguments,
            acquisition_source_expectations=expectations,
        )


def test_run_spec_expectation_rejects_change_after_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _raw, arguments = _source_verification_inputs(tmp_path)
    build_source = processing_spec_adapter.build_acquisition_source_identity

    def change_after_fingerprint(source_path, **kwargs):
        source = build_source(source_path, **kwargs)
        path.write_bytes(b"changed-during-run-spec-preparation")
        return source

    monkeypatch.setattr(
        processing_spec_adapter,
        "build_acquisition_source_identity",
        change_after_fingerprint,
    )
    with pytest.raises(identity_owner.IdentityContractError, match="changed during"):
        compile_discovery_processing_run_spec_with_expectations(
            (path,),
            discovery_settings=arguments["settings"],
        )


@pytest.mark.parametrize("stems", [("B", "A"), ("a_sample", "B_sample"), ("A", "B")])
def test_reused_run_spec_source_preserves_plan_request_and_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stems: tuple[str, str],
) -> None:
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    candidates = {}
    for path in paths:
        path.write_bytes(f"source-verification:{path.stem}".encode())
        _, candidates[path.stem] = _nearby_subject_candidates(
            path, settings=settings, peak_config=peak_config
        )
    arguments = dict(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(stems, 1)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:source-verification",
    )
    baseline = prepare_eager_untargeted_plan(**arguments)
    spec, expectations = compile_discovery_processing_run_spec_with_expectations(
        paths,
        discovery_settings=settings,
    )
    fingerprints = []
    fingerprint = identity_owner.fingerprint_file_content

    def count_fingerprint(source, **kwargs):
        fingerprints.append(source)
        return fingerprint(source, **kwargs)

    monkeypatch.setattr(identity_owner, "fingerprint_file_content", count_fingerprint)
    monkeypatch.setattr(raw_reader_owner, "fingerprint_file_content", count_fingerprint)
    reused = prepare_eager_untargeted_plan(
        **arguments,
        acquisition_source_expectations=expectations,
    )
    assert fingerprints == []
    for path, expectation, acquisition in zip(
        paths, expectations, spec.acquisition_inputs, strict=True
    ):
        assert acquisition.acquisition_source == expectation.source
        assert reused.context.acquisition_sources_by_sample[path.stem] is expectation
        assert reused.context.raw_paths_by_sample[path.stem] == path
    assert reused.subject_batch == baseline.subject_batch
    assert reused.context.processing_owners.request == (
        baseline.context.processing_owners.request
    )
    assert reused.context.candidates_by_sample == baseline.context.candidates_by_sample
    assert reused.excluded_candidates == baseline.excluded_candidates


@pytest.mark.parametrize("change", ["edit", "replace"])
def test_production_rejects_source_change_after_input_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    path, raw, arguments = _source_verification_inputs(tmp_path)
    plan = prepare_eager_untargeted_plan(**arguments)
    if change == "edit":
        path.write_bytes(b"changed-before-runtime")
    else:
        replacement = tmp_path / "replacement.synthetic"
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
    _install_runtime_raws(monkeypatch, sources_by_path={path: raw}, tmp_path=tmp_path)
    marker = OwnerPathRuntimeMarker(tmp_path / "runtime.json", "source")
    with pytest.raises(RuntimeError, match="failed before batch publication"):
        execute_untargeted_plan(
            plan,
            output_dir=tmp_path / "failed-output",
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            alignment_config=AlignmentConfig(),
        )
    snapshot = marker.read_optional()
    assert snapshot is not None
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.FAILED


def test_sample_job_builder_rejects_changed_request_bound_inputs(
    tmp_path: Path,
) -> None:
    from tests.processing_identity_no_raw_fixture import (
        build_processing_identity_no_raw_fixture,
    )
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject,
    )

    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path, settings=settings, peak_config=peak_config
    )
    batch = build_dense_partition(
        raw_paths=(raw_path,),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="study_sample",
            ),
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample={"S1": candidates},
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path, (raw_path,), {"S1": candidates}
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-batch",
    )
    first = batch.jobs[0]
    shared = {
        item.name: getattr(first, item.name)
        for item in fields(first)
        if item.init and item.name not in {"method_subject", "work_unit_key"}
    }
    work_units = tuple((job.method_subject, job.work_unit_key) for job in batch.jobs)
    assert len(work_units) == 2
    assert (
        build_sample_jobs(
            **shared, subject_work_units=work_units
        )
        == batch.jobs
    )
    other_dir = tmp_path / "other-request"
    other_dir.mkdir()
    other = build_processing_identity_no_raw_fixture(other_dir, sample_stems=("Other",))
    changed_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=600.0,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=116.0474,
    )
    for subject, key, message in (
        (changed_subject, "changed-subject", "not an exact member"),
        (None, "changed-subject", "wrong owner type"),
        (work_units[1][0], "", "work_unit_key"),
    ):
        with pytest.raises(UntargetedPartitionError, match=message):
            build_sample_jobs(
                **shared, subject_work_units=(work_units[0], (subject, key))
            )
    for changed, message in (
        ({"processing_owners": other.owners}, "supported real-data runtime contract"),
        ({"sample": other.sample("Other")}, "candidate universe"),
        ({"settings": replace(settings, rt_min=7.0)}, "Discovery settings"),
        (
            {
                "peak_config": replace(
                    peak_config, ms1_morphology_smoothing_window_points=17
                )
            },
            "peak settings",
        ),
        (
            {"candidates": (replace(candidates[0], reason="changed"), *candidates[1:])},
            "candidate universe",
        ),
    ):
        with pytest.raises(UntargetedPartitionError, match=message):
            build_sample_jobs(
                **(shared | changed), subject_work_units=work_units
            )


def test_alignment_adapter_prepares_and_executes_bounded_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "S1.raw"
    raw_path.write_bytes(b"alignment-adapter:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    receipts = _discovery_receipts(
        tmp_path,
        (raw_path,),
        {"S1": candidates},
    )
    discovery_batch = DiscoveryBatchInput(
        sample_order=("S1",),
        candidate_csvs={"S1": tmp_path / "S1.csv"},
        raw_files={"S1": raw_path},
        review_csvs={"S1": None},
        candidate_receipts=receipts,
    )
    built = object()
    completed = object()
    calls: list[tuple[str, object]] = []

    def build(**kwargs: object) -> object:
        calls.append(("build", kwargs))
        return built

    def execute(batch: object, **kwargs: object) -> object:
        assert batch is built
        calls.append(("execute", kwargs))
        return completed

    monkeypatch.setattr(
        alignment_adapter,
        "prepare_eager_untargeted_plan",
        build,
    )
    monkeypatch.setattr(
        alignment_adapter,
        "execute_untargeted_plan",
        execute,
    )

    result = alignment_adapter.complete_untargeted_alignment(
        discovery_batch=discovery_batch,
        candidates_by_sample={"S1": candidates},
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=peak_config,
        alignment_config=AlignmentConfig(),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=7,
                sample_role="blank",
                excluded=True,
                exclusion_reason="operator exclusion",
            ),
        ),
    )

    assert result is completed
    assert [name for name, _kwargs in calls] == ["build", "execute"]
    build_kwargs = calls[0][1]
    assert isinstance(build_kwargs, dict)
    assert build_kwargs["raw_paths"] == (raw_path,)
    assert build_kwargs["candidates_by_sample"] == {"S1": candidates}
    assert build_kwargs["discovery_receipts_by_sample"] == receipts
    sample_metadata = build_kwargs["sample_metadata"]
    assert isinstance(sample_metadata, tuple)
    assert tuple(row.raw_stem for row in sample_metadata) == ("S1",)
    assert tuple(row.injection_order for row in sample_metadata) == (7,)
    assert tuple(row.sample_role for row in sample_metadata) == ("blank",)
    assert tuple(row.excluded for row in sample_metadata) == (True,)


def test_production_batch_rejects_non_single_provider_settings(tmp_path: Path) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    crossed_settings = type(settings)(
        neutral_loss_profiles=(
            settings.neutral_loss_profiles[0],
            settings.neutral_loss_profiles[0],
        ),
        selected_tag_names=settings.selected_tag_names,
        tag_combine_mode="intersection",
        nl_tolerance_ppm=settings.nl_tolerance_ppm,
        precursor_mz_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
    )

    try:
        build_dense_partition(
            raw_paths=(raw_path,),
            sample_metadata=(SampleMetadata(sample_name="S1", raw_stem="S1"),),
            settings=crossed_settings,
            peak_config=peak_config,
            candidates_by_sample={"S1": candidates},
            discovery_receipts_by_sample=_discovery_receipts(
                tmp_path,
                (raw_path,),
                {"S1": candidates},
            ),
            dll_dir=tmp_path,
            sample_identity_namespace="study:production-batch",
        )
    except ValueError as exc:
        assert "single-provider" in str(exc)
    else:
        raise AssertionError("duplicate tag settings must be rejected")


def test_production_batch_executes_one_atomic_sample_phase(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import xic_extractor.alignment.cell_decision.owner as cell_decision_owner

    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    # The atomic phase must contain two disjoint method worksets.
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    batch = build_dense_partition(
        raw_paths=(raw_path,),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="study_sample",
            ),
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample={"S1": candidates},
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-batch",
    )
    vendor_raw = _install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_path,
        tmp_path=tmp_path,
    )

    result = execute_dense_partition(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json",
            "production-batch",
        ),
    )

    assert result.succeeded
    assert len(result.sample_results) == len(batch.jobs) == 2
    assert vendor_raw.chromatogram_request_counts == [2]

    validated_batches = []
    validate_batch = application_result_owner._validate_untarget_batch_request

    def record_batch_validation(request):
        validate_batch(request)
        validated_batches.append(request)

    monkeypatch.setattr(
        application_result_owner,
        "_validate_untarget_batch_request",
        record_batch_validation,
    )
    projected_pre_cells = []
    validated_observation_populations = []
    project_pre_cell = cell_decision_owner.project_untarget_pre_cell
    validate_observation_population = (
        cell_decision_owner.validate_final_group_cell_observation_population
    )

    def record_pre_cell_projection(finalized_alignment):
        projected_pre_cells.append(finalized_alignment)
        return project_pre_cell(finalized_alignment)

    def record_observation_population_validation(**kwargs):
        validated_observation_populations.append(kwargs)
        return validate_observation_population(**kwargs)

    monkeypatch.setattr(
        cell_decision_owner,
        "project_untarget_pre_cell",
        record_pre_cell_projection,
    )
    monkeypatch.setattr(
        cell_decision_owner,
        "validate_final_group_cell_observation_population",
        record_observation_population_validation,
    )
    alignment_config = AlignmentConfig(anchor_min_seed_events=1)
    completed = complete_dense_partition(
        batch,
        result,
        alignment_config=alignment_config,
    )

    assert type(completed) is CompletedUntargetBatchWorkflowResult
    assert completed.application_result.candidate_ids == tuple(
        candidate_id
        for subject in batch.subject_batch.subjects
        for candidate_id, method_subject_id in (
            batch.subject_batch.candidate_memberships
        )
        if method_subject_id == subject.method_subject_id
    )
    writer_inputs = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    assert writer_inputs.matrix.sample_order == ("S1",)
    assert len(writer_inputs.matrix.clusters) == 2
    assert len(writer_inputs.matrix.cells) == 2
    assert tuple(row.public_family_id for row in writer_inputs.matrix.clusters) == (
        "FAM000001",
        "FAM000002",
    )
    expected_source_ids: dict[tuple[str, str], str] = {}
    for child in completed.application_result.children:
        candidate_by_hypothesis = {
            (
                subject.canonical_sample_identity_id,
                subject.hypothesis.hypothesis_id,
            ): subject.candidate_id
            for subject in child.scientific_core.terminal_subjects
            if type(subject) is ObservedSearchSubject
        }
        for projected in child.completed_projection.rows:
            for decision in projected.decisions:
                hypothesis = decision.evidence.direct_hypotheses[0]
                expected_source_ids[
                    (
                        projected.row_key.untarget_row_key_id,
                        decision.subject.canonical_sample.metadata.raw_stem,
                    )
                ] = candidate_by_hypothesis[
                    (
                        decision.subject.canonical_sample.identity.sample_identity_id,
                        hypothesis.hypothesis_id,
                    )
                ]
    assert {
        (cell.cluster_id, cell.sample_stem): cell.source_candidate_id
        for cell in writer_inputs.matrix.cells
    } == expected_source_ids
    assert all(cell.group_hypothesis_id for cell in writer_inputs.matrix.cells)
    assert all(
        writer_inputs.production_decisions.cell(
            cell.cluster_id,
            cell.sample_stem,
        ).matrix_value
        is not None
        for cell in writer_inputs.matrix.cells
    )
    assert projected_pre_cells == []
    assert validated_observation_populations == []
    writer_dir = tmp_path / "writer"
    outputs = output_paths(
        writer_dir,
        output_level="machine",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    write_outputs_atomic(
        outputs,
        writer_inputs.matrix,
        metadata=alignment_metadata(
            discovery_batch_index=tmp_path / "discovery_batch_index.csv",
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            owner_build_xic_backend="raw",
            owner_backfill_xic_backend="raw",
            output_level="machine",
            peak_config=peak_config,
            audit_evidence_mode="none",
            heavy_audit_enabled=False,
            require_discovery_owner_closure=True,
            owner_backfill_peak_fact_provenance_row_count=1,
        ),
        alignment_config=alignment_config,
        production_decisions=writer_inputs.production_decisions,
    )
    assert {path.name for path in writer_dir.iterdir() if path.is_file()} == {
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
    }
    # Full owner validation is cohort-sized; sealing the same batch must reuse it.
    assert len(validated_batches) == 1
    _assert_partition_composition_parity(
        batch,
        result,
        completed,
        alignment_config=alignment_config,
    )


@pytest.mark.parametrize(
    "scenario",
    (
        "mixed",
        "all_hold",
        "hold_no_input",
        "support_no_input",
        "all_reject",
        "hold_reject",
        "other_support",
        "ambiguous_hold_below_threshold",
        "ambiguous_hold_above_threshold",
    ),
)
def test_production_composition_closes_observation_only_hold_without_membership(
    tmp_path: Path,
    monkeypatch,
    scenario: str,
) -> None:
    above_threshold = scenario == "ambiguous_hold_above_threshold"
    ambiguous = scenario.startswith("ambiguous_hold_")
    stems = ("Support", "Hold", "Support2") if above_threshold else ("Support", "Hold")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(f"production-hold:{raw_path.stem}".encode())
    # The other-support case must keep its independently rejected workset.
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)

    def observation_only_raw(peak_rts: tuple[float, ...]) -> _FakeRaw:
        # MS1 stays usable, but each NL scan precedes its integration interval.
        # The narrow peak fits inside the scan-centered discovery query.
        raw = _FakeRaw(tuple(rt - 0.15 for rt in peak_rts))
        raw.intensity[:] = [
            sum(
                1000.0 * math.exp(-0.5 * ((rt - center) / 0.015) ** 2)
                for center in peak_rts
            )
            for rt in raw.rt
        ]
        return raw

    source_raws = {raw_path: _FakeRaw((8.0,)) for raw_path in raw_paths}
    if scenario not in {"all_reject", "hold_reject", "other_support"}:
        source_raws[raw_paths[1]] = observation_only_raw(
            (7.8, 8.2) if ambiguous else (8.0,)
        )
    if scenario in {"all_hold", "hold_no_input", "hold_reject"}:
        source_raws[raw_paths[0]] = observation_only_raw((8.0,))
    rejected_samples = (
        ("Support", "Hold")
        if scenario == "all_reject"
        else ("Hold",)
        if scenario == "hold_reject"
        else ()
    )
    for raw_path, raw in source_raws.items():
        if raw_path.stem in rejected_samples:
            raw.intensity[:] = 0
    support = _current_candidates(
        source_raws[raw_paths[0]],
        raw_file=raw_paths[0],
        settings=settings,
        peak_config=peak_config,
    )[0]
    direct_hold_candidates = _current_candidates(
        source_raws[raw_paths[1]],
        raw_file=raw_paths[1],
        settings=settings,
        peak_config=peak_config,
    )
    direct_hold = direct_hold_candidates[0]
    candidates_by_sample: dict[str, tuple[DiscoveryCandidate, ...]] = {
        "Support": (support,),
        "Hold": (direct_hold,),
    }
    if scenario in {"hold_no_input", "support_no_input"}:
        candidates_by_sample["Hold"] = ()
    if ambiguous:
        candidates_by_sample["Hold"] = direct_hold_candidates
    if scenario in {"all_reject", "hold_reject"}:
        for stem in rejected_samples:
            candidate = support if stem == "Support" else direct_hold
            candidates_by_sample[stem] = (candidate,)
    if scenario == "other_support":
        for raw_path in raw_paths:
            first_raw = observation_only_raw((7.98,))
            second_raw = _FakeRaw(
                (7.98,),
                scan_precursor_mz=500.004,
                neutral_loss_das=(116.0474,),
            )
            second_raw.intensity = first_raw.intensity.copy()
            nearby = tuple(
                _current_candidates(
                    raw,
                    raw_file=raw_path,
                    settings=settings,
                    peak_config=peak_config,
                )[0]
                for raw in (first_raw, second_raw)
            )
            source_raw = second_raw
            source_raw.scans = (first_raw.scans[0], second_raw.scans[0])
            source_raws[raw_path] = source_raw
            first, second = sorted(nearby, key=lambda item: item.precursor_mz)
            candidates_by_sample[raw_path.stem] = (first, second)
    if above_threshold:
        candidates_by_sample["Support2"] = _current_candidates(
            source_raws[raw_paths[2]],
            raw_file=raw_paths[2],
            settings=settings,
            peak_config=peak_config,
        )
    batch = build_dense_partition(
        raw_paths=raw_paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(stems, start=1)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-hold",
    )
    opened = _install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )

    runtime_result = execute_dense_partition(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "hold.runtime.json",
            "production-hold",
        ),
    )
    hold_observation_ids = tuple(
        peak.content.peak_observation_content_id
        for result in runtime_result.sample_results
        for peak in result.effective_assessment.candidate_peaks
        if result.effective_assessment.content.method_result == "HOLD"
    )
    assert runtime_result.succeeded
    assert (
        len(hold_observation_ids)
        == {
            "mixed": 1,
            "all_hold": 2,
            "hold_no_input": 1,
            "support_no_input": 0,
            "all_reject": 0,
            "hold_reject": 1,
            "other_support": 2,
            "ambiguous_hold_below_threshold": 2,
            "ambiguous_hold_above_threshold": 2,
        }[scenario]
    )
    if scenario in {"hold_no_input", "support_no_input"}:
        assessments_by_stem = {
            result.job.sample.metadata.raw_stem: result.effective_assessment
            for result in runtime_result.sample_results
        }
        assert assessments_by_stem["Support"].content.technical_state == "evaluated"
        assert assessments_by_stem["Support"].content.method_result == (
            "HOLD" if scenario == "hold_no_input" else "SUPPORT"
        )
        no_input = assessments_by_stem["Hold"].content
        assert no_input.technical_state == "not_evaluated"
        assert no_input.method_result is None
        assert no_input.reason_codes == ("no_search_eligible_discovery_input",)
        assert no_input.evaluation_receipt.search_completed is False

    opens_before_composition = sum(map(len, opened.values()))
    completed = complete_dense_partition(
        batch,
        runtime_result,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )
    _assert_partition_composition_parity(
        batch,
        runtime_result,
        completed,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )

    assert not set(hold_observation_ids) & set(
        completed.application_result.physical_observation_ids
    )
    writer_inputs = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    assert writer_inputs.matrix.sample_order == stems
    if scenario == "hold_no_input":
        assert completed.application_result.children == ()
        assert writer_inputs.matrix.clusters == ()
        assert writer_inputs.matrix.cells == ()
        assert len(completed.application_result.excluded_candidates) == 1
    if scenario == "support_no_input":
        assert completed.application_result.excluded_candidates == ()
        (child,) = completed.application_result.children
        assert len(child.scientific_core.final_population) == 1
        assert len(writer_inputs.matrix.clusters) == 1
        cells = {
            cell.canonical_sample.metadata.raw_stem: cell
            for cell in child.scientific_core.cell_observations
        }
        assert cells["Support"].integrations
        assert cells["Hold"].evidence_state == "not_searched"
        assert cells["Hold"].predecessor_cells == ()
        assert cells["Hold"].method_assessments == ()
        assert cells["Hold"].integrations == ()
        (association,) = cells["Hold"].position_associations
        assert association.position.planning_reason == "no_discovery_input"
        assert association.position.primary_logical_requests == ()
        for decision in writer_inputs.production_decisions.cells.values():
            assert decision.write_matrix_value is (decision.sample_stem == "Support")
            assert (decision.matrix_value is None) is (decision.sample_stem == "Hold")
        return
    if scenario == "mixed":
        (child,) = completed.application_result.children
        (group,) = child.scientific_core.final_population
        member_ids = {
            physical_id
            for member in group.member_subjects
            for physical_id in member.physical_observation_ids
        }
        retained_observations = {
            observation.content.peak_observation_content_id: observation
            for cell in child.scientific_core.cell_observations
            for observation in cell.direct_observations
        }
        assert set(hold_observation_ids) <= set(retained_observations)
        assert not set(hold_observation_ids) & member_ids
        (row,) = writer_inputs.matrix.clusters
        assert row.physical_observation_ids == tuple(sorted(member_ids))
        member_observations = tuple(
            retained_observations[physical_id] for physical_id in sorted(member_ids)
        )
        assert row.family_center_mz == pytest.approx(
            sum(observation.content.mz for observation in member_observations)
            / len(member_observations)
        )
        assert row.family_center_rt == pytest.approx(
            sum(
                observation.content.integration.rt_apex_min
                for observation in member_observations
            )
            / len(member_observations)
        )
    if above_threshold:
        from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

        (child,) = completed.application_result.children
        scientific = child.scientific_core
        assert {subject.candidate_id for subject in scientific.terminal_subjects} == {
            candidate.candidate_id
            for values in candidates_by_sample.values()
            for candidate in values
        }
        hold_cell = next(
            cell
            for cell in scientific.cell_observations
            if cell.canonical_sample.metadata.raw_stem == "Hold"
        )
        (source,) = hold_cell.predecessor_cells
        assessment = next(
            result.effective_assessment
            for result in runtime_result.sample_results
            if result.job.sample.metadata.raw_stem == "Hold"
        )
        assert source.method_assessment is assessment
        assert assessment.content.method_result == "HOLD"
        assert len(assessment.candidate_peaks) == 2
        assert source.peak_observation is source.peak_hypothesis is None
        assert hold_cell.repair_receipts == hold_cell.repaired_observations == ()
        position = next(
            item
            for item in scientific.cell_observation_handoff.coverage.epoch.positions
            if item.sample.metadata.raw_stem == "Hold"
        )
        assert position.conservative_no_request_proof is None
        assert position.primary_logical_requests == ()
        assert all(
            not result.backfill_attempts for result in runtime_result.sample_results
        )
        assert sum(map(len, opened.values())) == opens_before_composition == 3
        cells = {cell.sample_stem: cell for cell in writer_inputs.matrix.cells}
        assert cells["Hold"].status == "ambiguous_ms1_owner"
        for stem in stems:
            decision = writer_inputs.production_decisions.cell(
                cells[stem].cluster_id, stem
            )
            assert decision.write_matrix_value is (stem != "Hold")
            assert (decision.matrix_value is None) is (stem == "Hold")
        matrix_path = tmp_path / "qualified-ambiguous-hold.tsv"
        write_alignment_matrix_tsv(
            matrix_path,
            writer_inputs.matrix,
            production_decisions=writer_inputs.production_decisions,
        )
        with matrix_path.open(encoding="utf-8", newline="") as handle:
            (row,) = tuple(csv.DictReader(handle, delimiter="\t"))
        assert row["Hold"] == ""
        assert float(row["Support"]) > 0 and float(row["Support2"]) > 0
        return
    if scenario == "ambiguous_hold_below_threshold":
        from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

        assert all(
            not result.backfill_attempts for result in runtime_result.sample_results
        )
        assert sum(map(len, opened.values())) == opens_before_composition == 2
        (child,) = completed.application_result.children
        scientific = child.scientific_core
        handoff = scientific.cell_observation_handoff
        coverage = handoff.coverage
        position = next(
            item
            for item in coverage.epoch.positions
            if item.sample.metadata.raw_stem == "Hold"
        )
        proof = position.conservative_no_request_proof
        assert proof.variant == "threshold_not_met"
        assert proof.completion is None
        assert proof.decision.group_facts.direct_support_count_s == 1
        assert proof.decision.group_facts.minimum_support_k == 2
        assert not proof.decision.group_facts.may_attempt_backfill
        assert proof.decision.reason_codes == ("direct_support_threshold_not_met",)
        assert position.primary_logical_requests == coverage.terminals == ()
        hold_cell = next(
            cell
            for cell in scientific.cell_observations
            if cell.canonical_sample.metadata.raw_stem == "Hold"
        )
        assert hold_cell.evidence_states == ("not_searched",)
        assert hold_cell.repair_receipts == hold_cell.repaired_observations == ()
        (source,) = hold_cell.predecessor_cells
        assert source is proof.decision.target_cell
        assert source.content.method_result == "HOLD"
        assert len(source.method_assessment.candidate_peaks) == 2
        assert source.method_assessment is next(
            result.adapter_result.assessment_input.assessment
            for result in runtime_result.sample_results
            if result.job.sample.metadata.raw_stem == "Hold"
        )
        assert source.peak_observation is source.peak_hypothesis is None
        assert all(
            not item.write_matrix_value and item.matrix_value is None
            for item in writer_inputs.production_decisions.cells.values()
            if item.sample_stem == "Hold"
        )
        matrix_path = tmp_path / "ambiguous-hold-matrix.tsv"
        write_alignment_matrix_tsv(
            matrix_path,
            writer_inputs.matrix,
            production_decisions=writer_inputs.production_decisions,
        )
        with matrix_path.open(encoding="utf-8", newline="") as handle:
            assert all(
                row["Hold"] == "" for row in csv.DictReader(handle, delimiter="\t")
            )
        _assert_conservative_proof_seals(
            proof, position, coverage, handoff, hold_cell, batch, runtime_result
        )
        return
    if scenario != "mixed":
        _assert_single_unsupported_exclusion(
            completed,
            runtime_result,
            batch,
            tmp_path,
            populated=scenario == "other_support",
        )
        return
    assert len(writer_inputs.matrix.clusters) == 1
    assert tuple(cell.sample_stem for cell in writer_inputs.matrix.cells) == (
        "Support",
        "Hold",
    )
    decisions = writer_inputs.production_decisions
    cluster_id = writer_inputs.matrix.cells[0].cluster_id
    assert decisions.cell(cluster_id, "Support").write_matrix_value is True
    assert decisions.cell(cluster_id, "Hold").write_matrix_value is False
    assert decisions.cell(cluster_id, "Hold").blank_reason != ""


@pytest.mark.parametrize("supported_samples", [2, 3])
def test_production_initial_groups_use_complete_feature_inference_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supported_samples: int,
) -> None:
    from xic_extractor.untargeted_execution import partition_composition as production

    stems = ("Early", "Middle", "Late")
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    settings = replace(_settings(), neutral_loss_profile=None)
    peak_config = _peak_config(tmp_path)
    raws = {path: _FakeRaw((8.0 + index * 0.1,)) for index, path in enumerate(paths)}
    candidates = {}
    for index, path in enumerate(paths):
        path.write_bytes(f"canonical-batch:{path.stem}".encode())
        if index >= supported_samples:
            raws[path].intensity[:] = 0
        candidates[path.stem] = _current_candidates(
            raws[path], raw_file=path, settings=settings, peak_config=peak_config
        )
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(stems, start=1)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:canonical-batch",
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    initial_contexts = []
    initial_edges = []
    cell_contexts = []
    build_context = production.build_initial_cid_nl_cross_sample_groups
    compose_cells = cid_nl_completion_owner.compose_cid_nl_predecessor_cells

    def retain_context(**kwargs):
        result = build_context(**kwargs)
        initial_contexts.append(result[0])
        return result

    monkeypatch.setattr(
        production, "build_initial_cid_nl_cross_sample_groups", retain_context
    )

    def retain_cells(**kwargs):
        cells = compose_cells(**kwargs)
        cell_contexts.extend(cell.group_context for cell in cells)
        return cells

    monkeypatch.setattr(
        cid_nl_completion_owner, "compose_cid_nl_predecessor_cells", retain_cells
    )
    progress_events = []
    runtime = execute_dense_partition(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "canonical-batch.runtime.json", "canonical-batch"
        ),
        alignment_config=AlignmentConfig(),
        progress_callback=progress_events.append,
    )
    assert runtime.succeeded
    membership_events = [
        event
        for event in progress_events
        if event.get("event") == "feature_membership_inference"
    ]
    assert len(membership_events) == 1
    membership_counts = membership_events[0]["operation_counts"]
    assert membership_events[0]["subject_count"] == 1
    assert membership_events[0]["group_count"] == 1
    assert membership_counts["input_owner_rows"] == supported_samples
    assert membership_counts["unique_owners"] == supported_samples
    assert membership_counts["owner_edge_evaluations"] > 0
    complete_dense_partition(
        batch, runtime, alignment_config=AlignmentConfig()
    )
    (context,) = initial_contexts
    (group,) = context.groups
    assert {owner.sample_stem for owner in group.owners} == set(
        stems[:supported_samples]
    )
    assert (
        group.inference_basis is not None
        and group.inference_basis.disposition == "assigned"
        and group.inference_basis.basis == "bidirectional_membership_agreement"
        and group.inference_basis.reason
        == "both_finite_directional_passes_support_the_same_membership"
    )
    assert initial_edges == []
    assert cell_contexts
    for cell_context in cell_contexts:
        assert cell_context.sample_universe == frozenset(stems)
        assert dict(cell_context.injection_order) == dict(zip(stems, (1, 2, 3)))
        if supported_samples == 2:
            for order in (None, tuple((stem, 1) for stem in stems)):
                rebound = replace(cell_context, injection_order=order)
                assert rebound.group is cell_context.group
                assert rebound.supports is cell_context.supports
        else:
            rebound = replace(cell_context, injection_order=None)
            assert rebound.group is cell_context.group
            assert rebound.supports is cell_context.supports


def test_known_istd_reference_closes_drifting_group_and_cell_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpyxl import Workbook

    from xic_extractor.acquisition.istd_reference import read_known_istd_reference

    paths = tuple(tmp_path / f"S{i}.synthetic" for i in range(2))
    precursor_mzs = (500.0, 500.03)
    configured_loss = 116.0474
    observed_losses = (116.0474, 116.0474005)
    settings = replace(
        _settings(),
        neutral_loss_profile=None,
        precursor_mz_tolerance_ppm=20.0,
        rt_max=10,
    )
    peak_config = _peak_config(tmp_path)
    raws = {
        path: _FakeRaw(
            (8.0,),
            scan_precursor_mz=precursor_mz,
            neutral_loss_das=(observed_loss,),
        )
        for path, precursor_mz, observed_loss in zip(
            paths, precursor_mzs, observed_losses, strict=True
        )
    }
    for i, raw in enumerate(raws.values()):
        raw.rt += i * 1.067
        raw.intensity *= 1.0 + i * 0.4
        raw.scans = tuple(replace(scan, rt=scan.rt + i * 1.067) for scan in raw.scans)
    candidates = {}
    for path, raw in zip(paths, raws.values(), strict=True):
        path.write_bytes(path.stem.encode())
        candidates[path.stem] = _current_candidates(
            raw,
            raw_file=path,
            settings=settings,
            peak_config=peak_config,
        )
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=p.stem,
                raw_stem=p.stem,
                injection_order=i + 1,
                sample_role="study_sample",
            )
            for i, p in enumerate(paths)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:known-istd",
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime_plain = execute_dense_partition(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime-plain.json", "plain"
        ),
        alignment_config=AlignmentConfig(),
    )
    assert runtime_plain.succeeded
    workbook = Workbook()
    targets = workbook.active
    targets.title = "Targets"
    targets.append(
        [
            "Label",
            "Role",
            "m/z",
            "RT min",
            "RT max",
            "ppm tol",
            "NL (Da)",
            "NL ppm warn",
            "NL ppm max",
            "ISTD Pair",
        ]
    )
    targets.append(["known", "ISTD", 500.015, 7, 10, 40, 116.0474, 20, 50, ""])
    results = workbook.create_sheet("XIC Results")
    results.append(["SampleName", "Target", "Role", "RT", "Area"])
    for i, path in enumerate(paths):
        results.append([path.stem, "known", "ISTD", 8.0 + i * 1.067, -999])
    reference_path = tmp_path / "reference.xlsx"
    workbook.save(reference_path)
    reference = read_known_istd_reference(reference_path)
    config = AlignmentConfig()
    runtime = execute_dense_partition(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "runtime.json", "reference"),
        alignment_config=config,
        known_istd_reference=reference,
    )
    assert runtime.succeeded
    assert tuple(
        sorted(subject.neutral_loss_da for subject in batch.subject_batch.subjects)
    ) == (configured_loss, configured_loss)
    plain = complete_dense_partition(
        batch,
        runtime_plain,
        alignment_config=AlignmentConfig(),
    )
    corrected = complete_dense_partition(
        batch,
        runtime,
        alignment_config=config,
        known_istd_reference=reference,
    )
    _assert_partition_composition_parity(
        batch,
        runtime,
        corrected,
        alignment_config=config,
        known_istd_reference=reference,
        subject_worksets=(batch.subject_batch.subjects,),
    )
    plain_matrix = project_completed_untarget_batch_writer_inputs(
        plain.application_result
    ).matrix
    corrected_matrix = project_completed_untarget_batch_writer_inputs(
        corrected.application_result
    ).matrix
    assert len(plain_matrix.clusters) == 2
    assert len(corrected_matrix.clusters) == 1
    assert {
        (cell.sample_stem, cell.apex_rt, cell.area) for cell in corrected_matrix.cells
    } == {
        (cell.sample_stem, cell.apex_rt, cell.area)
        for cell in plain_matrix.cells
        if cell.area is not None
    }
    assert all(
        cell.area is not None and cell.area > 0 for cell in corrected_matrix.cells
    )
    assert len({cell.area for cell in corrected_matrix.cells}) == len(paths)
    (child,) = corrected.application_result.children
    expected_loss_by_sample = {path.stem: configured_loss for path in paths}
    support_losses_by_sample = {
        cell.canonical_sample.metadata.raw_stem: {
            source.method_assessment.content.method_subject.neutral_loss_da
            for source in cell.predecessor_cells
            if source.content.method_result == "SUPPORT"
        }
        for cell in child.scientific_core.cell_observations
    }
    assert support_losses_by_sample == {
        sample: {loss} for sample, loss in expected_loss_by_sample.items()
    }
    contexts = tuple(
        source.group_context
        for cell in child.scientific_core.cell_observations
        for source in cell.predecessor_cells
    )
    assert contexts
    assert all(context.drift_lookup is contexts[0].drift_lookup for context in contexts)
    without_drift_lookup = replace(contexts[0], drift_lookup=None)
    assert without_drift_lookup.group is contexts[0].group
    assert without_drift_lookup.supports is contexts[0].supports


def _assert_conservative_coverage_handoff(
    completed, writer_inputs, batch, runtime_result, tmp_path, *, threshold: bool
) -> None:
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    (child,) = completed.application_result.children
    scientific = child.scientific_core
    handoff = scientific.cell_observation_handoff
    assert handoff is not None
    coverage = handoff.coverage
    (position,) = tuple(
        position
        for position in coverage.epoch.positions
        if position.sample.metadata.raw_stem == "Hold"
    )
    proof = position.conservative_no_request_proof
    if threshold:
        assert proof is None
        assert position.planning_reason == "no_discovery_input"
    else:
        assert proof is not None
        assert proof.variant == "completed_backfill_empty"
        assert proof.decision.group_facts.direct_support_count_s == 3
        assert proof.decision.group_facts.minimum_support_k == 2
    assert position.planning_state == "not_planned"
    assert position.primary_logical_requests == ()
    assert coverage.terminals == ()
    (hold_cell,) = tuple(
        cell
        for cell in scientific.cell_observations
        if cell.canonical_sample.metadata.raw_stem == "Hold"
    )
    assert hold_cell.evidence_states == (
        ("not_searched",) if threshold else ("unavailable",)
    )
    assert hold_cell.repair_receipts == hold_cell.repaired_observations == ()
    if threshold:
        assert hold_cell.predecessor_cells == ()
        assert hold_cell.method_assessments == ()
    else:
        (source,) = hold_cell.predecessor_cells
        assert source is proof.decision.target_cell
        assert source.method_assessment is next(
            result.effective_assessment
            for result in runtime_result.sample_results
            if result.job.sample is position.sample
        )
        assert source.content.method_result == "HOLD"
        assert source.content.technical_state == "evaluated"
        assert source.method_assessment.content.evaluation_receipt.search_completed
        assert source.peak_observation is source.peak_hypothesis is None
    hold_decisions = tuple(
        decision
        for decision in writer_inputs.production_decisions.cells.values()
        if decision.sample_stem == "Hold"
    )
    assert all(
        not decision.write_matrix_value and decision.matrix_value is None
        for decision in hold_decisions
    )
    matrix_path = tmp_path / "conservative-matrix.tsv"
    write_alignment_matrix_tsv(
        matrix_path,
        writer_inputs.matrix,
        production_decisions=writer_inputs.production_decisions,
    )
    with matrix_path.open(encoding="utf-8", newline="") as handle:
        matrix_rows = tuple(csv.DictReader(handle, delimiter="\t"))
    assert len(matrix_rows) == (0 if threshold else 1)
    assert all(row["Hold"] == "" for row in matrix_rows)
    if not threshold:
        assert all(
            float(matrix_rows[0][stem]) > 0
            for stem in ("Support1", "Support2", "Support3")
        )
    if proof is not None:
        _assert_conservative_proof_seals(
            proof, position, coverage, handoff, hold_cell, batch, runtime_result
        )


def _assert_conservative_proof_seals(
    proof, position, coverage, handoff, final_cell, batch, runtime_result
) -> None:
    import hashlib
    import json
    from copy import copy
    from dataclasses import fields

    from xic_extractor.alignment.backfill import evaluation_coverage as owner
    from xic_extractor.alignment.cell_observations import (
        _derive_lane_evidence_state,
        canonical_cell_observation_bytes,
    )
    from xic_extractor.processing_identity import issue_processing_attempt
    from xic_extractor.untargeted_execution.partition.models import (
        canonical_sample_result_bytes,
        project_completed_backfill,
    )

    # The group binding is shared across cell proofs; minting again must keep
    # identical persisted identities without revisiting all source assessments.
    with pytest.MonkeyPatch.context() as counted:
        bindings = []
        validate_binding = owner.validate_cid_nl_method_assessment_binding

        def record_binding(reference, **kwargs):
            bindings.append(reference)
            return validate_binding(reference, **kwargs)

        counted.setattr(
            owner, "validate_cid_nl_method_assessment_binding", record_binding
        )
        builder = owner.ConservativeNoRequestProofBuilder(
            processing_owners=proof.processing_owners,
            group_identity=proof.group_identity,
            group_facts=proof.decision.group_facts,
        )
        assert len(bindings) == len(proof.decision.group_facts.source_cells)
        for _ in range(3):
            rebuilt = builder.mint(
                sample=proof.sample,
                decision=proof.decision,
                completion=proof.completion,
            )
            assert rebuilt == proof
        assert len(bindings) == len(proof.decision.group_facts.source_cells)
        with pytest.raises(ValueError, match="bound group facts"):
            owner.mint_backfill_conservative_no_request_proof(
                processing_owners=proof.processing_owners,
                group_identity=proof.group_identity,
                sample=proof.sample,
                decision=proof.decision,
                completion=proof.completion,
                _group_binding=replace(builder, group_facts=copy(builder.group_facts)),
            )

    epoch = coverage.epoch
    epoch_args = {
        field.name: getattr(epoch, field.name)
        for field in fields(epoch)
        if field.name != "epoch_id"
    }
    basis_args = {
        key: value
        for key, value in epoch_args.items()
        if key not in {"processing_owners", "group_identities"}
    } | {
        "processing_request_id": epoch.processing_owners.request.processing_request_id,
        "groups": epoch.group_identities,
    }
    epoch_basis = owner._epoch_basis(**basis_args)
    assert epoch_basis["schema"] == "owner_backfill_planning_epoch"
    assert proof.proof_id in {
        row["conservative_no_request_proof_id"] for row in epoch_basis["positions"]
    }
    assert epoch.epoch_id == owner._typed_id(owner._EPOCH_PREFIX, epoch_basis)
    assert coverage.coverage_id == owner._typed_id(
        owner._BATCH_PREFIX,
        {
            "schema": "owner_backfill_evaluation_coverage",
            "epoch_id": epoch.epoch_id,
            "terminal_ids": [],
            "terminal_evaluations": [],
        },
    )
    assert json.loads(handoff.basis_bytes)["coverage_id"] == coverage.coverage_id
    assert handoff.handoff_id.endswith(hashlib.sha256(handoff.basis_bytes).hexdigest())
    assert json.loads(final_cell.basis_bytes)["handoff_id"] == handoff.handoff_id
    assert final_cell.cell_observation_id.endswith(
        hashlib.sha256(final_cell.basis_bytes).hexdigest()
    )
    assert (
        json.loads(
            canonical_cell_observation_bytes(proof.decision.target_cell.content)
        )["schema_version"]
        == "cell_observation"
    )

    def canonical(value):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()

    other_sample = next(
        sample for sample in epoch.samples if sample is not proof.sample
    )
    for substitution in (
        {"sample": other_sample},
        {"proof_id": "xic.owner-backfill-conservative-no-request:" + "0" * 64},
        {
            "processing_owners": replace(
                proof.processing_owners,
                attempt=issue_processing_attempt(proof.processing_owners.request),
            )
        },
    ):
        with pytest.raises(ValueError):
            replace(proof, **substitution)
    other_group = copy(proof.group_identity)
    object.__setattr__(other_group, "group_hypothesis_id", "crossed-group")
    with pytest.raises(ValueError):
        replace(proof, group_identity=other_group)
    from xic_extractor.alignment.conservative_backfill import (
        map_conservative_backfill_candidates,
    )

    (support_decision,) = map_conservative_backfill_candidates(
        (proof.decision.group_facts,), sample=other_sample, candidate_universes=()
    )
    with pytest.raises(ValueError):
        replace(proof, decision=support_decision)
    for substitution in (
        {"planning_state": "planned", "planning_reason": "planned"},
        {"planning_reason": "retained_local_owner"},
        {"conservative_no_request_proof": None},
    ):
        with pytest.raises(ValueError):
            replace(position, **substitution)
    with pytest.raises(ValueError):
        owner.validate_backfill_conservative_no_request_position(
            position, source_population=(proof.decision.target_cell,)
        )
    with pytest.raises(ValueError):
        owner.validate_backfill_conservative_no_request_position(
            position,
            source_cell=next(
                cell
                for cell in proof.decision.group_facts.source_cells
                if cell is not proof.decision.target_cell
            ),
        )

    from xic_extractor.alignment import conservative_backfill as conservative_owner

    def unexpected_reevaluation(*args, **kwargs):
        raise AssertionError("completed Backfill decision was reevaluated")

    with pytest.MonkeyPatch.context() as retained:
        retained.setattr(
            conservative_owner,
            "assess_conservative_backfill_group",
            unexpected_reevaluation,
        )
        retained.setattr(
            conservative_owner,
            "map_conservative_backfill_candidates",
            unexpected_reevaluation,
        )
        owner.validate_backfill_conservative_no_request_position(
            position,
            source_cell=proof.decision.target_cell,
            source_population=proof.decision.group_facts.source_cells,
        )
    lane_args = dict(
        association=final_cell.position_associations[0],
        source_cell=proof.decision.target_cell,
        coverage_terminals=(),
        repair_receipts=(),
        repaired_observations=(),
        final_group=final_cell.group,
        canonical_sample=proof.sample,
        processing_request_id=epoch.processing_owners.request.processing_request_id,
    )
    for field in ("coverage_terminals", "repair_receipts", "repaired_observations"):
        with pytest.raises(ValueError, match="physical or repair evidence"):
            _derive_lane_evidence_state(**(lane_args | {field: (object(),)}))

    result = next(
        item
        for item in runtime_result.sample_results
        if item.job.sample is proof.sample
    )
    context = proof.decision.group_facts.group_context
    original_result_bytes = canonical_sample_result_bytes(
        result.result_ref
    )
    with pytest.raises(ValueError, match="not a backfill"):
        project_completed_backfill(
            replace(result, backfill_attempts=()), group_context=context
        )
    if proof.completion is not None:
        completion = proof.completion
        with pytest.raises(ValueError):
            replace(proof, completion=None)
        assert (
            project_completed_backfill(result, group_context=context)
            == completion
        )
        other_coordinate = next(
            item.adapter_result.acquisition.traces[0].coordinate
            for item in runtime_result.sample_results
            if item.job.sample is other_sample
        )
        for substitution in (
            {"acquisition_sha256": "0" * 64},
            {"source_result_artifact": completion.initial_assessment_artifact},
            {"trace_coordinate": other_coordinate},
        ):
            with pytest.raises(ValueError):
                replace(completion, **substitution)
        recovery = result.backfill_attempt
        with pytest.raises(ValueError):
            project_completed_backfill(
                replace(
                    result,
                    backfill_attempts=(
                        replace(
                            recovery,
                            assessment=result.adapter_result.assessment_input.assessment,
                        ),
                    ),
                ),
                group_context=context,
            )
        for traces in ((), recovery.acquisition.traces * 2):
            wrong_acquisition = copy(recovery.acquisition)
            object.__setattr__(wrong_acquisition, "traces", traces)
            # The exact-coordinate check rejects both shapes before the outer
            # seal comparison is reached. That seal is covered directly in
            # test_cid_nl_multi_group_recovery.
            with pytest.raises(
                ValueError, match="backfill crossed its exact request coordinates"
            ):
                project_completed_backfill(
                    replace(
                        result,
                        backfill_attempts=(
                            replace(recovery, acquisition=wrong_acquisition),
                        ),
                    ),
                    group_context=context,
                )
    assert (
        canonical_sample_result_bytes(result.result_ref)
        == original_result_bytes
    )


@pytest.mark.parametrize("recovery_case", ("positive", "threshold", "completed_empty"))
def test_production_composition_closes_recovered_observation_without_candidate(
    tmp_path: Path,
    monkeypatch,
    recovery_case: str,
) -> None:
    sample_stems = (
        ("Support1", "Hold")
        if recovery_case == "threshold"
        else ("Support1", "Support2", "Support3", "Hold")
    )
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in sample_stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(f"production-recovery:{raw_path.stem}".encode())
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raws = {
        raw_path: _FakeRaw((apex_rt,))
        for raw_path, apex_rt in zip(
            raw_paths,
            (7.97, 8.03) if recovery_case == "threshold" else (7.97, 7.99, 8.01, 8.03),
            strict=True,
        )
    }
    if recovery_case != "positive":
        source_raws[raw_paths[-1]].intensity[:] = 0.0
    candidates_by_sample = {
        raw_path.stem: (
            _current_candidates(
                source_raws[raw_path],
                raw_file=raw_path,
                settings=settings,
                peak_config=peak_config,
            )[0],
        )
        for raw_path in raw_paths[:-1]
    }
    candidates_by_sample["Hold"] = ()
    batch = build_dense_partition(
        raw_paths=raw_paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(sample_stems, start=1)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-recovery",
    )
    opened = _install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )

    runtime_result = execute_dense_partition(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "recovery.runtime.json",
            "production-recovery",
        ),
    )
    recovered = tuple(
        result
        for result in runtime_result.sample_results
        if result.job.sample.metadata.raw_stem == "Hold"
    )
    assert len(recovered) == 1
    assert (recovered[0].backfill_attempt is not None) == (recovery_case != "threshold")
    assert len(recovered[0].effective_assessment.candidate_peaks) == (
        recovery_case == "positive"
    )
    opens_before_composition = sum(map(len, opened.values()))

    completed = complete_dense_partition(
        batch,
        runtime_result,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )
    _assert_partition_composition_parity(
        batch,
        runtime_result,
        completed,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )

    assert completed.application_result.children
    writer_inputs = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    if recovery_case != "positive":
        assert (
            sum(map(len, opened.values()))
            == opens_before_composition
            == len(sample_stems) - (recovery_case == "threshold")
        )
        _assert_conservative_coverage_handoff(
            completed,
            writer_inputs,
            batch,
            runtime_result,
            tmp_path,
            threshold=recovery_case == "threshold",
        )
        return
    from xic_extractor.untargeted_execution.partition.models import (
        project_completed_backfill,
    )

    (child,) = completed.application_result.children
    recovered_source = next(
        source
        for cell in child.scientific_core.cell_observations
        for source in cell.predecessor_cells
        if source.method_assessment is recovered[0].effective_assessment
    )
    with pytest.raises(ValueError, match="completed empty epoch"):
        project_completed_backfill(
            recovered[0], group_context=recovered_source.group_context
        )
    assert writer_inputs.matrix.sample_order == sample_stems
    assert len(writer_inputs.matrix.clusters) == 1
    assert (
        tuple(cell.sample_stem for cell in writer_inputs.matrix.cells) == sample_stems
    )
    cells_by_sample = {cell.sample_stem: cell for cell in writer_inputs.matrix.cells}
    assert all(
        cells_by_sample[sample_stem].source_candidate_id is not None
        for sample_stem in sample_stems[:-1]
    )
    assert cells_by_sample["Hold"].source_candidate_id is None
    row = writer_inputs.matrix.clusters[0]
    assert row.public_family_id == "FAM000001"
    expected_presentation_facts: dict[str, tuple[float, float]] = {}
    expected_cell_apexes: dict[str, float] = {}
    diagnostic_observation_ids: set[str] = set()
    for child in completed.application_result.children:
        for projected in child.completed_projection.rows:
            group = next(
                group
                for group in child.scientific_core.final_population
                if group.group_hypothesis_id == projected.row_key.group_hypothesis_id
            )
            member_ids = {
                physical_id
                for member in group.member_subjects
                for physical_id in member.physical_observation_ids
            }
            for decision in projected.decisions:
                direct_observations = decision.evidence.direct_observations
                selected_repairs = tuple(
                    observation
                    for observation in decision.evidence.repaired_observations
                    if observation.selected_for_output
                )
                if direct_observations and decision.value is not None:
                    assert len(direct_observations) == 1
                    expected_cell_apexes[
                        decision.subject.canonical_sample.metadata.raw_stem
                    ] = direct_observations[0].content.integration.rt_apex_min
                elif selected_repairs and decision.value is not None:
                    assert len(selected_repairs) == 1
                    assert selected_repairs[0].facts is not None
                    expected_cell_apexes[
                        decision.subject.canonical_sample.metadata.raw_stem
                    ] = selected_repairs[0].facts.integration.rt_apex_min
                for direct in decision.evidence.direct_observations:
                    physical_id = direct.content.peak_observation_content_id
                    if physical_id not in member_ids:
                        assert decision.subject.canonical_sample.metadata.raw_stem == (
                            "Hold"
                        )
                        # The group-bounded MS1 peak fills the Matrix without
                        # joining the group or its presentation facts.
                        assert decision.evidence.evidence_states == (
                            "ms1_only_backfill_observation",
                        )
                        assert decision.subject.member_subject is None
                        assert decision.value is not None
                        diagnostic_observation_ids.add(physical_id)
                        continue
                    expected_presentation_facts[physical_id] = (
                        direct.content.mz,
                        direct.content.integration.rt_apex_min,
                    )
                for repaired_observation in decision.evidence.repaired_observations:
                    if repaired_observation.selected_for_output:
                        assert repaired_observation.facts is not None
                        assert (
                            repaired_observation.facts.physical_evaluation is not None
                        )
                        assert (
                            repaired_observation.facts.physical_evaluation.facts
                            is not None
                        )
                        physical_facts = (
                            repaired_observation.facts.physical_evaluation.facts
                        )
                        expected_presentation_facts[physical_facts.fact_content_id] = (
                            repaired_observation.cell.backfill_seed_mz,
                            repaired_observation.cell.apex_rt,
                        )
    assert len(diagnostic_observation_ids) == 1
    assert not diagnostic_observation_ids & set(row.physical_observation_ids)
    assert len(expected_presentation_facts) == len(sample_stems) - 1
    assert row.physical_observation_ids == tuple(sorted(expected_presentation_facts))
    assert row.family_center_mz == pytest.approx(
        sum(value[0] for value in expected_presentation_facts.values())
        / len(expected_presentation_facts)
    )
    assert row.family_center_rt == pytest.approx(
        sum(value[1] for value in expected_presentation_facts.values())
        / len(expected_presentation_facts)
    )
    assert len(set(expected_cell_apexes.values())) > 1
    for sample_stem in sample_stems:
        cell_apex = cells_by_sample[sample_stem].apex_rt
        if sample_stem in expected_cell_apexes:
            assert cell_apex == pytest.approx(expected_cell_apexes[sample_stem])
        else:
            assert cell_apex is None
    assert row.center_mz_basis == "cross_sample_owner_arithmetic_mean"
    assert row.center_rt_basis == "cross_sample_owner_arithmetic_mean"

    # Discovery's apex MS1 readings replace the reported center only; members
    # without a reading and the repaired cell do not dilute it.
    direct_candidates = [
        candidate for values in candidates_by_sample.values() for candidate in values
    ]
    readings = {
        candidate.candidate_id: candidate.precursor_mz * (1 - (12 + index) * 1e-6)
        for index, candidate in enumerate(direct_candidates[:-1])
    }
    reported = project_completed_untarget_batch_writer_inputs(
        completed.application_result, ms1_apex_mz_by_candidate=readings
    )
    (reported_row,) = reported.matrix.clusters
    assert reported_row.family_center_mz == pytest.approx(
        sum(readings.values()) / len(readings)
    )
    assert reported_row.center_mz_basis == "discovery_ms1_apex_mean"
    assert reported_row.family_center_rt == row.family_center_rt
    assert reported.matrix.cells == writer_inputs.matrix.cells

    base_cells = writer_inputs.production_decisions.cells
    weighted_values = dict(zip(sample_stems, (1.0, 9.0, 2.0, 9.0), strict=True))
    for variant, no_write_sample in (("unequal", None), ("no-write", "Hold")):
        decisions = ProductionDecisionSet(
            cells={
                key: replace(
                    decision,
                    production_status=(
                        "blank"
                        if decision.sample_stem == no_write_sample
                        else decision.production_status
                    ),
                    backfill_tier=(
                        ""
                        if decision.sample_stem == no_write_sample
                        else decision.backfill_tier
                    ),
                    write_matrix_value=decision.sample_stem != no_write_sample,
                    matrix_value=(
                        None
                        if decision.sample_stem == no_write_sample
                        else weighted_values[decision.sample_stem]
                    ),
                    blank_reason=(
                        "regression_no_write"
                        if decision.sample_stem == no_write_sample
                        else decision.blank_reason
                    ),
                )
                for key, decision in base_cells.items()
            },
            rows=writer_inputs.production_decisions.rows,
        )
        accepted = tuple(
            (
                cells_by_sample[stem],
                decisions.cell(cells_by_sample[stem].cluster_id, stem),
            )
            for stem in sample_stems
            if stem != no_write_sample
        )
        legacy_rt = sum(
            cell.apex_rt * decision.matrix_value
            for cell, decision in accepted
            if cell.apex_rt is not None and decision.matrix_value is not None
        ) / sum(
            decision.matrix_value
            for _cell, decision in accepted
            if decision.matrix_value is not None
        )
        assert legacy_rt != pytest.approx(row.family_center_rt)

        writer_dir = tmp_path / f"writer-{variant}"
        outputs = output_paths(
            writer_dir,
            output_level="machine",
            emit_alignment_cells=False,
            emit_alignment_status_matrix=False,
        )
        write_outputs_atomic(
            outputs,
            writer_inputs.matrix,
            metadata=alignment_metadata(
                discovery_batch_index=tmp_path / "discovery_batch_index.csv",
                raw_dir=tmp_path,
                dll_dir=tmp_path,
                owner_build_xic_backend="raw",
                owner_backfill_xic_backend="raw",
                output_level="machine",
                peak_config=peak_config,
                audit_evidence_mode="none",
                heavy_audit_enabled=False,
                require_discovery_owner_closure=True,
                owner_backfill_peak_fact_provenance_row_count=1,
            ),
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            production_decisions=decisions,
        )

        assert outputs.matrix_tsv is not None
        with outputs.matrix_tsv.open(encoding="utf-8", newline="") as handle:
            matrix_row = next(csv.DictReader(handle, delimiter="\t"))
        assert float(matrix_row["RT"]) == pytest.approx(row.family_center_rt)

        assert outputs.matrix_identity_tsv is not None
        with outputs.matrix_identity_tsv.open(encoding="utf-8", newline="") as handle:
            identity_row = next(csv.DictReader(handle, delimiter="\t"))
        assert float(identity_row["RT"]) == pytest.approx(row.family_center_rt)
        assert identity_row["center_rt_basis"] == row.center_rt_basis
        assert identity_row["center_weight_basis"] == row.center_weight_basis


def test_atomic_publication_preserves_ms1_only_backfill_and_no_peak(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sample_stems = ("Support1", "Support2", "Support3", "MS1Only", "NoPeak")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in sample_stems)
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raws = {}
    for index, (raw_path, apex_rt) in enumerate(
        zip(raw_paths, (7.97, 7.99, 8.01, 8.03, 8.05), strict=True),
        start=1,
    ):
        raw_path.write_bytes(f"ms1-only-publication:{raw_path.stem}".encode())
        source_raw = _FakeRaw((apex_rt,))
        source_raw.intensity *= index
        if raw_path.stem in {"MS1Only", "NoPeak"}:
            source_raw.scans = ()
        if raw_path.stem == "NoPeak":
            source_raw.intensity[:] = 0.0
        source_raws[raw_path] = source_raw
    candidates_by_sample = {
        raw_path.stem: tuple(
            _current_candidates(
                source_raws[raw_path],
                raw_file=raw_path,
                settings=settings,
                peak_config=peak_config,
            )
        )
        for raw_path in raw_paths
    }
    assert all(len(candidates_by_sample[stem]) == 1 for stem in sample_stems[:3])
    assert candidates_by_sample["MS1Only"] == candidates_by_sample["NoPeak"] == ()
    for raw_path in raw_paths[3:]:
        assert (
            tuple(
                source_raws[raw_path].iter_ms2_scans(settings.rt_min, settings.rt_max)
            )
            == ()
        )
    assert source_raws[raw_paths[3]].intensity.max() > 0
    assert source_raws[raw_paths[4]].intensity.max() == 0
    batch = build_dense_partition(
        raw_paths=raw_paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(sample_stems, start=1)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path, raw_paths, candidates_by_sample
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:ms1-only-publication",
    )
    _install_runtime_raws(monkeypatch, sources_by_path=source_raws, tmp_path=tmp_path)

    runtime = execute_dense_partition(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json", "ms1-only-publication"
        ),
    )
    assert runtime.succeeded
    recovered = {
        result.job.sample.metadata.raw_stem: result for result in runtime.sample_results
    }
    assert recovered["MS1Only"].backfill_attempt is not None
    assert recovered["NoPeak"].backfill_attempt is not None
    assert recovered["MS1Only"].effective_assessment.content.method_result == "HOLD"
    assert recovered["NoPeak"].effective_assessment.content.method_result == "HOLD"
    alignment_config = AlignmentConfig(anchor_min_seed_events=1)
    completed = complete_dense_partition(
        batch, runtime, alignment_config=alignment_config
    )
    (child,) = completed.application_result.children
    (owner_row,) = child.completed_projection.rows
    owner_decisions = {
        decision.subject.canonical_sample.metadata.raw_stem: decision
        for decision in owner_row.decisions
    }
    assert set(owner_decisions) == set(sample_stems)
    assert owner_row.counts.numeric_cells == 4
    ms1_only = owner_decisions["MS1Only"]
    assert ms1_only.variant == "numeric" and ms1_only.value > 0
    assert ms1_only.evidence.evidence_states == ("ms1_only_backfill_observation",)
    assert len(ms1_only.evidence.ms1_only_backfill_observations) == 1
    assert ms1_only.subject.member_subject is None
    no_peak = owner_decisions["NoPeak"]
    assert no_peak.value is None
    assert no_peak.variant == "unavailable"
    assert no_peak.selected_integration is None
    assert all(owner_decisions[stem].variant == "numeric" for stem in sample_stems[:3])
    assert len({owner_decisions[stem].value for stem in sample_stems[:4]}) == 4

    writer_inputs = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    for cell in writer_inputs.matrix.cells:
        assert (
            cell.selected_integration
            is owner_decisions[cell.sample_stem].selected_integration
        )
    outputs = output_paths(
        tmp_path / "writer",
        output_level="machine",
        emit_alignment_cells=True,
        emit_alignment_status_matrix=False,
    )
    write_outputs_atomic(
        outputs,
        writer_inputs.matrix,
        metadata=alignment_metadata(
            discovery_batch_index=tmp_path / "discovery_batch_index.csv",
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            owner_build_xic_backend="raw",
            owner_backfill_xic_backend="raw",
            output_level="machine",
            peak_config=peak_config,
            audit_evidence_mode="none",
            heavy_audit_enabled=False,
            require_discovery_owner_closure=True,
            owner_backfill_peak_fact_provenance_row_count=1,
        ),
        alignment_config=alignment_config,
        production_decisions=writer_inputs.production_decisions,
    )
    assert outputs.matrix_tsv is not None
    assert outputs.review_tsv is not None
    assert outputs.cells_tsv is not None
    with outputs.matrix_tsv.open(encoding="utf-8", newline="") as handle:
        (matrix_row,) = tuple(csv.DictReader(handle, delimiter="	"))
    with outputs.review_tsv.open(encoding="utf-8", newline="") as handle:
        (review_row,) = tuple(csv.DictReader(handle, delimiter="	"))
    with outputs.cells_tsv.open(encoding="utf-8", newline="") as handle:
        cell_rows = {
            row["sample_stem"]: row for row in csv.DictReader(handle, delimiter="	")
        }
    assert set(cell_rows) == set(sample_stems)
    for stem in sample_stems[:4]:
        value = owner_decisions[stem].value
        assert float(matrix_row[stem]) == pytest.approx(value, rel=5e-6)
        assert float(cell_rows[stem]["area"]) == pytest.approx(value, rel=5e-6)
    assert matrix_row["NoPeak"] == cell_rows["NoPeak"]["area"] == ""
    assert review_row["detected_count"] == "3"
    assert review_row["quantifiable_detected_count"] == "3"
    assert review_row["quantifiable_backfill_count"] == "1"
    assert review_row["accepted_backfill_count"] == "1"
    assert review_row["accepted_cell_count"] == "4"
    assert review_row["absent_count"] == "1"
    for stem in sample_stems[:3]:
        assert cell_rows[stem]["status"] == "detected"
        assert cell_rows[stem]["source_candidate_id"] == (
            candidates_by_sample[stem][0].candidate_id
        )
    assert cell_rows["MS1Only"]["status"] == "backfilled"
    assert cell_rows["MS1Only"]["source_candidate_id"] == ""
    assert cell_rows["MS1Only"]["gap_fill_state"] == "accepted"
    assert cell_rows["MS1Only"]["gap_fill_reason"] == "ms1_only_backfill_observation"
    assert cell_rows["NoPeak"]["status"] == "absent"
    assert cell_rows["NoPeak"]["source_candidate_id"] == ""
    assert cell_rows["NoPeak"]["missing_observation_state"] == "unavailable"


@pytest.mark.parametrize("exclude_all", (False, True))
def test_production_composition_retains_typed_non_acquired_candidate_exclusion(
    tmp_path: Path,
    monkeypatch,
    exclude_all: bool,
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-orphan:S1")
    # Isolate the orphan's method rejection from the acquired candidate.
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    source_raw, initial_candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    excluded_source_ids = {
        candidate.candidate_id
        for candidate in (initial_candidates if exclude_all else initial_candidates[1:])
    }
    candidates = tuple(
        replace(
            candidate,
            ms1_peak_found=False,
            ms1_apex_rt=None,
            ms1_area=None,
            ms1_peak_rt_start=None,
            ms1_peak_rt_end=None,
            ms1_height=None,
            ms1_trace_quality="not_found",
            ms1_scan_support_score=None,
            discovery_candidate_state="review_only_orphan_nl",
            ms1_feature_row_id="",
        )
        if candidate.candidate_id in excluded_source_ids
        else candidate
        for candidate in initial_candidates
    )
    batch = build_dense_partition(
        raw_paths=(raw_path,),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="study_sample",
            ),
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample={"S1": candidates},
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-orphan",
    )
    _install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_path,
        tmp_path=tmp_path,
    )

    runtime_result = execute_dense_partition(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.json",
            "production-orphan",
        ),
    )
    completed = complete_dense_partition(
        batch,
        runtime_result,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )
    _assert_partition_composition_parity(
        batch,
        runtime_result,
        completed,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )

    assert completed.application_result.candidate_ids == tuple(
        candidate_id
        for subject in batch.subject_batch.subjects
        for candidate_id, method_subject_id in batch.subject_batch.candidate_memberships
        if method_subject_id == subject.method_subject_id
    )
    exclusions = completed.application_result.excluded_candidates
    assert {item.candidate_id for item in exclusions} == excluded_source_ids
    assert all(
        item.method_subject_id
        == dict(batch.subject_batch.candidate_memberships)[item.candidate_id]
        and item.discovery_candidate_state == "review_only_orphan_nl"
        and item.reason_code == "cid_nl_requirement_not_supported"
        and len(item.method_assessments) == 1
        and item.method_assessments[0].content.method_result == "REJECT"
        for item in exclusions
    )
    from xic_extractor.alignment.result.application_result import (
        ApplicationResultContractError,
    )

    for item in exclusions:
        legacy = replace(
            item,
            reason_code="discovery_candidate_not_search_eligible",
            method_assessments=(),
        )
        assert legacy.discovery_candidate_state == item.discovery_candidate_state
        for field in ("method_assessments", "composite_assessments"):
            with pytest.raises(ApplicationResultContractError):
                replace(legacy, **{field: item.method_assessments})
    if exclude_all:
        assert completed.application_result.children == ()
        assert completed.application_result.subject_ids == tuple(
            subject.method_subject_id for subject in batch.subject_batch.subjects
        )
        assert completed.application_result.canonical_samples == tuple(
            sorted(
                batch.context.samples,
                key=lambda sample: sample.identity.sample_identity_id,
            )
        )
