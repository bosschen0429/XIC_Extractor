"""Reconstruct complete strict Discovery scan receipts from one RAW handle."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    DiscoverySeed,
    DiscoverySettings,
)
from xic_extractor.discovery.ms2_seeds import discovery_seeds_from_scan
from xic_extractor.raw_reader import Ms2Scan, Ms2ScanEvent

_WRITER_ROUNDING_ABS_TOL = 5e-6
_WRITER_ROUNDING_REL_TOL = 5e-6


class StrictSeedReceiptError(ValueError):
    """Raised when current Discovery scan receipts cannot be reconstructed."""


class StrictSeedReceiptRaw(Protocol):
    def iter_ms2_scans(
        self,
        rt_min: float,
        rt_max: float,
    ) -> Iterator[Ms2ScanEvent]: ...


def read_complete_strict_scan_seeds(
    raw: StrictSeedReceiptRaw,
    *,
    candidates: tuple[DiscoveryCandidate, ...],
    settings: DiscoverySettings,
) -> tuple[tuple[Ms2Scan, DiscoverySeed], ...]:
    """Return every declared strict scan once, with its exact canonical seed."""

    by_scan: dict[int, tuple[Ms2Scan, DiscoverySeed]] = {}
    for candidate in candidates:
        for scan in _read_exact_scans(raw, candidate):
            seed = _read_exact_seed(
                scan,
                raw_file=candidate.raw_file,
                candidate=candidate,
                settings=settings,
            )
            existing = by_scan.get(scan.scan_number)
            if existing is not None and existing[1] != seed:
                raise StrictSeedReceiptError(
                    f"scan {scan.scan_number}: candidate receipts disagree on "
                    "canonical strict evidence"
                )
            by_scan[scan.scan_number] = (scan, seed)
    return tuple(by_scan[scan_number] for scan_number in sorted(by_scan))


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
    for event in raw.iter_ms2_scans(
        candidate.rt_seed_min - _WRITER_ROUNDING_ABS_TOL,
        candidate.rt_seed_max + _WRITER_ROUNDING_ABS_TOL,
    ):
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


def _read_exact_seed(
    scan: Ms2Scan,
    *,
    raw_file: Path,
    candidate: DiscoveryCandidate,
    settings: DiscoverySettings,
) -> DiscoverySeed:
    representative = scan.scan_number == candidate.best_ms2_scan_id
    tag_product_receipts = _scan_tag_product_receipts(
        candidate,
        scan_number=scan.scan_number,
    )
    matches = tuple(
        seed
        for seed in discovery_seeds_from_scan(
            scan,
            raw_file=raw_file,
            settings=settings,
        )
        if seed.scan_number in candidate.seed_scan_ids
        and seed.precursor_mz_basis == "scan_precursor"
        and any(
            seed.neutral_loss_tag == tag_name
            and _within_ppm(
                seed.product_mz,
                product_mz,
                settings.product_mz_tolerance_ppm,
            )
            for tag_name, product_mz in tag_product_receipts.items()
        )
        and _within_ppm(
            seed.precursor_mz,
            candidate.precursor_mz,
            settings.precursor_mz_tolerance_ppm,
        )
        and (
            not representative
            or (
                _receipt_close(seed.precursor_mz, candidate.precursor_mz)
                and _receipt_close(seed.product_mz, candidate.product_mz)
            )
        )
    )
    if not matches:
        if representative:
            raise StrictSeedReceiptError(
                f"{candidate.candidate_id}: exact canonical Discovery seed "
                "is not unique"
            )
        raise StrictSeedReceiptError(
            f"{candidate.candidate_id}: strict scan {scan.scan_number} does not "
            "reconstruct one canonical Discovery seed"
        )
    tag_precedence = {
        tag_name: index for index, tag_name in enumerate(settings.selected_tag_names)
    }
    return min(
        matches,
        key=lambda seed: tag_precedence.get(
            seed.neutral_loss_tag,
            len(tag_precedence),
        ),
    )


def _scan_tag_product_receipts(
    candidate: DiscoveryCandidate,
    *,
    scan_number: int,
) -> dict[str, float]:
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
    return receipts


def _within_ppm(observed: float, expected: float, tolerance_ppm: float) -> bool:
    return abs(observed - expected) / expected * 1_000_000.0 <= tolerance_ppm


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
    "read_complete_strict_scan_seeds",
]
