from __future__ import annotations

from copy import copy
from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.alignment.projection.target.pre_cell as target_projection_owner
import xic_extractor.alignment.result.scientific_alignment as finalizer_owner
from tests.cid_nl_peak_support_no_raw_fixture import cid_nl_peak
from tests.processing_identity_no_raw_fixture import (
    ProcessingIdentityNoRawFixture,
    build_processing_identity_no_raw_fixture,
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
from xic_extractor.alignment.cell_observation.observations import (
    compose_final_group_cell_observation_population,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSamplePartitionTransition,
    mint_cross_sample_group_identity,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.projection.target.pre_cell import (
    TargetProjectionContractError,
    project_target_pre_cell,
)
from xic_extractor.alignment.result import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
    SearchedNoObservationSubject,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
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


def test_public_target_projection_entry_point_exists() -> None:
    assert callable(project_target_pre_cell)


@pytest.mark.parametrize(
    ("observed", "group_specs", "variant"),
    [
        ((), (((), "provisional"),), "unbound"),
        (
            ("sample-a", "sample-b"),
            ((("sample-a", "sample-b"), "established"),),
            "bound",
        ),
        (
            ("sample-a", "sample-b", "sample-c", "sample-d"),
            (
                (("sample-a", "sample-b"), "established"),
                (("sample-c", "sample-d"), "established"),
            ),
            "bound",
        ),
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
            set(observed).union(stem for members, _ in group_specs for stem in members)
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
            group.group_hypothesis_id for group in projection.binding.eligible_groups
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
            == hypotheses[stem]
            .peak_observation_refs[0]
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
        group_specs=((("sample-a", "sample-b"), "provisional"),),
        reverse_scope_samples=True,
    )

    projection = project_target_pre_cell(result)

    assert projection.binding.variant == "bound"
    assert tuple(
        subject.sample.identity.sample_identity_id for subject in projection.subjects
    ) == tuple(
        sample.identity.sample_identity_id for sample in result.canonical_samples
    )
    subjects_by_sample = {
        subject.sample.lookup_token: subject for subject in projection.subjects
    }
    assert isinstance(
        subjects_by_sample["sample-a"].terminal_subject,
        ObservedSearchSubject,
    )
    assert isinstance(
        subjects_by_sample["sample-b"].terminal_subject,
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
            "xic.canonical-sample:extra",
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


def test_cross_sample_observed_hypothesis_fails_closed(tmp_path: Path) -> None:
    fixture, _, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=(((), "provisional"),),
    )
    observed = next(
        subject
        for subject in result.terminal_subjects
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


def _finalized_case(
    tmp_path: Path,
    *,
    sample_stems: tuple[str, ...],
    observed_stems: tuple[str, ...],
    group_specs: tuple[tuple[tuple[str, ...], str], ...],
    reverse_scope_samples: bool = False,
) -> tuple[
    ProcessingIdentityNoRawFixture,
    TargetSearchScope,
    FinalizedScientificAlignment,
    dict[str, object],
]:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=sample_stems
    )
    scope_samples = (
        tuple(reversed(fixture.samples)) if reverse_scope_samples else fixture.samples
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
        sample_assessments=tuple(zip(fixture.samples, assessments, strict=True)),
    )
    predecessor = validate_cross_sample_peak_group_identity(
        spine.group,
        require_canonical=True,
    )
    member_by_stem = {
        sample.lookup_token: next(
            member
            for member in predecessor.member_subjects
            if member.canonical_sample_identity_id == sample.identity.sample_identity_id
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
                    canonical_sample_identity_id=(sample.identity.sample_identity_id),
                    trace_group_id=hypothesis.trace_group_id,
                    hypothesis=hypothesis,
                    _mint_token=finalizer_owner._FINALIZER_MINT_TOKEN,
                )
            )
        else:
            terminals.append(
                SearchedNoObservationSubject(
                    candidate_id=candidate_id,
                    canonical_sample_identity_id=(sample.identity.sample_identity_id),
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
        grouping_result=receipt,
        _mint_token=finalizer_owner._FINALIZER_MINT_TOKEN,
    )
    return fixture, scope, result, hypotheses


def _owner_hypothesis(
    fixture: ProcessingIdentityNoRawFixture,
    sample_stem: str,
    offset: float,
) -> object:
    observation = cid_nl_peak(
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
    fixture: ProcessingIdentityNoRawFixture,
    groups: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSampleGroupingResult:
    population = tuple(sorted(groups, key=lambda group: group.group_hypothesis_id))
    return CrossSampleGroupingResult(
        processing_owners=fixture.owners,
        partition_transition=CrossSamplePartitionTransition(
            status="assessable",
            predecessors=population,
            successors=population,
            lineage=(),
            reason="unchanged_membership_no_lineage",
        ),
        rt_frame=SampleRtFrame(),
    )
