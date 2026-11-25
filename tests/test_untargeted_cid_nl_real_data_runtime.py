from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _FakeThermoRaw,
    _open_bound_raw,
    _settings,
    _source_for,
)
from tests.cid_nl_runtime_fixtures import (
    build_discovery_receipts,
    install_runtime_raws,
)
from tests.processing_identity_no_raw_fixture import (
    build_targeted_processing_identity_raw_fixture,
)
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    execute_partition_inline,
    partition_context,
)
from xic_extractor.acquisition_contract import (
    acquire_trace_batch,
    mint_trace_acquisition_capability,
)
from xic_extractor.alignment.cid_nl_inputs import CID_NL_ALIGNMENT_CONFIG
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.raw_superwindow_source import RawSuperWindowSource
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.partition import execution as runtime_module
from xic_extractor.untargeted_execution.partition import models as partition_models
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedPartitionJob,
    build_sample_jobs,
)
from xic_extractor.xic_models import XICRequest


@pytest.fixture(autouse=True)
def _disable_real_dll_loading(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "ensure_assemblies_loaded", lambda *_: None)


def test_untargeted_acquisition_batches_overlapping_scan_windows_exactly(
    tmp_path: Path,
) -> None:
    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
    )
    source = _source_for(fixture, "S1")
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="untargeted-window-batch-test",
    )
    requests = (
        XICRequest(mz=502.0, rt_min=8.351, rt_max=8.421, ppm_tol=10.0),
        XICRequest(mz=501.0, rt_min=7.851, rt_max=8.201, ppm_tol=10.0),
        XICRequest(mz=500.0, rt_min=7.751, rt_max=8.101, ppm_tol=10.0),
    )
    source_raw = _FakeRaw((8.0, 8.39))
    baseline_handle, baseline_vendor = _open_bound_raw(
        fixture, "S1", source_raw, tmp_path=tmp_path
    )
    with baseline_handle:
        baseline = acquire_trace_batch(capability, requests, baseline_handle)
    assert baseline_vendor.chromatogram_request_counts == [1, 1, 1]

    handle, vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    with handle:
        actual = acquire_trace_batch(capability, requests, RawSuperWindowSource(handle))
    assert vendor.chromatogram_request_counts == [2, 1]
    assert actual.adapter_chromatogram_call_count == 2
    assert tuple(map(_trace_content, actual.traces)) == tuple(
        map(_trace_content, baseline.traces)
    )


def _trace_content(acquired):
    return (
        acquired.coordinate,
        acquired.trace.rt.tolist(),
        acquired.trace.intensity.tolist(),
    )


def test_runtime_delivers_one_real_subject_cohort(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in ("S1", "S2", "S3"))
    for path in raw_paths:
        path.write_bytes(f"real-data-runtime:{path.stem}".encode())
    settings = _settings()
    source_raw = _FakeRaw((7.98, 8.02))
    candidates = _current_candidates(
        source_raw,
        raw_file=raw_paths[0],
        settings=settings,
    )
    second_source = _FakeRaw((7.98, 8.02))
    second_candidates = _current_candidates(
        second_source,
        raw_file=raw_paths[2],
        settings=settings,
    )
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=116.0474,
    )
    context = partition_context(
        raw_paths=raw_paths,
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="sample",
            ),
            SampleMetadata(
                sample_name="S2",
                raw_stem="S2",
                injection_order=2,
                sample_role="qc",
            ),
            SampleMetadata(
                sample_name="S3",
                raw_stem="S3",
                injection_order=3,
                sample_role="sample",
            ),
        ),
        method_subject=subject,
        settings=settings,
        peaks_by_sample={"S1": candidates, "S2": (), "S3": second_candidates},
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            {"S1": candidates, "S2": (), "S3": second_candidates},
        ),
        signal_processing_profile=TEST_PROFILE,
    )
    source_without_cid = _FakeRaw((8.1,))
    source_without_cid.scans = ()
    opened = install_runtime_raws(
        monkeypatch,
        sources_by_path={
            raw_paths[0]: source_raw,
            raw_paths[1]: source_without_cid,
            raw_paths[2]: second_source,
        },
        tmp_path=tmp_path,
    )
    jobs = tuple(
        UntargetedPartitionJob(
            processing_attempt_id=context.processing_attempt_id,
            method_subjects=context.method_subject,
            sample=sample,
            method_subject=context.method_subject,
            candidates=context.peaks_by_sample[sample.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[sample.raw_stem],
            settings=context.settings,
            raw_path=context.raw_paths_by_sample[sample.raw_stem],
            dll_dir=tmp_path,
            work_unit_key=f"untargeted-cid-nl:{sample.raw_stem}",
            acquisition_source_expectation=context.acquisition_sources_by_sample[
                sample.raw_stem
            ],
            signal_processing_profile=context.signal_processing_profile,
        )
        for sample in context.samples
    )
    result = execute_partition_inline(
        jobs,
        partition_key="runtime-fixture",
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
    )

    assert result.succeeded is True, result.failure
    assert len(result.sample_results) == 3
    assert (
        sum(
            len(item.adapter_result.candidate_observations)
            for item in result.sample_results
        )
        == 2
    )
    by_stem = {item.job.sample.raw_stem: item for item in result.sample_results}
    s1_assessment = by_stem["S1"].adapter_result.assessment_input.assessment
    s2_assessment = by_stem["S2"].adapter_result.assessment_input.assessment
    assert s1_assessment.method_result == "SUPPORT"
    assert s2_assessment.technical_state == "not_evaluated"
    assert s2_assessment.method_result is None
    assert not s2_assessment.evaluation_receipt.search_completed
    assert by_stem["S1"].adapter_result.strict_scan_count == 2
    (s2_peak,) = s2_assessment.candidate_peaks
    assert s2_peak.integration.rt_apex_min == pytest.approx(8.1)
    assert not s2_assessment.individual_support_refs
    assert all(opened[path][0].chromatogram_request_counts == [1] for path in raw_paths)
    assert [len(opened[path]) for path in (raw_paths[0], raw_paths[2])] == [1, 1]
    assert all(vendor.disposed for path in raw_paths for vendor in opened[path])


@pytest.mark.parametrize(
    "backfill_stop,cancel_accepted",
    [
        (None, False),
        (InterruptedError, True),
        (InterruptedError, False),
        (RuntimeError, False),
        (RuntimeError, True),
    ],
    ids=[
        "success",
        "accepted-cancellation",
        "unexpected-interruption",
        "execution-failure",
        "failure-after-cancellation",
    ],
)
def test_runtime_recovers_from_single_scan_cid_nl_support_owners(
    tmp_path: Path,
    monkeypatch,
    backfill_stop,
    cancel_accepted,
) -> None:
    from xic_extractor.alignment.cell_observation.records import CellObservation
    from xic_extractor.untargeted_execution.partition_composition import (
        group_acquired_partition,
    )

    construct_cell = CellObservation.__init__

    def reject_cell_materialization(_self, *args, **kwargs):
        raise AssertionError("recovery planning must not materialize matrix cells")

    monkeypatch.setattr(CellObservation, "__init__", reject_cell_materialization)
    stems = ("S1", "S2", "Missing", "NoPeak", "Blank")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for path in raw_paths:
        path.write_bytes(f"real-data-runtime-recovery:{path.stem}".encode())
    settings = _settings()
    source_raws = {
        raw_paths[0]: _FakeRaw((8.0,)),
        raw_paths[1]: _FakeRaw((8.0,)),
        raw_paths[2]: _FakeRaw((8.0,)),
        raw_paths[3]: _FakeRaw(()),
        raw_paths[4]: _FakeRaw((8.0,)),
    }
    peaks_by_sample = {
        "S1": _current_candidates(
            source_raws[raw_paths[0]],
            raw_file=raw_paths[0],
            settings=settings,
        ),
        "S2": _current_candidates(
            source_raws[raw_paths[1]],
            raw_file=raw_paths[1],
            settings=settings,
        ),
        "Missing": (),
        "NoPeak": (),
        "Blank": (),
    }
    batch = build_dense_partition(
        raw_paths=raw_paths,
        dll_dir=tmp_path,
        sample_metadata=(
            SampleMetadata(
                sample_name="S1",
                raw_stem="S1",
                injection_order=1,
                sample_role="sample",
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
                sample_role="sample",
            ),
            SampleMetadata(
                sample_name="NoPeak",
                raw_stem="NoPeak",
                injection_order=4,
                sample_role="sample",
            ),
            SampleMetadata(
                sample_name="Blank",
                raw_stem="Blank",
                injection_order=5,
                sample_role="blank",
            ),
        ),
        settings=settings,
        peaks_by_sample=peaks_by_sample,
        signal_processing_profile=TEST_PROFILE,
    )
    opened = install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )
    cancelled = threading.Event()
    if backfill_stop is not None:
        acquire = _FakeThermoRaw.GetChromatogramData

        def stop_backfill(raw, settings, start, end):
            handles = opened.get(raw_paths[3], ())
            if len(handles) > 1 and raw is handles[1]:
                if cancel_accepted:
                    cancelled.set()
                raise backfill_stop("backfill interrupted")
            return acquire(raw, settings, start, end)

        monkeypatch.setattr(_FakeThermoRaw, "GetChromatogramData", stop_backfill)

    result = execute_partition_inline(
        batch.jobs,
        partition_key="runtime-fixture",
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
        should_cancel=cancelled.is_set,
        prepare_grouping=lambda initial: group_acquired_partition(
            batch, initial, alignment_config=CID_NL_ALIGNMENT_CONFIG
        ),
    )

    if backfill_stop is not None:
        assert not result.succeeded
        cancelled_run = backfill_stop is InterruptedError and cancel_accepted
        assert isinstance(result.failure, InterruptedError) is cancelled_run
        if not cancelled_run:
            assert "backfill interrupted" in str(result.failure)
        assert not result.sample_results
        assert all(raw.disposed for raws in opened.values() for raw in raws)
        return

    assert result.succeeded is True
    monkeypatch.setattr(CellObservation, "__init__", construct_cell)
    by_stem = {item.job.sample.raw_stem: item for item in result.sample_results}
    assert by_stem["S1"].adapter_result.strict_scan_count == 1
    assert by_stem["S2"].adapter_result.strict_scan_count == 1
    for stem in ("Missing", "NoPeak", "Blank"):
        initial = by_stem[stem].adapter_result.assessment_input.assessment
        assert initial.technical_state == "not_evaluated"
        assert initial.method_result is None
        assert not initial.evaluation_receipt.search_completed
    missing = by_stem["Missing"]
    (peak,) = missing.adapter_result.assessment_input.assessment.candidate_peaks
    assert peak.integration.rt_apex_min == pytest.approx(8.0)
    assert missing.backfill_attempt is None
    no_peak = by_stem["NoPeak"]
    assert not no_peak.adapter_result.assessment_input.assessment.candidate_peaks
    assert no_peak.backfill_attempt is not None
    assert no_peak.backfill_input is None
    assert not no_peak.backfill_attempt.assessment.candidate_peaks
    assert by_stem["Blank"].backfill_attempt is None
    assert {path.stem: len(handles) for path, handles in opened.items()} == {
        "S1": 1,
        "S2": 1,
        "Missing": 1,
        "NoPeak": 2,
        "Blank": 1,
    }
    assert all(vendor.disposed for vendors in opened.values() for vendor in vendors)


def test_batch_candidate_selection_preserves_order_and_request_sample_membership(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_paths = (tmp_path / "S1.synthetic", tmp_path / "S2.synthetic")
    settings = _settings()
    peaks_by_sample = {}
    sources_by_path = {}
    for path in raw_paths:
        path.write_bytes(path.stem.encode())
        candidates = []
        scans = []
        for offset in range(3):
            source = _FakeRaw((8.0,))
            scan = source.scans[0]
            number = scan.scan_number + offset
            source.scans = (
                replace(
                    scan,
                    scan_number=number,
                    rt=float(source.rt[number - 1]),
                    precursor_mz=scan.precursor_mz + 0.05 * offset,
                    masses=scan.masses + 0.05 * offset,
                ),
            )
            scans.extend(source.scans)
            candidates.extend(
                _current_candidates(
                    source,
                    raw_file=path,
                    settings=settings,
                )
            )
        source.scans = tuple(scans)
        sources_by_path[path] = source
        peaks_by_sample[path.stem] = tuple(candidates)
    receipts = build_discovery_receipts(tmp_path, raw_paths, peaks_by_sample)
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
                    candidate.peak_id,
                    subjects[
                        int((stem == "S2" or index == 1) != swap)
                    ].method_subject_id,
                )
                for stem, candidates in peaks_by_sample.items()
                for index, candidate in enumerate(candidates)
            )
        )
        subject_batch = mint_untargeted_cid_nl_method_subject_batch(
            subjects=subjects, candidate_memberships=memberships
        )
        context = partition_context(
            raw_paths=raw_paths,
            sample_metadata=tuple(
                SampleMetadata(
                    sample_name=path.stem,
                    raw_stem=path.stem,
                    injection_order=index,
                    sample_role="sample",
                )
                for index, path in enumerate(raw_paths, start=1)
            ),
            method_subject=subject_batch,
            settings=settings,
            peaks_by_sample=peaks_by_sample,
            discovery_receipts_by_sample=receipts,
            signal_processing_profile=TEST_PROFILE,
        )
        jobs = tuple(
            job
            for sample in context.samples
            for job in build_sample_jobs(
                processing_attempt_id=context.processing_attempt_id,
                method_subjects=context.method_subject,
                sample=sample,
                subject_work_units=tuple(
                    (subject, f"{sample.raw_stem}:{subject.method_subject_id}")
                    for subject in subjects
                ),
                candidates=tuple(reversed(context.peaks_by_sample[sample.raw_stem])),
                discovery_receipt=receipts[sample.raw_stem],
                settings=settings,
                raw_path=context.raw_paths_by_sample[sample.raw_stem],
                dll_dir=tmp_path,
                acquisition_source_expectation=context.acquisition_sources_by_sample[
                    sample.raw_stem
                ],
                signal_processing_profile=context.signal_processing_profile,
            )
        )
        assert all(
            job.acquisition_source_expectation
            is context.acquisition_sources_by_sample[job.sample.raw_stem]
            for job in jobs
        )
        requests_and_jobs.append((memberships, jobs))
    for memberships, jobs in (*requests_and_jobs, requests_and_jobs[0]):
        for job in jobs:
            member_ids = {
                candidate_id
                for candidate_id, subject_id in memberships
                if subject_id == job.method_subject.method_subject_id
            }
            expected = tuple(c for c in job.candidates if c.peak_id in member_ids)
            selected = partition_models._request_subject_candidates(job)
            assert selected == expected
            assert all(
                left is right for left, right in zip(selected, expected, strict=True)
            )
            assert (
                partition_models._request_subject_candidates(replace(job)) == expected
            )
        assert any(
            not partition_models._request_subject_candidates(job) for job in jobs
        )
        assert any(
            len(partition_models._request_subject_candidates(job)) > 1 for job in jobs
        )


@pytest.mark.parametrize("excluded", [False, True])
def test_runtime_reads_a_region_only_where_its_events_are_when_below_quorum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    excluded: bool,
) -> None:
    stems = ("Support", "Empty")
    raw_paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    for raw_path in raw_paths:
        raw_path.write_bytes(raw_path.stem.encode())
    settings = _settings()
    source_raws = {raw_paths[0]: _FakeRaw((8.0,)), raw_paths[1]: _FakeRaw(())}
    peaks_by_sample = {
        "Support": _current_candidates(
            source_raws[raw_paths[0]], raw_file=raw_paths[0], settings=settings
        ),
        "Empty": (),
    }
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=116.0474,
    )
    context = partition_context(
        raw_paths=raw_paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="sample",
                excluded=excluded and stem == "Empty",
                exclusion_reason="test exclusion"
                if excluded and stem == "Empty"
                else "",
            )
            for index, stem in enumerate(stems, start=1)
        ),
        method_subject=subject,
        settings=settings,
        peaks_by_sample=peaks_by_sample,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            peaks_by_sample,
        ),
        signal_processing_profile=TEST_PROFILE,
    )
    opened = install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )
    jobs = tuple(
        UntargetedPartitionJob(
            processing_attempt_id=context.processing_attempt_id,
            method_subjects=context.method_subject,
            sample=sample,
            acquisition_source_expectation=context.acquisition_sources_by_sample[
                sample.raw_stem
            ],
            method_subject=context.method_subject,
            candidates=context.peaks_by_sample[sample.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[sample.raw_stem],
            settings=context.settings,
            raw_path=context.raw_paths_by_sample[sample.raw_stem],
            dll_dir=tmp_path,
            work_unit_key=f"untargeted-cid-nl:{sample.raw_stem}",
            signal_processing_profile=context.signal_processing_profile,
        )
        for sample in context.samples
    )

    result = execute_partition_inline(
        jobs,
        partition_key="runtime-fixture",
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
    )

    assert result.succeeded is True, result.failure
    support, empty = (
        item.adapter_result.assessment_input.assessment
        for item in result.sample_results
    )
    assert support.method_result == "SUPPORT"
    assert empty.technical_state == "not_evaluated"
    assert empty.method_result is None and not empty.candidate_peaks
    assert all(item.backfill_attempt is None for item in result.sample_results)
    for path, windows in zip(
        raw_paths, ([(1, len(source_raws[raw_paths[0]].rt))], []), strict=True
    ):
        (handle,) = opened[path]
        assert handle.chromatogram_windows == windows
        assert handle.disposed
