"""RAW-to-delta-mass orchestration and CID-NL(dR) composite admission."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path

import numpy as np

from xic_extractor.discovery.delta_mass_patterns import (
    DeltaMassCandidate,
    complete_delta_mass_candidates,
    prepare_delta_mass_candidates,
)
from xic_extractor.discovery.delta_mass_publication import (
    DeltaMassDiscoveryOutputs,
    write_delta_mass_outputs,
)
from xic_extractor.discovery.delta_mass_strategy import DeltaMassDiscoveryStrategy
from xic_extractor.discovery.local_ms1_features import (
    LocalMs1FeatureSettings,
    build_local_ms1_feature_table,
)
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.sample_process_execution import (
    raise_if_discovery_cancelled,
)
from xic_extractor.discovery.store import DiscoverySample, open_discovery, read_peaks
from xic_extractor.mass_accuracy import absolute_ppm_error
from xic_extractor.raw_reader import open_raw

_ALLOWED_CID_NL_TAG = "DNA_dR"
_JOINABLE_STATUSES = frozenset({"admitted"})
_CID_NL_MZ_TOLERANCE_PPM = 20.0
_CID_NL_RT_TOLERANCE_SEC = 30.0


def detector_settings_for_interval(
    strategy: DeltaMassDiscoveryStrategy, interval_sec: float
) -> LocalMs1FeatureSettings:
    """Convert preset seconds to the nearest positive odd smoothing window."""
    if not math.isfinite(interval_sec) or interval_sec <= 0:
        raise ValueError("at least two distinct MS1 scan times are required")
    points = max(
        1, 2 * round((strategy.smoothing_window_sec / interval_sec - 1) / 2) + 1
    )
    return LocalMs1FeatureSettings(
        gaussian_window_points=points,
        feature_link_ppm=strategy.settings.mz_tolerance_ppm,
    )


def run_delta_mass_discovery(
    *,
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    strategy: DeltaMassDiscoveryStrategy,
    cid_nl_store: Path | None = None,
    raw_paths: Sequence[Path] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> DeltaMassDiscoveryOutputs:
    """Detect features per RAW, acquire one batch, then write diagnostic results.

    ``raw_paths`` limits the run to selected samples of ``raw_dir``; omitted,
    every RAW in the folder is analyzed. A cancellation stops before the next
    RAW and before any output is written.

    A composite method joins the dR peaks of ``cid_nl_store``, the CID-NL
    Discovery of the same RAW.
    """
    raw_paths = sorted(
        raw_paths
        if raw_paths is not None
        else (
            p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() == ".raw"
        )
    )
    if not raw_paths:
        raise ValueError(f"no RAW files in {raw_dir}")
    if strategy.cid_nl_composite:
        if cid_nl_store is None:
            raise ValueError("composite preset requires --cid-nl-discovery")
        with open_discovery(cid_nl_store) as db:
            cid_rows = tuple(peak for _row_id, peak in read_peaks(db))
    else:
        cid_rows = ()
    candidates: list[DeltaMassCandidate] = []
    samples: list[DiscoverySample] = []
    feature_bounds: dict[tuple[str, str], tuple[float, float | None, float | None]] = {}
    for done, raw_path in enumerate(raw_paths):
        raise_if_discovery_cancelled(should_cancel)
        if progress_callback is not None:
            progress_callback(done, len(raw_paths), raw_path.stem)
        with open_raw(
            raw_path, dll_dir=dll_dir, mass_recalibration=strategy.mass_recalibration
        ) as reader:
            mass_result = reader.mass_recalibration_result
            index = reader.read_ms1_scan_index(centroid=True)
            intervals = np.diff([scan.rt for scan in index]) * 60
            interval = (
                float(np.median(intervals[intervals > 0]))
                if np.any(intervals > 0)
                else 0.0
            )
            detector = detector_settings_for_interval(strategy, interval)
            features = build_local_ms1_feature_table(
                index,
                sample_stem=raw_path.stem,
                settings=detector,
            )
            feature_bounds.update(
                ((raw_path.stem, f.feature_id), (f.mz, f.rt_start_min, f.rt_end_min))
                for f in features
            )
            prepared = prepare_delta_mass_candidates(
                features,
                strategy.settings,
                scan_interval_sec=interval,
            )
            traces = (
                reader.extract_xic_many(prepared.requests) if prepared.requests else ()
            )
            completed = complete_delta_mass_candidates(
                prepared, traces, strategy.settings
            )
        if strategy.cid_nl_composite:
            completed = _join_cid_nl(completed, cid_rows)
        candidates.extend(completed)
        samples.append(DiscoverySample(raw_path.stem, raw_path, None, mass_result))
    method = {
        **asdict(strategy.settings),
        "mass_recalibration": strategy.mass_recalibration.value,
        "height_ratio_policy": "evidence_only",
        "detector": {
            "smoothing_window_sec": strategy.smoothing_window_sec,
            "feature_link_ppm": strategy.settings.mz_tolerance_ppm,
            "spectrum_mode": "vendor_centroid",
        },
    }
    raise_if_discovery_cancelled(should_cancel)
    return write_delta_mass_outputs(
        output_dir,
        method_label=strategy.label,
        method=method,
        samples=samples,
        candidates=candidates,
        features=feature_bounds,
    )


def _join_cid_nl(
    candidates: tuple[DeltaMassCandidate, ...], rows: tuple[DiscoveryPeak, ...]
) -> tuple[DeltaMassCandidate, ...]:
    """Join detector-local bases by the existing bounded m/z/RT predicate.

    Only admitted dR peaks join. An unmatched base records why, checked in
    order: a non-admitted dR peak inside the join window (``not_admitted``,
    with its status), an admitted dR peak of the same ion outside the RT
    window (``outside_rt_tolerance``), otherwise ``no_cid_nl_peak``.
    """
    dr_rows_by_sample: dict[str, list[DiscoveryPeak]] = {}
    for row in rows:
        if _ALLOWED_CID_NL_TAG in row.matched_tag_names:
            dr_rows_by_sample.setdefault(row.sample_stem, []).append(row)
    joined = []
    for candidate in candidates:
        sample_rows = dr_rows_by_sample.get(candidate.sample_stem, ())
        admitted = [row for row in sample_rows if row.status in _JOINABLE_STATUSES]
        matches = [row for row in admitted if _fallback_matches(candidate, row)]
        if matches:
            match = _best_fallback_match(candidate, matches)
            candidate.cid_nl_base_status = "matched"
            candidate.cid_nl_base_peak_id = match.peak_id
            candidate.cid_nl_base_join_mode = "mz_rt_fallback"
            joined.append(candidate)
            continue
        candidate.cid_nl_base_status = "unmatched"
        in_window_statuses = sorted(
            {
                row.status
                for row in sample_rows
                if row.status not in _JOINABLE_STATUSES
                and _fallback_matches(candidate, row)
            }
        )
        if in_window_statuses:
            candidate.cid_nl_base_unmatched_reason = "not_admitted"
            candidate.cid_nl_base_peak_status = ";".join(in_window_statuses)
        elif any(_mz_matches(candidate, row) for row in admitted):
            candidate.cid_nl_base_unmatched_reason = "outside_rt_tolerance"
        else:
            candidate.cid_nl_base_unmatched_reason = "no_cid_nl_peak"
        joined.append(candidate)
    return tuple(joined)


def _ppm_error(candidate: DeltaMassCandidate, peak_mz: float) -> float:
    return absolute_ppm_error(peak_mz, candidate.base_mz)


def _mz_matches(candidate: DeltaMassCandidate, row: DiscoveryPeak) -> bool:
    return _ppm_error(candidate, row.peak_mz) <= _CID_NL_MZ_TOLERANCE_PPM


def _fallback_matches(candidate: DeltaMassCandidate, row: DiscoveryPeak) -> bool:
    if not _mz_matches(candidate, row):
        return False
    rt_delta_sec = abs(row.ms1_apex_rt - candidate.base_rt_apex_min) * 60.0
    return rt_delta_sec <= _CID_NL_RT_TOLERANCE_SEC


def _best_fallback_match(
    candidate: DeltaMassCandidate, matches: list[DiscoveryPeak]
) -> DiscoveryPeak:
    """Tie-break for multiple m/z/RT fallback matches (plan lines 836-838):
    smallest absolute RT delta, then smallest absolute m/z ppm, then highest
    ``evidence_score``."""

    def _key(row: DiscoveryPeak) -> tuple[float, float, float, str]:
        rt_delta_sec = abs(row.ms1_apex_rt - candidate.base_rt_apex_min) * 60.0
        return (
            rt_delta_sec,
            _ppm_error(candidate, row.peak_mz),
            -row.evidence_score,
            row.peak_id,
        )

    return min(matches, key=_key)
