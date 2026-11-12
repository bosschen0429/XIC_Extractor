"""RAW-to-delta-mass orchestration and CID-NL(dR) composite admission."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from xic_extractor.configuration.mass_recalibration import (
    serialize_mass_recalibration_result,
)
from xic_extractor.discovery.delta_mass_csv import (
    DeltaMassDiscoveryOutputs,
    write_delta_mass_outputs,
)
from xic_extractor.discovery.delta_mass_patterns import (
    DeltaMassCandidate,
    complete_delta_mass_candidates,
    prepare_delta_mass_candidates,
)
from xic_extractor.discovery.delta_mass_strategy import DeltaMassDiscoveryStrategy
from xic_extractor.discovery.detected_features import write_detected_features_csv
from xic_extractor.discovery.local_ms1_features import (
    LocalMs1FeatureSettings,
    build_local_ms1_feature_table,
)
from xic_extractor.discovery.sample_process_execution import (
    raise_if_discovery_cancelled,
)
from xic_extractor.mass_accuracy import absolute_ppm_error
from xic_extractor.raw_reader import open_raw
from xic_extractor.tabular_io import read_delimited_rows

_ALLOWED_CID_NL_TAG = "DNA_dR"
_JOINABLE_STATUSES = frozenset({"admitted"})
_CID_NL_REQUIRED_COLUMNS = (
    "peak_id",
    "sample_stem",
    "matched_tag_names",
    "status",
    "peak_mz",
)
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
    cid_nl_peaks_csv: Path | None = None,
    raw_paths: Sequence[Path] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> DeltaMassDiscoveryOutputs:
    """Detect features per RAW, acquire one batch, then write diagnostic results.

    ``raw_paths`` limits the run to selected samples of ``raw_dir``; omitted,
    every RAW in the folder is analyzed. A cancellation stops before the next
    RAW and before any output is written.
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
    if strategy.cid_nl_composite and cid_nl_peaks_csv is None:
        raise ValueError("composite preset requires --cid-nl-peaks-csv")
    cid_rows = (
        _load_cid_nl_peak_rows(cid_nl_peaks_csv)
        if cid_nl_peaks_csv is not None and strategy.cid_nl_composite
        else ()
    )
    candidates: list[DeltaMassCandidate] = []
    samples = []
    for done, raw_path in enumerate(raw_paths):
        raise_if_discovery_cancelled(should_cancel)
        if progress_callback is not None:
            progress_callback(done, len(raw_paths), raw_path.stem)
        started = time.perf_counter()
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
            features, summary, _ = build_local_ms1_feature_table(
                index,
                sample_stem=raw_path.stem,
                raw_path=str(raw_path),
                settings=detector,
            )
            sample_dir = output_dir / raw_path.stem
            sample_dir.mkdir(parents=True, exist_ok=True)
            write_detected_features_csv(sample_dir / "detected_features.csv", features)
            prepared = prepare_delta_mass_candidates(
                features,
                strategy.settings,
                scan_interval_sec=interval,
            )
            acquisition_started = time.perf_counter()
            traces = (
                reader.extract_xic_many(prepared.requests) if prepared.requests else ()
            )
            acquisition_seconds = time.perf_counter() - acquisition_started
            completed = complete_delta_mass_candidates(
                prepared, traces, strategy.settings
            )
        if strategy.cid_nl_composite:
            completed = _join_cid_nl(completed, cid_rows)
        candidates.extend(completed)
        counts = dict(prepared.counts)
        for layer in (3, 4, 5):
            counts[f"layer_{layer}_rejected"] = sum(
                c.rejected_at_layer == layer for c in prepared.candidates
            )
        for layer in (3, 4, 5):
            counts[f"layer_{layer}_passed"] = sum(
                c.rejected_at_layer is None or c.rejected_at_layer > layer
                for c in prepared.candidates
            )
        samples.append(
            {
                "sample_stem": raw_path.stem,
                "feature_count": len(features),
                **counts,
                "candidate_count": sum(c.status == "candidate" for c in completed),
                "layer_4_xic_requests": len(prepared.requests),
                "layer_4_seconds": acquisition_seconds,
                "median_ms1_scan_interval_sec": interval,
                "mass_recalibration": serialize_mass_recalibration_result(mass_result),
                "gaussian_window_points": detector.gaussian_window_points,
                "detector_seconds": summary.wall_time_sec,
                "total_seconds": time.perf_counter() - started,
            }
        )
    sidecar = {
        "preset": strategy.label,
        "method": {
            **asdict(strategy.settings),
            "mass_recalibration": strategy.mass_recalibration.value,
            "height_ratio_policy": "evidence_only",
            "detector": {
                "smoothing_window_sec": strategy.smoothing_window_sec,
                "feature_link_ppm": strategy.settings.mz_tolerance_ppm,
                "spectrum_mode": "vendor_centroid",
            },
        },
        "inputs": {
            "raw_dir": str(raw_dir),
            "raw_files": [p.name for p in raw_paths],
            "cid_nl_peaks_csv": str(cid_nl_peaks_csv) if cid_nl_peaks_csv else None,
        },
        "samples": samples,
        "status": "diagnostic_only",
    }
    raise_if_discovery_cancelled(should_cancel)
    return write_delta_mass_outputs(
        output_dir, tuple(candidates), sidecar, strategy.settings
    )


def _join_cid_nl(
    candidates: tuple[DeltaMassCandidate, ...], rows: tuple[_CidNlPeakRow, ...]
) -> tuple[DeltaMassCandidate, ...]:
    """Join detector-local bases by the existing bounded m/z/RT predicate.

    Only admitted dR peaks join. An unmatched base records why, checked in
    order: a non-admitted dR peak inside the join window (``not_admitted``,
    with its status), an admitted dR peak of the same ion outside the RT
    window (``outside_rt_tolerance``), otherwise ``no_cid_nl_peak``.
    """
    dr_rows_by_sample: dict[str, list[_CidNlPeakRow]] = {}
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


@dataclass(frozen=True)
class _CidNlPeakRow:
    """The fields of one ``discovery_peaks.csv`` row the dR base join reads.

    ``peak_id`` is required because composite output exposes the matched
    CID-NL peak identity. ``peak_mz`` is the peak's ion mass, the one Alignment
    extracts. The representative event's precursor is one MS2 isolation and can
    sit tens of ppm away on a dense peak.
    """

    sample_stem: str
    peak_id: str
    matched_tag_names: tuple[str, ...]
    status: str
    peak_mz: float | None
    ms1_apex_rt: float | None
    evidence_score: float | None


def _load_cid_nl_peak_rows(path: Path) -> tuple[_CidNlPeakRow, ...]:
    """Load the measured CID-NL peaks a delta-mass base may join.

    Every row must carry ``peak_id``, ``sample_stem`` and ``status``.
    Rows of every status load; only admitted peaks join, and the others only
    explain why a base did not match. A measured peak without an attached NL
    event does not supply dR support.
    """

    rows = read_delimited_rows(
        path,
        required_columns=_CID_NL_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )
    parsed: list[_CidNlPeakRow] = []
    for row_index, row in enumerate(rows):
        label = f"{path} row {row_index}"
        peak_id = _required_text(row.get("peak_id"), "peak_id", label)
        sample_stem = _required_text(row.get("sample_stem"), "sample_stem", label)
        for field, identity in (("peak_id", peak_id), ("sample_stem", sample_stem)):
            _reject_ambiguous_formula_escaped_identity(
                identity, field=field, row_label=label
            )
        status = _required_text(row.get("status"), "status", label)
        parsed.append(
            _CidNlPeakRow(
                sample_stem=sample_stem,
                peak_id=peak_id,
                matched_tag_names=tuple(
                    tag
                    for tag in (row.get("matched_tag_names") or "").split(";")
                    if tag
                ),
                status=status,
                peak_mz=_optional_finite_float(row.get("peak_mz"), "peak_mz", label),
                ms1_apex_rt=_optional_finite_float(
                    row.get("ms1_apex_rt"), "ms1_apex_rt", label
                ),
                evidence_score=_optional_finite_float(
                    row.get("evidence_score"), "evidence_score", label
                ),
            )
        )
    seen: set[str] = set()
    duplicates: set[str] = set()
    for parsed_row in parsed:
        if parsed_row.peak_id in seen:
            duplicates.add(parsed_row.peak_id)
        seen.add(parsed_row.peak_id)
    if duplicates:
        raise ValueError(
            f"{path}: duplicate CID-NL peak_id values: " + ", ".join(sorted(duplicates))
        )
    return tuple(parsed)


def _reject_ambiguous_formula_escaped_identity(
    value: str,
    *,
    field: str,
    row_label: str,
) -> None:
    """Fail closed when an identity may have been Excel-formula escaped.

    The legacy Discovery CSV writer prepends an apostrophe to strings beginning
    with ``=``, ``+``, ``-``, or ``@``. That serialized form is indistinguishable
    from an original identifier that genuinely began with the apostrophe, so a
    composite identity join must not silently unescape or compare it literally.
    """

    if len(value) >= 2 and value[0] == "'" and value[1] in "=+-@":
        raise ValueError(
            f"{row_label} has ambiguous Excel formula-escaped machine identity "
            f"in {field!r}: {value!r}"
        )


def _required_text(value: str | None, field: str, row_label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{row_label} is missing required field {field!r}")
    return text


def _optional_text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _optional_finite_float(
    value: str | None, field: str, row_label: str
) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError as error:
        raise ValueError(
            f"{row_label} has non-numeric optional field {field!r}: {value!r}"
        ) from error
    if not math.isfinite(parsed):
        raise ValueError(
            f"{row_label} has non-finite optional field {field!r}: {value!r}"
        )
    return parsed


def _ppm_error(candidate: DeltaMassCandidate, peak_mz: float) -> float:
    return absolute_ppm_error(peak_mz, candidate.base_mz)


def _mz_matches(candidate: DeltaMassCandidate, row: _CidNlPeakRow) -> bool:
    return (
        row.peak_mz is not None
        and _ppm_error(candidate, row.peak_mz) <= _CID_NL_MZ_TOLERANCE_PPM
    )


def _fallback_matches(candidate: DeltaMassCandidate, row: _CidNlPeakRow) -> bool:
    if row.ms1_apex_rt is None or not _mz_matches(candidate, row):
        return False
    rt_delta_sec = abs(row.ms1_apex_rt - candidate.base_rt_apex_min) * 60.0
    return rt_delta_sec <= _CID_NL_RT_TOLERANCE_SEC


def _best_fallback_match(
    candidate: DeltaMassCandidate, matches: list[_CidNlPeakRow]
) -> _CidNlPeakRow:
    """Tie-break for multiple m/z/RT fallback matches (plan lines 836-838):
    smallest absolute RT delta, then smallest absolute m/z ppm, then highest
    ``evidence_score``."""

    def _key(row: _CidNlPeakRow) -> tuple[float, float, float, str]:
        assert row.ms1_apex_rt is not None
        assert row.peak_mz is not None
        rt_delta_sec = abs(row.ms1_apex_rt - candidate.base_rt_apex_min) * 60.0
        ppm_error = _ppm_error(candidate, row.peak_mz)
        evidence_score = row.evidence_score if row.evidence_score is not None else 0.0
        return (
            rt_delta_sec,
            ppm_error,
            -evidence_score,
            row.peak_id,
        )

    return min(matches, key=_key)
