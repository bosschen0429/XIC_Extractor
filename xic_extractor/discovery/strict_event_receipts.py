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
from xic_extractor.discovery.models import DiscoveryEvent, DiscoveryPeak
from xic_extractor.discovery.ms2_events import discovery_events_from_scan
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
from xic_extractor.precursor_evidence import (
    RECORDED_PRECURSOR_BASES,
    MS1MassWindowSource,
)

_WRITER_ROUNDING_ABS_TOL = 5e-6
_WRITER_ROUNDING_REL_TOL = 5e-6


class StrictEventReceiptError(ValueError):
    """Raised when current Discovery scan receipts cannot be reconstructed."""


class StrictEventReceiptRaw(Protocol):
    def iter_ms2_scan_subset(
        self, rt_min: float, rt_max: float, scan_numbers: frozenset[int]
    ) -> Iterator[Ms2ScanEvent]:
        """Declared scans within the native RT-to-scan window."""


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


def read_complete_discovery_scan_events(
    raw: StrictEventReceiptRaw,
    *,
    peaks: tuple[DiscoveryPeak, ...],
    settings: DiscoverySettings,
    preserve_configured_losses: bool = False,
    include_inferred: bool = False,
) -> tuple[tuple[Ms2Scan, DiscoveryEvent], ...]:
    """Return declared events, retaining their direct or inferred precursor basis."""

    by_scan: dict[tuple[int, float, float, float], tuple[Ms2Scan, DiscoveryEvent]] = {}
    scan_event_memo: _ScanEventMemo = {}
    for peak in peaks:
        for scan in _read_exact_scans(raw, peak):
            events = _read_exact_event(
                scan,
                raw_file=peak.raw_file,
                peak=peak,
                settings=settings,
                preserve_configured_losses=preserve_configured_losses,
                include_inferred=include_inferred,
                scan_event_memo=scan_event_memo,
            )
            for event in events:
                key = (
                    scan.scan_number,
                    event.configured_neutral_loss_da
                    if preserve_configured_losses
                    else 0.0,
                    event.precursor_mz,
                    event.product_mz,
                )
                existing = by_scan.get(key)
                if existing is not None and existing[1] != event:
                    raise StrictEventReceiptError(
                        f"scan {scan.scan_number}: peak receipts disagree on "
                        "canonical strict evidence"
                    )
                by_scan[key] = (scan, event)
    return tuple(by_scan[scan_number] for scan_number in sorted(by_scan))


def resolve_discovery_scan_events(
    events: tuple[DiscoveryEvent, ...],
    *,
    settings: DiscoverySettings,
    preserve_configured_losses: bool = False,
    precursor_reference_requirement: PrecursorReferenceRequirement = "independent_mass",
) -> tuple[DiscoveryEvent, ...]:
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


def _read_exact_scans(
    raw: StrictEventReceiptRaw,
    peak: DiscoveryPeak,
) -> tuple[Ms2Scan, ...]:
    declared = tuple(sorted(set(peak.event_scan_ids)))
    if not declared:
        raise StrictEventReceiptError(
            f"{peak.peak_id}: peak declares no strict MS2 scans"
        )
    matches_by_scan: dict[int, list[Ms2Scan]] = {
        scan_number: [] for scan_number in declared
    }
    rt_min = peak.event_rt_min - _WRITER_ROUNDING_ABS_TOL
    rt_max = peak.event_rt_max + _WRITER_ROUNDING_ABS_TOL
    for read in raw.iter_ms2_scan_subset(rt_min, rt_max, frozenset(declared)):
        if (
            read.parse_error is None
            and read.scan is not None
            and read.scan.scan_number in matches_by_scan
        ):
            matches_by_scan[read.scan.scan_number].append(read.scan)
    if any(len(matches_by_scan[scan_number]) != 1 for scan_number in declared):
        raise StrictEventReceiptError(
            f"{peak.peak_id}: declared strict MS2 scans are not exact"
        )
    return tuple(matches_by_scan[scan_number][0] for scan_number in declared)


_ScanEventMemo = dict[tuple[Path, int], tuple[Ms2Scan, tuple[DiscoveryEvent, ...]]]


def _scan_events(
    scan: Ms2Scan,
    *,
    raw_file: Path,
    settings: DiscoverySettings,
    memo: _ScanEventMemo | None,
) -> tuple[DiscoveryEvent, ...]:
    """Enumerate one scan's events once per exact scan, not once per peak.

    Seed enumeration depends only on the scan numerics, its RAW file, and the
    settings, so peaks that declare the same scan reuse one enumeration.
    A memo entry is reused only when the scan it was built from is numerically
    identical; anything else recomputes. Candidate-specific receipt matching
    stays in the caller.
    """

    if memo is None:
        return discovery_events_from_scan(scan, raw_file=raw_file, settings=settings)
    key = (raw_file, scan.scan_number)
    cached = memo.get(key)
    if cached is not None and _same_scan_numerics(cached[0], scan):
        return cached[1]
    events = discovery_events_from_scan(scan, raw_file=raw_file, settings=settings)
    memo[key] = (scan, events)
    return events


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


def _read_exact_event(
    scan: Ms2Scan,
    *,
    raw_file: Path,
    peak: DiscoveryPeak,
    settings: DiscoverySettings,
    preserve_configured_losses: bool = False,
    include_inferred: bool = False,
    scan_event_memo: _ScanEventMemo | None = None,
) -> tuple[DiscoveryEvent, ...]:
    representative = scan.scan_number == peak.best_ms2_scan_id
    tag_product_receipts, precursor_receipt, tag_precursor_receipts = _scan_receipts(
        peak,
        scan_number=scan.scan_number,
    )
    scan_seeds = _scan_events(
        scan,
        raw_file=raw_file,
        settings=settings,
        memo=scan_event_memo,
    )
    matches = tuple(
        event
        for event in scan_seeds
        if event.scan_number in peak.event_scan_ids
        and (
            event.precursor_mz_basis in RECORDED_PRECURSOR_BASES
            or peak.precursor_mz_basis == "mixed"
            or include_inferred
        )
        and any(
            event.neutral_loss_tag == tag_name
            and (
                (
                    not representative
                    and event.precursor_mz_basis == "product_plus_neutral_loss"
                )
                or _within_ppm(
                    event.product_mz,
                    product_mz,
                    settings.product_mz_tolerance_ppm,
                )
                or (
                    # A shared-apex merge can retain another product's receipt
                    # under the same scan. The canonical row still owns its
                    # representative; reconstruct it from native scan events.
                    representative
                    and tag_name == peak.neutral_loss_tag
                    and _receipt_close(event.precursor_mz, peak.best_event_precursor_mz)
                    and _receipt_close(event.product_mz, peak.best_event_product_mz)
                )
            )
            for tag_name, product_mz in tag_product_receipts.items()
        )
        and (
            _within_ppm(
                event.precursor_mz,
                peak.best_event_precursor_mz,
                settings.precursor_mz_tolerance_ppm,
            )
            # Events group by their parent-MS1 ion, so inferred estimates on one
            # peak can differ by more than the tolerance. An inferred event
            # replays against its own scan receipt; Alignment then verifies its
            # measured precursor against the peak.
            or (
                event.precursor_mz_basis == "product_plus_neutral_loss"
                and event.neutral_loss_tag in tag_precursor_receipts
                and _receipt_close(
                    event.precursor_mz,
                    tag_precursor_receipts[event.neutral_loss_tag],
                )
            )
        )
        and (
            preserve_configured_losses
            or not representative
            or (
                _receipt_close(event.precursor_mz, peak.best_event_precursor_mz)
                and _receipt_close(event.product_mz, peak.best_event_product_mz)
            )
        )
    )
    if not matches:
        if (
            include_inferred
            and not representative
            and any(
                event.precursor_mz_basis == "product_plus_neutral_loss"
                and event.neutral_loss_tag in tag_product_receipts
                for event in scan_seeds
            )
        ):
            # The scan reconstructs inferred events, but none reproduces its
            # receipt for this peak, so it cannot support this mass question.
            return ()
        if (
            not representative
            and precursor_receipt is not None
            and any(
                event.neutral_loss_tag in tag_product_receipts
                and _within_ppm(
                    event.product_mz,
                    tag_product_receipts[event.neutral_loss_tag],
                    settings.product_mz_tolerance_ppm,
                )
                and _within_ppm(
                    event.precursor_mz,
                    precursor_receipt,
                    settings.precursor_mz_tolerance_ppm,
                )
                for event in scan_seeds
            )
        ):
            # A shared-apex merge absorbed this scan with its own receipt; it
            # reconstructs there but lies outside the final precursor window.
            return ()
        if representative:
            raise StrictEventReceiptError(
                f"{peak.peak_id}: exact canonical Discovery event is not unique"
            )
        raise StrictEventReceiptError(
            f"{peak.peak_id}: strict scan {scan.scan_number} does not "
            "reconstruct one canonical Discovery event"
        )
    if representative:
        canonical_matches = tuple(
            event
            for event in matches
            if event.neutral_loss_tag == peak.neutral_loss_tag
            and _receipt_close(event.precursor_mz, peak.best_event_precursor_mz)
            and _receipt_close(event.product_mz, peak.best_event_product_mz)
        )
        # Nearby native products may share the writer-rounding window without
        # sharing canonical identity. Prefer the persisted full-precision pair;
        # if rounding removed that identity, require an unambiguous receipt.
        exact_matches = tuple(
            event
            for event in canonical_matches
            if event.precursor_mz == peak.best_event_precursor_mz
            and event.product_mz == peak.best_event_product_mz
        )
        if len(exact_matches or canonical_matches) != 1:
            raise StrictEventReceiptError(
                f"{peak.peak_id}: exact canonical Discovery event is not unique"
            )
    tag_precedence = {
        tag_name: index for index, tag_name in enumerate(settings.selected_tag_names)
    }
    if preserve_configured_losses:
        return tuple(
            event
            for event in matches
            if include_inferred or event.precursor_mz_basis in RECORDED_PRECURSOR_BASES
        )
    if include_inferred:
        selected_tag = min(
            matches,
            key=lambda event: tag_precedence.get(
                event.neutral_loss_tag,
                len(tag_precedence),
            ),
        ).neutral_loss_tag
        return tuple(
            event for event in matches if event.neutral_loss_tag == selected_tag
        )
    # MS1 merging can retain inferred members beyond the final representative's
    # product window. They only account for declared scans here; the direct
    # product check still applies to each recorded reference. Reconstruction
    # preserves source facts; the method owner decides isolation/support.
    matches = tuple(
        event
        for event in matches
        if event.precursor_mz_basis in RECORDED_PRECURSOR_BASES
    )
    if not matches:
        return ()
    return (
        min(
            matches,
            key=lambda event: tag_precedence.get(
                event.neutral_loss_tag,
                len(tag_precedence),
            ),
        ),
    )


def _scan_receipts(
    peak: DiscoveryPeak,
    *,
    scan_number: int,
) -> tuple[dict[str, float], float | None, dict[str, float]]:
    """Return one scan's per-tag product, absorbed and per-tag precursor receipts.

    A scan absorbed by a shared-apex merge keeps the receipt of the peak
    that first claimed it; any other scan uses the peak's own product.
    """
    try:
        payload = json.loads(peak.tag_evidence_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise StrictEventReceiptError(
            f"{peak.peak_id}: tag evidence is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise StrictEventReceiptError(f"{peak.peak_id}: tag evidence must be an object")

    receipts: dict[str, float] = {}
    precursor_receipt: float | None = None
    precursor_receipts: dict[str, float] = {}
    matched_tags = set(peak.matched_tag_names)
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
                raise StrictEventReceiptError(
                    f"{peak.peak_id}: tag {tag_name!r} lacks a valid "
                    f"precursor receipt for strict scan {scan_number}"
                )
            precursor_receipt = float(raw_precursor_mz)
            precursor_receipts[tag_name] = precursor_receipt
        if (
            not isinstance(raw_product_mz, (int, float))
            or isinstance(raw_product_mz, bool)
            or not math.isfinite(float(raw_product_mz))
            or float(raw_product_mz) <= 0.0
        ):
            raise StrictEventReceiptError(
                f"{peak.peak_id}: tag {tag_name!r} lacks a valid "
                f"product receipt for strict scan {scan_number}"
            )
        receipts[tag_name] = float(raw_product_mz)
    if not receipts:
        raise StrictEventReceiptError(
            f"{peak.peak_id}: strict scan {scan_number} lacks owning per-tag provenance"
        )
    return receipts, precursor_receipt, precursor_receipts


_within_ppm = within_ppm


def _receipt_close(observed: float, receipt: float) -> bool:
    return math.isclose(
        observed,
        receipt,
        rel_tol=_WRITER_ROUNDING_REL_TOL,
        abs_tol=_WRITER_ROUNDING_ABS_TOL,
    )


__all__ = [
    "StrictEventReceiptError",
    "StrictEventReceiptRaw",
    "read_complete_discovery_scan_events",
    "resolve_discovery_scan_events",
    "read_parent_ms1_observations",
]
