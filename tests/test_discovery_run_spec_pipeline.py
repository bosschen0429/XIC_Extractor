from __future__ import annotations

import concurrent.futures
import multiprocessing
import pickle
from concurrent.futures import Future
from dataclasses import replace
from functools import partial
from pathlib import Path

import numpy as np
import pytest

from tests.discovery_pipeline_fixtures import (
    _OwnerBoundFakeRawHandle,
    _read_csv,
    _scan_event,
    _settings,
)
from xic_extractor.discovery.csv_reader import (
    read_discovery_batch_index,
    read_owner_bound_discovery_peaks_csv,
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
def test_discovery_persists_feature_facts_and_measured_review_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
) -> None:
    from xic_extractor.discovery import output_publication, pipeline
    from xic_extractor.discovery.csv_writer import write_discovery_peaks_csv

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

    def capture_publication(raw_path, candidates, events, **kwargs):
        captured[raw_path.stem] = (candidates, kwargs)
        return publish_sample(raw_path, candidates, events, **kwargs)

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
        candidates, _publication = captured[acquisition.sample_stem]
        # Only the m/z 700 and 701 ions have MS1 signal: two measured peaks.
        assert {item.precursor_mz_basis for item in candidates} == {
            "scan_precursor",
            "product_plus_neutral_loss",
        }
        assert len(candidates) == 2
        expected_path = tmp_path / f"{acquisition.sample_stem}.csv"
        write_discovery_peaks_csv(expected_path, candidates)
        assert output.peaks_csv.read_bytes() == expected_path.read_bytes()
        # The m/z 500 and 501 events are recorded without a peak.
        events = _read_csv(output.events_csv)
        assert {row["peak_id"] for row in events if row["peak_id"]} <= {
            item.peak_id for item in candidates
        }
        assert any(not row["peak_id"] and float(row["xic_mz"]) < 600 for row in events)
        receipt = batch.peak_receipts[acquisition.sample_stem]
        assert receipt.peak_count == 2
        assert (
            len(
                read_owner_bound_discovery_peaks_csv(
                    output.peaks_csv,
                    receipt=receipt,
                )
            )
            == 2
        )
        assert not (output.peaks_csv.parent / "discovery_physical_facts.json").exists()


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
            rt=np.linspace(7.4 + index, 8.6 + index, 241),
            intensity=1000.0
            * np.exp(-0.5 * ((np.linspace(7.4, 8.6, 241) - 8.0) / 0.04) ** 2),
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
            rt=np.linspace(7.4 + index, 8.6 + index, 241),
            intensity=1000.0
            * np.exp(-0.5 * ((np.linspace(7.4, 8.6, 241) - 8.0) / 0.04) ** 2),
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
        assert serial_output.peaks_csv.read_bytes() == (
            process_output.peaks_csv.read_bytes()
        )
        assert serial_output.events_csv.read_bytes() == (
            process_output.events_csv.read_bytes()
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
        settings=replace(settings, ms1_search_padding_min=0.33),
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
    assert result.signal_processing_profile == spec.signal_processing_profile
    assert result.acquisition_source_id == source.acquisition_source_id


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
    assert failure.signal_processing_profile == spec.signal_processing_profile
    assert failure.acquisition_source_id == source.acquisition_source_id
    assert failure.message == "RuntimeError: owner boom"
    assert opener_requests == [raw_path]

    crossed_job = replace(
        valid_job,
        settings=replace(settings, ms1_search_padding_min=0.33),
    )
    crossed = _run_discovery_sample_process_job(crossed_job)
    assert isinstance(crossed, DiscoverySampleProcessError)
    assert (
        crossed.message == "ValueError: Discovery process job crosses method settings"
    )
    assert crossed.signal_processing_profile == spec.signal_processing_profile
    assert crossed.acquisition_source_id == source.acquisition_source_id
    assert opener_requests == [raw_path]
