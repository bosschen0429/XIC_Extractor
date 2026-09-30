from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.tabular_io import optional_float, text_value


@dataclass(frozen=True)
class OverlayTraceRow:
    values: Mapping[str, object]

    @property
    def sample_stem(self) -> str:
        return text_value(self.values.get("sample_stem"))

    @property
    def status(self) -> str:
        return text_value(self.values.get("status"))

    @property
    def group(self) -> str:
        return text_value(self.values.get("group"))

    def first_text(self, fields: Sequence[str]) -> str:
        for field in fields:
            value = text_value(self.values.get(field))
            if value:
                return value
        return ""

    def optional_float(self, field: str) -> float | None:
        return optional_float(self.values.get(field))

    def optional_float_sequence(self, field: str) -> tuple[float, ...]:
        value = self.values.get(field)
        if not isinstance(value, Sequence) or isinstance(value, str | bytes):
            return ()
        parsed: list[float] = []
        for item in value:
            parsed_value = optional_float(item)
            if parsed_value is not None:
                parsed.append(parsed_value)
        return tuple(parsed)


@dataclass(frozen=True)
class OverlayTraceDataBundle:
    source_json: Path
    family_id: str
    traces: tuple[OverlayTraceRow, ...]
    payload: Mapping[str, object]

    @property
    def evidence_summary(self) -> Mapping[str, object]:
        value = self.payload.get("evidence_summary")
        return value if isinstance(value, Mapping) else {}

    @property
    def family_center_rt(self) -> float | None:
        return optional_float(self.payload.get("family_center_rt"))

    @property
    def rt_min(self) -> float | None:
        return optional_float(self.payload.get("rt_min"))

    @property
    def rt_max(self) -> float | None:
        return optional_float(self.payload.get("rt_max"))

    @property
    def trace_mappings(self) -> tuple[Mapping[str, object], ...]:
        return tuple(trace.values for trace in self.traces)


def load_overlay_trace_data(path: Path) -> OverlayTraceDataBundle:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"overlay trace data is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"overlay trace data must be a JSON object: {path}")
    family_id = text_value(payload.get("family_id") or payload.get("feature_family_id"))
    if not family_id:
        raise ValueError(f"overlay trace data does not declare family_id: {path}")
    traces = payload.get("traces")
    if not isinstance(traces, list):
        traces = payload.get("samples")
    if not isinstance(traces, list):
        raise ValueError(f"overlay trace data missing traces/samples array: {path}")
    return OverlayTraceDataBundle(
        source_json=path,
        family_id=family_id,
        traces=tuple(
            OverlayTraceRow(trace) for trace in traces if isinstance(trace, Mapping)
        ),
        payload=payload,
    )


def load_overlay_trace_data_many(
    paths: Sequence[Path],
) -> tuple[OverlayTraceDataBundle, ...]:
    return tuple(load_overlay_trace_data(path) for path in paths)
