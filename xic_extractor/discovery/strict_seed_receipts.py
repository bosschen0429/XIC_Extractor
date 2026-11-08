"""Reconstruct declared Discovery scan receipts from one RAW handle."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Protocol

import numpy as np

from xic_extractor.configuration.cid_nl_profile import PrecursorReferenceRequirement
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySeed
from xic_extractor.discovery.ms2_seeds import discovery_seeds_from_scan
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
from xic_extractor.precursor_evidence import (
    RECORDED_PRECURSOR_BASES,
    MS1MassWindowSource,
)

_WRITER_ROUNDING_ABS_TOL = 5e-6
_WRITER_ROUNDING_REL_TOL = 5e-6


class StrictSeedReceiptError(ValueError):
    """Raised when current Discovery scan receipts cannot be reconstructed."""


class StrictSeedReceiptRaw(Protocol):
    def iter_ms2_scan_subset(
        self, rt_min: float, rt_max: float, scan_numbers: frozenset[int]
    ) -> Iterator[Ms2ScanEvent]:
        """Declared scans within the native RT-to-scan window."""


def read_parent_ms1_observations(
    raw: MS1MassWindowSource,
    seeds: tuple[DiscoverySeed, ...],
    *,
    tolerance_ppm: float,
) -> tuple[DiscoverySeed, ...]:
    """Attach fresh parent-MS1 facts without changing a seed's search provenance."""
    windows: dict[int, tuple[int, float, float]] = {}
    for index, seed in enumerate(seeds):
        metadata = seed.precursor_metadata
        if (
            seed.precursor_mz_basis != "product_plus_neutral_loss"
            or metadata is None
            or metadata.master_scan_number is None
        ):
            continue
        radius = seed.precursor_mz * tolerance_ppm * 1e-6
        windows[index] = (
            metadata.master_scan_number,
            seed.precursor_mz - radius,
            seed.precursor_mz + radius,
        )
    if not windows:
        return seeds
    requests = tuple(dict.fromkeys(windows.values()))
    observations = dict(zip(requests, raw.read_ms1_mass_windows(requests), strict=True))
    return tuple(
        replace(seed, ms1_precursor_observation=observations[windows[index]])
        if index in windows
        else seed
        for index, seed in enumerate(seeds)
    )


def read_complete_discovery_scan_seeds(
    raw: StrictSeedReceiptRaw,
    *,
    candidates: tuple[DiscoveryCandidate, ...],
    settings: DiscoverySettings,
    preserve_configured_losses: bool = False,
    include_inferred: bool = False,
) -> tuple[tuple[Ms2Scan, DiscoverySeed], ...]:
    """Return declared seeds, retaining their direct or inferred precursor basis."""

    by_scan: dict[tuple[int, float, float, float], tuple[Ms2Scan, DiscoverySeed]] = {}
    scan_seed_memo: _ScanSeedMemo = {}
    for candidate in candidates:
        for scan in _read_exact_scans(raw, candidate):
            seeds = _read_exact_seed(
                scan,
                raw_file=candidate.raw_file,
                candidate=candidate,
                settings=settings,
                preserve_configured_losses=preserve_configured_losses,
                include_inferred=include_inferred,
                scan_seed_memo=scan_seed_memo,
            )
            for seed in seeds:
                key = (
                    scan.scan_number,
                    seed.configured_neutral_loss_da
                    if preserve_configured_losses
                    else 0.0,
                    seed.precursor_mz,
                    seed.product_mz,
                )
                existing = by_scan.get(key)
                if existing is not None and existing[1] != seed:
                    raise StrictSeedReceiptError(
                        f"scan {scan.scan_number}: candidate receipts disagree on "
                        "canonical strict evidence"
                    )
                by_scan[key] = (scan, seed)
    return tuple(by_scan[scan_number] for scan_number in sorted(by_scan))


def resolve_discovery_scan_events(
    seeds: tuple[DiscoverySeed, ...],
    *,
    settings: DiscoverySettings,
    preserve_configured_losses: bool = False,
    precursor_reference_requirement: PrecursorReferenceRequirement = "independent_mass",
) -> tuple[DiscoverySeed, ...]:
    """Resolve one evidence event per scan/loss after all parent facts are read.

    Receipt replay retains alternatives. The provider prefers independent NL
    compatibility at the requested reference level, then loss accuracy and
    intensity. An unresolved event remains available to the method owner
    without gaining strict support.
    The caller supplies one owner-bound acquisition batch.
    """
    tag_precedence = {
        tag: index for index, tag in enumerate(settings.selected_tag_names)
    }

    def rank(seed: DiscoverySeed) -> tuple[bool, float, float, int, float, float, str]:
        assessment = seed.assess_precursor_loss(settings.nl_tolerance_ppm)
        error = assessment.observed_loss_error_ppm
        return (
            not assessment.supports_reference(precursor_reference_requirement),
            abs(error) if error is not None else float("inf"),
            -seed.product_intensity,
            tag_precedence.get(seed.neutral_loss_tag, len(tag_precedence)),
            seed.precursor_mz,
            seed.product_mz,
            seed.precursor_mz_basis,
        )

    by_event: dict[tuple[int, float], DiscoverySeed] = {}
    for seed in sorted(seeds, key=rank):
        key = (
            seed.scan_number,
            seed.configured_neutral_loss_da if preserve_configured_losses else 0.0,
        )
        by_event.setdefault(key, seed)
    return tuple(by_event[key] for key in sorted(by_event))


def _read_exact_scans(
    raw: StrictSeedReceiptRaw,
    candidate: DiscoveryCandidate,
) -> tuple[Ms2Scan, ...]:
    declared = tuple(sorted(set(candidate.seed_scan_ids)))
    if not declared:
        raise StrictSeedReceiptError(
            f"{candidate.candidate_id}: candidate declares no strict MS2 scans"
        )
    matches_by_scan: dict[int, list[Ms2Scan]] = {
        scan_number: [] for scan_number in declared
    }
    rt_min = candidate.rt_seed_min - _WRITER_ROUNDING_ABS_TOL
    rt_max = candidate.rt_seed_max + _WRITER_ROUNDING_ABS_TOL
    for event in raw.iter_ms2_scan_subset(rt_min, rt_max, frozenset(declared)):
        if (
            event.parse_error is None
            and event.scan is not None
            and event.scan.scan_number in matches_by_scan
        ):
            matches_by_scan[event.scan.scan_number].append(event.scan)
    if any(len(matches_by_scan[scan_number]) != 1 for scan_number in declared):
        raise StrictSeedReceiptError(
            f"{candidate.candidate_id}: declared strict MS2 scans are not exact"
        )
    return tuple(matches_by_scan[scan_number][0] for scan_number in declared)


_ScanSeedMemo = dict[tuple[Path, int], tuple[Ms2Scan, tuple[DiscoverySeed, ...]]]


def _scan_seeds(
    scan: Ms2Scan,
    *,
    raw_file: Path,
    settings: DiscoverySettings,
    memo: _ScanSeedMemo | None,
) -> tuple[DiscoverySeed, ...]:
    """Enumerate one scan's seeds once per exact scan, not once per candidate.

    Seed enumeration depends only on the scan numerics, its RAW file, and the
    settings, so candidates that declare the same scan reuse one enumeration.
    A memo entry is reused only when the scan it was built from is numerically
    identical; anything else recomputes. Candidate-specific receipt matching
    stays in the caller.
    """

    if memo is None:
        return discovery_seeds_from_scan(scan, raw_file=raw_file, settings=settings)
    key = (raw_file, scan.scan_number)
    cached = memo.get(key)
    if cached is not None and _same_scan_numerics(cached[0], scan):
        return cached[1]
    seeds = discovery_seeds_from_scan(scan, raw_file=raw_file, settings=settings)
    memo[key] = (scan, seeds)
    return seeds


def _same_scan_numerics(left: Ms2Scan, right: Ms2Scan) -> bool:
    return (
        left.scan_number == right.scan_number
        and left.rt == right.rt
        and left.precursor_mz == right.precursor_mz
        and left.base_peak == right.base_peak
        and left.precursor_metadata == right.precursor_metadata
        and np.array_equal(left.masses, right.masses)
        and np.array_equal(left.intensities, right.intensities)
    )


def _read_exact_seed(
    scan: Ms2Scan,
    *,
    raw_file: Path,
    candidate: DiscoveryCandidate,
    settings: DiscoverySettings,
    preserve_configured_losses: bool = False,
    include_inferred: bool = False,
    scan_seed_memo: _ScanSeedMemo | None = None,
) -> tuple[DiscoverySeed, ...]:
    representative = scan.scan_number == candidate.best_ms2_scan_id
    tag_product_receipts, precursor_receipt = _scan_receipts(
        candidate,
        scan_number=scan.scan_number,
    )
    scan_seeds = _scan_seeds(
        scan,
        raw_file=raw_file,
        settings=settings,
        memo=scan_seed_memo,
    )
    matches = tuple(
        seed
        for seed in scan_seeds
        if seed.scan_number in candidate.seed_scan_ids
        and (
            seed.precursor_mz_basis in RECORDED_PRECURSOR_BASES
            or candidate.precursor_mz_basis == "mixed"
            or include_inferred
        )
        and any(
            seed.neutral_loss_tag == tag_name
            and (
                (
                    not representative
                    and seed.precursor_mz_basis == "product_plus_neutral_loss"
                )
                or _within_ppm(
                    seed.product_mz,
                    product_mz,
                    settings.product_mz_tolerance_ppm,
                )
                or (
                    # A shared-apex merge can retain another product's receipt
                    # under the same scan. The canonical row still owns its
                    # representative; reconstruct it from native scan seeds.
                    representative
                    and tag_name == candidate.neutral_loss_tag
                    and _receipt_close(seed.precursor_mz, candidate.precursor_mz)
                    and _receipt_close(seed.product_mz, candidate.product_mz)
                )
            )
            for tag_name, product_mz in tag_product_receipts.items()
        )
        and _within_ppm(
            seed.precursor_mz,
            candidate.precursor_mz,
            settings.precursor_mz_tolerance_ppm,
        )
        and (
            preserve_configured_losses
            or not representative
            or (
                _receipt_close(seed.precursor_mz, candidate.precursor_mz)
                and _receipt_close(seed.product_mz, candidate.product_mz)
            )
        )
    )
    if not matches:
        if (
            include_inferred
            and not representative
            and any(
                seed.precursor_mz_basis == "product_plus_neutral_loss"
                and seed.neutral_loss_tag in tag_product_receipts
                for seed in scan_seeds
            )
        ):
            # MS1 merging compares group representatives, so an older inferred
            # member can fall outside the final precursor window. Its declared
            # scan still reconstructs, but it cannot support this mass question.
            return ()
        if (
            not representative
            and precursor_receipt is not None
            and any(
                seed.neutral_loss_tag in tag_product_receipts
                and _within_ppm(
                    seed.product_mz,
                    tag_product_receipts[seed.neutral_loss_tag],
                    settings.product_mz_tolerance_ppm,
                )
                and _within_ppm(
                    seed.precursor_mz,
                    precursor_receipt,
                    settings.precursor_mz_tolerance_ppm,
                )
                for seed in scan_seeds
            )
        ):
            # A shared-apex merge absorbed this scan with its own receipt; it
            # reconstructs there but lies outside the final precursor window.
            return ()
        if representative:
            raise StrictSeedReceiptError(
                f"{candidate.candidate_id}: exact canonical Discovery seed "
                "is not unique"
            )
        raise StrictSeedReceiptError(
            f"{candidate.candidate_id}: strict scan {scan.scan_number} does not "
            "reconstruct one canonical Discovery seed"
        )
    if representative:
        canonical_matches = tuple(
            seed
            for seed in matches
            if seed.neutral_loss_tag == candidate.neutral_loss_tag
            and _receipt_close(seed.precursor_mz, candidate.precursor_mz)
            and _receipt_close(seed.product_mz, candidate.product_mz)
        )
        # Nearby native products may share the writer-rounding window without
        # sharing canonical identity. Prefer the persisted full-precision pair;
        # if rounding removed that identity, require an unambiguous receipt.
        exact_matches = tuple(
            seed
            for seed in canonical_matches
            if seed.precursor_mz == candidate.precursor_mz
            and seed.product_mz == candidate.product_mz
        )
        if len(exact_matches or canonical_matches) != 1:
            raise StrictSeedReceiptError(
                f"{candidate.candidate_id}: exact canonical Discovery seed "
                "is not unique"
            )
    tag_precedence = {
        tag_name: index for index, tag_name in enumerate(settings.selected_tag_names)
    }
    if preserve_configured_losses:
        return tuple(
            seed
            for seed in matches
            if include_inferred or seed.precursor_mz_basis in RECORDED_PRECURSOR_BASES
        )
    if include_inferred:
        selected_tag = min(
            matches,
            key=lambda seed: tag_precedence.get(
                seed.neutral_loss_tag,
                len(tag_precedence),
            ),
        ).neutral_loss_tag
        return tuple(seed for seed in matches if seed.neutral_loss_tag == selected_tag)
    # MS1 merging can retain inferred members beyond the final representative's
    # product window. They only account for declared scans here; the direct
    # product check still applies to each recorded reference. Reconstruction
    # preserves source facts; the method owner decides isolation/support.
    matches = tuple(
        seed for seed in matches if seed.precursor_mz_basis in RECORDED_PRECURSOR_BASES
    )
    if not matches:
        return ()
    return (
        min(
            matches,
            key=lambda seed: tag_precedence.get(
                seed.neutral_loss_tag,
                len(tag_precedence),
            ),
        ),
    )


def _scan_receipts(
    candidate: DiscoveryCandidate,
    *,
    scan_number: int,
) -> tuple[dict[str, float], float | None]:
    """Return one scan's per-tag product receipts and absorbed precursor receipt.

    A scan absorbed by a shared-apex merge keeps the receipt of the candidate
    that first claimed it; any other scan uses the candidate's own product.
    """
    try:
        payload = json.loads(candidate.tag_evidence_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise StrictSeedReceiptError(
            f"{candidate.candidate_id}: tag evidence is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise StrictSeedReceiptError(
            f"{candidate.candidate_id}: tag evidence must be an object"
        )

    receipts: dict[str, float] = {}
    precursor_receipt: float | None = None
    matched_tags = set(candidate.matched_tag_names)
    for raw_tag_name, raw_evidence in payload.items():
        tag_name = str(raw_tag_name)
        if tag_name not in matched_tags or not isinstance(raw_evidence, dict):
            continue
        raw_scan_ids = raw_evidence.get("scan_ids")
        if not isinstance(raw_scan_ids, list) or scan_number not in {
            value
            for value in raw_scan_ids
            if isinstance(value, int) and not isinstance(value, bool)
        }:
            continue
        raw_product_mz = raw_evidence.get("product_mz")
        scan_receipt = (raw_evidence.get("scan_receipts") or {}).get(str(scan_number))
        if isinstance(scan_receipt, dict):
            raw_product_mz = scan_receipt.get("product_mz")
            raw_precursor_mz = scan_receipt.get("precursor_mz")
            if (
                not isinstance(raw_precursor_mz, (int, float))
                or isinstance(raw_precursor_mz, bool)
                or not math.isfinite(float(raw_precursor_mz))
                or float(raw_precursor_mz) <= 0.0
            ):
                raise StrictSeedReceiptError(
                    f"{candidate.candidate_id}: tag {tag_name!r} lacks a valid "
                    f"precursor receipt for strict scan {scan_number}"
                )
            precursor_receipt = float(raw_precursor_mz)
        if (
            not isinstance(raw_product_mz, (int, float))
            or isinstance(raw_product_mz, bool)
            or not math.isfinite(float(raw_product_mz))
            or float(raw_product_mz) <= 0.0
        ):
            raise StrictSeedReceiptError(
                f"{candidate.candidate_id}: tag {tag_name!r} lacks a valid "
                f"product receipt for strict scan {scan_number}"
            )
        receipts[tag_name] = float(raw_product_mz)
    if not receipts:
        raise StrictSeedReceiptError(
            f"{candidate.candidate_id}: strict scan {scan_number} lacks owning "
            "per-tag provenance"
        )
    return receipts, precursor_receipt


_within_ppm = within_ppm


def _receipt_close(observed: float, receipt: float) -> bool:
    return math.isclose(
        observed,
        receipt,
        rel_tol=_WRITER_ROUNDING_REL_TOL,
        abs_tol=_WRITER_ROUNDING_ABS_TOL,
    )


__all__ = [
    "StrictSeedReceiptError",
    "StrictSeedReceiptRaw",
    "read_complete_discovery_scan_seeds",
    "resolve_discovery_scan_events",
    "read_parent_ms1_observations",
]
