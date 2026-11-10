"""Choices over the strict NL events locating one peak."""

from collections.abc import Iterable

from xic_extractor.discovery.models import (
    DiscoveryEvent,
    GroupPrecursorMzBasis,
)
from xic_extractor.discovery.settings import DiscoverySettings


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
