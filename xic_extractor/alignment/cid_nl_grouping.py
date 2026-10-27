"""CID-NL identity, support and mass-envelope rules for Alignment.

Shared grouping retains physical mass/RT/shape, sample uniqueness and membership;
this method owner interprets scalar losses and exact composite assessments.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_evidence import (
    envelope_fraction,
    pair_ppm,
)
from xic_extractor.alignment.grouping_rules import (
    DuplicateContext,
    GroupingRules,
    IndexQuery,
    MethodPairEvidence,
    SeedSupportLevel,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.mass_accuracy import absolute_ppm_error
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    supported_cid_nl_peaks,
)

_DISCOVERY_MODERATE_EVIDENCE_SCORE = 40

CidNlPairFailure = Literal[
    "neutral_loss_tag_mismatch",
    "product_mz_out_of_tolerance",
    "observed_loss_out_of_tolerance",
]


def cid_nl_pair_failures(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    config: AlignmentConfig,
) -> tuple[CidNlPairFailure | None, CidNlPairFailure | None]:
    """Resolve identity and measurement gates once, keeping their precedence."""
    first, second = left.primary_identity_event, right.primary_identity_event
    composite = compatible_composite_owners(left, right)
    if (first is None) != (second is None) or (
        not composite and left.neutral_loss_tag != right.neutral_loss_tag
    ):
        return "neutral_loss_tag_mismatch", None
    if first is None or composite:
        return None, None
    assert second is not None
    if pair_ppm(first.product_mz, second.product_mz) > config.product_mz_tolerance_ppm:
        return None, "product_mz_out_of_tolerance"
    if (
        pair_ppm(first.observed_neutral_loss_da, second.observed_neutral_loss_da)
        > config.observed_loss_tolerance_ppm
    ):
        return None, "observed_loss_out_of_tolerance"
    return None, None


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


def cid_nl_index_queries(
    owner: SampleLocalMS1Owner, config: AlignmentConfig
) -> tuple[tuple[tuple[str, str], float, float], ...]:
    event = owner.primary_identity_event
    axes = [("precursor", owner.precursor_mz, config.max_ppm)]
    if event is not None:
        axes.extend(
            (
                ("product", event.product_mz, config.product_mz_tolerance_ppm),
                (
                    "loss",
                    event.observed_neutral_loss_da,
                    config.observed_loss_tolerance_ppm,
                ),
            )
        )
    return tuple(
        ((owner.neutral_loss_tag, axis), value, tolerance)
        for axis, value, tolerance in axes
    )


def cid_nl_composition_index_key(owner: SampleLocalMS1Owner) -> tuple[str, str] | None:
    assessment = owner.cid_nl_composite_assessment
    # Range lookup only proposes candidates; the hard gate closes exact binding.
    return (
        None
        if assessment is None
        else (assessment.content.method_subject.method_subject_id, "composition")
    )


@dataclass(slots=True)
class CidNlFeatureEnvelope:
    """Method-only group extrema; common grouping owns physical extrema."""

    seed: SampleLocalMS1Owner
    product_min: float
    product_max: float
    loss_min: float
    loss_max: float
    composite_subject_id: str | None

    @classmethod
    def from_owner(cls, owner: SampleLocalMS1Owner) -> CidNlFeatureEnvelope:
        event = owner.primary_identity_event
        product = 0.0 if event is None else event.product_mz
        loss = 0.0 if event is None else event.observed_neutral_loss_da
        return cls(
            owner, product, product, loss, loss, owner_composite_subject_id(owner)
        )

    def add(self, owner: SampleLocalMS1Owner) -> None:
        if self.composite_subject_id is not None and not compatible_composite_owners(
            owner, self.seed
        ):
            self.composite_subject_id = None
        event = owner.primary_identity_event
        if event is not None:
            self.product_min = min(self.product_min, event.product_mz)
            self.product_max = max(self.product_max, event.product_mz)
            self.loss_min = min(self.loss_min, event.observed_neutral_loss_da)
            self.loss_max = max(self.loss_max, event.observed_neutral_loss_da)

    def failures(
        self, owner: SampleLocalMS1Owner, config: AlignmentConfig
    ) -> tuple[str, str]:
        """Resolve identity and whole-group envelopes, not just the joining edge."""
        event = owner.primary_identity_event
        if (event is None) != (self.seed.primary_identity_event is None):
            return "identity_evidence_type_mismatch", ""
        composite = (
            self.composite_subject_id is not None
            and compatible_composite_owners(owner, self.seed)
        )
        if not composite and owner.neutral_loss_tag != self.seed.neutral_loss_tag:
            return "neutral_loss_tag_mismatch", ""
        if event is None or composite:
            return "", ""
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
        if (
            envelope_fraction(
                self.loss_min,
                self.loss_max,
                event.observed_neutral_loss_da,
                config.observed_loss_tolerance_ppm,
            )
            > 1.0
        ):
            return "", "observed_loss_envelope"
        return "", ""

    def index_entries(
        self, precursor_min: float
    ) -> tuple[tuple[tuple[str, str], float], ...]:
        values = [("precursor", precursor_min)]
        if self.seed.primary_identity_event is not None:
            values.extend((("product", self.product_min), ("loss", self.loss_min)))
        entries = tuple(
            ((self.seed.neutral_loss_tag, axis), value) for axis, value in values
        )
        if self.composite_subject_id is not None:
            entries += (((self.composite_subject_id, "composition"), precursor_min),)
        return entries


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
            or member.sample_key != assessment.content.sample_key
        ):
            return False
        if len(member.physical_observation_ids) != 1:
            return False
        peak = next(
            (
                item
                for item in assessment.candidate_peaks
                if item.content.peak_observation_content_id
                == member.physical_observation_ids[0]
            ),
            None,
        )
        if peak is None or peak.content not in supported_cid_nl_peaks(assessment):
            return False
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
            abs(event.seed_rt - owner.owner_apex_rt) * 60.0
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


def _index_query_alternatives(
    owner: SampleLocalMS1Owner, config: AlignmentConfig
) -> tuple[tuple[IndexQuery, ...], ...]:
    alternatives: tuple[tuple[IndexQuery, ...], ...] = (
        cid_nl_index_queries(owner, config),
    )
    if key := cid_nl_composition_index_key(owner):
        alternatives += (((key, owner.precursor_mz, config.max_ppm),),)
    return alternatives


def _requires_directional_membership(owners: Sequence[SampleLocalMS1Owner]) -> bool:
    # Composition can bridge scalar tags; ordinary window bands cannot.
    return any(has_cid_nl_composition(owner) for owner in owners)


CID_NL_GROUPING_RULES = GroupingRules(
    pair_evidence=_pair_evidence,
    support_level=classify_seed_support,
    feature_envelope=CidNlFeatureEnvelope.from_owner,
    index_query_alternatives=_index_query_alternatives,
    requires_directional_membership=_requires_directional_membership,
    trajectory_witness_eligible=has_scalar_cid_nl_identity,
    chemistry_key=grouping_chemistry_key,
    mass_center=cid_nl_mass_center,
    mass_fraction=cid_nl_mass_fraction,
    pair_mass_fraction=cid_nl_pair_mass_fraction,
)


def cid_nl_backfill_comparison_rules(
    composite_observation_ids: frozenset[str],
) -> GroupingRules:
    """Compare authorized composite MS1-only observations without inventing CID-NL.

    These candidates are already bound to a fixed group by the Backfill owner.
    A composite subject has no scalar neutral loss to project as an identity
    event. Its absence is not a new identity conflict, and acceptance grants no
    direct SUPPORT. Initial grouping keeps the ordinary CID-NL rules.
    """
    if not composite_observation_ids:
        return CID_NL_GROUPING_RULES

    def pair_evidence(
        left: SampleLocalMS1Owner,
        right: SampleLocalMS1Owner,
        config: AlignmentConfig,
    ) -> MethodPairEvidence:
        if (
            left.owner_id in composite_observation_ids
            or right.owner_id in composite_observation_ids
        ):
            return MethodPairEvidence(None, None, "weak", "none")
        return _pair_evidence(left, right, config)

    return replace(CID_NL_GROUPING_RULES, pair_evidence=pair_evidence)
