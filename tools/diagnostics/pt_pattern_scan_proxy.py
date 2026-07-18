"""Scan-level Pt pattern proxy: a diagnostic bridge, not a feature detector.

Purpose (Task 7 of the Pt pattern Discovery plan):

The Pt pattern Discovery CLI (``scripts/run_pt_pattern_discovery.py``) only
consumes a schema-valid Detected Features CSV
(``xic_extractor.discovery.delta_mass_csv.DETECTED_FEATURES_COLUMNS``); it
never reads a RAW file directly. No accepted detected-feature CSV source
exists in this repo yet (no MS1 peak detector emits this exact schema), so
there is currently nothing to hand the Pt pattern matcher for a first look at
a real Fullscan sample.

This module is the fallback named by the plan's Rules: a SCAN-LEVEL proxy
that reads MS1 scans directly out of a RAW file and looks, scan by scan, for
the four Pt pattern m/z positions (``base``, ``pt_plus_1``, ``pt_plus_2``,
``pt_plus_4`` -- see ``xic_extractor.discovery.delta_mass_patterns.PT_PATTERN``)
near every plausible base peak. It groups same-scan co-occurring hits across
consecutive scans into one pseudo-"feature" per (position, candidate group)
and writes those pseudo-features out in the exact Detected Features CSV v1
shape, with ``source = "raw_scan_proxy_v1"`` on every row, plus a provenance
JSON recording what was actually done.

This is explicitly **not** a product feature detector:

- No chromatographic peak-shape modeling, no smoothing, no deconvolution.
- No real trace-quality assessment. The proxy only uses repeated scan-level
  full-pattern support as a diagnostic local-signal surrogate, records that
  surrogate in provenance, and leaves ``shape_score`` blank.
- Its own internal m/z lookup window (``--lookup-ppm``, default 20 ppm) matches
  the Pt pattern's real m/z gate by default. Wider exploratory windows remain
  available through the CLI, but the real gates are always re-applied later by
  ``xic_extractor.discovery.delta_mass_patterns.match_delta_mass_pattern``.

Reuse notes: this module reuses ``xic_extractor.raw_reader.open_raw`` and the
shared ``xic_extractor.ms1_scan_index`` reader for RAW access, so scan errors
fail closed instead of producing a partial proxy labeled as valid, and
``xic_extractor.discovery.delta_mass_patterns.PT_PATTERN`` /
``xic_extractor.discovery.delta_mass_csv.DETECTED_FEATURES_COLUMNS`` for the
pattern deltas and output schema. It does not import
``xic_extractor.alignment`` or ``xic_extractor.output``: this tool is
diagnostics-only, per the plan's boundary rules for the Pt pattern lane.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from xic_extractor.discovery.delta_mass_csv import DETECTED_FEATURES_COLUMNS
from xic_extractor.discovery.delta_mass_patterns import PT_PATTERN
from xic_extractor.ms1_scan_index import (
    MS1ScanReadError,
    build_ms1_scan_index_for_rt_window,
)
from xic_extractor.numeric_validation import require_finite_real, require_integer
from xic_extractor.raw_reader import RawReaderError, open_raw
from xic_extractor.tabular_io import write_delimited_rows

PROXY_SOURCE_LABEL = "raw_scan_proxy_v1"
PROVENANCE_SCHEMA_VERSION = "pt_pattern_scan_proxy_provenance.v1"

# Diagnostic-only local support fields. ``OK`` lets repeated full-pattern
# scan-proxy groups pass the Pt matcher's local-signal gate so this bridge can
# produce review candidates; provenance keeps the result from being confused
# with product-grade chromatographic peak quality. Shape score is blank because
# the proxy does not compute a chromatographic shape model.
_TRACE_QUALITY_PLACEHOLDER = "OK"
_SHAPE_SCORE_PLACEHOLDER = ""
_DEFAULT_LOOKUP_PPM = 20.0
_DEFAULT_MIN_SCANS_PER_GROUP = 2

_NON_BASE_LABELS: tuple[str, ...] = tuple(
    position.label for position in PT_PATTERN.positions if position.label != "base"
)
_DELTA_BY_LABEL: dict[str, float] = {
    position.label: position.delta_da for position in PT_PATTERN.positions
}


@dataclass(frozen=True)
class Ms1ScanRecord:
    """A single MS1 scan's centroid peak list."""

    scan_number: int
    rt_min: float
    masses: NDArray[np.float64]
    intensities: NDArray[np.float64]


@dataclass(frozen=True)
class ScanPatternHit:
    """One scan's full four-position Pt pattern lookup around a base peak."""

    scan_number: int
    rt_min: float
    base_mz: float
    base_intensity: float
    position_mz: dict[str, float]
    position_intensity: dict[str, float]
    position_ppm_error: dict[str, float]


@dataclass(frozen=True)
class ProxyFeature:
    """One pseudo-feature: the apex scan for one Pt position within one
    contiguous scan-hit group."""

    group_id: int
    label: str
    mz: float
    rt_apex_min: float
    rt_start_min: float
    rt_end_min: float
    height: float


def iter_ms1_scans(
    handle: Any,
    *,
    rt_min: float,
    rt_max: float,
) -> Iterator[Ms1ScanRecord]:
    """Iterate MS1 (``MSOrder == Ms``) scans in ``[rt_min, rt_max]``.

    Reuses the shared MS1 index reader and fails closed on malformed or
    unreadable scans. A partial index must not be mislabeled as an ``OK``
    proxy feature source.
    """

    _validate_rt_window(rt_min=rt_min, rt_max=rt_max)
    for scan in build_ms1_scan_index_for_rt_window(
        handle,
        rt_min=rt_min,
        rt_max=rt_max,
    ):
        yield Ms1ScanRecord(
            scan_number=scan.scan_number,
            rt_min=scan.rt,
            masses=scan.masses,
            intensities=scan.intensities,
        )


def _find_best_peak(
    masses: NDArray[np.float64],
    intensities: NDArray[np.float64],
    *,
    expected_mz: float,
    lookup_ppm: float,
    min_intensity: float,
) -> tuple[float, float, float] | None:
    """Return ``(mz, intensity, ppm_error)`` for the most intense centroid
    within ``lookup_ppm`` of ``expected_mz``, or ``None`` if none qualifies."""

    tolerance = expected_mz * lookup_ppm * 1e-6
    left = int(np.searchsorted(masses, expected_mz - tolerance, side="left"))
    right = int(np.searchsorted(masses, expected_mz + tolerance, side="right"))
    if left == right:
        return None
    window_mz = masses[left:right]
    window_intensity = intensities[left:right]
    valid = np.flatnonzero(
        np.isfinite(window_mz)
        & np.isfinite(window_intensity)
        & (window_intensity > min_intensity)
    )
    if valid.size == 0:
        return None
    best_offset = int(valid[np.argmax(window_intensity[valid])])
    peak_mz = float(window_mz[best_offset])
    peak_intensity = float(window_intensity[best_offset])
    ppm_error = (peak_mz - expected_mz) / expected_mz * 1e6
    return peak_mz, peak_intensity, ppm_error


def _base_candidate_indices(
    masses: NDArray[np.float64],
    intensities: NDArray[np.float64],
    *,
    min_intensity: float,
    max_candidates_per_scan: int,
) -> NDArray[np.intp]:
    mask = (
        np.isfinite(masses) & np.isfinite(intensities) & (intensities > min_intensity)
    )
    indices = np.flatnonzero(mask)
    if max_candidates_per_scan > 0 and indices.size > max_candidates_per_scan:
        top = np.argpartition(intensities[indices], -max_candidates_per_scan)[
            -max_candidates_per_scan:
        ]
        indices = indices[top]
    return indices[np.argsort(masses[indices])]


def scan_pattern_hits(
    scan: Ms1ScanRecord,
    *,
    lookup_ppm: float,
    min_base_intensity: float,
    min_position_intensity: float,
    max_candidates_per_scan: int,
) -> list[ScanPatternHit]:
    """Find every base peak in ``scan`` with all three non-base Pt positions
    present within ``lookup_ppm`` in the same scan."""

    _validate_scan_lookup_settings(
        lookup_ppm=lookup_ppm,
        min_base_intensity=min_base_intensity,
        min_position_intensity=min_position_intensity,
        max_candidates_per_scan=max_candidates_per_scan,
    )
    candidate_indices = _base_candidate_indices(
        scan.masses,
        scan.intensities,
        min_intensity=min_base_intensity,
        max_candidates_per_scan=max_candidates_per_scan,
    )
    hits: list[ScanPatternHit] = []
    for index in candidate_indices:
        base_mz = float(scan.masses[index])
        base_intensity = float(scan.intensities[index])
        position_mz: dict[str, float] = {}
        position_intensity: dict[str, float] = {}
        position_ppm_error: dict[str, float] = {}
        for label in _NON_BASE_LABELS:
            expected_mz = base_mz + _DELTA_BY_LABEL[label]
            found = _find_best_peak(
                scan.masses,
                scan.intensities,
                expected_mz=expected_mz,
                lookup_ppm=lookup_ppm,
                min_intensity=min_position_intensity,
            )
            if found is None:
                break
            peak_mz, peak_intensity, ppm_error = found
            position_mz[label] = peak_mz
            position_intensity[label] = peak_intensity
            position_ppm_error[label] = ppm_error
        if len(position_mz) != len(_NON_BASE_LABELS):
            continue
        hits.append(
            ScanPatternHit(
                scan_number=scan.scan_number,
                rt_min=scan.rt_min,
                base_mz=base_mz,
                base_intensity=base_intensity,
                position_mz=position_mz,
                position_intensity=position_intensity,
                position_ppm_error=position_ppm_error,
            )
        )
    return hits


def _group_key(base_mz: float, *, group_width_da: float) -> float:
    return round(base_mz / group_width_da) * group_width_da


def group_scan_hits(
    hits: Sequence[ScanPatternHit],
    *,
    group_width_da: float,
    rt_gap_split_min: float,
) -> list[list[ScanPatternHit]]:
    """Bucket scan hits by base m/z, then split each bucket into contiguous
    RT groups (a scan-level proxy for "one chromatographic peak region")."""

    _require_positive_finite(group_width_da, "group_width_da")
    _require_positive_finite(rt_gap_split_min, "rt_gap_split_min")
    by_bucket: dict[float, list[ScanPatternHit]] = {}
    for hit in hits:
        key = _group_key(hit.base_mz, group_width_da=group_width_da)
        by_bucket.setdefault(key, []).append(hit)

    groups: list[list[ScanPatternHit]] = []
    for bucket_hits in by_bucket.values():
        ordered = sorted(bucket_hits, key=lambda item: item.rt_min)
        current: list[ScanPatternHit] = []
        previous_rt: float | None = None
        for hit in ordered:
            if (
                current
                and previous_rt is not None
                and hit.rt_min - previous_rt > rt_gap_split_min
            ):
                groups.append(current)
                current = []
            current.append(hit)
            previous_rt = hit.rt_min
        if current:
            groups.append(current)
    return groups


def filter_scan_hit_groups(
    groups: Sequence[Sequence[ScanPatternHit]],
    *,
    min_scans_per_group: int,
) -> list[list[ScanPatternHit]]:
    """Keep only scan-hit groups observed across enough scans in one RAW file.

    A single scan can contain an accidental four-position co-occurrence. The
    proxy has no chromatographic shape model, so repeated scan-level support is
    its simplest diagnostic surrogate for local signal support. This is not a
    cross-sample or multi-RAW requirement.
    """

    if min_scans_per_group < 1:
        raise ValueError("min_scans_per_group must be >= 1")
    return [list(group) for group in groups if len(group) >= min_scans_per_group]


def features_from_groups(
    groups: Sequence[Sequence[ScanPatternHit]],
) -> list[ProxyFeature]:
    """Reduce each scan-hit group to one pseudo-feature per Pt position,
    keyed on the group's apex scan (highest base intensity)."""

    features: list[ProxyFeature] = []
    for group_id, members in enumerate(groups, start=1):
        apex = max(members, key=lambda item: item.base_intensity)
        rt_start_min = min(item.rt_min for item in members)
        rt_end_min = max(item.rt_min for item in members)
        features.append(
            ProxyFeature(
                group_id=group_id,
                label="base",
                mz=apex.base_mz,
                rt_apex_min=apex.rt_min,
                rt_start_min=rt_start_min,
                rt_end_min=rt_end_min,
                height=apex.base_intensity,
            )
        )
        for label in _NON_BASE_LABELS:
            features.append(
                ProxyFeature(
                    group_id=group_id,
                    label=label,
                    mz=apex.position_mz[label],
                    rt_apex_min=apex.rt_min,
                    rt_start_min=rt_start_min,
                    rt_end_min=rt_end_min,
                    height=apex.position_intensity[label],
                )
            )
    return features


def detected_feature_rows(
    features: Sequence[ProxyFeature],
    *,
    sample_stem: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for feature in features:
        feature_id = f"{sample_stem}|scan_proxy|group{feature.group_id}|{feature.label}"
        rows.append(
            {
                "sample_stem": sample_stem,
                "feature_id": feature_id,
                "source_feature_row_id": "",
                "mz": feature.mz,
                "rt_apex_min": feature.rt_apex_min,
                "rt_start_min": feature.rt_start_min,
                "rt_end_min": feature.rt_end_min,
                "height": feature.height,
                "area": "",
                "trace_quality": _TRACE_QUALITY_PLACEHOLDER,
                "shape_score": _SHAPE_SCORE_PLACEHOLDER,
                "source": PROXY_SOURCE_LABEL,
            }
        )
    return rows


def write_detected_features_csv(
    path: Path,
    rows: Sequence[dict[str, object]],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_delimited_rows(
        path,
        rows,
        DETECTED_FEATURES_COLUMNS,
        extrasaction="raise",
    )
    return path


def build_provenance_payload(
    *,
    raw_path: Path,
    dll_dir: Path,
    rt_min: float,
    rt_max: float,
    lookup_ppm: float,
    min_base_intensity: float,
    min_position_intensity: float,
    max_candidates_per_scan: int,
    group_width_da: float,
    rt_gap_split_min: float,
    min_scans_per_group: int,
    ms1_scan_count: int,
    scan_hit_count: int,
    raw_group_count: int,
    group_count: int,
    feature_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "diagnostic_only": True,
        "not_a_product_feature_detector": True,
        "detected_feature_source": PROXY_SOURCE_LABEL,
        "raw_file": str(raw_path),
        "dll_dir": str(dll_dir),
        "pattern": {
            "pattern_id": PT_PATTERN.pattern_id,
            "positions": [
                {"label": position.label, "delta_da": position.delta_da}
                for position in PT_PATTERN.positions
            ],
            "matcher_mz_tolerance_ppm": PT_PATTERN.mz_tolerance_ppm,
            "matcher_rt_tolerance_sec": PT_PATTERN.rt_tolerance_sec,
        },
        "proxy_lookup_parameters": {
            "lookup_ppm": lookup_ppm,
            "note": (
                "This is the proxy's scan-level lookup window. The default "
                "matches the real Pt matcher m/z gate; if widened for "
                "exploration, the Pt matcher still re-validates every "
                "candidate against the real 20ppm/30sec Global Constraint "
                "gates."
            ),
            "min_base_intensity": min_base_intensity,
            "min_position_intensity": min_position_intensity,
            "max_candidates_per_scan": max_candidates_per_scan,
            "group_width_da": group_width_da,
            "rt_gap_split_min": rt_gap_split_min,
            "min_scans_per_group": min_scans_per_group,
            "min_scans_per_group_note": (
                "Minimum MS1 scans inside one RAW file for a proxy group; "
                "this is not a sample-count or cross-RAW requirement."
            ),
        },
        "trace_quality_placeholder": _TRACE_QUALITY_PLACEHOLDER,
        "shape_score_placeholder": _SHAPE_SCORE_PLACEHOLDER,
        "rt_window_min": {"rt_min": rt_min, "rt_max": rt_max},
        "counts": {
            "ms1_scan_count": ms1_scan_count,
            "scan_hit_count": scan_hit_count,
            "raw_group_count": raw_group_count,
            "group_count": group_count,
            "feature_count": feature_count,
        },
    }


def run_proxy(
    *,
    raw_path: Path,
    dll_dir: Path,
    output_dir: Path,
    sample_stem: str,
    rt_min: float = 0.0,
    rt_max: float = 999.0,
    lookup_ppm: float = _DEFAULT_LOOKUP_PPM,
    min_base_intensity: float = 0.0,
    min_position_intensity: float = 0.0,
    max_candidates_per_scan: int = 500,
    group_width_da: float = 0.005,
    rt_gap_split_min: float = 0.20,
    min_scans_per_group: int = _DEFAULT_MIN_SCANS_PER_GROUP,
) -> tuple[Path, Path]:
    """Run the scan-level Pt pattern proxy end-to-end against a real RAW file.

    Writes ``detected_features.csv`` and ``proxy_provenance.json`` under
    ``output_dir`` and returns their paths. Requires a real Thermo DLL and RAW
    file; the smoke test in ``tests/test_pt_pattern_scan_proxy.py`` instead
    exercises ``detected_feature_rows`` / ``write_detected_features_csv``
    directly against a fake ``Ms1ScanRecord`` sequence, without opening a RAW
    file at all.
    """

    _validate_proxy_settings(
        sample_stem=sample_stem,
        rt_min=rt_min,
        rt_max=rt_max,
        lookup_ppm=lookup_ppm,
        min_base_intensity=min_base_intensity,
        min_position_intensity=min_position_intensity,
        max_candidates_per_scan=max_candidates_per_scan,
        group_width_da=group_width_da,
        rt_gap_split_min=rt_gap_split_min,
        min_scans_per_group=min_scans_per_group,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    with open_raw(raw_path, dll_dir) as handle:
        scans = list(iter_ms1_scans(handle, rt_min=rt_min, rt_max=rt_max))

    all_hits: list[ScanPatternHit] = []
    for scan in scans:
        all_hits.extend(
            scan_pattern_hits(
                scan,
                lookup_ppm=lookup_ppm,
                min_base_intensity=min_base_intensity,
                min_position_intensity=min_position_intensity,
                max_candidates_per_scan=max_candidates_per_scan,
            )
        )

    raw_groups = group_scan_hits(
        all_hits,
        group_width_da=group_width_da,
        rt_gap_split_min=rt_gap_split_min,
    )
    groups = filter_scan_hit_groups(
        raw_groups,
        min_scans_per_group=min_scans_per_group,
    )
    features = features_from_groups(groups)
    rows = detected_feature_rows(features, sample_stem=sample_stem)

    features_csv = write_detected_features_csv(
        output_dir / "detected_features.csv", rows
    )
    provenance_payload = build_provenance_payload(
        raw_path=raw_path,
        dll_dir=dll_dir,
        rt_min=rt_min,
        rt_max=rt_max,
        lookup_ppm=lookup_ppm,
        min_base_intensity=min_base_intensity,
        min_position_intensity=min_position_intensity,
        max_candidates_per_scan=max_candidates_per_scan,
        group_width_da=group_width_da,
        rt_gap_split_min=rt_gap_split_min,
        min_scans_per_group=min_scans_per_group,
        ms1_scan_count=len(scans),
        scan_hit_count=len(all_hits),
        raw_group_count=len(raw_groups),
        group_count=len(groups),
        feature_count=len(features),
    )
    provenance_json = output_dir / "proxy_provenance.json"
    provenance_json.write_text(
        json.dumps(provenance_payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return features_csv, provenance_json


def _validate_proxy_settings(
    *,
    sample_stem: str,
    rt_min: float,
    rt_max: float,
    lookup_ppm: float,
    min_base_intensity: float,
    min_position_intensity: float,
    max_candidates_per_scan: int,
    group_width_da: float,
    rt_gap_split_min: float,
    min_scans_per_group: int,
) -> None:
    if not sample_stem.strip():
        raise ValueError("sample_stem must be non-empty")
    _validate_rt_window(rt_min=rt_min, rt_max=rt_max)
    _validate_scan_lookup_settings(
        lookup_ppm=lookup_ppm,
        min_base_intensity=min_base_intensity,
        min_position_intensity=min_position_intensity,
        max_candidates_per_scan=max_candidates_per_scan,
    )
    _require_positive_finite(group_width_da, "group_width_da")
    _require_positive_finite(rt_gap_split_min, "rt_gap_split_min")
    _require_positive_int(min_scans_per_group, "min_scans_per_group")


def _validate_rt_window(*, rt_min: float, rt_max: float) -> None:
    parsed_rt_min = require_finite_real(rt_min, name="rt_min")
    parsed_rt_max = require_finite_real(rt_max, name="rt_max")
    if parsed_rt_min > parsed_rt_max:
        raise ValueError("rt_min must be <= rt_max")


def _validate_scan_lookup_settings(
    *,
    lookup_ppm: float,
    min_base_intensity: float,
    min_position_intensity: float,
    max_candidates_per_scan: int,
) -> None:
    _require_positive_finite(lookup_ppm, "lookup_ppm")
    _require_non_negative_finite(min_base_intensity, "min_base_intensity")
    _require_non_negative_finite(min_position_intensity, "min_position_intensity")
    _require_non_negative_int(max_candidates_per_scan, "max_candidates_per_scan")


def _require_positive_finite(value: float, name: str) -> None:
    if require_finite_real(value, name=name) <= 0:
        raise ValueError(f"{name} must be finite and > 0")


def _require_non_negative_finite(value: float, name: str) -> None:
    if require_finite_real(value, name=name) < 0:
        raise ValueError(f"{name} must be finite and >= 0")


def _require_positive_int(value: int, name: str) -> None:
    if require_integer(value, name=name) <= 0:
        raise ValueError(f"{name} must be an integer > 0")


def _require_non_negative_int(value: int, name: str) -> None:
    if require_integer(value, name=name) < 0:
        raise ValueError(f"{name} must be an integer >= 0")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True, help="Thermo RAW file.")
    parser.add_argument(
        "--dll-dir",
        type=Path,
        required=True,
        help="Directory containing Thermo RawFileReader DLLs.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--sample-stem",
        help="Detected Features CSV sample_stem. Defaults to the RAW file stem.",
    )
    parser.add_argument("--rt-min", type=float, default=0.0)
    parser.add_argument("--rt-max", type=float, default=999.0)
    parser.add_argument(
        "--lookup-ppm",
        type=float,
        default=_DEFAULT_LOOKUP_PPM,
        help=(
            "Proxy's scan-level lookup window. Defaults to the Pt matcher's "
            "real 20ppm gate; widen only for diagnostic exploration."
        ),
    )
    parser.add_argument("--min-base-intensity", type=float, default=0.0)
    parser.add_argument("--min-position-intensity", type=float, default=0.0)
    parser.add_argument("--max-candidates-per-scan", type=int, default=500)
    parser.add_argument("--group-width-da", type=float, default=0.005)
    parser.add_argument("--rt-gap-split-min", type=float, default=0.20)
    parser.add_argument(
        "--min-scans-per-group",
        type=int,
        default=_DEFAULT_MIN_SCANS_PER_GROUP,
        help=(
            "Require a scan-proxy Pt group to appear in at least this many "
            "MS1 scans within one RAW file before emitting pseudo-features; "
            "not a sample-count or cross-RAW requirement."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    raw_path = args.raw.resolve()
    dll_dir = args.dll_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not raw_path.is_file():
        print(f"{raw_path}: raw file does not exist", file=sys.stderr)
        return 2
    sample_stem = args.sample_stem or raw_path.stem

    try:
        features_csv, provenance_json = run_proxy(
            raw_path=raw_path,
            dll_dir=dll_dir,
            output_dir=output_dir,
            sample_stem=sample_stem,
            rt_min=args.rt_min,
            rt_max=args.rt_max,
            lookup_ppm=args.lookup_ppm,
            min_base_intensity=args.min_base_intensity,
            min_position_intensity=args.min_position_intensity,
            max_candidates_per_scan=args.max_candidates_per_scan,
            group_width_da=args.group_width_da,
            rt_gap_split_min=args.rt_gap_split_min,
            min_scans_per_group=args.min_scans_per_group,
        )
    except (MS1ScanReadError, RawReaderError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"diagnostic_only detected features CSV: {features_csv}")
    print(f"proxy provenance JSON: {provenance_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
