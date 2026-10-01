from pathlib import Path

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.chrom_peak_candidate_adapter import (
    chrom_peak_segment_candidates,
)
from xic_extractor.peak_detection.chrom_peak_segments import ChromPeakSegmentEnumeration


def test_chrom_peak_segment_candidates_use_configured_morphology_window(
    monkeypatch,
) -> None:
    captured: dict[str, int] = {}

    def _fake_enumerate(_rt, _intensity, _baseline, *, policy, **_kwargs):
        captured["window"] = policy.morphology_trace_window_points
        return ChromPeakSegmentEnumeration(
            status="NO_SIGNAL",
            segments=(),
            n_points=5,
            morphology_trace_method=policy.morphology_trace_method,
            morphology_trace_window_points=policy.morphology_trace_window_points,
            morphology_trace_effective_points=0,
        )

    monkeypatch.setattr(
        "xic_extractor.peak_detection.chrom_peak_candidate_adapter."
        "enumerate_chrom_peak_segments",
        _fake_enumerate,
    )

    chrom_peak_segment_candidates(
        np.linspace(0.0, 1.0, 5),
        np.array([1.0, 2.0, 5.0, 2.0, 1.0]),
        _config(ms1_morphology_smoothing_window_points=9),
    )

    assert captured["window"] == 9


def _config(**overrides: object) -> ExtractionConfig:
    config = ExtractionConfig(
        data_dir=Path("."),
        dll_dir=Path("."),
        output_csv=Path("out.csv"),
        diagnostics_csv=Path("diag.csv"),
        smooth_window=7,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
        resolver_min_scans=5,
    )
    return ExtractionConfig(**{**config.__dict__, **overrides})
