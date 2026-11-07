"""Acquire selected Delta Mass review windows without re-running Discovery."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import fields, replace
from pathlib import Path

from xic_extractor.configuration.cid_nl_profile import CidNlMassPolicy
from xic_extractor.diagnostics.delta_mass_nl_review import assess_review_neutral_losses
from xic_extractor.diagnostics.delta_mass_review_data import (
    DeltaMassReview,
    ReviewCandidate,
    ReviewNlMethod,
    ReviewPosition,
    ReviewReadError,
    ReviewSpectrum,
)
from xic_extractor.discovery.detected_features import load_detected_features_csv
from xic_extractor.discovery.models import DiscoverySeed
from xic_extractor.discovery.ms2_seeds import discovery_seeds_from_scan
from xic_extractor.discovery.strict_seed_receipts import read_parent_ms1_observations
from xic_extractor.evidence.pt_envelope import EnvelopeTrace, compare_pt_envelope
from xic_extractor.precursor_evidence import PrecursorMassScope
from xic_extractor.presets.apply import apply_to_discovery
from xic_extractor.presets.loader import load_preset
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
    "position_status",
)


def acquire_delta_mass_review(
    discovery_directory: Path,
    *,
    candidate_ids: Sequence[str] | None,
    raw_directory: Path,
    dll_directory: Path,
    padding_min: float = 2.0,
    nl_preset: str | None = None,
) -> DeltaMassReview:
    """Read each selected RAW once; batch XICs and merge overlapping MS2 windows.

    Without an NL preset only XICs are acquired. MS2 is retained solely as
    evidence for the unchanged candidate-level NL assessment.
    """
    if not math.isfinite(padding_min) or padding_min <= 0:
        raise ValueError("padding_min must be positive and finite")
    selected = _read_selected_candidates(
        discovery_directory, candidate_ids, padding_min
    )
    nl_settings = apply_to_discovery(load_preset(nl_preset)) if nl_preset else None
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
        feature_path = discovery_directory / sample / "detected_features.csv"
        features = (
            load_detected_features_csv(feature_path) if feature_path.is_file() else ()
        )
        feature_bounds = {
            f.feature_id: (f.rt_start_min, f.rt_end_min)
            for f in features
            if f.sample_stem == sample
            and f.rt_start_min is not None
            and f.rt_end_min is not None
        }
        feature_masses = {
            f.feature_id: f.mz for f in features if f.sample_stem == sample
        }
        bounded_candidates = []
        for candidate in candidates:
            bounded_positions = tuple(
                replace(
                    p,
                    peak_rt_window=feature_bounds.get(p.feature_id),
                    feature_mz=feature_masses.get(p.feature_id),
                )
                for p in candidate.positions
            )
            # NL asks about complete saved peaks, not just display padding.
            windows = [
                candidate.rt_window,
                *(
                    p.peak_rt_window
                    for p in bounded_positions
                    if p.peak_rt_window is not None
                ),
            ]
            bounded_candidates.append(
                replace(
                    candidate,
                    positions=bounded_positions,
                    rt_window=(min(w[0] for w in windows), max(w[1] for w in windows)),
                )
            )
        candidates = bounded_candidates
        targets = (
            tuple(
                (p.query_mz, p.query_mz * nl_settings.precursor_mz_tolerance_ppm * 1e-6)
                for c in candidates
                for p in c.positions
            )
            if nl_settings
            else ()
        )
        nl_scope = (
            PrecursorMassScope.around(
                targets, maximum_inference_radius_da=nl_settings.ms2_precursor_tol_da
            )
            if nl_settings
            else None
        )
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
                    XICRequest(p.expected_mz, *c.rt_window, c.xic_ppm)
                    for c in candidates
                    for p in c.positions
                )
            )
            traces = dict(zip(requests, reader.extract_xic_many(requests), strict=True))
            spectra: dict[int, ReviewSpectrum] = {}
            errors: list[ReviewReadError] = []
            seeds: list[DiscoverySeed] = []
            for lo, hi in (
                _merged_windows([c.rt_window for c in candidates])
                if nl_settings
                else ()
            ):
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
                    if (
                        nl_scope is not None
                        and nl_settings is not None
                        and nl_scope(scan.precursor_mz, scan.precursor_metadata)
                    ):
                        seeds.extend(
                            seed
                            for seed in discovery_seeds_from_scan(
                                scan, raw_file=raw_path, settings=nl_settings
                            )
                            if any(
                                abs(seed.precursor_mz - mz) <= tolerance
                                for mz, tolerance in targets
                            )
                        )
            qualified_seeds = (
                read_parent_ms1_observations(
                    reader,
                    tuple(seeds),
                    tolerance_ppm=nl_settings.precursor_mz_tolerance_ppm,
                )
                if nl_settings
                else ()
            )
            for candidate in candidates:
                positions = []
                for position in candidate.positions:
                    trace = traces[
                        XICRequest(
                            position.expected_mz,
                            *candidate.rt_window,
                            candidate.xic_ppm,
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
                result = replace(
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
                if result.pattern_id == "pt":
                    result = replace(
                        result,
                        envelope=compare_pt_envelope(
                            tuple(
                                EnvelopeTrace(p.label, p.raw_rt, p.raw_intensity)
                                for p in result.positions
                            ),
                            base_mz=result.base_mz,
                            rt_apex_min=result.base_rt_apex_min,
                        ),
                    )
                if nl_settings is not None:
                    result = replace(
                        result,
                        neutral_losses=assess_review_neutral_losses(
                            result,
                            features=features,
                            seeds=qualified_seeds,
                            settings=nl_settings,
                        ),
                    )
                    # The NL answer owns relevance. Do not duplicate thousands
                    # of unrelated context spectra in every candidate's report.
                    related_scans = {
                        scan
                        for n in result.neutral_losses
                        for scan in (
                            *n.related_scan_numbers,
                            *(m.scan_number for m in n.matches),
                        )
                    }
                    result = replace(
                        result,
                        spectra=tuple(
                            s for s in result.spectra if s.scan_number in related_scans
                        ),
                    )
                acquired[candidate.candidate_id] = result
    return DeltaMassReview(
        str(discovery_directory.resolve()),
        tuple(acquired[c.candidate_id] for c in selected),
        ReviewNlMethod(
            nl_preset,
            CidNlMassPolicy(
                **{
                    f.name: getattr(nl_settings, f.name)
                    for f in fields(CidNlMassPolicy)
                }
            ),
        )
        if nl_preset and nl_settings
        else None,
    )


def _read_selected_candidates(
    directory: Path, candidate_ids: Sequence[str] | None, padding_min: float
) -> tuple[ReviewCandidate, ...]:
    if candidate_ids is not None and not candidate_ids:
        raise ValueError(
            "Select at least one candidate ID, or request all candidates explicitly"
        )
    rows = read_delimited_rows(
        directory / "delta_mass_candidates.csv",
        required_columns=_CANDIDATE_FIELDS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    if candidate_ids is None:
        candidate_ids = [r["candidate_id"] for r in rows if r["status"] == "candidate"]
        if not candidate_ids:
            raise ValueError("No saved Delta Mass candidates to review")
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
    configured_positions = {p["label"]: p for p in method.get("positions", [])}
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
                delta_da=_required_number(
                    configured_positions.get(p["position_label"], p), "delta_da"
                ),
                expected_mz=_required_number(p, "expected_mz"),
                observed_mz=_saved_number(p, "observed_mz"),
                position_status=p["position_status"],
                feature_id=p.get("feature_id", ""),
            )
            for p in matched_positions
        )
        base_mz = _required_number(row, "base_mz")
        base_rt = _required_number(row, "base_rt_apex_min")
        if base_mz <= 0 or base_rt < 0 or any(p.query_mz <= 0 for p in parsed):
            raise ValueError(f"Invalid saved m/z or RT: {candidate_id}")
        apices = [
            base_rt,
            *(
                _required_number(p, "apex_rt_min")
                for p in matched_positions
                if p["apex_rt_min"] != ""
            ),
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
