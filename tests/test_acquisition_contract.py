from pathlib import Path

import numpy as np
import pytest

from xic_extractor.acquisition_contract import (
    AcquisitionContractError,
    TraceCoordinateKind,
    acquire_trace_batch,
    mint_trace_acquisition_capability,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.xic_models import XICRequest, XICTrace


class _NoRawBatchAdapter:
    def __init__(self) -> None:
        self.batch_calls = 0
        self.scan_calls = 0

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
    assert batch.adapter_chromatogram_call_count == 1
    assert batch.trace_point_count == 4
    assert [trace.coordinate.first_scan for trace in batch.traces] == [100, 100]
    assert all(
        trace.coordinate.acquisition_source_id == source.acquisition_source_id
        for trace in batch.traces
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


def _source(tmp_path: Path):
    path = tmp_path / "source.fixture"
    path.write_bytes(b"no-raw-source")
    return build_acquisition_source_identity(
        path,
        acquisition_format="fixture",
        observation_namespace="package0:no-raw",
    )
