"""Build the initial CID-NL grouping population and its physical bindings."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast, final

from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
    construct_cross_sample_peak_group_hypotheses,
    ungrouped_peak_group_hypotheses,
)
from xic_extractor.alignment.feature_membership_inference import (
    FeatureInferenceOperationCounts,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
    owner_from_cid_nl_assessment_support,
    owner_from_ms1_observation_hypothesis,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import (
    PeakTrajectories,
    resolve_peak_trajectories,
)
from xic_extractor.alignment.rt_anchors import (
    places_sample_on_rt_frame,
    reaches_ordinary_anchor_quorum,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubjectContractError,
    cid_nl_method_subject_resolver_interval,
    validate_cid_nl_alignment_subject_compatibility,
)
from xic_extractor.peak_detection.elution_context import SampleElutionContext
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
    MeasuredPeakHypothesis,
)
from xic_extractor.sample_metadata import SampleMetadata


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
        for name in ("mz_interval", "rt_interval"):
            lower, upper = getattr(self, name)
            object.__setattr__(self, name, (float(lower) or 0.0, float(upper) or 0.0))
        object.__setattr__(
            self,
            "groups",
            tuple(sorted(self.groups, key=lambda group: group.group_hypothesis_id)),
        )


@final
@dataclass(frozen=True, slots=True)
class RetainedGroupingCandidate:
    """One sample-local peak hypothesis kept for group Backfill and cells."""

    sample: SampleMetadata
    peak_hypothesis: MeasuredPeakHypothesis
    assessment: CidNlMethodAssessment

    @property
    def direct_support_assessment(self) -> CidNlMethodAssessment | None:
        if self.peak_hypothesis.method == HYPOTHESIS_METHOD_DDA_CID_NL:
            return self.assessment
        return None

    @property
    def raw_stem(self) -> str:
        return self.sample.raw_stem

    @property
    def peak_hypothesis_id(self) -> str:
        return self.peak_hypothesis.hypothesis_id


def bootstrap_cid_nl_initial_population(
    *,
    sample_assessments: tuple[tuple[SampleMetadata, CidNlMethodAssessment], ...],
    alignment_config: AlignmentConfig,
    sample_universe: frozenset[str] | None = None,
    rt_frame: SampleRtFrame,
    operation_counts_sink: list[FeatureInferenceOperationCounts] | None = None,
    elution_contexts: Sequence[SampleElutionContext] = (),
    retained_hypotheses_by_assessment: Mapping[
        CidNlMethodAssessment, tuple[MeasuredPeakHypothesis, ...]
    ]
    | None = None,
) -> tuple[
    InitialGroupingScope,
    tuple[RetainedGroupingCandidate, ...],
    SampleRtFrame,
]:
    """Build initial groups and their physical candidate bindings.

    Initial groups are inferred only where their anchors can calibrate: a
    workset short of the anchor quorum calibrates nothing.
    Otherwise every owner is its own group and only final grouping, which
    resolves trajectories on the frozen frame, assigns rows.
    """

    if not sample_assessments:
        raise GroupingBootstrapError("sample_assessments must not be empty")

    method_subjects_by_id = {
        assessment.method_subject.method_subject_id: (assessment.method_subject)
        for _, assessment in sample_assessments
    }
    method_subjects = tuple(
        sorted(method_subjects_by_id.values(), key=lambda item: item.method_subject_id)
    )
    try:
        validate_cid_nl_alignment_subject_compatibility(method_subjects)
    except CidNlMethodSubjectContractError as exc:
        raise GroupingBootstrapError(str(exc)) from exc

    hypotheses_by_sample: dict[str, tuple[MeasuredPeakHypothesis, ...]] = {}
    assessment_by_hypothesis: dict[str, CidNlMethodAssessment] = {}
    sample_by_id: dict[str, SampleMetadata] = {}
    owners: list[SampleLocalMS1Owner] = []
    anchor_samples: set[str] = set()
    retained_by_assessment = dict(retained_hypotheses_by_assessment or {})
    consumed_retained_assessments: set[CidNlMethodAssessment] = set()
    for sample, assessment in sample_assessments:
        raw_stem = sample.raw_stem
        retained_hypotheses = retained_by_assessment.get(assessment, ())
        if assessment in retained_by_assessment:
            consumed_retained_assessments.add(assessment)
        hypotheses = (
            tuple(sorted(retained_hypotheses, key=lambda item: item.hypothesis_id))
            if retained_hypotheses
            else compose_peak_hypotheses_from_assessment_universe(
                sample=sample,
                assessment=assessment,
            )
        )
        existing = hypotheses_by_sample.get(raw_stem, ())
        known = {hypothesis.hypothesis_id for hypothesis in existing}
        hypotheses_by_sample[raw_stem] = existing + tuple(
            hypothesis
            for hypothesis in hypotheses
            if hypothesis.hypothesis_id not in known
        )
        for hypothesis in hypotheses:
            assessment_by_hypothesis.setdefault(hypothesis.hypothesis_id, assessment)
        sample_by_id[raw_stem] = sample
    if consumed_retained_assessments != set(retained_by_assessment):
        raise GroupingBootstrapError(
            "retained hypotheses reference an assessment outside the bootstrap input"
        )
    has_direct_support = any(
        hypothesis.method == HYPOTHESIS_METHOD_DDA_CID_NL
        for hypotheses in hypotheses_by_sample.values()
        for hypothesis in hypotheses
    )
    for raw_stem in sorted(hypotheses_by_sample):
        sample = sample_by_id[raw_stem]
        hypotheses = hypotheses_by_sample[raw_stem]
        for hypothesis in hypotheses:
            assessment = assessment_by_hypothesis[hypothesis.hypothesis_id]
            if hypothesis.method == HYPOTHESIS_METHOD_DDA_CID_NL:
                owner = owner_from_cid_nl_assessment_support(
                    sample=sample,
                    assessment=assessment,
                    hypothesis=hypothesis,
                )
                owners.append(owner)
                if places_sample_on_rt_frame(owner):
                    anchor_samples.add(owner.raw_stem)
            else:
                owners.append(
                    owner_from_ms1_observation_hypothesis(
                        sample=sample,
                        hypothesis=hypothesis,
                    )
                )
    searched_no_observation = not owners and all(
        assessment.method_subject.target is not None
        and assessment.technical_state == "evaluated"
        and assessment.evaluation_receipt.acquisition_supported
        and assessment.evaluation_receipt.search_completed
        and not assessment.candidate_peaks
        for _, assessment in sample_assessments
    )
    if not owners and not searched_no_observation:
        raise GroupingBootstrapError("bootstrap assessments have no peak observations")
    rt_frame = rt_frame.bind_owners(tuple(owners))
    if reaches_ordinary_anchor_quorum(anchor_samples, rt_frame):
        correspondence = resolve_peak_trajectories(
            owners,
            elution_contexts,
            config=alignment_config,
            injection_order=rt_frame.injection_order,
            rt_frame=rt_frame,
        )
        provisional_groups = construct_cross_sample_peak_group_hypotheses(
            tuple(owners),
            config=alignment_config,
            sample_universe=sample_universe,
            rt_frame=rt_frame,
            operation_counts_sink=operation_counts_sink,
            bootstrap=True,
            peak_trajectories=correspondence,
            rules=CID_NL_GROUPING_RULES,
        )
    else:
        correspondence = PeakTrajectories(source_contexts=tuple(elution_contexts))
        provisional_groups = ungrouped_peak_group_hypotheses(owners)
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
    }
    if set(provisional_group_ids) != expected_owner_ids:
        raise GroupingBootstrapError(
            "every provisional owner hypothesis must own exactly one group"
        )

    direct_group_ids = {
        provisional_group_ids[hypothesis.hypothesis_id]
        for hypotheses in hypotheses_by_sample.values()
        for hypothesis in hypotheses
        if hypothesis.method == HYPOTHESIS_METHOD_DDA_CID_NL
    }
    if has_direct_support and not direct_group_ids:
        raise GroupingBootstrapError(
            "no provisional group has a direct CID-NL support owner"
        )
    hypotheses = tuple(
        hypothesis
        for raw_stem in sorted(hypotheses_by_sample)
        for hypothesis in hypotheses_by_sample[raw_stem]
    )
    method_subject_intervals = tuple(
        cid_nl_method_subject_resolver_interval(subject) for subject in method_subjects
    )
    mz_interval = _validated_interval(
        (
            min(interval[0] for interval in method_subject_intervals),
            max(interval[1] for interval in method_subject_intervals),
        ),
        field="mz_interval",
    )
    rt_interval = _validated_interval(
        (
            min(hypothesis.peak.rt_left_min for hypothesis in hypotheses)
            if hypotheses
            else min(
                cast(float, assessment.evaluation_receipt.query_rt_min)
                for _, assessment in sample_assessments
            ),
            max(hypothesis.peak.rt_right_min for hypothesis in hypotheses)
            if hypotheses
            else max(
                cast(float, assessment.evaluation_receipt.query_rt_max)
                for _, assessment in sample_assessments
            ),
        ),
        field="rt_interval",
    )
    _validate_group_ids(provisional_groups)
    grouping_scope = InitialGroupingScope(
        mz_interval, rt_interval, provisional_groups, correspondence
    )
    _validate_group_owners(grouping_scope.groups)
    _validate_group_bounds(
        grouping_scope.groups, mz_interval=mz_interval, rt_interval=rt_interval
    )
    retained_candidates = tuple(
        RetainedGroupingCandidate(
            sample=sample_by_id[raw_stem],
            peak_hypothesis=hypothesis,
            assessment=assessment_by_hypothesis[hypothesis.hypothesis_id],
        )
        for raw_stem in sorted(hypotheses_by_sample)
        for hypothesis in hypotheses_by_sample[raw_stem]
    )
    return grouping_scope, retained_candidates, rt_frame


def _validate_group_ids(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> None:
    group_ids = tuple(group.group_hypothesis_id for group in groups)
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("groups contain duplicate group_hypothesis_id")


def _validate_group_owners(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> None:
    owner_ids: list[str] = []
    for group in groups:
        group_owner_ids = tuple(group.owner_ids)
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
