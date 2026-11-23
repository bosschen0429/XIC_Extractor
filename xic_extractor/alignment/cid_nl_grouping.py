"""CID-NL identity, support and mass-envelope rules for Alignment.

Shared grouping retains physical mass/RT/shape, sample uniqueness and membership;
this method owner interprets scalar losses and exact composite assessments.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.grouping_evidence import envelope_fraction
from xic_extractor.alignment.grouping_rules import (
    DuplicateContext,
    GroupingRules,
    IndexQuery,
    MethodPairEvidence,
    SeedSupportLevel,
)
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


_DISCOVERY_MODERATE_EVIDENCE_SCORE = 40

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


def has_cid_nl_composition(owner: SampleLocalMS1Owner) -> bool:
    return owner.cid_nl_composite_assessment is not None


def has_scalar_cid_nl_identity(owner: SampleLocalMS1Owner) -> bool:
    return owner.primary_identity_event is not None and not has_cid_nl_composition(
        owner
    )


def cid_nl_mass_center(owners: Sequence[SampleLocalMS1Owner]) -> float | None:
    products = tuple(
        owner.primary_identity_event.product_mz
        for owner in owners
        if owner.primary_identity_event is not None
    )
    if len(products) == 1:
        return products[0]
    return statistics.median(products) if products else None


def cid_nl_mass_fraction(
    first: float | None, second: float | None, config: AlignmentConfig
) -> float:
    if first is None or second is None:
        return 0.0
    return absolute_ppm_error(first, second) / config.product_mz_tolerance_ppm


def cid_nl_pair_mass_fraction(
    left: SampleLocalMS1Owner, right: SampleLocalMS1Owner, config: AlignmentConfig
) -> float:
    first, second = left.primary_identity_event, right.primary_identity_event
    if first is None or second is None or compatible_composite_owners(left, right):
        return 0.0
    return cid_nl_mass_fraction(
        max(first.product_mz, second.product_mz),
        min(first.product_mz, second.product_mz),
        config,
    )


_PRECURSOR_INDEX = ("", "precursor")
_UNOBSERVED_PRECURSOR = ("", "unobserved_method")


def _index_query_alternatives(
    owner: SampleLocalMS1Owner, config: AlignmentConfig
) -> tuple[tuple[IndexQuery, ...], ...]:
    event = owner.primary_identity_event
    if event is None:
        return (((_PRECURSOR_INDEX, owner.peak.mz, config.max_ppm),),)
    tag = event.neutral_loss_tag
    alternatives: tuple[tuple[IndexQuery, ...], ...] = (
        (
            ((tag, "precursor"), owner.peak.mz, config.max_ppm),
            ((tag, "product"), event.product_mz, config.product_mz_tolerance_ppm),
        ),
        ((_UNOBSERVED_PRECURSOR, owner.peak.mz, config.max_ppm),),
    )
    assessment = owner.cid_nl_composite_assessment
    if assessment is not None:
        key = (assessment.method_subject.method_subject_id, "composition")
        alternatives += (((key, owner.peak.mz, config.max_ppm),),)
    return alternatives


@dataclass(slots=True)
class CidNlFeatureEnvelope:
    """Method-only group extrema; common grouping owns physical extrema."""

    seed: SampleLocalMS1Owner | None = None
    product_min: float = 0.0
    product_max: float = 0.0
    composite_subject_id: str | None = None

    @classmethod
    def from_owner(cls, owner: SampleLocalMS1Owner) -> CidNlFeatureEnvelope:
        envelope = cls()
        envelope.add(owner)
        return envelope

    def add(self, owner: SampleLocalMS1Owner) -> None:
        event = owner.primary_identity_event
        if event is None:
            return
        if self.seed is None:
            self.seed = owner
            self.product_min = self.product_max = event.product_mz
            self.composite_subject_id = owner_composite_subject_id(owner)
            return
        if self.composite_subject_id is not None and not compatible_composite_owners(
            owner, self.seed
        ):
            self.composite_subject_id = None
        self.product_min = min(self.product_min, event.product_mz)
        self.product_max = max(self.product_max, event.product_mz)

    def failures(
        self, owner: SampleLocalMS1Owner, config: AlignmentConfig
    ) -> tuple[str, str]:
        """Only observed method evidence can contradict a feature."""
        event = owner.primary_identity_event
        if event is None or self.seed is None:
            return "", ""
        composite = (
            self.composite_subject_id is not None
            and compatible_composite_owners(owner, self.seed)
        )
        if composite:
            return "", ""
        if owner.neutral_loss_tag != self.seed.neutral_loss_tag:
            return "neutral_loss_tag_mismatch", ""
        if (
            envelope_fraction(
                self.product_min,
                self.product_max,
                event.product_mz,
                config.product_mz_tolerance_ppm,
            )
            > 1.0
        ):
            return "", "product_envelope"
        return "", ""

    def index_entries(
        self, precursor_min: float
    ) -> tuple[tuple[tuple[str, str], float], ...]:
        entries: tuple[tuple[tuple[str, str], float], ...] = (
            (_PRECURSOR_INDEX, precursor_min),
        )
        if self.seed is None:
            return (*entries, (_UNOBSERVED_PRECURSOR, precursor_min))
        tag = self.seed.neutral_loss_tag
        entries += (
            ((tag, "precursor"), precursor_min),
            ((tag, "product"), self.product_min),
        )
        if self.composite_subject_id is not None:
            entries += (((self.composite_subject_id, "composition"), precursor_min),)
        return entries


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
    for owner, assessment in zip((left, right), assessments, strict=True):
        member = owner.cross_sample_member_subject
        if (
            member is None
            or member.subject_id != owner.owner_id
            or member.sample_key != assessment.sample_key
        ):
            return False
        if len(member.physical_peaks) != 1:
            return False
        peak = next(
            (
                item
                for item in assessment.candidate_peaks
                if item.physical_identity == member.physical_peaks[0]
            ),
            None,
        )
        if peak is None or peak not in supported_cid_nl_peaks(assessment):
            return False
        if (
            owner.raw_file != assessment.acquisition_source_id
            or owner.peak.integration != peak.integration
            or owner.peak.mz != peak.mz
        ):
            return False
    return True


def owner_composite_subject_id(owner: SampleLocalMS1Owner) -> str | None:
    """Return the composition identity bound to this owner's physical peak."""
    assessment = owner.cid_nl_composite_assessment
    if assessment is not None and compatible_composite_owners(owner, owner):
        return assessment.method_subject.method_subject_id
    return None


def grouping_chemistry_key(owner: SampleLocalMS1Owner) -> tuple[str, str]:
    subject_id = owner_composite_subject_id(owner)
    if subject_id is not None:
        return "composition", subject_id
    return (
        owner.neutral_loss_tag,
        "cid" if owner.primary_identity_event is not None else "ms1",
    )


def classify_seed_support(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    config: AlignmentConfig,
) -> SeedSupportLevel:
    """Consume Discovery identity evidence without re-estimating peak noise."""
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


def _duplicate_context(
    left: SampleLocalMS1Owner, right: SampleLocalMS1Owner, config: AlignmentConfig
) -> DuplicateContext:
    if any(
        owner.assignment_reason == "owner_tail_assignment"
        or any(
            abs(event.seed_rt - owner.peak.rt_apex_min) * 60.0
            > config.owner_apex_close_sec
            for event in owner.supporting_events
        )
        for owner in (left, right)
    ):
        return "tail_assignment"
    return (
        "same_owner_events"
        if left.supporting_events or right.supporting_events
        else "none"
    )


def _pair_evidence(
    left: SampleLocalMS1Owner, right: SampleLocalMS1Owner, config: AlignmentConfig
) -> MethodPairEvidence:
    identity, measurement = cid_nl_pair_failures(left, right, config)
    return MethodPairEvidence(
        identity_failure=identity,
        measurement_failure=measurement,
        seed_support_level=classify_seed_support(left, right, config),
        duplicate_context=_duplicate_context(left, right, config),
        measurement_failure_allows_rt_continuity=measurement is not None,
    )


def _requires_directional_membership(owners: Sequence[SampleLocalMS1Owner]) -> bool:
    return any(has_cid_nl_composition(owner) for owner in owners)


CID_NL_GROUPING_RULES = GroupingRules(
    pair_evidence=_pair_evidence,
    support_level=classify_seed_support,
    feature_envelope=CidNlFeatureEnvelope.from_owner,
    index_query_alternatives=_index_query_alternatives,
    requires_directional_membership=_requires_directional_membership,
    trajectory_witness_eligible=has_scalar_cid_nl_identity,
    chemistry_key=grouping_chemistry_key,
    method_unobserved=lambda owner: owner.primary_identity_event is None,
    establishes_group=has_cid_nl_activation_support,
    mass_center=cid_nl_mass_center,
    mass_fraction=cid_nl_mass_fraction,
    pair_mass_fraction=cid_nl_pair_mass_fraction,
)
