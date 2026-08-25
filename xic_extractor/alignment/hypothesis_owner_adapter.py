"""Workflow-neutral adapter from CID-NL assessment support to Alignment owner."""

from __future__ import annotations

import math

from xic_extractor.alignment.cross_sample_peak_groups import (
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
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_MS1_OBSERVATION,
    compose_cid_nl_peak_hypothesis,
    compose_ms1_observation_peak_hypothesis,
    validate_ms1_observation_peak_hypothesis_binding,
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

    if not isinstance(assessment, CidNlMethodAssessmentRef):
        raise HypothesisOwnerAdapterError(
            "assessment must be an owner-backed CID-NL method result"
        )
    if (
        assessment.artifact.producer_processing_request_id
        != processing_owners.request.processing_request_id
        or assessment.artifact.producer_processing_attempt_id
        != processing_owners.attempt.processing_attempt_id
        or assessment.content.canonical_sample_identity_id
        != sample.identity.sample_identity_id
    ):
        raise HypothesisOwnerAdapterError(
            "assessment belongs to a different processing owner or sample"
        )
    if assessment.content.method_result != "SUPPORT":
        return ()
    peak_by_id = {
        peak.content.peak_observation_content_id: peak
        for peak in assessment.candidate_peaks
    }
    refs_by_peak_id: dict[str, list[DdaCidNlPeakSupportRef]] = {}
    for relation in assessment.individual_support_refs:
        refs_by_peak_id.setdefault(
            relation.content.peak_observation_content_id,
            [],
        ).append(relation)
    hypotheses: list[PeakHypothesis] = []
    for peak_id in sorted(refs_by_peak_id):
        peak = peak_by_id.get(peak_id)
        if peak is None:
            raise HypothesisOwnerAdapterError(
                "assessment support references a physical peak outside its universe"
            )
        try:
            support = compose_cid_nl_chromatographic_support_ref(
                processing_owners=processing_owners,
                sample=sample,
                scan_support_refs=tuple(refs_by_peak_id[peak_id]),
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
) -> tuple[PeakHypothesis, ...]:
    """Preserve direct and observation-only physical peaks without relabeling."""

    direct = compose_cid_nl_peak_hypotheses_from_assessment(
        processing_owners=processing_owners,
        sample=sample,
        assessment=assessment,
    )
    direct_observation_ids = {
        item.peak_observation_refs[0].content.peak_observation_content_id
        for item in direct
    }
    observation_only = tuple(
        compose_ms1_observation_peak_hypothesis(
            processing_owners=processing_owners,
            sample=sample,
            peak_observation=observation,
        )
        for observation in assessment.candidate_peaks
        if observation.content.peak_observation_content_id not in direct_observation_ids
    )
    return tuple(
        sorted((*direct, *observation_only), key=lambda item: item.hypothesis_id)
    )


def owner_from_cid_nl_assessment_support(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
    hypothesis: PeakHypothesis,
) -> SampleLocalMS1Owner:
    """Project measured values without granting selection or write authority."""

    observation, support, evidence_refs = _assessment_hypothesis_components(
        sample=sample,
        assessment=assessment,
        hypothesis=hypothesis,
    )
    measured = tuple(evidence.observation for evidence in evidence_refs)
    aggregate_product_mz = math.fsum(
        observation.product_mz for observation in measured
    ) / len(measured)
    aggregate_observed_loss = math.fsum(
        observation.observed_neutral_loss_da for observation in measured
    ) / len(measured)
    integration = observation.content.integration
    neutral_loss_tag = (
        f"{assessment.content.method_id}:{measured[0].configured_neutral_loss_da:.12g}"
    )
    event = IdentityEvent(
        candidate_id=hypothesis.hypothesis_id,
        sample_stem=sample.metadata.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        neutral_loss_tag=neutral_loss_tag,
        precursor_mz=observation.content.mz,
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
        selected_integration=integration,
        cross_sample_member_subject=group_member_subject_from_peak_hypothesis(
            processing_request_id=(
                assessment.artifact.producer_processing_request_id
            ),
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
    try:
        validate_ms1_observation_peak_hypothesis_binding(
            hypothesis,
            processing_owners=processing_owners,
            sample=sample,
            peak_observation=observation,
        )
    except ValueError as exc:
        raise HypothesisOwnerAdapterError(str(exc)) from exc
    sample_id = sample.identity.sample_identity_id
    if (
        assessment.content.canonical_sample_identity_id != sample_id
        or observation.content.canonical_sample_identity_id != sample_id
    ):
        raise HypothesisOwnerAdapterError(
            "assessment or observation references a different canonical sample"
        )
    integration = observation.content.integration
    neutral_loss = assessment.content.method_subject.neutral_loss_da
    event = IdentityEvent(
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
        cross_sample_member_subject=group_member_subject_from_peak_hypothesis(
            processing_owners=processing_owners,
            sample=sample,
            hypothesis=hypothesis,
        ),
    )


def validate_cid_nl_assessment_hypothesis_closure(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
    hypothesis: PeakHypothesis,
) -> None:
    """Reject crossed assessment, individual-relation, or hypothesis joins."""

    _assessment_hypothesis_components(
        sample=sample,
        assessment=assessment,
        hypothesis=hypothesis,
    )


def _assessment_hypothesis_components(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
    hypothesis: PeakHypothesis,
) -> tuple[
    PeakObservationRef,
    CidNlChromatographicSupportRef,
    tuple[CidNlEvidenceRef, ...],
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
    sample_id = sample.identity.sample_identity_id
    if assessment.content.canonical_sample_identity_id != sample_id or (
        observation.content.canonical_sample_identity_id != sample_id
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
        if relation.content.peak_observation_content_id
        == observation.content.peak_observation_content_id
    )
    if not selected_relations:
        raise HypothesisOwnerAdapterError(
            "hypothesis does not match an assessment individual support subset"
        )
    assessment_relation_content_ids = tuple(
        sorted(
            relation.artifact.content_identity.artifact_content_id
            for relation in selected_relations
        )
    )
    hypothesis_relation_content_ids = tuple(
        sorted(
            member.dda_peak_support_content_id
            for member in support.content.scan_supports
        )
    )
    if assessment_relation_content_ids != hypothesis_relation_content_ids:
        raise HypothesisOwnerAdapterError(
            "hypothesis support does not close the assessment relations"
        )
    evidence_by_content_id = {
        evidence.artifact.content_identity.artifact_content_id: evidence
        for evidence in assessment.evidence_refs
    }
    support_evidence: list[CidNlEvidenceRef] = []
    for member in support.content.scan_supports:
        evidence = evidence_by_content_id.get(member.cid_nl_evidence_content_id)
        if evidence is None:
            raise HypothesisOwnerAdapterError(
                "hypothesis support evidence is missing from the assessment"
            )
        support_evidence.append(evidence)
    if len(support_evidence) != support.content.strict_nl_scan_count:
        raise HypothesisOwnerAdapterError(
            "hypothesis support scan count does not close its measured evidence"
        )
    return observation, support, tuple(support_evidence)


__all__ = [
    "HypothesisOwnerAdapterError",
    "compose_cid_nl_peak_hypotheses_from_assessment",
    "compose_peak_hypotheses_from_assessment_universe",
    "owner_from_cid_nl_assessment_observation",
    "owner_from_cid_nl_assessment_support",
    "validate_cid_nl_assessment_hypothesis_closure",
]
