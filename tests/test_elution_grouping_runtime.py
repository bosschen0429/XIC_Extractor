"""A crowded MS1 doublet crosses acquisition, grouping and missing-cell lookup."""

from dataclasses import replace

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import _FakeRaw, _open_bound_raw, _peak_config, _settings
from tests.cid_nl_runtime_fixtures import build_discovery_receipts, install_runtime_raws
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.ion_grouping import build_discovery_ions
from xic_extractor.discovery.ms1_capture import (
    capture_ms1_peaks,
)
from xic_extractor.discovery.ms2_events import collect_strict_nl_events
from xic_extractor.execution.result_store import StreamedResultStore
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.execution import (
    UntargetedPartitionPipelineAdapter,
)
from xic_extractor.untargeted_execution.input_planning import (
    prepare_eager_untargeted_plan,
)


@pytest.mark.parametrize("missing_pattern_location", [False, True])
def test_crowded_doublet_uses_context_before_grouping_and_preserves_query_lineage(
    tmp_path, monkeypatch, missing_pattern_location
):
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={f"S{i}": "study_sample" for i in range(4)},
        thermo_raw_sources=True,
    )
    settings = replace(
        _settings(),
        neutral_loss_profile=None,
        rt_min=7.0,
        rt_max=10.0,
        precursor_mz_tolerance_ppm=20.0,
    )
    profile = SignalProcessingProfile()
    config = _peak_config(tmp_path)
    raws, candidates, records = {}, {}, {}
    expected_initial = {}
    expected_query = {}
    shifts = (
        ((0.0, 0), (0.3, 1), (0.6, 0), (0.9, 1))
        if missing_pattern_location
        else ((0.0, 0), (0.05, 1), (0.6, 0), (0.65, 1))
    )
    for index, (shift, selected) in enumerate(shifts):
        sample = f"S{index}"
        path = fixture.source_paths[sample]
        apices = (7.8 + shift, 8.4 + shift)
        expected_initial[sample] = apices[selected]
        expected_query[sample] = apices[1 - selected]
        source = _FakeRaw((apices[selected],))
        source.rt = np.linspace(7.0, 10.0, 601)
        source.intensity = sum(
            height * np.exp(-0.5 * ((source.rt - apex) / 0.035) ** 2)
            for height, apex in zip((600.0, 1000.0), apices, strict=True)
        )
        source.scans = tuple(
            replace(scan, scan_number=int(np.argmin(abs(source.rt - scan.rt))) + 1)
            for scan in source.scans
        )
        raw, _ = _open_bound_raw(fixture, sample, source, tmp_path=tmp_path)
        with raw:
            seeds = collect_strict_nl_events(raw, raw_file=path, settings=settings)
            discovery = capture_ms1_peaks(
                raw,
                build_discovery_ions(seeds, settings=settings),
                acquisition_source=raw.acquisition_source,
                settings=settings,
                signal_processing_profile=profile,
                ms1_centroid=lambda *_: None,
            )
        assert len(discovery.peaks) == 1
        candidates[sample] = discovery.peaks
        records.update(
            {record.peak_id: record for record in discovery.physical_evaluations}
        )
        raws[path] = source
    paths = tuple(fixture.source_paths.values())
    plan = prepare_eager_untargeted_plan(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=index + 1,
                sample_role="study_sample",
            )
            for index, path in enumerate(paths)
        ),
        settings=settings,
        peak_config=config,
        peaks_by_sample=candidates,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, paths, candidates
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:elution-pattern",
        signal_processing_profile=profile,
        physical_records_by_peak=records,
    )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    import xic_extractor.untargeted_execution.partition.workers as workers

    monkeypatch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)
    adapter = UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        max_partition_jobs=100,
    )
    store = StreamedResultStore(
        tmp_path / "elution-spool", adapter.get_shared_objects()
    )
    initial_phase, grouping_phase, backfill_phase = adapter.execution_phases()
    assert len(initial_phase.partitions) == len(backfill_phase.partitions) == 1
    initial_partition = initial_phase.partitions[0]
    initial = adapter.execute_acquisition(initial_partition)
    assert initial.succeeded
    initial_grouping = initial.result[1].prepared_grouping
    assert initial_grouping is not None
    assert len(initial_grouping.rt_anchor_groups) == 2
    if missing_pattern_location:
        # The final sample has no local pattern position for the missing peak.
        # Other samples still supply its MS1 trajectory, including S1 where
        # that peak has no method-supported owner. Do not discard those points
        # and restart a prediction from only the sparse supported owners.
        initial_grouping = replace(
            initial_grouping,
            worksets=tuple(
                replace(
                    workset,
                    grouping_scope=replace(
                        workset.grouping_scope,
                        peak_trajectories=replace(
                            workset.grouping_scope.peak_trajectories,
                            tracks=tuple(
                                replace(
                                    track,
                                    sample_apices=tuple(
                                        (sample, apex)
                                        for sample, apex in track.sample_apices
                                        if sample != "S3"
                                    ),
                                )
                                for track in (
                                    workset.grouping_scope.peak_trajectories.tracks
                                )
                            ),
                        ),
                    ),
                )
                for workset in initial_grouping.worksets
            ),
        )
    states = [
        workers._RuntimeJobState(
            job=result.job,
            initial_requires_raw=False,
            adapter_result=result.adapter_result,
        )
        for result in initial.result[1].sample_results
    ]
    workers._prepare_group_authorized_backfills(
        states,
        prepared=initial_grouping,
    )
    for state in states:
        (target,) = state.backfill_targets
        if not missing_pattern_location:
            assert target.query_rt_basis == "trajectory_observed"
        assert target.query_rt == pytest.approx(
            expected_query[state.job.sample.metadata.raw_stem], abs=0.005
        )
    initial_composed = adapter.execute_composition(initial_partition, initial, store)
    assert grouping_phase.prepare_before is not None
    grouping_phase.prepare_before(store, lambda: None)
    partition = backfill_phase.partitions[0]
    acquisition = adapter.execute_acquisition(partition)
    assert acquisition.succeeded
    _, leaf = acquisition.result
    prepared = leaf.prepared_grouping
    assert prepared is not None
    groups = tuple(
        group
        for workset in prepared.worksets
        for group in workset.grouping_scope.groups
    )
    assert {
        frozenset(owner.sample_stem for owner in group.owners) for group in groups
    } == {frozenset(("S0", "S2")), frozenset(("S1", "S3"))}
    assert {
        owner.sample_stem: owner.owner_apex_rt
        for group in groups
        for owner in group.owners
    } == pytest.approx(expected_initial, abs=0.005)
    group_ids = {group.group_hypothesis_id for group in groups}
    observed_queries = {}
    for result in leaf.sample_results:
        sample = result.job.sample.metadata.raw_stem
        assert result.adapter_result.elution_contexts
        assert len(result.backfill_attempts) == 1
        attempt = result.backfill_attempts[0]
        assert attempt.group_hypothesis_id in group_ids
        assert attempt.query_rt_basis == "trajectory_observed"
        assert attempt.query_rt == pytest.approx(expected_query[sample], abs=0.005)
        assert attempt.assessment.content.method_result == "HOLD"
        assert not attempt.assessment.individual_support_refs
        (peak,) = attempt.assessment.candidate_peaks
        observed_queries[sample] = peak.content.integration.rt_apex_min
    assert observed_queries == pytest.approx(expected_query, abs=0.005)
    composed = adapter.execute_composition(
        partition, acquisition, store, retain_memory=True
    )
    persisted = store.read(composed.sealed.index)
    assert {
        group.group_hypothesis_id for group in persisted.initial_groups
    } == group_ids
    assert {
        group.group_hypothesis_id
        for child in persisted.children
        for group in child.scientific_core.grouping_result.final_population
    } == group_ids
    store.seal()
    completed = adapter.finalize_application(store, (initial_composed, composed))
    writer = project_completed_untarget_batch_writer_inputs(completed)
    assert len(writer.matrix.cells) == 8
    direct_samples = {
        group.group_hypothesis_id: {owner.sample_stem for owner in group.owners}
        for group in groups
    }
    for cell in writer.matrix.cells:
        direct = cell.sample_stem in direct_samples[cell.group_hypothesis_id]
        expected_apex = (
            expected_initial[cell.sample_stem]
            if direct
            else expected_query[cell.sample_stem]
        )
        assert cell.status == ("detected" if direct else "backfilled")
        assert cell.apex_rt == pytest.approx(expected_apex, abs=0.005)
        assert cell.area is not None and cell.area > 0


def test_review_only_input_leaves_each_group_cell_for_independent_backfill(
    tmp_path, monkeypatch
):
    # Three same-m/z peaks; S0-S5 support two samples per peak. S6 shows all
    # three in MS1 but its only MS2 event infers the precursor at the first
    # peak, so that group reuses S6's initial HOLD while the other two search.
    group_apices = (7.80, 8.25, 8.70)
    samples = tuple(f"S{i}" for i in range(7))
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={sample: "study_sample" for sample in samples},
        thermo_raw_sources=True,
    )
    settings = replace(
        _settings(),
        neutral_loss_profile=None,
        rt_min=7.0,
        rt_max=10.0,
        precursor_mz_tolerance_ppm=20.0,
    )
    profile = SignalProcessingProfile()
    config = _peak_config(tmp_path)
    raws, candidates, records = {}, {}, {}
    for index, sample in enumerate(samples):
        path = fixture.source_paths[sample]
        ms2_apex = group_apices[0 if sample == "S6" else index // 2]
        source = _FakeRaw((ms2_apex,))
        if sample == "S6":
            source.scans = tuple(
                replace(scan, precursor_mz=scan.precursor_mz + 0.5)
                for scan in source.scans
            )
        source.rt = np.linspace(7.0, 10.0, 601)
        source.intensity = sum(
            1000.0 * np.exp(-0.5 * ((source.rt - apex) / 0.035) ** 2)
            for apex in group_apices
        )
        source.scans = tuple(
            replace(scan, scan_number=int(np.argmin(abs(source.rt - scan.rt))) + 1)
            for scan in source.scans
        )
        raw, _ = _open_bound_raw(fixture, sample, source, tmp_path=tmp_path)
        with raw:
            seeds = collect_strict_nl_events(raw, raw_file=path, settings=settings)
            discovery = capture_ms1_peaks(
                raw,
                build_discovery_ions(seeds, settings=settings),
                acquisition_source=raw.acquisition_source,
                settings=settings,
                signal_processing_profile=profile,
                ms1_centroid=lambda *_: None,
            )
        assert len(discovery.peaks) == 1
        candidates[sample] = discovery.peaks
        records.update(
            {record.peak_id: record for record in discovery.physical_evaluations}
        )
        raws[path] = source
    assert candidates["S6"][0].precursor_mz_basis == "product_plus_neutral_loss"
    paths = tuple(fixture.source_paths.values())
    plan = prepare_eager_untargeted_plan(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=index + 1,
                sample_role="study_sample",
            )
            for index, path in enumerate(paths)
        ),
        settings=settings,
        peak_config=config,
        peaks_by_sample=candidates,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, paths, candidates
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:hold-reuse-beside-searches",
        signal_processing_profile=profile,
        physical_records_by_peak=records,
    )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    import xic_extractor.untargeted_execution.partition.workers as workers

    monkeypatch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)
    adapter = UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        max_partition_jobs=100,
    )
    store = StreamedResultStore(tmp_path / "reuse-spool", adapter.get_shared_objects())
    initial_phase, grouping_phase, backfill_phase = adapter.execution_phases()
    initial_partition = initial_phase.partitions[0]
    initial = adapter.execute_acquisition(initial_partition)
    assert initial.succeeded
    assert not any(
        result.job.sample.metadata.raw_stem == "S6"
        for result in initial.result[1].sample_results
    )
    initial_composed = adapter.execute_composition(initial_partition, initial, store)
    assert grouping_phase.prepare_before is not None
    grouping_phase.prepare_before(store, lambda: None)
    partition = backfill_phase.partitions[0]
    acquisition = adapter.execute_acquisition(partition)
    assert acquisition.succeeded
    _, leaf = acquisition.result
    prepared = leaf.prepared_grouping
    assert prepared is not None
    groups = tuple(
        group
        for workset in prepared.worksets
        for group in workset.grouping_scope.groups
    )
    group_apex = {
        group.group_hypothesis_id: min(
            group_apices, key=lambda apex: abs(apex - group.group_center_rt)
        )
        for group in groups
    }
    assert sorted(group_apex.values()) == list(group_apices)
    (s6,) = (
        result
        for result in leaf.sample_results
        if result.job.sample.metadata.raw_stem == "S6"
    )
    assert sorted(
        group_apex[attempt.group_hypothesis_id] for attempt in s6.backfill_attempts
    ) == list(group_apices)
    composed = adapter.execute_composition(
        partition, acquisition, store, retain_memory=True
    )
    store.seal()
    completed = adapter.finalize_application(store, (initial_composed, composed))
    writer = project_completed_untarget_batch_writer_inputs(completed)
    assert len(writer.matrix.cells) == 21
    for cell in writer.matrix.cells:
        apex = group_apex[cell.group_hypothesis_id]
        if cell.sample_stem == "S6":
            assert cell.source_candidate_id is None
        direct = cell.sample_stem in samples[2 * group_apices.index(apex) :][:2]
        assert cell.status == ("detected" if direct else "backfilled")
        assert cell.apex_rt == pytest.approx(apex, abs=0.005)
        assert cell.area is not None and cell.area > 0
