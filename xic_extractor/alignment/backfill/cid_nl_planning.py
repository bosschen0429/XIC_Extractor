"""Plan sparse CID-NL group revisits without runtime state or acquisition I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from xic_extractor.alignment.backfill.cid_nl_evidence import (
    decide_cid_nl_backfill_revisit,
)
from xic_extractor.alignment.backfill.conservative import (
    plan_conservative_backfill_targets,
)
from xic_extractor.alignment.band_grouping import group_peak_bands
from xic_extractor.alignment.band_rt_location import BackfillPeakLocation, BandRtLocator
from xic_extractor.alignment.cell_observation.group_context import (
    select_cid_nl_group_backfill_subject,
)
from xic_extractor.alignment.cid_nl_grouping import has_cid_nl_activation_support
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import CidNlMethodSubject
from xic_extractor.sample_metadata import SampleMetadata

if TYPE_CHECKING:
    from xic_extractor.alignment.cid_nl_completion import CidNlWorksetGrouping


@dataclass(frozen=True, slots=True)
class CidNlBackfillQuestion:
    """One eligible sample/group question, already localized by its science owner.

    Sparse execution may not have an initial assessment for this sample/subject
    yet. Runtime obtains that assessment before resolving the question; it does
    not choose a new group, query window or revisit policy.
    """

    sample_key: str
    group: CrossSamplePeakGroupHypothesis
    method_subject: CidNlMethodSubject
    localization: BackfillPeakLocation | None = None
    observed_apex_tolerance_sec: float = 0.0

    def resolve(self, assessment: CidNlMethodAssessment) -> CidNlBackfillDecision:
        return CidNlBackfillDecision(
            question=self,
            assessment=assessment,
            action=decide_cid_nl_backfill_revisit(
                assessment,
                observed_rt=(
                    None
                    if self.localization is None
                    else self.localization.location.observed_rt
                ),
                apex_tolerance_sec=self.observed_apex_tolerance_sec,
            ),
        )


@dataclass(frozen=True, slots=True)
class CidNlBackfillDecision:
    question: CidNlBackfillQuestion
    assessment: CidNlMethodAssessment
    action: Literal["reuse", "search", "skip"]


def plan_grouped_cid_nl_backfills(
    worksets: tuple[CidNlWorksetGrouping, ...],
    *,
    sample_population: tuple[SampleMetadata, ...],
    alignment_config: AlignmentConfig,
    rt_frame: SampleRtFrame,
    backfill_half_window_min: float,
) -> tuple[CidNlBackfillQuestion, ...]:
    """Resolve subject and window facts from frozen band membership."""
    samples = {sample.sample_name: sample for sample in sample_population}
    rt_frame = rt_frame.for_sample_stems(
        frozenset(sample.raw_stem for sample in sample_population)
    )
    questions: list[CidNlBackfillQuestion] = []
    for workset in worksets:
        groups = workset.grouping_scope.groups
        for group, target_ids in plan_conservative_backfill_targets(
            groups, sample_population=sample_population
        ):
            if not target_ids:
                continue
            subject = select_cid_nl_group_backfill_subject(
                group,
                tuple(
                    {
                        owner.assessment.method_subject
                        for owner in group.owners
                        if owner.assessment is not None
                        and has_cid_nl_activation_support(owner)
                    }
                ),
            )
            locator = BandRtLocator(
                tuple(
                    (owner.sample_stem, owner.peak.rt_apex_min)
                    for owner in group.owners
                )
            )
            locations = locator.backfill_locations(
                tuple(samples[sample_id].raw_stem for sample_id in sorted(target_ids)),
                reference_samples=frozenset(
                    owner.sample_stem
                    for owner in group.owners
                    if has_cid_nl_activation_support(owner)
                ),
                rt_frame=rt_frame,
                backfill_half_window_min=backfill_half_window_min,
                drift_offsets=_group_drift_offsets(group, rt_frame),
            )
            questions.extend(
                CidNlBackfillQuestion(
                    sample_key=sample_id,
                    group=group,
                    method_subject=subject,
                    localization=locations[samples[sample_id].raw_stem],
                    observed_apex_tolerance_sec=alignment_config.owner_apex_close_sec,
                )
                for sample_id in sorted(target_ids)
            )
    return tuple(questions)


def plan_subject_cid_nl_backfills(
    sample_assessments: tuple[tuple[SampleMetadata, CidNlMethodAssessment], ...],
    *,
    method_subject: CidNlMethodSubject,
    sample_population: tuple[SampleMetadata, ...],
    alignment_config: AlignmentConfig,
) -> tuple[CidNlBackfillQuestion, ...]:
    """Plan the direct subject route without inventing a frozen batch trajectory."""
    support_owners: list[SampleLocalMS1Owner] = []
    for sample, assessment in sample_assessments:
        if assessment.method_result != "SUPPORT":
            continue
        hypotheses = compose_cid_nl_peak_hypotheses_from_assessment(
            sample=sample, assessment=assessment
        )
        support_owners.extend(
            owner_from_cid_nl_assessment_support(
                sample=sample,
                assessment=assessment,
                hypothesis=hypothesis,
            )
            for hypothesis in hypotheses
        )
    if not support_owners:
        return ()
    groups = group_peak_bands(
        tuple(support_owners),
        config=alignment_config,
        rt_frame=SampleRtFrame.from_samples(sample_population),
        stage="targeted",
    )
    return tuple(
        CidNlBackfillQuestion(sample_id, group, method_subject)
        for group, target_ids in plan_conservative_backfill_targets(
            groups, sample_population=sample_population
        )
        for sample_id in sorted(target_ids)
    )


def _group_drift_offsets(
    group: CrossSamplePeakGroupHypothesis, rt_frame: SampleRtFrame
) -> Mapping[str, float] | None:
    """The settled group's modeled drift; Backfill never refits it."""
    drift = rt_frame.drift_model
    basis = group.inference_basis
    if drift is None or basis is None or not basis.rt_drift.nonzero_count:
        return None
    reference = group.drift_reference
    if reference is None:
        return None
    return drift.sample_offsets(basis.rt_drift, drift.batch_state(*reference))
