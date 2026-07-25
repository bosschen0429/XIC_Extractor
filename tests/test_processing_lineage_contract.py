import pytest

from xic_extractor.processing_lineage import (
    DeliveryCellKey,
    DeliveryRevisionRef,
    DeliverySealCandidate,
    EvidenceArtifactRef,
    LineageContractError,
    ProcessingAttemptRef,
    validate_delivery_seal_candidate,
    validate_retry_attempt,
)


def test_retry_requires_new_attempt_for_same_request() -> None:
    previous = ProcessingAttemptRef("attempt-1", "request-1")
    retry = ProcessingAttemptRef(
        "attempt-2",
        "request-1",
        predecessor_attempt_id="attempt-1",
    )

    validate_retry_attempt(previous, retry)

    with pytest.raises(LineageContractError, match="new processing attempt"):
        validate_retry_attempt(previous, previous)


def test_delivery_seal_closes_approved_revisions_and_acknowledged_artifacts() -> None:
    artifact = _artifact("artifact-evidence", "attempt-1")
    output = _artifact(
        "artifact-output",
        "attempt-1",
        dependency_artifact_ids=("artifact-evidence",),
    )
    revision = DeliveryRevisionRef(
        revision_id="revision-1",
        cell_key=DeliveryCellKey("targeted", "sample-1", "target-1"),
        producer_attempt_id="attempt-1",
        evidence_artifact_ids=("artifact-evidence",),
        authority_approved=True,
    )
    candidate = DeliverySealCandidate(
        revision_ids=("revision-1",),
        output_artifact_ids=("artifact-output",),
        acknowledged_artifact_ids=("artifact-evidence", "artifact-output"),
    )

    closure = validate_delivery_seal_candidate(
        candidate,
        revisions={revision.revision_id: revision},
        artifacts={
            artifact.artifact_id: artifact,
            output.artifact_id: output,
        },
    )

    assert closure.revisions == (revision,)
    assert {item.artifact_id for item in closure.artifacts} == {
        "artifact-evidence",
        "artifact-output",
    }


def test_delivery_seal_rejects_unapproved_revision() -> None:
    artifact = _artifact("artifact-evidence", "attempt-1")
    revision = DeliveryRevisionRef(
        revision_id="revision-1",
        cell_key=DeliveryCellKey("untargeted", "sample-1", "row-1"),
        producer_attempt_id="attempt-1",
        evidence_artifact_ids=("artifact-evidence",),
    )
    candidate = DeliverySealCandidate(
        revision_ids=("revision-1",),
        output_artifact_ids=("artifact-evidence",),
        acknowledged_artifact_ids=("artifact-evidence",),
    )

    with pytest.raises(LineageContractError, match="owner approval"):
        validate_delivery_seal_candidate(
            candidate,
            revisions={revision.revision_id: revision},
            artifacts={artifact.artifact_id: artifact},
        )


def test_delivery_seal_rejects_missing_owner_acknowledgement() -> None:
    artifact = _artifact("artifact-evidence", "attempt-1")
    revision = DeliveryRevisionRef(
        revision_id="revision-1",
        cell_key=DeliveryCellKey("targeted", "sample-1", "target-1"),
        producer_attempt_id="attempt-1",
        evidence_artifact_ids=("artifact-evidence",),
        authority_approved=True,
    )
    candidate = DeliverySealCandidate(
        revision_ids=("revision-1",),
        output_artifact_ids=("artifact-evidence",),
        acknowledged_artifact_ids=("unrelated-artifact",),
    )

    with pytest.raises(LineageContractError, match="unacknowledged"):
        validate_delivery_seal_candidate(
            candidate,
            revisions={revision.revision_id: revision},
            artifacts={artifact.artifact_id: artifact},
        )


def _artifact(
    artifact_id: str,
    attempt_id: str,
    *,
    dependency_artifact_ids: tuple[str, ...] = (),
) -> EvidenceArtifactRef:
    return EvidenceArtifactRef(
        artifact_id=artifact_id,
        producer_attempt_id=attempt_id,
        semantic_role="synthetic_contract_fixture",
        schema_version="fixture_v1",
        content_sha256="a" * 64,
        dependency_artifact_ids=dependency_artifact_ids,
    )
