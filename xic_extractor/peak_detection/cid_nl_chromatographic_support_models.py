"""Immutable values for one CID-NL chromatographic support event."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass

from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    mint_artifact_semantic_contract_ref,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    ACQUISITION_MODE,
    CidNlEvidenceRef,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
)

SCHEMA_VERSION = "cid_nl_chromatographic_support"
ARTIFACT_SEMANTIC_ROLE = "cid_nl_chromatographic_support_event"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf"
RELATIONSHIP = "strict_nl_scans_within_one_physical_ms1_peak"

_ARTIFACT_CONTENT_ID_PREFIX = "xic.artifact-content:"
_PEAK_CONTENT_ID_PREFIX = "xic.peak-observation-content:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_SEMANTIC_CONTRACT = mint_artifact_semantic_contract_ref(
    owner_namespace=("xic_extractor.peak_detection.cid_nl_chromatographic_support"),
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)


class CidNlChromatographicSupportContractError(ValueError):
    """Raised when scan support cannot form one physical-peak event."""


@dataclass(frozen=True, slots=True)
class DdaCidNlScanSupportMember:
    """One exact DDA scan relation retained inside the aggregate event."""

    dda_scan_number: int
    cid_nl_evidence_content_id: str
    dda_peak_support_content_id: str
    configured_neutral_loss_da: float | None = None

    def __post_init__(self) -> None:
        if self.configured_neutral_loss_da is not None and (
            isinstance(self.configured_neutral_loss_da, bool)
            or not isinstance(self.configured_neutral_loss_da, (float, int))
            or not math.isfinite(self.configured_neutral_loss_da)
            or self.configured_neutral_loss_da <= 0
        ):
            raise CidNlChromatographicSupportContractError(
                "configured loss must be positive and finite"
            )
        if (
            isinstance(self.dda_scan_number, bool)
            or not isinstance(self.dda_scan_number, int)
            or self.dda_scan_number <= 0
        ):
            raise CidNlChromatographicSupportContractError(
                "dda_scan_number must be a positive integer"
            )
        _validate_typed_digest_id(
            self.cid_nl_evidence_content_id,
            prefix=_ARTIFACT_CONTENT_ID_PREFIX,
            field="cid_nl_evidence_content_id",
        )
        _validate_typed_digest_id(
            self.dda_peak_support_content_id,
            prefix=_ARTIFACT_CONTENT_ID_PREFIX,
            field="dda_peak_support_content_id",
        )


@dataclass(frozen=True, slots=True)
class CidNlChromatographicSupportContent:
    """One physical MS1 support event backed by one or more exact DDA scans."""

    sample_key: str
    peak_observation_content_id: str
    candidate_peak_content_ids: tuple[str, ...]
    acquisition_mode: str
    policy: DdaCidNlPeakSupportPolicy
    scan_supports: tuple[DdaCidNlScanSupportMember, ...]
    strict_nl_scan_count: int
    chromatographic_support_event_count: int
    relationship: str

    def __post_init__(self) -> None:
        _validate_typed_digest_id(
            self.peak_observation_content_id,
            prefix=_PEAK_CONTENT_ID_PREFIX,
            field="peak_observation_content_id",
        )
        if not isinstance(self.candidate_peak_content_ids, tuple):
            raise CidNlChromatographicSupportContractError(
                "candidate_peak_content_ids must be a tuple"
            )
        candidate_ids = tuple(
            _validate_typed_digest_id(
                value,
                prefix=_PEAK_CONTENT_ID_PREFIX,
                field="candidate_peak_content_id",
            )
            for value in self.candidate_peak_content_ids
        )
        if not candidate_ids or candidate_ids != tuple(sorted(set(candidate_ids))):
            raise CidNlChromatographicSupportContractError(
                "candidate_peak_content_ids must be non-empty, unique, and sorted"
            )
        if self.peak_observation_content_id not in candidate_ids:
            raise CidNlChromatographicSupportContractError(
                "physical MS1 peak is missing from the candidate universe"
            )
        if self.acquisition_mode != ACQUISITION_MODE:
            raise CidNlChromatographicSupportContractError(
                f"acquisition_mode must be {ACQUISITION_MODE!r}"
            )
        if not isinstance(self.policy, DdaCidNlPeakSupportPolicy):
            raise CidNlChromatographicSupportContractError(
                "policy must be owner-normalized DDA CID-NL support policy"
            )
        if not isinstance(self.scan_supports, tuple) or not self.scan_supports:
            raise CidNlChromatographicSupportContractError(
                "missing strict DDA scan support"
            )
        if any(
            not isinstance(member, DdaCidNlScanSupportMember)
            for member in self.scan_supports
        ):
            raise CidNlChromatographicSupportContractError(
                "scan_supports must contain typed DDA scan support members"
            )
        canonical_members = tuple(sorted(self.scan_supports, key=_member_sort_key))
        if self.scan_supports != canonical_members:
            raise CidNlChromatographicSupportContractError(
                "scan_supports must be canonical-sorted"
            )
        scan_numbers = tuple(member.dda_scan_number for member in self.scan_supports)
        loss_values = tuple(
            member.configured_neutral_loss_da for member in self.scan_supports
        )
        if any(value is None for value in loss_values) and not all(
            value is None for value in loss_values
        ):
            raise CidNlChromatographicSupportContractError(
                "composite scan support loss dimensions must be complete"
            )
        event_keys = tuple(zip(scan_numbers, loss_values, strict=True))
        evidence_ids = tuple(
            member.cid_nl_evidence_content_id for member in self.scan_supports
        )
        support_ids = tuple(
            member.dda_peak_support_content_id for member in self.scan_supports
        )
        if (
            len(set(event_keys)) != len(event_keys)
            or len(set(evidence_ids)) != len(evidence_ids)
            or len(set(support_ids)) != len(support_ids)
        ):
            raise CidNlChromatographicSupportContractError(
                "duplicate DDA scan support cannot multiply one event"
            )
        if self.strict_nl_scan_count != len(set(scan_numbers)):
            raise CidNlChromatographicSupportContractError(
                "strict_nl_scan_count mismatches exact scan support"
            )
        if self.chromatographic_support_event_count != 1:
            raise CidNlChromatographicSupportContractError(
                "one physical MS1 peak must form exactly one support event"
            )
        if self.relationship != RELATIONSHIP:
            raise CidNlChromatographicSupportContractError(
                f"relationship must be {RELATIONSHIP!r}"
            )


@dataclass(frozen=True, slots=True)
class CidNlChromatographicSupportRef:
    """Immutable aggregate artifact with exact scan-support upstream refs."""

    content: CidNlChromatographicSupportContent
    artifact: EvidenceArtifactRef
    evidence_refs: tuple[CidNlEvidenceRef, ...] = ()


def canonical_cid_nl_chromatographic_support_bytes(
    content: CidNlChromatographicSupportContent,
) -> bytes:
    """Return canonical bytes for one chromatographic support event."""

    if not isinstance(content, CidNlChromatographicSupportContent):
        raise CidNlChromatographicSupportContractError(
            "canonical serialization requires chromatographic support content"
        )
    content_payload = asdict(content)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "content": content_payload,
    }
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CidNlChromatographicSupportContractError(
            "chromatographic support payload must be canonical JSON"
        ) from exc
    return (serialized + "\n").encode("utf-8")


def _member_sort_key(
    member: DdaCidNlScanSupportMember,
) -> tuple[int, str, str]:
    return (
        member.dda_scan_number,
        member.cid_nl_evidence_content_id,
        member.dda_peak_support_content_id,
    )


def _validate_typed_digest_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise CidNlChromatographicSupportContractError(
            f"{field} must use the {prefix!r} prefix"
        )
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise CidNlChromatographicSupportContractError(
            f"{field} must contain a SHA-256 digest"
        )
    return value


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "CidNlChromatographicSupportContent",
    "CidNlChromatographicSupportContractError",
    "CidNlChromatographicSupportRef",
    "DdaCidNlScanSupportMember",
    "RELATIONSHIP",
    "SCHEMA_VERSION",
    "canonical_cid_nl_chromatographic_support_bytes",
]
