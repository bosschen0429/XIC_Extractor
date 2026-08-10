"""Bind Untargeted explanation navigation and artifacts to live owner identity."""

from __future__ import annotations

from collections.abc import Sequence

from xic_extractor.alignment.artifact_write_receipts import (
    AlignmentArtifactFingerprintBatch,
)
from xic_extractor.alignment.evidence_claims import (
    EvidenceClaim,
    EvidenceClaimState,
)
from xic_extractor.alignment.untargeted_explanation_contract import (
    _LOCATOR_MINT_TOKEN,
    _RUN_BINDING_MINT_TOKEN,
    ExplanationLocator,
    ExplanationLocatorRequest,
    ExplanationRunBinding,
    ExplanationSubject,
    ExplanationTopic,
)
from xic_extractor.processing_identity import (
    FileContentFingerprint,
    ProcessingAttemptIdentity,
)


def bind_explanation_locators(
    subject: ExplanationSubject,
    requests: Sequence[ExplanationLocatorRequest],
) -> dict[ExplanationTopic, tuple[ExplanationLocator, ...]]:
    """Mint route-, attempt-, and topic-bound navigation from caller requests."""

    if not isinstance(subject, ExplanationSubject):
        raise TypeError("subject must be an ExplanationSubject")
    normalized = tuple(requests)
    if any(not isinstance(item, ExplanationLocatorRequest) for item in normalized):
        raise TypeError("requests must contain ExplanationLocatorRequest values")
    bound: dict[ExplanationTopic, list[ExplanationLocator]] = {
        topic: [] for topic in ExplanationTopic
    }
    for request in normalized:
        if (
            request.processing_attempt_id
            != subject.processing_attempt.processing_attempt_id
            or request.peak_hypothesis_id != subject.peak_hypothesis_id
            or request.canonical_sample_identity_id
            != subject.sample.identity.sample_identity_id
        ):
            raise ValueError(
                "navigation receipt crosses the requested route or attempt"
            )
        bound[request.topic].append(
            ExplanationLocator(
                kind=request.kind,
                topic=request.topic,
                relative_path=request.relative_path,
                processing_attempt_id=(
                    subject.processing_attempt.processing_attempt_id
                ),
                peak_hypothesis_id=subject.peak_hypothesis_id,
                canonical_sample_identity_id=(
                    subject.sample.identity.sample_identity_id
                ),
                artifact_fingerprint=request.artifact_fingerprint,
                _mint_token=_LOCATOR_MINT_TOKEN,
            )
        )
    return {topic: tuple(items) for topic, items in bound.items()}


def build_explanation_run_binding(
    processing_attempt: ProcessingAttemptIdentity,
    artifact_fingerprints: AlignmentArtifactFingerprintBatch | None,
) -> ExplanationRunBinding:
    """Preserve exact receipt ceilings without claiming Matrix-to-run proof."""

    matrix_fingerprint: FileContentFingerprint | None = None
    identity_fingerprint: FileContentFingerprint | None = None
    artifact_claim = EvidenceClaim(
        subject="matrix_artifact_bytes",
        state=EvidenceClaimState.UNAVAILABLE,
        reason=(
            "alignment_matrix.tsv and alignment_matrix_identity.tsv writer receipts "
            "were not both supplied"
        ),
    )
    if artifact_fingerprints is not None:
        if not isinstance(artifact_fingerprints, AlignmentArtifactFingerprintBatch):
            raise TypeError(
                "artifact_fingerprints must be an AlignmentArtifactFingerprintBatch"
            )
        receipts_by_name = {
            receipt.artifact_name: receipt for receipt in artifact_fingerprints.receipts
        }
        matrix_receipt = receipts_by_name.get("alignment_matrix.tsv")
        identity_receipt = receipts_by_name.get("alignment_matrix_identity.tsv")
        if matrix_receipt is not None and identity_receipt is not None:
            if (
                matrix_receipt.artifact_path.parent
                != identity_receipt.artifact_path.parent
            ):
                raise ValueError(
                    "Matrix and identity receipts must share one output root"
                )
            matrix_fingerprint = matrix_receipt.fingerprint
            identity_fingerprint = identity_receipt.fingerprint
            artifact_claim = EvidenceClaim(
                subject="matrix_artifact_bytes",
                state=EvidenceClaimState.BYTE_BOUND,
                reason=(
                    "Alignment writer receipts bind the exact Matrix and identity "
                    "sidecar bytes; semantic claims remain with their live owners"
                ),
            )
    return ExplanationRunBinding(
        processing_attempt=processing_attempt,
        attempt_claim=EvidenceClaim(
            subject="processing_attempt_identity",
            state=EvidenceClaimState.OWNER_REHYDRATED,
            reason="the processing identity owner validates the immutable attempt",
        ),
        matrix_artifact_claim=artifact_claim,
        matrix_run_binding_claim=EvidenceClaim(
            subject="matrix_run_binding",
            state=EvidenceClaimState.UNAVAILABLE,
            reason=(
                "writer byte receipts and processing attempt identity do not prove "
                "that the retained Matrix belongs to this attempt or live owner result"
            ),
        ),
        runtime_finalization_claim=EvidenceClaim(
            subject="runtime_finalization",
            state=EvidenceClaimState.UNAVAILABLE,
            reason=(
                "this bounded contract does not accept or reconstruct a runtime "
                "finalization receipt"
            ),
        ),
        matrix_fingerprint=matrix_fingerprint,
        matrix_identity_fingerprint=identity_fingerprint,
        _mint_token=_RUN_BINDING_MINT_TOKEN,
    )


__all__ = ("bind_explanation_locators", "build_explanation_run_binding")
