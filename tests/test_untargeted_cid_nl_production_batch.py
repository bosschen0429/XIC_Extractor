from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from dataclasses import asdict, fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.cid_nl_completion as cid_nl_completion_owner
import xic_extractor.discovery.processing_run_spec_adapter as processing_spec_adapter
import xic_extractor.processing_identity as identity_owner
import xic_extractor.raw_source_binding as raw_source_binding_owner
import xic_extractor.untargeted_execution.alignment as alignment_adapter
import xic_extractor.untargeted_execution.partition.request as input_owner
from tests.alignment_explanation_fixtures import explanation_summary
from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _multi_tag_settings,
    _peak_config,
    _settings,
)
from tests.cid_nl_runtime_fixtures import (
    build_discovery_receipts,
    install_runtime_raw,
    install_runtime_raws,
)
from tests.untargeted_cid_nl_batch_runtime_fixtures import _nearby_subject_candidates
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    complete_dense_partition,
    execute_dense_partition,
    fixture_assessment,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.production_decisions import ProductionDecisionSet
from xic_extractor.alignment.result.scientific_alignment import (
    ObservedSearchSubject,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
from xic_extractor.discovery.models import (
    MS1_FEATURE_BACKED_STATES,
    DiscoveryCandidate,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec_with_expectations,
)
from xic_extractor.execution.process_memory import (
    ProcessMemorySample,
    ProcessTreeMemorySampler,
)
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
        FinalizedUntargetBatchApplicationResult,
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
    result = FinalizedUntargetBatchApplicationResult(
        scope=original.scope,
        processing_request=original.processing_request,
        processing_attempt=original.processing_attempt,
        children=tuple(child for part in parts for child in part.children),
        excluded_candidates=exclusions,
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
    assert result.physical_peak_keys == original.physical_peak_keys
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
        (sample.identity.sample_key, memberships[candidate.candidate_id])
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
        or (sample.identity.sample_key, subject.method_subject_id) in candidate_keys
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


def _assert_composite_no_input_exclusion_coverage(
    completed, runtime_result, batch
) -> None:

    result = completed.application_result
    exclusions = result.excluded_candidates
    assert exclusions
    assert all(
        exclusion.reason_code == "composite_requirement_not_supported"
        and exclusion.composite_assessments
        and not exclusion.method_assessments
        for exclusion in exclusions
    )


def _expected_retained_curve(
    *,
    kind,
    sample_id,
    candidate_id,
    trace,
    evaluation,
    attempt_id,
    observation=None,
    method_subject_id=None,
):
    # Acceptance oracle starts with the original owner values, never a reader
    # result or a re-executed signal-processing calculation.
    signal = evaluation.retained_prepared_signal if evaluation is not None else None
    provenance = {
        "processing_attempt_id": attempt_id,
    }
    if method_subject_id is not None:
        provenance["method_subject_id"] = method_subject_id
    if evaluation is not None:
        provenance.update(
            profile=asdict(evaluation.prepared.profile),
            physical_state=evaluation.state,
            physical_reason=evaluation.reason,
        )
    if signal is not None:
        provenance.update(
            profile_version=signal.profile.schema_version,
            profile=asdict(signal.profile),
        )
    provenance["acquisition_source_id"] = trace.acquisition_source_id
    if observation is not None:
        generation = observation.generation_provenance
        for name in (
            "selected_trace_indices",
            "resolver_mode",
            "proposal_sources",
        ):
            provenance[name] = getattr(generation, name)
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
        "physical_peak_key": observation.content.physical_peak_key
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
        "provenance": {
            **dict.fromkeys(
                (
                    "profile_version",
                    "acquisition_source_id",
                    "processing_attempt_id",
                    "selected_trace_indices",
                    "resolver_mode",
                    "proposal_sources",
                    "method_subject_id",
                    "profile",
                    "physical_state",
                    "physical_reason",
                )
            ),
            **provenance,
        },
    }
    return json.loads(json.dumps(value))


def _expected_capture_curve(evidence):
    return _expected_retained_curve(
        kind="backfill_observation"
        if evidence.capture_stage == "method_backfill"
        else evidence.capture_stage,
        sample_id=evidence.sample_key,
        candidate_id=evidence.candidate_id,
        trace=evidence.trace,
        evaluation=evidence.physical_evaluation,
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
from xic_extractor.alignment_explanation.reader import (
    read_alignment_curves,
    read_alignment_explanation,
)
curves = read_alignment_curves(sys.argv[1])
links = {
    candidate: [
        record['payload_id'] for record in read_alignment_explanation(
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


@pytest.fixture(params=(False, True), ids=("eager", "indexed"))
def replay_indexed_input(request):
    return request.param


@pytest.fixture
def profile_replay(tmp_path, monkeypatch, request, replay_indexed_input):
    """Run persisted Discovery through the real adapter; keep each test isolated.

    Only the end-to-end matrix test expands the eight scientific scenarios.
    The owner-boundary tests use the two-sample single-tag baseline.
    """
    group_sizes, method_mode, nearby, family_context, family_precursor_delta = (
        request.param
        if hasattr(request, "param")
        else ((2,), "single", False, False, 0.0)
    )
    indexed_input = replay_indexed_input
    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.discovery.csv_reader import (
        read_owner_bound_discovery_candidates_csv,
    )
    from xic_extractor.discovery.grouping import group_discovery_seeds
    from xic_extractor.discovery.ms1_capture import (
        capture_ms1_candidates,
    )
    from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
    from xic_extractor.discovery.physical_fact_persistence import (
        write_discovery_physical_fact_sidecar,
    )
    from xic_extractor.discovery.processing_run_spec_adapter import (
        compile_discovery_processing_run_spec,
    )

    separate_groups = len(group_sizes) == 2
    sample_stems = tuple(f"S{index + 1}" for index in range(sum(group_sizes)))
    paths = tuple(tmp_path / f"{stem}.raw" for stem in sample_stems)
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
    opened_raws = install_runtime_raws(
        monkeypatch, sources_by_path=source_raws, tmp_path=tmp_path
    )
    receipts = {}
    csvs = {}
    candidates = {}
    physical_facts = {}
    source_records = {}
    source_evidence = {}
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
                    # Distinct MS2 receipts can now share one physical root.
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
            discovery = capture_ms1_candidates(
                discovery_raw,
                group_discovery_seeds(seeds, settings=settings),
                acquisition_source=source.acquisition_source,
                settings=settings,
                signal_processing_profile=profile,
                ms1_centroid_mz=lambda *_: None,
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
        write_discovery_physical_fact_sidecar(
            folder / "discovery_physical_facts.json",
            run_spec=spec,
            acquisition_input=source,
            work_manifest=discovery.physical_work_manifest,
            records=discovery.physical_evaluations,
        )
        receipts.update(
            build_discovery_receipts(folder, (path,), {path.stem: persisted_candidates})
        )
        csvs[path.stem] = folder / f"{path.stem}.owner-candidates.csv"
        candidates[path.stem] = tuple(
            candidate
            for candidate in read_owner_bound_discovery_candidates_csv(
                csvs[path.stem], receipt=receipts[path.stem]
            )
            if candidate.ms1_peak_found
        )
        source_evidence.update(
            (record.candidate_id, record) for record in discovery.physical_evaluations
        )
        source_records.update(
            (record.candidate_id, record)
            for record in discovery.physical_evaluations
            if record.candidate_id in {c.candidate_id for c in candidates[path.stem]}
        )
        if family_context:
            # Identical mass queries now share the complete peak population in
            # Discovery; the same peak is not left as two window-bound roots.
            assert len(candidates[path.stem]) == 1
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
                    query_rt_min=record.evaluation.question.query_rt_min,
                    request_context_rt_start_min=rt_min,
                    request_context_rt_end_min=rt_max,
                )
            assert (
                len(
                    {
                        family_evaluations[record.candidate_id].answer.selected_segment
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

    monkeypatch.setattr(bounded_owner, "materialize_untargeted_partition", retain_batch)
    from xic_extractor.alignment import (
        cid_nl_predecessor_cells as predecessor_cell_owner,
    )
    from xic_extractor.alignment import completion as workflow_owner
    from xic_extractor.alignment import grouping_bootstrap as bootstrap_owner
    from xic_extractor.untargeted_execution import partition_composition as production

    mint_captured = production.mint_captured_peak_observation
    mint_rechecks = []
    mint_args = []

    def record_captured_mint(capture, *args, **kwargs):
        counts = {"prepare": 0, "evaluate": 0}
        real_capture_prepare = capture_owner.prepare_physical_signal
        real_capture_evaluate = capture_owner.evaluate_physical_peak

        def count_capture_prepare(*call_args, **call_kwargs):
            counts["prepare"] += 1
            return real_capture_prepare(*call_args, **call_kwargs)

        def count_capture_evaluate(*call_args, **call_kwargs):
            counts["evaluate"] += 1
            return real_capture_evaluate(*call_args, **call_kwargs)

        with monkeypatch.context() as mint_patch:
            mint_patch.setattr(
                capture_owner, "prepare_physical_signal", count_capture_prepare
            )
            mint_patch.setattr(
                capture_owner, "evaluate_physical_peak", count_capture_evaluate
            )
            result = mint_captured(capture, *args, **kwargs)
        mint_args.append((capture, args, kwargs))
        mint_rechecks.append(counts)
        return result

    monkeypatch.setattr(
        production, "mint_captured_peak_observation", record_captured_mint
    )

    projection_builds = []
    project_matrix = workflow_owner.project_untarget_matrix

    def record_matrix_projection(*args):
        result = project_matrix(*args)
        projection_builds.append(result)
        return result

    monkeypatch.setattr(
        workflow_owner, "project_untarget_matrix", record_matrix_projection
    )
    bind_capture = capture_owner.bind_owner_backed_capture_hypothesis
    evaluate_physical = capture_owner.evaluate_physical_peak
    binding_rechecks = []
    bound_captures = []

    def record_binding(capture, **kwargs):
        rechecks = []

        def record_evaluation(*args, **evaluation_kwargs):
            rechecks.append(True)
            return evaluate_physical(*args, **evaluation_kwargs)

        with monkeypatch.context() as binding_patch:
            binding_patch.setattr(
                capture_owner, "evaluate_physical_peak", record_evaluation
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
                    kwargs["sample"].identity.sample_key,
                    assessment.content,
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
    completed_contexts = []
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
    admit_backfills = cid_nl_completion_owner.admit_cid_nl_group_backfills
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

    def retain_completed_context(**kwargs):
        result = admit_backfills(**kwargs)
        completed_contexts.append(result[0])
        return result

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
        cid_nl_completion_owner,
        "admit_cid_nl_group_backfills",
        retain_completed_context,
    )
    monkeypatch.setattr(
        workflow_owner, "finalize_scientific_alignment", retain_scientific_captures
    )

    runtime_results = []
    runtime_execute = bounded_owner.execute_partition

    def checked_runtime(*args, **kwargs):
        result = runtime_execute(*args, **kwargs)
        assert result.succeeded, (
            result.failure,
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
                        # Discovery already resolved this shared context, so
                        # runtime reuses its exact question rather than minting
                        # a comparison-window question with a different ID.
                        assert (
                            capture.physical_evaluation
                            == source_records[capture.candidate_id].evaluation
                        )
        return result

    monkeypatch.setattr(bounded_owner, "execute_partition", checked_runtime)
    preparation_fingerprints = []
    runtime_fingerprints = []
    preparation_fingerprint = identity_owner.fingerprint_file_content
    fingerprint = raw_source_binding_owner.fingerprint_file_content

    def count_preparation_fingerprint(path, **kwargs):
        preparation_fingerprints.append(path)
        return preparation_fingerprint(path, **kwargs)

    def count_runtime_fingerprint(path):
        runtime_fingerprints.append(path)
        return fingerprint(path)

    monkeypatch.setattr(
        raw_source_binding_owner, "fingerprint_file_content", count_runtime_fingerprint
    )
    monkeypatch.setattr(
        identity_owner, "fingerprint_file_content", count_preparation_fingerprint
    )
    from xic_extractor.discovery.input_source import DiscoveryInputSource

    obsolete_marker = tmp_path / "alignment" / ".cid_nl_runtime.json"
    obsolete_marker.parent.mkdir(exist_ok=True)
    obsolete_marker.write_bytes(b"obsolete attempt status")
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
    try:
        yield SimpleNamespace(
            completed=completed,
            case=SimpleNamespace(
                group_sizes=group_sizes,
                method_mode=method_mode,
                nearby=nearby,
                family_context=family_context,
                family_precursor_delta=family_precursor_delta,
                indexed_input=indexed_input,
            ),
            inputs=SimpleNamespace(
                candidates=candidates,
                paths=paths,
                center_rt=center_rt,
                peak_config=peak_config,
                profile=profile,
                settings=settings,
                receipts=receipts,
                sample_stems=sample_stems,
                separate_groups=separate_groups,
                source_raws=source_raws,
                opened_raws=opened_raws,
                source_evidence=source_evidence,
                source_records=source_records,
                physical_facts=physical_facts,
            ),
            trace=SimpleNamespace(
                captured_batches=captured_batches,
                captured_peaks=captured_peaks,
                bound_captures=bound_captures,
                binding_rechecks=binding_rechecks,
                completed_contexts=completed_contexts,
                consumed_captures=consumed_captures,
                consumed_contexts=consumed_contexts,
                hypothesis_universe_compositions=hypothesis_universe_compositions,
                mint_args=mint_args,
                mint_rechecks=mint_rechecks,
                obsolete_marker=obsolete_marker,
                preparation_fingerprints=preparation_fingerprints,
                projection_builds=projection_builds,
                retained_candidate_batches=retained_candidate_batches,
                retained_captures=retained_captures,
                retained_cell_batches=retained_cell_batches,
                retained_contexts=retained_contexts,
                retained_initial_edges=retained_initial_edges,
                runtime_fingerprints=runtime_fingerprints,
                runtime_results=runtime_results,
            ),
        )
    finally:
        completed.close()


@pytest.mark.parametrize(
    "profile_replay",
    (
        pytest.param(((2,), "single", False, False, 0.0), id="one-group"),
        pytest.param(((1, 1), "single", False, False, 0.0), id="two-singletons"),
        pytest.param(((2, 2), "single", False, False, 0.0), id="two-supported-groups"),
        pytest.param(((2,), "single", True, False, 0.0), id="nearby-single"),
        pytest.param(((2,), "union", True, False, 0.0), id="nearby-or-different-tags"),
        pytest.param(
            ((2,), "intersection", True, False, 0.0), id="nearby-and-incomplete"
        ),
        pytest.param(
            ((2,), "single", False, True, 0.0), id="overlapping-family-one-peak"
        ),
        pytest.param(
            ((4,), "single", False, True, 0.0065),
            id="overlapping-family-across-fixed-subject-boundary",
        ),
    ),
    indirect=True,
)
def test_profile_replay_closes_persisted_discovery_through_runtime_and_writer(
    tmp_path: Path,
    profile_replay,
) -> None:
    completed = profile_replay.completed
    inputs = profile_replay.inputs
    trace = profile_replay.trace
    case = profile_replay.case

    publication = completed.publication_inputs
    assert publication.alignment_config == AlignmentConfig(anchor_min_seed_events=1)
    assert publication.known_istd_reference_provenance is None
    assert dict(publication.ms1_apex_mz_by_candidate) == {
        candidate.candidate_id: candidate.ms1_apex_mz
        for sample_candidates in inputs.candidates.values()
        for candidate in sample_candidates
        if candidate.ms1_apex_mz is not None
    }
    assert {
        sample: tuple(candidate.candidate_id for candidate in sample_candidates)
        for sample, sample_candidates in publication.candidates_by_sample.items()
    } == {
        sample: tuple(candidate.candidate_id for candidate in sample_candidates)
        for sample, sample_candidates in inputs.candidates.items()
    }
    assert completed.processing_attempt_id == (
        completed.application_result.processing_attempt.processing_attempt_id
    )
    composed_universes = [item[1:] for item in trace.hypothesis_universe_compositions]
    assert len(composed_universes) == len(set(composed_universes)), (
        {
            owner_name: sum(
                item[0] == owner_name for item in trace.hypothesis_universe_compositions
            )
            for owner_name in {
                item[0] for item in trace.hypothesis_universe_compositions
            }
        },
        trace.hypothesis_universe_compositions,
    )
    retained_hypotheses_by_id = {
        candidate.peak_hypothesis_id: candidate.peak_hypothesis
        for retained_candidates in trace.retained_candidate_batches
        for candidate in retained_candidates
    }
    assert all(
        capture.selected_hypothesis is None
        or retained_hypotheses_by_id[capture.selected_hypothesis.hypothesis_id]
        == capture.selected_hypothesis
        for capture in trace.retained_captures
    )
    assert all(
        retained_hypotheses_by_id[support.hypothesis.hypothesis_id]
        == support.hypothesis
        for cells in trace.retained_cell_batches
        for cell in cells
        for support in cell.group_context.supports
    )
    # Owner-produced arrays survive both sidecar and runtime partition handoff.
    retained_discovery = tuple(completed.discovery_physical_records)
    # Indexed input exposes published candidate bindings lazily; eager input
    # also retains the source-only records behind merged representatives.
    expected_retained = (
        inputs.source_records if case.indexed_input else inputs.source_evidence
    )
    assert {item.candidate_id for item in retained_discovery} == set(expected_retained)
    for retained in retained_discovery:
        source = inputs.source_evidence[retained.candidate_id]
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
        for result in trace.runtime_results
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
        assert retained.sample_key == source.sample_key
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
            sample_id=samples_by_stem[work.sample_stem].identity.sample_key,
            trace=source_trace,
            evaluation=record.evaluation,
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

    assert trace.preparation_fingerprints == [], "preparation must not read RAW content"
    assert trace.obsolete_marker.read_bytes() == b"obsolete attempt status"
    assert trace.runtime_fingerprints == [], (
        "RAW opens must reuse the input owner's source verification",
        trace.runtime_fingerprints,
    )
    _assert_jobs_match_direct_construction(trace.captured_batches[0])
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
        write_alignment_explanation,
    )

    write_alignment_explanation(
        tmp_path / "producer-evidence.sqlite",
        full_audit=True,
        completed=completed,
        candidates_by_sample=inputs.candidates,
        matrix_path=matrix_path,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )
    assert len(trace.retained_contexts) == len(completed.application_result.children)
    assert len(trace.retained_contexts) == len(trace.consumed_contexts)
    # This fixture keeps the frozen partition; Backfill can add risk decisions.
    # The completed content, including those decisions, must survive finalization
    # and the spool. Python object identity is not a persisted contract.
    assert len(trace.completed_contexts) == len(trace.retained_contexts)
    if case.family_precursor_delta:
        assert any(
            context.backfill_membership_risks for context in trace.completed_contexts
        )
    assert all(
        retained.partition_transition == final.partition_transition
        and retained.processing_owners == final.processing_owners
        for retained, final in zip(
            trace.retained_contexts,
            trace.completed_contexts,
            strict=True,
        )
    )
    assert all(
        retained == consumed
        for retained, consumed in zip(
            trace.completed_contexts,
            trace.consumed_contexts,
            strict=True,
        )
    )
    # Bootstrap captures cross a disk handoff; preserve content, not addresses.
    for retained, consumed in zip(
        trace.retained_captures,
        trace.consumed_captures,
        strict=True,
    ):
        assert retained.candidate_id == consumed.candidate_id
        assert retained.selected_hypothesis == consumed.selected_hypothesis
        assert list(retained.trace.rt) == list(consumed.trace.rt)
        assert list(retained.trace.intensity) == list(consumed.trace.intensity)
    for context, child in zip(
        trace.completed_contexts,
        completed.application_result.children,
        strict=True,
    ):
        assert child.scientific_core.final_population == context.final_population
        assert child.scientific_core.lineage == ()
        assert child.scientific_core.grouping_result == context
    assert tuple(completed.initial_edges) == tuple(trace.retained_initial_edges)
    children = completed.application_result.children
    # Completed owner answers flow through projection, spool and writer once.
    assert len(trace.projection_builds) == len(children)
    # Binding consumes the closed capture; it must not repeat physical selection.
    assert not any(trace.binding_rechecks)
    if case.method_mode != "intersection":
        assert trace.binding_rechecks
        assert trace.mint_rechecks
        assert trace.mint_args
    assert len(trace.mint_rechecks) == len(trace.mint_args)
    assert all(
        counts == {"prepare": 0, "evaluate": 0} for counts in trace.mint_rechecks
    )
    if case.family_context:
        assert len(matrix_rows) == 1
        if case.family_precursor_delta:
            batch = trace.captured_batches[0]
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
                sample.metadata.raw_stem: sample.identity.sample_key
                for sample in child.scientific_core.canonical_samples
            }
            joint_group = next(
                group
                for group in child.scientific_core.final_population
                if len(group.member_subjects) == 2
            )
            assert {member.sample_key for member in joint_group.member_subjects} == {
                sample_id_by_stem["S2"],
                sample_id_by_stem["S3"],
            }
            joint_final_cell = next(
                cell
                for cell in child.scientific_core.cell_observations
                if cell.group_hypothesis_id == joint_group.group_hypothesis_id
                and cell.sample_key == sample_id_by_stem["S3"]
            )
            (joint_cell,) = joint_final_cell.predecessor_cells
            assert any(
                support.assessment is joint_cell.method_assessment
                and support.sample.identity.sample_key == sample_id_by_stem["S3"]
                for support in joint_cell.group_context.supports
            )
            assert all(
                assessment.content.reason_codes
                != ("no_search_eligible_discovery_input",)
                for assessment in completed.method_assessments
            )
            assert len(writer.matrix.clusters) == 3
            assert len(writer.matrix.cells) == 3 * len(inputs.sample_stems)
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
                and cell.sample_key == sample_id_by_stem["S4"]
            )
            assert s4_cell.evidence_states == ("ms1_only_backfill_observation",)
            assert matrix_row["S4"]
        else:
            assert len(writer.matrix.cells) == len(inputs.sample_stems)
        close_and_verify_persisted_curves()
        return
    if case.nearby:
        assert len(matrix_rows) == (0 if case.method_mode == "intersection" else 1)
        batch = trace.captured_batches[0]
        assert len(batch.subject_batch.subjects) == 1
        for result in trace.runtime_results[0].sample_results:
            stem = result.job.sample.metadata.raw_stem
            (candidate,) = batch.context.candidates_by_sample[stem]
            record = batch.context.physical_records_by_candidate[candidate.candidate_id]
            assert record.work_item.precursor_mz == candidate.precursor_mz
            assert record.acquired_trace.coordinate.request.mz == candidate.precursor_mz
            assert record.evaluation.facts == inputs.physical_facts[stem]
            assert fixture_assessment(result).content.method_result == (
                "HOLD" if case.method_mode == "intersection" else "SUPPORT"
            )
        if case.method_mode == "intersection":
            assert writer.matrix.cells == ()
            assert writer.matrix.clusters == ()
            assert all(
                "incomplete_composite_requirement"
                in fixture_assessment(result).content.reason_codes
                for result in trace.runtime_results[0].sample_results
            )
            _assert_composite_no_input_exclusion_coverage(
                completed, trace.runtime_results[0], batch
            )
            close_and_verify_persisted_curves()
            return
        if case.method_mode == "union":
            (child,) = completed.application_result.children
            assert sorted(
                len(group.member_subjects)
                for group in child.scientific_core.final_population
            ) == [2]
            selected_cells = tuple(
                cell for cell in writer.matrix.cells if cell.selected_integration
            )
            assert {cell.sample_stem for cell in selected_cells} == set(
                inputs.sample_stems
            )
            for cell in selected_cells:
                facts = inputs.physical_facts[cell.sample_stem]
                assert cell.area == facts.area_counts_seconds
                assert cell.selected_integration is not None
                assert cell.selected_integration.rt_apex_min == facts.apex_rt_min
            close_and_verify_persisted_curves()
            return
    assert len(writer.matrix.cells) == len(case.group_sizes) * len(inputs.sample_stems)
    for cell in writer.matrix.cells:
        if cell.selected_integration is None:
            assert inputs.separate_groups
            assert not writer.production_decisions.cell(
                cell.cluster_id, cell.sample_stem
            ).write_matrix_value
            continue
        facts = inputs.physical_facts[cell.sample_stem]
        assert cell.area == facts.area_counts_seconds
        assert cell.selected_integration is not None
        assert cell.selected_integration.area_raw_counts_seconds != cell.area
        assert cell.selected_integration.rt_apex_min == facts.apex_rt_min
        assert cell.selected_integration.height_smoothed == facts.height
    expected_row_count = sum(size > 1 for size in case.group_sizes)
    assert len(matrix_rows) == expected_row_count
    for sample_stem, facts in inputs.physical_facts.items():
        sample_values = [
            float(row[sample_stem]) for row in matrix_rows if row[sample_stem]
        ]
        assert sample_values == pytest.approx(
            [facts.area_counts_seconds] if expected_row_count else []
        )
    if case.nearby:
        assert len({cell.source_candidate_id for cell in writer.matrix.cells}) == 2
        assert len(matrix_rows) == 1
        close_and_verify_persisted_curves()
        return
    if inputs.separate_groups:
        (child,) = completed.application_result.children
        scientific = child.scientific_core
        assert len(scientific.final_population) == 2
        assert len(scientific.cell_observations) == 2 * len(inputs.sample_stems)
        # Each sample's SUPPORT belongs to its own group. A group with K direct
        # supports revisits the other samples' windows, which hold no peak;
        # below K the other samples stay unsearched outside-group cells.
        revisited = min(case.group_sizes) >= 2
        assert sum(
            cell.evidence_states
            == (("unavailable",) if revisited else ("not_searched",))
            for cell in scientific.cell_observations
        ) == len(inputs.sample_stems)
        assert all(
            sum(bool(row[stem]) for stem in inputs.sample_stems) == 2
            for row in matrix_rows
        )
        assert all(
            result.adapter_result.assessment_input.assessment.content.method_result
            == "SUPPORT"
            for result in trace.runtime_results[0].sample_results
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

    close_and_verify_persisted_curves()


def test_profile_replay_rejects_crossed_physical_capture(profile_replay) -> None:
    trace = profile_replay.trace
    from xic_extractor.peak_detection import capture as capture_owner

    bound_capture, binding_args = trace.bound_captures[0]
    changed_source = capture_owner.capture_peak_hypotheses(
        scope=bound_capture.scope,
        candidate_id=bound_capture.candidate_id,
        trace=replace(bound_capture.trace, source="different-capture-source"),
    )
    with pytest.raises(capture_owner.SampleLocalCaptureError):
        capture_owner.bind_owner_backed_capture_hypothesis(
            changed_source, **binding_args
        )


def test_profile_replay_retains_positive_and_empty_backfill_evidence(
    tmp_path: Path,
    monkeypatch,
    profile_replay,
) -> None:
    inputs = profile_replay.inputs
    trace = profile_replay.trace

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.peak_detection import capture as capture_owner

    batch = trace.captured_batches[0]
    from xic_extractor.peak_detection.cid_nl_peak_support import (
        DdaCidNlPeakSupportPolicy,
    )
    from xic_extractor.processing_identity import resolve_request_acquisition_source
    from xic_extractor.untargeted_execution.partition import (
        cid_nl_backfill_acquisition,
    )

    job = batch.jobs[0]
    recovery_raw = partition_workers.open_raw(
        inputs.paths[0],
        tmp_path,
        expected_acquisition_source=resolve_request_acquisition_source(
            processing_owners=job.processing_owners, sample=job.sample
        ),
    )
    recovery_args = dict(
        processing_owners=job.processing_owners,
        sample=job.sample,
        method_subject=job.method_subject,
        group_center_rt=inputs.center_rt,
        group_center_mz=job.method_subject.precursor_mz,
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01, neutral_loss_tolerance_ppm=10.0
        ),
        peak_config=inputs.peak_config,
        raw=recovery_raw,
        settings=inputs.settings,
        signal_processing_profile=inputs.profile,
    )
    recovered = cid_nl_backfill_acquisition.backfill_untargeted_cid_nl_sample_subject(
        **recovery_args
    )
    (observation,) = recovered.assessment.candidate_peaks
    acquired = recovered.acquisition.traces[0]
    recovery_prepared = prepare_physical_signal(
        acquired.trace.rt,
        acquired.trace.intensity,
        profile=inputs.profile,
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
        cid_nl_backfill_acquisition.backfill_untargeted_cid_nl_sample_subject(
            **(
                recovery_args
                | {
                    "signal_processing_profile": SignalProcessingProfile(
                        window_points=21
                    )
                }
            )
        )
    original_intensity = inputs.source_raws[inputs.paths[0]].intensity
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
            inputs.source_raws[inputs.paths[0]].intensity = isolated
            unavailable = (
                cid_nl_backfill_acquisition.backfill_untargeted_cid_nl_sample_subject(
                    **recovery_args
                )
            )
            assert unavailable.assessment.candidate_peaks == ()
            assert unavailable.assessment.content.method_result == "HOLD"
            assert unavailable.backfill_input is None
            assert recovery_evaluations[-1].reason == reason
            assert recovery_evaluations[-1].facts is None
            assert recovery_evaluations[-1].integration is None
            assert recovery_evaluations[-1].question.selection_policy_version == (
                "sample_local_background_qualified_peak_in_context"
            )

        inputs.source_raws[inputs.paths[0]].intensity = original_intensity * 0.0
        unavailable = (
            cid_nl_backfill_acquisition.backfill_untargeted_cid_nl_sample_subject(
                **recovery_args
            )
        )
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
        vendor = inputs.opened_raws[inputs.paths[0]][-1]
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
            empty = (
                cid_nl_backfill_acquisition.backfill_untargeted_cid_nl_sample_subject(
                    **recovery_args
                )
            )
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
        inputs.source_raws[inputs.paths[0]].intensity = original_intensity
        recovery_raw.__exit__(None, None, None)

    # The actual recovery owner ran above under this request and attempt.
    # Exercise its production sink directly, without altering the completed
    # scientific population or manufacturing a new recovery identity.
    from xic_extractor.alignment_explanation.writer import (
        ExplanationReportWriter,
    )
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
                evidence.sample_key,
                kind="backfill_observation",
                trace=evidence.trace,
                evaluation=evidence.physical_evaluation,
                provenance={
                    "processing_attempt_id": evidence.processing_attempt_id,
                    "method_subject_id": evidence.method_subject_id,
                },
            )
        sink.finalize(
            explanation_summary(
                {
                    "processing_attempt_id": (
                        job.processing_owners.attempt.processing_attempt_id
                    ),
                },
                review_only=sink.review_only,
            )
        )
    _assert_fresh_process_curve_parity(
        recovery_path, [_expected_capture_curve(item) for item in recovery_sources]
    )


def test_profile_replay_rejects_changed_partition_inputs(
    tmp_path: Path, profile_replay
) -> None:
    inputs = profile_replay.inputs
    trace = profile_replay.trace
    case = profile_replay.case

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.processing_identity import IdentityContractError

    batch = trace.captured_batches[0]
    from xic_extractor.untargeted_execution.partition.models import (
        UntargetedPartitionError,
    )

    with pytest.raises(UntargetedPartitionError, match="profile is not request-bound"):
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
    if not case.indexed_input:
        wrong_records = dict(batch.context.physical_records_by_candidate)
        keys = tuple(wrong_records)
        wrong_records[keys[0]] = wrong_records[keys[1]]
        with pytest.raises(IdentityContractError, match="crossed candidate"):
            alignment_adapter.prepare_eager_untargeted_plan(
                raw_paths=inputs.paths,
                sample_metadata=tuple(
                    sample.metadata for sample in batch.context.samples
                ),
                settings=inputs.settings,
                peak_config=inputs.peak_config,
                candidates_by_sample=batch.context.candidates_by_sample,
                discovery_receipts_by_sample=inputs.receipts,
                dll_dir=tmp_path,
                sample_identity_namespace="test:physical-replay",
                signal_processing_profile=inputs.profile,
                physical_records_by_candidate=wrong_records,
            )


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
            state="available",
            requires_overlap_review=False,
            facts=SimpleNamespace(
                apex_rt_min=apex,
                left_rt_min=bounds[0],
                right_rt_min=bounds[1],
            ),
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

    physical_records = {}
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"physical-ion-planning:S1")
    settings = replace(_settings(), neutral_loss_profile=None)
    _, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=_peak_config(tmp_path),
        physical_records=physical_records,
    )
    inferred = replace(
        candidates[0],
        precursor_mz=445.2140,
        product_mz=445.2140 - 116.0474,
        ms1_apex_mz=445.2071,
        precursor_mz_basis="product_plus_neutral_loss",
    )
    subjects = prepare_untargeted_subject_membership(
        candidates_by_sample={"S1": (inferred,)},
        settings=settings,
        physical_records_by_candidate=physical_records,
    )
    assert len(subjects.subjects) == 1
    assert subjects.subjects[0].precursor_mz == 445.2071
    assert inferred.precursor_mz == 445.2140
    assert inferred.product_mz == 445.2140 - 116.0474

    selected = replace(inferred, ms1_apex_mz=None, precursor_mz_basis="scan_precursor")
    fallback = prepare_untargeted_subject_membership(
        candidates_by_sample={"S1": (selected,)},
        settings=settings,
        physical_records_by_candidate=physical_records,
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
    physical_records = {}
    from xic_extractor.untargeted_execution import input_planning as planning_owner

    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"lower-anchor-subject-unit:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=20.0
    )
    _raw, base_candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=_peak_config(tmp_path),
        physical_records=physical_records,
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
        "build_partition_context",
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
            signal_processing_profile=TEST_PROFILE,
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
        match="duplicate method subjects",
    ):
        cid_nl_alignment_subject_worksets((first, first))


@pytest.mark.parametrize("backfill_enabled", (True, False))
def test_completed_empty_recovery_preserves_each_compatible_source_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backfill_enabled: bool,
) -> None:
    physical_records = {}
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
            physical_records=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:compatible-source-subject",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates_by_sample).values()
            for c in values
        },
    )
    assert len(batch.subject_batch.subjects) == 2
    assert (
        len({subject.method_subject_id for subject in batch.subject_batch.subjects})
        == 2
    )
    install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )
    alignment_config = AlignmentConfig(
        anchor_min_seed_events=1,
        product_mz_tolerance_ppm=30.0,
        backfill_enabled=backfill_enabled,
    )
    runtime = execute_dense_partition(
        batch,
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
            child.scientific_core.cell_observation_handoff.coverage.positions
        )
        if position.sample.metadata.raw_stem == "Missing"
        and position.group_identity.group_hypothesis_id == group.group_hypothesis_id
    )
    if backfill_enabled:
        assert position.no_request_decision is not None
        assert position.no_request_decision.variant == "completed_backfill_empty"
    else:
        assert not any(item.backfill_attempts for item in runtime.sample_results)
        assert position.no_request_decision is None
        assert position.planning_reason == "no_discovery_input"
        missing = next(
            cell
            for cell in child.scientific_core.cell_observations
            if cell.canonical_sample.metadata.raw_stem == "Missing"
        )
        assert missing.evidence_state == "not_searched"
        assert not missing.direct_hypotheses and not missing.integrations
        assert all(
            ref.content.method_result != "SUPPORT" for ref in missing.method_assessments
        )
        from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

        writer = project_completed_untarget_batch_writer_inputs(
            completed.application_result
        )
        destination = tmp_path / "no-backfill-matrix.tsv"
        write_alignment_matrix_tsv(
            destination, writer.matrix, production_decisions=writer.production_decisions
        )
        with destination.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        assert rows and all(row["Missing"] == "" for row in rows)
        assert all(float(rows[0][stem]) > 0 for stem in stems[:4])


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
    from xic_extractor.discovery.models import NeutralLossProfile

    physical_records = {}
    candidates = tuple(
        _current_candidates(
            _FakeRaw(
                (8.0 + index * 0.1,), scan_precursor_mz=mz, neutral_loss_das=(loss,)
            ),
            raw_file=raw_path,
            settings=replace(
                settings, neutral_loss_profiles=(NeutralLossProfile("DNA_dR", loss),)
            ),
            physical_records=physical_records,
        )[0]
        for index, (mz, loss) in enumerate(zip(masses, losses, strict=True))
    )
    arguments = dict(
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-batch",
    )

    batch = build_dense_partition(**arguments, candidates_by_sample={"S1": candidates})
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
    physical_records = {}
    path = tmp_path / "S1.synthetic"
    path.write_bytes(b"source-verification:S1")
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    raw, candidates = _nearby_subject_candidates(
        path,
        settings=settings,
        peak_config=peak_config,
        physical_records=physical_records,
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
            discovery_receipts_by_sample=build_discovery_receipts(
                tmp_path, (path,), {"S1": candidates}
            ),
            dll_dir=tmp_path,
            sample_identity_namespace="study:source-verification",
            signal_processing_profile=TEST_PROFILE,
            physical_records_by_candidate=physical_records,
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
    monkeypatch.setattr(
        raw_source_binding_owner, "fingerprint_file_content", count_fingerprint
    )
    plan = prepare_eager_untargeted_plan(**arguments)
    opened = install_runtime_raws(
        monkeypatch, sources_by_path={path: raw}, tmp_path=tmp_path
    )
    execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        max_partition_jobs=1,
    )
    assert len(opened[path]) == len(plan.subject_batch.subjects) > 1
    assert fingerprints == []


def test_production_rejects_source_change_during_expectation_mint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _raw, arguments = _source_verification_inputs(tmp_path)
    build_source = input_owner.build_local_acquisition_source_identity

    def change_after_fingerprint(source_path, **kwargs):
        source = build_source(source_path, **kwargs)
        path.write_bytes(b"changed-after-fingerprint")
        return source

    monkeypatch.setattr(
        input_owner, "build_local_acquisition_source_identity", change_after_fingerprint
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
    build_source = processing_spec_adapter.build_local_acquisition_source_identity

    def change_after_fingerprint(source_path, **kwargs):
        source = build_source(source_path, **kwargs)
        path.write_bytes(b"changed-during-run-spec-preparation")
        return source

    monkeypatch.setattr(
        processing_spec_adapter,
        "build_local_acquisition_source_identity",
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
    physical_records = {}
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    candidates = {}
    for path in paths:
        path.write_bytes(f"source-verification:{path.stem}".encode())
        _, candidates[path.stem] = _nearby_subject_candidates(
            path,
            settings=settings,
            peak_config=peak_config,
            physical_records=physical_records,
        )
    arguments = dict(
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, paths, candidates
        ),
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
    monkeypatch.setattr(
        raw_source_binding_owner, "fingerprint_file_content", count_fingerprint
    )
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
    install_runtime_raws(monkeypatch, sources_by_path={path: raw}, tmp_path=tmp_path)
    with pytest.raises(RuntimeError, match="failed before batch publication"):
        execute_untargeted_plan(
            plan,
            output_dir=tmp_path / "failed-output",
            alignment_config=AlignmentConfig(),
        )


def test_sample_job_builder_rejects_changed_request_bound_inputs(
    tmp_path: Path,
) -> None:
    physical_records = {}
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
        raw_path,
        settings=settings,
        peak_config=peak_config,
        physical_records=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, (raw_path,), {"S1": candidates}
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-batch",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in ({"S1": candidates}).values()
            for c in values
        },
    )
    first = batch.jobs[0]
    shared = {
        item.name: getattr(first, item.name)
        for item in fields(first)
        if item.init and item.name not in {"method_subject", "work_unit_key"}
    }
    work_units = tuple((job.method_subject, job.work_unit_key) for job in batch.jobs)
    assert len(work_units) == 2
    assert build_sample_jobs(**shared, subject_work_units=work_units) == batch.jobs
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
    ):
        with pytest.raises(UntargetedPartitionError, match=message):
            build_sample_jobs(**(shared | changed), subject_work_units=work_units)


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
    receipts = build_discovery_receipts(
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
    application_result = object()

    class MockCompleted:
        def __init__(self):
            self.application_result = application_result

        def bind_publication_inputs(self, **kwargs):
            self.publication_inputs = SimpleNamespace(**kwargs)
            return self

        def with_discovery_inputs(self, prepared):
            self.discovery_inputs = prepared
            return self

        def close(self):
            self.discovery_inputs.close()

    completed = MockCompleted()
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

    monkeypatch.setattr(
        alignment_adapter,
        "_load_physical_replay_inputs",
        lambda **kwargs: (kwargs["candidates_by_sample"], {}, None),
    )

    result = alignment_adapter.complete_untargeted_alignment(
        signal_processing_profile=TEST_PROFILE,
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

    assert result.application_result is application_result
    assert result.discovery_inputs is not None
    result.close()
    assert result.publication_inputs.candidates_by_sample == {"S1": candidates}
    assert result.publication_inputs.alignment_config == AlignmentConfig()
    assert result.publication_inputs.known_istd_reference is None
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
    physical_records = {}
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"production-batch:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    _source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
        physical_records=physical_records,
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
            discovery_receipts_by_sample=build_discovery_receipts(
                tmp_path,
                (raw_path,),
                {"S1": candidates},
            ),
            dll_dir=tmp_path,
            sample_identity_namespace="study:production-batch",
            signal_processing_profile=TEST_PROFILE,
            physical_records_by_candidate={
                c.candidate_id: physical_records[c.candidate_id]
                for values in ({"S1": candidates}).values()
                for c in values
            },
        )
    except ValueError as exc:
        assert "single-provider" in str(exc)
    else:
        raise AssertionError("duplicate tag settings must be rejected")


def test_production_batch_executes_one_atomic_sample_phase(
    tmp_path: Path,
    monkeypatch,
) -> None:

    physical_records = {}
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
        physical_records=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-batch",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in ({"S1": candidates}).values()
            for c in values
        },
    )
    vendor_raw = install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_path,
        tmp_path=tmp_path,
    )

    result = execute_dense_partition(
        batch,
    )

    assert result.succeeded
    assert len(result.sample_results) == len(batch.jobs) == 2
    assert vendor_raw.chromatogram_request_counts == [4]

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
    assert tuple(row.feature_id for row in writer_inputs.matrix.clusters) == (
        "F000001",
        "F000002",
    )
    feature_by_group = {
        row.group_hypothesis_id: row.feature_id for row in writer_inputs.matrix.clusters
    }
    expected_source_ids: dict[tuple[str, str], str] = {}
    for child in completed.application_result.children:
        candidate_by_hypothesis = {
            (
                subject.sample_key,
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
                        feature_by_group[projected.group.group_hypothesis_id],
                        decision.subject.canonical_sample.metadata.raw_stem,
                    )
                ] = candidate_by_hypothesis[
                    (
                        decision.subject.canonical_sample.identity.sample_key,
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
            owner_backfill_xic_backend="raw",
            output_level="machine",
            peak_config=peak_config,
            owner_backfill_peak_fact_provenance_row_count=1,
        ),
        production_decisions=writer_inputs.production_decisions,
    )
    assert {path.name for path in writer_dir.iterdir() if path.is_file()} == {
        "alignment_matrix.tsv",
        "alignment_run_metadata.json",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
    }
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
def test_discovery_review_rows_cannot_become_alignment_roots(
    tmp_path: Path,
    monkeypatch,
    scenario: str,
) -> None:
    physical_records = {}
    above_threshold = scenario == "ambiguous_hold_above_threshold"
    ambiguous = scenario.startswith("ambiguous_hold_")
    stems = ("Support", "Hold", "Support2") if above_threshold else ("Support", "Hold")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(f"production-hold:{raw_path.stem}".encode())
    # Nearby rejected and supported observations must not share admission.
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)

    def observation_only_raw(peak_rts: tuple[float, ...]) -> _FakeRaw:
        # An inferred precursor retains MS1 but cannot supply strict NL support.
        raw = _FakeRaw(tuple(rt - 0.01 for rt in peak_rts))
        raw.scans = tuple(replace(scan, precursor_mz=500.5) for scan in raw.scans)
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
        physical_records=physical_records,
    )[0]
    direct_hold_candidates = _current_candidates(
        source_raws[raw_paths[1]],
        raw_file=raw_paths[1],
        settings=settings,
        physical_records=physical_records,
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
                    physical_records=physical_records,
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
            physical_records=physical_records,
        )
    from xic_extractor.untargeted_execution.execution import execute_untargeted_plan
    from xic_extractor.untargeted_execution.input_planning import (
        prepare_eager_untargeted_plan,
    )

    plan = prepare_eager_untargeted_plan(
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-hold",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates_by_sample).values()
            for c in values
        },
    )
    opened = install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )

    admitted = {
        c.candidate_id
        for values in candidates_by_sample.values()
        for c in values
        if c.discovery_candidate_state == "ms1_feature_nl_supported"
    }
    rejected = {
        c.candidate_id for values in candidates_by_sample.values() for c in values
    } - admitted
    assert (
        len(admitted)
        == {
            "mixed": 1,
            "all_hold": 0,
            "hold_no_input": 0,
            "support_no_input": 1,
            "all_reject": 0,
            "hold_reject": 0,
            "other_support": 2,
            "ambiguous_hold_below_threshold": 1,
            "ambiguous_hold_above_threshold": 2,
        }[scenario]
    )
    assert {cid for cid, _ in plan.subject_batch.candidate_memberships} == admitted
    opens_before = sum(map(len, opened.values()))
    completed = execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "admission-output",
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        raw_workers=1,
    )
    assert set(completed.application_result.candidate_ids) == admitted
    assert not completed.application_result.excluded_candidates
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    assert writer.matrix.sample_order == stems
    assert not rejected.intersection(
        cell.source_candidate_id for cell in writer.matrix.cells
    )
    if not admitted:
        assert not completed.application_result.children
        assert not writer.matrix.clusters and not writer.matrix.cells
        assert sum(map(len, opened.values())) == opens_before


@pytest.mark.parametrize("supported_samples", [2, 3])
def test_production_initial_groups_use_complete_feature_inference_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supported_samples: int,
) -> None:
    physical_records = {}
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
            raws[path],
            raw_file=path,
            settings=settings,
            physical_records=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, paths, candidates
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:canonical-batch",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
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
    complete_dense_partition(batch, runtime, alignment_config=AlignmentConfig())
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
        assert dict(cell_context.rt_frame.injection_order) == dict(
            zip(stems, (1, 2, 3), strict=True)
        )
        rebound = replace(cell_context, rt_frame=SampleRtFrame())
        assert rebound.group is cell_context.group
        assert rebound.supports is cell_context.supports


def test_known_istd_reference_closes_drifting_group_and_cell_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical_records = {}
    from openpyxl import Workbook

    from xic_extractor.applications.untargeted.istd_reference import (
        read_known_istd_reference,
    )

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
            physical_records=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, paths, candidates
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:known-istd",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates).values()
            for c in values
        },
    )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    runtime_plain = execute_dense_partition(
        batch,
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
    for context in contexts:
        assert context.rt_frame.drift_source == "batch_anchor_calibration"
        assert set(context.rt_frame.known_identity_bindings) == {
            (owner_id, "known") for owner_id in context.group.owner_ids
        }
        left, right = context.group.owners
        assert context.rt_frame.has_direct_reference_pair(left, right)
        # One identity anchor closes the known group, but cannot define a
        # general RT map or override the final shared coordinate owner.
        assert context.rt_frame.corrected_pair_delta_sec(left, right) is None
    without_rt_context = replace(contexts[0], rt_frame=SampleRtFrame())
    assert without_rt_context.group is contexts[0].group
    assert without_rt_context.supports is contexts[0].supports


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
        for position in coverage.positions
        if position.sample.metadata.raw_stem == "Hold"
    )
    proof = position.no_request_decision
    if threshold:
        assert proof is None
        assert position.planning_reason == "no_discovery_input"
    else:
        assert proof is not None
        assert proof.variant == "completed_backfill_empty"
        assert proof.decision.group_facts.direct_support_count_s == 3
        assert proof.decision.group_facts.minimum_support_k == 2
    (hold_cell,) = tuple(
        cell
        for cell in scientific.cell_observations
        if cell.canonical_sample.metadata.raw_stem == "Hold"
    )
    assert hold_cell.evidence_states == (
        ("not_searched",) if threshold else ("unavailable",)
    )
    if threshold:
        assert hold_cell.predecessor_cells == ()
        assert hold_cell.method_assessments == ()
    else:
        (source,) = hold_cell.predecessor_cells
        assert source is proof.decision.target_cell
        assert source.method_assessment is next(
            fixture_assessment(result)
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
        _assert_conservative_completion_projection(
            proof, position, coverage, handoff, hold_cell, batch, runtime_result
        )


def _assert_conservative_completion_projection(
    proof, position, coverage, handoff, final_cell, batch, runtime_result
) -> None:
    import json
    from copy import copy

    from xic_extractor.untargeted_execution.partition.models import (
        project_completed_backfill,
    )

    assert position.no_request_decision is proof
    assert position in coverage.positions
    assert position.planning_reason == proof.planning_reason
    assert final_cell.canonical_sample is position.sample
    assert any(item.position is position for item in handoff.position_associations)

    def canonical(value):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()

    result = next(
        item
        for item in runtime_result.sample_results
        if item.job.sample is position.sample
    )
    context = proof.decision.group_facts.group_context
    original_result = result.effective_assessments
    with pytest.raises(ValueError, match="not a backfill"):
        project_completed_backfill(
            replace(result, backfill_attempts=()), group_context=context
        )
    if proof.completion is not None:
        completion = proof.completion
        assert project_completed_backfill(result, group_context=context) == completion
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
    assert result.effective_assessments == original_result


@pytest.mark.parametrize("recovery_case", ("positive", "threshold", "completed_empty"))
def test_production_composition_closes_recovered_observation_without_candidate(
    tmp_path: Path,
    monkeypatch,
    recovery_case: str,
) -> None:
    physical_records = {}
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
                physical_records=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:production-recovery",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates_by_sample).values()
            for c in values
        },
    )
    opened = install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )

    runtime_result = execute_dense_partition(
        batch,
    )
    recovered = tuple(
        result
        for result in runtime_result.sample_results
        if result.job.sample.metadata.raw_stem == "Hold"
    )
    assert len(recovered) == 1
    assert (recovered[0].backfill_attempt is not None) == (recovery_case != "threshold")
    assert len(fixture_assessment(recovered[0]).candidate_peaks) == (
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
        if source.method_assessment is fixture_assessment(recovered[0])
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
    assert row.feature_id == "F000001"
    expected_presentation_facts: dict[str, tuple[float, float]] = {}
    expected_cell_apexes: dict[str, float] = {}
    diagnostic_observation_ids: set[str] = set()
    for child in completed.application_result.children:
        for projected in child.completed_projection.rows:
            group = next(
                group
                for group in child.scientific_core.final_population
                if group.group_hypothesis_id == projected.group.group_hypothesis_id
            )
            member_ids = {
                peak.physical_peak_key
                for member in group.member_subjects
                for peak in member.physical_peaks
            }
            for decision in projected.decisions:
                direct_observations = decision.evidence.direct_observations
                if direct_observations and decision.value is not None:
                    assert len(direct_observations) == 1
                    expected_cell_apexes[
                        decision.subject.canonical_sample.metadata.raw_stem
                    ] = direct_observations[0].content.integration.rt_apex_min
                for direct in decision.evidence.direct_observations:
                    physical_id = direct.content.physical_peak_key
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
    assert len(diagnostic_observation_ids) == 1
    assert not diagnostic_observation_ids & set(row.physical_peak_keys)
    assert len(expected_presentation_facts) == len(sample_stems) - 1
    assert set(row.physical_peak_keys) == set(expected_presentation_facts)
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
                owner_backfill_xic_backend="raw",
                output_level="machine",
                peak_config=peak_config,
                owner_backfill_peak_fact_provenance_row_count=1,
            ),
            production_decisions=decisions,
        )

        assert outputs.matrix_tsv is not None
        with outputs.matrix_tsv.open(encoding="utf-8", newline="") as handle:
            matrix_row = next(csv.DictReader(handle, delimiter="\t"))
        assert float(matrix_row["RT"]) == pytest.approx(row.family_center_rt)

        assert outputs.review_tsv is not None
        with outputs.review_tsv.open(encoding="utf-8", newline="") as handle:
            review_row = next(csv.DictReader(handle, delimiter="\t"))
        assert float(review_row["matrix_rt"]) == pytest.approx(row.family_center_rt)
        assert outputs.run_metadata_json is not None
        metadata = json.loads(outputs.run_metadata_json.read_text(encoding="utf-8"))
        assert metadata["matrix_center_rt_basis"] == row.center_rt_basis
        assert metadata["matrix_center_weight_basis"] == row.center_weight_basis


def test_atomic_publication_preserves_ms1_only_backfill_and_no_peak(
    tmp_path: Path,
    monkeypatch,
) -> None:
    physical_records = {}
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
                physical_records=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, raw_paths, candidates_by_sample
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:ms1-only-publication",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.candidate_id: physical_records[c.candidate_id]
            for values in (candidates_by_sample).values()
            for c in values
        },
    )
    install_runtime_raws(monkeypatch, sources_by_path=source_raws, tmp_path=tmp_path)

    runtime = execute_dense_partition(
        batch,
    )
    assert runtime.succeeded
    recovered = {
        result.job.sample.metadata.raw_stem: result for result in runtime.sample_results
    }
    assert recovered["MS1Only"].backfill_attempt is not None
    assert recovered["NoPeak"].backfill_attempt is not None
    assert fixture_assessment(recovered["MS1Only"]).content.method_result == "HOLD"
    assert fixture_assessment(recovered["NoPeak"]).content.method_result == "HOLD"
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
            owner_backfill_xic_backend="raw",
            output_level="machine",
            peak_config=peak_config,
            owner_backfill_peak_fact_provenance_row_count=1,
        ),
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
