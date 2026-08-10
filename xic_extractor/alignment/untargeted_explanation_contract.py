"""Presentation-neutral contract for the Untargeted explanation index."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import final

from xic_extractor.alignment.backfill_evidence_contract import (
    BackfillEvidenceRecord,
    EvidenceClaim,
    EvidenceClaimState,
    EvidenceQueryResult,
)
from xic_extractor.processing_identity import (
    FileContentFingerprint,
    ProcessingAttemptIdentity,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

EXPLANATION_INDEX_SCHEMA_VERSION = "untargeted_explanation_index_v2"

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_CROSS_PLATFORM_FORBIDDEN_PATH_CHARS = frozenset('<>:"|?*\\#%')
_SECTION_MINT_TOKEN = object()
_SUBJECT_MINT_TOKEN = object()
_ENTRY_MINT_TOKEN = object()
_RUN_BINDING_MINT_TOKEN = object()
_INDEX_MINT_TOKEN = object()


class ExplanationTopic(str, Enum):
    DISCOVERY = "discovery"
    ALIGNMENT = "alignment"
    BACKFILL = "backfill"
    MATRIX = "matrix"


class ExplanationLocatorKind(str, Enum):
    DETAIL = "detail"
    OVERLAY = "overlay"


EXPLANATION_INFORMATION_REQUIREMENTS: Mapping[ExplanationTopic, tuple[str, ...]] = (
    MappingProxyType(
        {
            ExplanationTopic.DISCOVERY: (
                "candidate_universe_binding",
                "selected_peak_hypothesis_id",
                "selection_outcome",
                "selection_reason",
            ),
            ExplanationTopic.ALIGNMENT: (
                "group_hypothesis_id",
                "member_peak_hypothesis_ids",
                "grouping_disposition",
                "representative_group_hypothesis_id",
                "decision_reason",
            ),
            ExplanationTopic.BACKFILL: (
                "evaluation_scope_binding",
                "evaluated",
                "cell_disposition",
                "decision_reason",
                "peak_hypothesis_matrix_cell_binding",
            ),
            ExplanationTopic.MATRIX: (
                "matrix_row_binding",
                "canonical_sample_identity",
                "matrix_value",
            ),
        }
    )
)


@final
@dataclass(frozen=True, slots=True)
class ExplanationLocator:
    """Portable, relative navigation only; never evidence or authority."""

    kind: ExplanationLocatorKind
    relative_path: str

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ExplanationLocator is final")

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ExplanationLocatorKind):
            raise TypeError("locator kind must be an ExplanationLocatorKind")
        path = self.relative_path
        if not isinstance(path, str) or not path or path != path.strip():
            raise ValueError("explanation locator must be non-empty canonical text")
        if any(ord(char) < 32 for char in path):
            raise ValueError("explanation locator must not contain control characters")
        if _URL_SCHEME_RE.match(path):
            raise ValueError("explanation locator must not contain a URL scheme")
        if any(char in _CROSS_PLATFORM_FORBIDDEN_PATH_CHARS for char in path):
            raise ValueError(
                "explanation locator must use portable POSIX path characters"
            )
        posix_path = PurePosixPath(path)
        if posix_path.is_absolute() or PureWindowsPath(path).drive:
            raise ValueError("explanation locator must be relative")
        if any(part in {"", ".", ".."} for part in posix_path.parts):
            raise ValueError("explanation locator must not traverse directories")
        if posix_path.as_posix() != path:
            raise ValueError("explanation locator must use canonical POSIX separators")


@final
@dataclass(frozen=True, slots=True)
class MatrixExplanationPayload:
    written: bool
    matrix_value: str | None

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("MatrixExplanationPayload is final")

    def __post_init__(self) -> None:
        if not isinstance(self.written, bool):
            raise TypeError("written must be bool")
        if self.matrix_value is not None and not isinstance(self.matrix_value, str):
            raise TypeError("matrix_value must be text or None")
        if self.written != bool(self.matrix_value):
            raise ValueError(
                "matrix_value must be present exactly when the Matrix cell is written"
            )


@final
@dataclass(frozen=True, slots=True)
class DiscoveryExplanationPayload:
    candidate_universe_artifact_id: str
    candidate_peak_content_ids: tuple[str, ...]
    selected_peak_hypothesis_id: str | None
    selection_outcome: str
    selection_reasons: tuple[str, ...]

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("DiscoveryExplanationPayload is final")

    def __post_init__(self) -> None:
        _require_payload_text(
            self.candidate_universe_artifact_id,
            field="candidate_universe_artifact_id",
        )
        _require_unique_payload_texts(
            self.candidate_peak_content_ids,
            field="candidate_peak_content_ids",
        )
        if self.selected_peak_hypothesis_id is not None:
            _require_payload_text(
                self.selected_peak_hypothesis_id,
                field="selected_peak_hypothesis_id",
            )
        _require_payload_text(self.selection_outcome, field="selection_outcome")
        _require_unique_payload_texts(
            self.selection_reasons,
            field="selection_reasons",
            allow_empty=False,
        )


@final
@dataclass(frozen=True, slots=True)
class AlignmentExplanationPayload:
    group_hypothesis_id: str
    member_peak_hypothesis_ids: tuple[str, ...]
    sample_member_peak_hypothesis_id: str | None
    grouping_disposition: str
    representative_group_hypothesis_id: str
    decision_reasons: tuple[str, ...]

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("AlignmentExplanationPayload is final")

    def __post_init__(self) -> None:
        _require_payload_text(self.group_hypothesis_id, field="group_hypothesis_id")
        _require_unique_payload_texts(
            self.member_peak_hypothesis_ids,
            field="member_peak_hypothesis_ids",
            allow_empty=False,
        )
        if self.sample_member_peak_hypothesis_id is not None:
            _require_payload_text(
                self.sample_member_peak_hypothesis_id,
                field="sample_member_peak_hypothesis_id",
            )
            if (
                self.sample_member_peak_hypothesis_id
                not in self.member_peak_hypothesis_ids
            ):
                raise ValueError(
                    "sample member must belong to the Alignment group membership"
                )
        _require_payload_text(
            self.grouping_disposition,
            field="grouping_disposition",
        )
        _require_payload_text(
            self.representative_group_hypothesis_id,
            field="representative_group_hypothesis_id",
        )
        _require_unique_payload_texts(
            self.decision_reasons,
            field="decision_reasons",
            allow_empty=False,
        )


@final
@dataclass(frozen=True, slots=True)
class BackfillExplanationPayload:
    evaluated: bool
    disposition: str
    production_status: str
    write_matrix_value: bool
    policy_version: str
    matched_candidate_peak_content_id: str | None
    decision_reasons: tuple[str, ...]

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("BackfillExplanationPayload is final")

    def __post_init__(self) -> None:
        if not isinstance(self.evaluated, bool):
            raise TypeError("evaluated must be bool")
        if not isinstance(self.write_matrix_value, bool):
            raise TypeError("write_matrix_value must be bool")
        _require_payload_text(self.disposition, field="disposition")
        _require_payload_text(self.production_status, field="production_status")
        _require_payload_text(self.policy_version, field="policy_version")
        if self.matched_candidate_peak_content_id is not None:
            _require_payload_text(
                self.matched_candidate_peak_content_id,
                field="matched_candidate_peak_content_id",
            )
        _require_unique_payload_texts(
            self.decision_reasons,
            field="decision_reasons",
            allow_empty=False,
        )


ExplanationPayload = (
    DiscoveryExplanationPayload
    | AlignmentExplanationPayload
    | BackfillExplanationPayload
    | MatrixExplanationPayload
)


def _require_payload_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be non-empty canonical text")
    return value


def _require_unique_payload_texts(
    values: object,
    *,
    field: str,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value.strip() or value != value.strip()
        for value in values
    ):
        raise ValueError(f"{field} must contain canonical text values")
    if not allow_empty and not values:
        raise ValueError(f"{field} must not be empty")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field} must be unique and canonical-sorted")
    return values


@final
@dataclass(frozen=True, slots=True)
class ExplanationSection:
    topic: ExplanationTopic
    source_owner: str
    claim: EvidenceClaim
    payload: ExplanationPayload | None
    missing_requirements: tuple[str, ...]
    supporting_claims: tuple[EvidenceClaim, ...] = ()
    locators: tuple[ExplanationLocator, ...] = ()
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ExplanationSection is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _SECTION_MINT_TOKEN:
            raise ValueError("ExplanationSection must be minted by its owner adapter")
        if not isinstance(self.topic, ExplanationTopic):
            raise TypeError("topic must be an ExplanationTopic")
        if not isinstance(self.source_owner, str) or not self.source_owner.strip():
            raise ValueError("source_owner must be non-empty text")
        if not isinstance(self.claim, EvidenceClaim):
            raise TypeError("claim must be an EvidenceClaim")
        if len(self.missing_requirements) != len(set(self.missing_requirements)):
            raise ValueError("missing explanation requirements must be unique")
        required = set(EXPLANATION_INFORMATION_REQUIREMENTS[self.topic])
        if not set(self.missing_requirements).issubset(required):
            raise ValueError("unknown missing requirement for explanation topic")
        if any(
            not isinstance(claim, EvidenceClaim) for claim in self.supporting_claims
        ):
            raise TypeError("supporting_claims must contain EvidenceClaim values")
        supporting_subjects = tuple(claim.subject for claim in self.supporting_claims)
        if len(supporting_subjects) != len(set(supporting_subjects)):
            raise ValueError("supporting claim subjects must be unique")
        if any(
            not isinstance(locator, ExplanationLocator) for locator in self.locators
        ):
            raise TypeError("locators must contain ExplanationLocator values")
        locator_keys = tuple((item.kind, item.relative_path) for item in self.locators)
        if len(locator_keys) != len(set(locator_keys)):
            raise ValueError("explanation locators must be unique")

        claim_available = self.claim.state in {
            EvidenceClaimState.OWNER_REHYDRATED,
            EvidenceClaimState.PRODUCT_AUTHORIZED,
        }
        if self.payload is None:
            if claim_available:
                raise ValueError("an available explanation claim requires a payload")
            if not self.missing_requirements:
                raise ValueError(
                    "an unavailable explanation requires explicit missing information"
                )
        else:
            if not claim_available or self.missing_requirements:
                raise ValueError(
                    "an explanation payload requires an available claim and no "
                    "missing facts"
                )
            expected_payload_type = {
                ExplanationTopic.DISCOVERY: DiscoveryExplanationPayload,
                ExplanationTopic.ALIGNMENT: AlignmentExplanationPayload,
                ExplanationTopic.BACKFILL: BackfillExplanationPayload,
                ExplanationTopic.MATRIX: MatrixExplanationPayload,
            }[self.topic]
            if not isinstance(self.payload, expected_payload_type):
                raise TypeError(
                    f"{self.topic.value} explanation payload has the wrong type"
                )


@final
@dataclass(frozen=True, slots=True)
class ExplanationSubject:
    processing_attempt: ProcessingAttemptIdentity
    matrix_row_index: int
    matrix_sample_index: int
    matrix_row_binding_sha256: str
    matrix_row_identity_basis: str
    peak_hypothesis_id: str
    source_feature_family_id: str
    sample: ResolvedCanonicalSampleIdentity
    sample_stem: str
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ExplanationSubject is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _SUBJECT_MINT_TOKEN:
            raise ValueError(
                "ExplanationSubject must be minted from Matrix owner output"
            )
        if not isinstance(self.processing_attempt, ProcessingAttemptIdentity):
            raise TypeError("processing_attempt must be a ProcessingAttemptIdentity")
        if (
            isinstance(self.matrix_row_index, bool)
            or not isinstance(self.matrix_row_index, int)
            or self.matrix_row_index <= 0
        ):
            raise ValueError("matrix_row_index must be a positive integer")
        if (
            isinstance(self.matrix_sample_index, bool)
            or not isinstance(self.matrix_sample_index, int)
            or self.matrix_sample_index <= 0
        ):
            raise ValueError("matrix_sample_index must be a positive integer")
        if _SHA256_RE.fullmatch(self.matrix_row_binding_sha256) is None:
            raise ValueError("matrix_row_binding_sha256 must be lowercase SHA-256")
        for name, value in (
            ("matrix_row_identity_basis", self.matrix_row_identity_basis),
            ("peak_hypothesis_id", self.peak_hypothesis_id),
            ("source_feature_family_id", self.source_feature_family_id),
            ("sample_stem", self.sample_stem),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
        if not isinstance(self.sample, ResolvedCanonicalSampleIdentity):
            raise TypeError("sample must be a ResolvedCanonicalSampleIdentity")
        if self.sample.lookup_token != self.sample_stem:
            raise ValueError("sample_stem must equal the owner-resolved lookup token")

    @property
    def route_key(self) -> tuple[str, str]:
        return (
            self.peak_hypothesis_id,
            self.sample.identity.sample_identity_id,
        )


@final
@dataclass(frozen=True, slots=True)
class ExplanationIndexEntry:
    subject: ExplanationSubject
    sections: tuple[ExplanationSection, ...]
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ExplanationIndexEntry is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _ENTRY_MINT_TOKEN:
            raise ValueError("ExplanationIndexEntry must be minted by the index owner")
        if not isinstance(self.subject, ExplanationSubject):
            raise TypeError("subject must be an ExplanationSubject")
        topics = tuple(section.topic for section in self.sections)
        if topics != tuple(ExplanationTopic):
            raise ValueError("entry sections must contain the four canonical topics")

    def section(self, topic: ExplanationTopic) -> ExplanationSection:
        for section in self.sections:
            if section.topic is topic:
                return section
        raise KeyError(topic)


@final
@dataclass(frozen=True, slots=True)
class ExplanationRunBinding:
    processing_attempt: ProcessingAttemptIdentity
    attempt_claim: EvidenceClaim
    matrix_artifact_claim: EvidenceClaim
    matrix_run_binding_claim: EvidenceClaim
    runtime_finalization_claim: EvidenceClaim
    matrix_fingerprint: FileContentFingerprint | None
    matrix_identity_fingerprint: FileContentFingerprint | None
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ExplanationRunBinding is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RUN_BINDING_MINT_TOKEN:
            raise ValueError("ExplanationRunBinding must be minted by the index owner")
        if not isinstance(self.processing_attempt, ProcessingAttemptIdentity):
            raise TypeError("processing_attempt must be a ProcessingAttemptIdentity")
        for claim in (
            self.attempt_claim,
            self.matrix_artifact_claim,
            self.matrix_run_binding_claim,
            self.runtime_finalization_claim,
        ):
            if not isinstance(claim, EvidenceClaim):
                raise TypeError("run binding claims must be EvidenceClaim values")
        if (
            self.attempt_claim.subject != "processing_attempt_identity"
            or self.attempt_claim.state is not EvidenceClaimState.OWNER_REHYDRATED
        ):
            raise ValueError("attempt claim must preserve owner-rehydrated identity")
        if self.matrix_artifact_claim.subject != "matrix_artifact_bytes":
            raise ValueError("Matrix artifact claim has the wrong subject")
        fingerprints = (
            self.matrix_fingerprint,
            self.matrix_identity_fingerprint,
        )
        if self.matrix_artifact_claim.state is EvidenceClaimState.BYTE_BOUND:
            if any(
                not isinstance(item, FileContentFingerprint) for item in fingerprints
            ):
                raise ValueError("a byte-bound Matrix claim requires both fingerprints")
        elif self.matrix_artifact_claim.state is EvidenceClaimState.UNAVAILABLE:
            if any(item is not None for item in fingerprints):
                raise ValueError(
                    "an unavailable Matrix artifact claim cannot retain fingerprints"
                )
        else:
            raise ValueError("Matrix artifact claim must be byte_bound or unavailable")
        if (
            self.matrix_run_binding_claim.subject != "matrix_run_binding"
            or self.matrix_run_binding_claim.state is not EvidenceClaimState.UNAVAILABLE
        ):
            raise ValueError("Matrix-to-run binding must remain explicitly unavailable")
        if (
            self.runtime_finalization_claim.subject != "runtime_finalization"
            or self.runtime_finalization_claim.state
            is not EvidenceClaimState.UNAVAILABLE
        ):
            raise ValueError("runtime finalization must remain explicitly unavailable")


@final
@dataclass(frozen=True, slots=True)
class UntargetedExplanationIndex:
    schema_version: str
    run_binding: ExplanationRunBinding
    entries: tuple[ExplanationIndexEntry, ...]
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("UntargetedExplanationIndex is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _INDEX_MINT_TOKEN:
            raise ValueError("UntargetedExplanationIndex must be minted by its owner")
        if self.schema_version != EXPLANATION_INDEX_SCHEMA_VERSION:
            raise ValueError("unsupported Untargeted explanation index schema")
        if not isinstance(self.run_binding, ExplanationRunBinding):
            raise TypeError("run_binding must be an ExplanationRunBinding")
        expected_order = tuple(
            sorted(
                self.entries,
                key=lambda item: (
                    item.subject.matrix_row_index,
                    item.subject.matrix_sample_index,
                ),
            )
        )
        if self.entries != expected_order:
            raise ValueError("explanation entries must use deterministic owner order")
        keys = tuple(entry.subject.route_key for entry in self.entries)
        if len(keys) != len(set(keys)):
            raise ValueError("explanation index routes must be unique")
        if any(
            entry.subject.processing_attempt != self.run_binding.processing_attempt
            for entry in self.entries
        ):
            raise ValueError("every explanation route must bind the same run attempt")


@final
@dataclass(frozen=True, slots=True)
class ExplanationLookupRequest:
    peak_hypothesis_id: str
    sample: ResolvedCanonicalSampleIdentity
    locators: tuple[ExplanationLocator, ...] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ExplanationLookupRequest is final")

    def __post_init__(self) -> None:
        if not isinstance(self.peak_hypothesis_id, str) or not self.peak_hypothesis_id:
            raise ValueError("peak_hypothesis_id must be non-empty text")
        if not isinstance(self.sample, ResolvedCanonicalSampleIdentity):
            raise TypeError("sample must be a ResolvedCanonicalSampleIdentity")
        if any(
            not isinstance(locator, ExplanationLocator) for locator in self.locators
        ):
            raise TypeError("locators must contain ExplanationLocator values")

    @property
    def route_key(self) -> tuple[str, str]:
        return (
            self.peak_hypothesis_id,
            self.sample.identity.sample_identity_id,
        )


@final
@dataclass(frozen=True, slots=True)
class BackfillExplanationInput:
    route_key: tuple[str, str]
    query_result: EvidenceQueryResult[BackfillEvidenceRecord]

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("BackfillExplanationInput is final")

    def __post_init__(self) -> None:
        if (
            not isinstance(self.route_key, tuple)
            or len(self.route_key) != 2
            or any(not isinstance(value, str) or not value for value in self.route_key)
        ):
            raise ValueError("Backfill explanation route_key must contain two IDs")
        if not isinstance(self.query_result, EvidenceQueryResult):
            raise TypeError("query_result must be an EvidenceQueryResult")


__all__ = (
    "EXPLANATION_INDEX_SCHEMA_VERSION",
    "EXPLANATION_INFORMATION_REQUIREMENTS",
    "AlignmentExplanationPayload",
    "BackfillExplanationPayload",
    "BackfillExplanationInput",
    "DiscoveryExplanationPayload",
    "ExplanationIndexEntry",
    "ExplanationLocator",
    "ExplanationLocatorKind",
    "ExplanationLookupRequest",
    "ExplanationPayload",
    "ExplanationRunBinding",
    "ExplanationSection",
    "ExplanationSubject",
    "ExplanationTopic",
    "MatrixExplanationPayload",
    "UntargetedExplanationIndex",
)
