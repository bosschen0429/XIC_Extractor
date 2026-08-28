"""Untargeted workflow projection before cell-value decisions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleLineageEdge,
    CrossSampleSuccessorApplication,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
)
from xic_extractor.peak_detection.search_scope import UntargetedSearchScope
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    ProcessingRequestIdentity,
    canonical_identity_basis_bytes,
    typed_identity_digest,
    validate_typed_identity_id,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class UntargetProjectionContractError(ValueError):
    """Raised when an Untargeted pre-cell projection cannot close exactly."""


_UNTARGET_ROW_KEY_PREFIX = "xic.untarget-row-key.v1:"
_GROUP_CELL_SUBJECT_PREFIX = "xic.group-cell-subject.v1:"
_UNTARGET_ROW_KEY_SCHEMA_VERSION = "untarget_row_key_v1"
_GROUP_CELL_SUBJECT_SCHEMA_VERSION = "group_cell_subject_v1"
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


def _revalidate_owner_group(
    group: object,
    *,
    processing_request_id: str | None = None,
) -> CrossSampleGroupIdentity:
    if type(group) is not CrossSampleGroupIdentity:
        raise UntargetProjectionContractError(
            "pre-cell projection requires exact owner CrossSample groups"
        )
    group_request_id = _request_id(
        group.processing_request_id,
        field_name="group.processing_request_id",
    )
    if (
        processing_request_id is not None
        and group_request_id != processing_request_id
    ):
        raise UntargetProjectionContractError(
            "CrossSample group crossed the processing request"
        )
    try:
        members = tuple(
            _revalidate_owner_member(member)
            for member in group.member_subjects
        )
        canonical_group = CrossSampleGroupIdentity(
            group_hypothesis_id=group.group_hypothesis_id,
            processing_request_id=group_request_id,
            member_subjects=members,
            basis_bytes=group.basis_bytes,
            disposition=group.disposition,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise UntargetProjectionContractError(
            f"CrossSample group owner closure is invalid: {exc}"
        ) from exc
    if canonical_group != group:
        raise UntargetProjectionContractError(
            "CrossSample group owner identity closure is not exact"
        )
    return group


def _revalidate_owner_member(
    member: object,
) -> CrossSampleGroupMemberSubject:
    if type(member) is not CrossSampleGroupMemberSubject:
        raise UntargetProjectionContractError(
            "pre-cell projection requires exact owner group members"
        )
    try:
        canonical_member = CrossSampleGroupMemberSubject(
            member_subject_id=member.member_subject_id,
            processing_request_id=member.processing_request_id,
            canonical_sample_identity_id=member.canonical_sample_identity_id,
            subject_kind=member.subject_kind,
            subject_id=member.subject_id,
            physical_observation_ids=member.physical_observation_ids,
            basis_bytes=member.basis_bytes,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise UntargetProjectionContractError(
            f"CrossSample group member owner closure is invalid: {exc}"
        ) from exc
    if canonical_member != member:
        raise UntargetProjectionContractError(
            "CrossSample group member owner identity closure is not exact"
        )
    return member


def _revalidate_owner_lineage(
    edge: object,
    *,
    processing_request_id: str,
) -> CrossSampleLineageEdge:
    if type(edge) is not CrossSampleLineageEdge:
        raise UntargetProjectionContractError(
            "finalized group lineage is not owner-backed"
        )
    edge_request_id = _request_id(
        edge.processing_request_id,
        field_name="lineage.processing_request_id",
    )
    if edge_request_id != processing_request_id:
        raise UntargetProjectionContractError(
            "finalized group lineage crossed the processing request"
        )
    try:
        canonical_edge = CrossSampleLineageEdge(
            lineage_edge_id=edge.lineage_edge_id,
            predecessor_group_hypothesis_id=(
                edge.predecessor_group_hypothesis_id
            ),
            successor_group_hypothesis_id=edge.successor_group_hypothesis_id,
            processing_request_id=edge_request_id,
            predecessor_identity_basis_bytes=edge.predecessor_identity_basis_bytes,
            predecessor_group_hypothesis_ids=edge.predecessor_group_hypothesis_ids,
            member_subject_ids=edge.member_subject_ids,
            basis_bytes=edge.basis_bytes,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise UntargetProjectionContractError(
            f"CrossSample lineage owner closure is invalid: {exc}"
        ) from exc
    if canonical_edge != edge:
        raise UntargetProjectionContractError(
            "CrossSample lineage owner identity closure is not exact"
        )
    return edge


def _revalidate_owner_application(
    application: object,
    *,
    processing_request_id: str,
) -> CrossSampleSuccessorApplication:
    if type(application) is not CrossSampleSuccessorApplication:
        raise UntargetProjectionContractError(
            "final CrossSample application is not owner-backed"
        )
    try:
        predecessors = tuple(
            _revalidate_owner_group(
                group,
                processing_request_id=processing_request_id,
            )
            for group in application.predecessors
        )
        successors = tuple(
            _revalidate_owner_group(
                group,
                processing_request_id=processing_request_id,
            )
            for group in application.successors
        )
        lineage = tuple(
            _revalidate_owner_lineage(
                edge,
                processing_request_id=processing_request_id,
            )
            for edge in application.lineage
        )
        canonical_application = CrossSampleSuccessorApplication(
            status=application.status,
            predecessors=predecessors,
            successors=successors,
            lineage=lineage,
            reason=application.reason,
        )
    except UntargetProjectionContractError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise UntargetProjectionContractError(
            f"CrossSample application owner closure is invalid: {exc}"
        ) from exc
    if canonical_application != application:
        raise UntargetProjectionContractError(
            "CrossSample application owner identity closure is not exact"
        )
    return application


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


def _validate_request_bindings(
    *,
    receipt: object,
    finalized_alignment: FinalizedScientificAlignment,
    processing_request_id: str,
) -> tuple[tuple[CrossSampleGroupIdentity, ...], tuple[CrossSampleLineageEdge, ...]]:
    if type(receipt) is not CrossSampleFinalReceipt:
        raise UntargetProjectionContractError(
            "pre-cell projection requires an exact CrossSample final receipt"
        )
    receipt_request_id = _request_id(
        receipt.processing_request_id,
        field_name="final_cross_sample_receipt.processing_request_id",
    )
    if receipt_request_id != processing_request_id:
        raise UntargetProjectionContractError(
            "final CrossSample receipt crossed the processing request"
        )
    if type(receipt.final_population) is not tuple:
        raise UntargetProjectionContractError(
            "final CrossSample population must be an immutable tuple"
        )
    population = receipt.final_population
    _revalidate_owner_application(
        receipt.post_mt0_application,
        processing_request_id=processing_request_id,
    )
    final_application = _revalidate_owner_application(
        receipt.final_application,
        processing_request_id=processing_request_id,
    )
    lineage = final_application.lineage
    if type(lineage) is not tuple:
        raise UntargetProjectionContractError(
            "finalized group lineage must be an immutable tuple"
        )
    for group in population:
        _revalidate_owner_group(
            group,
            processing_request_id=processing_request_id,
        )
    return population, lineage


@dataclass(frozen=True, slots=True)
class UntargetRowKey:
    """One request-scoped row identity for one finalized CrossSample group."""

    untarget_row_key_id: str
    group: CrossSampleGroupIdentity
    basis_bytes: bytes

    def __post_init__(self) -> None:
        group = _revalidate_owner_group(self.group)
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

    group = _revalidate_owner_group(group)
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

    def __post_init__(self) -> None:
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
        if self.member_subject not in group.member_subjects:
            raise UntargetProjectionContractError(
                "GroupCellSubject member is absent from its group"
            )
        if self.member_subject.processing_request_id != (
            group.processing_request_id
        ):
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
        basis = canonical_identity_basis_bytes(
            {
                "schema_version": _GROUP_CELL_SUBJECT_SCHEMA_VERSION,
                "untarget_row_key_id": self.row_key.untarget_row_key_id,
                "canonical_sample_identity_id": (
                    self.canonical_sample.identity.sample_identity_id
                ),
            }
        )
        return typed_identity_digest(_GROUP_CELL_SUBJECT_PREFIX, basis)


@dataclass(frozen=True, slots=True)
class UntargetPreCellProjection:
    """Immutable row-key and Group×sample handoff before cell decisions."""

    finalized_alignment: FinalizedScientificAlignment
    processing_request: ProcessingRequestIdentity
    canonical_samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    final_population: tuple[CrossSampleGroupIdentity, ...]
    row_keys: tuple[UntargetRowKey, ...]
    cell_subjects: tuple[GroupCellSubject, ...]
    lineage: tuple[CrossSampleLineageEdge, ...]
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _PROJECTION_MINT_TOKEN:
            raise UntargetProjectionContractError(
                "pre-cell projection must be minted by its owner entry point"
            )
        _validate_projection(self)

    @property
    def processing_request_id(self) -> str:
        return self.processing_request.processing_request_id


def project_untarget_pre_cell(
    finalized_alignment: FinalizedScientificAlignment,
) -> UntargetPreCellProjection:
    """Project one exact finalized Untargeted alignment before cell decisions."""

    if type(finalized_alignment) is not FinalizedScientificAlignment:
        raise UntargetProjectionContractError(
            "pre-cell projection requires an exact FinalizedScientificAlignment"
        )
    if type(finalized_alignment.scope) is not UntargetedSearchScope:
        raise UntargetProjectionContractError(
            "pre-cell projection requires an Untargeted SearchScope"
        )
    if finalized_alignment.processing_request is not finalized_alignment.scope.request:
        raise UntargetProjectionContractError(
            "finalized alignment crossed its exact processing request"
        )

    try:
        request_id = _request_id(
            finalized_alignment.processing_request.processing_request_id,
            field_name="processing_request_id",
        )
        receipt = finalized_alignment.final_cross_sample_receipt
        population, lineage = _validate_request_bindings(
            receipt=receipt,
            finalized_alignment=finalized_alignment,
            processing_request_id=request_id,
        )
        validate_cross_sample_group_population(population)
        groups = tuple(
            sorted(population, key=lambda group: group.group_hypothesis_id)
        )
        rows = tuple(_mint_untarget_row_key(group) for group in groups)
        _validate_row_key_population(rows)
        samples = tuple(finalized_alignment.canonical_samples)
        cells = _build_cell_subjects(
            groups=groups,
            samples=samples,
            rows=rows,
        )
        return UntargetPreCellProjection(
            finalized_alignment=finalized_alignment,
            processing_request=finalized_alignment.processing_request,
            canonical_samples=samples,
            final_population=groups,
            row_keys=rows,
            cell_subjects=cells,
            lineage=lineage,
            _mint_token=_PROJECTION_MINT_TOKEN,
        )
    except UntargetProjectionContractError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise UntargetProjectionContractError(str(exc)) from exc


def _build_cell_subjects(
    *,
    groups: Sequence[CrossSampleGroupIdentity],
    samples: Sequence[ResolvedCanonicalSampleIdentity],
    rows: Sequence[UntargetRowKey],
) -> tuple[GroupCellSubject, ...]:
    if len(groups) != len(rows):
        raise UntargetProjectionContractError(
            "row-key population is incomplete"
        )
    result: list[GroupCellSubject] = []
    for group, row in zip(groups, rows, strict=True):
        if row.group is not group:
            raise UntargetProjectionContractError(
                "GroupCellSubject row crossed its owner group"
            )
        for sample in samples:
            matching = tuple(
                member
                for member in group.member_subjects
                if member.canonical_sample_identity_id
                == sample.identity.sample_identity_id
            )
            if len(matching) > 1:
                raise UntargetProjectionContractError(
                    "group has duplicate members for one canonical sample"
                )
            result.append(
                GroupCellSubject(
                    row_key=row,
                    canonical_sample=sample,
                    member_subject=matching[0] if matching else None,
                )
            )
    return tuple(result)


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


def _validate_projection(result: UntargetPreCellProjection) -> None:
    source = result.finalized_alignment
    if type(source) is not FinalizedScientificAlignment:
        raise UntargetProjectionContractError(
            "projection source must be an exact FinalizedScientificAlignment"
        )
    if type(source.scope) is not UntargetedSearchScope:
        raise UntargetProjectionContractError(
            "projection source must use an Untargeted SearchScope"
        )
    if result.processing_request is not source.processing_request:
        raise UntargetProjectionContractError(
            "projection crossed its exact processing request"
        )
    expected_samples = tuple(source.canonical_samples)
    if result.canonical_samples != expected_samples:
        raise UntargetProjectionContractError(
            "projection crossed its canonical sample universe"
        )
    if result.final_population != tuple(source.final_population):
        raise UntargetProjectionContractError(
            "projection crossed its finalized group population"
        )
    if result.lineage != tuple(source.lineage):
        raise UntargetProjectionContractError(
            "projection lost finalized group lineage"
        )
    if type(result.row_keys) is not tuple or type(result.cell_subjects) is not tuple:
        raise UntargetProjectionContractError(
            "pre-cell projection collections must be immutable tuples"
        )
    _validate_row_key_population(result.row_keys)
    population = result.final_population
    try:
        _validate_request_bindings(
            receipt=source.final_cross_sample_receipt,
            finalized_alignment=source,
            processing_request_id=result.processing_request_id,
        )
        validate_cross_sample_group_population(population)
    except (TypeError, ValueError) as exc:
        raise UntargetProjectionContractError(str(exc)) from exc
    expected_groups = tuple(
        sorted(population, key=lambda group: group.group_hypothesis_id)
    )
    expected_rows = tuple(
        _mint_untarget_row_key(group) for group in expected_groups
    )
    if result.row_keys != expected_rows:
        raise UntargetProjectionContractError(
            "projection row-key population is missing, duplicated, or crossed"
        )
    expected_cells = _build_cell_subjects(
        groups=expected_groups,
        samples=expected_samples,
        rows=expected_rows,
    )
    if result.cell_subjects != expected_cells:
        raise UntargetProjectionContractError(
            "projection Group×sample population is missing, duplicated, or crossed"
        )


__all__ = (
    "GroupCellSubject",
    "UntargetPreCellProjection",
    "UntargetProjectionContractError",
    "UntargetRowKey",
    "project_untarget_pre_cell",
)
