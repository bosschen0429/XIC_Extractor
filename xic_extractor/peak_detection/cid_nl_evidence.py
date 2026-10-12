"""Canonical no-write CID-NL evidence references for the bounded tracer.

The owner freezes only scientific fields shared by Targeted product evidence
and Untargeted Discovery seeds.  Workflow constraints, candidate identity,
selection, and delivery authority remain in their respective adapters.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass

from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    mint_artifact_semantic_contract_ref,
    mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.precursor_evidence import (
    Ms1PrecursorObservation,
    PrecursorLossAssessment,
    PrecursorMzBasis,
    assess_precursor_loss,
    isolated_precursor_mz,
    precursor_inference_bounds,
    precursor_isolation_membership,
    supports_direct_nl,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    IdentityContractError,
    ProcessingOwnerReferences,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "cid_nl_evidence_observation"
ARTIFACT_SEMANTIC_ROLE = "canonical_cid_nl_observation"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf"
ACQUISITION_MODE = "dda_ddms2"

_SAMPLE_ID_PREFIX = "xic.sample:"
_ACQUISITION_SOURCE_ID_PREFIX = "xic.acquisition-source:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_SEMANTIC_CONTRACT = mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.peak_detection.cid_nl_evidence",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)


class CidNlEvidenceContractError(ValueError):
    """Raised when canonical CID-NL evidence is forged or rebound."""


@dataclass(frozen=True, slots=True)
class CidNlEvidenceObservation:
    """CID-NL facts with an explicit measured or product-derived precursor.

    Inferred loss is a derivation with no independent error measurement.
    ``scan_precursor_mz`` retains the actual selected scan value.
    """

    canonical_sample_identity_id: str
    acquisition_source_id: str
    acquisition_mode: str
    scan_number: int
    precursor_mz: float
    product_mz: float
    configured_neutral_loss_da: float
    observed_neutral_loss_da: float
    observed_loss_error_ppm: float | None
    scan_rt_min: float
    product_intensity: float
    precursor_mz_basis: PrecursorMzBasis
    scan_precursor_mz: float
    precursor_inference_tolerance_da: float | None
    precursor_metadata: Ms2PrecursorMetadata | None = None
    ms1_precursor_observation: Ms1PrecursorObservation | None = None

    @property
    def support_rt_min(self) -> float:
        """Both the parent ion observation and its MS2 event must fit the peak."""
        parent = self.ms1_precursor_observation
        return self.scan_rt_min if parent is None else parent.rt_min

    def assess_precursor_loss(self, tolerance_ppm: float) -> PrecursorLossAssessment:
        return assess_precursor_loss(
            basis=self.precursor_mz_basis,
            precursor_mz=self.precursor_mz,
            product_mz=self.product_mz,
            neutral_loss_da=self.configured_neutral_loss_da,
            observed_loss_da=self.observed_neutral_loss_da,
            observed_loss_error_ppm=self.observed_loss_error_ppm,
            tolerance_ppm=tolerance_ppm,
            selected_mz=self.scan_precursor_mz,
            metadata=self.precursor_metadata,
            ms1_observation=self.ms1_precursor_observation,
        )

    @property
    def reference_product_mz(self) -> float:
        """The product in the frame of ``precursor_mz``, for peak-level loss checks.

        An isotopologue selection keeps its ¹³C on the measured product.
        """
        return self.product_mz - (
            isolated_precursor_mz(self.precursor_mz_basis, self.precursor_mz)
            - self.precursor_mz
        )

    @property
    def supports_direct_nl(self) -> bool:
        return supports_direct_nl(
            self.precursor_mz_basis,
            self.precursor_mz,
            self.scan_precursor_mz,
            self.precursor_metadata,
        )

    def __post_init__(self) -> None:
        _validate_typed_id(
            self.canonical_sample_identity_id,
            prefix=_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
        _validate_typed_id(
            self.acquisition_source_id,
            prefix=_ACQUISITION_SOURCE_ID_PREFIX,
            field="acquisition_source_id",
        )
        if self.acquisition_mode != ACQUISITION_MODE:
            raise CidNlEvidenceContractError(
                f"acquisition_mode must be {ACQUISITION_MODE!r}"
            )
        if (
            isinstance(self.scan_number, bool)
            or not isinstance(self.scan_number, int)
            or self.scan_number <= 0
        ):
            raise CidNlEvidenceContractError("scan_number must be a positive integer")
        for field in (
            "precursor_mz",
            "product_mz",
            "configured_neutral_loss_da",
            "observed_neutral_loss_da",
            "scan_rt_min",
            "product_intensity",
            "scan_precursor_mz",
        ):
            object.__setattr__(
                self,
                field,
                _finite_float(getattr(self, field), field=field),
            )
        if self.precursor_mz <= 0.0 or self.product_mz <= 0.0:
            raise CidNlEvidenceContractError(
                "precursor_mz and product_mz must be positive"
            )
        if self.scan_precursor_mz <= 0.0:
            raise CidNlEvidenceContractError("scan_precursor_mz must be positive")
        if self.precursor_metadata is not None:
            if not isinstance(self.precursor_metadata, Ms2PrecursorMetadata):
                raise CidNlEvidenceContractError(
                    "precursor_metadata must be typed vendor facts"
                )
            if self.precursor_metadata.charge not in (None, 1):
                raise CidNlEvidenceContractError(
                    "CID-NL evidence requires singly charged or unknown charge"
                )
        if self.precursor_mz_basis == "scan_precursor":
            if self.scan_precursor_mz != self.precursor_mz:
                raise CidNlEvidenceContractError(
                    "direct scan_precursor_mz must equal precursor_mz"
                )
            if self.precursor_inference_tolerance_da is not None:
                raise CidNlEvidenceContractError(
                    "direct evidence cannot declare precursor inference tolerance"
                )
        elif self.precursor_mz_basis == "scan_monoisotopic":
            if (
                self.precursor_metadata is None
                or self.precursor_metadata.monoisotopic_mz != self.precursor_mz
            ):
                raise CidNlEvidenceContractError(
                    "monoisotopic evidence requires its recorded precursor"
                )
            if self.precursor_inference_tolerance_da is not None:
                raise CidNlEvidenceContractError(
                    "direct evidence cannot declare precursor inference tolerance"
                )
        elif self.precursor_mz_basis == "scan_isotopologue":
            if not math.isclose(
                isolated_precursor_mz(self.precursor_mz_basis, self.precursor_mz),
                self.scan_precursor_mz,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise CidNlEvidenceContractError(
                    "isotopologue evidence must reference its recorded selection"
                )
            if self.precursor_inference_tolerance_da is not None:
                raise CidNlEvidenceContractError(
                    "direct evidence cannot declare precursor inference tolerance"
                )
        elif self.precursor_mz_basis == "product_plus_neutral_loss":
            tolerance = self.precursor_inference_tolerance_da
            if tolerance is None:
                raise CidNlEvidenceContractError(
                    "inferred evidence requires precursor_inference_tolerance_da"
                )
            tolerance = _finite_float(
                tolerance, field="precursor_inference_tolerance_da"
            )
            if tolerance <= 0.0:
                raise CidNlEvidenceContractError(
                    "precursor_inference_tolerance_da must be positive"
                )
            object.__setattr__(self, "precursor_inference_tolerance_da", tolerance)
            if not math.isclose(
                self.precursor_mz,
                self.product_mz + self.configured_neutral_loss_da,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise CidNlEvidenceContractError(
                    "inferred precursor_mz must equal product_mz + configured loss"
                )
            lower, upper = precursor_inference_bounds(
                self.scan_precursor_mz, self.precursor_metadata, tolerance
            )
            if not lower <= self.precursor_mz <= upper:
                raise CidNlEvidenceContractError(
                    "scan_precursor_mz exceeds precursor inference tolerance"
                )
        else:
            raise CidNlEvidenceContractError("precursor_mz_basis is not supported")
        parent = self.ms1_precursor_observation
        if parent is not None and (
            not isinstance(parent, Ms1PrecursorObservation)
            or self.precursor_mz_basis != "product_plus_neutral_loss"
            or self.precursor_metadata is None
            or parent.scan_number != self.precursor_metadata.master_scan_number
            or parent.scan_number >= self.scan_number
            or parent.rt_min > self.scan_rt_min
        ):
            raise CidNlEvidenceContractError(
                "MS1 observation does not match the recorded parent"
            )
        if self.configured_neutral_loss_da <= 0.0:
            raise CidNlEvidenceContractError(
                "configured_neutral_loss_da must be positive"
            )
        if self.observed_neutral_loss_da <= 0.0:
            raise CidNlEvidenceContractError(
                "observed_neutral_loss_da must be positive"
            )
        if self.precursor_mz_basis == "product_plus_neutral_loss":
            if self.observed_loss_error_ppm is not None:
                raise CidNlEvidenceContractError(
                    "inferred precursor has no independent observed_loss_error_ppm"
                )
        else:
            if self.observed_loss_error_ppm is None:
                raise CidNlEvidenceContractError(
                    "recorded precursor requires observed_loss_error_ppm"
                )
            object.__setattr__(
                self,
                "observed_loss_error_ppm",
                _finite_float(
                    self.observed_loss_error_ppm, field="observed_loss_error_ppm"
                ),
            )
        if (
            self.observed_loss_error_ppm is not None
            and self.observed_loss_error_ppm < 0.0
        ):
            raise CidNlEvidenceContractError(
                "observed_loss_error_ppm must be non-negative"
            )
        if self.scan_rt_min < 0.0:
            raise CidNlEvidenceContractError("scan_rt_min must be non-negative")
        if self.product_intensity <= 0.0:
            raise CidNlEvidenceContractError("product_intensity must be positive")

        # The loss is measured from the ion actually isolated for fragmentation.
        precursor_product_loss = (
            isolated_precursor_mz(self.precursor_mz_basis, self.precursor_mz)
            - self.product_mz
        )
        if not math.isclose(
            self.observed_neutral_loss_da,
            precursor_product_loss,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise CidNlEvidenceContractError(
                "observed_neutral_loss_da must equal isolated precursor - product_mz"
            )
        expected_error_ppm = (
            abs(precursor_product_loss - self.configured_neutral_loss_da)
            / self.configured_neutral_loss_da
            * 1_000_000.0
        )
        if self.observed_loss_error_ppm is not None and not math.isclose(
            self.observed_loss_error_ppm,
            expected_error_ppm,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise CidNlEvidenceContractError(
                "observed_loss_error_ppm mismatches precursor-product neutral loss"
            )


@dataclass(frozen=True, slots=True)
class CidNlEvidenceRef:
    """Owner-minted observation plus its immutable attempt artifact."""

    observation: CidNlEvidenceObservation
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        if not isinstance(self.observation, CidNlEvidenceObservation):
            raise CidNlEvidenceContractError(
                "observation must be owner-normalized CID-NL evidence"
            )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise CidNlEvidenceContractError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise CidNlEvidenceContractError(
                "artifact semantic contract is not owned by canonical CID-NL evidence"
            )
        if self.artifact.upstream_artifact_ids:
            raise CidNlEvidenceContractError(
                "canonical CID-NL observation must not invent artifact dependencies"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_cid_nl_evidence_bytes(self.observation),
            )
        except ValueError as exc:
            raise CidNlEvidenceContractError(
                "canonical CID-NL observation mismatches its immutable artifact"
            ) from exc


def mint_cid_nl_evidence_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    scan_number: int,
    precursor_mz: float,
    product_mz: float,
    configured_neutral_loss_da: float,
    observed_neutral_loss_da: float,
    observed_loss_error_ppm: float | None,
    scan_rt_min: float,
    product_intensity: float,
    precursor_mz_basis: PrecursorMzBasis = "scan_precursor",
    scan_precursor_mz: float | None = None,
    precursor_inference_tolerance_da: float | None = None,
    precursor_metadata: Ms2PrecursorMetadata | None = None,
    ms1_precursor_observation: Ms1PrecursorObservation | None = None,
) -> CidNlEvidenceRef:
    """Mint source-bound evidence without reading an acquisition source.

    Direct callers may omit the scan precursor; inferred callers must supply
    its actual measured value and the bounded precursor inference tolerance.
    """

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise CidNlEvidenceContractError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CidNlEvidenceContractError(
            "sample must be an owner-resolved canonical sample"
        )
    if sample not in processing_owners.sample_resolutions:
        raise CidNlEvidenceContractError(
            "sample references a different sample outside the processing owner closure"
        )
    acquisition_source = _request_acquisition_source(processing_owners, sample)
    if scan_precursor_mz is None:
        if precursor_mz_basis != "scan_precursor":
            raise CidNlEvidenceContractError(
                "inferred evidence requires explicit scan_precursor_mz"
            )
        scan_precursor_mz = precursor_mz
    observation = CidNlEvidenceObservation(
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=acquisition_source.acquisition_source_id,
        acquisition_mode=ACQUISITION_MODE,
        scan_number=scan_number,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        configured_neutral_loss_da=configured_neutral_loss_da,
        observed_neutral_loss_da=observed_neutral_loss_da,
        observed_loss_error_ppm=observed_loss_error_ppm,
        scan_rt_min=scan_rt_min,
        product_intensity=product_intensity,
        precursor_mz_basis=precursor_mz_basis,
        scan_precursor_mz=scan_precursor_mz,
        precursor_inference_tolerance_da=precursor_inference_tolerance_da,
        precursor_metadata=precursor_metadata,
        ms1_precursor_observation=ms1_precursor_observation,
    )
    artifact = mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=canonical_cid_nl_evidence_bytes(observation),
    )
    return CidNlEvidenceRef(observation=observation, artifact=artifact)


def canonical_cid_nl_evidence_bytes(
    observation: CidNlEvidenceObservation,
) -> bytes:
    """Return the canonical exact bytes owned by the shared evidence module."""

    if not isinstance(observation, CidNlEvidenceObservation):
        raise CidNlEvidenceContractError(
            "canonical serialization requires CidNlEvidenceObservation"
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "canonical_sample_identity_id": observation.canonical_sample_identity_id,
        "acquisition_source_id": observation.acquisition_source_id,
        "acquisition_mode": observation.acquisition_mode,
        "scan_number": observation.scan_number,
        "precursor_mz": observation.precursor_mz,
        "precursor_mz_basis": observation.precursor_mz_basis,
        "precursor_isolation_membership": precursor_isolation_membership(
            isolated_precursor_mz(
                observation.precursor_mz_basis, observation.precursor_mz
            ),
            observation.scan_precursor_mz,
            observation.precursor_metadata,
        ),
        "supports_direct_nl": observation.supports_direct_nl,
        "ms1_precursor_observation": asdict(observation.ms1_precursor_observation)
        if observation.ms1_precursor_observation is not None
        else None,
        "scan_precursor_mz": observation.scan_precursor_mz,
        "precursor_metadata": asdict(observation.precursor_metadata)
        if observation.precursor_metadata is not None
        else None,
        "precursor_inference_tolerance_da": (
            observation.precursor_inference_tolerance_da
        ),
        "product_mz": observation.product_mz,
        "configured_neutral_loss_da": observation.configured_neutral_loss_da,
        "observed_neutral_loss_da": observation.observed_neutral_loss_da,
        "observed_loss_error_ppm": observation.observed_loss_error_ppm,
        "scan_rt_min": observation.scan_rt_min,
        "product_intensity": observation.product_intensity,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def validate_cid_nl_evidence_binding(
    reference: CidNlEvidenceRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> None:
    """Reject sample, request, attempt, or semantic-contract substitution."""

    if not isinstance(reference, CidNlEvidenceRef):
        raise CidNlEvidenceContractError(
            "reference must be an owner-minted CidNlEvidenceRef"
        )
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise CidNlEvidenceContractError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CidNlEvidenceContractError(
            "sample must be an owner-resolved canonical sample"
        )
    acquisition_source = _request_acquisition_source(processing_owners, sample)
    artifact = reference.artifact
    if (
        artifact.producer_processing_request_id
        != processing_owners.request.processing_request_id
    ):
        raise CidNlEvidenceContractError(
            "canonical CID-NL evidence references a different processing request"
        )
    if (
        artifact.producer_processing_attempt_id
        != processing_owners.attempt.processing_attempt_id
    ):
        raise CidNlEvidenceContractError(
            "canonical CID-NL evidence references a different processing attempt"
        )
    if (
        reference.observation.canonical_sample_identity_id
        != sample.identity.sample_identity_id
    ):
        raise CidNlEvidenceContractError(
            "canonical CID-NL evidence references a different sample"
        )
    if (
        reference.observation.acquisition_source_id
        != acquisition_source.acquisition_source_id
    ):
        raise CidNlEvidenceContractError(
            "canonical CID-NL evidence references a different acquisition source"
        )


def _request_acquisition_source(
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> AcquisitionSourceIdentity:
    try:
        return resolve_request_acquisition_source(
            processing_owners=processing_owners,
            sample=sample,
        )
    except IdentityContractError as exc:
        raise CidNlEvidenceContractError(str(exc)) from exc


def _finite_float(value: float, *, field: str) -> float:
    if isinstance(value, bool):
        raise CidNlEvidenceContractError(f"{field} must be a finite number")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise CidNlEvidenceContractError(f"{field} must be a finite number") from exc
    if not math.isfinite(normalized):
        raise CidNlEvidenceContractError(f"{field} must be a finite number")
    return 0.0 if normalized == 0.0 else normalized


def _validate_typed_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise CidNlEvidenceContractError(f"{field} must use the {prefix!r} prefix")
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise CidNlEvidenceContractError(f"{field} must contain a SHA-256 digest")
    return value


__all__ = [
    "ACQUISITION_MODE",
    "ARTIFACT_SEMANTIC_ROLE",
    "CidNlEvidenceContractError",
    "CidNlEvidenceObservation",
    "CidNlEvidenceRef",
    "SCHEMA_VERSION",
    "canonical_cid_nl_evidence_bytes",
    "mint_cid_nl_evidence_ref",
    "validate_cid_nl_evidence_binding",
]
