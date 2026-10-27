from __future__ import annotations

from xic_extractor.execution._finalization import CoordinatorTerminalRecord
from xic_extractor.execution._observability import RuntimeMetricAvailability
from xic_extractor.execution.attempt import (
    RuntimeArtifactIoSample,
    RuntimeProcessTreePeakRssSample,
    RuntimeTelemetrySamples,
    RuntimeWorkerPeakRssSample,
    runtime_observation_event_count,
    runtime_started_worker_keys,
)


def build_runtime_telemetry_samples_from_coordinator(
    coordinator: CoordinatorTerminalRecord,
    *,
    event_step_ns: int = 10,
    process_tree_peak_rss_bytes: int = 0,
) -> RuntimeTelemetrySamples:
    """Build narrow deterministic inputs for the runtime-owned projection."""

    event_count = runtime_observation_event_count(coordinator)
    worker_keys = runtime_started_worker_keys(coordinator)
    preflight_failed = coordinator.state.runtime.manifest_preflight_failed
    return RuntimeTelemetrySamples(
        event_monotonic_offsets_ns=tuple(
            index * event_step_ns for index in range(event_count)
        ),
        artifact_io=RuntimeArtifactIoSample(
            read_count=0,
            read_bytes=0,
            write_count=0,
            write_bytes=0,
        ),
        process_tree_peak_rss=RuntimeProcessTreePeakRssSample(
            availability=(
                RuntimeMetricAvailability.UNSUPPORTED
                if preflight_failed
                else RuntimeMetricAvailability.MEASURED
            ),
            value=None if preflight_failed else process_tree_peak_rss_bytes,
            reason=("not_started_preflight_failure" if preflight_failed else None),
        ),
        worker_peak_rss=tuple(
            RuntimeWorkerPeakRssSample(
                worker_key=worker_key,
                availability=RuntimeMetricAvailability.MEASURED,
                value=0,
            )
            for worker_key in worker_keys
        ),
    )
