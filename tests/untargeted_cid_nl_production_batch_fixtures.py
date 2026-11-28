"""Persisted CID-NL replay baseline, synthetic input builders and parity oracles.

The replay baseline is two samples, a single DNA_dR loss, indexed input,
RT 13.1–13.4 min and Gaussian15 processing. Scenario parameters explicitly
select group sizes, tag composition, nearby mass and physical-family context.
Other builders retain their own settings; they do not inherit replay defaults.
"""

from __future__ import annotations

from dataclasses import replace
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
    _settings,
)
from tests.cid_nl_runtime_fixtures import install_runtime_raws
from tests.discovery_physical_measurement_fixtures import measured_peak_evaluations
from tests.discovery_store_fixtures import SampleRecords, write_discovery
from tests.untargeted_cid_nl_batch_runtime_fixtures import _nearby_subject_candidates
from tests.untargeted_partition_fixtures import (
    project_composed_child,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.discovery.models import representative_event_rt
from xic_extractor.execution.process_memory import (
    ProcessMemorySample,
    ProcessTreeMemorySampler,
)
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.partition import workers as partition_workers
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedPartitionJob,
)


def _assert_jobs_match_direct_construction(batch) -> None:

    context = batch.context
    expected = tuple(
        UntargetedPartitionJob(
            processing_attempt_id=context.processing_attempt_id,
            method_subjects=context.method_subject,
            sample=sample,
            method_subject=subject,
            candidates=context.peaks_by_sample[sample.raw_stem],
            settings=context.settings,
            raw_path=context.raw_paths_by_sample[sample.raw_stem],
            dll_dir=batch.jobs[0].dll_dir,
            work_unit_key=(
                f"untargeted-cid-nl:{sample.raw_stem}:{subject.method_subject_id}"
            ),
            signal_processing_profile=context.signal_processing_profile,
            subject_partition=batch.jobs[0].subject_partition,
            acquisition_source_expectation=context.acquisition_sources_by_sample[
                sample.raw_stem
            ],
        )
        for sample in context.samples
        for subject in batch.subject_batch.subjects
    )
    assert batch.jobs == expected
    assert tuple(job._request_subjects for job in batch.jobs) == tuple(
        job._request_subjects for job in expected
    )


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


@pytest.fixture
def profile_replay(tmp_path, monkeypatch, request):
    """Run persisted Discovery through the real adapter; keep each test isolated.

    Only the end-to-end matrix test expands the eight scientific scenarios.
    The owner-boundary tests use the two-sample single-tag baseline.
    """
    group_sizes, method_mode, nearby, family_context, family_precursor_delta = (
        request.param
        if hasattr(request, "param")
        else ((2,), "single", False, False, 0.0)
    )

    from xic_extractor.acquisition.physical_capture import (
        PHYSICAL_CONTEXT_HALF_WINDOW_MIN,
    )
    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.discovery.ion_grouping import build_discovery_ions
    from xic_extractor.discovery.ms1_capture import (
        capture_ms1_peaks,
    )
    from xic_extractor.discovery.ms2_events import collect_strict_nl_events
    from xic_extractor.discovery.processing_run_spec_adapter import (
        compile_discovery_processing_run_spec,
    )
    from xic_extractor.discovery.store import read_discovery_batch, read_sample_peaks

    separate_groups = len(group_sizes) == 2
    sample_stems = tuple(f"S{index + 1}" for index in range(sum(group_sizes)))
    paths = tuple(tmp_path / f"{stem}.raw" for stem in sample_stems)
    for path in paths:
        path.write_bytes(path.stem.encode())
    center_rt = 13.263299286133334
    precursor_mz = 307.1023254394531
    settings = replace(
        _settings() if method_mode == "single" else _multi_tag_settings(),
        tag_combine_mode=method_mode,
        rt_min=13.1,
        rt_max=17.4 if separate_groups or family_precursor_delta else 13.4,
    )
    profile = SignalProcessingProfile(window_points=15)
    if family_context:
        settings = replace(
            settings,
            product_mz_tolerance_ppm=1.0,
            rt_min=12.8,
        )
    padding_min = max(settings.ms1_search_padding_min, PHYSICAL_CONTEXT_HALF_WINDOW_MIN)
    spec = compile_discovery_processing_run_spec(
        paths, discovery_settings=settings, signal_processing_profile=profile
    )

    source_raws = {}
    opened_raws = install_runtime_raws(
        monkeypatch, sources_by_path=source_raws, tmp_path=tmp_path
    )
    persisted = []
    candidates = {}
    physical_peaks = {}
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
                    precursor_mz=sample_mz,
                    masses=(
                        raw.scans[0].masses
                        if family_precursor_delta
                        else raw.scans[0].masses + index * 0.0003
                    ),
                )
                for index in scan_indexes
            )
        if family_precursor_delta:
            raw.additional_ms1_ions = (
                {precursor_mz + 0.00615: raw.intensity * 0.75}
                if sample_index == 3
                else {}
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
                ms1_centroid=lambda *_: None,
            )
        persisted.append(
            SampleRecords(
                path.stem,
                path,
                peaks=discovery.peaks,
                attachments=discovery.attachments,
                acquisition_source=source.acquisition_source,
            )
        )
        candidates[path.stem] = discovery.peaks
        evaluations = measured_peak_evaluations(discovery, profile)
        peak_evaluations = tuple(zip(discovery.peaks, evaluations, strict=True))
        source_records.update(
            (peak.peak_id, evaluation) for peak, evaluation in peak_evaluations
        )
        if family_context:
            assert len(candidates[path.stem]) == 1
            assert all(True for candidate in candidates[path.stem])
            windows = [
                (
                    evaluation.question.query_rt_min - padding_min,
                    evaluation.question.query_rt_min + padding_min,
                )
                for evaluation in evaluations
            ]
            if not family_precursor_delta:
                ((window_min, window_max),) = windows
                assert window_min <= min(query_rts)
                assert window_max >= max(query_rts)
                (candidate,) = discovery.peaks
                observed_nl_rts = tuple(seed.scan.rt_min for seed in seeds)
                assert representative_event_rt(candidate) in observed_nl_rts
            rt_min = min(window_min for window_min, _ in windows)
            rt_max = max(window_max for _, window_max in windows)
            vendor = opened_raws[path][0]
            start = vendor.ScanNumberFromRetentionTime(rt_min) - 1
            end = vendor.ScanNumberFromRetentionTime(rt_max)
            prepared = prepare_physical_signal(
                raw.rt[start:end], raw.intensity[start:end], profile=profile
            )
            for peak, evaluation in peak_evaluations:
                family_evaluations[peak.peak_id] = evaluate_physical_peak(
                    prepared,
                    query_rt_min=evaluation.question.query_rt_min,
                    request_context_rt_start_min=rt_min,
                    request_context_rt_end_min=rt_max,
                )
            family_peaks = {
                family_evaluations[peak.peak_id].answer.selected_segment
                for peak, _ in peak_evaluations
            }
            assert len(family_peaks) == 1
            assert None not in family_peaks
            continue
        (evaluation,) = evaluations
        assert evaluation.answer.selected_segment is not None
        physical_peaks[path.stem] = evaluation.answer.selected_segment
    discovery_batch = read_discovery_batch(
        write_discovery(
            tmp_path / "discovery",
            persisted,
            neutral_loss_by_tag=settings.neutral_loss_by_tag,
        )
    )
    candidates = {
        stem: read_sample_peaks(discovery_batch.path, stem) for stem in sample_stems
    }
    captured_batches = []
    from xic_extractor.untargeted_execution import execution as bounded_owner

    materialize_batch = bounded_owner.materialize_untargeted_partition

    def retain_batch(*args, **kwargs):
        batch = materialize_batch(*args, **kwargs)
        captured_batches.append(batch)
        return batch

    monkeypatch.setattr(bounded_owner, "materialize_untargeted_partition", retain_batch)
    from xic_extractor.alignment import cid_nl_initial_grouping as production
    from xic_extractor.alignment import (
        cid_nl_predecessor_cells as predecessor_cell_owner,
    )
    from xic_extractor.alignment import completion as workflow_owner
    from xic_extractor.alignment import grouping_bootstrap as bootstrap_owner

    projection_builds = []
    project_matrix = workflow_owner.project_untarget_matrix

    def record_matrix_projection(*args):
        result = project_matrix(*args)
        projection_builds.append(result)
        return result

    monkeypatch.setattr(
        workflow_owner, "project_untarget_matrix", record_matrix_projection
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
                    kwargs["sample"].sample_name,
                    assessment,
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
    consumed_contexts = []
    retained_terminals = []
    consumed_terminals = []
    retained_band_members = []
    retained_cell_batches = []
    build_initial_context = production.build_initial_cid_nl_cross_sample_groups
    regroup_initial_context = production.regroup_cid_nl_cross_sample_groups
    compose_predecessor_cells = cid_nl_completion_owner.compose_cid_nl_predecessor_cells
    admit_backfills = cid_nl_completion_owner.admit_cid_nl_group_backfills
    finalize_scientific = workflow_owner.finalize_scientific_alignment
    workset_grouping = production.CidNlWorksetGrouping

    def retain_initial_context(**kwargs):
        grouping_scope, context = build_initial_context(**kwargs)
        bootstrap_contexts.append(context)
        retained_band_members.extend(
            owner for group in grouping_scope.groups for owner in group.owners
        )
        return grouping_scope, context

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

    def retain_workset_terminals(**kwargs):
        retained_terminals.extend(kwargs["terminal_subjects"])
        return workset_grouping(**kwargs)

    def retain_scientific_terminals(**kwargs):
        consumed_contexts.append(kwargs["grouping_result"])
        consumed_terminals.extend(kwargs["terminal_subjects"])
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
        workflow_owner, "finalize_scientific_alignment", retain_scientific_terminals
    )
    monkeypatch.setattr(production, "CidNlWorksetGrouping", retain_workset_terminals)

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
                    tuple(
                        (event.scan.scan_number, event.scan.rt_min)
                        for event in candidate.located_events
                    ),
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
                stem = sample_result.job.sample.raw_stem
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
                expected_capture_candidate_ids = {
                    candidate.peak_id
                    for candidate in candidates[stem]
                    if (
                        candidate.peak_id in expected_source_candidate_ids
                        and candidate.status in {"admitted", "no_nl_event"}
                    )
                }
                assessment = adapter.assessment_input.assessment
                if assessment.candidate_peaks:
                    assert all(
                        peak.source_trace.sample_name
                        == sample_result.job.sample.sample_name
                        for peak in assessment.candidate_peaks
                    )
                observations = dict(adapter.candidate_observations)
                if set(observations) != expected_capture_candidate_ids:
                    raise AssertionError(
                        (
                            stem,
                            sample_result.job.method_subject.method_subject_id,
                            sorted(observations),
                            sorted(expected_capture_candidate_ids),
                            assessment.method_result,
                            assessment.reason_codes,
                            tuple(
                                (peak.mz, peak.integration.rt_apex_min)
                                for peak in assessment.candidate_peaks
                            ),
                        )
                    )
                for candidate_id, observation in observations.items():
                    expected = family_evaluations[candidate_id]
                    measured = observation.physical_evaluation
                    if family_precursor_delta:
                        assert measured.answer.selected_segment is not None
                        assert expected.answer.selected_segment is not None
                        assert (
                            measured.answer.selected_segment.apex_rt_min
                            == pytest.approx(
                                expected.answer.selected_segment.apex_rt_min, abs=0.003
                            )
                        )
                    else:
                        assert measured == source_records[candidate_id]
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
        peaks_by_sample=(DiscoveryInputSource(discovery_batch)),
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "alignment",
        settings=settings,
        alignment_config=AlignmentConfig(),
        signal_processing_profile=profile,
        timing_recorder=TimingRecorder.disabled(
            "alignment",
            process_memory_sampler=ProcessTreeMemorySampler(
                sample_reader=lambda: ProcessMemorySample(123456, 654321, "measured"),
            ),
        ),
        project_child=project_composed_child,
        execution=ExecutionProfile(backend="thread"),
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
            ),
            inputs=SimpleNamespace(
                candidates=candidates,
                paths=paths,
                center_rt=center_rt,
                profile=profile,
                settings=settings,
                sample_stems=sample_stems,
                separate_groups=separate_groups,
                source_raws=source_raws,
                opened_raws=opened_raws,
                source_records=source_records,
                physical_peaks=physical_peaks,
            ),
            trace=SimpleNamespace(
                captured_batches=captured_batches,
                completed_contexts=completed_contexts,
                consumed_terminals=consumed_terminals,
                consumed_contexts=consumed_contexts,
                hypothesis_universe_compositions=hypothesis_universe_compositions,
                obsolete_marker=obsolete_marker,
                preparation_fingerprints=preparation_fingerprints,
                projection_builds=projection_builds,
                retained_band_members=retained_band_members,
                retained_terminals=retained_terminals,
                retained_cell_batches=retained_cell_batches,
                retained_contexts=retained_contexts,
                runtime_fingerprints=runtime_fingerprints,
                runtime_results=runtime_results,
            ),
        )
    finally:
        completed.close()


def _source_verification_inputs(tmp_path: Path):
    path = tmp_path / "S1.synthetic"
    path.write_bytes(b"source-verification:S1")
    settings = replace(_settings(), precursor_mz_tolerance_ppm=1.0)
    raw, candidates = _nearby_subject_candidates(
        path,
        settings=settings,
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
                    sample_role="sample",
                ),
            ),
            settings=settings,
            peaks_by_sample={"S1": candidates},
            dll_dir=tmp_path,
            signal_processing_profile=TEST_PROFILE,
        ),
    )
