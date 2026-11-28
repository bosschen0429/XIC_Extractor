"""Choices over the strict NL events locating one peak."""

from collections.abc import Iterable

from xic_extractor.configuration.cid_nl_profile import PrecursorReferenceRequirement
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


def ranked_neutral_losses(settings: DiscoverySettings) -> dict[str, float]:
    """Each configured tag's loss, in the order matched tags are listed."""
    losses = settings.neutral_loss_by_tag
    return {tag: losses[tag] for tag in ordered_tag_names(losses, settings=settings)}


def select_representative_event(
    events: Iterable[DiscoveryEvent], *, settings: DiscoverySettings
) -> DiscoveryEvent:
    """Configured tag order first; abundance chooses within that tag."""
    return min(
        events,
        key=lambda event: (
            tag_rank(event.neutral_loss_tag, settings=settings),
            *representative_order_within_tag(
                product_intensity=event.product_intensity,
                observed_loss_error_ppm=event.observed_loss_error_ppm,
                rt=event.rt,
                scan_number=event.scan_number,
                neutral_loss_tag=event.neutral_loss_tag,
                product_mz=event.product_mz,
            ),
        ),
    )


def representative_order_within_tag(
    *,
    product_intensity: float,
    observed_loss_error_ppm: float | None,
    rt: float,
    scan_number: int,
    neutral_loss_tag: str,
    product_mz: float,
) -> tuple[float, float, float, int, str, float]:
    """The representative event's order among events of one tag."""
    return (
        -product_intensity,
        abs(observed_loss_error_ppm)
        if observed_loss_error_ppm is not None
        else float("inf"),
        rt,
        scan_number,
        neutral_loss_tag,
        product_mz,
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
