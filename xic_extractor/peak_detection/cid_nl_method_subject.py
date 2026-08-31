"""Workflow-neutral identity for one CID-NL precursor method subject.

Targeted and Untargeted callers may constrain the same CID-NL assessment owner,
but a configured Target is not a universal scientific identity.  This module
keeps the common precursor/method facts explicit and retains Targeted identity
only when the subject was adapted from an owner-resolved target definition.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Literal, TypeAlias

from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
)

SCHEMA_VERSION = "cid_nl_method_subject_v1"
BATCH_SCHEMA_VERSION = "cid_nl_method_subject_batch_v1"
REQUEST_INPUT_KEY = "cid_nl_method_subject"
CID_NL_PROVIDER_ID = "dda_ddms2_cid_nl"
CID_NL_METHOD_ID = "cid_nl"
CID_NL_METHOD_VERSION = "1"

CidNlMethodSubjectKind: TypeAlias = Literal[
    "targeted_definition",
    "untargeted_precursor",
]

_SUBJECT_ID_PREFIX = "xic.cid-nl-method-subject.v1:"
_TARGET_KEY_ID_PREFIX = "xic.target-key.v1:"
_TARGET_DEFINITION_ID_PREFIX = "xic.target-definition.v1:"
_TARGET_SNAPSHOT_ID_PREFIX = "xic.target-list-snapshot.v1:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CidNlMethodSubjectContractError(ValueError):
    """Raised when a CID-NL method subject is not exact or request-bound."""


@dataclass(frozen=True, slots=True)
class CidNlMethodSubject:
    """Smallest shared subject for Targeted and Untargeted CID-NL assessment."""

    method_subject_id: str
    subject_kind: CidNlMethodSubjectKind
    precursor_mz: float
    precursor_tolerance_ppm: float
    neutral_loss_da: float
    target_snapshot_id: str | None = None
    target_key_id: str | None = None
    target_definition_id: str | None = None

    def __post_init__(self) -> None:
        precursor_mz = _positive_finite_float(self.precursor_mz, field="precursor_mz")
        precursor_tolerance_ppm = _positive_finite_float(
            self.precursor_tolerance_ppm,
            field="precursor_tolerance_ppm",
        )
        neutral_loss_da = _positive_finite_float(
            self.neutral_loss_da,
            field="neutral_loss_da",
        )
        if self.subject_kind not in {"targeted_definition", "untargeted_precursor"}:
            raise CidNlMethodSubjectContractError(
                "subject_kind must be targeted_definition or untargeted_precursor"
            )
        target_ids = (
            self.target_snapshot_id,
            self.target_key_id,
            self.target_definition_id,
        )
        if self.subject_kind == "targeted_definition":
            if any(value is None for value in target_ids):
                raise CidNlMethodSubjectContractError(
                    "targeted method subject requires the complete target identity"
                )
            _validate_typed_id(
                self.target_snapshot_id,
                prefix=_TARGET_SNAPSHOT_ID_PREFIX,
                field="target_snapshot_id",
            )
            _validate_typed_id(
                self.target_key_id,
                prefix=_TARGET_KEY_ID_PREFIX,
                field="target_key_id",
            )
            _validate_typed_id(
                self.target_definition_id,
                prefix=_TARGET_DEFINITION_ID_PREFIX,
                field="target_definition_id",
            )
        elif any(value is not None for value in target_ids):
            raise CidNlMethodSubjectContractError(
                "Untargeted method subject cannot carry configured Target identity"
            )

        object.__setattr__(self, "precursor_mz", precursor_mz)
        object.__setattr__(
            self,
            "precursor_tolerance_ppm",
            precursor_tolerance_ppm,
        )
        object.__setattr__(self, "neutral_loss_da", neutral_loss_da)
        expected_id = _method_subject_id(
            subject_kind=self.subject_kind,
            precursor_mz=precursor_mz,
            precursor_tolerance_ppm=precursor_tolerance_ppm,
            neutral_loss_da=neutral_loss_da,
            target_snapshot_id=self.target_snapshot_id,
            target_key_id=self.target_key_id,
            target_definition_id=self.target_definition_id,
        )
        if self.method_subject_id != expected_id:
            raise CidNlMethodSubjectContractError(
                "method_subject_id mismatches the canonical CID-NL subject"
            )

    def request_payload(self) -> dict[str, object]:
        """Return the exact value an Untargeted processing request must pin."""

        return {
            "schema_version": SCHEMA_VERSION,
            **asdict(self),
        }


@dataclass(frozen=True, slots=True)
class UntargetedCidNlMethodSubjectBatch:
    """Immutable owner closure for one or more Untargeted CID-NL subjects.

    The subject and candidate tuples are deliberately kept as ordered tuples.
    Their order is part of the request payload contract, while each candidate
    is bound to one exact subject rather than allocated by an m/z tolerance.
    """

    subjects: tuple[CidNlMethodSubject, ...]
    candidate_memberships: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _validate_untargeted_subject_batch(
            subjects=self.subjects,
            candidate_memberships=self.candidate_memberships,
        )

    @property
    def schema_version(self) -> str:
        return BATCH_SCHEMA_VERSION

    def subject_for_candidate(self, candidate_id: str) -> CidNlMethodSubject:
        """Return the owner subject bound to one exact candidate ID."""

        candidate = _normalized_text(candidate_id, field="candidate_id")
        membership = next(
            (
                (member_candidate, method_subject_id)
                for member_candidate, method_subject_id in self.candidate_memberships
                if member_candidate == candidate
            ),
            None,
        )
        if membership is None:
            raise CidNlMethodSubjectContractError(
                f"candidate {candidate!r} is not declared by the CID-NL subject batch"
            )
        _member_candidate, method_subject_id = membership
        return next(
            subject
            for subject in self.subjects
            if subject.method_subject_id == method_subject_id
        )

    def request_payload(self) -> dict[str, object]:
        """Return the canonical batch value pinned under ``REQUEST_INPUT_KEY``."""

        return {
            "schema_version": BATCH_SCHEMA_VERSION,
            "subjects": [subject.request_payload() for subject in self.subjects],
            "candidate_memberships": [
                {
                    "candidate_id": candidate_id,
                    "method_subject_id": method_subject_id,
                }
                for candidate_id, method_subject_id in self.candidate_memberships
            ],
        }


def cid_nl_method_subject_from_target(
    target: ResolvedTargetDefinition,
) -> CidNlMethodSubject:
    """Adapt one owner-resolved Target into the shared CID-NL subject."""

    if not isinstance(target, ResolvedTargetDefinition):
        raise CidNlMethodSubjectContractError(
            "target must be an owner-resolved target definition"
        )
    target_value = target.definition.target
    neutral_loss_da = target_value.neutral_loss_da
    if neutral_loss_da is None:
        raise CidNlMethodSubjectContractError(
            "CID-NL Targeted subject requires configured neutral loss"
        )
    return _mint_subject(
        subject_kind="targeted_definition",
        precursor_mz=target_value.mz,
        precursor_tolerance_ppm=target_value.ppm_tol,
        neutral_loss_da=neutral_loss_da,
        target_snapshot_id=target.snapshot.snapshot_id,
        target_key_id=target.definition.target_key.target_key_id,
        target_definition_id=target.definition.target_definition_id,
    )


def mint_untargeted_cid_nl_method_subject(
    *,
    precursor_mz: float,
    precursor_tolerance_ppm: float,
    neutral_loss_da: float,
) -> CidNlMethodSubject:
    """Mint an Untargeted subject without fabricating configured Target IDs."""

    return _mint_subject(
        subject_kind="untargeted_precursor",
        precursor_mz=precursor_mz,
        precursor_tolerance_ppm=precursor_tolerance_ppm,
        neutral_loss_da=neutral_loss_da,
    )


def mint_untargeted_cid_nl_method_subject_batch(
    *,
    subjects: tuple[CidNlMethodSubject, ...],
    candidate_memberships: tuple[tuple[str, str], ...],
) -> UntargetedCidNlMethodSubjectBatch:
    """Mint an immutable, canonically ordered Untargeted subject batch."""

    return UntargetedCidNlMethodSubjectBatch(
        subjects=subjects,
        candidate_memberships=candidate_memberships,
    )


def cid_nl_method_subject_from_request(
    request: ProcessingRequestIdentity,
) -> CidNlMethodSubject:
    """Rehydrate the exact Untargeted provider configuration pinned by a request."""

    payload = _request_cid_nl_payload(request)
    if payload.get("schema_version") == BATCH_SCHEMA_VERSION:
        raise CidNlMethodSubjectContractError(
            "Untargeted request pins a CID-NL method subject batch"
        )
    return _subject_from_payload(payload)


def cid_nl_method_subject_batch_from_request(
    request: ProcessingRequestIdentity,
) -> UntargetedCidNlMethodSubjectBatch:
    """Rehydrate a request-pinned multi-subject Untargeted CID-NL batch."""

    payload = _request_cid_nl_payload(request)
    if payload.get("schema_version") == SCHEMA_VERSION:
        raise CidNlMethodSubjectContractError(
            "Untargeted request pins a singular CID-NL method subject"
        )
    expected_fields = {"schema_version", "subjects", "candidate_memberships"}
    if set(payload) != expected_fields:
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL subject batch has wrong fields"
        )
    if payload["schema_version"] != BATCH_SCHEMA_VERSION:
        raise CidNlMethodSubjectContractError(
            f"Untargeted request CID-NL batch schema must be {BATCH_SCHEMA_VERSION!r}"
        )
    raw_subjects = payload["subjects"]
    if not isinstance(raw_subjects, list):
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL subject batch subjects must be a list"
        )
    subjects = tuple(_subject_from_payload(item) for item in raw_subjects)
    raw_memberships = payload["candidate_memberships"]
    if not isinstance(raw_memberships, list):
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL subject batch memberships must be a list"
        )
    memberships: list[tuple[str, str]] = []
    for item in raw_memberships:
        if not isinstance(item, dict) or set(item) != {
            "candidate_id",
            "method_subject_id",
        }:
            raise CidNlMethodSubjectContractError(
                "Untargeted request CID-NL subject batch has malformed membership"
            )
        candidate_id = item["candidate_id"]
        method_subject_id = item["method_subject_id"]
        if not isinstance(candidate_id, str) or not isinstance(
            method_subject_id, str
        ):
            raise CidNlMethodSubjectContractError(
                "Untargeted request CID-NL subject membership IDs must be text"
            )
        memberships.append((candidate_id, method_subject_id))
    try:
        return UntargetedCidNlMethodSubjectBatch(
            subjects=subjects,
            candidate_memberships=tuple(memberships),
        )
    except CidNlMethodSubjectContractError:
        raise
    except (TypeError, ValueError) as exc:
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL subject batch is malformed"
        ) from exc


def validate_cid_nl_method_subject_binding(
    subject: CidNlMethodSubject,
    *,
    processing_owners: ProcessingOwnerReferences,
    target: ResolvedTargetDefinition | None = None,
) -> None:
    """Reject a subject not pinned by the exact processing request owner."""

    if not isinstance(subject, CidNlMethodSubject):
        raise CidNlMethodSubjectContractError(
            "subject must be an owner-normalized CID-NL method subject"
        )
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise CidNlMethodSubjectContractError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if subject.subject_kind == "targeted_definition":
        if target is None or not isinstance(target, ResolvedTargetDefinition):
            raise CidNlMethodSubjectContractError(
                "targeted method subject requires its exact target adapter"
            )
        expected = cid_nl_method_subject_from_target(target)
        if subject != expected:
            raise CidNlMethodSubjectContractError(
                "targeted method subject mismatches its target definition"
            )
        snapshot = processing_owners.target_snapshot
        if snapshot is None or snapshot.snapshot_id != target.snapshot.snapshot_id:
            raise CidNlMethodSubjectContractError(
                "targeted method subject is outside the processing request"
            )
        return

    if target is not None:
        raise CidNlMethodSubjectContractError(
            "Untargeted method subject cannot be paired with a configured Target"
        )
    try:
        pinned_batch = cid_nl_method_subject_batch_from_request(
            processing_owners.request
        )
    except CidNlMethodSubjectContractError:
        try:
            pinned_subject = cid_nl_method_subject_from_request(
                processing_owners.request
            )
        except CidNlMethodSubjectContractError as exc:
            raise CidNlMethodSubjectContractError(
                "Untargeted method subject is not pinned by the processing request"
            ) from exc
        if pinned_subject != subject:
            raise CidNlMethodSubjectContractError(
                "Untargeted method subject is not pinned by the processing request"
            )
        return
    if subject not in pinned_batch.subjects:
        raise CidNlMethodSubjectContractError(
            "Untargeted method subject is not pinned by the processing request"
        )


def _request_cid_nl_payload(
    request: ProcessingRequestIdentity,
) -> dict[str, object]:
    if not isinstance(request, ProcessingRequestIdentity):
        raise CidNlMethodSubjectContractError(
            "request must be an owner-minted processing request"
        )
    try:
        request_inputs = json.loads(request.normalized_scientific_inputs_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CidNlMethodSubjectContractError(
            "Untargeted request scientific inputs are malformed"
        ) from exc
    if not isinstance(request_inputs, dict):
        raise CidNlMethodSubjectContractError(
            "Untargeted request scientific inputs must be a mapping"
        )
    payload = request_inputs.get(REQUEST_INPUT_KEY)
    if not isinstance(payload, dict):
        raise CidNlMethodSubjectContractError(
            "Untargeted request does not pin a CID-NL method subject"
        )
    return payload


def _subject_from_payload(payload: object) -> CidNlMethodSubject:
    if not isinstance(payload, dict):
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL method subject must be an object"
        )
    expected_fields = {
        "schema_version",
        "method_subject_id",
        "subject_kind",
        "precursor_mz",
        "precursor_tolerance_ppm",
        "neutral_loss_da",
        "target_snapshot_id",
        "target_key_id",
        "target_definition_id",
    }
    if set(payload) != expected_fields:
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL method subject has wrong fields"
        )
    if payload["schema_version"] != SCHEMA_VERSION:
        raise CidNlMethodSubjectContractError(
            f"Untargeted request CID-NL schema must be {SCHEMA_VERSION!r}"
        )
    subject_kind = payload["subject_kind"]
    if subject_kind != "untargeted_precursor":
        raise CidNlMethodSubjectContractError(
            "Untargeted request requires an untargeted_precursor method subject"
        )
    method_subject_id = payload["method_subject_id"]
    if not isinstance(method_subject_id, str):
        raise CidNlMethodSubjectContractError(
            "Untargeted request method_subject_id must be text"
        )
    target_ids = (
        payload["target_snapshot_id"],
        payload["target_key_id"],
        payload["target_definition_id"],
    )
    if any(value is not None for value in target_ids):
        raise CidNlMethodSubjectContractError(
            "Untargeted request method subject cannot carry Target identity"
        )
    try:
        return CidNlMethodSubject(
            method_subject_id=method_subject_id,
            subject_kind="untargeted_precursor",
            precursor_mz=_positive_finite_float(
                payload["precursor_mz"], field="precursor_mz"
            ),
            precursor_tolerance_ppm=_positive_finite_float(
                payload["precursor_tolerance_ppm"],
                field="precursor_tolerance_ppm",
            ),
            neutral_loss_da=_positive_finite_float(
                payload["neutral_loss_da"], field="neutral_loss_da"
            ),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, CidNlMethodSubjectContractError):
            raise
        raise CidNlMethodSubjectContractError(
            "Untargeted request CID-NL method subject is malformed"
        ) from exc


def _validate_untargeted_subject_batch(
    *,
    subjects: object,
    candidate_memberships: object,
) -> None:
    if type(subjects) is not tuple or not subjects:
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch requires a non-empty subject tuple"
        )
    if any(type(subject) is not CidNlMethodSubject for subject in subjects):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch contains a non-owner subject"
        )
    for subject in subjects:
        if subject.subject_kind != "untargeted_precursor":
            raise CidNlMethodSubjectContractError(
                "Untargeted CID-NL subject batch cannot contain a Targeted subject"
            )
        try:
            CidNlMethodSubject(
                method_subject_id=subject.method_subject_id,
                subject_kind=subject.subject_kind,
                precursor_mz=subject.precursor_mz,
                precursor_tolerance_ppm=subject.precursor_tolerance_ppm,
                neutral_loss_da=subject.neutral_loss_da,
                target_snapshot_id=subject.target_snapshot_id,
                target_key_id=subject.target_key_id,
                target_definition_id=subject.target_definition_id,
            )
        except (TypeError, ValueError) as exc:
            raise CidNlMethodSubjectContractError(
                "Untargeted CID-NL subject batch contains a malformed subject"
            ) from exc
    subject_ids = tuple(subject.method_subject_id for subject in subjects)
    if len(subject_ids) != len(set(subject_ids)):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch contains duplicate subjects"
        )
    if subject_ids != tuple(sorted(subject_ids)):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch subjects are not canonically ordered"
        )

    if type(candidate_memberships) is not tuple or not candidate_memberships:
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch requires candidate memberships"
        )
    normalized_memberships: list[tuple[str, str]] = []
    for membership in candidate_memberships:
        if type(membership) is not tuple or len(membership) != 2:
            raise CidNlMethodSubjectContractError(
                "Untargeted CID-NL subject batch has malformed membership"
            )
        candidate_id = _normalized_text(membership[0], field="candidate_id")
        method_subject_id = _normalized_text(
            membership[1], field="method_subject_id"
        )
        normalized_memberships.append((candidate_id, method_subject_id))
    if tuple(normalized_memberships) != candidate_memberships:
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch memberships are not normalized"
        )
    candidate_ids = tuple(candidate_id for candidate_id, _ in candidate_memberships)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch contains duplicate candidates"
        )
    if candidate_ids != tuple(sorted(candidate_ids)):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch memberships are not canonically ordered"
        )
    declared_subject_ids = set(subject_ids)
    linked_subject_ids = {
        method_subject_id for _, method_subject_id in candidate_memberships
    }
    if not linked_subject_ids.issubset(declared_subject_ids):
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch references an undeclared subject"
        )
    if linked_subject_ids != declared_subject_ids:
        raise CidNlMethodSubjectContractError(
            "Untargeted CID-NL subject batch contains an unused subject"
        )


def _normalized_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CidNlMethodSubjectContractError(
            f"{field} must be non-empty normalized text"
        )
    return value


def _mint_subject(
    *,
    subject_kind: CidNlMethodSubjectKind,
    precursor_mz: float,
    precursor_tolerance_ppm: float,
    neutral_loss_da: float,
    target_snapshot_id: str | None = None,
    target_key_id: str | None = None,
    target_definition_id: str | None = None,
) -> CidNlMethodSubject:
    precursor = _positive_finite_float(precursor_mz, field="precursor_mz")
    tolerance = _positive_finite_float(
        precursor_tolerance_ppm,
        field="precursor_tolerance_ppm",
    )
    neutral_loss = _positive_finite_float(neutral_loss_da, field="neutral_loss_da")
    return CidNlMethodSubject(
        method_subject_id=_method_subject_id(
            subject_kind=subject_kind,
            precursor_mz=precursor,
            precursor_tolerance_ppm=tolerance,
            neutral_loss_da=neutral_loss,
            target_snapshot_id=target_snapshot_id,
            target_key_id=target_key_id,
            target_definition_id=target_definition_id,
        ),
        subject_kind=subject_kind,
        precursor_mz=precursor,
        precursor_tolerance_ppm=tolerance,
        neutral_loss_da=neutral_loss,
        target_snapshot_id=target_snapshot_id,
        target_key_id=target_key_id,
        target_definition_id=target_definition_id,
    )


def _method_subject_id(
    *,
    subject_kind: CidNlMethodSubjectKind,
    precursor_mz: float,
    precursor_tolerance_ppm: float,
    neutral_loss_da: float,
    target_snapshot_id: str | None,
    target_key_id: str | None,
    target_definition_id: str | None,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "provider_id": CID_NL_PROVIDER_ID,
        "method_id": CID_NL_METHOD_ID,
        "method_version": CID_NL_METHOD_VERSION,
        "subject_kind": subject_kind,
        "precursor_mz": precursor_mz,
        "precursor_tolerance_ppm": precursor_tolerance_ppm,
        "neutral_loss_da": neutral_loss_da,
        "target_snapshot_id": target_snapshot_id,
        "target_key_id": target_key_id,
        "target_definition_id": target_definition_id,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _SUBJECT_ID_PREFIX + hashlib.sha256(encoded).hexdigest()


def _positive_finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CidNlMethodSubjectContractError(f"{field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise CidNlMethodSubjectContractError(
            f"{field} must be finite and positive"
        )
    return parsed


def _validate_typed_id(
    value: object,
    *,
    prefix: str,
    field: str,
) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise CidNlMethodSubjectContractError(
            f"{field} must use the {prefix!r} prefix"
        )
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise CidNlMethodSubjectContractError(
            f"{field} must contain a SHA-256 digest"
        )
    return value


__all__ = [
    "BATCH_SCHEMA_VERSION",
    "CID_NL_METHOD_ID",
    "CID_NL_METHOD_VERSION",
    "CID_NL_PROVIDER_ID",
    "CidNlMethodSubject",
    "CidNlMethodSubjectContractError",
    "CidNlMethodSubjectKind",
    "REQUEST_INPUT_KEY",
    "SCHEMA_VERSION",
    "UntargetedCidNlMethodSubjectBatch",
    "cid_nl_method_subject_batch_from_request",
    "cid_nl_method_subject_from_request",
    "cid_nl_method_subject_from_target",
    "mint_untargeted_cid_nl_method_subject",
    "mint_untargeted_cid_nl_method_subject_batch",
    "validate_cid_nl_method_subject_binding",
]
