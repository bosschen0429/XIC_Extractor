"""An under-equilibrated injection's early segment, measured by its standards."""

import pytest

from tests.membership_fixtures import cid_nl_owner
from xic_extractor.alignment.band_grouping import band_shift_rows, group_peak_bands
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.early_segment_shift import (
    early_segment_shape,
    measure_early_segment_shift,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    RtCalibrationPolicy,
    SampleRtCalibration,
)

REFERENCES = (("first", 9.0), ("second", 12.5))
FADING = (8.4544, 12.2195)
"""Standards at 9.0 and 12.5 min, 0.6 min early at the segment end: 33 s and 17 s."""


def _calibration(standards, *, unsupported_before=None):
    """Identity maps from ``unsupported_before`` (2 min by default) to 40 min."""
    starts = unsupported_before or {}
    return BatchRtCalibration(
        policy=RtCalibrationPolicy(),
        knots=(),
        samples={
            stem: SampleRtCalibration(
                stem,
                "calibrated",
                ((starts.get(stem, 2.0),) * 2, (40.0, 40.0)),
                None,
                None,
                "unmeasured",
                standard_residuals=tuple(
                    (label, observed, reference, 0.0)
                    for (label, reference), observed in zip(
                        REFERENCES, values, strict=False
                    )
                ),
            )
            for stem, values in standards.items()
        },
        front_shift_center_min=None,
        front_shift_mad_min=None,
        perturbation_tolerance_min=None,
    )


def test_an_under_equilibrated_injection_keeps_its_early_peak_in_the_band():
    """A compound at 5.0 min elutes at 3.44 in the affected injection, which has
    no anchor before 8 min; the steep shape there needs the shift solved at the
    elution time, not looked up at the observed one."""
    order = {f"s{i}": i for i in range(1, 10)}
    calibration = _calibration(
        dict.fromkeys(order, (9.0, 12.5)) | {"s5": FADING},
        unsupported_before={"s5": 8.0},
    )
    owners = tuple(
        cid_nl_owner(stem, "x", apex_rt=3.44 if stem == "s5" else 5.0) for stem in order
    )
    (band,) = group_peak_bands(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=order).with_sample_calibration(
            calibration
        ),
    )
    assert band.owners == owners


def test_only_an_early_shift_that_fades_with_rt_marks_an_injection():
    order = {f"s{i}": i for i in range(1, 10)}
    standards = dict.fromkeys(order, (9.0, 12.5)) | {
        "s3": FADING,
        "s5": (8.75, 12.25),
        "s7": (9.4, 12.6),
    }
    shift = measure_early_segment_shift(_calibration(standards), order)
    assert set(shift.amplitude_min) == {"s3"}
    assert shift.amplitude_min["s3"] == pytest.approx(-0.6, abs=1e-3)
    assert shift.neighbours["s3"] == ("s1", "s2", "s4", "s5", "s6")
    assert shift.elution_rt_min("s3", 9.2) is None
    assert shift.elution_rt_min("s3", 3.44) == pytest.approx(5.0, abs=1e-3)

    one_standard = {stem: values[:1] for stem, values in standards.items()}
    assert not measure_early_segment_shift(
        _calibration(one_standard), order
    ).amplitude_min
    assert early_segment_shape(2.0) == 0.0


def test_a_peak_placed_by_the_shift_does_not_vote_on_sample_shift():
    order = {f"s{i:02}": i for i in range(1, 22)}
    calibration = _calibration(
        dict.fromkeys(order, (9.0, 12.5)) | {"s10": FADING},
        unsupported_before={"s10": 8.0},
    )
    owners = tuple(
        cid_nl_owner(stem, "x", apex_rt=3.44 if stem == "s10" else 5.0)
        for stem in order
    )
    (row,) = band_shift_rows(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=order).with_sample_calibration(
            calibration
        ),
    )
    assert set(row) == set(order) - {"s10"}
