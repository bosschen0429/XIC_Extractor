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
        scan=seed.scan,
        sample=sample,
        acquisition_source=acquisition_source,
        precursor_inference_tolerance_da=settings.ms2_precursor_tol_da
        if seed.scan.precursor_mz_basis == "product_plus_neutral_loss"
        else None,
    )
