from collections.abc import Sequence

from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    RtCalibrationPolicy,
    SampleRtCalibration,
)


def calibration_from_knots(
    knots_by_sample: Sequence[tuple[str, tuple[tuple[float, float], ...]]],
) -> BatchRtCalibration:
    """An already chosen per-sample map, without fitting one."""
    return BatchRtCalibration(
        policy=RtCalibrationPolicy(),
        knots=(),
        samples={
            sample: SampleRtCalibration(sample, "calibrated", knots)
            for sample, knots in knots_by_sample
        },
    )
