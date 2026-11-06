"""Persist and replay the frozen sample RT calibration without fitting."""

from __future__ import annotations

import math
from dataclasses import asdict, fields
from typing import Any

from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    CalibrationKnot,
    RtCalibrationPolicy,
    RtEstimateSupport,
    SampleRtCalibration,
)


def _require_calibration_fields(value: object, expected: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(
            "RT calibration fields do not match the schema; rerun the analysis"
        )
    return value


def rt_calibration_from_payload(value: dict[str, Any]) -> BatchRtCalibration:
    _require_calibration_fields(
        value,
        {
            "schema",
            "policy",
            "knots",
            "samples",
            "front_shift_center_min",
            "front_shift_mad_min",
            "perturbation_tolerance_min",
        },
    )
    if value["schema"] != "sample_rt_calibration":
        raise ValueError(
            "Expected a sample_rt_calibration JSON artifact; rerun the analysis"
        )
    _require_calibration_fields(
        value["policy"], {field.name for field in fields(RtCalibrationPolicy)}
    )
    for knot in value["knots"]:
        _require_calibration_fields(
            knot, {field.name for field in fields(CalibrationKnot)}
        )
    samples = {}
    for row in value["samples"]:
        _require_calibration_fields(
            row,
            {
                "sample_stem",
                "status",
                "knots_observed_reference_min",
                "front_shift_min",
                "perturbation_status",
                "anchor_count",
                "standard_residuals",
                "estimate_support",
            },
        )
        knots = tuple(
            (float(x), float(y)) for x, y in row["knots_observed_reference_min"]
        )
        if (
            any(not math.isfinite(v) for pair in knots for v in pair)
            or any(
                x2 <= x1 or y2 <= y1
                for (x1, y1), (x2, y2) in zip(knots, knots[1:], strict=False)
            )
            or len(knots) == 1
        ):
            raise ValueError(f"Invalid RT knots for {row['sample_stem']}")
        stem = row["sample_stem"]
        if stem in samples:
            raise ValueError("RT calibration has duplicate samples")
        samples[stem] = SampleRtCalibration(
            sample_stem=stem,
            status=row["status"],
            knots=knots,
            front_shift_min=row["front_shift_min"],
            prior_injection=None,
            perturbation_status=row["perturbation_status"],
            anchor_count=row["anchor_count"],
            standard_residuals=tuple(tuple(item) for item in row["standard_residuals"]),
            estimate_support=_read_estimate_support(row["estimate_support"]),
        )
    policy = dict(value["policy"])
    return BatchRtCalibration(
        policy=RtCalibrationPolicy(**policy),
        knots=tuple(
            CalibrationKnot(**{**row, "feature_ids": tuple(row["feature_ids"])})
            for row in value["knots"]
        ),
        samples=samples,
        front_shift_center_min=value["front_shift_center_min"],
        front_shift_mad_min=value["front_shift_mad_min"],
        perturbation_tolerance_min=value["perturbation_tolerance_min"],
    )


def _read_estimate_support(value: object) -> RtEstimateSupport | None:
    """Decode persisted RT estimate measurements."""
    if value is None:
        return None
    value = _require_calibration_fields(
        value, {field.name for field in fields(RtEstimateSupport)}
    )
    positions = value.get("positions_min")
    if (
        not isinstance(positions, list)
        or len(positions) < 2
        or any(
            isinstance(x, bool)
            or not isinstance(x, (int, float))
            or not math.isfinite(x)
            for x in positions
        )
        or any(a >= b for a, b in zip(positions, positions[1:], strict=False))
    ):
        raise ValueError("RT estimate positions must be finite and strictly increasing")
    for key in ("median_shift_min", "shift_spread_min", "fit_spread_min"):
        number = value.get(key)
        if number is None and key == "fit_spread_min":
            continue
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(number)
            or (key != "median_shift_min" and number < 0)
        ):
            raise ValueError(f"Invalid RT estimate {key}")
    return RtEstimateSupport(
        tuple(positions),
        value["median_shift_min"],
        value["shift_spread_min"],
        value.get("fit_spread_min"),
    )


def rt_calibration_payload(calibration: BatchRtCalibration) -> dict[str, Any]:
    return {
        "schema": "sample_rt_calibration",
        "policy": asdict(calibration.policy),
        "knots": [asdict(knot) for knot in calibration.knots],
        "samples": [
            {
                "sample_stem": sample.sample_stem,
                "status": sample.status,
                "knots_observed_reference_min": sample.knots,
                "front_shift_min": sample.front_shift_min,
                "perturbation_status": sample.perturbation_status,
                "anchor_count": sample.anchor_count,
                "standard_residuals": sample.standard_residuals,
                "estimate_support": {
                    **asdict(sample.estimate_support),
                    "positions_min": list(sample.estimate_support.positions_min),
                }
                if sample.estimate_support is not None
                else None,
            }
            for sample in calibration.samples.values()
        ],
        "front_shift_center_min": calibration.front_shift_center_min,
        "front_shift_mad_min": calibration.front_shift_mad_min,
        "perturbation_tolerance_min": calibration.perturbation_tolerance_min,
    }
