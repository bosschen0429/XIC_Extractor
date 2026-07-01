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
- No real trace-quality assessment -- ``trace_quality``/``shape_score`` are
  fixed, conservative placeholders (see ``_TRACE_QUALITY_PLACEHOLDER`` /
  ``_SHAPE_SCORE_PLACEHOLDER`` below for the reasoning) precisely so this
  proxy cannot manufacture false "supported" signal for the real Pt matcher's
  local-signal-support gate.
- Its own internal m/z lookup window (``--lookup-ppm``, default 50 ppm) is
  deliberately looser than the Pt pattern's real 20 ppm / 30 sec Global
  Constraint gates. The real gates are re-applied later by
  ``xic_extractor.discovery.delta_mass_patterns.match_delta_mass_pattern``;
  this proxy only needs to emit plausible candidate rows for that matcher to
  filter, not to pre-filter with product-grade precision itself.

Reuse notes: this module reuses ``xic_extractor.raw_reader.open_raw`` /
``RawFileHandle`` for RAW access (the same low-level Thermo COM calls already
used by ``RawFileHandle.iter_ms2_scans`` -- ``GetFilterForScanNumber``,
``GetSimplifiedScan``, ``RetentionTimeFromScanNumber`` -- applied here to MS1
scans instead of MS2 scans) and
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
from xic_extractor.raw_reader import RawFileHandle, RawReaderError, open_raw
from xic_extractor.tabular_io import write_delimited_rows

PROXY_SOURCE_LABEL = "raw_scan_proxy_v1"
PROVENANCE_SCHEMA_VERSION = "pt_pattern_scan_proxy_provenance.v1"

# Deliberately conservative, fixed placeholders -- see module docstring. A
# scan-level proxy has no real chromatographic trace to assess, so it must
# not report a quality signal strong enough to pass the real Pt matcher's
# local-signal-support gate (`_LOCAL_SIGNAL_SUPPORT_QUALITIES` /
# `_LOCAL_SIGNAL_SUPPORT_SHAPE_SCORE_MIN` in delta_mass_patterns.py) on its
# own say-so. "REVIEW" is not one of the matcher's accepted "supported"
# qualities (GOOD/OK/PASS) and 0.0 is below the 0.80 shape_score floor, so
# every proxy row requires a human (or a smarter downstream detector) to
# actually confirm signal quality before a Pt candidate is trusted.
_TRACE_QUALITY_PLACEHOLDER = "REVIEW"
_SHAPE_SCORE_PLACEHOLDER = 0.0

_MS1_ORDER_LABEL = "Ms"

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
    handle: RawFileHandle,
    *,
    rt_min: float,
    rt_max: float,
) -> Iterator[Ms1ScanRecord]:
    """Iterate MS1 (``MSOrder == Ms``) scans in ``[rt_min, rt_max]``.

    Mirrors ``RawFileHandle.iter_ms2_scans``'s per-scan Thermo COM call
    sequence (``GetFilterForScanNumber`` -> filter check ->
    ``GetSimplifiedScan`` -> masses/intensities), applied to MS1 scans
    instead of MS2 scans. Malformed scans are skipped rather than raising,
    since this is a best-effort diagnostic scan, not a product pipeline.
    """

    raw_file = handle._raw_file  # noqa: SLF001 -- same access pattern as
    # xic_extractor.alignment.ms1_index_source._raw_file(); RawFileHandle has
    # no public MS1 scan iterator, only iter_ms2_scans and XIC extraction.
    start_scan = int(raw_file.ScanNumberFromRetentionTime(rt_min))
    end_scan = int(raw_file.ScanNumberFromRetentionTime(rt_max))
    for scan_number in range(start_scan, end_scan + 1):
        try:
            filter_obj = raw_file.GetFilterForScanNumber(scan_number)
            if str(getattr(filter_obj, "MSOrder", "")) != _MS1_ORDER_LABEL:
                continue
            scan_rt = float(raw_file.RetentionTimeFromScanNumber(scan_number))
            scan_data = raw_file.GetSimplifiedScan(scan_number)
            masses = np.asarray(getattr(scan_data, "Masses", []), dtype=float)
            intensities = np.asarray(
                getattr(scan_data, "Intensities", []), dtype=float
            )
            if masses.ndim != 1 or intensities.ndim != 1:
                continue
            if masses.shape != intensities.shape:
                continue
            if masses.size > 1 and not np.all(masses[:-1] <= masses[1:]):
                order = np.argsort(masses)
                masses = masses[order]
                intensities = intensities[order]
            yield Ms1ScanRecord(
                scan_number=scan_number,
                rt_min=scan_rt,
                masses=masses,
                intensities=intensities,
            )
        except Exception:  # noqa: BLE001 -- best-effort diagnostic scan;
            # a single malformed scan must not abort the whole proxy run.
            continue


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
    ms1_scan_count: int,
    scan_hit_count: int,
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
                "This is the proxy's own scan-level lookup window, looser "
                "than the matcher's real mz_tolerance_ppm above by design -- "
                "the Pt matcher re-validates every candidate against the "
                "real 20ppm/30sec Global Constraint gates."
            ),
            "min_base_intensity": min_base_intensity,
            "min_position_intensity": min_position_intensity,
            "max_candidates_per_scan": max_candidates_per_scan,
            "group_width_da": group_width_da,
            "rt_gap_split_min": rt_gap_split_min,
        },
        "trace_quality_placeholder": _TRACE_QUALITY_PLACEHOLDER,
        "shape_score_placeholder": _SHAPE_SCORE_PLACEHOLDER,
        "rt_window_min": {"rt_min": rt_min, "rt_max": rt_max},
        "counts": {
            "ms1_scan_count": ms1_scan_count,
            "scan_hit_count": scan_hit_count,
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
    lookup_ppm: float = 50.0,
    min_base_intensity: float = 0.0,
    min_position_intensity: float = 0.0,
    max_candidates_per_scan: int = 500,
    group_width_da: float = 0.005,
    rt_gap_split_min: float = 0.20,
) -> tuple[Path, Path]:
    """Run the scan-level Pt pattern proxy end-to-end against a real RAW file.

    Writes ``detected_features.csv`` and ``proxy_provenance.json`` under
    ``output_dir`` and returns their paths. Requires a real Thermo DLL and RAW
    file; the smoke test in ``tests/test_pt_pattern_scan_proxy.py`` instead
    exercises ``detected_feature_rows`` / ``write_detected_features_csv``
    directly against a fake ``Ms1ScanRecord`` sequence, without opening a RAW
    file at all.
    """

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

    groups = group_scan_hits(
        all_hits,
        group_width_da=group_width_da,
        rt_gap_split_min=rt_gap_split_min,
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
        ms1_scan_count=len(scans),
        scan_hit_count=len(all_hits),
        group_count=len(groups),
        feature_count=len(features),
    )
    provenance_json = output_dir / "proxy_provenance.json"
    provenance_json.write_text(
        json.dumps(provenance_payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return features_csv, provenance_json


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
        default=50.0,
        help=(
            "Proxy's own scan-level lookup window (looser than the Pt "
            "matcher's real 20ppm gate by design; see module docstring)."
        ),
    )
    parser.add_argument("--min-base-intensity", type=float, default=0.0)
    parser.add_argument("--min-position-intensity", type=float, default=0.0)
    parser.add_argument("--max-candidates-per-scan", type=int, default=500)
    parser.add_argument("--group-width-da", type=float, default=0.005)
    parser.add_argument("--rt-gap-split-min", type=float, default=0.20)
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
        )
    except RawReaderError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"diagnostic_only detected features CSV: {features_csv}")
    print(f"proxy provenance JSON: {provenance_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
