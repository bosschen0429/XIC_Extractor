from pathlib import Path

import pytest

import xic_extractor.execution_runtime_owner as owner_mod
import xic_extractor.execution_runtime_receipts as receipts_mod
from tests.test_execution_runtime_owner import _attempt
from xic_extractor.execution_runtime_finalization import (
    RuntimeFinalizationContractError,
)
from xic_extractor.execution_runtime_receipts import (
    RuntimeArtifactIoSample,
    RuntimeCancellationRequest,
    RuntimeExecutionPhase,
    RuntimeExecutionPhaseError,
    derive_runtime_raw_open_metrics,
)


def test_reexport_identity() -> None:
    """Symbols re-exported by execution_runtime_owner must be the exact same object."""
    exported_symbols = (
        "RuntimeAcquisitionMetrics",
        "RuntimeAcquisitionReceipt",
        "RuntimeArtifactIoSample",
        "RuntimeArtifactRegistrationReceipt",
        "RuntimeCancellationRequest",
        "RuntimeExecutionPhase",
        "RuntimeExecutionPhaseError",
        "RuntimeFinalizationPersister",
        "RuntimeFinalizationReceipt",
        "RuntimePeakFactCostMetrics",
        "RuntimePeakFactIdentityStatus",
        "RuntimePeakFactOperation",
        "RuntimePeakFactWorkReceipt",
        "RuntimeProcessTreePeakRssSample",
        "RuntimeRawOpenMetrics",
        "RuntimeResourceSettlementReceipt",
        "RuntimeTelemetryProvider",
        "RuntimeTelemetrySamples",
        "RuntimeWorkerBatchReceipt",
        "RuntimeWorkerBatchProvider",
        "RuntimeWorkerPeakRssSample",
        "accept_runtime_finalization_receipt",
        "compact_runtime_acquisition_receipt",
        "derive_runtime_acquisition_metrics",
        "derive_runtime_peak_fact_cost_metrics",
        "derive_runtime_raw_open_metrics",
    )
    for sym in exported_symbols:
        assert getattr(owner_mod, sym) is getattr(receipts_mod, sym), f"{sym} mismatch"


def test_runtime_cancellation_request(tmp_path: Path) -> None:
    att = _attempt(tmp_path)
    req = RuntimeCancellationRequest(attempt=att)
    assert req.attempt == att

    with pytest.raises(RuntimeFinalizationContractError):
        RuntimeCancellationRequest(attempt="invalid")  # type: ignore[arg-type]


def test_runtime_execution_phase_error(tmp_path: Path) -> None:
    att = _attempt(tmp_path)
    err = RuntimeExecutionPhaseError(att, RuntimeExecutionPhase.WORKER_BATCH_PROVIDER)
    assert err.attempt == att
    assert err.phase == RuntimeExecutionPhase.WORKER_BATCH_PROVIDER
    assert "worker_batch_provider" in str(err)

    with pytest.raises(RuntimeFinalizationContractError):
        RuntimeExecutionPhaseError(
            "invalid",  # type: ignore[arg-type]
            RuntimeExecutionPhase.WORKER_BATCH_PROVIDER,
        )


def test_runtime_artifact_io_sample() -> None:
    sample = RuntimeArtifactIoSample(
        read_count=10,
        read_bytes=1000,
        write_count=5,
        write_bytes=500,
    )
    assert sample.read_count == 10
    assert sample.write_bytes == 500

    with pytest.raises(RuntimeFinalizationContractError):
        RuntimeArtifactIoSample(
            read_count=-1,
            read_bytes=1000,
            write_count=5,
            write_bytes=500,
        )


def test_raw_open_metrics_derivation() -> None:
    metrics = derive_runtime_raw_open_metrics(None, ())
    assert metrics.raw_open_count is None
    assert "carries no owner session capability" in metrics.unavailable_reason
