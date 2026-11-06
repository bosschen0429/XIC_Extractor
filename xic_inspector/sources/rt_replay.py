"""Project raw curve coordinates through the frozen Alignment estimate owner."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from xic_extractor.alignment_explanation.storage import ExplanationError

if TYPE_CHECKING:
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration


def read_calibration(payload: Any) -> BatchRtCalibration | None:
    if payload is None:
        return None
    from xic_extractor.rt_calibration_codec import rt_calibration_from_payload

    try:
        return rt_calibration_from_payload(payload)
    except (ValueError, TypeError, KeyError) as exc:
        raise ExplanationError(
            f"Invalid sample RT calibration; rerun Alignment: {exc}"
        ) from exc


def align_curve(
    curve: dict[str, Any],
    sample_stem: str | None,
    calibration: BatchRtCalibration | None,
) -> None:
    """Retain intensities and measured bounds; only project displayed RTs."""
    sample = (
        calibration.samples.get(sample_stem)
        if calibration is not None and sample_stem is not None
        else None
    )
    methods: set[str] = set()

    def aligned(value):
        if value is None:
            return None
        estimate = sample.estimate_rt(value) if sample is not None else None
        methods.add(estimate.method if estimate is not None else "raw_fallback")
        return estimate.rt_min if estimate is not None else value

    curve["aligned_rts"] = [aligned(value) for value in curve["raw_rts"]]
    for processed in curve.get("processed_curves", []):
        processed["aligned_rts"] = [aligned(value) for value in processed["rts"]]
    for name in ("bound_left_rt", "bound_right_rt", "apex_rt"):
        curve[f"aligned_{name}"] = aligned(curve.get(name))
    curve["rt_alignment_methods"] = sorted(methods)
    curve["rt_alignment_label"] = (
        "無 RT 估計；顯示原始 RT"
        if "raw_fallback" in methods
        else "校正 RT：" + ", ".join(sorted(methods))
    )
