from __future__ import annotations

import concurrent.futures
import json
import multiprocessing
import pickle
from concurrent.futures import Future
from dataclasses import replace
from functools import partial
from pathlib import Path

import numpy as np
import pytest

from tests.test_discovery_pipeline import (
    _OwnerBoundFakeRawHandle,
    _read_csv,
    _scan_event,
    _settings,
)
from xic_extractor.acquisition_contract import (
    serialize_acquired_trace,
)
from xic_extractor.discovery.batch_contract import discovery_candidate_payload_sha256
from xic_extractor.discovery.csv_reader import (
    read_discovery_batch_index,
    read_owner_bound_discovery_candidates_csv,
)
from xic_extractor.discovery.pipeline import (
    discover_sample_for_process_job,
    run_discovery_batch,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.discovery.sample_process_execution import (
    DiscoverySampleProcessError,
    DiscoverySampleProcessJob,
    run_discovery_sample_process_job,
)
from xic_extractor.xic_models import XICTrace

_run_discovery_sample_process_job = partial(
    run_discovery_sample_process_job,
    discover_sample=discover_sample_for_process_job,
)


@pytest.mark.parametrize("workers", (1, 2))
@pytest.mark.parametrize("surface", ("rows", "facts"))
def test_discovery_persists_feature_facts_and_measured_review_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
    surface: str,
) -> None:
    from xic_extractor.discovery import output_publication, pipeline
    from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv
    from xic_extractor.discovery.models import MS1_FEATURE_BACKED_STATES
    from xic_extractor.discovery.physical_fact_persistence import (
        read_discovery_physical_fact_records,
    )

    paths = (tmp_path / "sample-a.raw", tmp_path / "sample-b.raw")
    for path in paths:
        path.write_bytes(path.stem.encode("utf-8"))
    settings = _settings(ms2_precursor_tol_da=1.6)
    spec = compile_discovery_processing_run_spec(paths, discovery_settings=settings)
    rt = np.linspace(7.2, 8.4, 25)

    class SelectiveTraceRaw(_OwnerBoundFakeRawHandle):
        def extract_xic_many(self, requests):
            self.raw_chromatogram_call_count += len(requests)
            return tuple(
                XICTrace.from_arrays(
                    self._rt,
                    self._intensity if request.mz > 600 else np.zeros_like(rt),
                )
                for request in requests
            )

    handles = {
        path: SelectiveTraceRaw(
            spec.acquisition_inputs[index].acquisition_source,
            [
                _scan_event(
                    scan_number=scan,
                    rt=7.8,
                    precursor_mz=mz + 1.0,
                    masses=[mz - 116.0474, mz + 1.0 - 116.0474],
                    intensities=[10000.0, 12000.0],
                )
                for scan, mz in ((101, 500.0), (202, 700.0))
            ],
            rt=rt,
            intensity=100.0
            + (index + 1) * 500.0 * np.exp(-0.5 * ((rt - 7.8) / 0.08) ** 2),
        )
        for index, path in enumerate(paths)
    }
    captured = {}
    publish_sample = output_publication.publish_discovery_batch_sample

    def capture_publication(raw_path, candidates, **kwargs):
        captured[raw_path.stem] = (candidates, kwargs)
        return publish_sample(raw_path, candidates, **kwargs)

    monkeypatch.setattr(
        output_publication, "publish_discovery_batch_sample", capture_publication
    )
    monkeypatch.setattr(
        pipeline,
        "_owner_bound_raw_opener",
        lambda *_args: lambda path, _dll: handles[path],
    )

    class ImmediateExecutor:
        def __init__(self, **kwargs):
            pass

        def submit(self, worker, job):
            future = Future()
            future.set_result(worker(job))
            return future

        def shutdown(self, **kwargs):
            pass

    outputs = run_discovery_batch(
        paths,
        output_dir=tmp_path / "discovery",
        settings=settings,
        dll_dir=tmp_path / "dll",
        run_spec=spec,
        max_workers=workers,
        raw_opener=(lambda path, _dll: handles[path]) if workers == 1 else None,
        executor_factory=ImmediateExecutor if workers > 1 else None,
    )
    batch = read_discovery_batch_index(
        outputs.batch_index_csv,
    )
    for acquisition, output in zip(
        spec.acquisition_inputs,
        outputs.per_sample,
        strict=True,
    ):
        candidates, physical = captured[acquisition.sample_stem]
        features = tuple(
            item
            for item in candidates
            if item.ms1_peak_found
            and item.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
        )
        assert {item.precursor_mz_basis for item in features} == {
            "scan_precursor",
            "product_plus_neutral_loss",
        }
        support = tuple(
            item
            for item in candidates
            if not item.ms1_peak_found and item.precursor_mz_basis == "scan_precursor"
        )
        dropped = tuple(
            item
            for item in candidates
            if not item.ms1_peak_found
            and item.precursor_mz_basis == "product_plus_neutral_loss"
        )
        assert len(features) == 2 and len(support) == len(dropped) == 1
        expected = (*features, *support)
        expected_path = tmp_path / f"{acquisition.sample_stem}.csv"
        write_discovery_candidates_csv(expected_path, expected)
        assert output.physical_facts_json is not None
        payload = json.loads(output.physical_facts_json.read_text(encoding="utf-8"))
        if surface == "facts":
            assert len(payload["records"]) == len(payload["work_manifest"]) == 2
        assert output.candidates_csv.read_bytes() == expected_path.read_bytes()
        assert {row["candidate_id"] for row in _read_csv(output.review_csv)} == {
            item.candidate_id for item in expected
        }
        receipt = batch.candidate_receipts[acquisition.sample_stem]
        assert receipt.candidate_count == 3
        assert (
            len(
                read_owner_bound_discovery_candidates_csv(
                    output.candidates_csv,
                    receipt=receipt,
                )
            )
            == 3
        )
        assert len(payload["records"]) == len(payload["work_manifest"]) == 2
        records = read_discovery_physical_fact_records(
            output.physical_facts_json,
            expected_run_spec=spec,
            expected_acquisition_input=acquisition,
        )
        assert {record.candidate_id for record in records} == {
            item.candidate_id for item in features
        }
        original_records = {
            record.candidate_id: record for record in physical["physical_evaluations"]
        }
        for record in records:
            original = original_records[record.candidate_id]
            assert record.evaluation == original.evaluation
            assert serialize_acquired_trace(record.acquired_trace) == (
                serialize_acquired_trace(original.acquired_trace)
            )


def test_physical_bindings_match_published_candidates_by_id(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-order")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=settings,
    )
    raw = _OwnerBoundFakeRawHandle(
        spec.acquisition_inputs[0].acquisition_source,
        [
            _scan_event(
                scan_number=202,
                rt=8.0,
                product_intensity=9000.0,
                precursor_mz=258.1085,
            ),
            _scan_event(
                scan_number=101,
                rt=8.0,
                product_intensity=3000.0,
                precursor_mz=300.1085,
            ),
        ],
        rt=np.asarray([7.9, 8.0, 8.1], dtype=float),
        intensity=np.asarray([1.0, 100.0, 1.0], dtype=float),
    )
    outputs = run_discovery_batch(
        (raw_path,),
        output_dir=tmp_path / "discovery",
        settings=settings,
        dll_dir=tmp_path / "dll",
        raw_opener=lambda _path, _dll_dir: raw,
        run_spec=spec,
    )
    batch = read_discovery_batch_index(
        outputs.batch_index_csv,
    )
    receipt = batch.candidate_receipts[raw_path.stem]
    canonical_candidates = read_owner_bound_discovery_candidates_csv(
        batch.candidate_csvs[raw_path.stem],
        receipt=receipt,
    )
    bindings = outputs.per_sample[0].physical_bindings
    reordered_candidates = tuple(reversed(canonical_candidates))
    assert tuple(binding.candidate.candidate_id for binding in bindings) != tuple(
        candidate.candidate_id for candidate in reordered_candidates
    )

    assert discovery_candidate_payload_sha256(
        reordered_candidates
    ) == discovery_candidate_payload_sha256(
        tuple(binding.candidate for binding in bindings)
    )


def test_batch_serial_and_process_share_one_run_spec_and_sample_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_paths = (tmp_path / "sample-b.raw", tmp_path / "sample-a.raw")
    for raw_path in raw_paths:
        raw_path.write_bytes(raw_path.stem.encode())
    settings = _settings()
    spec = compile_discovery_processing_run_spec(raw_paths, discovery_settings=settings)

    serial_handles = {
        path: _OwnerBoundFakeRawHandle(
            spec.acquisition_inputs[index].acquisition_source,
            [_scan_event(scan_number=index + 1, rt=8.0 + index)],
            rt=np.asarray([7.9 + index, 8.0 + index, 8.1 + index], dtype=float),
            intensity=np.asarray([1.0, 100.0, 1.0], dtype=float),
        )
        for index, path in enumerate(raw_paths)
    }
    serial = run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "serial-spec",
        settings=settings,
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: serial_handles[path],
        run_spec=spec,
    )

    process_handles = {
        path: _OwnerBoundFakeRawHandle(
            spec.acquisition_inputs[index].acquisition_source,
            [_scan_event(scan_number=index + 1, rt=8.0 + index)],
            rt=np.asarray([7.9 + index, 8.0 + index, 8.1 + index], dtype=float),
            intensity=np.asarray([1.0, 100.0, 1.0], dtype=float),
        )
        for index, path in enumerate(raw_paths)
    }
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline._owner_bound_raw_opener",
        lambda acquisition_source, _mass_recalibration: (
            lambda path, dll_dir: process_handles[path]
        ),
    )

    submitted_jobs: list[DiscoverySampleProcessJob] = []
    completed_worker_results = []

    class _SynchronousExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            submitted_jobs.append(job)
            future = Future()
            result = worker(job)
            completed_worker_results.append(result)
            future.set_result(result)
            return future

        def shutdown(self, *, wait, cancel_futures=False) -> None:
            pass

    process = run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "process-spec",
        settings=settings,
        dll_dir=tmp_path / "dll",
        max_workers=2,
        executor_factory=_SynchronousExecutor,
        run_spec=spec,
    )

    assert tuple(job.run_spec for job in submitted_jobs) == (spec, spec)
    assert tuple(job.acquisition_input for job in submitted_jobs) == (
        spec.acquisition_inputs
    )
    assert all(pickle.loads(pickle.dumps(job)) == job for job in submitted_jobs)
    assert _read_csv(serial.batch_index_csv) == _read_csv(process.batch_index_csv)
    for serial_output, process_output in zip(
        serial.per_sample, process.per_sample, strict=True
    ):
        assert serial_output.physical_facts_json is not None
        assert process_output.physical_facts_json is not None
        assert serial_output.physical_facts_json.read_bytes() == (
            process_output.physical_facts_json.read_bytes()
        )
        assert serial_output.physical_bindings
        assert process_output.physical_bindings == serial_output.physical_bindings
        assert pickle.loads(pickle.dumps(process_output.physical_bindings)) == (
            process_output.physical_bindings
        )

    context = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=1,
        mp_context=context,
    ) as executor:
        restored_result = executor.submit(
            pickle.loads,
            pickle.dumps(completed_worker_results[0]),
        ).result(timeout=30)
    assert restored_result.sample_index == completed_worker_results[0].sample_index
    assert restored_result.publication.receipt == (
        completed_worker_results[0].publication.receipt
    )
    assert restored_result.publication.index_row == (
        completed_worker_results[0].publication.index_row
    )
    assert restored_result.publication.outputs == process.per_sample[0]

    first_bindings = serial.per_sample[0].physical_bindings
    assert first_bindings[0].run_spec_id == spec.run_spec_id
    assert first_bindings[0].acquisition_source == (
        spec.acquisition_inputs[0].acquisition_source
    )


def test_real_spawn_rejects_full_mismatched_process_job_with_exact_identity(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"real-spawn-mismatched-job")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    acquisition_input = spec.acquisition_inputs[0]
    source = acquisition_input.acquisition_source
    job = DiscoverySampleProcessJob(
        sample_index=0,
        raw_path=raw_path,
        output_dir=tmp_path / "out",
        settings=replace(settings, seed_rt_gap_min=0.33),
        dll_dir=tmp_path / "dll",
        acquisition_source=source,
        run_spec=spec,
        acquisition_input=acquisition_input,
    )

    context = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=1,
        mp_context=context,
    ) as executor:
        result = executor.submit(_run_discovery_sample_process_job, job).result(
            timeout=30
        )

    assert isinstance(result, DiscoverySampleProcessError)
    assert result.message == "ValueError: Discovery process job crosses method settings"
    assert result.run_spec_id == spec.run_spec_id
    assert result.signal_processing_profile == spec.signal_processing_profile
    assert result.acquisition_source_id == source.acquisition_source_id
    assert all(
        value.strip()
        for value in (
            result.run_spec_id,
            result.acquisition_source_id,
        )
    )


def test_process_job_validates_binding_and_preserves_failure_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"process identity")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    acquisition_input = spec.acquisition_inputs[0]
    source = acquisition_input.acquisition_source
    opener_requests: list[Path] = []

    def _rejecting_owner_opener(_source, _mass_recalibration):
        def _open(path: Path, _dll_dir: Path):
            opener_requests.append(path)
            raise RuntimeError("owner boom")

        return _open

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline._owner_bound_raw_opener",
        _rejecting_owner_opener,
    )
    valid_job = DiscoverySampleProcessJob(
        sample_index=0,
        raw_path=raw_path,
        output_dir=tmp_path / "out",
        settings=settings,
        dll_dir=tmp_path / "dll",
        acquisition_source=source,
        run_spec=spec,
        acquisition_input=acquisition_input,
    )

    failure = _run_discovery_sample_process_job(valid_job)

    assert isinstance(failure, DiscoverySampleProcessError)
    assert failure.run_spec_id == spec.run_spec_id
    assert failure.signal_processing_profile == spec.signal_processing_profile
    assert failure.acquisition_source_id == source.acquisition_source_id
    assert failure.message == "RuntimeError: owner boom"
    assert opener_requests == [raw_path]

    crossed_job = replace(
        valid_job,
        settings=replace(settings, seed_rt_gap_min=0.33),
    )
    crossed = _run_discovery_sample_process_job(crossed_job)
    assert isinstance(crossed, DiscoverySampleProcessError)
    assert (
        crossed.message == "ValueError: Discovery process job crosses method settings"
    )
    assert crossed.run_spec_id == spec.run_spec_id
    assert crossed.signal_processing_profile == spec.signal_processing_profile
    assert crossed.acquisition_source_id == source.acquisition_source_id
    assert opener_requests == [raw_path]
