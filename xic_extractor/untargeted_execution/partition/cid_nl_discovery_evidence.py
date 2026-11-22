"""Project retained Discovery scan facts to the CID-NL method owner."""

from xic_extractor.discovery.models import DiscoveryEvent
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceObservation,
    cid_nl_evidence_observation,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.sample_metadata import SampleMetadata


def discovery_scan_evidence(
    seed: DiscoveryEvent,
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    settings: DiscoverySettings,
) -> CidNlEvidenceObservation:
    """Carry the recorded scan and parent MS1, not an inferred peak identity."""
    return cid_nl_evidence_observation(
        sample=sample,
        acquisition_source=acquisition_source,
        scan_number=seed.scan_number,
        precursor_mz=seed.precursor_mz,
        product_mz=seed.product_mz,
        configured_neutral_loss_da=seed.configured_neutral_loss_da,
        observed_neutral_loss_da=seed.observed_neutral_loss_da,
        observed_loss_error_ppm=seed.observed_loss_error_ppm,
        scan_rt_min=seed.rt,
        product_intensity=seed.product_intensity,
        precursor_mz_basis=seed.precursor_mz_basis,
        scan_precursor_mz=seed.scan_precursor_mz,
        precursor_metadata=seed.precursor_metadata,
        ms1_precursor_observation=seed.ms1_precursor_observation,
        precursor_inference_tolerance_da=(
            settings.ms2_precursor_tol_da
            if seed.precursor_mz_basis == "product_plus_neutral_loss"
            else None
        ),
    )
