"""Bind a sample region population to its declared CID-NL method subjects."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path

from xic_extractor.alignment.backfill.conservative import is_backfill_eligible_sample
from xic_extractor.alignment.cid_nl_inputs import (
    UntargetedCidNlAssessmentInput,
)
from xic_extractor.discovery.ion_grouping import event_ion_mz
from xic_extractor.discovery.models import (
    DiscoveryPeak,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import mz_tolerance_da
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelation,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceObservation,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentContractError,
    CidNlTechnicalState,
    mint_cid_nl_method_assessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupport,
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
    RetainedCaptureEvidence,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.partition.cid_nl_raw_source import (
    CidNlAcquisitionError,
    CidNlRawSource,
    _within_ppm,
    read_direct_cid_nl_seeds,
)
from xic_extractor.untargeted_execution.partition.cid_nl_region_acquisition import (
    CidNlSampleRegion,
    attach_cid_nl_events,
)

_WRITER_ROUNDING_REL_TOL = 5e-6
_WRITER_ROUNDING_ABS_TOL = 5e-6


@dataclass(frozen=True, slots=True)
class CidNlAcquisitionResult:
    assessment_input: UntargetedCidNlAssessmentInput
    candidate_observations: tuple[tuple[str, PeakObservation], ...]
    source_candidate_ids: tuple[str, ...]
    strict_scan_count: int
    capture_evidence: tuple[RetainedCaptureEvidence, ...] = dataclass_field(
        default=(), compare=False
    )


def assess_sample_regions(
    *,
    regions: tuple[CidNlSampleRegion, ...],
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    scopes: tuple[tuple[CidNlMethodSubject, tuple[DiscoveryPeak, ...]], ...],
    processing_attempt_id: str,
    settings: DiscoverySettings,
    raw: CidNlRawSource | None,
) -> dict[str, CidNlAcquisitionResult]:
    """Bind a sample's measured peaks and separate scan relations to subjects.

    A Discovery row supplies lineage only if its recorded representative
    scan resolves to an independently measured physical peak.
    """
    evidence = tuple(ref for region in regions for ref in region.evidence)
    supports = tuple(ref for region in regions for ref in region.individual_relations)
    ambiguities = tuple(ref for region in regions for ref in region.ambiguous_relations)
    observed_by_scan: dict[tuple[int, float], set[PeakObservation]] = {}
    for relation in supports:
        observed_by_scan.setdefault(
            (relation.dda_scan_number, relation.configured_neutral_loss_da), set()
        ).add(relation.peak_observation)

    bindings: dict[str, list[tuple[str, PeakObservation]]] = {}
    lineage_subject_by_peak: dict[PeakObservation, str] = {}
    for subject, candidates in scopes:
        key = subject.method_subject_id
        for candidate in candidates:
            loss = settings.neutral_loss_by_tag[candidate.neutral_loss_tag]
            observed = [
                peak
                for peak in observed_by_scan.get((candidate.best_ms2_scan_id, loss), ())
                if _within_ppm(
                    peak.mz, subject.precursor_mz, subject.precursor_tolerance_ppm
                )
            ]
            if len(observed) != 1:
                continue
            (peak,) = observed
            prior = lineage_subject_by_peak.setdefault(peak, key)
            if prior == key:
                bindings.setdefault(key, []).append((candidate.peak_id, peak))

    peaks_by_subject: dict[str, list[PeakObservation]] = {}
    for region in regions:
        for peak in region.peaks:
            compatible = tuple(
                subject
                for subject, _ in scopes
                if _within_ppm(
                    peak.mz, subject.precursor_mz, subject.precursor_tolerance_ppm
                )
            )
            selected_key = lineage_subject_by_peak.get(peak)
            if selected_key is None:
                if not compatible:
                    continue
                subject = min(
                    compatible,
                    key=lambda item: (
                        abs(peak.mz - item.precursor_mz),
                        item.precursor_mz,
                    ),
                )
                selected_key = subject.method_subject_id
            peaks_by_subject.setdefault(selected_key, []).append(peak)

    additional_refs: frozenset[CidNlEvidenceObservation] = frozenset()
    if (
        raw is not None
        and settings.tag_combine_mode == "intersection"
        and is_backfill_eligible_sample(sample)
    ):
        pending = _unresolved_intersection_peaks(scopes, peaks_by_subject, supports)
        if pending:
            declared = frozenset(
                (ref.scan.scan_number, ref.scan.configured_neutral_loss_da)
                for ref in evidence
            )
            new_refs, new_support, new_sets = _acquire_intersection_relations(
                raw,
                sample=sample,
                acquisition_source=acquisition_source,
                settings=settings,
                pending=pending,
                regions=regions,
                declared=declared,
            )
            additional_refs = frozenset(new_refs)
            evidence += new_refs
            supports += new_support
            ambiguities += new_sets

    by_subject = {}
    for subject, candidates in scopes:
        key = subject.method_subject_id
        peaks = tuple(peaks_by_subject.get(key, ()))
        own = frozenset(peaks)

        policy = DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=mz_tolerance_da(
                subject.precursor_mz,
                subject.precursor_tolerance_ppm,
                minimum_da=_WRITER_ROUNDING_ABS_TOL,
            ),
            neutral_loss_tolerance_ppm=settings.nl_tolerance_ppm,
        )

        individual = tuple(
            item
            for item in supports
            if item.peak_observation in own
            and item.configured_neutral_loss_da in subject.configured_losses
        )
        ambiguous = tuple(
            item
            for item in ambiguities
            if item.evidence.scan.configured_neutral_loss_da
            in subject.configured_losses
            and any(p in own for p in item.matched_candidates)
        )
        linked = {item.evidence for item in individual} | {
            item.evidence for item in ambiguous
        }
        refs = tuple(
            item
            for item in evidence
            if item in linked
            or (
                item.scan.configured_neutral_loss_da in subject.configured_losses
                and _within_ppm(
                    item.scan.assess_precursor_loss(
                        settings.nl_tolerance_ppm
                    ).reference_mz
                    or item.scan.precursor_mz,
                    subject.precursor_mz,
                    subject.precursor_tolerance_ppm,
                )
            )
        )
        assessment = _mint_assessment(
            sample=sample,
            acquisition_source=acquisition_source,
            method_subject=subject,
            settings=settings,
            raw=raw,
            support_policy=policy,
            candidate_peaks=peaks,
            evidence_refs=refs,
            individual_support_refs=individual,
            set_relation_refs=ambiguous,
            technical_state="evaluated" if refs else "not_evaluated",
            supplemental_scan_keys=tuple(
                sorted(
                    (ref.scan.scan_number, ref.scan.configured_neutral_loss_da)
                    for ref in refs
                    if ref in additional_refs
                )
            ),
            technical_reason_code=None
            if refs
            else "no_search_eligible_discovery_input",
        )
        bound = tuple(bindings.get(key, ()))
        candidate_by_peak = {p: cid for cid, p in bound}
        captures = tuple(
            RetainedCaptureEvidence(
                sample_key=sample.sample_name,
                processing_attempt_id=processing_attempt_id,
                method_subject_id=key,
                candidate_id=candidate_by_peak.get(peak),
                capture_stage="initial_capture",
                trace=peak.source_trace,
                physical_evaluation=peak.physical_evaluation,
                observation_ref=peak,
            )
            for peak in peaks
        )
        by_subject[key] = CidNlAcquisitionResult(
            assessment_input=UntargetedCidNlAssessmentInput(sample, assessment),
            candidate_observations=bound,
            source_candidate_ids=tuple(p.peak_id for p in candidates),
            strict_scan_count=len(
                {
                    item.scan.scan_number
                    for item in refs
                    if item.scan.assess_precursor_loss(
                        settings.nl_tolerance_ppm
                    ).supports_nl
                }
            ),
            capture_evidence=captures,
        )
    return by_subject


def _unresolved_intersection_peaks(
    scopes: tuple[tuple[CidNlMethodSubject, tuple[DiscoveryPeak, ...]], ...],
    peaks_by_subject: Mapping[str, list[PeakObservation]],
    supports: tuple[DdaCidNlPeakSupport, ...],
) -> frozenset[PeakObservation]:
    support_losses: dict[PeakObservation, set[float]] = {}
    for support in supports:
        support_losses.setdefault(support.peak_observation, set()).add(
            support.configured_neutral_loss_da
        )
    return frozenset(
        peak
        for subject, _ in scopes
        if subject.composition is not None
        and subject.composition.combine_mode == "intersection"
        for peak in peaks_by_subject.get(subject.method_subject_id, ())
        if not set(subject.configured_losses).issubset(support_losses.get(peak, ()))
    )


def _merged_peak_windows(
    peaks: frozenset[PeakObservation],
) -> Iterator[tuple[float, float]]:
    start, end = 0.0, -1.0
    for low, high in sorted((peak.rt_left_min, peak.rt_right_min) for peak in peaks):
        if low > end:
            if end >= start:
                yield start, end
            start, end = low, high
        else:
            end = max(end, high)
    if end >= start:
        yield start, end


def _acquire_intersection_relations(
    raw: CidNlRawSource,
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    settings: DiscoverySettings,
    pending: frozenset[PeakObservation],
    regions: tuple[CidNlSampleRegion, ...],
    declared: frozenset[tuple[int, float]],
) -> tuple[
    tuple[CidNlEvidenceObservation, ...],
    tuple[DdaCidNlPeakSupport, ...],
    tuple[DdaCidNlCandidateSetRelation, ...],
]:
    """Resolve unreported local ranks without searching beyond measured lobes."""
    seeds = tuple(
        seed
        for rt_min, rt_max in _merged_peak_windows(pending)
        for seed in read_direct_cid_nl_seeds(
            raw,
            raw_file=Path(sample.raw_stem),
            settings=settings,
            rt_min=rt_min,
            rt_max=rt_max,
        )
        if (seed.scan.scan_number, seed.scan.configured_neutral_loss_da) not in declared
    )
    evidence: list[CidNlEvidenceObservation] = []
    supports: list[DdaCidNlPeakSupport] = []
    ambiguities: list[DdaCidNlCandidateSetRelation] = []
    for region in regions:
        if pending.isdisjoint(region.peaks):
            continue
        low, high = region.region.mz_bounds
        events = tuple(seed for seed in seeds if low <= event_ion_mz(seed) <= high)
        local_evidence, local_support, local_sets = attach_cid_nl_events(
            sample=sample,
            acquisition_source=acquisition_source,
            peaks=region.peaks,
            events=events,
            settings=settings,
        )
        kept_support = tuple(
            ref for ref in local_support if ref.peak_observation in pending
        )
        kept_sets = tuple(
            ref for ref in local_sets if not pending.isdisjoint(ref.matched_candidates)
        )
        supports.extend(kept_support)
        ambiguities.extend(kept_sets)
        related = {ref.evidence for ref in kept_support} | {
            ref.evidence for ref in kept_sets
        }
        evidence.extend(ref for ref in local_evidence if ref in related)
    return tuple(evidence), tuple(supports), tuple(ambiguities)


def _mint_assessment(
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    method_subject: CidNlMethodSubject,
    settings: DiscoverySettings,
    raw: CidNlRawSource | None,
    support_policy: DdaCidNlPeakSupportPolicy,
    candidate_peaks: tuple[PeakObservation, ...] = (),
    evidence_refs: tuple[CidNlEvidenceObservation, ...] = (),
    individual_support_refs: tuple[DdaCidNlPeakSupport, ...] = (),
    set_relation_refs: tuple[DdaCidNlCandidateSetRelation, ...] = (),
    technical_state: CidNlTechnicalState = "evaluated",
    supplemental_scan_keys: tuple[tuple[int, float], ...] = (),
    technical_reason_code: str | None = None,
):
    search_completed = technical_state == "evaluated"
    candidate_query_rt_mins = tuple(
        peak.generation_provenance.query_rt_min for peak in candidate_peaks
    )
    candidate_query_rt_maxes = tuple(
        peak.generation_provenance.query_rt_max for peak in candidate_peaks
    )
    query_rt_min, query_rt_max = settings.rt_min, settings.rt_max
    if search_completed and (query_rt_min is None or query_rt_max is None):
        if raw is None:
            raise CidNlAcquisitionError(
                "automatic acquisition bounds require an opened RAW"
            )
        acquisition_start, acquisition_end = raw.acquisition_rt_bounds()
        query_rt_min = acquisition_start if query_rt_min is None else query_rt_min
        query_rt_max = acquisition_end if query_rt_max is None else query_rt_max
    elif not search_completed and (query_rt_min is None or query_rt_max is None):
        query_rt_min = query_rt_max = None
    try:
        return mint_cid_nl_method_assessment(
            sample=sample,
            acquisition_source=acquisition_source,
            method_subject=method_subject,
            detector_invocation_id="untargeted-cid-nl-real-data",
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=(
                    min(query_rt_min, *candidate_query_rt_mins)
                    if query_rt_min is not None and candidate_query_rt_mins
                    else query_rt_min
                ),
                query_rt_max=(
                    max(query_rt_max, *candidate_query_rt_maxes)
                    if query_rt_max is not None and candidate_query_rt_maxes
                    else query_rt_max
                ),
                support_policy=support_policy,
                acquisition_supported=technical_state != "unsupported",
                search_completed=search_completed,
                reason_code=technical_reason_code,
                evidence_scope="subject_window"
                if supplemental_scan_keys
                else "declared_scans",
                supplemental_scan_keys=supplemental_scan_keys,
            ),
            candidate_peaks=candidate_peaks,
            evidence_refs=evidence_refs,
            individual_support_refs=individual_support_refs,
            set_relation_refs=set_relation_refs,
            technical_state=technical_state,
        )
    except CidNlMethodAssessmentContractError as exc:
        raise CidNlAcquisitionError(str(exc)) from exc


def _bounded_subject_candidates(
    peaks: tuple[DiscoveryPeak, ...],
    *,
    method_subject: CidNlMethodSubject,
    settings: DiscoverySettings,
) -> tuple[DiscoveryPeak, ...]:
    return tuple(
        sorted(
            (
                peak
                for peak in peaks
                if _within_ppm(
                    peak.peak_mz,
                    method_subject.precursor_mz,
                    method_subject.precursor_tolerance_ppm,
                )
                and peak.neutral_loss_tag in settings.selected_tag_names
                and any(
                    math.isclose(
                        settings.neutral_loss_by_tag[peak.neutral_loss_tag],
                        loss,
                        rel_tol=_WRITER_ROUNDING_REL_TOL,
                        abs_tol=_WRITER_ROUNDING_ABS_TOL,
                    )
                    for loss in method_subject.configured_losses
                )
            ),
            key=lambda item: item.peak_id,
        )
    )
