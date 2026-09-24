from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import numpy as np

from xic_extractor.instrument_qc.hcd_registry import products_for_group
from xic_extractor.instrument_qc.models import (
    ActivationMethod,
    HCDAuditRow,
    HCDAuditStatus,
    HCDProductIon,
    SDOLEKTrendRow,
)
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent

DEFAULT_HCD_RT_WINDOW_MIN = 0.20
DEFAULT_HCD_PRODUCT_PPM_MAX = 20.0
DEFAULT_HCD_DIAGNOSTIC_PPM_MAX = 60.0
DEFAULT_HCD_MIN_PRODUCT_BASE_RATIO = 0.01
DEFAULT_HCD_PRECURSOR_TOL_DA = 1.6
DEFAULT_HCD_FALLBACK_RT_MIN = 0.0
DEFAULT_HCD_FALLBACK_RT_MAX = 60.0


class MS2Source(Protocol):
    def iter_ms2_scans(self, rt_min: float, rt_max: float) -> Iterator[Ms2ScanEvent]:
        ...


@dataclass(frozen=True)
class _ProductEvidence:
    trigger_scan_count: int = 0
    parse_error_count: int = 0
    closest_trigger_scan_rt: float | None = None
    best_product_scan_rt: float | None = None
    best_ppm: float | None = None
    best_ratio: float | None = None
    matched_products: tuple[str, ...] = ()
    diagnostic_product_seen: bool = False


def build_hcd_audit_row(
    *,
    trend_row: SDOLEKTrendRow,
    raw: MS2Source,
    products: tuple[HCDProductIon, ...],
    instrument_method: str,
    activation_method: ActivationMethod,
    hcd_product_group: str | None,
    hcd_mapping_source: str,
    cid_neutral_loss_da: float | None = None,
    rt_window_min: float = DEFAULT_HCD_RT_WINDOW_MIN,
    product_ppm_max: float = DEFAULT_HCD_PRODUCT_PPM_MAX,
    diagnostic_ppm_max: float = DEFAULT_HCD_DIAGNOSTIC_PPM_MAX,
    min_product_base_ratio: float = DEFAULT_HCD_MIN_PRODUCT_BASE_RATIO,
    precursor_tol_da: float = DEFAULT_HCD_PRECURSOR_TOL_DA,
    fallback_rt_min: float = DEFAULT_HCD_FALLBACK_RT_MIN,
    fallback_rt_max: float = DEFAULT_HCD_FALLBACK_RT_MAX,
) -> HCDAuditRow | None:
    selected_products = products_for_group(
        products,
        compound=trend_row.compound,
        product_group=hcd_product_group,
        activation=activation_method,
    )
    selected_products = selected_products + _neutral_loss_products(
        trend_row=trend_row,
        activation_method=activation_method,
        neutral_loss_da=cid_neutral_loss_da,
    )
    review_flags: list[str] = []
    if activation_method == "unknown":
        review_flags.append("activation_unknown_review")
    if not selected_products:
        return _row(
            trend_row=trend_row,
            instrument_method=instrument_method,
            activation_method=activation_method,
            hcd_mapping_source=hcd_mapping_source,
            hcd_product_group=hcd_product_group or "",
            hcd_status="hcd_group_unmapped",
            review_flags=tuple(review_flags),
            review_reason="No HCD product group could be mapped for this target.",
        )

    evidence = _empty_product_evidence()
    if trend_row.status == "detected" and trend_row.apex_rt_min is not None:
        rt_min = max(0.0, trend_row.apex_rt_min - rt_window_min)
        rt_max = trend_row.apex_rt_min + rt_window_min
        evidence = _collect_product_evidence(
            raw=raw,
            rt_min=rt_min,
            rt_max=rt_max,
            anchor_rt=trend_row.apex_rt_min,
            precursor_mz=trend_row.precursor_mz,
            products=selected_products,
            product_ppm_max=product_ppm_max,
            diagnostic_ppm_max=diagnostic_ppm_max,
            min_product_base_ratio=min_product_base_ratio,
            precursor_tol_da=precursor_tol_da,
        )

    if not evidence.matched_products:
        fallback_evidence = _collect_product_evidence(
            raw=raw,
            rt_min=fallback_rt_min,
            rt_max=fallback_rt_max,
            anchor_rt=trend_row.apex_rt_min,
            precursor_mz=trend_row.precursor_mz,
            products=selected_products,
            product_ppm_max=product_ppm_max,
            diagnostic_ppm_max=diagnostic_ppm_max,
            min_product_base_ratio=min_product_base_ratio,
            precursor_tol_da=precursor_tol_da,
        )
        if fallback_evidence.matched_products:
            evidence = fallback_evidence
            review_flags.append("target_rt_window_review")

    if evidence.trigger_scan_count == 0:
        status = "ms2_parse_error" if evidence.parse_error_count else "no_ms2_trigger"
        reason = (
            "MS2 parse errors occurred in the apex window."
            if evidence.parse_error_count
            else "No precursor-matched MS2 trigger in the MS1 apex window."
        )
    elif evidence.matched_products:
        status = "hcd_supported"
        if "target_rt_window_review" in review_flags:
            reason = (
                "Expected product ion matched outside the selected MS1 RT window; "
                "target RT prior/window may be stale."
            )
        else:
            reason = (
                "At least one expected product ion matched ppm and intensity gates."
            )
    elif evidence.diagnostic_product_seen:
        status = "hcd_partial"
        reason = "A nearby product ion was observed but failed ppm or intensity gates."
    else:
        status = "no_product_match"
        reason = "MS2 trigger exists, but no expected product ion was observed."

    return _row(
        trend_row=trend_row,
        instrument_method=instrument_method,
        activation_method=activation_method,
        hcd_mapping_source=hcd_mapping_source,
        hcd_product_group=hcd_product_group or "",
        hcd_status=status,
        trigger_scan_count=evidence.trigger_scan_count,
        expected_product_count=len(selected_products),
        matched_product_count=len(evidence.matched_products),
        best_ms2_scan_rt_min=(
            evidence.best_product_scan_rt or evidence.closest_trigger_scan_rt
        ),
        best_product_ppm=evidence.best_ppm,
        best_product_base_ratio=evidence.best_ratio,
        matched_products=tuple(sorted(evidence.matched_products)),
        review_flags=tuple(review_flags),
        review_reason=reason,
    )


def _empty_product_evidence() -> _ProductEvidence:
    return _ProductEvidence()


def _collect_product_evidence(
    *,
    raw: MS2Source,
    rt_min: float,
    rt_max: float,
    anchor_rt: float | None,
    precursor_mz: float,
    products: Iterable[HCDProductIon],
    product_ppm_max: float,
    diagnostic_ppm_max: float,
    min_product_base_ratio: float,
    precursor_tol_da: float,
) -> _ProductEvidence:
    trigger_scan_count = 0
    parse_error_count = 0
    closest_trigger_scan_rt: float | None = None
    closest_trigger_delta: float | None = None
    best_product_scan_rt: float | None = None
    best_ppm: float | None = None
    best_ratio: float | None = None
    matched_products: dict[str, tuple[float, float]] = {}
    diagnostic_product_seen = False

    for event in raw.iter_ms2_scans(rt_min, rt_max):
        if event.parse_error is not None or event.scan is None:
            parse_error_count += 1
            continue
        if abs(event.scan.precursor_mz - precursor_mz) > precursor_tol_da:
            continue
        trigger_scan_count += 1
        scan_delta = (
            abs(event.scan.rt - anchor_rt)
            if anchor_rt is not None
            else min(abs(event.scan.rt - rt_min), abs(event.scan.rt - rt_max))
        )
        if closest_trigger_delta is None or scan_delta < closest_trigger_delta:
            closest_trigger_delta = scan_delta
            closest_trigger_scan_rt = event.scan.rt
        scan_matches = _product_matches_for_scan(
            event.scan,
            products,
            diagnostic_ppm_max=diagnostic_ppm_max,
        )
        if not scan_matches:
            continue
        diagnostic_product_seen = True
        for product, ppm, ratio in scan_matches:
            if best_ppm is None or abs(ppm) < abs(best_ppm):
                best_product_scan_rt = event.scan.rt
                best_ppm = ppm
                best_ratio = ratio
            if abs(ppm) <= product_ppm_max and ratio >= min_product_base_ratio:
                label = _matched_product_label(product)
                previous = matched_products.get(label)
                if previous is None or abs(ppm) < abs(previous[0]):
                    matched_products[label] = (ppm, ratio)

    return _ProductEvidence(
        trigger_scan_count=trigger_scan_count,
        parse_error_count=parse_error_count,
        closest_trigger_scan_rt=closest_trigger_scan_rt,
        best_product_scan_rt=best_product_scan_rt,
        best_ppm=best_ppm,
        best_ratio=best_ratio,
        matched_products=tuple(sorted(matched_products)),
        diagnostic_product_seen=diagnostic_product_seen,
    )


def _product_matches_for_scan(
    scan: Ms2Scan,
    products: Iterable[HCDProductIon],
    *,
    diagnostic_ppm_max: float,
) -> list[tuple[HCDProductIon, float, float]]:
    if len(scan.masses) == 0 or scan.base_peak <= 0:
        return []
    matches: list[tuple[HCDProductIon, float, float]] = []
    for product in products:
        index = int(np.argmin(np.abs(scan.masses - product.product_mz)))
        observed_mz = float(scan.masses[index])
        ppm = (observed_mz - product.product_mz) / product.product_mz * 1_000_000.0
        if abs(ppm) > diagnostic_ppm_max:
            continue
        intensity = float(scan.intensities[index])
        ratio = intensity / scan.base_peak
        matches.append((product, ppm, ratio))
    return matches


def _neutral_loss_products(
    *,
    trend_row: SDOLEKTrendRow,
    activation_method: ActivationMethod,
    neutral_loss_da: float | None,
) -> tuple[HCDProductIon, ...]:
    if neutral_loss_da is None or neutral_loss_da <= 0:
        return ()
    if activation_method not in {"CID", "wHCD", "HCD", "CIDwHCD", "unknown"}:
        return ()
    product_mz = trend_row.precursor_mz - neutral_loss_da
    if product_mz <= 0:
        return ()
    return (
        HCDProductIon(
            compound_or_group=trend_row.compound,
            precursor_mz=trend_row.precursor_mz,
            activation=activation_method,
            product_label=f"NL-{neutral_loss_da:.4f}",
            product_mz=product_mz,
            product_role="neutral_loss_product",
        ),
    )


def _matched_product_label(product: HCDProductIon) -> str:
    return f"{product.activation}:{product.product_label}"


def _row(
    *,
    trend_row: SDOLEKTrendRow,
    instrument_method: str,
    activation_method: ActivationMethod,
    hcd_mapping_source: str,
    hcd_product_group: str,
    hcd_status: str,
    best_ms2_scan_rt_min: float | None = None,
    trigger_scan_count: int = 0,
    expected_product_count: int = 0,
    matched_product_count: int = 0,
    best_product_ppm: float | None = None,
    best_product_base_ratio: float | None = None,
    matched_products: tuple[str, ...] = (),
    review_flags: tuple[str, ...] = (),
    review_reason: str = "",
) -> HCDAuditRow:
    apex_delta = (
        abs(trend_row.apex_rt_min - best_ms2_scan_rt_min)
        if trend_row.apex_rt_min is not None and best_ms2_scan_rt_min is not None
        else None
    )
    return HCDAuditRow(
        sample_name=trend_row.sample_name,
        raw_path=Path(trend_row.raw_path),
        injection_order=trend_row.injection_order,
        compound=trend_row.compound,
        precursor_mz=trend_row.precursor_mz,
        ms1_apex_rt_min=trend_row.apex_rt_min,
        ms1_status=trend_row.status,
        instrument_method=instrument_method,
        activation_method=activation_method,
        hcd_mapping_source=hcd_mapping_source,
        hcd_product_group=hcd_product_group,
        hcd_status=cast(HCDAuditStatus, hcd_status),
        best_ms2_scan_rt_min=best_ms2_scan_rt_min,
        apex_ms2_delta_min=apex_delta,
        trigger_scan_count=trigger_scan_count,
        expected_product_count=expected_product_count,
        matched_product_count=matched_product_count,
        best_product_ppm=best_product_ppm,
        best_product_base_ratio=best_product_base_ratio,
        matched_products=matched_products,
        review_flags=review_flags,
        review_reason=review_reason,
    )
