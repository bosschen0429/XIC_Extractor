"""Workflow-neutral adapter from CID-NL assessment support to Alignment owner."""

from __future__ import annotations

import math

from xic_extractor.alignment.cross_sample.peak_groups import (
    group_member_subject_from_peak_hypothesis,
)
from xic_extractor.alignment.ownership_models import IdentityEvent, SampleLocalMS1Owner
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    CidNlChromatographicSupportRef,
    compose_cid_nl_chromatographic_support_ref,
)
from xic_extractor.peak_detection.cid_nl_evidence import CidNlEvidenceRef
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    supported_cid_nl_peaks,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.ms1_shape_identity import prepare_local_peak_shape
from xic_extractor.peak_detection.observations import (
    PeakObservationContent,
    PeakObservationRef,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
    HYPOTHESIS_METHOD_MS1_OBSERVATION,
    compose_cid_nl_peak_hypothesis,
    compose_ms1_observation_peak_hypothesis,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class HypothesisOwnerAdapterError(ValueError):
    """Raised when measured SUPPORT identity cannot enter Alignment exactly."""


def compose_cid_nl_peak_hypotheses_from_assessment(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
) -> tuple[PeakHypothesis, ...]:
    """Preserve every individually supported physical peak as one hypothesis."""

    return _compose_cid_nl_peak_hypotheses_from_assessment(
        processing_owners=processing_owners,
        sample=sample,
        assessment=assessment,
        retained_by_peak={},
    )


def _compose_cid_nl_peak_hypotheses_from_assessment(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
    retained_by_peak: dict[PeakObservationContent, PeakHypothesis],
) -> tuple[PeakHypothesis, ...]:

    if not isinstance(assessment, CidNlMethodAssessmentRef):
        raise HypothesisOwnerAdapterError(
            "assessment must be an owner-backed CID-NL method result"
        )
    if (
        assessment.artifact.producer_processing_request_id
        != processing_owners.request.processing_request_id
        or assessment.artifact.producer_processing_attempt_id
        != processing_owners.attempt.processing_attempt_id
        or assessment.content.sample_key != sample.identity.sample_key
    ):
        raise HypothesisOwnerAdapterError(
            "assessment belongs to a different processing owner or sample"
        )
    if assessment.content.method_result != "SUPPORT":
        return ()
    peak_by_content = {peak.content: peak for peak in assessment.candidate_peaks}
    refs_by_peak: dict[PeakObservationContent, list[DdaCidNlPeakSupportRef]] = {}
    for relation in assessment.individual_support_refs:
        refs_by_peak.setdefault(
            relation.content.peak_observation,
            [],
        ).append(relation)
    hypotheses: list[PeakHypothesis] = []
    for peak_content in supported_cid_nl_peaks(assessment):
        peak = peak_by_content.get(peak_content)
        if peak is None:
            raise HypothesisOwnerAdapterError(
                "assessment support references a physical peak outside its universe"
            )
        hypothesis = retained_by_peak.get(peak_content)
        if hypothesis is not None:
            hypotheses.append(hypothesis)
            continue
        try:
            support = compose_cid_nl_chromatographic_support_ref(
                processing_owners=processing_owners,
                sample=sample,
                scan_support_refs=tuple(refs_by_peak[peak_content]),
                evidence_refs=tuple(
                    ref
                    for ref in assessment.evidence_refs
                    if ref.observation
                    in {
                        support.content.evidence
                        for support in refs_by_peak[peak_content]
                    }
                )
                if assessment.content.method_subject.composition
                else (),
            )
            hypothesis = compose_cid_nl_peak_hypothesis(
                processing_owners=processing_owners,
                sample=sample,
                peak_observation=peak,
                chromatographic_support=support,
            )
        except ValueError as exc:
            raise HypothesisOwnerAdapterError(str(exc)) from exc
        _assessment_hypothesis_components(
            sample=sample,
            assessment=assessment,
            hypothesis=hypothesis,
        )
        hypotheses.append(hypothesis)
    return tuple(sorted(hypotheses, key=lambda item: item.hypothesis_id))


def compose_peak_hypotheses_from_assessment_universe(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
    retained_hypotheses: tuple[PeakHypothesis, ...] = (),
) -> tuple[PeakHypothesis, ...]:
    """Preserve direct and observation-only physical peaks without relabeling."""

    retained_by_peak = _bind_retained_assessment_hypotheses(
        sample=sample,
        assessment=assessment,
        retained_hypotheses=retained_hypotheses,
    )
    direct = _compose_cid_nl_peak_hypotheses_from_assessment(
        processing_owners=processing_owners,
        sample=sample,
        assessment=assessment,
        retained_by_peak=retained_by_peak,
    )
    direct_observations = {item.peak_observation_refs[0].content for item in direct}
    observation_only = tuple(
        retained_by_peak.get(observation.content)
        or compose_ms1_observation_peak_hypothesis(
            processing_owners=processing_owners,
            sample=sample,
            peak_observation=observation,
        )
        for observation in assessment.candidate_peaks
        if observation.content not in direct_observations
    )
    return tuple(
        sorted((*direct, *observation_only), key=lambda item: item.hypothesis_id)
    )


def bind_retained_peak_hypotheses_to_assessment_universe(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
    retained_hypotheses: tuple[PeakHypothesis, ...],
) -> tuple[PeakHypothesis, ...]:
    """Bind a complete earlier owner result to the same assessment universe."""

    retained_by_peak = _bind_retained_assessment_hypotheses(
        sample=sample,
        assessment=assessment,
        retained_hypotheses=retained_hypotheses,
    )
    candidate_peaks = {candidate.content for candidate in assessment.candidate_peaks}
    if set(retained_by_peak) != candidate_peaks:
        raise HypothesisOwnerAdapterError(
            "retained hypotheses do not cover the exact assessment universe"
        )
    return tuple(sorted(retained_hypotheses, key=lambda item: item.hypothesis_id))


def _bind_retained_assessment_hypotheses(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
    retained_hypotheses: tuple[PeakHypothesis, ...],
) -> dict[PeakObservationContent, PeakHypothesis]:
    if not isinstance(retained_hypotheses, tuple):
        raise HypothesisOwnerAdapterError(
            "retained hypotheses must be an immutable owner result"
        )
    if not retained_hypotheses:
        return {}
    candidate_by_peak = {
        candidate.content: candidate for candidate in assessment.candidate_peaks
    }
    supported_peaks = supported_cid_nl_peaks(assessment)
    retained_by_peak: dict[PeakObservationContent, PeakHypothesis] = {}
    for hypothesis in retained_hypotheses:
        if (
            not isinstance(hypothesis, PeakHypothesis)
            or not hypothesis.is_owner_backed
            or len(hypothesis.peak_observation_refs) != 1
        ):
            raise HypothesisOwnerAdapterError(
                "retained hypothesis crossed its processing owner"
            )
        observation = hypothesis.peak_observation_refs[0]
        peak_content = observation.content
        if (
            candidate_by_peak.get(peak_content) is not observation
            or observation.content.sample_key != sample.identity.sample_key
            or peak_content in retained_by_peak
        ):
            raise HypothesisOwnerAdapterError(
                "retained hypothesis crossed its assessment sample or observation"
            )
        expected_method = (
            HYPOTHESIS_METHOD_DDA_CID_NL
            if peak_content in supported_peaks
            else HYPOTHESIS_METHOD_MS1_OBSERVATION
        )
        if hypothesis.hypothesis_method != expected_method:
            raise HypothesisOwnerAdapterError(
                "retained hypothesis crossed its assessment support class"
            )
        retained_by_peak[peak_content] = hypothesis
    return retained_by_peak


def owner_from_cid_nl_assessment_support(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
    hypothesis: PeakHypothesis,
) -> SampleLocalMS1Owner:
    """Project measured values without granting selection or write authority."""

    observation, support, support_relations = _assessment_hypothesis_components(
        sample=sample,
        assessment=assessment,
        hypothesis=hypothesis,
    )
    # Accepted relations resolve independent precursor references; inferred
    # evidence can still carry its constructed precursor/loss fields.
    measured = tuple(relation.content for relation in support_relations)
    aggregate_precursor_mz = math.fsum(
        relation.precursor_mz for relation in measured
    ) / len(measured)
    aggregate_product_mz = math.fsum(
        relation.observed_product_mz for relation in measured
    ) / len(measured)
    aggregate_observed_loss = math.fsum(
        relation.precursor_mz - relation.observed_product_mz for relation in measured
    ) / len(measured)
    integration = observation.content.integration
    neutral_loss_tag = (
        assessment.content.method_subject.method_subject_id
        if assessment.content.method_subject.composition
        else (
            f"{assessment.content.method_id}:"
            f"{measured[0].configured_neutral_loss_da:.12g}"
        )
    )
    event = IdentityEvent(
        candidate_id=hypothesis.hypothesis_id,
        sample_stem=sample.metadata.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        neutral_loss_tag=neutral_loss_tag,
        precursor_mz=aggregate_precursor_mz,
        product_mz=aggregate_product_mz,
        observed_neutral_loss_da=aggregate_observed_loss,
        seed_rt=integration.rt_apex_min,
        evidence_score=100,
        seed_event_count=support.content.strict_nl_scan_count,
    )
    return SampleLocalMS1Owner(
        owner_id=hypothesis.hypothesis_id,
        sample_stem=sample.metadata.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        precursor_mz=observation.content.mz,
        owner_apex_rt=integration.rt_apex_min,
        owner_peak_start_rt=integration.rt_left_min,
        owner_peak_end_rt=integration.rt_right_min,
        owner_area=integration.area_raw_counts_seconds,
        owner_height=integration.height_raw,
        primary_identity_event=event,
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="cid_nl_method_assessment_unique_support",
        cid_nl_composite_assessment=assessment
        if assessment.content.method_subject.composition
        else None,
        selected_integration=integration,
        peak_shape=prepare_local_peak_shape(
            observation.content.rt,
            observation.content.intensity,
            apex_rt_min=integration.rt_apex_min,
        ),
        cross_sample_member_subject=group_member_subject_from_peak_hypothesis(
            processing_request_id=(assessment.artifact.producer_processing_request_id),
            sample=sample,
            hypothesis=hypothesis,
        ),
    )


def owner_from_cid_nl_assessment_observation(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
    hypothesis: PeakHypothesis,
) -> SampleLocalMS1Owner:
    """Project one non-training physical observation into provisional grouping."""

    if hypothesis.hypothesis_method != HYPOTHESIS_METHOD_MS1_OBSERVATION:
        raise HypothesisOwnerAdapterError(
            "observation projection requires an MS1-only PeakHypothesis"
        )
    observation = hypothesis.peak_observation_refs[0]
    if observation not in assessment.candidate_peaks:
        raise HypothesisOwnerAdapterError(
            "observation-only hypothesis is outside the assessment universe"
        )
    sample_id = sample.identity.sample_key
    if (
        assessment.content.sample_key != sample_id
        or observation.content.sample_key != sample_id
    ):
        raise HypothesisOwnerAdapterError(
            "assessment or observation references a different canonical sample"
        )
    integration = observation.content.integration
    neutral_loss = assessment.content.method_subject.neutral_loss_da
    event = (
        None
        if neutral_loss is None
        else IdentityEvent(
            candidate_id=hypothesis.hypothesis_id,
            sample_stem=sample.metadata.raw_stem,
            raw_file=observation.generation_provenance.acquisition_source_id,
            neutral_loss_tag=(f"{assessment.content.method_id}:{neutral_loss:.12g}"),
            precursor_mz=observation.content.mz,
            product_mz=observation.content.mz - neutral_loss,
            observed_neutral_loss_da=neutral_loss,
            seed_rt=integration.rt_apex_min,
            evidence_score=100,
            seed_event_count=1,
        )
    )
    return SampleLocalMS1Owner(
        owner_id=hypothesis.hypothesis_id,
        sample_stem=sample.metadata.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        precursor_mz=observation.content.mz,
        owner_apex_rt=integration.rt_apex_min,
        owner_peak_start_rt=integration.rt_left_min,
        owner_peak_end_rt=integration.rt_right_min,
        owner_area=integration.area_raw_counts_seconds,
        owner_height=integration.height_raw,
        primary_identity_event=event,
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="cid_nl_method_assessment_observation_only",
        selected_integration=integration,
        peak_shape=prepare_local_peak_shape(
            observation.content.rt,
            observation.content.intensity,
            apex_rt_min=integration.rt_apex_min,
        ),
        cross_sample_member_subject=group_member_subject_from_peak_hypothesis(
            processing_owners=processing_owners,
            sample=sample,
            hypothesis=hypothesis,
        ),
    )


def owner_from_ms1_observation_hypothesis(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    hypothesis: PeakHypothesis,
) -> SampleLocalMS1Owner:
    """Project one workflow-neutral physical MS1 observation into Alignment."""

    if hypothesis.hypothesis_method != HYPOTHESIS_METHOD_MS1_OBSERVATION:
        raise HypothesisOwnerAdapterError(
            "MS1 owner projection requires an MS1 observation PeakHypothesis"
        )
    if len(hypothesis.peak_observation_refs) != 1:
        raise HypothesisOwnerAdapterError(
            "MS1 owner projection requires one physical observation"
        )
    observation = hypothesis.peak_observation_refs[0]
    integration = observation.content.integration
    return SampleLocalMS1Owner(
        owner_id=hypothesis.hypothesis_id,
        sample_stem=sample.metadata.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        precursor_mz=observation.content.mz,
        owner_apex_rt=integration.rt_apex_min,
        owner_peak_start_rt=integration.rt_left_min,
        owner_peak_end_rt=integration.rt_right_min,
        owner_area=integration.area_raw_counts_seconds,
        owner_height=integration.height_raw,
        primary_identity_event=None,
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="ms1_observation_hypothesis",
        selected_integration=integration,
        peak_shape=prepare_local_peak_shape(
            observation.content.rt,
            observation.content.intensity,
            apex_rt_min=integration.rt_apex_min,
        ),
        cross_sample_member_subject=group_member_subject_from_peak_hypothesis(
            processing_owners=processing_owners,
            sample=sample,
            hypothesis=hypothesis,
        ),
    )


def _assessment_hypothesis_components(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
    hypothesis: PeakHypothesis,
) -> tuple[
    PeakObservationRef,
    CidNlChromatographicSupportRef,
    tuple[DdaCidNlPeakSupportRef, ...],
]:
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise HypothesisOwnerAdapterError(
            "sample must be an owner-resolved canonical identity"
        )
    if not isinstance(assessment, CidNlMethodAssessmentRef):
        raise HypothesisOwnerAdapterError(
            "assessment must be an owner-backed CID-NL method result"
        )
    if assessment.content.method_result != "SUPPORT":
        raise HypothesisOwnerAdapterError(
            "only a SUPPORT assessment may enter provisional grouping"
        )
    if not isinstance(hypothesis, PeakHypothesis) or not hypothesis.is_owner_backed:
        raise HypothesisOwnerAdapterError(
            "hypothesis must be an owner-backed PeakHypothesis"
        )
    if len(hypothesis.peak_observation_refs) != 1:
        raise HypothesisOwnerAdapterError(
            "CID-NL hypothesis must reference exactly one physical peak"
        )
    observation = hypothesis.peak_observation_refs[0]
    sample_id = sample.identity.sample_key
    if assessment.content.sample_key != sample_id or (
        observation.content.sample_key != sample_id
    ):
        raise HypothesisOwnerAdapterError(
            "assessment or hypothesis references a different canonical sample"
        )
    support = hypothesis.evidence.cid_nl_chromatographic_support_ref
    if support is None:
        raise HypothesisOwnerAdapterError(
            "CID-NL hypothesis is missing chromatographic support"
        )
    selected_relations = tuple(
        relation
        for relation in assessment.individual_support_refs
        if relation.content.peak_observation == observation.content
    )
    if not selected_relations:
        raise HypothesisOwnerAdapterError(
            "hypothesis does not match an assessment individual support subset"
        )
    hypothesis_relations = tuple(
        member.support for member in support.content.scan_supports
    )
    if len(selected_relations) != len(hypothesis_relations) or any(
        relation.content not in hypothesis_relations for relation in selected_relations
    ):
        raise HypothesisOwnerAdapterError(
            "hypothesis support does not close the assessment relations"
        )
    evidence_by_observation = {
        evidence.observation: evidence for evidence in assessment.evidence_refs
    }
    support_evidence: list[CidNlEvidenceRef] = []
    for member in support.content.scan_supports:
        evidence = evidence_by_observation.get(member.support.evidence)
        if evidence is None:
            raise HypothesisOwnerAdapterError(
                "hypothesis support evidence is missing from the assessment"
            )
        support_evidence.append(evidence)
    if (
        len({ref.observation.scan_number for ref in support_evidence})
        != support.content.strict_nl_scan_count
    ):
        raise HypothesisOwnerAdapterError(
            "hypothesis support scan count does not close its measured evidence"
        )
    return observation, support, selected_relations


__all__ = [
    "HypothesisOwnerAdapterError",
    "bind_retained_peak_hypotheses_to_assessment_universe",
    "compose_cid_nl_peak_hypotheses_from_assessment",
    "compose_peak_hypotheses_from_assessment_universe",
    "owner_from_cid_nl_assessment_observation",
    "owner_from_cid_nl_assessment_support",
    "owner_from_ms1_observation_hypothesis",
]
