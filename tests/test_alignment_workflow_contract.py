"""Closed public contract for shared Target/Untarget alignment composition."""

from __future__ import annotations

from dataclasses import fields
from inspect import signature
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest

import xic_extractor.alignment.workflow as workflow_owner
from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
from xic_extractor.alignment.workflow import (
    CancelledWorkflowResult,
    CaptureWorkflowArguments,
    CompletedTargetWorkflowResult,
    CompletedUntargetWorkflowResult,
    FailedWorkflowResult,
    ModeTrackWorkflowArguments,
    ObservationRepairWorkflowArguments,
    TargetWorkflowAdapter,
    TargetWorkflowRequest,
    UntargetWorkflowAdapter,
    UntargetWorkflowRequest,
    WorkflowRuntimeInvocation,
    WorkflowStage,
    WorkflowTerminalResult,
    execute_alignment_workflow,
)
from xic_extractor.processing_identity import issue_processing_attempt


def test_public_workflow_surface_is_closed_and_variant_specific() -> None:
    assert tuple(signature(execute_alignment_workflow).parameters) == (
        "request",
        "adapter",
    )
    assert {stage.value for stage in WorkflowStage} == {
        "runtime",
        "capture",
        "mode_track_bootstrap",
        "cross_sample_mt0",
        "observation_repair",
        "cross_sample_repair",
        "mode_track_mt1",
        "cross_sample_final",
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
    )
    assert tuple(field.name for field in fields(ModeTrackWorkflowArguments)) == (
        "sample_assessments",
        "alignment_config",
        "trajectory_policy",
    )
    assert tuple(
        field.name for field in fields(ObservationRepairWorkflowArguments)
    ) == ("coverage", "cell_observations", "execute")
    common_adapter_fields = (
        "capture_inputs",
        "mode_track",
        "repair",
        "predecessor_cell_observations",
        "drift_lookup",
    )
    assert tuple(field.name for field in fields(TargetWorkflowAdapter)) == (
        common_adapter_fields
    )
    assert tuple(field.name for field in fields(UntargetWorkflowAdapter)) == (
        common_adapter_fields
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
        TargetWorkflowAdapter([], object(), object(), (), None)

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


def test_workflow_accepts_capture_binding_subset_of_declared_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("sample-a",))
    sample = fixture.samples[0]
    scope = object.__new__(workflow_owner.TargetSearchScope)
    object.__setattr__(scope, "request", fixture.request)
    object.__setattr__(scope, "samples", fixture.samples)
    object.__setattr__(
        scope,
        "declared_candidate_ids",
        ("capture:candidate", "inspection-only:candidate"),
    )
    object.__setattr__(
        scope,
        "capture_bindings",
        (SimpleNamespace(candidate_id="capture:candidate"),),
    )
    request = object.__new__(TargetWorkflowRequest)
    object.__setattr__(request, "scope", scope)
    object.__setattr__(request, "processing_request", fixture.request)
    object.__setattr__(request, "processing_attempt", fixture.attempt)
    object.__setattr__(request, "runtime", object())
    adapter = object.__new__(TargetWorkflowAdapter)
    object.__setattr__(
        adapter,
        "capture_inputs",
        (SimpleNamespace(candidate_id="capture:candidate"),),
    )
    object.__setattr__(
        adapter,
        "mode_track",
        SimpleNamespace(sample_assessments=((sample, object()),)),
    )
    object.__setattr__(
        adapter,
        "repair",
        SimpleNamespace(
            coverage=SimpleNamespace(
                epoch=SimpleNamespace(
                    context=SimpleNamespace(processing_owners=fixture.owners)
                )
            )
        ),
    )
    object.__setattr__(adapter, "predecessor_cell_observations", ())

    monkeypatch.setattr(TargetWorkflowRequest, "__post_init__", lambda self: None)
    monkeypatch.setattr(TargetWorkflowAdapter, "__post_init__", lambda self: None)
    monkeypatch.setattr(
        workflow_owner,
        "_validate_runtime_attempt_binding",
        lambda invocation, attempt: None,
    )
    monkeypatch.setattr(
        workflow_owner,
        "validate_scope_owner_binding",
        lambda supplied_scope, *, processing_owners, sample: None,
    )

    assert workflow_owner._validate_request_adapter(request, adapter) is fixture.owners


def test_workflow_runtime_rejects_a_crossed_processing_attempt(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("sample-a",))
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
