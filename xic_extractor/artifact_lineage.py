"""Closure validation over producer-owned immutable evidence-artifact refs."""

from __future__ import annotations

from xic_extractor.evidence_artifacts import EvidenceArtifactRef


class EvidenceArtifactClosureError(ValueError):
    """Raised when a supplied artifact dependency closure is not complete."""


def canonical_evidence_artifact_closure(
    *,
    processing_request_id: str,
    processing_attempt_id: str,
    artifacts: tuple[EvidenceArtifactRef, ...],
) -> tuple[EvidenceArtifactRef, ...]:
    """Validate one same-attempt closure and return canonical artifact order."""

    if not isinstance(artifacts, tuple) or any(
        not isinstance(artifact, EvidenceArtifactRef) for artifact in artifacts
    ):
        raise EvidenceArtifactClosureError(
            "evidence_artifacts must contain owner-minted EvidenceArtifactRef values"
        )
    by_id: dict[str, EvidenceArtifactRef] = {}
    for artifact in artifacts:
        artifact_id = artifact.evidence_artifact_id
        if artifact_id in by_id:
            raise EvidenceArtifactClosureError(
                f"evidence artifact {artifact_id!r} is ambiguous"
            )
        if artifact.producer_processing_request_id != processing_request_id:
            raise EvidenceArtifactClosureError(
                "evidence artifact references a different processing request"
            )
        if artifact.producer_processing_attempt_id != processing_attempt_id:
            raise EvidenceArtifactClosureError(
                "evidence artifact references a different processing attempt"
            )
        by_id[artifact_id] = artifact

    known_ids = set(by_id)
    for artifact in artifacts:
        missing = sorted(set(artifact.upstream_artifact_ids) - known_ids)
        if missing:
            raise EvidenceArtifactClosureError(
                f"evidence artifact {artifact.evidence_artifact_id!r} has missing "
                f"upstream dependencies: {', '.join(missing)}"
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(artifact_id: str) -> None:
        if artifact_id in visited:
            return
        if artifact_id in visiting:
            raise EvidenceArtifactClosureError(
                "evidence artifact dependency graph contains a cycle"
            )
        visiting.add(artifact_id)
        for upstream_id in by_id[artifact_id].upstream_artifact_ids:
            visit(upstream_id)
        visiting.remove(artifact_id)
        visited.add(artifact_id)

    for artifact_id in by_id:
        visit(artifact_id)

    return tuple(sorted(artifacts, key=lambda artifact: artifact.evidence_artifact_id))


__all__ = [
    "EvidenceArtifactClosureError",
    "canonical_evidence_artifact_closure",
]
