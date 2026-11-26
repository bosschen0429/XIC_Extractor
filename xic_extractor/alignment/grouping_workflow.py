"""Compose stable cross-sample membership before bounded missing-cell work."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from statistics import median

from xic_extractor.alignment.band_grouping import band_rt, group_peak_bands
from xic_extractor.alignment.band_identity import merge_one_band_groups
from xic_extractor.alignment.cid_nl_grouping import cid_nl_pair_failures
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingContractError,
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleContractError,
    CrossSampleGroupMemberSubject,
    CrossSamplePartitionTransition,
    CrossSamplePeakGroupHypothesis,
    _expected_cross_sample_lineage,
    _hypothesis_from_owner_group,
)
from xic_extractor.alignment.grouping_bootstrap import (
    InitialGroupingScope,
    bootstrap_cid_nl_initial_population,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
    owner_from_cid_nl_assessment_support,
    owner_from_ms1_observation_hypothesis,
)
from xic_extractor.alignment.membership_risk import (
    AcceptedMembershipRisk,
    measured_members,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_band_linking import MAX_BAND_PRECURSOR_SPAN_PPM
from xic_extractor.alignment.result.scientific_alignment import (
    ObservedSearchSubject,
    TerminalSearchObservation,
)
from xic_extractor.alignment.row_contrast import reject_low_contrast_groups
from xic_extractor.alignment.rt_frame import RtFrame, SampleRtFrame
from xic_extractor.alignment.shape_consistency import reject_inconsistent_groups
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    is_ms1_only_cid_nl_backfill_assessment,
)
from xic_extractor.peak_detection.ms1_shape_identity import compare_local_peak_shapes
from xic_extractor.peak_detection.observations import (
    PeakObservation,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
    HYPOTHESIS_METHOD_MS1_OBSERVATION,
    MeasuredPeakHypothesis,
)
from xic_extractor.peak_detection.physical_peak_equivalence import (
    PhysicalPeakView,
    compare_physical_peak_views,
    physical_peak_view_from_observation,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
)
from xic_extractor.sample_metadata import SampleMetadata


def build_targeted_cross_sample_groups(
    *,
    samples: tuple[SampleMetadata, ...],
    terminal_subjects: Sequence[TerminalSearchObservation],
    sample_assessments: tuple[tuple[SampleMetadata, CidNlMethodAssessment], ...],
    alignment_config: AlignmentConfig,
    rt_frame: SampleRtFrame,
) -> CrossSampleGroupingResult:
    """Close Targeted membership from the selected hypothesis in each cell."""

    sample_by_id = {sample.sample_name: sample for sample in samples}
    assessment_by_id = {
        sample.sample_name: assessment for sample, assessment in sample_assessments
    }
    owners: list[SampleLocalMS1Owner] = []
    selected_by_id: dict[str, MeasuredPeakHypothesis] = {}
    for terminal in terminal_subjects:
        if not isinstance(terminal, ObservedSearchSubject):
            continue
        selected = terminal.hypothesis
        observation = selected.peak
        sample_id = observation.sample_key
        sample = sample_by_id.get(sample_id)
        if sample is None or sample_id in selected_by_id:
            raise CrossSampleGroupingContractError(
                "Targeted selection crossed or duplicated its canonical sample"
            )
        assessment = assessment_by_id.get(sample_id)
        if selected.method == HYPOTHESIS_METHOD_DDA_CID_NL:
            if assessment is None:
                raise CrossSampleGroupingContractError(
                    "Targeted CID-NL selection lacks its method assessment"
                )
            owner = owner_from_cid_nl_assessment_support(
                sample=sample,
                assessment=assessment,
                hypothesis=selected,
            )
        elif selected.method == HYPOTHESIS_METHOD_MS1_OBSERVATION:
            owner = owner_from_ms1_observation_hypothesis(
                sample=sample,
                hypothesis=selected,
            )
        else:
            raise CrossSampleGroupingContractError(
                "Targeted selection has an unsupported hypothesis method"
            )
        owners.append(owner)
        selected_by_id[sample_id] = selected

    groups = group_peak_bands(
        owners,
        config=alignment_config,
        rt_frame=rt_frame,
        stage="targeted",
    )
    population = tuple(sorted(groups, key=lambda group: group.group_hypothesis_id))
    if tuple(sorted(owner.owner_id for owner in owners)) != tuple(
        sorted(owner_id for group in groups for owner_id in group.owner_ids)
    ):
        raise CrossSampleGroupingContractError(
            "Targeted grouping lost or invented selected hypothesis identity"
        )
    return CrossSampleGroupingResult(
        samples=samples,
        partition_transition=_partition_transition(
            predecessors=population,
            successors=population,
        ),
        rt_frame=rt_frame,
    )


def build_initial_cid_nl_cross_sample_groups(
    *,
    samples: tuple[SampleMetadata, ...],
    sample_assessments: tuple[tuple[SampleMetadata, CidNlMethodAssessment], ...],
    alignment_config: AlignmentConfig,
    rt_frame: SampleRtFrame,
    retained_hypotheses_by_assessment: Mapping[
        CidNlMethodAssessment, tuple[MeasuredPeakHypothesis, ...]
    ]
    | None = None,
) -> tuple[
    InitialGroupingScope,
    CrossSampleGroupingResult,
]:
    """Link CID-NL peaks in raw RT to supply batch calibration anchors."""
    grouping_scope, rt_frame = bootstrap_cid_nl_initial_population(
        sample_assessments=sample_assessments,
        alignment_config=alignment_config,
        rt_frame=rt_frame,
        retained_hypotheses_by_assessment=retained_hypotheses_by_assessment,
    )
    groups = grouping_scope.groups
    population = tuple(sorted(groups, key=lambda group: group.group_hypothesis_id))
    result = CrossSampleGroupingResult(
        samples=samples,
        partition_transition=_partition_transition(
            predecessors=population, successors=population
        ),
        rt_frame=rt_frame,
    )
    return grouping_scope, result


def regroup_cid_nl_cross_sample_groups(
    *,
    samples: tuple[SampleMetadata, ...],
    grouping_scope: InitialGroupingScope,
    alignment_config: AlignmentConfig,
    rt_frame: SampleRtFrame,
) -> tuple[InitialGroupingScope, CrossSampleGroupingResult]:
    """Reconsider every ordinary owner after the batch anchor warp is known."""
    owners = tuple(owner for group in grouping_scope.groups for owner in group.owners)
    groups = finalize_cid_nl_bands(owners, config=alignment_config, rt_frame=rt_frame)
    population = tuple(sorted(groups, key=lambda group: group.group_hypothesis_id))
    scope = replace(grouping_scope, groups=groups)
    result = CrossSampleGroupingResult(
        samples=samples,
        partition_transition=_partition_transition(
            predecessors=population, successors=population
        ),
        rt_frame=rt_frame,
    )
    return scope, result


def finalize_cid_nl_bands(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    config: AlignmentConfig,
    rt_frame: RtFrame,
    known_identity_bindings: Sequence[tuple[str, str]] = (),
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Share the complete final membership decision with the replay reader."""
    return reject_low_contrast_groups(
        reject_inconsistent_groups(
            merge_one_band_groups(
                group_peak_bands(
                    owners,
                    config=config,
                    rt_frame=rt_frame,
                    known_identity_bindings=known_identity_bindings,
                ),
                config=config,
            )
        )
    )


def admit_cid_nl_group_backfills(
    *,
    result: CrossSampleGroupingResult,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    backfills: Mapping[
        tuple[str, str, str],
        tuple[SampleMetadata, CidNlMethodAssessment],
    ],
    alignment_config: AlignmentConfig,
    member_observations: Mapping[PeakObservation, PeakObservation],
    physical_peak_views: Mapping[PeakObservation, PhysicalPeakView] | None = None,
) -> tuple[
    CrossSampleGroupingResult,
    tuple[CrossSamplePeakGroupHypothesis, ...],
    dict[str, str],
    dict[CidNlMethodAssessment, tuple[MeasuredPeakHypothesis, ...]],
    frozenset[tuple[str, str]],
]:
    """Choose bounded acquired candidates while preserving all original owners."""
    by_id = {group.group_hypothesis_id: group for group in groups}
    proposals = []
    retained: dict[CidNlMethodAssessment, tuple[MeasuredPeakHypothesis, ...]] = {}
    for key, (sample, assessment) in sorted(backfills.items()):
        group_id, sample_id, subject_id = key
        group = by_id[group_id]
        if (
            sample_id != sample.sample_name
            or subject_id != assessment.method_subject.method_subject_id
        ):
            raise CrossSampleGroupingContractError(
                "backfill crossed its sample or subject"
            )
        if (
            assessment.method_result != "SUPPORT"
            and not is_ms1_only_cid_nl_backfill_assessment(assessment)
        ):
            continue
        hypotheses = compose_peak_hypotheses_from_assessment_universe(
            sample=sample,
            assessment=assessment,
        )
        if len(hypotheses) != 1:
            continue
        hypothesis = hypotheses[0]
        owner = (
            owner_from_cid_nl_assessment_support(
                sample=sample,
                assessment=assessment,
                hypothesis=hypothesis,
            )
            if assessment.method_result == "SUPPORT"
            else owner_from_ms1_observation_hypothesis(
                sample=sample,
                hypothesis=hypothesis,
            )
        )
        physical_key = (
            sample_id,
            hypothesis.peak.physical_identity,
        )
        if group.destination != "matrix" or any(
            member.sample_key == sample_id for member in group.member_subjects
        ):
            continue
        proposals.append((group_id, physical_key, owner, hypothesis, assessment))
    if not proposals:
        return (
            result,
            tuple(sorted(groups, key=lambda group: group.group_hypothesis_id)),
            {g.group_hypothesis_id: g.group_hypothesis_id for g in groups},
            retained,
            frozenset((group, sample) for group, sample, _ in backfills),
        )
    comparison_views = dict(physical_peak_views or {})
    for _, _, _, hypothesis, _ in proposals:
        observation = hypothesis.peak
        observation_id = observation.physical_identity
        if observation_id not in comparison_views:
            comparison_views[observation_id] = physical_peak_view_from_observation(
                observation
            )
    occupied = contested_backfill_peaks(
        groups=groups,
        backfills=backfills,
        member_observations=member_observations,
        physical_peak_views=comparison_views,
        direct_only=True,
    )
    eligible = [p for p in proposals if p[1] not in occupied]
    if not eligible:
        return (
            result,
            tuple(sorted(groups, key=lambda group: group.group_hypothesis_id)),
            {g.group_hypothesis_id: g.group_hypothesis_id for g in groups},
            retained,
            frozenset((group, sample) for group, sample, _ in backfills),
        )
    views = [comparison_views[p[1][1]] for p in eligible]
    conflicts = frozenset(
        (j, i)
        for i, proposal in enumerate(eligible)
        for j, other in enumerate(eligible[:i])
        if proposal[1][0] == other[1][0]
        and (
            proposal[0] == other[0]
            or proposal[1] == other[1]
            or compare_physical_peak_views(views[i], views[j]) == "same"
        )
    )
    precursor_ppm = min(
        alignment_config.precursor_mz_tolerance_ppm, MAX_BAND_PRECURSOR_SPAN_PPM
    )
    spans = {
        group.group_hypothesis_id: (
            min(owner.peak.mz for owner in group.owners),
            max(owner.peak.mz for owner in group.owners),
        )
        for group in groups
        if group.owners
    }
    ranked = []
    group_keys = {}
    centers: dict[str, float] = {}
    for i, (group_id, _, owner, hypothesis, _) in enumerate(eligible):
        group = by_id[group_id]
        if any(
            any(cid_nl_pair_failures(peer, owner, alignment_config))
            for peer in group.owners
        ):
            continue
        evaluation = hypothesis.peak.physical_evaluation
        if evaluation is None:
            if group_id not in centers:
                centers[group_id] = median(
                    band_rt(peer, result.rt_frame) for peer in group.owners
                )
            distance = abs(band_rt(owner, result.rt_frame) - centers[group_id])
            if distance * 60.0 > alignment_config.preferred_rt_sec:
                continue
            unlocated = True
        else:
            question = evaluation.question
            rt = owner.peak.rt_apex_min
            window = question.search_rt_window
            if window is not None and not window[0] <= rt <= window[1]:
                continue
            distance = abs(rt - question.query_rt_min)
            unlocated = (
                question.selection_policy_version
                == PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY
            )
        if group_id not in group_keys:
            group_keys[group_id] = measured_members(group.owners)
        low, high = spans[group_id]
        mz = owner.peak.mz
        if not within_ppm(max(high, mz), min(low, mz), precursor_ppm):
            continue
        ranked.append(
            (
                unlocated,
                distance,
                group_keys[group_id],
                owner.peak.measured_order_key,
                i,
            )
        )
    ranked.sort(key=lambda item: item[:-1])
    rivals = defaultdict(list)
    allowed = {item[-1] for item in ranked}
    for a, b in conflicts:
        if a in allowed and b in allowed:
            rivals[a].append(b)
            rivals[b].append(a)
    selected: set[int] = set()
    ranks_by_index = {item[-1]: rank for rank, item in enumerate(ranked)}
    accepted_by_group: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    for _, _, _, _, i in ranked:
        group_id, _, owner, _, _ = eligible[i]
        if any(j in selected for j in rivals[i]):
            continue
        lo, hi = spans[group_id]
        lo, hi = min(lo, owner.peak.mz), max(hi, owner.peak.mz)
        if not within_ppm(hi, lo, precursor_ppm):
            continue
        spans[group_id] = lo, hi
        selected.add(i)
        accepted_by_group[group_id].append(owner)
    risks = {}
    for unlocated, _, _, _, i in ranked:
        if i not in selected:
            continue
        group_id, _, owner, _, _ = eligible[i]
        peers = (*by_id[group_id].owners, *accepted_by_group[group_id])
        alternatives = sorted(rivals[i], key=ranks_by_index.__getitem__)
        flags: list[str] = []
        alternative_partition = None
        if alternatives:
            other_group_id, _, other_owner, _, _ = eligible[alternatives[0]]
            alternative_partition = (
                measured_members((*by_id[other_group_id].owners, other_owner)),
            )
            flags.extend(("accepted_competition", "backfill_competition"))
        if unlocated:
            flags.append("backfill_low_localization_confidence")
        shape_disagreement = owner.peak_shape is not None and any(
            peer.peak_shape is not None
            and (
                similarity := compare_local_peak_shapes(
                    owner.peak_shape, peer.peak_shape
                ).similarity
            )
            is not None
            and similarity < alignment_config.min_peak_shape_similarity
            for peer in peers
            if peer is not owner
        )
        if shape_disagreement:
            flags.append("accepted_contradiction")
        if flags:
            risks[i] = AcceptedMembershipRisk(
                status="accepted",
                phase="backfill",
                selected_owners=measured_members(peers),
                alternative_partition=alternative_partition,
                equivalent_alternatives=(),
                rt_basis="unlocated" if unlocated else "band_location",
                evidence_basis="fixed_band_backfill",
                soft_contradictions=("shape_disagreement",)
                if shape_disagreement
                else (),
                risk_flags=tuple(sorted(flags)),
            )
    selected_cells = {(eligible[i][0], eligible[i][1][0]) for i in selected}
    rejected_cells = frozenset(
        (group, sample)
        for group, sample, _ in backfills
        if (group, sample) not in selected_cells
    )
    additions: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    for index in sorted(selected):
        group_id, physical_key, owner, hypothesis, assessment = eligible[index]
        if assessment.method_result != "SUPPORT":
            continue
        additions[group_id].append(owner)
        retained[assessment] = (hypothesis,)
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
            successor = _hypothesis_from_owner_group(
                combined,
                disposition=group.disposition,
                evidence=group.evidence,
                center=(group.group_center_mz, group.group_center_rt),
                inference_basis=group.inference_basis,
                member_risks=group.member_risks,
                drift_reference=group.drift_reference,
                one_band=group.one_band,
            )
            admitted.extend(owner.cross_sample_member_subject for owner in owners)
        completed.append(successor)
        successor_ids[group.group_hypothesis_id] = successor.group_hypothesis_id
    result = replace(
        result,
        backfill_membership_risks=tuple(
            (successor_ids[eligible[i][0]], eligible[i][1][0], risks[i])
            for i in sorted(risks)
        ),
    )
    if not admitted:
        return (
            result,
            tuple(sorted(groups, key=lambda group: group.group_hypothesis_id)),
            successor_ids,
            retained,
            rejected_cells,
        )
    successors = tuple(sorted(completed, key=lambda group: group.group_hypothesis_id))
    partition = CrossSamplePartitionTransition(
        status="assessable",
        predecessors=result.initial_population,
        successors=successors,
        lineage=_expected_cross_sample_lineage(
            predecessors=result.initial_population, successors=successors
        ),
        reason="membership_changed",
        admitted_members=tuple(admitted),
    )
    _validate_complete_partition(partition)
    return (
        replace(result, partition_transition=partition),
        successors,
        successor_ids,
        retained,
        rejected_cells,
    )


def contested_backfill_peaks(
    *,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    backfills: Mapping[
        tuple[str, str, str],
        tuple[SampleMetadata, CidNlMethodAssessment],
    ],
    member_observations: Mapping[PeakObservation, PeakObservation],
    physical_peak_views: Mapping[PeakObservation, PhysicalPeakView] | None = None,
    direct_only: bool = False,
) -> frozenset[tuple[str, PeakObservation]]:
    """Resolve physical claims once, before SUPPORT admission or cell creation.

    Direct members of Matrix rows retain occupancy. ``direct_only`` excludes
    competition between Backfills: the bounded assignment owner resolves those
    claims instead of rejecting every claimant. An archive member writes no
    value and cannot occupy a peak. The caller supplies retained observations,
    not reconstructed peak-shape summaries or rounded coordinates.
    """

    def view_for(peak: PeakObservation) -> PhysicalPeakView:
        if physical_peak_views is not None:
            view = physical_peak_views.get(peak.physical_identity)
            if view is not None:
                return view
        return physical_peak_view_from_observation(peak)

    members_by_sample: dict[str, list[tuple[PeakObservation, PhysicalPeakView]]] = (
        defaultdict(list)
    )
    occupied = set()
    for group in groups:
        if group.destination != "matrix":
            continue
        for member in group.member_subjects:
            sample_id = member.sample_key
            for observation_id in member.physical_peaks:
                occupied.add((sample_id, observation_id))
                observation = member_observations[observation_id]
                members_by_sample[sample_id].append(
                    (observation_id, view_for(observation))
                )
    backfills_by_sample: dict[
        str, list[tuple[str, PeakObservation, PhysicalPeakView]]
    ] = defaultdict(list)
    for (group_id, sample_id, _), (_, assessment) in backfills.items():
        for peak in assessment.candidate_peaks:
            backfills_by_sample[sample_id].append(
                (
                    group_id,
                    peak.physical_identity,
                    view_for(peak),
                )
            )
    contested = set()
    for sample_id, entries in backfills_by_sample.items():
        for index, (group_id, observation_id, view) in enumerate(entries):
            key = (sample_id, observation_id)
            if key in occupied or any(
                compare_physical_peak_views(view, member_view) == "same"
                for _, member_view in members_by_sample[sample_id]
            ):
                contested.add(key)
            for other_group, other_id, other_view in (
                () if direct_only else entries[:index]
            ):
                if other_group != group_id and (
                    observation_id == other_id
                    or compare_physical_peak_views(view, other_view) == "same"
                ):
                    contested.update((key, (sample_id, other_id)))
    return frozenset(contested)


def _validate_complete_partition(model: CrossSamplePartitionTransition) -> None:
    if model.status == "assessable":
        expected = Counter(
            member for group in model.predecessors for member in group.member_subjects
        )
        expected.update(model.admitted_members)
        actual = Counter(
            member for group in model.successors for member in group.member_subjects
        )
        if actual != expected:
            raise CrossSampleContractError(
                "partition transition is not a complete partition"
            )


def _partition_transition(
    *,
    predecessors: tuple[CrossSamplePeakGroupHypothesis, ...],
    successors: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> CrossSamplePartitionTransition:
    transition = CrossSamplePartitionTransition(
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
    _validate_complete_partition(transition)
    return transition
