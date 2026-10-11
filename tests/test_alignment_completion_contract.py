"""Closed public contract for shared Target/Untarget alignment composition."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import xic_extractor.alignment.completion as workflow_owner
import xic_extractor.applications.targeted.production_workflow as production_workflow
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from tests.test_search_scope_capture import _capture_binding, _peak_config
from xic_extractor.alignment.completion import (
    CaptureWorkflowArguments,
    TargetWorkflowAdapter,
    TargetWorkflowRequest,
    UntargetWorkflowAdapter,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.peak_detection.capture import capture_peak_hypotheses
from xic_extractor.peak_detection.cid_nl_method_subject import CID_NL_PROVIDER_ID
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    TargetSearchScope,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    resolve_request_acquisition_source,
)


def _target_scope_and_captures(fixture, spine, tmp_path: Path):
    method_subject = spine.cells[0].group_context.method_subject
    rt = np.linspace(7.7, 8.3, 61)
    candidate_ids = tuple(
        f"workflow-final:{sample.lookup_token}" for sample in fixture.samples
    )
    bindings = []
    traces = []
    for candidate_id, sample in zip(candidate_ids, fixture.samples, strict=True):
        source = resolve_request_acquisition_source(
            processing_owners=fixture.owners,
            sample=sample,
        )
        bindings.append(
            _capture_binding(
                candidate_id=candidate_id,
                method_subject_id=method_subject.method_subject_id,
                sample=sample,
                acquisition_source_id=source.acquisition_source_id,
                mz=method_subject.precursor_mz,
                ppm_tol=method_subject.precursor_tolerance_ppm,
                rt_min=float(rt[0]),
                rt_max=float(rt[-1]),
            )
        )
        intensity = np.zeros_like(rt)
        traces.append(
            Trace.from_arrays(
                sample_name=sample.metadata.sample_name,
                mz=method_subject.precursor_mz,
                rt=rt,
                intensity=intensity,
                rt_min=float(rt[0]),
                rt_max=float(rt[-1]),
                ppm_tol=method_subject.precursor_tolerance_ppm,
                source="alignment_workflow_contract_test",
                acquisition_source_id=source.acquisition_source_id,
            )
        )
    scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=fixture.request,
        samples=fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=_peak_config(tmp_path),
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=candidate_ids,
        capture_bindings=tuple(bindings),
        target_snapshot=fixture.target.snapshot,
        target=fixture.target,
    )
    return scope, tuple(
        capture_peak_hypotheses(
            scope=scope,
            candidate_id=candidate_id,
            trace=trace,
        )
        for candidate_id, trace in zip(candidate_ids, traces, strict=True)
    )


def test_public_workflow_carriers_reject_non_exact_mutable_inputs() -> None:
    class CandidateId(str):
        pass

    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match="capture candidate_id must be exact text",
    ):
        CaptureWorkflowArguments(
            CandidateId("candidate"),
            object.__new__(workflow_owner.Trace),
        )

    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match="Target request scope must be exact",
    ):
        TargetWorkflowRequest(object(), object(), object())

    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match="capture inputs must be an immutable typed tuple",
    ):
        TargetWorkflowAdapter(
            capture_inputs=[],
            predecessor_cell_observations=(),
            sample_assessments=(),
            alignment_config=AlignmentConfig(),
        )

    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match="Untarget adapter requires closed cell coverage",
    ):
        UntargetWorkflowAdapter(
            capture_inputs=(),
            predecessor_cell_observations=(),
            sample_assessments=(),
        )


@pytest.mark.parametrize(
    "mismatch",
    [
        "capture-subset",
        "capture-order",
        "crossed-request",
        "crossed-attempt",
        "missing-assessment",
        "crossed-sample",
        "unknown-sample",
        "assessment-order",
    ],
)
def test_workflow_rejects_incomplete_or_crossed_owner_population(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mismatch: str,
) -> None:
    from tests.test_cell_observations import _owner_handoff

    fixture, spine, handoff = _owner_handoff(tmp_path)
    coverage = handoff.coverage
    scope, captures = _target_scope_and_captures(fixture, spine, tmp_path)
    if mismatch == "capture-subset":
        scope = replace(
            scope,
            declared_candidate_ids=(
                *scope.declared_candidate_ids,
                "inspection-only:candidate",
            ),
        )
    runtime = SimpleNamespace(
        manifest=SimpleNamespace(attempt=fixture.attempt),
        history=SimpleNamespace(attempt=fixture.attempt),
    )
    request = TargetWorkflowRequest(
        scope=scope,
        processing_request=fixture.request,
        processing_attempt=fixture.attempt,
    )
    if mismatch in {"crossed-request", "crossed-attempt"}:
        (tmp_path / "other").mkdir()
        other = build_processing_identity_no_raw_fixture(
            tmp_path / "other", sample_stems=("Other",)
        )
        if mismatch == "crossed-request":
            request = replace(request, processing_request=other.request)
        else:
            request = replace(request, processing_attempt=other.attempt)
    if mismatch == "capture-order":
        captures = tuple(reversed(captures))
    assessments = tuple(
        sorted(
            zip(fixture.samples, spine.assessments, strict=True),
            key=lambda item: item[0].identity.sample_identity_id,
        )
    )
    if mismatch == "missing-assessment":
        assessments = assessments[:-1]
    elif mismatch == "crossed-sample":
        assessments = (
            (replace(assessments[0][0]), assessments[0][1]),
            *assessments[1:],
        )
    elif mismatch == "unknown-sample":
        (tmp_path / "other").mkdir()
        other = build_processing_identity_no_raw_fixture(
            tmp_path / "other", sample_stems=("Unknown",)
        )
        assessments = tuple(
            sorted(
                (*assessments, (other.samples[0], assessments[0][1])),
                key=lambda pair: pair[0].identity.sample_identity_id,
            )
        )
    elif mismatch == "assessment-order":
        assessments = tuple(reversed(assessments))
    adapter = TargetWorkflowAdapter(
        capture_inputs=tuple(
            CaptureWorkflowArguments(capture.candidate_id, capture.trace)
            for capture in captures
        ),
        sample_assessments=assessments,
        alignment_config=coverage.epoch.alignment_config,
        predecessor_cell_observations=spine.cells,
        drift_lookup=None,
    )

    class RuntimeReached(Exception):
        pass

    def stop_at_runtime(*_args, **_kwargs):
        raise RuntimeReached

    monkeypatch.setattr(production_workflow, "execute_runtime_attempt", stop_at_runtime)

    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match=(
            "declared candidate order"
            if mismatch in {"capture-subset", "capture-order"}
            else "crossed processing request or attempt"
            if mismatch in {"crossed-request", "crossed-attempt"}
            else "exact scope samples"
        ),
    ):
        production_workflow._complete_retained_target(
            request,
            adapter,
            runtime=runtime,
            workflow_artifact=object(),
            work_unit_key="retained",
        )
