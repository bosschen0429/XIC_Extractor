"""Persisted CID-NL replay baseline, synthetic input builders and parity oracles.

The replay baseline is two samples, a single DNA_dR loss, eager/indexed input,
RT 13.1–13.4 min and Gaussian15 processing. Scenario parameters explicitly
select group sizes, tag composition, nearby mass and physical-family context.
Other builders retain their own settings; they do not inherit replay defaults.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.cid_nl_completion as cid_nl_completion_owner
import xic_extractor.processing_identity as identity_owner
import xic_extractor.raw_source_binding as raw_source_binding_owner
import xic_extractor.untargeted_execution.alignment as alignment_adapter
from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _FakeRaw,
    _multi_tag_settings,
    _peak_config,
    _settings,
)
from tests.cid_nl_runtime_fixtures import (
    build_discovery_receipts,
    install_runtime_raws,
)
from tests.untargeted_cid_nl_batch_runtime_fixtures import _nearby_subject_candidates
from tests.untargeted_partition_fixtures import (
    fixture_assessment,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.execution.process_memory import (
    ProcessMemorySample,
    ProcessTreeMemorySampler,
)
from xic_extractor.execution.timing import TimingRecorder
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
from xic_extractor.untargeted_execution.partition import models as partition_models
from xic_extractor.untargeted_execution.partition import workers as partition_workers
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedPartitionJob,
)
from xic_extractor.untargeted_execution.partition_composition import (
    compose_untargeted_partition,
)


def _physical_subject_facts(candidates, records):
    facts = []
    for candidate in candidates:
        record = records.get(candidate.peak_id)
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
                candidate_id=candidate.peak_id,
                physical_mz=candidate.peak_mz,
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
        (sample.identity.sample_key, memberships[candidate.peak_id])
        for sample in context.samples
        for candidate in context.peaks_by_sample[sample.metadata.raw_stem]
        if candidate.peak_id in memberships
    }
    expected = tuple(
        UntargetedPartitionJob(
            processing_owners=context.processing_owners,
            sample=sample,
            method_subject=subject,
            candidates=context.peaks_by_sample[sample.metadata.raw_stem],
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
                    context.physical_records_by_peak.items()
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
            if memberships.get(record.peak_id) == job.method_subject.method_subject_id
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
        read_owner_bound_discovery_peaks_csv,
    )
    from xic_extractor.discovery.csv_writer import (
        write_discovery_events_csv,
    )
    from xic_extractor.discovery.ion_grouping import build_discovery_ions
    from xic_extractor.discovery.ms1_capture import (
        capture_ms1_peaks,
    )
    from xic_extractor.discovery.ms2_events import collect_strict_nl_events
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
    event_csvs = {}
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
            seeds = collect_strict_nl_events(
                discovery_raw, raw_file=path, settings=settings
            )
            discovery = capture_ms1_peaks(
                discovery_raw,
                build_discovery_ions(seeds, settings=settings),
                acquisition_source=source.acquisition_source,
                settings=settings,
                signal_processing_profile=profile,
                ms1_centroid_mz=lambda *_: None,
            )
        folder = tmp_path / path.stem
        folder.mkdir()
        persisted_candidates = discovery.peaks
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
        event_csvs[path.stem] = write_discovery_events_csv(
            folder / "discovery_events.csv", discovery.attachments
        )
        candidates[path.stem] = tuple(
            candidate
            for candidate in read_owner_bound_discovery_peaks_csv(
                csvs[path.stem], receipt=receipts[path.stem]
            )
        )
        source_evidence.update(
            (record.peak_id, record) for record in discovery.physical_evaluations
        )
        source_records.update(
            (record.peak_id, record)
            for record in discovery.physical_evaluations
            if record.peak_id in {c.peak_id for c in candidates[path.stem]}
        )
        if family_context:
            # Identical mass queries now share the complete peak population in
            # Discovery; the same peak is not left as two window-bound roots.
            assert len(candidates[path.stem]) == 1
            assert all(True for candidate in candidates[path.stem])
            if not family_precursor_delta:
                # Both NL receipts identify one measured peak, not two records.
                (record,) = discovery.physical_evaluations
                assert record.work_item.xic_rt_min <= min(query_rts)
                assert record.work_item.xic_rt_max >= max(query_rts)
                (candidate,) = discovery.peaks
                # The physical raw apex must not replace the retained NL time.
                observed_nl_rts = tuple(seed.rt for seed in seeds)
                assert candidate.best_event_rt in observed_nl_rts
                assert record.work_item.query_rt_min not in observed_nl_rts
                assert candidate.ms1_event_delta_min == pytest.approx(
                    candidate.ms1_apex_rt - candidate.best_event_rt
                )
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
                family_evaluations[record.peak_id] = evaluate_physical_peak(
                    prepared,
                    query_rt_min=record.evaluation.question.query_rt_min,
                    request_context_rt_start_min=rt_min,
                    request_context_rt_end_min=rt_max,
                )
            assert (
                len(
                    {
                        family_evaluations[record.peak_id].answer.selected_segment
                        for record in discovery.physical_evaluations
                        if family_evaluations[record.peak_id].facts is not None
                    }
                )
                == 1
            )
            assert all(
                family_evaluations[record.peak_id].facts is not None
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
                candidate.peak_id,
                candidate.configured_neutral_loss_da,
                source_records[candidate.peak_id].work_item.acquisition_source_id,
                source_records[candidate.peak_id].evaluation.facts.apex_rt_min,
                source_records[candidate.peak_id].evaluation.facts.left_rt_min,
                source_records[candidate.peak_id].evaluation.facts.right_rt_min,
            )
            for sample_candidates in candidates.values()
            for candidate in sample_candidates
        )
        assert all(
            len(
                {
                    source_records[candidate.peak_id].evaluation.facts.apex_rt_min
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
        peak_csvs=csvs,
        raw_files={path.stem: path for path in paths},
        event_csvs=event_csvs,
        peak_receipts=receipts,
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
                    candidate.peak_id,
                    candidate.event_scan_ids,
                    candidate.event_rt_min,
                    candidate.event_rt_max,
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
                    candidate.peak_id
                    for candidate in candidates[stem]
                    if dict(
                        captured_batches[0].subject_batch.candidate_memberships
                    ).get(candidate.peak_id)
                    == sample_result.job.method_subject.method_subject_id
                }
                assert (
                    set(adapter.source_candidate_ids) == expected_source_candidate_ids
                )
                assert (
                    captured_batches[0].context.physical_records_by_peak
                    == source_records
                )
                expected_capture_candidate_ids = {
                    candidate.peak_id
                    for candidate in candidates[stem]
                    if (
                        candidate.peak_id in expected_source_candidate_ids
                        and candidate.is_clean_peak
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
        peaks_by_sample=(
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


def _subject_proof_record(
    candidate: DiscoveryPeak,
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
            xic_mz=candidate.best_event_precursor_mz if xic_mz is None else xic_mz,
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
            peaks_by_sample={"S1": candidates},
            discovery_receipts_by_sample=build_discovery_receipts(
                tmp_path, (path,), {"S1": candidates}
            ),
            dll_dir=tmp_path,
            sample_identity_namespace="study:source-verification",
            signal_processing_profile=TEST_PROFILE,
            physical_records_by_peak=physical_records,
        ),
    )


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
