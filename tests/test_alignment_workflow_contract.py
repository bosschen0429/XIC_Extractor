"""Closed public contract for shared Target/Untarget alignment composition."""

from __future__ import annotations

from dataclasses import fields, replace
from inspect import signature
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import numpy as np
import pytest

import xic_extractor.alignment.workflow as workflow_owner
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from tests.test_search_scope_capture import _capture_binding, _peak_config
from xic_extractor.alignment.workflow import (
    CancelledWorkflowResult,
    CaptureWorkflowArguments,
    CompletedTargetWorkflowResult,
    CompletedUntargetBatchWorkflowResult,
    CompletedUntargetWorkflowResult,
    FailedWorkflowResult,
    ModeTrackWorkflowArguments,
    TargetWorkflowAdapter,
    TargetWorkflowRequest,
    UntargetWorkflowAdapter,
    UntargetWorkflowRequest,
    WorkflowRuntimeInvocation,
    WorkflowStage,
    WorkflowTerminalResult,
    execute_alignment_workflow,
)
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
    issue_processing_attempt,
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


def test_public_workflow_surface_is_closed_and_variant_specific() -> None:
    assert tuple(signature(execute_alignment_workflow).parameters) == (
        "request",
        "adapter",
    )
    assert {stage.value for stage in WorkflowStage} == {
        "runtime",
        "capture",
        "mode_track_bootstrap",
        "cross_sample_grouping",
        "scientific_final",
        "pre_cell_projection",
        "cell_decision",
        "post_cell_projection",
        "application_result",
    }
    assert tuple(field.name for field in fields(WorkflowRuntimeInvocation)) == (
        "runtime",
        "reservations",
        "worker_batch_provider",
        "telemetry_provider",
        "persister",
    )
    assert tuple(field.name for field in fields(CaptureWorkflowArguments)) == (
        "candidate_id",
        "trace",
        "owner_observation",
        "owner_hypothesis",
    )
    assert tuple(field.name for field in fields(ModeTrackWorkflowArguments)) == (
        "sample_assessments",
        "alignment_config",
        "trajectory_policy",
        "retain_observation_only_candidates",
    )
    common_adapter_fields = (
        "capture_inputs",
        "mode_track",
        "predecessor_cell_observations",
        "drift_lookup",
    )
    assert tuple(field.name for field in fields(TargetWorkflowAdapter)) == (
        common_adapter_fields
    )
    assert tuple(field.name for field in fields(UntargetWorkflowAdapter)) == (
        *common_adapter_fields,
        "completed_grouping",
        "closed_cell_coverage",
    )
    assert tuple(field.name for field in fields(TargetWorkflowRequest)) == (
        "scope",
        "processing_request",
        "processing_attempt",
        "runtime",
    )
    assert tuple(field.name for field in fields(UntargetWorkflowRequest)) == (
        "scope",
        "processing_request",
        "processing_attempt",
        "runtime",
    )
    assert set(get_args(WorkflowTerminalResult)) == {
        CompletedTargetWorkflowResult,
        CompletedUntargetWorkflowResult,
        CompletedUntargetBatchWorkflowResult,
        CancelledWorkflowResult,
        FailedWorkflowResult,
    }


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
        TargetWorkflowRequest(object(), object(), object(), object())

    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match="capture inputs must be an immutable typed tuple",
    ):
        TargetWorkflowAdapter([], object(), (), None)

    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match="owner-minted runtime receipt",
    ):
        CancelledWorkflowResult(object())

    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match="runtime must be an exact attempt",
    ):
        WorkflowRuntimeInvocation(
            runtime=object(),
            reservations=(),
            worker_batch_provider=lambda _value: None,
            telemetry_provider=lambda _value: None,
            persister=lambda value: value,
        )


@pytest.mark.parametrize(
    "mismatch",
    [
        "capture-subset",
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
    from tests.test_backfill_observation_repair_authorization import (
        _authorization_population,
    )

    fixture, spine, coverage, _cells = _authorization_population(
        tmp_path,
        hold_samples=(),
    )
    scope, captures = _target_scope_and_captures(fixture, spine, tmp_path)
    if mismatch == "capture-subset":
        scope = replace(
            scope,
            declared_candidate_ids=(
                *scope.declared_candidate_ids,
                "inspection-only:candidate",
            ),
        )
    runtime = object.__new__(workflow_owner.AttemptRuntime)
    object.__setattr__(runtime, "manifest", SimpleNamespace(attempt=fixture.attempt))
    object.__setattr__(runtime, "history", SimpleNamespace(attempt=fixture.attempt))
    request = TargetWorkflowRequest(
        scope=scope,
        processing_request=fixture.request,
        processing_attempt=fixture.attempt,
        runtime=WorkflowRuntimeInvocation(
            runtime=runtime,
            reservations=(),
            worker_batch_provider=lambda _value: None,
            telemetry_provider=lambda _value: None,
            persister=lambda value: value,
        ),
    )
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
        mode_track=ModeTrackWorkflowArguments(
            sample_assessments=assessments,
            alignment_config=coverage.epoch.alignment_config,
            trajectory_policy=workflow_owner.ModeTrackTrajectoryPolicy(
                trajectory_policy_version="anchor-trajectory",
                band_policy_version="fixed-band",
                prediction_half_width_min=0.1,
                max_interpolation_gap=10,
                max_extrapolation_gap=10,
            ),
        ),
        predecessor_cell_observations=spine.cells,
        drift_lookup=None,
    )

    class RuntimeReached(Exception):
        pass

    def stop_at_runtime(*_args, **_kwargs):
        raise RuntimeReached

    monkeypatch.setattr(workflow_owner, "execute_runtime_attempt", stop_at_runtime)

    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match="declared candidate order"
        if mismatch == "capture-subset"
        else "exact scope samples",
    ):
        execute_alignment_workflow(request, adapter)


def test_workflow_runtime_rejects_a_crossed_processing_attempt(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-a",)
    )
    crossed_attempt = issue_processing_attempt(
        fixture.request,
        retry_of=fixture.attempt,
    )
    crossed_runtime = object.__new__(workflow_owner.AttemptRuntime)
    object.__setattr__(
        crossed_runtime,
        "manifest",
        SimpleNamespace(attempt=crossed_attempt),
    )
    object.__setattr__(
        crossed_runtime,
        "history",
        SimpleNamespace(attempt=crossed_attempt),
    )
    invocation = WorkflowRuntimeInvocation(
        runtime=crossed_runtime,
        reservations=(),
        worker_batch_provider=lambda _aggregate: (),
        telemetry_provider=lambda _aggregate: (),
        persister=lambda value: value,
    )

    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match="runtime crossed the exact processing attempt",
    ):
        workflow_owner._validate_runtime_attempt_binding(
            invocation,
            fixture.attempt,
        )
