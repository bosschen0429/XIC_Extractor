"""Row order of one sample's Discovery peaks."""

from dataclasses import replace

from xic_extractor.discovery.models import representative_event_rt
from xic_extractor.discovery.physical_fact_record import DiscoveryPeakPhysicalBinding
from xic_extractor.discovery.settings import DiscoverySettings

_RT_DELTA_EPSILON_MIN = 1e-9


def order_discovery_peak_bindings(
    bindings: tuple[DiscoveryPeakPhysicalBinding, ...],
    *,
    settings: DiscoverySettings,
) -> tuple[DiscoveryPeakPhysicalBinding, ...]:
    """Order peaks for the peak table and physical sidecar, numbering each one.

    Peaks a reviewer should see first lead: two or more attached events at the
    event RT on an unshouldered peak, then the rest, then blank background;
    stronger evidence first within each.
    """

    ordered = sorted(
        bindings, key=lambda binding: _output_sort_key(binding, settings=settings)
    )
    return tuple(
        replace(binding, final_ordinal=final_ordinal)
        for final_ordinal, binding in enumerate(ordered)
    )


def _output_sort_key(
    binding: DiscoveryPeakPhysicalBinding, *, settings: DiscoverySettings
) -> tuple[object, ...]:
    peak = binding.peak
    best_event_rt = representative_event_rt(peak)
    return (
        _review_rank(binding, best_event_rt=best_event_rt, settings=settings),
        -peak.evidence_score,
        -peak.attached_event_count,
        -max(event.product_intensity for event in peak.located_events),
        -peak.ms1_area,
        best_event_rt,
        peak.peak_id,
    )


def _review_rank(
    binding: DiscoveryPeakPhysicalBinding,
    *,
    best_event_rt: float,
    settings: DiscoverySettings,
) -> int:
    peak = binding.peak
    if peak.status == "blank":
        return 2
    evaluation = binding.physical_evaluation
    shouldered = (
        evaluation.state not in {"unavailable", "failed"}
        and evaluation.requires_overlap_review
    )
    if (
        peak.attached_event_count >= 2
        and abs(peak.ms1_apex_rt - best_event_rt)
        <= settings.ms1_search_padding_min + _RT_DELTA_EPSILON_MIN
        and not shouldered
    ):
        return 0
    return 1
