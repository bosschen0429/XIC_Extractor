"""Typed Backfill receipt model consumed by final cell composition."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSamplePartitionTransition,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.owner_backfill_request_plan import (
    OwnerBackfillLogicalRequestMember,
    OwnerBackfillRequestIdentity,
)
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    resolve_canonical_sample_identity,
)

from .observation_repair_authorization import (
    _AUTHORIZATION_PREFIX,
    _CANDIDATE_PREFIX,
    ObservationRepairAuthorization,
    ObservationRepairAuthorizationState,
    ObservationRepairCandidate,
    ObservationRepairContractError,
    ObservationRepairMultipleEligibleAuthorization,
    ObservationRepairNotRunAuthorization,
    ObservationRepairRepairAuthorization,
    _validate_candidate,
    _validate_typed_id,
)

ObservationRepairOutcome: TypeAlias = Literal["no_repair", "repaired"]

_SUBJECT_PREFIX = "xic.observation-repair-subject:"
_RECEIPT_PREFIX = "xic.observation-repair-receipt:"
_NO_REPAIR_TERMINAL_PREFIX = "xic.observation-repair-no-repair-terminal:"
_PHYSICAL_ANSWER_PREFIX = "physical-answer:"
_SUBJECT_MINT_TOKEN = object()
_RECEIPT_MINT_TOKEN = object()
_NO_REPAIR_TERMINAL_MINT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class ObservationRepairSubject:
    """Owner-minted subject for exactly one repair epoch."""

    subject_id: str
    request_identity: OwnerBackfillRequestIdentity
    logical_request: OwnerBackfillLogicalRequestMember
    predecessor_group: CrossSampleGroupIdentity
    member_subject: CrossSampleGroupMemberSubject
    sample_identity: ResolvedCanonicalSampleIdentity
    successor_application: CrossSamplePartitionTransition
    repair_epoch: int
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _SUBJECT_MINT_TOKEN:
            raise ObservationRepairContractError(
                "repair subject must be minted by Backfill"
            )
        _validate_subject(self)


@dataclass(frozen=True, slots=True)
class ObservationRepairEvidence:
    """Process-safe evidence facts; this record carries no selection authority."""

    logical_request_id: str
    request_identity_id: str
    group_hypothesis_id: str
    member_subject_id: str | None
    sample_stem: str
    work_unit_key: str
    candidate_index: int
    candidate_phase: str
    acquisition_source_id: str
    selection_subject_id: str
    physical_question_id: str
    physical_answer_id: str
    physical_segment_id: str
    fact_content_id: str
    selected_integration_authority_content_id: str
    rt_left_min: float
    rt_apex_min: float
    rt_right_min: float
    height: float
    scan_support_count: int
    area_counts_seconds: float
    area_source: str


@dataclass(frozen=True, slots=True)
class ObservationRepairNoRepairTerminal:
    """Exact physical terminal fact for a membership-neutral no-repair result."""

    terminal_id: str
    subject_id: str
    authorization_id: str
    candidate_id: str
    repair_epoch: int
    work_unit_key: str
    physical_answer_id: str
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _NO_REPAIR_TERMINAL_MINT_TOKEN:
            raise ObservationRepairContractError(
                "no-repair terminal must be minted by Backfill"
            )
        _validate_no_repair_terminal(self)


@dataclass(frozen=True, slots=True)
class ObservationRepairReceipt:
    """Terminal content-bound handoff from Backfill to its next owner."""

    receipt_id: str
    subject: ObservationRepairSubject | ObservationRepairCandidate
    subject_id: str
    repair_epoch: int
    outcome: ObservationRepairOutcome
    sample_stem: str
    evidence: ObservationRepairEvidence | None
    no_repair_terminal: ObservationRepairNoRepairTerminal | None = None
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RECEIPT_MINT_TOKEN:
            raise ObservationRepairContractError(
                "repair receipt must be minted by Backfill"
            )
        if type(self.subject) not in {
            ObservationRepairSubject,
            ObservationRepairCandidate,
        }:
            raise TypeError("repair receipt subject must be owner-minted")
        _validate_subject(self.subject)
        if self.subject_id != self.subject.subject_id:
            raise ObservationRepairContractError(
                "repair receipt crosses its exact subject"
            )
        if self.repair_epoch != 1:
            raise ObservationRepairContractError("repair receipt exceeds one epoch")
        if self.repair_epoch != self.subject.repair_epoch:
            raise ObservationRepairContractError("repair receipt crosses subject epoch")
        if self.sample_stem != self.subject.request_identity.sample_stem:
            raise ObservationRepairContractError(
                "repair receipt crosses subject sample"
            )
        expected_outcome = "repaired" if self.evidence is not None else "no_repair"
        if self.outcome != expected_outcome:
            raise ObservationRepairContractError(
                "repair receipt outcome crosses its terminal evidence"
            )
        if self.evidence is not None and self.no_repair_terminal is not None:
            raise ObservationRepairContractError(
                "repaired receipt cannot carry a no-repair terminal"
            )
        if (
            type(self.subject) is ObservationRepairSubject
            and self.no_repair_terminal is not None
        ):
            raise ObservationRepairContractError(
                "no-repair terminal is candidate-scoped"
            )
        if self.evidence is None:
            if (
                type(self.subject) is ObservationRepairCandidate
                and self.no_repair_terminal is None
            ):
                raise ObservationRepairContractError(
                    "candidate no-repair receipt requires an exact terminal"
                )
            if self.no_repair_terminal is not None:
                if (
                    type(self.no_repair_terminal)
                    is not ObservationRepairNoRepairTerminal
                ):
                    raise TypeError("no-repair terminal must be owner-minted")
                self.no_repair_terminal.__post_init__(_NO_REPAIR_TERMINAL_MINT_TOKEN)
                if (
                    self.no_repair_terminal.subject_id != self.subject_id
                    or self.no_repair_terminal.repair_epoch != self.repair_epoch
                    or self.no_repair_terminal.authorization_id != self.authorization_id
                    or self.no_repair_terminal.candidate_id != self.candidate_id
                ):
                    raise ObservationRepairContractError(
                        "no-repair terminal crosses exact subject or epoch"
                    )
        expected = _receipt_id(
            subject_id=self.subject_id,
            repair_epoch=self.repair_epoch,
            outcome=self.outcome,
            sample_stem=self.sample_stem,
            evidence=self.evidence,
            no_repair_terminal=self.no_repair_terminal,
            authorization_id=self.authorization_id,
            candidate_id=self.candidate_id,
        )
        if self.receipt_id != expected:
            raise ObservationRepairContractError(
                "repair receipt id is not bound to its complete content"
            )

    @property
    def evidence_authority(self) -> str:
        return "observation_evidence_only"

    @property
    def membership_authority(self) -> str:
        return "none"

    @property
    def value_authority(self) -> str:
        return "none"

    @property
    def matrix_authority(self) -> str:
        return "none"

    @property
    def activation_authority(self) -> str:
        return "none"

    @property
    def writer_authority(self) -> str:
        return "none"

    @property
    def authorization_id(self) -> str | None:
        """Expose the candidate authorization without granting new authority."""

        if type(self.subject) is ObservationRepairCandidate:
            return self.subject.authorization_id
        return None

    @property
    def candidate_id(self) -> str | None:
        """Expose the membership-neutral candidate identity when present."""

        if type(self.subject) is ObservationRepairCandidate:
            return self.subject.candidate_id
        return None


def _validate_subject(
    subject: ObservationRepairSubject | ObservationRepairCandidate,
) -> None:
    if type(subject) is ObservationRepairCandidate:
        _validate_candidate(subject)
        return
    if type(subject) is not ObservationRepairSubject:
        raise TypeError("subject must be owner-minted")
    if type(subject.request_identity) is not OwnerBackfillRequestIdentity:
        raise TypeError("request identity must be owner-minted")
    if type(subject.logical_request) is not OwnerBackfillLogicalRequestMember:
        raise TypeError("logical request must be owner-minted")
    if type(subject.predecessor_group) is not CrossSampleGroupIdentity:
        raise TypeError("predecessor group must be owner-minted")
    if type(subject.member_subject) is not CrossSampleGroupMemberSubject:
        raise TypeError("member subject must be owner-minted")
    if type(subject.sample_identity) is not ResolvedCanonicalSampleIdentity:
        raise TypeError("sample identity must be owner-resolved")
    if type(subject.successor_application) is not CrossSamplePartitionTransition:
        raise TypeError("partition transition must be CrossSample-minted")
    replayed_application = CrossSamplePartitionTransition(
        status=subject.successor_application.status,
        predecessors=subject.successor_application.predecessors,
        successors=subject.successor_application.successors,
        lineage=subject.successor_application.lineage,
        reason=subject.successor_application.reason,
    )
    if replayed_application != subject.successor_application:
        raise ObservationRepairContractError(
            "repair subject partition transition is not canonical"
        )
    resolved_sample = resolve_canonical_sample_identity(
        subject.sample_identity.snapshot,
        subject.sample_identity.lookup_token,
        expected_identity=subject.sample_identity.identity,
    )
    if resolved_sample != subject.sample_identity:
        raise ObservationRepairContractError(
            "repair subject sample resolution is not canonical"
        )
    replayed_group = mint_cross_sample_group_identity(
        processing_request_id=subject.predecessor_group.processing_request_id,
        member_subjects=subject.predecessor_group.member_subjects,
        disposition=subject.predecessor_group.disposition,
    )
    if replayed_group != subject.predecessor_group:
        raise ObservationRepairContractError(
            "repair subject predecessor group is not canonical"
        )
    if type(subject.repair_epoch) is not int or subject.repair_epoch != 1:
        raise ObservationRepairContractError("repair epoch must be exactly 1")
    request_binding = (
        subject.logical_request.item.sample_stem,
        subject.logical_request.item.request,
    )
    if request_binding != (
        subject.request_identity.sample_stem,
        subject.request_identity.request,
    ):
        raise ObservationRepairContractError(
            "repair subject crosses exact request identity"
        )
    if (
        subject.logical_request.group_hypothesis_id
        != subject.predecessor_group.group_hypothesis_id
    ):
        raise ObservationRepairContractError(
            "repair subject crosses predecessor group identity"
        )
    if subject.member_subject not in subject.predecessor_group.member_subjects:
        raise ObservationRepairContractError(
            "repair subject member is absent from predecessor group"
        )
    if subject.predecessor_group not in subject.successor_application.successors:
        raise ObservationRepairContractError(
            "repair subject group is absent from partition transition"
        )
    if (
        subject.sample_identity.identity.sample_identity_id
        != subject.member_subject.canonical_sample_identity_id
    ):
        raise ObservationRepairContractError(
            "repair subject member crosses canonical sample identity"
        )
    if (
        subject.sample_identity.metadata.raw_stem
        != subject.request_identity.sample_stem
    ):
        raise ObservationRepairContractError(
            "repair subject sample metadata crosses exact Backfill sample"
        )
    if (
        subject.member_subject.processing_request_id
        != subject.predecessor_group.processing_request_id
    ):
        raise ObservationRepairContractError(
            "repair subject member crosses processing request"
        )
    expected = _subject_id(
        request_identity=subject.request_identity,
        logical_request=subject.logical_request,
        predecessor_group=subject.predecessor_group,
        member_subject=subject.member_subject,
        sample_identity=subject.sample_identity,
        successor_application=subject.successor_application,
        repair_epoch=subject.repair_epoch,
    )
    if subject.subject_id != expected:
        raise ObservationRepairContractError(
            "repair subject id is not bound to its complete content"
        )


def _subject_id(
    *,
    request_identity: OwnerBackfillRequestIdentity,
    logical_request: OwnerBackfillLogicalRequestMember,
    predecessor_group: CrossSampleGroupIdentity,
    member_subject: CrossSampleGroupMemberSubject,
    sample_identity: ResolvedCanonicalSampleIdentity,
    successor_application: CrossSamplePartitionTransition,
    repair_epoch: int,
) -> str:
    return typed_identity_digest(
        _SUBJECT_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_subject",
                "request_identity_id": request_identity.request_identity_id,
                "logical_request_id": logical_request.logical_request_id,
                "predecessor_group_hypothesis_id": (
                    predecessor_group.group_hypothesis_id
                ),
                "member_subject_id": member_subject.member_subject_id,
                "canonical_sample_identity_id": (
                    sample_identity.identity.sample_identity_id
                ),
                "sample_metadata_snapshot_id": sample_identity.snapshot.snapshot_id,
                "sample_raw_stem": sample_identity.metadata.raw_stem,
                "successor_application": _successor_application_basis(
                    successor_application
                ),
                "repair_epoch": repair_epoch,
            }
        ),
    )


def _successor_application_basis(
    application: CrossSamplePartitionTransition,
) -> dict[str, object]:
    return {
        "status": application.status,
        "reason": application.reason,
        "predecessors": [
            _group_identity_basis(group) for group in application.predecessors
        ],
        "successors": [
            _group_identity_basis(group) for group in application.successors
        ],
        "lineage": [
            {
                "lineage_edge_id": edge.lineage_edge_id,
                "predecessor_group_hypothesis_id": (
                    edge.predecessor_group_hypothesis_id
                ),
                "successor_group_hypothesis_id": edge.successor_group_hypothesis_id,
                "processing_request_id": edge.processing_request_id,
                "predecessor_identity_basis_hex": (
                    edge.predecessor_identity_basis_bytes.hex()
                ),
                "predecessor_group_hypothesis_ids": list(
                    edge.predecessor_group_hypothesis_ids
                ),
                "member_subject_ids": list(edge.member_subject_ids),
                "basis_hex": edge.basis_bytes.hex(),
            }
            for edge in application.lineage
        ],
    }


def _group_identity_basis(group: CrossSampleGroupIdentity) -> dict[str, object]:
    return {
        "group_hypothesis_id": group.group_hypothesis_id,
        "processing_request_id": group.processing_request_id,
        "member_subject_ids": [
            member.member_subject_id for member in group.member_subjects
        ],
        "basis_hex": group.basis_bytes.hex(),
        "disposition": group.disposition,
    }


def _no_repair_terminal_id(
    *,
    subject_id: str,
    authorization_id: str,
    candidate_id: str,
    repair_epoch: int,
    work_unit_key: str,
    physical_answer_id: str,
) -> str:
    return typed_identity_digest(
        _NO_REPAIR_TERMINAL_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_no_repair_terminal",
                "subject_id": subject_id,
                "authorization_id": authorization_id,
                "candidate_id": candidate_id,
                "repair_epoch": repair_epoch,
                "work_unit_key": work_unit_key,
                "physical_answer_id": physical_answer_id,
            }
        ),
    )


def _validate_no_repair_terminal(
    terminal: ObservationRepairNoRepairTerminal,
) -> None:
    if terminal.authorization_id is None or terminal.candidate_id is None:
        raise ObservationRepairContractError("no-repair terminal is candidate-scoped")
    _validate_typed_id(
        terminal.terminal_id,
        _NO_REPAIR_TERMINAL_PREFIX,
        "no-repair terminal_id",
    )
    _validate_typed_id(
        terminal.authorization_id,
        _AUTHORIZATION_PREFIX,
        "no-repair authorization_id",
    )
    _validate_typed_id(
        terminal.candidate_id,
        _CANDIDATE_PREFIX,
        "no-repair candidate_id",
    )
    if terminal.subject_id != terminal.candidate_id:
        raise ObservationRepairContractError(
            "candidate no-repair terminal crosses its subject"
        )
    if terminal.repair_epoch != 1:
        raise ObservationRepairContractError(
            "no-repair terminal repair epoch must be exactly 1"
        )
    if type(terminal.work_unit_key) is not str or not terminal.work_unit_key:
        raise ObservationRepairContractError(
            "no-repair terminal work_unit_key is required"
        )
    _validate_typed_id(
        terminal.physical_answer_id,
        _PHYSICAL_ANSWER_PREFIX,
        "no-repair physical_answer_id",
    )
    expected = _no_repair_terminal_id(
        subject_id=terminal.subject_id,
        authorization_id=terminal.authorization_id,
        candidate_id=terminal.candidate_id,
        repair_epoch=terminal.repair_epoch,
        work_unit_key=terminal.work_unit_key,
        physical_answer_id=terminal.physical_answer_id,
    )
    if terminal.terminal_id != expected:
        raise ObservationRepairContractError(
            "no-repair terminal id is not bound to its complete content"
        )


def _receipt_id(
    *,
    subject_id: str,
    repair_epoch: int,
    outcome: ObservationRepairOutcome,
    sample_stem: str,
    evidence: ObservationRepairEvidence | None,
    no_repair_terminal: ObservationRepairNoRepairTerminal | None = None,
    authorization_id: str | None = None,
    candidate_id: str | None = None,
) -> str:
    if (authorization_id is None) != (candidate_id is None):
        raise ObservationRepairContractError(
            "candidate receipt identity requires authorization and candidate ids"
        )
    candidate_receipt = candidate_id is not None
    if not candidate_receipt and no_repair_terminal is not None:
        raise ObservationRepairContractError("no-repair terminal is candidate-scoped")
    evidence_basis: object = None
    if evidence is not None:
        evidence_basis = {
            name: getattr(evidence, name)
            for name in ObservationRepairEvidence.__dataclass_fields__
        }
    payload: dict[str, object] = {
        "schema_version": "observation_repair_receipt",
        "repair_scope": "candidate" if candidate_receipt else "subject",
        "authorization_id": authorization_id,
        "candidate_id": candidate_id,
        "no_repair_terminal": None,
        "subject_id": subject_id,
        "repair_epoch": repair_epoch,
        "outcome": outcome,
        "sample_stem": sample_stem,
        "evidence": evidence_basis,
    }
    if no_repair_terminal is not None:
        payload["no_repair_terminal"] = {
            "terminal_id": no_repair_terminal.terminal_id,
            "subject_id": no_repair_terminal.subject_id,
            "authorization_id": no_repair_terminal.authorization_id,
            "candidate_id": no_repair_terminal.candidate_id,
            "repair_epoch": no_repair_terminal.repair_epoch,
            "work_unit_key": no_repair_terminal.work_unit_key,
            "physical_answer_id": no_repair_terminal.physical_answer_id,
        }
    return typed_identity_digest(
        _RECEIPT_PREFIX,
        canonical_identity_basis_bytes(payload),
    )


__all__ = (
    "ObservationRepairAuthorization",
    "ObservationRepairAuthorizationState",
    "ObservationRepairContractError",
    "ObservationRepairCandidate",
    "ObservationRepairEvidence",
    "ObservationRepairMultipleEligibleAuthorization",
    "ObservationRepairNoRepairTerminal",
    "ObservationRepairNotRunAuthorization",
    "ObservationRepairOutcome",
    "ObservationRepairRepairAuthorization",
    "ObservationRepairReceipt",
    "ObservationRepairSubject",
)
