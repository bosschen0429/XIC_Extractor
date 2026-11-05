"""Helpers shared by test_xic_inspector_curve_bundle and other test modules."""

from xic_inspector.sources.curve_bundle import (
    ChromatogramCurve,
    CurveBundle,
    CurveBundleTrace,
    PeakBounds,
    TransitionCurves,
)


def _fixture_bundle() -> CurveBundle:
    return CurveBundle(
        transitions=(
            TransitionCurves(
                key="transition-A",
                review_rt_window=(4.0, 7.0),
                quality_flags={
                    "gate_labels": "mass and RT passed",
                    "identity_decisions": "successor supported",
                    "existing_count": 2,
                    "delta_mz": 0.012,
                    "delta_rt_min": 0.08,
                    "median_ratio": 1.25,
                },
                traces=(
                    CurveBundleTrace(
                        trace_id="source-s1",
                        sample_id="sample-1",
                        sample_label="sample-one",
                        role="source",
                        raw_identity="raw-source-1",
                        mz=501.2,
                        ppm=10.0,
                        extraction_rt_window=(3.5, 7.5),
                        producer="test-diagnostic",
                        producer_version="1.0",
                        raw=ChromatogramCurve(
                            (4.0, 5.0, 6.0, 7.0), (0.0, 5.0, 9.0, 0.0)
                        ),
                        gaussian15=ChromatogramCurve(
                            (4.0, 5.0, 6.0, 7.0), (1.0, 4.0, 7.0, 1.0)
                        ),
                        baseline=ChromatogramCurve(
                            (4.0, 5.0, 6.0, 7.0), (0.0, 1.0, 1.0, 0.0)
                        ),
                        peak_bounds=PeakBounds(4.0, 6.0, 7.0),
                        quality_flags={"gate": "accepted"},
                    ),
                    CurveBundleTrace(
                        trace_id="successor-s2",
                        sample_id="sample-2",
                        sample_label="sample-two",
                        role="successor",
                        raw_identity="raw-successor-2",
                        mz=501.188,
                        ppm=10.0,
                        extraction_rt_window=(3.5, 7.5),
                        producer="test-diagnostic",
                        producer_version="1.0",
                        raw=ChromatogramCurve(
                            (4.0, 5.0, 6.0, 7.0), (0.0, 2.0, 12.0, 0.0)
                        ),
                        gaussian15=ChromatogramCurve(
                            (4.0, 5.0, 6.0, 7.0), (1.0, 3.0, 9.0, 1.0)
                        ),
                        peak_bounds=PeakBounds(4.0, 6.0, 7.0),
                    ),
                ),
            ),
        )
    )
