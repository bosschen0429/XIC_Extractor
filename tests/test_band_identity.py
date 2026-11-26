"""Same-m/z rows are merged only when they are one chromatographic band."""

from __future__ import annotations

import random
from dataclasses import replace

import numpy as np

from tests.alignment_pipeline_helpers import _owner
from tests.owner_peak_fixtures import with_peak
from xic_extractor.alignment.band_identity import merge_one_band_groups
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    _hypothesis_from_owner_group,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.physical_peak_evaluation import (
    PHYSICAL_PEAK_POINT_POLICY,
    evaluate_physical_peak,
    measure_peak_envelope,
    peak_envelope,
    prepare_physical_signal,
)

_CONFIG = AlignmentConfig()
_MZ = 312.0691
_RT = np.round(np.arange(8.0, 10.5, 0.02), 4)


def _trace(*peaks: tuple[float, float]) -> np.ndarray:
    return sum(
        height * np.exp(-0.5 * ((_RT - apex) / 0.06) ** 2) for apex, height in peaks
    )


def _member(sample: str, suffix: str, trace: np.ndarray, query: float):
    evaluation = evaluate_physical_peak(
        prepare_physical_signal(_RT, trace),
        query_rt_min=query,
        selection_policy_version=PHYSICAL_PEAK_POINT_POLICY,
    )
    segment = evaluation.answer.selected_segment
    assert segment is not None
    owner = with_peak(
        _owner(sample, suffix, apex_rt=segment.apex_rt_min, precursor_mz=_MZ),
        apex_rt=segment.apex_rt_min,
        start_rt=segment.left_rt_min,
        end_rt=segment.right_rt_min,
        area=segment.area_counts_seconds,
    )
    return replace(owner, peak=replace(owner.peak, physical_evaluation=evaluation))


def _split(sample: str, left: float = 1e5, right: float = 1e5):
    """One band cut at a valley into two halves that share the cut scan."""
    trace = _trace((9.0, left), (9.3, right))
    return _member(sample, "early", trace, 9.0), _member(sample, "late", trace, 9.3)


def _rows(*samples: tuple[SampleLocalMS1Owner, ...]):
    early = [pair[0] for pair in samples]
    late = [pair[1] for pair in samples if len(pair) > 1]
    return tuple(
        _hypothesis_from_owner_group(
            tuple(owners), disposition="established", evidence=""
        )
        for owners in (early, late)
    )


def test_split_halves_with_a_stable_ratio_become_one_band() -> None:
    whole = _member("S4", "early", _trace((9.15, 2e5)), 9.15)
    groups = _rows(_split("S1"), _split("S2", 1.2e5, 1e5), _split("S3"), (whole,))

    (merged,) = merge_one_band_groups(groups, config=_CONFIG)

    assert merged.one_band and merged.disposition == "established"
    assert sorted(owner.raw_stem for owner in merged.owners) == [
        "S1",
        "S2",
        "S3",
        "S4",
    ]


def test_a_pair_resolved_in_any_injection_stays_two_rows() -> None:
    resolved = _trace((9.0, 1e5), (9.6, 1e5))
    groups = _rows(
        _split("S1"),
        _split("S2"),
        (_member("S3", "early", resolved, 9.0), _member("S3", "late", resolved, 9.6)),
    )

    assert merge_one_band_groups(groups, config=_CONFIG) == groups


def test_halves_whose_ratio_varies_beyond_twofold_stay_two_rows() -> None:
    groups = _rows(_split("S1", 1e5, 1e5), _split("S2", 1e5, 3e4))

    assert merge_one_band_groups(groups, config=_CONFIG) == groups


def test_one_ratio_is_not_enough_evidence() -> None:
    groups = _rows(_split("S1"))

    assert merge_one_band_groups(groups, config=_CONFIG) == groups


def test_merge_is_independent_of_group_order() -> None:
    groups = _rows(_split("S1"), _split("S2"), _split("S3"))
    shuffled = list(groups)
    random.Random(7).shuffle(shuffled)

    assert merge_one_band_groups(groups, config=_CONFIG) == merge_one_band_groups(
        tuple(reversed(shuffled)), config=_CONFIG
    )


def test_envelope_value_is_the_whole_band_from_either_half() -> None:
    early, late = _split("S1")
    evaluation = early.peak.physical_evaluation
    segments = peak_envelope(
        evaluation.segment_universe, evaluation.answer.selected_segment
    )

    from_early = measure_peak_envelope(early.peak.physical_evaluation)
    from_late = measure_peak_envelope(late.peak.physical_evaluation)

    assert len(segments) == 2
    assert from_early.area_ms1_morphology == from_late.area_ms1_morphology
    assert from_early.area_ms1_morphology == sum(
        segment.area_counts_seconds for segment in segments
    )
    assert (from_early.rt_left_min, from_early.rt_right_min) == (
        segments[0].left_rt_min,
        segments[-1].right_rt_min,
    )
