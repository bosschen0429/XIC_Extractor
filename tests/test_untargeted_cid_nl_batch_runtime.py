from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_real_data_adapter import (
    _current_candidates,
    _FakeRaw,
    _peak_config,
    _settings,
)
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _discovery_receipts,
    _install_runtime_raw,
    _install_runtime_raws,
)
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import start_runtime_attempt
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    build_untargeted_cid_nl_real_data_context,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRealDataRuntimeJob,
    execute_untargeted_cid_nl_real_data_runtime,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.sample_metadata import SampleMetadata


def test_runtime_batch_request_shares_initial_acquisition_across_exact_subjects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"m7-runtime-batch:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raw, candidates = _nearby_subject_candidates(
        raw_path,
        settings=settings,
        peak_config=peak_config,
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
    context = build_untargeted_cid_nl_real_data_context(
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
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        sample_identity_namespace="study:m7-runtime-batch-test",
    )
    vendor_raw = _install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_path,
        tmp_path=tmp_path,
    )
    sample = context.samples[0]
    jobs = tuple(
        UntargetedCidNlRealDataRuntimeJob(
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
        )
        for subject in subjects
    )
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation="untargeted-cid-nl-m7-batch-test:v1",
    )

    result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "m7.batch.runtime.json",
            "untargeted-cid-nl-m7-batch:test",
        ),
    )

    assert result.succeeded is True, tuple(
        settlement.failure_detail.message
        for settlement in result.finalization.coordinator.state.runtime.settlements
        if settlement.failure_detail is not None
    )
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
    assert vendor_raw.chromatogram_request_counts == [2]


def test_runtime_batch_request_bounds_initial_and_recovery_by_sample_phase(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stems = ("S1", "S2", "Missing")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(f"m7-runtime-batch-recovery:{raw_path.stem}".encode())
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raws: dict[Path, _FakeRaw] = {}
    candidates_by_sample: dict[str, tuple[DiscoveryCandidate, ...]] = {}
    for raw_path in raw_paths[:2]:
        source_raw, candidates = _nearby_subject_candidates(
            raw_path,
            settings=settings,
            peak_config=peak_config,
        )
        source_raws[raw_path] = source_raw
        candidates_by_sample[raw_path.stem] = candidates
    source_raws[raw_paths[2]] = _FakeRaw((7.98,))
    candidates_by_sample["Missing"] = ()
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
    context = build_untargeted_cid_nl_real_data_context(
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
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        sample_identity_namespace="study:m7-runtime-batch-recovery-test",
    )
    opened = _install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )
    jobs = tuple(
        UntargetedCidNlRealDataRuntimeJob(
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
        )
        for subject in subjects
        for sample in context.samples
    )
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation=(
            "untargeted-cid-nl-m7-batch-recovery-test:v1"
        ),
    )

    result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "m7.batch.recovery.runtime.json",
            "untargeted-cid-nl-m7-batch-recovery:test",
        ),
    )

    assert result.succeeded is True, tuple(
        settlement.failure_detail.message
        for settlement in result.finalization.coordinator.state.runtime.settlements
        if settlement.failure_detail is not None
    )
    missing_results = tuple(
        item
        for item in result.sample_results
        if item.job.sample.metadata.raw_stem == "Missing"
    )
    assert len(missing_results) == 2
    assert all(item.recovery_attempt is not None for item in missing_results)
    assert {
        path: [vendor.chromatogram_request_counts for vendor in vendors]
        for path, vendors in opened.items()
    } == {
        raw_paths[0]: [[2]],
        raw_paths[1]: [[2]],
        raw_paths[2]: [[2]],
    }
    resource_keys = tuple(
        resource.reservation.resource_key
        for resource in result.finalization.coordinator.state.resources
    )
    assert sum(":initial:" in key for key in resource_keys) == 2
    assert sum(":recovery:" in key for key in resource_keys) == 3


def _nearby_subject_candidates(
    raw_path: Path,
    *,
    settings,
    peak_config,
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
                    peak_config=peak_config,
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
