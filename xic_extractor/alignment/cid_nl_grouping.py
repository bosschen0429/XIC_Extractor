"""CID-NL identity, support and mass-envelope rules for Alignment.

Shared grouping retains physical mass/RT/shape, sample uniqueness and membership;
this method owner interprets scalar losses and exact composite assessments.
"""

from __future__ import annotations

from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.mass_accuracy import absolute_ppm_error, mz_window
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    cid_nl_support_requires_supplement,
    supported_cid_nl_peaks,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    cid_nl_alignment_subject_worksets,
)


def alignment_subject_worksets(
    subjects: tuple[CidNlMethodSubject, ...],
    *,
    known_istd_reference: KnownIstdReference | None = None,
) -> tuple[tuple[CidNlMethodSubject, ...], ...]:
    """Close compatible subjects including the supplied known-identity intervals."""
    return cid_nl_alignment_subject_worksets(
        subjects,
        comparison_mz_intervals=_known_istd_comparison_intervals(known_istd_reference),
    )


def _known_istd_comparison_intervals(
    reference: KnownIstdReference | None,
) -> tuple[tuple[float, float], ...]:
    """Co-locate possible known identities without assigning identity by mass."""
    if reference is None:
        return ()
    return tuple(
        mz_window(target.mz, target.ppm_tol)
        for target in reference.anchor.targets
        if target.neutral_loss_da is not None
        and target.label in reference.anchor.observed_labels
    )


CidNlPairFailure = Literal["neutral_loss_tag_mismatch", "product_mz_out_of_tolerance"]


def cid_nl_pair_failures(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    config: AlignmentConfig,
) -> tuple[CidNlPairFailure | None, CidNlPairFailure | None]:
    """Resolve identity and measurement gates once, keeping their precedence."""
    first, second = left.primary_identity_event, right.primary_identity_event
    if first is None or second is None or compatible_composite_owners(left, right):
        return None, None
    if left.neutral_loss_tag != right.neutral_loss_tag:
        return "neutral_loss_tag_mismatch", None
    if (
        absolute_ppm_error(second.product_mz, first.product_mz)
        > config.product_mz_tolerance_ppm
    ):
        return None, "product_mz_out_of_tolerance"
    return None, None


def has_cid_nl_activation_support(owner: SampleLocalMS1Owner) -> bool:
    """Row activation needs independently measured area and direct CID-NL."""
    assessment = owner.cid_nl_composite_assessment
    return (
        owner.peak.integration is not None
        and owner.primary_identity_event is not None
        and (
            assessment is None
            or not cid_nl_support_requires_supplement(assessment, owner.peak)
        )
    )


def compatible_composite_owners(
    left: SampleLocalMS1Owner, right: SampleLocalMS1Owner
) -> bool:
    """Only exact typed assessment/physical membership closes composition."""
    first, second = left.cid_nl_composite_assessment, right.cid_nl_composite_assessment
    if not isinstance(first, CidNlMethodAssessment) or not isinstance(
        second, CidNlMethodAssessment
    ):
        return False
    assessments = (first, second)
    if (
        first.method_subject.composition is None
        or first.method_subject != second.method_subject
    ):
        return False
    return all(
        owner.peak in supported_cid_nl_peaks(assessment)
        for owner, assessment in zip((left, right), assessments, strict=True)
    )
