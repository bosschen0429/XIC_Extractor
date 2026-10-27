"""Untargeted diagnostic projection over canonical CID-NL evidence."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from xic_extractor.discovery.models import DiscoverySeed
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    mint_artifact_semantic_contract_ref,
    mint_evidence_artifact_ref,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceContractError,
    CidNlEvidenceRef,
    validate_cid_nl_evidence_binding,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadataError,
    resolve_canonical_sample_identity,
)

SCHEMA_VERSION = "untargeted_cid_nl_evidence_projection"
ARTIFACT_SEMANTIC_ROLE = "untargeted_cid_nl_evidence_projection"
VALIDATION_STATUS = "diagnostic_only"
_ARTIFACT_SEMANTIC_CONTRACT = mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.discovery.cid_nl_evidence_projection",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf",
)


class UntargetedCidNlProjectionError(ValueError):
    """Raised when a Discovery seed does not match canonical CID-NL evidence."""


@dataclass(frozen=True, slots=True)
class UntargetedCidNlProjectionRef:
    """No-write Untargeted context bound to one canonical evidence artifact."""

    sample_key: str
    neutral_loss_tag: str
    canonical_evidence_artifact_id: str
    artifact: EvidenceArtifactRef


def mint_untargeted_cid_nl_projection_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    canonical_evidence: CidNlEvidenceRef,
    discovery_seed: DiscoverySeed,
) -> UntargetedCidNlProjectionRef:
    """Bind one exact measured or inferred Discovery seed without Matrix authority."""

    if not isinstance(discovery_seed, DiscoverySeed):
        raise UntargetedCidNlProjectionError(
            "discovery_seed must be an owner-typed DiscoverySeed"
        )
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise UntargetedCidNlProjectionError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    try:
        validate_cid_nl_evidence_binding(
            canonical_evidence,
            processing_owners=processing_owners,
            sample=sample,
        )
    except CidNlEvidenceContractError as exc:
        raise UntargetedCidNlProjectionError(str(exc)) from exc
    if (
        discovery_seed.precursor_mz_basis
        != canonical_evidence.observation.precursor_mz_basis
    ):
        raise UntargetedCidNlProjectionError(
            "Discovery precursor_mz_basis mismatches canonical evidence: "
            f"{canonical_evidence.observation.precursor_mz_basis}"
        )
    scan_precursor_mz = discovery_seed.scan_precursor_mz
    if (
        isinstance(scan_precursor_mz, bool)
        or not isinstance(scan_precursor_mz, (int, float))
        or not math.isfinite(float(scan_precursor_mz))
    ):
        raise UntargetedCidNlProjectionError(
            "Discovery scan_precursor_mz must be a finite measured value"
        )
    try:
        resolved_seed_sample = resolve_canonical_sample_identity(
            sample.snapshot,
            discovery_seed.sample_stem,
            expected_identity=sample.identity,
        )
    except SampleMetadataError as exc:
        raise UntargetedCidNlProjectionError(
            "Discovery seed references a different canonical sample"
        ) from exc
    if resolved_seed_sample.identity != sample.identity:
        raise UntargetedCidNlProjectionError(
            "Discovery seed references a different canonical sample"
        )
    if discovery_seed.raw_file.stem != discovery_seed.sample_stem:
        raise UntargetedCidNlProjectionError(
            "Discovery raw_file mismatches its canonical sample binding"
        )

    observation = canonical_evidence.observation
    if discovery_seed.precursor_metadata != observation.precursor_metadata:
        raise UntargetedCidNlProjectionError(
            "Discovery precursor metadata mismatches canonical evidence"
        )
    if (
        discovery_seed.ms1_precursor_observation
        != observation.ms1_precursor_observation
    ):
        raise UntargetedCidNlProjectionError(
            "Discovery parent MS1 observation mismatches canonical evidence"
        )
    if discovery_seed.acquisition_source_id != observation.acquisition_source_id:
        raise UntargetedCidNlProjectionError(
            "Discovery acquisition source mismatches canonical CID-NL evidence"
        )
    if discovery_seed.scan_number != observation.scan_number:
        raise UntargetedCidNlProjectionError(
            "Discovery scan_number mismatches canonical CID-NL evidence"
        )
    _require_close(
        float(scan_precursor_mz),
        observation.scan_precursor_mz,
        field="scan_precursor_mz",
    )
    _require_close(
        discovery_seed.precursor_mz,
        observation.precursor_mz,
        field="precursor_mz",
    )
    _require_close(
        discovery_seed.product_mz,
        observation.product_mz,
        field="product_mz",
    )
    _require_close(
        discovery_seed.product_intensity,
        observation.product_intensity,
        field="product_intensity",
    )
    _require_close(
        discovery_seed.rt,
        observation.scan_rt_min,
        field="scan_rt",
    )
    _require_close(
        discovery_seed.configured_neutral_loss_da,
        observation.configured_neutral_loss_da,
        field="configured_neutral_loss_da",
    )
    _require_close(
        discovery_seed.observed_neutral_loss_da,
        observation.observed_neutral_loss_da,
        field="observed_neutral_loss_da",
    )
    if (
        discovery_seed.observed_loss_error_ppm is None
        or observation.observed_loss_error_ppm is None
    ):
        if (
            discovery_seed.observed_loss_error_ppm
            != observation.observed_loss_error_ppm
        ):
            raise UntargetedCidNlProjectionError("observed_loss_error_ppm mismatches")
    else:
        _require_close(
            discovery_seed.observed_loss_error_ppm,
            observation.observed_loss_error_ppm,
            field="observed_loss_error_ppm",
            abs_tol=1e-9,
        )

    projection = UntargetedCidNlProjectionRef(
        sample_key=sample.identity.sample_key,
        neutral_loss_tag=discovery_seed.neutral_loss_tag,
        canonical_evidence_artifact_id=(
            canonical_evidence.artifact.evidence_artifact_id
        ),
        artifact=mint_evidence_artifact_ref(
            producer_processing_request_id=(
                processing_owners.request.processing_request_id
            ),
            producer_processing_attempt_id=(
                processing_owners.attempt.processing_attempt_id
            ),
            semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
            payload_bytes=_untargeted_projection_bytes(
                sample_key=sample.identity.sample_key,
                neutral_loss_tag=discovery_seed.neutral_loss_tag,
                canonical_evidence_artifact_id=(
                    canonical_evidence.artifact.evidence_artifact_id
                ),
            ),
            upstream_artifacts=(canonical_evidence.artifact,),
        ),
    )
    return projection


def canonical_untargeted_cid_nl_projection_bytes(
    reference: UntargetedCidNlProjectionRef,
) -> bytes:
    """Return exact Untargeted projection bytes for verification only."""

    if not isinstance(reference, UntargetedCidNlProjectionRef):
        raise UntargetedCidNlProjectionError(
            "canonical serialization requires UntargetedCidNlProjectionRef"
        )
    return _untargeted_projection_bytes(
        sample_key=reference.sample_key,
        neutral_loss_tag=reference.neutral_loss_tag,
        canonical_evidence_artifact_id=reference.canonical_evidence_artifact_id,
    )


def _untargeted_projection_bytes(
    *,
    sample_key: str,
    neutral_loss_tag: str,
    canonical_evidence_artifact_id: str,
) -> bytes:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "projection_policy": "untargeted_observation_reference_only",
        "sample_key": sample_key,
        "neutral_loss_tag": neutral_loss_tag,
        "canonical_evidence_artifact_id": canonical_evidence_artifact_id,
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


def _require_close(
    observed: float,
    expected: float,
    *,
    field: str,
    abs_tol: float = 1e-12,
) -> None:
    if not math.isclose(
        float(observed),
        float(expected),
        rel_tol=1e-12,
        abs_tol=abs_tol,
    ):
        raise UntargetedCidNlProjectionError(
            f"Discovery {field} mismatches canonical CID-NL evidence"
        )


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "SCHEMA_VERSION",
    "UntargetedCidNlProjectionError",
    "UntargetedCidNlProjectionRef",
    "canonical_untargeted_cid_nl_projection_bytes",
    "mint_untargeted_cid_nl_projection_ref",
]
