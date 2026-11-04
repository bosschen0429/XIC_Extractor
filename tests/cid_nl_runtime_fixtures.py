"""Shared synthetic CID-NL runtime wiring and partition parity assertions."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.alignment_pipeline_helpers import physical_facts_fingerprint_beside
from tests.cid_nl_raw_fixture import (
    _FakeRaw,
    _FakeThermoRaw,
    _working_imports,
)
from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.discovery.batch_contract import (
    DiscoveryCandidateUniverseReceipt,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.processing_identity import (
    build_local_acquisition_source_identity,
)
from xic_extractor.raw_reader import (
    EXPECTED_THERMO_DLLS,
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    open_raw,
)
from xic_extractor.untargeted_execution.partition import cid_nl_acquisition
from xic_extractor.untargeted_execution.partition import execution as runtime_module
from xic_extractor.untargeted_execution.partition import workers as partition_workers


def build_discovery_receipts(
    root: Path,
    raw_paths: tuple[Path, ...],
    candidates_by_sample: dict[str, tuple[DiscoveryCandidate, ...]],
) -> dict[str, DiscoveryCandidateUniverseReceipt]:
    receipts: dict[str, DiscoveryCandidateUniverseReceipt] = {}
    for raw_path in raw_paths:
        candidates = candidates_by_sample[raw_path.stem]
        candidate_csv = root / f"{raw_path.stem}.owner-candidates.csv"
        write_discovery_candidates_csv(candidate_csv, candidates)
        source = build_local_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        receipts[raw_path.stem] = mint_discovery_candidate_universe_receipt(
            sample_stem=raw_path.stem,
            acquisition_source=source,
            candidate_csv=candidate_csv,
            physical_facts=physical_facts_fingerprint_beside(candidate_csv),
            candidates=candidates,
        )
    return receipts


def install_runtime_raw(
    monkeypatch,
    *,
    source_raw: _FakeRaw,
    raw_path: Path,
    tmp_path: Path,
) -> _FakeThermoRaw:
    monkeypatch.setattr(runtime_module, "ensure_assemblies_loaded", lambda *_: None)
    for filename in EXPECTED_THERMO_DLLS:
        (tmp_path / filename).write_bytes(b"dll")
    vendor_raw = _FakeThermoRaw(source_raw)

    def _open_runtime_raw(
        path,
        dll_dir,
        *,
        mass_recalibration="none",
        expected_acquisition_source,
        _expected_path_identity=None,
        prepared_mass_recalibration=None,
    ):
        assert path == raw_path
        return open_raw(
            path,
            dll_dir,
            mass_recalibration=mass_recalibration,
            expected_acquisition_source=expected_acquisition_source,
            _expected_path_identity=_expected_path_identity,
            prepared_mass_recalibration=prepared_mass_recalibration,
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
                ms1_order="Ms1",
            ),
        )

    monkeypatch.setattr(partition_workers, "open_raw", _open_runtime_raw)
    return vendor_raw


def install_runtime_raws(
    monkeypatch,
    *,
    sources_by_path: dict[Path, _FakeRaw],
    tmp_path: Path,
) -> dict[Path, list[_FakeThermoRaw]]:
    # Keep synthetic spectra on the production worker/reader path, without vendor I/O.
    monkeypatch.setattr(runtime_module, "ensure_assemblies_loaded", lambda *_: None)

    def _forbid_vendor_api(_import_module):
        pytest.fail("Synthetic RAW fixtures must not reach the real Thermo API")

    monkeypatch.setattr("xic_extractor.raw_reader._load_thermo_api", _forbid_vendor_api)

    # This fixture supplies spectra, not vendor headers; the analysis-set owner
    # still orders the synthetic files without opening fake bytes natively.
    install_fake_raw_headers(monkeypatch)
    for filename in EXPECTED_THERMO_DLLS:
        (tmp_path / filename).write_bytes(b"dll")
    opened: dict[Path, list[_FakeThermoRaw]] = {}

    def _open_runtime_raw(
        path,
        dll_dir,
        *,
        mass_recalibration="none",
        expected_acquisition_source,
        _expected_path_identity=None,
        prepared_mass_recalibration=None,
    ):
        vendor_raw = _FakeThermoRaw(sources_by_path[path])
        opened.setdefault(path, []).append(vendor_raw)
        return open_raw(
            path,
            dll_dir,
            mass_recalibration=mass_recalibration,
            expected_acquisition_source=expected_acquisition_source,
            _expected_path_identity=_expected_path_identity,
            prepared_mass_recalibration=prepared_mass_recalibration,
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
                ms1_order="Ms1",
            ),
        )

    monkeypatch.setattr(partition_workers, "open_raw", _open_runtime_raw)
    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", _open_runtime_raw)
    return opened


def assert_partition_matches_runtime(jobs, full_result, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("RAW must not open")

    observed_batches = []
    for name in (
        "acquire_untargeted_cid_nl_sample_trace_batch",
        "acquire_untargeted_cid_nl_backfill_trace_batch",
    ):
        original = getattr(cid_nl_acquisition, name)

        def observe(*args, _original=original, **kwargs):
            batch = _original(*args, **kwargs)
            if batch is not None:
                observed_batches.append(batch.acquisition)
            return batch

        monkeypatch.setattr(cid_nl_acquisition, name, observe)
    events = []
    result = runtime_module.execute_partition(
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
            actual.adapter_result.source_candidate_ids
            == expected.adapter_result.source_candidate_ids
        )
        assert tuple(
            a.assessment.content.evaluation_receipt for a in actual.backfill_attempts
        ) == tuple(
            a.assessment.content.evaluation_receipt for a in expected.backfill_attempts
        )
    assert observed_batches
    assert all(batch.traces for batch in observed_batches)
    for stage in ("partition_initial", "partition_backfill", "partition_seal"):
        progress = [event for event in events if event.get("stage") == stage]
        assert progress[-1]["completed"] == progress[-1]["total"]
    # A RAW open failure fails the partition and names its cause.
    monkeypatch.setattr(partition_workers, "open_raw", forbidden)
    from xic_extractor.untargeted_execution.partition import workers as worker_module

    monkeypatch.setattr(worker_module, "open_raw", forbidden)
    failed_jobs = tuple(replace(job, acquisition_cache_dir=None) for job in jobs)
    failed = runtime_module.execute_partition(
        failed_jobs,
        partition_key="part-error",
        alignment_config=runtime_module.CID_NL_ALIGNMENT_CONFIG,
    )
    assert not failed.succeeded
    assert isinstance(failed.failure, AssertionError)
    assert str(failed.failure) == "RAW must not open"
