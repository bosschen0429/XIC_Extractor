"""CID-NL ion channels: strict NL events grouped by their measured MS1 ion."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from xic_extractor.discovery.event_evidence import tag_rank
from xic_extractor.discovery.models import DiscoveryChannel, DiscoverySeed
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import within_ppm


def seed_ion_mz(seed: DiscoverySeed) -> float:
    """The MS1-measured ion of one event, before any XIC is acquired.

    A recorded scan precursor is an MS1 reading. An inferred precursor uses its
    single parent-MS1 centroid; product + loss, read at MS2 resolution, is the
    fallback only when the parent scan reads no single ion.
    """
    observation = seed.ms1_precursor_observation
    if (
        seed.precursor_mz_basis == "product_plus_neutral_loss"
        and observation is not None
        and len(observation.centroid_mzs) == 1
    ):
        return observation.centroid_mzs[0]
    return seed.precursor_mz


def _measured_by_ms1(seed: DiscoverySeed) -> bool:
    return seed.precursor_mz_basis != "product_plus_neutral_loss" or (
        seed_ion_mz(seed) != seed.precursor_mz
    )


def build_discovery_channels(
    seeds: Iterable[DiscoverySeed],
    *,
    settings: DiscoverySettings,
) -> tuple[DiscoveryChannel, ...]:
    """Group one sample's events by ion, independent of tag, product, RT and order.

    Ions are sorted by m/z. Each channel opens at the lowest unassigned ion and
    takes every ion within the precursor tolerance above it, so any two members
    differ by at most that tolerance and no member bridges two channels. The
    XIC m/z is the representative member's ion: an MS1-measured ion first, then
    the configured tag order and product intensity.
    """
    tolerance = settings.precursor_mz_tolerance_ppm
    by_sample: dict[tuple[str, str], list[DiscoverySeed]] = {}
    for seed in dict.fromkeys(seeds):
        by_sample.setdefault((str(seed.raw_file), seed.sample_stem), []).append(seed)
    channels: list[DiscoveryChannel] = []
    for (_raw_text, sample_stem), sample_seeds in sorted(by_sample.items()):
        ordered = sorted(
            sample_seeds,
            key=lambda seed: (seed_ion_mz(seed), *_event_order(seed, settings)),
        )
        start = 0
        while start < len(ordered):
            anchor = seed_ion_mz(ordered[start])
            stop = start + 1
            while stop < len(ordered) and within_ppm(
                seed_ion_mz(ordered[stop]), anchor, tolerance
            ):
                stop += 1
            members = tuple(
                sorted(
                    ordered[start:stop],
                    key=lambda seed: (seed.rt, *_event_order(seed, settings)),
                )
            )
            representative = min(
                members,
                key=lambda seed: (
                    not _measured_by_ms1(seed),
                    tag_rank(seed.neutral_loss_tag, settings=settings),
                    -seed.product_intensity,
                    _event_order(seed, settings),
                ),
            )
            channels.append(
                DiscoveryChannel(
                    raw_file=Path(members[0].raw_file),
                    sample_stem=sample_stem,
                    mz=seed_ion_mz(representative),
                    seeds=members,
                )
            )
            start = stop
    return tuple(channels)


def _event_order(
    seed: DiscoverySeed, settings: DiscoverySettings
) -> tuple[int, str, float, int, float, float]:
    return (
        tag_rank(seed.neutral_loss_tag, settings=settings),
        seed.neutral_loss_tag,
        seed.rt,
        seed.scan_number,
        seed.precursor_mz,
        seed.product_mz,
    )
