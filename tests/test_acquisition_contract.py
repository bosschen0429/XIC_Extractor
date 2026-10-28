import json
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.acquisition_contract import (
    AcquisitionContractError,
    TraceCoordinateKind,
    acquire_trace_batch,
    deserialize_acquired_trace,
    mint_trace_acquisition_capability,
    serialize_acquired_trace,
)
from xic_extractor.execution.attempt import (
    derive_runtime_acquisition_metrics,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.xic_models import XICRequest, XICTrace


class _NoRawBatchAdapter:
    def __init__(self) -> None:
        self.batch_calls = 0
        self.scan_calls = 0
        self.raw_chromatogram_call_count = 0

    def extract_xic_many(self, requests):
        self.batch_calls += 1
        return tuple(
            XICTrace.from_arrays(
                [request.rt_min, request.rt_max],
                [0.0, 10.0],
            )
            for request in requests
        )

    def scan_window_for_request(self, request):
        self.scan_calls += 1
        return (100, 120)


def test_capability_aware_batch_trace_coordinates_are_executable_no_raw(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="tests.no_raw_batch_adapter",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    requests = (
        XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),
        XICRequest(mz=142.0611, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),
    )
    adapter = _NoRawBatchAdapter()

    batch = acquire_trace_batch(capability, requests, adapter)

    assert adapter.batch_calls == 1
    assert adapter.scan_calls == 2
    assert batch.logical_xic_request_count == 2
    assert batch.xic_batch_count == 1
    assert batch.adapter_chromatogram_call_count == 0
    assert batch.trace_point_count == 4
    assert [trace.coordinate.first_scan for trace in batch.traces] == [100, 100]
    assert all(
        trace.coordinate.capability.acquisition_source == source
        for trace in batch.traces
    )


def test_acquired_trace_owner_roundtrip_closes_coordinate_request_and_content(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="tests.acquired_trace_persistence",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    request = XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0)
    acquired = acquire_trace_batch(capability, (request,), _NoRawBatchAdapter()).traces[
        0
    ]
    payload = serialize_acquired_trace(acquired)

    restored = deserialize_acquired_trace(
        payload,
        expected_acquisition_source=source,
    )

    assert restored.coordinate == acquired.coordinate
    assert np.array_equal(restored.trace.rt, acquired.trace.rt)
    assert np.array_equal(restored.trace.intensity, acquired.trace.intensity)

    other_path = tmp_path / "different.fixture"
    other_path.write_bytes(b"different acquired source")
    other_source = build_acquisition_source_identity(
        other_path,
        acquisition_format="fixture",
        observation_namespace=source.observation_namespace,
    )
    with pytest.raises(AcquisitionContractError, match="expected acquisition source"):
        deserialize_acquired_trace(payload, expected_acquisition_source=other_source)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["coordinate"].__setitem__("trace_point_count", 1),
        lambda value: value["trace"]["intensity"].__setitem__(0, "-0x0.0p+0"),
        lambda value: value["trace"]["intensity"].__setitem__(0, "0x0p+0"),
        lambda value: value["trace"]["intensity"].__setitem__(0, "nan"),
        lambda value: value["trace"]["intensity"].__setitem__(0, "inf"),
        lambda value: value["coordinate"].__setitem__("first_scan", 100.0),
    ],
)
def test_acquired_trace_owner_rejects_resealed_tamper(
    tmp_path: Path,
    mutate,
) -> None:
    source = _source(tmp_path)
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="tests.acquired_trace_tamper",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    acquired = acquire_trace_batch(
        capability,
        (XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),),
        _NoRawBatchAdapter(),
    ).traces[0]
    value = json.loads(serialize_acquired_trace(acquired))
    mutate(value)

    with pytest.raises(AcquisitionContractError):
        deserialize_acquired_trace(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )


def test_runtime_owner_aggregates_only_typed_acquisition_receipts(
    tmp_path: Path,
) -> None:
    capability = mint_trace_acquisition_capability(
        _source(tmp_path),
        adapter_namespace="tests.runtime_owner_aggregation",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    adapter = _NoRawBatchAdapter()
    request = XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0)
    batches = (
        acquire_trace_batch(capability, (request,), adapter),
        acquire_trace_batch(capability, (request, request), adapter),
    )

    metrics = derive_runtime_acquisition_metrics(batches)

    assert metrics.logical_xic_request_count == 3
    assert metrics.xic_batch_count == 2
    assert metrics.adapter_chromatogram_call_count == 0
    assert metrics.trace_point_count == 6


def test_batch_trace_contract_records_observed_backend_call_delta(
    tmp_path: Path,
) -> None:
    capability = mint_trace_acquisition_capability(
        _source(tmp_path),
        adapter_namespace="tests.multi_window_batch_adapter",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    requests = (
        XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),
        XICRequest(mz=142.0611, rt_min=11.0, rt_max=13.0, ppm_tol=20.0),
    )

    class MultiWindowAdapter(_NoRawBatchAdapter):
        def extract_xic_many(self, requests):
            traces = super().extract_xic_many(requests)
            self.raw_chromatogram_call_count += 2
            return traces

    batch = acquire_trace_batch(capability, requests, MultiWindowAdapter())

    assert batch.adapter_chromatogram_call_count == 2


def test_batch_trace_contract_requires_observed_backend_call_counter(
    tmp_path: Path,
) -> None:
    capability = mint_trace_acquisition_capability(
        _source(tmp_path),
        adapter_namespace="tests.unobservable_batch_adapter",
        coordinate_kind=TraceCoordinateKind.RT_ONLY,
    )

    class UnobservableAdapter:
        def extract_xic_many(self, requests):
            return tuple(
                XICTrace.from_arrays(
                    [request.rt_min, request.rt_max],
                    [0.0, 10.0],
                )
                for request in requests
            )

        def scan_window_for_request(self, request):
            return (100, 120)

    with pytest.raises(AcquisitionContractError, match="call counter"):
        acquire_trace_batch(
            capability,
            (XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),),
            UnobservableAdapter(),
        )


def test_batch_trace_contract_rejects_rewound_backend_call_counter(
    tmp_path: Path,
) -> None:
    capability = mint_trace_acquisition_capability(
        _source(tmp_path),
        adapter_namespace="tests.rewound_batch_adapter",
        coordinate_kind=TraceCoordinateKind.RT_ONLY,
    )

    class RewoundAdapter(_NoRawBatchAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.raw_chromatogram_call_count = 2

        def extract_xic_many(self, requests):
            traces = super().extract_xic_many(requests)
            self.raw_chromatogram_call_count = 1
            return traces

    with pytest.raises(AcquisitionContractError, match="monotonic"):
        acquire_trace_batch(
            capability,
            (XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),),
            RewoundAdapter(),
        )


def test_rt_only_capability_does_not_claim_or_query_scan_coordinates(
    tmp_path: Path,
) -> None:
    capability = mint_trace_acquisition_capability(
        _source(tmp_path),
        adapter_namespace="tests.no_raw_batch_adapter",
        coordinate_kind=TraceCoordinateKind.RT_ONLY,
    )
    adapter = _NoRawBatchAdapter()

    batch = acquire_trace_batch(
        capability,
        (XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),),
        adapter,
    )

    assert adapter.scan_calls == 0
    assert batch.traces[0].coordinate.first_scan is None
    assert batch.traces[0].coordinate.last_scan is None


def test_batch_trace_contract_rejects_wrong_result_cardinality(tmp_path: Path) -> None:
    capability = mint_trace_acquisition_capability(
        _source(tmp_path),
        adapter_namespace="tests.invalid_batch_adapter",
        coordinate_kind=TraceCoordinateKind.RT_ONLY,
    )

    class InvalidAdapter(_NoRawBatchAdapter):
        def extract_xic_many(self, requests):
            return ()

    with pytest.raises(AcquisitionContractError, match="exactly one trace"):
        acquire_trace_batch(
            capability,
            (XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),),
            InvalidAdapter(),
        )


def test_batch_trace_contract_rejects_empty_batch(tmp_path: Path) -> None:
    capability = mint_trace_acquisition_capability(
        _source(tmp_path),
        adapter_namespace="tests.no_raw_batch_adapter",
        coordinate_kind=TraceCoordinateKind.RT_ONLY,
    )

    with pytest.raises(AcquisitionContractError, match="non-empty"):
        acquire_trace_batch(capability, (), _NoRawBatchAdapter())


def test_batch_trace_contract_rejects_nonfinite_trace_values(tmp_path: Path) -> None:
    capability = mint_trace_acquisition_capability(
        _source(tmp_path),
        adapter_namespace="tests.invalid_batch_adapter",
        coordinate_kind=TraceCoordinateKind.RT_ONLY,
    )

    class InvalidAdapter(_NoRawBatchAdapter):
        def extract_xic_many(self, requests):
            return (XICTrace.from_arrays([8.0], [np.nan]),)

    with pytest.raises(AcquisitionContractError, match="finite"):
        acquire_trace_batch(
            capability,
            (XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),),
            InvalidAdapter(),
        )


@pytest.mark.parametrize(
    "trace",
    (
        XICTrace(np.array([8.0, 9.0]), np.array([1.0])),
        XICTrace(np.array([[8.0, 9.0]]), np.array([[1.0, 2.0]])),
    ),
)
def test_batch_trace_contract_rejects_mismatched_or_multidimensional_arrays(
    tmp_path: Path,
    trace: XICTrace,
) -> None:
    capability = mint_trace_acquisition_capability(
        _source(tmp_path),
        adapter_namespace="tests.invalid_shape_batch_adapter",
        coordinate_kind=TraceCoordinateKind.RT_ONLY,
    )

    class InvalidAdapter(_NoRawBatchAdapter):
        def extract_xic_many(self, requests):
            return (trace,)

    with pytest.raises(AcquisitionContractError, match="matching one-dimensional"):
        acquire_trace_batch(
            capability,
            (XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),),
            InvalidAdapter(),
        )


def _source(tmp_path: Path):
    path = tmp_path / "source.fixture"
    path.write_bytes(b"no-raw-source")
    return build_acquisition_source_identity(
        path,
        acquisition_format="fixture",
        observation_namespace="package0:no-raw",
    )
