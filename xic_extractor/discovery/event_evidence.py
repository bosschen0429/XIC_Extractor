"""Evidence summaries over the strict NL events attached to one peak."""

import json
import math
from collections.abc import Iterable
from dataclasses import asdict
from typing import Literal

from xic_extractor.discovery.models import (
    DiscoveryEvent,
    GroupPrecursorMzBasis,
    NeutralLossErrorBasis,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import neutral_loss_match

TagIntersectionStatus = Literal["not_required", "complete", "incomplete"]


def tag_rank(tag: str, *, settings: DiscoverySettings) -> int:
    try:
        return settings.selected_tag_names.index(tag)
    except ValueError:
        return len(settings.selected_tag_names)


def ordered_tag_names(
    values: Iterable[str],
    *,
    settings: DiscoverySettings,
) -> tuple[str, ...]:
    unique = {value for value in values if value}
    return tuple(
        sorted(unique, key=lambda tag: (tag_rank(tag, settings=settings), tag))
    )


def tag_intersection_status(
    matched_tag_names: tuple[str, ...],
    *,
    settings: DiscoverySettings,
) -> TagIntersectionStatus:
    if settings.tag_combine_mode != "intersection":
        return "not_required"
    return (
        "complete"
        if set(settings.selected_tag_names).issubset(set(matched_tag_names))
        else "incomplete"
    )


def select_representative_event(
    events: Iterable[DiscoveryEvent], *, settings: DiscoverySettings
) -> DiscoveryEvent:
    """Configured tag order first; abundance chooses within that tag."""
    return min(
        events,
        key=lambda event: (
            tag_rank(event.neutral_loss_tag, settings=settings),
            -event.product_intensity,
            abs(event.observed_loss_error_ppm)
            if event.observed_loss_error_ppm is not None
            else float("inf"),
            event.rt,
            event.scan_number,
            event.neutral_loss_tag,
            event.product_mz,
        ),
    )


def _strongest_event(events: Iterable[DiscoveryEvent]) -> DiscoveryEvent:
    """Select by intensity, then measured loss error, RT and scan number."""
    return min(
        events,
        key=lambda event: (
            -event.product_intensity,
            abs(event.observed_loss_error_ppm)
            if event.observed_loss_error_ppm is not None
            else float("inf"),
            event.rt,
            event.scan_number,
        ),
    )


def merge_event_evidence(
    events: list[DiscoveryEvent], *, settings: DiscoverySettings
) -> str:
    """Per-tag summary of the events locating one peak."""
    by_tag: dict[str, dict[str, object]] = {}
    events_by_tag: dict[str, list[DiscoveryEvent]] = {}
    for event in events:
        events_by_tag.setdefault(event.neutral_loss_tag, []).append(event)
    for tag, tag_events in events_by_tag.items():
        best_event = _strongest_event(tag_events)
        entry: dict[str, object] = {
            "scan_count": len(tag_events),
            "scan_ids": sorted({event.scan_number for event in tag_events}),
            "rt_min": min(event.rt for event in tag_events),
            "rt_max": max(event.rt for event in tag_events),
            "precursor_mz_basis": events_precursor_mz_basis(tag_events),
            "scan_precursor_mz": best_event.scan_precursor_mz,
            "precursor_metadata": asdict(best_event.precursor_metadata)
            if best_event.precursor_metadata is not None
            else None,
            "scan_precursor_delta_da": scan_precursor_delta_da(best_event),
            "max_scan_precursor_abs_delta_da": max(
                abs(scan_precursor_delta_da(event) or 0.0) for event in tag_events
            ),
            "product_mz": best_event.product_mz,
            "max_intensity": best_event.product_intensity,
            "neutral_loss_error_ppm": best_event.observed_loss_error_ppm,
        }
        for index, event in enumerate(tag_events):
            if (
                (index == 0 or event is best_event)
                and math.isfinite(event.precursor_mz)
                and event.precursor_mz > 0
                and math.isfinite(event.product_mz)
                and event.product_mz > 0
            ):
                loss_error_da, loss_window_da, _ = neutral_loss_match(
                    event.precursor_mz,
                    event.product_mz,
                    event.configured_neutral_loss_da,
                    settings.nl_tolerance_ppm,
                    observed_loss_da=event.observed_neutral_loss_da,
                )
                entry["neutral_loss_error_da"] = (
                    loss_error_da if event.observed_loss_error_ppm is not None else None
                )
                entry["neutral_loss_window_da"] = loss_window_da
        by_tag[tag] = entry
    return json.dumps(by_tag, sort_keys=True, separators=(",", ":"))


def scan_precursor_delta_da(event: DiscoveryEvent) -> float | None:
    if event.scan_precursor_mz is None:
        return None
    return event.scan_precursor_mz - event.precursor_mz


def events_precursor_mz_basis(
    events: list[DiscoveryEvent],
) -> GroupPrecursorMzBasis:
    values = {event.precursor_mz_basis for event in events}
    if values == {"scan_precursor"}:
        return "scan_precursor"
    if values == {"scan_monoisotopic"}:
        return "scan_monoisotopic"
    if values == {"product_plus_neutral_loss"}:
        return "product_plus_neutral_loss"
    return "mixed"


def events_neutral_loss_error_basis(
    events: list[DiscoveryEvent],
) -> NeutralLossErrorBasis:
    values = {_neutral_loss_error_basis(event) for event in events}
    if values == {"measured_scan_precursor_product"}:
        return "measured_scan_precursor_product"
    if values == {"recorded_monoisotopic_precursor_product"}:
        return "recorded_monoisotopic_precursor_product"
    if values == {"configured_loss_inferred_precursor"}:
        return "configured_loss_inferred_precursor"
    return "mixed"


def _neutral_loss_error_basis(event: DiscoveryEvent) -> NeutralLossErrorBasis:
    if event.precursor_mz_basis == "scan_monoisotopic":
        return "recorded_monoisotopic_precursor_product"
    if event.precursor_mz_basis == "product_plus_neutral_loss":
        return "configured_loss_inferred_precursor"
    return "measured_scan_precursor_product"


def max_scan_precursor_abs_delta_da(events: list[DiscoveryEvent]) -> float | None:
    values = [
        abs(delta)
        for event in events
        if (delta := scan_precursor_delta_da(event)) is not None
    ]
    if not values:
        return None
    return max(values)
