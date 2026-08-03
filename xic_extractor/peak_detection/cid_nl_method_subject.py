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
from xic_extractor.processing_identity import ProcessingOwnerReferences

SCHEMA_VERSION = "cid_nl_method_subject_v1"
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
    request_inputs = json.loads(
        processing_owners.request.normalized_scientific_inputs_json
    )
    pinned_payload = json.dumps(
        request_inputs.get(REQUEST_INPUT_KEY),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    expected_payload = json.dumps(
        subject.request_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if pinned_payload != expected_payload:
        raise CidNlMethodSubjectContractError(
            "Untargeted method subject is not pinned by the processing request"
        )


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
    "CID_NL_METHOD_ID",
    "CID_NL_METHOD_VERSION",
    "CID_NL_PROVIDER_ID",
    "CidNlMethodSubject",
    "CidNlMethodSubjectContractError",
    "CidNlMethodSubjectKind",
    "REQUEST_INPUT_KEY",
    "SCHEMA_VERSION",
    "cid_nl_method_subject_from_target",
    "mint_untargeted_cid_nl_method_subject",
    "validate_cid_nl_method_subject_binding",
]
