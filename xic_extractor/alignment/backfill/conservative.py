"""Conservative no-write Backfill eligibility and mapping facts.

This module plans backfill from direct-SUPPORT group membership and consumes
canonical CellObservationRef evidence for final decisions. It may decide
whether an already-existing, frozen group is eligible for a bounded
Backfill revisit and whether supplied backfill evidence has an unambiguous
ordered one-to-one mapping.  It never creates a group, mints a cell, selects a
numeric product value, or calls a writer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, cast, final

from xic_extractor.alignment.backfill.cid_nl_evidence import (
    cid_nl_allows_physical_localization,
    cid_nl_backfill_block_reason,
)
from xic_extractor.alignment.cell_observation.observations import (
    CellObservationRef,
    CidNlCellGroupContext,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.peak_trajectories import BackfillPeakLocation
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.physical_peak_evaluation import (
    answer_physical_peak_question,
    mint_physical_peak_question,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PHYSICAL_PEAK_BACKFILL_POLICY,
    PreparedSignalResult,
)
from xic_extractor.sample_metadata import SampleMetadata

POLICY_VERSION = "conservative-backfill"
PRODUCT_AUTHORITY = "none"
ACTIVATION_ELIGIBLE_SAMPLE_ROLES = frozenset({"study_sample", "qc", "pooled_qc"})

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
    sample_population: tuple[SampleMetadata, ...]
    activation_eligible_sample_ids: tuple[str, ...]
    direct_support_sample_ids: tuple[str, ...]
    activation_eligible_support_sample_ids: tuple[str, ...]
    support_trace_refs: tuple[PeakObservationRef, ...]
    sample_count_n: int
    minimum_support_k: int
    direct_support_count_s: int
    group_frozen: bool
    may_attempt_backfill: bool
    reason_codes: tuple[str, ...]
    policy_version: str = POLICY_VERSION
    unsearched_sample_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConservativeBackfillCandidateUniverse:
    """Owner receipt binding one sample×method-subject revisit to its peak set."""

    sample: SampleMetadata
    group_facts: tuple[ConservativeBackfillGroupFacts, ...]
    candidate_assessment: CidNlMethodAssessmentRef
    localization: BackfillPeakLocation | None = None
    localization_sources: tuple[PeakObservationRef, ...] = ()

    @property
    def method_subject_id(self) -> str:
        return _group_method_subject_id(self.group_facts[0])

    @property
    def candidate_peaks(self) -> tuple[PeakObservationRef, ...]:
        return self.candidate_assessment.candidate_peaks


@dataclass(frozen=True, slots=True)
class ConservativeBackfillEvidence:
    """One peak selected only from an owner-bound backfill universe receipt."""

    candidate_universe: ConservativeBackfillCandidateUniverse
    peak_observation: PeakObservationRef
    evidence_source: str = "owner_backfill_revisit"

    @property
    def method_subject_id(self) -> str:
        return self.candidate_universe.method_subject_id


@final
@dataclass(frozen=True, slots=True)
class ConservativeBackfillCellDecision:
    """A no-write mapping verdict for one sample × existing group cell."""

    group_facts: ConservativeBackfillGroupFacts
    target_cell: CellObservationRef
    candidate_universe: ConservativeBackfillCandidateUniverse | None
    candidate_evidence: tuple[ConservativeBackfillEvidence, ...]
    matched_candidate: ConservativeBackfillEvidence | None
    disposition: ConservativeBackfillDisposition
    reason_codes: tuple[str, ...]
    policy_version: str = POLICY_VERSION

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ConservativeBackfillCellDecision is final")


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


def mint_conservative_backfill_candidate_universe(
    *,
    sample: SampleMetadata,
    group_facts: tuple[ConservativeBackfillGroupFacts, ...],
    candidate_assessment: CidNlMethodAssessmentRef,
    localization: BackfillPeakLocation | None = None,
    localization_sources: tuple[PeakObservationRef, ...] = (),
) -> ConservativeBackfillCandidateUniverse:
    """Mint the sole target↔peak pairing authority for one Backfill revisit."""

    facts = _canonical_backfill_group_facts(group_facts)
    if (
        candidate_assessment.content.sample_key != sample.sample_name
        or candidate_assessment.content.method_subject.method_subject_id
        != _group_method_subject_id(facts[0])
    ):
        raise ConservativeBackfillContractError(
            "backfill assessment does not bind the exact sample and target-compatible "
            "method subject"
        )
    return ConservativeBackfillCandidateUniverse(
        sample=sample,
        group_facts=facts,
        candidate_assessment=candidate_assessment,
        localization=localization,
        localization_sources=localization_sources,
    )


def plan_conservative_backfill_targets(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    *,
    sample_population: tuple[SampleMetadata, ...],
) -> tuple[tuple[CrossSamplePeakGroupHypothesis, frozenset[str]], ...]:
    """Plan missing-sample work from frozen direct-SUPPORT membership.

    The acquisition caller supplies groups constructed only from direct SUPPORT
    owners and a complete sample population. Scheduling needs their sample votes,
    not a dense population of CellObservationRefs or quantitation artifacts.
    Final cell composition remains responsible for the output evidence. A
    rejected group is no row, so it searches no missing sample.
    """
    eligible_ids = frozenset(
        sample.sample_name
        for sample in sample_population
        if _sample_is_activation_eligible(sample)
    )
    minimum_support = minimum_direct_support_count(len(eligible_ids))
    planned = []
    for group in groups:
        if group.disposition == "rejected":
            continue
        support_ids = frozenset(member.sample_key for member in group.member_subjects)
        if len(support_ids & eligible_ids) < minimum_support:
            continue
        targets = eligible_ids - support_ids
        if targets:
            planned.append((group, targets))
    return tuple(planned)


def assess_conservative_backfill_group(
    source_cells: tuple[CellObservationRef, ...],
    *,
    sample_population: tuple[SampleMetadata, ...],
    unsearched_sample_ids: tuple[str, ...] = (),
) -> ConservativeBackfillGroupFacts:
    """Derive no-write S/K facts without creating a group or cell."""

    if not source_cells:
        raise ConservativeBackfillContractError(
            "Backfill assessment requires an existing CellObservation group"
        )
    ordered_cells = tuple(
        sorted(source_cells, key=lambda cell: cell.content.sample_key)
    )
    ordered_samples = tuple(
        sorted(
            sample_population,
            key=lambda sample: sample.sample_name,
        )
    )
    group_context = ordered_cells[0].group_context
    cells = _canonical_cells(ordered_cells, group_context=group_context)
    samples = _canonical_samples(ordered_samples)
    eligible_ids = tuple(
        sample.sample_name
        for sample in samples
        if _sample_is_activation_eligible(sample)
    )
    direct_support_ids = tuple(
        support.sample.sample_name for support in group_context.supports
    )
    eligible_support_ids = tuple(
        sample_id for sample_id in direct_support_ids if sample_id in eligible_ids
    )
    support_traces = tuple(
        _support_trace(support.hypothesis.peak_observation_refs)
        for support in group_context.supports
    )
    cell_sample_ids = tuple(cell.content.sample_key for cell in cells)
    sample_ids = tuple(sample.sample_name for sample in samples)
    sample_count = len(eligible_ids)
    minimum_support = minimum_direct_support_count(sample_count)
    support_count = len(eligible_support_ids)
    frozen = set(cell_sample_ids).union(unsearched_sample_ids) == set(sample_ids)
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
        may_attempt_backfill=frozen and support_count >= minimum_support,
        reason_codes=_group_reason_codes(
            group_frozen=frozen,
            direct_support_count=support_count,
            minimum_support=minimum_support,
        ),
        unsearched_sample_ids=unsearched_sample_ids,
    )


def map_conservative_backfill_candidates(
    group_facts: tuple[ConservativeBackfillGroupFacts, ...],
    *,
    sample: SampleMetadata,
    candidate_universes: tuple[
        ConservativeBackfillCandidateUniverse,
        ...,
    ],
) -> tuple[ConservativeBackfillCellDecision, ...]:
    """Map like-to-like candidates jointly by strict cardinality and RT order."""

    if (
        not isinstance(group_facts, tuple)
        or not group_facts
        or any(
            not isinstance(item, ConservativeBackfillGroupFacts) for item in group_facts
        )
    ):
        raise ConservativeBackfillContractError(
            "mapping requires one or more conservative group facts"
        )
    if not isinstance(sample, SampleMetadata):
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
        not isinstance(item, ConservativeBackfillCandidateUniverse)
        for item in candidate_universes
    ):
        raise ConservativeBackfillContractError(
            "mapping requires typed immutable backfill universe receipts"
        )
    sample_id = sample.sample_name
    method_subject_ids = tuple(
        universe.method_subject_id for universe in candidate_universes
    )
    if len(set(method_subject_ids)) != len(method_subject_ids):
        raise ConservativeBackfillContractError(
            "one sample×method-subject mapping allows at most one backfill universe "
            "receipt"
        )
    for universe in candidate_universes:
        if universe.sample is not sample:
            raise ConservativeBackfillContractError(
                "backfill universe does not bind the exact mapping sample"
            )
        matching_facts = tuple(
            fact
            for fact in group_facts
            if _group_method_subject_id(fact) == universe.method_subject_id
        )
        expected_facts = (
            _canonical_backfill_group_facts(matching_facts) if matching_facts else ()
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
                "backfill universe does not bind the exact mapped group set"
            )
    universes_by_subject = {
        universe.method_subject_id: universe for universe in candidate_universes
    }
    known_subject_ids = {_group_method_subject_id(facts) for facts in group_facts}
    unknown_subject_ids = set(universes_by_subject).difference(known_subject_ids)
    if unknown_subject_ids:
        raise ConservativeBackfillContractError(
            "backfill evidence cannot create a first group or method subject"
        )

    target_cells = tuple(
        _target_cell(facts, sample_id=sample_id) for facts in group_facts
    )
    decisions: list[ConservativeBackfillCellDecision | None] = [None] * len(group_facts)
    active_by_subject: dict[str, list[int]] = {}
    for index, (facts, cell) in enumerate(zip(group_facts, target_cells, strict=True)):
        candidate_universe = universes_by_subject.get(_group_method_subject_id(facts))
        target_candidates = _backfill_evidence_from_universe(candidate_universe)
        if reason := cid_nl_backfill_block_reason(cell.method_assessment):
            decisions[index] = _decision(
                facts,
                cell,
                disposition="not_applicable",
                reasons=(reason,),
                candidate_universe=candidate_universe,
                candidates=target_candidates,
            )
        elif not facts.group_frozen:
            decisions[index] = _decision(
                facts,
                cell,
                disposition="hold",
                reasons=("group_not_frozen",),
                candidate_universe=candidate_universe,
                candidates=target_candidates,
            )
        elif not facts.may_attempt_backfill:
            decisions[index] = _decision(
                facts,
                cell,
                disposition="hold",
                reasons=("direct_support_threshold_not_met",),
                candidate_universe=candidate_universe,
                candidates=target_candidates,
            )
        else:
            active_by_subject.setdefault(
                _group_method_subject_id(facts),
                [],
            ).append(index)

    for method_subject_id, indexes in active_by_subject.items():
        candidate_universe = universes_by_subject.get(method_subject_id)
        all_candidates = _backfill_evidence_from_universe(candidate_universe)
        target_candidates = _localized_backfill_candidates(
            candidate_universe, all_candidates
        )
        if len(target_candidates) != len(indexes):
            reason = (
                "recovery_evidence_missing"
                if candidate_universe is None
                else "backfill_candidate_universe_empty"
                if not all_candidates
                else "sample_localization_unreliable"
                if candidate_universe.localization is not None
                and not candidate_universe.localization.reliable
                else "no_unique_qualified_ms1_peak"
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
                    candidates=all_candidates,
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
                    candidates=all_candidates,
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
                    candidates=all_candidates,
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
                    candidates=all_candidates,
                )
            continue
        for index, candidate in pairs:
            decisions[index] = _decision(
                group_facts[index],
                target_cells[index],
                disposition="candidate_mapped",
                reasons=("ordered_one_to_one_recovery_evidence",),
                candidate_universe=candidate_universe,
                candidates=all_candidates,
                matched_candidate=candidate,
            )

    if any(item is None for item in decisions):
        raise ConservativeBackfillContractError(
            "conservative Backfill mapping did not close every group cell"
        )
    return tuple(item for item in decisions if item is not None)


def _localized_backfill_candidates(
    universe: ConservativeBackfillCandidateUniverse | None,
    candidates: tuple[ConservativeBackfillEvidence, ...],
) -> tuple[ConservativeBackfillEvidence, ...]:
    """Qualify retained physical evidence without dropping its full provenance."""
    if universe is None or universe.localization is None:
        return candidates
    location = universe.localization
    if not location.reliable or not candidates:
        return ()
    if not cid_nl_allows_physical_localization(universe.candidate_assessment):
        return ()
    evaluation = candidates[0].peak_observation.physical_evaluation
    if evaluation is None or evaluation.retained_prepared_signal is None:
        return ()
    assert location.apex_tolerance_sec is not None
    query_rt = location.location.query_rt
    half_window = location.apex_tolerance_sec / 60.0
    question = mint_physical_peak_question(
        evaluation.prepared,
        evaluation.segment_universe,
        query_rt_min=query_rt,
        selection_policy_version=PHYSICAL_PEAK_BACKFILL_POLICY,
        search_rt_window=(max(0.0, query_rt - half_window), query_rt + half_window),
    )
    answer = answer_physical_peak_question(
        PreparedSignalResult(
            evaluation.prepared, evaluation.retained_prepared_signal, ()
        ),
        evaluation.segment_universe,
        question,
    )
    if answer.state != "available":
        return ()
    return tuple(
        candidate
        for candidate in candidates
        if candidate.peak_observation.physical_evaluation is not None
        and candidate.peak_observation.physical_evaluation.answer.selected_segment
        == answer.answer.selected_segment
    )


def _canonical_cells(
    cells: object,
    *,
    group_context: CidNlCellGroupContext,
) -> tuple[CellObservationRef, ...]:
    if (
        not isinstance(cells, tuple)
        or not cells
        or any(not isinstance(item, CellObservationRef) for item in cells)
    ):
        raise ConservativeBackfillContractError(
            "Backfill facts require immutable canonical CellObservation references"
        )
    sample_ids = tuple(cell.content.sample_key for cell in cells)
    if sample_ids != tuple(sorted(set(sample_ids))):
        raise ConservativeBackfillContractError(
            "CellObservation references must be unique and canonical-sorted by sample"
        )
    if any(cell.group_context is not group_context for cell in cells):
        raise ConservativeBackfillContractError(
            "Backfill facts must reuse one exact frozen group context"
        )
    return cells


def _canonical_backfill_group_facts(
    facts: object,
) -> tuple[ConservativeBackfillGroupFacts, ...]:
    if (
        not isinstance(facts, tuple)
        or not facts
        or any(not isinstance(item, ConservativeBackfillGroupFacts) for item in facts)
    ):
        raise ConservativeBackfillContractError(
            "backfill universe requires one or more conservative group facts"
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
            "backfill universe group facts must be unique"
        )
    return ordered


def _backfill_evidence_from_universe(
    universe: ConservativeBackfillCandidateUniverse | None,
) -> tuple[ConservativeBackfillEvidence, ...]:
    if universe is None:
        return ()
    return tuple(
        ConservativeBackfillEvidence(
            candidate_universe=universe,
            peak_observation=peak,
        )
        for peak in universe.candidate_peaks
    )


def _canonical_samples(
    samples: object,
) -> tuple[SampleMetadata, ...]:
    if not isinstance(samples, tuple) or not samples:
        raise ConservativeBackfillContractError(
            "sample_population must contain owner-resolved run samples"
        )
    sample_ids = tuple(sample.sample_name for sample in samples)
    if sample_ids != tuple(sorted(set(sample_ids))):
        raise ConservativeBackfillContractError(
            "sample_population must be unique and canonical-sorted"
        )
    return samples


def _sample_is_activation_eligible(sample: SampleMetadata) -> bool:
    return (
        not sample.excluded and sample.sample_role in ACTIVATION_ELIGIBLE_SAMPLE_ROLES
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
        cell for cell in facts.source_cells if cell.content.sample_key == sample_id
    )
    if len(cells) != 1:
        raise ConservativeBackfillContractError(
            "frozen group does not contain exactly one canonical target sample cell"
        )
    return cells[0]


def _group_method_subject_id(facts: ConservativeBackfillGroupFacts) -> str:
    return facts.group_context.method_subject_id


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
    candidate: ConservativeBackfillEvidence,
) -> str | None:
    if candidate.method_subject_id != _group_method_subject_id(facts):
        return "like_to_like_rt_mz_mismatch"
    group = facts.group_context.group
    group_rt = _group_center_rt(facts)
    group_mz = group.group_center_mz
    if group_mz is None or not math.isfinite(group_mz) or group_mz <= 0.0:
        return "like_to_like_rt_mz_mismatch"
    observation = candidate.peak_observation
    provenance = observation.generation_provenance
    location = candidate.candidate_universe.localization
    query_rt = group_rt if location is None else location.location.query_rt
    if not provenance.query_rt_min <= query_rt <= provenance.query_rt_max:
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
    candidate_universe: ConservativeBackfillCandidateUniverse | None = None,
    candidates: tuple[ConservativeBackfillEvidence, ...] = (),
    matched_candidate: ConservativeBackfillEvidence | None = None,
) -> ConservativeBackfillCellDecision:
    return ConservativeBackfillCellDecision(
        group_facts=facts,
        target_cell=cell,
        candidate_universe=candidate_universe,
        candidate_evidence=candidates,
        matched_candidate=matched_candidate,
        disposition=disposition,
        reason_codes=tuple(sorted(set(reasons))),
    )


__all__ = [
    "ACTIVATION_ELIGIBLE_SAMPLE_ROLES",
    "ConservativeBackfillCellDecision",
    "ConservativeBackfillContractError",
    "ConservativeBackfillGroupFacts",
    "ConservativeBackfillCandidateUniverse",
    "ConservativeBackfillEvidence",
    "POLICY_VERSION",
    "assess_conservative_backfill_group",
    "map_conservative_backfill_candidates",
    "minimum_direct_support_count",
    "mint_conservative_backfill_candidate_universe",
]
