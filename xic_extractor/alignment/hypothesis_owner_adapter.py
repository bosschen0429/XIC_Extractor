"""Workflow-neutral adapter from CID-NL assessment support to Alignment owner."""

from __future__ import annotations

from xic_extractor.alignment.ownership_models import IdentityEvent, SampleLocalMS1Owner
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    CidNlChromatographicSupportRef,
)
from xic_extractor.peak_detection.cid_nl_evidence import CidNlEvidenceRef
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class HypothesisOwnerAdapterError(ValueError):
    """Raised when measured SUPPORT identity cannot enter Alignment exactly."""


def owner_from_cid_nl_assessment_support(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    assessment: CidNlMethodAssessmentRef,
    hypothesis: PeakHypothesis,
) -> SampleLocalMS1Owner:
    """Project measured values without granting selection or write authority."""

    observation, support, evidence = _assessment_hypothesis_components(
        sample=sample,
        assessment=assessment,
        hypothesis=hypothesis,
    )
    measured = evidence.observation
    integration = observation.content.integration
    neutral_loss_tag = (
        f"{assessment.content.method_id}:{measured.configured_neutral_loss_da:.12g}"
    )
    event = IdentityEvent(
        candidate_id=hypothesis.hypothesis_id,
        sample_stem=sample.metadata.raw_stem,
        raw_file=observation.generation_provenance.acquisition_source_id,
        neutral_loss_tag=neutral_loss_tag,
        precursor_mz=observation.content.mz,
        product_mz=measured.product_mz,
        observed_neutral_loss_da=measured.observed_neutral_loss_da,
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
) -> tuple[PeakObservationRef, CidNlChromatographicSupportRef, CidNlEvidenceRef]:
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
    selected_peak_ids = {
        relation.content.peak_observation_content_id
        for relation in assessment.individual_support_refs
    }
    if selected_peak_ids != {observation.content.peak_observation_content_id}:
        raise HypothesisOwnerAdapterError(
            "hypothesis does not match the assessment's unique individual support"
        )
    assessment_relation_content_ids = tuple(
        sorted(
            relation.artifact.content_identity.artifact_content_id
            for relation in assessment.individual_support_refs
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
    first_member = support.content.scan_supports[0]
    evidence = evidence_by_content_id.get(first_member.cid_nl_evidence_content_id)
    if evidence is None:
        raise HypothesisOwnerAdapterError(
            "hypothesis support evidence is missing from the assessment"
        )
    return observation, support, evidence


__all__ = [
    "HypothesisOwnerAdapterError",
    "owner_from_cid_nl_assessment_support",
    "validate_cid_nl_assessment_hypothesis_closure",
]
