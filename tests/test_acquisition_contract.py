import json
import pickle
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import xic_extractor.acquisition_contract as acquisition_contract_module
from xic_extractor.acquisition_contract import (
    AcquisitionContractError,
    TraceCoordinate,
    TraceCoordinateKind,
    acquire_trace_batch,
    acquired_trace_content_id,
    deserialize_acquired_trace,
    mint_raw_acquisition_session_capability,
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


def test_raw_session_capability_is_unique_and_pickleable(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)

    first = mint_raw_acquisition_session_capability(source)
    second = mint_raw_acquisition_session_capability(source)

    assert first != second
    assert first.acquisition_source == source
    assert pickle.loads(pickle.dumps(first)) == first


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
        trace.coordinate.acquisition_source_id == source.acquisition_source_id
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
        expected_acquisition_source_id=source.acquisition_source_id,
        expected_trace_coordinate_id=acquired.coordinate.trace_coordinate_id,
        expected_trace_content_id=acquired_trace_content_id(acquired),
    )

    assert restored.coordinate == acquired.coordinate
    assert np.array_equal(restored.trace.rt, acquired.trace.rt)
    assert np.array_equal(restored.trace.intensity, acquired.trace.intensity)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["coordinate"].__setitem__(
            "acquisition_source_id", "cross-source"
        ),
        lambda value: value["coordinate"]["request"].__setitem__(
            "mz", float(999.0).hex()
        ),
        lambda value: value["coordinate"].__setitem__("trace_point_count", 1),
        lambda value: value["trace"]["rt"].__setitem__(0, float(9.0).hex()),
        lambda value: value.__setitem__(
            "acquired_trace_content_id", "xic.acquired-trace-content:" + "0" * 64
        ),
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
    with pytest.raises(ValueError, match="TraceAcquisitionBatch receipts"):
        derive_runtime_acquisition_metrics(  # type: ignore[arg-type]
            ({"logical_xic_request_count": 999},)
        )


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


def test_acquisition_batch_rejects_coordinate_source_or_kind_substitution(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="tests.no_raw_batch_adapter",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    request = XICRequest(mz=258.1085, rt_min=8.0, rt_max=10.0, ppm_tol=20.0)
    acquisition = acquire_trace_batch(capability, (request,), _NoRawBatchAdapter())
    acquired = acquisition.traces[0]
    other_path = tmp_path / "other.fixture"
    other_path.write_bytes(b"no-raw-other-source")
    other_source = build_acquisition_source_identity(
        other_path,
        acquisition_format="fixture",
        observation_namespace="package0:no-raw",
    )

    wrong_source = _replace_coordinate(
        acquired.coordinate,
        acquisition_source_id=other_source.acquisition_source_id,
    )
    with pytest.raises(AcquisitionContractError, match="capability source"):
        replace(
            acquisition,
            traces=(replace(acquired, coordinate=wrong_source),),
            logical_xic_request_count=1,
            trace_point_count=len(acquired.trace.rt),
        )

    wrong_kind = _replace_coordinate(
        acquired.coordinate,
        coordinate_kind=TraceCoordinateKind.CONVERSION_SCAN,
    )
    with pytest.raises(AcquisitionContractError, match="coordinate kind"):
        replace(
            acquisition,
            traces=(replace(acquired, coordinate=wrong_kind),),
            logical_xic_request_count=1,
            trace_point_count=len(acquired.trace.rt),
        )


def _source(tmp_path: Path):
    path = tmp_path / "source.fixture"
    path.write_bytes(b"no-raw-source")
    return build_acquisition_source_identity(
        path,
        acquisition_format="fixture",
        observation_namespace="package0:no-raw",
    )


def _replace_coordinate(
    coordinate: TraceCoordinate,
    *,
    acquisition_source_id: str | None = None,
    coordinate_kind: TraceCoordinateKind | None = None,
) -> TraceCoordinate:
    source_id = acquisition_source_id or coordinate.acquisition_source_id
    kind = coordinate_kind or coordinate.coordinate_kind
    basis = acquisition_contract_module._trace_coordinate_basis(
        capability_id=coordinate.capability_id,
        acquisition_source_id=source_id,
        request=coordinate.request,
        coordinate_kind=kind,
        first_scan=coordinate.first_scan,
        last_scan=coordinate.last_scan,
        trace_point_count=coordinate.trace_point_count,
    )
    return TraceCoordinate(
        trace_coordinate_id=acquisition_contract_module._typed_id(
            "xic.trace-coordinate:",
            basis,
        ),
        capability_id=coordinate.capability_id,
        acquisition_source_id=source_id,
        request=coordinate.request,
        coordinate_kind=kind,
        first_scan=coordinate.first_scan,
        last_scan=coordinate.last_scan,
        trace_point_count=coordinate.trace_point_count,
    )
