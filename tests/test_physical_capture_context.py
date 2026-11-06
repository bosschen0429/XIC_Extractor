"""Shared XIC context is a physical population, not per-question cropping."""

from dataclasses import replace

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import TEST_PROFILE
from tests.discovery_ms1_capture_fixtures import (
    _BoundedRaw,
    _FakeRaw,
    _gaussian,
    _seed,
    _settings,
)
from xic_extractor.acquisition import physical_capture
from xic_extractor.acquisition.physical_capture import (
    PhysicalPeakCapture,
    PhysicalPeakSearch,
)
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.ms1_capture import capture_ms1_candidates
from xic_extractor.raw_superwindow_source import RawSuperWindowSource
from xic_extractor.xic_models import XICRequest


def test_overlapping_queries_share_peak_population_and_prepare_once(monkeypatch):
    rt = np.linspace(4.0, 6.0, 801)
    intensity = _gaussian(rt, center=4.8, sigma=0.04, height=1000.0)
    intensity += _gaussian(rt, center=5.25, sigma=0.05, height=2000.0)
    raw = _BoundedRaw(rt=rt, intensity=intensity)
    counts = {
        "prepare": 0,
        "enumerate": 0,
        "raw_split": 0,
        "measure": 0,
        "descriptor": 0,
    }
    prepare = physical_capture.prepare_physical_signal
    # This is the unchanged physical owner, not a substitute peak detector.
    from xic_extractor.peak_detection import physical_peak_evaluation as owner

    enumerate_segments = owner.enumerate_physical_segment_universe
    raw_split = physical_capture.discovery_raw_peak_components
    measure = owner.measure_chrom_peak_component
    descriptor = owner._physical_raw_descriptor_projection

    def counted_raw_split(*args, **kwargs):
        counts["raw_split"] += 1
        return raw_split(*args, **kwargs)

    def counted_measure(*args, **kwargs):
        counts["measure"] += 1
        return measure(*args, **kwargs)

    def counted_descriptor(*args, **kwargs):
        counts["descriptor"] += 1
        return descriptor(*args, **kwargs)

    monkeypatch.setattr(
        physical_capture, "discovery_raw_peak_components", counted_raw_split
    )
    monkeypatch.setattr(owner, "measure_chrom_peak_component", counted_measure)
    monkeypatch.setattr(
        owner, "_physical_raw_descriptor_projection", counted_descriptor
    )

    def counted_prepare(*args, **kwargs):
        counts["prepare"] += 1
        return prepare(*args, **kwargs)

    def counted_enumerate(*args, **kwargs):
        counts["enumerate"] += 1
        return enumerate_segments(*args, **kwargs)

    monkeypatch.setattr(physical_capture, "prepare_physical_signal", counted_prepare)
    monkeypatch.setattr(owner, "enumerate_physical_segment_universe", counted_enumerate)
    monkeypatch.setattr(
        physical_capture, "enumerate_physical_segment_universe", counted_enumerate
    )
    capture = PhysicalPeakCapture(
        RawSuperWindowSource(raw),
        acquisition_source=raw.acquisition_source,
        adapter_namespace="tests.shared_context",
        profile=TEST_PROFILE,
    )
    searches = (
        PhysicalPeakSearch("left", 4.8, XICRequest(500.0, 4.2, 5.4, 20.0), True),
        PhysicalPeakSearch("right", 5.25, XICRequest(500.0, 4.65, 5.85, 20.0), True),
    )
    left, right, repeated = capture.capture((*searches, searches[0]))

    assert left.evaluation.state == right.evaluation.state == "available"
    assert left.evaluation.facts.apex_rt_min == pytest.approx(4.8)
    assert right.evaluation.facts.apex_rt_min == pytest.approx(5.25)
    assert left.evaluation.facts.area_counts_seconds == pytest.approx(
        1000 * 0.04 * np.sqrt(2 * np.pi) * 60, rel=0.06
    )
    assert right.evaluation.facts.area_counts_seconds == pytest.approx(
        2000 * 0.05 * np.sqrt(2 * np.pi) * 60, rel=0.06
    )
    assert left.evaluation.answer.selected_segment != (
        right.evaluation.answer.selected_segment
    )
    assert left.evaluation.segment_universe == right.evaluation.segment_universe
    assert (
        left.search.request
        == right.search.request
        == XICRequest(500.0, 4.2, 5.85, 20.0)
    )
    assert capture.initial_request_count == 1
    assert repeated.work_ordinal == left.work_ordinal
    replay = capture.reselect(left, selection_subject_id="left-copy", query_rt_min=4.8)
    assert (
        replay.evaluation.answer.selected_segment
        == left.evaluation.answer.selected_segment
    )
    assert (
        replay.evaluation.facts.area_counts_seconds
        == left.evaluation.facts.area_counts_seconds
    )
    # Two physical peaks are measured once each, regardless of NL duplicates
    # or a later query for the same raw basin.
    assert counts == {
        "prepare": 1,
        "enumerate": 1,
        "raw_split": 1,
        "measure": 2,
        "descriptor": 2,
    }


def test_sparse_extension_reunites_contexts_without_repeating_extension():
    rt = np.linspace(3.0, 7.0, 81)
    intensity = _gaussian(rt, center=4.8, sigma=0.07, height=1000.0)
    intensity += _gaussian(rt, center=6.0, sigma=0.07, height=2000.0)
    raw = _BoundedRaw(rt=rt, intensity=intensity)
    capture = PhysicalPeakCapture(
        RawSuperWindowSource(raw),
        acquisition_source=raw.acquisition_source,
        adapter_namespace="tests.extended_context",
        profile=TEST_PROFILE,
    )
    left, right = capture.capture(
        (
            PhysicalPeakSearch("left", 4.8, XICRequest(500.0, 4.75, 4.85, 20.0), True),
            PhysicalPeakSearch("right", 6.0, XICRequest(500.0, 5.0, 6.5, 20.0), True),
        )
    )
    assert capture.initial_request_count == 2
    assert capture.reacquisition_request_count == 1
    assert left.search.request == right.search.request
    assert left.search.request.rt_min < 4.75
    assert left.search.request.rt_max == 6.5
    assert left.evaluation.segment_universe == right.evaluation.segment_universe
    assert right.evaluation.facts.apex_rt_min == pytest.approx(6.0)
    # One bounded extension does not override the owner's remaining uncertainty
    # or keep enlarging the population until an acceptable answer is obtained.
    assert left.evaluation.reason == "incomplete_peak_context"
    assert left.evaluation.facts is None


def test_mass_channel_uses_existing_ppm_rule_without_isotope_or_ppm_merging():
    rt = np.linspace(4.0, 6.0, 801)
    raw = _FakeRaw(
        rt=rt, intensity=_gaussian(rt, center=5.0, sigma=0.04, height=1000.0)
    )
    capture = PhysicalPeakCapture(
        raw,
        acquisition_source=raw.acquisition_source,
        adapter_namespace="tests.distinct_channels",
        profile=TEST_PROFILE,
    )
    first = PhysicalPeakSearch("a", 5.0, XICRequest(500.0, 4.4, 5.6, 20.0), True)
    searches = (
        first,
        replace(
            first, selection_subject_id="b", request=replace(first.request, mz=500.001)
        ),
        replace(
            first,
            selection_subject_id="c",
            request=replace(first.request, ppm_tol=10.0),
        ),
    )
    captured = capture.capture(searches)
    # Every mass window is actually read. Equality over the complete final
    # signal, not candidate mass tolerance alone, permits shared preparation.
    # Different ppm windows and isotope-separated channels stay separate.
    assert captured[0].search.request == captured[1].search.request
    assert captured[0].work_ordinal == captured[1].work_ordinal
    assert captured[2].search.request.ppm_tol == 10.0
    assert capture.initial_request_count == 3
    isotope = replace(
        first,
        selection_subject_id="isotope",
        request=replace(first.request, mz=501.0033548),
    )
    isotope_capture = PhysicalPeakCapture(
        raw,
        acquisition_source=raw.acquisition_source,
        adapter_namespace="tests.isotope_channels",
        profile=TEST_PROFILE,
    )
    separated = isotope_capture.capture((first, isotope))
    assert separated[0].search.request.mz != separated[1].search.request.mz
    assert isotope_capture.initial_request_count == 2


def test_seed_grouping_order_and_duplicates_do_not_change_physical_peaks():
    rt = np.linspace(4.0, 6.0, 2001)
    intensity = _gaussian(rt, center=5.0, sigma=0.015, height=1000.0)
    intensity += _gaussian(rt, center=5.15, sigma=0.02, height=2000.0)
    seeds = tuple(
        _seed(scan_number=i + 10, rt=t) for i, t in enumerate((5.0, 5.15, 5.3))
    )
    settings = _settings()
    grouped = group_discovery_seeds(seeds, settings=settings)
    separate = tuple(
        group_discovery_seeds((seed,), settings=settings)[0] for seed in seeds
    )
    variants = (
        grouped,
        separate,
        tuple(reversed(separate)),
        separate + separate,
        group_discovery_seeds(seeds + seeds, settings=settings),
    )
    previous = None
    for groups in variants:
        raw = _BoundedRaw(rt=rt, intensity=intensity)
        result = capture_ms1_candidates(
            raw,
            groups,
            acquisition_source=raw.acquisition_source,
            settings=settings,
            signal_processing_profile=TEST_PROFILE,
            ms1_centroid_mz=lambda *_: None,
        )
        measured = [c for c in result.candidates if c.ms1_peak_found]
        assert len(result.physical_evaluations) == 2
        assert (
            len({b.representative_work_ordinal for b in result.physical_bindings}) == 2
        )
        assert sorted(c.ms1_apex_rt for c in measured) == pytest.approx([5.0, 5.15])
        # The analytic second Gaussian remains one raw basin past its 10%
        # quantitative return; its tail event is not an orphan component.
        assert {c.seed_scan_ids for c in measured} == {(10,), (11, 12)}
        orphan = [c for c in result.candidates if not c.ms1_peak_found]
        assert orphan == []
        assert result.measurements.initial_request_count == 1
        snapshot = sorted(
            (
                c.seed_scan_ids,
                c.ms1_apex_rt,
                c.ms1_area,
                c.ms1_peak_rt_start,
                c.ms1_peak_rt_end,
                c.discovery_candidate_state,
            )
            for c in result.candidates
        )
        if previous is not None:
            assert snapshot == previous
        previous = snapshot


def test_identical_narrow_probes_do_not_substitute_final_mass_windows():
    rt = np.linspace(1.0, 8.0, 1401)
    common = _gaussian(rt, center=5.0, sigma=0.3, height=1000.0)
    extra = _gaussian(rt, center=4.0, sigma=0.1, height=1500.0)
    extra[rt >= 5.5] = 0.0

    class MassResolvedRaw(_BoundedRaw):
        def extract_xic_many(self, requests):
            from xic_extractor.xic_models import XICTrace

            return tuple(
                XICTrace.from_arrays(
                    rt[(rt >= request.rt_min) & (rt <= request.rt_max)],
                    (common + (extra if request.mz > 500.0 else 0))[
                        (rt >= request.rt_min) & (rt <= request.rt_max)
                    ],
                )
                for request in requests
            )

    raw = MassResolvedRaw(rt=rt, intensity=common)
    capture = PhysicalPeakCapture(
        raw,
        acquisition_source=raw.acquisition_source,
        adapter_namespace="tests.final_mass_windows",
        profile=TEST_PROFILE,
    )
    searches = tuple(
        PhysicalPeakSearch(str(i), 6.2, XICRequest(mz, 5.6, 6.8, 20.0), True)
        for i, mz in enumerate((500.0, 500.001))
    )
    first, second = capture.capture(searches)
    assert capture.initial_request_count == capture.reacquisition_request_count == 2
    assert first.search.request.mz == 500.0
    assert second.search.request.mz == 500.001
    assert first.work_ordinal != second.work_ordinal
    assert not np.array_equal(
        first.acquired_trace.trace.intensity, second.acquired_trace.trace.intensity
    )
    assert first.evaluation.facts is not None
    assert second.evaluation.facts is not None
