"""CID-NL Discovery measures each ion's physical peaks once and attaches events."""

import itertools
import random
from dataclasses import replace

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import TEST_PROFILE
from tests.discovery_csv_fixtures import located_scan_ids
from tests.discovery_ms1_capture_fixtures import (
    _FakeRaw,
    _gaussian,
    _multi_tag_settings,
    _seed,
    _settings,
)
from xic_extractor.discovery.ion_grouping import build_discovery_ions
from xic_extractor.discovery.models import build_discovery_peak_id
from xic_extractor.discovery.ms1_capture import capture_ms1_peaks
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.xic_models import XICTrace


def _capture(raw, seeds, settings: DiscoverySettings):
    return capture_ms1_peaks(
        raw,
        build_discovery_ions(seeds, settings=settings),
        acquisition_source=raw.acquisition_source,
        settings=settings,
        signal_processing_profile=TEST_PROFILE,
        ms1_centroid=lambda *_: None,
    )


def _events_by_scan(result):
    return {event.ms2_scan_number: event for event in result.attachments}


def _two_peak_raw(*, first: float, second: float, sigma: float = 0.04) -> _FakeRaw:
    rt = np.linspace(7.2, 8.6, 281)
    return _FakeRaw(
        rt=rt,
        intensity=_gaussian(rt, center=first, sigma=sigma, height=900.0)
        + _gaussian(rt, center=second, sigma=sigma, height=700.0),
    )


def test_events_across_a_broad_peak_measure_one_peak() -> None:
    # Events 0.6 min apart on one broad peak used to open separate channels.
    rt = np.linspace(26.0, 31.0, 501)
    raw = _FakeRaw(rt=rt, intensity=_gaussian(rt, center=28.4, sigma=0.5, height=1e6))
    seeds = (
        _seed(scan_number=10, rt=27.9, precursor_mz=300.1610),
        _seed(scan_number=20, rt=28.5, precursor_mz=300.1612),
        _seed(scan_number=30, rt=29.1, precursor_mz=300.1609),
    )

    result = _capture(raw, seeds, _settings(ms1_search_padding_min=0.3))

    (peak,) = result.peaks
    assert peak.attached_event_count == 3
    assert located_scan_ids(peak) == (10, 20, 30)
    assert peak.admitted
    assert {event.peak_id for event in result.attachments} == {peak.peak_id}


def test_isomers_at_one_mass_are_two_peaks_on_one_channel() -> None:
    raw = _two_peak_raw(first=7.6, second=8.2, sigma=0.03)
    seeds = (_seed(scan_number=10, rt=7.6), _seed(scan_number=20, rt=8.2))
    settings = _settings(ms1_search_padding_min=0.2)

    (channel,) = build_discovery_ions(seeds, settings=settings)
    result = _capture(raw, seeds, settings)

    assert channel.events == seeds
    assert len(result.peaks) == 2
    # Every peak is named by the one ion XIC that measured it.
    assert all(
        peak.peak_id
        == build_discovery_peak_id(
            sample_stem=peak.sample_stem,
            xic_mz=channel.mz,
            peak_rt_min=peak.query_rt_min,
        )
        for peak in result.peaks
    )
    events = _events_by_scan(result)
    assert events[10].peak_id != events[20].peak_id
    assert all(event.peak_id for event in result.attachments)


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_channels_never_chain_masses_beyond_the_tolerance(order) -> None:
    # 10 ppm of 500 is 5 mDa: A~B and B~C, but A and C differ by 16 ppm.
    masses = (500.000, 500.004, 500.008)
    seeds = tuple(
        _seed(scan_number=10 + index, rt=8.0, precursor_mz=masses[index])
        for index in order
    )
    settings = _settings(precursor_mz_tolerance_ppm=10.0)

    channels = build_discovery_ions(seeds, settings=settings)

    assert [sorted(s.precursor_mz for s in c.events) for c in channels] == [
        [500.000, 500.004],
        [500.008],
    ]
    for channel in channels:
        for left, right in itertools.combinations(channel.events, 2):
            assert within_ppm(left.precursor_mz, right.precursor_mz, 10.0)


def test_coeluting_ions_within_the_tolerance_share_one_channel() -> None:
    seeds = (
        _seed(scan_number=10, rt=8.0, precursor_mz=500.0000, product_intensity=5e3),
        _seed(scan_number=11, rt=8.0, precursor_mz=500.0080, product_intensity=9e3),
    )

    (channel,) = build_discovery_ions(seeds, settings=_settings())

    # The stronger event's measured ion sets the XIC mass.
    assert channel.mz == 500.0080


def test_an_event_on_a_shared_valley_attaches_to_the_earlier_peak() -> None:
    # Adjacent raw components share the 7.90 min valley scan.
    raw = _two_peak_raw(first=7.8, second=8.0)
    seeds = (
        _seed(scan_number=10, rt=7.8, product_intensity=4e3),
        _seed(scan_number=20, rt=7.9, product_intensity=9e3),
        _seed(scan_number=30, rt=8.0, product_intensity=4e3),
    )
    settings = _settings(ms1_search_padding_min=0.3)

    results = [_capture(raw, ordered, settings) for ordered in (seeds, seeds[::-1])]

    for result in results:
        events = _events_by_scan(result)
        assert events[20].peak_id == events[10].peak_id != events[30].peak_id
    assert results[0].peaks == results[1].peaks
    assert results[0].attachments == results[1].attachments


def test_multitag_events_of_one_ion_attach_to_one_peak() -> None:
    settings = _multi_tag_settings()
    seeds = tuple(
        replace(
            _seed(scan_number=scan, rt=9.0 + 0.01 * index, product_mz=500.0 - loss),
            neutral_loss_tag=tag,
            configured_neutral_loss_da=loss,
            observed_neutral_loss_da=loss,
        )
        for index, (scan, tag, loss) in enumerate(
            ((10, "dR", 116.047344), (20, "R", 132.0423), (30, "R", 132.0423))
        )
    )
    rt = np.linspace(8.6, 9.4, 161)
    raw = _FakeRaw(rt=rt, intensity=_gaussian(rt, center=9.02, sigma=0.04, height=1e3))

    result = _capture(raw, seeds, settings)

    (peak,) = result.peaks
    assert peak.matched_tag_names == ("dR", "R")
    assert peak.attached_event_count == 3
    assert peak.neutral_loss_tag == "dR"
    assert {event.peak_id for event in result.attachments} == {peak.peak_id}


def test_every_event_attaches_to_one_peak_or_records_why_not() -> None:
    raw = _two_peak_raw(first=7.6, second=8.2, sigma=0.03)
    seeds = (
        _seed(scan_number=10, rt=7.6),
        # An inferred precursor whose parent MS1 scan was not read.
        replace(
            _seed(scan_number=20, rt=8.2),
            precursor_mz_basis="product_plus_neutral_loss",
        ),
        # Located in the first peak's raw basin, beyond its bounds and margin.
        _seed(scan_number=30, rt=7.85),
    )

    result = _capture(raw, seeds, _settings(ms1_search_padding_min=0.2))

    events = _events_by_scan(result)
    assert len(result.attachments) == len(seeds)
    assert events[10].peak_id and not events[10].unattached_reason
    assert events[20].unattached_reason == "parent_ms1_unavailable"
    assert events[30].unattached_reason == "rt_outside_peak"
    # The second peak was measured on the channel, but no event attaches.
    (unattached_peak,) = (p for p in result.peaks if not p.attached_event_count)
    assert unattached_peak.status == "no_nl_event"
    assert not unattached_peak.admitted


def test_seed_order_does_not_change_peaks_or_events() -> None:
    raw = _two_peak_raw(first=7.6, second=8.2, sigma=0.03)
    seeds = [
        _seed(scan_number=scan, rt=rt, precursor_mz=mz, product_intensity=intensity)
        for scan, rt, mz, intensity in (
            (10, 7.58, 500.0000, 3e3),
            (11, 7.62, 500.0021, 8e3),
            (12, 8.18, 500.0013, 5e3),
            (13, 8.22, 499.9990, 6e3),
            (14, 7.95, 500.0004, 2e3),
        )
    ]
    settings = _settings(ms1_search_padding_min=0.2)
    expected = _capture(raw, tuple(seeds), settings)

    for seed in range(5):
        shuffled = list(seeds)
        random.Random(seed).shuffle(shuffled)
        result = _capture(raw, tuple(shuffled), settings)
        assert result.peaks == expected.peaks
        assert sorted(result.attachments, key=lambda e: e.ms2_scan_number) == sorted(
            expected.attachments, key=lambda e: e.ms2_scan_number
        )


def test_tag_order_does_not_change_peak_identity_or_count() -> None:
    seeds = tuple(
        replace(
            _seed(
                scan_number=scan, rt=rt, product_mz=500.0 - loss, product_intensity=i
            ),
            neutral_loss_tag=tag,
            configured_neutral_loss_da=loss,
            observed_neutral_loss_da=loss,
        )
        for scan, rt, tag, loss, i in (
            (10, 7.6, "dR", 116.047344, 9e3),
            (20, 7.61, "R", 132.0423, 5e3),
            (30, 8.2, "R", 132.0423, 7e3),
        )
    )
    raw = _two_peak_raw(first=7.6, second=8.2, sigma=0.03)
    base = _multi_tag_settings()
    reordered = replace(base, selected_tag_names=("R", "MeR", "dR"))

    first = _capture(raw, seeds, replace(base, ms1_search_padding_min=0.2))
    second = _capture(raw, seeds, replace(reordered, ms1_search_padding_min=0.2))

    assert [p.peak_id for p in first.peaks] == [p.peak_id for p in second.peaks]
    assert [p.attached_event_count for p in first.peaks] == [
        p.attached_event_count for p in second.peaks
    ]


def test_an_event_without_a_measured_peak_is_recorded_without_a_peak() -> None:
    rt = np.linspace(7.2, 8.6, 281)
    raw = _FakeRaw(rt=rt, intensity=np.zeros_like(rt))

    result = _capture(raw, (_seed(scan_number=10, rt=7.9),), _settings())

    assert result.peaks == ()
    (event,) = result.attachments
    assert not event.peak_id
    assert event.unattached_reason


class _MassDependentRaw(_FakeRaw):
    """Each XIC window reads the same peak plus its own off-peak signal."""

    def extract_xic_many(self, requests):
        super().extract_xic_many(requests)
        traces = []
        for request in requests:
            intensity = self.intensity.copy()
            # A different off-peak reading per window keeps traces unequal.
            intensity[0] += 1.0 + (request.mz - 499.0) * 1e3
            traces.append(XICTrace.from_arrays(self.rt, intensity))
        return tuple(traces)


def test_adjacent_channels_measure_their_shared_peak_once() -> None:
    # The first channel opens at 499.995 and takes 500.0049 within 20 ppm; the
    # next opens at 500.0051. Both XIC windows read the ion at the same apex.
    rt = np.linspace(8.6, 9.4, 161)
    raw = _MassDependentRaw(
        rt=rt, intensity=_gaussian(rt, center=9.02, sigma=0.04, height=1e4)
    )
    seeds = (
        _seed(scan_number=10, rt=9.00, precursor_mz=499.9950, product_intensity=1e3),
        _seed(scan_number=11, rt=9.01, precursor_mz=500.0049, product_intensity=9e3),
        _seed(scan_number=12, rt=9.03, precursor_mz=500.0051, product_intensity=5e3),
    )
    settings = _settings()
    channels = build_discovery_ions(seeds, settings=settings)

    result = _capture(raw, seeds, settings)

    assert len(channels) == 2
    (peak,) = result.peaks
    assert {event.peak_id for event in result.attachments if event.peak_id} == {
        peak.peak_id
    }
    assert all(event.peak_id for event in result.attachments)


class _RecentredWindowRaw(_FakeRaw):
    """The ion's own window reads two peaks; a shifted window reads one broad one.

    The shifted window's broad peak covers the second peak's apex but has its
    raw apex at the first peak's apex, as when extra signal enters a window.
    """

    def extract_xic_many(self, requests):
        super().extract_xic_many(requests)
        broad = _gaussian(self.rt, center=9.00, sigma=0.20, height=1e4)
        return tuple(
            XICTrace.from_arrays(
                self.rt, self.intensity if request.mz == 500.0 else broad
            )
            for request in requests
        )


def test_a_recentred_window_that_selects_another_peak_keeps_the_first() -> None:
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata

    rt = np.linspace(8.4, 9.8, 281)
    raw = _RecentredWindowRaw(
        rt=rt,
        intensity=_gaussian(rt, center=9.00, sigma=0.03, height=1e4)
        + _gaussian(rt, center=9.40, sigma=0.03, height=3e3),
    )
    inferred = replace(
        _seed(scan_number=12, rt=9.40),
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
    seeds = (_seed(scan_number=10, rt=9.00), inferred)
    settings = _settings()

    result = capture_ms1_peaks(
        raw,
        build_discovery_ions(seeds, settings=settings),
        acquisition_source=raw.acquisition_source,
        settings=settings,
        signal_processing_profile=TEST_PROFILE,
        # The second peak's apex reads the ion 6 ppm above its window centre.
        ms1_centroid=lambda _rt, low, high: (
            (500.003, 1.0) if low < 500.003 < high else None
        ),
    )

    apex_by_peak = {peak.peak_id: peak.ms1_apex_rt for peak in result.peaks}
    events = _events_by_scan(result)
    assert sorted(round(apex, 2) for apex in apex_by_peak.values()) == [9.0, 9.4]
    assert round(apex_by_peak[events[10].peak_id], 2) == 9.0
    assert round(apex_by_peak[events[12].peak_id], 2) == 9.4
