from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

import numpy as np

from xic_extractor.discovery.models import (
    DiscoverySeed,
    DiscoverySettings,
    NeutralLossProfile,
    PrecursorMzBasis,
)
from xic_extractor.mass_accuracy import neutral_loss_match
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
from xic_extractor.precursor_evidence import (
    precursor_inference_bounds,
    recorded_precursor_references,
)


class MS2ScanSource(Protocol):
    def iter_ms2_scans(self, rt_min: float, rt_max: float) -> Iterator[Ms2ScanEvent]:
        """Yield MS2 scan events within the requested retention-time window."""


def collect_strict_nl_seeds(
    raw: MS2ScanSource,
    *,
    raw_file: Path,
    settings: DiscoverySettings,
) -> tuple[DiscoverySeed, ...]:
    seeds: list[DiscoverySeed] = []
    for event in raw.iter_ms2_scans(settings.rt_min, settings.rt_max):
        if event.parse_error is not None or event.scan is None:
            continue

        seeds.extend(
            discovery_seeds_from_scan(
                event.scan,
                raw_file=raw_file,
                settings=settings,
            )
        )

    return tuple(seeds)


def discovery_seeds_from_scan(
    scan: Ms2Scan,
    *,
    raw_file: Path,
    settings: DiscoverySettings,
) -> tuple[DiscoverySeed, ...]:
    """Apply the canonical Discovery seed owner to one already-read MS2 scan."""
    seeds: list[DiscoverySeed] = []
    for profile in settings.neutral_loss_profiles:
        seeds.extend(
            _seeds_from_scan_profile(
                scan,
                raw_file=raw_file,
                settings=settings,
                profile=profile,
            )
        )
    return tuple(seeds)


def _seeds_from_scan_profile(
    scan: Ms2Scan,
    *,
    raw_file: Path,
    settings: DiscoverySettings,
    profile: NeutralLossProfile,
) -> tuple[DiscoverySeed, ...]:
    neutral_loss_da = profile.neutral_loss_da
    references = recorded_precursor_references(scan, settings.nl_tolerance_ppm)
    if not references or scan.base_peak <= 0.0 or neutral_loss_da <= 0.0:
        return ()

    masses = np.asarray(scan.masses, dtype=float)
    intensities = np.asarray(scan.intensities, dtype=float)
    if (
        masses.ndim != 1
        or intensities.ndim != 1
        or masses.size == 0
        or masses.size != intensities.size
    ):
        return ()

    intensity_floor = scan.base_peak * settings.nl_min_intensity_ratio
    finite_signal_mask = (
        (intensities >= intensity_floor)
        & np.isfinite(masses)
        & np.isfinite(intensities)
    )
    lower, upper = precursor_inference_bounds(
        scan.precursor_mz, scan.precursor_metadata, settings.ms2_precursor_tol_da
    )
    inferred_precursors = masses + neutral_loss_da
    inferred_mask = (
        (inferred_precursors >= lower)
        & (inferred_precursors <= upper)
        & (intensities >= intensity_floor)
        & np.isfinite(masses)
        & np.isfinite(intensities)
        & np.isfinite(inferred_precursors)
        & (inferred_precursors > 0.0)
    )

    candidates: list[DiscoverySeed] = []
    direct_indices: set[int] = set()
    for precursor_mz, basis in references:
        direct_mask = _direct_product_mask(
            masses,
            intensities,
            precursor_mz=precursor_mz,
            settings=settings,
            neutral_loss_da=neutral_loss_da,
            intensity_floor=intensity_floor,
        )
        direct_candidates: list[DiscoverySeed] = []
        for index in np.flatnonzero(direct_mask & finite_signal_mask):
            product_mz = float(masses[int(index)])
            observed_loss_da = precursor_mz - product_mz
            observed_loss_error_ppm = (
                abs(observed_loss_da - neutral_loss_da) / neutral_loss_da * 1_000_000.0
            )
            _, _, matches = neutral_loss_match(
                precursor_mz,
                product_mz,
                neutral_loss_da,
                settings.nl_tolerance_ppm,
                observed_loss_da=observed_loss_da,
            )
            if not matches:
                # The selected precursor's direct window owns its products. An
                # additional recorded reference only claims a product it
                # supports, so its NL miss stays available for inference.
                if basis == "scan_precursor":
                    direct_indices.add(int(index))
                continue

            direct_indices.add(int(index))
            direct_candidates.append(
                _build_seed(
                    raw_file=raw_file,
                    scan=scan,
                    profile=profile,
                    precursor_mz=precursor_mz,
                    product_mz=product_mz,
                    product_intensity=float(intensities[int(index)]),
                    neutral_loss_da=neutral_loss_da,
                    observed_loss_da=observed_loss_da,
                    observed_loss_error_ppm=observed_loss_error_ppm,
                    tolerance_ppm=settings.nl_tolerance_ppm,
                    precursor_mz_basis=basis,
                )
            )
        if direct_candidates:
            candidates.append(min(direct_candidates, key=_seed_candidate_rank))

    for index in np.flatnonzero(inferred_mask & finite_signal_mask):
        if int(index) in direct_indices:
            continue
        product_mz = float(masses[int(index)])
        precursor_mz = product_mz + neutral_loss_da
        candidates.append(
            _build_seed(
                raw_file=raw_file,
                scan=scan,
                profile=profile,
                precursor_mz=precursor_mz,
                product_mz=product_mz,
                product_intensity=float(intensities[int(index)]),
                neutral_loss_da=neutral_loss_da,
                observed_loss_da=neutral_loss_da,
                observed_loss_error_ppm=None,
                tolerance_ppm=settings.nl_tolerance_ppm,
                precursor_mz_basis="product_plus_neutral_loss",
            )
        )

    return tuple(
        sorted(
            _dedupe_seed_candidates(candidates),
            key=lambda seed: (
                seed.precursor_mz,
                seed.product_mz,
                seed.scan_number,
                seed.neutral_loss_tag,
            ),
        )
    )


def _direct_product_mask(
    masses: np.ndarray,
    intensities: np.ndarray,
    *,
    precursor_mz: float,
    settings: DiscoverySettings,
    neutral_loss_da: float,
    intensity_floor: float,
) -> np.ndarray:
    expected_product = precursor_mz - neutral_loss_da
    if expected_product <= 0.0:
        return np.zeros_like(masses, dtype=bool)
    effective_product_search_ppm = max(
        settings.product_search_ppm,
        2.0 * precursor_mz * settings.nl_tolerance_ppm / expected_product,
    )
    product_window_ppm = (
        np.abs(masses - expected_product) / expected_product * 1_000_000.0
    )
    return (
        (product_window_ppm <= effective_product_search_ppm)
        & (intensities >= intensity_floor)
        & np.isfinite(masses)
        & np.isfinite(intensities)
    )


def _build_seed(
    *,
    raw_file: Path,
    scan: Ms2Scan,
    profile: NeutralLossProfile,
    precursor_mz: float,
    product_mz: float,
    product_intensity: float,
    neutral_loss_da: float,
    observed_loss_da: float,
    observed_loss_error_ppm: float | None,
    tolerance_ppm: float,
    precursor_mz_basis: PrecursorMzBasis,
) -> DiscoverySeed:
    return DiscoverySeed(
        raw_file=raw_file,
        sample_stem=raw_file.stem,
        scan_number=scan.scan_number,
        rt=scan.rt,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        product_intensity=product_intensity,
        neutral_loss_tag=profile.tag,
        configured_neutral_loss_da=neutral_loss_da,
        observed_neutral_loss_da=observed_loss_da,
        observed_loss_error_ppm=observed_loss_error_ppm,
        matched_tag_names=(profile.tag,),
        tag_evidence_json=_tag_evidence_json(
            profile.tag,
            scan=scan,
            precursor_mz=precursor_mz,
            product_mz=product_mz,
            product_intensity=product_intensity,
            observed_loss_error_ppm=observed_loss_error_ppm,
            neutral_loss_da=neutral_loss_da,
            tolerance_ppm=tolerance_ppm,
            precursor_mz_basis=precursor_mz_basis,
        ),
        scan_precursor_mz=scan.precursor_mz,
        precursor_mz_basis=precursor_mz_basis,
        precursor_metadata=scan.precursor_metadata,
    )


def _dedupe_seed_candidates(
    candidates: list[DiscoverySeed],
) -> tuple[DiscoverySeed, ...]:
    best_by_key: dict[tuple[int, int, str], DiscoverySeed] = {}
    for candidate in candidates:
        key = (
            round(candidate.precursor_mz * 1_000_000),
            round(candidate.product_mz * 1_000_000),
            candidate.neutral_loss_tag,
        )
        existing = best_by_key.get(key)
        if existing is None or _seed_candidate_rank(candidate) < _seed_candidate_rank(
            existing
        ):
            best_by_key[key] = candidate
    return tuple(best_by_key.values())


def _seed_candidate_rank(seed: DiscoverySeed) -> tuple[float, float, int]:
    return (
        seed.observed_loss_error_ppm
        if seed.observed_loss_error_ppm is not None
        else float("inf"),
        -seed.product_intensity,
        seed.scan_number,
    )


def _tag_evidence_json(
    tag: str,
    *,
    scan: Ms2Scan,
    precursor_mz: float,
    product_mz: float,
    product_intensity: float,
    observed_loss_error_ppm: float | None,
    neutral_loss_da: float,
    tolerance_ppm: float,
    precursor_mz_basis: PrecursorMzBasis,
) -> str:
    scan_precursor_delta_da = scan.precursor_mz - precursor_mz
    loss_error_da, loss_window_da, _ = neutral_loss_match(
        precursor_mz, product_mz, neutral_loss_da, tolerance_ppm
    )
    payload = {
        tag: {
            "scan_count": 1,
            "scan_ids": [scan.scan_number],
            "rt_min": scan.rt,
            "rt_max": scan.rt,
            "precursor_mz_basis": precursor_mz_basis,
            "scan_precursor_mz": scan.precursor_mz,
            "precursor_metadata": asdict(scan.precursor_metadata)
            if scan.precursor_metadata is not None
            else None,
            "scan_precursor_delta_da": scan_precursor_delta_da,
            "product_mz": product_mz,
            "max_intensity": product_intensity,
            "neutral_loss_error_ppm": observed_loss_error_ppm,
            "neutral_loss_error_da": loss_error_da
            if observed_loss_error_ppm is not None
            else None,
            "neutral_loss_window_da": loss_window_da,
        }
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
