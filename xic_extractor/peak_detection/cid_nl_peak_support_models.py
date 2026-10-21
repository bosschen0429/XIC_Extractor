"""Immutable values for one DDA CID-NL event-to-MS1-peak binding."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field

from xic_extractor.configuration.cid_nl_profile import PrecursorReferenceRequirement
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    mint_artifact_semantic_contract_ref,
)
from xic_extractor.mass_accuracy import neutral_loss_match
from xic_extractor.peak_detection.cid_nl_evidence import ACQUISITION_MODE
from xic_extractor.precursor_evidence import (
    PrecursorLossAssessment,
)

SCHEMA_VERSION = "dda_cid_nl_peak_support"
ARTIFACT_SEMANTIC_ROLE = "dda_cid_nl_peak_support"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf"
RELATIONSHIP = "strict_nl_inside_ms1_peak_scope"

_ARTIFACT_CONTENT_ID_PREFIX = "xic.artifact-content:"
_PEAK_CONTENT_ID_PREFIX = "xic.peak-observation-content:"
_SAMPLE_ID_PREFIX = "xic.sample:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_SEMANTIC_CONTRACT = mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.peak_detection.cid_nl_peak_support",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)


class DdaCidNlPeakSupportContractError(ValueError):
    """Raised when a DDA CID-NL event cannot bind to one physical MS1 peak."""


@dataclass(frozen=True, slots=True)
class DdaCidNlPeakSupportPolicy:
    """Owner-normalized matching thresholds already present in method inputs."""

    precursor_tolerance_da: float
    neutral_loss_tolerance_ppm: float
    precursor_reference_requirement: PrecursorReferenceRequirement = "independent_mass"

    def __post_init__(self) -> None:
        if self.precursor_reference_requirement not in {
            "independent_mass",
            "recorded_precursor",
        }:
            raise DdaCidNlPeakSupportContractError(
                "invalid precursor reference requirement"
            )
        precursor_tolerance = _finite_float(
            self.precursor_tolerance_da,
            field="precursor_tolerance_da",
        )
        neutral_loss_tolerance = _finite_float(
            self.neutral_loss_tolerance_ppm,
            field="neutral_loss_tolerance_ppm",
        )
        if precursor_tolerance <= 0.0:
            raise DdaCidNlPeakSupportContractError(
                "precursor_tolerance_da must be positive"
            )
        if neutral_loss_tolerance <= 0.0:
            raise DdaCidNlPeakSupportContractError(
                "neutral_loss_tolerance_ppm must be positive"
            )
        object.__setattr__(self, "precursor_tolerance_da", precursor_tolerance)
        object.__setattr__(
            self,
            "neutral_loss_tolerance_ppm",
            neutral_loss_tolerance,
        )

    def accepts_precursor(self, assessment: PrecursorLossAssessment) -> bool:
        return assessment.supports_reference(self.precursor_reference_requirement)


@dataclass(frozen=True, slots=True)
class DdaCidNlPeakSupportContent:
    """Immutable method relation between one DDA event and one MS1 peak."""

    canonical_sample_identity_id: str
    cid_nl_evidence_content_id: str
    peak_observation_content_id: str
    candidate_peak_content_ids: tuple[str, ...]
    acquisition_mode: str
    dda_scan_number: int
    scan_rt_min: float
    peak_rt_min: float
    peak_rt_max: float
    precursor_mz: float
    peak_mz: float
    precursor_delta_da: float
    observed_loss_error_ppm: float
    observed_product_mz: float
    configured_neutral_loss_da: float
    policy: DdaCidNlPeakSupportPolicy
    relationship: str
    observed_loss_error_da: float = field(init=False)
    neutral_loss_window_da: float = field(init=False)

    def __post_init__(self) -> None:
        _validate_typed_digest_id(
            self.canonical_sample_identity_id,
            prefix=_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
        _validate_typed_digest_id(
            self.cid_nl_evidence_content_id,
            prefix=_ARTIFACT_CONTENT_ID_PREFIX,
            field="cid_nl_evidence_content_id",
        )
        _validate_typed_digest_id(
            self.peak_observation_content_id,
            prefix=_PEAK_CONTENT_ID_PREFIX,
            field="peak_observation_content_id",
        )
        if not isinstance(self.candidate_peak_content_ids, tuple):
            raise DdaCidNlPeakSupportContractError(
                "candidate_peak_content_ids must be a tuple"
            )
        candidate_content_ids = tuple(
            _validate_typed_digest_id(
                value,
                prefix=_PEAK_CONTENT_ID_PREFIX,
                field="candidate_peak_content_id",
            )
            for value in self.candidate_peak_content_ids
        )
        if not candidate_content_ids or candidate_content_ids != tuple(
            sorted(set(candidate_content_ids))
        ):
            raise DdaCidNlPeakSupportContractError(
                "candidate_peak_content_ids must be non-empty, unique, and sorted"
            )
        if self.peak_observation_content_id not in candidate_content_ids:
            raise DdaCidNlPeakSupportContractError(
                "selected peak observation is missing from the candidate universe"
            )
        if self.acquisition_mode != ACQUISITION_MODE:
            raise DdaCidNlPeakSupportContractError(
                f"acquisition_mode must be {ACQUISITION_MODE!r}"
            )
        if (
            isinstance(self.dda_scan_number, bool)
            or not isinstance(self.dda_scan_number, int)
            or self.dda_scan_number <= 0
        ):
            raise DdaCidNlPeakSupportContractError(
                "dda_scan_number must be a positive integer"
            )
        scan_rt = _finite_float(self.scan_rt_min, field="scan_rt_min")
        peak_rt_min = _finite_float(self.peak_rt_min, field="peak_rt_min")
        peak_rt_max = _finite_float(self.peak_rt_max, field="peak_rt_max")
        precursor_mz = _finite_float(self.precursor_mz, field="precursor_mz")
        peak_mz = _finite_float(self.peak_mz, field="peak_mz")
        precursor_delta = _finite_float(
            self.precursor_delta_da,
            field="precursor_delta_da",
        )
        observed_loss_error = _finite_float(
            self.observed_loss_error_ppm,
            field="observed_loss_error_ppm",
        )
        observed_product_mz = _finite_float(
            self.observed_product_mz, field="observed_product_mz"
        )
        configured_loss_da = _finite_float(
            self.configured_neutral_loss_da, field="configured_neutral_loss_da"
        )
        if peak_rt_min >= peak_rt_max:
            raise DdaCidNlPeakSupportContractError(
                "peak_rt_min must be less than peak_rt_max"
            )
        if not peak_rt_min <= scan_rt <= peak_rt_max:
            raise DdaCidNlPeakSupportContractError(
                "DDA scan RT is outside the physical MS1 peak scope"
            )
        if precursor_mz <= 0.0 or peak_mz <= 0.0:
            raise DdaCidNlPeakSupportContractError(
                "precursor_mz and peak_mz must be positive"
            )
        expected_delta = precursor_mz - peak_mz
        if not math.isclose(
            precursor_delta,
            expected_delta,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise DdaCidNlPeakSupportContractError(
                "precursor_delta_da mismatches the bound observations"
            )
        if not isinstance(self.policy, DdaCidNlPeakSupportPolicy):
            raise DdaCidNlPeakSupportContractError(
                "policy must be owner-normalized DDA CID-NL support policy"
            )
        if abs(precursor_delta) > self.policy.precursor_tolerance_da:
            raise DdaCidNlPeakSupportContractError(
                "DDA precursor is outside the MS1 peak m/z tolerance"
            )
        loss_error_da, loss_window_da, matches = neutral_loss_match(
            precursor_mz,
            observed_product_mz,
            configured_loss_da,
            self.policy.neutral_loss_tolerance_ppm,
        )
        if not matches:
            raise DdaCidNlPeakSupportContractError(
                "CID-NL evidence does not satisfy the strict neutral-loss tolerance"
            )
        object.__setattr__(self, "observed_loss_error_da", loss_error_da)
        object.__setattr__(self, "neutral_loss_window_da", loss_window_da)
        if self.relationship != RELATIONSHIP:
            raise DdaCidNlPeakSupportContractError(
                f"relationship must be {RELATIONSHIP!r}"
            )
        for field_name, value in (
            ("scan_rt_min", scan_rt),
            ("peak_rt_min", peak_rt_min),
            ("peak_rt_max", peak_rt_max),
            ("precursor_mz", precursor_mz),
            ("peak_mz", peak_mz),
            ("precursor_delta_da", precursor_delta),
            ("observed_loss_error_ppm", observed_loss_error),
        ):
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class DdaCidNlPeakSupportRef:
    """One immutable DDA-to-peak support artifact with exact upstream refs."""

    content: DdaCidNlPeakSupportContent
    artifact: EvidenceArtifactRef


def canonical_dda_cid_nl_peak_support_bytes(
    reference: DdaCidNlPeakSupportRef,
) -> bytes:
    """Return canonical bytes for one immutable support reference."""

    if not isinstance(reference, DdaCidNlPeakSupportRef):
        raise DdaCidNlPeakSupportContractError(
            "canonical serialization requires DdaCidNlPeakSupportRef"
        )
    return _support_bytes(reference.content)


def _support_bytes(content: DdaCidNlPeakSupportContent) -> bytes:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "content": asdict(content),
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
        raise DdaCidNlPeakSupportContractError(
            "DDA CID-NL peak support payload must be canonical JSON"
        ) from exc
    return (serialized + "\n").encode("utf-8")


def _finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DdaCidNlPeakSupportContractError(f"{field} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise DdaCidNlPeakSupportContractError(f"{field} must be a finite number")
    return 0.0 if normalized == 0.0 else normalized


def _validate_typed_digest_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise DdaCidNlPeakSupportContractError(
            f"{field} must use the {prefix!r} prefix"
        )
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise DdaCidNlPeakSupportContractError(f"{field} must contain a SHA-256 digest")
    return value


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "DdaCidNlPeakSupportContent",
    "DdaCidNlPeakSupportContractError",
    "DdaCidNlPeakSupportPolicy",
    "DdaCidNlPeakSupportRef",
    "RELATIONSHIP",
    "SCHEMA_VERSION",
    "canonical_dda_cid_nl_peak_support_bytes",
]
