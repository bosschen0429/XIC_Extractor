import json
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    DiscoverySettings,
    GroupPrecursorMzBasis,
    NeutralLossErrorBasis,
    assign_discovery_candidate_state,
    build_ms1_feature_row_id,
)
from xic_extractor.discovery.priority import (
    assign_review_priority,
)
from xic_extractor.mass_accuracy import (
    PPM_BOUNDARY_EPSILON,
    neutral_loss_match,
    within_ppm,
)

_TagIntersectionStatus = Literal["not_required", "complete", "incomplete"]


MS1CentroidMz = Callable[[float, float, float], float | None]
"""(apex RT, m/z min, m/z max) -> most intense MS1 centroid m/z at that scan."""


@dataclass(frozen=True, slots=True)
class DiscoveryCandidateMergeBinding:
    final_ordinal: int
    representative_source_ordinal: int
    member_source_ordinals: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryCandidateMergeResult:
    candidates: tuple[DiscoveryCandidate, ...]
    bindings: tuple[DiscoveryCandidateMergeBinding, ...]


class _PrecursorMassIndex:
    """Candidate ordinals bucketed by precursor m/z for exact merge filtering."""

    _BUCKET_WIDTH_DA = 1.0

    def __init__(self) -> None:
        self._buckets: dict[int, set[int]] = {}

    def add(self, ordinal: int, precursor_mz: float) -> None:
        bucket = self._bucket(precursor_mz)
        if bucket is not None:
            self._buckets.setdefault(bucket, set()).add(ordinal)

    def remove(self, ordinal: int, precursor_mz: float) -> None:
        bucket = self._bucket(precursor_mz)
        if bucket is None:
            return
        ordinals = self._buckets.get(bucket)
        if ordinals is None:
            return
        ordinals.discard(ordinal)
        if not ordinals:
            del self._buckets[bucket]

    def candidate_ordinals(
        self, reference_mz: float, tolerance_ppm: float
    ) -> tuple[int, ...]:
        if (
            not math.isfinite(reference_mz)
            or not math.isfinite(tolerance_ppm)
            or reference_mz <= 0
            or tolerance_ppm < 0
        ):
            return ()
        radius_da = (
            reference_mz * tolerance_ppm * 1e-6
            + reference_mz * PPM_BOUNDARY_EPSILON * 1e-6
        )
        if not math.isfinite(radius_da):
            return self._all_ordinals()
        radius_da = math.nextafter(radius_da, math.inf)
        first_bucket = math.floor((reference_mz - radius_da) / self._BUCKET_WIDTH_DA)
        last_bucket = math.floor((reference_mz + radius_da) / self._BUCKET_WIDTH_DA)
        bucket_span = last_bucket - first_bucket + 1
        if bucket_span <= max(64, 2 * len(self._buckets)):
            ordinals = {
                ordinal
                for bucket in range(first_bucket, last_bucket + 1)
                for ordinal in self._buckets.get(bucket, ())
            }
        else:
            ordinals = {
                ordinal
                for bucket, bucket_ordinals in self._buckets.items()
                if first_bucket <= bucket <= last_bucket
                for ordinal in bucket_ordinals
            }
        return tuple(sorted(ordinals))

    def _all_ordinals(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                ordinal
                for bucket_ordinals in self._buckets.values()
                for ordinal in bucket_ordinals
            )
        )

    @classmethod
    def _bucket(cls, precursor_mz: float) -> int | None:
        if not math.isfinite(precursor_mz) or precursor_mz <= 0:
            return None
        return math.floor(precursor_mz / cls._BUCKET_WIDTH_DA)


def merge_candidates_by_ms1_peak_with_lineage(
    candidates: Iterable[DiscoveryCandidate],
    *,
    settings: DiscoverySettings,
    ms1_centroid_mz: MS1CentroidMz,
) -> DiscoveryCandidateMergeResult:
    merged: list[DiscoveryCandidate] = []
    mass_index = _PrecursorMassIndex()
    member_source_ordinals: list[list[int]] = []
    representative_source_ordinals: list[int] = []
    for source_ordinal, candidate in enumerate(candidates):
        match = _matching_ms1_peak_candidate(
            candidate,
            merged,
            settings=settings,
            ms1_centroid_mz=ms1_centroid_mz,
            candidate_ordinals=mass_index.candidate_ordinals(
                candidate.precursor_mz,
                2 * settings.precursor_mz_tolerance_ppm,
            ),
        )
        if match is None:
            merged.append(candidate)
            mass_index.add(len(merged) - 1, candidate.precursor_mz)
            member_source_ordinals.append([source_ordinal])
            representative_source_ordinals.append(source_ordinal)
            continue
        merge_index, anchor = match
        current = merged[merge_index]
        representative = anchor or _representative_candidate(
            current,
            candidate,
            settings=settings,
        )
        mass_index.remove(merge_index, current.precursor_mz)
        merged_candidate = _merge_candidate_pair(
            current,
            candidate,
            settings=settings,
            representative=representative,
        )
        merged[merge_index] = merged_candidate
        mass_index.add(merge_index, merged_candidate.precursor_mz)
        member_source_ordinals[merge_index].append(source_ordinal)
        if representative is candidate:
            representative_source_ordinals[merge_index] = source_ordinal
    return DiscoveryCandidateMergeResult(
        candidates=tuple(merged),
        bindings=tuple(
            DiscoveryCandidateMergeBinding(
                final_ordinal=final_ordinal,
                representative_source_ordinal=representative_source_ordinals[
                    final_ordinal
                ],
                member_source_ordinals=tuple(member_source_ordinals[final_ordinal]),
            )
            for final_ordinal in range(len(merged))
        ),
    )


def same_apex_representative_alternatives(
    candidates: Sequence[DiscoveryCandidate],
    binding: DiscoveryCandidateMergeBinding,
    *,
    settings: DiscoverySettings,
) -> Iterable[tuple[DiscoveryCandidate, DiscoveryCandidateMergeBinding]]:
    """Rebuild merged facts around ranked original same-apex physical members.

    Qualification remains with the caller. Broader overlap/ion-anchor merges
    must not replace the retained physical peak through this fallback.
    """
    retained = candidates[binding.representative_source_ordinal]
    alternatives = sorted(
        (
            ordinal
            for ordinal in binding.member_source_ordinals
            if ordinal != binding.representative_source_ordinal
            and _same_ms1_apex_within_extraction_tolerance(
                retained, candidates[ordinal], settings=settings
            )
        ),
        key=lambda ordinal: _representative_rank(
            candidates[ordinal], settings=settings
        ),
    )
    for ordinal in alternatives:
        representative = candidates[ordinal]
        rebuilt = representative
        for member in binding.member_source_ordinals:
            if member != ordinal:
                rebuilt = _merge_candidate_pair(
                    rebuilt,
                    candidates[member],
                    settings=settings,
                    representative=representative,
                )
        yield rebuilt, replace(binding, representative_source_ordinal=ordinal)


def close_physical_candidate_units(
    merged: DiscoveryCandidateMergeResult,
    units: tuple[tuple[float | None, tuple[str, ...]], ...],
    *,
    settings: DiscoverySettings,
) -> DiscoveryCandidateMergeResult:
    """Close proven duplicate views, retaining one area and all source lineage."""
    by_id = {
        candidate.candidate_id: (candidate, binding)
        for candidate, binding in zip(merged.candidates, merged.bindings, strict=True)
    }
    closed: list[tuple[DiscoveryCandidate, DiscoveryCandidateMergeBinding]] = []
    for _, ids in units:
        members = [by_id[candidate_id] for candidate_id in ids]
        representative, representative_binding = min(
            members, key=lambda item: _representative_rank(item[0], settings=settings)
        )
        candidate = representative
        for other, _ in members:
            if other is not representative:
                candidate = _merge_candidate_pair(
                    candidate, other, settings=settings, representative=representative
                )
        ordinals = tuple(
            sorted(
                ordinal
                for _, binding in members
                for ordinal in binding.member_source_ordinals
            )
        )
        closed.append(
            (
                candidate,
                DiscoveryCandidateMergeBinding(
                    final_ordinal=min(binding.final_ordinal for _, binding in members),
                    representative_source_ordinal=representative_binding.representative_source_ordinal,
                    member_source_ordinals=ordinals,
                ),
            )
        )
    closed.sort(key=lambda item: item[1].final_ordinal)
    return DiscoveryCandidateMergeResult(
        candidates=tuple(candidate for candidate, _ in closed),
        bindings=tuple(
            replace(binding, final_ordinal=index)
            for index, (_, binding) in enumerate(closed)
        ),
    )


def _matching_ms1_peak_candidate(
    candidate: DiscoveryCandidate,
    merged: list[DiscoveryCandidate],
    *,
    settings: DiscoverySettings,
    ms1_centroid_mz: MS1CentroidMz,
    candidate_ordinals: Iterable[int] | None = None,
) -> tuple[int, DiscoveryCandidate | None] | None:
    """The merge target and, for one measured ion, the candidate that holds it."""

    if candidate.discovery_candidate_state != "ms1_feature_nl_supported":
        # Repeated delivery of the identical observation is not new support.
        for index in (
            range(len(merged)) if candidate_ordinals is None else candidate_ordinals
        ):
            if merged[index] == candidate:
                return index, None
        return None

    # Every merge path requires precursor agreement; the measured-ion path
    # permits twice the extraction tolerance. Reject distant ions before the
    # more expensive provenance, peak and centroid checks, retaining scan order.
    precursor_tolerance = 2 * settings.precursor_mz_tolerance_ppm
    ordinals = range(len(merged)) if candidate_ordinals is None else candidate_ordinals
    for index in ordinals:
        existing = merged[index]
        if existing.discovery_candidate_state != "ms1_feature_nl_supported":
            continue
        if not _within_ppm(
            existing.precursor_mz, candidate.precursor_mz, precursor_tolerance
        ):
            continue
        if _can_merge_by_ms1_peak(existing, candidate, settings=settings):
            return index, None
        if not _tags_can_merge(existing, candidate, settings=settings):
            continue
        anchor = _same_ms1_peak_on_one_measured_ion(
            existing, candidate, settings=settings, ms1_centroid_mz=ms1_centroid_mz
        )
        if anchor is not None:
            return index, anchor
    return None


def _tags_can_merge(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
) -> bool:
    return (
        first.neutral_loss_tag == second.neutral_loss_tag
        or settings.tag_combine_mode in {"union", "intersection"}
    )


def _can_merge_by_ms1_peak(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
) -> bool:
    if _same_ms1_apex_within_extraction_tolerance(first, second, settings=settings):
        return _tags_can_merge(first, second, settings=settings)
    same_ms1_feature_row = _same_ms1_feature_row_identity(first, second)
    if not (
        first.raw_file == second.raw_file
        and first.sample_stem == second.sample_stem
        and first.ms1_peak_found
        and second.ms1_peak_found
        and _candidate_peak_bounds_present(first)
        and _candidate_peak_bounds_present(second)
        and _within_ppm(
            first.precursor_mz,
            second.precursor_mz,
            settings.precursor_mz_tolerance_ppm,
        )
        and _peak_intervals_overlap(first, second)
        and (_seed_ranges_touch_shared_peak(first, second) or same_ms1_feature_row)
    ):
        return False
    if first.neutral_loss_tag == second.neutral_loss_tag:
        if not _within_ppm(
            first.product_mz,
            second.product_mz,
            settings.product_mz_tolerance_ppm,
        ):
            return False
        if same_ms1_feature_row:
            return _configured_neutral_loss_compatible(
                first,
                settings=settings,
            ) and _configured_neutral_loss_compatible(second, settings=settings)
        return _within_ppm(
            first.observed_neutral_loss_da,
            second.observed_neutral_loss_da,
            settings.nl_tolerance_ppm,
        )
    return settings.tag_combine_mode in {"union", "intersection"}


def _same_ms1_feature_row_identity(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> bool:
    return (
        bool(first.ms1_feature_row_id)
        and first.ms1_feature_row_id == second.ms1_feature_row_id
    )


def _configured_neutral_loss_compatible(
    candidate: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
) -> bool:
    return neutral_loss_match(
        candidate.precursor_mz,
        candidate.product_mz,
        candidate.configured_neutral_loss_da,
        settings.nl_tolerance_ppm,
        observed_loss_da=candidate.observed_neutral_loss_da,
    )[2]


def _merge_candidate_pair(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
    representative: DiscoveryCandidate,
) -> DiscoveryCandidate:
    if first == second:
        return first
    seed_scan_ids = tuple(sorted(set(first.seed_scan_ids + second.seed_scan_ids)))
    seed_event_count = first.seed_event_count + second.seed_event_count
    rt_seed_min = min(first.rt_seed_min, second.rt_seed_min)
    rt_seed_max = max(first.rt_seed_max, second.rt_seed_max)
    seed_delta = (
        representative.ms1_apex_rt - representative.best_seed_rt
        if representative.ms1_apex_rt is not None
        else None
    )
    priority = assign_review_priority(
        seed_event_count=seed_event_count,
        ms1_peak_found=True,
        ms1_seed_delta_min=seed_delta,
        settings=settings,
    )
    reason = "MS2 NL seeds merged by shared MS1 peak; MS1 peak found near seed RT"
    matched_tag_names = ordered_tag_names(
        first.matched_tag_names + second.matched_tag_names,
        settings=settings,
    )
    precursor_mz_basis = _combined_precursor_mz_basis(first, second)
    return replace(
        representative,
        seed_event_count=seed_event_count,
        seed_scan_ids=seed_scan_ids,
        rt_seed_min=rt_seed_min,
        rt_seed_max=rt_seed_max,
        ms1_search_rt_min=min(first.ms1_search_rt_min, second.ms1_search_rt_min),
        ms1_search_rt_max=max(first.ms1_search_rt_max, second.ms1_search_rt_max),
        ms1_seed_delta_min=seed_delta,
        ms2_product_max_intensity=max(
            first.ms2_product_max_intensity,
            second.ms2_product_max_intensity,
        ),
        neutral_loss_error_basis=_combined_neutral_loss_error_basis(first, second),
        precursor_mz_basis=precursor_mz_basis,
        max_scan_precursor_abs_delta_da=_combined_max_scan_precursor_abs_delta_da(
            first,
            second,
        ),
        discovery_candidate_state=assign_discovery_candidate_state(
            ms1_peak_found=True,
            has_nl_support=any(
                item.discovery_candidate_state == "ms1_feature_nl_supported"
                for item in (first, second)
            ),
        ),
        ms1_feature_row_id=build_ms1_feature_row_id(
            sample_stem=representative.sample_stem,
            neutral_loss_tag=representative.neutral_loss_tag,
            precursor_mz=representative.precursor_mz,
            best_seed_rt=representative.best_seed_rt,
            ms1_peak_found=True,
            ms1_apex_rt=representative.ms1_apex_rt,
            ms1_peak_rt_start=representative.ms1_peak_rt_start,
            ms1_peak_rt_end=representative.ms1_peak_rt_end,
        ),
        review_priority=priority,
        reason=reason,
        selected_tag_count=len(settings.selected_tag_names),
        matched_tag_count=len(matched_tag_names),
        matched_tag_names=matched_tag_names,
        primary_tag_name=representative.neutral_loss_tag,
        tag_combine_mode=settings.tag_combine_mode,
        tag_intersection_status=tag_intersection_status(
            matched_tag_names,
            settings=settings,
        ),
        tag_evidence_json=_merge_tag_evidence_json(first, second),
        is_provisional=(first.is_provisional and second.is_provisional),
        # A selected member supplies the precursor; only a purely inferred
        # candidate reports the ion read at its apex.
        ms1_apex_mz=(
            next(
                (
                    member.ms1_apex_mz
                    for member in (representative, first, second)
                    if member.ms1_apex_mz is not None
                ),
                None,
            )
            if precursor_mz_basis == "product_plus_neutral_loss"
            else None
        ),
    )


def _same_ms1_apex_within_extraction_tolerance(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
) -> bool:
    """Whether two candidates resolved the same MS1 peak.

    Each candidate's trace is extracted at its own precursor m/z with the MS1
    tolerance. Within that tolerance both traces hold the same ions, so a shared
    apex scan identifies one peak whatever the seeds, fragments or boundaries.
    Only the precursor m/z decides: an observed loss inherits the absolute error
    of precursor and product, so a ppm comparison on a ~116 Da loss would split
    one peak on measurement scatter.
    """

    return (
        first.raw_file == second.raw_file
        and first.sample_stem == second.sample_stem
        and first.ms1_peak_found
        and second.ms1_peak_found
        and first.ms1_apex_rt is not None
        and first.ms1_apex_rt == second.ms1_apex_rt
        and _within_ppm(
            first.precursor_mz,
            second.precursor_mz,
            settings.precursor_mz_tolerance_ppm,
        )
    )


def _same_ms1_peak_on_one_measured_ion(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
    ms1_centroid_mz: MS1CentroidMz,
) -> DiscoveryCandidate | None:
    """The candidate whose trace holds the whole peak of one ion both traced.

    A precursor inferred as product + loss carries the MS2 product error, so two
    candidates of one ion can differ by more than the MS1 tolerance. A window
    that clips the ion shows only part of its peak, such as the tail, and the
    resolver reports that part with its own apex. The higher candidate (the
    anchor) traced the peak itself: the other apex inside the anchor's bounds is
    one peak on the anchor's trace, and the ions read at the two apexes, each
    inside its own window, agreeing within tolerance show that both traces came
    from one ion. Returns the anchor, or None when either condition fails.
    """

    tolerance = settings.precursor_mz_tolerance_ppm
    if not (
        first.raw_file == second.raw_file
        and first.sample_stem == second.sample_stem
        and _has_resolved_ms1_peak(first)
        and _has_resolved_ms1_peak(second)
        and _within_ppm(first.precursor_mz, second.precursor_mz, 2 * tolerance)
    ):
        return None
    anchor, other = sorted((first, second), key=lambda c: -cast(float, c.ms1_height))
    if not (
        cast(float, anchor.ms1_peak_rt_start)
        <= cast(float, other.ms1_apex_rt)
        <= cast(float, anchor.ms1_peak_rt_end)
    ):
        return None
    anchor_ion = _ion_in_window(anchor, ms1_centroid_mz, tolerance)
    other_ion = _ion_in_window(other, ms1_centroid_mz, tolerance)
    if (
        anchor_ion is None
        or other_ion is None
        or not _within_ppm(anchor_ion, other_ion, tolerance)
    ):
        return None
    return anchor


def _has_resolved_ms1_peak(candidate: DiscoveryCandidate) -> bool:
    return (
        candidate.ms1_peak_found
        and candidate.ms1_apex_rt is not None
        and candidate.ms1_height is not None
        and _candidate_peak_bounds_present(candidate)
    )


def _ion_in_window(
    candidate: DiscoveryCandidate,
    ms1_centroid_mz: MS1CentroidMz,
    tolerance_ppm: float,
) -> float | None:
    """The MS1 ion at the candidate's apex inside its own extraction window."""

    return ms1_centroid_mz(
        cast(float, candidate.ms1_apex_rt),
        candidate.precursor_mz * (1 - tolerance_ppm * 1e-6),
        candidate.precursor_mz * (1 + tolerance_ppm * 1e-6),
    )


def _representative_candidate(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
) -> DiscoveryCandidate:
    return min(
        (first, second),
        key=lambda candidate: _representative_rank(candidate, settings=settings),
    )


def _representative_rank(
    candidate: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
) -> tuple[int, float, float, float, int]:
    return (
        _tag_rank(candidate.neutral_loss_tag, settings=settings),
        -candidate.ms2_product_max_intensity,
        abs(candidate.neutral_loss_mass_error_ppm)
        if candidate.neutral_loss_mass_error_ppm is not None
        else float("inf"),
        candidate.best_seed_rt,
        candidate.best_ms2_scan_id,
    )


def _candidate_peak_bounds_present(candidate: DiscoveryCandidate) -> bool:
    return (
        candidate.ms1_peak_rt_start is not None
        and candidate.ms1_peak_rt_end is not None
        and candidate.ms1_peak_rt_start <= candidate.ms1_peak_rt_end
    )


def _combined_precursor_mz_basis(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> GroupPrecursorMzBasis:
    if first.precursor_mz_basis == second.precursor_mz_basis:
        return first.precursor_mz_basis
    return "mixed"


def _combined_neutral_loss_error_basis(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> NeutralLossErrorBasis:
    if first.neutral_loss_error_basis == second.neutral_loss_error_basis:
        return first.neutral_loss_error_basis
    return "mixed"


def _combined_max_scan_precursor_abs_delta_da(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> float | None:
    values = [
        value
        for value in (
            first.max_scan_precursor_abs_delta_da,
            second.max_scan_precursor_abs_delta_da,
        )
        if value is not None
    ]
    if not values:
        return None
    return max(values)


def _peak_intervals_overlap(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> bool:
    first_start = first.ms1_peak_rt_start
    first_end = first.ms1_peak_rt_end
    second_start = second.ms1_peak_rt_start
    second_end = second.ms1_peak_rt_end
    if (
        first_start is None
        or first_end is None
        or second_start is None
        or second_end is None
    ):
        return False
    return max(first_start, second_start) <= min(first_end, second_end)


def _seed_ranges_touch_shared_peak(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> bool:
    return _seed_range_touches_peak(first, second) and _seed_range_touches_peak(
        second, first
    )


def _seed_range_touches_peak(
    seed_candidate: DiscoveryCandidate,
    peak_candidate: DiscoveryCandidate,
) -> bool:
    peak_start = peak_candidate.ms1_peak_rt_start
    peak_end = peak_candidate.ms1_peak_rt_end
    if peak_start is None or peak_end is None:
        return False
    return (
        seed_candidate.rt_seed_min <= peak_end
        and seed_candidate.rt_seed_max >= peak_start
    )


def ordered_tag_names(
    values: Iterable[str],
    *,
    settings: DiscoverySettings,
) -> tuple[str, ...]:
    unique = {value for value in values if value}
    return tuple(
        sorted(unique, key=lambda tag: (_tag_rank(tag, settings=settings), tag))
    )


def _tag_rank(tag: str, *, settings: DiscoverySettings) -> int:
    try:
        return settings.selected_tag_names.index(tag)
    except ValueError:
        return len(settings.selected_tag_names)


def tag_intersection_status(
    matched_tag_names: tuple[str, ...],
    *,
    settings: DiscoverySettings,
) -> _TagIntersectionStatus:
    if settings.tag_combine_mode != "intersection":
        return "not_required"
    return (
        "complete"
        if set(settings.selected_tag_names).issubset(set(matched_tag_names))
        else "incomplete"
    )


def _merge_tag_evidence_json(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> str:
    payload: dict[str, Any] = {}
    for candidate in (first, second):
        try:
            parsed = json.loads(candidate.tag_evidence_json or "{}")
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            for tag_name, evidence in parsed.items():
                tag_key = str(tag_name)
                if not isinstance(evidence, dict):
                    payload.setdefault(tag_key, evidence)
                    continue
                evidence = _with_scan_receipts(
                    cast(dict[str, Any], evidence), candidate
                )
                if isinstance(payload.get(tag_key), dict):
                    payload[tag_key] = _merge_tag_evidence_entry(
                        cast(dict[str, Any], payload[tag_key]),
                        evidence,
                    )
                else:
                    payload[tag_key] = evidence
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _with_scan_receipts(
    evidence: dict[str, Any],
    candidate: DiscoveryCandidate,
) -> dict[str, Any]:
    """Keep the precursor and product each scan was strictly read against.

    A merged peak can absorb scans whose product lies beyond the product
    tolerance of the representative; strict replay checks each scan against
    the receipt of the candidate that originally claimed it.
    """

    result = dict(evidence)
    if "scan_receipts" not in result and "product_mz" in result:
        result["scan_receipts"] = {
            str(scan_id): {
                "precursor_mz": candidate.precursor_mz,
                "product_mz": result["product_mz"],
            }
            for scan_id in _int_values(result.get("scan_ids"))
        }
    return result


def _merge_tag_evidence_entry(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(first)
    for key, value in second.items():
        merged.setdefault(key, value)
    scan_receipts = {
        **cast(dict[str, Any], second.get("scan_receipts") or {}),
        **cast(dict[str, Any], first.get("scan_receipts") or {}),
    }
    if scan_receipts:
        merged["scan_receipts"] = scan_receipts
    if first.get("precursor_mz_basis") != second.get("precursor_mz_basis"):
        merged["precursor_mz_basis"] = "mixed"

    scan_ids = sorted(
        set(_int_values(first.get("scan_ids")))
        | set(_int_values(second.get("scan_ids")))
    )
    if scan_ids:
        merged["scan_ids"] = scan_ids

    scan_count = _optional_int(first.get("scan_count")) + _optional_int(
        second.get("scan_count")
    )
    if scan_count:
        merged["scan_count"] = scan_count
    elif scan_ids:
        merged["scan_count"] = len(scan_ids)

    _merge_float_min(merged, first, second, "rt_min")
    _merge_float_max(merged, first, second, "rt_max")
    _merge_max_intensity_fields(merged, first, second)
    return merged


def _merge_float_min(
    merged: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
    key: str,
) -> None:
    values = [
        value
        for value in (_optional_float(first.get(key)), _optional_float(second.get(key)))
        if value is not None
    ]
    if values:
        merged[key] = min(values)


def _merge_float_max(
    merged: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
    key: str,
) -> None:
    values = [
        value
        for value in (_optional_float(first.get(key)), _optional_float(second.get(key)))
        if value is not None
    ]
    if values:
        merged[key] = max(values)


def _merge_max_intensity_fields(
    merged: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
) -> None:
    first_intensity = _optional_float(first.get("max_intensity"))
    second_intensity = _optional_float(second.get("max_intensity"))
    if first_intensity is None and second_intensity is None:
        return
    winner = (
        second
        if first_intensity is None
        or (second_intensity is not None and second_intensity > first_intensity)
        else first
    )
    merged["max_intensity"] = _optional_float(winner.get("max_intensity"))
    for key in (
        "product_mz",
        "neutral_loss_error_ppm",
        "neutral_loss_error_da",
        "neutral_loss_window_da",
        "scan_precursor_mz",
        "precursor_metadata",
        "scan_precursor_delta_da",
    ):
        if key in winner:
            merged[key] = winner[key]


def _int_values(value: object) -> tuple[int, ...]:
    if not isinstance(value, list | tuple):
        return ()
    parsed: list[int] = []
    for item in value:
        integer = _optional_int(item)
        if integer:
            parsed.append(integer)
    return tuple(parsed)


def _optional_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


_within_ppm = within_ppm
