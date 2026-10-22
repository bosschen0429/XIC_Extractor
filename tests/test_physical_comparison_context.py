"""Missing context closes physical claims through their production consumers."""

import json
from dataclasses import replace

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _FakeRaw,
    _peak_config,
    _settings,
)
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _discovery_receipts,
    _install_runtime_raws,
)
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.ms1_capture import capture_ms1_candidates
from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
from xic_extractor.discovery.physical_fact_persistence import (
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.peak_detection.physical_peak_equivalence import (
    PhysicalPeakView,
    compare_physical_peak_views,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.alignment import (
    prepare_untargeted_alignment_inputs,
)
from xic_extractor.untargeted_execution.execution import (
    UntargetedPartitionPipelineAdapter,
)
from xic_extractor.untargeted_execution.input_planning import (
    prepare_untargeted_subject_membership,
)
from xic_extractor.untargeted_execution.physical_comparison_context import (
    PHYSICAL_COMPARISON_INPUT_KEY,
)


@pytest.mark.parametrize("indexed_input", [False, True], ids=["eager", "indexed"])
def test_fresh_context_unifies_subject_and_capture_without_losing_sample_values(
    tmp_path, monkeypatch, request, indexed_input
):
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"A": "study_sample", "B": "study_sample"},
        thermo_raw_sources=True,
    )
    settings = replace(
        _settings(),
        rt_min=6.0,
        rt_max=12.0,
        seed_rt_gap_min=0.01,
        precursor_mz_tolerance_ppm=20.0,
    )
    paths = tuple(fixture.source_paths.values())
    spec = compile_discovery_processing_run_spec(
        paths, discovery_settings=settings, signal_processing_profile=TEST_PROFILE
    )
    candidates, records, raws, receipts, csvs = {}, {}, {}, {}, {}
    expected = {}
    for sample_index, (stem, path) in enumerate(fixture.source_paths.items()):
        raw = _FakeRaw((6.8, 9.3, 10.4))
        raw.rt = np.linspace(6.0, 12.0, 301)
        raw.intensity = (sample_index + 1) * (
            800 * np.exp(-0.5 * ((raw.rt - 6.8) / 0.08) ** 2)
            + 1000 * np.exp(-0.5 * ((raw.rt - 9.3) / 0.5) ** 2)
            + 700 * np.exp(-0.5 * ((raw.rt - 10.4) / 0.5) ** 2)
        )
        masses = (499.995, 500.0, 500.015)
        raw.scans = tuple(
            replace(
                scan,
                scan_number=int(np.argmin(abs(raw.rt - scan.rt))) + 1,
                precursor_mz=mz,
                masses=scan.masses + mz - 500.0,
            )
            for scan, mz in zip(raw.scans, masses, strict=True)
        )
        source = next(
            item for item in spec.acquisition_inputs if item.sample_stem == stem
        )
        seeds = collect_strict_nl_seeds(raw, raw_file=path, settings=settings)
        discovery = capture_ms1_candidates(
            raw,
            group_discovery_seeds(seeds, settings=settings),
            acquisition_source=source.acquisition_source,
            run_spec_id=spec.run_spec_id,
            settings=settings,
            signal_processing_profile=TEST_PROFILE,
            ms1_centroid_mz=lambda *_: None,
        )
        candidates[stem] = discovery.candidates
        records.update(
            (record.candidate_id, record) for record in discovery.physical_evaluations
        )
        assert len(candidates[stem]) == 3
        expected[stem] = tuple(sorted(candidates[stem], key=lambda c: c.precursor_mz))
        raws[path] = raw
        folder = tmp_path / stem
        folder.mkdir()
        receipts.update(_discovery_receipts(folder, (path,), {stem: candidates[stem]}))
        csvs[stem] = folder / f"{stem}.owner-candidates.csv"
        write_discovery_physical_fact_sidecar(
            folder / "discovery_physical_facts.json",
            run_spec=spec,
            acquisition_input=source,
            work_manifest=discovery.physical_work_manifest,
            records=discovery.physical_evaluations,
        )
    baseline = prepare_untargeted_subject_membership(
        candidates_by_sample=candidates,
        settings=settings,
        physical_records_by_candidate=records,
    )
    baseline_ids = dict(baseline.candidate_memberships)
    for lower, early, late in expected.values():
        assert baseline_ids[lower.candidate_id] == baseline_ids[early.candidate_id]
        assert baseline_ids[early.candidate_id] != baseline_ids[late.candidate_id]

    opened = _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    from xic_extractor.untargeted_execution import physical_comparison_context
    from xic_extractor.untargeted_execution.partition import workers

    monkeypatch.setattr(physical_comparison_context, "open_raw", workers.open_raw)
    monkeypatch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)
    discovery_batch = DiscoveryBatchInput(
        sample_order=tuple(fixture.source_paths),
        candidate_csvs=csvs,
        raw_files=fixture.source_paths,
        review_csvs={stem: None for stem in candidates},
        candidate_receipts=receipts,
    )
    prepared = prepare_untargeted_alignment_inputs(
        discovery_batch=discovery_batch,
        candidates_by_sample=DiscoveryInputSource(discovery_batch)
        if indexed_input
        else candidates,
        raw_dir=tmp_path,
        output_dir=tmp_path / "prepared",
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
        peak_config=_peak_config(tmp_path),
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
        raw_workers=1,
    )
    request.addfinalizer(prepared.close)
    plan = prepared.plan
    memberships = dict(plan.subject_batch.candidate_memberships)
    assert set(memberships) == set(records)
    for lower, early, late in expected.values():
        assert memberships[lower.candidate_id] != memberships[early.candidate_id]
        assert memberships[early.candidate_id] == memberships[late.candidate_id]
    request_inputs = json.loads(
        plan.context.processing_owners.request.normalized_scientific_inputs_json
    )
    assert len(request_inputs[PHYSICAL_COMPARISON_INPUT_KEY]) == 4
    assert all(len(handles) == 1 and handles[0].disposed for handles in opened.values())
    assert all(
        len(handles[0].chromatogram_request_counts) == 1 for handles in opened.values()
    )

    adapter = UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        max_partition_jobs=100,
    )
    initial_phase, _ = adapter.execution_phases()
    assert len(initial_phase.partitions) == 1
    acquired = adapter.execute_acquisition(initial_phase.partitions[0])
    assert acquired.succeeded
    leaf = acquired.result[1]
    assert leaf.prepared_grouping is not None
    groups = tuple(
        group
        for workset in leaf.prepared_grouping.worksets
        for group in workset.grouping_scope.groups
    )
    assert len(groups) == 2
    assert all(
        {owner.sample_stem for owner in group.owners} == {"A", "B"} for group in groups
    )
    areas = {}
    for result in leaf.sample_results:
        peaks = result.adapter_result.assessment_input.assessment.candidate_peaks
        assert len(peaks) == 1
        areas.setdefault(result.job.sample.metadata.raw_stem, []).append(
            peaks[0].content.integration.area_ms1_morphology
        )
    assert sorted(areas["B"]) == pytest.approx(
        [2 * area for area in sorted(areas["A"])]
    )


@pytest.mark.parametrize("different_ions", [False, True], ids=["two-lobes", "two-ions"])
def test_fresh_comparison_keeps_positive_separation_and_unequal_ion_signal(
    different_ions,
):
    rt = np.linspace(7.0, 13.0, 301)
    sigma = 0.5 if different_ions else 0.08
    raw = 1000 * np.exp(-0.5 * ((rt - 9.3) / sigma) ** 2)
    raw += 700 * np.exp(-0.5 * ((rt - 10.4) / sigma) ** 2)
    other_raw = raw.copy()
    if different_ions:
        other_raw[int(np.argmin(abs(rt - 9.9)))] += 20.0
    views = []
    for index, (lower, upper, apex, signal) in enumerate(
        ((8.7, 9.9, 9.3, raw), (9.8, 11.0, 10.4, other_raw))
    ):
        selected = (rt >= lower) & (rt <= upper)
        views.append(
            PhysicalPeakView(
                "same-source",
                apex,
                lower,
                upper,
                500.0 + index * 0.001,
                20.0,
                rt[selected],
                signal[selected],
            )
        )
    assert compare_physical_peak_views(*views) == "unresolved"
    with_context = tuple(
        replace(view, comparison_trace=(rt, signal))
        for view, signal in zip(views, (raw, other_raw), strict=True)
    )
    assert compare_physical_peak_views(*with_context) == (
        "unresolved" if different_ions else "distinct"
    )


def test_incomplete_tail_context_does_not_mint_same_peak_evidence():
    rt = np.linspace(1.0, 5.0, 101)
    raw = 1000 * np.exp(-rt / 2.0)
    views = []
    for apex, lower, upper in ((3.6, 1.0, 3.9), (4.6, 4.1, 5.0)):
        selected = (rt >= lower) & (rt <= upper)
        views.append(
            PhysicalPeakView(
                "same-source",
                apex,
                apex - 0.1,
                apex + 0.1,
                500.0,
                20.0,
                rt[selected],
                raw[selected],
                comparison_trace=(rt, raw),
            )
        )
    # The acquired decay has no observed right return. A shared truncated
    # segment is not proof that these two retained views may be deduplicated.
    assert compare_physical_peak_views(*views) == "unresolved"
    assert compare_physical_peak_views(*reversed(views)) == "unresolved"
