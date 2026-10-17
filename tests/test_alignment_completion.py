"""Exact scientific-owner handoffs for pure shared completion."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import xic_extractor.alignment.completion as workflow_owner
from xic_extractor.alignment.completion import (
    TargetWorkflowAdapter,
    TargetWorkflowRequest,
    UntargetWorkflowAdapter,
    UntargetWorkflowCompositionRequest,
    WorkflowStage,
    complete_analysis,
)
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.projection.target.post_cell import (
    TargetPostCellProjection,
)
from xic_extractor.alignment.projection.untarget.matrix import (
    UntargetMatrixProjection,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedTargetApplicationResult,
    FinalizedUntargetApplicationResult,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
)


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
    request = _exact_uninitialized(
        TargetWorkflowRequest
        if variant == "target"
        else UntargetWorkflowCompositionRequest,
        scope=object(),
    )
    adapter_fields = {
        "capture_inputs": (
            SimpleNamespace(candidate_id="candidate:a", trace=object()),
        ),
        "predecessor_cell_observations": (),
        "backfill_cell_observations": (),
        "rt_frame": object(),
    }
    if variant == "target":
        adapter_fields.update(sample_assessments=(), alignment_config=object())
    else:
        adapter_fields["sample_assessments"] = ()
    adapter = _exact_uninitialized(
        TargetWorkflowAdapter if variant == "target" else UntargetWorkflowAdapter,
        **adapter_fields,
    )
    closed_coverage = SimpleNamespace(epoch=SimpleNamespace(processing_owners=owners))
    if variant != "target":
        object.__setattr__(adapter, "closed_cell_coverage", closed_coverage)
    capture = object()
    context = _exact_uninitialized(CrossSampleGroupingResult)
    if variant != "target":
        object.__setattr__(adapter, "completed_grouping", context)
    final_receipt = context
    scientific = _exact_uninitialized(FinalizedScientificAlignment)
    pre_cell = object()
    decisions = object()
    projection = _exact_uninitialized(
        TargetPostCellProjection if variant == "target" else UntargetMatrixProjection
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
        "validate_completion_inputs",
        lambda supplied_request, supplied_adapter: (
            calls.append(("validate", supplied_request, supplied_adapter)) or owners
        ),
    )
    monkeypatch.setattr(
        workflow_owner,
        "capture_peak_hypotheses",
        lambda **kwargs: calls.append(("capture", kwargs)) or capture,
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
        decision_owner,
        "decide_target_cell_population",
        lambda value: decisions,
    )
    monkeypatch.setattr(
        decision_owner,
        "decide_group_cell_population",
        lambda scientific_core: decisions,
    )
    monkeypatch.setattr(
        workflow_owner,
        "project_target_post_cell",
        lambda before, after: projection,
    )
    monkeypatch.setattr(
        workflow_owner,
        "project_untarget_matrix",
        lambda before, after: projection,
    )
    monkeypatch.setattr(
        workflow_owner,
        "finalize_alignment_result"
        if variant == "target"
        else "_finalize_fresh_untarget_result",
        lambda value: application_result,
    )

    if crossed_result:
        with pytest.raises(workflow_owner.WorkflowStageFailure) as failure:
            complete_analysis(request, adapter)
        assert failure.value.stage is WorkflowStage.APPLICATION_RESULT
        assert (
            type(failure.value.error) is workflow_owner.AlignmentWorkflowContractError
        )
        assert "crossed" in str(failure.value.error)
    else:
        assert complete_analysis(request, adapter) is application_result
    expected_calls = [
        "validate",
        "capture",
    ]
    expected_calls.append("target_grouping" if variant == "target" else "association")
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
