"""Owner-spine integration for the shared alignment workflow."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import xic_extractor.alignment.workflow as workflow_owner
from xic_extractor.alignment.grouping_result import (
    CrossSampleGroupingResult,
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
def test_workflow_forwards_exact_owner_objects_once(
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    crossed_result: bool,
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
    adapter_fields = {
        "capture_inputs": (
            SimpleNamespace(candidate_id="candidate:a", trace=object()),
        ),
        "predecessor_cell_observations": (),
        "backfill_cell_observations": (),
        "drift_lookup": object(),
    }
    if variant == "target":
        adapter_fields.update(sample_assessments=(), alignment_config=object())
    else:
        adapter_fields["mode_track"] = SimpleNamespace(
            sample_assessments=(),
            alignment_config=object(),
            trajectory_policy=object(),
            retain_observation_only_candidates=False,
        )
    adapter = _exact_uninitialized(
        TargetWorkflowAdapter if variant == "target" else UntargetWorkflowAdapter,
        **adapter_fields,
    )
    closed_coverage = SimpleNamespace(epoch=SimpleNamespace(processing_owners=owners))
    if variant != "target":
        object.__setattr__(adapter, "closed_cell_coverage", closed_coverage)
    capture = object()
    run = SimpleNamespace(fit_once_source=object())
    context = _exact_uninitialized(CrossSampleGroupingResult)
    if variant != "target":
        object.__setattr__(
            adapter,
            "completed_grouping",
            None,
        )
    final_receipt = context
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
    monkeypatch.setattr(
        workflow_owner,
        "build_targeted_cross_sample_groups",
        lambda **kwargs: calls.append(("target_grouping", kwargs)) or context,
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
        "_decide_fresh_group_cell_population",
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
        "finalize_alignment_result"
        if variant == "target"
        else "_finalize_fresh_untarget_result",
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
    expected_calls.extend(
        ("target_grouping",) if variant == "target" else ("mt0", "cross_mt0")
    )
    if variant != "target":
        expected_calls.append("association")
    expected_calls.append("scientific")
    assert [item[0] for item in calls] == expected_calls
    scientific_kwargs = next(item[1] for item in calls if item[0] == "scientific")
    assert scientific_kwargs["grouping_result"] is final_receipt
    assert scientific_kwargs["predecessor_cell_observations"] == ()
    if variant == "target":
        assert scientific_kwargs["cell_observation_handoff"] is None
    else:
        association_kwargs = next(item[1] for item in calls if item[0] == "association")
        assert association_kwargs["coverage"] is closed_coverage
        assert association_kwargs["final_receipt"] is final_receipt
