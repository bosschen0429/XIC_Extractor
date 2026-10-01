"""Pure Untarget batch closure and exact scientific population contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import xic_extractor.alignment.completion as workflow_owner
import xic_extractor.alignment.result.application_result as application_result_owner
import xic_extractor.untargeted_execution.execution_result as execution_result_owner
from tests.test_search_scope_capture import _capture_binding, _peak_config
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.alignment.completion import (
    UntargetWorkflowBundle,
    UntargetWorkflowCompositionRequest,
    WorkflowStage,
    complete_untarget_batch,
)
from xic_extractor.alignment.result.application_result import (
    ApplicationResultContractError,
    FinalizedUntargetApplicationResult,
    FinalizedUntargetBatchApplicationResult,
    UntargetBatchApplicationResultRequest,
    UntargetedCandidateExclusion,
    finalize_alignment_result,
)
from xic_extractor.discovery.batch_contract import (
    CANDIDATE_UNIVERSE_INPUT_KEY,
    CANDIDATE_UNIVERSE_SCHEMA_VERSION,
    validate_candidate_universe_payload,
)
from xic_extractor.execution._lifecycle import AttemptTerminalOutcome
from xic_extractor.immutable_sequence import ImmutableSequence
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
    IdentityContractError,
    build_processing_request_identity,
    issue_processing_attempt,
    resolve_request_acquisition_source,
)
from xic_extractor.untargeted_execution.execution_result import (
    CompletedUntargetBatchWorkflowResult,
)


def _uninitialized(value_type, **values):
    value = object.__new__(value_type)
    for name, item in values.items():
        object.__setattr__(value, name, item)
    return value


def test_batch_request_requires_real_complete_request_scope(tmp_path) -> None:
    fixture = build_untargeted_cid_nl_no_raw_fixture(
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
                    precursor_mz=243.108,
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
        scientific_input_schema_version="untargeted-batch-request-test",
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
    request = UntargetWorkflowCompositionRequest(
        scope=scope,
        processing_request=request_identity,
        processing_attempt=attempt,
    )
    workflow_owner._validate_untarget_batch_composition_request(request)

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
        workflow_owner._validate_untarget_batch_composition_request(
            UntargetWorkflowCompositionRequest(
                scope=child_scope,
                processing_request=request_identity,
                processing_attempt=attempt,
            )
        )


def test_completed_batch_rejects_crossed_runtime_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        execution_result_owner, "RuntimeFinalizationReceipt", SimpleNamespace
    )
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
        schema_version="search_scope",
        request=request,
        samples=samples,
        method_constraints=object(),
        evidence_providers=tuple(
            SimpleNamespace(method_subject_id=subject_id) for subject_id in subject_ids
        ),
        declared_candidate_ids=candidate_ids,
        capture_bindings=bindings,
    )


def _candidate_universe_payload(
    *,
    processing_owners,
    samples,
    candidate_ids_by_stem: dict[str, tuple[str, ...]],
) -> dict[str, object]:
    return {
        "schema_version": CANDIDATE_UNIVERSE_SCHEMA_VERSION,
        "samples": [
            {
                "sample_stem": sample.metadata.raw_stem,
                "candidate_count": len(
                    candidate_ids_by_stem.get(sample.metadata.raw_stem, ())
                ),
                "candidate_ids": list(
                    candidate_ids_by_stem.get(sample.metadata.raw_stem, ())
                ),
                "candidate_payload_sha256": "0" * 64,
                "discovery_receipt_id": ("xic.discovery-candidate-universe:test"),
                "candidate_artifact_schema_version": "discovery_candidates_csv",
                "candidate_artifact_sha256": "0" * 64,
                "candidate_artifact_byte_size": 0,
                "acquisition_source_id": resolve_request_acquisition_source(
                    processing_owners=processing_owners,
                    sample=sample,
                ).acquisition_source_id,
            }
            for sample in sorted(
                samples,
                key=lambda item: item.metadata.raw_stem,
            )
        ],
    }


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
        grouping_result=SimpleNamespace(
            final_population=(group,),
            partition_transition=SimpleNamespace(lineage=()),
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
    fixture = build_untargeted_cid_nl_no_raw_fixture(
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
        scientific_input_schema_version="untargeted-batch-workflow-test",
        processing_algorithm_version=base_request.processing_algorithm_version,
        processing_contract_version=base_request.processing_contract_version,
        scientific_inputs={
            REQUEST_INPUT_KEY: subject_batch.request_payload(),
            CANDIDATE_UNIVERSE_INPUT_KEY: _candidate_universe_payload(
                processing_owners=fixture.owners,
                samples=fixture.samples,
                candidate_ids_by_stem={"sample-a": ("candidate:a",)},
            ),
        },
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
        match="duplicate subjects across children",
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


def test_batch_application_result_rejects_exclusion_crossed_to_another_sample(
    tmp_path,
) -> None:
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"sample-a": "qc", "sample-b": "qc"},
    )
    candidate_id = "candidate:a"
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=(fixture.method_subject,),
        candidate_memberships=(
            (candidate_id, fixture.method_subject.method_subject_id),
        ),
    )
    base_request = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=base_request.acquisition_sources,
        acquisition_bindings=base_request.acquisition_bindings,
        scientific_input_schema_version="untargeted-batch-sample-binding-test",
        processing_algorithm_version=base_request.processing_algorithm_version,
        processing_contract_version=base_request.processing_contract_version,
        scientific_inputs={
            REQUEST_INPUT_KEY: subject_batch.request_payload(),
            CANDIDATE_UNIVERSE_INPUT_KEY: _candidate_universe_payload(
                processing_owners=fixture.owners,
                samples=fixture.samples,
                candidate_ids_by_stem={"sample-a": (candidate_id,)},
            ),
        },
    )
    attempt = issue_processing_attempt(request)
    parent_scope = _fake_scope(
        request=request,
        samples=fixture.samples,
        candidate_ids=(candidate_id,),
        subject_ids=(fixture.method_subject.method_subject_id,),
    )
    sample_b = fixture.sample("sample-b")

    with pytest.raises(
        ApplicationResultContractError,
        match="crossed candidate, subject, or sample",
    ):
        finalize_alignment_result(
            UntargetBatchApplicationResultRequest(
                scope=parent_scope,
                processing_request=request,
                processing_attempt=attempt,
                children=(),
                excluded_candidates=(
                    UntargetedCandidateExclusion(
                        candidate_id=candidate_id,
                        method_subject_id=fixture.method_subject.method_subject_id,
                        canonical_sample_identity_id=(
                            sample_b.identity.sample_identity_id
                        ),
                        discovery_candidate_state="review_only_orphan_nl",
                    ),
                ),
            )
        )


def test_batch_application_result_requires_owner_candidate_universe(
    tmp_path,
) -> None:
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"sample-a": "qc"},
    )
    candidate_id = "candidate:a"
    subject_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=(fixture.method_subject,),
        candidate_memberships=(
            (candidate_id, fixture.method_subject.method_subject_id),
        ),
    )
    base_request = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=base_request.acquisition_sources,
        acquisition_bindings=base_request.acquisition_bindings,
        scientific_input_schema_version="untargeted-batch-missing-universe-test",
        processing_algorithm_version=base_request.processing_algorithm_version,
        processing_contract_version=base_request.processing_contract_version,
        scientific_inputs={REQUEST_INPUT_KEY: subject_batch.request_payload()},
    )
    sample = fixture.sample("sample-a")

    with pytest.raises(
        ApplicationResultContractError,
        match="owner-validated candidate/sample universe",
    ):
        UntargetBatchApplicationResultRequest(
            scope=_fake_scope(
                request=request,
                samples=fixture.samples,
                candidate_ids=(candidate_id,),
                subject_ids=(fixture.method_subject.method_subject_id,),
            ),
            processing_request=request,
            processing_attempt=issue_processing_attempt(request),
            children=(),
            excluded_candidates=(
                UntargetedCandidateExclusion(
                    candidate_id=candidate_id,
                    method_subject_id=fixture.method_subject.method_subject_id,
                    canonical_sample_identity_id=sample.identity.sample_identity_id,
                    discovery_candidate_state="review_only_orphan_nl",
                ),
            ),
        )


@pytest.mark.parametrize("carrier", ["tuple", "chunks"])
@pytest.mark.parametrize(
    "case", ["valid", "reversed", "wrong_attempt", "duplicate_physical"]
)
def test_batch_application_result_preserves_order_and_binding_across_chunks(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    carrier: str,
    case: str,
) -> None:
    fixture = build_untargeted_cid_nl_no_raw_fixture(
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
                    precursor_mz=243.108,
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
        scientific_input_schema_version="untargeted-batch-order-test",
        processing_algorithm_version=base_request.processing_algorithm_version,
        processing_contract_version=base_request.processing_contract_version,
        scientific_inputs={
            REQUEST_INPUT_KEY: subject_batch.request_payload(),
            CANDIDATE_UNIVERSE_INPUT_KEY: _candidate_universe_payload(
                processing_owners=fixture.owners,
                samples=fixture.samples,
                candidate_ids_by_stem={
                    "sample-a": tuple(candidate_id for candidate_id, _ in memberships)
                },
            ),
        },
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
        for candidate_id, method_subject_id in memberships
    )
    monkeypatch.setattr(
        application_result_owner,
        "_validate_untarget_result",
        lambda _child: None,
    )

    if case == "reversed":
        children = tuple(reversed(children))
    elif case == "wrong_attempt":
        handoff = children[1].scientific_core.cell_observation_handoff
        handoff.coverage.epoch.processing_owners.attempt = issue_processing_attempt(
            request
        )
    elif case == "duplicate_physical":
        children[1].scientific_core.final_population[0].member_subjects[
            0
        ].physical_observation_ids = (
            children[0]
            .scientific_core.final_population[0]
            .member_subjects[0]
            .physical_observation_ids
        )
    loads = []

    def load_chunk(index):
        loads.append(index)
        return (children[index],)

    supplied_children = (
        ImmutableSequence(chunk_lengths=(1, 1), load_chunk=load_chunk)
        if carrier == "chunks"
        else children
    )
    kwargs = dict(
        scope=parent_scope,
        processing_request=request,
        processing_attempt=attempt,
        children=supplied_children,
    )
    if case == "valid":
        result = finalize_alignment_result(
            UntargetBatchApplicationResultRequest(**kwargs)
        )
        assert result.children is supplied_children
        assert tuple(result.application_results) == children
        assert result.rows == tuple(
            child.completed_projection.rows[0] for child in children
        )
    else:
        message = {
            "reversed": "canonical order",
            "wrong_attempt": "crossed its processing attempt",
            "duplicate_physical": "share a physical observation",
        }[case]
        with pytest.raises(ApplicationResultContractError, match=message):
            finalize_alignment_result(UntargetBatchApplicationResultRequest(**kwargs))
    if carrier == "chunks":
        assert 1 in loads


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
    owners = object()
    child = _uninitialized(FinalizedUntargetApplicationResult)
    monkeypatch.setattr(workflow_owner, "validate_completion_inputs", lambda *_: owners)
    monkeypatch.setattr(
        workflow_owner,
        "_untarget_scope_memberships",
        lambda _: {"candidate": "subject"},
    )

    def compose(supplied_request, supplied_adapter, *, owners, completed_captures):
        assert supplied_request is request
        assert supplied_adapter is adapter
        if fails:
            raise workflow_owner._WorkflowStageFailure(
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


def _universe_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "sample_stem": "S1",
        "candidate_count": 1,
        "candidate_ids": ["c1"],
        "candidate_payload_sha256": "a" * 64,
        "discovery_receipt_id": "xic.discovery-candidate-universe:test",
        "candidate_artifact_schema_version": "discovery_candidates_csv",
        "candidate_artifact_sha256": "b" * 64,
        "candidate_artifact_byte_size": 0,
        "acquisition_source_id": "xic.acquisition-source:test",
    }
    entry.update(overrides)
    return entry


def test_candidate_universe_payload_accepts_lowercase_digests() -> None:
    entry = _universe_entry()
    payload = {"schema_version": CANDIDATE_UNIVERSE_SCHEMA_VERSION, "samples": [entry]}

    assert validate_candidate_universe_payload(
        payload, expected_sample_stems=["S1"]
    ) == (entry,)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("candidate_payload_sha256", "A" * 64, "digest is invalid"),
        ("candidate_payload_sha256", "a" * 63, "digest is invalid"),
        ("candidate_payload_sha256", None, "digest is invalid"),
        ("candidate_artifact_sha256", "B" * 64, "owner receipt facts are invalid"),
        ("candidate_artifact_sha256", 7, "owner receipt facts are invalid"),
    ],
)
def test_candidate_universe_payload_rejects_non_lowercase_sha256(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = {
        "schema_version": CANDIDATE_UNIVERSE_SCHEMA_VERSION,
        "samples": [_universe_entry(**{field: value})],
    }

    with pytest.raises(IdentityContractError, match=message):
        validate_candidate_universe_payload(payload, expected_sample_stems=["S1"])


def test_batch_child_failure_keeps_stage_and_never_finalizes_partial_results(
    monkeypatch,
):
    request = object()
    bundle = _uninitialized(UntargetWorkflowBundle, request=object(), adapter=object())
    monkeypatch.setattr(
        workflow_owner, "_validate_untarget_batch_composition_request", lambda _: None
    )
    monkeypatch.setattr(
        workflow_owner, "_validate_untarget_batch_bundles", lambda *_: None
    )
    error = ValueError("child failed")

    def fail_child(*args, **kwargs):
        raise workflow_owner._WorkflowStageFailure(WorkflowStage.CAPTURE, error)

    monkeypatch.setattr(workflow_owner, "complete_analysis", fail_child)
    monkeypatch.setattr(
        workflow_owner,
        "finalize_alignment_result",
        lambda _: pytest.fail("partial batch finalized"),
    )
    with pytest.raises(workflow_owner._WorkflowStageFailure) as failure:
        complete_untarget_batch(request, (bundle,))
    assert failure.value.stage is WorkflowStage.CAPTURE
    assert failure.value.error is error
