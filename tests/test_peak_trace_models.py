import numpy as np
import pytest

from xic_extractor.peak_detection.traces import (
    Trace,
    targeted_trace_group,
    trace_from_xic_request,
)
from xic_extractor.xic_models import XICRequest, XICTrace


def test_trace_from_xic_request_preserves_xic_metadata() -> None:
    request = XICRequest(mz=269.1388, rt_min=24.0, rt_max=26.0, ppm_tol=10.0)
    xic = XICTrace.from_arrays([24.0, 25.0, 26.0], [0.0, 100.0, 0.0])

    trace = trace_from_xic_request(
        sample_name="TumorBC2312_DNA",
        request=request,
        xic_trace=xic,
        source="alignment_owner",
    )

    assert trace.sample_name == "TumorBC2312_DNA"
    assert trace.mz == 269.1388
    assert trace.rt_min == 24.0
    assert trace.rt_max == 26.0
    assert trace.ppm_tol == 10.0
    assert trace.scan_count == 3
    assert trace.source == "alignment_owner"


def test_trace_rejects_mismatched_arrays() -> None:
    with pytest.raises(ValueError, match="matching 1D arrays"):
        Trace.from_arrays(
            sample_name="s1",
            mz=250.0,
            rt=np.asarray([1.0, 2.0]),
            intensity=np.asarray([10.0]),
            rt_min=1.0,
            rt_max=2.0,
            ppm_tol=10.0,
        )


def test_targeted_trace_group_adapter_sets_context() -> None:
    trace = Trace.from_arrays(
        sample_name="s1",
        mz=269.1388,
        rt=[24.0, 25.0, 26.0],
        intensity=[0.0, 100.0, 0.0],
        rt_min=24.0,
        rt_max=26.0,
        ppm_tol=10.0,
    )

    targeted = targeted_trace_group(
        trace,
        target_label="15N5-8-oxodG",
        expected_rt_min=25.0,
        neutral_loss_tag="DNA_dR",
        product_mz=153.048,
        role="ISTD",
    )

    assert targeted.trace_group_id == "s1|15N5-8-oxodG"
    assert targeted.analysis_mode == "targeted"
    assert targeted.role == "ISTD"
