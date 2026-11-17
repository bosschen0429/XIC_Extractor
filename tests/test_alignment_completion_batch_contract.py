"""Pure Untarget subject composition and its failure-stage contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import xic_extractor.alignment.completion as workflow_owner
from xic_extractor.alignment.completion import (
    UntargetWorkflowCompositionRequest,
    WorkflowStage,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetApplicationResult,
)


def _uninitialized(value_type, **values):
    value = object.__new__(value_type)
    for name, item in values.items():
        object.__setattr__(value, name, item)
    return value


@pytest.mark.parametrize("fails", [False, True])
def test_subject_composition_closes_without_runtime_and_preserves_failure_stage(
    monkeypatch: pytest.MonkeyPatch,
    fails: bool,
) -> None:
    request = _uninitialized(
        UntargetWorkflowCompositionRequest,
        scope=SimpleNamespace(
            evidence_providers=(SimpleNamespace(method_subject_id="subject"),)
        ),
        excluded_candidates=(),
    )
    adapter = object()
    child = _uninitialized(FinalizedUntargetApplicationResult)
    monkeypatch.setattr(
        workflow_owner,
        "_untarget_scope_memberships",
        lambda _: {"candidate": "subject"},
    )

    def compose(supplied_request, supplied_adapter):
        assert supplied_request is request
        assert supplied_adapter is adapter
        if fails:
            raise workflow_owner.WorkflowStageFailure(
                WorkflowStage.CELL_DECISION, ValueError("selection failed")
            )
        return child

    monkeypatch.setattr(workflow_owner, "_compose_application_result", compose)
    if fails:
        with pytest.raises(
            workflow_owner.AlignmentWorkflowContractError,
            match="cell_decision.*selection failed",
        ):
            workflow_owner.compose_untarget_subject(request, adapter)
    else:
        assert workflow_owner.compose_untarget_subject(request, adapter) is child
