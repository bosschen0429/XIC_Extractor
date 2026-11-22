"""Workflow-neutral adapter from CID-NL assessment support to Alignment owner."""

from __future__ import annotations

import math

from xic_extractor.alignment.cross_sample.peak_groups import (
    group_member_subject_from_peak_hypothesis,
)
from xic_extractor.alignment.ownership_models import (
    MS1_OBSERVATION_ASSIGNMENT,
    IdentityEvent,
    SampleLocalMS1Owner,
)
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    CidNlChromatographicSupport,
    compose_cid_nl_chromatographic_support,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    supported_cid_nl_peaks,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupport,
)
from xic_extractor.peak_detection.ms1_shape_identity import prepare_local_peak_shape
from xic_extractor.peak_detection.observations import (
    PeakObservation,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_MS1_OBSERVATION,
    MeasuredPeakHypothesis,
    compose_cid_nl_peak_hypothesis,
    compose_ms1_observation_peak_hypothesis,
)
from xic_extractor.sample_metadata import SampleMetadata


class HypothesisOwnerAdapterError(ValueError):
    """Raised when measured SUPPORT identity cannot enter Alignment exactly."""


def compose_cid_nl_peak_hypotheses_from_assessment(
    *,
    sample: SampleMetadata,
    assessment: CidNlMethodAssessment,
) -> tuple[MeasuredPeakHypothesis, ...]:
    """Preserve every individually supported physical peak as one hypothesis."""

    if assessment.method_result != "SUPPORT":
        return ()
    supports_by_peak: dict[PeakObservation, list[DdaCidNlPeakSupport]] = {}
    for relation in assessment.individual_support_refs:
        supports_by_peak.setdefault(
            relation.peak_observation,
            [],
        ).append(relation)
    hypotheses: list[MeasuredPeakHypothesis] = []
    for supported_peak in supported_cid_nl_peaks(assessment):
        try:
            support = compose_cid_nl_chromatographic_support(
                sample=sample,
                scan_support_refs=tuple(supports_by_peak[supported_peak]),
                evidence_refs=tuple(
                    ref
                    for ref in assessment.evidence_observations
                    if ref
                    in {
                        support.evidence for support in supports_by_peak[supported_peak]
                    }
                )
                if assessment.method_subject.composition
                else (),
            )
            hypothesis = compose_cid_nl_peak_hypothesis(
                peak_observation=supported_peak,
                chromatographic_support=support,
            )
        except ValueError as exc:
            raise HypothesisOwnerAdapterError(str(exc)) from exc
        hypotheses.append(hypothesis)
    return tuple(sorted(hypotheses, key=lambda item: item.hypothesis_id))


def compose_peak_hypotheses_from_assessment_universe(
    *,
    sample: SampleMetadata,
    assessment: CidNlMethodAssessment,
) -> tuple[MeasuredPeakHypothesis, ...]:
    """Preserve direct and observation-only physical peaks without relabeling."""

    direct = compose_cid_nl_peak_hypotheses_from_assessment(
        sample=sample,
        assessment=assessment,
    )
    direct_observations = {item.peak for item in direct}
    observation_only = tuple(
        compose_ms1_observation_peak_hypothesis(peak_observation=observation)
        for observation in assessment.candidate_peaks
        if observation not in direct_observations
    )
    return tuple(
        sorted((*direct, *observation_only), key=lambda item: item.hypothesis_id)
    )


def owner_from_cid_nl_assessment_support(
    *,
    sample: SampleMetadata,
    assessment: CidNlMethodAssessment,
    hypothesis: MeasuredPeakHypothesis,
) -> SampleLocalMS1Owner:
    """Project measured values without granting selection or write authority."""

    observation, support, support_relations = _assessment_hypothesis_components(
        sample=sample,
        assessment=assessment,
        hypothesis=hypothesis,
    )
    measured = support_relations
    aggregate_precursor_mz = math.fsum(
        relation.precursor_mz for relation in measured
    ) / len(measured)
    aggregate_product_mz = math.fsum(
        relation.observed_product_mz for relation in measured
    ) / len(measured)
    aggregate_observed_loss = math.fsum(
        relation.precursor_mz - relation.observed_product_mz for relation in measured
    ) / len(measured)
    integration = observation.integration
    neutral_loss_tag = (
        assessment.method_subject.method_subject_id
        if assessment.method_subject.composition
        else (f"{assessment.method_id}:{measured[0].configured_neutral_loss_da:.12g}")
    )
    event = IdentityEvent(
        candidate_id=hypothesis.hypothesis_id,
        sample_stem=sample.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        neutral_loss_tag=neutral_loss_tag,
        precursor_mz=aggregate_precursor_mz,
        product_mz=aggregate_product_mz,
        observed_neutral_loss_da=aggregate_observed_loss,
        seed_rt=integration.rt_apex_min,
        evidence_score=100,
        seed_event_count=support.strict_nl_scan_count,
    )
    return SampleLocalMS1Owner(
        owner_id=hypothesis.hypothesis_id,
        sample_stem=sample.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        peak=observation,
        primary_identity_event=event,
        supporting_events=(),
        assignment_reason="cid_nl_method_assessment_unique_support",
        cid_nl_composite_assessment=assessment
        if assessment.method_subject.composition
        else None,
        peak_shape=prepare_local_peak_shape(
            observation.rt,
            observation.intensity,
            apex_rt_min=integration.rt_apex_min,
        ),
        cross_sample_member_subject=group_member_subject_from_peak_hypothesis(
            sample=sample,
            hypothesis=hypothesis,
        ),
    )


def owner_from_cid_nl_assessment_observation(
    *,
    sample: SampleMetadata,
    assessment: CidNlMethodAssessment,
    hypothesis: MeasuredPeakHypothesis,
) -> SampleLocalMS1Owner:
    """Project one non-training physical observation into provisional grouping."""

    if hypothesis.method != HYPOTHESIS_METHOD_MS1_OBSERVATION:
        raise HypothesisOwnerAdapterError(
            "observation projection requires an MS1-only MeasuredPeakHypothesis"
        )
    observation = hypothesis.peak
    integration = observation.integration
    neutral_loss = assessment.method_subject.neutral_loss_da
    event = (
        None
        if neutral_loss is None
        else IdentityEvent(
            candidate_id=hypothesis.hypothesis_id,
            sample_stem=sample.raw_stem,
            raw_file=observation.generation_provenance.acquisition_source_id,
            neutral_loss_tag=(f"{assessment.method_id}:{neutral_loss:.12g}"),
            precursor_mz=observation.mz,
            product_mz=observation.mz - neutral_loss,
            observed_neutral_loss_da=neutral_loss,
            seed_rt=integration.rt_apex_min,
            evidence_score=100,
            seed_event_count=1,
        )
    )
    return SampleLocalMS1Owner(
        owner_id=hypothesis.hypothesis_id,
        sample_stem=sample.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        peak=observation,
        primary_identity_event=event,
        supporting_events=(),
        assignment_reason="cid_nl_method_assessment_observation_only",
        peak_shape=prepare_local_peak_shape(
            observation.rt,
            observation.intensity,
            apex_rt_min=integration.rt_apex_min,
        ),
        cross_sample_member_subject=group_member_subject_from_peak_hypothesis(
            sample=sample,
            hypothesis=hypothesis,
        ),
    )


def owner_from_ms1_observation_hypothesis(
    *,
    sample: SampleMetadata,
    hypothesis: MeasuredPeakHypothesis,
) -> SampleLocalMS1Owner:
    """Project one workflow-neutral physical MS1 observation into Alignment."""

    if hypothesis.method != HYPOTHESIS_METHOD_MS1_OBSERVATION:
        raise HypothesisOwnerAdapterError(
            "MS1 owner projection requires an MS1 observation MeasuredPeakHypothesis"
        )
    observation = hypothesis.peak
    integration = observation.integration
    return SampleLocalMS1Owner(
        owner_id=hypothesis.hypothesis_id,
        sample_stem=sample.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        peak=observation,
        primary_identity_event=None,
        supporting_events=(),
        assignment_reason=MS1_OBSERVATION_ASSIGNMENT,
        peak_shape=prepare_local_peak_shape(
            observation.rt,
            observation.intensity,
            apex_rt_min=integration.rt_apex_min,
        ),
        cross_sample_member_subject=group_member_subject_from_peak_hypothesis(
            sample=sample,
            hypothesis=hypothesis,
        ),
    )


def _assessment_hypothesis_components(
    *,
    sample: SampleMetadata,
    assessment: CidNlMethodAssessment,
    hypothesis: MeasuredPeakHypothesis,
) -> tuple[
    PeakObservation,
    CidNlChromatographicSupport,
    tuple[DdaCidNlPeakSupport, ...],
]:
    if assessment.method_result != "SUPPORT":
        raise HypothesisOwnerAdapterError(
            "only a SUPPORT assessment may enter provisional grouping"
        )
    observation = hypothesis.peak
    support = hypothesis.chromatographic_support
    if support is None:
        raise HypothesisOwnerAdapterError(
            "CID-NL hypothesis is missing chromatographic support"
        )
    selected_relations = tuple(
        relation
        for relation in assessment.individual_support_refs
        if relation.peak_observation == observation
    )
    if not selected_relations:
        raise HypothesisOwnerAdapterError(
            "hypothesis does not match an assessment individual support subset"
        )
    return observation, support, selected_relations


__all__ = [
    "HypothesisOwnerAdapterError",
    "compose_cid_nl_peak_hypotheses_from_assessment",
    "compose_peak_hypotheses_from_assessment_universe",
    "owner_from_cid_nl_assessment_observation",
    "owner_from_cid_nl_assessment_support",
    "owner_from_ms1_observation_hypothesis",
]
