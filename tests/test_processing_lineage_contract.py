import pytest

from xic_extractor.processing_lineage import (
    AcquisitionSourceRef,
    DeliveryCellKey,
    DeliveryRevisionRef,
    DeliverySealCandidate,
    EvidenceArtifactRef,
    LineageContractError,
    ProcessingAttemptRef,
    ProcessingRequestIdentityBasis,
    build_processing_request_ref,
    derive_processing_request_id,
    validate_delivery_seal_candidate,
    validate_retry_attempt,
)
from xic_extractor.review_actions_models import ImmutableReviewRecordRef
from xic_extractor.sample_metadata import (
    SampleIdentityRef,
    SampleMetadata,
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


def test_processing_request_identity_changes_with_semantic_inputs() -> None:
    basis = _identity_basis()
    request = build_processing_request_ref(basis)

    assert request.request_id == derive_processing_request_id(basis)

    changed = ProcessingRequestIdentityBasis(
        acquisition_sources=basis.acquisition_sources,
        normalized_query_sha256="c" * 64,
        method_constraints_sha256=basis.method_constraints_sha256,
        algorithm_version=basis.algorithm_version,
        schema_version=basis.schema_version,
        contract_version=basis.contract_version,
    )
    assert derive_processing_request_id(changed) != request.request_id


def test_delivery_seal_closes_approved_revisions_and_acknowledged_artifacts() -> None:
    artifact = _artifact("artifact-evidence", "attempt-1")
    output = _artifact(
        "artifact-output",
        "attempt-1",
        dependency_artifact_ids=("artifact-evidence",),
    )
    revision = DeliveryRevisionRef(
        revision_id="revision-1",
        cell_key=DeliveryCellKey(
            "targeted",
            SampleIdentityRef("sample-1"),
            "target-1",
        ),
        producer_attempt_id="attempt-1",
        evidence_artifact_ids=("artifact-evidence",),
        review_record_refs=(_review_record(),),
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
        sample_metadata_rows=_sample_rows(),
        review_records=(_review_record(),),
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
        cell_key=DeliveryCellKey(
            "untargeted",
            SampleIdentityRef("sample-1"),
            "row-1",
        ),
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
            sample_metadata_rows=_sample_rows(),
            review_records=(_review_record(),),
        )


def test_delivery_seal_rejects_missing_owner_acknowledgement() -> None:
    artifact = _artifact("artifact-evidence", "attempt-1")
    revision = DeliveryRevisionRef(
        revision_id="revision-1",
        cell_key=DeliveryCellKey(
            "targeted",
            SampleIdentityRef("sample-1"),
            "target-1",
        ),
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
            sample_metadata_rows=_sample_rows(),
            review_records=(_review_record(),),
        )


def test_delivery_seal_rejects_mapping_identity_mismatch() -> None:
    artifact = _artifact("artifact-evidence", "attempt-1")
    revision = _revision("revision-real", "target-1", artifact.artifact_id)
    candidate = DeliverySealCandidate(
        revision_ids=("revision-alias",),
        output_artifact_ids=(artifact.artifact_id,),
        acknowledged_artifact_ids=(artifact.artifact_id,),
    )

    with pytest.raises(LineageContractError, match="does not match object identity"):
        validate_delivery_seal_candidate(
            candidate,
            revisions={"revision-alias": revision},
            artifacts={artifact.artifact_id: artifact},
            sample_metadata_rows=_sample_rows(),
            review_records=(_review_record(),),
        )


def test_delivery_seal_rejects_multiple_revisions_for_one_cell() -> None:
    artifact = _artifact("artifact-evidence", "attempt-1")
    first = _revision("revision-1", "target-1", artifact.artifact_id)
    second = _revision("revision-2", "target-1", artifact.artifact_id)
    candidate = DeliverySealCandidate(
        revision_ids=(first.revision_id, second.revision_id),
        output_artifact_ids=(artifact.artifact_id,),
        acknowledged_artifact_ids=(artifact.artifact_id,),
    )

    with pytest.raises(LineageContractError, match="one delivery cell"):
        validate_delivery_seal_candidate(
            candidate,
            revisions={
                first.revision_id: first,
                second.revision_id: second,
            },
            artifacts={artifact.artifact_id: artifact},
            sample_metadata_rows=_sample_rows(),
            review_records=(_review_record(),),
        )


def test_delivery_seal_rejects_artifact_dependency_cycle() -> None:
    first = _artifact(
        "artifact-1",
        "attempt-1",
        dependency_artifact_ids=("artifact-2",),
    )
    second = _artifact(
        "artifact-2",
        "attempt-1",
        dependency_artifact_ids=("artifact-1",),
    )
    revision = _revision("revision-1", "target-1", first.artifact_id)
    candidate = DeliverySealCandidate(
        revision_ids=(revision.revision_id,),
        output_artifact_ids=(first.artifact_id,),
        acknowledged_artifact_ids=(first.artifact_id, second.artifact_id),
    )

    with pytest.raises(LineageContractError, match="contains a cycle"):
        validate_delivery_seal_candidate(
            candidate,
            revisions={revision.revision_id: revision},
            artifacts={
                first.artifact_id: first,
                second.artifact_id: second,
            },
            sample_metadata_rows=_sample_rows(),
            review_records=(_review_record(),),
        )


def test_delivery_seal_requires_exact_acknowledgement_closure() -> None:
    artifact = _artifact("artifact-evidence", "attempt-1")
    unrelated = _artifact("artifact-unrelated", "attempt-2")
    revision = _revision("revision-1", "target-1", artifact.artifact_id)
    candidate = DeliverySealCandidate(
        revision_ids=(revision.revision_id,),
        output_artifact_ids=(artifact.artifact_id,),
        acknowledged_artifact_ids=(artifact.artifact_id, unrelated.artifact_id),
    )

    with pytest.raises(LineageContractError, match="outside its lineage closure"):
        validate_delivery_seal_candidate(
            candidate,
            revisions={revision.revision_id: revision},
            artifacts={
                artifact.artifact_id: artifact,
                unrelated.artifact_id: unrelated,
            },
            sample_metadata_rows=_sample_rows(),
            review_records=(_review_record(),),
        )


def test_delivery_revision_rejects_direct_cross_attempt_evidence() -> None:
    old_artifact = _artifact("artifact-old", "attempt-old")
    revision = DeliveryRevisionRef(
        revision_id="revision-1",
        cell_key=DeliveryCellKey(
            "targeted",
            SampleIdentityRef("sample-1"),
            "target-1",
        ),
        producer_attempt_id="attempt-new",
        evidence_artifact_ids=(old_artifact.artifact_id,),
        authority_approved=True,
    )
    candidate = DeliverySealCandidate(
        revision_ids=(revision.revision_id,),
        output_artifact_ids=(old_artifact.artifact_id,),
        acknowledged_artifact_ids=(old_artifact.artifact_id,),
    )

    with pytest.raises(LineageContractError, match="dependency edge"):
        validate_delivery_seal_candidate(
            candidate,
            revisions={revision.revision_id: revision},
            artifacts={old_artifact.artifact_id: old_artifact},
            sample_metadata_rows=_sample_rows(),
            review_records=(),
        )


def test_delivery_revision_allows_cross_attempt_dependency() -> None:
    old_artifact = _artifact("artifact-old", "attempt-old")
    current_artifact = _artifact(
        "artifact-current",
        "attempt-new",
        dependency_artifact_ids=(old_artifact.artifact_id,),
    )
    revision = DeliveryRevisionRef(
        revision_id="revision-1",
        cell_key=DeliveryCellKey(
            "targeted",
            SampleIdentityRef("sample-1"),
            "target-1",
        ),
        producer_attempt_id="attempt-new",
        evidence_artifact_ids=(current_artifact.artifact_id,),
        authority_approved=True,
    )
    candidate = DeliverySealCandidate(
        revision_ids=(revision.revision_id,),
        output_artifact_ids=(current_artifact.artifact_id,),
        acknowledged_artifact_ids=(
            current_artifact.artifact_id,
            old_artifact.artifact_id,
        ),
    )

    closure = validate_delivery_seal_candidate(
        candidate,
        revisions={revision.revision_id: revision},
        artifacts={
            current_artifact.artifact_id: current_artifact,
            old_artifact.artifact_id: old_artifact,
        },
        sample_metadata_rows=_sample_rows(),
        review_records=(),
    )

    assert {artifact.artifact_id for artifact in closure.artifacts} == {
        "artifact-current",
        "artifact-old",
    }


def test_delivery_seal_requires_known_superseded_seal() -> None:
    artifact = _artifact("artifact-evidence", "attempt-1")
    revision = _revision("revision-1", "target-1", artifact.artifact_id)
    candidate = DeliverySealCandidate(
        revision_ids=(revision.revision_id,),
        output_artifact_ids=(artifact.artifact_id,),
        acknowledged_artifact_ids=(artifact.artifact_id,),
        supersedes_seal_id="seal-missing",
    )

    with pytest.raises(LineageContractError, match="unknown prior seal"):
        validate_delivery_seal_candidate(
            candidate,
            revisions={revision.revision_id: revision},
            artifacts={artifact.artifact_id: artifact},
            sample_metadata_rows=_sample_rows(),
            review_records=(_review_record(),),
        )


def test_delivery_seal_revalidates_sample_ref_against_owner() -> None:
    artifact = _artifact("artifact-evidence", "attempt-1")
    revision = DeliveryRevisionRef(
        revision_id="revision-1",
        cell_key=DeliveryCellKey(
            "targeted",
            SampleIdentityRef("sample.raw"),
            "target-1",
        ),
        producer_attempt_id="attempt-1",
        evidence_artifact_ids=(artifact.artifact_id,),
        authority_approved=True,
    )
    candidate = DeliverySealCandidate(
        revision_ids=(revision.revision_id,),
        output_artifact_ids=(artifact.artifact_id,),
        acknowledged_artifact_ids=(artifact.artifact_id,),
    )

    with pytest.raises(LineageContractError, match="sample owner reference"):
        validate_delivery_seal_candidate(
            candidate,
            revisions={revision.revision_id: revision},
            artifacts={artifact.artifact_id: artifact},
            sample_metadata_rows=_sample_rows(),
            review_records=(),
        )


def test_delivery_seal_rejects_superseded_review_ref() -> None:
    artifact = _artifact("artifact-evidence", "attempt-1")
    original = _review_record()
    successor = ImmutableReviewRecordRef(
        review_record_id="review-2",
        schema_version="review-record-v1",
        content_sha256="e" * 64,
        evidence_artifact_ids=(artifact.artifact_id,),
        supersedes_review_record_id=original.review_record_id,
        supersession_reason="adjudicated",
    )
    revision = DeliveryRevisionRef(
        revision_id="revision-1",
        cell_key=DeliveryCellKey(
            "targeted",
            SampleIdentityRef("sample-1"),
            "target-1",
        ),
        producer_attempt_id="attempt-1",
        evidence_artifact_ids=(artifact.artifact_id,),
        review_record_refs=(original,),
        authority_approved=True,
    )
    candidate = DeliverySealCandidate(
        revision_ids=(revision.revision_id,),
        output_artifact_ids=(artifact.artifact_id,),
        acknowledged_artifact_ids=(artifact.artifact_id,),
    )

    with pytest.raises(LineageContractError, match="review owner reference"):
        validate_delivery_seal_candidate(
            candidate,
            revisions={revision.revision_id: revision},
            artifacts={artifact.artifact_id: artifact},
            sample_metadata_rows=_sample_rows(),
            review_records=(original, successor),
        )


def test_both_projection_keys_require_owner_backed_sample_refs() -> None:
    sample_ref = SampleIdentityRef("sample-1")

    assert DeliveryCellKey("targeted", sample_ref, "target-1").sample_key == (
        "sample-1"
    )
    assert DeliveryCellKey("untargeted", sample_ref, "matrix-row-1").sample_key == (
        "sample-1"
    )


def _identity_basis() -> ProcessingRequestIdentityBasis:
    return ProcessingRequestIdentityBasis(
        acquisition_sources=(
            AcquisitionSourceRef(
                "source-1",
                "thermo_raw",
                "controllerType=0 controllerNumber=1",
            ),
        ),
        normalized_query_sha256="a" * 64,
        method_constraints_sha256="b" * 64,
        algorithm_version="cid-dR-v1",
        schema_version="targeted-request-v1",
        contract_version="processing-lineage-v1",
    )


def _revision(
    revision_id: str,
    projection_identity: str,
    artifact_id: str,
) -> DeliveryRevisionRef:
    return DeliveryRevisionRef(
        revision_id=revision_id,
        cell_key=DeliveryCellKey(
            "targeted",
            SampleIdentityRef("sample-1"),
            projection_identity,
        ),
        producer_attempt_id="attempt-1",
        evidence_artifact_ids=(artifact_id,),
        authority_approved=True,
    )


def _review_record() -> ImmutableReviewRecordRef:
    return ImmutableReviewRecordRef(
        review_record_id="review-1",
        schema_version="review-record-v1",
        content_sha256="d" * 64,
        evidence_artifact_ids=("artifact-evidence",),
    )


def _sample_rows() -> tuple[SampleMetadata, ...]:
    return (
        SampleMetadata(
            sample_name="sample-1",
            raw_stem="sample_acquisition_1",
        ),
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
