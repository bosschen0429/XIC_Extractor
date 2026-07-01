"""Performance regression test for ``match_delta_mass_pattern``.

Task 2's matcher groups features by ``sample_stem`` and sorts each group by
m/z so that, per position, the m/z-bounded window lookup can be found in
O(log n) via bisect and only that bounded slice needs scanning. An earlier
implementation computed the window bounds but never used them to narrow the
scan, degrading to an O(n) linear scan per position per base candidate --
i.e. O(n^2) overall per sample. On the real ~78,976-row Task 8 proxy dataset
that took roughly 19 minutes; this test uses a smaller synthetic fixture
(comfortably reproducible on a slower CI runner) that would still take tens
of seconds to minutes under the old O(n^2) behavior but completes in well
under a second with the O(n log n) bisect-based fix.

The synthetic fixture spreads base candidates evenly across a wide m/z range
(no clustering) across several samples so the sorted-list windowing is
exercised the way it would be on realistic proxy data, and is fully
deterministic (no randomness) to avoid flaky pathological clustering.
"""

from __future__ import annotations

import time

from xic_extractor.discovery.delta_mass_patterns import match_delta_mass_pattern
from xic_extractor.discovery.detected_features import DetectedFeature

_FEATURE_COUNT = 24000
_SAMPLE_COUNT = 4
_MZ_RANGE_LOW = 200.0
_MZ_RANGE_HIGH = 1200.0
_WALL_CLOCK_BUDGET_SEC = 30.0


def _make_synthetic_features() -> list[DetectedFeature]:
    features: list[DetectedFeature] = []
    per_sample = _FEATURE_COUNT // _SAMPLE_COUNT
    mz_span = _MZ_RANGE_HIGH - _MZ_RANGE_LOW
    for sample_index in range(_SAMPLE_COUNT):
        sample_stem = f"perf_sample_{sample_index}"
        for row_index in range(per_sample):
            mz = _MZ_RANGE_LOW + (row_index * mz_span / per_sample)
            rt_apex_min = 5.0 + (row_index % 50) * 0.1
            features.append(
                DetectedFeature(
                    sample_stem=sample_stem,
                    feature_id=f"{sample_stem}_f{row_index}",
                    source_feature_row_id=None,
                    mz=mz,
                    rt_apex_min=rt_apex_min,
                    rt_start_min=None,
                    rt_end_min=None,
                    height=1000.0 + row_index,
                    area=None,
                    trace_quality="GOOD",
                    shape_score=None,
                    source="synthetic_perf",
                )
            )
    return features


def test_match_delta_mass_pattern_completes_within_budget_for_large_input() -> None:
    """Guards against O(n^2) regression in the m/z-bounded window lookup.

    Threshold rationale: with the bisect-based O(n log n) fix, this fixture
    (24,000 rows / 4 samples, 6,000 rows per sample) completes in well under
    a second locally. Under the old unbounded-linear-scan O(n^2) behavior,
    6,000^2 = 36,000,000 comparisons per sample x 3 non-base positions would
    take tens of seconds per sample (matching the observed real-data
    slowdown at ~78,976 rows taking ~19 minutes). A 30-second budget is
    generous enough to avoid flakiness on a slower CI/self-hosted runner
    while still clearly failing under the old quadratic behavior.
    """

    features = _make_synthetic_features()

    start = time.perf_counter()
    result = match_delta_mass_pattern(features)
    elapsed_sec = time.perf_counter() - start

    assert isinstance(result, tuple)
    assert elapsed_sec < _WALL_CLOCK_BUDGET_SEC, (
        f"match_delta_mass_pattern took {elapsed_sec:.2f}s for "
        f"{_FEATURE_COUNT} synthetic features, exceeding the "
        f"{_WALL_CLOCK_BUDGET_SEC:.0f}s budget -- likely an O(n^2) "
        "regression in the m/z-bounded window lookup"
    )
