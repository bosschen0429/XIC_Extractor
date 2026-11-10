"""Pure Untarget batch closure and exact scientific population contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import xic_extractor.alignment.completion as workflow_owner
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.alignment.completion import (
    UntargetWorkflowCompositionRequest,
    WorkflowStage,
)
from xic_extractor.alignment.result.application_result import (
    ApplicationResultContractError,
    FinalizedUntargetApplicationResult,
    FinalizedUntargetBatchApplicationResult,
)
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)


def _uninitialized(value_type, **values):
    value = object.__new__(value_type)
    for name, item in values.items():
        object.__setattr__(value, name, item)
    return value


def _fake_scope(
    *,
    method_subjects,
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
        method_subjects=method_subjects,
        samples=samples,
        method_constraints=object(),
        evidence_providers=tuple(
            SimpleNamespace(method_subject_id=subject_id) for subject_id in subject_ids
        ),
        declared_candidate_ids=candidate_ids,
        capture_bindings=bindings,
    )


def _fake_application(*, scope, samples, row_id, physical_id):
    member = SimpleNamespace(physical_peaks=(physical_id,))
    group = SimpleNamespace(
        group_hypothesis_id=f"group:{row_id}",
        member_subjects=(member,),
    )
    core = _uninitialized(
        workflow_owner.FinalizedScientificAlignment,
        scope=scope,
        canonical_samples=samples,
        grouping_result=SimpleNamespace(
            final_population=(group,),
            partition_transition=SimpleNamespace(lineage=()),
        ),
    )
    row = SimpleNamespace(group=group)
    projection = SimpleNamespace(rows=(row,))
    return _uninitialized(
        FinalizedUntargetApplicationResult,
        scientific_core=core,
        completed_projection=projection,
    )


@pytest.mark.parametrize("carrier", ["tuple", "chunks"])
@pytest.mark.parametrize("case", ["valid", "duplicate_physical"])
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
    parent_scope = _fake_scope(
        method_subjects=subject_batch,
        samples=fixture.samples,
        candidate_ids=tuple(candidate_id for candidate_id, _ in memberships),
        subject_ids=tuple(subject.method_subject_id for subject in subjects),
    )
    children = tuple(
        _fake_application(
            scope=_fake_scope(
                method_subjects=subject_batch,
                samples=fixture.samples,
                candidate_ids=(candidate_id,),
                subject_ids=(method_subject_id,),
            ),
            samples=fixture.samples,
            row_id=f"row:{candidate_id}",
            physical_id=f"physical:{candidate_id}",
        )
        for candidate_id, method_subject_id in memberships
    )

    if case == "duplicate_physical":
        children[1].scientific_core.final_population[0].member_subjects[
            0
        ].physical_peaks = (
            children[0]
            .scientific_core.final_population[0]
            .member_subjects[0]
            .physical_peaks
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
        processing_attempt_id=fixture.processing_attempt_id,
        acquisition_sources=tuple(fixture.acquisition_sources.values()),
        children=supplied_children,
    )
    if case == "valid":
        result = FinalizedUntargetBatchApplicationResult(**kwargs)
        assert result.children is supplied_children
        assert result.rows == tuple(
            child.completed_projection.rows[0] for child in children
        )
    else:
        with pytest.raises(
            ApplicationResultContractError, match="share a physical observation"
        ):
            FinalizedUntargetBatchApplicationResult(**kwargs)
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
    child = _uninitialized(FinalizedUntargetApplicationResult)
    monkeypatch.setattr(
        workflow_owner,
        "_untarget_scope_memberships",
        lambda _: {"candidate": "subject"},
    )

    def compose(supplied_request, supplied_adapter, *, completed_captures):
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
