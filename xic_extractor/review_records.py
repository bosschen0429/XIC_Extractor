"""Immutable review-record references owned by the review-roundtrip contract."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.review_actions import parse_review_actions
from xic_extractor.review_actions_models import ReviewAction, ReviewActionError
from xic_extractor.review_actions_schema import REVIEW_ACTION_SCHEMA_VERSION
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

_REVIEW_RECORD_ID_PREFIX = "xic.review-record.v1:"
_REVIEW_RECORD_SNAPSHOT_PREFIX = "xic.review-record-snapshot.v1:"
_REVIEW_SUBJECT_ID_PREFIX = "xic.review-subject.v1:"
_CANONICAL_SAMPLE_ID_PREFIX = "xic.sample.v1:"
_TARGET_KEY_ID_PREFIX = "xic.target-key.v1:"
_TARGET_DEFINITION_ID_PREFIX = "xic.target-definition.v1:"
_PROCESSING_REQUEST_ID_PREFIX = "xic.processing-request.v1:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReviewRecordReferenceError(ValueError):
    """Raised when retained review evidence is not an immutable current record."""


@dataclass(frozen=True, slots=True)
class ImmutableReviewRecordRef:
    """Owner-minted reference to one immutable typed review conclusion."""

    review_record_id: str
    review_subject_id: str
    canonical_sample_identity_id: str
    target_key_id: str
    target_definition_id: str
    processing_request_id: str
    schema_version: str
    content_sha256: str
    evidence_artifact_ids: tuple[str, ...]
    supersedes_review_record_id: str = ""
    supersession_reason: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != REVIEW_ACTION_SCHEMA_VERSION:
            raise ReviewRecordReferenceError(
                "schema_version must be the owner-supported review_action_v1"
            )
        schema_version = self.schema_version
        subject_id = _validate_typed_digest_id(
            self.review_subject_id,
            prefix=_REVIEW_SUBJECT_ID_PREFIX,
            field="review_subject_id",
        )
        sample_id = _validate_typed_digest_id(
            self.canonical_sample_identity_id,
            prefix=_CANONICAL_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
        target_key_id = _validate_typed_digest_id(
            self.target_key_id,
            prefix=_TARGET_KEY_ID_PREFIX,
            field="target_key_id",
        )
        target_definition_id = _validate_typed_digest_id(
            self.target_definition_id,
            prefix=_TARGET_DEFINITION_ID_PREFIX,
            field="target_definition_id",
        )
        processing_request_id = _validate_typed_digest_id(
            self.processing_request_id,
            prefix=_PROCESSING_REQUEST_ID_PREFIX,
            field="processing_request_id",
        )
        if subject_id != _review_subject_id(sample_id, target_key_id):
            raise ReviewRecordReferenceError(
                "review_subject_id mismatches owner-backed sample and target key"
            )
        content_sha256 = _validate_sha256(
            self.content_sha256,
            field="content_sha256",
        )
        artifact_ids = _canonical_artifact_ids(self.evidence_artifact_ids)
        if artifact_ids != self.evidence_artifact_ids:
            raise ReviewRecordReferenceError(
                "evidence_artifact_ids must be unique and canonical-sorted"
            )
        predecessor_id = self.supersedes_review_record_id
        reason = self.supersession_reason
        if predecessor_id:
            _validate_review_record_id(
                predecessor_id,
                field="supersedes_review_record_id",
            )
            _required_text(reason, field="supersession_reason")
        elif reason:
            raise ReviewRecordReferenceError(
                "supersession_reason requires an owner-backed predecessor"
            )
        expected_id = _review_record_id(
            review_subject_id=subject_id,
            canonical_sample_identity_id=sample_id,
            target_key_id=target_key_id,
            target_definition_id=target_definition_id,
            processing_request_id=processing_request_id,
            schema_version=schema_version,
            content_sha256=content_sha256,
            evidence_artifact_ids=artifact_ids,
            supersedes_review_record_id=predecessor_id,
            supersession_reason=reason,
        )
        if self.review_record_id != expected_id:
            raise ReviewRecordReferenceError(
                "review_record_id mismatches immutable review-record content"
            )
        if predecessor_id == self.review_record_id:
            raise ReviewRecordReferenceError("a review record cannot supersede itself")


@dataclass(frozen=True, slots=True)
class ReviewRecordRegistrySnapshot:
    """Content-digested owner snapshot of one complete in-memory registry."""

    snapshot_id: str
    content_sha256: str
    records: tuple[ImmutableReviewRecordRef, ...]

    def __post_init__(self) -> None:
        content_sha256 = _validate_sha256(
            self.content_sha256,
            field="content_sha256",
        )
        if any(
            not isinstance(record, ImmutableReviewRecordRef) for record in self.records
        ):
            raise ReviewRecordReferenceError(
                "registry snapshot must contain owner-minted review records"
            )
        canonical_records = tuple(
            sorted(self.records, key=lambda record: record.review_record_id)
        )
        if canonical_records != self.records:
            raise ReviewRecordReferenceError(
                "review registry snapshot records must be canonical-sorted"
            )
        _validate_review_record_graph(canonical_records)
        expected_content_sha256 = _review_registry_content_sha256(canonical_records)
        if content_sha256 != expected_content_sha256:
            raise ReviewRecordReferenceError(
                "review registry snapshot digest mismatches owner records"
            )
        expected_id = f"{_REVIEW_RECORD_SNAPSHOT_PREFIX}{content_sha256}"
        if self.snapshot_id != expected_id:
            raise ReviewRecordReferenceError(
                "review registry snapshot ID mismatches owner content"
            )


@dataclass(frozen=True, slots=True)
class ResolvedReviewRecordRef:
    """Owner-validated current record within one immutable registry snapshot."""

    reference: ImmutableReviewRecordRef
    registry_snapshot: ReviewRecordRegistrySnapshot
    expected_record: ReviewAction
    expected_sample: ResolvedCanonicalSampleIdentity
    expected_target: ResolvedTargetDefinition
    expected_processing_request_id: str
    expected_evidence_artifact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.reference, ImmutableReviewRecordRef):
            raise ReviewRecordReferenceError(
                "reference must be an owner-minted review record"
            )
        if not isinstance(self.registry_snapshot, ReviewRecordRegistrySnapshot):
            raise ReviewRecordReferenceError("registry_snapshot must be owner-minted")
        content_sha256 = _review_action_content_sha256(self.expected_record)
        (
            sample_id,
            target_key_id,
            target_definition_id,
            processing_request_id,
        ) = _review_owner_binding_ids(
            self.expected_record,
            sample=self.expected_sample,
            target=self.expected_target,
            processing_request_id=self.expected_processing_request_id,
        )
        subject_id = _review_subject_id(sample_id, target_key_id)
        artifact_ids = _canonical_artifact_ids(self.expected_evidence_artifact_ids)
        if artifact_ids != self.expected_evidence_artifact_ids:
            raise ReviewRecordReferenceError(
                "expected_evidence_artifact_ids must be canonical-sorted"
            )
        resolved = _resolve_current_reference(
            self.registry_snapshot.records,
            review_record_id=self.reference.review_record_id,
            expected_review_subject_id=subject_id,
            expected_canonical_sample_identity_id=sample_id,
            expected_target_key_id=target_key_id,
            expected_target_definition_id=target_definition_id,
            expected_processing_request_id=processing_request_id,
            expected_content_sha256=content_sha256,
            expected_evidence_artifact_ids=artifact_ids,
        )
        if resolved != self.reference:
            raise ReviewRecordReferenceError(
                "resolved review record does not match the registry snapshot"
            )


def mint_review_record_reference(
    *,
    record: ReviewAction,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
    processing_request_id: str,
    evidence_artifact_ids: Sequence[str],
    supersedes: ImmutableReviewRecordRef | None = None,
    supersession_reason: str = "",
) -> ImmutableReviewRecordRef:
    """Mint a content-bound review reference without storing or publishing it."""

    content_sha256 = _review_action_content_sha256(record)
    (
        sample_id,
        target_key_id,
        target_definition_id,
        processing_request_id,
    ) = _review_owner_binding_ids(
        record,
        sample=sample,
        target=target,
        processing_request_id=processing_request_id,
    )
    subject_id = _review_subject_id(sample_id, target_key_id)
    schema_version = REVIEW_ACTION_SCHEMA_VERSION
    artifact_ids = _canonical_artifact_ids(evidence_artifact_ids)
    if supersedes is None:
        if supersession_reason:
            raise ReviewRecordReferenceError(
                "supersession_reason requires an owner-backed predecessor"
            )
        predecessor_id = ""
        reason = ""
    else:
        if not isinstance(supersedes, ImmutableReviewRecordRef):
            raise ReviewRecordReferenceError(
                "supersedes must be an owner-minted review record"
            )
        if not supersession_reason:
            raise ReviewRecordReferenceError("supersession requires a non-empty reason")
        reason = _required_text(
            supersession_reason,
            field="supersession_reason",
        )
        predecessor_id = supersedes.review_record_id
        if supersedes.review_subject_id != subject_id:
            raise ReviewRecordReferenceError(
                "supersession requires the same review subject"
            )
    record_id = _review_record_id(
        review_subject_id=subject_id,
        canonical_sample_identity_id=sample_id,
        target_key_id=target_key_id,
        target_definition_id=target_definition_id,
        processing_request_id=processing_request_id,
        schema_version=schema_version,
        content_sha256=content_sha256,
        evidence_artifact_ids=artifact_ids,
        supersedes_review_record_id=predecessor_id,
        supersession_reason=reason,
    )
    return ImmutableReviewRecordRef(
        review_record_id=record_id,
        review_subject_id=subject_id,
        canonical_sample_identity_id=sample_id,
        target_key_id=target_key_id,
        target_definition_id=target_definition_id,
        processing_request_id=processing_request_id,
        schema_version=schema_version,
        content_sha256=content_sha256,
        evidence_artifact_ids=artifact_ids,
        supersedes_review_record_id=predecessor_id,
        supersession_reason=reason,
    )


def mint_review_record_registry_snapshot(
    records: Sequence[ImmutableReviewRecordRef],
) -> ReviewRecordRegistrySnapshot:
    """Mint an order-neutral immutable snapshot of the supplied owner registry."""

    if isinstance(records, (str, bytes)):
        raise ReviewRecordReferenceError(
            "review registry snapshot requires review records"
        )
    try:
        canonical_records = tuple(
            sorted(records, key=lambda record: record.review_record_id)
        )
    except (AttributeError, TypeError) as exc:
        raise ReviewRecordReferenceError(
            "review registry snapshot requires owner-minted review records"
        ) from exc
    if not canonical_records:
        raise ReviewRecordReferenceError(
            "review registry snapshot must contain records"
        )
    if any(
        not isinstance(record, ImmutableReviewRecordRef) for record in canonical_records
    ):
        raise ReviewRecordReferenceError(
            "review registry snapshot requires owner-minted review records"
        )
    _validate_review_record_graph(canonical_records)
    content_sha256 = _review_registry_content_sha256(canonical_records)
    return ReviewRecordRegistrySnapshot(
        snapshot_id=f"{_REVIEW_RECORD_SNAPSHOT_PREFIX}{content_sha256}",
        content_sha256=content_sha256,
        records=canonical_records,
    )


def resolve_current_review_record(
    snapshot: ReviewRecordRegistrySnapshot,
    *,
    review_record_id: str,
    expected_record: ReviewAction,
    expected_sample: ResolvedCanonicalSampleIdentity,
    expected_target: ResolvedTargetDefinition,
    expected_processing_request_id: str,
    expected_evidence_artifact_ids: Sequence[str],
) -> ResolvedReviewRecordRef:
    """Resolve one current record and reject stale, rebound, or superseded input."""

    if not isinstance(snapshot, ReviewRecordRegistrySnapshot):
        raise ReviewRecordReferenceError(
            "review resolution requires an owner-minted registry snapshot"
        )
    _validate_review_record_id(review_record_id, field="review_record_id")
    expected_content_sha256 = _review_action_content_sha256(expected_record)
    (
        expected_sample_id,
        expected_target_key_id,
        expected_target_definition_id,
        expected_processing_request_id,
    ) = _review_owner_binding_ids(
        expected_record,
        sample=expected_sample,
        target=expected_target,
        processing_request_id=expected_processing_request_id,
    )
    expected_subject_id = _review_subject_id(
        expected_sample_id,
        expected_target_key_id,
    )
    expected_artifact_ids = _canonical_artifact_ids(expected_evidence_artifact_ids)
    record = _resolve_current_reference(
        snapshot.records,
        review_record_id=review_record_id,
        expected_review_subject_id=expected_subject_id,
        expected_canonical_sample_identity_id=expected_sample_id,
        expected_target_key_id=expected_target_key_id,
        expected_target_definition_id=expected_target_definition_id,
        expected_processing_request_id=expected_processing_request_id,
        expected_content_sha256=expected_content_sha256,
        expected_evidence_artifact_ids=expected_artifact_ids,
    )
    return ResolvedReviewRecordRef(
        reference=record,
        registry_snapshot=snapshot,
        expected_record=expected_record,
        expected_sample=expected_sample,
        expected_target=expected_target,
        expected_processing_request_id=expected_processing_request_id,
        expected_evidence_artifact_ids=expected_artifact_ids,
    )


def _resolve_current_reference(
    records: tuple[ImmutableReviewRecordRef, ...],
    *,
    review_record_id: str,
    expected_review_subject_id: str,
    expected_canonical_sample_identity_id: str,
    expected_target_key_id: str,
    expected_target_definition_id: str,
    expected_processing_request_id: str,
    expected_content_sha256: str,
    expected_evidence_artifact_ids: tuple[str, ...],
) -> ImmutableReviewRecordRef:
    _validate_review_record_graph(records)
    matches = [
        record for record in records if record.review_record_id == review_record_id
    ]
    if not matches:
        raise ReviewRecordReferenceError(
            f"review record {review_record_id!r} is missing"
        )
    if len(matches) != 1:
        raise ReviewRecordReferenceError(
            f"review record {review_record_id!r} is ambiguous"
        )
    record = matches[0]
    successors = sorted(
        successor.review_record_id
        for successor in records
        if successor.supersedes_review_record_id == review_record_id
    )
    if successors:
        raise ReviewRecordReferenceError(
            f"review record {review_record_id!r} is superseded by "
            f"{', '.join(successors)}"
        )
    if record.review_subject_id != expected_review_subject_id:
        raise ReviewRecordReferenceError(
            f"review record {review_record_id!r} subject binding is stale"
        )
    if record.canonical_sample_identity_id != expected_canonical_sample_identity_id:
        raise ReviewRecordReferenceError(
            f"review record {review_record_id!r} sample binding is stale"
        )
    if record.target_key_id != expected_target_key_id:
        raise ReviewRecordReferenceError(
            f"review record {review_record_id!r} target key binding is stale"
        )
    if record.target_definition_id != expected_target_definition_id:
        raise ReviewRecordReferenceError(
            f"review record {review_record_id!r} target definition binding is stale"
        )
    if record.processing_request_id != expected_processing_request_id:
        raise ReviewRecordReferenceError(
            f"review record {review_record_id!r} processing request binding is stale"
        )
    if record.content_sha256 != expected_content_sha256:
        raise ReviewRecordReferenceError(
            f"review record {review_record_id!r} content digest is stale"
        )
    if record.evidence_artifact_ids != expected_evidence_artifact_ids:
        raise ReviewRecordReferenceError(
            f"review record {review_record_id!r} artifact binding mismatch"
        )
    return record


def _validate_review_record_graph(
    records: tuple[ImmutableReviewRecordRef, ...],
) -> None:
    by_id: dict[str, ImmutableReviewRecordRef] = {}
    for record in records:
        if record.review_record_id in by_id:
            raise ReviewRecordReferenceError(
                f"review record {record.review_record_id!r} is ambiguous"
            )
        by_id[record.review_record_id] = record

    successor_by_predecessor: dict[str, str] = {}
    for record in records:
        predecessor_id = record.supersedes_review_record_id
        if not predecessor_id:
            continue
        if predecessor_id not in by_id:
            raise ReviewRecordReferenceError(
                f"review record {record.review_record_id!r} has orphan predecessor "
                f"{predecessor_id!r}"
            )
        predecessor = by_id[predecessor_id]
        if predecessor.review_subject_id != record.review_subject_id:
            raise ReviewRecordReferenceError(
                "review record supersession requires the same review subject"
            )
        if predecessor_id in successor_by_predecessor:
            raise ReviewRecordReferenceError(
                f"review record {predecessor_id!r} has multiple successors"
            )
        successor_by_predecessor[predecessor_id] = record.review_record_id

    for start_id in by_id:
        seen: set[str] = set()
        current_id = start_id
        while current_id in successor_by_predecessor:
            if current_id in seen:
                raise ReviewRecordReferenceError(
                    "review record supersession graph contains a cycle"
                )
            seen.add(current_id)
            current_id = successor_by_predecessor[current_id]


def _review_owner_binding_ids(
    record: ReviewAction,
    *,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
    processing_request_id: str,
) -> tuple[str, str, str, str]:
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise ReviewRecordReferenceError(
            "sample must be an owner-resolved canonical sample identity"
        )
    if not isinstance(target, ResolvedTargetDefinition):
        raise ReviewRecordReferenceError(
            "target must be an owner-resolved target definition"
        )
    request_id = _validate_typed_digest_id(
        processing_request_id,
        prefix=_PROCESSING_REQUEST_ID_PREFIX,
        field="processing_request_id",
    )
    if record.sample_name != sample.metadata.sample_name:
        raise ReviewRecordReferenceError(
            "review action sample_name mismatches canonical sample identity"
        )
    if record.target_label != target.definition.target_key.target_label:
        raise ReviewRecordReferenceError(
            "review action target_label mismatches owner target key"
        )
    return (
        sample.identity.sample_identity_id,
        target.definition.target_key.target_key_id,
        target.definition.target_definition_id,
        request_id,
    )


def _review_action_content_sha256(record: ReviewAction) -> str:
    if not isinstance(record, ReviewAction):
        raise ReviewRecordReferenceError("record must be an owner-typed ReviewAction")
    content: dict[str, object] = {
        "schema_version": REVIEW_ACTION_SCHEMA_VERSION,
        "sample_name": record.sample_name,
        "target_label": record.target_label,
        "action_type": record.action_type,
        "candidate_id": record.candidate_id,
        "boundary_id": record.boundary_id,
        "rt_left_min": record.rt_left_min,
        "rt_apex_min": record.rt_apex_min,
        "rt_right_min": record.rt_right_min,
        "comment": record.comment,
        "reviewer": record.reviewer,
        "reviewed_at": record.reviewed_at,
        "expected_diff_required": record.expected_diff_required,
    }
    parse_row = {
        **content,
        "rt_left_min": "" if record.rt_left_min is None else record.rt_left_min,
        "rt_apex_min": "" if record.rt_apex_min is None else record.rt_apex_min,
        "rt_right_min": "" if record.rt_right_min is None else record.rt_right_min,
        "expected_diff_required": (
            "TRUE" if record.expected_diff_required else "FALSE"
        ),
    }
    try:
        parsed = parse_review_actions(
            (parse_row,),
            source="<immutable-review-record>",
        )
    except ReviewActionError as exc:
        raise ReviewRecordReferenceError(
            "record must satisfy the valid review_action_v1 contract"
        ) from exc
    if len(parsed) != 1 or parsed[0] != record:
        raise ReviewRecordReferenceError(
            "record must be owner-normalized valid review_action_v1 content"
        )
    return _content_sha256(content)


def _content_sha256(content: Mapping[str, object]) -> str:
    if not isinstance(content, Mapping):
        raise ReviewRecordReferenceError("content must be a JSON object")
    if any(not isinstance(key, str) for key in content):
        raise ReviewRecordReferenceError("content keys must be strings")
    payload = _canonical_json(dict(content), field="content")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _review_record_id(
    *,
    review_subject_id: str,
    canonical_sample_identity_id: str,
    target_key_id: str,
    target_definition_id: str,
    processing_request_id: str,
    schema_version: str,
    content_sha256: str,
    evidence_artifact_ids: tuple[str, ...],
    supersedes_review_record_id: str,
    supersession_reason: str,
) -> str:
    basis = {
        "schema": "immutable_review_record_reference_v1",
        "review_subject_id": review_subject_id,
        "canonical_sample_identity_id": canonical_sample_identity_id,
        "target_key_id": target_key_id,
        "target_definition_id": target_definition_id,
        "processing_request_id": processing_request_id,
        "record_schema_version": schema_version,
        "content_sha256": content_sha256,
        "evidence_artifact_ids": list(evidence_artifact_ids),
        "supersedes_review_record_id": supersedes_review_record_id,
        "supersession_reason": supersession_reason,
    }
    digest = hashlib.sha256(
        _canonical_json(basis, field="review record identity").encode("utf-8")
    ).hexdigest()
    return f"{_REVIEW_RECORD_ID_PREFIX}{digest}"


def _review_subject_id(
    canonical_sample_identity_id: str,
    target_key_id: str,
) -> str:
    sample_id = _validate_typed_digest_id(
        canonical_sample_identity_id,
        prefix=_CANONICAL_SAMPLE_ID_PREFIX,
        field="canonical_sample_identity_id",
    )
    stable_target_key_id = _validate_typed_digest_id(
        target_key_id,
        prefix=_TARGET_KEY_ID_PREFIX,
        field="target_key_id",
    )
    digest = hashlib.sha256(
        _canonical_json(
            {
                "schema": "review_subject_identity_v1",
                "review_action_schema_version": REVIEW_ACTION_SCHEMA_VERSION,
                "canonical_sample_identity_id": sample_id,
                "target_key_id": stable_target_key_id,
            },
            field="review subject identity",
        ).encode("utf-8")
    ).hexdigest()
    return f"{_REVIEW_SUBJECT_ID_PREFIX}{digest}"


def _review_registry_content_sha256(
    records: tuple[ImmutableReviewRecordRef, ...],
) -> str:
    payload = {
        "schema": "review_record_registry_snapshot_v1",
        "records": [
            {
                "review_record_id": record.review_record_id,
                "review_subject_id": record.review_subject_id,
                "canonical_sample_identity_id": (record.canonical_sample_identity_id),
                "target_key_id": record.target_key_id,
                "target_definition_id": record.target_definition_id,
                "processing_request_id": record.processing_request_id,
                "schema_version": record.schema_version,
                "content_sha256": record.content_sha256,
                "evidence_artifact_ids": list(record.evidence_artifact_ids),
                "supersedes_review_record_id": (record.supersedes_review_record_id),
                "supersession_reason": record.supersession_reason,
            }
            for record in records
        ],
    }
    return hashlib.sha256(
        _canonical_json(payload, field="review registry snapshot").encode("utf-8")
    ).hexdigest()


def _canonical_artifact_ids(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ReviewRecordReferenceError(
            "evidence_artifact_ids must be a sequence of immutable IDs"
        )
    try:
        items = tuple(values)
    except TypeError as exc:
        raise ReviewRecordReferenceError(
            "evidence_artifact_ids must be a sequence of immutable IDs"
        ) from exc
    if not items:
        raise ReviewRecordReferenceError(
            "evidence_artifact_ids must bind at least one immutable artifact"
        )
    for item in items:
        _required_text(item, field="evidence_artifact_id")
        if item != item.strip():
            raise ReviewRecordReferenceError(
                "evidence_artifact_ids must be owner-normalized text"
            )
    if len(set(items)) != len(items):
        raise ReviewRecordReferenceError("evidence_artifact_ids must be unique")
    return tuple(sorted(items))


def _canonical_json(value: Any, *, field: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReviewRecordReferenceError(f"{field} must be canonical JSON") from exc


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReviewRecordReferenceError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _validate_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ReviewRecordReferenceError(f"{field} must be a lower-case SHA-256 digest")
    return value


def _validate_review_record_id(value: object, *, field: str) -> str:
    return _validate_typed_digest_id(
        value,
        prefix=_REVIEW_RECORD_ID_PREFIX,
        field=field,
    )


def _validate_typed_digest_id(
    value: object,
    *,
    prefix: str,
    field: str,
) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ReviewRecordReferenceError(f"{field} must use the {prefix!r} prefix")
    _validate_sha256(value[len(prefix) :], field=field)
    return value


__all__ = [
    "ImmutableReviewRecordRef",
    "ReviewRecordRegistrySnapshot",
    "ResolvedReviewRecordRef",
    "ReviewRecordReferenceError",
    "mint_review_record_reference",
    "mint_review_record_registry_snapshot",
    "resolve_current_review_record",
]
