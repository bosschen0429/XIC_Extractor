"""Co-acquired peaks retain their own MS1 and CID-NL evidence."""

from dataclasses import replace

import numpy as np
import pytest

from tests.alignment_pipeline_helpers import (
    written_cells,
    written_source_candidates,
)
from tests.cid_nl_raw_fixture import _FakeRaw, _open_bound_raw, _settings
from tests.cid_nl_runtime_fixtures import (
    install_runtime_raws,
    prepare_persisted_discovery_plan,
)
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from tests.untargeted_partition_fixtures import project_composed_child
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.configuration.execution_profile import in_process_execution
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.ion_grouping import build_discovery_ions
from xic_extractor.discovery.ms1_capture import (
    capture_ms1_peaks,
)
from xic_extractor.discovery.ms2_events import collect_strict_nl_events
from xic_extractor.execution.result_store import StreamedResultStore
from xic_extractor.output.alignment.tsv_writer import cell_status
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.execution import (
    UntargetedPartitionPipelineAdapter,
)


@pytest.mark.parametrize("staggered", [False, True])
def test_crowded_doublet_keeps_both_physical_peaks_and_local_cid_lineage(
    tmp_path, monkeypatch, staggered
):
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={f"S{i}": "sample" for i in range(4)},
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
    raws, candidates = {}, {}
    expected_initial = {}
    expected_apices = {}
    shifts = (
        ((0.0, 0), (0.10, 1), (0.20, 0), (0.30, 1))
        if staggered
        else ((0.0, 0), (0.05, 1), (0.10, 0), (0.15, 1))
    )
    for index, (shift, selected) in enumerate(shifts):
        sample = f"S{index}"
        path = fixture.source_paths[sample]
        apices = (7.8 + shift, 8.4 + shift)
        expected_initial[sample] = apices[selected]
        expected_apices[sample] = apices
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
        raws[path] = source
    paths = tuple(fixture.source_paths.values())
    plan = prepare_persisted_discovery_plan(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=index + 1,
                sample_role="sample",
            )
            for index, path in enumerate(paths)
        ),
        settings=settings,
        peaks_by_sample=candidates,
        root=tmp_path / "persisted",
        dll_dir=tmp_path,
        signal_processing_profile=profile,
    )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    import xic_extractor.untargeted_execution.partition.workers as workers

    monkeypatch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)
    adapter = UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=AlignmentConfig(),
        max_partition_jobs=100,
        project_child=project_composed_child,
        execution=in_process_execution(),
    )
    store = StreamedResultStore(
        tmp_path / "elution-spool", adapter.get_shared_objects()
    )
    initial_phase, grouping_phase, backfill_phase = adapter.execution_phases()
    assert len(initial_phase.partitions) == 1
    initial_partition = initial_phase.partitions[0]
    initial = adapter.execute_acquisition(initial_partition)
    assert initial.succeeded
    initial_grouping = initial.result[1].prepared_grouping
    assert initial_grouping is not None
    assert initial_grouping.rt_anchor_observations
    for result in initial.result[1].sample_results:
        sample = result.job.sample.raw_stem
        assessment = result.adapter_result.assessment_input.assessment
        assert [
            peak.integration.rt_apex_min for peak in assessment.candidate_peaks
        ] == pytest.approx(expected_apices[sample], abs=0.005)
        (relation,) = assessment.individual_support_refs
        assert relation.peak_observation.integration.rt_apex_min == pytest.approx(
            expected_initial[sample], abs=0.005
        )
    initial_composed = adapter.execute_composition(initial_partition, initial, store)
    assert grouping_phase.prepare_before is not None
    grouping_phase.prepare_before(store, lambda: None)
    final = store.read_header_field(adapter._final_groupings[0].index, "grouping")
    prepared = final.prepared
    groups = tuple(
        group
        for workset in prepared.worksets
        for group in workset.grouping_scope.groups
    )
    assert len(groups) == 2
    groups = sorted(groups, key=lambda group: group.group_center_rt)
    for lobe, group in enumerate(groups):
        assert {
            owner.sample_stem: owner.peak.integration.rt_apex_min
            for owner in group.owners
        } == pytest.approx(
            {sample: apices[lobe] for sample, apices in expected_apices.items()},
            abs=0.005,
        )
    group_ids = {group.group_hypothesis_id for group in groups}
    assert not final.backfill_questions
    assert backfill_phase.prepare_before is not None
    backfill_phase.prepare_before(store, lambda: None)
    (composed,) = adapter._backfill_partitions
    persisted = store.read(composed.sealed.index)
    assert {
        group.group_hypothesis_id for group in persisted.initial_groups
    } == group_ids
    assert {
        group.group_hypothesis_id
        for output in persisted.child_outputs
        for group in output.child.scientific_core.grouping_result.final_population
    } == group_ids
    store.seal()
    completed = adapter.finalize_application(store, (initial_composed,))
    writer = completed
    cells = tuple(written_cells(writer))
    assert len(cells) == 8
    group_lobes = {group.group_hypothesis_id: lobe for lobe, group in enumerate(groups)}
    for row, sample_stem, cell in cells:
        expected_apex = expected_apices[sample_stem][
            group_lobes[row.group_hypothesis_id]
        ]
        direct = expected_apex == expected_initial[sample_stem]
        assert cell_status(cell) == ("detected" if direct else "backfilled")
        assert cell.selected_integration.rt_apex_min == pytest.approx(
            expected_apex, abs=0.005
        )
        assert cell.value is not None and cell.value > 0


def test_review_only_input_supplies_each_physical_member_without_cid(
    tmp_path, monkeypatch
):
    group_apices = (7.80, 8.25, 8.70)
    samples = tuple(f"S{i}" for i in range(7))
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={sample: "sample" for sample in samples},
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
    raws, candidates = {}, {}
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
        raws[path] = source
    assert candidates["S6"][0].precursor_mz_basis == "product_plus_neutral_loss"
    paths = tuple(fixture.source_paths.values())
    plan = prepare_persisted_discovery_plan(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=index + 1,
                sample_role="sample",
            )
            for index, path in enumerate(paths)
        ),
        settings=settings,
        peaks_by_sample=candidates,
        root=tmp_path / "persisted",
        dll_dir=tmp_path,
        signal_processing_profile=profile,
    )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    import xic_extractor.untargeted_execution.partition.workers as workers

    monkeypatch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)
    adapter = UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=AlignmentConfig(),
        max_partition_jobs=100,
        project_child=project_composed_child,
        execution=in_process_execution(),
    )
    store = StreamedResultStore(tmp_path / "reuse-spool", adapter.get_shared_objects())
    initial_phase, grouping_phase, backfill_phase = adapter.execution_phases()
    initial_partition = initial_phase.partitions[0]
    initial = adapter.execute_acquisition(initial_partition)
    assert initial.succeeded
    (s6_initial,) = (
        result.adapter_result.assessment_input.assessment
        for result in initial.result[1].sample_results
        if result.job.sample.raw_stem == "S6"
    )
    assert [
        peak.integration.rt_apex_min for peak in s6_initial.candidate_peaks
    ] == pytest.approx(group_apices, abs=0.005)
    assert not s6_initial.individual_support_refs
    initial_composed = adapter.execute_composition(initial_partition, initial, store)
    assert grouping_phase.prepare_before is not None
    grouping_phase.prepare_before(store, lambda: None)
    final = store.read_header_field(adapter._final_groupings[0].index, "grouping")
    prepared = final.prepared
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
    assert not final.backfill_questions
    assert backfill_phase.prepare_before is not None
    backfill_phase.prepare_before(store, lambda: None)
    store.seal()
    completed = adapter.finalize_application(store, (initial_composed,))
    writer = completed
    cells = tuple(written_cells(writer))
    sources = written_source_candidates(tmp_path / "written", writer)
    assert len(cells) == 21
    for row, sample_stem, cell in cells:
        apex = group_apex[row.group_hypothesis_id]
        if sample_stem == "S6":
            assert not sources[row.group_hypothesis_id, sample_stem]
        direct = sample_stem in samples[2 * group_apices.index(apex) :][:2]
        assert cell_status(cell) == ("detected" if direct else "backfilled")
        assert cell.selected_integration.rt_apex_min == pytest.approx(apex, abs=0.005)
        assert cell.value is not None and cell.value > 0
