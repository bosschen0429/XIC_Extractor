"""Persisted Discovery admission through both formal Alignment input routes."""

from dataclasses import replace

import pytest

from tests.cid_nl_raw_fixture import TEST_PROFILE, _FakeRaw, _peak_config, _settings
from tests.cid_nl_runtime_fixtures import build_discovery_receipts, install_runtime_raws
from tests.physical_peak_evaluation_persistence_fixtures import (
    _overlapping_triplet_trace,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.csv_reader import (
    DiscoveryBatchInput,
    read_owner_bound_discovery_peaks_csv,
)
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.ion_channels import build_discovery_channels
from xic_extractor.discovery.ms1_capture import capture_ms1_peaks
from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
from xic_extractor.discovery.physical_fact_persistence import (
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.peak_detection.capture import capture_physical_backfill_observation
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.alignment import (
    prepare_untargeted_alignment_inputs,
)
from xic_extractor.untargeted_execution.execution import (
    execute_untargeted_plan,
    materialize_untargeted_partition,
)


@pytest.mark.parametrize("indexed", [False, True], ids=["eager", "indexed"])
@pytest.mark.parametrize("with_supported", [False, True], ids=["all_review", "mixed"])
def test_review_rows_never_become_alignment_roots(
    tmp_path, monkeypatch, indexed, with_supported
):
    stems = ("Rescued", "Overlap", "Orphan") + (
        ("Clean1", "Clean2") if with_supported else ()
    )
    paths = tuple(tmp_path / f"{stem}.raw" for stem in stems)
    for path in paths:
        path.write_bytes(path.stem.encode())
    settings = replace(_settings(), ms1_search_padding_min=0.6)
    spec = compile_discovery_processing_run_spec(
        paths, discovery_settings=settings, signal_processing_profile=TEST_PROFILE
    )
    raws, candidates, csvs, receipts = {}, {}, {}, {}
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
        seeds = collect_strict_nl_seeds(raw, raw_file=path, settings=settings)
        capture = capture_ms1_peaks(
            raw,
            build_discovery_channels(seeds, settings=settings),
            acquisition_source=source.acquisition_source,
            settings=settings,
            signal_processing_profile=TEST_PROFILE,
            ms1_centroid_mz=lambda *_: None,
        )
        assert len(capture.events) == len(seeds)
        if path.stem == "Orphan":
            assert not capture.peaks
            assert all(event.unattached_reason for event in capture.events)
        else:
            (peak,) = capture.peaks
            assert (
                peak.admission_reason
                == {
                    "Rescued": "no_attached_event",
                    "Overlap": "requires_overlap_review",
                    "Clean1": "admitted",
                    "Clean2": "admitted",
                }[path.stem]
            )
            (expected_ids if peak.admitted else rejected_ids).add(peak.peak_id)
        folder = tmp_path / path.stem
        folder.mkdir()
        write_discovery_physical_fact_sidecar(
            folder / "discovery_physical_facts.json",
            run_spec=spec,
            acquisition_input=source,
            work_manifest=capture.physical_work_manifest,
            records=capture.physical_evaluations,
        )
        receipts.update(
            build_discovery_receipts(folder, (path,), {path.stem: capture.peaks})
        )
        csvs[path.stem] = folder / f"{path.stem}.owner-candidates.csv"
        candidates[path.stem] = read_owner_bound_discovery_peaks_csv(
            csvs[path.stem], receipt=receipts[path.stem]
        )
    batch = DiscoveryBatchInput(
        sample_order=stems,
        peak_csvs=csvs,
        raw_files={p.stem: p for p in paths},
        event_csvs={stem: None for stem in stems},
        peak_receipts=receipts,
    )
    opened = install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    prepared = prepare_untargeted_alignment_inputs(
        discovery_batch=batch,
        candidates_by_sample=DiscoveryInputSource(batch) if indexed else candidates,
        raw_dir=tmp_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "inputs",
        settings=settings,
        peak_config=_peak_config(tmp_path),
        signal_processing_profile=TEST_PROFILE,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=i + 1,
                sample_role="study_sample",
            )
            for i, stem in enumerate(stems)
        ),
        raw_workers=1,
    )
    try:
        plan = prepared.plan
        assert {
            cid for cid, _ in plan.subject_batch.candidate_memberships
        } == expected_ids
        if with_supported:
            partition = materialize_untargeted_partition(plan, plan.subject_batch)
            assert {
                c.peak_id for job in partition.jobs for c in job.candidates
            } == expected_ids
            assert all(not job.support_candidates for job in partition.jobs)
            # An accepted root does not authorize an unresolved MS1-only refill.
            sample = next(
                s for s in plan.context.samples if s.metadata.raw_stem == "Overlap"
            )
            raw = raws[paths[1]]
            evaluations = []
            observation = capture_physical_backfill_observation(
                processing_owners=plan.context.processing_owners,
                sample=sample,
                method_subject=plan.subject_batch.subjects[0],
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
                physical_mz=500.0,
                profile=TEST_PROFILE,
                resolver_mode="local_minimum",
                evaluation_sink=evaluations.append,
            )
            assert (
                evaluations[0].state == "available"
                and evaluations[0].requires_overlap_review
            )
            assert observation is None
        result = execute_untargeted_plan(
            plan,
            output_dir=tmp_path / "alignment",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            raw_workers=1,
        )
        assert set(result.application_result.candidate_ids) == expected_ids
        assert not result.application_result.excluded_candidates
        assert not rejected_ids.intersection(
            subject.candidate_id
            for child in result.application_result.children
            for subject in child.scientific_core.terminal_subjects
        )
        if not with_supported:
            assert (
                not result.application_result.children and not result.method_assessments
            )
            assert sum(map(len, opened.values())) == 0
    finally:
        prepared.close()
