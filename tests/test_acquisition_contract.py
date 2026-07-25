import pytest

from xic_extractor.acquisition_contract import (
    AcquisitionCapability,
    AcquisitionContractError,
    TraceCoordinateMode,
    TraceObservation,
    TraceQuery,
    require_scan_identity,
    validate_trace_capability,
    validate_trace_observation,
)
from xic_extractor.processing_lineage import AcquisitionSourceRef


def test_native_scan_observation_preserves_source_namespace() -> None:
    query = _query(TraceCoordinateMode.NATIVE_SCAN)
    capability = AcquisitionCapability(
        source_id=query.source.source_id,
        supported_coordinate_modes=(TraceCoordinateMode.NATIVE_SCAN,),
        source_provenance_verified=True,
    )
    observation = TraceObservation(
        query_id=query.query_id,
        source_id=query.source.source_id,
        coordinate_mode=TraceCoordinateMode.NATIVE_SCAN,
        rt=(1.0, 1.1),
        intensity=(10.0, 20.0),
        observation_ids=(
            "controllerType=0 controllerNumber=1 scan=10",
            "controllerType=0 controllerNumber=1 scan=11",
        ),
    )

    validate_trace_capability(capability, query)
    validate_trace_observation(query, observation)
    assert require_scan_identity(observation) == observation.observation_ids


def test_rt_only_fails_closed_for_scan_identity_requirement() -> None:
    observation = TraceObservation(
        query_id="query-1",
        source_id="source-1",
        coordinate_mode=TraceCoordinateMode.RT_ONLY,
        rt=(1.0,),
        intensity=(10.0,),
    )

    with pytest.raises(AcquisitionContractError, match="scan-identity"):
        require_scan_identity(observation)


def test_capability_rejects_silent_coordinate_fallback() -> None:
    query = _query(TraceCoordinateMode.NATIVE_SCAN)
    capability = AcquisitionCapability(
        source_id=query.source.source_id,
        supported_coordinate_modes=(TraceCoordinateMode.RT_ONLY,),
        source_provenance_verified=True,
    )

    with pytest.raises(AcquisitionContractError, match="does not support"):
        validate_trace_capability(capability, query)


def test_conversion_scan_requires_explicit_provenance() -> None:
    with pytest.raises(AcquisitionContractError, match="conversion provenance"):
        TraceObservation(
            query_id="query-1",
            source_id="source-1",
            coordinate_mode=TraceCoordinateMode.CONVERSION_SCAN,
            rt=(1.0,),
            intensity=(10.0,),
            observation_ids=("scan=10",),
        )


def _query(mode: TraceCoordinateMode) -> TraceQuery:
    return TraceQuery(
        query_id="query-1",
        source=AcquisitionSourceRef(
            source_id="source-1",
            acquisition_format="thermo_raw",
            observation_namespace="controllerType=0 controllerNumber=1",
        ),
        mz=250.0,
        ppm_tol=20.0,
        rt_min=0.5,
        rt_max=1.5,
        coordinate_mode=mode,
        scan_predicate="ms_order=1",
    )
