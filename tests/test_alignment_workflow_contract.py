"""Closed public contract for shared Target/Untarget alignment composition."""

from __future__ import annotations

from dataclasses import fields
from inspect import signature

import pytest

import xic_extractor.alignment.workflow as workflow_owner
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
    execute_alignment_workflow,
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
    assert {
        CompletedTargetWorkflowResult,
        CompletedUntargetWorkflowResult,
        CancelledWorkflowResult,
        FailedWorkflowResult,
    }


def test_public_workflow_carriers_reject_non_exact_mutable_inputs() -> None:
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
