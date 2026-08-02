"""Conservative no-write Backfill eligibility and mapping facts.

This module consumes the canonical M1 ``CellObservationRef`` owner.  It may
decide whether an already-existing, frozen group is eligible for a bounded
Backfill revisit and whether supplied recovery evidence has an unambiguous
ordered one-to-one mapping.  It never creates a group, mints a cell, selects a
numeric product value, or calls a writer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, cast

from xic_extractor.alignment.cell_observations import (
    CellObservationRef,
    CidNlCellGroupContext,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    TargetedCellObservationReferenceProjection,
    UntargetedCellObservationReferenceProjection,
)
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

POLICY_VERSION = "conservative-backfill-v1"
VALIDATION_STATUS = "shadow_ready"
PRODUCT_AUTHORITY = "none"
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
class ConservativeBackfillRecoveryEvidence:
    """One owner-backed physical candidate for a no-write Backfill revisit."""

    target: ResolvedTargetDefinition
    peak_observation: PeakObservationRef
    evidence_source: str = "owner_backfill_revisit"
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY

    def __post_init__(self) -> None:
        if not isinstance(self.target, ResolvedTargetDefinition):
            raise ConservativeBackfillContractError(
                "recovery evidence requires an owner-resolved target"
            )
        if not isinstance(self.peak_observation, PeakObservationRef):
            raise ConservativeBackfillContractError(
                "recovery evidence requires an owner-backed physical peak"
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
        return (
            self.target.definition.target_key.target_key_id,
            self.target.definition.target_definition_id,
        )


@dataclass(frozen=True, slots=True)
class ConservativeBackfillCellDecision:
    """A no-write mapping verdict for one sample × existing group cell."""

    group_facts: ConservativeBackfillGroupFacts
    target_cell: CellObservationRef
    candidate_evidence: tuple[ConservativeBackfillRecoveryEvidence, ...]
    matched_candidate: ConservativeBackfillRecoveryEvidence | None
    disposition: ConservativeBackfillDisposition
    reason_codes: tuple[str, ...]
    policy_version: str = POLICY_VERSION
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY

    def __post_init__(self) -> None:
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
        if not isinstance(self.candidate_evidence, tuple) or any(
            not isinstance(item, ConservativeBackfillRecoveryEvidence)
            for item in self.candidate_evidence
        ):
            raise ConservativeBackfillContractError(
                "candidate_evidence must contain typed immutable recovery evidence"
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
    candidates: tuple[ConservativeBackfillRecoveryEvidence, ...],
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
    if not isinstance(candidates, tuple) or any(
        not isinstance(item, ConservativeBackfillRecoveryEvidence)
        for item in candidates
    ):
        raise ConservativeBackfillContractError(
            "mapping candidates must be typed immutable recovery evidence"
        )
    sample_id = sample.identity.sample_identity_id
    candidate_ids = tuple(
        candidate.peak_observation.content.peak_observation_content_id
        for candidate in candidates
    )
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ConservativeBackfillContractError(
            "recovery evidence contains a duplicate physical peak"
        )
    if any(
        candidate.peak_observation.content.canonical_sample_identity_id != sample_id
        for candidate in candidates
    ):
        raise ConservativeBackfillContractError(
            "recovery evidence belongs to a different sample"
        )

    candidates_by_target: dict[
        tuple[str, str], list[ConservativeBackfillRecoveryEvidence]
    ] = {}
    for candidate in candidates:
        candidates_by_target.setdefault(candidate.target_key, []).append(candidate)
    known_target_keys = {_group_target_key(facts) for facts in group_facts}
    unknown_target_keys = set(candidates_by_target).difference(known_target_keys)
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
        target_candidates = tuple(
            candidates_by_target.get(_group_target_key(facts), ())
        )
        if not facts.group_frozen:
            decisions[index] = _decision(
                facts,
                cell,
                disposition="hold",
                reasons=("group_not_frozen",),
                candidates=target_candidates,
            )
        elif not facts.may_attempt_recovery:
            decisions[index] = _decision(
                facts,
                cell,
                disposition="hold",
                reasons=("direct_support_threshold_not_met",),
                candidates=target_candidates,
            )
        elif cell.content.method_result == "SUPPORT":
            decisions[index] = _decision(
                facts,
                cell,
                disposition="not_applicable",
                reasons=("cell_already_has_direct_support",),
            )
        else:
            active_by_target.setdefault(_group_target_key(facts), []).append(index)

    for target_key, indexes in active_by_target.items():
        target_candidates = tuple(candidates_by_target.get(target_key, ()))
        if len(target_candidates) != len(indexes):
            reason = (
                "recovery_evidence_missing"
                if not target_candidates
                else "uncertain_candidate_cardinality"
            )
            for index in indexes:
                decisions[index] = _decision(
                    group_facts[index],
                    target_cells[index],
                    disposition="hold",
                    reasons=(reason,),
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
                    candidates=target_candidates,
                )
            continue
        for index, candidate in pairs:
            decisions[index] = _decision(
                group_facts[index],
                target_cells[index],
                disposition="candidate_mapped",
                reasons=("ordered_one_to_one_recovery_evidence",),
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
    candidates: tuple[ConservativeBackfillRecoveryEvidence, ...] = (),
    matched_candidate: ConservativeBackfillRecoveryEvidence | None = None,
) -> ConservativeBackfillCellDecision:
    return ConservativeBackfillCellDecision(
        group_facts=facts,
        target_cell=cell,
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
    )


__all__ = [
    "ACTIVATION_ELIGIBLE_SAMPLE_ROLES",
    "ConservativeBackfillCellDecision",
    "ConservativeBackfillContractError",
    "ConservativeBackfillGroupFacts",
    "ConservativeBackfillRecoveryEvidence",
    "POLICY_VERSION",
    "assess_conservative_backfill_group",
    "assess_targeted_conservative_backfill_group",
    "assess_untargeted_conservative_backfill_group",
    "map_conservative_backfill_candidates",
    "minimum_direct_support_count",
]
