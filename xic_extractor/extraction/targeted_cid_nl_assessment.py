"""CID-NL method assessment of retained Targeted acquisition evidence.

Each sample's retained MS2 products become CID-NL evidence refs, which the
method owner resolves into support and set relations over the sample's peak
observation universe. This is the Targeted reading of ``ExtractionResult``;
Untargeted CID-NL re-acquires its evidence under its own policy.
"""

from __future__ import annotations

from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.extraction.result_models import ExtractionResult
from xic_extractor.extraction.targeted_capture_population import (
    TargetedCapturePopulation,
)
from xic_extractor.peak_detection.cid_nl_evidence import mint_cid_nl_evidence_ref
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    mint_cid_nl_method_assessment_ref,
    resolve_cid_nl_method_relations,
)
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


def assess_targeted_cid_nl_samples(
    population: TargetedCapturePopulation,
) -> tuple[tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...]:
    """Assess every sample of a CID-NL Target; an MS1-only Target has none."""

    if population.ms1_only:
        return ()
    return tuple(
        (
            entry.sample,
            _assess_sample(
                processing_owners=population.processing_owners,
                target=population.target,
                sample=entry.sample,
                result=entry.result,
                candidate_peaks=entry.observation_universe,
            ),
        )
        for entry in population.entries
    )


def _assess_sample(
    *,
    processing_owners: ProcessingOwnerReferences,
    target: ResolvedTargetDefinition,
    sample: ResolvedCanonicalSampleIdentity,
    result: ExtractionResult,
    candidate_peaks: tuple[PeakObservationRef, ...],
) -> CidNlMethodAssessmentRef:
    """Mint one sample's assessment after checking source and scan identity."""

    if result.cid_nl_evaluation_receipt is None or result.cid_nl_relations is None:
        raise RuntimeError("Targeted acquisition lost its complete CID-NL universe")
    acquisition_source = resolve_request_acquisition_source(
        processing_owners=processing_owners, sample=sample
    )
    evidence = []
    for product in result.cid_nl_products:
        if product.acquisition_source_id != acquisition_source.acquisition_source_id:
            raise RuntimeError(
                "Targeted MS2 evidence crossed the resolved acquisition source"
            )
        if product.scan_number is None:
            raise RuntimeError("Targeted MS2 evidence lost its acquired scan identity")
        evidence.append(
            mint_cid_nl_evidence_ref(
                processing_owners=processing_owners,
                sample=sample,
                scan_number=product.scan_number,
                precursor_mz=product.precursor_mz,
                product_mz=product.product_mz,
                configured_neutral_loss_da=_neutral_loss(target.definition.target),
                observed_neutral_loss_da=product.observed_loss_da,
                observed_loss_error_ppm=product.observed_loss_error_ppm,
                scan_rt_min=product.scan_rt,
                product_intensity=product.product_intensity,
                precursor_mz_basis=product.precursor_mz_basis,
                scan_precursor_mz=product.scan_precursor_mz,
                precursor_metadata=product.precursor_metadata,
            )
        )
    evidence_refs = tuple(evidence)
    support_refs, set_refs = resolve_cid_nl_method_relations(
        processing_owners=processing_owners,
        sample=sample,
        evidence_refs=evidence_refs,
        candidate_peaks=candidate_peaks,
        policy=result.cid_nl_evaluation_receipt.support_policy,
        relations=result.cid_nl_relations,
    )
    return mint_cid_nl_method_assessment_ref(
        processing_owners=processing_owners,
        sample=sample,
        target=target,
        detector_invocation_id="targeted-production-retained-trace",
        evaluation_receipt=result.cid_nl_evaluation_receipt,
        candidate_peaks=candidate_peaks,
        evidence_refs=evidence_refs,
        individual_support_refs=support_refs,
        set_relation_refs=set_refs,
    )


def _neutral_loss(target: Target) -> float:
    if target.neutral_loss_da is None:
        raise RuntimeError("Targeted production requires a neutral-loss definition")
    return target.neutral_loss_da


__all__ = ["assess_targeted_cid_nl_samples"]
