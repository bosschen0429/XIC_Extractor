from __future__ import annotations

from tests.processing_identity_no_raw_fixture import (
    NEUTRAL_LOSS_DA,
    PRECURSOR_MZ,
    PRODUCT_MZ,
    ProcessingIdentityNoRawFixture,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceRef,
    mint_cid_nl_evidence_ref,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
    DdaCidNlPeakSupportRef,
    resolve_dda_cid_nl_peak_support_ref,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    mint_peak_observation_ref,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import resolve_request_acquisition_source


def cid_nl_peak(
    fixture: ProcessingIdentityNoRawFixture,
    sample_stem: str,
    *,
    mz: float = PRECURSOR_MZ,
    apex_rt: float = 8.0,
    rt_left_min: float = 7.9,
    rt_right_min: float = 8.1,
    height: float = 500.0,
) -> PeakObservationRef:
    sample = fixture.sample(sample_stem)
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=sample,
    )
    trace = Trace.from_arrays(
        sample_name=sample_stem,
        mz=mz,
        rt=(
            rt_left_min - 0.1,
            rt_left_min,
            apex_rt,
            rt_right_min,
            rt_right_min + 0.1,
        ),
        intensity=(0.0, height / 10.0, height, height / 10.0, 0.0),
        rt_min=rt_left_min - 0.1,
        rt_max=rt_right_min + 0.1,
        ppm_tol=10.0,
        source="cid_nl_peak_support_no_raw_fixture",
        acquisition_source_id=source.acquisition_source_id,
    )
    integration = IntegrationResult(
        rt_left_min=rt_left_min,
        rt_apex_min=apex_rt,
        rt_right_min=rt_right_min,
        raw_apex_rt_min=apex_rt,
        rt_width_min=rt_right_min - rt_left_min,
        height_raw=height,
        height_smoothed=height,
        area_raw_counts_seconds=height * 6.6,
        integration_method="raw_trapezoid",
        boundary_sources=("candidate_interval", "gaussian15"),
        raw_scan_indices=(1, 2, 3),
    )
    return mint_peak_observation_ref(
        processing_owners=fixture.owners,
        sample=sample,
        trace=trace,
        integration=integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum", "gaussian15"),
    )


def cid_nl_evidence(
    fixture: ProcessingIdentityNoRawFixture,
    sample_stem: str,
    *,
    scan_number: int,
    scan_rt_min: float,
) -> CidNlEvidenceRef:
    return mint_cid_nl_evidence_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        scan_number=scan_number,
        precursor_mz=PRECURSOR_MZ,
        product_mz=PRODUCT_MZ,
        configured_neutral_loss_da=NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=NEUTRAL_LOSS_DA,
        observed_loss_error_ppm=0.0,
        scan_rt_min=scan_rt_min,
        product_intensity=400.0,
    )


def cid_nl_unique_support(
    fixture: ProcessingIdentityNoRawFixture,
    sample_stem: str,
    *,
    peak: PeakObservationRef,
    evidence: CidNlEvidenceRef,
    candidate_peaks: tuple[PeakObservationRef, ...] | None = None,
) -> DdaCidNlPeakSupportRef:
    return resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        canonical_evidence=evidence,
        candidate_peaks=candidate_peaks or (peak,),
        policy=cid_nl_support_policy(),
    )


def cid_nl_support_policy() -> DdaCidNlPeakSupportPolicy:
    return DdaCidNlPeakSupportPolicy(
        precursor_tolerance_da=0.01,
        neutral_loss_tolerance_ppm=20.0,
    )
