from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from xic_extractor.discovery.targeted_candidate_set import TargetedCandidateSetRef
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.evidence_semantics import DecisionClass
from xic_extractor.peak_detection.hypotheses import (
    PeakHypothesis,
    validate_peak_hypothesis_id_selection_authority,
    validate_peak_hypothesis_selection_authority,
)
from xic_extractor.peak_detection.models import PeakDetectionResult

LegacyProjectionStatus = Literal["active_policy_remaining", "successor_owned"]
SelectionDecisionPolicySource = Literal["selected_hypothesis_decision_v1"]
SelectionDecisionCompatibilityOracle = Literal[
    "legacy_peak_scoring_current_oracle",
    "successor_evidence_decision_semantics",
]
DiagnosticSelectionStatus = Literal[
    "selected",
    "ambiguous",
    "no_candidate",
    "failed",
]

DIAGNOSTIC_SELECTION_SCHEMA_VERSION = "diagnostic_peak_hypothesis_selection_v2"
DIAGNOSTIC_SELECTION_VALIDATION_STATUS = "diagnostic_only"
DIAGNOSTIC_SELECTION_POLICY = "unique_owner_backed_hypothesis_v1"
_DIAGNOSTIC_SELECTION_SEMANTIC_ROLE = "diagnostic_peak_hypothesis_selection"
_DIAGNOSTIC_SELECTION_ARTIFACT_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.peak_detection.selection_decision",
    semantic_role=_DIAGNOSTIC_SELECTION_SEMANTIC_ROLE,
    schema_version=DIAGNOSTIC_SELECTION_SCHEMA_VERSION,
    validation_status=DIAGNOSTIC_SELECTION_VALIDATION_STATUS,
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf-v1",
)
_SAMPLE_ID_PREFIX = "xic.sample.v1:"
_TARGET_SNAPSHOT_PREFIX = "xic.target-list-snapshot.v1:"
_TARGET_KEY_PREFIX = "xic.target-key.v1:"
_TARGET_DEFINITION_PREFIX = "xic.target-definition.v1:"
_EVIDENCE_ARTIFACT_ID_PREFIX = "xic.evidence-artifact.v1:"
_HYPOTHESIS_ID_PREFIX = "xic.peak-hypothesis.v1:"
_ARTIFACT_CONTENT_ID_PREFIX = "xic.artifact-content.v1:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DiagnosticPeakHypothesisSelectionError(ValueError):
    """Raised when diagnostic selection externalization is not owner-closed."""


@dataclass(frozen=True, slots=True)
class DiagnosticPeakHypothesisSelectionRef:
    """Immutable no-product-authority selection over one complete candidate set."""

    canonical_sample_identity_id: str
    target_snapshot_id: str
    target_key_id: str
    target_definition_id: str
    candidate_set_artifact_id: str
    candidate_hypothesis_ids: tuple[str, ...]
    candidate_hypothesis_content_ids: tuple[str, ...]
    status: DiagnosticSelectionStatus
    selected_hypothesis_id: str | None
    reasons: tuple[str, ...]
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        _validate_typed_id(
            self.canonical_sample_identity_id,
            prefix=_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
        _validate_typed_id(
            self.target_snapshot_id,
            prefix=_TARGET_SNAPSHOT_PREFIX,
            field="target_snapshot_id",
        )
        _validate_typed_id(
            self.target_key_id,
            prefix=_TARGET_KEY_PREFIX,
            field="target_key_id",
        )
        _validate_typed_id(
            self.candidate_set_artifact_id,
            prefix=_EVIDENCE_ARTIFACT_ID_PREFIX,
            field="candidate_set_artifact_id",
        )
        _validate_typed_id(
            self.target_definition_id,
            prefix=_TARGET_DEFINITION_PREFIX,
            field="target_definition_id",
        )
        candidate_ids = _validate_candidate_ids(self.candidate_hypothesis_ids)
        content_ids = _validate_content_ids(self.candidate_hypothesis_content_ids)
        if len(candidate_ids) != len(content_ids):
            raise DiagnosticPeakHypothesisSelectionError(
                "candidate hypothesis and content identities must be one-to-one"
            )
        _validate_diagnostic_selection_state(
            status=self.status,
            candidate_hypothesis_ids=candidate_ids,
            selected_hypothesis_id=self.selected_hypothesis_id,
            reasons=self.reasons,
        )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise DiagnosticPeakHypothesisSelectionError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _DIAGNOSTIC_SELECTION_ARTIFACT_CONTRACT:
            raise DiagnosticPeakHypothesisSelectionError(
                "artifact semantic contract is not owned by diagnostic selection"
            )
        if self.artifact.upstream_artifact_ids != (self.candidate_set_artifact_id,):
            raise DiagnosticPeakHypothesisSelectionError(
                "diagnostic selection must consume exactly one candidate-set receipt"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_diagnostic_peak_hypothesis_selection_bytes(self),
            )
        except ValueError as exc:
            raise DiagnosticPeakHypothesisSelectionError(
                "diagnostic selection content mismatches its immutable artifact"
            ) from exc


def mint_diagnostic_peak_hypothesis_selection_ref(
    *,
    candidate_set: TargetedCandidateSetRef,
) -> DiagnosticPeakHypothesisSelectionRef:
    """Select only from one Discovery-owned complete candidate-set receipt."""

    if not isinstance(candidate_set, TargetedCandidateSetRef):
        raise DiagnosticPeakHypothesisSelectionError(
            "candidate_set must be an owner-minted TargetedCandidateSetRef"
        )
    candidates = candidate_set.candidate_hypotheses
    if candidate_set.status == "failed":
        status: DiagnosticSelectionStatus = "failed"
        selected_hypothesis_id = None
        reasons = (candidate_set.failure_reason,)
    elif not candidates:
        status = "no_candidate"
        selected_hypothesis_id = None
        reasons = ("no_owner_backed_candidate",)
    elif len(candidates) == 1:
        status = "selected"
        selected_hypothesis_id = candidates[0].hypothesis_id
        reasons = ("unique_owner_backed_candidate",)
    else:
        status = "ambiguous"
        selected_hypothesis_id = None
        reasons = ("multiple_owner_backed_candidates",)

    candidate_ids = tuple(item.hypothesis_id for item in candidates)
    candidate_content_ids = tuple(
        _candidate_artifact(item).content_identity.artifact_content_id
        for item in candidates
    )
    payload = _diagnostic_selection_bytes(
        canonical_sample_identity_id=candidate_set.canonical_sample_identity_id,
        target_snapshot_id=candidate_set.target_snapshot_id,
        target_key_id=candidate_set.target_key_id,
        target_definition_id=candidate_set.target_definition_id,
        candidate_set_artifact_id=candidate_set.artifact.evidence_artifact_id,
        candidate_hypothesis_ids=candidate_ids,
        candidate_hypothesis_content_ids=candidate_content_ids,
        status=status,
        selected_hypothesis_id=selected_hypothesis_id,
        reasons=reasons,
    )
    return DiagnosticPeakHypothesisSelectionRef(
        canonical_sample_identity_id=candidate_set.canonical_sample_identity_id,
        target_snapshot_id=candidate_set.target_snapshot_id,
        target_key_id=candidate_set.target_key_id,
        target_definition_id=candidate_set.target_definition_id,
        candidate_set_artifact_id=candidate_set.artifact.evidence_artifact_id,
        candidate_hypothesis_ids=candidate_ids,
        candidate_hypothesis_content_ids=candidate_content_ids,
        status=status,
        selected_hypothesis_id=selected_hypothesis_id,
        reasons=reasons,
        artifact=_mint_evidence_artifact_ref(
            producer_processing_request_id=(
                candidate_set.processing_request_id
            ),
            producer_processing_attempt_id=(
                candidate_set.processing_attempt_id
            ),
            semantic_contract=_DIAGNOSTIC_SELECTION_ARTIFACT_CONTRACT,
            payload_bytes=payload,
            upstream_artifacts=(candidate_set.artifact,),
        ),
    )


def canonical_diagnostic_peak_hypothesis_selection_bytes(
    reference: DiagnosticPeakHypothesisSelectionRef,
) -> bytes:
    if not isinstance(reference, DiagnosticPeakHypothesisSelectionRef):
        raise DiagnosticPeakHypothesisSelectionError(
            "canonical serialization requires DiagnosticPeakHypothesisSelectionRef"
        )
    return _diagnostic_selection_bytes(
        canonical_sample_identity_id=reference.canonical_sample_identity_id,
        target_snapshot_id=reference.target_snapshot_id,
        target_key_id=reference.target_key_id,
        target_definition_id=reference.target_definition_id,
        candidate_set_artifact_id=reference.candidate_set_artifact_id,
        candidate_hypothesis_ids=reference.candidate_hypothesis_ids,
        candidate_hypothesis_content_ids=reference.candidate_hypothesis_content_ids,
        status=reference.status,
        selected_hypothesis_id=reference.selected_hypothesis_id,
        reasons=reference.reasons,
    )


def resolve_diagnostic_peak_hypothesis_selection(
    reference: DiagnosticPeakHypothesisSelectionRef,
    candidate_set: TargetedCandidateSetRef,
) -> PeakHypothesis | None:
    """Resolve only the exact Discovery receipt consumed by the selection ref."""

    if not isinstance(reference, DiagnosticPeakHypothesisSelectionRef):
        raise DiagnosticPeakHypothesisSelectionError(
            "reference must be DiagnosticPeakHypothesisSelectionRef"
        )
    if not isinstance(candidate_set, TargetedCandidateSetRef):
        raise DiagnosticPeakHypothesisSelectionError(
            "candidate_set must be an owner-minted TargetedCandidateSetRef"
        )
    candidates = candidate_set.candidate_hypotheses
    if (
        candidate_set.processing_request_id
        != reference.artifact.producer_processing_request_id
        or candidate_set.processing_attempt_id
        != reference.artifact.producer_processing_attempt_id
        or candidate_set.canonical_sample_identity_id
        != reference.canonical_sample_identity_id
        or candidate_set.target_snapshot_id != reference.target_snapshot_id
        or candidate_set.target_key_id != reference.target_key_id
        or candidate_set.target_definition_id != reference.target_definition_id
        or candidate_set.artifact.evidence_artifact_id
        != reference.candidate_set_artifact_id
        or candidate_set.candidate_hypothesis_ids
        != reference.candidate_hypothesis_ids
        or candidate_set.candidate_hypothesis_content_ids
        != reference.candidate_hypothesis_content_ids
        or reference.artifact.upstream_artifact_ids
        != (candidate_set.artifact.evidence_artifact_id,)
    ):
        raise DiagnosticPeakHypothesisSelectionError(
            "candidate-set receipt does not match the immutable selection"
        )
    if reference.status != "selected":
        return None
    selected = tuple(
        item
        for item in candidates
        if item.hypothesis_id == reference.selected_hypothesis_id
    )
    if len(selected) != 1:
        raise DiagnosticPeakHypothesisSelectionError(
            "immutable selection does not resolve exactly one hypothesis"
        )
    return selected[0]


@dataclass(frozen=True)
class PeakHypothesisSelectionDecision:
    selected_candidate_id: str
    trace_group_id: str
    decision_class: DecisionClass
    projected_confidence: str
    projected_reason: str
    support_reasons: tuple[str, ...] = ()
    conflict_reasons: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    not_counted_reasons: tuple[str, ...] = ()
    exclusion_reasons: tuple[str, ...] = ()
    ambiguity_reasons: tuple[str, ...] = ()
    compatibility_labels: tuple[str, ...] = ()
    evidence_sources: tuple[str, ...] = ()
    legacy_projection_status: LegacyProjectionStatus = "active_policy_remaining"
    policy_source: SelectionDecisionPolicySource = "selected_hypothesis_decision_v1"
    compatibility_oracle: SelectionDecisionCompatibilityOracle = (
        "legacy_peak_scoring_current_oracle"
    )

    def __post_init__(self) -> None:
        validate_peak_hypothesis_id_selection_authority(
            self.selected_candidate_id,
            consumer="PeakHypothesisSelectionDecision",
        )


def selection_decision_from_hypothesis(
    hypothesis: PeakHypothesis,
    *,
    peak_result: PeakDetectionResult | None = None,
    fallback_confidence: str = "",
    fallback_reason: str = "",
) -> PeakHypothesisSelectionDecision:
    validate_peak_hypothesis_selection_authority(
        hypothesis,
        consumer="selection_decision_from_hypothesis",
    )
    if peak_result is not None:
        fallback_confidence = _fallback_confidence(peak_result)
        fallback_reason = _fallback_reason(peak_result)

    evidence = hypothesis.evidence
    semantics = evidence.decision_semantics
    if semantics is None:
        decision_class: DecisionClass = "review"
        support_reasons: tuple[str, ...] = ()
        conflict_reasons: tuple[str, ...] = ()
        review_reasons: tuple[str, ...] = ("insufficient_typed_evidence",)
        not_counted_reasons: tuple[str, ...] = ()
        exclusion_reasons: tuple[str, ...] = ()
        ambiguity_reasons: tuple[str, ...] = ()
        compatibility_labels: tuple[str, ...] = _legacy_compatibility_labels(hypothesis)
    else:
        decision_class = semantics.decision_class
        support_reasons = semantics.support_reasons
        conflict_reasons = semantics.conflict_reasons
        review_reasons = semantics.review_reasons
        not_counted_reasons = semantics.not_counted_reasons
        exclusion_reasons = semantics.exclusion_reasons
        ambiguity_reasons = semantics.ambiguity_reasons
        compatibility_labels = semantics.compatibility_labels

    legacy_projection_status, compatibility_oracle = _ownership_markers(
        not_counted_reasons,
        has_typed_projection=bool(
            evidence.evidence_facts is not None
            or evidence.projected_confidence
            or evidence.projected_reason
        ),
    )

    return PeakHypothesisSelectionDecision(
        selected_candidate_id=hypothesis.hypothesis_id,
        trace_group_id=hypothesis.trace_group_id,
        decision_class=decision_class,
        projected_confidence=(
            evidence.projected_confidence or evidence.confidence or fallback_confidence
        ),
        projected_reason=(
            evidence.projected_reason or evidence.reason or fallback_reason
        ),
        support_reasons=support_reasons,
        conflict_reasons=conflict_reasons,
        review_reasons=review_reasons,
        not_counted_reasons=not_counted_reasons,
        exclusion_reasons=exclusion_reasons,
        ambiguity_reasons=ambiguity_reasons,
        compatibility_labels=compatibility_labels,
        evidence_sources=_evidence_sources(hypothesis),
        legacy_projection_status=legacy_projection_status,
        compatibility_oracle=compatibility_oracle,
    )


def _fallback_confidence(peak_result: PeakDetectionResult) -> str:
    if peak_result.peak is None:
        return ""
    return peak_result.confidence or "HIGH"


def _fallback_reason(peak_result: PeakDetectionResult) -> str:
    return peak_result.reason or ""


def _legacy_compatibility_labels(
    hypothesis: PeakHypothesis,
) -> tuple[str, ...]:
    evidence = hypothesis.evidence
    return tuple(
        dict.fromkeys(
            (
                *evidence.support_labels,
                *evidence.concern_labels,
                *evidence.cap_labels,
                *hypothesis.audit.proposal_sources,
                *evidence.quality_flags,
            )
        )
    )


def _ownership_markers(
    not_counted_reasons: tuple[str, ...],
    *,
    has_typed_projection: bool = False,
) -> tuple[LegacyProjectionStatus, SelectionDecisionCompatibilityOracle]:
    if has_typed_projection or "missing_ms2_policy_not_counted" in not_counted_reasons:
        return "successor_owned", "successor_evidence_decision_semantics"
    return "active_policy_remaining", "legacy_peak_scoring_current_oracle"


def _evidence_sources(hypothesis: PeakHypothesis) -> tuple[str, ...]:
    evidence = hypothesis.evidence
    semantics = evidence.decision_semantics
    reasons = (
        ()
        if semantics is None
        else (
            *semantics.support_reasons,
            *semantics.conflict_reasons,
            *semantics.review_reasons,
            *semantics.not_counted_reasons,
            *semantics.exclusion_reasons,
            *semantics.ambiguity_reasons,
        )
    )
    sources: list[str] = []
    if (
        evidence.common is not None
        or evidence.prominence is not None
        or evidence.region_scan_count is not None
    ):
        sources.append("ms1_trace")
    if (
        evidence.ms2_present is not None
        or evidence.nl_match is not None
        or any("ms2" in reason or "nl" in reason for reason in reasons)
    ):
        sources.append("candidate_aligned_ms2_nl")
    if evidence.rt_prior_min is not None or any("rt" in reason for reason in reasons):
        sources.append("role_aware_rt")
    if (
        evidence.cwt_best_scale is not None
        or evidence.cwt_ridge_persistence is not None
        or "centwave_cwt" in hypothesis.audit.proposal_sources
        or "cwt_boundary_morphology_context" in reasons
    ):
        sources.append("cwt_boundary_morphology_context")
    if (
        "chrom_peak_segment" in hypothesis.audit.proposal_sources
        or "chrom_peak_segment_context" in reasons
    ):
        sources.append("chrom_peak_segment_context")
    if (
        evidence.quality_flags
        or evidence.region_trace_continuity is not None
        or evidence.region_edge_ratio is not None
        or "chrom_peak_segment" in hypothesis.audit.proposal_sources
        or any("trace_morphology" in reason for reason in reasons)
    ):
        sources.append("trace_morphology")
    if (
        evidence.evidence_facts is not None
        or evidence.projected_confidence
        or evidence.projected_reason
    ):
        sources.append("typed_candidate_evidence_facts")
    if (
        evidence.confidence
        or evidence.reason
        or evidence.raw_score is not None
        or evidence.support_labels
        or evidence.concern_labels
        or evidence.cap_labels
    ):
        sources.append("legacy_compatibility_projection")
    return tuple(dict.fromkeys(sources))


def _candidate_artifact(hypothesis: PeakHypothesis) -> EvidenceArtifactRef:
    if not isinstance(hypothesis.artifact, EvidenceArtifactRef):
        raise DiagnosticPeakHypothesisSelectionError(
            "candidate hypothesis must carry its immutable artifact"
        )
    return hypothesis.artifact


def _validate_candidate_ids(values: object) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise DiagnosticPeakHypothesisSelectionError(
            "candidate_hypothesis_ids must be an immutable tuple"
        )
    normalized = tuple(
        _validate_typed_id(value, prefix=_HYPOTHESIS_ID_PREFIX, field="hypothesis_id")
        for value in values
    )
    if normalized != tuple(sorted(normalized)) or len(normalized) != len(
        set(normalized)
    ):
        raise DiagnosticPeakHypothesisSelectionError(
            "candidate_hypothesis_ids must be unique and canonical-sorted"
        )
    return normalized


def _validate_content_ids(values: object) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise DiagnosticPeakHypothesisSelectionError(
            "candidate_hypothesis_content_ids must be an immutable tuple"
        )
    return tuple(
        _validate_typed_id(
            value,
            prefix=_ARTIFACT_CONTENT_ID_PREFIX,
            field="candidate_hypothesis_content_id",
        )
        for value in values
    )


def _validate_diagnostic_selection_state(
    *,
    status: object,
    candidate_hypothesis_ids: tuple[str, ...],
    selected_hypothesis_id: object,
    reasons: object,
) -> None:
    if status not in {"selected", "ambiguous", "no_candidate", "failed"}:
        raise DiagnosticPeakHypothesisSelectionError(
            "status must be selected, ambiguous, no_candidate, or failed"
        )
    if not isinstance(reasons, tuple) or not reasons:
        raise DiagnosticPeakHypothesisSelectionError(
            "diagnostic selection requires immutable reasons"
        )
    normalized_reasons = tuple(_required_reason(value) for value in reasons)
    if normalized_reasons != reasons or len(reasons) != len(set(reasons)):
        raise DiagnosticPeakHypothesisSelectionError(
            "diagnostic selection reasons must be normalized and unique"
        )
    if status == "selected":
        if len(candidate_hypothesis_ids) != 1:
            raise DiagnosticPeakHypothesisSelectionError(
                "selected status requires exactly one candidate hypothesis"
            )
        if selected_hypothesis_id != candidate_hypothesis_ids[0]:
            raise DiagnosticPeakHypothesisSelectionError(
                "selected hypothesis must be the unique candidate"
            )
        return
    if selected_hypothesis_id is not None:
        raise DiagnosticPeakHypothesisSelectionError(
            "non-selected diagnostic status cannot carry a selected hypothesis"
        )
    if status == "ambiguous" and len(candidate_hypothesis_ids) < 2:
        raise DiagnosticPeakHypothesisSelectionError(
            "ambiguous status requires at least two candidate hypotheses"
        )
    if status == "no_candidate" and candidate_hypothesis_ids:
        raise DiagnosticPeakHypothesisSelectionError(
            "no_candidate status requires an empty candidate universe"
        )
    if status == "failed" and candidate_hypothesis_ids:
        raise DiagnosticPeakHypothesisSelectionError(
            "failed status requires an empty candidate universe"
        )


def _diagnostic_selection_bytes(
    *,
    canonical_sample_identity_id: str,
    target_snapshot_id: str,
    target_key_id: str,
    target_definition_id: str,
    candidate_set_artifact_id: str,
    candidate_hypothesis_ids: tuple[str, ...],
    candidate_hypothesis_content_ids: tuple[str, ...],
    status: DiagnosticSelectionStatus,
    selected_hypothesis_id: str | None,
    reasons: tuple[str, ...],
) -> bytes:
    payload = {
        "schema_version": DIAGNOSTIC_SELECTION_SCHEMA_VERSION,
        "validation_status": DIAGNOSTIC_SELECTION_VALIDATION_STATUS,
        "selection_policy": DIAGNOSTIC_SELECTION_POLICY,
        "canonical_sample_identity_id": canonical_sample_identity_id,
        "target_snapshot_id": target_snapshot_id,
        "target_key_id": target_key_id,
        "target_definition_id": target_definition_id,
        "candidate_set_artifact_id": candidate_set_artifact_id,
        "candidate_hypotheses": [
            {
                "hypothesis_id": hypothesis_id,
                "artifact_content_id": content_id,
            }
            for hypothesis_id, content_id in zip(
                candidate_hypothesis_ids,
                candidate_hypothesis_content_ids,
                strict=True,
            )
        ],
        "status": status,
        "selected_hypothesis_id": selected_hypothesis_id,
        "reasons": list(reasons),
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


def _validate_typed_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise DiagnosticPeakHypothesisSelectionError(
            f"{field} must use the {prefix!r} prefix"
        )
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise DiagnosticPeakHypothesisSelectionError(
            f"{field} must contain a SHA-256 digest"
        )
    return value


def _required_reason(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DiagnosticPeakHypothesisSelectionError(
            "selection reason must be non-empty normalized text"
        )
    return value
