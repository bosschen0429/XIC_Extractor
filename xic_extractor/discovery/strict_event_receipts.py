"""Parent-MS1 facts and per-scan resolution for strict Discovery events."""

from __future__ import annotations

from dataclasses import replace

from xic_extractor.configuration.cid_nl_profile import PrecursorReferenceRequirement
from xic_extractor.discovery.models import DiscoveryEvent
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.precursor_evidence import (
    MS1MassWindowSource,
)


def read_parent_ms1_observations(
    raw: MS1MassWindowSource,
    events: tuple[DiscoveryEvent, ...],
    *,
    tolerance_ppm: float,
) -> tuple[DiscoveryEvent, ...]:
    """Attach fresh parent-MS1 facts without changing an event's search provenance."""
    windows: dict[int, tuple[int, float, float]] = {}
    for index, event in enumerate(events):
        metadata = event.precursor_metadata
        if (
            event.precursor_mz_basis != "product_plus_neutral_loss"
            or metadata is None
            or metadata.master_scan_number is None
        ):
            continue
        radius = event.precursor_mz * tolerance_ppm * 1e-6
        windows[index] = (
            metadata.master_scan_number,
            event.precursor_mz - radius,
            event.precursor_mz + radius,
        )
    if not windows:
        return events
    requests = tuple(dict.fromkeys(windows.values()))
    observations = dict(zip(requests, raw.read_ms1_mass_windows(requests), strict=True))
    return tuple(
        replace(event, ms1_precursor_observation=observations[windows[index]])
        if index in windows
        else event
        for index, event in enumerate(events)
    )


def resolve_discovery_scan_events(
    events: tuple[DiscoveryEvent, ...],
    *,
    settings: DiscoverySettings,
    preserve_configured_losses: bool = False,
    precursor_reference_requirement: PrecursorReferenceRequirement = "independent_mass",
) -> tuple[DiscoveryEvent, ...]:
    """Resolve one evidence event per scan/loss after all parent facts are read.

    The event table retains alternatives. The provider prefers independent NL
    compatibility at the requested reference level, then loss accuracy and
    intensity. An unresolved event remains available to the method owner
    without gaining strict support.
    The caller supplies one owner-bound acquisition batch.
    """
    tag_precedence = {
        tag: index for index, tag in enumerate(settings.selected_tag_names)
    }

    def rank(
        event: DiscoveryEvent,
    ) -> tuple[bool, float, float, int, float, float, str]:
        assessment = event.assess_precursor_loss(settings.nl_tolerance_ppm)
        error = assessment.observed_loss_error_ppm
        return (
            not assessment.supports_reference(precursor_reference_requirement),
            abs(error) if error is not None else float("inf"),
            -event.product_intensity,
            tag_precedence.get(event.neutral_loss_tag, len(tag_precedence)),
            event.precursor_mz,
            event.product_mz,
            event.precursor_mz_basis,
        )

    by_event: dict[tuple[int, float], DiscoveryEvent] = {}
    for event in sorted(events, key=rank):
        key = (
            event.scan_number,
            event.configured_neutral_loss_da if preserve_configured_losses else 0.0,
        )
        by_event.setdefault(key, event)
    return tuple(by_event[key] for key in sorted(by_event))


__all__ = [
    "resolve_discovery_scan_events",
    "read_parent_ms1_observations",
]
