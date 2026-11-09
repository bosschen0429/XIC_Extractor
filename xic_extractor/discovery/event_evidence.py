"""Evidence summaries over the strict NL events attached to one peak."""

import json
import math
from collections.abc import Iterable
from dataclasses import asdict
from typing import Literal

from xic_extractor.discovery.models import (
    DiscoverySeed,
    GroupPrecursorMzBasis,
    NeutralLossErrorBasis,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import neutral_loss_match
from xic_extractor.precursor_evidence import precursor_isolation_membership

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
    seeds: Iterable[DiscoverySeed], *, settings: DiscoverySettings
) -> DiscoverySeed:
    """Configured tag order first; abundance chooses within that tag."""
    return min(
        seeds,
        key=lambda seed: (
            tag_rank(seed.neutral_loss_tag, settings=settings),
            -seed.product_intensity,
            abs(seed.observed_loss_error_ppm)
            if seed.observed_loss_error_ppm is not None
            else float("inf"),
            seed.rt,
            seed.scan_number,
            seed.neutral_loss_tag,
            seed.product_mz,
        ),
    )


def select_representative_seed(seeds: Iterable[DiscoverySeed]) -> DiscoverySeed:
    """Select by intensity, then measured loss error, RT and scan number."""
    return min(
        seeds,
        key=lambda seed: (
            -seed.product_intensity,
            abs(seed.observed_loss_error_ppm)
            if seed.observed_loss_error_ppm is not None
            else float("inf"),
            seed.rt,
            seed.scan_number,
        ),
    )


def merge_seed_evidence(
    seeds: list[DiscoverySeed], *, settings: DiscoverySettings
) -> str:
    """Per-tag scan receipts that strict replay checks each scan against."""
    by_tag: dict[str, dict[str, object]] = {}
    seeds_by_tag: dict[str, list[DiscoverySeed]] = {}
    for seed in seeds:
        seeds_by_tag.setdefault(seed.neutral_loss_tag, []).append(seed)
    for tag, tag_seeds in seeds_by_tag.items():
        best_seed = select_representative_seed(tag_seeds)
        receipts: dict[str, object] = {}
        entry: dict[str, object] = {
            "scan_count": len(tag_seeds),
            "scan_ids": sorted({seed.scan_number for seed in tag_seeds}),
            "scan_receipts": receipts,
            "rt_min": min(seed.rt for seed in tag_seeds),
            "rt_max": max(seed.rt for seed in tag_seeds),
            "precursor_mz_basis": events_precursor_mz_basis(tag_seeds),
            "scan_precursor_mz": best_seed.scan_precursor_mz,
            "precursor_metadata": asdict(best_seed.precursor_metadata)
            if best_seed.precursor_metadata is not None
            else None,
            "scan_precursor_delta_da": scan_precursor_delta_da(best_seed),
            "max_scan_precursor_abs_delta_da": max(
                abs(scan_precursor_delta_da(seed) or 0.0) for seed in tag_seeds
            ),
            "product_mz": best_seed.product_mz,
            "max_intensity": best_seed.product_intensity,
            "neutral_loss_error_ppm": best_seed.observed_loss_error_ppm,
        }
        for index, seed in enumerate(tag_seeds):
            receipt: dict[str, object] = {
                "precursor_mz": seed.precursor_mz,
                "product_mz": seed.product_mz,
                "scan_rt": seed.rt,
                "precursor_mz_basis": seed.precursor_mz_basis,
                "scan_precursor_mz": seed.scan_precursor_mz,
                "neutral_loss_error_ppm": seed.observed_loss_error_ppm,
                "precursor_isolation_membership": precursor_isolation_membership(
                    seed.precursor_mz, seed.scan_precursor_mz, seed.precursor_metadata
                ),
                "supports_direct_nl": seed.supports_direct_nl,
            }
            if seed.precursor_mz_basis == "product_plus_neutral_loss":
                receipt["ms1_precursor_observation"] = (
                    asdict(seed.ms1_precursor_observation)
                    if seed.ms1_precursor_observation is not None
                    else None
                )
                receipt["precursor_verification"] = asdict(
                    seed.assess_precursor_loss(settings.nl_tolerance_ppm)
                )
            receipts[str(seed.scan_number)] = receipt
            if (
                (index == 0 or seed is best_seed)
                and math.isfinite(seed.precursor_mz)
                and seed.precursor_mz > 0
                and math.isfinite(seed.product_mz)
                and seed.product_mz > 0
            ):
                loss_error_da, loss_window_da, _ = neutral_loss_match(
                    seed.precursor_mz,
                    seed.product_mz,
                    seed.configured_neutral_loss_da,
                    settings.nl_tolerance_ppm,
                    observed_loss_da=seed.observed_neutral_loss_da,
                )
                entry["neutral_loss_error_da"] = (
                    loss_error_da if seed.observed_loss_error_ppm is not None else None
                )
                entry["neutral_loss_window_da"] = loss_window_da
        by_tag[tag] = entry
    return json.dumps(by_tag, sort_keys=True, separators=(",", ":"))


def scan_precursor_delta_da(seed: DiscoverySeed) -> float | None:
    if seed.scan_precursor_mz is None:
        return None
    return seed.scan_precursor_mz - seed.precursor_mz


def events_precursor_mz_basis(
    seeds: list[DiscoverySeed],
) -> GroupPrecursorMzBasis:
    values = {seed.precursor_mz_basis for seed in seeds}
    if values == {"scan_precursor"}:
        return "scan_precursor"
    if values == {"scan_monoisotopic"}:
        return "scan_monoisotopic"
    if values == {"product_plus_neutral_loss"}:
        return "product_plus_neutral_loss"
    return "mixed"


def events_neutral_loss_error_basis(
    seeds: list[DiscoverySeed],
) -> NeutralLossErrorBasis:
    values = {_neutral_loss_error_basis(seed) for seed in seeds}
    if values == {"measured_scan_precursor_product"}:
        return "measured_scan_precursor_product"
    if values == {"recorded_monoisotopic_precursor_product"}:
        return "recorded_monoisotopic_precursor_product"
    if values == {"configured_loss_inferred_precursor"}:
        return "configured_loss_inferred_precursor"
    return "mixed"


def _neutral_loss_error_basis(seed: DiscoverySeed) -> NeutralLossErrorBasis:
    if seed.precursor_mz_basis == "scan_monoisotopic":
        return "recorded_monoisotopic_precursor_product"
    if seed.precursor_mz_basis == "product_plus_neutral_loss":
        return "configured_loss_inferred_precursor"
    return "measured_scan_precursor_product"


def max_scan_precursor_abs_delta_da(seeds: list[DiscoverySeed]) -> float | None:
    values = [
        abs(delta)
        for seed in seeds
        if (delta := scan_precursor_delta_da(seed)) is not None
    ]
    if not values:
        return None
    return max(values)
