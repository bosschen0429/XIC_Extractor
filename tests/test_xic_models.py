import numpy as np
import pytest

from xic_extractor.xic_models import XICTrace, crop_xic_trace_by_rt


def test_xic_trace_normalizes_arrays_to_float() -> None:
    trace = XICTrace.from_arrays([8.1, 8.2], [10, 20])

    assert isinstance(trace.rt, np.ndarray)
    assert isinstance(trace.intensity, np.ndarray)
    assert trace.rt.dtype == float
    assert trace.intensity.dtype == float
    assert trace.rt.tolist() == [8.1, 8.2]
    assert trace.intensity.tolist() == [10.0, 20.0]


def test_xic_trace_rejects_mismatched_arrays() -> None:
    with pytest.raises(ValueError, match="matching 1D arrays"):
        XICTrace.from_arrays([8.1, 8.2], [10.0])


def test_crop_xic_trace_by_rt_uses_inclusive_bounds() -> None:
    trace = XICTrace.from_arrays([1.0, 1.1, 1.2, 1.3], [10, 20, 30, 40])

    cropped = crop_xic_trace_by_rt(trace, 1.1, 1.2)

    assert cropped.rt.tolist() == [1.1, 1.2]
    assert cropped.intensity.tolist() == [20.0, 30.0]


def test_crop_xic_trace_by_rt_accepts_reversed_bounds() -> None:
    trace = XICTrace.from_arrays([1.0, 1.1, 1.2, 1.3], [10, 20, 30, 40])

    cropped = crop_xic_trace_by_rt(trace, 1.2, 1.1)

    assert cropped.rt.tolist() == [1.1, 1.2]
    assert cropped.intensity.tolist() == [20.0, 30.0]


def test_crop_xic_trace_by_rt_preserves_mask_semantics_for_unsorted_rt() -> None:
    trace = XICTrace.from_arrays([1.2, 1.0, 1.1, 1.3], [30, 10, 20, 40])

    cropped = crop_xic_trace_by_rt(trace, 1.1, 1.2)

    assert cropped.rt.tolist() == [1.2, 1.1]
    assert cropped.intensity.tolist() == [30.0, 20.0]
