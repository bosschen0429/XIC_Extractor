from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    NEUTRAL_LOSS_DA,
    fake_acquisition_source,
    unresolved_overlap_trace,
)
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.discovery.ms2_events import discovery_events_from_scan
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.ms2_scan_models import Ms2Scan
from xic_extractor.raw_reader_errors import RawReaderError
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.partition import cid_nl_region_acquisition
from xic_extractor.untargeted_execution.partition.cid_nl_region_acquisition import (
    acquire_cid_nl_sample_regions,
    plan_cid_nl_mass_regions,
    relate_region_events,
)
from xic_extractor.xic_models import XICRequest, XICTrace

SETTINGS = DiscoverySettings(
    neutral_loss_profile=NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
    precursor_mz_tolerance_ppm=20,
    rt_min=2.5,
    rt_max=3.5,
)


class _Raw:
    """Known acquired, mass-resolved Gaussians and independent native windows."""

    def __init__(self, peaks=(), *, spike=False, fail=False):
        self.rt = np.linspace(0.0, 20.0, 1001)
        self.ions = [
            (mz, height * np.exp(-0.5 * ((self.rt - apex) / 0.09) ** 2))
            for mz, apex, height in peaks
        ]
        if spike:
            self.ions.append((500.0, 125_000 * (self.rt == 16.0)))
        self.acquisition_source = fake_acquisition_source()
        self.fail = fail
        self.raw_chromatogram_call_count = 0

    def acquisition_rt_bounds(self):
        return 0.0, 20.0

    def scan_window_for_request(self, request):
        return tuple(
            int(np.argmin(np.abs(self.rt - value)))
            for value in (request.rt_min, request.rt_max)
        )

    def retention_time_for_scan(self, scan_number):
        return float(self.rt[scan_number])

    def extract_xic_many(self, requests: Sequence[XICRequest]):
        if self.fail:
            raise RawReaderError("lost native chromatogram")
        self.raw_chromatogram_call_count += 1
        traces = []
        for request in requests:
            left, right = self.scan_window_for_request(request)
            width = request.mz * request.ppm_tol * 1e-6
            intensity = sum(
                (signal for mz, signal in self.ions if abs(mz - request.mz) <= width),
                np.zeros_like(self.rt),
            )
            traces.append(
                XICTrace.from_arrays(
                    self.rt[left : right + 1], intensity[left : right + 1]
                )
            )
        return tuple(traces)

    def ms1_centroid(self, rt, mz_min, mz_max):
        index = int(np.argmin(np.abs(self.rt - rt)))
        return max(
            (
                (mz, float(signal[index]))
                for mz, signal in self.ions
                if mz_min <= mz <= mz_max and signal[index] > 0
            ),
            key=lambda centroid: centroid[1],
            default=None,
        )


def _scan_events(sample, scan, rt, mz):
    return discovery_events_from_scan(
        Ms2Scan(scan, rt, mz, np.array([mz - NEUTRAL_LOSS_DA]), np.array([1e4]), 1e4),
        raw_file=Path(f"{sample}.raw"),
        settings=SETTINGS,
    )


def _capture(sample, raw, regions, events):
    return acquire_cid_nl_sample_regions(
        raw,
        sample=SampleMetadata(sample, sample),
        acquisition_source=raw.acquisition_source,
        regions=regions,
        events=events,
        settings=SETTINGS,
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )


def _measurements(result):
    return [
        (peak.integration.rt_apex_min, peak.mz, peak.integration.height_raw)
        for peak in result.peaks
    ]


def test_localized_overlap_lobes_keep_separate_events_without_areas():
    raw = _Raw()
    raw.rt, y = unresolved_overlap_trace()
    raw.ions = [(500.0, y)]
    early = _scan_events("A", 100, 15.195, 500.0)
    late = _scan_events("A", 200, 15.514, 500.0)
    valley = _scan_events("A", 150, 15.4268, 500.0)
    events = (*early, *late, *valley)
    regions = plan_cid_nl_mass_regions(events, settings=SETTINGS)

    (result,) = _capture("A", raw, regions, events)

    a, b = result.peaks
    assert (a.rt_apex_min, b.rt_apex_min) == pytest.approx((15.2193, 15.5835))
    assert a.integration is b.integration is None
    assert [
        (ref.dda_scan_number, ref.peak_observation)
        for ref in result.individual_relations
    ] == [(100, a), (200, b)]
    (unassigned,) = result.ambiguous_relations
    assert unassigned.dda_scan_number == 150
    assert unassigned.matched_candidates == (a, b)


def test_exact_two_scan_alias_from_overlapping_windows_is_one_observation():
    local = _scan_events("A", 302, 3.0, 499.995)
    other = _scan_events("B", 1300, 13.0, 500.005)
    regions = plan_cid_nl_mass_regions((*local, *other), settings=SETTINGS)
    raw = _Raw(((499.990, 13.0, 2.3e5), (500.011, 15.0, 1.7e5)))
    shared_signal = np.zeros_like(raw.rt)
    shared_signal[149:151] = [42_700, 72_500]
    raw.ions.append((500.000, shared_signal))

    (result,) = _capture("A", raw, regions, local)

    assert len(result.coverage) == 2
    assert [peak.mz for peak in result.peaks] == pytest.approx(
        (500.000, 500.011), abs=1e-6
    )
    assert [
        (relation.dda_scan_number, relation.peak_observation.mz)
        for relation in result.individual_relations
    ] == [(302, pytest.approx(500.000, abs=1e-6))]
    assert not result.ambiguous_relations


def test_peak_seen_as_segment_and_as_overlap_lobe_is_one_observation():
    """TumorBC2292 m/z 312.145 in 85RAW, shifted to 500 with the shared scans.

    Both ion windows hold the crest; one scan's centroid falls only inside
    the lower window, which then reads one overlap with a lobe where the
    upper window reads a separate segment of the same bounds.
    """
    local_rt = np.fromstring(
        "16.8859 16.8959 16.9027 16.9147 16.9235 16.9356 16.9414 16.9576 16.9726 "
        "16.9815 16.9945 17.0024 17.0195 17.0305 17.0414 17.0504 17.0624 17.0744 "
        "17.0812 17.0901 17.0948 17.1017 17.1127 17.1216 17.1336 17.1385 17.1567 "
        "17.1699 17.1850 17.1961 17.2144 17.2265 17.2427 17.2547 17.2669 17.2841 "
        "17.2920 17.3082 17.3203 17.3303 17.3475 17.3575 17.3756 17.3806 17.3998",
        sep=" ",
    )
    shared = 1000.0 * np.fromstring(
        "0 0 12.2 8.2 11.6 16.2 18.3 24.3 0 20.4 47.1 29.7 50.0 43.6 54.0 50.5 "
        "47.7 41.1 33.7 48.3 36.3 40.3 43.1 30.8 27.4 29.9 17.6 21.1 26.9 25.1 "
        "41.9 49.4 56.9 60.7 53.4 67.9 58.6 70.7 57.9 55.1 81.8 53.0 56.2 49.4 52.7",
        sep=" ",
    )
    before = np.arange(14.0, 16.88, 0.01)
    after = np.arange(17.41, 20.01, 0.01)
    lower_only = np.zeros_like(local_rt)
    lower_only[8] = 27_800.0
    raw = _Raw()
    raw.rt = np.concatenate((before, local_rt, after))
    raw.ions = [
        (500.006, np.pad(shared, (len(before), len(after)))),
        (499.995, np.pad(lower_only, (len(before), len(after)))),
    ]
    events = (
        *_scan_events("A", 1, 17.0, 500.000),
        *_scan_events("A", 2, 17.0, 500.012),
    )
    regions = plan_cid_nl_mass_regions(events, settings=SETTINGS)
    assert [ion.mz for region in regions for ion in region.ions] == pytest.approx(
        [500.000, 500.012]
    )

    (result,) = _capture("A", raw, regions, events)

    crest = [peak for peak in result.peaks if peak.rt_apex_min == 17.0414]
    (kept,) = crest
    assert kept.source_trace.mz == pytest.approx(500.000)
    assert np.count_nonzero(kept.intensity) == np.count_nonzero(shared[1:27]) + 1


def test_batch_seed_search_retains_local_competition_without_local_cid(monkeypatch):
    early = _scan_events("A", 301, 3.0, 500.000)
    late = _scan_events("A", 1301, 13.0, 499.995)
    events = (*early, *late)
    regions = plan_cid_nl_mass_regions(events, settings=SETTINGS)
    assert len(regions) == 1 and regions[0].centers == (499.995, 500.0)

    a_raw = _Raw(((500.002, 3.0, 1.1e6), (499.997, 13.0, 3.1e5)), spike=True)
    b_raw = _Raw(((500.001, 3.1, 2.3e5), (499.996, 12.9, 1.7e5)))
    (a,) = _capture("A", a_raw, regions, events)
    (b,) = _capture("B", b_raw, regions, events)
    assert a_raw.raw_chromatogram_call_count == b_raw.raw_chromatogram_call_count == 2
    for result, expected in (
        (a, [(3.0, 500.002, 1.1e6), (13.0, 499.997, 3.1e5)]),
        (b, [(3.1, 500.001, 2.3e5), (12.9, 499.996, 1.7e5)]),
    ):
        for observed, target in zip(_measurements(result), expected, strict=True):
            assert observed == pytest.approx(target)
        assert {
            (cover.request.rt_min, cover.request.rt_max) for cover in result.coverage
        } == {(0.0, 6.0), (10.0, 16.0)}
        assert all(
            peak.physical_evaluation.state == "available" for peak in result.peaks
        )

    assert [
        (item.dda_scan_number, item.peak_observation.integration.rt_apex_min)
        for item in a.individual_relations
    ] == [(301, 3.0), (1301, 13.0)]
    assert b.evidence == b.individual_relations == b.ambiguous_relations == ()
    assert all(
        not np.isclose(peak.mz, peak.source_trace.mz, atol=1e-4, rtol=0)
        for peak in a.peaks
    )
    assert all(peak.rt_apex_min != pytest.approx(16.0) for peak in a.peaks)

    (without_remote,) = _capture("A", a_raw, regions, early)
    assert without_remote.peaks == a.peaks
    assert [item.dda_scan_number for item in without_remote.individual_relations] == [
        301
    ]

    monkeypatch.setattr(
        cid_nl_region_acquisition, "PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS", 1
    )
    reversed_events = tuple(reversed(events))
    assert plan_cid_nl_mass_regions(reversed_events, settings=SETTINGS) == regions
    (chunked,) = _capture("A", a_raw, regions, reversed_events)
    assert chunked.peaks == a.peaks
    assert chunked.individual_relations == a.individual_relations


def test_only_peaks_near_some_batch_event_become_observations():
    events = (
        *_scan_events("A", 301, 3.0, 500.0),
        *_scan_events("B", 901, 9.0, 500.0),
    )
    regions = plan_cid_nl_mass_regions(events, settings=SETTINGS)
    raw = _Raw(((500.0, 3.2, 1.0e6), (500.0, 11.5, 4.0e5), (500.0, 15.0, 8.0e5)))

    (result,) = _capture("C", raw, regions, ())

    assert [peak.rt_apex_min for peak in result.peaks] == pytest.approx([3.2, 11.5])
    (coverage,) = result.coverage
    assert (coverage.request.rt_min, coverage.request.rt_max) == (0.0, 12.0)


def test_raw_failure_and_acquired_flat_signal_keep_distinct_coverage():
    events = _scan_events("A", 301, 3.0, 500.0)
    regions = plan_cid_nl_mass_regions(events, settings=SETTINGS)
    (flat,) = _capture("B", _Raw(), regions, ())
    (failed,) = _capture("B", _Raw(fail=True), regions, ())
    assert flat.peaks == failed.peaks == ()
    (flat_coverage,) = flat.coverage
    (failed_coverage,) = failed.coverage
    assert flat_coverage.scan_bounds is not None
    assert flat_coverage.reason == "no_signal_above_baseline"
    assert failed_coverage.scan_bounds is None
    assert "lost native chromatogram" in failed_coverage.reason


def test_acquired_edge_segment_keeps_incomplete_reason():
    events = _scan_events("A", 301, 3.0, 500.0)
    regions = plan_cid_nl_mass_regions(events, settings=SETTINGS)
    raw = _Raw(((500.0, 0.08, 1.1e6), (500.0, 5.0, 5.1e5)))
    (result,) = _capture("B", raw, regions, ())
    assert [(p.integration.rt_apex_min, p.mz) for p in result.peaks] == [(5.0, 500.0)]


def test_overlapping_requests_do_not_merge_two_coeluting_ions():
    events = (
        *_scan_events("A", 301, 3.0, 500.0),
        *_scan_events("A", 302, 3.0, 500.015),
    )
    regions = plan_cid_nl_mass_regions(events, settings=SETTINGS)
    assert len(regions) == 1
    raw = _Raw(((499.998, 3.0, 1.0e6), (500.018, 3.0, 2.0e5)))
    (result,) = _capture("A", raw, regions, events)
    assert [peak.integration.rt_apex_min for peak in result.peaks] == [3.0, 3.0]
    assert [peak.mz for peak in result.peaks] == pytest.approx([499.998, 500.018])
    assert [item.dda_scan_number for item in result.individual_relations] == [301, 302]
    assert [item.peak_observation.mz for item in result.individual_relations] == (
        pytest.approx([499.998, 500.018])
    )


def test_scan_shared_by_isotope_and_monoisotope_binds_only_the_subject_ion():
    from xic_extractor.discovery.models import DiscoveryPeak
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject,
    )
    from xic_extractor.untargeted_execution.partition.cid_nl_sample_acquisition import (
        assess_sample_regions,
    )

    monoisotope, isotope = 300.1614, 301.1649
    events = (
        *_scan_events("A", 151, 3.0, monoisotope),
        *_scan_events("A", 151, 3.0, isotope),
    )
    raw = _Raw(((monoisotope, 3.0, 1.0e6), (isotope, 3.0, 2.0e5)))
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=monoisotope,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=NEUTRAL_LOSS_DA,
    )
    candidate = DiscoveryPeak(
        peak_id="A@monoisotope",
        sample_stem="A",
        peak_mz=monoisotope,
        neutral_loss_tag="DNA_dR",
        best_ms2_scan_id=151,
        attached_event_count=1,
        ms1_apex_rt=3.0,
        ms1_search_rt_min=2.5,
        ms1_search_rt_max=3.5,
        query_rt_min=3.0,
        status="admitted",
    )
    for ordered in (events, tuple(reversed(events))):
        regions = plan_cid_nl_mass_regions(ordered, settings=SETTINGS)
        result = assess_sample_regions(
            regions=_capture("A", raw, regions, ordered),
            sample=SampleMetadata("A", "A"),
            acquisition_source=raw.acquisition_source,
            scopes=((subject, (candidate,)),),
            processing_attempt_id="attempt",
            settings=SETTINGS,
            raw=None,
            rt_reference_only=False,
        )[subject.method_subject_id]
        assert [(cid, peak.mz) for cid, peak in result.candidate_observations] == [
            ("A@monoisotope", pytest.approx(monoisotope, abs=1e-4))
        ]
        assert [
            peak.mz for peak in result.assessment_input.assessment.candidate_peaks
        ] == [pytest.approx(monoisotope, abs=1e-4)]


def test_reference_relates_only_its_own_events_to_the_first_pass_peaks():
    early = _scan_events("A", 300, 3.0, 499.995)
    late = _scan_events("A", 800, 8.0, 500.005)
    raw = _Raw(((500.0, 3.0, 9e4), (500.0, 8.0, 2e5)))
    sample = SampleMetadata("A", "A")
    full = acquire_cid_nl_sample_regions(
        raw,
        sample=sample,
        acquisition_source=raw.acquisition_source,
        regions=plan_cid_nl_mass_regions((*early, *late), settings=SETTINGS),
        events=(*early, *late),
        settings=SETTINGS,
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    calls = raw.raw_chromatogram_call_count

    (reference,) = relate_region_events(
        full,
        sample=sample,
        acquisition_source=raw.acquisition_source,
        events=late,
        settings=SETTINGS,
    )

    assert raw.raw_chromatogram_call_count == calls
    assert reference.peaks == full[0].peaks
    assert [r.dda_scan_number for r in full[0].individual_relations] == [300, 800]
    assert [r.dda_scan_number for r in reference.individual_relations] == [800]


def test_region_below_the_event_quorum_is_read_only_where_its_events_are():
    """An ion with events in fewer samples than the quorum cannot become a
    published row, so a sample without its own event does not read it."""
    a = _scan_events("A", 100, 3.0, 500.0)
    b = _scan_events("B", 200, 3.0, 500.0)
    peak = ((500.0, 3.0, 2.0e5),)

    sparse = plan_cid_nl_mass_regions(a, settings=SETTINGS, minimum_event_samples=2)
    (own,) = _capture("A", _Raw(peak), sparse, a)
    assert len(own.peaks) == 1
    (elsewhere,) = _capture("C", _Raw(peak), sparse, ())
    assert elsewhere.peaks == ()
    assert elsewhere.coverage == ()

    shared = plan_cid_nl_mass_regions(
        (*a, *b), settings=SETTINGS, minimum_event_samples=2
    )
    (elsewhere,) = _capture("C", _Raw(peak), shared, ())
    assert len(elsewhere.peaks) == 1


def test_repeated_loss_under_one_peak_keeps_a_sparse_region_read_everywhere():
    """Two scans of one peak both showing the loss mark a reproducible signal,
    so the region is read in every sample although only one carries events."""
    first = _scan_events("A", 100, 3.0, 500.0)
    second = _scan_events("A", 101, 3.02, 500.0)
    peak = ((500.0, 3.0, 2.0e5),)

    def read_elsewhere(peak_events):
        regions = plan_cid_nl_mass_regions(
            (*first, *second),
            settings=SETTINGS,
            minimum_event_samples=2,
            peak_events=peak_events,
        )
        (result,) = _capture("C", _Raw(peak), regions, ())
        return len(result.peaks)

    assert read_elsewhere(((*first, *second),)) == 1
    assert read_elsewhere((first, second)) == 0
