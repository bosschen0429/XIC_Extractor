"""Compose stable cross-sample membership before bounded missing-cell work."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSamplePartitionTransition,
    CrossSamplePeakGroupHypothesis,
    _expected_cross_sample_lineage,
    _hypothesis_from_owner_group,
    _identity_from_owners,
    construct_cross_sample_peak_group_hypotheses,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.edge_scoring import DriftLookupProtocol
from xic_extractor.alignment.feature_membership_inference import (
    FeatureInferenceOperationCounts,
    FeatureMembershipGroup,
    RtCoordinates,
    supports_existing_feature,
)
from xic_extractor.alignment.grouping_bootstrap import (
    InitialGroupingScope,
    RetainedGroupingCandidate,
    bootstrap_cid_nl_initial_population,
)
from xic_extractor.alignment.grouping_result import (
    CrossSampleGroupingContractError,
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
    owner_from_cid_nl_assessment_observation,
    owner_from_cid_nl_assessment_support,
    owner_from_ms1_observation_hypothesis,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.peak_detection.capture import SampleLocalCapture
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
    HYPOTHESIS_METHOD_MS1_OBSERVATION,
)
from xic_extractor.peak_detection.search_scope import (
    TargetSearchScope,
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


def build_targeted_cross_sample_groups(
    *,
    processing_owners: ProcessingOwnerReferences,
    captures: Sequence[SampleLocalCapture],
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None = None,
) -> CrossSampleGroupingResult:
    """Close Targeted membership from the exact selected capture in each cell."""

    sample_by_id = {
        sample.identity.sample_identity_id: sample
        for sample in processing_owners.sample_resolutions
    }
    assessment_by_id = {
        sample.identity.sample_identity_id: assessment
        for sample, assessment in sample_assessments
    }
    owners: list[SampleLocalMS1Owner] = []
    selected_by_id: dict[str, PeakHypothesis] = {}
    for capture in captures:
        selected = capture.selected_hypothesis
        if selected is None:
            continue
        observation = selected.peak_observation_refs[0]
        sample_id = observation.content.canonical_sample_identity_id
        sample = sample_by_id.get(sample_id)
        if sample is None or sample_id in selected_by_id:
            raise CrossSampleGroupingContractError(
                "Targeted selection crossed or duplicated its canonical sample"
            )
        assessment = assessment_by_id.get(sample_id)
        if selected.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL:
            if assessment is None:
                raise CrossSampleGroupingContractError(
                    "Targeted CID-NL selection lacks its method assessment"
                )
            owner = owner_from_cid_nl_assessment_support(
                sample=sample, assessment=assessment, hypothesis=selected
            )
        elif selected.hypothesis_method == HYPOTHESIS_METHOD_MS1_OBSERVATION:
            owner = (
                owner_from_cid_nl_assessment_observation(
                    processing_owners=processing_owners,
                    sample=sample,
                    assessment=assessment,
                    hypothesis=selected,
                )
                if assessment is not None
                else owner_from_ms1_observation_hypothesis(
                    processing_owners=processing_owners,
                    sample=sample,
                    hypothesis=selected,
                )
            )
        else:
            raise CrossSampleGroupingContractError(
                "Targeted selection has an unsupported hypothesis method"
            )
        owners.append(owner)
        selected_by_id[sample_id] = selected

    if captures:
        _validate_capture_owner_hypotheses(
            captures=captures,
            owners=owners,
            source_by_id={
                selected.hypothesis_id: selected for selected in selected_by_id.values()
            },
        )
    groups = construct_cross_sample_peak_group_hypotheses(
        tuple(owners),
        config=alignment_config,
        sample_universe=frozenset(
            sample.metadata.raw_stem for sample in processing_owners.sample_resolutions
        ),
        injection_order={
            sample.metadata.raw_stem: sample.metadata.injection_order
            for sample in processing_owners.sample_resolutions
            if sample.metadata.injection_order is not None
        },
        drift_lookup=drift_lookup,
    )
    identities = tuple(
        validate_cross_sample_peak_group_identity(group, require_canonical=True)
        for group in groups
    )
    population = tuple(
        sorted(
            (identity for identity in identities if identity is not None),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    if tuple(sorted(owner.owner_id for owner in owners)) != tuple(
        sorted(owner_id for group in groups for owner_id in group.owner_ids)
    ):
        raise CrossSampleGroupingContractError(
            "Targeted grouping lost or invented selected hypothesis identity"
        )
    return CrossSampleGroupingResult(
        processing_owners=processing_owners,
        partition_transition=_partition_transition(
            predecessors=population,
            successors=population,
        ),
        drift_lookup=drift_lookup,
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
    InitialGroupingScope,
    CrossSampleGroupingResult,
    tuple[RetainedGroupingCandidate, ...],
]:
    """Infer stable CID-NL groups without producing unused RT trajectories."""
    batch_samples = processing_owners.sample_resolutions
    grouping_scope, retained_candidates, drift_lookup = (
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
    groups = grouping_scope.groups
    _validate_capture_owner_hypotheses(
        captures=captures,
        owners=tuple(owner for group in groups for owner in group.owners),
        source_by_id={
            candidate.peak_hypothesis_id: candidate.peak_hypothesis
            for candidate in retained_candidates
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
    return grouping_scope, result, retained_candidates


def regroup_cid_nl_cross_sample_groups(
    *,
    processing_owners: ProcessingOwnerReferences,
    grouping_scope: InitialGroupingScope,
    grouping_result: CrossSampleGroupingResult,
    alignment_config: AlignmentConfig,
    injection_order: Mapping[str, int],
    rt_warp: RtCoordinates,
    backfill_half_window_min: float,
    operation_counts_sink: list[FeatureInferenceOperationCounts] | None = None,
) -> tuple[InitialGroupingScope, CrossSampleGroupingResult]:
    """Reconsider every ordinary owner after the batch anchor warp is known."""
    owners = tuple(owner for group in grouping_scope.groups for owner in group.owners)
    groups = group_cid_nl_owners(
        owners,
        config=alignment_config,
        sample_universe=frozenset(
            sample.metadata.raw_stem for sample in processing_owners.sample_resolutions
        ),
        injection_order=injection_order,
        drift_lookup=grouping_result.drift_lookup,
        rt_warp=rt_warp,
        backfill_half_window_min=backfill_half_window_min,
        operation_counts_sink=operation_counts_sink,
    )
    identities = tuple(
        validate_cross_sample_peak_group_identity(group, require_canonical=True)
        for group in groups
    )
    if any(identity is None for identity in identities):
        raise CrossSampleGroupingContractError(
            "window group lost its canonical identity"
        )
    population = tuple(
        sorted(
            (identity for identity in identities if identity is not None),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    scope = replace(grouping_scope, groups=groups)
    result = CrossSampleGroupingResult(
        processing_owners=processing_owners,
        partition_transition=_partition_transition(
            predecessors=population, successors=population
        ),
        drift_lookup=grouping_result.drift_lookup,
    )
    return scope, result


def group_cid_nl_owners(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    config: AlignmentConfig,
    sample_universe: frozenset[str],
    injection_order: Mapping[str, int],
    rt_warp: RtCoordinates | None,
    backfill_half_window_min: float,
    drift_lookup: DriftLookupProtocol | None = None,
    known_identity_bindings: Sequence[tuple[str, str]] = (),
    operation_counts_sink: list[FeatureInferenceOperationCounts] | None = None,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Run the shared membership, disjoint-merge and shoulder decisions."""
    groups = construct_cross_sample_peak_group_hypotheses(
        tuple(owners),
        config=config,
        sample_universe=sample_universe,
        injection_order=injection_order,
        drift_lookup=drift_lookup,
        known_identity_bindings=known_identity_bindings,
        rt_warp=rt_warp,
        operation_counts_sink=operation_counts_sink,
    )
    return reject_shoulder_groups(
        merge_sample_disjoint_groups(
            groups,
            config=config,
            backfill_half_window_min=backfill_half_window_min,
        ),
        config=config,
    )


def admit_cid_nl_group_backfills(
    *,
    result: CrossSampleGroupingResult,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    backfills: Mapping[
        tuple[str, str, str],
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef],
    ],
    alignment_config: AlignmentConfig,
    injection_order: Mapping[str, int] | None = None,
    rt_warp: RtCoordinates | None = None,
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
            injection_order=injection_order,
            rt_warp=rt_warp,
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


def reject_shoulder_groups(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    *,
    config: AlignmentConfig,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Reject an established group that is the split-off shoulder of another.

    Discovery can cut one chromatographic peak at a valley into a small shoulder
    and the peak. A sample holds one member per group, so the shoulders then form
    their own group, and its Backfill finds the whole peak where no shoulder was
    cut. A group is such a shoulder when more than half of its members share a
    boundary with a same-m/z member of one other established group and every
    member it shares a sample with is such a smaller, adjacent neighbour.
    """
    by_sample = {
        group.group_hypothesis_id: {owner.sample_stem: owner for owner in group.owners}
        for group in groups
        if group.disposition == "established"
    }
    rejected: dict[str, str] = {}
    for group_id, owners in by_sample.items():
        for other_id, others in by_sample.items():
            shared = owners.keys() & others.keys()
            if (
                other_id != group_id
                and 2 * len(shared) > len(owners)
                and all(
                    _is_shoulder_of(owners[sample], others[sample], config=config)
                    for sample in shared
                )
            ):
                rejected[group_id] = other_id
                break
    if not rejected:
        return groups
    return tuple(
        replace(
            group,
            disposition="rejected",
            evidence=(
                f"{group.evidence};shoulder_of:{rejected[group.group_hypothesis_id]}"
            ),
        )
        if group.group_hypothesis_id in rejected
        else group
        for group in groups
    )


def merge_sample_disjoint_groups(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    *,
    config: AlignmentConfig,
    backfill_half_window_min: float,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Merge established rows of one feature that no sample tells apart.

    Two peaks are only evidenced by one sample holding both. Rows of the same
    m/z with no shared sample, whose centers lie within one Backfill half
    window, would Backfill each other's peak, so they are one feature. Closer
    rows merge first; a merge never joins two rows that share a sample.
    """
    candidates = sorted(
        (
            group
            for group in groups
            if group.disposition == "established"
            and not (
                group.inference_basis is not None
                and group.inference_basis.known_identity_label
            )
        ),
        key=lambda group: group.family_center_mz,
    )
    pairs = []
    for index, first in enumerate(candidates):
        for second in candidates[index + 1 :]:
            if not within_ppm(
                first.family_center_mz, second.family_center_mz, config.preferred_ppm
            ):
                break
            gap = abs(first.family_center_rt - second.family_center_rt)
            if gap <= backfill_half_window_min:
                pairs.append(
                    (gap, first.group_hypothesis_id, second.group_hypothesis_id)
                )
    if not pairs:
        return groups
    root = {group.group_hypothesis_id: group.group_hypothesis_id for group in groups}
    samples = {
        group.group_hypothesis_id: {owner.sample_stem for owner in group.owners}
        for group in groups
    }

    def find(group_id: str) -> str:
        while root[group_id] != group_id:
            group_id = root[group_id]
        return group_id

    for _, first_id, second_id in sorted(pairs):
        first_root, second_root = find(first_id), find(second_id)
        if first_root == second_root or samples[first_root] & samples[second_root]:
            continue
        keep, absorbed = sorted((first_root, second_root))
        root[absorbed] = keep
        samples[keep] |= samples.pop(absorbed)
    members: dict[str, list[CrossSamplePeakGroupHypothesis]] = defaultdict(list)
    for group in groups:
        members[find(group.group_hypothesis_id)].append(group)
    merged = []
    for parts in members.values():
        if len(parts) == 1:
            merged.append(parts[0])
            continue
        parts.sort(key=lambda group: group.public_family_id)
        owners = tuple(
            sorted(
                (owner for group in parts for owner in group.owners),
                key=lambda owner: owner.owner_id,
            )
        )
        hypothesis = _hypothesis_from_owner_group(
            owners,
            group_identity=_identity_from_owners(
                owners, processing_request_id=parts[0].processing_request_id
            ),
            public_family_id=parts[0].public_family_id,
            evidence=";".join(
                (
                    parts[0].evidence,
                    *(
                        f"sample_disjoint_merge:{group.group_hypothesis_id}"
                        for group in parts[1:]
                    ),
                )
            ),
            identity_conflict=any(group.identity_conflict for group in parts),
        )
        merged.append(
            replace(
                hypothesis,
                inference_basis=FeatureMembershipGroup(
                    owner_ids=tuple(owner.owner_id for owner in owners),
                    disposition="assigned",
                    basis="window_level_rt_band",
                    reason="sample_disjoint_within_backfill_window",
                ),
                inference_decisions=tuple(
                    decision
                    for group in parts
                    for decision in group.inference_decisions
                ),
            )
        )
    return tuple(merged)


def _is_shoulder_of(
    owner: SampleLocalMS1Owner,
    neighbour: SampleLocalMS1Owner,
    *,
    config: AlignmentConfig,
) -> bool:
    return (
        owner.owner_area < neighbour.owner_area
        and (
            owner.owner_peak_end_rt == neighbour.owner_peak_start_rt
            or owner.owner_peak_start_rt == neighbour.owner_peak_end_rt
        )
        and within_ppm(owner.precursor_mz, neighbour.precursor_mz, config.preferred_ppm)
    )


def contested_backfill_peaks(
    *,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    backfills: Mapping[
        tuple[str, str, str],
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef],
    ],
    alignment_config: AlignmentConfig,
) -> frozenset[tuple[str, str]]:
    """Physical peaks a Backfill value cannot claim for one group alone.

    A peak several groups' Backfills found, or one a group member already
    holds, belongs to no single row. ``groups`` are the admitted successors.
    Each group-bounded window traces the peak again, so its content ID differs
    from a member's or another window's; as in Discovery, one apex scan within
    the m/z tolerance in one sample is one peak.
    """
    claims: dict[tuple[str, str], set[str]] = defaultdict(set)
    at_apex: dict[tuple[str, float], list[tuple[float, str]]] = defaultdict(list)
    for (group_id, sample_id, _), (_, assessment) in backfills.items():
        for peak in assessment.candidate_peaks:
            content = peak.content
            claims[(sample_id, content.peak_observation_content_id)].add(group_id)
            at_apex[(sample_id, content.integration.rt_apex_min)].append(
                (content.mz, group_id)
            )
    occupied = {
        (member.canonical_sample_identity_id, observation_id)
        for group in groups
        for member in group.member_subjects
        for observation_id in member.physical_observation_ids
    }
    # An archive row writes no value, so only a Matrix row's member holds a peak.
    for group in groups:
        if group.disposition != "established":
            continue
        for owner in group.owners:
            subject = owner.cross_sample_member_subject
            if subject is None:
                continue
            at_apex[(subject.canonical_sample_identity_id, owner.owner_apex_rt)].append(
                (owner.precursor_mz, "")
            )
    contested = set()
    for (group_id, sample_id, _), (_, assessment) in backfills.items():
        for peak in assessment.candidate_peaks:
            content = peak.content
            key = (sample_id, content.peak_observation_content_id)
            if (
                len(claims[key]) > 1
                or key in occupied
                or any(
                    holder != group_id
                    and within_ppm(content.mz, mz, alignment_config.preferred_ppm)
                    for mz, holder in at_apex[
                        (sample_id, content.integration.rt_apex_min)
                    ]
                )
            ):
                contested.add(key)
    return frozenset(contested)


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
