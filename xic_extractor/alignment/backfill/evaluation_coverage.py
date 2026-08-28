from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Literal, cast

from xic_extractor.alignment.backfill_scope import (
    BackfillScope,
    backfill_request_planning_reason,
    backfill_scope_exclusion_reasons,
    select_backfill_features,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupDisposition,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.ms1_index_source import OwnerBackfillXicBackend
from xic_extractor.alignment.owner_backfill_request_plan import (
    OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
    OwnerBackfillLogicalRequestMember,
    OwnerBackfillRequestIdentity,
    OwnerBackfillRequestItem,
    OwnerBackfillRequestPlan,
    OwnerBackfillValidationMode,
    OwnerBackfillWindowStrategy,
    mint_owner_backfill_logical_request_member,
)
from xic_extractor.alignment.owner_group_delivery import OwnerGroupDeliveryFeatures
from xic_extractor.peak_detection.evidence_facts import (
    PhysicalPeakEvaluation,
    validate_bounded_physical_peak_evaluation,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.xic_models import XICRequest

OwnerBackfillPlanningState = Literal["planned", "not_planned"]
OwnerBackfillPlanningReason = Literal[
    "planned",
    "production_equivalent_excluded",
    "selected_family_excluded",
    "review_only",
    "insufficient_detected_samples",
    "raw_source_unavailable",
    "retained_local_owner",
    "local_owner_not_supersedable",
]
OWNER_BACKFILL_PLANNING_REASONS: tuple[OwnerBackfillPlanningReason, ...] = (
    "planned",
    "production_equivalent_excluded",
    "selected_family_excluded",
    "review_only",
    "insufficient_detected_samples",
    "raw_source_unavailable",
    "retained_local_owner",
    "local_owner_not_supersedable",
)

OwnerBackfillCandidatePhase = Literal[
    "primary_query",
    "prefilter_query",
    "primary_fallback",
    "validation_query",
    "validation_unavailable",
    "validation_fallback",
]
OWNER_BACKFILL_CANDIDATE_PHASES: tuple[OwnerBackfillCandidatePhase, ...] = (
    "primary_query",
    "prefilter_query",
    "primary_fallback",
    "validation_query",
    "validation_unavailable",
    "validation_fallback",
)
OwnerBackfillTerminalReason = Literal[
    "primary_unassessable",
    "validation_source_unavailable",
    "validation_unassessable",
]
OWNER_BACKFILL_TERMINAL_REASONS: tuple[OwnerBackfillTerminalReason, ...] = (
    "primary_unassessable",
    "validation_source_unavailable",
    "validation_unassessable",
)

_POSITION_PREFIX = "xic.owner-backfill-coverage-position.v1:"
_TERMINAL_PREFIX = "xic.owner-backfill-coverage-terminal.v1:"
_EPOCH_PREFIX = "xic.owner-backfill-planning-epoch.v1:"
_BATCH_PREFIX = "xic.owner-backfill-evaluation-coverage.v1:"


@dataclass(frozen=True, slots=True)
class OwnerBackfillEvaluationCoverageContext:
    processing_owners: ProcessingOwnerReferences
    input_features: OwnerGroupDeliveryFeatures
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    sample_order: tuple[str, ...]
    raw_sample_stems: frozenset[str]
    alignment_config: AlignmentConfig
    backend: OwnerBackfillXicBackend
    window_strategy: OwnerBackfillWindowStrategy
    superwindow_span_factor: int
    validation_mode: OwnerBackfillValidationMode
    result_semantics: str
    scope: BackfillScope
    selected_family_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.processing_owners, ProcessingOwnerReferences):
            raise TypeError("processing_owners must be owner-backed")
        if not isinstance(self.input_features, tuple):
            raise TypeError("input_features must be a canonical tuple")
        if not isinstance(self.samples, tuple) or not self.samples:
            raise ValueError("coverage context requires canonical samples")
        if self.samples != self.processing_owners.sample_resolutions:
            raise ValueError("coverage context crossed the processing sample snapshot")
        if self.sample_order != tuple(sample.lookup_token for sample in self.samples):
            raise ValueError("coverage context sample order crossed owner resolutions")
        if not isinstance(self.raw_sample_stems, frozenset):
            raise TypeError("raw_sample_stems must be a frozenset")
        if not isinstance(self.alignment_config, AlignmentConfig):
            raise TypeError("alignment_config must be owner-resolved")
        _validate_request_semantics(
            backend=self.backend,
            window_strategy=self.window_strategy,
            superwindow_span_factor=self.superwindow_span_factor,
            validation_mode=self.validation_mode,
            result_semantics=self.result_semantics,
        )
        if self.scope not in {
            "full-audit",
            "production-equivalent",
            "selected-families",
        }:
            raise ValueError("coverage context Backfill scope is not closed")
        if not isinstance(self.selected_family_ids, frozenset):
            raise TypeError("selected_family_ids must be a frozenset")


@dataclass(frozen=True, slots=True)
class OwnerBackfillPlannedLogicalRequest:
    logical_request: OwnerBackfillLogicalRequestMember
    request_identity: OwnerBackfillRequestIdentity

    def __post_init__(self) -> None:
        if not isinstance(
            self.logical_request,
            OwnerBackfillLogicalRequestMember,
        ):
            raise TypeError("logical_request must be owner-minted")
        if not isinstance(self.request_identity, OwnerBackfillRequestIdentity):
            raise TypeError("request_identity must be owner-minted")
        if self.logical_request.item.sample_stem != self.request_identity.sample_stem:
            raise ValueError("logical request crosses canonical samples")
        if self.logical_request.item.request != self.request_identity.request:
            raise ValueError("logical request crosses canonical extraction semantics")


@dataclass(frozen=True, slots=True)
class OwnerBackfillPlanningPosition:
    position_id: str
    group_identity: CrossSampleGroupIdentity
    sample: ResolvedCanonicalSampleIdentity
    planning_state: OwnerBackfillPlanningState
    planning_reason: OwnerBackfillPlanningReason
    primary_logical_requests: tuple[OwnerBackfillPlannedLogicalRequest, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.group_identity, CrossSampleGroupIdentity):
            raise TypeError("group_identity must be CrossSample-owned")
        if not isinstance(self.sample, ResolvedCanonicalSampleIdentity):
            raise TypeError("sample must be owner-resolved")
        expected = mint_owner_backfill_position_id(
            processing_request_id=self.group_identity.processing_request_id,
            group_hypothesis_id=self.group_identity.group_hypothesis_id,
            sample_identity_id=self.sample.identity.sample_identity_id,
        )
        if self.position_id != expected:
            raise ValueError("position_id mismatches exact group/sample identity")
        if self.planning_state not in {"planned", "not_planned"}:
            raise ValueError("planning_state is not closed")
        if self.planning_reason not in OWNER_BACKFILL_PLANNING_REASONS:
            raise ValueError("planning_reason is not closed")
        if not isinstance(self.primary_logical_requests, tuple):
            raise TypeError("primary_logical_requests must be a tuple")
        if any(
            not isinstance(item, OwnerBackfillPlannedLogicalRequest)
            for item in self.primary_logical_requests
        ):
            raise TypeError("primary logical request is not owner-minted")
        if self.planning_state == "planned":
            if self.planning_reason != "planned":
                raise ValueError("planned position requires the planned reason")
            if not self.primary_logical_requests:
                raise ValueError("planned position requires primary logical requests")
        elif self.planning_reason == "planned" or self.primary_logical_requests:
            raise ValueError("not-planned position cannot retain logical requests")
        logical_ids: set[str] = set()
        for item in self.primary_logical_requests:
            logical = item.logical_request
            if logical.logical_request_id in logical_ids:
                raise ValueError("position duplicates a primary logical request")
            logical_ids.add(logical.logical_request_id)
            if logical.group_hypothesis_id != self.group_identity.group_hypothesis_id:
                raise ValueError("logical request crosses input group identity")
            if logical.item.sample_stem != self.sample.lookup_token:
                raise ValueError("logical request crosses canonical sample lookup")


@dataclass(frozen=True, slots=True)
class OwnerBackfillEvaluationPlanningEpoch:
    epoch_id: str
    processing_owners: ProcessingOwnerReferences
    group_identities: tuple[CrossSampleGroupIdentity, ...]
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    raw_sample_stems: frozenset[str]
    alignment_config: AlignmentConfig
    backend: OwnerBackfillXicBackend
    window_strategy: OwnerBackfillWindowStrategy
    superwindow_span_factor: int
    validation_mode: OwnerBackfillValidationMode
    result_semantics: str
    scope: BackfillScope
    selected_family_ids: frozenset[str]
    positions: tuple[OwnerBackfillPlanningPosition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.processing_owners, ProcessingOwnerReferences):
            raise TypeError("processing_owners must be owner-backed")
        groups = validate_cross_sample_group_population(self.group_identities)
        if groups != self.group_identities:
            raise ValueError("group identities are not in canonical producer order")
        if not isinstance(self.samples, tuple) or not self.samples:
            raise ValueError("planning epoch requires canonical samples")
        if any(
            not isinstance(sample, ResolvedCanonicalSampleIdentity)
            for sample in self.samples
        ):
            raise TypeError("planning epoch samples must be owner-resolved")
        if self.samples != self.processing_owners.sample_resolutions:
            raise ValueError("planning epoch crossed the processing sample snapshot")
        if not isinstance(self.raw_sample_stems, frozenset):
            raise TypeError("planning epoch RAW sample stems must be a frozenset")
        if not isinstance(self.alignment_config, AlignmentConfig):
            raise TypeError("planning epoch config must be owner-resolved")
        _validate_request_semantics(
            backend=self.backend,
            window_strategy=self.window_strategy,
            superwindow_span_factor=self.superwindow_span_factor,
            validation_mode=self.validation_mode,
            result_semantics=self.result_semantics,
        )
        if self.scope not in {
            "full-audit",
            "production-equivalent",
            "selected-families",
        }:
            raise ValueError("planning epoch Backfill scope is not closed")
        if not isinstance(self.selected_family_ids, frozenset):
            raise TypeError("planning epoch selected families must be a frozenset")
        request_id = self.processing_owners.request.processing_request_id
        if any(group.processing_request_id != request_id for group in groups):
            raise ValueError("planning epoch crossed the processing request")
        expected_positions = tuple(
            (group.group_hypothesis_id, sample.identity.sample_identity_id)
            for group in groups
            for sample in self.samples
        )
        actual_positions = tuple(
            (
                position.group_identity.group_hypothesis_id,
                position.sample.identity.sample_identity_id,
            )
            for position in self.positions
        )
        if actual_positions != expected_positions:
            raise ValueError("planning epoch does not close the Cartesian population")
        expected_id = _typed_id(
            _EPOCH_PREFIX,
            _epoch_basis(
                processing_request_id=request_id,
                groups=groups,
                samples=self.samples,
                raw_sample_stems=self.raw_sample_stems,
                alignment_config=self.alignment_config,
                backend=self.backend,
                window_strategy=self.window_strategy,
                superwindow_span_factor=self.superwindow_span_factor,
                validation_mode=self.validation_mode,
                result_semantics=self.result_semantics,
                scope=self.scope,
                selected_family_ids=self.selected_family_ids,
                positions=self.positions,
            ),
        )
        if self.epoch_id != expected_id:
            raise ValueError("epoch_id mismatches complete planning semantics")


@dataclass(frozen=True, slots=True)
class OwnerBackfillAttemptTerminal:
    terminal_id: str
    position_id: str
    logical_request: OwnerBackfillLogicalRequestMember
    request_identity: OwnerBackfillRequestIdentity
    candidate_phase: OwnerBackfillCandidatePhase
    candidate_index: int
    trace_evaluated: bool
    selected_for_output: bool
    terminal_reason: OwnerBackfillTerminalReason | None
    physical_evaluation: PhysicalPeakEvaluation | None

    def __post_init__(self) -> None:
        if not isinstance(self.logical_request, OwnerBackfillLogicalRequestMember):
            raise TypeError("terminal logical_request must be owner-minted")
        if not isinstance(self.request_identity, OwnerBackfillRequestIdentity):
            raise TypeError("terminal request_identity must be owner-minted")
        if self.logical_request.item.sample_stem != self.request_identity.sample_stem:
            raise ValueError("terminal crosses canonical samples")
        if self.logical_request.item.request != self.request_identity.request:
            raise ValueError("terminal crosses canonical extraction semantics")
        if self.candidate_phase not in OWNER_BACKFILL_CANDIDATE_PHASES:
            raise ValueError("terminal candidate phase is not closed")
        if (
            isinstance(self.candidate_index, bool)
            or not isinstance(self.candidate_index, int)
            or self.candidate_index < 1
        ):
            raise ValueError("terminal candidate_index must be positive")
        if not isinstance(self.trace_evaluated, bool):
            raise TypeError("terminal trace_evaluated must be boolean")
        if not isinstance(self.selected_for_output, bool):
            raise TypeError("terminal selected_for_output must be boolean")
        query_phase = self.candidate_phase in {
            "primary_query",
            "prefilter_query",
            "validation_query",
        }
        expected_reason: OwnerBackfillTerminalReason | None = None
        if self.candidate_phase == "primary_fallback":
            expected_reason = "primary_unassessable"
        elif self.candidate_phase == "validation_unavailable":
            expected_reason = "validation_source_unavailable"
        elif self.candidate_phase == "validation_fallback":
            expected_reason = "validation_unassessable"
        if query_phase:
            if not self.trace_evaluated:
                raise ValueError("query terminal must retain an evaluated trace")
            if self.terminal_reason is not None:
                raise ValueError("query terminal cannot carry a fallback reason")
            if not isinstance(self.physical_evaluation, PhysicalPeakEvaluation):
                raise ValueError("query terminal requires physical evaluation facts")
            validate_bounded_physical_peak_evaluation(self.physical_evaluation)
            expected_subject_id = mint_owner_backfill_selection_subject_id(
                request_identity=self.request_identity,
                group_hypothesis_id=self.logical_request.group_hypothesis_id,
                sample_stem=self.logical_request.item.sample_stem,
                request=self.logical_request.item.request,
                candidate_index=self.candidate_index,
                candidate_phase=self.candidate_phase,
            )
            if (
                self.physical_evaluation.question.selection_subject_id
                != expected_subject_id
            ):
                raise ValueError("terminal crossed its physical evaluation subject")
        else:
            if self.trace_evaluated or self.physical_evaluation is not None:
                raise ValueError("fallback terminal cannot claim physical evaluation")
            if self.terminal_reason != expected_reason:
                raise ValueError("fallback terminal reason mismatches its phase")
        expected = mint_owner_backfill_terminal_id(
            position_id=self.position_id,
            logical_request_id=self.logical_request.logical_request_id,
            request_identity_id=self.request_identity.request_identity_id,
            candidate_phase=self.candidate_phase,
            candidate_index=self.candidate_index,
        )
        if self.terminal_id != expected:
            raise ValueError("terminal_id mismatches exact attempt identity")


@dataclass(frozen=True, slots=True)
class OwnerBackfillEvaluationCoverageBatch:
    coverage_id: str
    epoch: OwnerBackfillEvaluationPlanningEpoch
    terminals: tuple[OwnerBackfillAttemptTerminal, ...]

    def __post_init__(self) -> None:
        _validate_batch(self.epoch, self.terminals)
        expected = _typed_id(
            _BATCH_PREFIX,
            {
                "schema": "owner_backfill_evaluation_coverage_v1",
                "epoch_id": self.epoch.epoch_id,
                "terminal_ids": [item.terminal_id for item in self.terminals],
                "terminal_evaluations": [
                    _terminal_evaluation_basis(item) for item in self.terminals
                ],
            },
        )
        if self.coverage_id != expected:
            raise ValueError("coverage_id mismatches complete evaluation coverage")


def mint_owner_backfill_position_id(
    *,
    processing_request_id: str,
    group_hypothesis_id: str,
    sample_identity_id: str,
) -> str:
    return _typed_id(
        _POSITION_PREFIX,
        {
            "schema": "owner_backfill_coverage_position_v1",
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


def mint_owner_backfill_terminal_id(
    *,
    position_id: str,
    logical_request_id: str,
    request_identity_id: str,
    candidate_phase: OwnerBackfillCandidatePhase,
    candidate_index: int,
) -> str:
    return _typed_id(
        _TERMINAL_PREFIX,
        {
            "schema": "owner_backfill_coverage_terminal_v1",
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


def mint_owner_backfill_selection_subject_id(
    *,
    request_identity: OwnerBackfillRequestIdentity | None,
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
            "schema_version": "owner_backfill_selection_subject_v1",
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
    return "owner-backfill-subject.v1:" + hashlib.sha256(canonical).hexdigest()


def mint_owner_backfill_planning_epoch(
    *,
    processing_owners: ProcessingOwnerReferences,
    group_identities: tuple[CrossSampleGroupIdentity, ...],
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
    backend: OwnerBackfillXicBackend,
    window_strategy: OwnerBackfillWindowStrategy,
    superwindow_span_factor: int,
    validation_mode: OwnerBackfillValidationMode,
    result_semantics: str,
    scope: BackfillScope,
    selected_family_ids: frozenset[str],
    positions: tuple[OwnerBackfillPlanningPosition, ...],
) -> OwnerBackfillEvaluationPlanningEpoch:
    request_id = processing_owners.request.processing_request_id
    return OwnerBackfillEvaluationPlanningEpoch(
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


def build_owner_backfill_planning_epoch(
    context: OwnerBackfillEvaluationCoverageContext,
    *,
    selected_features: tuple[object, ...],
    request_plan: OwnerBackfillRequestPlan,
) -> OwnerBackfillEvaluationPlanningEpoch:
    if not isinstance(context, OwnerBackfillEvaluationCoverageContext):
        raise TypeError("coverage context must be owner-closed")
    if not isinstance(request_plan, OwnerBackfillRequestPlan):
        raise TypeError("request_plan must be Owner Backfill-minted")
    if not request_plan.exact_equivalence_available:
        raise ValueError("evaluation coverage requires exact request identity")
    expected_selection = select_backfill_features(
        context.input_features,
        sample_order=context.sample_order,
        raw_sample_stems=context.raw_sample_stems,
        alignment_config=context.alignment_config,
        scope=context.scope,
        selected_family_ids=context.selected_family_ids,
    )
    if expected_selection.features != selected_features:
        raise ValueError("selected Backfill features crossed the owner scope")
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
    positions: list[OwnerBackfillPlanningPosition] = []
    expected_logical_ids: set[str] = set()
    for feature, group_identity in zip(
        context.input_features,
        group_identities,
        strict=True,
    ):
        scope_reason = exclusion_reasons.get(id(feature))
        for sample in context.samples:
            reason: OwnerBackfillPlanningReason = (
                scope_reason
                if scope_reason is not None
                else backfill_request_planning_reason(
                    feature,
                    sample_stem=sample.lookup_token,
                    raw_sample_stems=context.raw_sample_stems,
                    alignment_config=context.alignment_config,
                )
            )
            logical_requests: tuple[OwnerBackfillPlannedLogicalRequest, ...] = ()
            if reason == "planned":
                logical_requests = tuple(
                    OwnerBackfillPlannedLogicalRequest(logical, identity)
                    for logical, identity in request_plan.logical_bindings_for_position(
                        sample_stem=sample.lookup_token,
                        group_hypothesis_id=group_identity.group_hypothesis_id,
                    )
                )
                expected_logical_ids.update(
                    item.logical_request.logical_request_id for item in logical_requests
                )
            positions.append(
                OwnerBackfillPlanningPosition(
                    position_id=mint_owner_backfill_position_id(
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
    return mint_owner_backfill_planning_epoch(
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


def mint_owner_backfill_attempt_terminal(
    *,
    position_id: str,
    logical_request: OwnerBackfillLogicalRequestMember,
    request_identity: OwnerBackfillRequestIdentity,
    candidate_phase: OwnerBackfillCandidatePhase,
    candidate_index: int,
    trace_evaluated: bool,
    selected_for_output: bool,
    terminal_reason: OwnerBackfillTerminalReason | None,
    physical_evaluation: PhysicalPeakEvaluation | None,
) -> OwnerBackfillAttemptTerminal:
    return OwnerBackfillAttemptTerminal(
        terminal_id=mint_owner_backfill_terminal_id(
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


def build_owner_backfill_evaluation_coverage(
    epoch: OwnerBackfillEvaluationPlanningEpoch,
    terminals: Sequence[OwnerBackfillAttemptTerminal],
) -> OwnerBackfillEvaluationCoverageBatch:
    canonical = _canonical_terminals(epoch, tuple(terminals))
    return OwnerBackfillEvaluationCoverageBatch(
        coverage_id=_typed_id(
            _BATCH_PREFIX,
            {
                "schema": "owner_backfill_evaluation_coverage_v1",
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


def validate_owner_backfill_evaluation_coverage(
    batch: OwnerBackfillEvaluationCoverageBatch,
) -> None:
    if not isinstance(batch, OwnerBackfillEvaluationCoverageBatch):
        raise TypeError("batch must be Owner Backfill evaluation coverage")
    OwnerBackfillEvaluationCoverageBatch(
        coverage_id=batch.coverage_id,
        epoch=batch.epoch,
        terminals=batch.terminals,
    )


def _validate_batch(
    epoch: OwnerBackfillEvaluationPlanningEpoch,
    terminals: tuple[OwnerBackfillAttemptTerminal, ...],
) -> None:
    if not isinstance(epoch, OwnerBackfillEvaluationPlanningEpoch):
        raise TypeError("coverage requires an owner planning epoch")
    if not isinstance(terminals, tuple) or any(
        not isinstance(item, OwnerBackfillAttemptTerminal) for item in terminals
    ):
        raise TypeError("coverage terminals must be a typed tuple")
    if terminals != _canonical_terminals(epoch, terminals):
        raise ValueError("coverage terminals are not in canonical producer order")
    position_by_id = {position.position_id: position for position in epoch.positions}
    if len(position_by_id) != len(epoch.positions):
        raise ValueError("coverage epoch duplicates a position")
    terminal_ids: set[str] = set()
    primary_by_position: dict[str, set[str]] = {}
    prefilter_validation_requirements: list[tuple[object, ...]] = []
    validation_index_by_sample: Counter[str] = Counter()
    validation_terminals: list[tuple[object, ...]] = []
    for terminal in terminals:
        if terminal.terminal_id in terminal_ids:
            raise ValueError("coverage duplicates an attempt terminal")
        terminal_ids.add(terminal.terminal_id)
        position = position_by_id.get(terminal.position_id)
        if position is None:
            raise ValueError("coverage contains an extra or rebound position")
        logical = terminal.logical_request
        if logical.group_hypothesis_id != position.group_identity.group_hypothesis_id:
            raise ValueError("terminal crosses its input group position")
        if logical.item.sample_stem != position.sample.lookup_token:
            raise ValueError("terminal crosses its canonical sample position")
        if position.planning_state != "planned":
            raise ValueError("not-planned position retained an attempt terminal")
        if terminal.candidate_phase in {
            "primary_query",
            "prefilter_query",
            "primary_fallback",
        }:
            primary_ids = primary_by_position.setdefault(position.position_id, set())
            if logical.logical_request_id in primary_ids:
                raise ValueError("logical request has two primary terminals")
            primary_ids.add(logical.logical_request_id)
            expected_primary_bindings = {
                item.logical_request.logical_request_id: (
                    item.request_identity.request_identity_id
                )
                for item in position.primary_logical_requests
            }
            if expected_primary_bindings.get(logical.logical_request_id) != (
                terminal.request_identity.request_identity_id
            ):
                raise ValueError("primary terminal is missing, extra, or rebound")
            evaluation = terminal.physical_evaluation
            if (
                terminal.candidate_phase == "prefilter_query"
                and evaluation is not None
                and evaluation.state == "available"
                and evaluation.facts is not None
            ):
                validation_index = validation_index_by_sample[logical.item.sample_stem]
                validation_index_by_sample[logical.item.sample_stem] += 1
                validation_member = mint_owner_backfill_logical_request_member(
                    logical_index=validation_index,
                    group_hypothesis_id=logical.group_hypothesis_id,
                    item=logical.item,
                )
                prefilter_validation_requirements.append(
                    (
                        *_terminal_request_signature(terminal),
                        validation_member.logical_index,
                        validation_member.logical_request_id,
                    )
                )
        else:
            matching_primary = tuple(
                item
                for item in position.primary_logical_requests
                if _planned_request_signature(item)
                == _terminal_request_signature(terminal)
            )
            if not matching_primary:
                raise ValueError(
                    "validation terminal is not bound to a planned request"
                )
            _validate_validation_request_identity(
                terminal.request_identity,
                primary_identity=matching_primary[0].request_identity,
            )
            validation_terminals.append(
                (
                    *_terminal_request_signature(terminal),
                    logical.logical_index,
                    logical.logical_request_id,
                )
            )
    for position in epoch.positions:
        actual = primary_by_position.get(position.position_id, set())
        expected_primary_ids = {
            item.logical_request.logical_request_id
            for item in position.primary_logical_requests
        }
        if actual != expected_primary_ids:
            raise ValueError("coverage is missing or adds a primary terminal")
    if Counter(validation_terminals) != Counter(prefilter_validation_requirements):
        raise ValueError("coverage is missing or adds a validation terminal")


def _canonical_terminals(
    epoch: OwnerBackfillEvaluationPlanningEpoch,
    terminals: tuple[OwnerBackfillAttemptTerminal, ...],
) -> tuple[OwnerBackfillAttemptTerminal, ...]:
    terminals = tuple(_canonical_terminal_payload(epoch, item) for item in terminals)
    position_rank = {
        position.position_id: index for index, position in enumerate(epoch.positions)
    }
    phase_rank = {
        phase: index for index, phase in enumerate(OWNER_BACKFILL_CANDIDATE_PHASES)
    }
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
    epoch: OwnerBackfillEvaluationPlanningEpoch,
    terminal: OwnerBackfillAttemptTerminal,
) -> OwnerBackfillAttemptTerminal:
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
    canonical_item = OwnerBackfillRequestItem(
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
    planned: OwnerBackfillPlannedLogicalRequest,
) -> tuple[object, ...]:
    logical = planned.logical_request
    return (
        logical.group_hypothesis_id,
        logical.item.sample_stem,
        logical.item.request,
        logical.item.preferred_rt,
    )


def _terminal_request_signature(
    terminal: OwnerBackfillAttemptTerminal,
) -> tuple[object, ...]:
    logical = terminal.logical_request
    return (
        logical.group_hypothesis_id,
        logical.item.sample_stem,
        logical.item.request,
        logical.item.preferred_rt,
    )


def _validate_validation_request_identity(
    validation: OwnerBackfillRequestIdentity,
    *,
    primary_identity: OwnerBackfillRequestIdentity,
) -> None:
    if validation.validation_mode != "validation":
        raise ValueError("validation terminal lacks validation request semantics")
    if (
        validation.sample_stem != primary_identity.sample_stem
        or validation.acquisition_source != primary_identity.acquisition_source
        or validation.request != primary_identity.request
        or validation.backend != "raw"
        or validation.window_strategy != "exact"
        or validation.superwindow_span_factor
        != primary_identity.superwindow_span_factor
        or validation.result_semantics != primary_identity.result_semantics
    ):
        raise ValueError("validation terminal crossed primary request identity")


def _epoch_basis(
    *,
    processing_request_id: str,
    groups: tuple[CrossSampleGroupIdentity, ...],
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
    backend: OwnerBackfillXicBackend,
    window_strategy: OwnerBackfillWindowStrategy,
    superwindow_span_factor: int,
    validation_mode: OwnerBackfillValidationMode,
    result_semantics: str,
    scope: BackfillScope,
    selected_family_ids: frozenset[str],
    positions: tuple[OwnerBackfillPlanningPosition, ...],
) -> Mapping[str, object]:
    return {
        "schema": "owner_backfill_planning_epoch_v1",
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
            }
            for item in positions
        ],
    }


def _validate_request_semantics(
    *,
    backend: OwnerBackfillXicBackend,
    window_strategy: OwnerBackfillWindowStrategy,
    superwindow_span_factor: int,
    validation_mode: OwnerBackfillValidationMode,
    result_semantics: str,
) -> None:
    if backend not in {"raw", "ms1_index", "ms1_index_hybrid"}:
        raise ValueError("coverage request backend is not closed")
    if window_strategy not in {"exact", "super-window"}:
        raise ValueError("coverage request window strategy is not closed")
    if (
        isinstance(superwindow_span_factor, bool)
        or not isinstance(superwindow_span_factor, int)
        or superwindow_span_factor < 1
    ):
        raise ValueError("coverage superwindow span factor must be positive")
    if validation_mode not in {"primary", "prefilter", "validation"}:
        raise ValueError("coverage validation mode is not closed")
    if result_semantics != OWNER_BACKFILL_TRACE_RESULT_SEMANTICS:
        raise ValueError("coverage result semantics crossed the owner contract")


def _terminal_evaluation_basis(
    terminal: OwnerBackfillAttemptTerminal,
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
    "OWNER_BACKFILL_CANDIDATE_PHASES",
    "OWNER_BACKFILL_PLANNING_REASONS",
    "OWNER_BACKFILL_TERMINAL_REASONS",
    "OwnerBackfillAttemptTerminal",
    "OwnerBackfillCandidatePhase",
    "OwnerBackfillEvaluationCoverageBatch",
    "OwnerBackfillEvaluationCoverageContext",
    "OwnerBackfillEvaluationPlanningEpoch",
    "OwnerBackfillPlannedLogicalRequest",
    "OwnerBackfillPlanningPosition",
    "OwnerBackfillPlanningReason",
    "OwnerBackfillPlanningState",
    "OwnerBackfillTerminalReason",
    "build_owner_backfill_evaluation_coverage",
    "build_owner_backfill_planning_epoch",
    "mint_owner_backfill_attempt_terminal",
    "mint_owner_backfill_planning_epoch",
    "mint_owner_backfill_position_id",
    "mint_owner_backfill_selection_subject_id",
    "mint_owner_backfill_terminal_id",
    "validate_owner_backfill_evaluation_coverage",
]
