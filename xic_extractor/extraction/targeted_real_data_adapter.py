"""Fail-closed adapter from current Discovery candidates to Targeted lifecycle.

The adapter reacquires exact physical observations from one already-open RAW
handle.  Discovery CSV values are validation receipts, never evidence payloads.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from xic_extractor.acquisition_contract import (
    BatchXICAdapter,
    TraceAcquisitionBatch,
    TraceCoordinateKind,
    acquire_trace_batch,
    mint_trace_acquisition_capability,
)
from xic_extractor.alignment.targeted_hypothesis_alignment import (
    TargetedHypothesisAlignmentInput,
)
from xic_extractor.artifact_lineage import canonical_evidence_artifact_closure
from xic_extractor.config import ExtractionConfig
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.discovery.models import (
    MS1_FEATURE_BACKED_STATES,
    DiscoveryCandidate,
    DiscoverySeed,
    DiscoverySettings,
)
from xic_extractor.discovery.ms2_seeds import discovery_seeds_from_scan
from xic_extractor.discovery.targeted_candidate_set import (
    complete_targeted_candidate_discovery,
    mint_targeted_candidate_set_ref,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.extraction.cid_nl_evidence_projection import (
    mint_targeted_cid_nl_projection_ref,
)
from xic_extractor.neutral_loss import MS2ProductEvidence
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    compose_cid_nl_chromatographic_support_ref,
)
from xic_extractor.peak_detection.cid_nl_evidence import mint_cid_nl_evidence_ref
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
    resolve_dda_cid_nl_peak_support_ref,
)
from xic_extractor.peak_detection.hypotheses import build_peak_hypotheses
from xic_extractor.peak_detection.ms1_trace_detection import detect_ms1_trace_peak
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    mint_peak_observation_ref,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    compose_cid_nl_peak_hypothesis,
)
from xic_extractor.peak_detection.selection_decision import (
    mint_diagnostic_peak_hypothesis_selection_ref,
)
from xic_extractor.peak_detection.traces import trace_from_xic_request
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    resolve_request_acquisition_source,
)
from xic_extractor.raw_reader import Ms2Scan, Ms2ScanEvent
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.signal_processing import find_peak_and_area
from xic_extractor.xic_models import XICRequest

VALIDATION_STATUS = "diagnostic_only"
_ADAPTER_NAMESPACE = "xic_extractor.targeted_real_data_adapter"
_WRITER_ROUNDING_REL_TOL = 5e-6
_WRITER_ROUNDING_ABS_TOL = 5e-6


class TargetedRealDataAdapterError(ValueError):
    """Raised when current candidate receipts cannot be reacquired exactly."""


class TargetedRealDataRaw(BatchXICAdapter, Protocol):
    def iter_ms2_scans(
        self,
        rt_min: float,
        rt_max: float,
    ) -> Iterator[Ms2ScanEvent]: ...


@dataclass(frozen=True, slots=True)
class TargetedRealDataAdapterResult:
    alignment_input: TargetedHypothesisAlignmentInput
    acquisition: TraceAcquisitionBatch | None
    source_candidate_ids: tuple[str, ...]
    validation_status: str = VALIDATION_STATUS


def adapt_current_discovery_sample_target(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
    candidates: tuple[DiscoveryCandidate, ...],
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw: TargetedRealDataRaw,
) -> TargetedRealDataAdapterResult:
    """Reacquire one complete sample-target candidate universe from one RAW."""

    _validate_inputs(
        processing_owners=processing_owners,
        sample=sample,
        target=target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )
    bounded = tuple(
        sorted(
            (item for item in candidates if _matches_target(item, target=target)),
            key=lambda item: item.candidate_id,
        )
    )
    _validate_bounded_candidates(bounded, sample=sample, target=target)
    if not bounded:
        return _empty_result(
            processing_owners=processing_owners,
            sample=sample,
            target=target,
        )

    source = resolve_request_acquisition_source(
        processing_owners=processing_owners,
        sample=sample,
    )
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace=_ADAPTER_NAMESPACE,
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    scans_and_seeds = tuple(
        (
            scan := _read_exact_scan(raw, candidate),
            _read_exact_seed(
                scan,
                raw_file=candidate.raw_file,
                candidate=candidate,
                settings=settings,
            ),
        )
        for candidate in bounded
    )
    requests = tuple(
        XICRequest(
            mz=seed.precursor_mz,
            rt_min=candidate.ms1_search_rt_min,
            rt_max=candidate.ms1_search_rt_max,
            ppm_tol=settings.precursor_mz_tolerance_ppm,
        )
        for candidate, (_scan, seed) in zip(
            bounded,
            scans_and_seeds,
            strict=True,
        )
    )
    acquisition = acquire_trace_batch(capability, requests, raw)

    observations: list[PeakObservationRef] = []
    for candidate, acquired in zip(bounded, acquisition.traces, strict=True):
        detection = detect_ms1_trace_peak(
            acquired.trace.rt,
            acquired.trace.intensity,
            peak_config=peak_config,
            preferred_rt=candidate.best_seed_rt,
            peak_finder=find_peak_and_area,
        )
        if detection.status != "detected" or detection.result is None:
            raise TargetedRealDataAdapterError(
                f"{candidate.candidate_id}: exact MS1 peak was not reacquired"
            )
        audit_hypotheses = build_peak_hypotheses(
            sample_name=sample.metadata.sample_name,
            target_label=target.definition.target.label,
            role="ISTD" if target.definition.target.is_istd else "Analyte",
            istd_pair=target.definition.target.istd_pair,
            resolver_mode=settings.resolver_mode,
            peak_result=detection.result,
            rt=detection.rt,
            intensity=detection.intensity,
            baseline_integration_method=peak_config.baseline_integration_method,
            ms1_morphology_smoothing_window_points=(
                peak_config.ms1_morphology_smoothing_window_points
            ),
        )
        selected = tuple(item for item in audit_hypotheses if item.audit.selected)
        if len(selected) != 1:
            raise TargetedRealDataAdapterError(
                f"{candidate.candidate_id}: integration owner did not select "
                "exactly one peak"
            )
        integration = selected[0].integration
        _validate_candidate_integration(candidate, integration)
        trace = trace_from_xic_request(
            sample_name=sample.metadata.sample_name,
            request=acquired.coordinate.request,
            xic_trace=acquired.trace,
            source=f"{_ADAPTER_NAMESPACE}:{TraceCoordinateKind.NATIVE_SCAN.value}",
            acquisition_source_id=source.acquisition_source_id,
        )
        observation = mint_peak_observation_ref(
            processing_owners=processing_owners,
            sample=sample,
            trace=trace,
            integration=integration,
            resolver_mode=settings.resolver_mode,
            proposal_sources=selected[0].audit.proposal_sources,
        )
        observations.append(observation)

    hypotheses = []
    projections = []
    artifacts: list[EvidenceArtifactRef] = [item.artifact for item in observations]
    for candidate, observation, (scan, seed) in zip(
        bounded,
        observations,
        scans_and_seeds,
        strict=True,
    ):
        seed = replace(seed, acquisition_source_id=source.acquisition_source_id)
        evidence = mint_cid_nl_evidence_ref(
            processing_owners=processing_owners,
            sample=sample,
            scan_number=seed.scan_number,
            precursor_mz=seed.precursor_mz,
            product_mz=seed.product_mz,
            configured_neutral_loss_da=seed.configured_neutral_loss_da,
            observed_neutral_loss_da=seed.observed_neutral_loss_da,
            observed_loss_error_ppm=seed.observed_loss_error_ppm,
            scan_rt_min=seed.rt,
            product_intensity=seed.product_intensity,
        )
        support = resolve_dda_cid_nl_peak_support_ref(
            processing_owners=processing_owners,
            sample=sample,
            canonical_evidence=evidence,
            candidate_peaks=tuple(observations),
            policy=DdaCidNlPeakSupportPolicy(
                precursor_tolerance_da=max(
                    target.definition.target.mz
                    * target.definition.target.ppm_tol
                    / 1_000_000.0,
                    _WRITER_ROUNDING_ABS_TOL,
                ),
                neutral_loss_tolerance_ppm=settings.nl_tolerance_ppm,
            ),
        )
        event = compose_cid_nl_chromatographic_support_ref(
            processing_owners=processing_owners,
            sample=sample,
            scan_support_refs=(support,),
        )
        hypothesis = compose_cid_nl_peak_hypothesis(
            processing_owners=processing_owners,
            sample=sample,
            peak_observation=observation,
            chromatographic_support=event,
        )
        expected_product = (
            target.definition.target.mz - target.definition.target.neutral_loss_da
        )
        projection = mint_targeted_cid_nl_projection_ref(
            processing_owners=processing_owners,
            sample=sample,
            target=target,
            canonical_evidence=evidence,
            targeted_evidence=MS2ProductEvidence(
                scan_rt=seed.rt,
                precursor_mz=seed.precursor_mz,
                product_mz=seed.product_mz,
                product_intensity=seed.product_intensity,
                product_base_ratio=seed.product_intensity / scan.base_peak,
                target_product_ppm=(
                    abs(seed.product_mz - expected_product)
                    / expected_product
                    * 1_000_000.0
                ),
                observed_loss_da=seed.observed_neutral_loss_da,
                observed_loss_error_ppm=seed.observed_loss_error_ppm,
                scan_number=seed.scan_number,
                acquisition_source_id=source.acquisition_source_id,
            ),
        )
        hypotheses.append(hypothesis)
        projections.append(projection)
        artifacts.extend(
            (
                evidence.artifact,
                support.artifact,
                event.artifact,
                hypothesis.artifact,
                projection.artifact,
            )
        )

    lineage = canonical_evidence_artifact_closure(
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        artifacts=tuple(artifacts),
    )
    completion = complete_targeted_candidate_discovery(
        processing_owners=processing_owners,
        sample=sample,
        target=target,
        hypotheses=tuple(hypotheses),
        targeted_cid_nl_projections=tuple(projections),
        lineage_artifacts=lineage,
    )
    candidate_set = mint_targeted_candidate_set_ref(
        processing_owners=processing_owners,
        sample=sample,
        target=target,
        completion=completion,
    )
    selection = mint_diagnostic_peak_hypothesis_selection_ref(
        candidate_set=candidate_set
    )
    return TargetedRealDataAdapterResult(
        alignment_input=TargetedHypothesisAlignmentInput(
            sample=sample,
            target=target,
            candidate_set=candidate_set,
            selection=selection,
        ),
        acquisition=acquisition,
        source_candidate_ids=tuple(item.candidate_id for item in bounded),
    )


def _empty_result(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
) -> TargetedRealDataAdapterResult:
    completion = complete_targeted_candidate_discovery(
        processing_owners=processing_owners,
        sample=sample,
        target=target,
        hypotheses=(),
        targeted_cid_nl_projections=(),
        lineage_artifacts=(),
    )
    candidate_set = mint_targeted_candidate_set_ref(
        processing_owners=processing_owners,
        sample=sample,
        target=target,
        completion=completion,
    )
    selection = mint_diagnostic_peak_hypothesis_selection_ref(
        candidate_set=candidate_set
    )
    return TargetedRealDataAdapterResult(
        alignment_input=TargetedHypothesisAlignmentInput(
            sample=sample,
            target=target,
            candidate_set=candidate_set,
            selection=selection,
        ),
        acquisition=None,
        source_candidate_ids=(),
    )


def _matches_target(
    candidate: DiscoveryCandidate,
    *,
    target: ResolvedTargetDefinition,
) -> bool:
    value = target.definition.target
    if value.neutral_loss_da is None:
        return False
    return (
        _within_ppm(candidate.precursor_mz, value.mz, value.ppm_tol)
        and value.rt_min <= candidate.best_seed_rt <= value.rt_max
        and math.isclose(
            candidate.configured_neutral_loss_da,
            value.neutral_loss_da,
            rel_tol=0.0,
            abs_tol=0.001,
        )
        and candidate.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
    )


def _validate_bounded_candidates(
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
) -> None:
    ids = tuple(item.candidate_id for item in candidates)
    if len(ids) != len(set(ids)):
        raise TargetedRealDataAdapterError("bounded candidate IDs must be unique")
    for item in candidates:
        if item.sample_stem != sample.metadata.raw_stem:
            raise TargetedRealDataAdapterError(
                f"{item.candidate_id}: candidate references a different sample"
            )
        if not item.ms1_peak_found:
            raise TargetedRealDataAdapterError(
                f"{item.candidate_id}: candidate lacks an owner-backed MS1 peak"
            )
        if target.definition.target.neutral_loss_da is None:
            raise TargetedRealDataAdapterError("target lacks a CID-NL definition")


def _validate_candidate_integration(
    candidate: DiscoveryCandidate,
    integration: object,
) -> None:
    for field, expected in (
        ("rt_apex_min", candidate.ms1_apex_rt),
        ("rt_left_min", candidate.ms1_peak_rt_start),
        ("rt_right_min", candidate.ms1_peak_rt_end),
        ("area_raw_counts_seconds", candidate.ms1_area),
        ("height_raw", candidate.ms1_height),
    ):
        actual = getattr(integration, field)
        if expected is None or not math.isclose(
            actual,
            expected,
            rel_tol=_WRITER_ROUNDING_REL_TOL,
            abs_tol=_WRITER_ROUNDING_ABS_TOL,
        ):
            raise TargetedRealDataAdapterError(
                f"{candidate.candidate_id}: reacquired {field} mismatches "
                "current receipt"
            )


def _read_exact_scan(
    raw: TargetedRealDataRaw,
    candidate: DiscoveryCandidate,
) -> Ms2Scan:
    matches = tuple(
        event.scan
        for event in raw.iter_ms2_scans(
            candidate.rt_seed_min - _WRITER_ROUNDING_ABS_TOL,
            candidate.rt_seed_max + _WRITER_ROUNDING_ABS_TOL,
        )
        if event.parse_error is None
        and event.scan is not None
        and event.scan.scan_number == candidate.best_ms2_scan_id
    )
    if len(matches) != 1:
        raise TargetedRealDataAdapterError(
            f"{candidate.candidate_id}: exact representative MS2 scan is not unique"
        )
    return matches[0]


def _read_exact_seed(
    scan: Ms2Scan,
    *,
    raw_file: Path,
    candidate: DiscoveryCandidate,
    settings: DiscoverySettings,
) -> DiscoverySeed:
    matches = tuple(
        seed
        for seed in discovery_seeds_from_scan(
            scan,
            raw_file=raw_file,
            settings=settings,
        )
        if seed.scan_number == candidate.best_ms2_scan_id
        and seed.neutral_loss_tag == candidate.neutral_loss_tag
        and seed.precursor_mz_basis == "scan_precursor"
        and _receipt_close(seed.precursor_mz, candidate.precursor_mz)
        and _receipt_close(seed.product_mz, candidate.product_mz)
    )
    if len(matches) != 1:
        raise TargetedRealDataAdapterError(
            f"{candidate.candidate_id}: exact canonical Discovery seed is not unique"
        )
    return matches[0]


def _validate_inputs(**values: object) -> None:
    expected = {
        "processing_owners": ProcessingOwnerReferences,
        "sample": ResolvedCanonicalSampleIdentity,
        "target": ResolvedTargetDefinition,
        "candidates": tuple,
        "settings": DiscoverySettings,
        "peak_config": ExtractionConfig,
    }
    for name, kind in expected.items():
        if not isinstance(values[name], kind):
            raise TargetedRealDataAdapterError(f"{name} has the wrong owner type")
    candidates = values["candidates"]
    if any(not isinstance(item, DiscoveryCandidate) for item in candidates):
        raise TargetedRealDataAdapterError(
            "candidates must contain current DiscoveryCandidate values"
        )
    raw = values["raw"]
    for method in ("extract_xic_many", "scan_window_for_request", "iter_ms2_scans"):
        if not callable(getattr(raw, method, None)):
            raise TargetedRealDataAdapterError(f"raw adapter lacks {method}")


def _within_ppm(observed: float, expected: float, tolerance_ppm: float) -> bool:
    return abs(observed - expected) / expected * 1_000_000.0 <= tolerance_ppm


def _receipt_close(observed: float, receipt: float) -> bool:
    return math.isclose(
        observed,
        receipt,
        rel_tol=_WRITER_ROUNDING_REL_TOL,
        abs_tol=_WRITER_ROUNDING_ABS_TOL,
    )


__all__ = [
    "TargetedRealDataAdapterError",
    "TargetedRealDataAdapterResult",
    "adapt_current_discovery_sample_target",
]
