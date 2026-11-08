"""Project shared CID-NL answers onto saved Delta Mass detector intervals.

This is review evidence, not physical requalification or a Matrix admission.
The complete nearby detector population retains overlap ambiguity; selecting
fewer Delta Mass candidates must not manufacture unique NL attribution.
"""

from __future__ import annotations

from xic_extractor.diagnostics.delta_mass_review_data import (
    ReviewCandidate,
    ReviewNlMatch,
    ReviewNlPosition,
)
from xic_extractor.discovery.detected_features import DetectedFeature
from xic_extractor.discovery.models import DiscoverySeed
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlCandidateInterval,
    CidNlEventQuery,
    evaluate_cid_nl_method_relations,
)
from xic_extractor.peak_detection.cid_nl_peak_support_models import (
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.precursor_evidence import PrecursorMassScope


def assess_review_neutral_losses(
    candidate: ReviewCandidate,
    *,
    features: tuple[DetectedFeature, ...],
    seeds: tuple[DiscoverySeed, ...],
    settings: DiscoverySettings,
) -> tuple[ReviewNlPosition, ...]:
    """Use saved peak bounds and the existing mass/precursor/relation owners."""
    results = []
    spectra = {s.scan_number: s for s in candidate.spectra}
    for position in candidate.positions:
        tolerance = position.query_mz * settings.precursor_mz_tolerance_ppm * 1e-6
        scope = PrecursorMassScope.around(
            ((position.query_mz, tolerance),),
            maximum_inference_radius_da=settings.ms2_precursor_tol_da,
        )
        population = tuple(
            (f.feature_id, CidNlCandidateInterval(f.mz, f.rt_start_min, f.rt_end_min))
            for f in features
            if f.sample_stem == candidate.sample_stem
            and abs(f.mz - position.query_mz) <= 2 * tolerance
            and f.rt_start_min is not None
            and f.rt_end_min is not None
        )
        target = next(
            (
                i
                for i, (feature_id, _) in enumerate(population)
                if feature_id == position.feature_id
            ),
            None,
        )
        bounds = position.peak_rt_window
        for profile in settings.neutral_loss_profiles:
            if bounds is None or target is None:
                results.append(
                    ReviewNlPosition(
                        position.label,
                        profile.tag,
                        profile.neutral_loss_da,
                        "no_peak_bounds",
                        (),
                        (),
                    )
                )
                continue
            lo, hi = bounds
            related = tuple(
                s.scan_number
                for s in candidate.spectra
                if lo <= s.rt_min <= hi and scope(s.precursor_mz, s.precursor_metadata)
            )
            relevant = tuple(
                seed
                for seed in seeds
                if seed.sample_stem == candidate.sample_stem
                and seed.neutral_loss_tag == profile.tag
                and abs(seed.precursor_mz - position.query_mz) <= tolerance
                and lo <= seed.rt <= hi
            )
            queries = tuple(
                CidNlEventQuery(
                    seed.assess_precursor_loss(settings.nl_tolerance_ppm),
                    seed.rt,
                    seed.ms1_precursor_observation.rt_min
                    if seed.ms1_precursor_observation
                    else seed.rt,
                )
                for seed in relevant
            )
            relations = evaluate_cid_nl_method_relations(
                candidate_intervals=tuple(interval for _, interval in population),
                event_queries=queries,
                policy=DdaCidNlPeakSupportPolicy(tolerance, settings.nl_tolerance_ppm),
            )
            supported = {
                event
                for event, peak in relations.individual_relations
                if peak == target
            }
            matches = []
            for index, (seed, query) in enumerate(zip(relevant, queries, strict=True)):
                assessment = query.precursor
                status = (
                    "supported"
                    if index in supported
                    else "ambiguous"
                    if index in relations.set_relation_event_indexes
                    else assessment.status
                    if not assessment.supports_nl
                    else "unassigned_peak"
                )
                scan = spectra[seed.scan_number]
                base = max(scan.intensity, default=0.0)
                matches.append(
                    ReviewNlMatch(
                        seed.scan_number,
                        assessment.reference_mz or seed.precursor_mz,
                        seed.product_mz,
                        seed.product_intensity / base if base > 0 else 0.0,
                        assessment.observed_loss_da,
                        assessment.observed_loss_error_ppm,
                        assessment.reference_source or seed.precursor_mz_basis,
                        status,
                    )
                )
            incomplete = (
                candidate.rt_window[0] > lo
                or candidate.rt_window[1] < hi
                or any(
                    e.requested_rt_window[0] <= hi and e.requested_rt_window[1] >= lo
                    for e in candidate.ms2_read_errors
                )
            )
            status = (
                "supported"
                if supported
                else "ambiguous"
                if any(m.status == "ambiguous" for m in matches)
                else "unresolved"
                if matches
                else "incomplete"
                if incomplete
                else "no_ms2"
                if not related
                else "no_spectrum"
                if not any(spectra[n].mz for n in related)
                else "no_match"
            )
            results.append(
                ReviewNlPosition(
                    position.label,
                    profile.tag,
                    profile.neutral_loss_da,
                    status,
                    related,
                    tuple(matches),
                )
            )
    return tuple(results)
