"""Acquire selected Delta Mass review windows without re-running Discovery."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from xic_extractor.diagnostics.delta_mass_review_data import (
    DeltaMassReview,
    ReviewCandidate,
    ReviewPosition,
    ReviewReadError,
    ReviewSpectrum,
)
from xic_extractor.raw_reader import open_raw
from xic_extractor.tabular_io import read_delimited_rows
from xic_extractor.xic_models import XICRequest

_CANDIDATE_FIELDS = (
    "candidate_id",
    "sample_stem",
    "method_label",
    "pattern_id",
    "status",
    "reject_reason",
    "base_mz",
    "base_rt_apex_min",
)
_POSITION_FIELDS = (
    "candidate_id",
    "sample_stem",
    "position_label",
    "delta_da",
    "expected_mz",
    "observed_mz",
    "apex_rt_min",
    "height",
    "height_ratio_to_base",
    "shape_r",
    "ratio_status",
    "position_status",
)


def acquire_delta_mass_review(
    discovery_directory: Path,
    *,
    candidate_ids: Sequence[str],
    raw_directory: Path,
    dll_directory: Path,
    padding_min: float = 2.0,
) -> DeltaMassReview:
    """Read each selected RAW once; batch XICs and merge overlapping MS2 windows.

    All MS2 in the requested RT windows are retained, irrespective of precursor.
    Temporal proximity does not attribute a spectrum to a pattern position.
    """
    if not math.isfinite(padding_min) or padding_min <= 0:
        raise ValueError("padding_min must be positive and finite")
    selected = _read_selected_candidates(
        discovery_directory, candidate_ids, padding_min
    )
    by_sample: dict[str, list[ReviewCandidate]] = defaultdict(list)
    for candidate in selected:
        by_sample[candidate.sample_stem].append(candidate)
    raw_paths = {
        p.stem: p
        for p in raw_directory.iterdir()
        if p.is_file() and p.suffix.lower() == ".raw"
    }
    missing = by_sample.keys() - raw_paths.keys()
    if missing:
        raise ValueError(f"No RAW with the exact saved sample name: {sorted(missing)}")

    acquired: dict[str, ReviewCandidate] = {}
    for sample, candidates in by_sample.items():
        raw_path = raw_paths[sample]
        with open_raw(raw_path, dll_dir=dll_directory) as reader:
            start, end = reader.acquisition_rt_bounds()
            candidates = [
                replace(
                    c, rt_window=(max(start, c.rt_window[0]), min(end, c.rt_window[1]))
                )
                for c in candidates
            ]
            if any(c.rt_window[0] >= c.rt_window[1] for c in candidates):
                raise ValueError(
                    f"Saved candidate RT is outside RAW acquisition: {sample}"
                )
            requests = tuple(
                dict.fromkeys(
                    XICRequest(p.query_mz, *c.rt_window, c.xic_ppm)
                    for c in candidates
                    for p in c.positions
                )
            )
            traces = dict(zip(requests, reader.extract_xic_many(requests), strict=True))
            spectra: dict[int, ReviewSpectrum] = {}
            errors: list[ReviewReadError] = []
            for lo, hi in _merged_windows([c.rt_window for c in candidates]):
                for event in reader.iter_ms2_scans(lo, hi):
                    if event.scan is None:
                        errors.append(
                            ReviewReadError(
                                event.scan_number,
                                (lo, hi),
                                event.parse_error or "MS2 unreadable",
                            )
                        )
                        continue
                    scan = event.scan
                    # The vendor RT-to-scan lookup can include the nearest endpoint.
                    if not lo <= scan.rt <= hi:
                        continue
                    spectra[scan.scan_number] = ReviewSpectrum(
                        scan.scan_number,
                        scan.rt,
                        scan.precursor_mz,
                        scan.precursor_metadata,
                        tuple(float(v) for v in scan.masses),
                        tuple(float(v) for v in scan.intensities),
                    )
            for candidate in candidates:
                positions = []
                for position in candidate.positions:
                    trace = traces[
                        XICRequest(
                            position.query_mz, *candidate.rt_window, candidate.xic_ppm
                        )
                    ]
                    positions.append(
                        replace(
                            position,
                            raw_rt=tuple(float(v) for v in trace.rt),
                            raw_intensity=tuple(float(v) for v in trace.intensity),
                        )
                    )
                lo, hi = candidate.rt_window
                acquired[candidate.candidate_id] = replace(
                    candidate,
                    raw_file=str(raw_path.resolve()),
                    positions=tuple(positions),
                    spectra=tuple(
                        spectrum
                        for _, spectrum in sorted(spectra.items())
                        if lo <= spectrum.rt_min <= hi
                    ),
                    ms2_read_errors=tuple(
                        error
                        for error in errors
                        if error.requested_rt_window[0] <= hi
                        and error.requested_rt_window[1] >= lo
                    ),
                )
    return DeltaMassReview(
        str(discovery_directory.resolve()),
        tuple(acquired[c.candidate_id] for c in selected),
    )


def _read_selected_candidates(
    directory: Path, candidate_ids: Sequence[str], padding_min: float
) -> tuple[ReviewCandidate, ...]:
    if not candidate_ids:
        raise ValueError(
            "Select at least one candidate ID; review never scans all candidates"
        )
    rows = read_delimited_rows(
        directory / "delta_mass_candidates.csv",
        required_columns=_CANDIDATE_FIELDS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    positions = read_delimited_rows(
        directory / "delta_mass_positions.csv",
        required_columns=_POSITION_FIELDS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    sidecar = json.loads(
        (directory / "delta_mass_sidecar.json").read_text(encoding="utf-8")
    )
    method = sidecar.get("method", {}) if isinstance(sidecar, dict) else {}
    if not isinstance(method, dict):
        raise ValueError("Delta Mass sidecar method must be an object")
    try:
        ppm = float(method["mz_tolerance_ppm"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "Use current Delta Mass outputs with method.mz_tolerance_ppm in the sidecar"
        ) from exc
    if not math.isfinite(ppm) or ppm <= 0:
        raise ValueError("Saved m/z tolerance must be positive and finite")
    selected = []
    for candidate_id in dict.fromkeys(candidate_ids):
        matches = [r for r in rows if r["candidate_id"] == candidate_id]
        if len(matches) != 1:
            raise ValueError(
                f"Candidate ID must identify exactly one saved row: {candidate_id}"
            )
        row = matches[0]
        matched_positions = [
            p
            for p in positions
            if p["candidate_id"] == candidate_id
            and p["sample_stem"] == row["sample_stem"]
        ]
        labels = [p["position_label"] for p in matched_positions]
        if (
            not labels
            or any(not label for label in labels)
            or len(set(labels)) != len(labels)
        ):
            raise ValueError(f"Missing or duplicate saved positions: {candidate_id}")
        parsed = tuple(
            ReviewPosition(
                label=p["position_label"],
                delta_da=_required_number(p, "delta_da"),
                expected_mz=_required_number(p, "expected_mz"),
                observed_mz=_saved_number(p, "observed_mz"),
                apex_rt_min=_saved_number(p, "apex_rt_min"),
                height=_saved_number(p, "height"),
                height_ratio_to_base=_saved_number(p, "height_ratio_to_base"),
                shape_r=_saved_number(p, "shape_r"),
                ratio_status=p["ratio_status"],
                position_status=p["position_status"],
            )
            for p in matched_positions
        )
        base_mz = _required_number(row, "base_mz")
        base_rt = _required_number(row, "base_rt_apex_min")
        if base_mz <= 0 or base_rt < 0 or any(p.query_mz <= 0 for p in parsed):
            raise ValueError(f"Invalid saved m/z or RT: {candidate_id}")
        apices = [
            base_rt,
            *(p.apex_rt_min for p in parsed if p.apex_rt_min is not None),
        ]
        selected.append(
            ReviewCandidate(
                candidate_id=candidate_id,
                sample_stem=row["sample_stem"],
                method_label=row["method_label"],
                pattern_id=row["pattern_id"],
                status=row["status"],
                reject_reason=row["reject_reason"],
                base_mz=base_mz,
                base_rt_apex_min=base_rt,
                ratio_reference_label=method.get("ratio_reference_position")
                or min(parsed, key=lambda p: p.delta_da).label,
                shape_reference_label=method.get("shape_reference_position")
                or min(parsed, key=lambda p: p.delta_da).label,
                positions=parsed,
                raw_file="",
                xic_ppm=ppm,
                rt_window=(
                    max(0.0, min(apices) - padding_min),
                    max(apices) + padding_min,
                ),
            )
        )
    return tuple(selected)


def _saved_number(row: dict[str, str], field: str) -> float | None:
    value = row[field]
    if value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"Saved {field} must be finite")
    return result


def _required_number(row: dict[str, str], field: str) -> float:
    result = _saved_number(row, field)
    if result is None:
        raise ValueError(f"Saved {field} is required")
    return result


def _merged_windows(
    windows: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for lo, hi in sorted(windows):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged
