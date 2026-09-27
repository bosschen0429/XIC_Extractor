"""Build per-sample RT calibration from batch-wide detected alignment features."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from statistics import median

from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    RtCalibrationPolicy,
    build_batch_rt_calibration,
)

DEFAULT_BAND_EDGES = (6.0, 10.0, 15.0, 20.0, 30.0)


def read_detected_rts(
    cells_path: Path,
) -> tuple[dict[str, dict[str, float]], tuple[str, ...]]:
    """Directly detected apex RTs by feature; every cell sample is in the batch."""
    detected: dict[str, dict[str, float]] = {}
    samples: set[str] = set()
    with cells_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        missing = {"feature_family_id", "sample_stem", "status", "apex_rt"} - set(
            reader.fieldnames or ()
        )
        if missing:
            raise ValueError(f"{cells_path} lacks columns: {sorted(missing)}")
        for row in reader:
            samples.add(row["sample_stem"])
            if row["status"] != "detected" or not row["apex_rt"]:
                continue
            rt = float(row["apex_rt"])
            if math.isfinite(rt):
                detected.setdefault(row["feature_family_id"], {})[
                    row["sample_stem"]
                ] = rt
    return detected, tuple(sorted(samples))


def read_perturbation_flags(timeline_path: Path) -> dict[str, bool]:
    payload = json.loads(timeline_path.read_text(encoding="utf-8"))
    if payload.get("schema") != "injection_timeline":
        raise ValueError(f"{timeline_path} is not an injection_timeline sidecar")
    return {
        row["raw_stem"]: bool(row["possibly_perturbed"])
        for row in payload["injections"]
    }


def heldout_band_offsets(
    detected: Mapping[str, Mapping[str, float]],
    calibration: BatchRtCalibration,
    *,
    min_samples: int,
    band_edges: Sequence[float],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Offsets of non-anchor features, before and after calibration.

    Both sides use the same observations: those inside each sample's anchor
    span, for features with at least ``min_samples`` such observations.
    """
    anchor_ids = {f for knot in calibration.knots for f in knot.feature_ids}
    raw_by_band: dict[tuple[str, str], list[float]] = {}
    cal_by_band: dict[tuple[str, str], list[float]] = {}
    raw_mads: list[float] = []
    cal_mads: list[float] = []
    outside = 0
    for feature_id, rt_by_sample in detected.items():
        if feature_id in anchor_ids or len(rt_by_sample) < min_samples:
            continue
        calibrated: dict[str, float] = {}
        for sample, rt in rt_by_sample.items():
            sample_cal = calibration.samples.get(sample)
            value = sample_cal.calibrated_rt(rt) if sample_cal else None
            if value is None:
                outside += 1
            else:
                calibrated[sample] = value
        if len(calibrated) >= min_samples:
            # Paired comparison: the same observations before and after.
            paired_raw = [rt_by_sample[sample] for sample in calibrated]
            paired_center = median(paired_raw)
            raw_mads.append(median(abs(rt - paired_center) for rt in paired_raw))
            cal_center = median(calibrated.values())
            band = _band_label(paired_center, band_edges)
            for sample, value in calibrated.items():
                raw_by_band.setdefault((sample, band), []).append(
                    rt_by_sample[sample] - paired_center
                )
                cal_by_band.setdefault((sample, band), []).append(value - cal_center)
            cal_mads.append(
                median(abs(value - cal_center) for value in calibrated.values())
            )
    rows: list[dict[str, object]] = [
        {
            "sample_stem": sample,
            "rt_band": band,
            "paired_feature_count": len(raw_values),
            "raw_median_offset_min": _round(median(raw_values)),
            "calibrated_median_offset_min": _round(median(cal_by_band[(sample, band)])),
        }
        for (sample, band), raw_values in sorted(raw_by_band.items())
    ]
    summary: dict[str, object] = {
        "heldout_min_samples": min_samples,
        "paired_feature_count": len(cal_mads),
        "raw_median_feature_mad_min": _round(median(raw_mads) if raw_mads else None),
        "calibrated_median_feature_mad_min": _round(
            median(cal_mads) if cal_mads else None
        ),
        "observations_outside_anchor_span": outside,
    }
    return rows, summary


def knot_band_counts(
    calibration: BatchRtCalibration, band_edges: Sequence[float]
) -> dict[str, int]:
    counts = {_band_label(edge, band_edges): 0 for edge in (-math.inf, *band_edges)}
    for knot in calibration.knots:
        counts[_band_label(knot.reference_rt_min, band_edges)] += 1
    return counts


def _band_label(rt: float, edges: Sequence[float]) -> str:
    lower = None
    for edge in edges:
        if rt < edge:
            return f"<{edge:g}" if lower is None else f"{lower:g}-{edge:g}"
        lower = edge
    return f">={lower:g}"


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _sample_rows(calibration: BatchRtCalibration) -> list[dict[str, object]]:
    rows = []
    for sample in calibration.samples.values():
        span = sample.reliable_observed_range
        rows.append(
            {
                "sample_stem": sample.sample_stem,
                "status": sample.status,
                "knot_count": len(sample.knots),
                "reliable_observed_start_min": _round(span[0]) if span else None,
                "reliable_observed_end_min": _round(span[1]) if span else None,
                "front_shift_min": _round(sample.front_shift_min),
                "possibly_perturbed": sample.possibly_perturbed,
                "perturbation_status": sample.perturbation_status,
            }
        )
    return rows


def _write_tsv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    defaults = RtCalibrationPolicy()
    parser.add_argument("--alignment-cells", type=Path, required=True)
    parser.add_argument(
        "--injection-timeline",
        type=Path,
        help="injection_timeline JSON; without it no injection is flagged",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--anchor-min-sample-fraction",
        type=float,
        default=defaults.anchor_min_sample_fraction,
    )
    parser.add_argument(
        "--knot-merge-tolerance-min",
        type=float,
        default=defaults.knot_merge_tolerance_min,
    )
    parser.add_argument(
        "--front-knot-count", type=int, default=defaults.front_knot_count
    )
    parser.add_argument(
        "--perturbation-mad-multiplier",
        type=float,
        default=defaults.perturbation_mad_multiplier,
    )
    parser.add_argument(
        "--perturbation-min-shift-min",
        type=float,
        default=defaults.perturbation_min_shift_min,
    )
    parser.add_argument(
        "--heldout-min-sample-fraction",
        type=float,
        default=0.7,
        help="non-anchor features detected in at least this fraction validate",
    )
    parser.add_argument(
        "--band-edges",
        type=lambda text: tuple(float(v) for v in text.split(",")),
        default=DEFAULT_BAND_EDGES,
    )
    args = parser.parse_args(argv)
    policy = RtCalibrationPolicy(
        anchor_min_sample_fraction=args.anchor_min_sample_fraction,
        knot_merge_tolerance_min=args.knot_merge_tolerance_min,
        front_knot_count=args.front_knot_count,
        perturbation_mad_multiplier=args.perturbation_mad_multiplier,
        perturbation_min_shift_min=args.perturbation_min_shift_min,
    )
    band_edges = tuple(sorted(args.band_edges))
    detected, samples = read_detected_rts(args.alignment_cells)
    flags = (
        read_perturbation_flags(args.injection_timeline)
        if args.injection_timeline
        else {}
    )
    calibration = build_batch_rt_calibration(
        detected, sample_stems=samples, possibly_perturbed=flags, policy=policy
    )
    heldout_rows, heldout_summary = heldout_band_offsets(
        detected,
        calibration,
        min_samples=math.ceil(args.heldout_min_sample_fraction * len(samples)),
        band_edges=band_edges,
    )
    sample_rows = _sample_rows(calibration)
    payload = {
        "schema": "sample_rt_calibration",
        "schema_version": 1,
        "evidence_status": "diagnostic_only",
        "alignment_cells": str(args.alignment_cells.resolve()),
        "injection_timeline": (
            str(args.injection_timeline.resolve()) if args.injection_timeline else None
        ),
        "policy": asdict(policy),
        "sample_count": len(samples),
        "timeline_unmatched_samples": sorted(set(samples) - set(flags))
        if flags
        else None,
        "front_shift_center_min": _round(calibration.front_shift_center_min),
        "front_shift_mad_min": _round(calibration.front_shift_mad_min),
        "perturbation_tolerance_min": _round(calibration.perturbation_tolerance_min),
        "knot_count_by_rt_band": knot_band_counts(calibration, band_edges),
        "knots": [asdict(knot) for knot in calibration.knots],
        "samples": [
            {
                **row,
                "knots_observed_reference_min": [
                    list(knot)
                    for knot in calibration.samples[str(row["sample_stem"])].knots
                ],
            }
            for row in sample_rows
        ],
        "heldout_validation": heldout_summary,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "sample_rt_calibration.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _write_tsv(args.output_dir / "sample_rt_calibration_samples.tsv", sample_rows)
    _write_tsv(args.output_dir / "sample_rt_calibration_heldout.tsv", heldout_rows)
    print(
        json.dumps(
            {
                "samples": len(samples),
                "knots": len(calibration.knots),
                "status": Counter(str(row["status"]) for row in sample_rows),
                "perturbation": Counter(
                    str(row["perturbation_status"]) for row in sample_rows
                ),
                "heldout": heldout_summary,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
