from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor import review_records
from xic_extractor.config import Target
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    TargetListSnapshot,
    mint_target_list_snapshot,
    resolve_target_definition,
    target_list_snapshot_payload,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    IdentityContractError,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.review_actions_models import ReviewAction
from xic_extractor.review_records import (
    ImmutableReviewRecordRef,
    ResolvedReviewRecordRef,
    ReviewRecordReferenceError,
    ReviewRecordRegistrySnapshot,
    mint_review_record_reference,
    mint_review_record_registry_snapshot,
    resolve_current_review_record,
)
from xic_extractor.sample_metadata import (
    CanonicalSampleIdentity,
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    SampleMetadataError,
    SampleMetadataSnapshot,
    mint_canonical_sample_identity,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)

_REVIEW_REQUEST_ID = "xic.processing-request.v1:" + "a" * 64


def test_sample_owner_mints_canonical_identity_from_primary_sample_key() -> None:
    row = SampleMetadata(
        sample_name="Sample A",
        raw_stem="sample_a_acquisition",
        injection_order=3,
        sample_role="study_sample",
        batch_id="batch-1",
    )

    snapshot = _sample_snapshot(row)
    identity = mint_canonical_sample_identity(
        row,
        identity_namespace=snapshot.identity_namespace,
    )

    assert snapshot.snapshot_id == (
        "xic.sample-metadata-snapshot.v1:"
        "a5c133f3c37c7e2988747f99a1f0a491a1024795a806ef015b0129b951990787"
    )
    assert identity.sample_identity_id == (
        "xic.sample.v1:c6eb17e155ab6d78368c18428f6cc967e9b7762cde9d3d792e6c598f7c87b592"
    )
    assert identity.sample_key == "Sample A"
    assert identity.sample_metadata_schema_version == "sample_metadata_v1"
    by_key = resolve_canonical_sample_identity(snapshot, "Sample A")
    by_alias = resolve_canonical_sample_identity(snapshot, "sample_a_acquisition")

    assert by_key.identity == identity
    assert by_alias.identity == identity
    assert by_key.lookup_token == "Sample A"
    assert by_alias.lookup_token == "sample_a_acquisition"


def test_sample_identity_ignores_projection_metadata_but_not_sample_rename() -> None:
    original = SampleMetadata(sample_name="Sample A", raw_stem="raw_a")
    projection_changed = replace(
        original,
        injection_order=9,
        sample_role="qc",
        batch_id="batch-2",
        excluded=True,
        exclusion_reason="operator exclusion",
    )
    renamed = replace(original, sample_name="Sample A corrected")

    assert mint_canonical_sample_identity(
        projection_changed,
        identity_namespace="study:package-0-test",
    ) == (
        mint_canonical_sample_identity(
            original,
            identity_namespace="study:package-0-test",
        )
    )
    assert mint_canonical_sample_identity(
        renamed,
        identity_namespace="study:package-0-test",
    ) != (
        mint_canonical_sample_identity(
            original,
            identity_namespace="study:package-0-test",
        )
    )
    assert _sample_snapshot(projection_changed).snapshot_id != (
        _sample_snapshot(original).snapshot_id
    )


def test_sample_resolution_fails_closed_for_missing_ambiguous_or_stale_alias() -> None:
    original_snapshot = _sample_snapshot(
        SampleMetadata(sample_name="Sample A", raw_stem="shared")
    )
    original = resolve_canonical_sample_identity(original_snapshot, "shared")

    with pytest.raises(SampleMetadataError, match="not owned"):
        resolve_canonical_sample_identity(original_snapshot, "shared.raw")
    with pytest.raises(SampleMetadataError, match="not owned"):
        resolve_canonical_sample_identity(original_snapshot, "1")
    with pytest.raises(SampleMetadataError, match="alias collision|duplicate"):
        _sample_snapshot(
            SampleMetadata(sample_name="Sample A", raw_stem="shared"),
            SampleMetadata(sample_name="Sample B", raw_stem="shared"),
        )
    changed_snapshot = _sample_snapshot(
        SampleMetadata(sample_name="Sample B", raw_stem="shared")
    )
    with pytest.raises(SampleMetadataError, match="stale"):
        resolve_canonical_sample_identity(
            changed_snapshot,
            "shared",
            expected_identity=original.identity,
        )
    with pytest.raises(SampleMetadataError, match="owner-normalized"):
        resolve_canonical_sample_identity(original_snapshot, " shared")


def test_sample_identity_namespace_prevents_cross_owner_collisions() -> None:
    row = SampleMetadata(sample_name="Sample A", raw_stem="raw_a")

    assert mint_canonical_sample_identity(
        row,
        identity_namespace="study:first",
    ) != mint_canonical_sample_identity(
        row,
        identity_namespace="study:second",
    )


def test_sample_snapshot_is_order_neutral_and_content_bound() -> None:
    first = SampleMetadata(sample_name="Sample A", raw_stem="raw_a")
    second = SampleMetadata(sample_name="Sample B", raw_stem="raw_b")

    forward = _sample_snapshot(first, second)
    reverse = _sample_snapshot(second, first)
    mutated = _sample_snapshot(first, replace(second, raw_stem="raw_b_changed"))

    assert forward == reverse
    assert forward.snapshot_id != mutated.snapshot_id


def test_sample_resolution_rejects_non_snapshot_owner_input() -> None:
    with pytest.raises(SampleMetadataError, match="SampleMetadataSnapshot"):
        resolve_canonical_sample_identity(  # type: ignore[arg-type]
            (SampleMetadata(sample_name="Sample A", raw_stem="shared"),),
            "shared",
        )


def test_sample_identity_rejects_forged_owner_binding() -> None:
    identity = mint_canonical_sample_identity(
        SampleMetadata(sample_name="Sample A", raw_stem="raw_a"),
        identity_namespace="study:package-0-test",
    )

    with pytest.raises(SampleMetadataError, match="mismatches"):
        replace(identity, sample_key="Sample B")


def test_review_owner_mints_digest_and_typed_id_from_immutable_content() -> None:
    first = _review_record()
    same = _review_record()
    changed = _review_record(action=_review_action(comment="changed conclusion"))

    assert first == same
    assert first.content_sha256 == (
        "eae99977675b9f2b8c79918d9042d66cbfe323ab32be08c93b97abbac386acf5"
    )
    assert first.review_record_id == (
        "xic.review-record.v1:"
        "ba948d555d51b5cacf70bcb0f252e664efe0ec113ddf485a825c91c925c3fe83"
    )
    assert _review_snapshot(first).snapshot_id == (
        "xic.review-record-snapshot.v1:"
        "4023dbda2509e8d0247c7922844e9b6149990fb0a60d05a0f127778c36fe2932"
    )
    assert changed.review_record_id != first.review_record_id
    assert changed.content_sha256 != first.content_sha256


def test_review_artifact_reorder_is_neutral_but_substitution_invalidates() -> None:
    first = _review_record(evidence_artifact_ids=("artifact-b", "artifact-a"))
    reordered = _review_record(evidence_artifact_ids=("artifact-a", "artifact-b"))
    substituted = _review_record(evidence_artifact_ids=("artifact-a", "artifact-c"))

    assert first == reordered
    assert first.evidence_artifact_ids == ("artifact-a", "artifact-b")
    assert substituted.review_record_id != first.review_record_id


def test_review_subject_is_stable_while_definition_and_request_bind_exactly() -> None:
    original_target = _resolved_target(mz=250.0)
    changed_target = _resolved_target(mz=251.0)
    original = _review_record(target=original_target)
    successor = _review_record(
        action=_review_action(comment="reviewed changed definition"),
        target=changed_target,
        processing_request_id="xic.processing-request.v1:" + "b" * 64,
        supersedes=original,
        supersession_reason="target definition changed before a new request",
    )

    assert successor.review_subject_id == original.review_subject_id
    assert successor.target_key_id == original.target_key_id
    assert successor.target_definition_id != original.target_definition_id
    assert successor.processing_request_id != original.processing_request_id
    with pytest.raises(ReviewRecordReferenceError, match="target definition.*stale"):
        _resolve_review(original, target=changed_target)
    with pytest.raises(ReviewRecordReferenceError, match="processing request.*stale"):
        _resolve_review(
            original,
            processing_request_id="xic.processing-request.v1:" + "b" * 64,
        )


def test_review_owner_rejects_bare_label_rebinding_across_owner_references() -> None:
    action = _review_action()

    with pytest.raises(ReviewRecordReferenceError, match="sample_name mismatches"):
        _review_record(action=action, sample=_resolved_sample("Sample B"))
    with pytest.raises(ReviewRecordReferenceError, match="target_label mismatches"):
        _review_record(action=action, target=_resolved_target("Other"))
    with pytest.raises(ReviewRecordReferenceError, match="processing_request_id"):
        _review_record(action=action, processing_request_id="caller-run-id")


def test_review_record_rejects_caller_rebinding() -> None:
    record = _review_record()

    with pytest.raises(ReviewRecordReferenceError, match="mismatches"):
        replace(record, content_sha256="b" * 64)


def test_review_resolver_rejects_missing_ambiguous_stale_and_rebound_inputs() -> None:
    record = _review_record()
    snapshot = _review_snapshot(record)

    with pytest.raises(ReviewRecordReferenceError, match="missing"):
        resolve_current_review_record(
            snapshot,
            review_record_id="xic.review-record.v1:" + "f" * 64,
            expected_record=_review_action(),
            expected_sample=_resolved_sample(),
            expected_target=_resolved_target(),
            expected_processing_request_id=_REVIEW_REQUEST_ID,
            expected_evidence_artifact_ids=record.evidence_artifact_ids,
        )
    with pytest.raises(ReviewRecordReferenceError, match="ambiguous"):
        _review_snapshot(record, record)
    with pytest.raises(ReviewRecordReferenceError, match="digest.*stale"):
        _resolve_review(record, action=_review_action(comment="changed conclusion"))
    with pytest.raises(ReviewRecordReferenceError, match="binding mismatch"):
        _resolve_review(record, evidence_artifact_ids=("artifact-a", "artifact-c"))


def test_review_supersession_is_append_only_and_current_only() -> None:
    original = _review_record()
    successor = _review_record(
        action=_review_action(comment="adjudicated"),
        supersedes=original,
        supersession_reason="adjudicated after artifact review",
    )
    snapshot = _review_snapshot(original, successor)

    with pytest.raises(ReviewRecordReferenceError, match="superseded by"):
        _resolve_review(original, snapshot=snapshot)
    resolved = _resolve_review(
        successor,
        snapshot=snapshot,
        action=_review_action(comment="adjudicated"),
    )
    assert resolved.reference is successor
    assert successor.supersedes_review_record_id == original.review_record_id


def test_review_supersession_rejects_incomplete_or_invalid_graphs() -> None:
    original = _review_record()

    with pytest.raises(ReviewRecordReferenceError, match="requires.*reason"):
        _review_record(supersedes=original)
    with pytest.raises(ReviewRecordReferenceError, match="requires.*predecessor"):
        _review_record(supersession_reason="missing predecessor")

    orphan = _review_record(
        action=_review_action(comment="orphan fixture"),
        supersedes=original,
        supersession_reason="fixture omits predecessor from registry",
    )
    with pytest.raises(ReviewRecordReferenceError, match="orphan predecessor"):
        _review_snapshot(orphan)

    successor_a = _review_record(
        action=_review_action(comment="first"),
        supersedes=original,
        supersession_reason="first adjudication",
    )
    successor_b = _review_record(
        action=_review_action(comment="second"),
        supersedes=original,
        supersession_reason="second adjudication",
    )
    with pytest.raises(ReviewRecordReferenceError, match="multiple successors"):
        _review_snapshot(original, successor_a, successor_b)


def test_review_supersession_requires_the_same_owner_subject() -> None:
    original = _review_record()

    with pytest.raises(ReviewRecordReferenceError, match="same review subject"):
        _review_record(
            action=_review_action(sample_name="Sample B"),
            supersedes=original,
            supersession_reason="wrong subject",
        )


def test_review_snapshot_rejects_deserialized_cross_subject_successor() -> None:
    original = _review_record()
    other_subject = _review_record(action=_review_action(sample_name="Sample B"))
    reason = "forged cross-subject edge"
    forged_id = review_records._review_record_id(  # noqa: SLF001
        review_subject_id=other_subject.review_subject_id,
        canonical_sample_identity_id=other_subject.canonical_sample_identity_id,
        target_key_id=other_subject.target_key_id,
        target_definition_id=other_subject.target_definition_id,
        processing_request_id=other_subject.processing_request_id,
        schema_version=other_subject.schema_version,
        content_sha256=other_subject.content_sha256,
        evidence_artifact_ids=other_subject.evidence_artifact_ids,
        supersedes_review_record_id=original.review_record_id,
        supersession_reason=reason,
    )
    forged = ImmutableReviewRecordRef(
        review_record_id=forged_id,
        review_subject_id=other_subject.review_subject_id,
        canonical_sample_identity_id=other_subject.canonical_sample_identity_id,
        target_key_id=other_subject.target_key_id,
        target_definition_id=other_subject.target_definition_id,
        processing_request_id=other_subject.processing_request_id,
        schema_version=other_subject.schema_version,
        content_sha256=other_subject.content_sha256,
        evidence_artifact_ids=other_subject.evidence_artifact_ids,
        supersedes_review_record_id=original.review_record_id,
        supersession_reason=reason,
    )

    with pytest.raises(ReviewRecordReferenceError, match="same review subject"):
        _review_snapshot(original, forged)


def test_review_registry_snapshot_is_order_neutral_and_content_bound() -> None:
    first = _review_record()
    second = _review_record(action=_review_action(sample_name="Sample B"))

    forward = _review_snapshot(first, second)
    reverse = _review_snapshot(second, first)
    truncated = _review_snapshot(first)

    assert forward == reverse
    assert forward.snapshot_id != truncated.snapshot_id


def test_review_mint_accepts_only_owner_typed_and_validated_actions() -> None:
    with pytest.raises(ReviewRecordReferenceError, match="typed ReviewAction"):
        mint_review_record_reference(  # type: ignore[arg-type]
            record={"verdict": "accept"},
            sample=_resolved_sample(),
            target=_resolved_target(),
            processing_request_id=_REVIEW_REQUEST_ID,
            evidence_artifact_ids=("artifact-a",),
        )
    invalid = ReviewAction(
        sample_name="Sample A",
        target_label="5-mdC",
        action_type="anything",  # type: ignore[arg-type]
    )
    with pytest.raises(ReviewRecordReferenceError, match="valid review_action_v1"):
        mint_review_record_reference(
            record=invalid,
            sample=_resolved_sample(),
            target=_resolved_target(),
            processing_request_id=_REVIEW_REQUEST_ID,
            evidence_artifact_ids=("artifact-a",),
        )


def test_lineage_only_composes_owner_minted_references(tmp_path: Path) -> None:
    request = _request(tmp_path, payload=b"request-a")
    attempt = issue_processing_attempt(request)
    sample_snapshot = _sample_snapshot(
        SampleMetadata(sample_name="Sample A", raw_stem="request-a"),
        SampleMetadata(sample_name="Sample B", raw_stem="request-b"),
    )
    sample = resolve_canonical_sample_identity(
        sample_snapshot,
        "request-a",
    )
    target_snapshot = _target_snapshot()
    target = resolve_target_definition(target_snapshot, "5-mdC")
    review_ref = _review_record(
        sample=sample,
        target=target,
        processing_request_id=request.processing_request_id,
    )
    review_snapshot = _review_snapshot(review_ref)
    review = _resolve_review(
        review_ref,
        snapshot=review_snapshot,
        sample=sample,
        target=target,
        processing_request_id=request.processing_request_id,
    )

    references = compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=sample_snapshot,
        sample_resolutions=(sample,),
        target_snapshot=target_snapshot,
        review_target=target,
        review_snapshot=review_snapshot,
        review_record=review,
    )

    assert references.request is request
    assert references.attempt is attempt
    assert references.sample_snapshot is sample_snapshot
    assert references.sample_resolutions == (sample,)
    assert references.target_snapshot is target_snapshot
    assert references.review_target is target
    assert references.review_snapshot is review_snapshot
    assert references.review_record is review


def test_lineage_rejects_review_from_different_request_or_target(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, payload=b"request-a")
    sample_snapshot = _sample_snapshot(
        SampleMetadata(sample_name="Sample A", raw_stem="request-a")
    )
    sample = resolve_canonical_sample_identity(sample_snapshot, "request-a")
    target_snapshot = _target_snapshot()
    target = resolve_target_definition(target_snapshot, "5-mdC")
    wrong_target = _resolved_target(mz=251.0)
    wrong_request_ref = _review_record(
        sample=sample,
        target=target,
        processing_request_id="xic.processing-request.v1:" + "b" * 64,
    )
    wrong_request_snapshot = _review_snapshot(wrong_request_ref)
    wrong_request = _resolve_review(
        wrong_request_ref,
        snapshot=wrong_request_snapshot,
        sample=sample,
        target=target,
    )

    with pytest.raises(IdentityContractError, match="different processing request"):
        compose_processing_owner_references(
            request=request,
            attempt=issue_processing_attempt(request),
            sample_snapshot=sample_snapshot,
            sample_resolutions=(sample,),
            target_snapshot=target_snapshot,
            review_target=target,
            review_snapshot=wrong_request_snapshot,
            review_record=wrong_request,
        )

    changed_request = _request(
        tmp_path,
        payload=b"request-a",
        target_snapshot=wrong_target.snapshot,
    )
    wrong_target_ref = _review_record(
        sample=sample,
        target=target,
        processing_request_id=changed_request.processing_request_id,
    )
    wrong_target_snapshot = _review_snapshot(wrong_target_ref)
    wrong_target_review = _resolve_review(
        wrong_target_ref,
        snapshot=wrong_target_snapshot,
        sample=sample,
        target=target,
    )
    with pytest.raises(IdentityContractError, match="different target definition"):
        compose_processing_owner_references(
            request=changed_request,
            attempt=issue_processing_attempt(changed_request),
            sample_snapshot=sample_snapshot,
            sample_resolutions=(sample,),
            target_snapshot=wrong_target.snapshot,
            review_target=wrong_target,
            review_snapshot=wrong_target_snapshot,
            review_record=wrong_target_review,
        )


def test_lineage_composition_rejects_raw_or_cross_request_references(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, payload=b"request-a")
    other_request = _request(tmp_path, payload=b"request-b")
    attempt = issue_processing_attempt(other_request)
    sample_snapshot = _sample_snapshot(
        SampleMetadata(sample_name="Sample A", raw_stem="request-a"),
        SampleMetadata(sample_name="Sample B", raw_stem="request-b"),
    )
    sample = resolve_canonical_sample_identity(
        sample_snapshot,
        "request-a",
    )
    wrong_sample = resolve_canonical_sample_identity(
        sample_snapshot,
        "request-b",
    )
    review_record = _review_record()
    review_snapshot = _review_snapshot(review_record)
    review = _resolve_review(review_record, snapshot=review_snapshot)

    with pytest.raises(IdentityContractError, match="different processing request"):
        compose_processing_owner_references(
            request=request,
            attempt=attempt,
            sample_snapshot=sample_snapshot,
            sample_resolutions=(sample,),
            review_snapshot=review_snapshot,
            review_record=review,
        )
    with pytest.raises(IdentityContractError, match="cover.*acquisition binding"):
        compose_processing_owner_references(
            request=request,
            attempt=issue_processing_attempt(request),
            sample_snapshot=sample_snapshot,
            sample_resolutions=(wrong_sample,),
            review_snapshot=review_snapshot,
            review_record=review,
        )
    with pytest.raises(IdentityContractError, match="owner-resolved"):
        compose_processing_owner_references(
            request=request,
            attempt=issue_processing_attempt(request),
            sample_snapshot=sample_snapshot,
            sample_resolutions=("Sample A",),  # type: ignore[arg-type]
            review_snapshot=review_snapshot,
            review_record=review,
        )
    with pytest.raises(IdentityContractError, match="owner-resolved"):
        compose_processing_owner_references(
            request=request,
            attempt=issue_processing_attempt(request),
            sample_snapshot=sample_snapshot,
            sample_resolutions=(sample,),
            review_snapshot=review_snapshot,
            review_record=review_record,  # type: ignore[arg-type]
        )


def test_lineage_rejects_resolutions_mixed_across_owner_snapshots(
    tmp_path: Path,
) -> None:
    request = _request_many(tmp_path, payloads=(b"request-a", b"request-b"))
    complete = _sample_snapshot(
        SampleMetadata(sample_name="Sample A", raw_stem="request-a"),
        SampleMetadata(sample_name="Sample B", raw_stem="request-b"),
    )
    truncated = _sample_snapshot(
        SampleMetadata(sample_name="Sample B", raw_stem="request-b")
    )
    first = resolve_canonical_sample_identity(complete, "request-a")
    mixed_second = resolve_canonical_sample_identity(truncated, "request-b")

    with pytest.raises(IdentityContractError, match="same sample metadata snapshot"):
        compose_processing_owner_references(
            request=request,
            attempt=issue_processing_attempt(request),
            sample_snapshot=complete,
            sample_resolutions=(first, mixed_second),
        )


def _review_record(
    *,
    action: ReviewAction | None = None,
    sample: ResolvedCanonicalSampleIdentity | None = None,
    target: ResolvedTargetDefinition | None = None,
    processing_request_id: str = _REVIEW_REQUEST_ID,
    evidence_artifact_ids: tuple[str, ...] = ("artifact-a", "artifact-b"),
    supersedes: ImmutableReviewRecordRef | None = None,
    supersession_reason: str = "",
) -> ImmutableReviewRecordRef:
    review_action = action or _review_action()
    return mint_review_record_reference(
        record=review_action,
        sample=sample or _resolved_sample(review_action.sample_name),
        target=target or _resolved_target(review_action.target_label),
        processing_request_id=processing_request_id,
        evidence_artifact_ids=evidence_artifact_ids,
        supersedes=supersedes,
        supersession_reason=supersession_reason,
    )


def _resolve_review(
    record: ImmutableReviewRecordRef,
    *,
    snapshot: ReviewRecordRegistrySnapshot | None = None,
    action: ReviewAction | None = None,
    sample: ResolvedCanonicalSampleIdentity | None = None,
    target: ResolvedTargetDefinition | None = None,
    processing_request_id: str | None = None,
    evidence_artifact_ids: tuple[str, ...] | None = None,
) -> ResolvedReviewRecordRef:
    expected_action = action or _review_action()
    return resolve_current_review_record(
        snapshot or _review_snapshot(record),
        review_record_id=record.review_record_id,
        expected_record=expected_action,
        expected_sample=sample or _resolved_sample(expected_action.sample_name),
        expected_target=target or _resolved_target(expected_action.target_label),
        expected_processing_request_id=(
            processing_request_id or record.processing_request_id
        ),
        expected_evidence_artifact_ids=(
            evidence_artifact_ids or record.evidence_artifact_ids
        ),
    )


def _review_action(
    *,
    sample_name: str = "Sample A",
    target_label: str = "5-mdC",
    comment: str = "clean",
) -> ReviewAction:
    return ReviewAction(
        sample_name=sample_name,
        target_label=target_label,
        action_type="accept_current",
        comment=comment,
        reviewer="analyst",
        reviewed_at="2026-07-26T10:00:00Z",
    )


def _request(
    tmp_path: Path,
    *,
    payload: bytes,
    target_snapshot: TargetListSnapshot | None = None,
):
    return _request_many(
        tmp_path,
        payloads=(payload,),
        target_snapshot=target_snapshot,
    )


def _request_many(
    tmp_path: Path,
    *,
    payloads: tuple[bytes, ...],
    target_snapshot: TargetListSnapshot | None = None,
):
    sources = []
    bindings = []
    for injection_order, payload in enumerate(payloads, start=1):
        source_path = tmp_path / f"{payload.decode('ascii')}.raw"
        source_path.write_bytes(payload)
        source = build_acquisition_source_identity(
            source_path,
            acquisition_format="thermo_raw",
            observation_namespace="thermo_raw_file_reader:Device.MS:1",
        )
        sources.append(source)
        bindings.append(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key=source_path.stem,
                injection_order=injection_order,
            )
        )
    return build_processing_request_identity(
        acquisition_sources=tuple(sources),
        acquisition_bindings=tuple(bindings),
        scientific_input_schema_version="owner-reference-fixture-v1",
        processing_algorithm_version="owner-reference-fixture-v1",
        processing_contract_version="owner-reference-fixture-v1",
        scientific_inputs={
            "fixture": [payload.decode("ascii") for payload in payloads],
            "target_list_snapshot": target_list_snapshot_payload(
                target_snapshot or _target_snapshot()
            ),
        },
    )


def _sample_snapshot(
    *rows: SampleMetadata,
    identity_namespace: str = "study:package-0-test",
) -> SampleMetadataSnapshot:
    return mint_sample_metadata_snapshot(
        rows,
        identity_namespace=identity_namespace,
    )


def _resolved_sample(sample_name: str = "Sample A") -> ResolvedCanonicalSampleIdentity:
    snapshot = _sample_snapshot(
        SampleMetadata(sample_name=sample_name, raw_stem=sample_name)
    )
    return resolve_canonical_sample_identity(snapshot, sample_name)


def _target_snapshot(
    *,
    target_label: str = "5-mdC",
    mz: float = 250.0,
) -> TargetListSnapshot:
    return mint_target_list_snapshot(
        (
            Target(
                label=target_label,
                mz=mz,
                rt_min=1.0,
                rt_max=3.0,
                ppm_tol=10.0,
                neutral_loss_da=116.0,
                nl_ppm_warn=20.0,
                nl_ppm_max=40.0,
                is_istd=False,
                istd_pair="",
            ),
        ),
        identity_namespace="target-list:package-0-test",
    )


def _resolved_target(
    target_label: str = "5-mdC",
    *,
    mz: float = 250.0,
) -> ResolvedTargetDefinition:
    snapshot = _target_snapshot(target_label=target_label, mz=mz)
    return resolve_target_definition(snapshot, target_label)


def _review_snapshot(
    *records: ImmutableReviewRecordRef,
) -> ReviewRecordRegistrySnapshot:
    return mint_review_record_registry_snapshot(records)


def test_owner_reference_types_are_frozen() -> None:
    sample_snapshot = _sample_snapshot(
        SampleMetadata(sample_name="Sample A", raw_stem="raw_a")
    )
    sample = resolve_canonical_sample_identity(
        sample_snapshot,
        "raw_a",
    )
    review_record = _review_record()
    review = _resolve_review(
        review_record,
        snapshot=_review_snapshot(review_record),
    )

    assert isinstance(sample, ResolvedCanonicalSampleIdentity)
    assert isinstance(sample.identity, CanonicalSampleIdentity)
    assert isinstance(review, ResolvedReviewRecordRef)
    assert isinstance(review.reference, ImmutableReviewRecordRef)
