"""Compose stable cross-sample membership before bounded missing-cell work."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSamplePartitionTransition,
    CrossSamplePeakGroupHypothesis,
    _expected_cross_sample_lineage,
    _hypothesis_from_owner_group,
    _identity_from_owners,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.edge_scoring import DriftLookupProtocol
from xic_extractor.alignment.feature_membership_inference import (
    FeatureInferenceOperationCounts,
    supports_existing_feature,
)
from xic_extractor.alignment.grouping_result import (
    CrossSampleGroupingContractError,
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackCandidateObservation,
)
from xic_extractor.alignment.mode_track.assignment_scope import ModeTrackAssignmentScope
from xic_extractor.alignment.mode_track.contracts import (
    AcceptedModeTrackRefinement,
    apply_accepted_mode_track_refinement,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.capture import SampleLocalCapture
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.search_scope import (
    TargetSearchScope,
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

if TYPE_CHECKING:
    from xic_extractor.alignment.mode_track.refinement import ModeTrackRefinementRun


def apply_mode_track_refinement_run_for_workflow(
    *,
    processing_owners: ProcessingOwnerReferences,
    captures: Sequence[SampleLocalCapture],
    run: ModeTrackRefinementRun,
) -> CrossSampleGroupingResult:
    """Apply the owner's complete initial refinement once, then fix membership."""
    from xic_extractor.alignment.mode_track.refinement import ModeTrackRefinementRun

    if (
        type(run) is not ModeTrackRefinementRun
        or run.fit_once_source.processing_owners is not processing_owners
    ):
        raise CrossSampleGroupingContractError(
            "refinement crossed its processing owners"
        )
    _validate_capture_owner_hypotheses(
        captures=captures,
        owners=run.owners,
        source_by_id={
            candidate.peak_hypothesis_id: candidate.peak_hypothesis
            for sample in run.fit_once_source.sample_sources
            for candidate in sample.candidates
        },
    )
    # Transactions are disjoint components closed by the refinement producer.
    # Accumulate their answers once instead of rebuilding the whole population
    # after every component.
    successors: list[CrossSampleGroupIdentity] = []
    for transaction in run.transactions:
        if type(transaction.proposal) is AcceptedModeTrackRefinement:
            application = apply_accepted_mode_track_refinement(
                trajectory_input=transaction.trajectory_input,
                proposal=transaction.proposal,
                application_context=transaction.application_context,
                population=run.predecessors,
            )
            successors.extend(application.successors)
        else:
            successors.extend(transaction.application_context.predecessors)
    population = tuple(sorted(successors, key=lambda group: group.group_hypothesis_id))
    partition = _partition_transition(
        predecessors=run.predecessors, successors=population
    )
    return CrossSampleGroupingResult(
        processing_owners=processing_owners, partition_transition=partition
    )


def build_initial_cid_nl_cross_sample_groups(
    *,
    processing_owners: ProcessingOwnerReferences,
    captures: Sequence[SampleLocalCapture],
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None = None,
    known_istd_reference: KnownIstdReference | None = None,
    operation_counts_sink: list[FeatureInferenceOperationCounts] | None = None,
    retained_hypotheses_by_assessment: Mapping[
        tuple[str, str], tuple[PeakHypothesis, ...]
    ]
    | None = None,
) -> tuple[
    ModeTrackAssignmentScope,
    CrossSampleGroupingResult,
    tuple[ModeTrackCandidateObservation, ...],
]:
    """Infer stable CID-NL groups without producing unused RT trajectories."""
    from xic_extractor.alignment.mode_track.producer import (
        bootstrap_cid_nl_initial_population,
    )

    batch_samples = processing_owners.sample_resolutions
    assignment_scope, _anchors, sample_candidates, drift_lookup = (
        bootstrap_cid_nl_initial_population(
            processing_owners=processing_owners,
            sample_assessments=sample_assessments,
            alignment_config=alignment_config,
            sample_universe=frozenset(
                sample.metadata.raw_stem for sample in batch_samples
            ),
            injection_order={
                sample.metadata.raw_stem: sample.metadata.injection_order
                for sample in batch_samples
                if sample.metadata.injection_order is not None
            },
            drift_lookup=drift_lookup,
            retain_observation_only_candidates=True,
            known_istd_reference=known_istd_reference,
            operation_counts_sink=operation_counts_sink,
            retained_hypotheses_by_assessment=retained_hypotheses_by_assessment,
        )
    )
    groups = assignment_scope.groups
    _validate_capture_owner_hypotheses(
        captures=captures,
        owners=tuple(owner for group in groups for owner in group.owners),
        source_by_id={
            candidate.peak_hypothesis_id: candidate.peak_hypothesis
            for _, candidates in sample_candidates
            for candidate in candidates
        },
    )
    identities = tuple(
        validate_cross_sample_peak_group_identity(group, require_canonical=True)
        for group in groups
    )
    if any(identity is None for identity in identities):
        raise CrossSampleGroupingContractError(
            "initial group lost its canonical identity"
        )
    population = tuple(
        sorted(
            (identity for identity in identities if identity is not None),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    result = CrossSampleGroupingResult(
        processing_owners=processing_owners,
        partition_transition=_partition_transition(
            predecessors=population, successors=population
        ),
        drift_lookup=drift_lookup,
    )
    retained_candidates = tuple(
        sorted(
            (
                candidate
                for _, candidates in sample_candidates
                for candidate in candidates
            ),
            key=lambda candidate: candidate.candidate_observation_id,
        )
    )
    return assignment_scope, result, retained_candidates


def admit_cid_nl_group_backfills(
    *,
    result: CrossSampleGroupingResult,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    backfills: Mapping[
        tuple[str, str, str],
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef],
    ],
    alignment_config: AlignmentConfig,
) -> tuple[
    CrossSampleGroupingResult,
    tuple[CrossSamplePeakGroupHypothesis, ...],
    dict[str, str],
    dict[tuple[str, str], tuple[PeakHypothesis, ...]],
]:
    """Extend only the requested group with uniquely claimed supported evidence.

    Initial owners never move. A candidate claimed by several groups, an
    occupied sample, or an unsupported match remains unassigned evidence.
    """
    by_id = {group.group_hypothesis_id: group for group in groups}
    claims: dict[tuple[str, str], set[str]] = defaultdict(set)
    occupied = {
        (member.canonical_sample_identity_id, observation_id)
        for group in groups
        for member in group.member_subjects
        for observation_id in member.physical_observation_ids
    }
    proposals = []
    retained: dict[tuple[str, str], tuple[PeakHypothesis, ...]] = {}
    for key, (sample, assessment) in sorted(backfills.items()):
        group_id, sample_id, subject_id = key
        group = by_id[group_id]
        if (
            sample_id != sample.identity.sample_identity_id
            or subject_id != assessment.content.method_subject.method_subject_id
        ):
            raise CrossSampleGroupingContractError(
                "backfill crossed its sample or subject"
            )
        if assessment.content.method_result != "SUPPORT":
            continue
        hypotheses = compose_peak_hypotheses_from_assessment_universe(
            processing_owners=result.processing_owners,
            sample=sample,
            assessment=assessment,
        )
        if len(hypotheses) != 1:
            continue
        hypothesis = hypotheses[0]
        owner = owner_from_cid_nl_assessment_support(
            sample=sample, assessment=assessment, hypothesis=hypothesis
        )
        # The content ID excludes query provenance and binds the exact measured
        # chromatogram and integral. Equal coordinates alone are insufficient.
        physical_key = (
            sample_id,
            hypothesis.peak_observation_refs[0].content.peak_observation_content_id,
        )
        if physical_key in occupied:
            continue
        if any(
            member.canonical_sample_identity_id == sample_id
            for member in group.member_subjects
        ):
            continue
        if not supports_existing_feature(
            owner,
            group.owners,
            config=alignment_config,
            drift_lookup=result.drift_lookup,
        ):
            continue
        claims[physical_key].add(group_id)
        proposals.append((group_id, physical_key, owner, hypothesis, assessment))
    additions: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    cell_claims: dict[tuple[str, str], int] = defaultdict(int)
    for group_id, physical_key, *_ in proposals:
        cell_claims[(group_id, physical_key[0])] += 1
    for group_id, physical_key, owner, hypothesis, assessment in proposals:
        if (
            len(claims[physical_key]) != 1
            or cell_claims[(group_id, physical_key[0])] != 1
        ):
            continue
        additions[group_id].append(owner)
        retained[
            (physical_key[0], assessment.artifact.content_identity.artifact_content_id)
        ] = (hypothesis,)
    completed = []
    successor_ids = {}
    admitted: list[CrossSampleGroupMemberSubject] = []
    for group in groups:
        owners = additions.get(group.group_hypothesis_id, [])
        if not owners:
            successor = group
        else:
            combined = tuple(
                sorted((*group.owners, *owners), key=lambda item: item.owner_id)
            )
            identity = _identity_from_owners(
                combined,
                processing_request_id=result.processing_request_id,
                disposition=group.disposition,
            )
            successor = replace(
                _hypothesis_from_owner_group(
                    combined,
                    group_identity=identity,
                    public_family_id=group.public_family_id,
                    evidence=group.evidence,
                    identity_conflict=group.identity_conflict,
                    review_only=group.review_only,
                ),
                # The original inference/ISTD binding remains about its original
                # owners; backfill does not label new owners as known standards.
                inference_basis=group.inference_basis,
                inference_decisions=group.inference_decisions,
                # Keep the reference coordinates used to choose the canonical
                # backfill subject. New members cannot redirect another sample's
                # already completed group-bound query to a different subject.
                group_center_mz=group.group_center_mz,
                group_center_rt=group.group_center_rt,
            )
            admitted.extend(
                cast(CrossSampleGroupMemberSubject, owner.cross_sample_member_subject)
                for owner in owners
            )
        completed.append(successor)
        successor_ids[group.group_hypothesis_id] = successor.group_hypothesis_id
    if not admitted:
        return (
            result,
            tuple(sorted(groups, key=lambda group: group.group_hypothesis_id)),
            successor_ids,
            retained,
        )
    identities = tuple(
        sorted(
            (
                cast(
                    CrossSampleGroupIdentity,
                    validate_cross_sample_peak_group_identity(
                        group, require_canonical=True
                    ),
                )
                for group in completed
            ),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    partition = CrossSamplePartitionTransition(
        status="assessable",
        predecessors=result.initial_population,
        successors=identities,
        lineage=_expected_cross_sample_lineage(
            predecessors=result.initial_population, successors=identities
        ),
        reason="membership_changed",
        admitted_members=tuple(
            sorted(admitted, key=lambda member: member.member_subject_id)
        ),
    )
    return (
        replace(result, partition_transition=partition),
        tuple(sorted(completed, key=lambda group: group.group_hypothesis_id)),
        successor_ids,
        retained,
    )


def _partition_transition(
    *,
    predecessors: tuple[CrossSampleGroupIdentity, ...],
    successors: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSamplePartitionTransition:
    return CrossSamplePartitionTransition(
        status="assessable",
        predecessors=predecessors,
        successors=successors,
        lineage=_expected_cross_sample_lineage(
            predecessors=predecessors, successors=successors
        ),
        reason=(
            "unchanged_membership_no_lineage"
            if predecessors == successors
            else "membership_changed"
        ),
    )


def _validate_capture_owner_hypotheses(
    *,
    captures: Sequence[SampleLocalCapture],
    owners: Sequence[SampleLocalMS1Owner],
    source_by_id: Mapping[str, PeakHypothesis],
) -> None:
    capture_tuple = tuple(captures)
    if not capture_tuple or any(
        type(capture) is not SampleLocalCapture for capture in capture_tuple
    ):
        raise CrossSampleGroupingContractError(
            "grouping workflow captures do not close exact owner results"
        )
    capture_scope = capture_tuple[0].scope
    if type(capture_scope) not in {TargetSearchScope, UntargetedSearchScope} or any(
        capture.scope is not capture_scope for capture in capture_tuple
    ):
        raise CrossSampleGroupingContractError(
            "grouping workflow captures crossed their exact SearchScope"
        )
    candidate_ids = tuple(capture.candidate_id for capture in capture_tuple)
    declared_candidate_ids = capture_scope.declared_candidate_ids
    if candidate_ids != declared_candidate_ids or len(candidate_ids) != len(
        set(candidate_ids)
    ):
        raise CrossSampleGroupingContractError(
            "grouping workflow captures do not preserve the declared candidate order"
        )
    selected_ids = tuple(
        sorted(
            capture.selected_hypothesis.hypothesis_id
            for capture in capture_tuple
            if capture.selected_hypothesis is not None
        )
    )
    owner_ids = tuple(sorted(owner.owner_id for owner in owners))
    if not set(selected_ids).issubset(source_by_id) or len(owner_ids) != len(
        set(owner_ids)
    ):
        raise CrossSampleGroupingContractError(
            "grouping workflow captures cross the exact retained candidate population"
        )
    if any(
        capture.selected_hypothesis.integration
        is not source_by_id[capture.selected_hypothesis.hypothesis_id].integration
        for capture in capture_tuple
        if capture.selected_hypothesis is not None
    ):
        raise CrossSampleGroupingContractError(
            "grouping workflow captures crossed exact retained physical integration"
        )
    if not set(owner_ids).issubset(source_by_id) or any(
        owner.selected_integration is not source_by_id[owner.owner_id].integration
        or owner.owner_apex_rt != source_by_id[owner.owner_id].integration.rt_apex_min
        or owner.owner_peak_start_rt
        != source_by_id[owner.owner_id].integration.rt_left_min
        or owner.owner_peak_end_rt
        != source_by_id[owner.owner_id].integration.rt_right_min
        or owner.owner_area
        != source_by_id[owner.owner_id].integration.area_raw_counts_seconds
        or owner.owner_height != source_by_id[owner.owner_id].integration.height_raw
        for owner in owners
    ):
        raise CrossSampleGroupingContractError(
            "grouping workflow source crossed exact owner physical integration"
        )
