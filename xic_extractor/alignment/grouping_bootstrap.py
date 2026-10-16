"""Build the initial CID-NL grouping population and its physical bindings."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast, final

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.feature_membership_inference import (
    FeatureInferenceOperationCounts,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    bind_retained_peak_hypotheses_to_assessment_universe,
    compose_peak_hypotheses_from_assessment_universe,
    owner_from_cid_nl_assessment_observation,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import (
    PeakTrajectories,
    resolve_peak_trajectories,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubjectContractError,
    cid_nl_method_subject_resolver_interval,
    validate_cid_nl_alignment_subject_compatibility,
)
from xic_extractor.peak_detection.elution_context import SampleElutionContext
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class GroupingBootstrapError(ValueError):
    """Raised when assessment support cannot close the initial population."""


@final
@dataclass(frozen=True, slots=True)
class InitialGroupingScope:
    """Exact initial groups with the m/z and RT bounds of their evidence."""

    mz_interval: tuple[float, float]
    rt_interval: tuple[float, float]
    groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    peak_trajectories: PeakTrajectories = PeakTrajectories()

    def __post_init__(self) -> None:
        mz_interval = _validated_interval(self.mz_interval, field="mz_interval")
        rt_interval = _validated_interval(self.rt_interval, field="rt_interval")
        groups = _canonical_groups(self.groups)
        _validate_group_owners(groups)
        _validate_group_bounds(
            groups,
            mz_interval=mz_interval,
            rt_interval=rt_interval,
        )
        object.__setattr__(self, "mz_interval", mz_interval)
        object.__setattr__(self, "rt_interval", rt_interval)
        object.__setattr__(self, "groups", groups)


@final
@dataclass(frozen=True, slots=True)
class RetainedGroupingCandidate:
    """One sample-local peak hypothesis kept for group Backfill and cells."""

    sample: ResolvedCanonicalSampleIdentity
    peak_hypothesis: PeakHypothesis
    direct_support_assessment: CidNlMethodAssessmentRef | None

    @property
    def canonical_sample_identity_id(self) -> str:
        return self.sample.identity.sample_identity_id

    @property
    def peak_hypothesis_id(self) -> str:
        return self.peak_hypothesis.hypothesis_id


def bootstrap_cid_nl_initial_population(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    sample_universe: frozenset[str] | None = None,
    rt_frame: SampleRtFrame,
    operation_counts_sink: list[FeatureInferenceOperationCounts] | None = None,
    retain_observation_only_candidates: bool = False,
    elution_contexts: Sequence[SampleElutionContext] = (),
    retained_hypotheses_by_assessment: Mapping[
        tuple[str, str], tuple[PeakHypothesis, ...]
    ]
    | None = None,
) -> tuple[
    InitialGroupingScope,
    tuple[RetainedGroupingCandidate, ...],
    SampleRtFrame,
]:
    """Build initial groups and their physical candidate bindings."""

    if not sample_assessments:
        raise GroupingBootstrapError("sample_assessments must not be empty")
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise GroupingBootstrapError("processing_owners must be owner-validated")
    if not isinstance(alignment_config, AlignmentConfig):
        raise GroupingBootstrapError("alignment_config must be explicit and typed")
    if not isinstance(retain_observation_only_candidates, bool):
        raise GroupingBootstrapError(
            "retain_observation_only_candidates must be boolean"
        )

    method_subjects_by_id = {
        assessment.content.method_subject.method_subject_id: (
            assessment.content.method_subject
        )
        for _, assessment in sample_assessments
    }
    method_subjects = tuple(
        sorted(method_subjects_by_id.values(), key=lambda item: item.method_subject_id)
    )
    try:
        validate_cid_nl_alignment_subject_compatibility(method_subjects)
    except CidNlMethodSubjectContractError as exc:
        raise GroupingBootstrapError(str(exc)) from exc

    hypotheses_by_sample: dict[str, tuple[PeakHypothesis, ...]] = {}
    assessment_by_hypothesis: dict[str, CidNlMethodAssessmentRef] = {}
    sample_by_id: dict[str, ResolvedCanonicalSampleIdentity] = {}
    owners: list[SampleLocalMS1Owner] = []
    retained_by_assessment = dict(retained_hypotheses_by_assessment or {})
    consumed_retained_assessment_ids: set[tuple[str, str]] = set()
    for sample, assessment in sample_assessments:
        sample_id = sample.identity.sample_identity_id
        assessment_content_id = assessment.artifact.content_identity.artifact_content_id
        retained_key = (sample_id, assessment_content_id)
        retained_hypotheses = retained_by_assessment.get(retained_key, ())
        if retained_key in retained_by_assessment:
            consumed_retained_assessment_ids.add(retained_key)
        hypotheses = (
            bind_retained_peak_hypotheses_to_assessment_universe(
                processing_owners=processing_owners,
                sample=sample,
                assessment=assessment,
                retained_hypotheses=retained_hypotheses,
            )
            if retained_hypotheses
            else compose_peak_hypotheses_from_assessment_universe(
                processing_owners=processing_owners,
                sample=sample,
                assessment=assessment,
            )
        )
        existing = hypotheses_by_sample.get(sample_id, ())
        known = {hypothesis.hypothesis_id for hypothesis in existing}
        hypotheses_by_sample[sample_id] = existing + tuple(
            hypothesis
            for hypothesis in hypotheses
            if hypothesis.hypothesis_id not in known
        )
        for hypothesis in hypotheses:
            assessment_by_hypothesis.setdefault(hypothesis.hypothesis_id, assessment)
        sample_by_id[sample_id] = sample
    if consumed_retained_assessment_ids != set(retained_by_assessment):
        raise GroupingBootstrapError(
            "retained hypotheses reference an assessment outside the bootstrap input"
        )
    has_direct_support = any(
        hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
        for hypotheses in hypotheses_by_sample.values()
        for hypothesis in hypotheses
    )
    include_observation_only = (
        not retain_observation_only_candidates or not has_direct_support
    )
    for sample_id in sorted(hypotheses_by_sample):
        sample = sample_by_id[sample_id]
        hypotheses = hypotheses_by_sample[sample_id]
        for hypothesis in hypotheses:
            assessment = assessment_by_hypothesis[hypothesis.hypothesis_id]
            if hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL:
                owners.append(
                    owner_from_cid_nl_assessment_support(
                        sample=sample,
                        assessment=assessment,
                        hypothesis=hypothesis,
                    )
                )
            elif include_observation_only:
                owners.append(
                    owner_from_cid_nl_assessment_observation(
                        processing_owners=processing_owners,
                        sample=sample,
                        assessment=assessment,
                        hypothesis=hypothesis,
                    )
                )
    searched_no_observation = not owners and all(
        assessment.content.target_snapshot_id is not None
        and assessment.content.technical_state == "evaluated"
        and assessment.content.evaluation_receipt.acquisition_supported
        and assessment.content.evaluation_receipt.search_completed
        and not assessment.content.candidate_peak_content_ids
        for _, assessment in sample_assessments
    )
    if not owners and not searched_no_observation:
        raise GroupingBootstrapError("bootstrap assessments have no peak observations")
    # Bind every retained physical owner, including MS1-only observations.
    rt_frame = rt_frame.bind_owners(tuple(owners))
    correspondence = resolve_peak_trajectories(
        owners,
        elution_contexts,
        config=alignment_config,
        injection_order=rt_frame.injection_order,
    )
    provisional_groups = construct_cross_sample_peak_group_hypotheses(
        tuple(owners),
        config=alignment_config,
        sample_universe=sample_universe,
        rt_frame=rt_frame,
        operation_counts_sink=operation_counts_sink,
        bootstrap=True,
        peak_trajectories=correspondence,
    )
    if not provisional_groups and not searched_no_observation:
        raise GroupingBootstrapError(
            "bootstrap observations did not produce consumable cross-sample groups"
        )
    provisional_group_ids = {
        owner_id: group.group_hypothesis_id
        for group in provisional_groups
        for owner_id in group.owner_ids
    }
    expected_owner_ids = {
        hypothesis.hypothesis_id
        for hypotheses in hypotheses_by_sample.values()
        for hypothesis in hypotheses
        if (
            include_observation_only
            or hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
        )
    }
    if set(provisional_group_ids) != expected_owner_ids:
        raise GroupingBootstrapError(
            "every provisional owner hypothesis must own exactly one group"
        )

    direct_group_ids = {
        provisional_group_ids[hypothesis.hypothesis_id]
        for hypotheses in hypotheses_by_sample.values()
        for hypothesis in hypotheses
        if hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
    }
    if has_direct_support and not direct_group_ids:
        raise GroupingBootstrapError(
            "no provisional group has a direct CID-NL support owner"
        )
    hypotheses = tuple(
        hypothesis
        for sample_id in sorted(hypotheses_by_sample)
        for hypothesis in hypotheses_by_sample[sample_id]
    )
    method_subject_intervals = tuple(
        cid_nl_method_subject_resolver_interval(subject) for subject in method_subjects
    )
    # With no hypotheses, searched_no_observation requires completed receipts;
    # their owner guarantees numeric query bounds.
    grouping_scope = InitialGroupingScope(
        peak_trajectories=correspondence,
        mz_interval=(
            min(interval[0] for interval in method_subject_intervals),
            max(interval[1] for interval in method_subject_intervals),
        ),
        rt_interval=(
            min(hypothesis.integration.rt_left_min for hypothesis in hypotheses)
            if hypotheses
            else min(
                cast(float, assessment.content.evaluation_receipt.query_rt_min)
                for _, assessment in sample_assessments
            ),
            max(hypothesis.integration.rt_right_min for hypothesis in hypotheses)
            if hypotheses
            else max(
                cast(float, assessment.content.evaluation_receipt.query_rt_max)
                for _, assessment in sample_assessments
            ),
        ),
        groups=provisional_groups,
    )
    retained_candidates = tuple(
        RetainedGroupingCandidate(
            sample=sample_by_id[sample_id],
            peak_hypothesis=hypothesis,
            direct_support_assessment=(
                assessment_by_hypothesis[hypothesis.hypothesis_id]
                if hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
                else None
            ),
        )
        for sample_id in sorted(hypotheses_by_sample)
        for hypothesis in hypotheses_by_sample[sample_id]
    )
    return grouping_scope, retained_candidates, rt_frame


def _canonical_groups(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    if not isinstance(groups, tuple):
        raise ValueError("groups must be an immutable tuple")
    if any(not isinstance(group, CrossSamplePeakGroupHypothesis) for group in groups):
        raise ValueError("groups must contain existing group hypotheses")
    group_ids = tuple(
        _required_text(group.group_hypothesis_id, field="group_hypothesis_id")
        for group in groups
    )
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("groups contain duplicate group_hypothesis_id")
    return tuple(sorted(groups, key=lambda group: group.group_hypothesis_id))


def _validate_group_owners(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> None:
    owner_ids: list[str] = []
    for group in groups:
        if not isinstance(group.owner_ids, tuple):
            raise ValueError("group owner_ids must be a tuple")
        group_owner_ids = tuple(
            _required_text(owner_id, field="group owner_ids")
            for owner_id in group.owner_ids
        )
        if len(group_owner_ids) != len(set(group_owner_ids)):
            raise ValueError("group owner_ids must be unique")
        owner_ids.extend(group_owner_ids)
    if len(owner_ids) != len(set(owner_ids)):
        raise ValueError("initial groups must not share owner ids")


def _validate_group_bounds(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    *,
    mz_interval: tuple[float, float],
    rt_interval: tuple[float, float],
) -> None:
    for group in groups:
        center_mz = _finite_float(group.group_center_mz, field="group_center_mz")
        center_rt = _finite_float(group.group_center_rt, field="group_center_rt")
        if not mz_interval[0] <= center_mz <= mz_interval[1]:
            raise ValueError(
                f"group {group.group_hypothesis_id!r} is outside the grouping "
                "scope m/z interval"
            )
        if not rt_interval[0] <= center_rt <= rt_interval[1]:
            raise ValueError(
                f"group {group.group_hypothesis_id!r} is outside the grouping "
                "scope RT interval"
            )


def _validated_interval(
    value: tuple[float, float],
    *,
    field: str,
) -> tuple[float, float]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError(f"{field} must contain exactly two bounds")
    lower = _finite_float(value[0], field=f"{field}[0]")
    upper = _finite_float(value[1], field=f"{field}[1]")
    if lower >= upper:
        raise ValueError(f"{field} lower bound must be less than upper bound")
    return (lower, upper)


def _finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field} must be finite")
    return 0.0 if normalized == 0.0 else normalized


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    if value != value.strip():
        raise ValueError(f"{field} must not contain surrounding whitespace")
    return value
