"""Project Discovery acquisition and RAW-session facts into timing observations."""

from __future__ import annotations

from xic_extractor.acquisition_contract import (
    RawAcquisitionSessionReceipt,
)
from xic_extractor.discovery.ms1_capture import _DiscoveryMs1Measurements
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.processing_identity import AcquisitionSourceIdentity

DISCOVERY_MS1_MEASUREMENT_NAMES = frozenset(
    {
        "discovery_ms1_input_group_count",
        "discovery_ms1_empty_group_count",
        "discovery_ms1_exact_scan_window_count",
        "discovery_ms1_predicted_chunked_call_count",
        "discovery_ms1_batch_max_requests",
        "discovery_ms1_reacquisition_request_count",
        "discovery_ms1_predicted_call_reduction_pct",
    }
)


def record_discovery_ms1_measurements(
    timing_recorder: TimingRecorder,
    measurements: _DiscoveryMs1Measurements | None,
    sample_stem: str,
    *,
    unavailable_reason: str = (
        "RAW handle is not bound to the acquisition owner batch contract"
    ),
) -> None:
    if measurements is None:
        _record_unavailable_discovery_ms1_measurements(
            timing_recorder,
            sample_stem,
            reason=unavailable_reason,
        )
        return

    batches = measurements.acquisition_batches
    logical_xic_request_count = sum(
        batch.logical_xic_request_count for batch in batches
    )
    nonempty_group_count = (
        measurements.input_group_count - measurements.empty_group_count
    )
    if measurements.initial_request_count > nonempty_group_count or (
        logical_xic_request_count
        != measurements.initial_request_count + measurements.reacquisition_request_count
    ):
        raise ValueError(
            "Discovery MS1 acquisition receipts do not cover the input population"
        )
    acquisition_source = "xic_extractor.acquisition_contract.TraceAcquisitionBatch"
    discovery_source = "xic_extractor.discovery.ms1_capture"
    observed = (
        (
            "logical_xic_request_count",
            logical_xic_request_count,
            "requests",
            acquisition_source,
        ),
        (
            "xic_batch_count",
            sum(batch.xic_batch_count for batch in batches),
            "batches",
            acquisition_source,
        ),
        (
            "adapter_chromatogram_call_count",
            sum(batch.adapter_chromatogram_call_count for batch in batches),
            "calls",
            acquisition_source,
        ),
        (
            "trace_point_count",
            sum(batch.trace_point_count for batch in batches),
            "points",
            acquisition_source,
        ),
        (
            "discovery_ms1_input_group_count",
            measurements.input_group_count,
            "groups",
            discovery_source,
        ),
        (
            "discovery_ms1_empty_group_count",
            measurements.empty_group_count,
            "groups",
            discovery_source,
        ),
        (
            "discovery_ms1_exact_scan_window_count",
            measurements.exact_scan_window_count,
            "windows",
            discovery_source,
        ),
        (
            "discovery_ms1_predicted_chunked_call_count",
            measurements.predicted_chunked_call_count,
            "calls",
            discovery_source,
        ),
        (
            "discovery_ms1_batch_max_requests",
            measurements.batch_max_requests,
            "requests/batch",
            discovery_source,
        ),
        (
            "discovery_ms1_reacquisition_request_count",
            measurements.reacquisition_request_count,
            "requests",
            discovery_source,
        ),
        (
            "discovery_ms1_predicted_call_reduction_pct",
            (
                0.0
                if logical_xic_request_count == 0
                else 100.0
                * (
                    logical_xic_request_count
                    - measurements.predicted_chunked_call_count
                )
                / logical_xic_request_count
            ),
            "percent",
            discovery_source,
        ),
    )
    for metric, value, unit, source in observed:
        timing_recorder.observe(
            metric,
            value=value,
            scope=sample_stem,
            unit=unit,
            source=source,
        )
    _record_rss_unavailable(timing_recorder, sample_stem)
    validate_discovery_ms1_observations(timing_recorder, sample_stem)


def _record_unavailable_discovery_ms1_measurements(
    timing_recorder: TimingRecorder,
    sample_stem: str,
    *,
    reason: str,
) -> None:
    unavailable = (
        ("logical_xic_request_count", "requests"),
        ("xic_batch_count", "batches"),
        ("adapter_chromatogram_call_count", "calls"),
        ("trace_point_count", "points"),
        ("discovery_ms1_input_group_count", "groups"),
        ("discovery_ms1_empty_group_count", "groups"),
        ("discovery_ms1_exact_scan_window_count", "windows"),
        ("discovery_ms1_predicted_chunked_call_count", "calls"),
        ("discovery_ms1_batch_max_requests", "requests/batch"),
        ("discovery_ms1_reacquisition_request_count", "requests"),
        ("discovery_ms1_predicted_call_reduction_pct", "percent"),
    )
    for metric, unit in unavailable:
        timing_recorder.observe(
            metric,
            value=None,
            scope=sample_stem,
            unit=unit,
            source="xic_extractor.discovery.ms1_capture",
            availability="unavailable",
            reason=reason,
        )
    _record_rss_unavailable(timing_recorder, sample_stem)
    validate_discovery_ms1_observations(timing_recorder, sample_stem)


def _record_rss_unavailable(
    timing_recorder: TimingRecorder,
    sample_stem: str,
) -> None:
    reason = "unsupported: Discovery runner has no owner-bound RSS sampler"
    for metric in ("worker_peak_rss_bytes", "process_tree_peak_rss_bytes"):
        timing_recorder.observe(
            metric,
            value=None,
            scope=sample_stem,
            unit="bytes",
            aggregation="max",
            source="xic_extractor.discovery.pipeline",
            availability="unavailable",
            reason=reason,
        )


def record_raw_open_metrics(
    timing_recorder: TimingRecorder,
    acquisition_source: AcquisitionSourceIdentity | None,
    raw_open_receipt: RawAcquisitionSessionReceipt | None,
    sample_stem: str,
) -> None:
    """Count the one RAW session a sample opens, or say why it is unknown."""
    raw_open_count: int | None = None
    if acquisition_source is None:
        reason = "RAW opener has no bound acquisition source"
    elif raw_open_receipt is None:
        reason = "process result carries no owner RAW-open receipt"
    else:
        raw_open_count = 1
        reason = ""
    timing_recorder.observe(
        "raw_open_count",
        value=raw_open_count,
        scope=sample_stem,
        unit="opens",
        source="xic_extractor.discovery.runtime_measurements.record_raw_open_metrics",
        availability="measured" if raw_open_count is not None else "unavailable",
        reason=reason,
    )


def portable_worker_observations(
    timing_recorder: TimingRecorder,
) -> tuple[dict[str, object], ...]:
    return tuple(
        observation
        for observation in timing_recorder.measurement_observations
        if not _is_reserved_process_measurement(observation)
    )


def _is_reserved_process_measurement(observation: dict[str, object]) -> bool:
    metric = observation.get("metric")
    reserved = {
        "raw_open_count",
        "logical_xic_request_count",
        "xic_batch_count",
        "adapter_chromatogram_call_count",
        "trace_point_count",
        "worker_peak_rss_bytes",
        "process_tree_peak_rss_bytes",
    }
    return isinstance(metric, str) and (
        metric.startswith("discovery_ms1_") or metric in reserved
    )


def reject_caller_authored_discovery_ms1_measurement(
    observation: dict[str, object],
) -> None:
    if _is_reserved_process_measurement(observation):
        raise ValueError(
            "Discovery worker diagnostics cannot author acquisition/runtime counts"
        )


def validate_discovery_ms1_observations(
    timing_recorder: TimingRecorder,
    sample_stem: str,
) -> None:
    if not timing_recorder.enabled:
        return
    names = tuple(
        str(row["metric"])
        for row in timing_recorder.measurement_observations
        if row.get("scope") == sample_stem
        and isinstance(row.get("metric"), str)
        and str(row["metric"]).startswith("discovery_ms1_")
    )
    if len(names) != len(set(names)):
        raise ValueError("duplicate Discovery MS1 measurement field")
    if set(names) != DISCOVERY_MS1_MEASUREMENT_NAMES:
        unexpected = sorted(set(names) - DISCOVERY_MS1_MEASUREMENT_NAMES)
        missing = sorted(DISCOVERY_MS1_MEASUREMENT_NAMES - set(names))
        raise ValueError(
            "unknown or missing Discovery MS1 measurement field: "
            f"unexpected={unexpected!r}, missing={missing!r}"
        )


def record_raw_measurements(
    timing_recorder: TimingRecorder,
    raw: object,
    sample_stem: str,
) -> None:
    """Project Discovery owner facts without inventing unavailable counters."""

    try:
        raw_calls = getattr(raw, "raw_chromatogram_call_count", None)
        raw_calls_reason = (
            "" if isinstance(raw_calls, int) else "raw handle has no call counter"
        )
    except Exception as exc:
        raw_calls = None
        raw_calls_reason = (
            f"raw handle call counter read failed: {type(exc).__name__}: {exc}"
        )
    timing_recorder.observe(
        "logical_xic_request_count",
        value=None,
        scope=sample_stem,
        unit="requests",
        source="discovery.raw_handle",
        availability="unavailable",
        reason="Discovery raw adapter exposes no logical request counter",
    )
    timing_recorder.observe(
        "xic_batch_count",
        value=None,
        scope=sample_stem,
        unit="batches",
        source="discovery.raw_handle",
        availability="unavailable",
        reason="Discovery raw adapter exposes no batch counter",
    )
    timing_recorder.observe(
        "adapter_chromatogram_call_count",
        value=raw_calls,
        scope=sample_stem,
        unit="calls",
        source="discovery.raw_handle.raw_chromatogram_call_count",
        availability="measured" if isinstance(raw_calls, int) else "unavailable",
        reason=raw_calls_reason,
    )
    timing_recorder.observe(
        "trace_point_count",
        value=None,
        scope=sample_stem,
        unit="points",
        source="discovery.raw_handle",
        availability="unavailable",
        reason="Discovery raw adapter does not expose returned point count",
    )
