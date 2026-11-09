from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _FakeThermoRaw,
    _open_bound_raw,
    _peak_config,
    _settings,
    _source_for,
)
from tests.cid_nl_runtime_fixtures import (
    build_discovery_receipts,
    install_runtime_raw,
    install_runtime_raws,
)
from tests.processing_identity_no_raw_fixture import (
    build_targeted_processing_identity_raw_fixture,
)
from tests.untargeted_cid_nl_real_data_adapter_fixtures import _without_ms1_peak
from xic_extractor.acquisition_contract import (
    TraceCoordinateKind,
    acquire_trace_batch,
    mint_trace_acquisition_capability,
    serialize_acquired_trace,
)
from xic_extractor.alignment.cid_nl_inputs import CID_NL_ALIGNMENT_CONFIG
from xic_extractor.discovery.batch_contract import (
    ALIGNMENT_FEATURE_INPUT_KEY,
)
from xic_extractor.peak_detection import capture as capture_owner
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.processing_identity import (
    IdentityContractError,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.raw_reader_errors import RawReaderError
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.acquisition_cache import (
    CachedUntargetedRaw,
    acquire_untargeted_trace_batch,
    bind_untargeted_acquisition_cache,
)
from xic_extractor.untargeted_execution.partition import cid_nl_sample_acquisition
from xic_extractor.untargeted_execution.partition import execution as runtime_module
from xic_extractor.untargeted_execution.partition import models as partition_models
from xic_extractor.untargeted_execution.partition import workers as partition_workers
from xic_extractor.untargeted_execution.partition.execution import execute_partition
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedPartitionError,
    UntargetedPartitionJob,
    build_sample_jobs,
)
from xic_extractor.untargeted_execution.partition.request import (
    build_partition_context,
)
from xic_extractor.xic_models import XICRequest


@pytest.fixture(autouse=True)
def _disable_real_dll_loading(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "ensure_assemblies_loaded", lambda *_: None)


@pytest.mark.parametrize("winner", [b"same facts", b"different facts", None])
def test_acquisition_cache_windows_publish_race(tmp_path, monkeypatch, winner):
    from xic_extractor.untargeted_execution import acquisition_cache as cache_owner

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
    from xic_extractor.untargeted_execution import acquisition_cache as cache_owner

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
    from xic_extractor.untargeted_execution import acquisition_cache as cache_owner

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
    from xic_extractor.untargeted_execution import acquisition_cache as cache_module

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
        assert replay.adapter_chromatogram_call_count == 0
        assert tuple(map(serialize_acquired_trace, replay.traces)) == expected


def test_lazy_acquisition_cache_replay_skips_opener_and_opens_once_on_miss(
    tmp_path: Path,
) -> None:
    from xic_extractor.untargeted_execution import acquisition_cache as cache_owner

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
        cold_vendor.RunHeaderEx = SimpleNamespace(StartTime=7.4, EndTime=75.0)
        cold_bounds = cold_cached.acquisition_rt_bounds()
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
        assert lazy_warm.acquisition_rt_bounds() == cold_bounds == (7.4, 75.0)
        warm_batch = acquire_untargeted_trace_batch(
            capability, (first_request,), lazy_warm
        )
        warm_events = tuple(lazy_warm.iter_ms2_scans(7.8, 8.2))
    assert opener_calls == []
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


def test_runtime_delivers_one_real_subject_cohort_with_cache_replay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    physical_records = {}
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
        physical_records=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            {"S1": candidates, "S2": ()},
        ),
        sample_identity_namespace="study:real-data-runtime-test",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.peak_id: physical_records[c.peak_id]
            for values in ({"S1": candidates, "S2": ()}).values()
            for c in values
        },
    )
    vendor_raw = install_runtime_raw(
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
            signal_processing_profile=context.signal_processing_profile,
            physical_records=tuple(
                context.physical_records_by_candidate[c.peak_id]
                for c in (context.candidates_by_sample[sample.metadata.raw_stem])
            ),
        )
        for sample in context.samples
    )
    from xic_extractor.untargeted_execution.partition.workers import _open_job_raw

    changed_path = tmp_path / "Support.changed.synthetic"
    changed_path.write_bytes(b"changed-after-source-verification")
    with pytest.raises(
        RawReaderError,
        match="RAW path state does not match expected acquisition source",
    ):
        _open_job_raw(replace(jobs[0], raw_path=changed_path))

    result = execute_partition(
        jobs,
        partition_key="runtime-fixture",
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
    )

    assert result.succeeded is True, result.failure
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
    # One integration trace plus its separate elution-context trace.
    assert vendor_raw.chromatogram_request_counts == [2]
    assert vendor_raw.disposed is True

    warm_vendor_raw = install_runtime_raw(
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
    warm_result = execute_partition(
        jobs,
        partition_key="runtime-fixture",
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
    )
    assert warm_result.succeeded is True, warm_result.failure
    warm_by_stem = {
        item.job.sample.metadata.raw_stem: item for item in warm_result.sample_results
    }
    warm_s1 = warm_by_stem["S1"].adapter_result
    assert warm_s1.assessment_input.assessment.content == (s1_assessment.content)
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
    assert warm_open_calls == []


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
    physical_records = {}
    from xic_extractor.alignment.cell_observation.records import CellObservationRef

    construct_cell = CellObservationRef.__init__

    def reject_cell_materialization(_self, *args, **kwargs):
        raise AssertionError("recovery planning must not materialize matrix cells")

    monkeypatch.setattr(CellObservationRef, "__init__", reject_cell_materialization)
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
            physical_records=physical_records,
        ),
        "S2": _current_candidates(
            source_raws[raw_paths[1]],
            raw_file=raw_paths[1],
            settings=settings,
            physical_records=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        sample_identity_namespace="study:real-data-runtime-recovery-test",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.peak_id: physical_records[c.peak_id]
            for values in (candidates_by_sample).values()
            for c in values
        },
    )
    opened = install_runtime_raws(
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
            signal_processing_profile=context.signal_processing_profile,
            physical_records=tuple(
                context.physical_records_by_candidate[c.peak_id]
                for c in (context.candidates_by_sample[sample.metadata.raw_stem])
            ),
        )
        for sample in context.samples
    )

    cancelled = threading.Event()
    if backfill_stop is not None:
        acquire = _FakeThermoRaw.GetChromatogramData

        def stop_backfill(raw, *args, **kwargs):
            if raw.source is source_raws[raw_paths[2]]:
                if cancel_accepted:
                    cancelled.set()
                raise backfill_stop("backfill interrupted")
            return acquire(raw, *args, **kwargs)

        monkeypatch.setattr(_FakeThermoRaw, "GetChromatogramData", stop_backfill)

    result = execute_partition(
        jobs,
        partition_key="runtime-fixture",
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
        should_cancel=cancelled.is_set,
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
    monkeypatch.setattr(CellObservationRef, "__init__", construct_cell)
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
    assert len(by_stem["Missing"].backfill_attempt.assessment.candidate_peaks) == 1
    assert by_stem["NoPeak"].backfill_attempt is not None
    assert by_stem["NoPeak"].backfill_input is None
    assert len(by_stem["NoPeak"].backfill_attempt.assessment.candidate_peaks) == 0
    assert by_stem["Blank"].backfill_attempt is None
    assert raw_paths[4] not in opened
    assert {
        path: [vendor.chromatogram_request_counts for vendor in vendors]
        for path, vendors in opened.items()
    } == {
        # Initial sample batches retain integration and elution-context traces.
        raw_paths[0]: [[2]],
        raw_paths[1]: [[2]],
        raw_paths[2]: [[2]],
        raw_paths[3]: [[2]],
    }
    assert all(vendor.disposed for vendors in opened.values() for vendor in vendors)


def test_batch_candidate_selection_preserves_order_and_request_sample_membership(
    tmp_path: Path,
    monkeypatch,
) -> None:
    physical_records = {}
    raw_paths = (tmp_path / "S1.synthetic", tmp_path / "S2.synthetic")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates_by_sample = {}
    sources_by_path = {}
    for path in raw_paths:
        path.write_bytes(path.stem.encode())
        candidates = []
        scans = []
        for offset in range(3):
            source = _FakeRaw((8.0,))
            scan = source.scans[0]
            number = scan.scan_number + offset
            # Distinct ions: one measured peak per channel, not one peak thrice.
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
                    physical_records=physical_records,
                )
            )
        source.scans = tuple(scans)
        sources_by_path[path] = source
        candidates_by_sample[path.stem] = tuple(candidates)
    receipts = build_discovery_receipts(tmp_path, raw_paths, candidates_by_sample)
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
            signal_processing_profile=TEST_PROFILE,
            physical_records_by_candidate={
                c.peak_id: physical_records[c.peak_id]
                for values in (candidates_by_sample).values()
                for c in values
            },
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
                signal_processing_profile=context.signal_processing_profile,
                physical_records=tuple(
                    context.physical_records_by_candidate[c.peak_id]
                    for c in (
                        tuple(
                            reversed(
                                context.candidates_by_sample[sample.metadata.raw_stem]
                            )
                        )
                    )
                ),
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
    with pytest.raises(UntargetedPartitionError, match="not an exact member"):
        replace(
            requests_and_jobs[0][1][0],
            method_subject=mint_untargeted_cid_nl_method_subject(
                precursor_mz=700.0,
                precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
                neutral_loss_da=116.0474,
            ),
        )
    install_runtime_raws(
        monkeypatch,
        sources_by_path=sources_by_path,
        tmp_path=tmp_path,
    )
    selected_jobs = tuple(
        replace(job, candidates=tuple(reversed(job.candidates)))
        for job in jobs
        if job.method_subject == subjects[1] and job.sample.metadata.raw_stem == "S1"
    )
    acquired_candidates = []
    real_acquire = (
        cid_nl_sample_acquisition.acquire_untargeted_cid_nl_sample_trace_batch
    )

    def record_acquire(**kwargs):
        acquired_candidates.append((kwargs["sample"], kwargs["candidates"]))
        return real_acquire(**kwargs)

    monkeypatch.setattr(
        cid_nl_sample_acquisition,
        "acquire_untargeted_cid_nl_sample_trace_batch",
        record_acquire,
    )
    partition = runtime_module.execute_partition(
        selected_jobs,
        partition_key="subset",
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
    )
    assert partition.succeeded, partition.failure
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
    physical_records = {}
    from xic_extractor.untargeted_execution.partition import models as models_owner

    raw_path = tmp_path / "S1.synthetic"
    raw_path.write_bytes(b"partition-runtime")
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        _FakeRaw((7.8, 8.2)),
        raw_file=raw_path,
        settings=settings,
        physical_records=physical_records,
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
                candidate.peak_id,
                subjects[index].method_subject_id,
            )
            for index, candidate in enumerate(candidates)
        )
    )
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=memberships,
    )
    receipts = build_discovery_receipts(
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
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.peak_id: physical_records[c.peak_id]
            for values in ({"S1": candidates}).values()
            for c in values
        },
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
                candidate.peak_id for candidate in candidates
            )
        },
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(),
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
        if candidate.peak_id == partition_membership[0]
    )

    models_owner._request_input_index.cache_clear()
    build_args = dict(
        processing_owners=owners,
        signal_processing_profile=TEST_PROFILE,
        physical_records=(physical_records[local_candidate.peak_id],),
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
        replace(job, candidates=(), physical_records=())

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


def test_context_rejects_same_stem_receipt_from_different_acquisition(
    tmp_path: Path,
) -> None:
    physical_records = {}
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
        physical_records=physical_records,
    )
    receipt = build_discovery_receipts(
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
            signal_processing_profile=TEST_PROFILE,
            physical_records_by_candidate={
                c.peak_id: physical_records[c.peak_id]
                for values in ({"S1": candidates}).values()
                for c in values
            },
        )


@pytest.mark.parametrize("excluded", [False, True])
def test_runtime_does_not_open_recovery_resource_when_owner_condition_is_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    excluded: bool,
) -> None:
    physical_records = {}
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
        physical_records=physical_records,
    )
    reject = tuple(
        _without_ms1_peak(candidate)
        for candidate in _current_candidates(
            source_raws[raw_paths[1]],
            raw_file=raw_paths[1],
            settings=settings,
            physical_records=physical_records,
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
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path,
            raw_paths,
            candidates_by_sample,
        ),
        sample_identity_namespace="study:real-data-runtime-conditional-resource-test",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate={
            c.peak_id: physical_records[c.peak_id]
            for values in (candidates_by_sample).values()
            for c in values
        },
    )
    opened = install_runtime_raws(
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
            signal_processing_profile=context.signal_processing_profile,
            physical_records=tuple(
                context.physical_records_by_candidate[c.peak_id]
                for c in (context.candidates_by_sample[sample.metadata.raw_stem])
            ),
        )
        for sample in context.samples
    )

    result = execute_partition(
        jobs,
        partition_key="runtime-fixture",
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
    )

    assert result.succeeded is True
    assert [
        item.adapter_result.assessment_input.assessment.content.method_result
        for item in result.sample_results
    ] == ["SUPPORT", "REJECT", None]
    assert raw_paths[2] not in opened
    assert sum(len(vendors) for vendors in opened.values()) == 2


def test_ms2_cache_preserves_precursor_metadata_and_reacquires_old_schema(tmp_path):
    from types import SimpleNamespace

    from xic_extractor.untargeted_execution import acquisition_cache as owner

    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path, sample_stems=("S1",), peak_config=_peak_config(tmp_path)
    )
    source = _source_for(fixture, "S1")
    cache_dir = tmp_path / "metadata-cache"
    reads = []

    def opener():
        reads.append(True)
        handle, vendor = _open_bound_raw(
            fixture, "S1", _FakeRaw((8.0,)), tmp_path=tmp_path
        )
        vendor.trailer_labels = [
            "Monoisotopic M/Z:",
            "Charge State:",
            "Master Scan Number:",
        ]
        vendor.GetTrailerExtraInformation = lambda _: SimpleNamespace(
            Labels=["Monoisotopic M/Z:", "Charge State:", "Master Scan Number:"],
            Values=["499.0", "1", "12"],
        )
        return handle

    def acquire():
        with CachedUntargetedRaw(opener, source=source, cache_dir=cache_dir) as cached:
            return tuple(cached.iter_ms2_scans(7.8, 8.2))

    cold = acquire()
    warm = acquire()
    assert len(reads) == 1
    assert cold[0].scan.precursor_metadata == warm[0].scan.precursor_metadata
    assert warm[0].scan.precursor_metadata.monoisotopic_mz == 499.0
    assert warm[0].scan.precursor_metadata.master_scan_number == 12
    cache = owner._FactCache(cache_dir, source.acquisition_source_id)
    key = owner._key_bytes("ms2_window", owner._float_text(7.8), owner._float_text(8.2))
    payload = json.loads(cache.read("ms2", key))
    payload["schema_version"] = "untargeted_raw_fact_cache"
    for event in payload["events"]:
        event["scan"].pop("precursor_metadata")
    cache._path("ms2", key).write_bytes(
        owner._canonical_json(cache._envelope(key, owner._canonical_json(payload)))
    )
    repaired = acquire()
    assert len(reads) == 2
    assert repaired[0].scan.precursor_metadata == cold[0].scan.precursor_metadata
    acquire()
    assert len(reads) == 2


def test_acquisition_cache_rejects_mismatched_handle_mass_policy(tmp_path):
    from xic_extractor.configuration.mass_recalibration import MassRecalibration
    from xic_extractor.untargeted_execution.acquisition_cache import (
        UntargetedAcquisitionCacheError,
    )

    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path, sample_stems=("S1",), peak_config=_peak_config(tmp_path)
    )
    handle, vendor = _open_bound_raw(fixture, "S1", _FakeRaw((8.0,)), tmp_path=tmp_path)
    try:
        with pytest.raises(UntargetedAcquisitionCacheError, match="mass recalibration"):
            CachedUntargetedRaw(
                handle,
                source=_source_for(fixture, "S1"),
                cache_dir=tmp_path / "cache",
                mass_recalibration=MassRecalibration.EASY_IC_PROBE,
            )
    finally:
        handle.__exit__()
