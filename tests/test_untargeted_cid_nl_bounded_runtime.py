"""End-to-end bounded cohort closure through the retained scientific owners."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event, Lock, get_ident
from time import perf_counter, sleep

import pytest
from openpyxl import Workbook

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_real_data_adapter import _peak_config, _settings
from tests.test_untargeted_cid_nl_batch_runtime import _nearby_subject_candidates
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _current_candidates,
    _discovery_receipts,
    _FakeRaw,
    _FakeThermoRaw,
    _install_runtime_raws,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import read_known_istd_reference
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
from xic_extractor.execution_runtime import AttemptTerminalOutcome
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    compose_untargeted_cid_nl_production_result,
    execute_untargeted_cid_nl_production_batch,
    materialize_untargeted_cid_nl_partition,
    prepare_untargeted_cid_nl_production_plan,
)
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.sample_metadata import SampleMetadata


def _plan(tmp_path, monkeypatch, *, scenario="positive"):
    paths = tuple(tmp_path / f"S{i}.synthetic" for i in range(4))
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    raws = {}
    candidates = {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw, sample_candidates = _nearby_subject_candidates(
            path, settings=settings, peak_config=peak_config
        )
        third_raw = _FakeRaw(
            (7.98,), scan_precursor_mz=500.008, neutral_loss_das=(116.0474,)
        )
        third_scan_number = raw.scans[-1].scan_number + 1
        third_raw.scans = (
            replace(
                third_raw.scans[0],
                scan_number=third_scan_number,
                rt=float(raw.rt[third_scan_number - 1]),
            ),
        )
        third_candidates = _current_candidates(
            third_raw, raw_file=path, settings=settings, peak_config=peak_config
        )
        sample_candidates = tuple(
            sorted(
                (*sample_candidates, *third_candidates),
                key=lambda item: item.candidate_id,
            )
        )
        raw.scans = (*raw.scans, *third_raw.scans)
        if scenario == "unsupported":
            raw.intensity[:] = 0.0
            sample_candidates = _current_candidates(
                raw, raw_file=path, settings=settings, peak_config=peak_config
            )
        raws[path] = raw
        candidates[path.stem] = (
            () if scenario == "recovery" and path == paths[-1] else sample_candidates
        )
    plan = prepare_untargeted_cid_nl_production_plan(
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
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:bounded-runtime",
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    reference = None
    if scenario == "istd":
        workbook = Workbook()
        targets = workbook.active
        targets.title = "Targets"
        targets.append(
            [
                "Label",
                "Role",
                "m/z",
                "RT min",
                "RT max",
                "ppm tol",
                "NL (Da)",
                "NL ppm warn",
                "NL ppm max",
                "ISTD Pair",
            ]
        )
        targets.append(["known", "ISTD", 500, 7, 10, 20, 116.0474, 20, 50, ""])
        results = workbook.create_sheet("XIC Results")
        results.append(["SampleName", "Target", "Role", "RT", "Area"])
        for path in paths:
            results.append([path.stem, "known", "ISTD", 7.98, -999])
        reference_path = tmp_path / "reference.xlsx"
        workbook.save(reference_path)
        reference = read_known_istd_reference(reference_path)
    assert len(plan.subject_batch.subjects) == 3
    return plan, reference


@pytest.mark.parametrize("subjects_per_partition", [1, 2])
@pytest.mark.parametrize("raw_workers", [1, 2])
@pytest.mark.parametrize("scenario", ["positive", "recovery", "unsupported", "istd"])
def test_bounded_runtime_matches_eager_after_disk_owner_round_trip(
    tmp_path: Path,
    monkeypatch,
    scenario: str,
    subjects_per_partition: int,
    raw_workers: int,
):
    import xic_extractor.diagnostics.timing as timing_owner
    from xic_extractor.diagnostics.timing import TimingRecorder
    from xic_extractor.extraction.untargeted_cid_nl_bounded_runtime import (
        execute_bounded_untargeted_cid_nl,
    )

    plan, reference = _plan(tmp_path, monkeypatch, scenario=scenario)
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as bounded_owner

    coordinator_thread = get_ident()
    warm_threads = []
    monkeypatch.setattr(
        bounded_owner,
        "_ensure_assemblies_loaded",
        lambda *_args: warm_threads.append(get_ident()),
        raising=False,
    )
    config = AlignmentConfig(anchor_min_seed_events=1)
    batch = materialize_untargeted_cid_nl_partition(plan, plan.subject_batch)
    eager_runtime = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "eager.json", "eager"),
    )
    eager = compose_untargeted_cid_nl_production_result(
        batch, eager_runtime, alignment_config=config, known_istd_reference=reference
    )
    marker = OwnerPathRuntimeMarker(tmp_path / "bounded.json", "bounded")
    progress_events = []
    live_path = tmp_path / "alignment-live.json"
    recorder = TimingRecorder("alignment", live_output_path=live_path)
    monkeypatch.setattr(timing_owner, "perf_counter", lambda: 1.0)
    writes = []
    write_live = recorder._write_live

    def record_write():
        writes.append(dict(recorder._heartbeat))
        write_live()

    monkeypatch.setattr(recorder, "_write_live", record_write)

    def report_progress(event):
        assert get_ident() == coordinator_thread
        recorder.heartbeat_event(event)
        progress_events.append(event)

    with recorder.stage("alignment.untargeted_cid_nl_runtime"):
        bounded = execute_bounded_untargeted_cid_nl(
            plan,
            output_dir=tmp_path / "bounded-output",
            alignment_config=config,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            known_istd_reference=reference,
            progress_callback=report_progress,
            max_partition_jobs=(
                subjects_per_partition * len(plan.context.samples) * raw_workers
            ),
            raw_workers=raw_workers,
        )
    assert type(bounded) is CompletedUntargetBatchWorkflowResult
    assert warm_threads == ([coordinator_thread] if raw_workers > 1 else [])
    assert type(bounded.application_result.children) is ImmutableSequence
    assert project_completed_untarget_batch_writer_inputs(bounded).matrix == (
        project_completed_untarget_batch_writer_inputs(eager).matrix
    )
    actual = bounded.application_result
    expected = eager.application_result
    assert actual.candidate_ids == expected.candidate_ids
    assert actual.physical_observation_ids == expected.physical_observation_ids
    assert tuple(actual.excluded_candidates) == expected.excluded_candidates
    owners = plan.context.processing_owners
    assert actual.processing_request is owners.request
    assert actual.processing_attempt is owners.attempt
    assert bounded.runtime_receipt.supervisor.attempt is owners.attempt
    assert all(
        left is right
        for left, right in zip(
            actual.canonical_samples, expected.canonical_samples, strict=True
        )
    )
    for child in actual.children:
        assert child.processing_request is owners.request
        assert (
            child.scientific_core.cell_observation_handoff.coverage.epoch.processing_owners
            is owners
        )
        assert all(
            left is right
            for left, right in zip(
                child.canonical_samples, expected.canonical_samples, strict=True
            )
        )
    assert sorted(
        item.artifact.evidence_artifact_id for item in bounded.method_assessments
    ) == sorted(item.artifact.evidence_artifact_id for item in eager.method_assessments)
    _assert_explanation_parity(tmp_path, plan, config, eager, bounded)
    partition_stages = [
        (event["stage"], event["completed"])
        for event in progress_events
        if event.get("event") == "partition_progress"
        and event["stage"]
        in {
            "partition_start",
            "partition_compose",
            "partition_released",
            "global_closure",
            "batch_completed",
        }
    ]
    partition_count = (
        len(plan.subject_batch.subjects) + subjects_per_partition - 1
    ) // subjects_per_partition
    assert partition_stages == [
        event
        for index in range(partition_count)
        for event in (
            ("partition_start", index),
            ("partition_compose", index),
            ("partition_released", index + 1),
        )
    ] + [("global_closure", partition_count), ("batch_completed", partition_count)]
    seal_writes = [row for row in writes if row.get("stage") == "partition_seal"]
    assert len(seal_writes) <= 2 * partition_count
    heartbeat = json.loads(live_path.read_text(encoding="utf-8"))["heartbeat"]
    assert heartbeat["stage"] == "batch_completed"
    assert heartbeat["completed"] == heartbeat["total"] == partition_count
    eager_manifest = eager_runtime.finalization.coordinator.state.runtime.manifest
    assert len(eager_manifest.work_unit_keys) == len(batch.jobs)
    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert len(snapshot.manifest_work_unit_keys) == (
        partition_count * len(plan.context.samples) * 2
    )
    if scenario == "unsupported":
        assert not actual.children
        assert len(actual.excluded_candidates) == len(actual.candidate_ids)
    else:
        assert actual.children
    if scenario == "recovery":
        assert any(result.recovery_attempts for result in eager_runtime.sample_results)


def _assert_explanation_parity(tmp_path, plan, config, eager, bounded):
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv
    from xic_extractor.diagnostics.cid_nl_explanation import read_cid_nl_explanation
    from xic_extractor.diagnostics.cid_nl_explanation_projection import (
        write_cid_nl_explanation,
    )

    candidate_id = plan.subject_batch.candidate_memberships[0][0]
    records = []
    for name, completed in (("eager", eager), ("bounded", bounded)):
        writer = project_completed_untarget_batch_writer_inputs(completed)
        matrix = tmp_path / f"{name}.tsv"
        write_alignment_matrix_tsv(
            matrix,
            writer.matrix,
            alignment_config=config,
            production_decisions=writer.production_decisions,
        )
        report = write_cid_nl_explanation(
            tmp_path / f"{name}.sqlite",
            completed=completed,
            writer_inputs=writer,
            candidates_by_sample=plan.context.candidates_by_sample,
            matrix_path=matrix,
            alignment_config=config,
        )
        detail = read_cid_nl_explanation(report, candidate_id=candidate_id)
        records.append(detail["records"])
    assert records[0] == records[1]


@pytest.mark.parametrize("failure_seam", ["later_partition", "later_spool"])
def test_bounded_runtime_failure_never_publishes_completed_batch(
    tmp_path: Path, monkeypatch, failure_seam: str
):
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as bounded_owner

    plan, _ = _plan(tmp_path, monkeypatch)
    marker = OwnerPathRuntimeMarker(tmp_path / "failed.json", "failed")
    original_error = OSError("second partition storage unavailable")
    calls = []
    if failure_seam == "later_partition":
        seam_owner = bounded_owner
        seam_name = "execute_untargeted_cid_nl_runtime_partition"
    else:
        seam_owner = bounded_owner.PartitionResultStore
        seam_name = "append"
    original = getattr(seam_owner, seam_name)

    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise original_error
        return original(*args, **kwargs)

    monkeypatch.setattr(seam_owner, seam_name, fail_second)

    def forbid_completed(*args, **kwargs):
        pytest.fail("An incomplete partition must not publish a completed result")

    monkeypatch.setattr(
        bounded_owner, "CompletedUntargetBatchWorkflowResult", forbid_completed
    )
    with pytest.raises(RuntimeError, match="failed before batch publication") as error:
        bounded_owner.execute_bounded_untargeted_cid_nl(
            plan,
            output_dir=tmp_path / "failed-output",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            max_partition_jobs=len(plan.context.samples),
        )
    assert len(calls) == 2
    assert error.value.__cause__ is original_error
    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.FAILED
    assert snapshot.processing_attempt_id == (
        plan.context.processing_owners.attempt.processing_attempt_id
    )


@pytest.mark.parametrize("managed_call", [False, True])
def test_parallel_partitions_overlap_with_one_job_budget_and_writer(
    tmp_path: Path, monkeypatch, managed_call: bool
):
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as owner

    if managed_call:
        pytest.importorskip("clr")
        from System.Threading import Thread

        def pause():
            Thread.Sleep(100)
    else:

        def pause():
            sleep(0.1)

    plan, _ = _plan(tmp_path, monkeypatch, scenario="recovery")
    monkeypatch.setattr(owner, "_ensure_assemblies_loaded", lambda *_args: None)
    barrier = Barrier(2)
    lock = Lock()
    coordinator = get_ident()
    intervals = {}
    handle_threads = {}
    disposed = set()
    live_jobs = 0
    peak_jobs = 0
    admitted = 0
    append_order = []
    original_materialize = owner.materialize_untargeted_cid_nl_partition
    original_chromatogram = _FakeThermoRaw.GetChromatogramData
    original_init = _FakeThermoRaw.__init__
    original_dispose = _FakeThermoRaw.Dispose
    original_append = owner.PartitionResultStore.append

    def materialize(*args):
        nonlocal live_jobs, peak_jobs, admitted
        batch = original_materialize(*args)
        assert len(batch.jobs) == len(plan.context.samples)
        assert {job.sample for job in batch.jobs} == set(plan.context.samples)
        assert all(
            job.processing_owners is plan.context.processing_owners
            for job in batch.jobs
        )
        with lock:
            admitted += 1
            live_jobs += len(batch.jobs)
            peak_jobs = max(peak_jobs, live_jobs)
        return batch

    def initialize(handle, *args):
        original_init(handle, *args)
        handle_threads[id(handle)] = get_ident()

    def dispose(handle):
        assert handle_threads[id(handle)] == get_ident()
        disposed.add(id(handle))
        original_dispose(handle)

    def chromatogram(handle, *args):
        thread = get_ident()
        assert handle_threads[id(handle)] == thread
        if thread not in intervals:
            intervals[thread] = None
            barrier.wait(timeout=10)
            start = perf_counter()
            pause()
            intervals[thread] = (start, perf_counter())
        return original_chromatogram(handle, *args)

    def append(store, partition_key, payload):
        assert get_ident() == coordinator
        if not append_order:
            assert admitted == 2
        append_order.append(partition_key)
        return original_append(store, partition_key, payload)

    def progress(event):
        nonlocal live_jobs
        assert get_ident() == coordinator
        if event["stage"] == "partition_released":
            with lock:
                live_jobs -= len(plan.context.samples)

    monkeypatch.setattr(owner, "materialize_untargeted_cid_nl_partition", materialize)
    monkeypatch.setattr(_FakeThermoRaw, "__init__", initialize)
    monkeypatch.setattr(_FakeThermoRaw, "Dispose", dispose)
    monkeypatch.setattr(_FakeThermoRaw, "GetChromatogramData", chromatogram)
    monkeypatch.setattr(owner.PartitionResultStore, "append", append)
    result = owner.execute_bounded_untargeted_cid_nl(
        plan,
        output_dir=tmp_path / "parallel",
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "parallel.json", "parallel"),
        max_partition_jobs=8,
        raw_workers=2,
        progress_callback=progress,
    )
    assert type(result) is CompletedUntargetBatchWorkflowResult
    assert peak_jobs == 8 and live_jobs == 0
    assert append_order == [f"partition-{index:05d}" for index in range(3)]
    assert len(intervals) == 2
    assert min(end for _, end in intervals.values()) > max(
        start for start, _ in intervals.values()
    )
    assert set(handle_threads) == disposed
    assert coordinator not in handle_threads.values()


@pytest.mark.parametrize("failure_mode", ["cancel", "vendor_failure", "spool_failure"])
def test_parallel_failure_drains_launched_leaves_without_replacement_or_publication(
    tmp_path: Path, monkeypatch, failure_mode: str
):
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as owner

    plan, _ = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(owner, "_ensure_assemblies_loaded", lambda *_args: None)
    barrier = Barrier(2)
    caller_cancel = Event()
    caller_observed_cancel = Event()
    threads = {}
    entered = set()
    opened = {}
    closed = set()
    batches = []
    original_leaf = owner.execute_untargeted_cid_nl_runtime_partition
    original_chromatogram = _FakeThermoRaw.GetChromatogramData
    original_init = _FakeThermoRaw.__init__
    original_dispose = _FakeThermoRaw.Dispose
    original_execute = owner.execute_runtime_attempt

    def leaf(*args, **kwargs):
        threads[get_ident()] = (kwargs["partition_key"], kwargs["should_cancel"])
        entered.add(kwargs["partition_key"])
        return original_leaf(*args, **kwargs)

    def initialize(handle, *args):
        original_init(handle, *args)
        opened[id(handle)] = get_ident()

    def dispose(handle):
        assert opened[id(handle)] == get_ident()
        closed.add(id(handle))
        original_dispose(handle)

    first_calls = set()

    def chromatogram(handle, *args):
        thread = get_ident()
        partition, stopped = threads[thread]
        if thread not in first_calls:
            first_calls.add(thread)
            barrier.wait(timeout=10)
            if failure_mode == "cancel":
                caller_cancel.set()
                assert caller_observed_cancel.wait(timeout=10)
            elif failure_mode == "vendor_failure":
                if partition.endswith("00001"):
                    raise OSError("injected vendor failure")
                for _ in range(1000):
                    if stopped():
                        break
                    sleep(0.01)
                assert stopped(), "Peer failure must reach the running leaf"
        return original_chromatogram(handle, *args)

    def should_cancel():
        if caller_cancel.is_set():
            caller_observed_cancel.set()
            return True
        return False

    def execute(*args, worker_batch_provider, **kwargs):
        def observe(aggregate):
            batch = worker_batch_provider(aggregate)
            assert set(opened) == closed
            batches.append(batch)
            return batch

        return original_execute(*args, worker_batch_provider=observe, **kwargs)

    def fail_spool(*_args):
        raise OSError("injected spool failure")

    monkeypatch.setattr(owner, "execute_untargeted_cid_nl_runtime_partition", leaf)
    monkeypatch.setattr(owner, "execute_runtime_attempt", execute)
    monkeypatch.setattr(_FakeThermoRaw, "__init__", initialize)
    monkeypatch.setattr(_FakeThermoRaw, "Dispose", dispose)
    monkeypatch.setattr(_FakeThermoRaw, "GetChromatogramData", chromatogram)
    if failure_mode == "spool_failure":
        monkeypatch.setattr(owner.PartitionResultStore, "append", fail_spool)
    marker = OwnerPathRuntimeMarker(tmp_path / "failure.json", "parallel-failure")
    with pytest.raises(RuntimeError, match="failed before batch publication") as error:
        owner.execute_bounded_untargeted_cid_nl(
            plan,
            output_dir=tmp_path / "failed",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            raw_workers=2,
            max_partition_jobs=8,
            should_cancel=should_cancel,
        )
    if failure_mode == "vendor_failure":
        assert "injected vendor failure" in str(error.value)
    assert entered == {"partition-00000", "partition-00001"}
    assert set(opened) == closed and len(opened) >= 2
    assert marker.read().terminal_outcome is AttemptTerminalOutcome.FAILED
    assert len(batches) == 1
    resources = batches[0].resource_settlements
    assert all(
        receipt.cleanup_outcome is RuntimeResourceCleanupOutcome.SUCCEEDED
        for receipt in resources
    )
    assert sum(
        receipt.open_outcome is RuntimeResourceOpenOutcome.OPENED
        for receipt in resources
    ) == len(opened)
    assert all(
        receipt.open_outcome is RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED
        for receipt in resources
        if "partition-00002:" in receipt.resource_key
    )
