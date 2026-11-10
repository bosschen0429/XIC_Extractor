"""Compose group-bound CID-NL predecessor cells from retained method evidence."""

from __future__ import annotations

from collections.abc import Mapping

from xic_extractor.alignment.cell_observation.group_context import (
    CidNlCellGroupContext,
    CidNlCellGroupSupport,
)
from xic_extractor.alignment.cell_observation.records import (
    CellObservation,
    CellObservationContractError,
    mint_cell_observation,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
)
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    is_ms1_only_cid_nl_backfill_assessment,
)
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.peak_hypothesis_identity import MeasuredPeakHypothesis
from xic_extractor.sample_metadata import SampleMetadata


def compose_cid_nl_predecessor_cells(
    *,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    sample_assessments: tuple[tuple[SampleMetadata, CidNlMethodAssessment], ...],
    alignment_config: AlignmentConfig,
    sample_universe: frozenset[str] | None = None,
    rt_frame: RtFrame,
    backfill_assessments_by_group: Mapping[
        tuple[str, str, str],
        tuple[SampleMetadata, CidNlMethodAssessment],
    ]
    | None = None,
    selected_cells: frozenset[tuple[str, str]] | None = None,
    retained_hypotheses_by_assessment: Mapping[
        CidNlMethodAssessment, tuple[MeasuredPeakHypothesis, ...]
    ]
    | None = None,
    rejected_backfill_cells: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[CellObservation, ...]:
    """Attach initial evidence and only each group's own backfill assessment.

    An MS1-only Backfill peak in ``rejected_backfill_cells`` stays unresolved.
    """
    initial_assessment_objects = {
        id(assessment) for _, assessment in sample_assessments
    }
    backfills = backfill_assessments_by_group or {}
    for (_, sample_id, subject_id), (sample, assessment) in backfills.items():
        if (
            sample.sample_name != sample_id
            or assessment.sample_key != sample_id
            or assessment.method_subject.method_subject_id != subject_id
        ):
            raise CellObservationContractError(
                "group backfill crossed its sample or method subject"
            )
    # Match the sample and measured assessment values, not artifact identities.
    unique_assessments: dict[
        CidNlMethodAssessment,
        tuple[SampleMetadata, CidNlMethodAssessment],
    ] = {}
    for sample, assessment in (*sample_assessments, *backfills.values()):
        unique_assessments.setdefault(assessment, (sample, assessment))
    sample_assessments = tuple(
        sorted(
            unique_assessments.values(),
            key=lambda pair: pair[0].sample_name,
        )
    )
    retained_hypothesis_ids = frozenset(
        owner_id for group in groups for owner_id in group.owner_ids
    )
    retained_by_assessment = dict(retained_hypotheses_by_assessment or {})
    consumed_retained_keys: set[CidNlMethodAssessment] = set()
    supports_list: list[CidNlCellGroupSupport] = []
    for sample, assessment in sample_assessments:
        retained_hypotheses = retained_by_assessment.get(assessment, ())
        if assessment in retained_by_assessment:
            consumed_retained_keys.add(assessment)
        supports_list.extend(
            CidNlCellGroupSupport(
                sample=sample,
                assessment=assessment,
                hypothesis=hypothesis,
            )
            for hypothesis in retained_hypotheses
            if hypothesis.hypothesis_id in retained_hypothesis_ids
        )
    if consumed_retained_keys != set(retained_by_assessment):
        raise CellObservationContractError(
            "retained hypotheses reference an assessment outside the cell input"
        )
    retained_support_ids = {
        support.hypothesis.hypothesis_id for support in supports_list
    }
    missing_owner_ids = retained_hypothesis_ids - retained_support_ids
    if missing_owner_ids:
        missing_member_observations_by_sample: dict[str, set[PeakObservation]] = {}
        for group in groups:
            for member in group.member_subjects:
                if member.subject_id in missing_owner_ids:
                    missing_member_observations_by_sample.setdefault(
                        member.sample_key,
                        set(),
                    ).update(member.physical_peaks)
        for sample, assessment in sample_assessments:
            sample_id = sample.sample_name
            missing_observation_ids = missing_member_observations_by_sample.get(
                sample_id,
                set(),
            )
            if not missing_observation_ids.intersection(
                candidate.physical_identity for candidate in assessment.candidate_peaks
            ):
                continue
            supports_list.extend(
                CidNlCellGroupSupport(
                    sample=sample,
                    assessment=assessment,
                    hypothesis=hypothesis,
                )
                for hypothesis in compose_peak_hypotheses_from_assessment_universe(
                    sample=sample,
                    assessment=assessment,
                )
                if hypothesis.hypothesis_id in missing_owner_ids
            )
    supports = tuple(supports_list)
    supports_by_hypothesis_id = {
        support.hypothesis.hypothesis_id: support for support in supports
    }
    if len(supports_by_hypothesis_id) != len(supports):
        raise CellObservationContractError(
            "group SUPPORT closures contain duplicate hypotheses"
        )
    contexts = tuple(
        CidNlCellGroupContext(
            group=group,
            supports=tuple(
                sorted(
                    (
                        supports_by_hypothesis_id[owner_id]
                        for owner_id in group.owner_ids
                        if owner_id in supports_by_hypothesis_id
                    ),
                    key=lambda support: support.sample.sample_name,
                )
            ),
            config=alignment_config,
            sample_universe=sample_universe,
            rt_frame=rt_frame,
        )
        for group in groups
    )
    assessments_by_sample_subject: dict[
        tuple[str, str], list[CidNlMethodAssessment]
    ] = {}
    samples = {}
    for sample, assessment in sample_assessments:
        sample_id = sample.sample_name
        samples[sample_id] = sample
        if id(assessment) in initial_assessment_objects:
            assessments_by_sample_subject.setdefault(
                (sample_id, assessment.method_subject.method_subject_id), []
            ).append(assessment)
    support_contexts: dict[tuple[str, int], list[CidNlCellGroupContext]] = {}
    for context in contexts:
        for support in context.supports:
            support_contexts.setdefault(
                (
                    support.sample.sample_name,
                    id(support.assessment),
                ),
                [],
            ).append(context)
    cells = []
    for context in contexts:
        method_subject_id = context.method_subject_id
        context_support_by_sample_id = {
            support.sample.sample_name: support for support in context.supports
        }
        for sample_id in samples:
            if (
                selected_cells is not None
                and (context.group.group_hypothesis_id, sample_id) not in selected_cells
            ):
                continue
            sample = samples[sample_id]
            local_support = context_support_by_sample_id.get(sample_id)
            local_refs = () if local_support is None else (local_support.assessment,)
            canonical_refs = assessments_by_sample_subject.get(
                (sample_id, method_subject_id), ()
            )
            backfill = backfills.get(
                (
                    context.group.group_hypothesis_id,
                    sample_id,
                    method_subject_id,
                )
            )
            recovered = backfill[1] if backfill is not None else None
            if local_refs:
                assessment = local_refs[0]
            elif recovered is not None:
                assessment = recovered
            elif canonical_refs:
                assessment = canonical_refs[0]
            else:
                continue
            local = (
                ()
                if local_support is None
                or local_support.sample is not sample
                or local_support.assessment is not assessment
                else (local_support,)
            )
            outside_context = None
            observation = None
            hypothesis = None
            if local:
                (support,) = local
                hypothesis = support.hypothesis
                observation = hypothesis.peak
            elif assessment.method_result == "SUPPORT":
                siblings = tuple(
                    sibling
                    for sibling in support_contexts.get((sample_id, id(assessment)), ())
                    if sibling is not context
                )
                if not siblings and recovered is not assessment:
                    raise ValueError(
                        "outside-group cell requires one exact sibling SUPPORT context"
                    )
                outside_context = (
                    min(siblings, key=lambda sibling: sibling.group.group_hypothesis_id)
                    if siblings
                    else None
                )
            elif (
                assessment.method_result == "HOLD"
                and len(assessment.candidate_peaks) == 1
                and not (
                    is_ms1_only_cid_nl_backfill_assessment(assessment)
                    and (
                        context.group.group_hypothesis_id,
                        sample_id,
                    )
                    in rejected_backfill_cells
                )
            ):
                observation = assessment.candidate_peaks[0]
            cells.append(
                mint_cell_observation(
                    sample=sample,
                    group_context=context,
                    method_assessment=assessment,
                    peak_observation=observation,
                    peak_hypothesis=hypothesis,
                    outside_group_context=outside_context,
                    unassigned_support=(
                        assessment.method_result == "SUPPORT"
                        and not local
                        and outside_context is None
                    ),
                )
            )
    return tuple(cells)
