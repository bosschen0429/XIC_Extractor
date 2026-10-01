import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from xic_extractor.execution.process_memory import (
    ProcessMemoryPeak,
    ProcessMemorySample,
    ProcessTreeMemorySampler,
)
from xic_extractor.execution.timing import TimingRecorder


def test_timing_recorder_records_stage_metrics_and_writes_json(tmp_path: Path) -> None:
    recorder = TimingRecorder("discovery", run_id="run-1", timer=_Timer([10.0, 12.5]))

    with recorder.stage(
        "discover.ms2_seeds",
        sample_stem="Sample_A",
        metrics={"seed_count": 0},
    ) as stage:
        stage.metrics["seed_count"] = 3
        stage.metrics["raw_file"] = Path("Sample_A.raw")

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.stage == "discover.ms2_seeds"
    assert record.sample_stem == "Sample_A"
    assert record.elapsed_sec == pytest.approx(2.5)
    assert record.metrics == {
        "seed_count": 3,
        "raw_file": "Sample_A.raw",
    }

    output_path = tmp_path / "diagnostics" / "timing.json"
    recorder.write_json(output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-1"
    assert payload["pipeline"] == "discovery"
    assert payload["records"] == [
        {
            "sample_stem": "Sample_A",
            "stage": "discover.ms2_seeds",
            "elapsed_sec": pytest.approx(2.5),
            "metrics": {
                "seed_count": 3,
                "raw_file": "Sample_A.raw",
            },
        }
    ]
    assert payload["summaries"]["stage_summary"] == [
        {
            "stage": "discover.ms2_seeds",
            "record_count": 1,
            "sample_count": 1,
            "total_elapsed_sec": pytest.approx(2.5),
            "max_elapsed_sec": pytest.approx(2.5),
        }
    ]
    assert payload["summaries"]["raw_xic_locality_summary"] == []


def test_timing_recorder_summarizes_raw_xic_locality() -> None:
    recorder = TimingRecorder("alignment", run_id="run-locality")

    recorder.record(
        "alignment.build_owners.extract_xic",
        elapsed_sec=2.0,
        sample_stem="S1",
        metrics={
            "extract_xic_count": 10,
            "extract_xic_batch_count": 5,
            "raw_chromatogram_call_count": 9,
            "point_count": 100,
        },
    )
    recorder.record(
        "alignment.owner_backfill.extract_xic",
        elapsed_sec=1.0,
        sample_stem="S1",
        metrics={
            "extract_xic_count": 100,
            "extract_xic_batch_count": 2,
            "raw_chromatogram_call_count": 1,
            "point_count": 2000,
        },
    )

    locality = {
        row["stage"]: row
        for row in recorder.to_json_dict()["summaries"]["raw_xic_locality_summary"]
    }
    assert locality["alignment.build_owners.extract_xic"][
        "raw_calls_per_xic"
    ] == pytest.approx(0.9)
    assert locality["alignment.owner_backfill.extract_xic"][
        "raw_calls_per_xic"
    ] == pytest.approx(0.01)
    assert locality["alignment.owner_backfill.extract_xic"][
        "mean_xic_per_raw_chromatogram_call"
    ] == pytest.approx(100.0)


def test_timing_recorder_records_exception_stage_before_reraising() -> None:
    recorder = TimingRecorder("alignment", run_id="run-2", timer=_Timer([1.0, 1.25]))

    with pytest.raises(ValueError, match="boom"):
        with recorder.stage("alignment.claim_registry"):
            raise ValueError("boom")

    assert len(recorder.records) == 1
    assert recorder.records[0].stage == "alignment.claim_registry"
    assert recorder.records[0].elapsed_sec == pytest.approx(0.25)


def test_timing_recorder_live_output_updates_on_each_record(tmp_path: Path) -> None:
    live_path = tmp_path / "live" / "timing.json"
    recorder = TimingRecorder(
        "alignment",
        run_id="run-live",
        live_output_path=live_path,
        timer=_Timer([1.0, 1.5, 2.0, 2.25]),
    )

    recorder.record("alignment.run_config", elapsed_sec=0.0)
    payload = json.loads(live_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "timing.live"
    assert [record["stage"] for record in payload["recent_records"]] == [
        "alignment.run_config"
    ]

    with recorder.stage("alignment.read_batch_index"):
        pass

    payload = json.loads(live_path.read_text(encoding="utf-8"))
    assert [record["stage"] for record in payload["recent_records"]] == [
        "alignment.run_config",
        "alignment.read_batch_index",
    ]
    assert payload["recent_records"][1]["elapsed_sec"] == pytest.approx(0.5)


def test_failed_partition_timeline_survives_without_final_report(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "timing.live.json"
    recorder = TimingRecorder("alignment", live_output_path=live_path)
    timeline = [
        {
            "partition_key": "partition-00000",
            "acquire_start": 10.0,
            "acquire_end": 12.0,
            "compose_start": 12.0,
            "compose_end": 15.0,
            "status": "failed",
        }
    ]

    with pytest.raises(InterruptedError, match="cancelled"):
        recorder.heartbeat_event({"event": "partition_timeline", "timeline": timeline})
        raise InterruptedError("cancelled before final timing publication")

    payload = json.loads(live_path.read_text(encoding="utf-8"))
    assert payload["partition_timeline"] == timeline
    assert payload["partition_timeline"] == recorder.to_json_dict()[
        "partition_timeline"
    ]


def test_live_output_replace_failure_preserves_last_good_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_path = tmp_path / "timing.live.json"
    live_path.write_text('{"last_good": true}', encoding="utf-8")
    original_replace = Path.replace

    replace_attempts = 0

    def _fail_live_replace(path: Path, target: Path) -> Path:
        nonlocal replace_attempts
        if path == live_path.with_name(f"{live_path.name}.tmp"):
            replace_attempts += 1
            raise PermissionError("replace blocked")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", _fail_live_replace)
    monkeypatch.setattr("xic_extractor.execution.timing.sleep", lambda _: None)
    recorder = TimingRecorder("alignment", live_output_path=live_path)

    with pytest.raises(PermissionError, match="replace blocked"):
        recorder.record("alignment.run_config", elapsed_sec=0.0)

    assert json.loads(live_path.read_text(encoding="utf-8")) == {"last_good": True}
    assert replace_attempts == 5


def test_live_output_replace_retries_transient_windows_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_path = tmp_path / "timing.live.json"
    original_replace = Path.replace
    replace_attempts = 0
    delays: list[float] = []

    def _transient_live_replace(path: Path, target: Path) -> Path:
        nonlocal replace_attempts
        if path == live_path.with_name(f"{live_path.name}.tmp"):
            replace_attempts += 1
            if replace_attempts < 3:
                raise PermissionError("replace temporarily blocked")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", _transient_live_replace)
    monkeypatch.setattr(
        "xic_extractor.execution.timing.sleep",
        delays.append,
    )
    recorder = TimingRecorder("alignment", live_output_path=live_path)

    recorder.record("alignment.run_config", elapsed_sec=0.0)

    payload = json.loads(live_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "timing.live"
    assert replace_attempts == 3
    assert delays == [0.05, 0.1]


def test_timing_recorder_disabled_mode_is_a_complete_noop(tmp_path: Path) -> None:
    live_path = tmp_path / "timing.live.json"
    recorder = TimingRecorder(
        "discovery",
        enabled=False,
        live_output_path=live_path,
        timer=_Timer([]),
    )

    with recorder.stage("discover.group_seeds") as stage:
        stage.metrics["group_count"] = 7
    recorder.record("discover.record", elapsed_sec=1.0)
    recorder.heartbeat(stage="discover.sample", completed=1, total=1)
    recorder.heartbeat_event({"event": "discover.sample", "completed": 1})
    recorder.observe("raw_open_count", value=1)
    recorder.set_measurement_contract(cache_state="warm")

    assert recorder.records == ()
    assert recorder.measurement_observations == ()
    assert recorder.to_json_dict()["measurement_contract"] == {}
    assert not live_path.exists()


def test_timing_recorder_nested_stages_keep_completion_order() -> None:
    recorder = TimingRecorder(
        "alignment",
        run_id="run-3",
        timer=_Timer([1.0, 2.0, 3.0, 5.0]),
    )

    with recorder.stage("alignment.outer"):
        with recorder.stage("alignment.inner"):
            pass

    assert [record.stage for record in recorder.records] == [
        "alignment.inner",
        "alignment.outer",
    ]
    assert [record.elapsed_sec for record in recorder.records] == [
        pytest.approx(1.0),
        pytest.approx(4.0),
    ]


def test_timing_recorder_separates_nested_and_exclusive_wall() -> None:
    recorder = TimingRecorder(
        "alignment",
        run_id="run-exclusive",
        timer=_Timer([0.0, 1.0, 2.0, 4.0]),
    )

    with recorder.stage("outer"):
        with recorder.stage("inner"):
            pass

    summaries = recorder.to_json_dict()["summaries"]
    assert summaries["nested_stage_summary"] == [
        {
            "stage": "inner",
            "record_count": 1,
            "total_elapsed_sec": pytest.approx(1.0),
            "max_elapsed_sec": pytest.approx(1.0),
        }
    ]
    assert summaries["non_overlapping_stage_summary"] == [
        {
            "stage": "outer",
            "record_count": 1,
            "total_exclusive_elapsed_sec": pytest.approx(3.0),
            "max_exclusive_elapsed_sec": pytest.approx(3.0),
        },
        {
            "stage": "inner",
            "record_count": 1,
            "total_exclusive_elapsed_sec": pytest.approx(1.0),
            "max_exclusive_elapsed_sec": pytest.approx(1.0),
        },
    ]


def test_timing_recorder_caller_elapsed_is_not_exclusive_wall() -> None:
    recorder = TimingRecorder("worker")
    recorder.record("worker.local_elapsed", elapsed_sec=12.0)

    summaries = recorder.to_json_dict()["summaries"]
    assert summaries["nested_stage_summary"] == []
    assert summaries["non_overlapping_stage_summary"] == []
    semantics = recorder.to_json_dict()["span_semantics"]
    assert semantics == [
        {"span_kind": "caller", "aggregation": "sum", "overlap": "unknown"}
    ]


def test_timing_stage_freezes_elapsed_before_live_serialization(tmp_path: Path) -> None:
    recorder = TimingRecorder(
        "writer",
        live_output_path=tmp_path / "timing.live.json",
        timer=_Timer([10.0, 11.0]),
    )

    with recorder.stage("writer.render") as stage:
        stage.metrics["bytes"] = 5

    assert stage.elapsed_sec == pytest.approx(1.0)
    assert recorder.records[0].elapsed_sec == pytest.approx(stage.elapsed_sec)


def test_timing_recorder_live_history_is_bounded_and_heartbeat_is_latest(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "timing.live.json"
    recorder = TimingRecorder(
        "discovery",
        live_output_path=live_path,
        live_history_limit=2,
    )
    for index in range(4):
        recorder.record(f"stage-{index}", elapsed_sec=1.0)
    recorder.heartbeat(stage="sample", sample_stem="S4", completed=4, total=4)

    payload = json.loads(live_path.read_text(encoding="utf-8"))
    assert [row["stage"] for row in payload["recent_records"]] == [
        "stage-2",
        "stage-3",
    ]
    assert payload["record_count"] == 4
    assert payload["dropped_record_count"] == 2
    assert payload["heartbeat"] == {
        "stage": "sample",
        "sample_stem": "S4",
        "completed": 4,
        "total": 4,
    }


@pytest.mark.parametrize("fail_stage", [False, True])
def test_live_heartbeat_coalesces_bursts_and_flushes_at_stage_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_stage: bool
) -> None:
    now = 0.0
    monkeypatch.setattr("xic_extractor.execution.timing.perf_counter", lambda: now)
    live_path = tmp_path / "live.json"
    recorder = TimingRecorder(
        "alignment", live_output_path=live_path, timer=_Timer([10.0, 11.0])
    )

    def publish(index: int) -> None:
        recorder.heartbeat_event(
            {"event": "sample_terminal", "work_unit_key": str(index), "succeeded": True}
        )

    def run_stage() -> None:
        nonlocal now
        with recorder.stage("runtime"):
            publish(1)
            first = live_path.read_bytes()
            now = 0.5
            publish(2)
            assert live_path.read_bytes() == first
            now = 1.0
            publish(3)
            assert (
                json.loads(live_path.read_text())["heartbeat"]["work_unit_key"] == "3"
            )
            now = 1.1
            publish(4)
            assert (
                json.loads(live_path.read_text())["heartbeat"]["work_unit_key"] == "3"
            )
            if fail_stage:
                raise ValueError("stage failed")

    if fail_stage:
        with pytest.raises(ValueError, match="stage failed"):
            run_stage()
    else:
        run_stage()
    payload = json.loads(live_path.read_text())
    assert payload["heartbeat"]["work_unit_key"] == "4"
    assert [row["stage"] for row in payload["recent_records"]] == ["runtime"]
    assert len(recorder.to_json_dict()["records"]) == 1
    assert recorder.records[0].elapsed_sec == 1.0


def test_live_heartbeat_publishes_transitions_failures_completion_and_unscoped_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("xic_extractor.execution.timing.perf_counter", lambda: 0.0)
    live_path = tmp_path / "live.json"
    recorder = TimingRecorder("alignment", live_output_path=live_path)
    with recorder.stage("runtime"):
        recorder.heartbeat(stage="initial", completed=1, total=3)
        recorder.heartbeat(stage="recovery", completed=1, total=3)
        assert json.loads(live_path.read_text())["heartbeat"]["stage"] == "recovery"
        recorder.heartbeat(stage="recovery", completed=2, total=3, succeeded=False)
        assert json.loads(live_path.read_text())["heartbeat"]["succeeded"] is False
        recorder.heartbeat(stage="recovery", completed=3, total=3)
        assert json.loads(live_path.read_text())["heartbeat"]["completed"] == 3
    with recorder.stage("next_runtime"):
        recorder.heartbeat(stage="recovery", completed=1, total=3)
        assert json.loads(live_path.read_text())["heartbeat"]["completed"] == 1
    recorder.heartbeat(stage="unscoped", completed=1)
    recorder.heartbeat(stage="unscoped", completed=2)
    assert json.loads(live_path.read_text())["heartbeat"]["completed"] == 2


def test_timing_recorder_measurement_contract_and_unavailable_observation() -> None:
    recorder = TimingRecorder("workflow")
    recorder.set_measurement_contract(
        cache_state="unavailable",
        repetition_noise_design={"minimum_repetitions": 2},
        numeric_budget={"next_milestone_seconds": 1800},
    )
    recorder.observe(
        "process_tree_peak_rss_bytes",
        availability="unavailable",
        reason="no reliable process sampler",
    )

    payload = recorder.to_json_dict()
    assert payload["measurement_contract"]["cache_state"] == "unavailable"
    assert payload["measurement_observations"][0]["value"] is None
    assert payload["measurement_observations"][0]["availability"] == "unavailable"


def test_process_memory_scope_is_available_when_timing_is_disabled() -> None:
    sampler = _FakeProcessMemorySampler()
    recorder = TimingRecorder.disabled("workflow", process_memory_sampler=sampler)

    recorder.start_process_memory()
    sampler.current = (100, 80)
    with recorder.process_memory_scope("retained_runtime") as runtime_peak:
        pass
    sampler.current = (900, 700)
    foreground_peak = recorder.stop_process_memory(scope="foreground")

    assert runtime_peak.rss_bytes == 100
    assert runtime_peak.private_bytes == 80
    assert foreground_peak.rss_bytes == 900
    assert foreground_peak.private_bytes == 700
    assert sampler.start_count == 1
    assert sampler.stop_count == 1
    assert recorder.measurement_observations == ()


def test_process_memory_scope_stops_owned_sampler_on_exception() -> None:
    sampler = _FakeProcessMemorySampler()
    recorder = TimingRecorder("workflow", process_memory_sampler=sampler)
    sampler.current = (120, 90)

    with pytest.raises(RuntimeError, match="runtime failed"):
        with recorder.process_memory_scope("retained_runtime"):
            raise RuntimeError("runtime failed")

    assert not sampler.running
    assert (sampler.start_count, sampler.stop_count) == (1, 1)
    assert {row["value"] for row in recorder.measurement_observations} == {120, 90}


def test_unavailable_process_memory_is_never_reported_as_zero() -> None:
    sampler = ProcessTreeMemorySampler(
        sample_reader=lambda: ProcessMemorySample(
            rss_bytes=None,
            private_bytes=None,
            availability="unavailable",
            reason="unsupported platform",
        )
    )
    recorder = TimingRecorder("workflow", process_memory_sampler=sampler)

    with recorder.process_memory_scope("retained_runtime") as peak:
        pass

    assert peak.rss_bytes is None
    assert peak.private_bytes is None
    observations = recorder.measurement_observations
    assert {row["value"] for row in observations} == {None}
    assert {row["availability"] for row in observations} == {"unavailable"}


def test_nested_memory_scopes_sample_edges_once_and_remove_by_identity() -> None:
    current = [10]
    sample_count = 0

    def read_sample() -> ProcessMemorySample:
        nonlocal sample_count
        sample_count += 1
        return ProcessMemorySample(
            rss_bytes=current[0],
            private_bytes=current[0],
            availability="measured",
        )

    sampler = ProcessTreeMemorySampler(interval_sec=60, sample_reader=read_sample)
    with sampler.scope() as outer_peak:
        with sampler.scope() as inner_peak:
            current[0] = 20
        current[0] = 30

    assert sample_count == 4
    assert inner_peak.private_bytes == 20
    assert outer_peak.private_bytes == 30


def test_timing_recorder_live_observations_are_bounded(tmp_path: Path) -> None:
    live_path = tmp_path / "timing.live.json"
    recorder = TimingRecorder(
        "workflow",
        live_output_path=live_path,
        live_observation_limit=2,
    )
    for index in range(4):
        recorder.observe("metric", value=index)

    payload = json.loads(live_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "timing.live"
    assert "records" not in payload
    assert payload["observation_count"] == 4
    assert payload["dropped_observation_count"] == 2
    assert [row["value"] for row in payload["recent_measurement_observations"]] == [
        2,
        3,
    ]


def test_measurement_summary_merges_observations_without_alias_inference() -> None:
    recorder = TimingRecorder("discovery")
    recorder.record(
        "alignment.extract",
        elapsed_sec=1.0,
        metrics={"extract_xic_count": 99},
    )
    recorder.observe(
        "logical_xic_request_count",
        value=3,
        scope="S1",
        unit="requests",
    )
    recorder.observe(
        "logical_xic_request_count",
        value=None,
        scope="S1",
        unit="requests",
        availability="unavailable",
        reason="owner gap",
    )

    summary = recorder.to_json_dict()["summaries"]["measurement_summary"]
    rows = [row for row in summary if row["metric"] == "logical_xic_request_count"]
    assert rows == [
        {
            "metric": "logical_xic_request_count",
            "scope": "S1",
            "value": 3.0,
            "unit": "requests",
            "availability": "partial",
            "reason": "owner gap",
        }
    ]
    assert all(row["metric"] != "extract_xic_count" for row in summary)


def test_measurement_summary_preserves_direct_partial_value_and_reason() -> None:
    recorder = TimingRecorder("discovery")
    recorder.observe(
        "trace_point_count",
        value=12,
        scope="S1",
        unit="points",
        availability="partial",
        reason="one adapter path lacks point count",
    )

    rows = recorder.to_json_dict()["summaries"]["measurement_summary"]
    assert [row for row in rows if row["metric"] == "trace_point_count"] == [
        {
            "metric": "trace_point_count",
            "scope": "S1",
            "value": 12.0,
            "unit": "points",
            "availability": "partial",
            "reason": "one adapter path lacks point count",
        }
    ]


def test_measurement_summary_preserves_known_value_from_failed_observation() -> None:
    recorder = TimingRecorder("workflow")
    recorder.observe(
        "fingerprint_bytes",
        value=17,
        unit="bytes",
        availability="failed",
        reason="source path was replaced during hashing",
    )

    rows = recorder.to_json_dict()["summaries"]["measurement_summary"]
    assert rows == [
        {
            "metric": "fingerprint_bytes",
            "scope": "run",
            "value": 17.0,
            "unit": "bytes",
            "availability": "failed",
            "reason": "source path was replaced during hashing",
        }
    ]


def test_measurement_summary_preserves_all_distinct_partial_failure_reasons() -> None:
    recorder = TimingRecorder("workflow")
    recorder.observe(
        "ipc_bytes",
        value=11,
        scope="S1",
        unit="bytes",
        availability="partial",
        reason="executor framing excluded",
    )
    recorder.observe(
        "ipc_bytes",
        value=None,
        scope="S1",
        unit="bytes",
        availability="failed",
        reason="result pickle failed",
    )

    rows = recorder.to_json_dict()["summaries"]["measurement_summary"]
    assert rows == [
        {
            "metric": "ipc_bytes",
            "scope": "S1",
            "value": 11.0,
            "unit": "bytes",
            "availability": "partial",
            "reason": "executor framing excluded; result pickle failed",
        }
    ]


def test_measurement_summary_honors_sum_max_and_last_aggregation() -> None:
    recorder = TimingRecorder("workflow")
    for metric, aggregation, values in (
        ("bytes", "sum", (2, 5)),
        ("rss", "max", (7, 3)),
        ("heartbeat_offset", "last", (11, 13)),
    ):
        for value in values:
            recorder.observe(
                metric,
                value=value,
                unit="count",
                aggregation=aggregation,
            )

    rows = {
        row["metric"]: row
        for row in recorder.to_json_dict()["summaries"]["measurement_summary"]
    }
    assert rows["bytes"]["value"] == 7
    assert rows["rss"]["value"] == 7
    assert rows["heartbeat_offset"]["value"] == 13


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"unit": "bytes"}, {"unit": "calls"}),
        ({"aggregation": "sum"}, {"aggregation": "max"}),
    ],
)
def test_measurement_summary_rejects_mixed_contracts(
    first: dict[str, str],
    second: dict[str, str],
) -> None:
    recorder = TimingRecorder("workflow")
    recorder.observe("metric", value=1, **first)
    recorder.observe("metric", value=2, **second)

    with pytest.raises(ValueError, match="measurement contract mismatch"):
        recorder.to_json_dict()


def test_measurement_summary_rejects_unknown_aggregation() -> None:
    recorder = TimingRecorder("workflow")

    with pytest.raises(ValueError, match="unsupported measurement aggregation"):
        recorder.observe("metric", value=1, aggregation="median")


def test_measurement_observation_rejects_unknown_availability() -> None:
    recorder = TimingRecorder("workflow")

    with pytest.raises(ValueError, match="unsupported measurement availability"):
        recorder.observe("metric", value=1, availability="unknown")


class _Timer:
    def __init__(self, values: list[float]) -> None:
        self._values = values
        self._index = 0

    def __call__(self) -> float:
        value = self._values[self._index]
        self._index += 1
        return value


class _FakeProcessMemorySampler:
    def __init__(self) -> None:
        self.current = (0, 0)
        self.running = False
        self.start_count = 0
        self.stop_count = 0

    def start(self) -> bool:
        self.running = True
        self.start_count += 1
        return True

    def stop(self) -> ProcessMemoryPeak:
        self.running = False
        self.stop_count += 1
        return self._peak()

    @contextmanager
    def scope(self):
        started_here = not self.running
        if started_here:
            self.start()
        peak = ProcessMemoryPeak()
        try:
            yield peak
        finally:
            measured = self._peak()
            peak.rss_bytes = measured.rss_bytes
            peak.private_bytes = measured.private_bytes
            peak.availability = measured.availability
            if started_here:
                self.stop()

    def _peak(self) -> ProcessMemoryPeak:
        return ProcessMemoryPeak(
            rss_bytes=self.current[0],
            private_bytes=self.current[1],
            availability="measured",
        )


def test_timing_recorder_concurrent_live_output_thread_safety(tmp_path: Path) -> None:
    live_path = tmp_path / "timing.live.json"
    recorder = TimingRecorder("alignment", live_output_path=live_path)

    import concurrent.futures

    def worker(worker_id: int) -> None:
        for i in range(20):
            recorder.heartbeat_event(
                {
                    "event": "partition_progress",
                    "stage": f"worker_{worker_id}",
                    "completed": i,
                    "total": 20,
                }
            )
            recorder.record(f"stage_{worker_id}_{i}", elapsed_sec=0.01)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker, i) for i in range(4)]
        for future in futures:
            future.result()

    assert live_path.exists()
    payload = json.loads(live_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "timing.live"
    assert payload["record_count"] == 80
