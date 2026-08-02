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

import numpy as np

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    BatchXICAdapter,
    TraceAcquisitionBatch,
    TraceCoordinateKind,
    acquire_trace_batch,
    mint_trace_acquisition_capability,
)
from xic_extractor.alignment.targeted_hypothesis_alignment import (
    TargetedHypothesisAlignmentInput,
)
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
from xic_extractor.extraction.handoff_spine_runtime import (
    promote_targeted_boundary_hypothesis,
)
from xic_extractor.extraction.rt_windows import get_rt_window
from xic_extractor.neutral_loss import MS2ProductEvidence
from xic_extractor.peak_detection.baseline import BaselineMethod
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    CidNlChromatographicSupportRef,
    compose_cid_nl_chromatographic_support_ref,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceRef,
    mint_cid_nl_evidence_ref,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportContractError,
    DdaCidNlPeakSupportPolicy,
    DdaCidNlPeakSupportRef,
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
    canonical_current_attempt_artifact_closure,
    canonical_evidence_artifact_closure,
    resolve_request_acquisition_source,
)
from xic_extractor.raw_reader import (
    Ms2Scan,
    Ms2ScanEvent,
    RawReaderError,
    require_owner_bound_raw_handle,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.signal_processing import find_peak_and_area
from xic_extractor.xic_models import XICRequest, crop_xic_trace_by_rt

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


class _TargetedRealDataWorkObserver:
    """Private worker facts retained even when scientific projection fails."""

    __slots__ = ("_artifacts", "_processing_owners", "acquisition")

    def __init__(self, processing_owners: ProcessingOwnerReferences) -> None:
        if not isinstance(processing_owners, ProcessingOwnerReferences):
            raise TargetedRealDataAdapterError(
                "observer requires validated processing owners"
            )
        self._processing_owners = processing_owners
        self.acquisition: TraceAcquisitionBatch | None = None
        self._artifacts: dict[str, EvidenceArtifactRef] = {}

    def record_acquisition(self, acquisition: TraceAcquisitionBatch) -> None:
        if not isinstance(acquisition, TraceAcquisitionBatch):
            raise TargetedRealDataAdapterError(
                "observer acquisition must be a TraceAcquisitionBatch"
            )
        if self.acquisition is not None and self.acquisition is not acquisition:
            raise TargetedRealDataAdapterError(
                "one bounded work unit cannot replace its observed acquisition"
            )
        self.acquisition = acquisition

    def record_artifact(self, artifact: EvidenceArtifactRef) -> None:
        if not isinstance(artifact, EvidenceArtifactRef):
            raise TargetedRealDataAdapterError(
                "observer artifact must be an EvidenceArtifactRef"
            )
        attempt = self._processing_owners.attempt
        if (
            artifact.producer_processing_request_id
            != attempt.processing_request_id
            or artifact.producer_processing_attempt_id
            != attempt.processing_attempt_id
        ):
            raise TargetedRealDataAdapterError(
                "observer artifact must bind the current processing attempt"
            )
        existing = self._artifacts.get(artifact.evidence_artifact_id)
        if existing is not None and existing != artifact:
            raise TargetedRealDataAdapterError(
                "observer artifact identity was rebound"
            )
        self._artifacts[artifact.evidence_artifact_id] = artifact

    def artifact_closure(self) -> tuple[EvidenceArtifactRef, ...]:
        return canonical_current_attempt_artifact_closure(
            producer_attempt=self._processing_owners.attempt,
            artifacts=tuple(self._artifacts.values()),
        )

    def observed_artifacts(self) -> tuple[EvidenceArtifactRef, ...]:
        """Return already-minted facts even when closure validation fails."""

        return tuple(
            sorted(
                self._artifacts.values(),
                key=lambda artifact: artifact.evidence_artifact_id,
            )
        )


def _adapt_current_discovery_sample_target(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
    candidates: tuple[DiscoveryCandidate, ...],
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw: TargetedRealDataRaw | None,
    _observer: _TargetedRealDataWorkObserver | None = None,
) -> TargetedRealDataAdapterResult:
    """Private scientific core invoked only by the atomic runtime adapter."""

    _validate_inputs(
        processing_owners=processing_owners,
        sample=sample,
        target=target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )
    target_value = target.definition.target
    neutral_loss_da = target_value.neutral_loss_da
    if neutral_loss_da is None:
        raise TargetedRealDataAdapterError("target lacks a CID-NL definition")
    baseline_integration_method: BaselineMethod = "asls"
    if peak_config.baseline_integration_method != baseline_integration_method:
        raise TargetedRealDataAdapterError(
            "peak_config requires canonical asls baseline integration"
        )
    bounded = _bounded_current_discovery_candidates(
        candidates=candidates,
        sample=sample,
        target=target,
    )
    if not bounded:
        return _empty_result(
            processing_owners=processing_owners,
            sample=sample,
            target=target,
        )
    if raw is None:
        raise TargetedRealDataAdapterError(
            "bounded candidate universe requires an owner-opened RAW adapter"
        )
    _validate_raw_adapter(raw)

    source = resolve_request_acquisition_source(
        processing_owners=processing_owners,
        sample=sample,
    )
    try:
        bound_raw = require_owner_bound_raw_handle(raw, source)
    except RawReaderError as exc:
        raise TargetedRealDataAdapterError(
            "raw handle is not owner-bound to the resolved acquisition source"
        ) from exc
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace=_ADAPTER_NAMESPACE,
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    strict_scan_seeds = _read_complete_strict_scan_seeds(
        bound_raw,
        candidates=bounded,
        settings=settings,
    )
    targeted_rt_min, targeted_rt_max, _anchor_used, _anchor_rt = get_rt_window(
        bound_raw,
        target_value,
        peak_config,
        reference_rt=None,
    )
    acquisition_rt_min = min(
        targeted_rt_min,
        *(candidate.ms1_search_rt_min for candidate in bounded),
    )
    acquisition_rt_max = max(
        targeted_rt_max,
        *(candidate.ms1_search_rt_max for candidate in bounded),
    )
    request = XICRequest(
        mz=target_value.mz,
        rt_min=acquisition_rt_min,
        rt_max=acquisition_rt_max,
        ppm_tol=target_value.ppm_tol,
    )
    acquisition = _freeze_acquisition(
        acquire_trace_batch(capability, (request,), bound_raw)
    )
    if _observer is not None:
        _observer.record_acquisition(acquisition)
    if len(acquisition.traces) != 1:
        raise TargetedRealDataAdapterError(
            "shared candidate-universe acquisition must produce one trace"
        )
    acquired = acquisition.traces[0]
    observations: list[PeakObservationRef] = []
    artifacts: list[EvidenceArtifactRef] = []
    for candidate in bounded:
        candidate_request = XICRequest(
            mz=target_value.mz,
            rt_min=candidate.ms1_search_rt_min,
            rt_max=candidate.ms1_search_rt_max,
            ppm_tol=target_value.ppm_tol,
        )
        candidate_trace = crop_xic_trace_by_rt(
            acquired.trace,
            candidate_request.rt_min,
            candidate_request.rt_max,
        )
        receipt_detection = detect_ms1_trace_peak(
            candidate_trace.rt,
            candidate_trace.intensity,
            peak_config=peak_config,
            preferred_rt=candidate.best_seed_rt,
            peak_finder=find_peak_and_area,
        )
        if receipt_detection.status != "detected" or receipt_detection.result is None:
            raise TargetedRealDataAdapterError(
                f"{candidate.candidate_id}: current receipt was not reacquired"
            )
        receipt_hypotheses = build_peak_hypotheses(
            sample_name=sample.metadata.sample_name,
            target_label=target.definition.target.label,
            role="ISTD" if target.definition.target.is_istd else "Analyte",
            istd_pair=target.definition.target.istd_pair,
            resolver_mode=settings.resolver_mode,
            peak_result=receipt_detection.result,
            rt=receipt_detection.rt,
            intensity=receipt_detection.intensity,
            baseline_integration_method=baseline_integration_method,
            ms1_morphology_smoothing_window_points=(
                peak_config.ms1_morphology_smoothing_window_points
            ),
        )
        receipt_selected = tuple(
            item for item in receipt_hypotheses if item.audit.selected
        )
        if len(receipt_selected) != 1:
            raise TargetedRealDataAdapterError(
                f"{candidate.candidate_id}: receipt owner did not select one peak"
            )
        _validate_candidate_integration(candidate, receipt_selected[0].integration)

        detection = detect_ms1_trace_peak(
            candidate_trace.rt,
            candidate_trace.intensity,
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
            baseline_integration_method=baseline_integration_method,
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
        boundary_promotion = promote_targeted_boundary_hypothesis(
            selected[0],
            rt=detection.rt,
            intensity=detection.intensity,
            context_rt_start=float(detection.rt[0]),
            context_rt_end=float(detection.rt[-1]),
            morphology_window_points=(
                peak_config.ms1_morphology_smoothing_window_points
            ),
        )
        promoted = boundary_promotion.hypothesis
        integration = promoted.integration
        trace = trace_from_xic_request(
            sample_name=sample.metadata.sample_name,
            request=candidate_request,
            xic_trace=candidate_trace,
            source=f"{_ADAPTER_NAMESPACE}:{TraceCoordinateKind.NATIVE_SCAN.value}",
            acquisition_source_id=source.acquisition_source_id,
        )
        observation = mint_peak_observation_ref(
            processing_owners=processing_owners,
            sample=sample,
            trace=trace,
            integration=integration,
            resolver_mode=settings.resolver_mode,
            proposal_sources=promoted.audit.proposal_sources,
        )
        observations.append(observation)
        _record_artifact(artifacts, _observer, observation.artifact)

    evidence_by_scan: dict[int, tuple[Ms2Scan, DiscoverySeed, CidNlEvidenceRef]] = {}
    support_by_scan: dict[int, DdaCidNlPeakSupportRef] = {}
    supports_by_peak: dict[str, list[DdaCidNlPeakSupportRef]] = {}
    support_policy = DdaCidNlPeakSupportPolicy(
        precursor_tolerance_da=max(
            target.definition.target.mz
            * target.definition.target.ppm_tol
            / 1_000_000.0,
            _WRITER_ROUNDING_ABS_TOL,
        ),
        neutral_loss_tolerance_ppm=settings.nl_tolerance_ppm,
    )
    for scan, seed in strict_scan_seeds:
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
        _record_artifact(artifacts, _observer, evidence.artifact)
        try:
            support = resolve_dda_cid_nl_peak_support_ref(
                processing_owners=processing_owners,
                sample=sample,
                canonical_evidence=evidence,
                candidate_peaks=tuple(observations),
                policy=support_policy,
            )
        except DdaCidNlPeakSupportContractError as exc:
            raise TargetedRealDataAdapterError(
                f"strict scan {scan.scan_number} does not link to exactly one "
                "candidate physical MS1 peak"
            ) from exc
        _record_artifact(artifacts, _observer, support.artifact)
        evidence_by_scan[scan.scan_number] = (scan, seed, evidence)
        support_by_scan[scan.scan_number] = support
        supports_by_peak.setdefault(
            support.content.peak_observation_content_id,
            [],
        ).append(support)

    events_by_peak: dict[str, CidNlChromatographicSupportRef] = {}
    for observation in observations:
        peak_id = observation.content.peak_observation_content_id
        scan_supports = tuple(supports_by_peak.get(peak_id, ()))
        if not scan_supports:
            raise TargetedRealDataAdapterError(
                "candidate physical MS1 peak lacks strict CID-NL scan support"
            )
        event = compose_cid_nl_chromatographic_support_ref(
            processing_owners=processing_owners,
            sample=sample,
            scan_support_refs=scan_supports,
        )
        _record_artifact(artifacts, _observer, event.artifact)
        events_by_peak[peak_id] = event

    hypotheses = []
    projections = []
    for candidate, observation in zip(bounded, observations, strict=True):
        representative = evidence_by_scan.get(candidate.best_ms2_scan_id)
        representative_support = support_by_scan.get(candidate.best_ms2_scan_id)
        if representative is None or representative_support is None:
            raise TargetedRealDataAdapterError(
                f"{candidate.candidate_id}: representative strict scan was not retained"
            )
        scan, seed, evidence = representative
        peak_id = observation.content.peak_observation_content_id
        if representative_support.content.peak_observation_content_id != peak_id:
            raise TargetedRealDataAdapterError(
                f"{candidate.candidate_id}: representative strict scan links to "
                "a different physical MS1 peak"
            )
        event = events_by_peak[peak_id]
        hypothesis = compose_cid_nl_peak_hypothesis(
            processing_owners=processing_owners,
            sample=sample,
            peak_observation=observation,
            chromatographic_support=event,
        )
        hypothesis_artifact = hypothesis.artifact
        if hypothesis_artifact is None:
            raise TargetedRealDataAdapterError(
                "CID-NL hypothesis owner omitted its evidence artifact"
            )
        _record_artifact(artifacts, _observer, hypothesis_artifact)
        expected_product = target_value.mz - neutral_loss_da
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
        _record_artifact(artifacts, _observer, projection.artifact)
        hypotheses.append(hypothesis)
        projections.append(projection)

    lineage = canonical_evidence_artifact_closure(
        producer_attempt=processing_owners.attempt,
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
    if _observer is not None:
        for artifact in candidate_set.lineage_artifacts:
            _observer.record_artifact(artifact)
        _observer.record_artifact(selection.artifact)
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


def _bounded_current_discovery_candidates(
    *,
    candidates: tuple[DiscoveryCandidate, ...],
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
) -> tuple[DiscoveryCandidate, ...]:
    bounded = tuple(
        sorted(
            (item for item in candidates if _matches_target(item, target=target)),
            key=lambda item: item.candidate_id,
        )
    )
    _validate_bounded_candidates(bounded, sample=sample, target=target)
    return bounded


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


def _read_exact_scans(
    raw: TargetedRealDataRaw,
    candidate: DiscoveryCandidate,
) -> tuple[Ms2Scan, ...]:
    declared = tuple(sorted(set(candidate.seed_scan_ids)))
    matches_by_scan: dict[int, list[Ms2Scan]] = {
        scan_number: [] for scan_number in declared
    }
    for event in raw.iter_ms2_scans(
        candidate.rt_seed_min - _WRITER_ROUNDING_ABS_TOL,
        candidate.rt_seed_max + _WRITER_ROUNDING_ABS_TOL,
    ):
        if (
            event.parse_error is None
            and event.scan is not None
            and event.scan.scan_number in matches_by_scan
        ):
            matches_by_scan[event.scan.scan_number].append(event.scan)
    if any(len(matches_by_scan[scan_number]) != 1 for scan_number in declared):
        raise TargetedRealDataAdapterError(
            f"{candidate.candidate_id}: declared strict MS2 scans are not exact"
        )
    return tuple(matches_by_scan[scan_number][0] for scan_number in declared)


def _read_complete_strict_scan_seeds(
    raw: TargetedRealDataRaw,
    *,
    candidates: tuple[DiscoveryCandidate, ...],
    settings: DiscoverySettings,
) -> tuple[tuple[Ms2Scan, DiscoverySeed], ...]:
    by_scan: dict[int, tuple[Ms2Scan, DiscoverySeed]] = {}
    for candidate in candidates:
        for scan in _read_exact_scans(raw, candidate):
            seed = _read_exact_seed(
                scan,
                raw_file=candidate.raw_file,
                candidate=candidate,
                settings=settings,
            )
            existing = by_scan.get(scan.scan_number)
            if existing is not None and existing[1] != seed:
                raise TargetedRealDataAdapterError(
                    f"scan {scan.scan_number}: candidate receipts disagree on "
                    "canonical strict evidence"
                )
            by_scan[scan.scan_number] = (scan, seed)
    return tuple(by_scan[scan_number] for scan_number in sorted(by_scan))


def _read_exact_seed(
    scan: Ms2Scan,
    *,
    raw_file: Path,
    candidate: DiscoveryCandidate,
    settings: DiscoverySettings,
) -> DiscoverySeed:
    representative = scan.scan_number == candidate.best_ms2_scan_id
    matches = tuple(
        seed
        for seed in discovery_seeds_from_scan(
            scan,
            raw_file=raw_file,
            settings=settings,
        )
        if seed.scan_number in candidate.seed_scan_ids
        and seed.neutral_loss_tag == candidate.neutral_loss_tag
        and seed.precursor_mz_basis == "scan_precursor"
        and _within_ppm(
            seed.precursor_mz,
            candidate.precursor_mz,
            settings.precursor_mz_tolerance_ppm,
        )
        and _within_ppm(
            seed.product_mz,
            candidate.product_mz,
            settings.product_mz_tolerance_ppm,
        )
        and _within_ppm(
            seed.observed_neutral_loss_da,
            candidate.observed_neutral_loss_da,
            settings.nl_tolerance_ppm,
        )
        and (
            not representative
            or (
                _receipt_close(seed.precursor_mz, candidate.precursor_mz)
                and _receipt_close(seed.product_mz, candidate.product_mz)
            )
        )
    )
    if len(matches) != 1:
        if representative:
            raise TargetedRealDataAdapterError(
                f"{candidate.candidate_id}: exact canonical Discovery seed "
                "is not unique"
            )
        raise TargetedRealDataAdapterError(
            f"{candidate.candidate_id}: strict scan {scan.scan_number} does not "
            "reconstruct one canonical Discovery seed"
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
    if not isinstance(candidates, tuple):
        raise TargetedRealDataAdapterError("candidates has the wrong owner type")
    if any(not isinstance(item, DiscoveryCandidate) for item in candidates):
        raise TargetedRealDataAdapterError(
            "candidates must contain current DiscoveryCandidate values"
        )


def _validate_raw_adapter(raw: object) -> None:
    for method in ("extract_xic_many", "scan_window_for_request", "iter_ms2_scans"):
        if not callable(getattr(raw, method, None)):
            raise TargetedRealDataAdapterError(f"raw adapter lacks {method}")


def _freeze_acquisition(
    acquisition: TraceAcquisitionBatch,
) -> TraceAcquisitionBatch:
    frozen_traces: list[AcquiredTrace] = []
    for acquired in acquisition.traces:
        rt = np.array(acquired.trace.rt, dtype=float, copy=True)
        intensity = np.array(acquired.trace.intensity, dtype=float, copy=True)
        rt.setflags(write=False)
        intensity.setflags(write=False)
        frozen_traces.append(
            replace(
                acquired,
                trace=type(acquired.trace)(rt=rt, intensity=intensity),
            )
        )
    return replace(acquisition, traces=tuple(frozen_traces))


def _record_artifact(
    artifacts: list[EvidenceArtifactRef],
    observer: _TargetedRealDataWorkObserver | None,
    artifact: EvidenceArtifactRef,
) -> None:
    artifacts.append(artifact)
    if observer is not None:
        observer.record_artifact(artifact)


def _within_ppm(observed: float, expected: float, tolerance_ppm: float) -> bool:
    return abs(observed - expected) / expected * 1_000_000.0 <= tolerance_ppm


def _receipt_close(observed: float, receipt: float) -> bool:
    return math.isclose(
        observed,
        receipt,
        rel_tol=_WRITER_ROUNDING_REL_TOL,
        abs_tol=_WRITER_ROUNDING_ABS_TOL,
    )


__all__ = (
    "TargetedRealDataAdapterError",
)
