from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_real_data_adapter import (
    _current_candidates,
    _FakeRaw,
    _FakeThermoRaw,
    _peak_config,
    _settings,
    _working_imports,
)
from tests.test_untargeted_cid_nl_real_data_adapter import _without_ms1_peak
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    build_untargeted_cid_nl_golden_path,
)
from xic_extractor.discovery.batch_contract import (
    DiscoveryCandidateUniverseReceipt,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.execution_runtime_finalization import RuntimeResourceOpenOutcome
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import start_runtime_attempt
from xic_extractor.extraction import (
    untargeted_cid_nl_real_data_runtime as runtime_module,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    build_untargeted_cid_nl_real_data_context,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRealDataRuntimeError,
    UntargetedCidNlRealDataRuntimeJob,
    build_untargeted_cid_nl_sample_runtime_jobs,
    execute_untargeted_cid_nl_real_data_runtime,
    untargeted_cid_nl_runtime_call_cost,
)
from xic_extractor.peak_detection import capture as capture_owner
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.processing_identity import (
    IdentityContractError,
    build_acquisition_source_identity,
)
from xic_extractor.raw_reader import (
    EXPECTED_THERMO_DLLS,
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    open_raw,
    reset_reader_state,
)
from xic_extractor.sample_metadata import SampleMetadata


def test_runtime_delivers_one_real_subject_cohort_into_golden_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    capture_variants: list[str] = []
    real_capture = capture_owner.capture_peak_hypotheses

    def _trace_capture(**kwargs):
        capture_variants.append(kwargs["scope"].variant)
        return real_capture(**kwargs)

    monkeypatch.setattr(capture_owner, "capture_peak_hypotheses", _trace_capture)
    raw_paths = (tmp_path / "S1.synthetic", tmp_path / "S2.synthetic")
    for path in raw_paths:
        path.write_bytes(f"real-data-runtime:{path.stem}".encode())
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raw = _FakeRaw((7.98, 8.02))
    candidates = _current_candidates(
        source_raw,
        raw_file=raw_paths[0],
        settings=settings,
        peak_config=peak_config,
    )
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=116.0474,
    )
    context = build_untargeted_cid_nl_real_data_context(
        raw_paths=raw_paths,
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="study_sample",
            ),
            SampleMetadata(
                sample_name="S2",
                raw_stem="S2",
                injection_order=2,
                sample_role="qc",
            ),
        ),
        method_subject=subject,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample={"S1": candidates, "S2": ()},
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            {"S1": candidates, "S2": ()},
        ),
        sample_identity_namespace="study:real-data-runtime-test",
    )
    vendor_raw = _install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_paths[0],
        tmp_path=tmp_path,
    )
    jobs = tuple(
        UntargetedCidNlRealDataRuntimeJob(
            processing_owners=context.processing_owners,
            sample=sample,
            method_subject=context.method_subject,
            candidates=context.candidates_by_sample[sample.metadata.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[
                sample.metadata.raw_stem
            ],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
            dll_dir=tmp_path,
            work_unit_key=f"untargeted-cid-nl:{sample.metadata.raw_stem}",
        )
        for sample in context.samples
    )
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation="untargeted-cid-nl-real-data-runtime-test:v1",
    )

    result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "real_data_runtime.json",
            "untargeted-cid-nl-real-data-runtime:test",
        ),
    )

    assert result.succeeded is True, (
        result.finalization.coordinator.proposed_outcome,
        result.finalization.supervisor.terminal_outcome,
        result.finalization.coordinator.state.runtime.settlements,
        result.finalization.coordinator.state.resources,
        result.finalization.coordinator.state.artifacts,
        result.finalization.coordinator.state.failures,
    )
    assert capture_variants == ["untargeted"]
    assert len(result.sample_results) == 2
    by_stem = {
        item.job.sample.metadata.raw_stem: item for item in result.sample_results
    }
    s1_assessment = by_stem["S1"].adapter_result.assessment_input.assessment
    s2_assessment = by_stem["S2"].adapter_result.assessment_input.assessment
    assert s1_assessment.content.method_result == "SUPPORT"
    assert s2_assessment.content.method_result == "HOLD"
    assert by_stem["S1"].adapter_result.strict_scan_count == 2
    assert by_stem["S2"].adapter_result.acquisition is None
    assert vendor_raw.chromatogram_request_counts == [1]
    assert vendor_raw.disposed is True

    projection = build_untargeted_cid_nl_golden_path(
        processing_owners=context.processing_owners,
        method_subject=context.method_subject,
        sample_inputs=tuple(
            item.adapter_result.assessment_input for item in result.sample_results
        ),
        source_artifact_relpath=(
            "output/real_data_runtime/untargeted_cid_nl_source.json"
        ),
    )
    assert len(projection.spine.cells) == 2
    assert projection.matrix_header == ("Mz", "RT", "S1", "S2")
    assert projection.input_quant_matrix_rows[0]["S1"]
    assert projection.input_quant_matrix_rows[0]["S2"] == ""
    _assert_partition_matches_runtime(jobs, result, monkeypatch)


def test_runtime_holds_inferred_precursor_without_physical_ms1_peak(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"real-data-runtime-unsupported:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raw = _FakeRaw((8.0,))
    source_raw.scans = tuple(
        replace(scan, precursor_mz=scan.precursor_mz + 0.5) for scan in source_raw.scans
    )
    source_raw.intensity.fill(0.0)
    inferred = _current_candidates(
        source_raw,
        raw_file=raw_path,
        settings=settings,
        peak_config=peak_config,
    )[0]
    assert inferred.precursor_mz_basis == "product_plus_neutral_loss"
    assert not inferred.ms1_peak_found
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=116.0474,
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
        method_subject=subject,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample={"S1": (inferred,)},
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": (inferred,)},
        ),
        sample_identity_namespace="study:real-data-runtime-unsupported-test",
    )
    job = UntargetedCidNlRealDataRuntimeJob(
        processing_owners=context.processing_owners,
        sample=context.samples[0],
        method_subject=context.method_subject,
        candidates=context.candidates_by_sample["S1"],
        discovery_receipt=context.discovery_receipts_by_sample["S1"],
        settings=context.settings,
        peak_config=context.peak_config,
        raw_path=raw_path,
        dll_dir=tmp_path,
        work_unit_key="untargeted-cid-nl:S1",
    )
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=(job.work_unit_key,),
        owner_completeness_attestation="untargeted-cid-nl-real-data-runtime-test:v1",
    )

    vendor_raw = _install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_path,
        tmp_path=tmp_path,
    )
    result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        (job,),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "real_data_runtime.unsupported.json",
            "untargeted-cid-nl-real-data-runtime:test",
        ),
    )

    assert result.succeeded is True, (
        result.finalization.coordinator.state.runtime.settlements
    )
    assert len(result.sample_results) == 1
    adapter = result.sample_results[0].adapter_result
    assert adapter.assessment_input.assessment.content.technical_state == "evaluated"
    assert adapter.assessment_input.assessment.content.method_result == "HOLD"
    assert adapter.source_candidate_ids == (inferred.candidate_id,)
    assert adapter.assessment_input.assessment.candidate_peaks == ()
    assert adapter.strict_scan_count == 0
    assert adapter.acquisition is None
    assert vendor_raw.disposed is True
    assert result.sample_results[0].recovery_attempt is None


def test_runtime_recovers_from_single_scan_cid_nl_support_owners(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stems = ("S1", "S2", "Missing", "NoPeak", "Blank")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for path in raw_paths:
        path.write_bytes(f"real-data-runtime-recovery:{path.stem}".encode())
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raws = {
        raw_paths[0]: _FakeRaw((8.0,)),
        raw_paths[1]: _FakeRaw((8.0,)),
        raw_paths[2]: _FakeRaw((8.0,)),
        raw_paths[3]: _FakeRaw(()),
        raw_paths[4]: _FakeRaw((8.0,)),
    }
    candidates_by_sample = {
        "S1": _current_candidates(
            source_raws[raw_paths[0]],
            raw_file=raw_paths[0],
            settings=settings,
            peak_config=peak_config,
        ),
        "S2": _current_candidates(
            source_raws[raw_paths[1]],
            raw_file=raw_paths[1],
            settings=settings,
            peak_config=peak_config,
        ),
        "Missing": (),
        "NoPeak": (),
        "Blank": (),
    }
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=116.0474,
    )
    context = build_untargeted_cid_nl_real_data_context(
        raw_paths=raw_paths,
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="study_sample",
            ),
            SampleMetadata(
                sample_name="S2",
                raw_stem="S2",
                injection_order=2,
                sample_role="qc",
            ),
            SampleMetadata(
                sample_name="Missing",
                raw_stem="Missing",
                injection_order=3,
                sample_role="study_sample",
            ),
            SampleMetadata(
                sample_name="NoPeak",
                raw_stem="NoPeak",
                injection_order=4,
                sample_role="study_sample",
            ),
            SampleMetadata(
                sample_name="Blank",
                raw_stem="Blank",
                injection_order=5,
                sample_role="blank",
            ),
        ),
        method_subject=subject,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        sample_identity_namespace="study:real-data-runtime-recovery-test",
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
            method_subject=context.method_subject,
            candidates=context.candidates_by_sample[sample.metadata.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[
                sample.metadata.raw_stem
            ],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
            dll_dir=tmp_path,
            work_unit_key=f"untargeted-cid-nl:{sample.metadata.raw_stem}",
        )
        for sample in context.samples
    )
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation=(
            "untargeted-cid-nl-real-data-runtime-recovery-test:v1"
        ),
    )

    result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "real_data_runtime.recovery.json",
            "untargeted-cid-nl-real-data-runtime-recovery:test",
        ),
    )

    assert result.succeeded is True
    by_stem = {
        item.job.sample.metadata.raw_stem: item for item in result.sample_results
    }
    assert by_stem["S1"].adapter_result.strict_scan_count == 1
    assert by_stem["S2"].adapter_result.strict_scan_count == 1
    assert by_stem["Missing"].recovery_attempt is not None
    assert by_stem["Missing"].recovery_input is not None
    assert by_stem["Missing"].result_ref.recovery_candidate_count == 1
    assert by_stem["NoPeak"].recovery_attempt is not None
    assert by_stem["NoPeak"].recovery_input is None
    assert by_stem["NoPeak"].result_ref.recovery_assessment_artifact_id is not None
    assert by_stem["NoPeak"].result_ref.recovery_candidate_count == 0
    assert by_stem["Blank"].recovery_attempt is None
    assert raw_paths[4] not in opened
    assert {
        path: [vendor.chromatogram_request_counts for vendor in vendors]
        for path, vendors in opened.items()
    } == {
        raw_paths[0]: [[1]],
        raw_paths[1]: [[1]],
        raw_paths[2]: [[1]],
        raw_paths[3]: [[1]],
    }
    assert all(vendor.disposed for vendors in opened.values() for vendor in vendors)

    projection = build_untargeted_cid_nl_golden_path(
        processing_owners=context.processing_owners,
        method_subject=context.method_subject,
        sample_inputs=tuple(
            item.adapter_result.assessment_input for item in result.sample_results
        ),
        recovery_inputs=tuple(
            item.recovery_input
            for item in result.sample_results
            if item.recovery_input is not None
        ),
        source_artifact_relpath="output/real_data_runtime/recovered_source.json",
    )
    decisions = {
        decision.target_cell.content.canonical_sample_identity_id: decision
        for decision in projection.backfill_decisions
    }
    missing_id = by_stem["Missing"].job.sample.identity.sample_identity_id
    blank_id = by_stem["Blank"].job.sample.identity.sample_identity_id
    no_peak_id = by_stem["NoPeak"].job.sample.identity.sample_identity_id
    assert decisions[missing_id].disposition == "candidate_mapped"
    assert decisions[no_peak_id].disposition == "hold"
    assert decisions[blank_id].disposition == "hold"
    assert len(projection.pending_acceptance_rows) == 1
    _assert_partition_matches_runtime(jobs, result, monkeypatch)


def test_runtime_job_rejects_candidate_content_changed_after_request_mint(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"real-data-runtime-request-pin:S1")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raw = _FakeRaw((8.0,))
    candidates = _current_candidates(
        source_raw,
        raw_file=raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=116.0474,
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
        method_subject=subject,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample={"S1": candidates},
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            (raw_path,),
            {"S1": candidates},
        ),
        sample_identity_namespace="study:real-data-runtime-request-pin-test",
    )

    with pytest.raises(
        UntargetedCidNlRealDataRuntimeError,
        match="candidate universe does not match the processing request",
    ):
        UntargetedCidNlRealDataRuntimeJob(
            processing_owners=context.processing_owners,
            sample=context.samples[0],
            method_subject=context.method_subject,
            candidates=(replace(candidates[0], reason="tampered-after-request"),),
            discovery_receipt=context.discovery_receipts_by_sample["S1"],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=raw_path,
            dll_dir=tmp_path,
            work_unit_key="untargeted-cid-nl:S1",
        )


def test_batch_candidate_selection_preserves_order_and_request_sample_membership(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_paths = (tmp_path / "S1.synthetic", tmp_path / "S2.synthetic")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates_by_sample = {}
    for path in raw_paths:
        path.write_bytes(path.stem.encode())
        first = _current_candidates(
            _FakeRaw((8.0,)), raw_file=path, settings=settings, peak_config=peak_config
        )[0]
        candidates_by_sample[path.stem] = tuple(
            replace(first, candidate_id=f"{path.stem}:{suffix}")
            for suffix in ("a", "b", "c")
        )
    receipts = _discovery_receipts(tmp_path, raw_paths, candidates_by_sample)
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=mz,
                    precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
                    neutral_loss_da=116.0474,
                )
                for mz in (500.0, 500.004)
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    requests_and_jobs = []
    for swap in (False, True):
        memberships = tuple(
            sorted(
                (
                    candidate.candidate_id,
                    subjects[
                        int((stem == "S2" or index == 1) != swap)
                    ].method_subject_id,
                )
                for stem, candidates in candidates_by_sample.items()
                for index, candidate in enumerate(candidates)
            )
        )
        subject_batch = mint_untargeted_cid_nl_method_subject_batch(
            subjects=subjects, candidate_memberships=memberships
        )
        context = build_untargeted_cid_nl_real_data_context(
            raw_paths=raw_paths,
            sample_metadata=tuple(
                SampleMetadata(
                    sample_name=path.stem,
                    raw_stem=path.stem,
                    injection_order=index,
                    sample_role="study_sample",
                )
                for index, path in enumerate(raw_paths, start=1)
            ),
            method_subject=subject_batch,
            settings=settings,
            peak_config=peak_config,
            candidates_by_sample=candidates_by_sample,
            discovery_receipts_by_sample=receipts,
            sample_identity_namespace="study:runtime-selection",
        )
        jobs = tuple(
            job
            for sample in context.samples
            for job in build_untargeted_cid_nl_sample_runtime_jobs(
                processing_owners=context.processing_owners,
                sample=sample,
                subject_work_units=tuple(
                    (subject, f"{sample.metadata.raw_stem}:{subject.method_subject_id}")
                    for subject in subjects
                ),
                candidates=tuple(
                    reversed(context.candidates_by_sample[sample.metadata.raw_stem])
                ),
                discovery_receipt=receipts[sample.metadata.raw_stem],
                settings=settings,
                peak_config=peak_config,
                raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
                dll_dir=tmp_path,
            )
        )
        requests_and_jobs.append((memberships, jobs))
    # Revisit the first request after the second has selected a different universe.
    for memberships, jobs in (*requests_and_jobs, requests_and_jobs[0]):
        for job in jobs:
            member_ids = {
                candidate_id
                for candidate_id, subject_id in memberships
                if subject_id == job.method_subject.method_subject_id
            }
            expected = tuple(c for c in job.candidates if c.candidate_id in member_ids)
            selected = runtime_module._request_subject_candidates(job)
            assert selected == expected
            assert all(left is right for left, right in zip(selected, expected))
            assert runtime_module._request_subject_candidates(replace(job)) == expected
        assert any(not runtime_module._request_subject_candidates(job) for job in jobs)
        assert any(
            len(runtime_module._request_subject_candidates(job)) > 1 for job in jobs
        )
    with pytest.raises(
        UntargetedCidNlRealDataRuntimeError, match="not an exact member"
    ):
        replace(
            requests_and_jobs[0][1][0],
            method_subject=mint_untargeted_cid_nl_method_subject(
                precursor_mz=700.0,
                precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
                neutral_loss_da=116.0474,
            ),
        )
    _install_runtime_raws(
        monkeypatch,
        sources_by_path={path: _FakeRaw((8.0,)) for path in raw_paths},
        tmp_path=tmp_path,
    )
    selected_jobs = tuple(
        replace(job, candidates=tuple(reversed(job.candidates)))
        for job in jobs
        if job.method_subject == subjects[1] and job.sample.metadata.raw_stem == "S1"
    )
    acquired_candidates = []
    real_acquire = runtime_module.acquire_untargeted_cid_nl_sample_trace_batch

    def record_acquire(**kwargs):
        acquired_candidates.append((kwargs["sample"], kwargs["candidates"]))
        return real_acquire(**kwargs)

    monkeypatch.setattr(
        runtime_module, "acquire_untargeted_cid_nl_sample_trace_batch", record_acquire
    )
    partition = runtime_module.execute_untargeted_cid_nl_runtime_partition(
        selected_jobs,
        partition_key="subset",
        alignment_config=runtime_module.CID_NL_ALIGNMENT_CONFIG,
    )
    assert partition.succeeded, partition.worker_batch.settlements
    assert acquired_candidates
    for sample, acquired in acquired_candidates:
        job = next(job for job in selected_jobs if job.sample == sample)
        assert acquired == runtime_module._request_subject_candidates(job)
    assert any(
        len(acquired) < len(job.candidates)
        for sample, acquired in acquired_candidates
        for job in selected_jobs
        if job.sample == sample
    )


def test_context_rejects_same_stem_receipt_from_different_acquisition(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    replay_dir = tmp_path / "replay"
    source_dir.mkdir()
    replay_dir.mkdir()
    source_path = source_dir / "S1.synthetic"
    replay_path = replay_dir / "S1.synthetic"
    source_path.write_bytes(b"discovery-acquisition")
    replay_path.write_bytes(b"different-replay-acquisition")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        _FakeRaw((8.0,)),
        raw_file=source_path,
        settings=settings,
        peak_config=peak_config,
    )
    receipt = _discovery_receipts(
        tmp_path,
        (source_path,),
        {"S1": candidates},
    )["S1"]
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=116.0474,
    )

    with pytest.raises(
        IdentityContractError,
        match="current acquisition source does not match the Discovery receipt",
    ):
        build_untargeted_cid_nl_real_data_context(
            raw_paths=(replay_path,),
            sample_metadata=(
                SampleMetadata(
                    sample_name="S1",
                    raw_stem="S1",
                    injection_order=1,
                    sample_role="study_sample",
                ),
            ),
            method_subject=subject,
            settings=settings,
            peak_config=peak_config,
            candidates_by_sample={"S1": candidates},
            discovery_receipts_by_sample={"S1": receipt},
            sample_identity_namespace="study:real-data-runtime-crossed-acquisition-test",
        )


@pytest.mark.parametrize("excluded", [False, True])
def test_runtime_does_not_open_recovery_resource_when_owner_condition_is_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    excluded: bool,
) -> None:
    stems = ("Support", "Reject", "Empty")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(raw_path.stem.encode())
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raws = {path: _FakeRaw((8.0,)) for path in raw_paths}
    support = _current_candidates(
        source_raws[raw_paths[0]],
        raw_file=raw_paths[0],
        settings=settings,
        peak_config=peak_config,
    )
    reject = tuple(
        _without_ms1_peak(candidate)
        for candidate in _current_candidates(
            source_raws[raw_paths[1]],
            raw_file=raw_paths[1],
            settings=settings,
            peak_config=peak_config,
        )
    )
    candidates_by_sample = {
        "Support": support,
        "Reject": reject,
        "Empty": (),
    }
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=116.0474,
    )
    context = build_untargeted_cid_nl_real_data_context(
        raw_paths=raw_paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
                excluded=excluded and stem == "Empty",
                exclusion_reason="test exclusion"
                if excluded and stem == "Empty"
                else "",
            )
            for index, stem in enumerate(stems, start=1)
        ),
        method_subject=subject,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        sample_identity_namespace="study:real-data-runtime-conditional-resource-test",
    )
    opened = _install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )
    installed_open_raw = runtime_module.open_raw

    def _reject_unused_open(path: Path, *args: object, **kwargs: object):
        if path == raw_paths[2]:
            raise AssertionError("owner-false recovery resource must not open RAW")
        return installed_open_raw(path, *args, **kwargs)

    monkeypatch.setattr(runtime_module, "open_raw", _reject_unused_open)
    jobs = tuple(
        UntargetedCidNlRealDataRuntimeJob(
            processing_owners=context.processing_owners,
            sample=sample,
            method_subject=context.method_subject,
            candidates=context.candidates_by_sample[sample.metadata.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[
                sample.metadata.raw_stem
            ],
            settings=context.settings,
            peak_config=context.peak_config,
            raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
            dll_dir=tmp_path,
            work_unit_key=f"untargeted-cid-nl:{sample.metadata.raw_stem}",
        )
        for sample in context.samples
    )
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation=(
            "real-data-runtime-conditional-resource-test:v1"
        ),
    )

    result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "real_data_runtime.conditional.json",
            "real-data-runtime-conditional-resource:test",
        ),
    )

    assert result.succeeded is True
    assert [
        item.adapter_result.assessment_input.assessment.content.method_result
        for item in result.sample_results
    ] == ["SUPPORT", "REJECT", "HOLD"]
    assert raw_paths[2] not in opened
    if not excluded:
        empty_resource = next(
            resource
            for resource in result.finalization.coordinator.state.resources
            if resource.reservation.resource_key.endswith(":Empty")
        )
        assert empty_resource.open_outcome is (
            RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED
        )
        assert empty_resource.reservation.owner_conditional is True
    assert (
        untargeted_cid_nl_runtime_call_cost(result.finalization)["raw_open_count"] == 2
    )
    _assert_partition_matches_runtime(jobs, result, monkeypatch)


def _discovery_receipts(
    root: Path,
    raw_paths: tuple[Path, ...],
    candidates_by_sample: dict[str, tuple[DiscoveryCandidate, ...]],
) -> dict[str, DiscoveryCandidateUniverseReceipt]:
    receipts: dict[str, DiscoveryCandidateUniverseReceipt] = {}
    for raw_path in raw_paths:
        candidates = candidates_by_sample[raw_path.stem]
        candidate_csv = root / f"{raw_path.stem}.owner-candidates.csv"
        write_discovery_candidates_csv(candidate_csv, candidates)
        source = build_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        receipts[raw_path.stem] = mint_discovery_candidate_universe_receipt(
            sample_stem=raw_path.stem,
            acquisition_source=source,
            candidate_csv=candidate_csv,
            candidates=candidates,
        )
    return receipts


def _install_runtime_raw(
    monkeypatch,
    *,
    source_raw: _FakeRaw,
    raw_path: Path,
    tmp_path: Path,
) -> _FakeThermoRaw:
    reset_reader_state()
    for filename in EXPECTED_THERMO_DLLS:
        (tmp_path / filename).write_bytes(b"dll")
    vendor_raw = _FakeThermoRaw(source_raw)

    def _open_runtime_raw(path, dll_dir, *, expected_acquisition_source):
        assert path == raw_path
        return open_raw(
            path,
            dll_dir,
            expected_acquisition_source=expected_acquisition_source,
            _import_module=_working_imports,
            _thermo=SimpleNamespace(
                raw_file_reader_adapter=SimpleNamespace(
                    FileFactory=lambda _path: vendor_raw
                ),
                device_ms="MS",
                mass_range_factory=lambda low, high: (low, high),
                trace_settings_factory=lambda *_args: SimpleNamespace(
                    MassRanges=None,
                    Filter=None,
                ),
                mass_range_trace_type="MassRange",
                ms2_order="Ms2",
            ),
        )

    monkeypatch.setattr(runtime_module, "open_raw", _open_runtime_raw)
    return vendor_raw


def _install_runtime_raws(
    monkeypatch,
    *,
    sources_by_path: dict[Path, _FakeRaw],
    tmp_path: Path,
) -> dict[Path, list[_FakeThermoRaw]]:
    reset_reader_state()
    for filename in EXPECTED_THERMO_DLLS:
        (tmp_path / filename).write_bytes(b"dll")
    opened: dict[Path, list[_FakeThermoRaw]] = {}

    def _open_runtime_raw(path, dll_dir, *, expected_acquisition_source):
        vendor_raw = _FakeThermoRaw(sources_by_path[path])
        opened.setdefault(path, []).append(vendor_raw)
        return open_raw(
            path,
            dll_dir,
            expected_acquisition_source=expected_acquisition_source,
            _import_module=_working_imports,
            _thermo=SimpleNamespace(
                raw_file_reader_adapter=SimpleNamespace(
                    FileFactory=lambda _path: vendor_raw
                ),
                device_ms="MS",
                mass_range_factory=lambda low, high: (low, high),
                trace_settings_factory=lambda *_args: SimpleNamespace(
                    MassRanges=None,
                    Filter=None,
                ),
                mass_range_trace_type="MassRange",
                ms2_order="Ms2",
            ),
        )

    monkeypatch.setattr(runtime_module, "open_raw", _open_runtime_raw)
    return opened


def _assert_partition_matches_runtime(jobs, full_result, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("partition must not execute a runtime attempt")

    monkeypatch.setattr(runtime_module, "execute_runtime_attempt", forbidden)
    observed_batches = []
    for name in (
        "acquire_untargeted_cid_nl_sample_trace_batch",
        "acquire_untargeted_cid_nl_recovery_trace_batch",
    ):
        original = getattr(runtime_module, name)

        def observe(*args, _original=original, **kwargs):
            batch = _original(*args, **kwargs)
            if batch is not None:
                observed_batches.append(batch.acquisition)
            return batch

        monkeypatch.setattr(runtime_module, name, observe)
    events = []
    result = runtime_module.execute_untargeted_cid_nl_runtime_partition(
        jobs,
        partition_key="part-1",
        alignment_config=runtime_module.CID_NL_ALIGNMENT_CONFIG,
        progress_callback=events.append,
    )
    assert result.succeeded
    assert len(result.sample_results) == len(full_result.sample_results)
    for actual, expected in zip(
        result.sample_results, full_result.sample_results, strict=True
    ):
        actual_assessments = actual.effective_assessments
        expected_assessments = expected.effective_assessments
        assert len(actual_assessments) == len(expected_assessments)
        for left, right in zip(actual_assessments, expected_assessments, strict=True):
            assert left.content.method_result == right.content.method_result
            assert left.content.technical_state == right.content.technical_state
            assert left.candidate_peaks == right.candidate_peaks
        assert (
            actual.result_ref.source_candidate_ids
            == expected.result_ref.source_candidate_ids
        )
        assert (
            actual.result_ref.recovery_query_bounds
            == expected.result_ref.recovery_query_bounds
        )
    expected_keys = {
        f"part-1:{job.sample.identity.sample_identity_id}:{phase}"
        for job in jobs
        for phase in ("initial", "recovery")
    }
    resources = result.worker_batch.resource_settlements
    assert {item.resource_key for item in resources} == {
        "acquisition-session:" + key for key in expected_keys
    }
    assert len(resources) == len(expected_keys)
    assert len({item.work_unit_key for item in result.acquisitions}) == len(
        result.acquisitions
    )
    assert all(item.work_unit_key in expected_keys for item in result.acquisitions)
    assert {item.work_unit_key for item in result.acquisitions} <= {
        item.resource_key.removeprefix("acquisition-session:")
        for item in resources
        if item.open_outcome is RuntimeResourceOpenOutcome.OPENED
    }
    assert all(item.acquisition.traces for item in result.acquisitions)
    assert len(observed_batches) == len(result.acquisitions)
    assert all(
        any(item.acquisition is batch for item in result.acquisitions)
        for batch in observed_batches
    )
    for stage in ("partition_initial", "partition_recovery", "partition_seal"):
        progress = [event for event in events if event.get("stage") == stage]
        assert progress[-1]["completed"] == progress[-1]["total"]
    # An acquisition failure must settle its jobs and return all resource cleanup facts.
    monkeypatch.setattr(runtime_module, "open_raw", forbidden)
    failed = runtime_module.execute_untargeted_cid_nl_runtime_partition(
        jobs,
        partition_key="part-error",
        alignment_config=runtime_module.CID_NL_ALIGNMENT_CONFIG,
    )
    assert not failed.succeeded
    assert len(failed.worker_batch.settlements) == len(jobs)
    assert len(failed.worker_batch.resource_settlements) == len(expected_keys)
    assert any(
        item.open_outcome is RuntimeResourceOpenOutcome.OPEN_FAILED
        for item in failed.worker_batch.resource_settlements
    )
