from __future__ import annotations

import concurrent.futures
import multiprocessing
import pickle
import sqlite3
from concurrent.futures import Future
from contextlib import closing
from pathlib import Path

import numpy as np
import pytest

from tests.discovery_peak_fixtures import representative_event
from tests.discovery_pipeline_fixtures import (
    _OwnerBoundFakeRawHandle,
    _scan_event,
    _settings,
)
from tests.discovery_store_fixtures import stored_events, stored_peaks
from tests.execution_fixtures import in_process_execution
from xic_extractor.configuration.execution_profile import (
    ExecutionProfile,
)
from xic_extractor.discovery.cid_nl_search import DiscoverySampleJob
from xic_extractor.discovery.pipeline import run_discovery_batch
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.xic_models import XICTrace


@pytest.mark.parametrize("workers", (1, 2))
def test_discovery_persists_feature_facts_and_measured_review_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
) -> None:
    from xic_extractor.discovery import cid_nl_search

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
    publish_sample = cid_nl_search.publish_discovery_batch_sample

    def capture_publication(raw_path, candidates, events, **kwargs):
        captured[raw_path.stem] = (candidates, kwargs)
        return publish_sample(raw_path, candidates, events, **kwargs)

    monkeypatch.setattr(
        cid_nl_search, "publish_discovery_batch_sample", capture_publication
    )
    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda path, _dll, **_kwargs: handles[path],
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
        spec,
        output_dir=tmp_path / "discovery",
        dll_dir=tmp_path / "dll",
        execution=ExecutionProfile(parallel_workers=workers),
        raw_opener=(lambda path, _dll: handles[path]) if workers == 1 else None,
        executor_factory=ImmediateExecutor if workers > 1 else None,
    )
    peaks = stored_peaks(outputs.store)
    events = stored_events(outputs.store)
    for acquisition in spec.acquisition_inputs:
        stem = acquisition.raw_path.stem
        candidates, _publication = captured[stem]
        assert {
            representative_event(item).scan.precursor_mz_basis for item in candidates
        } == {"scan_precursor", "product_plus_neutral_loss"}
        assert len(candidates) == 2
        assert [p for p in peaks if p.sample_stem == stem] == list(candidates)
        sample_events = [item for item in events if item.sample_stem == stem]
        assert {item.peak_id for item in sample_events if item.peak_id} <= {
            item.peak_id for item in candidates
        }
        assert any(
            not item.peak_id and item.event.scan.precursor_mz < 600
            for item in sample_events
        )
    assert sorted(path.name for path in outputs.store.parent.iterdir()) == [
        "discovery.sqlite"
    ]


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
        spec,
        output_dir=tmp_path / "serial-spec",
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: serial_handles[path],
        execution=in_process_execution(),
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
        "xic_extractor.raw_reader.open_raw",
        lambda path, dll_dir, **_kwargs: process_handles[path],
    )

    submitted_jobs: list[DiscoverySampleJob] = []
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
        spec,
        output_dir=tmp_path / "process-spec",
        dll_dir=tmp_path / "dll",
        execution=ExecutionProfile(parallel_workers=2),
        executor_factory=_SynchronousExecutor,
    )

    assert tuple(job.acquisition_input for job in submitted_jobs) == (
        spec.acquisition_inputs
    )
    assert all(pickle.loads(pickle.dumps(job)) == job for job in submitted_jobs)
    assert serial.sample_stems == process.sample_stems
    assert _dump(serial.store) == _dump(process.store)

    context = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=1,
        mp_context=context,
    ) as executor:
        restored_result = executor.submit(
            pickle.loads,
            pickle.dumps(completed_worker_results[0]),
        ).result(timeout=30)
    assert restored_result.publication == completed_worker_results[0].publication


def _dump(store: Path) -> list[str]:
    """The file's full content, statement by statement."""
    with closing(sqlite3.connect(store)) as db:
        return list(db.iterdump())
