"""Published row and cell identities for completed Untarget results."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
)
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    canonical_identity_basis_bytes,
    mint_typed_identity,
    typed_identity_digest,
    validate_typed_identity_id,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class UntargetProjectionContractError(ValueError):
    """Raised when an Untargeted projection cannot close exactly."""


_UNTARGET_ROW_KEY_PREFIX = "xic.untarget-row-key:"
_GROUP_CELL_SUBJECT_PREFIX = "xic.group-cell-subject:"
_UNTARGET_ROW_KEY_SCHEMA_VERSION = "untarget_row_key"
_GROUP_CELL_SUBJECT_SCHEMA_VERSION = "group_cell_subject"


def _request_id(value: object, *, field_name: str) -> str:
    try:
        return validate_typed_identity_id(
            value,
            prefix=PROCESSING_REQUEST_ID_PREFIX,
            field=field_name,
        )
    except (TypeError, ValueError) as exc:
        raise UntargetProjectionContractError(str(exc)) from exc


def _untarget_row_key_basis(
    *,
    processing_request_id: str,
    group: CrossSampleGroupIdentity,
) -> bytes:
    return canonical_identity_basis_bytes(
        {
            "schema_version": _UNTARGET_ROW_KEY_SCHEMA_VERSION,
            "processing_request_id": processing_request_id,
            "group_hypothesis_id": group.group_hypothesis_id,
            "group_identity_basis_hex": group.basis_bytes.hex(),
        }
    )


def _untarget_row_key_id(basis_bytes: bytes) -> str:
    return typed_identity_digest(_UNTARGET_ROW_KEY_PREFIX, basis_bytes)


@dataclass(frozen=True, slots=True)
class UntargetRowKey:
    """One request-scoped row identity for one finalized CrossSample group."""

    untarget_row_key_id: str
    group: CrossSampleGroupIdentity
    basis_bytes: bytes

    @property
    def processing_request_id(self) -> str:
        return self.group.processing_request_id

    @property
    def group_hypothesis_id(self) -> str:
        return self.group.group_hypothesis_id


def _mint_untarget_row_key(group: CrossSampleGroupIdentity) -> UntargetRowKey:
    """Mint the row key from an owner-validated canonical group."""

    request_id = _request_id(
        group.processing_request_id,
        field_name="group.processing_request_id",
    )
    basis = _untarget_row_key_basis(
        processing_request_id=request_id,
        group=group,
    )
    return UntargetRowKey(
        untarget_row_key_id=_untarget_row_key_id(basis),
        group=group,
        basis_bytes=basis,
    )


@dataclass(frozen=True, slots=True)
class GroupCellSubject:
    """One finalized group crossed with one canonical sample.

    ``member_subject`` is deliberately optional: ``None`` is the explicit
    owner-preserved absence for a finalized group/sample position.
    """

    row_key: UntargetRowKey
    canonical_sample: ResolvedCanonicalSampleIdentity
    member_subject: CrossSampleGroupMemberSubject | None

    @property
    def group(self) -> CrossSampleGroupIdentity:
        return self.row_key.group

    @property
    def processing_request_id(self) -> str:
        return self.row_key.processing_request_id

    @property
    def group_hypothesis_id(self) -> str:
        return self.group.group_hypothesis_id

    @property
    def subject_id(self) -> str:
        identity, _ = mint_typed_identity(
            _GROUP_CELL_SUBJECT_PREFIX,
            {
                "schema_version": _GROUP_CELL_SUBJECT_SCHEMA_VERSION,
                "untarget_row_key_id": self.row_key.untarget_row_key_id,
                "sample_key": (self.canonical_sample.identity.sample_key),
            },
        )
        return identity
