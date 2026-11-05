from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xic_extractor.tabular_io import text_value

if TYPE_CHECKING:
    from xic_extractor.execution.timing import TimingRecord


@dataclass(frozen=True)
class TimingRecordView:
    stage: str
    elapsed_sec: float
    sample_stem: str = ""
    metrics: Mapping[str, object] | None = None


def summarize_timing_records(
    records: Iterable[TimingRecord],
    *,
    exclusive_elapsed: Iterable[float | None] | None = None,
    measurement_observations: Iterable[Mapping[str, object]] = (),
    span_semantics: Iterable[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Build no-RAW timing and XIC locality summaries from recorded spans."""

    views = tuple(_record_view(record) for record in records)
    measurement_rows = _measurement_summary(views, tuple(measurement_observations))
    payload: dict[str, object] = {
        "stage_summary": _stage_summary(views),
        "raw_xic_locality_summary": _raw_xic_locality_summary(views),
        "measurement_summary": measurement_rows,
    }
    payload["fingerprint_summary"] = [
        row
        for row in measurement_rows
        if text_value(row.get("metric")).startswith("fingerprint")
    ]
    if exclusive_elapsed is not None:
        exclusive = tuple(
            None if value is None else float(value) for value in exclusive_elapsed
        )
        payload["non_overlapping_stage_summary"] = _exclusive_stage_summary(
            views, exclusive
        )
        payload["nested_stage_summary"] = _nested_stage_summary(
            views, tuple(span_semantics)
        )
    return payload


_MEASUREMENT_FIELDS: dict[str, tuple[str, ...]] = {
    "raw_open_count": ("raw_open_count", "raw_open_calls"),
    "logical_xic_request_count": (
        "logical_xic_request_count",
        "extract_xic_count",
    ),
    "xic_batch_count": ("xic_batch_count", "extract_xic_batch_count"),
    "adapter_chromatogram_call_count": (
        "adapter_chromatogram_call_count",
        "raw_chromatogram_call_count",
    ),
    "trace_point_count": ("trace_point_count", "point_count"),
}


def _measurement_summary(
    records: tuple[TimingRecordView, ...],
    observations: tuple[Mapping[str, object], ...],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for observation in observations:
        metric = text_value(observation.get("metric"))
        scope = text_value(observation.get("scope"))
        if metric:
            grouped[(metric, scope)].append(observation)
    # Record metrics are accepted only when they use the exact canonical name.
    # In particular, Alignment's extract_xic aliases must not become Discovery
    # measurements by inference.
    canonical_fields = tuple(_MEASUREMENT_FIELDS)
    for record in records:
        metrics = record.metrics or {}
        for metric in canonical_fields:
            if metric in metrics:
                grouped[(metric, record.sample_stem)].append(
                    {
                        "metric": metric,
                        "scope": record.sample_stem,
                        "unit": "count",
                        "aggregation": "sum",
                        "source": "timing_record",
                        "availability": "measured",
                        "value": metrics[metric],
                        "reason": "",
                    }
                )
    rows: list[dict[str, object]] = []
    for (metric, scope), items in grouped.items():
        units = {text_value(item.get("unit")) for item in items}
        aggregations = {text_value(item.get("aggregation")) or "sum" for item in items}
        if len(units) != 1 or len(aggregations) != 1:
            raise ValueError(
                f"measurement contract mismatch for {metric!r} in scope {scope!r}"
            )
        aggregation = next(iter(aggregations))
        if aggregation not in {"sum", "max", "last"}:
            raise ValueError(f"unsupported measurement aggregation {aggregation!r}")
        measured = [
            item
            for item in items
            if item.get("availability") in {"measured", "partial"}
        ]
        unavailable = [
            item
            for item in items
            if item.get("availability") in {"unavailable", "failed"}
        ]
        partial = [item for item in items if item.get("availability") == "partial"]
        numeric_values = [
            float(value)
            for item in items
            if item.get("availability") in {"measured", "partial", "failed"}
            for value in (item.get("value"),)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if partial or (measured and unavailable):
            availability = "partial"
        elif measured:
            availability = "measured"
        elif any(item.get("availability") == "failed" for item in items):
            availability = "failed"
        else:
            availability = "unavailable"
        reasons = tuple(
            dict.fromkeys(
                text_value(item.get("reason"))
                for item in items
                if text_value(item.get("reason"))
            )
        )
        aggregate_value: float | None = None
        if numeric_values:
            if aggregation == "sum":
                aggregate_value = sum(numeric_values)
            elif aggregation == "max":
                aggregate_value = max(numeric_values)
            else:
                aggregate_value = numeric_values[-1]
        rows.append(
            {
                "metric": metric,
                "scope": scope,
                "value": aggregate_value,
                "unit": next(iter(units)),
                "availability": availability,
                "reason": "; ".join(reasons),
            }
        )
    return sorted(
        rows,
        key=lambda row: (text_value(row["metric"]), text_value(row["scope"])),
    )


def _exclusive_stage_summary(
    records: tuple[TimingRecordView, ...],
    exclusive_elapsed: tuple[float | None, ...],
) -> list[dict[str, object]]:
    """Attribute every wall span once after subtracting nested child wall."""

    by_stage: dict[str, list[tuple[TimingRecordView, float]]] = defaultdict(list)
    for index, record in enumerate(records):
        elapsed = exclusive_elapsed[index] if index < len(exclusive_elapsed) else None
        if elapsed is None:
            continue
        by_stage[record.stage].append((record, max(0.0, elapsed)))
    rows: list[dict[str, object]] = []
    for stage, stage_records in by_stage.items():
        values = [value for _record, value in stage_records]
        rows.append(
            {
                "stage": stage,
                "record_count": len(values),
                "total_exclusive_elapsed_sec": sum(values),
                "max_exclusive_elapsed_sec": max(values, default=0.0),
            }
        )
    return sorted(rows, key=_summary_sort_key_exclusive)


def _nested_stage_summary(
    records: tuple[TimingRecordView, ...],
    span_semantics: tuple[Mapping[str, object], ...],
) -> list[dict[str, object]]:
    """Report inclusive wall for spans explicitly nested under another span."""

    by_stage: dict[str, list[float]] = defaultdict(list)
    for index, record in enumerate(records):
        semantics = span_semantics[index] if index < len(span_semantics) else {}
        if semantics.get("span_kind") != "wall" or semantics.get("overlap") != "nested":
            continue
        by_stage[record.stage].append(max(0.0, record.elapsed_sec))
    rows: list[dict[str, object]] = [
        {
            "stage": stage,
            "record_count": len(values),
            "total_elapsed_sec": sum(values),
            "max_elapsed_sec": max(values, default=0.0),
        }
        for stage, values in by_stage.items()
    ]
    return sorted(rows, key=_summary_sort_key)


def _summary_sort_key_exclusive(row: Mapping[str, object]) -> tuple[float, str]:
    return (
        -_float_value(row.get("total_exclusive_elapsed_sec")),
        text_value(row.get("stage")),
    )


def _stage_summary(
    records: tuple[TimingRecordView, ...],
) -> list[dict[str, object]]:
    by_stage: dict[str, list[TimingRecordView]] = defaultdict(list)
    for record in records:
        by_stage[record.stage].append(record)
    rows: list[dict[str, object]] = []
    for stage, stage_records in by_stage.items():
        elapsed_values = [record.elapsed_sec for record in stage_records]
        sample_stems = {
            record.sample_stem for record in stage_records if record.sample_stem
        }
        rows.append(
            {
                "stage": stage,
                "record_count": len(stage_records),
                "sample_count": len(sample_stems),
                "total_elapsed_sec": sum(elapsed_values),
                "max_elapsed_sec": max(elapsed_values, default=0.0),
            }
        )
    return sorted(
        rows,
        key=_summary_sort_key,
    )


def _raw_xic_locality_summary(
    records: tuple[TimingRecordView, ...],
) -> list[dict[str, object]]:
    by_stage: dict[str, list[TimingRecordView]] = defaultdict(list)
    for record in records:
        metrics = record.metrics or {}
        if any(
            _int_metric(metrics, field)
            for field in (
                "extract_xic_count",
                "extract_xic_batch_count",
                "raw_chromatogram_call_count",
                "point_count",
            )
        ):
            by_stage[record.stage].append(record)

    rows: list[dict[str, object]] = []
    for stage, stage_records in by_stage.items():
        extract_xic_count = _sum_metric(stage_records, "extract_xic_count")
        extract_xic_batch_count = _sum_metric(
            stage_records,
            "extract_xic_batch_count",
        )
        raw_call_count = _sum_metric(stage_records, "raw_chromatogram_call_count")
        point_count = _sum_metric(stage_records, "point_count")
        elapsed_sec = sum(record.elapsed_sec for record in stage_records)
        rows.append(
            {
                "stage": stage,
                "record_count": len(stage_records),
                "total_elapsed_sec": elapsed_sec,
                "extract_xic_count": extract_xic_count,
                "extract_xic_batch_count": extract_xic_batch_count,
                "raw_chromatogram_call_count": raw_call_count,
                "point_count": point_count,
                "raw_calls_per_xic": _ratio_or_none(
                    raw_call_count,
                    extract_xic_count,
                ),
                "mean_xic_per_raw_chromatogram_call": (
                    None if raw_call_count == 0 else extract_xic_count / raw_call_count
                ),
                "mean_xic_per_extract_batch": (
                    None
                    if extract_xic_batch_count == 0
                    else extract_xic_count / extract_xic_batch_count
                ),
            }
        )
    return sorted(
        rows,
        key=_summary_sort_key,
    )


def _summary_sort_key(row: Mapping[str, object]) -> tuple[float, str]:
    return (-_float_value(row.get("total_elapsed_sec")), text_value(row.get("stage")))


def _ratio_or_none(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _sum_metric(records: Iterable[TimingRecordView], field: str) -> int:
    return sum(_int_metric(record.metrics or {}, field) for record in records)


def _int_metric(metrics: Mapping[str, object], field: str) -> int:
    value = metrics.get(field)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = text_value(value)
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _record_view(record: TimingRecord) -> TimingRecordView:
    return TimingRecordView(
        stage=text_value(record.stage),
        elapsed_sec=_float_value(record.elapsed_sec),
        sample_stem=text_value(record.sample_stem),
        metrics=_mapping_or_empty(record.metrics),
    )


def _mapping_or_empty(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _float_value(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(text_value(value))
    except ValueError:
        return 0.0
