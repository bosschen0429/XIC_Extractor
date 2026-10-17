"""Published row and cell identities for completed Untarget results."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field

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
_PROJECTION_MINT_TOKEN = object()


def _request_id(value: object, *, field_name: str) -> str:
    try:
        return validate_typed_identity_id(
            value,
            prefix=PROCESSING_REQUEST_ID_PREFIX,
            field=field_name,
        )
    except (TypeError, ValueError) as exc:
        raise UntargetProjectionContractError(str(exc)) from exc


def _require_owner_group(group: object) -> CrossSampleGroupIdentity:
    if type(group) is not CrossSampleGroupIdentity:
        raise UntargetProjectionContractError(
            "row identity requires exact owner CrossSample groups"
        )
    return group


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

    def __post_init__(self) -> None:
        group = _require_owner_group(self.group)
        request_id = _request_id(
            group.processing_request_id,
            field_name="group.processing_request_id",
        )
        expected_basis = _untarget_row_key_basis(
            processing_request_id=request_id,
            group=group,
        )
        if self.basis_bytes != expected_basis:
            raise UntargetProjectionContractError(
                "UntargetRowKey basis bytes are not exact"
            )
        try:
            expected_id = _untarget_row_key_id(expected_basis)
        except (TypeError, ValueError) as exc:
            raise UntargetProjectionContractError(str(exc)) from exc
        if self.untarget_row_key_id != expected_id:
            raise UntargetProjectionContractError(
                "untarget row key ID mismatches its exact basis bytes"
            )

    @property
    def processing_request_id(self) -> str:
        return self.group.processing_request_id

    @property
    def group_hypothesis_id(self) -> str:
        return self.group.group_hypothesis_id

    @property
    def group_identity_basis_bytes(self) -> bytes:
        return self.group.basis_bytes


def _mint_untarget_row_key(group: CrossSampleGroupIdentity) -> UntargetRowKey:
    """Mint the row key from an owner-validated canonical group."""

    group = _require_owner_group(group)
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
    _mint_token: InitVar[object | None] = field(default=None, kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object | None) -> None:
        if type(self.row_key) is not UntargetRowKey:
            raise UntargetProjectionContractError(
                "GroupCellSubject requires an exact UntargetRowKey"
            )
        if type(self.canonical_sample) is not ResolvedCanonicalSampleIdentity:
            raise UntargetProjectionContractError(
                "GroupCellSubject requires an owner-resolved canonical sample"
            )
        group = self.row_key.group
        if self.member_subject is None:
            return
        if type(self.member_subject) is not CrossSampleGroupMemberSubject:
            raise UntargetProjectionContractError(
                "GroupCellSubject member must be an exact CrossSample subject"
            )
        if (
            _mint_token is not _PROJECTION_MINT_TOKEN
            and self.member_subject not in group.member_subjects
        ):
            raise UntargetProjectionContractError(
                "GroupCellSubject member is absent from its group"
            )
        if self.member_subject.processing_request_id != (group.processing_request_id):
            raise UntargetProjectionContractError(
                "GroupCellSubject member crossed the processing request"
            )
        if self.member_subject.canonical_sample_identity_id != (
            self.canonical_sample.identity.sample_identity_id
        ):
            raise UntargetProjectionContractError(
                "GroupCellSubject member crossed the canonical sample"
            )

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
                "canonical_sample_identity_id": (
                    self.canonical_sample.identity.sample_identity_id
                ),
            },
        )
        return identity


def _validate_row_key_population(
    row_keys: Sequence[UntargetRowKey],
) -> None:
    basis_by_id: dict[str, bytes] = {}
    for row_key in row_keys:
        if type(row_key) is not UntargetRowKey:
            raise UntargetProjectionContractError(
                "row-key population contains a non-owner row key"
            )
        prior_basis = basis_by_id.get(row_key.untarget_row_key_id)
        if prior_basis is not None and prior_basis != row_key.basis_bytes:
            raise UntargetProjectionContractError(
                "row-key identity collision has unequal basis bytes"
            )
        basis_by_id[row_key.untarget_row_key_id] = row_key.basis_bytes
