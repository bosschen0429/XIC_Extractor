"""Canonical no-write CID-NL evidence references for the bounded tracer.

The owner freezes only scientific fields shared by Targeted product evidence
and Untargeted Discovery seeds.  Workflow constraints, candidate identity,
selection, and delivery authority remain in their respective adapters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from xic_extractor.precursor_evidence import CidNlScan
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.sample_metadata import SampleMetadata

ACQUISITION_MODE = "dda_ddms2"


class CidNlEvidenceContractError(ValueError):
    """Raised when canonical CID-NL evidence is forged or rebound."""


@dataclass(frozen=True, slots=True)
class CidNlEvidenceObservation:
    """One scan bound to its sample, acquisition and method inference tolerance."""

    sample_key: str
    acquisition_source_id: str
    acquisition_mode: str
    scan: CidNlScan
    precursor_inference_tolerance_da: float | None


def cid_nl_evidence_observation(
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    scan: CidNlScan,
    precursor_inference_tolerance_da: float | None = None,
) -> CidNlEvidenceObservation:
    """Bind an acquired scan to a sample and the method's inference tolerance."""
    if scan.scan_precursor_mz is None:
        if scan.precursor_mz_basis != "scan_precursor":
            raise CidNlEvidenceContractError(
                "inferred evidence requires explicit scan_precursor_mz"
            )
        scan = replace(scan, scan_precursor_mz=scan.precursor_mz)
    if isinstance(scan.scan_number, bool) or scan.scan_number <= 0:
        raise CidNlEvidenceContractError("scan_number must be a positive integer")
    for name, value in (
        ("precursor_mz", scan.precursor_mz),
        ("product_mz", scan.product_mz),
        ("scan_precursor_mz", scan.scan_precursor_mz),
        ("configured_neutral_loss_da", scan.configured_neutral_loss_da),
        ("observed_neutral_loss_da", scan.observed_neutral_loss_da),
        ("product_intensity", scan.product_intensity),
    ):
        if value is None or _finite_float(value, field=name) <= 0:
            raise CidNlEvidenceContractError(f"{name} must be positive")
    if _finite_float(scan.rt_min, field="scan_rt_min") < 0:
        raise CidNlEvidenceContractError("scan_rt_min must be non-negative")
    if scan.precursor_metadata is not None and scan.precursor_metadata.charge not in (
        None,
        1,
    ):
        raise CidNlEvidenceContractError(
            "CID-NL evidence requires singly charged or unknown charge"
        )
    if scan.precursor_mz_basis == "product_plus_neutral_loss":
        tolerance = precursor_inference_tolerance_da
        if tolerance is None:
            raise CidNlEvidenceContractError(
                "inferred evidence requires precursor_inference_tolerance_da"
            )
        tolerance = _finite_float(tolerance, field="precursor_inference_tolerance_da")
        if tolerance <= 0.0:
            raise CidNlEvidenceContractError(
                "precursor_inference_tolerance_da must be positive"
            )
        precursor_inference_tolerance_da = tolerance
        if scan.observed_loss_error_ppm is not None:
            raise CidNlEvidenceContractError(
                "inferred precursor has no independent observed_loss_error_ppm"
            )
    else:
        if precursor_inference_tolerance_da is not None:
            raise CidNlEvidenceContractError(
                "direct evidence cannot declare precursor inference tolerance"
            )
        if scan.observed_loss_error_ppm is None:
            raise CidNlEvidenceContractError(
                "recorded precursor requires observed_loss_error_ppm"
            )
        error_ppm = _finite_float(
            scan.observed_loss_error_ppm, field="observed_loss_error_ppm"
        )
        if error_ppm < 0.0:
            raise CidNlEvidenceContractError(
                "observed_loss_error_ppm must be non-negative"
            )
    return CidNlEvidenceObservation(
        sample_key=sample.sample_name,
        acquisition_source_id=acquisition_source.acquisition_source_id,
        acquisition_mode=ACQUISITION_MODE,
        scan=scan,
        precursor_inference_tolerance_da=precursor_inference_tolerance_da,
    )


def _finite_float(value: float, *, field: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value):
        raise CidNlEvidenceContractError(f"{field} must be a finite number")
    return value


__all__ = [
    "ACQUISITION_MODE",
    "CidNlEvidenceContractError",
    "CidNlEvidenceObservation",
    "cid_nl_evidence_observation",
]
