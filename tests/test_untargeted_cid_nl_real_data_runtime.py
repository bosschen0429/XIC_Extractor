from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    _current_candidates,
    _FakeRaw,
    _FakeThermoRaw,
    _open_bound_raw,
    _peak_config,
    _settings,
    _source_for,
    _working_imports,
)
from tests.processing_identity_no_raw_fixture import (
    build_targeted_processing_identity_raw_fixture,
)
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_untargeted_cid_nl_real_data_adapter import _without_ms1_peak
from xic_extractor.acquisition_contract import (
    TraceCoordinateKind,
    acquire_trace_batch,
    mint_trace_acquisition_capability,
    serialize_acquired_trace,
)
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    build_untargeted_cid_nl_golden_path,
)
from xic_extractor.discovery.batch_contract import (
    ALIGNMENT_FEATURE_INPUT_KEY,
    DiscoveryCandidateUniverseReceipt,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.execution._finalization import RuntimeResourceOpenOutcome
from xic_extractor.execution.attempt import start_runtime_attempt
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.extraction.untargeted_acquisition_cache import (
    CachedUntargetedRaw,
    acquire_untargeted_trace_batch,
    bind_untargeted_acquisition_cache,
)
from xic_extractor.extraction.untargeted_partition import cid_nl_acquisition
from xic_extractor.extraction.untargeted_partition import execution as runtime_module
from xic_extractor.extraction.untargeted_partition import models as partition_models
from xic_extractor.extraction.untargeted_partition import workers as partition_workers
from xic_extractor.extraction.untargeted_partition.execution import (
    execute_attempt,
    runtime_call_cost,
)
from xic_extractor.extraction.untargeted_partition.models import (
    UntargetedPartitionError,
    UntargetedPartitionJob,
    build_sample_jobs,
)
from xic_extractor.extraction.untargeted_partition.request import (
    build_partition_context,
)
from xic_extractor.peak_detection import capture as capture_owner
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.processing_identity import (
    IdentityContractError,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.raw_reader import (
    EXPECTED_THERMO_DLLS,
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    RawReaderError,
    open_raw,
    reset_reader_state,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.xic_models import XICRequest


@pytest.fixture(autouse=True)
def _disable_real_dll_loading(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "_ensure_assemblies_loaded", lambda *_: None)


@pytest.mark.parametrize("winner", [b"same facts", b"different facts", None])
def test_acquisition_cache_windows_publish_race(tmp_path, monkeypatch, winner):
    from xic_extractor.extraction import untargeted_acquisition_cache as cache_owner

    cache = cache_owner._FactCache(tmp_path, "source")
    key = b"query"
    payload = b"same facts"

    def concurrent_publish(temporary, destination):
        if winner is not None:
            destination.write_bytes(
                cache_owner._canonical_json(cache._envelope(key, winner))
            )
        raise PermissionError("destination is held by a concurrent reader")

    monkeypatch.setattr(cache_owner.os, "replace", concurrent_publish)
    if winner == payload:
        cache.write("ms2", key, payload)
        assert cache.read("ms2", key) == payload
    elif winner is None:
        with pytest.raises(PermissionError):
            cache.write("ms2", key, payload)
    else:
        with pytest.raises(cache_owner.UntargetedAcquisitionCacheError):
            cache.write("ms2", key, payload)
        assert cache.read("ms2", key) == winner
    assert not tuple(tmp_path.rglob("*.tmp"))


def test_fact_cache_read_preserves_facts_without_outer_reencoding(
    tmp_path: Path, monkeypatch
) -> None:
    from xic_extractor.extraction import untargeted_acquisition_cache as cache_owner

    cache = cache_owner._FactCache(tmp_path, "source")
    key = b"numeric-fact-key"
    payload = b'{"area": "1.25", "height": "2.5"}'
    cache.write("ms2", key, payload)
    calls = {"envelope": 0, "canonical_json": 0}
    original_envelope = cache_owner._FactCache._envelope
    original_canonical_json = cache_owner._canonical_json

    def count_envelope(self, envelope_key, envelope_payload):
        calls["envelope"] += 1
        return original_envelope(self, envelope_key, envelope_payload)

    def count_canonical_json(value):
        calls["canonical_json"] += 1
        return original_canonical_json(value)

    monkeypatch.setattr(cache_owner._FactCache, "_envelope", count_envelope)
    monkeypatch.setattr(cache_owner, "_canonical_json", count_canonical_json)
    assert cache.read("ms2", key) == payload
    assert calls == {"envelope": 0, "canonical_json": 0}


def test_fact_cache_rejects_wrong_identity_and_invalid_base64(tmp_path: Path) -> None:
    from xic_extractor.extraction import untargeted_acquisition_cache as cache_owner

    mutations = {
        "schema_version": "wrong-schema",
        "source_id": "wrong-source",
        "key_sha256": "0" * 64,
        "payload_sha256": "1" * 64,
        "payload_base64": "not-base64!",
    }
    for index, (field, value) in enumerate(mutations.items()):
        cache = cache_owner._FactCache(tmp_path / field, "source")
        key = f"key-{index}".encode()
        payload = b'{"numeric": "3.5"}'
        cache.write("ms2", key, payload)
        path = cache._path("ms2", key)
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope[field] = value
        path.write_text(json.dumps(envelope), encoding="utf-8")
        assert cache.read("ms2", key) is None
        assert not path.exists()


def test_acquisition_cache_replays_xic_and_ms2_and_reacquires_only_rejected_query(
    tmp_path: Path,
) -> None:
    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path,
        sample_stems=("S1", "S2"),
        peak_config=_peak_config(tmp_path),
    )
    source_raw = _FakeRaw((8.0,))
    source = _source_for(fixture, "S1")
    cache_dir = tmp_path / ".cid_nl_acquisition_cache"
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="untargeted-cache-test",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    first_request = XICRequest(mz=500.0, rt_min=7.8, rt_max=8.2, ppm_tol=10.0)
    second_request = replace(first_request, mz=500.01)

    def acquire(requests: tuple[XICRequest, ...]):
        handle, vendor = _open_bound_raw(
            fixture,
            "S1",
            source_raw,
            tmp_path=tmp_path,
        )
        ms2_reads = 0
        original = vendor.GetFilterForScanNumber

        def count_ms2(scan_number: int):
            nonlocal ms2_reads
            ms2_reads += 1
            return original(scan_number)

        vendor.GetFilterForScanNumber = count_ms2
        cached = bind_untargeted_acquisition_cache(
            handle,
            source=source,
            cache_dir=cache_dir,
        )
        with cached:
            batch = acquire_untargeted_trace_batch(capability, requests, cached)
            events = tuple(cached.iter_ms2_scans(7.8, 8.2))
        return (batch, vendor, ms2_reads, events)

    cold_batch, cold_vendor, cold_ms2_reads, cold_events = acquire((first_request,))
    warm_batch, warm_vendor, warm_ms2_reads, warm_events = acquire((first_request,))

    assert tuple(map(serialize_acquired_trace, warm_batch.traces)) == tuple(
        map(serialize_acquired_trace, cold_batch.traces)
    )
    assert len(warm_events) == len(cold_events)
    for warm, cold in zip(warm_events, cold_events, strict=True):
        assert warm.scan_number == cold.scan_number
        assert warm.parse_error == cold.parse_error
        assert warm.scan is not None and cold.scan is not None
        np.testing.assert_array_equal(warm.scan.masses, cold.scan.masses)
        np.testing.assert_array_equal(warm.scan.intensities, cold.scan.intensities)
    assert cold_vendor.chromatogram_request_counts == [1]
    assert cold_ms2_reads > 0
    assert warm_vendor.chromatogram_request_counts == []
    assert warm_ms2_reads == 0

    changed_batch, changed_vendor, changed_ms2_reads, _events = acquire(
        (first_request, second_request)
    )
    assert len(changed_batch.traces) == 2
    assert changed_vendor.chromatogram_request_counts == [1]
    assert changed_ms2_reads == 0

    xic_entries = sorted(cache_dir.glob("*/xic/*.json"))
    assert len(xic_entries) == 2
    xic_entries[0].write_bytes(b"truncated")
    _repaired, repaired_vendor, repaired_ms2_reads, _events = acquire(
        (first_request, second_request)
    )
    assert repaired_vendor.chromatogram_request_counts == [1]
    assert repaired_ms2_reads == 0

    ms2_entries_before = set(cache_dir.glob("*/ms2/*.json"))
    transient_handle, transient_vendor = _open_bound_raw(
        fixture, "S1", source_raw, tmp_path=tmp_path
    )

    def transient_scan_failure(_scan_number: int):
        raise RuntimeError("temporary vendor scan read failure")

    transient_vendor.GetSimplifiedScan = transient_scan_failure
    transient_cached = bind_untargeted_acquisition_cache(
        transient_handle, source=source, cache_dir=cache_dir
    )
    with transient_cached:
        transient_events = tuple(transient_cached.iter_ms2_scans(7.95, 8.05))
    assert any(event.scan is None and event.parse_error for event in transient_events)
    assert set(cache_dir.glob("*/ms2/*.json")) == ms2_entries_before

    healthy_handle, _healthy_vendor = _open_bound_raw(
        fixture, "S1", source_raw, tmp_path=tmp_path
    )
    healthy_cached = bind_untargeted_acquisition_cache(
        healthy_handle, source=source, cache_dir=cache_dir
    )
    with healthy_cached:
        healthy_events = tuple(healthy_cached.iter_ms2_scans(7.95, 8.05))
    assert healthy_events
    assert all(
        event.scan is not None and event.parse_error is None for event in healthy_events
    )
    assert len(set(cache_dir.glob("*/ms2/*.json"))) == len(ms2_entries_before) + 1

    replay_handle, replay_vendor = _open_bound_raw(
        fixture, "S1", source_raw, tmp_path=tmp_path
    )

    def forbid_scan_read(_scan_number: int):
        raise AssertionError("successful MS2 window should replay without vendor reads")

    replay_vendor.GetFilterForScanNumber = forbid_scan_read
    replay_vendor.GetSimplifiedScan = forbid_scan_read
    replay_cached = bind_untargeted_acquisition_cache(
        replay_handle, source=source, cache_dir=cache_dir
    )
    with replay_cached:
        replay_events = tuple(replay_cached.iter_ms2_scans(7.95, 8.05))
    assert len(replay_events) == len(healthy_events)
    assert all(event.scan is not None for event in replay_events)

    second_source = _source_for(fixture, "S2")
    second_capability = mint_trace_acquisition_capability(
        second_source,
        adapter_namespace="untargeted-cache-test",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    second_handle, second_vendor = _open_bound_raw(
        fixture, "S2", source_raw, tmp_path=tmp_path
    )
    second_cached = bind_untargeted_acquisition_cache(
        second_handle, source=second_source, cache_dir=cache_dir
    )
    with second_cached:
        second_batch = acquire_untargeted_trace_batch(
            second_capability, (first_request,), second_cached
        )
    assert len(second_batch.traces) == 1
    assert second_vendor.chromatogram_request_counts == [1]


def test_warm_ms2_full_window_subset_hydrates_selected_scan_without_opening_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.extraction import untargeted_acquisition_cache as cache_module

    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    source = _source_for(fixture, "S1")
    source_raw = _FakeRaw((7.95, 8.05))
    cache_dir = tmp_path / "full-window-cache"
    cold_handle, _cold_vendor = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )
    with CachedUntargetedRaw(
        cold_handle,
        source=source,
        cache_dir=cache_dir,
    ) as cold:
        full_events = tuple(cold.iter_ms2_scans(7.8, 8.2))
    assert len(full_events) == 2
    selected_number = full_events[-1].scan_number
    full_cache_before = {path: path.read_bytes() for path in cache_dir.rglob("*.json")}

    hydration_calls = 0
    original_hydrator = cache_module._ms2_event_from_payload

    def count_hydration(payload):
        nonlocal hydration_calls
        hydration_calls += 1
        return original_hydrator(payload)

    monkeypatch.setattr(cache_module, "_ms2_event_from_payload", count_hydration)

    def forbidden_open():
        raise AssertionError("warm subset replay attempted native open")

    warm = CachedUntargetedRaw(
        forbidden_open,
        source=source,
        cache_dir=cache_dir,
    )
    with warm:
        selected_events = tuple(
            warm.iter_ms2_scan_subset(
                7.8,
                8.2,
                frozenset({selected_number}),
            )
        )

    assert warm.native_open_attempted is False
    assert warm.native_opened is False
    assert hydration_calls == 1
    assert len(selected_events) == 1
    assert selected_events[0].scan_number == selected_number
    assert selected_events[0].scan is not None
    assert full_events[-1].scan is not None
    np.testing.assert_array_equal(
        selected_events[0].scan.masses,
        full_events[-1].scan.masses,
    )
    np.testing.assert_array_equal(
        selected_events[0].scan.intensities,
        full_events[-1].scan.intensities,
    )
    assert full_cache_before == {
        path: path.read_bytes() for path in cache_dir.rglob("*.json")
    }


def test_ms2_subset_cache_miss_writes_subset_key_without_full_window_pollution(
    tmp_path: Path,
) -> None:
    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    source = _source_for(fixture, "S1")
    source_raw = _FakeRaw((7.95, 8.05))
    selected_number = source_raw.scans[-1].scan_number
    handle, vendor = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )
    native_scan_reads = []
    original_scan_reader = vendor.GetSimplifiedScan

    def count_native_scan_read(scan_number):
        native_scan_reads.append(scan_number)
        return original_scan_reader(scan_number)

    vendor.GetSimplifiedScan = count_native_scan_read
    cache_dir = tmp_path / "subset-only-cache"
    cached = CachedUntargetedRaw(handle, source=source, cache_dir=cache_dir)
    with cached:
        events = tuple(
            cached.iter_ms2_scan_subset(
                7.8,
                8.2,
                frozenset({selected_number}),
            )
        )
        native_scan_reads.clear()
        full_events = tuple(cached.iter_ms2_scans(7.8, 8.2))

    assert [event.scan_number for event in events] == [selected_number]
    assert sorted(event.scan_number for event in full_events) == sorted(
        scan.scan_number for scan in source_raw.scans
    )
    assert native_scan_reads == sorted(scan.scan_number for scan in source_raw.scans)
    cache_files = tuple(cache_dir.rglob("*.json"))
    assert len(cache_files) == 2
    assert {path.parent.name for path in cache_files} == {"ms2"}
    assert cached.native_open_attempted is True


@pytest.mark.parametrize("use_cache", (False, True))
def test_untargeted_acquisition_batches_overlapping_scan_windows_exactly(
    tmp_path: Path, use_cache: bool
) -> None:
    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    source = _source_for(fixture, "S1")
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="untargeted-window-batch-test",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
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
    cache_dir = tmp_path / "cache" if use_cache else None
    raw = bind_untargeted_acquisition_cache(handle, source=source, cache_dir=cache_dir)
    with raw:
        actual = acquire_untargeted_trace_batch(capability, requests, raw)
    assert vendor.chromatogram_request_counts == [2, 1]
    assert actual.adapter_chromatogram_call_count == 2
    expected = tuple(map(serialize_acquired_trace, baseline.traces))
    assert tuple(map(serialize_acquired_trace, actual.traces)) == expected

    if cache_dir is not None:

        def forbid_open():
            raise AssertionError("cached exact requests must not reopen RAW")

        with CachedUntargetedRaw(
            forbid_open, source=source, cache_dir=cache_dir
        ) as warm:
            replay = acquire_untargeted_trace_batch(capability, requests, warm)
        assert not warm.native_open_attempted
        assert replay.adapter_chromatogram_call_count == 0
        assert tuple(map(serialize_acquired_trace, replay.traces)) == expected


def test_lazy_acquisition_cache_replay_skips_opener_and_opens_once_on_miss(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction import untargeted_acquisition_cache as cache_owner

    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
    )
    source_raw = _FakeRaw((8.0,))
    source = _source_for(fixture, "S1")
    cache_dir = tmp_path / ".cid_nl_acquisition_cache"
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="untargeted-cache-lazy-test",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    first_request = XICRequest(mz=500.0, rt_min=7.8, rt_max=8.2, ppm_tol=10.0)
    third_request = replace(first_request, mz=500.02)

    cold_handle, cold_vendor = _open_bound_raw(
        fixture, "S1", source_raw, tmp_path=tmp_path
    )
    cold_cached = bind_untargeted_acquisition_cache(
        cold_handle, source=source, cache_dir=cache_dir
    )
    with cold_cached:
        cold_batch = acquire_untargeted_trace_batch(
            capability, (first_request,), cold_cached
        )
        cold_events = tuple(cold_cached.iter_ms2_scans(7.8, 8.2))

    opener_calls: list[_FakeThermoRaw] = []

    def forbidden_opener():
        raise AssertionError("warm cache replay must not open the native handle")

    lazy_warm = CachedUntargetedRaw(
        forbidden_opener, source=source, cache_dir=cache_dir
    )
    with lazy_warm:
        warm_batch = acquire_untargeted_trace_batch(
            capability, (first_request,), lazy_warm
        )
        warm_events = tuple(lazy_warm.iter_ms2_scans(7.8, 8.2))
    assert opener_calls == []
    assert lazy_warm.native_open_attempted is False
    assert lazy_warm.native_opened is False
    assert tuple(map(serialize_acquired_trace, warm_batch.traces)) == tuple(
        map(serialize_acquired_trace, cold_batch.traces)
    )

    def event_payload(event):
        scan = event.scan
        return (
            event.scan_number,
            event.parse_error,
            None
            if scan is None
            else (
                scan.scan_number,
                scan.rt,
                scan.precursor_mz,
                tuple(scan.masses),
                tuple(scan.intensities),
                scan.base_peak,
            ),
        )

    assert tuple(map(event_payload, warm_events)) == tuple(
        map(event_payload, cold_events)
    )
    assert cold_vendor.disposed is True

    missing_handles: list[tuple[object, _FakeThermoRaw]] = []

    def open_missing():
        handle, vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
        missing_handles.append((handle, vendor))
        return handle

    lazy_missing = CachedUntargetedRaw(open_missing, source=source, cache_dir=cache_dir)
    with lazy_missing:
        missing_batch = acquire_untargeted_trace_batch(
            capability, (third_request,), lazy_missing
        )
    assert len(missing_batch.traces) == 1
    assert len(missing_handles) == 1
    assert missing_handles[0][1].disposed is True
    assert lazy_missing.native_open_attempted is True
    assert lazy_missing.native_opened is True

    cache = cache_owner._FactCache(cache_dir, source.acquisition_source_id)
    cache_path = cache._path("xic", cache_owner._trace_key(capability, third_request))
    cache_path.write_bytes(b"rejected cache payload")
    rejected_handles: list[tuple[object, _FakeThermoRaw]] = []

    def open_rejected():
        handle, vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
        rejected_handles.append((handle, vendor))
        return handle

    lazy_rejected = CachedUntargetedRaw(
        open_rejected, source=source, cache_dir=cache_dir
    )
    with lazy_rejected:
        rejected_batch = acquire_untargeted_trace_batch(
            capability, (third_request,), lazy_rejected
        )
    assert len(rejected_batch.traces) == 1
    assert len(rejected_handles) == 1
    assert rejected_handles[0][1].disposed is True


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
    context = build_partition_context(
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
        UntargetedPartitionJob(
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
            acquisition_source_expectation=context.acquisition_sources_by_sample[
                sample.metadata.raw_stem
            ],
            acquisition_cache_dir=tmp_path / "runtime-cache",
        )
        for sample in context.samples
    )
    from xic_extractor.extraction.untargeted_partition.workers import _open_job_raw

    changed_path = tmp_path / "Support.changed.synthetic"
    changed_path.write_bytes(b"changed-after-source-verification")
    with pytest.raises(
        RawReaderError,
        match="RAW path state does not match expected acquisition source",
    ):
        _open_job_raw(replace(jobs[0], raw_path=changed_path))
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation="untargeted-cid-nl-real-data-runtime-test",
    )

    result = execute_attempt(
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
    assert s2_assessment.content.technical_state == "not_evaluated"
    assert s2_assessment.content.method_result is None
    assert not s2_assessment.content.evaluation_receipt.search_completed
    assert by_stem["S1"].adapter_result.strict_scan_count == 2
    assert by_stem["S2"].adapter_result.acquisition is None
    assert vendor_raw.chromatogram_request_counts == [1]
    assert vendor_raw.disposed is True

    warm_vendor_raw = _install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_paths[0],
        tmp_path=tmp_path,
    )
    warm_open_calls: list[Path] = []
    installed_warm_open = partition_workers.open_raw

    def count_warm_open(path: Path, *args: object, **kwargs: object):
        warm_open_calls.append(path)
        return installed_warm_open(path, *args, **kwargs)

    monkeypatch.setattr(partition_workers, "open_raw", count_warm_open)
    warm_runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation="untargeted-cid-nl-real-data-runtime-warm-test",
    )
    warm_result = execute_attempt(
        warm_runtime,
        jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "real_data_runtime.warm.json",
            "untargeted-cid-nl-real-data-runtime:warm-test",
        ),
    )
    assert warm_result.succeeded is True, (
        warm_result.finalization.coordinator.proposed_outcome,
        warm_result.finalization.supervisor.terminal_outcome,
        warm_result.finalization.coordinator.state.runtime.settlements,
        warm_result.finalization.coordinator.state.resources,
        warm_result.finalization.coordinator.state.failures,
        warm_result.sample_results,
    )
    warm_by_stem = {
        item.job.sample.metadata.raw_stem: item for item in warm_result.sample_results
    }
    warm_s1 = warm_by_stem["S1"].adapter_result
    assert warm_s1.assessment_input.assessment.content == (s1_assessment.content)
    assert warm_s1.assessment_input.assessment.artifact.evidence_artifact_id == (
        s1_assessment.artifact.evidence_artifact_id
    )
    assert warm_s1.acquisition is not None
    assert by_stem["S1"].adapter_result.acquisition is not None
    assert tuple(map(serialize_acquired_trace, warm_s1.acquisition.traces)) == tuple(
        map(serialize_acquired_trace, by_stem["S1"].adapter_result.acquisition.traces)
    )
    assert warm_vendor_raw.chromatogram_request_counts == []
    assert warm_vendor_raw.disposed is False, (
        warm_open_calls,
        sorted(str(path) for path in (tmp_path / "runtime-cache").rglob("*.json")),
        by_stem["S1"].backfill_attempt,
        warm_by_stem["S1"].backfill_attempt,
    )
    warm_resources = warm_result.finalization.coordinator.state.resources
    assert any(
        item.open_outcome is RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED
        for item in warm_resources
    )
    assert all(
        item.open_outcome is not RuntimeResourceOpenOutcome.OPENED
        for item in warm_resources
        if item.reservation.resource_key.endswith(":S1")
    )
    _assert_partition_matches_runtime(jobs, result, monkeypatch)

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


def test_runtime_does_not_evaluate_ineligible_inferred_precursor(
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
    job = UntargetedPartitionJob(
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
        owner_completeness_attestation="untargeted-cid-nl-real-data-runtime-test",
    )

    vendor_raw = _install_runtime_raw(
        monkeypatch,
        source_raw=source_raw,
        raw_path=raw_path,
        tmp_path=tmp_path,
    )
    result = execute_attempt(
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
    assert (
        adapter.assessment_input.assessment.content.technical_state == "not_evaluated"
    )
    assert adapter.assessment_input.assessment.content.method_result is None
    assert adapter.assessment_input.assessment.content.reason_codes == (
        "no_search_eligible_discovery_input",
    )
    assert not (
        adapter.assessment_input.assessment.content.evaluation_receipt.search_completed
    )
    assert adapter.source_candidate_ids == (inferred.candidate_id,)
    assert adapter.assessment_input.assessment.candidate_peaks == ()
    assert adapter.strict_scan_count == 0
    assert adapter.acquisition is None
    assert vendor_raw.disposed is True
    assert result.sample_results[0].backfill_attempt is None


def test_runtime_recovers_from_single_scan_cid_nl_support_owners(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from xic_extractor.alignment.cell_observation_records import CellObservationRef

    validate_cell = CellObservationRef.__post_init__

    def reject_cell_materialization(_self):
        raise AssertionError("recovery planning must not materialize matrix cells")

    monkeypatch.setattr(
        CellObservationRef, "__post_init__", reject_cell_materialization
    )
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
    context = build_partition_context(
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
        UntargetedPartitionJob(
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
            "untargeted-cid-nl-real-data-runtime-recovery-test"
        ),
    )

    result = execute_attempt(
        runtime,
        jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "real_data_runtime.recovery.json",
            "untargeted-cid-nl-real-data-runtime-recovery:test",
        ),
    )

    assert result.succeeded is True
    monkeypatch.setattr(CellObservationRef, "__post_init__", validate_cell)
    by_stem = {
        item.job.sample.metadata.raw_stem: item for item in result.sample_results
    }
    assert by_stem["S1"].adapter_result.strict_scan_count == 1
    assert by_stem["S2"].adapter_result.strict_scan_count == 1
    for stem in ("Missing", "NoPeak", "Blank"):
        initial = by_stem[stem].adapter_result.assessment_input.assessment
        assert initial.content.technical_state == "not_evaluated"
        assert initial.content.method_result is None
        assert not initial.content.evaluation_receipt.search_completed
    assert by_stem["Missing"].backfill_attempt is not None
    assert by_stem["Missing"].backfill_input is not None
    assert by_stem["Missing"].result_ref.backfill_candidate_count == 1
    assert by_stem["NoPeak"].backfill_attempt is not None
    assert by_stem["NoPeak"].backfill_input is None
    assert by_stem["NoPeak"].result_ref.backfill_assessment_artifact_id is not None
    assert by_stem["NoPeak"].result_ref.backfill_candidate_count == 0
    assert by_stem["Blank"].backfill_attempt is None
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
        backfill_inputs=tuple(
            item.backfill_input
            for item in result.sample_results
            if item.backfill_input is not None
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
        UntargetedPartitionError,
        match="candidate universe does not match the processing request",
    ):
        UntargetedPartitionJob(
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
        context = build_partition_context(
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
            for job in build_sample_jobs(
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
                acquisition_source_expectation=context.acquisition_sources_by_sample[
                    sample.metadata.raw_stem
                ],
            )
        )
        assert all(
            job.acquisition_source_expectation
            is context.acquisition_sources_by_sample[job.sample.metadata.raw_stem]
            for job in jobs
        )
        with pytest.raises(
            UntargetedPartitionError,
            match="acquisition source expectation must match the request sample",
        ):
            replace(
                jobs[0],
                acquisition_source_expectation=context.acquisition_sources_by_sample[
                    "S2"
                ],
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
            selected = partition_models._request_subject_candidates(job)
            assert selected == expected
            assert all(left is right for left, right in zip(selected, expected))
            assert (
                partition_models._request_subject_candidates(replace(job)) == expected
            )
        assert any(
            not partition_models._request_subject_candidates(job) for job in jobs
        )
        assert any(
            len(partition_models._request_subject_candidates(job)) > 1 for job in jobs
        )
    with pytest.raises(
        UntargetedPartitionError, match="not an exact member"
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
    real_acquire = cid_nl_acquisition.acquire_untargeted_cid_nl_sample_trace_batch

    def record_acquire(**kwargs):
        acquired_candidates.append((kwargs["sample"], kwargs["candidates"]))
        return real_acquire(**kwargs)

    monkeypatch.setattr(
        cid_nl_acquisition,
        "acquire_untargeted_cid_nl_sample_trace_batch",
        record_acquire,
    )
    partition = runtime_module.execute_partition(
        selected_jobs,
        partition_key="subset",
        alignment_config=runtime_module.CID_NL_ALIGNMENT_CONFIG,
    )
    assert partition.succeeded, partition.worker_batch.settlements
    assert acquired_candidates
    for sample, acquired in acquired_candidates:
        job = next(job for job in selected_jobs if job.sample == sample)
        assert acquired == partition_models._request_subject_candidates(job)
    assert any(
        len(acquired) < len(job.candidates)
        for sample, acquired in acquired_candidates
        for job in selected_jobs
        if job.sample == sample
    )


def test_partition_runtime_job_requires_exact_local_feature_membership(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.untargeted_partition import models as models_owner

    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"partition-runtime")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        _FakeRaw((7.8, 8.2)),
        raw_file=raw_path,
        settings=settings,
        peak_config=peak_config,
    )
    assert len(candidates) == 2
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
    memberships = tuple(
        sorted(
            (
                candidate.candidate_id,
                subjects[index].method_subject_id,
            )
            for index, candidate in enumerate(candidates)
        )
    )
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=memberships,
    )
    receipts = _discovery_receipts(
        tmp_path,
        (raw_path,),
        {"S1": candidates},
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
        discovery_receipts_by_sample=receipts,
        sample_identity_namespace="study:partition-runtime-job",
    )
    old_request = context.processing_owners.request
    request = build_processing_request_identity(
        acquisition_sources=old_request.acquisition_sources,
        acquisition_bindings=old_request.acquisition_bindings,
        scientific_input_schema_version=old_request.scientific_input_schema_version,
        processing_algorithm_version=old_request.processing_algorithm_version,
        processing_contract_version=old_request.processing_contract_version,
        scientific_inputs=json.loads(old_request.normalized_scientific_inputs_json)
        | {
            ALIGNMENT_FEATURE_INPUT_KEY: sorted(
                candidate.candidate_id for candidate in candidates
            )
        },
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=context.processing_owners.sample_snapshot,
        sample_resolutions=context.samples,
    )
    partition_subject = subjects[0]
    partition_membership = next(
        membership
        for membership in memberships
        if membership[1] == partition_subject.method_subject_id
    )
    partition = mint_untargeted_cid_nl_method_subject_batch(
        subjects=(partition_subject,),
        candidate_memberships=(partition_membership,),
    )
    local_candidate = next(
        candidate
        for candidate in candidates
        if candidate.candidate_id == partition_membership[0]
    )

    models_owner._request_input_index.cache_clear()
    build_args = dict(
        processing_owners=owners,
        sample=context.sample("S1"),
        subject_work_units=((partition_subject, "partition:S1"),),
        subject_partition=partition,
        candidates=(local_candidate,),
        discovery_receipt=receipts["S1"],
        settings=settings,
        peak_config=peak_config,
        raw_path=raw_path,
        dll_dir=tmp_path,
        acquisition_source_expectation=context.acquisition_sources_by_sample["S1"],
    )
    (job,) = build_sample_jobs(**build_args)
    build_sample_jobs(**build_args)
    request_index_cache = models_owner._request_input_index.cache_info()
    assert request_index_cache.misses == 1
    assert request_index_cache.hits == 1
    assert partition_models._request_subject_candidates(job) == (local_candidate,)

    with pytest.raises(
        UntargetedPartitionError,
        match="exactly cover its selected feature IDs",
    ):
        replace(job, candidates=())

    foreign_membership = next(
        membership for membership in memberships if membership != partition_membership
    )
    foreign_partition = mint_untargeted_cid_nl_method_subject_batch(
        subjects=(partition_subject,),
        candidate_memberships=(
            (foreign_membership[0], partition_subject.method_subject_id),
        ),
    )
    with pytest.raises(
        UntargetedPartitionError,
        match="foreign candidate membership",
    ):
        replace(job, subject_partition=foreign_partition)


def test_direct_subject_batch_seals_initial_and_recovery_acquisitions_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_paths = tuple(tmp_path / f"S{index}.synthetic" for index in range(1, 5))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    source_raws = {path: _FakeRaw((8.0,)) for path in raw_paths}
    candidates_by_sample = {}
    for path in raw_paths:
        path.write_bytes(path.stem.encode())
        candidates_by_sample[path.stem] = _current_candidates(
            source_raws[path],
            raw_file=path,
            settings=settings,
            peak_config=peak_config,
        )
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
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=tuple(
            (
                candidate.candidate_id,
                subjects[index // 2].method_subject_id,
            )
            for index, path in enumerate(raw_paths)
            for candidate in candidates_by_sample[path.stem]
        ),
    )
    context = build_partition_context(
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
        discovery_receipts_by_sample=_discovery_receipts(
            tmp_path, raw_paths, candidates_by_sample
        ),
        sample_identity_namespace="study:runtime-phase-acquisition-seal",
    )
    jobs = tuple(
        job
        for sample in context.samples
        for job in build_sample_jobs(
            processing_owners=context.processing_owners,
            sample=sample,
            subject_work_units=tuple(
                (
                    subject,
                    f"{sample.metadata.raw_stem}:{subject.method_subject_id}",
                )
                for subject in subjects
            ),
            candidates=context.candidates_by_sample[sample.metadata.raw_stem],
            discovery_receipt=context.discovery_receipts_by_sample[
                sample.metadata.raw_stem
            ],
            settings=settings,
            peak_config=peak_config,
            raw_path=context.raw_paths_by_sample[sample.metadata.raw_stem],
            dll_dir=tmp_path,
            acquisition_source_expectation=context.acquisition_sources_by_sample[
                sample.metadata.raw_stem
            ],
        )
    )
    _install_runtime_raws(
        monkeypatch,
        sources_by_path=source_raws,
        tmp_path=tmp_path,
    )
    observed_phase_acquisitions = []
    for name in (
        "acquire_untargeted_cid_nl_sample_trace_batch",
        "acquire_untargeted_cid_nl_backfill_trace_batch",
    ):
        original = getattr(cid_nl_acquisition, name)

        def observe(*args, _original=original, **kwargs):
            acquired = _original(*args, **kwargs)
            if acquired is not None:
                observed_phase_acquisitions.append(acquired.acquisition)
            return acquired

        monkeypatch.setattr(cid_nl_acquisition, name, observe)

    submitted_batches = []
    owner_execute_attempt = runtime_module.execute_runtime_attempt

    def capture_worker_batch(
        runtime,
        reservations,
        *,
        worker_batch_provider,
        telemetry_provider,
        persister,
    ):
        def provide_and_capture(aggregate):
            batch = worker_batch_provider(aggregate)
            submitted_batches.append(batch)
            return batch

        return owner_execute_attempt(
            runtime,
            reservations,
            worker_batch_provider=provide_and_capture,
            telemetry_provider=telemetry_provider,
            persister=persister,
        )

    monkeypatch.setattr(runtime_module, "execute_runtime_attempt", capture_worker_batch)
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation="runtime-phase-acquisition-seal",
    )
    result = execute_attempt(
        runtime,
        jobs,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "runtime.phase-acquisitions.json",
            "runtime-phase-acquisition-seal:test",
        ),
    )

    assert result.succeeded
    assert len(submitted_batches) == 1
    receipts = submitted_batches[0].acquisitions
    assert len(observed_phase_acquisitions) == 8
    assert len(receipts) == len(observed_phase_acquisitions)
    assert all(
        receipt.work_unit_key in runtime.manifest.work_unit_keys for receipt in receipts
    )
    assert all(
        sum(receipt.acquisition is acquisition for receipt in receipts) == 1
        for acquisition in observed_phase_acquisitions
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
        build_partition_context(
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
    context = build_partition_context(
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
    installed_open_raw = partition_workers.open_raw

    def _reject_unused_open(path: Path, *args: object, **kwargs: object):
        if path == raw_paths[2]:
            raise AssertionError("owner-false recovery resource must not open RAW")
        return installed_open_raw(path, *args, **kwargs)

    monkeypatch.setattr(partition_workers, "open_raw", _reject_unused_open)
    jobs = tuple(
        UntargetedPartitionJob(
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
        owner_completeness_attestation=("real-data-runtime-conditional-resource-test"),
    )

    result = execute_attempt(
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
    ] == ["SUPPORT", "REJECT", None]
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
        runtime_call_cost(result.finalization)["raw_open_count"] == 2
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
    monkeypatch.setattr(runtime_module, "_ensure_assemblies_loaded", lambda *_: None)
    reset_reader_state()
    for filename in EXPECTED_THERMO_DLLS:
        (tmp_path / filename).write_bytes(b"dll")
    vendor_raw = _FakeThermoRaw(source_raw)

    def _open_runtime_raw(
        path, dll_dir, *, expected_acquisition_source, _expected_path_identity=None
    ):
        assert path == raw_path
        return open_raw(
            path,
            dll_dir,
            expected_acquisition_source=expected_acquisition_source,
            _expected_path_identity=_expected_path_identity,
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

    monkeypatch.setattr(partition_workers, "open_raw", _open_runtime_raw)
    return vendor_raw


def _install_runtime_raws(
    monkeypatch,
    *,
    sources_by_path: dict[Path, _FakeRaw],
    tmp_path: Path,
) -> dict[Path, list[_FakeThermoRaw]]:
    # Callers importing this helper do not inherit this module's autouse fixture.
    monkeypatch.setattr(runtime_module, "_ensure_assemblies_loaded", lambda *_: None)
    reset_reader_state()
    for filename in EXPECTED_THERMO_DLLS:
        (tmp_path / filename).write_bytes(b"dll")
    opened: dict[Path, list[_FakeThermoRaw]] = {}

    def _open_runtime_raw(
        path, dll_dir, *, expected_acquisition_source, _expected_path_identity=None
    ):
        vendor_raw = _FakeThermoRaw(sources_by_path[path])
        opened.setdefault(path, []).append(vendor_raw)
        return open_raw(
            path,
            dll_dir,
            expected_acquisition_source=expected_acquisition_source,
            _expected_path_identity=_expected_path_identity,
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

    monkeypatch.setattr(partition_workers, "open_raw", _open_runtime_raw)
    return opened


def _assert_partition_matches_runtime(jobs, full_result, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("partition must not execute a runtime attempt")

    monkeypatch.setattr(runtime_module, "execute_runtime_attempt", forbidden)
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
            actual.result_ref.source_candidate_ids
            == expected.result_ref.source_candidate_ids
        )
        assert (
            actual.result_ref.backfill_query_bounds
            == expected.result_ref.backfill_query_bounds
        )
    expected_keys = {
        f"part-1:{job.sample.identity.sample_identity_id}:{phase}"
        for job in jobs
        for phase in ("initial", "backfill")
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
    for stage in ("partition_initial", "partition_backfill", "partition_seal"):
        progress = [event for event in events if event.get("stage") == stage]
        assert progress[-1]["completed"] == progress[-1]["total"]
    # An acquisition failure must settle its jobs and return all resource cleanup facts.
    monkeypatch.setattr(partition_workers, "open_raw", forbidden)
    from xic_extractor.extraction.untargeted_partition import workers as worker_module

    monkeypatch.setattr(worker_module, "open_raw", forbidden)
    failed_jobs = tuple(replace(job, acquisition_cache_dir=None) for job in jobs)
    failed = runtime_module.execute_partition(
        failed_jobs,
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
