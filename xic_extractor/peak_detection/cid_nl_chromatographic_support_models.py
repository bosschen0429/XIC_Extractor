"""Immutable values for one CID-NL chromatographic support event."""

from __future__ import annotations

import json
import math
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
    DdaCidNlPeakSupportContent,
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.peak_detection.observations import PeakObservationContent

SCHEMA_VERSION = "cid_nl_chromatographic_support"
ARTIFACT_SEMANTIC_ROLE = "cid_nl_chromatographic_support_event"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf"
RELATIONSHIP = "strict_nl_scans_within_one_physical_ms1_peak"

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

    support: DdaCidNlPeakSupportContent
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


@dataclass(frozen=True, slots=True)
class CidNlChromatographicSupportContent:
    """One physical MS1 support event backed by one or more exact DDA scans."""

    sample_key: str
    peak_observation: PeakObservationContent
    candidate_peaks: tuple[PeakObservationContent, ...]
    acquisition_mode: str
    policy: DdaCidNlPeakSupportPolicy
    scan_supports: tuple[DdaCidNlScanSupportMember, ...]
    strict_nl_scan_count: int
    chromatographic_support_event_count: int
    relationship: str

    def __post_init__(self) -> None:
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
        scan_numbers = tuple(
            member.support.dda_scan_number for member in self.scan_supports
        )
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
        if len(set(event_keys)) != len(event_keys):
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
    content_payload = {
        "sample_key": content.sample_key,
        "peak_observation_content_id": (
            content.peak_observation.peak_observation_content_id
        ),
        "candidate_peak_content_ids": tuple(
            peak.peak_observation_content_id for peak in content.candidate_peaks
        ),
        "acquisition_mode": content.acquisition_mode,
        "policy": asdict(content.policy),
        "scan_supports": tuple(
            {
                "dda_scan_number": member.support.dda_scan_number,
                "evidence": asdict(member.support.evidence),
                "configured_neutral_loss_da": member.configured_neutral_loss_da,
            }
            for member in content.scan_supports
        ),
        "strict_nl_scan_count": content.strict_nl_scan_count,
        "chromatographic_support_event_count": (
            content.chromatographic_support_event_count
        ),
        "relationship": content.relationship,
    }
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
) -> tuple[int, float]:
    return (
        member.support.dda_scan_number,
        member.configured_neutral_loss_da or 0.0,
    )


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
