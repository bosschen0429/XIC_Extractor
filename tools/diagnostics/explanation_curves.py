"""Acquire explicitly selected review windows without Alignment or Backfill."""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.diagnostics.alignment_explanation import (
    ExplanationError,
    read_cid_nl_explanation,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)
from xic_extractor.raw_reader import open_raw
from xic_inspector.sources.curve_bundle import (
    ChromatogramCurve,
    CurveBundle,
    CurveBundleTrace,
    TransitionCurves,
    write_curve_bundle,
)


def export_explanation_curves(
    report: Path,
    output: Path,
    *,
    raw_dir: Path,
    dll_dir: Path,
    candidate_id: str | None = None,
    group_id: str | None = None,
    padding_min: float = 0.5,
    ppm: float = 5.0,
) -> dict[str, Any]:
    """Plot new bounded XICs around saved observations; never re-decide area."""
    if (candidate_id is None) == (group_id is None):
        raise ExplanationError(
            "Choose exactly one candidate ID or group ID for curve export"
        )
    if not math.isfinite(padding_min + ppm) or padding_min <= 0 or ppm <= 0:
        raise ExplanationError("Curve padding and ppm must be positive and finite")
    if output.resolve() == report.resolve() or output.exists():
        raise FileExistsError(f"Curve output must be a new file: {output}")
    summary = read_cid_nl_explanation(report)
    sample_labels = {
        row["canonical_sample_identity_id"]: row["sample_stem"]
        for row in summary["samples"]
    }
    if group_id is not None:
        with sqlite3.connect(report.resolve().as_uri() + "?mode=ro", uri=True) as db:
            candidate_ids = [
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT candidate_id FROM links "
                    "WHERE kind='final_groups' AND record_id=? ORDER BY candidate_id",
                    (group_id,),
                )
            ]
        if not candidate_ids:
            raise ExplanationError(
                f"Group has no retained candidate observations: {group_id}"
            )
    else:
        candidate_ids = [candidate_id]
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for cid in candidate_ids:
        detail = read_cid_nl_explanation(report, candidate_id=cid)
        for candidate in detail["candidates"]:
            sid = candidate["canonical_sample_identity_id"]
            terminals = [
                terminal
                for terminal in detail["records"].get("terminal_subjects", [])
                if terminal["canonical_sample_identity_id"] == sid
                and terminal["candidate_id"] == cid
            ]
            allowed = None
            if group_id is not None:
                allowed = {
                    oid
                    for group in detail["records"].get("final_groups", [])
                    if group["group_hypothesis_id"] == group_id
                    for member in group["member_subjects"]
                    if member["canonical_sample_identity_id"] == sid
                    for oid in member["physical_observation_ids"]
                }
            for terminal in terminals:
                for observation in terminal.get("observations", []):
                    oid = observation["peak_observation_content_id"]
                    if allowed is not None and oid not in allowed:
                        continue
                    selected[sid, oid] = {
                        "candidate_id": cid,
                        "mz": candidate["precursor_mz"],
                        "rt": observation["rt_apex_min"],
                    }
    if not selected:
        raise ExplanationError(
            "Selection has no retained apex coordinates for bounded curve acquisition"
        )
    by_sample: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for (sid, oid), observation in selected.items():
        by_sample[sid].append((oid, observation))
    traces = []
    profile = resolve_signal_processing_profile()
    for sid, observations in sorted(by_sample.items()):
        stem = sample_labels[sid]
        # External sample stems must stay inside the selected RAW directory.
        if Path(stem).name != stem or stem in (".", ".."):
            raise ExplanationError("Invalid sample stem in report")
        raw_path = raw_dir / f"{stem}.raw"
        if not raw_path.is_file():
            raise ExplanationError(
                f"RAW is required for this selected curve: {raw_path}"
            )
        with open_raw(raw_path, dll_dir) as raw:
            for oid, observation in observations:
                lower = max(0.0, observation["rt"] - padding_min)
                upper = observation["rt"] + padding_min
                rt, intensity = raw.extract_xic(observation["mz"], lower, upper, ppm)
                prepared = prepare_physical_signal(
                    rt, intensity, profile=profile
                ).prepared
                if prepared is None:
                    raise ExplanationError(
                        f"Selected window has no usable curve: {stem}, "
                        f"{observation['candidate_id']}"
                    )
                traces.append(
                    CurveBundleTrace(
                        trace_id=f"review:{sid}:{oid}",
                        sample_id=sid,
                        sample_label=stem,
                        role="diagnostic_reacquisition",
                        raw_identity=str(raw_path.resolve()),
                        mz=observation["mz"],
                        ppm=ppm,
                        extraction_rt_window=(lower, upper),
                        producer="explanation_selected_xic",
                        producer_version="1",
                        raw=ChromatogramCurve(
                            tuple(map(float, rt)), tuple(map(float, intensity))
                        ),
                        gaussian15=ChromatogramCurve(prepared.rt, prepared.smoothed),
                        baseline=ChromatogramCurve(prepared.rt, prepared.baseline),
                        positive_signal=ChromatogramCurve(
                            prepared.rt, prepared.positive_signal
                        ),
                        quality_flags={
                            "candidate_id": observation["candidate_id"],
                            "new_diagnostic_extraction": True,
                        },
                    )
                )
    window = (
        min(trace.extraction_rt_window[0] for trace in traces),
        max(trace.extraction_rt_window[1] for trace in traces),
    )
    bundle = CurveBundle(
        (
            TransitionCurves(
                key=group_id or str(candidate_id),
                review_rt_window=window,
                quality_flags={
                    "new_diagnostic_extraction": True,
                    "processing_request_id": summary["processing_request_id"],
                },
                traces=tuple(traces),
            ),
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_curve_bundle(bundle, output)
    return {
        "output": str(output.resolve()),
        "sample_count": len(by_sample),
        "curve_count": len(traces),
        "bytes": output.stat().st_size,
    }
