import csv
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import TEST_PROFILE
from tests.discovery_ms1_capture_fixtures import (
    _FakeRaw,
    _gaussian,
    _group,
    _multi_tag_settings,
    _OwnerBoundBatchRaw,
    _seed,
    _settings,
)
from xic_extractor.acquisition_contract import AcquisitionContractError
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySeedGroup
from xic_extractor.discovery.ms1_capture import (
    capture_ms1_candidates,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_acquisition_source_identity,
)
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.raw_superwindow_source import RawSuperWindowSource
from xic_extractor.xic_models import XICRequest, XICTrace


def _acquisition_totals(batches):
    """Sum the per-batch acquisition counts that timing reports."""
    names = (
        "logical_xic_request_count",
        "xic_batch_count",
        "adapter_chromatogram_call_count",
        "trace_point_count",
    )
    return SimpleNamespace(
        **{name: sum(getattr(batch, name) for batch in batches) for name in names}
    )


def test_physical_capture_accepts_search_coordinates_without_method_evidence() -> None:
    from xic_extractor.acquisition.physical_capture import (
        PhysicalPeakCapture,
        PhysicalPeakSearch,
    )

    rt = np.linspace(8.60, 9.40, 161)
    raw = _FakeRaw(
        rt=rt, intensity=_gaussian(rt, center=9.02, sigma=0.04, height=1000.0)
    )
    capture = PhysicalPeakCapture(
        raw,
        acquisition_source=raw.acquisition_source,
        adapter_namespace="tests.physical_capture",
        profile=TEST_PROFILE,
    )
    search = PhysicalPeakSearch(
        selection_subject_id="feature:sample-a:500",
        query_rt_min=9.0,
        request=XICRequest(mz=500.0, rt_min=8.4, rt_max=9.6, ppm_tol=20.0),
        allow_sparse_reacquisition=True,
    )
    first, repeated = capture.capture((search, search))

    assert first.work_ordinal == repeated.work_ordinal
    assert first.evaluation == repeated.evaluation
    assert len(raw.requests) == 1
    facts = first.evaluation.facts
    assert facts is not None
    assert facts.apex_rt_min == pytest.approx(9.02)
    # Gaussian integral, converted from counts*minutes to counts*seconds.
    assert facts.area_counts_seconds == pytest.approx(
        1000.0 * 0.04 * np.sqrt(2.0 * np.pi) * 60.0, rel=0.06
    )
    # NL is a lookup coordinate; the retained measurement is localized at raw apex.
    assert first.search.query_rt_min == search.query_rt_min
    assert first.evaluation.question.query_rt_min == pytest.approx(9.02)
    assert first.trace_quality == "clean"


def test_capture_runs_physical_peak_detection() -> None:
    group = _group((_seed(scan_number=10, rt=9.00),))
    rt = np.linspace(8.60, 9.40, 161)
    intensity = _gaussian(rt, center=9.02, sigma=0.04, height=1000.0)
    raw = _FakeRaw(rt=rt, intensity=intensity)

    candidate = _candidates_from_raw(
        raw,
        (group,),
        settings=_settings(ms1_search_padding_min=0.30),
    )[0]

    assert candidate.ms1_peak_found is True
    assert candidate.ms1_apex_rt == pytest.approx(9.02)
    assert candidate.ms1_height is not None and 900.0 < candidate.ms1_height < 1000.0
    assert candidate.ms1_area is not None
    assert candidate.ms1_seed_delta_min == pytest.approx(0.02)


@pytest.mark.parametrize(
    ("masses", "parent_rt", "status", "state"),
    [
        ((500.001,), 8.99, "compatible", "ms1_feature_nl_supported"),
        ((), 8.99, "parent_ms1_ion_missing", "ms1_feature_nl_rescued"),
        (
            (500.001, 500.002),
            8.99,
            "parent_ms1_ion_ambiguous",
            "ms1_feature_nl_rescued",
        ),
        # The 10%-height return of this Gaussian is 9.02 - 0.086 min. A parent
        # scan within the attachment tolerance of that bound still supports it;
        # one further out on the same raw basin does not.
        ((500.001,), 8.88, "compatible", "ms1_feature_nl_supported"),
        ((500.001,), 8.6, "compatible", "ms1_feature_nl_rescued"),
    ],
)
def test_discovery_verifies_inferred_seeds_after_ms1_without_moving_peak(
    monkeypatch: pytest.MonkeyPatch, masses, parent_rt, status, state
) -> None:
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
    from xic_extractor.precursor_evidence import Ms1PrecursorObservation

    group = _group(
        tuple(
            replace(
                _seed(scan_number=number, rt=rt),
                precursor_mz_basis="product_plus_neutral_loss",
                observed_loss_error_ppm=None,
                scan_precursor_mz=500.5,
                precursor_metadata=Ms2PrecursorMetadata(
                    master_scan_number=9,
                    charge=1,
                    isolation_width_da=1.6,
                    isolation_offset_da=0.0,
                ),
            )
            for number, rt in ((10, 9.0), (11, 9.01))
        )
    )
    rt = np.linspace(8.6, 9.4, 161)
    raw = _FakeRaw(rt=rt, intensity=_gaussian(rt, center=9.02, sigma=0.04, height=1000))
    settings = _settings(ms1_search_padding_min=0.3)
    baseline = _candidates_from_raw(raw, (group,), settings=settings)[0]
    batches = []

    def read_windows(self, requests):
        batches.append(requests)
        return (Ms1PrecursorObservation(9, parent_rt, masses),)

    monkeypatch.setattr(_FakeRaw, "read_ms1_mass_windows", read_windows, raising=False)
    candidate = _candidates_from_raw(raw, (group,), settings=settings)[0]
    assert len(batches) == 1 and len(batches[0]) == 1
    assert candidate.discovery_candidate_state == state
    assert candidate.precursor_mz_basis == "product_plus_neutral_loss"
    assert candidate.ms1_area == baseline.ms1_area
    assert candidate.ms1_apex_rt == baseline.ms1_apex_rt
    assert candidate.ms1_peak_rt_start == baseline.ms1_peak_rt_start
    assert candidate.ms1_peak_rt_end == baseline.ms1_peak_rt_end
    receipts = json.loads(candidate.tag_evidence_json)["DNA_dR"]["scan_receipts"]
    assert set(receipts) == {"10", "11"}
    for receipt in receipts.values():
        assert receipt["precursor_verification"]["status"] == status
        assert receipt["neutral_loss_error_ppm"] is None
        assert receipt["precursor_mz"] == 500.0


@pytest.mark.parametrize(
    ("scan_rt", "state"),
    [
        # The Gaussian's 10%-height return ends at 9.02 + 0.086 min.
        (9.05, "ms1_feature_nl_supported"),
        (9.17, "ms1_feature_nl_supported"),
        (9.25, "ms1_feature_nl_rescued"),
    ],
)
def test_measured_precursor_event_must_fall_on_its_peak(scan_rt, state) -> None:
    group = _group((_seed(scan_number=10, rt=scan_rt),))
    rt = np.linspace(8.6, 9.6, 201)
    raw = _FakeRaw(rt=rt, intensity=_gaussian(rt, center=9.02, sigma=0.04, height=1000))

    candidate = _candidates_from_raw(
        raw, (group,), settings=_settings(ms1_search_padding_min=0.3)
    )[0]

    assert candidate.ms1_peak_found
    assert candidate.ms1_apex_rt == pytest.approx(9.02, abs=0.01)
    assert candidate.discovery_candidate_state == state


@pytest.mark.parametrize(
    ("neighbor_height", "state"),
    [(0.0, "ms1_feature_nl_supported"), (1000.0, "ms1_feature_nl_rescued")],
)
def test_parent_scan_on_a_neighbor_peak_does_not_support_this_peak(
    monkeypatch: pytest.MonkeyPatch, neighbor_height, state
) -> None:
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
    from xic_extractor.precursor_evidence import Ms1PrecursorObservation

    seed = replace(
        _seed(scan_number=10, rt=8.97),
        precursor_mz_basis="product_plus_neutral_loss",
        observed_loss_error_ppm=None,
        scan_precursor_mz=500.5,
        precursor_metadata=Ms2PrecursorMetadata(
            master_scan_number=9,
            charge=1,
            isolation_width_da=1.6,
            isolation_offset_da=0.0,
        ),
    )
    rt = np.linspace(8.4, 9.6, 241)
    intensity = _gaussian(rt, center=9.02, sigma=0.04, height=1000)
    intensity += _gaussian(rt, center=8.80, sigma=0.04, height=neighbor_height)
    raw = _FakeRaw(rt=rt, intensity=intensity)

    def read_windows(self, requests):
        # The parent MS1 scan is within the attachment tolerance of this peak's
        # left bound; with the neighbor present it lies on the neighbor's apex.
        return (Ms1PrecursorObservation(9, 8.85, (500.001,)),)

    monkeypatch.setattr(_FakeRaw, "read_ms1_mass_windows", read_windows, raising=False)
    candidate = _candidates_from_raw(
        raw, (_group((seed,)),), settings=_settings(ms1_search_padding_min=0.3)
    )[0]

    assert candidate.ms1_apex_rt == pytest.approx(9.02, abs=0.01)
    assert candidate.discovery_candidate_state == state


@pytest.mark.parametrize("padding", [0.15, 0.8])
def test_capture_extracts_group_precursor_xic_with_padded_rt_window_and_ppm(
    padding: float,
) -> None:
    group = _group((_seed(scan_number=10, rt=7.80), _seed(scan_number=20, rt=7.90)))
    raw = _peak_raw(center=7.90)
    candidates = _candidates_from_raw(
        raw,
        (group,),
        settings=_settings(
            ms1_search_padding_min=padding, precursor_mz_tolerance_ppm=12.0
        ),
    )
    expected_padding = max(0.60, padding)
    assert raw.requests == [
        (
            group.precursor_mz,
            pytest.approx(7.80 - expected_padding),
            pytest.approx(7.90 + expected_padding),
            12.0,
        )
    ]
    assert len(candidates) == 1
    assert candidates[0].ms1_peak_found


def test_capture_clamps_ms1_search_rt_min_to_zero() -> None:
    group = _group((_seed(scan_number=10, rt=0.05),))
    raw = _FakeRaw(rt=np.array([], dtype=float), intensity=np.array([], dtype=float))

    candidate = _candidates_from_raw(
        raw,
        (group,),
        settings=_settings(ms1_search_padding_min=0.20),
    )[0]

    assert raw.requests == [
        (
            group.precursor_mz,
            0.0,
            pytest.approx(0.65),
            20.0,
        )
    ]
    assert candidate.ms1_search_rt_min == 0.0
    assert candidate.ms1_search_rt_max == pytest.approx(0.65)


@pytest.mark.parametrize("scans_target", [10, 1000])
def test_ms1_peak_fields_are_filled_from_physical_facts(scans_target: int) -> None:
    group = _group(
        (
            _seed(scan_number=10, rt=7.80, product_intensity=3000.0),
            _seed(scan_number=20, rt=7.90, product_intensity=9000.0),
        )
    )
    raw = _peak_raw(center=7.91, height=900.0)
    settings = _settings()
    settings = replace(
        settings,
        evidence_profile=replace(
            settings.evidence_profile,
            thresholds=replace(
                settings.evidence_profile.thresholds, scan_support_target=scans_target
            ),
        ),
    )
    result = capture_ms1_candidates(
        raw,
        (group,),
        acquisition_source=raw.acquisition_source,
        signal_processing_profile=TEST_PROFILE,
        settings=settings,
        ms1_centroid_mz=lambda *_: None,
    )
    (candidate,) = result.candidates
    (record,) = result.physical_evaluations
    facts = record.evaluation.facts
    assert facts is not None
    assert candidate.ms1_peak_found
    assert candidate.ms1_apex_rt == pytest.approx(7.91)
    assert candidate.ms1_height == facts.height
    assert candidate.ms1_area == facts.area_counts_seconds
    assert candidate.ms1_scan_support_score == min(
        1.0, facts.scan_support_count / scans_target
    )
    if scans_target == 10:
        assert candidate.ms1_scan_support_score == 1.0
    else:
        assert 0.0 < candidate.ms1_scan_support_score < 1.0
    assert candidate.ms1_peak_rt_start == facts.left_rt_min
    assert candidate.ms1_peak_rt_end == facts.right_rt_min
    assert candidate.ms1_seed_delta_min == pytest.approx(0.01)
    assert candidate.ms1_trace_quality == "clean"
    assert candidate.review_priority == "HIGH"
    assert candidate.reason == "strong MS2 NL seed group; MS1 peak found near seed RT"
    assert candidate.best_ms2_scan_id == 20
    assert candidate.seed_scan_ids == (10, 20)
    assert candidate.ms2_product_max_intensity == 9000.0


@pytest.mark.parametrize(
    "rt,intensity",
    [
        (np.array([], dtype=float), np.array([], dtype=float)),
        (np.linspace(7.2, 8.4, 241), np.zeros(241)),
    ],
)
def test_missing_or_empty_ms1_trace_still_creates_low_priority_candidate(
    rt: np.ndarray, intensity: np.ndarray
) -> None:
    group = _group((_seed(scan_number=10, rt=7.80),))
    candidate = _candidates_from_raw(
        _FakeRaw(rt=rt, intensity=intensity), (group,), settings=_settings()
    )[0]
    assert not candidate.ms1_peak_found
    assert candidate.ms1_apex_rt is None
    assert candidate.ms1_height is None
    assert candidate.ms1_area is None
    assert candidate.ms1_peak_rt_start is None
    assert candidate.ms1_peak_rt_end is None
    assert candidate.ms1_seed_delta_min is None
    assert candidate.ms1_scan_support_score is None
    assert candidate.ms1_trace_quality == "physical_unavailable"
    assert candidate.review_priority == "LOW"
    assert candidate.reason.startswith("physical MS1 evaluation unavailable:")


def test_single_seed_with_ms1_peak_is_medium_priority() -> None:
    group = _group((_seed(scan_number=10, rt=7.80),))
    raw = _peak_raw(center=7.8, sigma=0.06, height=100)

    candidate = _candidates_from_raw(
        raw,
        (group,),
        settings=_settings(ms1_search_padding_min=0.20),
    )[0]

    assert candidate.review_priority == "MEDIUM"
    assert candidate.reason == "single MS2 NL seed; MS1 peak found"


@pytest.mark.parametrize("sigma", [0.12, 0.15])
def test_single_raw_basin_keeps_events_outside_quantitative_return(
    sigma: float,
) -> None:
    group = _group(
        (
            _seed(scan_number=10, rt=7.80, product_intensity=3000.0),
            _seed(scan_number=20, rt=7.90, product_intensity=9000.0),
        )
    )
    raw = _peak_raw(center=8.16, sigma=sigma, height=100)

    candidates = _candidates_from_raw(
        raw,
        (group,),
        settings=_settings(ms1_search_padding_min=0.20),
    )
    candidate = next(c for c in candidates if 20 in c.seed_scan_ids)

    # Each analytic input is one positive Gaussian with no intervening raw
    # valley. Its 10% return is an integration bound, not a different NL owner.
    assert candidate.ms1_peak_found
    assert candidate.ms1_seed_delta_min == pytest.approx(0.26)
    assert candidate.review_priority == "MEDIUM"
    assert candidate.seed_scan_ids == (10, 20)
    assert candidate.ms1_area is not None and candidate.ms1_area > 0
    assert not any(not c.ms1_peak_found for c in candidates)


def test_capture_merges_candidates_inside_same_ms1_peak_boundary() -> None:
    first_group = _group((_seed(scan_number=10, rt=7.80, product_intensity=3000.0),))
    second_group = _group((_seed(scan_number=20, rt=8.04, product_intensity=9000.0),))
    raw = _peak_raw(center=7.92, sigma=0.1, height=900)

    candidates = _candidates_from_raw(
        raw,
        (first_group, second_group),
        settings=_settings(ms1_search_padding_min=0.20),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.best_ms2_scan_id == 20
    assert candidate.seed_scan_ids == (10, 20)
    assert candidate.seed_event_count == 2
    assert candidate.rt_seed_min == pytest.approx(7.80)
    assert candidate.rt_seed_max == pytest.approx(8.04)
    assert candidate.ms1_search_rt_min == pytest.approx(7.20)
    assert candidate.ms1_search_rt_max == pytest.approx(8.64)
    assert candidate.ms1_area is not None and candidate.ms1_area > 0
    assert candidate.ms2_product_max_intensity == 9000.0
    assert candidate.review_priority == "HIGH"
    # Evidence is combined before candidate creation, not by merging quantities.
    assert candidate.reason == "strong MS2 NL seed group; MS1 peak found near seed RT"


@pytest.mark.parametrize("dominant_tag", ["dR", "R"])
@pytest.mark.parametrize("equal_intensity", [False, True])
def test_verified_multitag_peak_persists_each_tags_best_scan(
    tmp_path: Path, dominant_tag: str, equal_intensity: bool
) -> None:
    from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata

    settings = _multi_tag_settings()
    seeds = []
    expected_by_tag = {}
    for tag_index, (tag, loss) in enumerate((("dR", 116.047344), ("R", 132.0423))):
        best_intensity = 9000.0 if tag == dominant_tag else 5000.0
        for is_best in (False, True):
            scan = 10 + tag_index * 10 + int(is_best)
            loss_error_da = 0.0001 if is_best else 0.0003
            seed = replace(
                _seed(
                    scan_number=scan,
                    rt=9.01 if is_best else 9.0,
                    product_mz=500.0 - loss - loss_error_da,
                    product_intensity=(
                        best_intensity if is_best or equal_intensity else 1000.0
                    ),
                    observed_loss_error_ppm=loss_error_da / loss * 1e6,
                ),
                neutral_loss_tag=tag,
                configured_neutral_loss_da=loss,
                observed_neutral_loss_da=loss + loss_error_da,
                scan_precursor_mz=500.0,
                precursor_metadata=Ms2PrecursorMetadata(
                    master_scan_number=scan - 1,
                    charge=1,
                    isolation_width_da=1.6,
                    isolation_offset_da=0.0,
                ),
            )
            seeds.append(seed)
            if is_best:
                expected_by_tag[tag] = seed

    rt = np.linspace(8.6, 9.4, 161)
    candidates = _candidates_from_raw(
        _FakeRaw(rt=rt, intensity=_gaussian(rt, center=9.02, sigma=0.04, height=1000)),
        group_discovery_seeds(seeds, settings=settings),
        settings=settings,
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.matched_tag_names == ("dR", "R")
    assert candidate.seed_scan_ids == (10, 11, 20, 21)
    assert candidate.best_ms2_scan_id == expected_by_tag["dR"].scan_number

    path = write_discovery_candidates_csv(tmp_path / "candidates.csv", candidates)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        (row,) = csv.DictReader(stream)
    evidence = json.loads(row["tag_evidence_json"])
    for tag, best in expected_by_tag.items():
        summary = evidence[tag]
        assert summary["max_intensity"] == best.product_intensity
        assert summary["product_mz"] == best.product_mz
        assert summary["neutral_loss_error_ppm"] == best.observed_loss_error_ppm
        assert summary["neutral_loss_error_da"] == pytest.approx(0.0001)
        assert summary["scan_precursor_mz"] == best.scan_precursor_mz
        assert (
            summary["precursor_metadata"]["master_scan_number"] == best.scan_number - 1
        )
        assert summary["scan_count"] == 2
        assert set(summary["scan_receipts"]) == {
            str(seed.scan_number) for seed in seeds if seed.neutral_loss_tag == tag
        }
        for seed in seeds:
            if seed.neutral_loss_tag == tag:
                receipt = summary["scan_receipts"][str(seed.scan_number)]
                assert receipt["product_mz"] == seed.product_mz
                assert receipt["neutral_loss_error_ppm"] == seed.observed_loss_error_ppm


def test_capture_does_not_merge_distinct_ms1_peak_boundaries() -> None:
    first_group = _group((_seed(scan_number=10, rt=7.80, product_intensity=3000.0),))
    second_group = _group((_seed(scan_number=20, rt=8.04, product_intensity=9000.0),))
    rt = np.linspace(7.2, 8.6, 281)
    raw = _FakeRaw(
        rt=rt,
        intensity=_gaussian(rt, center=7.80, sigma=0.025, height=900.0)
        + _gaussian(rt, center=8.04, sigma=0.025, height=850.0),
    )

    candidates = _candidates_from_raw(
        raw,
        (first_group, second_group),
        settings=_settings(ms1_search_padding_min=0.20),
    )

    assert len(candidates) == 2
    assert [candidate.seed_scan_ids for candidate in candidates] == [(10,), (20,)]


def test_ms1_seed_delta_is_signed_but_priority_uses_absolute_distance() -> None:
    group = _group(
        (
            _seed(scan_number=10, rt=7.80, product_intensity=3000.0),
            _seed(scan_number=20, rt=7.90, product_intensity=9000.0),
        )
    )
    raw = _peak_raw(center=7.7, sigma=0.1, height=100)

    candidate = _candidates_from_raw(
        raw,
        (group,),
        settings=_settings(ms1_search_padding_min=0.20),
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
    raw = _peak_raw(center=7.9, sigma=0.06, height=500)

    candidate = _candidates_from_raw(
        raw,
        (group,),
        settings=_settings(),
    )[0]

    assert candidate.best_ms2_scan_id == expected_scan
    assert candidate.best_seed_rt == expected_rt
    assert candidate.ms1_seed_delta_min == pytest.approx(7.90 - expected_rt)


def test_owner_acquisition_shares_identical_contexts_and_preserves_separate_windows(
    tmp_path: Path,
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
    later = _group((_seed(scan_number=30, rt=9.20),))
    empty = replace(first, seeds=())
    raw = _OwnerBoundBatchRaw(source)

    result = capture_ms1_candidates(
        raw,
        (first, duplicate, later, empty),
        acquisition_source=source,
        signal_processing_profile=TEST_PROFILE,
        settings=_settings(),
        ms1_centroid_mz=lambda *_: None,
    )

    measurements = result.measurements
    runtime_metrics = _acquisition_totals(measurements.acquisition_batches)
    assert measurements.input_group_count == 4
    assert measurements.empty_group_count == 1
    assert runtime_metrics.logical_xic_request_count == 2
    assert runtime_metrics.xic_batch_count == 1
    assert runtime_metrics.adapter_chromatogram_call_count == 2
    assert runtime_metrics.trace_point_count == 42
    assert measurements.exact_scan_window_count == 2
    assert measurements.predicted_chunked_call_count == 2
    assert measurements.batch_max_requests == 256
    assert [len(batch) for batch in raw.request_batches] == [2]
    assert raw.request_batches[0][0] != raw.request_batches[0][1]


def test_owner_acquisition_batches_distinct_contexts_at_256(
    tmp_path: Path,
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
        replace(
            group,
            precursor_mz=500.0 + index,
            seeds=(_seed(scan_number=index + 1, rt=7.80, precursor_mz=500.0 + index),),
        )
        for index in range(257)
    )
    raw = _OwnerBoundBatchRaw(source)

    result = capture_ms1_candidates(
        raw,
        groups,
        acquisition_source=source,
        signal_processing_profile=TEST_PROFILE,
        settings=_settings(),
        ms1_centroid_mz=lambda *_: None,
    )

    metrics = _acquisition_totals(result.measurements.acquisition_batches)
    assert [len(batch) for batch in raw.request_batches] == [256, 1]
    assert metrics.logical_xic_request_count == 257
    assert metrics.xic_batch_count == 2
    assert metrics.adapter_chromatogram_call_count == 2
    assert metrics.trace_point_count == 5397
    assert result.measurements.exact_scan_window_count == 1
    assert result.measurements.predicted_chunked_call_count == 2
    assert result.measurements.batch_max_requests == 256


def test_owner_acquisition_reads_overlapping_windows_once_with_exact_traces(
    tmp_path: Path,
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
        _group(
            (
                _seed(
                    scan_number=20,
                    rt=7.90,
                    precursor_mz=600.0,
                    product_mz=483.9526,
                ),
            )
        ),
    )
    raw = _ScanGridRaw(source)

    result = capture_ms1_candidates(
        raw,
        groups,
        acquisition_source=source,
        signal_processing_profile=TEST_PROFILE,
        settings=_settings(),
        ms1_centroid_mz=lambda *_: None,
    )

    batches = result.measurements.acquisition_batches
    runtime_metrics = _acquisition_totals(batches)
    acquired = [trace for batch in batches for trace in batch.traces]
    # exact_trace is a strictly increasing ramp: both questions require the
    # one bounded continuation. The two overlapping channels share each round.
    assert result.measurements.initial_request_count == 2
    assert result.measurements.reacquisition_request_count == 2
    assert runtime_metrics.logical_xic_request_count == 4
    assert result.measurements.exact_scan_window_count == 4
    assert runtime_metrics.adapter_chromatogram_call_count == 2
    for item in acquired:
        exact = raw.exact_trace(item.coordinate.request)
        assert item.trace.rt.tolist() == exact.rt.tolist()
        assert item.trace.intensity.tolist() == exact.intensity.tolist()


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
        capture_ms1_candidates(
            raw,
            (_group((_seed(scan_number=10, rt=7.80),)),),
            acquisition_source=source,
            signal_processing_profile=TEST_PROFILE,
            settings=_settings(),
            ms1_centroid_mz=lambda *_: None,
        )


class _ScanGridRaw:
    """Owner-bound RAW with one scan per 0.01 min whose readings never change."""

    def __init__(self, source: AcquisitionSourceIdentity) -> None:
        self.acquisition_source = source
        self.raw_chromatogram_call_count = 0

    def extract_xic_many(
        self,
        requests: Sequence[XICRequest],
    ) -> tuple[XICTrace, ...]:
        self.raw_chromatogram_call_count += len(
            {self.scan_window_for_request(request) for request in requests}
        )
        return tuple(self.exact_trace(request) for request in requests)

    def exact_trace(self, request: XICRequest) -> XICTrace:
        start, end = self.scan_window_for_request(request)
        scans = np.arange(start, end + 1)
        return XICTrace.from_arrays(scans / 100, request.mz + scans)

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return round(request.rt_min * 100), round(request.rt_max * 100)

    def retention_time_for_scan(self, scan_number: int) -> float:
        return scan_number / 100

    def batch_xic_source(self):
        return RawSuperWindowSource(self)

    def read_ms1_mass_windows(self, requests):
        """This fake records no parent-MS1 centroids."""
        return (None,) * len(requests)


def _candidates_from_raw(
    raw: _FakeRaw,
    groups: Sequence[DiscoverySeedGroup],
    *,
    settings: DiscoverySettings,
) -> tuple[DiscoveryCandidate, ...]:
    return capture_ms1_candidates(
        raw,
        groups,
        acquisition_source=raw.acquisition_source,
        signal_processing_profile=TEST_PROFILE,
        settings=settings,
        ms1_centroid_mz=lambda *_: None,
    ).candidates


def _peak_raw(
    *, center: float, sigma: float = 0.06, height: float = 1000.0
) -> _FakeRaw:
    rt = np.linspace(center - 0.6, center + 0.6, 241)
    return _FakeRaw(
        rt=rt, intensity=_gaussian(rt, center=center, sigma=sigma, height=height)
    )
