from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _peak_config,
    _settings,
)
from tests.cid_nl_runtime_fixtures import (
    assert_partition_matches_runtime,
    build_discovery_receipts,
    install_runtime_raw,
    install_runtime_raws,
)
from tests.untargeted_partition_fixtures import fixture_assessment
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.partition.execution import (
    CID_NL_ALIGNMENT_CONFIG,
    execute_attempt,
    execute_partition,
)
from xic_extractor.untargeted_execution.partition.models import UntargetedPartitionJob
from xic_extractor.untargeted_execution.partition.request import (
    build_partition_context,
)


def test_runtime_batch_request_shares_initial_acquisition_across_exact_subjects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    physical_records = {}
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"real-data-runtime-batch:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
        physical_records=physical_records,
    )
    subjects = _subjects(candidates, settings.precursor_mz_tolerance_ppm)
    subject_by_precursor = {subject.precursor_mz: subject for subject in subjects}
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=tuple(
            sorted(
                (
                    candidate.candidate_id,
                    subject_by_precursor[candidate.precursor_mz].method_subject_id,
                )
                for candidate in candidates
            )
        ),
    )
    context = build_partition_context(
        raw_paths=(raw_path,),
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="study_sample",
            ),
        ),
        method_subject=subject_batch,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample={"S1": candidates},
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        sample_identity_namespace="study:real-data-runtime-batch-test",
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
    sample = context.samples[0]
    jobs = tuple(
        UntargetedPartitionJob(
            processing_owners=context.processing_owners,
            sample=sample,
            method_subject=subject,
            candidates=context.candidates_by_sample["S1"],
            discovery_receipt=context.discovery_receipts_by_sample["S1"],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=context.raw_paths_by_sample["S1"],
            dll_dir=tmp_path,
            work_unit_key=f"untargeted-cid-nl:S1:{subject.method_subject_id}",
            signal_processing_profile=context.signal_processing_profile,
            physical_records=tuple(
                context.physical_records_by_candidate[c.candidate_id]
                for c in (context.candidates_by_sample["S1"])
            ),
        )
        for subject in subjects
    )

    result = execute_attempt(
        jobs,
    )

    assert result.succeeded is True, result.failure
    assert len(result.sample_results) == 2
    assert {
        item.job.method_subject.method_subject_id: (
            item.adapter_result.source_candidate_ids
        )
        for item in result.sample_results
    } == {
        subject_by_precursor[candidate.precursor_mz].method_subject_id: (
            candidate.candidate_id,
        )
        for candidate in candidates
    }
    assert vendor_raw.chromatogram_request_counts == [4]


@pytest.mark.parametrize("initial_trace_without_peak", [False, True])
def test_runtime_batch_request_bounds_initial_and_recovery_by_sample_phase(
    tmp_path: Path,
    monkeypatch,
    initial_trace_without_peak: bool,
) -> None:
    physical_records = {}
    stems = ("S1", "S2", "Missing")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(
            f"real-data-runtime-batch-recovery:{raw_path.stem}".encode()
        )
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raws: dict[Path, _FakeRaw] = {}
    candidates_by_sample: dict[str, tuple[DiscoveryCandidate, ...]] = {}
    for raw_path in raw_paths[:2]:
        source_raw, candidates = _nearby_subject_candidates(
            raw_path,
            settings=settings,
            peak_config=peak_config,
            physical_records=physical_records,
        )
        source_raws[raw_path] = source_raw
        candidates_by_sample[raw_path.stem] = candidates
    source_raws[raw_paths[2]] = _FakeRaw((7.98,))
    candidates_by_sample["Missing"] = ()
    if initial_trace_without_peak:
        source_raw, candidates = _nearby_subject_candidates(
            raw_paths[2],
            settings=settings,
            peak_config=peak_config,
            physical_records=physical_records,
        )
        source_raws[raw_paths[2]] = source_raw
        candidates_by_sample["Missing"] = candidates
        from xic_extractor.untargeted_execution.partition import cid_nl_acquisition

        acquire_peaks = cid_nl_acquisition._reacquire_peak_observations

        def capture_without_peak(**kwargs):
            acquisition, observations, captures = acquire_peaks(**kwargs)
            if kwargs["sample"].metadata.raw_stem == "Missing":
                # Physical capture may reject every peak after acquiring the XIC.
                # Exercise that runtime boundary with the real acquisition retained.
                return acquisition, (), captures
            return acquisition, observations, captures

        monkeypatch.setattr(
            cid_nl_acquisition,
            "_reacquire_peak_observations",
            capture_without_peak,
        )
    subjects = _subjects(
        candidates_by_sample["S1"],
        settings.precursor_mz_tolerance_ppm,
    )
    subject_by_precursor = {subject.precursor_mz: subject for subject in subjects}
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=tuple(
            sorted(
                (
                    candidate.candidate_id,
                    subject_by_precursor[candidate.precursor_mz].method_subject_id,
                )
                for candidates in candidates_by_sample.values()
                for candidate in candidates
            )
        ),
    )
    context = build_partition_context(
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
        method_subject=subject_batch,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        sample_identity_namespace="study:real-data-runtime-batch-recovery-test",
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
    jobs = tuple(
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
            dll_dir=tmp_path,
            work_unit_key=(
                f"untargeted-cid-nl:{sample.metadata.raw_stem}:"
                f"{subject.method_subject_id}"
            ),
            signal_processing_profile=context.signal_processing_profile,
            physical_records=tuple(
                context.physical_records_by_candidate[c.candidate_id]
                for c in (context.candidates_by_sample[sample.metadata.raw_stem])
            ),
        )
        for subject in subjects
        for sample in context.samples
    )
    if initial_trace_without_peak:
        partition = execute_partition(
            jobs,
            partition_key="initial-and-recovery",
            alignment_config=CID_NL_ALIGNMENT_CONFIG,
        )
        assert partition.succeeded, partition.failure
        missing = tuple(
            item
            for item in partition.sample_results
            if item.job.sample.metadata.raw_stem == "Missing"
        )
        assert len(missing) == 2
        for item in missing:
            initial = item.adapter_result.acquisition
            assert initial is not None
            assert item.adapter_result.assessment_input.assessment.candidate_peaks == ()
            recovery = item.backfill_attempt
            assert recovery is not None
            assert recovery.acquisition is not initial
        assert [
            vendor.chromatogram_request_counts for vendor in opened[raw_paths[2]]
        ] == [[4], [4]]
        return

    result = execute_attempt(
        jobs,
    )

    assert result.succeeded is True, result.failure
    missing_results = tuple(
        item
        for item in result.sample_results
        if item.job.sample.metadata.raw_stem == "Missing"
    )
    assert len(missing_results) == 2
    assert all(item.backfill_attempt is not None for item in missing_results)
    assert all(
        fixture_assessment(item) is item.backfill_attempt.assessment
        for item in missing_results
        if item.backfill_attempt is not None
    )
    assert {
        path: [vendor.chromatogram_request_counts for vendor in vendors]
        for path, vendors in opened.items()
    } == {
        raw_paths[0]: [[4]],
        raw_paths[1]: [[4]],
        raw_paths[2]: [[4]],
    }
    assert_partition_matches_runtime(jobs, result, monkeypatch)


def _nearby_subject_candidates(
    raw_path: Path,
    *,
    settings,
    peak_config,
    physical_records=None,
) -> tuple[_FakeRaw, tuple[DiscoveryCandidate, ...]]:
    first_raw = _FakeRaw((7.98,))
    second_raw = _FakeRaw(
        (7.98,),
        scan_precursor_mz=500.004,
        neutral_loss_das=(116.0474,),
    )
    second_raw.scans = (
        replace(
            second_raw.scans[0],
            scan_number=first_raw.scans[0].scan_number + 1,
        ),
    )
    candidates = tuple(
        sorted(
            (
                _current_candidates(
                    raw,
                    raw_file=raw_path,
                    settings=settings,
                    physical_records=physical_records,
                )[0]
                for raw in (first_raw, second_raw)
            ),
            key=lambda item: item.candidate_id,
        )
    )
    source_raw = _FakeRaw((7.98,))
    source_raw.scans = (first_raw.scans[0], second_raw.scans[0])
    return source_raw, candidates


def _subjects(
    candidates: tuple[DiscoveryCandidate, ...],
    precursor_tolerance_ppm: float,
) -> tuple[CidNlMethodSubject, ...]:
    return tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=candidate.precursor_mz,
                    precursor_tolerance_ppm=precursor_tolerance_ppm,
                    neutral_loss_da=candidate.configured_neutral_loss_da,
                )
                for candidate in candidates
            ),
            key=lambda item: item.method_subject_id,
        )
    )
