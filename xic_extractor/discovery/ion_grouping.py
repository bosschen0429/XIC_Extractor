"""CID-NL ion grouping: strict NL events grouped by their measured MS1 ion."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from xic_extractor.discovery.event_evidence import tag_rank
from xic_extractor.discovery.models import DiscoveryEvent, DiscoveryIon
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import within_ppm


def event_ion_mz(event: DiscoveryEvent) -> float:
    """The MS1-measured ion of one event, before any XIC is acquired.

    A recorded scan precursor is an MS1 reading. An inferred precursor uses its
    single parent-MS1 centroid; product + loss, read at MS2 resolution, is the
    fallback only when the parent scan reads no single ion.
    """
    observation = event.ms1_precursor_observation
    if (
        event.precursor_mz_basis == "product_plus_neutral_loss"
        and observation is not None
        and len(observation.centroid_mzs) == 1
    ):
        return observation.centroid_mzs[0]
    return event.precursor_mz


def _measured_by_ms1(event: DiscoveryEvent) -> bool:
    return event.precursor_mz_basis != "product_plus_neutral_loss" or (
        event_ion_mz(event) != event.precursor_mz
    )


def build_discovery_ions(
    events: Iterable[DiscoveryEvent],
    *,
    settings: DiscoverySettings,
) -> tuple[DiscoveryIon, ...]:
    """Group one sample's events by ion, independent of tag, product, RT and order.

    Ions are sorted by m/z. Each group opens at the lowest unassigned ion and
    takes every ion within the precursor tolerance above it, so any two members
    differ by at most that tolerance and no member bridges two ions. The
    XIC m/z is the representative member's ion: an MS1-measured ion first, then
    the configured tag order and product intensity.
    """
    tolerance = settings.precursor_mz_tolerance_ppm
    by_sample: dict[tuple[str, str], list[DiscoveryEvent]] = {}
    for event in dict.fromkeys(events):
        by_sample.setdefault((str(event.raw_file), event.sample_stem), []).append(event)
    ions: list[DiscoveryIon] = []
    for (_raw_text, sample_stem), sample_events in sorted(by_sample.items()):
        ordered = sorted(
            sample_events,
            key=lambda event: (event_ion_mz(event), *_event_order(event, settings)),
        )
        start = 0
        while start < len(ordered):
            anchor = event_ion_mz(ordered[start])
            stop = start + 1
            while stop < len(ordered) and within_ppm(
                event_ion_mz(ordered[stop]), anchor, tolerance
            ):
                stop += 1
            members = tuple(
                sorted(
                    ordered[start:stop],
                    key=lambda event: (event.rt, *_event_order(event, settings)),
                )
            )
            representative = min(
                members,
                key=lambda event: (
                    not _measured_by_ms1(event),
                    tag_rank(event.neutral_loss_tag, settings=settings),
                    -event.product_intensity,
                    _event_order(event, settings),
                ),
            )
            ions.append(
                DiscoveryIon(
                    raw_file=Path(members[0].raw_file),
                    sample_stem=sample_stem,
                    mz=event_ion_mz(representative),
                    events=members,
                )
            )
            start = stop
    return tuple(ions)


def _event_order(
    event: DiscoveryEvent, settings: DiscoverySettings
) -> tuple[int, str, float, int, float, float]:
    return (
        tag_rank(event.neutral_loss_tag, settings=settings),
        event.neutral_loss_tag,
        event.rt,
        event.scan_number,
        event.precursor_mz,
        event.product_mz,
    )
