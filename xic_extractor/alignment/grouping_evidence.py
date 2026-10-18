"""Interpret resolved peak and method evidence for grouping consumers.

Membership, edge scoring, and matrix identity consume completed support decisions
without unpacking CID-NL assessment/physical membership bindings themselves.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.owner_area import owner_quantification_eligible
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    supported_cid_nl_peak_ids,
)
from xic_extractor.peak_detection.ms1_shape_identity import compare_local_peak_shapes

SeedSupportLevel = Literal["strong", "moderate", "weak"]

_DETECTION_LIMIT_SNR = 3.0
_QUANTITATION_LIMIT_SNR = 10.0
_DISCOVERY_MODERATE_EVIDENCE_SCORE = 40


def peak_shape_union_conflict(
    left: Sequence[SampleLocalMS1Owner],
    right: Sequence[SampleLocalMS1Owner],
    config: AlignmentConfig,
) -> tuple[str, str] | None:
    """A measured shape contradiction cannot be repaired by a mass/RT bridge.

    Check the members, not just the joining edge: otherwise an intermediate
    shape can transitively reunite two incompatible peaks. Missing/flat/short
    traces supply no shape evidence, rather than a fabricated match or veto.
    """
    for first in left:
        if first.peak_shape is None:
            continue
        for second in right:
            if second.peak_shape is None or first.owner_id == second.owner_id:
                continue
            evidence = compare_local_peak_shapes(first.peak_shape, second.peak_shape)
            if (
                evidence.similarity is not None
                and evidence.similarity < config.min_peak_shape_similarity
            ):
                return first.owner_id, second.owner_id
    return None


def compatible_composite_owners(
    left: SampleLocalMS1Owner, right: SampleLocalMS1Owner
) -> bool:
    """Only exact typed assessment/physical membership closes composition."""
    first, second = left.cid_nl_composite_assessment, right.cid_nl_composite_assessment
    if not isinstance(first, CidNlMethodAssessmentRef) or not isinstance(
        second, CidNlMethodAssessmentRef
    ):
        return False
    assessments = (first, second)
    if (
        first.content.method_subject.composition is None
        or first.content.method_subject != second.content.method_subject
    ):
        return False
    for owner, assessment in zip((left, right), assessments, strict=True):
        member = owner.cross_sample_member_subject
        if (
            member is None
            or member.subject_id != owner.owner_id
            or member.processing_request_id
            != assessment.artifact.producer_processing_request_id
            or member.canonical_sample_identity_id
            != assessment.content.canonical_sample_identity_id
        ):
            return False
        if len(member.physical_observation_ids) != 1 or not set(
            member.physical_observation_ids
        ).issubset(supported_cid_nl_peak_ids(assessment)):
            return False
        peak = next(
            item
            for item in assessment.candidate_peaks
            if item.content.peak_observation_content_id
            == member.physical_observation_ids[0]
        )
        if (
            owner.raw_file != assessment.content.acquisition_source_id
            or owner.selected_integration != peak.content.integration
            or owner.precursor_mz != peak.content.mz
        ):
            return False
    return True


def owner_composite_subject_id(owner: SampleLocalMS1Owner) -> str | None:
    """Return the composition identity bound to this owner's physical peak."""
    assessment = owner.cid_nl_composite_assessment
    if assessment is not None and compatible_composite_owners(owner, owner):
        return assessment.content.method_subject.method_subject_id
    return None


def grouping_chemistry_key(owner: SampleLocalMS1Owner) -> tuple[str, str]:
    subject_id = owner_composite_subject_id(owner)
    if subject_id is not None:
        return "composition", subject_id
    return (
        owner.neutral_loss_tag,
        "cid" if owner.primary_identity_event is not None else "ms1",
    )


def owner_uncertainty_reason(
    owner: SampleLocalMS1Owner,
    config: AlignmentConfig,
) -> str:
    if owner.identity_conflict:
        return "owner_identity_conflict"
    if owner.is_provisional:
        return "provisional_owner"
    if owner.owner_area <= 0.0:
        return "nonpositive_owner_area"
    if "ambiguous" in owner.assignment_reason:
        return "ambiguous_owner_assignment"
    if classify_seed_support(owner, owner, config) == "weak":
        return "weak_identity_evidence"
    return ""


def classify_seed_support(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    config: AlignmentConfig,
) -> SeedSupportLevel:
    """Rate the pair on the peaks Alignment resolved, not on Discovery's score.

    Discovery's ``evidence_score`` is dominated by whether Discovery itself
    found an MS1 peak.  Every owner exists because ownership re-extracted the
    XIC and did find one, so reading that score back here judges an owner by a
    search its own construction superseded.  Rate the resolved peaks instead,
    at the detection- and quantitation-limit conventions, and keep the MS2 seed
    count, which Alignment does not re-derive.  Owners whose integration never
    recorded a baseline residual keep the Discovery-score rating.
    """
    left_event = left.primary_identity_event
    right_event = right.primary_identity_event
    min_snr = _min_owner_peak_snr(left, right)
    if left_event is None or right_event is None:
        if min_snr is not None and min_snr >= _QUANTITATION_LIMIT_SNR:
            return "strong"
        if min_snr is not None and min_snr >= _DETECTION_LIMIT_SNR:
            return "moderate"
        return "weak"
    min_seed_count = min(left_event.seed_event_count, right_event.seed_event_count)
    if min_snr is None:
        return _discovery_seed_support_level(left, right, config)
    if min_snr >= _QUANTITATION_LIMIT_SNR and (
        min_seed_count >= config.anchor_min_seed_events
    ):
        return "strong"
    if min_snr >= _DETECTION_LIMIT_SNR and min_seed_count >= 1:
        return "moderate"
    return "weak"


def _discovery_seed_support_level(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    config: AlignmentConfig,
) -> SeedSupportLevel:
    left_event = left.primary_identity_event
    right_event = right.primary_identity_event
    if left_event is None or right_event is None:
        return "weak"
    min_score = min(left_event.evidence_score, right_event.evidence_score)
    min_seed_count = min(left_event.seed_event_count, right_event.seed_event_count)
    if (
        min_score >= config.anchor_min_evidence_score
        and min_seed_count >= config.anchor_min_seed_events
    ):
        return "strong"
    if min_score >= _DISCOVERY_MODERATE_EVIDENCE_SCORE and min_seed_count >= 1:
        return "moderate"
    return "weak"


def _min_owner_peak_snr(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
) -> float | None:
    left_snr = owner_peak_snr(left)
    right_snr = owner_peak_snr(right)
    if left_snr is None or right_snr is None:
        return None
    return min(left_snr, right_snr)


def owner_peak_snr(owner: SampleLocalMS1Owner) -> float | None:
    """Signal-to-noise of the peak Alignment resolved for this owner."""
    integration = owner.selected_integration
    if integration is None:
        return None
    residual = integration.baseline_residual_mad
    if residual is None or residual <= 0.0:
        return None
    return integration.height_raw / residual


def owner_peak_meets_quantitation_limit(owner: SampleLocalMS1Owner) -> bool:
    """Whether the resolved peak clears the quantitation-limit convention."""
    snr = owner_peak_snr(owner)
    return (
        owner_quantification_eligible(owner)
        and snr is not None
        and snr >= _QUANTITATION_LIMIT_SNR
    )
