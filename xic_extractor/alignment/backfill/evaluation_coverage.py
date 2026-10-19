"""Retain owner decisions and sample-by-group coverage at assembly."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal

from xic_extractor.alignment.backfill.completion_evidence import (
    BackfillCompletionProjection,
)
from xic_extractor.alignment.backfill.request_plan import (
    BackfillValidationMode,
    BackfillWindowStrategy,
)
from xic_extractor.alignment.backfill.scope import (
    BackfillScope,
)
from xic_extractor.alignment.config import AlignmentConfig, BackfillXicBackend
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

if TYPE_CHECKING:
    from xic_extractor.alignment.backfill.conservative import (
        ConservativeBackfillCellDecision,
    )


BackfillPlanningReason = Literal[
    "retained_local_owner",
    "retained_unresolved_candidates",
    "local_owner_not_supersedable",
    "direct_support_threshold_not_met",
    "backfill_search_completed_empty",
    "no_discovery_input",
]

_POSITION_PREFIX = "xic.owner-backfill-coverage-position:"
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
class BackfillPlanningPosition:
    position_id: str
    group_identity: CrossSampleGroupIdentity
    sample: ResolvedCanonicalSampleIdentity
    planning_reason: BackfillPlanningReason
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
class BackfillEvaluationCoverageBatch:
    coverage_id: str
    epoch: BackfillEvaluationPlanningEpoch


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


def build_backfill_evaluation_coverage(
    epoch: BackfillEvaluationPlanningEpoch,
) -> BackfillEvaluationCoverageBatch:
    return BackfillEvaluationCoverageBatch(
        coverage_id=_typed_id(
            _BATCH_PREFIX,
            {
                "schema": "owner_backfill_evaluation_coverage",
                "epoch_id": epoch.epoch_id,
                # Canonical coverage identity includes empty terminal slots.
                "terminal_ids": [],
                "terminal_evaluations": [],
            },
        ),
        epoch=epoch,
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
                # Positions are never planned for requests; these constants
                # keep the published epoch identity unchanged.
                "planning_state": "not_planned",
                "planning_reason": item.planning_reason,
                "logical_request_ids": [],
                "request_identity_ids": [],
                "conservative_no_request_proof_id": (
                    item.conservative_no_request_proof.proof_id
                    if item.conservative_no_request_proof is not None
                    else None
                ),
            }
            for item in positions
        ],
    }


def _typed_id(prefix: str, basis: Mapping[str, object]) -> str:
    payload = json.dumps(
        basis,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return prefix + hashlib.sha256(payload).hexdigest()


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty normalized text")
    return value


__all__ = [
    "BackfillConservativeNoRequestProof",
    "BackfillEvaluationCoverageBatch",
    "BackfillEvaluationPlanningEpoch",
    "BackfillPlanningPosition",
    "BackfillPlanningReason",
    "build_backfill_evaluation_coverage",
    "mint_backfill_conservative_no_request_proof",
    "mint_backfill_planning_epoch",
    "mint_backfill_position_id",
]
