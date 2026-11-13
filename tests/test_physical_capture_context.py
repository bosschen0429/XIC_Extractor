"""Shared XIC context is a physical population, not per-question cropping."""

from dataclasses import replace

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import TEST_PROFILE
from tests.discovery_csv_fixtures import located_scan_ids
from tests.discovery_ms1_capture_fixtures import (
    _BoundedRaw,
    _FakeRaw,
    _gaussian,
    _seed,
    _settings,
)
from xic_extractor.acquisition.physical_capture import (
    PhysicalPeakCapture,
    PhysicalPeakSearch,
)
from xic_extractor.discovery.ion_grouping import build_discovery_ions
from xic_extractor.discovery.ms1_capture import capture_ms1_peaks
from xic_extractor.raw_superwindow_source import RawSuperWindowSource
from xic_extractor.xic_models import XICRequest


def _capture(raw, namespace: str) -> PhysicalPeakCapture:
    return PhysicalPeakCapture(
        RawSuperWindowSource(raw),
        acquisition_source=raw.acquisition_source,
        adapter_namespace=namespace,
        profile=TEST_PROFILE,
        context_half_width_peak_widths=2.0,
    )


def test_each_located_peak_is_measured_once_in_its_own_centred_window():
    rt = np.linspace(4.0, 6.0, 801)
    intensity = _gaussian(rt, center=4.8, sigma=0.04, height=1000.0)
    intensity += _gaussian(rt, center=5.25, sigma=0.05, height=2000.0)
    raw = _BoundedRaw(rt=rt, intensity=intensity)
    capture = _capture(raw, "tests.centred_context")
    searches = (
        PhysicalPeakSearch("left", 4.8, XICRequest(500.0, 4.2, 5.4, 20.0)),
        PhysicalPeakSearch("right", 5.25, XICRequest(500.0, 4.65, 5.85, 20.0)),
        # Another query on the left peak's flank locates the same apex.
        PhysicalPeakSearch("left-flank", 4.84, XICRequest(500.0, 4.24, 5.44, 20.0)),
    )
    left, right, flank, repeated = capture.capture((*searches, searches[0]))

    assert left.evaluation.facts.apex_rt_min == pytest.approx(4.8)
    assert right.evaluation.facts.apex_rt_min == pytest.approx(5.25)
    assert left.evaluation.facts.area_counts_seconds == pytest.approx(
        1000 * 0.04 * np.sqrt(2 * np.pi) * 60, rel=0.06
    )
    assert right.evaluation.facts.area_counts_seconds == pytest.approx(
        2000 * 0.05 * np.sqrt(2 * np.pi) * 60, rel=0.06
    )
    # Each search window is read on its own, only to locate its peak; the
    # repeated search shares its identical window.
    assert capture.initial_request_count == 3
    # Each peak is read once more, centred on its own raw apex.
    assert capture.reacquisition_request_count == 2
    for captured in (left, right):
        request = captured.search.request
        apex = captured.evaluation.question.query_rt_min
        assert request.rt_max - apex == pytest.approx(apex - request.rt_min)
    assert left.search.request != right.search.request
    assert flank.search.request == left.search.request
    assert flank.work_ordinal == repeated.work_ordinal == left.work_ordinal
    assert flank.search.query_rt_min == 4.84


def test_measured_context_never_exceeds_the_peak_widths_of_its_located_peak():
    """Each measurement window is the located raw apex +/- 2 located widths."""
    from xic_extractor.peak_detection.physical_peak_population import (
        measure_physical_peak_population,
    )

    rt = np.linspace(0.0, 30.0, 6001)
    intensity = 50.0 + _gaussian(rt, center=10.0, sigma=0.05, height=1000.0)
    intensity += _gaussian(rt, center=10.4, sigma=0.08, height=3000.0)
    intensity += _gaussian(rt, center=20.0, sigma=0.2, height=800.0)
    raw = _BoundedRaw(rt=rt, intensity=intensity)
    capture = _capture(raw, "tests.bounded_context")
    searches = tuple(
        PhysicalPeakSearch(str(t), t, XICRequest(500.0, t - 0.6, t + 0.6, 20.0))
        for t in (9.95, 10.4, 10.45, 19.9, 20.3)
    )
    captured = capture.capture(searches)
    # Chained events never stretch a search window: each event locates its peak
    # in its own window, and the widest width located for an apex sets W.
    widest: dict[float, float] = {}
    for search in searches:
        request = search.request
        inside = (rt >= request.rt_min) & (rt <= request.rt_max)
        population = measure_physical_peak_population(
            rt[inside],
            raw.intensity[inside],
            context_rt_start_min=request.rt_min,
            context_rt_end_min=request.rt_max,
            profile=TEST_PROFILE,
        )
        located = population.segment_at(search.query_rt_min)
        apex = population.raw_apex_rt(located)
        width = located.right_rt_min - located.left_rt_min
        widest[apex] = max(width, widest.get(apex, 0.0))
    for item in captured:
        assert item.evaluation.facts is not None
        request = item.search.request
        apex = (request.rt_min + request.rt_max) / 2
        (located_apex,) = (a for a in widest if a == pytest.approx(apex))
        assert request.rt_max - request.rt_min == pytest.approx(
            4.0 * widest[located_apex]
        )
        assert request.rt_max - request.rt_min <= 4.0 * 1.2 + 1e-9


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
        context_half_width_peak_widths=2.0,
    )
    first = PhysicalPeakSearch("a", 5.0, XICRequest(500.0, 4.4, 5.6, 20.0))
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
        context_half_width_peak_widths=2.0,
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
    grouped = build_discovery_ions(seeds, settings=settings)
    separate = tuple(
        build_discovery_ions((seed,), settings=settings)[0] for seed in seeds
    )
    variants = (
        grouped,
        separate,
        tuple(reversed(separate)),
        separate + separate,
        build_discovery_ions(seeds + seeds, settings=settings),
    )
    previous = None
    for groups in variants:
        raw = _BoundedRaw(rt=rt, intensity=intensity)
        result = capture_ms1_peaks(
            raw,
            groups,
            acquisition_source=raw.acquisition_source,
            settings=settings,
            signal_processing_profile=TEST_PROFILE,
            ms1_centroid=lambda *_: None,
        )
        measured = list(result.peaks)
        assert len(result.physical_bindings) == 2
        assert {b.final_ordinal for b in result.physical_bindings} == {0, 1}
        assert sorted(c.ms1_apex_rt for c in measured) == pytest.approx([5.0, 5.15])
        # The analytic second Gaussian remains one raw basin past its 10%
        # quantitative return; its tail event is not an orphan component.
        assert {located_scan_ids(c) for c in measured} == {(10,), (11, 12)}
        # Each event's own search window; duplicates share theirs.
        assert result.measurements.initial_request_count == 3
        snapshot = sorted(
            (
                located_scan_ids(c),
                b.peak.ms1_apex_rt,
                b.physical_evaluation.facts,
                b.peak.status,
            )
            for b in result.physical_bindings
            for c in (b.peak,)
        )
        if previous is not None:
            assert snapshot == previous
        previous = snapshot


def test_identical_search_windows_do_not_substitute_centred_mass_windows():
    rt = np.linspace(1.0, 8.0, 1401)
    common = _gaussian(rt, center=6.2, sigma=0.1, height=1000.0)
    # Only the wider mass window reads another ion, outside both search windows
    # but inside the peak-centred windows.
    extra = _gaussian(rt, center=6.95, sigma=0.03, height=1500.0)
    extra[rt <= 6.85] = 0.0

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
        context_half_width_peak_widths=2.0,
    )
    searches = tuple(
        PhysicalPeakSearch(str(i), 6.2, XICRequest(mz, 5.6, 6.8, 20.0))
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


def _measured_peaks(seeds, intensity, rt):
    """Each measured peak's facts, bounds, status and NL membership by event RT."""
    settings = _settings()
    raw = _BoundedRaw(rt=rt, intensity=intensity)
    result = capture_ms1_peaks(
        raw,
        build_discovery_ions(seeds, settings=settings),
        acquisition_source=raw.acquisition_source,
        settings=settings,
        signal_processing_profile=TEST_PROFILE,
        ms1_centroid=lambda *_: None,
    )
    return {
        tuple(sorted(event.rt for event in binding.peak.located_events)): (
            binding.physical_evaluation.facts,
            binding.peak.status,
            binding.peak.attached_event_count,
            binding.peak.ms1_search_rt_min,
            binding.peak.ms1_search_rt_max,
            binding.peak.query_rt_min,
        )
        for binding in result.physical_bindings
    }


@pytest.mark.parametrize(
    "unrelated_rt",
    [
        # Its search window overlaps the peak's search windows.
        5.7,
        # Its search window stays apart on the same ion.
        8.0,
    ],
)
def test_unrelated_event_order_and_ids_do_not_change_a_peaks_measurement(
    unrelated_rt,
):
    rt = np.linspace(2.0, 12.0, 4001)
    # A sloping background makes the measurement depend on the window it is
    # prepared in, as RAW traces do.
    intensity = 300.0 + 40.0 * (rt - 2.0)
    intensity += _gaussian(rt, center=5.0, sigma=0.05, height=4000.0)
    intensity += _gaussian(rt, center=5.8, sigma=0.06, height=9000.0)
    intensity += _gaussian(rt, center=8.0, sigma=0.06, height=7000.0)
    peak = (_seed(scan_number=10, rt=5.0), _seed(scan_number=11, rt=5.03))
    unrelated = _seed(scan_number=20, rt=unrelated_rt)

    alone = _measured_peaks(peak, intensity, rt)
    assert set(alone) == {(5.0, 5.03)}
    variants = (
        peak + (unrelated,),
        (unrelated,) + tuple(reversed(peak)),
        # The same events under other scan numbers, which name them.
        (
            _seed(scan_number=90, rt=5.03),
            _seed(scan_number=91, rt=unrelated_rt),
            _seed(scan_number=92, rt=5.0),
        ),
    )
    for seeds in variants:
        measured = _measured_peaks(seeds, intensity, rt)
        assert set(measured) == {(5.0, 5.03), (unrelated_rt,)}
        assert measured[(5.0, 5.03)] == alone[(5.0, 5.03)]


def test_apexes_reframed_onto_one_peak_share_its_single_measurement():
    """Two search windows locate different apexes whose centred windows both
    hold one peak with another raw apex; that peak is measured once."""
    import json
    from pathlib import Path

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )

    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures" / "discovery_reframed_shared_apex.json"
        ).read_text(encoding="utf-8")
    )
    rt = np.asarray([float.fromhex(v) for v in fixture["trace"]["rt"]])
    intensity = np.asarray([float.fromhex(v) for v in fixture["trace"]["intensity"]])
    mz = fixture["precursor_mz"]
    seeds = tuple(
        replace(
            _seed(
                scan_number=scan,
                rt=event_rt,
                precursor_mz=mz,
                product_mz=mz - 116.0474,
            ),
            precursor_mz_basis="product_plus_neutral_loss",
        )
        for scan, event_rt in zip(
            fixture["seed_scans"], fixture["seed_rt"], strict=True
        )
    )
    settings = _settings()
    raw = _BoundedRaw(rt=rt, intensity=intensity)
    result = capture_ms1_peaks(
        raw,
        build_discovery_ions(seeds, settings=settings),
        acquisition_source=raw.acquisition_source,
        settings=settings,
        signal_processing_profile=SignalProcessingProfile(),
        ms1_centroid=lambda *_: None,
    )

    query_rts = [peak.query_rt_min for peak in result.peaks]
    assert len(query_rts) == len(set(query_rts))
    (shared,) = (
        peak
        for peak in result.peaks
        if peak.query_rt_min == pytest.approx(23.349, abs=1e-3)
    )
    # The window centred on the peak's own apex measures it.
    assert (shared.ms1_search_rt_min + shared.ms1_search_rt_max) / 2 == (
        pytest.approx(shared.query_rt_min)
    )
    assert {round(event.rt, 3) for event in shared.located_events} >= {22.535, 24.531}
