from __future__ import annotations

from copy import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.backfill.observation_repair as repair_owner
import xic_extractor.alignment.cross_sample_reconciliation as reconciliation_owner
import xic_extractor.alignment.projection.target.pre_cell as target_projection_owner
import xic_extractor.alignment.result.scientific_alignment as finalizer_owner
from tests.issue161_no_raw_fixture import issue161_peak
from tests.package2_no_raw_fixture import (
    Package2NoRawFixture,
    build_package2_no_raw_fixture,
)
from tests.test_cell_observations import (
    _coverage_for_groups,
    _receipt_fixture,
    _support_assessment,
)
from tests.test_cross_sample_association_lineage import (
    _final_receipt as _lineage_final_receipt,
)
from tests.test_search_scope_capture import _capture_binding, _peak_config
from xic_extractor.alignment.backfill import mint_observation_repair_subject
from xic_extractor.alignment.cell_observations import (
    compose_final_group_cell_observation_population,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cross_sample import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleSuccessorApplication,
    mint_cross_sample_group_identity,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSampleReconciledState,
)
from xic_extractor.alignment.owner_backfill_request_plan import (
    OwnerBackfillRequestItem,
    mint_owner_backfill_logical_request_member,
    mint_owner_backfill_request_identity,
)
from xic_extractor.alignment.projection.target import (
    TargetProjectionContractError,
    project_target_pre_cell,
)
from xic_extractor.alignment.result import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
    SearchedNoObservationSubject,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    cid_nl_method_subject_from_target,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    compose_ms1_observation_peak_hypothesis,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    TargetSearchScope,
)
from xic_extractor.processing_identity import resolve_request_acquisition_source
from xic_extractor.xic_models import XICRequest


def test_public_target_projection_entry_point_exists() -> None:
    assert callable(project_target_pre_cell)


@pytest.mark.parametrize(
    ("observed", "group_specs", "variant"),
    [
        ((), (((), "provisional"),), "unbound"),
        (("sample-a", "sample-b"),
         ((("sample-a", "sample-b"), "established"),), "bound"),
        (("sample-a", "sample-b", "sample-c", "sample-d"),
         ((("sample-a", "sample-b"), "established"),
          (("sample-c", "sample-d"), "established")),
         "bound"),
    ],
)
def test_projects_closed_row_binding_and_complete_cell_population(
    tmp_path: Path,
    observed: tuple[str, ...],
    group_specs: tuple[tuple[tuple[str, ...], str], ...],
    variant: str,
) -> None:
    stems = tuple(
        sorted(
            set(observed).union(
                stem for members, _ in group_specs for stem in members
            )
            or {"sample-a", "sample-b"}
        )
    )
    _, scope, result, _ = _finalized_case(
        tmp_path,
        sample_stems=stems,
        observed_stems=observed,
        group_specs=group_specs,
    )

    projection = project_target_pre_cell(result)

    assert projection.binding.variant == variant
    assert tuple(
        group.group_hypothesis_id for group in projection.binding.eligible_groups
    ) == tuple(
        sorted(
            group.group_hypothesis_id
            for group in projection.binding.eligible_groups
        )
    )
    assert len(projection.subjects) == len(scope.samples)
    assert tuple(subject.sample for subject in projection.subjects) == tuple(
        sorted(scope.samples, key=lambda sample: sample.identity.sample_identity_id)
    )
    assert all(subject.target is scope.target for subject in projection.subjects)
    assert all(
        subject.processing_request is result.processing_request
        for subject in projection.subjects
    )
    assert all(
        subject.row_binding is projection.binding for subject in projection.subjects
    )
    assert all(
        subject.terminal_subject.canonical_sample_identity_id
        == subject.sample.identity.sample_identity_id
        for subject in projection.subjects
    )


def test_eligibility_uses_full_owner_member_equality_and_excludes_statuses(
    tmp_path: Path,
) -> None:
    fixture, _, result, hypotheses = _finalized_case(
        tmp_path,
        sample_stems=(
            "sample-a",
            "sample-b",
            "sample-c",
            "sample-d",
            "sample-e",
        ),
        observed_stems=(
            "sample-a",
            "sample-b",
            "sample-c",
            "sample-d",
            "sample-e",
        ),
        group_specs=(
            (("sample-a", "sample-b"), "established"),
            (("sample-c", "sample-d"), "unresolved"),
            (("sample-e",), "rejected"),
        ),
    )
    # Reusing a raw hypothesis ID is insufficient when its owner physical facts differ.
    for stem in ("sample-a", "sample-b"):
        altered = _owner_hypothesis(fixture, stem, 9.0)
        object.__setattr__(
            altered,
            "hypothesis_id",
            hypotheses[stem].hypothesis_id,
        )
        observed = next(
            subject
            for subject in result.terminal_subjects
            if isinstance(subject, ObservedSearchSubject)
            and subject.canonical_sample_identity_id
            == hypotheses[stem].peak_observation_refs[0]
            .content.canonical_sample_identity_id
        )
        object.__setattr__(observed, "hypothesis", altered)
        object.__setattr__(observed, "trace_group_id", altered.trace_group_id)

    projection = project_target_pre_cell(result)

    assert projection.binding.variant == "unbound"
    assert projection.binding.eligible_groups == ()


def test_reversed_sample_order_and_no_observation_are_preserved(tmp_path: Path) -> None:
    _, scope, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=(( ("sample-a", "sample-b"), "provisional"),),
        reverse_scope_samples=True,
    )

    projection = project_target_pre_cell(result)

    assert projection.binding.variant == "bound"
    assert tuple(subject.sample.lookup_token for subject in projection.subjects) == (
        "sample-a",
        "sample-b",
    )
    assert isinstance(projection.subjects[0].terminal_subject, ObservedSearchSubject)
    assert isinstance(
        projection.subjects[1].terminal_subject,
        SearchedNoObservationSubject,
    )
    assert scope.samples[0].lookup_token == "sample-b"


def test_equal_looking_target_is_retained_from_exact_scope(tmp_path: Path) -> None:
    _, scope, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=(),
        group_specs=(((), "provisional"),),
    )
    equal_target = replace(scope.target)
    assert equal_target == scope.target
    assert equal_target is not scope.target
    object.__setattr__(scope, "target", equal_target)

    projection = project_target_pre_cell(result)

    assert projection.binding.target is equal_target
    assert all(subject.target is equal_target for subject in projection.subjects)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate"])
def test_projector_rejects_terminal_population_shape_errors(
    tmp_path: Path,
    mutation: str,
) -> None:
    _, _, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=(),
        group_specs=(((), "provisional"),),
    )
    if mutation == "missing":
        object.__setattr__(result, "terminal_subjects", result.terminal_subjects[:-1])
    elif mutation == "extra":
        terminal = result.terminal_subjects[0]
        object.__setattr__(
            terminal,
            "canonical_sample_identity_id",
            "xic.canonical-sample.v1:extra",
        )
    else:
        terminal = result.terminal_subjects[0]
        object.__setattr__(
            result,
            "terminal_subjects",
            result.terminal_subjects + (terminal,),
        )

    with pytest.raises(TargetProjectionContractError, match="terminal"):
        project_target_pre_cell(result)


def test_projector_rejects_candidate_sample_crossing(tmp_path: Path) -> None:
    _, _, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=(),
        group_specs=(((), "provisional"),),
    )
    first, second = result.terminal_subjects
    first_candidate = first.candidate_id
    object.__setattr__(first, "candidate_id", second.candidate_id)
    object.__setattr__(second, "candidate_id", first_candidate)

    with pytest.raises(TargetProjectionContractError, match="capture-binding sample"):
        project_target_pre_cell(result)


def test_projector_rejects_observed_trace_group_crossing(tmp_path: Path) -> None:
    _, _, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=(((), "provisional"),),
    )
    observed = next(
        subject for subject in result.terminal_subjects
        if isinstance(subject, ObservedSearchSubject)
    )
    object.__setattr__(observed, "trace_group_id", "trace-crossed")

    with pytest.raises(TargetProjectionContractError, match="trace-group"):
        project_target_pre_cell(result)


def test_non_owner_and_cross_request_observed_hypotheses_fail_closed(
    tmp_path: Path,
) -> None:
    fixture, _, result, hypotheses = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=(((), "provisional"),),
    )
    observed = next(
        subject for subject in result.terminal_subjects
        if isinstance(subject, ObservedSearchSubject)
    )
    raw = copy(hypotheses["sample-a"])
    object.__setattr__(raw, "hypothesis_id", "raw-hypothesis")
    object.__setattr__(observed, "hypothesis", raw)
    with pytest.raises(TargetProjectionContractError, match="owner group member"):
        project_target_pre_cell(result)

    crossed_path = tmp_path / "crossed"
    crossed_path.mkdir()
    crossed = build_package2_no_raw_fixture(
        crossed_path,
        sample_stems=("sample-a", "sample-c"),
    )
    crossed_hypothesis = _owner_hypothesis(crossed, "sample-a", 8.0)
    object.__setattr__(observed, "hypothesis", crossed_hypothesis)
    object.__setattr__(observed, "trace_group_id", crossed_hypothesis.trace_group_id)
    with pytest.raises(TargetProjectionContractError, match="owner group member"):
        project_target_pre_cell(result)
    assert fixture.request != crossed.request


def test_cross_sample_observed_hypothesis_fails_closed(tmp_path: Path) -> None:
    fixture, _, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=(((), "provisional"),),
    )
    observed = next(
        subject for subject in result.terminal_subjects
        if isinstance(subject, ObservedSearchSubject)
    )
    crossed_hypothesis = _owner_hypothesis(fixture, "sample-b", 8.0)
    object.__setattr__(observed, "hypothesis", crossed_hypothesis)
    object.__setattr__(observed, "trace_group_id", crossed_hypothesis.trace_group_id)

    with pytest.raises(TargetProjectionContractError, match="owner group member"):
        project_target_pre_cell(result)


def test_owner_member_constructor_failure_is_projection_contract_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=(((), "provisional"),),
    )

    def fail_owner_constructor(**_: object) -> object:
        raise RuntimeError("owner constructor failed")

    monkeypatch.setattr(
        target_projection_owner,
        "group_member_subject_from_peak_hypothesis",
        fail_owner_constructor,
    )

    with pytest.raises(TargetProjectionContractError, match="owner group member"):
        project_target_pre_cell(result)


def test_duplicate_final_population_is_rejected(tmp_path: Path) -> None:
    _, _, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=(),
        group_specs=(((), "provisional"),),
    )
    group = result.final_population[0]
    object.__setattr__(
        result.final_cross_sample_receipt,
        "final_population",
        (group, group),
    )

    with pytest.raises(TargetProjectionContractError, match="population"):
        project_target_pre_cell(result)


@pytest.mark.parametrize("mutation", ["member", "group"])
def test_forged_final_population_owner_basis_is_rejected(
    tmp_path: Path,
    mutation: str,
) -> None:
    _, _, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=(),
        group_specs=(((), "provisional"),),
    )
    group = result.final_population[0]
    target = group.member_subjects[0] if mutation == "member" else group
    object.__setattr__(target, "basis_bytes", b"forged-owner-basis")

    with pytest.raises(TargetProjectionContractError, match="owner identity"):
        project_target_pre_cell(result)


@pytest.mark.parametrize(
    "ingress",
    [
        "finalized_envelope",
        "scope_target",
        "scope_snapshot",
        "scope_capture",
        "canonical_sample",
        "observed_terminal",
        "negative_terminal",
        "final_receipt",
        "group",
        "member",
        "member_constructor",
    ],
)
def test_projection_revalidates_each_consumed_owner_ingress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ingress: str,
) -> None:
    _, scope, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=(((), "provisional"),),
    )
    observed = next(
        subject for subject in result.terminal_subjects
        if isinstance(subject, ObservedSearchSubject)
    )
    negative = next(
        subject for subject in result.terminal_subjects
        if isinstance(subject, SearchedNoObservationSubject)
    )
    group = result.final_population[0]

    if ingress == "finalized_envelope":
        object.__setattr__(
            result,
            "canonical_samples",
            tuple(reversed(result.canonical_samples)),
        )
    elif ingress == "scope_target":
        object.__setattr__(scope.target, "lookup_label", "crossed-target")
    elif ingress == "scope_snapshot":
        object.__setattr__(scope.target_snapshot, "snapshot_id", "crossed-snapshot")
    elif ingress == "scope_capture":
        object.__setattr__(
            scope.capture_bindings[0],
            "sample_identity_id",
            "xic.canonical-sample.v1:crossed",
        )
    elif ingress == "canonical_sample":
        object.__setattr__(scope.samples[0], "lookup_token", "crossed-sample")
    elif ingress == "observed_terminal":
        object.__setattr__(observed, "trace_group_id", "crossed-trace")
    elif ingress == "negative_terminal":
        object.__setattr__(negative, "detection_status", "unknown")
    elif ingress == "final_receipt":
        object.__setattr__(
            result.final_cross_sample_receipt,
            "processing_request_id",
            "xic.processing-request.v1:crossed",
        )
    elif ingress == "group":
        object.__setattr__(group, "basis_bytes", b"crossed-group-basis")
    elif ingress == "member":
        object.__setattr__(
            group.member_subjects[0],
            "basis_bytes",
            b"crossed-member-basis",
        )
    else:
        def fail_owner_constructor(**_: object) -> object:
            raise RuntimeError("owner constructor failed")

        monkeypatch.setattr(
            target_projection_owner,
            "group_member_subject_from_peak_hypothesis",
            fail_owner_constructor,
        )

    with pytest.raises(TargetProjectionContractError):
        project_target_pre_cell(result)


def _finalized_case(
    tmp_path: Path,
    *,
    sample_stems: tuple[str, ...],
    observed_stems: tuple[str, ...],
    group_specs: tuple[tuple[tuple[str, ...], str], ...],
    reverse_scope_samples: bool = False,
) -> tuple[
    Package2NoRawFixture,
    TargetSearchScope,
    FinalizedScientificAlignment,
    dict[str, object],
]:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=sample_stems)
    scope_samples = (
        tuple(reversed(fixture.samples))
        if reverse_scope_samples
        else fixture.samples
    )
    target_value = fixture.target.definition.target
    method_subject = cid_nl_method_subject_from_target(fixture.target)
    bindings = tuple(
        _capture_binding(
            candidate_id=f"candidate-{sample.lookup_token}",
            method_subject_id=method_subject.method_subject_id,
            sample=sample,
            acquisition_source_id=resolve_request_acquisition_source(
                processing_owners=fixture.owners,
                sample=sample,
            ).acquisition_source_id,
            mz=target_value.mz,
            ppm_tol=target_value.ppm_tol,
            rt_min=target_value.rt_min,
            rt_max=target_value.rt_max,
        )
        for sample in scope_samples
    )
    scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=fixture.request,
        samples=scope_samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=_peak_config(tmp_path),
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=tuple(binding.candidate_id for binding in bindings),
        capture_bindings=bindings,
        target_snapshot=fixture.target.snapshot,
        target=fixture.target,
    )
    assessments = tuple(
        _support_assessment(
            fixture,
            sample.lookup_token,
            suffix="target-projection",
            apex_rt=8.0,
            scan_number=80 + index,
            seed_count=2,
        )
        for index, sample in enumerate(fixture.samples)
    )
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=tuple(
            zip(fixture.samples, assessments, strict=True)
        ),
    )
    predecessor = validate_cross_sample_peak_group_identity(
        spine.group,
        require_canonical=True,
    )
    member_by_stem = {
        sample.lookup_token: next(
            member
            for member in predecessor.member_subjects
            if member.canonical_sample_identity_id
            == sample.identity.sample_identity_id
        )
        for sample in fixture.samples
    }
    hypothesis_by_stem = {
        stem: copy(
            next(
                hypothesis
                for hypothesis in spine.support_hypotheses
                if hypothesis.hypothesis_id == member.subject_id
            )
        )
        for stem, member in member_by_stem.items()
    }
    for stem, hypothesis in hypothesis_by_stem.items():
        object.__setattr__(hypothesis, "trace_group_id", f"trace-{stem}")
    groups = []
    for group_stems, disposition in group_specs:
        members = [member_by_stem[stem] for stem in group_stems]
        if not members:
            members.extend(predecessor.member_subjects)
        groups.append(
            mint_cross_sample_group_identity(
                processing_request_id=fixture.request.processing_request_id,
                member_subjects=tuple(members),
                disposition=disposition,
            )
        )
    receipt = _lineage_final_receipt(
        _receipt_fixture(fixture, fixture.samples),
        predecessors=(predecessor,),
        successors=tuple(groups),
    )
    coverage = _coverage_for_groups(
        fixture,
        (predecessor,),
        fixture.samples,
        planned=True,
    )
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )
    observed_set = set(observed_stems)
    hypotheses: dict[str, object] = {}
    terminals = []
    for index, sample in enumerate(fixture.samples):
        candidate_id = f"candidate-{sample.lookup_token}"
        if sample.lookup_token in observed_set:
            hypothesis = hypothesis_by_stem[sample.lookup_token]
            hypotheses[sample.lookup_token] = hypothesis
            terminals.append(
                ObservedSearchSubject(
                    candidate_id=candidate_id,
                    canonical_sample_identity_id=(
                        sample.identity.sample_identity_id
                    ),
                    trace_group_id=hypothesis.trace_group_id,
                    hypothesis=hypothesis,
                    _mint_token=finalizer_owner._FINALIZER_MINT_TOKEN,
                )
            )
        else:
            terminals.append(
                SearchedNoObservationSubject(
                    candidate_id=candidate_id,
                    canonical_sample_identity_id=(
                        sample.identity.sample_identity_id
                    ),
                    trace_group_id=f"trace-{sample.lookup_token}",
                    detection_status="not_detected",
                    _mint_token=finalizer_owner._FINALIZER_MINT_TOKEN,
                )
            )
    cell_observations = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=spine.cells,
    )
    result = FinalizedScientificAlignment(
        scope=scope,
        processing_request=scope.request,
        canonical_samples=tuple(
            sorted(
                scope.samples,
                key=lambda sample: sample.identity.sample_identity_id,
            )
        ),
        terminal_subjects=tuple(
            sorted(terminals, key=finalizer_owner._terminal_sort_key)
        ),
        cell_observation_handoff=handoff,
        cell_observations=cell_observations,
        final_cross_sample_receipt=receipt,
        _mint_token=finalizer_owner._FINALIZER_MINT_TOKEN,
    )
    return fixture, scope, result, hypotheses


def _owner_hypothesis(
    fixture: Package2NoRawFixture,
    sample_stem: str,
    offset: float,
) -> object:
    observation = issue161_peak(
        fixture,
        sample_stem,
        apex_rt=8.0 + offset,
        rt_left_min=7.9 + offset,
        rt_right_min=8.1 + offset,
    )
    hypothesis = compose_ms1_observation_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        peak_observation=observation,
    )
    object.__setattr__(hypothesis, "trace_group_id", f"trace-{sample_stem}-{offset}")
    return hypothesis


def _final_receipt(
    fixture: Package2NoRawFixture,
    groups: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSampleFinalReceipt:
    population = tuple(sorted(groups, key=lambda group: group.group_hypothesis_id))
    group = population[0]
    member = group.member_subjects[0]
    sample = next(
        sample
        for sample in fixture.samples
        if sample.identity.sample_identity_id == member.canonical_sample_identity_id
    )
    target_value = fixture.target.definition.target
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=sample,
    )
    request = XICRequest(
        mz=target_value.mz,
        rt_min=target_value.rt_min,
        rt_max=target_value.rt_max,
        ppm_tol=target_value.ppm_tol,
    )
    request_identity = mint_owner_backfill_request_identity(
        sample_stem=sample.lookup_token,
        acquisition_source=source,
        request=request,
        backend="raw",
        window_strategy="exact",
        superwindow_span_factor=1,
        validation_mode="validation",
    )
    item = OwnerBackfillRequestItem(
        feature=SimpleNamespace(group_hypothesis_id=group.group_hypothesis_id),
        sample_stem=sample.lookup_token,
        request=request,
        preferred_rt=8.0,
    )
    logical_request = mint_owner_backfill_logical_request_member(
        logical_index=0,
        group_hypothesis_id=group.group_hypothesis_id,
        item=item,
    )
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=population,
        successors=population,
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )
    subject = mint_observation_repair_subject(
        request_identity=request_identity,
        logical_request=logical_request,
        predecessor_group=group,
        member_subject=member,
        sample_identity=sample,
        successor_application=application,
    )
    repair_receipt = repair_owner.ObservationRepairReceipt(
        receipt_id=repair_owner._receipt_id(
            subject_id=subject.subject_id,
            repair_epoch=1,
            outcome="no_repair",
            sample_stem=sample.lookup_token,
            evidence=None,
        ),
        subject=subject,
        subject_id=subject.subject_id,
        repair_epoch=1,
        outcome="no_repair",
        sample_stem=sample.lookup_token,
        evidence=None,
        _mint_token=repair_owner._RECEIPT_MINT_TOKEN,
    )
    trajectory_id = "xic.mode-track-trajectory-input.v1:test"
    state = CrossSampleReconciledState(
        processing_request_id=fixture.request.processing_request_id,
        population=population,
        repair_receipt_id=repair_receipt.receipt_id,
        repair_epoch=1,
        repair_outcome="no_repair",
        member_subject_ids=tuple(
            sorted(
                member.member_subject_id
                for group in population
                for member in group.member_subjects
            )
        ),
        trajectory_input_id=trajectory_id,
        trajectory_changed=False,
    )
    basis = reconciliation_owner._final_receipt_basis(
        processing_request_id=fixture.request.processing_request_id,
        post_mt0_application=application,
        repair_receipt=repair_receipt,
        repair_epoch=1,
        cs2_state=state,
        pre_trajectory_input_id=trajectory_id,
        post_trajectory_input_id=trajectory_id,
        mode_track_proposal=None,
        final_application=application,
        final_population=population,
    )
    return CrossSampleFinalReceipt(
        receipt_id=reconciliation_owner.typed_identity_digest(
            reconciliation_owner._FINAL_RECEIPT_PREFIX,
            basis,
        ),
        processing_request_id=fixture.request.processing_request_id,
        post_mt0_application=application,
        repair_receipt=repair_receipt,
        repair_epoch=1,
        cs2_state=state,
        pre_trajectory_input_id=trajectory_id,
        post_trajectory_input_id=trajectory_id,
        mode_track_proposal=None,
        final_application=application,
        final_population=population,
        basis_bytes=basis,
        _mint_token=reconciliation_owner._FINAL_RECEIPT_MINT_TOKEN,
    )
