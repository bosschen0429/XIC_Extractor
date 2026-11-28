"""Acquire selected Delta Mass review windows without re-running Discovery."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import fields, replace
from pathlib import Path

from xic_extractor.configuration.cid_nl_profile import CidNlMassPolicy
from xic_extractor.configuration.mass_recalibration import (
    VENDOR_MASS_RECALIBRATION_RESULT,
    MassRecalibration,
    MassRecalibrationResult,
    deserialize_mass_recalibration_result,
    resolve_mass_recalibration,
)
from xic_extractor.diagnostics.delta_mass_nl_review import assess_review_neutral_losses
from xic_extractor.diagnostics.delta_mass_review_data import (
    DeltaMassReview,
    ReviewCandidate,
    ReviewNlMethod,
    ReviewPosition,
    ReviewReadError,
    ReviewSpectrum,
)
from xic_extractor.discovery.models import DiscoveryEvent
from xic_extractor.discovery.ms2_events import (
    discovery_events_from_scan,
    read_parent_ms1_observations,
)
from xic_extractor.discovery.store import DISCOVERY_ARTIFACT_NAME, open_discovery
from xic_extractor.evidence.pt_envelope import EnvelopeTrace, compare_pt_envelope
from xic_extractor.mass_accuracy import mz_tolerance_da
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlCandidateInterval,
)
from xic_extractor.precursor_evidence import PrecursorMassScope
from xic_extractor.presets.apply import apply_to_discovery
from xic_extractor.presets.loader import load_preset
from xic_extractor.raw_reader import open_raw
from xic_extractor.xic_models import XICRequest


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
    selected, mass_results, features = _read_selected_candidates(
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
        targets = (
            tuple(
                (
                    p.query_mz,
                    mz_tolerance_da(p.query_mz, nl_settings.precursor_mz_tolerance_ppm),
                )
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
        mass_result = mass_results.get(sample, VENDOR_MASS_RECALIBRATION_RESULT)
        with open_raw(
            raw_path, dll_dir=dll_directory, mass_recalibration=mass_result.policy
        ) as reader:
            if mass_result.policy is not MassRecalibration.NONE:
                reader.use_mass_recalibration_result(mass_result)
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
            seeds: list[DiscoveryEvent] = []
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
                            for seed in discovery_events_from_scan(
                                scan, raw_file=raw_path, settings=nl_settings
                            )
                            if any(
                                abs(seed.scan.precursor_mz - mz) <= tolerance
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
                            features=features.get(sample, {}),
                            seeds=qualified_seeds,
                            settings=nl_settings,
                        ),
                    )
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
) -> tuple[
    tuple[ReviewCandidate, ...],
    dict[str, MassRecalibrationResult],
    dict[str, dict[str, CidNlCandidateInterval]],
]:
    if candidate_ids is not None and not candidate_ids:
        raise ValueError(
            "Select at least one candidate ID, or request all candidates explicitly"
        )
    with open_discovery(directory / DISCOVERY_ARTIFACT_NAME) as db:
        run = db.execute("SELECT method_label,method FROM delta_mass_run").fetchone()
        if run is None:
            raise ValueError("No Delta Mass result in this Discovery output")
        method_label, method = run[0], json.loads(run[1])
        rows = {
            cid: (row_id, stem, pattern, mz, rt, recalibration)
            for row_id, cid, stem, pattern, mz, rt, recalibration in db.execute(
                "SELECT c.id,c.candidate_id,s.sample_stem,c.pattern_id,c.base_mz,"
                "c.base_rt_apex_min,s.mass_recalibration_result "
                "FROM delta_mass_candidates c JOIN samples s ON s.id=c.sample "
                "ORDER BY c.id"
            )
        }
        features: dict[str, dict[str, CidNlCandidateInterval]] = {}
        for stem, feature_id, mz, start, end in db.execute(
            "SELECT s.sample_stem,f.feature_id,f.mz,f.rt_start_min,f.rt_end_min "
            "FROM delta_mass_features f JOIN samples s ON s.id=f.sample "
            "WHERE f.rt_start_min IS NOT NULL AND f.rt_end_min IS NOT NULL"
        ):
            features.setdefault(stem, {})[feature_id] = CidNlCandidateInterval(
                mz, start, end
            )
        positions: dict[int, list[tuple[ReviewPosition, float | None]]] = {}
        for (
            row_id,
            stem,
            label,
            delta,
            expected,
            observed,
            status,
            feature,
            apex,
        ) in db.execute(
            "SELECT p.candidate,s.sample_stem,p.position_label,p.delta_da,"
            "p.expected_mz,p.observed_mz,p.position_status,"
            "COALESCE(p.feature_id,''),p.apex_rt_min "
            "FROM delta_mass_positions p "
            "JOIN delta_mass_candidates c ON c.id=p.candidate "
            "JOIN samples s ON s.id=c.sample ORDER BY p.candidate,p.ordinal"
        ):
            interval = features.get(stem, {}).get(feature)
            positions.setdefault(row_id, []).append(
                (
                    ReviewPosition(
                        label=label,
                        delta_da=delta,
                        expected_mz=expected,
                        observed_mz=observed,
                        position_status=status,
                        feature_id=feature,
                        peak_rt_window=None
                        if interval is None
                        else (interval.rt_left_min, interval.rt_right_min),
                        feature_mz=None if interval is None else interval.mz,
                    ),
                    apex,
                )
            )
    if candidate_ids is None:
        candidate_ids = list(rows)
        if not candidate_ids:
            raise ValueError("No saved Delta Mass candidates to review")
    policy = resolve_mass_recalibration(method.get("mass_recalibration", "none"))
    ppm = float(method["mz_tolerance_ppm"])
    selected = []
    mass_results: dict[str, MassRecalibrationResult] = {}
    for candidate_id in dict.fromkeys(candidate_ids):
        if candidate_id not in rows:
            raise ValueError(
                f"Candidate ID must identify exactly one saved row: {candidate_id}"
            )
        row_id, stem, pattern, base_mz, base_rt, recalibration = rows[candidate_id]
        saved = positions.get(row_id, [])
        parsed = tuple(position for position, _apex in saved)
        if not parsed:
            raise ValueError(f"Missing saved positions: {candidate_id}")
        apices = [base_rt, *(apex for _position, apex in saved if apex is not None)]
        windows = [
            (max(0.0, min(apices) - padding_min), max(apices) + padding_min),
            *(p.peak_rt_window for p in parsed if p.peak_rt_window is not None),
        ]
        if policy is not MassRecalibration.NONE:
            result = deserialize_mass_recalibration_result(recalibration)
            if result.policy is not policy:
                raise ValueError(f"Missing saved mass restoration for sample: {stem}")
            mass_results[stem] = result
        selected.append(
            ReviewCandidate(
                candidate_id=candidate_id,
                sample_stem=stem,
                method_label=method_label,
                pattern_id=pattern,
                status="candidate",
                reject_reason="",
                base_mz=base_mz,
                base_rt_apex_min=base_rt,
                positions=parsed,
                raw_file="",
                xic_ppm=ppm,
                rt_window=(min(w[0] for w in windows), max(w[1] for w in windows)),
            )
        )
    return tuple(selected), mass_results, features


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
