from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter, sleep

JsonMetric = str | int | float | bool | None
TimingObserver = Callable[[Mapping[str, object]], None]


@dataclass(frozen=True)
class TimingRecord:
    stage: str
    elapsed_sec: float
    sample_stem: str = ""
    metrics: dict[str, JsonMetric] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "sample_stem": self.sample_stem,
            "stage": self.stage,
            "elapsed_sec": self.elapsed_sec,
            "metrics": dict(self.metrics),
        }


@dataclass
class TimingStage:
    metrics: dict[str, JsonMetric] = field(default_factory=dict)
    elapsed_sec: float | None = None


class TimingRecorder:
    def __init__(
        self,
        pipeline: str,
        *,
        run_id: str | None = None,
        enabled: bool = True,
        live_output_path: Path | None = None,
        live_history_limit: int = 32,
        live_observation_limit: int = 64,
        instrument_writers: bool = False,
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        if live_history_limit < 1 or live_observation_limit < 1:
            raise ValueError("live_history_limit must be positive")
        self.pipeline = pipeline
        self.run_id = run_id or _default_run_id()
        self.enabled = enabled
        self.live_output_path = live_output_path
        self.live_history_limit = live_history_limit
        self.live_observation_limit = live_observation_limit
        self.instrument_writers = instrument_writers
        self._timer = timer
        self._records: list[TimingRecord] = []
        self._exclusive_elapsed: list[float | None] = []
        self._span_semantics: list[dict[str, str]] = []
        self._span_stack: list[list[float | int]] = []
        self._heartbeat: dict[str, object] = {}
        self._last_heartbeat_publish: float | None = None
        self._measurement_observations: list[dict[str, object]] = []
        self._measurement_contract: dict[str, object] = {}

    @classmethod
    def disabled(
        cls,
        pipeline: str,
        *,
        timer: Callable[[], float] = perf_counter,
    ) -> TimingRecorder:
        return cls(pipeline, enabled=False, timer=timer)

    @property
    def records(self) -> tuple[TimingRecord, ...]:
        return tuple(self._records)

    @property
    def measurement_observations(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(row) for row in self._measurement_observations)

    def record(
        self,
        stage: str,
        *,
        elapsed_sec: float,
        sample_stem: str = "",
        metrics: Mapping[str, object] | None = None,
    ) -> None:
        if not self.enabled:
            return
        self._append_record(
            TimingRecord(
                stage=stage,
                elapsed_sec=max(0.0, elapsed_sec),
                sample_stem=sample_stem,
                metrics=_clean_metrics(metrics or {}),
            ),
            span_kind="caller",
            aggregation="sum",
            overlap="unknown",
        )

    def heartbeat(
        self,
        *,
        stage: str,
        sample_stem: str = "",
        completed: int | None = None,
        total: int | None = None,
        **values: object,
    ) -> None:
        """Keep latest progress; coalesce stage-local bursts until the next second.

        Stage exit flushes the latest heartbeat, including on exceptions.
        Unscoped updates, transitions, failures and completion publish immediately.
        """

        if not self.enabled:
            return

        progress: dict[str, object] = {"stage": stage}
        if sample_stem:
            progress["sample_stem"] = sample_stem
        if completed is not None:
            progress["completed"] = completed
        if total is not None:
            progress["total"] = total
        progress.update(values)
        previous_stage = self._heartbeat.get("stage")
        self._heartbeat = progress
        if self.live_output_path is None:
            return
        # Use a separate monotonic clock so progress cannot consume an injected
        # stage-duration timer. Only active stages guarantee a final flush.
        now = perf_counter()
        if (
            self._span_stack
            and self._last_heartbeat_publish is not None
            and previous_stage == stage
            and values.get("succeeded") is not False
            and not (completed is not None and total is not None and completed >= total)
            and now - self._last_heartbeat_publish < 1.0
        ):
            return
        self._write_live()
        self._last_heartbeat_publish = now

    def heartbeat_event(self, event: Mapping[str, object]) -> None:
        """Accept a workflow progress mapping while keeping kwargs typed."""

        if not self.enabled:
            return

        event_name = event.get("event")
        if not isinstance(event_name, str):
            return
        stage = event.get("stage", event_name)
        sample = event.get("sample_stem", "")
        completed = event.get("completed")
        total = event.get("total")
        extras = {
            str(key): value
            for key, value in event.items()
            if key not in {"event", "stage", "sample_stem", "completed", "total"}
        }
        self.heartbeat(
            stage=stage if isinstance(stage, str) else event_name,
            sample_stem=sample if isinstance(sample, str) else "",
            completed=completed if isinstance(completed, int) else None,
            total=total if isinstance(total, int) else None,
            **extras,
        )

    def observe(
        self,
        metric: str,
        *,
        value: object = None,
        scope: str = "run",
        unit: str = "",
        aggregation: str = "sum",
        source: str = "",
        availability: str = "measured",
        reason: str = "",
    ) -> None:
        """Append one mechanical observation to the diagnostic projection."""

        if not self.enabled:
            return
        if aggregation not in {"sum", "max", "last"}:
            raise ValueError(f"unsupported measurement aggregation {aggregation!r}")
        if availability not in {"measured", "partial", "unavailable", "failed"}:
            raise ValueError(f"unsupported measurement availability {availability!r}")

        row: dict[str, object] = {
            "metric": metric,
            "scope": scope,
            "unit": unit,
            "aggregation": aggregation,
            "source": source,
            "availability": availability,
            "value": _clean_metric(value),
            "reason": reason,
        }
        self._measurement_observations.append(row)
        self._write_live()

    def set_measurement_contract(
        self,
        *,
        cache_state: str = "unavailable",
        aggregation: Mapping[str, object] | None = None,
        repetition_noise_design: Mapping[str, object] | None = None,
        numeric_budget: Mapping[str, object] | None = None,
    ) -> None:
        if not self.enabled:
            return
        self._measurement_contract = {
            "cache_state": cache_state,
            "aggregation": dict(aggregation or {}),
            "repetition_noise_design": dict(repetition_noise_design or {}),
            "numeric_budget": dict(numeric_budget or {}),
        }
        self._write_live()

    @contextmanager
    def stage(
        self,
        stage: str,
        *,
        sample_stem: str = "",
        metrics: Mapping[str, object] | None = None,
    ) -> Iterator[TimingStage]:
        scope = TimingStage(_clean_metrics(metrics or {}))
        if not self.enabled:
            yield scope
            return

        if not self._span_stack:
            self._last_heartbeat_publish = None
        start = self._timer()
        parent_start = self._span_stack[-1][1] if self._span_stack else None
        record_index = len(self._records)
        self._span_stack.append([record_index, start, 0.0])
        try:
            yield scope
        finally:
            elapsed = self._timer() - start
            scope.elapsed_sec = max(0.0, elapsed)
            entry = self._span_stack.pop()
            exclusive = max(0.0, elapsed - float(entry[2]))
            if self._span_stack:
                # Charge nested wall time to the active parent. The parent is
                # not recorded until its scope closes, so retain the child
                # duration on the stack rather than summing completed records.
                self._span_stack[-1][2] += max(0.0, elapsed)
            self._append_record(
                TimingRecord(
                    stage=stage,
                    elapsed_sec=max(0.0, elapsed),
                    sample_stem=sample_stem,
                    metrics=_clean_metrics(scope.metrics),
                ),
                span_kind="wall",
                aggregation="last",
                overlap="nested" if parent_start is not None else "none",
            )
            self._exclusive_elapsed[-1] = exclusive

    def write_json(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_json_dict()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def to_json_dict(self) -> dict[str, object]:
        from xic_extractor.diagnostics.timing_summary import summarize_timing_records

        return {
            "schema_version": "timing.v1",
            "run_id": self.run_id,
            "pipeline": self.pipeline,
            "records": [record.to_json_dict() for record in self._records],
            "summaries": summarize_timing_records(
                self._records,
                exclusive_elapsed=self._exclusive_elapsed,
                measurement_observations=self._measurement_observations,
                span_semantics=self._span_semantics,
            ),
            "measurement_contract": dict(self._measurement_contract),
            "measurement_observations": list(self._measurement_observations),
            "span_semantics": list(self._span_semantics),
        }

    def _append_record(
        self,
        record: TimingRecord,
        *,
        span_kind: str,
        aggregation: str,
        overlap: str,
    ) -> None:
        self._records.append(record)
        self._exclusive_elapsed.append(None)
        self._span_semantics.append(
            {
                "span_kind": span_kind,
                "aggregation": aggregation,
                "overlap": overlap,
            }
        )
        self._write_live()

    def _write_live(self) -> None:
        if self.live_output_path is None:
            return
        self.live_output_path.parent.mkdir(parents=True, exist_ok=True)
        start = max(0, len(self._records) - self.live_history_limit)
        records = [record.to_json_dict() for record in self._records[start:]]
        observation_start = max(
            0,
            len(self._measurement_observations) - self.live_observation_limit,
        )
        observations = self._measurement_observations[observation_start:]
        payload = {
            "schema_version": "timing.live.v2",
            "run_id": self.run_id,
            "pipeline": self.pipeline,
            "heartbeat": dict(self._heartbeat),
            "recent_records": records,
            "record_count": len(self._records),
            "dropped_record_count": start,
            "measurement_contract": dict(self._measurement_contract),
            "measurement_observations": observations,
            "recent_measurement_observations": observations,
            "observation_count": len(self._measurement_observations),
            "dropped_observation_count": observation_start,
            "span_semantics": self._span_semantics[start:],
        }
        serialized = json.dumps(payload, indent=2, sort_keys=True)
        temporary_path = self.live_output_path.with_name(
            f"{self.live_output_path.name}.tmp"
        )
        temporary_path.write_text(serialized, encoding="utf-8")
        _replace_live_output(temporary_path, self.live_output_path)


def _replace_live_output(temporary_path: Path, output_path: Path) -> None:
    """Retry transient Windows locks while preserving atomic live updates."""

    for attempt in range(5):
        try:
            temporary_path.replace(output_path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            sleep(0.05 * (attempt + 1))


def _clean_metrics(metrics: Mapping[str, object]) -> dict[str, JsonMetric]:
    return {str(key): _clean_metric(value) for key, value in metrics.items()}


def _clean_metric(value: object) -> JsonMetric:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
