import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.acquisition_contract import AcquisitionContractError
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    DiscoverySeed,
    DiscoverySettings,
    NeutralLossProfile,
)
from xic_extractor.discovery.ms1_backfill import (
    _backfill_ms1_candidates_with_acquisition,
    backfill_ms1_candidates,
    merge_candidates_by_ms1_peak,
)
from xic_extractor.execution_runtime_owner import (
    derive_runtime_acquisition_metrics,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_acquisition_source_identity,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.signal_processing import PeakDetectionResult, PeakResult
from xic_extractor.xic_models import XICRequest, XICTrace

NEUTRAL_LOSS_DA = 116.0474
RAW_FILE = Path("C:/data/TumorBC2312_DNA.raw")


def test_backfill_runs_real_peak_detection_with_extraction_config() -> None:
    group = _group((_seed(scan_number=10, rt=9.00),))
    rt = np.linspace(8.60, 9.40, 161)
    intensity = _gaussian(rt, center=9.02, sigma=0.04, height=1000.0)
    raw = _FakeRaw(rt=rt, intensity=intensity)

    candidate = backfill_ms1_candidates(
        raw,
        (group,),
        settings=_settings(ms1_search_padding_min=0.30),
        peak_config=_peak_config(),
    )[0]

    assert candidate.ms1_peak_found is True
    assert candidate.ms1_apex_rt == pytest.approx(9.02)
    assert candidate.ms1_height == pytest.approx(1000.0)
    assert candidate.ms1_area is not None
    assert candidate.ms1_seed_delta_min == pytest.approx(0.02)


def test_backfill_extracts_group_precursor_xic_with_padded_rt_window_and_ppm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _group(
        (
            _seed(scan_number=10, rt=7.80, product_intensity=3000.0),
            _seed(scan_number=20, rt=7.90, product_intensity=9000.0),
        ),
        ms1_search_padding_min=0.15,
        precursor_mz_tolerance_ppm=12.0,
    )
    raw = _FakeRaw(
        rt=np.array([7.76, 7.90, 8.01]),
        intensity=np.array([10.0, 500.0, 20.0]),
    )
    peak_config = _peak_config(resolver_mode="local_minimum")
    peak_calls: list[
        tuple[np.ndarray, np.ndarray, ExtractionConfig, float | None, bool]
    ] = []

    def _fake_find_peak_and_area(
        rt: np.ndarray,
        intensity: np.ndarray,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
    ) -> PeakDetectionResult:
        peak_calls.append((rt, intensity, config, preferred_rt, strict_preferred_rt))
        return _ok_peak(rt=7.90, intensity=500.0, area=42.0, start=7.76, end=8.01)

    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        _fake_find_peak_and_area,
    )

    candidates = backfill_ms1_candidates(
        raw,
        (group,),
        settings=_settings(
            ms1_search_padding_min=0.15,
            precursor_mz_tolerance_ppm=12.0,
        ),
        peak_config=peak_config,
    )

    assert raw.requests == [
        (
            group.precursor_mz,
            pytest.approx(7.65),
            pytest.approx(8.05),
            12.0,
        )
    ]
    assert len(peak_calls) == 1
    assert peak_calls[0][0] is raw.rt
    assert peak_calls[0][1] is raw.intensity
    assert peak_calls[0][2] is peak_config
    assert peak_calls[0][3] == 7.90
    assert peak_calls[0][4] is False
    assert len(candidates) == 1


def test_backfill_clamps_ms1_search_rt_min_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _group((_seed(scan_number=10, rt=0.05),))
    raw = _FakeRaw(rt=np.array([], dtype=float), intensity=np.array([], dtype=float))

    candidate = backfill_ms1_candidates(
        raw,
        (group,),
        settings=_settings(ms1_search_padding_min=0.20),
        peak_config=_peak_config(),
    )[0]

    assert raw.requests == [
        (
            group.precursor_mz,
            0.0,
            pytest.approx(0.25),
            20.0,
        )
    ]
    assert candidate.ms1_search_rt_min == 0.0
    assert candidate.ms1_search_rt_max == pytest.approx(0.25)


def test_ms1_peak_fields_are_filled_from_peak_detection_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _group(
        (
            _seed(scan_number=10, rt=7.80, product_intensity=3000.0),
            _seed(scan_number=20, rt=7.90, product_intensity=9000.0),
        )
    )
    raw = _FakeRaw(
        rt=np.array([7.70, 7.91, 8.08]),
        intensity=np.array([20.0, 900.0, 25.0]),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.91,
            intensity=900.0,
            area=1234.5,
            start=7.70,
            end=8.08,
        ),
    )

    candidate = backfill_ms1_candidates(
        raw,
        (group,),
        settings=_settings(ms1_search_padding_min=0.20),
        peak_config=_peak_config(),
    )[0]

    assert candidate.ms1_peak_found is True
    assert candidate.ms1_apex_rt == 7.91
    assert candidate.ms1_height == 900.0
    assert candidate.ms1_area == 1234.5
    assert candidate.ms1_peak_rt_start == 7.70
    assert candidate.ms1_peak_rt_end == 8.08
    assert candidate.ms1_seed_delta_min == pytest.approx(0.01)
    assert candidate.ms1_trace_quality == "clean"
    assert candidate.review_priority == "HIGH"
    assert candidate.reason == "strong MS2 NL seed group; MS1 peak found near seed RT"
    assert candidate.best_ms2_scan_id == 20
    assert candidate.seed_scan_ids == (10, 20)
    assert candidate.ms2_product_max_intensity == 9000.0


@pytest.mark.parametrize(
    ("rt", "intensity", "peak_result"),
    (
        (np.array([], dtype=float), np.array([], dtype=float), None),
        (
            np.array([7.70, 7.90, 8.10]),
            np.array([0.0, 0.0, 0.0]),
            PeakDetectionResult(
                status="NO_SIGNAL",
                peak=None,
                n_points=3,
                max_smoothed=0.0,
                n_prominent_peaks=0,
            ),
        ),
    ),
)
def test_missing_or_empty_ms1_trace_still_creates_low_priority_candidate(
    monkeypatch: pytest.MonkeyPatch,
    rt: np.ndarray,
    intensity: np.ndarray,
    peak_result: PeakDetectionResult | None,
) -> None:
    group = _group((_seed(scan_number=10, rt=7.80),))
    raw = _FakeRaw(rt=rt, intensity=intensity)
    if peak_result is not None:
        monkeypatch.setattr(
            "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
            lambda *args, **kwargs: peak_result,
        )

    candidate = backfill_ms1_candidates(
        raw,
        (group,),
        settings=_settings(),
        peak_config=_peak_config(),
    )[0]

    assert candidate.ms1_peak_found is False
    assert candidate.ms1_apex_rt is None
    assert candidate.ms1_height is None
    assert candidate.ms1_area is None
    assert candidate.ms1_peak_rt_start is None
    assert candidate.ms1_peak_rt_end is None
    assert candidate.ms1_seed_delta_min is None
    assert candidate.ms1_trace_quality == "missing"
    assert candidate.review_priority == "LOW"
    assert candidate.reason == "strict MS2 NL seed; MS1 peak missing"


def test_single_seed_with_ms1_peak_is_medium_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _group((_seed(scan_number=10, rt=7.80),))
    raw = _FakeRaw(
        rt=np.array([7.70, 7.80, 7.90]),
        intensity=np.array([10.0, 100.0, 10.0]),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.80,
            intensity=100.0,
            area=50.0,
            start=7.70,
            end=7.90,
        ),
    )

    candidate = backfill_ms1_candidates(
        raw,
        (group,),
        settings=_settings(ms1_search_padding_min=0.20),
        peak_config=_peak_config(),
    )[0]

    assert candidate.review_priority == "MEDIUM"
    assert candidate.reason == "single MS2 NL seed; MS1 peak found"


def test_ms1_peak_far_from_best_seed_is_not_high_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _group(
        (
            _seed(scan_number=10, rt=7.80, product_intensity=3000.0),
            _seed(scan_number=20, rt=7.90, product_intensity=9000.0),
        )
    )
    raw = _FakeRaw(
        rt=np.array([7.70, 8.16, 8.30]),
        intensity=np.array([10.0, 100.0, 10.0]),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=8.16,
            intensity=100.0,
            area=50.0,
            start=8.10,
            end=8.22,
        ),
    )

    candidate = backfill_ms1_candidates(
        raw,
        (group,),
        settings=_settings(ms1_search_padding_min=0.20),
        peak_config=_peak_config(),
    )[0]

    assert candidate.ms1_seed_delta_min == pytest.approx(0.26)
    assert candidate.review_priority == "MEDIUM"
    assert candidate.reason == "MS1 peak offset from seed RT"


def test_backfill_merges_candidates_inside_same_ms1_peak_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_group = _group((_seed(scan_number=10, rt=7.80, product_intensity=3000.0),))
    second_group = _group((_seed(scan_number=20, rt=8.04, product_intensity=9000.0),))
    raw = _FakeRaw(
        rt=np.array([7.70, 7.92, 8.10]),
        intensity=np.array([20.0, 900.0, 25.0]),
    )
    peak_results = iter(
        (
            _ok_peak(rt=7.92, intensity=900.0, area=1234.5, start=7.70, end=8.10),
            _ok_peak(rt=7.92, intensity=900.0, area=1234.5, start=7.70, end=8.10),
        )
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: next(peak_results),
    )

    candidates = backfill_ms1_candidates(
        raw,
        (first_group, second_group),
        settings=_settings(ms1_search_padding_min=0.20),
        peak_config=_peak_config(),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.best_ms2_scan_id == 20
    assert candidate.seed_scan_ids == (10, 20)
    assert candidate.seed_event_count == 2
    assert candidate.rt_seed_min == pytest.approx(7.80)
    assert candidate.rt_seed_max == pytest.approx(8.04)
    assert candidate.ms1_search_rt_min == pytest.approx(7.60)
    assert candidate.ms1_search_rt_max == pytest.approx(8.24)
    assert candidate.ms1_area == 1234.5
    assert candidate.ms2_product_max_intensity == 9000.0
    assert candidate.review_priority == "HIGH"
    assert candidate.reason == (
        "MS2 NL seeds merged by shared MS1 peak; MS1 peak found near seed RT"
    )


def test_cross_tag_candidates_merge_only_when_ms1_peak_overlaps() -> None:
    settings = _multi_tag_settings()
    candidates = [
        _candidate("dR", product_mz=283.952656, observed_loss=116.047344),
        _candidate("R", product_mz=267.957700, observed_loss=132.042300),
        _candidate("MeR", product_mz=253.942100, observed_loss=146.057900),
    ]

    merged = merge_candidates_by_ms1_peak(candidates, settings=settings)

    assert len(merged) == 1
    assert merged[0].matched_tag_names == ("dR", "R", "MeR")
    assert merged[0].matched_tag_count == 3
    assert merged[0].neutral_loss_tag == "dR"
    assert merged[0].tag_evidence_json


def test_merge_with_lineage_preserves_candidate_policy_and_source_ordinals() -> None:
    from xic_extractor.discovery.ms1_backfill import (
        merge_candidates_by_ms1_peak_with_lineage,
    )

    settings = _multi_tag_settings()
    first = replace(
        _candidate("dR"),
        ms2_product_max_intensity=1_000.0,
    )
    second = replace(
        first,
        ms2_product_max_intensity=3_000.0,
    )

    expected_candidates = merge_candidates_by_ms1_peak(
        (first, second),
        settings=settings,
    )
    result = merge_candidates_by_ms1_peak_with_lineage(
        (first, second),
        settings=settings,
    )

    assert result.candidates == expected_candidates
    assert [
        (
            binding.final_ordinal,
            binding.representative_source_ordinal,
            binding.member_source_ordinals,
        )
        for binding in result.bindings
    ] == [(0, 1, (0, 1))]


def test_merge_candidates_preserves_same_tag_evidence_details() -> None:
    settings = _multi_tag_settings()
    first = replace(
        _candidate("dR"),
        seed_scan_ids=(101,),
        seed_event_count=1,
        tag_evidence_json=json.dumps(
            {
                "dR": {
                    "scan_count": 1,
                    "scan_ids": [101],
                    "rt_min": 5.01,
                    "rt_max": 5.04,
                    "product_mz": 283.90,
                    "max_intensity": 1000.0,
                    "neutral_loss_error_ppm": 3.0,
                }
            }
        ),
    )
    second = replace(
        _candidate("dR", product_mz=first.product_mz),
        seed_scan_ids=(102, 103),
        seed_event_count=2,
        tag_evidence_json=json.dumps(
            {
                "dR": {
                    "scan_count": 2,
                    "scan_ids": [102, 103],
                    "rt_min": 5.02,
                    "rt_max": 5.08,
                    "product_mz": 283.95,
                    "max_intensity": 3000.0,
                    "neutral_loss_error_ppm": 1.0,
                }
            }
        ),
    )

    merged = merge_candidates_by_ms1_peak([first, second], settings=settings)

    assert len(merged) == 1
    evidence = json.loads(merged[0].tag_evidence_json)
    assert evidence["dR"]["scan_count"] == 3
    assert evidence["dR"]["scan_ids"] == [101, 102, 103]
    assert evidence["dR"]["rt_min"] == 5.01
    assert evidence["dR"]["rt_max"] == 5.08
    assert evidence["dR"]["max_intensity"] == 3000.0
    assert evidence["dR"]["product_mz"] == 283.95
    assert evidence["dR"]["neutral_loss_error_ppm"] == 1.0


def test_merge_candidates_by_shared_ms1_feature_row_id_when_seed_is_on_tail() -> None:
    settings = _multi_tag_settings()
    row_id = "TumorBC2312_DNA|dR|400|5.0786"
    first = replace(
        _candidate("dR", peak_start=5.03, peak_end=5.12),
        candidate_id="TumorBC2312_DNA#30051@mz400_p283.952656",
        best_ms2_scan_id=30051,
        seed_scan_ids=(30051,),
        rt_seed_min=5.00,
        rt_seed_max=5.04,
        best_seed_rt=5.0273,
        ms1_apex_rt=5.0786,
        ms1_feature_row_id=row_id,
    )
    second = replace(
        _candidate("dR", peak_start=5.0786, peak_end=5.12),
        candidate_id="TumorBC2312_DNA#30272@mz400_p283.952656",
        best_ms2_scan_id=30272,
        seed_scan_ids=(30272,),
        rt_seed_min=5.2693,
        rt_seed_max=5.2693,
        best_seed_rt=5.2693,
        ms1_apex_rt=5.0786,
        ms1_feature_row_id=row_id,
    )

    merged = merge_candidates_by_ms1_peak([first, second], settings=settings)

    assert len(merged) == 1
    assert merged[0].ms1_feature_row_id == row_id
    assert merged[0].seed_scan_ids == (30051, 30272)
    assert merged[0].seed_event_count == 2


@pytest.mark.parametrize(
    ("second_precursor_mz", "expected_count"),
    [(400.0011, 1), (400.0100, 2)],
)
def test_candidates_sharing_an_ms1_apex_within_extraction_tolerance_are_one_peak(
    second_precursor_mz: float,
    expected_count: int,
) -> None:
    settings = _multi_tag_settings()
    first = replace(
        _candidate("dR", peak_start=40.1631, peak_end=41.1616),
        candidate_id="NormalBC2259_DNA#35352@mz400_p283.952656",
        best_ms2_scan_id=35352,
        seed_scan_ids=(35352,),
        rt_seed_min=40.4549,
        rt_seed_max=41.1204,
        best_seed_rt=40.6,
        ms1_apex_rt=40.579393,
        seed_event_count=6,
        ms1_feature_row_id="NormalBC2259_DNA|dR|400.000000|40.579393",
    )
    second = replace(
        _candidate(
            "dR",
            observed_loss=116.049344,
            peak_start=40.2048,
            peak_end=41.4945,
        ),
        candidate_id="NormalBC2259_DNA#36012@mz400_p283.952656",
        precursor_mz=second_precursor_mz,
        best_ms2_scan_id=36012,
        seed_scan_ids=(36012,),
        rt_seed_min=41.3285,
        rt_seed_max=41.3285,
        best_seed_rt=41.3285,
        ms1_apex_rt=40.579393,
        ms1_feature_row_id=(
            f"NormalBC2259_DNA|dR|{second_precursor_mz:.6f}|40.579393"
        ),
    )

    merged = merge_candidates_by_ms1_peak([first, second], settings=settings)

    assert len(merged) == expected_count
    if expected_count == 1:
        assert merged[0].seed_scan_ids == (35352, 36012)
        assert merged[0].seed_event_count == 7


def test_merge_shared_ms1_feature_row_id_with_configured_loss_edge_evidence() -> None:
    settings = _multi_tag_settings()
    row_id = "TumorBC2312_DNA|dR|400|8.6355"
    first = replace(
        _candidate(
            "dR",
            product_mz=283.95045,
            observed_loss=116.04955,
            peak_start=8.47,
            peak_end=8.72,
        ),
        seed_scan_ids=(5577,),
        rt_seed_min=8.49,
        rt_seed_max=8.52,
        ms1_apex_rt=8.6355,
        ms1_feature_row_id=row_id,
    )
    second = replace(
        _candidate(
            "dR",
            product_mz=283.95445,
            observed_loss=116.04555,
            peak_start=8.59,
            peak_end=8.80,
        ),
        seed_scan_ids=(5876,),
        rt_seed_min=8.78,
        rt_seed_max=8.85,
        ms1_apex_rt=8.6355,
        ms1_feature_row_id=row_id,
    )

    merged = merge_candidates_by_ms1_peak([first, second], settings=settings)

    assert len(merged) == 1
    assert merged[0].ms1_feature_row_id == row_id
    assert merged[0].seed_scan_ids == (5577, 5876)


@pytest.mark.parametrize(
    ("product_mz", "observed_loss"),
    [(283.90, None), (283.952656, 116.0520)],
    ids=["distinct-product", "loss-scatter"],
)
def test_shared_ms1_apex_merges_whatever_the_fragment_or_loss(
    product_mz: float,
    observed_loss: float | None,
) -> None:
    settings = _multi_tag_settings()
    row_id = "TumorBC2312_DNA|dR|400|5.0786"
    first = replace(
        _candidate("dR", product_mz=283.952656),
        ms1_feature_row_id=row_id,
    )
    second = replace(
        _candidate("dR", product_mz=product_mz, observed_loss=observed_loss),
        ms1_feature_row_id=row_id,
    )

    merged = merge_candidates_by_ms1_peak([first, second], settings=settings)

    assert len(merged) == 1
    assert merged[0].seed_event_count == 2


def test_cross_tag_candidates_do_not_merge_without_ms1_overlap() -> None:
    settings = _multi_tag_settings()
    left = _candidate("dR", peak_start=5.00, peak_end=5.05)
    right = _candidate("R", peak_start=5.20, peak_end=5.25)

    merged = merge_candidates_by_ms1_peak([left, right], settings=settings)

    assert len(merged) == 2


def test_backfill_does_not_merge_distinct_ms1_peak_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_group = _group((_seed(scan_number=10, rt=7.80, product_intensity=3000.0),))
    second_group = _group((_seed(scan_number=20, rt=8.04, product_intensity=9000.0),))
    raw = _FakeRaw(
        rt=np.array([7.70, 7.80, 8.04, 8.14]),
        intensity=np.array([20.0, 900.0, 850.0, 25.0]),
    )
    peak_results = iter(
        (
            _ok_peak(rt=7.80, intensity=900.0, area=100.0, start=7.70, end=7.88),
            _ok_peak(rt=8.04, intensity=850.0, area=90.0, start=7.96, end=8.14),
        )
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: next(peak_results),
    )

    candidates = backfill_ms1_candidates(
        raw,
        (first_group, second_group),
        settings=_settings(ms1_search_padding_min=0.20),
        peak_config=_peak_config(),
    )

    assert len(candidates) == 2
    assert [candidate.seed_scan_ids for candidate in candidates] == [(10,), (20,)]


def test_ms1_seed_delta_is_signed_but_priority_uses_absolute_distance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _group(
        (
            _seed(scan_number=10, rt=7.80, product_intensity=3000.0),
            _seed(scan_number=20, rt=7.90, product_intensity=9000.0),
        )
    )
    raw = _FakeRaw(
        rt=np.array([7.60, 7.70, 7.80]),
        intensity=np.array([10.0, 100.0, 10.0]),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.70,
            intensity=100.0,
            area=50.0,
            start=7.60,
            end=7.80,
        ),
    )

    candidate = backfill_ms1_candidates(
        raw,
        (group,),
        settings=_settings(ms1_search_padding_min=0.20),
        peak_config=_peak_config(),
    )[0]

    assert candidate.ms1_seed_delta_min == pytest.approx(-0.20)
    assert candidate.review_priority == "HIGH"
    assert candidate.reason == "strong MS2 NL seed group; MS1 peak found near seed RT"


@pytest.mark.parametrize(
    ("seed_values", "expected_scan", "expected_rt"),
    (
        (
            (
                (20, 7.82, 0.50),
                (30, 7.86, -0.25),
            ),
            30,
            7.86,
        ),
        (
            (
                (40, 7.88, 0.25),
                (50, 7.84, -0.25),
            ),
            50,
            7.84,
        ),
        (
            (
                (60, 7.84, 0.25),
                (55, 7.84, -0.25),
            ),
            55,
            7.84,
        ),
    ),
)
def test_best_seed_tie_breaks_by_error_then_rt_then_scan_for_candidate(
    monkeypatch: pytest.MonkeyPatch,
    seed_values: tuple[tuple[int, float, float], ...],
    expected_scan: int,
    expected_rt: float,
) -> None:
    seeds = tuple(
        _seed(
            scan_number=scan_number,
            rt=rt,
            product_intensity=10000.0,
            observed_loss_error_ppm=observed_loss_error_ppm,
        )
        for scan_number, rt, observed_loss_error_ppm in seed_values
    )
    group = _group(seeds)
    raw = _FakeRaw(
        rt=np.array([7.70, 7.90, 8.05]),
        intensity=np.array([10.0, 500.0, 20.0]),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.90,
            intensity=500.0,
            area=42.0,
            start=7.70,
            end=8.05,
        ),
    )

    candidate = backfill_ms1_candidates(
        raw,
        (group,),
        settings=_settings(),
        peak_config=_peak_config(),
    )[0]

    assert candidate.best_ms2_scan_id == expected_scan
    assert candidate.best_seed_rt == expected_rt
    assert candidate.ms1_seed_delta_min == pytest.approx(7.90 - expected_rt)


def test_owner_acquisition_batches_requests_and_preserves_exact_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    first = _group((_seed(scan_number=10, rt=7.80),))
    duplicate = replace(
        first,
        seeds=(_seed(scan_number=20, rt=7.80),),
    )
    later = _group((_seed(scan_number=30, rt=9.00),))
    empty = replace(first, seeds=())
    raw = _OwnerBoundBatchRaw(source)
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.8,
            intensity=100.0,
            area=10.0,
            start=7.7,
            end=7.9,
        ),
    )

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (first, duplicate, later, empty),
        acquisition_source=source,
        settings=_settings(),
        peak_config=_peak_config(),
    )

    measurements = result.measurements
    runtime_metrics = derive_runtime_acquisition_metrics(
        measurements.acquisition_batches
    )
    assert measurements.input_group_count == 4
    assert measurements.empty_group_count == 1
    assert runtime_metrics.logical_xic_request_count == 3
    assert runtime_metrics.xic_batch_count == 1
    assert runtime_metrics.adapter_chromatogram_call_count == 2
    assert runtime_metrics.trace_point_count == 9
    assert measurements.exact_scan_window_count == 2
    assert measurements.predicted_chunked_call_count == 2
    assert measurements.batch_max_requests == 256
    assert [len(batch) for batch in raw.request_batches] == [3]
    assert raw.request_batches[0][0] == raw.request_batches[0][1]


def test_owner_acquisition_splits_request_order_at_256_without_deduplication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    group = _group((_seed(scan_number=10, rt=7.80),))
    groups = tuple(
        replace(group, seeds=(_seed(scan_number=index + 1, rt=7.80),))
        for index in range(257)
    )
    raw = _OwnerBoundBatchRaw(source)
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.8,
            intensity=100.0,
            area=10.0,
            start=7.7,
            end=7.9,
        ),
    )

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        groups,
        acquisition_source=source,
        settings=_settings(),
        peak_config=_peak_config(),
    )

    metrics = derive_runtime_acquisition_metrics(
        result.measurements.acquisition_batches
    )
    assert [len(batch) for batch in raw.request_batches] == [256, 1]
    assert metrics.logical_xic_request_count == 257
    assert metrics.xic_batch_count == 2
    assert metrics.adapter_chromatogram_call_count == 2
    assert metrics.trace_point_count == 771
    assert result.measurements.exact_scan_window_count == 1
    assert result.measurements.predicted_chunked_call_count == 2
    assert result.measurements.batch_max_requests == 256


def test_owner_acquisition_preserves_scalar_candidate_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    groups = (
        _group((_seed(scan_number=10, rt=7.80),)),
        _group((_seed(scan_number=20, rt=7.84),)),
    )
    rt = np.asarray([7.7, 7.8, 7.9], dtype=float)
    intensity = np.asarray([10.0, 100.0, 10.0], dtype=float)
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.8,
            intensity=100.0,
            area=10.0,
            start=7.7,
            end=7.9,
        ),
    )

    scalar = backfill_ms1_candidates(
        _FakeRaw(rt=rt, intensity=intensity),
        groups,
        settings=_settings(),
        peak_config=_peak_config(),
    )
    acquired = _backfill_ms1_candidates_with_acquisition(
        _OwnerBoundBatchRaw(source),
        groups,
        acquisition_source=source,
        settings=_settings(),
        peak_config=_peak_config(),
    )

    assert acquired.candidates == scalar


def test_owner_acquisition_fails_closed_on_wrong_trace_cardinality(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    raw = _OwnerBoundBatchRaw(source, drop_results=True)

    with pytest.raises(
        AcquisitionContractError,
        match="exactly one trace per request",
    ):
        _backfill_ms1_candidates_with_acquisition(
            raw,
            (_group((_seed(scan_number=10, rt=7.80),)),),
            acquisition_source=source,
            settings=_settings(),
            peak_config=_peak_config(),
        )


def _settings(**overrides: float) -> DiscoverySettings:
    values = {
        "neutral_loss_profile": NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
        **overrides,
    }
    return DiscoverySettings(**values)


def _multi_tag_settings() -> DiscoverySettings:
    return DiscoverySettings(
        selected_tag_names=("dR", "R", "MeR"),
        tag_combine_mode="union",
        neutral_loss_profiles=(
            NeutralLossProfile("dR", 116.047344),
            NeutralLossProfile("R", 132.0423),
            NeutralLossProfile("MeR", 146.0579),
        ),
    )


def _candidate(
    tag: str,
    *,
    product_mz: float | None = None,
    observed_loss: float | None = None,
    peak_start: float = 5.00,
    peak_end: float = 5.10,
) -> DiscoveryCandidate:
    settings = _multi_tag_settings()
    loss_by_tag = {"dR": 116.047344, "R": 132.0423, "MeR": 146.0579}
    configured_loss = loss_by_tag[tag]
    product = product_mz if product_mz is not None else 400.0 - configured_loss
    observed = observed_loss if observed_loss is not None else configured_loss
    seed = DiscoverySeed(
        raw_file=RAW_FILE,
        sample_stem="TumorBC2312_DNA",
        scan_number=int(round(configured_loss * 1000)),
        rt=5.05,
        precursor_mz=400.0,
        product_mz=product,
        product_intensity=50000.0,
        neutral_loss_tag=tag,
        configured_neutral_loss_da=configured_loss,
        observed_neutral_loss_da=observed,
        observed_loss_error_ppm=0.0,
        matched_tag_names=(tag,),
        tag_evidence_json=f'{{"{tag}":{{"scan_count":1}}}}',
    )
    return replace(
        DiscoveryCandidate.from_values(
            raw_file=RAW_FILE,
            sample_stem="TumorBC2312_DNA",
            precursor_mz=400.0,
            product_mz=product,
            observed_neutral_loss_da=observed,
            best_seed=seed,
            seed_scan_ids=(seed.scan_number,),
            neutral_loss_tag=tag,
            configured_neutral_loss_da=configured_loss,
            neutral_loss_mass_error_ppm=0.0,
            rt_seed_min=5.03,
            rt_seed_max=5.07,
            ms1_search_rt_min=4.80,
            ms1_search_rt_max=5.30,
            ms1_seed_delta_min=0.0,
            ms1_peak_rt_start=peak_start,
            ms1_peak_rt_end=peak_end,
            ms1_height=1000.0,
            ms1_trace_quality="clean",
            ms1_scan_support_score=1.0,
            seed_event_count=1,
            ms1_peak_found=True,
            ms1_apex_rt=(peak_start + peak_end) / 2.0,
            ms1_area=10000.0,
            ms2_product_max_intensity=50000.0,
            review_priority="MEDIUM",
            reason="single MS2 NL seed; MS1 peak found",
        ),
        selected_tag_count=len(settings.selected_tag_names),
        matched_tag_count=1,
        matched_tag_names=(tag,),
        primary_tag_name=tag,
        tag_combine_mode="union",
        tag_intersection_status="not_required",
        tag_evidence_json=seed.tag_evidence_json,
    )


def _peak_config(*, resolver_mode: str = "legacy_savgol") -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("raw"),
        dll_dir=Path("dll"),
        output_csv=Path("output/xic_results.csv"),
        diagnostics_csv=Path("output/xic_diagnostics.csv"),
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode=resolver_mode,
    )


def _gaussian(
    rt: np.ndarray,
    *,
    center: float,
    sigma: float,
    height: float,
) -> np.ndarray:
    return height * np.exp(-0.5 * ((rt - center) / sigma) ** 2)


def _group(
    seeds: tuple[DiscoverySeed, ...],
    **settings_overrides: float,
):
    return group_discovery_seeds(seeds, settings=_settings(**settings_overrides))[0]


def _seed(
    *,
    scan_number: int,
    rt: float,
    precursor_mz: float = 500.0000,
    product_mz: float = 383.9526,
    product_intensity: float = 10000.0,
    observed_loss_error_ppm: float = 0.0,
) -> DiscoverySeed:
    return DiscoverySeed(
        raw_file=RAW_FILE,
        sample_stem="TumorBC2312_DNA",
        scan_number=scan_number,
        rt=rt,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        product_intensity=product_intensity,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=NEUTRAL_LOSS_DA,
        observed_loss_error_ppm=observed_loss_error_ppm,
    )


def _ok_peak(
    *,
    rt: float,
    intensity: float,
    area: float,
    start: float,
    end: float,
) -> PeakDetectionResult:
    return PeakDetectionResult(
        status="OK",
        peak=PeakResult(
            rt=rt,
            intensity=intensity,
            intensity_smoothed=intensity,
            area=area,
            peak_start=start,
            peak_end=end,
        ),
        n_points=3,
        max_smoothed=intensity,
        n_prominent_peaks=1,
    )


class _OwnerBoundBatchRaw:
    def __init__(
        self,
        source: AcquisitionSourceIdentity,
        *,
        drop_results: bool = False,
    ) -> None:
        self.acquisition_source = source
        self.drop_results = drop_results
        self.raw_chromatogram_call_count = 0
        self.request_batches: list[tuple[XICRequest, ...]] = []
        self.rt = np.asarray([7.7, 7.8, 7.9], dtype=float)
        self.intensity = np.asarray([10.0, 100.0, 10.0], dtype=float)

    def extract_xic_many(
        self,
        requests: Sequence[XICRequest],
    ) -> tuple[XICTrace, ...]:
        self.request_batches.append(tuple(requests))
        self.raw_chromatogram_call_count += len(
            {self.scan_window_for_request(request) for request in requests}
        )
        if self.drop_results:
            return ()
        return tuple(
            XICTrace.from_arrays(self.rt, self.intensity) for _request in requests
        )

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        if request.rt_min < 8.5:
            return 100, 200
        return 300, 400


class _FakeRaw:
    def __init__(self, *, rt: np.ndarray, intensity: np.ndarray) -> None:
        self.rt = rt
        self.intensity = intensity
        self.requests: list[tuple[float, float, float, float]] = []

    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        self.requests.append((mz, rt_min, rt_max, ppm_tol))
        return self.rt, self.intensity
