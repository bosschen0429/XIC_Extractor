from types import SimpleNamespace

from xic_extractor.peak_detection.candidate_scoring import (
    has_same_apex_cwt_support,
)


def test_cwt_same_apex_support_keeps_legacy_positive_finite_guard() -> None:
    supported = SimpleNamespace(
        proposal_sources=("legacy_savgol", "centwave_cwt"),
        cwt_best_scale=4.0,
        cwt_ridge_persistence=0.0,
    )
    cwt_only = SimpleNamespace(
        proposal_sources=("centwave_cwt",),
        cwt_best_scale=4.0,
        cwt_ridge_persistence=0.5,
    )
    non_finite = SimpleNamespace(
        proposal_sources=("legacy_savgol", "centwave_cwt"),
        cwt_best_scale=float("nan"),
        cwt_ridge_persistence=0.0,
    )

    assert has_same_apex_cwt_support(supported) is True
    assert has_same_apex_cwt_support(cwt_only) is False
    assert has_same_apex_cwt_support(non_finite) is False
