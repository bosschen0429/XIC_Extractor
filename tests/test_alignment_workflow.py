"""Owner-spine integration for the shared alignment workflow."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import xic_extractor.alignment.workflow as workflow_owner
from xic_extractor.alignment.backfill.observation_repair import (
    ObservationRepairReceipt,
)
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSamplePostRepairModeTrackInput,
)
from xic_extractor.alignment.projection.target.post_cell import (
    TargetPostCellProjection,
)
from xic_extractor.alignment.projection.untarget.post_cell import (
    UntargetPostCellProjection,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedTargetApplicationResult,
    FinalizedUntargetApplicationResult,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
)
from xic_extractor.alignment.workflow import (
    CompletedTargetWorkflowResult,
    CompletedUntargetWorkflowResult,
    FailedWorkflowResult,
    TargetWorkflowAdapter,
    TargetWorkflowRequest,
    UntargetWorkflowAdapter,
    UntargetWorkflowRequest,
    WorkflowStage,
    execute_alignment_workflow,
)
from xic_extractor.execution_runtime import AttemptTerminalOutcome


def _exact_uninitialized(value_type, **fields):
    value = object.__new__(value_type)
    for name, field_value in fields.items():
        object.__setattr__(value, name, field_value)
    return value


@pytest.mark.parametrize("variant", ["target", "untarget"])
@pytest.mark.parametrize("crossed_result", [False, True])
@pytest.mark.parametrize("changed_trajectory", [False, True])
def test_workflow_forwards_exact_owner_objects_once(
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    crossed_result: bool,
    changed_trajectory: bool,
) -> None:
    import xic_extractor.alignment.cell_decision.owner as decision_owner

    calls = []
    owners = object()
    runtime_receipt = SimpleNamespace(
        supervisor=SimpleNamespace(
            terminal_outcome=AttemptTerminalOutcome.SUCCEEDED,
        ),
    )
    request = _exact_uninitialized(
        TargetWorkflowRequest if variant == "target" else UntargetWorkflowRequest,
        scope=object(),
        runtime=SimpleNamespace(
            runtime=object(),
            reservations=(),
            worker_batch_provider=object(),
            telemetry_provider=object(),
            persister=object(),
        ),
    )
    callback_result = _exact_uninitialized(
        OwnerBackfillSampleResult,
        peak_fact_observations=(),
    )
    repair_receipt = _exact_uninitialized(
        ObservationRepairReceipt,
        evidence=None,
    )
    terminal = SimpleNamespace(
        receipt=repair_receipt if changed_trajectory else None,
    )
    repair_arguments = SimpleNamespace(
        coverage=object(),
        cell_observations=(),
        execute=lambda candidate: (
            calls.append(("repair_callback", candidate)) or callback_result
        ),
    )
    adapter = _exact_uninitialized(
        TargetWorkflowAdapter if variant == "target" else UntargetWorkflowAdapter,
        capture_inputs=(SimpleNamespace(candidate_id="candidate:a", trace=object()),),
        mode_track=SimpleNamespace(
            sample_assessments=(),
            alignment_config=object(),
            trajectory_policy=object(),
        ),
        repair=repair_arguments,
        predecessor_cell_observations=(),
        drift_lookup=object(),
    )
    capture = object()
    run = SimpleNamespace(fit_once_source=object())
    context = object()
    if variant != "target":
        object.__setattr__(
            adapter,
            "completed_cross_sample",
            None,
        )
    post_repair = _exact_uninitialized(CrossSamplePostRepairModeTrackInput)
    final_attempt = object()
    final_receipt = _exact_uninitialized(CrossSampleFinalReceipt)
    scientific = _exact_uninitialized(FinalizedScientificAlignment)
    pre_cell = object()
    decisions = object()
    projection = _exact_uninitialized(
        TargetPostCellProjection if variant == "target" else UntargetPostCellProjection
    )
    application_result = _exact_uninitialized(
        FinalizedTargetApplicationResult
        if variant == "target"
        else FinalizedUntargetApplicationResult,
        scientific_core=(
            _exact_uninitialized(FinalizedScientificAlignment)
            if crossed_result
            else scientific
        ),
        completed_projection=projection,
    )

    monkeypatch.setattr(
        workflow_owner,
        "_validate_request_adapter",
        lambda supplied_request, supplied_adapter: (
            calls.append(("validate", supplied_request, supplied_adapter)) or owners
        ),
    )
    monkeypatch.setattr(
        workflow_owner,
        "RuntimeFinalizationReceipt",
        SimpleNamespace,
    )
    monkeypatch.setattr(
        workflow_owner,
        "execute_runtime_attempt",
        lambda *_args, **_kwargs: calls.append(("runtime",)) or runtime_receipt,
    )
    monkeypatch.setattr(
        workflow_owner,
        "capture_peak_hypotheses",
        lambda **kwargs: calls.append(("capture", kwargs)) or capture,
    )
    monkeypatch.setattr(
        workflow_owner,
        "bootstrap_mode_track_refinement_from_cid_nl_support",
        lambda **kwargs: calls.append(("mt0", kwargs)) or run,
    )
    monkeypatch.setattr(
        workflow_owner,
        "apply_mode_track_refinement_run_for_workflow",
        lambda **kwargs: calls.append(("cross_mt0", kwargs)) or context,
    )

    def repair_owner(**kwargs):
        if changed_trajectory:
            candidate = object()
            result = kwargs["execute"](candidate)
            assert result is callback_result
        calls.append(("repair", kwargs))
        return terminal

    monkeypatch.setattr(workflow_owner, "execute_observation_repair", repair_owner)
    monkeypatch.setattr(
        workflow_owner,
        "consume_observation_repair_terminal",
        lambda **kwargs: (
            calls.append(("cross_repair", kwargs))
            or (post_repair if changed_trajectory else final_receipt)
        ),
    )
    monkeypatch.setattr(
        workflow_owner,
        "run_post_repair_mode_track_final_attempt",
        lambda **kwargs: calls.append(("mt1", kwargs)) or final_attempt,
    )
    monkeypatch.setattr(
        workflow_owner,
        "consume_mode_track_final_attempt",
        lambda **kwargs: calls.append(("cross_final", kwargs)) or final_receipt,
    )
    monkeypatch.setattr(
        workflow_owner,
        "build_cross_sample_association_lineage_handoff",
        lambda **kwargs: calls.append(("association", kwargs)) or object(),
    )
    monkeypatch.setattr(
        workflow_owner,
        "finalize_scientific_alignment",
        lambda **kwargs: calls.append(("scientific", kwargs)) or scientific,
    )
    monkeypatch.setattr(
        workflow_owner,
        "project_target_pre_cell",
        lambda value: pre_cell,
    )
    monkeypatch.setattr(
        workflow_owner,
        "project_untarget_pre_cell",
        lambda value: pre_cell,
    )
    monkeypatch.setattr(
        decision_owner,
        "decide_target_cell_population",
        lambda value: decisions,
    )
    monkeypatch.setattr(
        decision_owner,
        "decide_group_cell_population",
        lambda before, scientific_core: decisions,
    )
    monkeypatch.setattr(
        workflow_owner,
        "project_target_post_cell",
        lambda before, after: projection,
    )
    monkeypatch.setattr(
        workflow_owner,
        "project_untarget_post_cell",
        lambda before, after: projection,
    )
    monkeypatch.setattr(
        workflow_owner,
        "finalize_alignment_result",
        lambda value: application_result,
    )

    result = execute_alignment_workflow(request, adapter)

    if crossed_result:
        assert type(result) is FailedWorkflowResult
        assert result.stage is WorkflowStage.APPLICATION_RESULT
        assert result.failure_type == (
            "xic_extractor.alignment.workflow.AlignmentWorkflowContractError"
        )
    else:
        assert type(result) is (
            CompletedTargetWorkflowResult
            if variant == "target"
            else CompletedUntargetWorkflowResult
        )
        assert result.application_result is application_result
    assert result.runtime_receipt is runtime_receipt
    expected_calls = [
        "validate",
        "runtime",
        "capture",
    ]
    expected_calls.extend(("mt0", "cross_mt0"))
    if changed_trajectory:
        expected_calls.append("repair_callback")
    expected_calls.extend(("repair", "cross_repair"))
    if changed_trajectory:
        expected_calls.extend(("mt1", "cross_final"))
    expected_calls.extend(("association", "scientific"))
    assert [item[0] for item in calls] == expected_calls
    cross_repair_kwargs = next(item[1] for item in calls if item[0] == "cross_repair")
    assert cross_repair_kwargs["context"] is context
    assert cross_repair_kwargs["terminal"] is terminal
    assert cross_repair_kwargs["repair_result"] is (
        callback_result if changed_trajectory else None
    )
    if changed_trajectory:
        assert (
            next(item[1] for item in calls if item[0] == "mt1")["source"]
            is run.fit_once_source
        )
        cross_final_kwargs = next(item[1] for item in calls if item[0] == "cross_final")
        assert cross_final_kwargs["terminal"] is terminal
        assert cross_final_kwargs["post_repair"] is post_repair
        assert cross_final_kwargs["final_attempt"] is final_attempt
    else:
        scientific_kwargs = next(item[1] for item in calls if item[0] == "scientific")
        assert scientific_kwargs["repair_receipts"] == ()
        assert scientific_kwargs["repaired_observations"] == ()
