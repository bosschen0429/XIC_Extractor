"""Failure projection at the shared workflow boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import xic_extractor.alignment.workflow as workflow_owner
from xic_extractor.alignment.workflow import (
    CancelledWorkflowResult,
    FailedWorkflowResult,
    TargetWorkflowAdapter,
    TargetWorkflowRequest,
    WorkflowStage,
    execute_alignment_workflow,
)
from xic_extractor.execution_runtime import AttemptTerminalOutcome


def _request_and_adapter(outcome: AttemptTerminalOutcome):
    request = object.__new__(TargetWorkflowRequest)
    adapter = object.__new__(TargetWorkflowAdapter)
    receipt = SimpleNamespace(
        supervisor=SimpleNamespace(terminal_outcome=outcome),
    )
    invocation = SimpleNamespace(
        runtime=object(),
        reservations=(),
        worker_batch_provider=lambda _aggregate: None,
        telemetry_provider=lambda _aggregate: None,
        persister=lambda value: value,
    )
    object.__setattr__(request, "runtime", invocation)
    object.__setattr__(request, "scope", object())
    return request, adapter, receipt


@pytest.mark.parametrize(
    ("outcome", "expected_type"),
    [
        (AttemptTerminalOutcome.CANCELLED, CancelledWorkflowResult),
        (AttemptTerminalOutcome.FAILED, FailedWorkflowResult),
    ],
)
def test_runtime_terminal_maps_one_to_one_without_entering_science(
    monkeypatch: pytest.MonkeyPatch,
    outcome: AttemptTerminalOutcome,
    expected_type: type[object],
) -> None:
    request, adapter, receipt = _request_and_adapter(outcome)
    monkeypatch.setattr(
        workflow_owner,
        "_validate_request_adapter",
        lambda *_: object(),
    )
    monkeypatch.setattr(
        workflow_owner,
        "RuntimeFinalizationReceipt",
        SimpleNamespace,
    )
    monkeypatch.setattr(
        workflow_owner,
        "execute_runtime_attempt",
        lambda *_args, **_kwargs: receipt,
    )
    monkeypatch.setattr(
        workflow_owner,
        "capture_peak_hypotheses",
        lambda **_kwargs: pytest.fail("runtime terminal entered science"),
    )

    result = execute_alignment_workflow(request, adapter)

    assert type(result) is expected_type
    assert result.runtime_receipt is receipt
    if type(result) is FailedWorkflowResult:
        assert result.stage is WorkflowStage.RUNTIME
        assert result.failure_type is None
        assert result.failure_message is None


def test_runtime_owner_exception_propagates_without_workflow_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, adapter, _receipt = _request_and_adapter(
        AttemptTerminalOutcome.SUCCEEDED
    )
    monkeypatch.setattr(
        workflow_owner,
        "_validate_request_adapter",
        lambda *_: object(),
    )
    monkeypatch.setattr(
        workflow_owner,
        "RuntimeFinalizationReceipt",
        SimpleNamespace,
    )

    class RuntimeOwnerError(RuntimeError):
        pass

    def fail_runtime(*_args, **_kwargs):
        raise RuntimeOwnerError("owner phase failed")

    monkeypatch.setattr(workflow_owner, "execute_runtime_attempt", fail_runtime)

    with pytest.raises(RuntimeOwnerError, match="owner phase failed"):
        execute_alignment_workflow(request, adapter)


def test_post_runtime_failure_projects_exact_stage_type_and_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, adapter, receipt = _request_and_adapter(
        AttemptTerminalOutcome.SUCCEEDED
    )
    object.__setattr__(
        adapter,
        "capture_inputs",
        (SimpleNamespace(candidate_id="c", trace=object()),),
    )
    monkeypatch.setattr(
        workflow_owner,
        "_validate_request_adapter",
        lambda *_: object(),
    )
    monkeypatch.setattr(
        workflow_owner,
        "RuntimeFinalizationReceipt",
        SimpleNamespace,
    )
    monkeypatch.setattr(
        workflow_owner,
        "execute_runtime_attempt",
        lambda *_args, **_kwargs: receipt,
    )

    class CaptureFailure(RuntimeError):
        pass

    def fail_capture(**_kwargs):
        raise CaptureFailure("capture broke")

    monkeypatch.setattr(workflow_owner, "capture_peak_hypotheses", fail_capture)

    result = execute_alignment_workflow(request, adapter)

    assert type(result) is FailedWorkflowResult
    assert result.stage is WorkflowStage.CAPTURE
    assert result.failure_type == (
        f"{CaptureFailure.__module__}.{CaptureFailure.__qualname__}"
    )
    assert result.failure_message == "capture broke"
