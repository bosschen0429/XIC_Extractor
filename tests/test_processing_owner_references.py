import pytest

from xic_extractor.review_actions_models import (
    ImmutableReviewRecordRef,
    ReviewRecordReferenceError,
    resolve_current_review_record,
)
from xic_extractor.sample_metadata import (
    SampleIdentityRef,
    SampleMetadata,
    SampleMetadataError,
    resolve_sample_identity,
)


def test_sample_identity_resolves_canonical_key_from_owner_aliases() -> None:
    rows = (
        SampleMetadata(sample_name="Sample A", raw_stem="sample_a_acquisition"),
    )

    assert resolve_sample_identity(rows, "Sample A") == SampleIdentityRef("Sample A")
    assert resolve_sample_identity(rows, "sample_a_acquisition") == (
        SampleIdentityRef("Sample A")
    )


def test_sample_identity_does_not_infer_from_filename_or_row_order() -> None:
    rows = (SampleMetadata(sample_name="Sample A", raw_stem="sample_a"),)

    with pytest.raises(SampleMetadataError, match="not owned"):
        resolve_sample_identity(rows, "sample_a.raw")
    with pytest.raises(SampleMetadataError, match="not owned"):
        resolve_sample_identity(rows, "1")


def test_sample_identity_fails_closed_when_owner_rows_are_ambiguous() -> None:
    rows = (
        SampleMetadata(sample_name="Sample A", raw_stem="shared"),
        SampleMetadata(sample_name="Sample B", raw_stem="shared"),
    )

    with pytest.raises(SampleMetadataError, match="ambiguous"):
        resolve_sample_identity(rows, "shared")


def test_review_record_reference_binds_full_digest_and_exact_artifacts() -> None:
    record = _record("review-1")

    assert (
        resolve_current_review_record(
            (record,),
            review_record_id="review-1",
            expected_content_sha256="a" * 64,
            expected_evidence_artifact_ids=("artifact-1", "artifact-2"),
        )
        is record
    )


def test_review_record_reference_rejects_stale_or_rebound_evidence() -> None:
    record = _record("review-1")

    with pytest.raises(ReviewRecordReferenceError, match="digest is stale"):
        resolve_current_review_record(
            (record,),
            review_record_id="review-1",
            expected_content_sha256="b" * 64,
            expected_evidence_artifact_ids=record.evidence_artifact_ids,
        )
    with pytest.raises(ReviewRecordReferenceError, match="binding mismatch"):
        resolve_current_review_record(
            (record,),
            review_record_id="review-1",
            expected_content_sha256=record.content_sha256,
            expected_evidence_artifact_ids=("artifact-2", "artifact-1"),
        )


def test_review_record_reference_rejects_superseded_record() -> None:
    original = _record("review-1")
    successor = ImmutableReviewRecordRef(
        review_record_id="review-2",
        schema_version="review_record_v1",
        content_sha256="b" * 64,
        evidence_artifact_ids=("artifact-1", "artifact-2"),
        supersedes_review_record_id="review-1",
        supersession_reason="adjudicated after artifact review",
    )

    with pytest.raises(ReviewRecordReferenceError, match="superseded by review-2"):
        resolve_current_review_record(
            (original, successor),
            review_record_id="review-1",
            expected_content_sha256=original.content_sha256,
            expected_evidence_artifact_ids=original.evidence_artifact_ids,
        )


def test_review_record_reference_requires_complete_supersession_edge() -> None:
    with pytest.raises(ReviewRecordReferenceError, match="requires both"):
        ImmutableReviewRecordRef(
            review_record_id="review-2",
            schema_version="review_record_v1",
            content_sha256="b" * 64,
            evidence_artifact_ids=("artifact-1",),
            supersedes_review_record_id="review-1",
        )


def _record(record_id: str) -> ImmutableReviewRecordRef:
    return ImmutableReviewRecordRef(
        review_record_id=record_id,
        schema_version="review_record_v1",
        content_sha256="a" * 64,
        evidence_artifact_ids=("artifact-1", "artifact-2"),
    )
