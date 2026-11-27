"""Persisted Discovery admission through both formal Alignment input routes."""

from dataclasses import replace

import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _settings,
)
from tests.cid_nl_runtime_fixtures import build_discovery_receipts, install_runtime_raws
from tests.physical_peak_evaluation_fixtures import (
    _overlapping_triplet_trace,
)
from tests.untargeted_partition_fixtures import children_of, project_composed_child
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.discovery.csv_reader import (
    DiscoveryBatchInput,
    read_owner_bound_discovery_peaks_csv,
)
from xic_extractor.discovery.csv_writer import write_discovery_events_csv
from xic_extractor.discovery.delta_mass_pipeline import _load_cid_nl_peak_rows
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.ion_grouping import build_discovery_ions
from xic_extractor.discovery.ms1_capture import capture_ms1_peaks
from xic_extractor.discovery.ms2_events import collect_strict_nl_events
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.alignment import (
    prepare_untargeted_alignment_inputs,
)
from xic_extractor.untargeted_execution.execution import (
    execute_untargeted_plan,
    materialize_untargeted_partition,
)
from xic_extractor.untargeted_execution.partition.cid_nl_backfill_acquisition import (
    physical_backfill_observation,
)


@pytest.mark.parametrize("apex_mz", [None, 500.012])
def test_alignment_and_delta_mass_read_one_peak_mass(tmp_path, apex_mz):
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"physical-mass-index")
    (peak,) = _current_candidates(
        _FakeRaw((8.0,)), raw_file=raw_path, settings=_settings()
    )
    peak_mz = 500.0 if apex_mz is None else apex_mz
    peak = replace(
        peak,
        ms1_apex_mz=apex_mz,
        peak_mz=peak_mz,
        status="admitted",
        matched_tag_names=("DNA_dR",),
    )
    receipts = build_discovery_receipts(tmp_path, (raw_path,), {"sample": (peak,)})
    batch = DiscoveryBatchInput(
        sample_order=("sample",),
        peak_csvs={"sample": tmp_path / "sample.owner-candidates.csv"},
        raw_files={"sample": raw_path},
        event_csvs={"sample": None},
        peak_receipts=receipts,
    )
    source = DiscoveryInputSource(batch)
    (delta_mass_row,) = _load_cid_nl_peak_rows((batch.peak_csvs["sample"],))
    assert source.locations[peak.peak_id].peak_mz == peak_mz
    assert source["sample"][0].peak_mz == peak_mz
    assert delta_mass_row.peak_mz == peak_mz


@pytest.mark.parametrize("with_supported", [False, True], ids=["all_review", "mixed"])
def test_held_rows_never_become_alignment_roots(tmp_path, monkeypatch, with_supported):
    """Discovery holds peaks without NL support; an unresolved multi-component
    envelope is admitted as one peak without overlap review."""
    stems = (
        ("Rescued", "Overlap", "Orphan", "Clean1", "Clean2")
        if with_supported
        else ("Rescued", "Orphan")
    )
    paths = tuple(tmp_path / f"{stem}.raw" for stem in stems)
    for path in paths:
        path.write_bytes(path.stem.encode())
    settings = replace(_settings(), ms1_search_padding_min=0.6)
    spec = compile_discovery_processing_run_spec(
        paths, discovery_settings=settings, signal_processing_profile=TEST_PROFILE
    )
    raws, candidates, csvs, receipts, event_csvs = {}, {}, {}, {}, {}
    expected_ids, rejected_ids = set(), set()
    for path, source in zip(paths, spec.acquisition_inputs, strict=True):
        raw = _FakeRaw(
            (8.0,),
            scan_precursor_mz=500.5 if path.stem == "Rescued" else 500.0,
            acquisition_source=source.acquisition_source,
        )
        if path.stem == "Overlap":
            rt, intensity = _overlapping_triplet_trace()
            raw.rt = 7.5 + rt * 0.1
            raw.intensity = intensity
            raw.scans = tuple(replace(scan, scan_number=501) for scan in raw.scans)
        if path.stem == "Orphan":
            raw.intensity[:] = 0
        if path.stem == "Clean2":
            raw.intensity *= 2
        raws[path] = raw
        seeds = collect_strict_nl_events(raw, raw_file=path, settings=settings)
        capture = capture_ms1_peaks(
            raw,
            build_discovery_ions(seeds, settings=settings),
            acquisition_source=source.acquisition_source,
            settings=settings,
            signal_processing_profile=TEST_PROFILE,
            ms1_centroid=lambda *_: None,
        )
        assert len(capture.attachments) == len(seeds)
        if path.stem == "Orphan":
            assert not capture.peaks
            assert all(event.unattached_reason for event in capture.attachments)
        else:
            (peak,) = capture.peaks
            assert (
                peak.status
                == {
                    "Rescued": "no_nl_event",
                    "Overlap": "admitted",
                    "Clean1": "admitted",
                    "Clean2": "admitted",
                }[path.stem]
            )
            (expected_ids if peak.admitted else rejected_ids).add(peak.peak_id)
        folder = tmp_path / path.stem
        folder.mkdir()
        receipts.update(
            build_discovery_receipts(folder, (path,), {path.stem: capture.peaks})
        )
        csvs[path.stem] = folder / f"{path.stem}.owner-candidates.csv"
        event_csvs[path.stem] = write_discovery_events_csv(
            folder / "discovery_events.csv", capture.attachments
        )
        candidates[path.stem] = read_owner_bound_discovery_peaks_csv(
            csvs[path.stem], receipt=receipts[path.stem]
        )
    batch = DiscoveryBatchInput(
        sample_order=stems,
        peak_csvs=csvs,
        raw_files={p.stem: p for p in paths},
        event_csvs=event_csvs,
        peak_receipts=receipts,
    )
    opened = install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    source = DiscoveryInputSource(batch)
    for peaks in candidates.values():
        for peak in peaks:
            assert source.locations[peak.peak_id].peak_mz == peak.peak_mz
    plan = prepare_untargeted_alignment_inputs(
        discovery_batch=batch,
        peaks_by_sample=source,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "inputs",
        settings=settings,
        signal_processing_profile=TEST_PROFILE,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=i + 1,
                sample_role="sample",
            )
            for i, stem in enumerate(stems)
        ),
        execution=ExecutionProfile(alignment_workers=1, backend="thread"),
    )
    assert {cid for cid, _ in plan.subject_batch.candidate_memberships} == expected_ids
    if with_supported:
        partition = materialize_untargeted_partition(plan, plan.subject_batch)
        assert {
            c.peak_id for job in partition.jobs for c in job.candidates
        } == expected_ids
        assert all(not job.support_candidates for job in partition.jobs)
        sample = next(s for s in plan.context.samples if s.raw_stem == "Overlap")
        raw = raws[paths[1]]
        evaluations = []
        observation = physical_backfill_observation(
            sample=sample,
            trace=Trace(
                sample_name="Overlap",
                mz=500.0,
                rt=raw.rt,
                intensity=raw.intensity,
                rt_min=float(raw.rt[0]),
                rt_max=float(raw.rt[-1]),
                ppm_tol=settings.precursor_mz_tolerance_ppm,
                acquisition_source_id=spec.acquisition_inputs[
                    1
                ].acquisition_source.acquisition_source_id,
            ),
            query_rt_min=8.0,
            profile=TEST_PROFILE,
            evaluation_sink=evaluations.append,
        )
        assert evaluations[0].state == "available"
        assert observation is not None
        assert observation.physical_evaluation is evaluations[0]
    result = execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "alignment",
        alignment_config=AlignmentConfig(),
        execution=ExecutionProfile(
            alignment_workers=1, backend="thread", overlap_partitions=False
        ),
        project_child=project_composed_child,
    )
    assert set(result.application_result.candidate_ids) == expected_ids
    assert not result.application_result.excluded_candidates
    assert not rejected_ids.intersection(
        subject.candidate_id
        for child in children_of(result.application_result)
        for subject in child.scientific_core.terminal_subjects
    )
    if not with_supported:
        assert (
            not children_of(result.application_result) and not result.method_assessments
        )
        assert sum(map(len, opened.values())) == 0


@pytest.mark.parametrize("alignment_signal", ["flat", "overlap"])
def test_alignment_measurement_decides_admission_through_the_whole_run(
    tmp_path, monkeypatch, alignment_signal
):
    """Discovery admits Clean2's peak and Alignment measures it again. A flat
    trace there holds no available peak, so the peak does not enter Alignment;
    an overlapping trace keeps the peak and its cell carries the review."""
    from tests.alignment_pipeline_helpers import render
    from tests.cid_nl_runtime_fixtures import (
        load_plan_inputs,
        persist_discovery_batch,
        representative_precursors,
    )
    from xic_extractor.applications.untargeted.alignment_explanation_projection import (
        write_alignment_explanation,
    )

    stems = ("Clean1", "Clean2")
    paths = tuple(tmp_path / f"{stem}.raw" for stem in stems)
    for path in paths:
        path.write_bytes(path.stem.encode())
    settings = replace(_settings(), ms1_search_padding_min=0.6)
    raws, candidates = {}, {}
    for path in paths:
        raw = _FakeRaw((8.0,))
        if path.stem == "Clean2":
            raw.intensity *= 2
        raws[path] = raw
        candidates[path.stem] = _current_candidates(
            raw, raw_file=path, settings=settings
        )
        assert [peak.status for peak in candidates[path.stem]] == ["admitted"]
    batch = persist_discovery_batch(
        raw_paths=paths,
        settings=settings,
        peaks_by_sample=candidates,
        root=tmp_path / "discovery",
    )
    changed = raws[paths[1]]
    if alignment_signal == "flat":
        changed.intensity[:] = 0.0
    else:
        rt, intensity = _overlapping_triplet_trace()
        changed.rt = 7.5 + rt * 0.1
        changed.intensity = intensity
        changed.scans = tuple(
            replace(scan, rt=float(changed.rt[scan.scan_number - 1]))
            for scan in changed.scans
        )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    source = DiscoveryInputSource(batch)
    plan = prepare_untargeted_alignment_inputs(
        discovery_batch=batch,
        peaks_by_sample=source,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "inputs",
        settings=settings,
        signal_processing_profile=TEST_PROFILE,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=i + 1,
                sample_role="sample",
            )
            for i, stem in enumerate(stems)
        ),
        execution=ExecutionProfile(alignment_workers=1, backend="thread"),
    )
    (kept,) = candidates["Clean1"]
    (dropped,) = candidates["Clean2"]
    assert {cid for cid, _ in plan.subject_batch.candidate_memberships} == {
        kept.peak_id,
        dropped.peak_id,
    }
    config = AlignmentConfig()
    completed = execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "alignment",
        alignment_config=config,
        execution=ExecutionProfile(
            alignment_workers=1, backend="thread", overlap_partitions=False
        ),
        project_child=project_composed_child,
    )
    measured = {
        evidence.candidate_id
        for evidence in completed.capture_evidence
        if evidence.capture_stage == "initial_capture"
    }
    if alignment_signal == "flat":
        assert measured == {kept.peak_id}
        (exclusion,) = completed.application_result.excluded_candidates
        assert exclusion.candidate_id == dropped.peak_id
        assert exclusion.peak_status == "admitted"
        assert exclusion.reason_code == "discovery_candidate_not_search_eligible"
    else:
        assert measured == {kept.peak_id, dropped.peak_id}
        assert not completed.application_result.excluded_candidates
    matrix = render("matrix", tmp_path / "matrix.tsv", completed.application_result)
    loaded = load_plan_inputs(plan)
    report = write_alignment_explanation(
        tmp_path / "explanation.sqlite",
        completed=completed,
        children=children_of(completed.application_result),
        peaks_by_sample=loaded,
        matrix_path=matrix,
        alignment_config=config,
        precursor_mz_by_candidate=representative_precursors(loaded),
        neutral_loss_by_tag=settings.neutral_loss_by_tag,
        full_audit=True,
    )
    assert report.exists()


def test_alignment_rejects_raw_replaced_after_discovery_without_recalibration(
    tmp_path,
):
    from tests.cid_nl_runtime_fixtures import persist_discovery_batch
    from xic_extractor.configuration.mass_recalibration import MassRecalibration

    stems = ("Clean1", "Clean2")
    paths = tuple(tmp_path / f"{stem}.raw" for stem in stems)
    for path in paths:
        path.write_bytes(path.stem.encode())
    settings = replace(
        _settings(),
        ms1_search_padding_min=0.6,
        mass_recalibration=MassRecalibration.NONE,
    )
    candidates = {
        path.stem: _current_candidates(
            _FakeRaw((8.0,)), raw_file=path, settings=settings
        )
        for path in paths
    }
    batch = persist_discovery_batch(
        raw_paths=paths,
        settings=settings,
        peaks_by_sample=candidates,
        root=tmp_path / "discovery",
    )
    paths[1].write_bytes(b"another acquisition under the same name")

    with pytest.raises(ValueError, match="RAW changed after Discovery.*Clean2"):
        prepare_untargeted_alignment_inputs(
            discovery_batch=batch,
            peaks_by_sample=DiscoveryInputSource(batch),
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=tmp_path / "inputs",
            settings=settings,
            signal_processing_profile=TEST_PROFILE,
            sample_metadata=tuple(
                SampleMetadata(
                    sample_name=stem,
                    raw_stem=stem,
                    injection_order=i + 1,
                    sample_role="sample",
                )
                for i, stem in enumerate(stems)
            ),
            execution=ExecutionProfile(alignment_workers=1, backend="thread"),
        )


@pytest.mark.parametrize("damage", ["event_rows_missing", "event_table_missing"])
def test_peak_without_its_representative_event_row_fails_at_alignment_input(
    tmp_path, monkeypatch, damage
):
    """A peak names its representative MS2 scan; the event table must hold it."""
    import csv

    stems = ("Clean1", "Clean2")
    paths = tuple(tmp_path / f"{stem}.raw" for stem in stems)
    for path in paths:
        path.write_bytes(path.stem.encode())
    settings = replace(_settings(), ms1_search_padding_min=0.6)
    spec = compile_discovery_processing_run_spec(
        paths, discovery_settings=settings, signal_processing_profile=TEST_PROFILE
    )
    raws, csvs, receipts, event_csvs = {}, {}, {}, {}
    for path, source in zip(paths, spec.acquisition_inputs, strict=True):
        raw = _FakeRaw((8.0,), acquisition_source=source.acquisition_source)
        raws[path] = raw
        capture = capture_ms1_peaks(
            raw,
            build_discovery_ions(
                collect_strict_nl_events(raw, raw_file=path, settings=settings),
                settings=settings,
            ),
            acquisition_source=source.acquisition_source,
            settings=settings,
            signal_processing_profile=TEST_PROFILE,
            ms1_centroid=lambda *_: None,
        )
        folder = tmp_path / path.stem
        folder.mkdir()
        receipts.update(
            build_discovery_receipts(folder, (path,), {path.stem: capture.peaks})
        )
        csvs[path.stem] = folder / f"{path.stem}.owner-candidates.csv"
        event_csvs[path.stem] = write_discovery_events_csv(
            folder / "discovery_events.csv", capture.attachments
        )
    if damage == "event_table_missing":
        event_csvs["Clean1"] = None
    else:
        with event_csvs["Clean1"].open(encoding="utf-8", newline="") as stream:
            header = next(csv.reader(stream))
        with event_csvs["Clean1"].open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerow(header)
    batch = DiscoveryBatchInput(
        sample_order=stems,
        peak_csvs=csvs,
        raw_files={p.stem: p for p in paths},
        event_csvs=event_csvs,
        peak_receipts=receipts,
    )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    plan = prepare_untargeted_alignment_inputs(
        discovery_batch=batch,
        peaks_by_sample=DiscoveryInputSource(batch),
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "inputs",
        settings=settings,
        signal_processing_profile=TEST_PROFILE,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=i + 1,
                sample_role="sample",
            )
            for i, stem in enumerate(stems)
        ),
        execution=ExecutionProfile(alignment_workers=1, backend="thread"),
    )

    with pytest.raises(ValueError, match="Clean1.*lack the representative scan"):
        materialize_untargeted_partition(plan, plan.subject_batch)
