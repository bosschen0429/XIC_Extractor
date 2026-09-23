import concurrent.futures
import multiprocessing
import pickle
from dataclasses import replace

import pytest

from tests.alignment.identity_coherence.output_fixtures import output_record
from xic_extractor.alignment.identity_coherence.models import (
    CandidateTrace,
    IdentityCoherenceResult,
    IdentityCoherenceTraceRequest,
    IdentityCoherenceTraceResult,
)
from xic_extractor.alignment.identity_coherence.process_payload import (
    identity_coherence_trace_payload_smoke_worker,
)


def _trace_request() -> IdentityCoherenceTraceRequest:
    return IdentityCoherenceTraceRequest(
        decision_id="DEC-1",
        request_id="REQ-1",
        sample_id="RAW-2",
        candidate_id="CAND-2",
        precursor_mz=500.0,
        ppm_tolerance=10.0,
        rt_min=4.0,
        rt_max=6.0,
    )


def test_trace_request_rejects_empty_identifiers() -> None:
    with pytest.raises(ValueError, match="decision_id must be non-empty"):
        IdentityCoherenceTraceRequest(
            decision_id="",
            request_id="REQ-1",
            sample_id="RAW-2",
            candidate_id="CAND-2",
            precursor_mz=500.0,
            ppm_tolerance=10.0,
            rt_min=4.0,
            rt_max=6.0,
        )


def test_trace_request_rejects_nonfinite_or_invalid_windows() -> None:
    with pytest.raises(ValueError, match="precursor_mz must be finite positive"):
        replace(_trace_request(), precursor_mz=float("nan"))

    with pytest.raises(ValueError, match="ppm_tolerance must be finite positive"):
        replace(_trace_request(), ppm_tolerance=0.0)

    with pytest.raises(ValueError, match="rt_min must be <= rt_max"):
        replace(_trace_request(), rt_min=7.0, rt_max=6.0)


def test_trace_result_rejects_inconsistent_trace_point_count() -> None:
    trace = CandidateTrace(rt_min=(4.0, 5.0), intensity=(10.0, 20.0))

    with pytest.raises(ValueError, match="xic_point_count must equal trace length"):
        IdentityCoherenceTraceResult(
            request=_trace_request(),
            trace=trace,
            status="pass",
            raw_xic_request_count=1,
            raw_chromatogram_call_count=1,
            xic_point_count=3,
            elapsed_sec=0.0,
        )


def test_trace_result_rejects_point_count_without_trace() -> None:
    with pytest.raises(ValueError, match="xic_point_count must be 0"):
        IdentityCoherenceTraceResult(
            request=_trace_request(),
            trace=None,
            status="not_assessed",
            raw_xic_request_count=0,
            raw_chromatogram_call_count=0,
            xic_point_count=5,
            elapsed_sec=0.0,
        )


def test_trace_result_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="unsupported trace result status"):
        IdentityCoherenceTraceResult(
            request=_trace_request(),
            trace=None,
            status="backfilled",
            raw_xic_request_count=0,
            raw_chromatogram_call_count=0,
            xic_point_count=0,
            elapsed_sec=0.0,
        )


def test_trace_result_rejects_contradictory_status_and_blocked_reason() -> None:
    with pytest.raises(ValueError, match="pass status cannot have blocked_reason"):
        IdentityCoherenceTraceResult(
            request=_trace_request(),
            trace=CandidateTrace(rt_min=(4.0, 5.0), intensity=(0.0, 1.0)),
            status="pass",
            blocked_reason="raw_open_failed",
            raw_xic_request_count=1,
            raw_chromatogram_call_count=1,
            xic_point_count=2,
            elapsed_sec=0.0,
        )

    with pytest.raises(ValueError, match="blocked_infrastructure requires"):
        IdentityCoherenceTraceResult(
            request=_trace_request(),
            trace=None,
            status="blocked_infrastructure",
            raw_xic_request_count=0,
            raw_chromatogram_call_count=0,
            xic_point_count=0,
            elapsed_sec=0.0,
        )


def test_identity_coherence_result_rejects_join_mismatches() -> None:
    record = output_record()
    mismatched_decision = replace(
        record.row_result.decision,
        decision_id="OTHER-DECISION",
    )

    with pytest.raises(ValueError, match="decision_id mismatch"):
        IdentityCoherenceResult(
            request=record.seed_gate.resolved_request,
            decision=mismatched_decision,
            cells=record.row_result.cells,
        )


def test_identity_coherence_payloads_are_pickleable() -> None:
    record = output_record()
    result = IdentityCoherenceResult(
        request=record.seed_gate.resolved_request,
        decision=record.row_result.decision,
        cells=record.row_result.cells,
    )
    trace_result = IdentityCoherenceTraceResult(
        request=_trace_request(),
        trace=CandidateTrace(rt_min=(4.0, 5.0), intensity=(0.0, 1.0)),
        status="pass",
        raw_xic_request_count=1,
        raw_chromatogram_call_count=1,
        xic_point_count=2,
        elapsed_sec=0.0,
    )

    assert pickle.loads(pickle.dumps(result)) == result
    assert pickle.loads(pickle.dumps(trace_result)) == trace_result


def test_spawn_round_trips_identity_coherence_trace_payload() -> None:
    context = multiprocessing.get_context("spawn")
    request = _trace_request()

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=1,
        mp_context=context,
    ) as executor:
        future = executor.submit(
            identity_coherence_trace_payload_smoke_worker,
            request,
        )
        result = future.result(timeout=30)

    assert result.request == request
    assert result.status == "pass"
    assert result.raw_xic_request_count == 0
    assert result.raw_chromatogram_call_count == 0
    assert result.xic_point_count == 2
    assert result.trace is not None
    assert result.trace.rt_min == (4.0, 6.0)
    assert result.trace.intensity == (0.0, 0.0)
