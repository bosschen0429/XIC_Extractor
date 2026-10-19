"""Retain owner decisions and close Backfill request coverage at assembly."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Literal, cast

from xic_extractor.alignment.backfill.completion_evidence import (
    BackfillCompletionProjection,
)
from xic_extractor.alignment.backfill.request_plan import (
    BackfillLogicalRequestMember,
    BackfillRequestIdentity,
    BackfillRequestItem,
    BackfillRequestPlan,
    BackfillValidationMode,
    BackfillWindowStrategy,
)
from xic_extractor.alignment.backfill.scope import (
    BackfillScope,
    backfill_request_planning_reason,
    backfill_scope_exclusion_reasons,
)
from xic_extractor.alignment.config import AlignmentConfig, BackfillXicBackend
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupDisposition,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
)
from xic_extractor.alignment.owner_group_delivery import OwnerGroupDeliveryFeatures
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.xic_models import XICRequest

if TYPE_CHECKING:
    from xic_extractor.alignment.backfill.conservative import (
        ConservativeBackfillCellDecision,
    )


BackfillPlanningState = Literal["planned", "not_planned"]
BackfillPlanningReason = Literal[
    "planned",
    "production_equivalent_excluded",
    "selected_family_excluded",
    "review_only",
    "insufficient_detected_samples",
    "raw_source_unavailable",
    "retained_local_owner",
    "retained_unresolved_candidates",
    "local_owner_not_supersedable",
    "direct_support_threshold_not_met",
    "backfill_search_completed_empty",
    "no_discovery_input",
]
BACKFILL_PLANNING_REASONS: tuple[BackfillPlanningReason, ...] = (
    "planned",
    "production_equivalent_excluded",
    "selected_family_excluded",
    "review_only",
    "insufficient_detected_samples",
    "raw_source_unavailable",
    "retained_local_owner",
    "retained_unresolved_candidates",
    "local_owner_not_supersedable",
    "direct_support_threshold_not_met",
    "backfill_search_completed_empty",
    "no_discovery_input",
)

BackfillCandidatePhase = Literal[
    "primary_query",
    "prefilter_query",
    "primary_fallback",
    "validation_query",
    "validation_unavailable",
    "validation_fallback",
]
BACKFILL_CANDIDATE_PHASES: tuple[BackfillCandidatePhase, ...] = (
    "primary_query",
    "prefilter_query",
    "primary_fallback",
    "validation_query",
    "validation_unavailable",
    "validation_fallback",
)
BackfillTerminalReason = Literal[
    "primary_unassessable",
    "validation_source_unavailable",
    "validation_unassessable",
]
BACKFILL_TERMINAL_REASONS: tuple[BackfillTerminalReason, ...] = (
    "primary_unassessable",
    "validation_source_unavailable",
    "validation_unassessable",
)

_POSITION_PREFIX = "xic.owner-backfill-coverage-position:"
_TERMINAL_PREFIX = "xic.owner-backfill-coverage-terminal:"
_EPOCH_PREFIX = "xic.owner-backfill-planning-epoch:"
_BATCH_PREFIX = "xic.owner-backfill-evaluation-coverage:"


@dataclass(frozen=True, slots=True)
class BackfillConservativeNoRequestProof:
    """Exact conservative decision retained across a no-request coverage lane."""

    proof_id: str
    processing_owners: ProcessingOwnerReferences
    group_identity: CrossSampleGroupIdentity
    sample: ResolvedCanonicalSampleIdentity
    decision: ConservativeBackfillCellDecision
    completion: BackfillCompletionProjection | None = None

    @property
    def variant(self) -> Literal["threshold_not_met", "completed_backfill_empty"]:
        return (
            "threshold_not_met"
            if self.completion is None
            else "completed_backfill_empty"
        )

    @property
    def planning_reason(self) -> BackfillPlanningReason:
        return (
            "direct_support_threshold_not_met"
            if self.completion is None
            else "backfill_search_completed_empty"
        )


def mint_backfill_conservative_no_request_proof(
    *,
    processing_owners: ProcessingOwnerReferences,
    group_identity: CrossSampleGroupIdentity,
    sample: ResolvedCanonicalSampleIdentity,
    decision: ConservativeBackfillCellDecision,
    completion: BackfillCompletionProjection | None = None,
) -> BackfillConservativeNoRequestProof:
    return BackfillConservativeNoRequestProof(
        proof_id=_typed_id(
            "xic.owner-backfill-conservative-no-request:",
            _conservative_no_request_basis(
                processing_owners=processing_owners,
                group_identity=group_identity,
                sample=sample,
                decision=decision,
                completion=completion,
            ),
        ),
        processing_owners=processing_owners,
        group_identity=group_identity,
        sample=sample,
        decision=decision,
        completion=completion,
    )


def _conservative_no_request_basis(
    *,
    processing_owners: ProcessingOwnerReferences,
    group_identity: CrossSampleGroupIdentity,
    sample: ResolvedCanonicalSampleIdentity,
    decision: ConservativeBackfillCellDecision,
    completion: BackfillCompletionProjection | None,
) -> Mapping[str, object]:
    facts = decision.group_facts
    basis: dict[str, object] = {
        "schema": "owner_backfill_conservative_no_request",
        "variant": "threshold_not_met"
        if completion is None
        else "completed_backfill_empty",
        "policy_version": decision.policy_version,
        "processing_request_id": processing_owners.request.processing_request_id,
        "processing_attempt_id": processing_owners.attempt.processing_attempt_id,
        "group_id": group_identity.group_hypothesis_id,
        "group_basis": group_identity.basis_bytes.hex(),
        "group_center_rt": facts.group_context.group.group_center_rt,
        "sample_id": sample.identity.sample_identity_id,
        "sample_snapshot_id": sample.snapshot.snapshot_id,
        "sample_snapshot_sha256": sample.snapshot.content_sha256,
        "source_cells": [
            cell.artifact.evidence_artifact_id for cell in facts.source_cells
        ],
        "sample_population": [
            (
                item.identity.sample_identity_id,
                item.snapshot.snapshot_id,
                item.snapshot.content_sha256,
            )
            for item in facts.sample_population
        ],
        "support_artifacts": [
            (
                item.assessment.artifact.evidence_artifact_id,
                item.hypothesis.hypothesis_id,
            )
            for item in facts.group_context.supports
        ],
        "target_cell_artifact": decision.target_cell.artifact.evidence_artifact_id,
        "assessment_artifact": (
            decision.target_cell.method_assessment.artifact.evidence_artifact_id
        ),
        "sample_count_n": facts.sample_count_n,
        "direct_support_count_s": facts.direct_support_count_s,
        "minimum_support_k": facts.minimum_support_k,
        "disposition": decision.disposition,
        "reason_codes": decision.reason_codes,
        "completion_artifact": None
        if completion is None
        else completion.artifact.evidence_artifact_id,
        "candidate_universe_artifact": (
            None
            if decision.candidate_universe is None
            else decision.candidate_universe.artifact.evidence_artifact_id
        ),
    }
    if facts.unsearched_sample_ids:
        basis["schema"] = (
            "owner_backfill_conservative_no_request_with_unsearched_samples"
        )
        basis["unsearched_sample_ids"] = facts.unsearched_sample_ids
    return basis


@dataclass(frozen=True, slots=True)
class BackfillEvaluationCoverageContext:
    processing_owners: ProcessingOwnerReferences
    input_features: OwnerGroupDeliveryFeatures
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    sample_order: tuple[str, ...]
    raw_sample_stems: frozenset[str]
    alignment_config: AlignmentConfig
    backend: BackfillXicBackend
    window_strategy: BackfillWindowStrategy
    superwindow_span_factor: int
    validation_mode: BackfillValidationMode
    result_semantics: str
    scope: BackfillScope
    selected_family_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class BackfillPlannedLogicalRequest:
    logical_request: BackfillLogicalRequestMember
    request_identity: BackfillRequestIdentity


@dataclass(frozen=True, slots=True)
class BackfillPlanningPosition:
    position_id: str
    group_identity: CrossSampleGroupIdentity
    sample: ResolvedCanonicalSampleIdentity
    planning_state: BackfillPlanningState
    planning_reason: BackfillPlanningReason
    primary_logical_requests: tuple[BackfillPlannedLogicalRequest, ...]
    conservative_no_request_proof: BackfillConservativeNoRequestProof | None = None


@dataclass(frozen=True, slots=True)
class BackfillEvaluationPlanningEpoch:
    epoch_id: str
    processing_owners: ProcessingOwnerReferences
    group_identities: tuple[CrossSampleGroupIdentity, ...]
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    raw_sample_stems: frozenset[str]
    alignment_config: AlignmentConfig
    backend: BackfillXicBackend
    window_strategy: BackfillWindowStrategy
    superwindow_span_factor: int
    validation_mode: BackfillValidationMode
    result_semantics: str
    scope: BackfillScope
    selected_family_ids: frozenset[str]
    positions: tuple[BackfillPlanningPosition, ...]


@dataclass(frozen=True, slots=True)
class BackfillAttemptTerminal:
    terminal_id: str
    position_id: str
    logical_request: BackfillLogicalRequestMember
    request_identity: BackfillRequestIdentity
    candidate_phase: BackfillCandidatePhase
    candidate_index: int
    trace_evaluated: bool
    selected_for_output: bool
    terminal_reason: BackfillTerminalReason | None
    physical_evaluation: PhysicalPeakEvaluation | None


@dataclass(frozen=True, slots=True)
class BackfillEvaluationCoverageBatch:
    coverage_id: str
    epoch: BackfillEvaluationPlanningEpoch
    terminals: tuple[BackfillAttemptTerminal, ...]


def mint_backfill_position_id(
    *,
    processing_request_id: str,
    group_hypothesis_id: str,
    sample_identity_id: str,
) -> str:
    return _typed_id(
        _POSITION_PREFIX,
        {
            "schema": "owner_backfill_coverage_position",
            "processing_request_id": _required_text(
                processing_request_id,
                "processing_request_id",
            ),
            "input_group_hypothesis_id": _required_text(
                group_hypothesis_id,
                "group_hypothesis_id",
            ),
            "canonical_sample_identity_id": _required_text(
                sample_identity_id,
                "sample_identity_id",
            ),
        },
    )


def mint_backfill_terminal_id(
    *,
    position_id: str,
    logical_request_id: str,
    request_identity_id: str,
    candidate_phase: BackfillCandidatePhase,
    candidate_index: int,
) -> str:
    return _typed_id(
        _TERMINAL_PREFIX,
        {
            "schema": "owner_backfill_coverage_terminal",
            "position_id": _required_text(position_id, "position_id"),
            "logical_request_id": _required_text(
                logical_request_id,
                "logical_request_id",
            ),
            "request_identity_id": _required_text(
                request_identity_id,
                "request_identity_id",
            ),
            "candidate_phase": candidate_phase,
            "candidate_index": candidate_index,
        },
    )


def mint_backfill_selection_subject_id(
    *,
    request_identity: BackfillRequestIdentity | None,
    group_hypothesis_id: str,
    sample_stem: str,
    request: XICRequest,
    candidate_index: int,
    candidate_phase: str,
) -> str:
    request_key = (
        request_identity.request_identity_id
        if request_identity is not None
        else "unsealed:" + _canonical_request_digest(request)
    )
    canonical = json.dumps(
        {
            "schema_version": "owner_backfill_selection_subject",
            "request_identity_id": request_key,
            "group_hypothesis_id": group_hypothesis_id,
            "sample_stem": sample_stem,
            "candidate_index": candidate_index,
            "candidate_phase": candidate_phase,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "owner-backfill-subject:" + hashlib.sha256(canonical).hexdigest()


def mint_backfill_planning_epoch(
    *,
    processing_owners: ProcessingOwnerReferences,
    group_identities: tuple[CrossSampleGroupIdentity, ...],
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
    backend: BackfillXicBackend,
    window_strategy: BackfillWindowStrategy,
    superwindow_span_factor: int,
    validation_mode: BackfillValidationMode,
    result_semantics: str,
    scope: BackfillScope,
    selected_family_ids: frozenset[str],
    positions: tuple[BackfillPlanningPosition, ...],
) -> BackfillEvaluationPlanningEpoch:
    request_id = processing_owners.request.processing_request_id
    return BackfillEvaluationPlanningEpoch(
        epoch_id=_typed_id(
            _EPOCH_PREFIX,
            _epoch_basis(
                processing_request_id=request_id,
                groups=group_identities,
                samples=samples,
                raw_sample_stems=raw_sample_stems,
                alignment_config=alignment_config,
                backend=backend,
                window_strategy=window_strategy,
                superwindow_span_factor=superwindow_span_factor,
                validation_mode=validation_mode,
                result_semantics=result_semantics,
                scope=scope,
                selected_family_ids=selected_family_ids,
                positions=positions,
            ),
        ),
        processing_owners=processing_owners,
        group_identities=group_identities,
        samples=samples,
        raw_sample_stems=raw_sample_stems,
        alignment_config=alignment_config,
        backend=backend,
        window_strategy=window_strategy,
        superwindow_span_factor=superwindow_span_factor,
        validation_mode=validation_mode,
        result_semantics=result_semantics,
        scope=scope,
        selected_family_ids=selected_family_ids,
        positions=positions,
    )


def build_backfill_planning_epoch(
    context: BackfillEvaluationCoverageContext,
    *,
    request_plan: BackfillRequestPlan,
) -> BackfillEvaluationPlanningEpoch:
    if not request_plan.exact_equivalence_available:
        raise ValueError("evaluation coverage requires exact request identity")
    group_identities = tuple(
        _group_identity_from_feature(
            feature,
            processing_request_id=(
                context.processing_owners.request.processing_request_id
            ),
        )
        for feature in context.input_features
    )
    exclusion_reasons = backfill_scope_exclusion_reasons(
        context.input_features,
        alignment_config=context.alignment_config,
        scope=context.scope,
        selected_family_ids=context.selected_family_ids,
    )
    positions: list[BackfillPlanningPosition] = []
    expected_logical_ids: set[str] = set()
    for feature, group_identity in zip(
        context.input_features,
        group_identities,
        strict=True,
    ):
        scope_reason = exclusion_reasons.get(id(feature))
        for sample in context.samples:
            reason: BackfillPlanningReason = (
                scope_reason
                if scope_reason is not None
                else backfill_request_planning_reason(
                    feature,
                    sample_stem=sample.lookup_token,
                    raw_sample_stems=context.raw_sample_stems,
                    alignment_config=context.alignment_config,
                )
            )
            logical_requests: tuple[BackfillPlannedLogicalRequest, ...] = ()
            if reason == "planned":
                logical_requests = tuple(
                    BackfillPlannedLogicalRequest(logical, identity)
                    for logical, identity in request_plan.logical_bindings_for_position(
                        sample_stem=sample.lookup_token,
                        group_hypothesis_id=group_identity.group_hypothesis_id,
                    )
                )
                expected_logical_ids.update(
                    item.logical_request.logical_request_id for item in logical_requests
                )
            positions.append(
                BackfillPlanningPosition(
                    position_id=mint_backfill_position_id(
                        processing_request_id=group_identity.processing_request_id,
                        group_hypothesis_id=group_identity.group_hypothesis_id,
                        sample_identity_id=sample.identity.sample_identity_id,
                    ),
                    group_identity=group_identity,
                    sample=sample,
                    planning_state=(
                        "planned" if reason == "planned" else "not_planned"
                    ),
                    planning_reason=reason,
                    primary_logical_requests=logical_requests,
                )
            )
    actual_logical_ids = {
        logical.logical_request_id
        for sample_stem in context.sample_order
        for logical, _identity in request_plan.logical_bindings_for_sample(sample_stem)
    }
    if actual_logical_ids != expected_logical_ids:
        raise ValueError("request plan is missing, extra, or rebound to coverage")
    return mint_backfill_planning_epoch(
        processing_owners=context.processing_owners,
        group_identities=group_identities,
        samples=context.samples,
        raw_sample_stems=context.raw_sample_stems,
        alignment_config=context.alignment_config,
        backend=context.backend,
        window_strategy=context.window_strategy,
        superwindow_span_factor=context.superwindow_span_factor,
        validation_mode=context.validation_mode,
        result_semantics=context.result_semantics,
        scope=context.scope,
        selected_family_ids=context.selected_family_ids,
        positions=tuple(positions),
    )


def mint_backfill_attempt_terminal(
    *,
    position_id: str,
    logical_request: BackfillLogicalRequestMember,
    request_identity: BackfillRequestIdentity,
    candidate_phase: BackfillCandidatePhase,
    candidate_index: int,
    trace_evaluated: bool,
    selected_for_output: bool,
    terminal_reason: BackfillTerminalReason | None,
    physical_evaluation: PhysicalPeakEvaluation | None,
) -> BackfillAttemptTerminal:
    return BackfillAttemptTerminal(
        terminal_id=mint_backfill_terminal_id(
            position_id=position_id,
            logical_request_id=logical_request.logical_request_id,
            request_identity_id=request_identity.request_identity_id,
            candidate_phase=candidate_phase,
            candidate_index=candidate_index,
        ),
        position_id=position_id,
        logical_request=logical_request,
        request_identity=request_identity,
        candidate_phase=candidate_phase,
        candidate_index=candidate_index,
        trace_evaluated=trace_evaluated,
        selected_for_output=selected_for_output,
        terminal_reason=terminal_reason,
        physical_evaluation=physical_evaluation,
    )


def build_backfill_evaluation_coverage(
    epoch: BackfillEvaluationPlanningEpoch,
    terminals: Sequence[BackfillAttemptTerminal],
) -> BackfillEvaluationCoverageBatch:
    """Join owner terminal streams without revalidating their upstream facts."""
    canonical = _canonical_terminals(epoch, tuple(terminals))
    _require_terminal_coverage(epoch, canonical)
    return BackfillEvaluationCoverageBatch(
        coverage_id=_typed_id(
            _BATCH_PREFIX,
            {
                "schema": "owner_backfill_evaluation_coverage",
                "epoch_id": epoch.epoch_id,
                "terminal_ids": [item.terminal_id for item in canonical],
                "terminal_evaluations": [
                    _terminal_evaluation_basis(item) for item in canonical
                ],
            },
        ),
        epoch=epoch,
        terminals=canonical,
    )


def _require_terminal_coverage(
    epoch: BackfillEvaluationPlanningEpoch,
    terminals: tuple[BackfillAttemptTerminal, ...],
) -> None:
    """Close request cardinality once, where owner terminal streams are joined."""
    expected_primary = Counter(
        (position.position_id, item.logical_request.logical_request_id)
        for position in epoch.positions
        for item in position.primary_logical_requests
    )
    actual_primary = Counter(
        (item.position_id, item.logical_request.logical_request_id)
        for item in terminals
        if item.candidate_phase
        in {"primary_query", "prefilter_query", "primary_fallback"}
    )
    if actual_primary != expected_primary:
        raise ValueError("coverage is missing, duplicates, or adds a primary terminal")
    validation_index_by_sample: Counter[str] = Counter()
    expected_validation: Counter[tuple[object, ...]] = Counter()
    actual_validation: Counter[tuple[object, ...]] = Counter()
    for item in terminals:
        signature = (item.position_id, *_terminal_request_signature(item))
        evaluation = item.physical_evaluation
        if (
            item.candidate_phase == "prefilter_query"
            and evaluation is not None
            and evaluation.state == "available"
            and evaluation.facts is not None
        ):
            sample = item.logical_request.item.sample_stem
            index = validation_index_by_sample[sample]
            validation_index_by_sample[sample] += 1
            expected_validation[(*signature, index)] += 1
        elif item.candidate_phase in {
            "validation_query",
            "validation_unavailable",
            "validation_fallback",
        }:
            actual_validation[(*signature, item.logical_request.logical_index)] += 1
    if actual_validation != expected_validation:
        raise ValueError("coverage is missing or adds a validation terminal")


def _canonical_terminals(
    epoch: BackfillEvaluationPlanningEpoch,
    terminals: tuple[BackfillAttemptTerminal, ...],
) -> tuple[BackfillAttemptTerminal, ...]:
    terminals = tuple(_canonical_terminal_payload(epoch, item) for item in terminals)
    position_rank = {
        position.position_id: index for index, position in enumerate(epoch.positions)
    }
    phase_rank = {phase: index for index, phase in enumerate(BACKFILL_CANDIDATE_PHASES)}
    return tuple(
        sorted(
            terminals,
            key=lambda item: (
                position_rank.get(item.position_id, len(position_rank)),
                phase_rank[item.candidate_phase],
                item.candidate_index,
                item.logical_request.logical_request_id,
                item.request_identity.request_identity_id,
            ),
        )
    )


def _canonical_terminal_payload(
    epoch: BackfillEvaluationPlanningEpoch,
    terminal: BackfillAttemptTerminal,
) -> BackfillAttemptTerminal:
    position = next(
        (item for item in epoch.positions if item.position_id == terminal.position_id),
        None,
    )
    if position is None:
        return terminal
    if terminal.candidate_phase in {
        "primary_query",
        "prefilter_query",
        "primary_fallback",
    }:
        planned = next(
            (
                item
                for item in position.primary_logical_requests
                if item.logical_request.logical_request_id
                == terminal.logical_request.logical_request_id
            ),
            None,
        )
        if planned is None or (
            planned.request_identity.request_identity_id
            != terminal.request_identity.request_identity_id
        ):
            return terminal
        return replace(
            terminal,
            logical_request=planned.logical_request,
            request_identity=planned.request_identity,
        )
    matching_primary = tuple(
        item
        for item in position.primary_logical_requests
        if _planned_request_signature(item) == _terminal_request_signature(terminal)
    )
    if not matching_primary:
        return terminal
    source_item = terminal.logical_request.item
    canonical_item = BackfillRequestItem(
        matching_primary[0].logical_request.item.feature,
        source_item.sample_stem,
        source_item.request,
        source_item.preferred_rt,
    )
    return replace(
        terminal,
        logical_request=replace(terminal.logical_request, item=canonical_item),
    )


def _planned_request_signature(
    planned: BackfillPlannedLogicalRequest,
) -> tuple[object, ...]:
    logical = planned.logical_request
    return (
        logical.group_hypothesis_id,
        logical.item.sample_stem,
        logical.item.request,
        logical.item.preferred_rt,
    )


def _terminal_request_signature(
    terminal: BackfillAttemptTerminal,
) -> tuple[object, ...]:
    logical = terminal.logical_request
    return (
        logical.group_hypothesis_id,
        logical.item.sample_stem,
        logical.item.request,
        logical.item.preferred_rt,
    )


def _epoch_basis(
    *,
    processing_request_id: str,
    groups: tuple[CrossSampleGroupIdentity, ...],
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
    backend: BackfillXicBackend,
    window_strategy: BackfillWindowStrategy,
    superwindow_span_factor: int,
    validation_mode: BackfillValidationMode,
    result_semantics: str,
    scope: BackfillScope,
    selected_family_ids: frozenset[str],
    positions: tuple[BackfillPlanningPosition, ...],
) -> Mapping[str, object]:
    return {
        "schema": "owner_backfill_planning_epoch",
        "processing_request_id": processing_request_id,
        "groups": [
            {
                "group_hypothesis_id": item.group_hypothesis_id,
                "basis_bytes_hex": item.basis_bytes.hex(),
                "disposition": item.disposition,
                "member_subject_ids": [
                    member.member_subject_id for member in item.member_subjects
                ],
            }
            for item in groups
        ],
        "canonical_samples": [
            {
                "sample_identity_id": item.identity.sample_identity_id,
                "snapshot_id": item.snapshot.snapshot_id,
                "snapshot_content_sha256": item.snapshot.content_sha256,
                "lookup_token": item.lookup_token,
            }
            for item in samples
        ],
        "raw_sample_stems": sorted(raw_sample_stems),
        "alignment_config": asdict(alignment_config),
        "request_semantics": {
            "backend": backend,
            "window_strategy": window_strategy,
            "superwindow_span_factor": superwindow_span_factor,
            "validation_mode": validation_mode,
            "result_semantics": result_semantics,
        },
        "scope": scope,
        "selected_family_ids": sorted(selected_family_ids),
        "positions": [
            {
                "position_id": item.position_id,
                "planning_state": item.planning_state,
                "planning_reason": item.planning_reason,
                "logical_request_ids": [
                    request.logical_request.logical_request_id
                    for request in item.primary_logical_requests
                ],
                "request_identity_ids": [
                    request.request_identity.request_identity_id
                    for request in item.primary_logical_requests
                ],
                "conservative_no_request_proof_id": (
                    item.conservative_no_request_proof.proof_id
                    if item.conservative_no_request_proof is not None
                    else None
                ),
            }
            for item in positions
        ],
    }


def _terminal_evaluation_basis(
    terminal: BackfillAttemptTerminal,
) -> Mapping[str, object]:
    evaluation = terminal.physical_evaluation
    return {
        "terminal_id": terminal.terminal_id,
        "trace_evaluated": terminal.trace_evaluated,
        "selected_for_output": terminal.selected_for_output,
        "terminal_reason": terminal.terminal_reason,
        "physical_state": evaluation.state if evaluation is not None else None,
        "physical_reason": evaluation.reason if evaluation is not None else None,
        "prepared_signal_id": (
            evaluation.prepared.prepared_signal_id if evaluation is not None else None
        ),
        "physical_question_id": (
            evaluation.question.physical_question_id if evaluation is not None else None
        ),
        "physical_answer_id": (
            evaluation.answer.physical_answer_id if evaluation is not None else None
        ),
        "physical_fact_content_id": (
            evaluation.facts.fact_content_id
            if evaluation is not None and evaluation.facts is not None
            else None
        ),
    }


def _typed_id(prefix: str, basis: Mapping[str, object]) -> str:
    payload = json.dumps(
        basis,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return prefix + hashlib.sha256(payload).hexdigest()


def _canonical_request_digest(request: XICRequest) -> str:
    canonical = json.dumps(
        {
            "mz": float(request.mz).hex(),
            "rt_min": float(request.rt_min).hex(),
            "rt_max": float(request.rt_max).hex(),
            "ppm_tol": float(request.ppm_tol).hex(),
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _group_identity_from_feature(
    feature: object,
    *,
    processing_request_id: str,
) -> CrossSampleGroupIdentity:
    group_hypothesis_id = _required_text(
        getattr(feature, "group_hypothesis_id", None),
        "feature.group_hypothesis_id",
    )
    feature_request_id = _required_text(
        getattr(feature, "processing_request_id", None),
        "feature.processing_request_id",
    )
    if feature_request_id != processing_request_id:
        raise ValueError("input group crossed the processing request")
    member_subjects = getattr(feature, "member_subjects", None)
    if not isinstance(member_subjects, tuple) or not member_subjects:
        raise ValueError("input group is missing canonical member subjects")
    basis_bytes = getattr(feature, "identity_basis_bytes", None)
    if not isinstance(basis_bytes, bytes) or not basis_bytes:
        raise ValueError("input group is missing canonical identity basis")
    disposition = getattr(feature, "disposition", None)
    return CrossSampleGroupIdentity(
        group_hypothesis_id=group_hypothesis_id,
        processing_request_id=feature_request_id,
        member_subjects=cast(
            tuple[CrossSampleGroupMemberSubject, ...],
            member_subjects,
        ),
        basis_bytes=basis_bytes,
        disposition=cast(CrossSampleGroupDisposition, disposition),
    )


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty normalized text")
    return value


__all__ = [
    "BACKFILL_CANDIDATE_PHASES",
    "BACKFILL_PLANNING_REASONS",
    "BACKFILL_TERMINAL_REASONS",
    "BackfillAttemptTerminal",
    "BackfillCandidatePhase",
    "BackfillEvaluationCoverageBatch",
    "BackfillEvaluationCoverageContext",
    "BackfillEvaluationPlanningEpoch",
    "BackfillPlannedLogicalRequest",
    "BackfillPlanningPosition",
    "BackfillPlanningReason",
    "BackfillPlanningState",
    "BackfillTerminalReason",
    "build_backfill_evaluation_coverage",
    "build_backfill_planning_epoch",
    "mint_backfill_attempt_terminal",
    "mint_backfill_planning_epoch",
    "mint_backfill_position_id",
    "mint_backfill_selection_subject_id",
    "mint_backfill_terminal_id",
]
