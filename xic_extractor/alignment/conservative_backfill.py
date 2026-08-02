"""Conservative no-write Backfill eligibility and mapping facts.

This module consumes the canonical M1 ``CellObservationRef`` owner.  It may
decide whether an already-existing, frozen group is eligible for a bounded
Backfill revisit and whether supplied recovery evidence has an unambiguous
ordered one-to-one mapping.  It never creates a group, mints a cell, selects a
numeric product value, or calls a writer.
"""

from __future__ import annotations

import json
import math
from dataclasses import InitVar, dataclass, field
from typing import Literal, cast, final

from xic_extractor.alignment.cell_observations import (
    CellObservationRef,
    CidNlCellGroupContext,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    TargetedCellObservationReferenceProjection,
    UntargetedCellObservationReferenceProjection,
)
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.processing_identity import (
    ProcessingAttemptIdentity,
    ProcessingOwnerReferences,
    canonical_current_attempt_artifact_closure,
    retry_predecessor_attempt_ids,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

POLICY_VERSION = "conservative-backfill-v1"
VALIDATION_STATUS = "shadow_ready"
PRODUCT_AUTHORITY = "none"
RECOVERY_UNIVERSE_SCHEMA_VERSION = (
    "conservative_backfill_recovery_candidate_universe_v2"
)
RECOVERY_UNIVERSE_ARTIFACT_SEMANTIC_ROLE = (
    "conservative_backfill_recovery_candidate_universe"
)
_RECOVERY_UNIVERSE_ARTIFACT_SEMANTIC_CONTRACT = (
    _mint_artifact_semantic_contract_ref(
        owner_namespace="xic_extractor.alignment.conservative_backfill",
        semantic_role=RECOVERY_UNIVERSE_ARTIFACT_SEMANTIC_ROLE,
        schema_version=RECOVERY_UNIVERSE_SCHEMA_VERSION,
        validation_status=VALIDATION_STATUS,
        media_type="application/json",
        serialization_format="canonical-json-utf8-lf-v1",
    )
)
_CELL_DECISION_MINT_TOKEN = object()
ACTIVATION_ELIGIBLE_SAMPLE_ROLES = frozenset(
    {"study_sample", "qc", "pooled_qc"}
)

ConservativeBackfillDisposition = Literal[
    "candidate_mapped",
    "hold",
    "not_applicable",
]


class ConservativeBackfillContractError(ValueError):
    """Raised when a caller crosses the frozen no-write Backfill boundary."""


@dataclass(frozen=True, slots=True)
class ConservativeBackfillGroupFacts:
    """Derived S/K and trace facts for one existing group snapshot."""

    group_context: CidNlCellGroupContext
    source_cells: tuple[CellObservationRef, ...]
    sample_population: tuple[ResolvedCanonicalSampleIdentity, ...]
    activation_eligible_sample_ids: tuple[str, ...]
    direct_support_sample_ids: tuple[str, ...]
    activation_eligible_support_sample_ids: tuple[str, ...]
    support_trace_refs: tuple[PeakObservationRef, ...]
    sample_count_n: int
    minimum_support_k: int
    direct_support_count_s: int
    group_frozen: bool
    may_attempt_recovery: bool
    reason_codes: tuple[str, ...]
    policy_version: str = POLICY_VERSION
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY

    def __post_init__(self) -> None:
        if not isinstance(self.group_context, CidNlCellGroupContext):
            raise ConservativeBackfillContractError(
                "Backfill facts require an existing owner-validated group context"
            )
        cells = _canonical_cells(self.source_cells, group_context=self.group_context)
        samples = _canonical_samples(self.sample_population)
        sample_by_id = {
            sample.identity.sample_identity_id: sample for sample in samples
        }
        cell_sample_ids = tuple(
            cell.content.canonical_sample_identity_id for cell in cells
        )
        unknown_cell_samples = set(cell_sample_ids).difference(sample_by_id)
        if unknown_cell_samples:
            raise ConservativeBackfillContractError(
                "CellObservation population contains samples outside the declared run"
            )
        for support in self.group_context.supports:
            sample_id = support.sample.identity.sample_identity_id
            if (
                sample_id not in sample_by_id
                or sample_by_id[sample_id] is not support.sample
            ):
                raise ConservativeBackfillContractError(
                    "group SUPPORT sample is not the exact declared run sample"
                )

        eligible_ids = tuple(
            sample.identity.sample_identity_id
            for sample in samples
            if _sample_is_activation_eligible(sample)
        )
        direct_support_ids = tuple(
            support.sample.identity.sample_identity_id
            for support in self.group_context.supports
        )
        eligible_support_ids = tuple(
            sample_id for sample_id in direct_support_ids if sample_id in eligible_ids
        )
        support_traces = tuple(
            _support_trace(support.hypothesis.peak_observation_refs)
            for support in self.group_context.supports
        )
        sample_count = len(eligible_ids)
        minimum_support = minimum_direct_support_count(sample_count)
        support_count = len(eligible_support_ids)
        frozen = cell_sample_ids == tuple(sample_by_id)
        may_attempt = frozen and support_count >= minimum_support
        reasons = _group_reason_codes(
            group_frozen=frozen,
            direct_support_count=support_count,
            minimum_support=minimum_support,
        )

        expected = {
            "activation_eligible_sample_ids": eligible_ids,
            "direct_support_sample_ids": direct_support_ids,
            "activation_eligible_support_sample_ids": eligible_support_ids,
            "support_trace_refs": support_traces,
            "sample_count_n": sample_count,
            "minimum_support_k": minimum_support,
            "direct_support_count_s": support_count,
            "group_frozen": frozen,
            "may_attempt_recovery": may_attempt,
            "reason_codes": reasons,
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                raise ConservativeBackfillContractError(
                    f"{field_name} does not match the owner-derived Backfill facts"
                )
        if self.policy_version != POLICY_VERSION:
            raise ConservativeBackfillContractError(
                f"policy_version must be {POLICY_VERSION!r}"
            )
        if self.validation_status != VALIDATION_STATUS:
            raise ConservativeBackfillContractError(
                f"validation_status must be {VALIDATION_STATUS!r}"
            )
        if self.product_authority != PRODUCT_AUTHORITY:
            raise ConservativeBackfillContractError(
                "conservative Backfill facts cannot grant product authority"
            )


@dataclass(frozen=True, slots=True)
class ConservativeBackfillRecoveryCandidateUniverse:
    """Owner receipt binding one sample×target revisit to its full peak set."""

    processing_owners: ProcessingOwnerReferences
    processing_attempt: ProcessingAttemptIdentity
    sample: ResolvedCanonicalSampleIdentity
    group_facts: tuple[ConservativeBackfillGroupFacts, ...]
    candidate_assessment: CidNlMethodAssessmentRef
    artifact: EvidenceArtifactRef
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY

    def __post_init__(self) -> None:
        if not isinstance(self.processing_owners, ProcessingOwnerReferences):
            raise ConservativeBackfillContractError(
                "recovery universe requires validated processing owners"
            )
        if not isinstance(self.processing_attempt, ProcessingAttemptIdentity):
            raise ConservativeBackfillContractError(
                "recovery universe requires an owner-issued processing attempt"
            )
        if self.processing_attempt is not self.processing_owners.attempt:
            raise ConservativeBackfillContractError(
                "recovery universe attempt is not the exact processing owner attempt"
            )
        if not isinstance(self.sample, ResolvedCanonicalSampleIdentity):
            raise ConservativeBackfillContractError(
                "recovery universe requires an owner-resolved sample"
            )
        if not any(
            self.sample is owned
            for owned in self.processing_owners.sample_resolutions
        ):
            raise ConservativeBackfillContractError(
                "recovery universe sample is outside the processing owner closure"
            )
        facts = _canonical_recovery_group_facts(self.group_facts)
        if facts != self.group_facts:
            raise ConservativeBackfillContractError(
                "recovery universe group facts must be canonical-sorted"
            )
        _validate_recovery_group_populations(
            facts,
            processing_owners=self.processing_owners,
        )
        _validate_recovery_sample_and_target(facts, sample=self.sample)
        _validate_recovery_candidate_assessment(
            self.candidate_assessment,
            processing_attempt=self.processing_attempt,
            sample=self.sample,
            group_facts=facts,
        )
        _validate_recovery_cell_attempts(
            facts,
            processing_attempt=self.processing_attempt,
        )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise ConservativeBackfillContractError(
                "recovery universe requires an owner-minted artifact"
            )
        if (
            self.artifact.semantic_contract
            != _RECOVERY_UNIVERSE_ARTIFACT_SEMANTIC_CONTRACT
        ):
            raise ConservativeBackfillContractError(
                "recovery universe artifact is not owned by conservative Backfill"
            )
        if (
            self.artifact.producer_processing_request_id
            != self.processing_attempt.processing_request_id
            or self.artifact.producer_processing_attempt_id
            != self.processing_attempt.processing_attempt_id
        ):
            raise ConservativeBackfillContractError(
                "recovery universe artifact references a different attempt"
            )
        expected_upstream_ids = tuple(
            artifact.evidence_artifact_id
            for artifact in _recovery_universe_upstream_artifacts(
                facts,
                self.candidate_assessment,
            )
        )
        if self.artifact.upstream_artifact_ids != expected_upstream_ids:
            raise ConservativeBackfillContractError(
                "recovery universe artifact does not bind its exact cells and peaks"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_conservative_backfill_recovery_universe_bytes(
                    processing_owners=self.processing_owners,
                    sample=self.sample,
                    group_facts=facts,
                    candidate_assessment=self.candidate_assessment,
                ),
            )
        except ValueError as exc:
            raise ConservativeBackfillContractError(
                "recovery universe mismatches its immutable artifact"
            ) from exc
        if self.validation_status != VALIDATION_STATUS:
            raise ConservativeBackfillContractError(
                f"validation_status must be {VALIDATION_STATUS!r}"
            )
        if self.product_authority != PRODUCT_AUTHORITY:
            raise ConservativeBackfillContractError(
                "recovery universe cannot grant product authority"
            )

    @property
    def target_key(self) -> tuple[str, str]:
        return _group_target_key(self.group_facts[0])

    @property
    def candidate_peaks(self) -> tuple[PeakObservationRef, ...]:
        return self.candidate_assessment.candidate_peaks


@dataclass(frozen=True, slots=True)
class ConservativeBackfillRecoveryEvidence:
    """One peak selected only from an owner-bound recovery universe receipt."""

    candidate_universe: ConservativeBackfillRecoveryCandidateUniverse
    peak_observation: PeakObservationRef
    evidence_source: str = "owner_backfill_revisit"
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY

    def __post_init__(self) -> None:
        if not isinstance(
            self.candidate_universe,
            ConservativeBackfillRecoveryCandidateUniverse,
        ):
            raise ConservativeBackfillContractError(
                "recovery evidence requires an owner-bound candidate universe"
            )
        if not isinstance(self.peak_observation, PeakObservationRef):
            raise ConservativeBackfillContractError(
                "recovery evidence requires an owner-backed physical peak"
            )
        if not any(
            self.peak_observation is peak
            for peak in self.candidate_universe.candidate_peaks
        ):
            raise ConservativeBackfillContractError(
                "recovery evidence peak is outside its owner-bound candidate universe"
            )
        if self.evidence_source != "owner_backfill_revisit":
            raise ConservativeBackfillContractError(
                "recovery evidence cannot claim a direct-support source"
            )
        if self.validation_status != VALIDATION_STATUS:
            raise ConservativeBackfillContractError(
                f"validation_status must be {VALIDATION_STATUS!r}"
            )
        if self.product_authority != PRODUCT_AUTHORITY:
            raise ConservativeBackfillContractError(
                "recovery evidence cannot grant product authority"
            )

    @property
    def target_key(self) -> tuple[str, str]:
        return self.candidate_universe.target_key


@final
@dataclass(frozen=True, slots=True)
class ConservativeBackfillCellDecision:
    """A no-write mapping verdict for one sample × existing group cell."""

    group_facts: ConservativeBackfillGroupFacts
    target_cell: CellObservationRef
    candidate_universe: ConservativeBackfillRecoveryCandidateUniverse | None
    candidate_evidence: tuple[ConservativeBackfillRecoveryEvidence, ...]
    matched_candidate: ConservativeBackfillRecoveryEvidence | None
    disposition: ConservativeBackfillDisposition
    reason_codes: tuple[str, ...]
    _mint_token: InitVar[object] = field(kw_only=True)
    policy_version: str = POLICY_VERSION
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ConservativeBackfillCellDecision is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _CELL_DECISION_MINT_TOKEN:
            raise ConservativeBackfillContractError(
                "cell decisions must be minted by the conservative Backfill mapper"
            )
        if not isinstance(self.group_facts, ConservativeBackfillGroupFacts):
            raise ConservativeBackfillContractError(
                "cell decision requires owner-derived group facts"
            )
        if not isinstance(self.target_cell, CellObservationRef) or not any(
            self.target_cell is cell for cell in self.group_facts.source_cells
        ):
            raise ConservativeBackfillContractError(
                "cell decision must reuse an exact canonical source cell"
            )
        if self.candidate_universe is not None and not isinstance(
            self.candidate_universe,
            ConservativeBackfillRecoveryCandidateUniverse,
        ):
            raise ConservativeBackfillContractError(
                "cell decision candidate universe must be an owner receipt"
            )
        if not isinstance(self.candidate_evidence, tuple) or any(
            not isinstance(item, ConservativeBackfillRecoveryEvidence)
            for item in self.candidate_evidence
        ):
            raise ConservativeBackfillContractError(
                "candidate_evidence must contain typed immutable recovery evidence"
            )
        if self.candidate_universe is None:
            if self.candidate_evidence:
                raise ConservativeBackfillContractError(
                    "cell decision cannot retain evidence without its owner receipt"
                )
        else:
            evidence_peaks = tuple(
                item.peak_observation for item in self.candidate_evidence
            )
            if any(
                item.candidate_universe is not self.candidate_universe
                for item in self.candidate_evidence
            ) or evidence_peaks != self.candidate_universe.candidate_peaks:
                raise ConservativeBackfillContractError(
                    "cell decision must retain the complete owner-bound universe"
                )
        if self.matched_candidate is not None and not any(
            self.matched_candidate is item for item in self.candidate_evidence
        ):
            raise ConservativeBackfillContractError(
                "matched candidate must be an exact supplied evidence reference"
            )
        if self.disposition not in {"candidate_mapped", "hold", "not_applicable"}:
            raise ConservativeBackfillContractError(
                "unsupported conservative Backfill disposition"
            )
        if self.disposition == "candidate_mapped" and self.matched_candidate is None:
            raise ConservativeBackfillContractError(
                "candidate_mapped requires one exact recovery evidence reference"
            )
        if (
            self.disposition != "candidate_mapped"
            and self.matched_candidate is not None
        ):
            raise ConservativeBackfillContractError(
                "held or inapplicable cells cannot select recovery evidence"
            )
        if (
            not isinstance(self.reason_codes, tuple)
            or not self.reason_codes
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
        ):
            raise ConservativeBackfillContractError(
                "reason_codes must be non-empty, unique, and canonical-sorted"
            )
        if self.policy_version != POLICY_VERSION:
            raise ConservativeBackfillContractError(
                f"policy_version must be {POLICY_VERSION!r}"
            )
        if self.validation_status != VALIDATION_STATUS:
            raise ConservativeBackfillContractError(
                f"validation_status must be {VALIDATION_STATUS!r}"
            )
        if self.product_authority != PRODUCT_AUTHORITY:
            raise ConservativeBackfillContractError(
                "conservative Backfill decisions cannot grant product authority"
            )


def minimum_direct_support_count(sample_count_n: int) -> int:
    """Return K = max(2, floor(0.05 × N)) for an eligible run population."""

    if (
        isinstance(sample_count_n, bool)
        or not isinstance(sample_count_n, int)
        or sample_count_n < 0
    ):
        raise ConservativeBackfillContractError(
            "sample_count_n must be a non-negative integer"
        )
    return max(2, sample_count_n // 20)


def mint_conservative_backfill_recovery_candidate_universe(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    group_facts: tuple[ConservativeBackfillGroupFacts, ...],
    candidate_assessment: CidNlMethodAssessmentRef,
) -> ConservativeBackfillRecoveryCandidateUniverse:
    """Mint the sole target↔peak pairing authority for one Backfill revisit."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ConservativeBackfillContractError(
            "recovery universe requires validated processing owners"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity) or not any(
        sample is owned for owned in processing_owners.sample_resolutions
    ):
        raise ConservativeBackfillContractError(
            "recovery universe sample is outside the processing owner closure"
        )
    facts = _canonical_recovery_group_facts(group_facts)
    _validate_recovery_group_populations(
        facts,
        processing_owners=processing_owners,
    )
    _validate_recovery_sample_and_target(facts, sample=sample)
    target_definition_id = facts[0].group_context.target_definition_id
    target_snapshot = processing_owners.target_snapshot
    if target_snapshot is None or not any(
        definition.target_definition_id == target_definition_id
        for definition in target_snapshot.definitions
    ):
        raise ConservativeBackfillContractError(
            "recovery universe target is outside the processing owner closure"
        )
    _validate_recovery_candidate_assessment(
        candidate_assessment,
        processing_attempt=processing_owners.attempt,
        sample=sample,
        group_facts=facts,
    )
    _validate_recovery_cell_attempts(
        facts,
        processing_attempt=processing_owners.attempt,
    )
    payload = canonical_conservative_backfill_recovery_universe_bytes(
        processing_owners=processing_owners,
        sample=sample,
        group_facts=facts,
        candidate_assessment=candidate_assessment,
    )
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=(
            processing_owners.request.processing_request_id
        ),
        producer_processing_attempt_id=(
            processing_owners.attempt.processing_attempt_id
        ),
        semantic_contract=_RECOVERY_UNIVERSE_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=payload,
        upstream_artifacts=_recovery_universe_upstream_artifacts(
            facts,
            candidate_assessment,
        ),
    )
    return ConservativeBackfillRecoveryCandidateUniverse(
        processing_owners=processing_owners,
        processing_attempt=processing_owners.attempt,
        sample=sample,
        group_facts=facts,
        candidate_assessment=candidate_assessment,
        artifact=artifact,
    )


def canonical_conservative_backfill_recovery_universe_bytes(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    group_facts: tuple[ConservativeBackfillGroupFacts, ...],
    candidate_assessment: CidNlMethodAssessmentRef,
) -> bytes:
    """Serialize the exact no-write recovery universe for artifact replay."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ConservativeBackfillContractError(
            "recovery universe requires validated processing owners"
        )
    facts = _canonical_recovery_group_facts(group_facts)
    _validate_recovery_group_populations(
        facts,
        processing_owners=processing_owners,
    )
    _validate_recovery_sample_and_target(facts, sample=sample)
    if not isinstance(candidate_assessment, CidNlMethodAssessmentRef):
        raise ConservativeBackfillContractError(
            "recovery universe requires an owner-minted method assessment"
        )
    peaks = candidate_assessment.candidate_peaks
    processing_attempt = processing_owners.attempt
    request_sample_population = _processing_owner_sample_population(
        processing_owners
    )
    payload = {
        "schema_version": RECOVERY_UNIVERSE_SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "product_authority": PRODUCT_AUTHORITY,
        "processing_request_id": processing_attempt.processing_request_id,
        "processing_attempt_id": processing_attempt.processing_attempt_id,
        "canonical_sample_identity_id": sample.identity.sample_identity_id,
        "request_sample_population_ids": [
            member.identity.sample_identity_id
            for member in request_sample_population
        ],
        "target_key_id": facts[0].group_context.target_key_id,
        "target_definition_id": facts[0].group_context.target_definition_id,
        "candidate_assessment_artifact_id": (
            candidate_assessment.artifact.evidence_artifact_id
        ),
        "groups": [
            {
                "group_hypothesis_id": fact.group_context.group.group_hypothesis_id,
                "source_cell_artifact_ids": [
                    cell.artifact.evidence_artifact_id for cell in fact.source_cells
                ],
            }
            for fact in facts
        ],
        "candidate_peaks": [
            {
                "peak_observation_content_id": (
                    peak.content.peak_observation_content_id
                ),
                "peak_observation_artifact_id": peak.artifact.evidence_artifact_id,
            }
            for peak in peaks
        ],
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def assess_conservative_backfill_group(
    source_cells: tuple[CellObservationRef, ...],
    *,
    sample_population: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> ConservativeBackfillGroupFacts:
    """Derive no-write S/K facts without creating a group or cell."""

    if not source_cells:
        raise ConservativeBackfillContractError(
            "Backfill assessment requires an existing CellObservation group"
        )
    ordered_cells = tuple(
        sorted(source_cells, key=lambda cell: cell.content.canonical_sample_identity_id)
    )
    ordered_samples = tuple(
        sorted(
            sample_population,
            key=lambda sample: sample.identity.sample_identity_id,
        )
    )
    group_context = ordered_cells[0].group_context
    cells = _canonical_cells(ordered_cells, group_context=group_context)
    samples = _canonical_samples(ordered_samples)
    eligible_ids = tuple(
        sample.identity.sample_identity_id
        for sample in samples
        if _sample_is_activation_eligible(sample)
    )
    direct_support_ids = tuple(
        support.sample.identity.sample_identity_id
        for support in group_context.supports
    )
    eligible_support_ids = tuple(
        sample_id for sample_id in direct_support_ids if sample_id in eligible_ids
    )
    support_traces = tuple(
        _support_trace(support.hypothesis.peak_observation_refs)
        for support in group_context.supports
    )
    cell_sample_ids = tuple(
        cell.content.canonical_sample_identity_id for cell in cells
    )
    sample_ids = tuple(sample.identity.sample_identity_id for sample in samples)
    sample_count = len(eligible_ids)
    minimum_support = minimum_direct_support_count(sample_count)
    support_count = len(eligible_support_ids)
    frozen = cell_sample_ids == sample_ids
    return ConservativeBackfillGroupFacts(
        group_context=group_context,
        source_cells=cells,
        sample_population=samples,
        activation_eligible_sample_ids=eligible_ids,
        direct_support_sample_ids=direct_support_ids,
        activation_eligible_support_sample_ids=eligible_support_ids,
        support_trace_refs=support_traces,
        sample_count_n=sample_count,
        minimum_support_k=minimum_support,
        direct_support_count_s=support_count,
        group_frozen=frozen,
        may_attempt_recovery=frozen and support_count >= minimum_support,
        reason_codes=_group_reason_codes(
            group_frozen=frozen,
            direct_support_count=support_count,
            minimum_support=minimum_support,
        ),
    )


def assess_targeted_conservative_backfill_group(
    projections: tuple[TargetedCellObservationReferenceProjection, ...],
    *,
    sample_population: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> ConservativeBackfillGroupFacts:
    """Thin Targeted adapter over the exact M1 CellObservation references."""

    if not projections or any(
        not isinstance(item, TargetedCellObservationReferenceProjection)
        for item in projections
    ):
        raise ConservativeBackfillContractError(
            "Targeted adapter requires M1 Targeted reference projections"
        )
    return assess_conservative_backfill_group(
        tuple(item.cell for item in projections),
        sample_population=sample_population,
    )


def assess_untargeted_conservative_backfill_group(
    projections: tuple[UntargetedCellObservationReferenceProjection, ...],
    *,
    sample_population: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> ConservativeBackfillGroupFacts:
    """Thin Untargeted adapter over the exact M1 CellObservation references."""

    if not projections or any(
        not isinstance(item, UntargetedCellObservationReferenceProjection)
        for item in projections
    ):
        raise ConservativeBackfillContractError(
            "Untargeted adapter requires M1 Untargeted reference projections"
        )
    return assess_conservative_backfill_group(
        tuple(item.cell for item in projections),
        sample_population=sample_population,
    )


def map_conservative_backfill_candidates(
    group_facts: tuple[ConservativeBackfillGroupFacts, ...],
    *,
    sample: ResolvedCanonicalSampleIdentity,
    candidate_universes: tuple[
        ConservativeBackfillRecoveryCandidateUniverse,
        ...,
    ],
) -> tuple[ConservativeBackfillCellDecision, ...]:
    """Map like-to-like candidates jointly by strict cardinality and RT order."""

    if not isinstance(group_facts, tuple) or not group_facts or any(
        not isinstance(item, ConservativeBackfillGroupFacts) for item in group_facts
    ):
        raise ConservativeBackfillContractError(
            "mapping requires one or more conservative group facts"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise ConservativeBackfillContractError(
            "mapping requires an owner-resolved target sample"
        )
    if len({id(item.group_context) for item in group_facts}) != len(group_facts):
        raise ConservativeBackfillContractError(
            "mapping contains the same exact group context more than once"
        )
    if any(
        not any(sample is member for member in facts.sample_population)
        for facts in group_facts
    ):
        raise ConservativeBackfillContractError(
            "mapping sample is not the exact declared run sample"
        )
    if not isinstance(candidate_universes, tuple) or any(
        not isinstance(item, ConservativeBackfillRecoveryCandidateUniverse)
        for item in candidate_universes
    ):
        raise ConservativeBackfillContractError(
            "mapping requires typed immutable recovery universe receipts"
        )
    sample_id = sample.identity.sample_identity_id
    target_keys = tuple(universe.target_key for universe in candidate_universes)
    if len(set(target_keys)) != len(target_keys):
        raise ConservativeBackfillContractError(
            "one sample×target mapping requires at most one recovery universe receipt"
        )
    for universe in candidate_universes:
        if universe.sample is not sample:
            raise ConservativeBackfillContractError(
                "recovery universe does not bind the exact mapping sample"
            )
        matching_facts = tuple(
            fact
            for fact in group_facts
            if _group_target_key(fact) == universe.target_key
        )
        expected_facts = (
            _canonical_recovery_group_facts(matching_facts)
            if matching_facts
            else ()
        )
        if len(expected_facts) != len(universe.group_facts) or any(
            expected is not bound
            for expected, bound in zip(
                expected_facts,
                universe.group_facts,
                strict=True,
            )
        ):
            raise ConservativeBackfillContractError(
                "recovery universe does not bind the exact mapped group set"
            )
    universes_by_target = {
        universe.target_key: universe for universe in candidate_universes
    }
    known_target_keys = {_group_target_key(facts) for facts in group_facts}
    unknown_target_keys = set(universes_by_target).difference(known_target_keys)
    if unknown_target_keys:
        raise ConservativeBackfillContractError(
            "recovery evidence cannot create a first group or target universe"
        )

    target_cells = tuple(
        _target_cell(facts, sample_id=sample_id) for facts in group_facts
    )
    decisions: list[ConservativeBackfillCellDecision | None] = [None] * len(group_facts)
    active_by_target: dict[tuple[str, str], list[int]] = {}
    for index, (facts, cell) in enumerate(zip(group_facts, target_cells, strict=True)):
        candidate_universe = universes_by_target.get(_group_target_key(facts))
        target_candidates = _recovery_evidence_from_universe(
            candidate_universe
        )
        if not facts.group_frozen:
            decisions[index] = _decision(
                facts,
                cell,
                disposition="hold",
                reasons=("group_not_frozen",),
                candidate_universe=candidate_universe,
                candidates=target_candidates,
            )
        elif not facts.may_attempt_recovery:
            decisions[index] = _decision(
                facts,
                cell,
                disposition="hold",
                reasons=("direct_support_threshold_not_met",),
                candidate_universe=candidate_universe,
                candidates=target_candidates,
            )
        elif cell.content.method_result == "SUPPORT":
            decisions[index] = _decision(
                facts,
                cell,
                disposition="not_applicable",
                reasons=("cell_already_has_direct_support",),
                candidate_universe=candidate_universe,
                candidates=target_candidates,
            )
        else:
            active_by_target.setdefault(_group_target_key(facts), []).append(index)

    for target_key, indexes in active_by_target.items():
        candidate_universe = universes_by_target.get(target_key)
        target_candidates = _recovery_evidence_from_universe(candidate_universe)
        if len(target_candidates) != len(indexes):
            reason = (
                "recovery_evidence_missing"
                if candidate_universe is None
                else "recovery_candidate_universe_empty"
                if not target_candidates
                else "uncertain_candidate_cardinality"
            )
            for index in indexes:
                decisions[index] = _decision(
                    group_facts[index],
                    target_cells[index],
                    disposition="hold",
                    reasons=(reason,),
                    candidate_universe=candidate_universe,
                    candidates=target_candidates,
                )
            continue

        group_rt_by_index = {
            index: _finite_group_center_rt(group_facts[index]) for index in indexes
        }
        if any(value is None for value in group_rt_by_index.values()):
            for index in indexes:
                decisions[index] = _decision(
                    group_facts[index],
                    target_cells[index],
                    disposition="hold",
                    reasons=("group_rt_unavailable",),
                    candidate_universe=candidate_universe,
                    candidates=target_candidates,
                )
            continue
        ordered_indexes = sorted(
            indexes,
            key=lambda index: cast(float, group_rt_by_index[index]),
        )
        ordered_candidates = tuple(
            sorted(
                target_candidates,
                key=lambda item: item.peak_observation.content.integration.rt_apex_min,
            )
        )
        group_rts = tuple(
            _group_center_rt(group_facts[index]) for index in ordered_indexes
        )
        candidate_rts = tuple(
            item.peak_observation.content.integration.rt_apex_min
            for item in ordered_candidates
        )
        if len(set(group_rts)) != len(group_rts) or len(set(candidate_rts)) != len(
            candidate_rts
        ):
            for index in indexes:
                decisions[index] = _decision(
                    group_facts[index],
                    target_cells[index],
                    disposition="hold",
                    reasons=("non_unique_elution_order",),
                    candidate_universe=candidate_universe,
                    candidates=target_candidates,
                )
            continue
        pairs = tuple(zip(ordered_indexes, ordered_candidates, strict=True))
        mismatch_reasons = tuple(
            sorted(
                {
                    mismatch
                    for index, candidate in pairs
                    if (
                        mismatch := _candidate_group_mismatch_reason(
                            group_facts[index],
                            target_cells[index],
                            candidate,
                        )
                    )
                    is not None
                }
            )
        )
        if mismatch_reasons:
            for index in indexes:
                decisions[index] = _decision(
                    group_facts[index],
                    target_cells[index],
                    disposition="hold",
                    reasons=mismatch_reasons,
                    candidate_universe=candidate_universe,
                    candidates=target_candidates,
                )
            continue
        for index, candidate in pairs:
            decisions[index] = _decision(
                group_facts[index],
                target_cells[index],
                disposition="candidate_mapped",
                reasons=("ordered_one_to_one_recovery_evidence",),
                candidate_universe=candidate_universe,
                candidates=target_candidates,
                matched_candidate=candidate,
            )

    if any(item is None for item in decisions):
        raise ConservativeBackfillContractError(
            "conservative Backfill mapping did not close every group cell"
        )
    return tuple(item for item in decisions if item is not None)


def _canonical_cells(
    cells: object,
    *,
    group_context: CidNlCellGroupContext,
) -> tuple[CellObservationRef, ...]:
    if not isinstance(cells, tuple) or not cells or any(
        not isinstance(item, CellObservationRef) for item in cells
    ):
        raise ConservativeBackfillContractError(
            "Backfill facts require immutable canonical CellObservation references"
        )
    sample_ids = tuple(cell.content.canonical_sample_identity_id for cell in cells)
    if sample_ids != tuple(sorted(set(sample_ids))):
        raise ConservativeBackfillContractError(
            "CellObservation references must be unique and canonical-sorted by sample"
        )
    if any(cell.group_context is not group_context for cell in cells):
        raise ConservativeBackfillContractError(
            "Backfill facts must reuse one exact frozen group context"
        )
    return cells


def _canonical_recovery_group_facts(
    facts: object,
) -> tuple[ConservativeBackfillGroupFacts, ...]:
    if not isinstance(facts, tuple) or not facts or any(
        not isinstance(item, ConservativeBackfillGroupFacts) for item in facts
    ):
        raise ConservativeBackfillContractError(
            "recovery universe requires one or more conservative group facts"
        )
    ordered = tuple(
        sorted(
            facts,
            key=lambda item: (
                item.group_context.group.group_hypothesis_id,
                _group_center_rt(item),
                item.group_context.group.owner_ids,
            ),
        )
    )
    if len({id(item.group_context) for item in ordered}) != len(ordered):
        raise ConservativeBackfillContractError(
            "recovery universe group facts must be unique"
        )
    return ordered


def _validate_recovery_sample_and_target(
    facts: tuple[ConservativeBackfillGroupFacts, ...],
    *,
    sample: ResolvedCanonicalSampleIdentity,
) -> None:
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise ConservativeBackfillContractError(
            "recovery universe requires an owner-resolved sample"
        )
    target_keys = {_group_target_key(fact) for fact in facts}
    if len(target_keys) != 1:
        raise ConservativeBackfillContractError(
            "one recovery universe must bind exactly one target"
        )
    if any(
        not any(sample is member for member in fact.sample_population)
        for fact in facts
    ):
        raise ConservativeBackfillContractError(
            "recovery universe sample is outside its declared group populations"
        )


def _processing_owner_sample_population(
    processing_owners: ProcessingOwnerReferences,
) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ConservativeBackfillContractError(
            "recovery universe requires validated processing owners"
        )
    return _canonical_samples(
        tuple(
            sorted(
                processing_owners.sample_resolutions,
                key=lambda member: member.identity.sample_identity_id,
            )
        )
    )


def _validate_recovery_group_populations(
    facts: tuple[ConservativeBackfillGroupFacts, ...],
    *,
    processing_owners: ProcessingOwnerReferences,
) -> None:
    owner_population = _processing_owner_sample_population(processing_owners)
    if any(
        len(fact.sample_population) != len(owner_population)
        or any(
            declared is not owned
            for declared, owned in zip(
                fact.sample_population,
                owner_population,
                strict=True,
            )
        )
        for fact in facts
    ):
        raise ConservativeBackfillContractError(
            "recovery group population must close the full processing request"
        )


def _canonical_recovery_peaks(
    peaks: object,
    *,
    sample: ResolvedCanonicalSampleIdentity,
) -> tuple[PeakObservationRef, ...]:
    if not isinstance(peaks, tuple) or any(
        not isinstance(item, PeakObservationRef) for item in peaks
    ):
        raise ConservativeBackfillContractError(
            "recovery universe peaks must be immutable PeakObservation references"
        )
    sample_id = sample.identity.sample_identity_id
    if any(
        peak.content.canonical_sample_identity_id != sample_id for peak in peaks
    ):
        raise ConservativeBackfillContractError(
            "recovery universe peak belongs to a different sample"
        )
    ordered = tuple(
        sorted(
            peaks,
            key=lambda peak: peak.content.peak_observation_content_id,
        )
    )
    peak_ids = tuple(
        peak.content.peak_observation_content_id for peak in ordered
    )
    if len(peak_ids) != len(set(peak_ids)):
        raise ConservativeBackfillContractError(
            "recovery universe cannot contain duplicate physical peaks"
        )
    return ordered


def _recovery_universe_upstream_artifacts(
    facts: tuple[ConservativeBackfillGroupFacts, ...],
    candidate_assessment: CidNlMethodAssessmentRef,
) -> tuple[EvidenceArtifactRef, ...]:
    artifacts = (
        *(cell.artifact for fact in facts for cell in fact.source_cells),
        candidate_assessment.artifact,
    )
    by_id = {artifact.evidence_artifact_id: artifact for artifact in artifacts}
    return tuple(by_id[artifact_id] for artifact_id in sorted(by_id))


def _validate_recovery_candidate_assessment(
    assessment: object,
    *,
    processing_attempt: ProcessingAttemptIdentity,
    sample: ResolvedCanonicalSampleIdentity,
    group_facts: tuple[ConservativeBackfillGroupFacts, ...],
) -> None:
    if not isinstance(assessment, CidNlMethodAssessmentRef):
        raise ConservativeBackfillContractError(
            "recovery universe requires an owner-minted method assessment"
        )
    target_key = _group_target_key(group_facts[0])
    if (
        assessment.content.canonical_sample_identity_id
        != sample.identity.sample_identity_id
        or (
            assessment.content.target_key_id,
            assessment.content.target_definition_id,
        )
        != target_key
    ):
        raise ConservativeBackfillContractError(
            "recovery assessment does not bind the exact sample and target"
        )
    if (
        assessment.artifact.producer_processing_request_id
        != processing_attempt.processing_request_id
        or assessment.artifact.producer_processing_attempt_id
        != processing_attempt.processing_attempt_id
    ):
        raise ConservativeBackfillContractError(
            "recovery assessment is outside the exact processing attempt"
        )
    artifacts = (
        assessment.artifact,
        *(peak.artifact for peak in assessment.candidate_peaks),
        *(item.artifact for item in assessment.evidence_refs),
        *(item.artifact for item in assessment.individual_support_refs),
        *(item.artifact for item in assessment.set_relation_refs),
    )
    try:
        canonical_current_attempt_artifact_closure(
            producer_attempt=processing_attempt,
            artifacts=artifacts,
        )
    except ValueError as exc:
        raise ConservativeBackfillContractError(
            "recovery assessment artifact graph is outside the processing attempt"
        ) from exc


def _recovery_evidence_from_universe(
    universe: ConservativeBackfillRecoveryCandidateUniverse | None,
) -> tuple[ConservativeBackfillRecoveryEvidence, ...]:
    if universe is None:
        return ()
    return tuple(
        ConservativeBackfillRecoveryEvidence(
            candidate_universe=universe,
            peak_observation=peak,
        )
        for peak in universe.candidate_peaks
    )


def _validate_recovery_cell_attempts(
    facts: tuple[ConservativeBackfillGroupFacts, ...],
    *,
    processing_attempt: ProcessingAttemptIdentity,
) -> None:
    allowed_attempt_ids = {
        processing_attempt.processing_attempt_id,
        *retry_predecessor_attempt_ids(processing_attempt),
    }
    if any(
        cell.artifact.producer_processing_request_id
        != processing_attempt.processing_request_id
        or cell.artifact.producer_processing_attempt_id not in allowed_attempt_ids
        for fact in facts
        for cell in fact.source_cells
    ):
        raise ConservativeBackfillContractError(
            "recovery group cells are outside the processing attempt lineage"
        )


def _canonical_samples(
    samples: object,
) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
    if not isinstance(samples, tuple) or not samples or any(
        not isinstance(item, ResolvedCanonicalSampleIdentity) for item in samples
    ):
        raise ConservativeBackfillContractError(
            "sample_population must contain owner-resolved run samples"
        )
    sample_ids = tuple(sample.identity.sample_identity_id for sample in samples)
    if sample_ids != tuple(sorted(set(sample_ids))):
        raise ConservativeBackfillContractError(
            "sample_population must be unique and canonical-sorted"
        )
    snapshots = {sample.snapshot.snapshot_id for sample in samples}
    if len(snapshots) != 1:
        raise ConservativeBackfillContractError(
            "sample_population must come from one owner snapshot"
        )
    return samples


def _sample_is_activation_eligible(sample: ResolvedCanonicalSampleIdentity) -> bool:
    return (
        not sample.metadata.excluded
        and sample.metadata.sample_role in ACTIVATION_ELIGIBLE_SAMPLE_ROLES
    )


def _support_trace(
    refs: tuple[PeakObservationRef, ...],
) -> PeakObservationRef:
    if len(refs) != 1 or not isinstance(refs[0], PeakObservationRef):
        raise ConservativeBackfillContractError(
            "CID-NL direct SUPPORT must retain exactly one physical trace reference"
        )
    return refs[0]


def _group_reason_codes(
    *,
    group_frozen: bool,
    direct_support_count: int,
    minimum_support: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not group_frozen:
        reasons.append("group_not_frozen")
    if direct_support_count < minimum_support:
        reasons.append("direct_support_threshold_not_met")
    if not reasons:
        reasons.append("direct_support_threshold_met")
    return tuple(sorted(reasons))


def _target_cell(
    facts: ConservativeBackfillGroupFacts,
    *,
    sample_id: str,
) -> CellObservationRef:
    cells = tuple(
        cell
        for cell in facts.source_cells
        if cell.content.canonical_sample_identity_id == sample_id
    )
    if len(cells) != 1:
        raise ConservativeBackfillContractError(
            "frozen group does not contain exactly one canonical target sample cell"
        )
    return cells[0]


def _group_target_key(facts: ConservativeBackfillGroupFacts) -> tuple[str, str]:
    return (
        facts.group_context.target_key_id,
        facts.group_context.target_definition_id,
    )


def _group_center_rt(facts: ConservativeBackfillGroupFacts) -> float:
    value = _finite_group_center_rt(facts)
    if value is None:
        raise ConservativeBackfillContractError(
            "ordered Backfill mapping requires a finite group center RT"
        )
    return value


def _finite_group_center_rt(
    facts: ConservativeBackfillGroupFacts,
) -> float | None:
    value = facts.group_context.group.group_center_rt
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def _candidate_group_mismatch_reason(
    facts: ConservativeBackfillGroupFacts,
    target_cell: CellObservationRef,
    candidate: ConservativeBackfillRecoveryEvidence,
) -> str | None:
    if candidate.target_key != _group_target_key(facts):
        return "like_to_like_rt_mz_mismatch"
    candidate_artifact = candidate.peak_observation.artifact
    assessment_artifact = target_cell.method_assessment.artifact
    if (
        candidate_artifact.producer_processing_request_id
        != assessment_artifact.producer_processing_request_id
        or candidate_artifact.producer_processing_attempt_id
        != assessment_artifact.producer_processing_attempt_id
    ):
        return "processing_attempt_mismatch"
    group = facts.group_context.group
    group_rt = _group_center_rt(facts)
    group_mz = group.group_center_mz
    if group_mz is None or not math.isfinite(group_mz) or group_mz <= 0.0:
        return "like_to_like_rt_mz_mismatch"
    observation = candidate.peak_observation
    provenance = observation.generation_provenance
    if not provenance.query_rt_min <= group_rt <= provenance.query_rt_max:
        return "like_to_like_rt_mz_mismatch"
    ppm_delta = abs(observation.content.mz - group_mz) / group_mz * 1_000_000.0
    if ppm_delta > provenance.query_ppm_tol:
        return "like_to_like_rt_mz_mismatch"
    return None


def _decision(
    facts: ConservativeBackfillGroupFacts,
    cell: CellObservationRef,
    *,
    disposition: ConservativeBackfillDisposition,
    reasons: tuple[str, ...],
    candidate_universe: ConservativeBackfillRecoveryCandidateUniverse | None = None,
    candidates: tuple[ConservativeBackfillRecoveryEvidence, ...] = (),
    matched_candidate: ConservativeBackfillRecoveryEvidence | None = None,
) -> ConservativeBackfillCellDecision:
    return ConservativeBackfillCellDecision(
        group_facts=facts,
        target_cell=cell,
        candidate_universe=candidate_universe,
        candidate_evidence=tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.peak_observation.content.integration.rt_apex_min,
                    item.peak_observation.content.peak_observation_content_id,
                ),
            )
        ),
        matched_candidate=matched_candidate,
        disposition=disposition,
        reason_codes=tuple(sorted(set(reasons))),
        _mint_token=_CELL_DECISION_MINT_TOKEN,
    )


__all__ = [
    "ACTIVATION_ELIGIBLE_SAMPLE_ROLES",
    "ConservativeBackfillCellDecision",
    "ConservativeBackfillContractError",
    "ConservativeBackfillGroupFacts",
    "ConservativeBackfillRecoveryCandidateUniverse",
    "ConservativeBackfillRecoveryEvidence",
    "POLICY_VERSION",
    "assess_conservative_backfill_group",
    "assess_targeted_conservative_backfill_group",
    "assess_untargeted_conservative_backfill_group",
    "canonical_conservative_backfill_recovery_universe_bytes",
    "map_conservative_backfill_candidates",
    "minimum_direct_support_count",
    "mint_conservative_backfill_recovery_candidate_universe",
]
