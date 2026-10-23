import json
import math
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

from xic_extractor.discovery.models import (
    DiscoverySeed,
    DiscoverySeedGroup,
    DiscoverySettings,
    GroupPrecursorMzBasis,
    NeutralLossErrorBasis,
)
from xic_extractor.mass_accuracy import neutral_loss_match, within_ppm
from xic_extractor.precursor_evidence import precursor_isolation_membership


def group_discovery_seeds(
    seeds: Iterable[DiscoverySeed],
    *,
    settings: DiscoverySettings,
) -> tuple[DiscoverySeedGroup, ...]:
    sorted_seeds = sorted(
        dict.fromkeys(seeds),
        key=lambda seed: _seed_sort_key(seed, settings=settings),
    )
    if not sorted_seeds:
        return ()

    active_groups: list[list[DiscoverySeed]] = []

    for seed in sorted_seeds:
        matching_group = _matching_active_group(seed, active_groups, settings=settings)
        if matching_group is None:
            active_groups.append([seed])
        else:
            matching_group.append(seed)

    return tuple(
        build_discovery_seed_group(group, settings=settings) for group in active_groups
    )


def _matching_active_group(
    seed: DiscoverySeed,
    active_groups: list[list[DiscoverySeed]],
    *,
    settings: DiscoverySettings,
) -> list[DiscoverySeed] | None:
    for group in active_groups:
        if _can_join_group(seed, group, settings=settings):
            return group
    return None


def _can_join_group(
    seed: DiscoverySeed,
    current: list[DiscoverySeed],
    *,
    settings: DiscoverySettings,
) -> bool:
    first = current[0]
    previous = current[-1]
    return (
        seed.raw_file == first.raw_file
        and seed.sample_stem == first.sample_stem
        and seed.neutral_loss_tag == first.neutral_loss_tag
        and seed.rt - previous.rt <= settings.seed_rt_gap_min
        and all(
            _within_ppm(
                seed.precursor_mz,
                existing.precursor_mz,
                settings.precursor_mz_tolerance_ppm,
            )
            and _within_ppm(
                seed.product_mz,
                existing.product_mz,
                settings.product_mz_tolerance_ppm,
            )
            and _within_ppm(
                seed.observed_neutral_loss_da,
                existing.observed_neutral_loss_da,
                settings.nl_tolerance_ppm,
            )
            for existing in current
        )
    )


def build_discovery_seed_group(
    seeds: list[DiscoverySeed], *, settings: DiscoverySettings
) -> DiscoverySeedGroup:
    """Bind representative mass and complete evidence to one retained seed set."""
    best_seed = select_representative_seed(seeds)
    scan_precursor_delta_da = _scan_precursor_delta_da(best_seed)
    return DiscoverySeedGroup(
        raw_file=best_seed.raw_file,
        sample_stem=best_seed.sample_stem,
        seeds=tuple(sorted(seeds, key=_seed_sort_key)),
        neutral_loss_tag=best_seed.neutral_loss_tag,
        configured_neutral_loss_da=best_seed.configured_neutral_loss_da,
        precursor_mz=best_seed.precursor_mz,
        product_mz=best_seed.product_mz,
        observed_neutral_loss_da=best_seed.observed_neutral_loss_da,
        neutral_loss_mass_error_ppm=best_seed.observed_loss_error_ppm,
        rt_seed_min=min(seed.rt for seed in seeds),
        rt_seed_max=max(seed.rt for seed in seeds),
        matched_tag_names=(best_seed.neutral_loss_tag,),
        tag_evidence_json=merge_seed_evidence(seeds, settings=settings),
        precursor_mz_basis=_group_precursor_mz_basis(seeds),
        neutral_loss_error_basis=_group_neutral_loss_error_basis(seeds),
        scan_precursor_mz=best_seed.scan_precursor_mz,
        scan_precursor_delta_da=scan_precursor_delta_da,
        max_scan_precursor_abs_delta_da=_max_scan_precursor_abs_delta_da(seeds),
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


_within_ppm = within_ppm


def merge_seed_evidence(
    seeds: list[DiscoverySeed], *, settings: DiscoverySettings
) -> str:
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
            "precursor_mz_basis": _group_precursor_mz_basis(tag_seeds),
            "scan_precursor_mz": best_seed.scan_precursor_mz,
            "precursor_metadata": asdict(best_seed.precursor_metadata)
            if best_seed.precursor_metadata is not None
            else None,
            "scan_precursor_delta_da": _scan_precursor_delta_da(best_seed),
            "max_scan_precursor_abs_delta_da": max(
                abs(_scan_precursor_delta_da(seed) or 0.0) for seed in tag_seeds
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


def _scan_precursor_delta_da(seed: DiscoverySeed) -> float | None:
    if seed.scan_precursor_mz is None:
        return None
    return seed.scan_precursor_mz - seed.precursor_mz


def _group_precursor_mz_basis(
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


def _group_neutral_loss_error_basis(
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


def _max_scan_precursor_abs_delta_da(seeds: list[DiscoverySeed]) -> float | None:
    values = [
        abs(delta)
        for seed in seeds
        if (delta := _scan_precursor_delta_da(seed)) is not None
    ]
    if not values:
        return None
    return max(values)


def _seed_sort_key(
    seed: DiscoverySeed,
    settings: DiscoverySettings | None = None,
) -> tuple[str, str, int, str, float, int, float, float]:
    return (
        _path_sort_key(seed.raw_file),
        seed.sample_stem,
        _tag_rank(seed.neutral_loss_tag, settings=settings),
        seed.neutral_loss_tag,
        seed.rt,
        seed.scan_number,
        seed.precursor_mz,
        seed.product_mz,
    )


def _tag_rank(tag: str, *, settings: DiscoverySettings | None) -> int:
    if settings is None:
        return 0
    try:
        return settings.selected_tag_names.index(tag)
    except ValueError:
        return len(settings.selected_tag_names)


def _path_sort_key(path: Path) -> str:
    return str(path)
