"""Contract tests for the post-runtime Untarget batch workflow seam."""

from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.result.application_result as application_result_owner
import xic_extractor.alignment.workflow as workflow_owner
from tests.issue175_no_raw_fixture import build_issue175_no_raw_fixture
from tests.test_search_scope_capture import _capture_binding, _peak_config
from xic_extractor.alignment.result.application_result import (
    ApplicationResultContractError,
    FinalizedUntargetApplicationResult,
    FinalizedUntargetBatchApplicationResult,
    UntargetBatchApplicationResultRequest,
    finalize_alignment_result,
)
from xic_extractor.alignment.workflow import (
    CancelledWorkflowResult,
    CompletedUntargetBatchWorkflowResult,
    FailedWorkflowResult,
    UntargetWorkflowBatchProvider,
    UntargetWorkflowBundle,
    UntargetWorkflowCompositionRequest,
    UntargetWorkflowRequest,
    WorkflowRuntimeInvocation,
    WorkflowStage,
    compose_untarget_batch_after_runtime,
    execute_untarget_batch_workflow,
)
from xic_extractor.execution_runtime import AttemptTerminalOutcome
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    REQUEST_INPUT_KEY,
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import (
    build_processing_request_identity,
    issue_processing_attempt,
    resolve_request_acquisition_source,
)


def _uninitialized(value_type, **values):
    value = object.__new__(value_type)
    for name, item in values.items():
        object.__setattr__(value, name, item)
    return value


def _request_for_lifecycle(outcome: AttemptTerminalOutcome):
    attempt = object()
    runtime = _uninitialized(
        WorkflowRuntimeInvocation,
        runtime=object(),
        reservations=(),
        worker_batch_provider=lambda _aggregate: None,
        telemetry_provider=lambda _aggregate: None,
        persister=lambda receipt: receipt,
    )
    request = _uninitialized(
        UntargetWorkflowRequest,
        scope=object(),
        processing_request=object(),
        processing_attempt=attempt,
        runtime=runtime,
    )
    receipt = SimpleNamespace(
        supervisor=SimpleNamespace(terminal_outcome=outcome, attempt=attempt),
    )
    return request, receipt


def test_batch_surface_has_explicit_typed_provider_and_result() -> None:
    assert tuple(field.name for field in fields(UntargetWorkflowBundle)) == (
        "request",
        "adapter",
    )
    assert tuple(
        field.name for field in fields(UntargetBatchApplicationResultRequest)
    ) == (
        "scope",
        "processing_request",
        "processing_attempt",
        "children",
    )
    assert tuple(
        field.name for field in fields(FinalizedUntargetBatchApplicationResult)
    ) == (
        "scope",
        "processing_request",
        "processing_attempt",
        "children",
    )
    assert UntargetWorkflowBatchProvider is not object
    assert tuple(
        field.name for field in fields(UntargetWorkflowCompositionRequest)
    ) == (
        "scope",
        "processing_request",
        "processing_attempt",
    )


def test_batch_request_requires_real_complete_request_scope(tmp_path) -> None:
    fixture = build_issue175_no_raw_fixture(
        tmp_path,
        sample_roles={"sample-a": "qc"},
    )
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=243.098,
                    precursor_tolerance_ppm=10.0,
                    neutral_loss_da=fixture.method_subject.neutral_loss_da,
                ),
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=243.099,
                    precursor_tolerance_ppm=10.0,
                    neutral_loss_da=fixture.method_subject.neutral_loss_da,
                ),
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    memberships = tuple(
        (f"candidate:{index}", subject.method_subject_id)
        for index, subject in enumerate(subjects)
    )
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=memberships,
    )
    base_request = fixture.owners.request
    request_identity = build_processing_request_identity(
        acquisition_sources=base_request.acquisition_sources,
        acquisition_bindings=base_request.acquisition_bindings,
        scientific_input_schema_version="untargeted-batch-request-test-v1",
        processing_algorithm_version=base_request.processing_algorithm_version,
        processing_contract_version=base_request.processing_contract_version,
        scientific_inputs={REQUEST_INPUT_KEY: subject_batch.request_payload()},
    )
    attempt = issue_processing_attempt(request_identity)
    sample = fixture.samples[0]
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=sample,
    )
    bindings = tuple(
        _capture_binding(
            candidate_id=candidate_id,
            method_subject_id=method_subject_id,
            sample=sample,
            acquisition_source_id=source.acquisition_source_id,
            mz=subject_batch.subject_for_candidate(candidate_id).precursor_mz,
            ppm_tol=subject_batch.subject_for_candidate(
                candidate_id
            ).precursor_tolerance_ppm,
            rt_min=7.7,
            rt_max=8.3,
        )
        for candidate_id, method_subject_id in memberships
    )
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=request_identity,
        samples=fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=_peak_config(tmp_path),
        ),
        evidence_providers=tuple(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=subject.method_subject_id,
            )
            for subject in subjects
        ),
        declared_candidate_ids=tuple(candidate_id for candidate_id, _ in memberships),
        capture_bindings=bindings,
    )
    runtime = object.__new__(workflow_owner.AttemptRuntime)
    object.__setattr__(runtime, "manifest", SimpleNamespace(attempt=attempt))
    object.__setattr__(runtime, "history", SimpleNamespace(attempt=attempt))
    invocation = WorkflowRuntimeInvocation(
        runtime=runtime,
        reservations=(),
        worker_batch_provider=lambda _aggregate: (),
        telemetry_provider=lambda _record: (),
        persister=lambda receipt: receipt,
    )
    request = UntargetWorkflowRequest(
        scope=scope,
        processing_request=request_identity,
        processing_attempt=attempt,
        runtime=invocation,
    )
    workflow_owner._validate_untarget_batch_request(request)

    child_scope = UntargetedSearchScope(
        schema_version=scope.schema_version,
        request=request_identity,
        samples=scope.samples,
        method_constraints=scope.method_constraints,
        evidence_providers=(scope.evidence_providers[0],),
        declared_candidate_ids=(bindings[0].candidate_id,),
        capture_bindings=(bindings[0],),
    )
    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match="complete request universe",
    ):
        workflow_owner._validate_untarget_batch_request(
            UntargetWorkflowRequest(
                scope=child_scope,
                processing_request=request_identity,
                processing_attempt=attempt,
                runtime=invocation,
            )
        )


@pytest.mark.parametrize(
    "outcome",
    [AttemptTerminalOutcome.CANCELLED, AttemptTerminalOutcome.FAILED],
)
def test_batch_never_requests_child_provider_after_non_success_runtime(
    monkeypatch: pytest.MonkeyPatch,
    outcome: AttemptTerminalOutcome,
) -> None:
    request, receipt = _request_for_lifecycle(outcome)
    provider_calls: list[object] = []

    monkeypatch.setattr(
        workflow_owner,
        "_validate_untarget_batch_request",
        lambda _request: None,
    )
    monkeypatch.setattr(workflow_owner, "RuntimeFinalizationReceipt", SimpleNamespace)
    monkeypatch.setattr(
        workflow_owner,
        "execute_runtime_attempt",
        lambda *_args, **_kwargs: receipt,
    )

    def provider():
        provider_calls.append(object())
        return ()

    result = execute_untarget_batch_workflow(request, provider)

    assert type(result) is (
        CancelledWorkflowResult
        if outcome is AttemptTerminalOutcome.CANCELLED
        else FailedWorkflowResult
    )
    assert result.runtime_receipt is receipt
    assert provider_calls == []


def test_completed_batch_rejects_crossed_runtime_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workflow_owner, "RuntimeFinalizationReceipt", SimpleNamespace)
    receipt = SimpleNamespace(
        supervisor=SimpleNamespace(
            terminal_outcome=AttemptTerminalOutcome.SUCCEEDED,
            attempt=object(),
        ),
    )
    application_result = _uninitialized(
        FinalizedUntargetBatchApplicationResult,
        scope=object(),
        processing_request=object(),
        processing_attempt=object(),
        children=(),
    )

    with pytest.raises(
        workflow_owner.AlignmentWorkflowContractError,
        match="crossed its runtime attempt",
    ):
        CompletedUntargetBatchWorkflowResult(
            runtime_receipt=receipt,
            application_result=application_result,
        )


def test_batch_composes_children_after_one_runtime_without_recursive_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, receipt = _request_for_lifecycle(AttemptTerminalOutcome.SUCCEEDED)
    bundle = _uninitialized(
        UntargetWorkflowBundle,
        request=object(),
        adapter=object(),
    )
    monkeypatch.setattr(workflow_owner, "RuntimeFinalizationReceipt", SimpleNamespace)
    child_application = object()
    child_result = _uninitialized(
        workflow_owner.CompletedUntargetWorkflowResult,
        runtime_receipt=receipt,
        application_result=child_application,
    )
    batch_application = _uninitialized(
        FinalizedUntargetBatchApplicationResult,
        scope=object(),
        processing_request=object(),
        processing_attempt=request.processing_attempt,
        children=(),
    )
    calls: list[str] = []

    monkeypatch.setattr(
        workflow_owner,
        "_validate_untarget_batch_request",
        lambda _request: None,
    )
    monkeypatch.setattr(
        workflow_owner,
        "execute_runtime_attempt",
        lambda *_args, **_kwargs: calls.append("runtime") or receipt,
    )
    monkeypatch.setattr(
        workflow_owner,
        "_validate_untarget_batch_bundles",
        lambda _request, _bundles: calls.append("validate_bundles"),
    )
    monkeypatch.setattr(
        workflow_owner, "_validate_request_adapter", lambda *_args: object()
    )
    monkeypatch.setattr(
        workflow_owner,
        "UntargetBatchApplicationResultRequest",
        lambda **values: values,
    )
    monkeypatch.setattr(
        workflow_owner,
        "_execute_composition_after_runtime",
        lambda _request, _adapter, runtime_receipt, **_kwargs: calls.append(
            "composition"
        )
        or child_result,
    )
    monkeypatch.setattr(
        workflow_owner,
        "finalize_alignment_result",
        lambda _request: calls.append("batch_finalize") or batch_application,
    )
    monkeypatch.setattr(
        workflow_owner,
        "execute_alignment_workflow",
        lambda *_args, **_kwargs: pytest.fail(
            "batch must not recursively execute the runtime workflow"
        ),
    )

    result = execute_untarget_batch_workflow(
        request,
        lambda: calls.append("provider") or (bundle,),
    )

    assert type(result) is CompletedUntargetBatchWorkflowResult
    assert result.runtime_receipt is receipt
    assert result.application_result is batch_application
    assert calls == [
        "runtime",
        "provider",
        "validate_bundles",
        "composition",
        "batch_finalize",
    ]


def test_batch_can_compose_an_owner_completed_runtime_without_reexecution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, receipt = _request_for_lifecycle(AttemptTerminalOutcome.SUCCEEDED)
    composition_request = _uninitialized(
        UntargetWorkflowCompositionRequest,
        scope=request.scope,
        processing_request=request.processing_request,
        processing_attempt=request.processing_attempt,
    )
    bundle = _uninitialized(UntargetWorkflowBundle, request=object(), adapter=object())
    child = _uninitialized(
        workflow_owner.CompletedUntargetWorkflowResult,
        runtime_receipt=receipt,
        application_result=object(),
    )
    batch = _uninitialized(
        FinalizedUntargetBatchApplicationResult,
        processing_attempt=request.processing_attempt,
    )
    monkeypatch.setattr(workflow_owner, "RuntimeFinalizationReceipt", SimpleNamespace)
    monkeypatch.setattr(
        workflow_owner,
        "execute_runtime_attempt",
        lambda *_args, **_kwargs: pytest.fail("completed runtime must not reexecute"),
    )
    monkeypatch.setattr(
        workflow_owner,
        "_validate_untarget_batch_composition_request",
        lambda _r: None,
    )
    monkeypatch.setattr(
        workflow_owner, "_validate_untarget_batch_bundles", lambda *_a: None
    )
    monkeypatch.setattr(
        workflow_owner, "_validate_request_adapter", lambda *_a: object()
    )
    monkeypatch.setattr(
        workflow_owner,
        "_execute_composition_after_runtime",
        lambda *_a, **_k: child,
    )
    monkeypatch.setattr(
        workflow_owner, "UntargetBatchApplicationResultRequest", lambda **v: v
    )
    monkeypatch.setattr(workflow_owner, "finalize_alignment_result", lambda _v: batch)

    result = compose_untarget_batch_after_runtime(
        composition_request,
        lambda: (bundle,),
        receipt,
    )

    assert type(result) is CompletedUntargetBatchWorkflowResult
    assert result.runtime_receipt is receipt


def test_batch_child_failure_is_atomic_and_does_not_finalize_partial_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, receipt = _request_for_lifecycle(AttemptTerminalOutcome.SUCCEEDED)
    bundle = _uninitialized(
        UntargetWorkflowBundle,
        request=object(),
        adapter=object(),
    )
    failure = FailedWorkflowResult.__new__(FailedWorkflowResult)
    object.__setattr__(failure, "runtime_receipt", receipt)
    object.__setattr__(failure, "stage", WorkflowStage.CAPTURE)
    object.__setattr__(failure, "failure_type", "test.Failure")
    object.__setattr__(failure, "failure_message", "child failed")
    finalize_calls: list[object] = []

    monkeypatch.setattr(
        workflow_owner,
        "_validate_untarget_batch_request",
        lambda _request: None,
    )
    monkeypatch.setattr(workflow_owner, "RuntimeFinalizationReceipt", SimpleNamespace)
    monkeypatch.setattr(
        workflow_owner,
        "execute_runtime_attempt",
        lambda *_args, **_kwargs: receipt,
    )
    monkeypatch.setattr(
        workflow_owner,
        "_validate_untarget_batch_bundles",
        lambda _request, _bundles: None,
    )
    monkeypatch.setattr(
        workflow_owner, "_validate_request_adapter", lambda *_args: object()
    )
    monkeypatch.setattr(
        workflow_owner,
        "_execute_composition_after_runtime",
        lambda *_args, **_kwargs: failure,
    )
    monkeypatch.setattr(
        workflow_owner,
        "finalize_alignment_result",
        lambda value: finalize_calls.append(value),
    )

    result = execute_untarget_batch_workflow(
        request,
        lambda: (bundle,),
    )

    assert type(result) is FailedWorkflowResult
    assert result.runtime_receipt is receipt
    assert result.stage is WorkflowStage.CAPTURE
    assert finalize_calls == []


def _fake_scope(
    *,
    request,
    samples,
    candidate_ids: tuple[str, ...],
    subject_ids: tuple[str, ...],
):
    bindings = tuple(
        SimpleNamespace(
            candidate_id=candidate_id,
            method_subject_id=subject_ids[index % len(subject_ids)],
        )
        for index, candidate_id in enumerate(candidate_ids)
    )
    return _uninitialized(
        workflow_owner.UntargetedSearchScope,
        schema_version="search_scope_v1",
        request=request,
        samples=samples,
        method_constraints=object(),
        evidence_providers=tuple(
            SimpleNamespace(method_subject_id=subject_id)
            for subject_id in subject_ids
        ),
        declared_candidate_ids=candidate_ids,
        capture_bindings=bindings,
    )


def _fake_application(*, scope, request, attempt, samples, row_id, physical_id):
    member = SimpleNamespace(physical_observation_ids=(physical_id,))
    group = SimpleNamespace(
        group_hypothesis_id=f"group:{row_id}",
        member_subjects=(member,),
    )
    core = _uninitialized(
        workflow_owner.FinalizedScientificAlignment,
        scope=scope,
        processing_request=request,
        canonical_samples=samples,
        cell_observation_handoff=SimpleNamespace(
            coverage=SimpleNamespace(
                epoch=SimpleNamespace(
                    processing_owners=SimpleNamespace(attempt=attempt),
                ),
            ),
        ),
        final_cross_sample_receipt=SimpleNamespace(
            final_population=(group,),
            final_application=SimpleNamespace(lineage=()),
        ),
    )
    row = SimpleNamespace(
        row_key=SimpleNamespace(
            untarget_row_key_id=row_id,
            group=group,
        )
    )
    projection = SimpleNamespace(rows=(row,))
    return _uninitialized(
        FinalizedUntargetApplicationResult,
        scientific_core=core,
        completed_projection=projection,
    )


def test_batch_application_result_rejects_duplicate_subject_and_crossed_attempt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = build_issue175_no_raw_fixture(
        tmp_path,
        sample_roles={"sample-a": "qc"},
    )
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=(fixture.method_subject,),
        candidate_memberships=(
            ("candidate:a", fixture.method_subject.method_subject_id),
        ),
    )
    base_request = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=base_request.acquisition_sources,
        acquisition_bindings=base_request.acquisition_bindings,
        scientific_input_schema_version="untargeted-batch-workflow-test-v1",
        processing_algorithm_version=base_request.processing_algorithm_version,
        processing_contract_version=base_request.processing_contract_version,
        scientific_inputs={REQUEST_INPUT_KEY: subject_batch.request_payload()},
    )
    attempt = issue_processing_attempt(request)
    subject_id = fixture.method_subject.method_subject_id
    parent_scope = _fake_scope(
        request=request,
        samples=fixture.samples,
        candidate_ids=("candidate:a",),
        subject_ids=(subject_id,),
    )
    child_scope = _fake_scope(
        request=request,
        samples=fixture.samples,
        candidate_ids=("candidate:a",),
        subject_ids=(subject_id,),
    )
    child = _fake_application(
        scope=child_scope,
        request=request,
        attempt=attempt,
        samples=fixture.samples,
        row_id="row:a",
        physical_id="physical:a",
    )
    monkeypatch.setattr(
        application_result_owner,
        "_validate_untarget_result",
        lambda _child: None,
    )

    with pytest.raises(
        ApplicationResultContractError,
        match="duplicate subject-local children",
    ):
        finalize_alignment_result(
            UntargetBatchApplicationResultRequest(
                scope=parent_scope,
                processing_request=request,
                processing_attempt=attempt,
                children=(child, child),
            )
        )

    retry = issue_processing_attempt(request, retry_of=attempt)
    with pytest.raises(
        ApplicationResultContractError,
        match="crossed its processing attempt",
    ):
        finalize_alignment_result(
            UntargetBatchApplicationResultRequest(
                scope=parent_scope,
                processing_request=request,
                processing_attempt=retry,
                children=(child,),
            )
        )


def test_batch_application_result_rejects_reversed_subject_order(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = build_issue175_no_raw_fixture(
        tmp_path,
        sample_roles={"sample-a": "qc"},
    )
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=243.098,
                    precursor_tolerance_ppm=10.0,
                    neutral_loss_da=fixture.method_subject.neutral_loss_da,
                ),
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=243.099,
                    precursor_tolerance_ppm=10.0,
                    neutral_loss_da=fixture.method_subject.neutral_loss_da,
                ),
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    memberships = tuple(
        (f"candidate:{index}", subject.method_subject_id)
        for index, subject in enumerate(subjects)
    )
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=memberships,
    )
    base_request = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=base_request.acquisition_sources,
        acquisition_bindings=base_request.acquisition_bindings,
        scientific_input_schema_version="untargeted-batch-order-test-v1",
        processing_algorithm_version=base_request.processing_algorithm_version,
        processing_contract_version=base_request.processing_contract_version,
        scientific_inputs={REQUEST_INPUT_KEY: subject_batch.request_payload()},
    )
    attempt = issue_processing_attempt(request)
    parent_scope = _fake_scope(
        request=request,
        samples=fixture.samples,
        candidate_ids=tuple(candidate_id for candidate_id, _ in memberships),
        subject_ids=tuple(subject.method_subject_id for subject in subjects),
    )
    children = tuple(
        _fake_application(
            scope=_fake_scope(
                request=request,
                samples=fixture.samples,
                candidate_ids=(candidate_id,),
                subject_ids=(method_subject_id,),
            ),
            request=request,
            attempt=attempt,
            samples=fixture.samples,
            row_id=f"row:{candidate_id}",
            physical_id=f"physical:{candidate_id}",
        )
        for candidate_id, method_subject_id in reversed(memberships)
    )
    monkeypatch.setattr(
        application_result_owner,
        "_validate_untarget_result",
        lambda _child: None,
    )

    with pytest.raises(
        ApplicationResultContractError,
        match="canonical order",
    ):
        finalize_alignment_result(
            UntargetBatchApplicationResultRequest(
                scope=parent_scope,
                processing_request=request,
                processing_attempt=attempt,
                children=children,
            )
        )
