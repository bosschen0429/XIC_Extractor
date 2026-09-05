from __future__ import annotations

import concurrent.futures
import multiprocessing
import pickle
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.test_discovery_pipeline import (
    _OwnerBoundFakeRawHandle,
    _peak_config,
    _read_csv,
    _scan_event,
    _settings,
)
from xic_extractor.acquisition_contract import (
    mint_raw_acquisition_session_capability,
)
from xic_extractor.alignment.csv_io import (
    read_discovery_batch_index,
    read_owner_bound_discovery_candidates_csv,
)
from xic_extractor.discovery.batch_contract import (
    validate_discovery_candidate_physical_bindings,
)
from xic_extractor.discovery.pipeline import (
    DiscoverySampleProcessError,
    DiscoverySampleProcessJob,
    _run_discovery_sample_process_job,
    run_discovery_batch,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)


def test_physical_binding_validation_joins_canonical_candidates_by_id(
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
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda _path, _dll_dir: raw,
        run_spec=spec,
    )
    batch = read_discovery_batch_index(
        outputs.batch_index_csv,
        require_owner_closure=True,
    )
    receipt = batch.candidate_receipts[raw_path.stem]
    assert receipt is not None
    canonical_candidates = read_owner_bound_discovery_candidates_csv(
        batch.candidate_csvs[raw_path.stem],
        receipt=receipt,
        require_successor_columns=True,
    )
    bindings = outputs.per_sample[0].physical_bindings
    reordered_candidates = tuple(reversed(canonical_candidates))
    assert tuple(binding.candidate.candidate_id for binding in bindings) != tuple(
        candidate.candidate_id for candidate in reordered_candidates
    )

    validated = validate_discovery_candidate_physical_bindings(
        run_spec=spec,
        sample_stem=raw_path.stem,
        candidates=reordered_candidates,
        bindings=bindings,
    )

    assert validated == bindings


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
        peak_config=_peak_config(tmp_path),
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
        lambda acquisition_source, raw_session_capability: (
            lambda path, dll_dir: process_handles[path]
        ),
    )

    submitted_jobs: list[DiscoverySampleProcessJob] = []

    class _SynchronousExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            submitted_jobs.append(job)
            future = Future()
            future.set_result(worker(job))
            return future

        def shutdown(self, *, wait, cancel_futures=False) -> None:
            pass

    process = run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "process-spec",
        settings=settings,
        peak_config=_peak_config(tmp_path),
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
        restored_output = executor.submit(
            pickle.loads,
            pickle.dumps(process.per_sample[0]),
        ).result(timeout=30)
    assert restored_output.physical_bindings == (
        process.per_sample[0].physical_bindings
    )

    first_bindings = serial.per_sample[0].physical_bindings
    assert first_bindings[0].run_spec_id == spec.run_spec_id
    assert first_bindings[0].acquisition_source == (
        spec.acquisition_inputs[0].acquisition_source
    )
    first_candidates = tuple(binding.candidate for binding in first_bindings)
    with pytest.raises(ValueError, match="cardinality mismatch"):
        validate_discovery_candidate_physical_bindings(
            run_spec=spec,
            sample_stem=raw_paths[0].stem,
            candidates=first_candidates,
            bindings=(),
        )
    with pytest.raises(ValueError, match="order is not contiguous"):
        validate_discovery_candidate_physical_bindings(
            run_spec=spec,
            sample_stem=raw_paths[0].stem,
            candidates=first_candidates,
            bindings=(replace(first_bindings[0], final_ordinal=1),),
        )
    with pytest.raises(ValueError, match="crosses sample identity"):
        validate_discovery_candidate_physical_bindings(
            run_spec=spec,
            sample_stem=raw_paths[1].stem,
            candidates=first_candidates,
            bindings=first_bindings,
        )

    crossed_binding = replace(
        serial.per_sample[0].physical_bindings[0],
        candidate=replace(
            serial.per_sample[0].physical_bindings[0].candidate,
            ms2_product_max_intensity=(
                serial.per_sample[0]
                .physical_bindings[0]
                .candidate.ms2_product_max_intensity
                + 1.0
            ),
        ),
    )
    with pytest.raises(ValueError, match="crosses candidate content"):
        validate_discovery_candidate_physical_bindings(
            run_spec=spec,
            sample_stem=raw_paths[0].stem,
            candidates=first_candidates,
            bindings=(crossed_binding,),
        )

    foreign_dir = tmp_path / "foreign"
    foreign_dir.mkdir()
    foreign_paths = tuple(foreign_dir / path.name for path in raw_paths)
    for foreign_path in foreign_paths:
        foreign_path.write_bytes(f"foreign-{foreign_path.stem}".encode())
    foreign_spec = compile_discovery_processing_run_spec(
        foreign_paths,
        discovery_settings=settings,
    )
    foreign_binding = replace(
        first_bindings[0],
        run_spec_id=foreign_spec.run_spec_id,
    )
    with pytest.raises(ValueError, match="crosses processing run"):
        validate_discovery_candidate_physical_bindings(
            run_spec=spec,
            sample_stem=raw_paths[0].stem,
            candidates=first_candidates,
            bindings=(foreign_binding,),
        )

    with pytest.raises(ValueError, match="crosses acquisition record"):
        replace(
            first_bindings[0],
            acquisition_source=foreign_spec.acquisition_inputs[0].acquisition_source,
        )
    acquisition_crossed_binding = pickle.loads(pickle.dumps(first_bindings[0]))
    object.__setattr__(
        acquisition_crossed_binding,
        "acquisition_source",
        foreign_spec.acquisition_inputs[0].acquisition_source,
    )
    with pytest.raises(ValueError, match="crosses acquisition record"):
        validate_discovery_candidate_physical_bindings(
            run_spec=spec,
            sample_stem=raw_paths[0].stem,
            candidates=first_candidates,
            bindings=(acquisition_crossed_binding,),
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
        peak_config=_peak_config(tmp_path),
        acquisition_source=source,
        raw_session_capability=mint_raw_acquisition_session_capability(source),
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
    assert result.profile_id == spec.profile_id
    assert result.acquisition_source_id == source.acquisition_source_id
    assert all(
        value.strip()
        for value in (
            result.run_spec_id,
            result.profile_id,
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
    capability = mint_raw_acquisition_session_capability(source)
    opener_requests: list[Path] = []

    def _rejecting_owner_opener(_source, _capability):
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
        peak_config=_peak_config(tmp_path),
        acquisition_source=source,
        raw_session_capability=capability,
        run_spec=spec,
        acquisition_input=acquisition_input,
    )

    failure = _run_discovery_sample_process_job(valid_job)

    assert isinstance(failure, DiscoverySampleProcessError)
    assert failure.run_spec_id == spec.run_spec_id
    assert failure.profile_id == spec.profile_id
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
    assert crossed.profile_id == spec.profile_id
    assert crossed.acquisition_source_id == source.acquisition_source_id
    assert opener_requests == [raw_path]
