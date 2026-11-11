"""Alignment adoption of Discovery measurements and adjacent peak capture."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    _FakeRaw,
    _open_bound_raw,
    _peak_config,
    _settings,
)
from tests.discovery_csv_fixtures import located_scan_ids, representative_event
from tests.untargeted_cid_nl_real_data_adapter_fixtures import (
    build_sample_adapter_fixture,
)
from xic_extractor.untargeted_execution.partition.cid_nl_sample_acquisition import (
    acquire_untargeted_cid_nl_sample_trace_batch,
    adapt_current_discovery_sample_subject,
)


@pytest.mark.parametrize(
    ("fresh_available", "precursor_case"),
    [
        (True, "shifted"),
        (False, "shifted"),
        (True, "inferred"),
        (False, "inferred"),
    ],
)
def test_alignment_remeasures_the_discovery_peak_and_preserves_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fresh_available: bool,
    precursor_case: str,
) -> None:
    from types import SimpleNamespace

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.discovery.ion_grouping import build_discovery_ions
    from xic_extractor.discovery.ms1_capture import (
        capture_ms1_peaks,
    )
    from xic_extractor.discovery.ms2_events import collect_strict_nl_events
    from xic_extractor.discovery.strict_event_receipts import (
        read_parent_ms1_observations,
    )
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
    from xic_extractor.peak_detection.observations import (
        physical_peak_observation_integration,
    )
    from xic_extractor.xic_models import XICRequest

    fixture = build_sample_adapter_fixture(tmp_path)
    shifted_mass = precursor_case == "shifted"
    center = 13.263299286133334
    source_raw = _FakeRaw((8.0,))
    source_raw.rt = np.linspace(12.517828061866668, 13.92044642215, 521)
    source_raw.rt[int(np.argmin(np.abs(source_raw.rt - center)))] = center
    source_raw.scans = tuple(
        replace(
            source_raw.scans[0],
            rt=rt,
            scan_number=int(np.argmin(np.abs(source_raw.rt - rt))) + 1,
            precursor_mz=500.009 + index * 0.0006 if shifted_mass else 500.0,
            masses=source_raw.scans[0].masses
            + index * 0.0006
            + (0.009 if shifted_mass else 0.0),
        )
        for index, rt in enumerate((center - 0.25, center + 0.05))
    )
    if precursor_case == "inferred":
        source_raw.scans = tuple(
            replace(
                scan,
                precursor_mz=500.5,
                precursor_metadata=Ms2PrecursorMetadata(
                    charge=1,
                    isolation_width_da=1.6,
                    isolation_offset_da=0.0,
                    master_scan_number=scan.scan_number - 1,
                ),
            )
            for scan in source_raw.scans
        )
        for scan in source_raw.scans:
            source_raw.rt[scan.scan_number - 1] = scan.rt
    source_raw.intensity = 250.0 + 1000.0 * np.exp(
        -0.5 * ((source_raw.rt - center) / 0.15) ** 2
    )
    settings = replace(
        _settings(),
        neutral_loss_profile=None,
        # Retain two chemical hypotheses while their MS1 capture contexts overlap.
        product_mz_tolerance_ppm=1.0,
        rt_min=12.8,
        rt_max=13.4,
    )
    peak_config = _peak_config(tmp_path)
    profile = SignalProcessingProfile(window_points=15)
    raw, vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    if precursor_case == "inferred":
        from xic_extractor.precursor_evidence import Ms1PrecursorObservation

        # Discovery reads the fixture scans with their precursor metadata.
        monkeypatch.setattr(
            type(raw),
            "iter_ms2_scans",
            lambda self, lo, hi: source_raw.iter_ms2_scans(lo, hi),
        )

        def read_parent_windows(self, requests):
            return tuple(
                Ms1PrecursorObservation(
                    scan_number=number,
                    rt_min=float(source_raw.rt[number - 1]),
                    centroid_mzs=(500.001,),
                )
                for number, _low, _high in requests
            )

        monkeypatch.setattr(type(raw), "read_ms1_mass_windows", read_parent_windows)
    if shifted_mass:
        # The ion is inside both source windows but outside the method-center
        # window. Respect the vendor mass ranges instead of mocking capture.
        def mass_filtered_chromatogram(trace_settings, start_scan, end_scan):
            vendor.chromatogram_request_counts.append(len(trace_settings))
            rt = source_raw.rt[start_scan - 1 : end_scan]
            signal = source_raw.intensity[start_scan - 1 : end_scan]
            return SimpleNamespace(
                PositionsArray=[rt.tolist() for _ in trace_settings],
                IntensitiesArray=[
                    (
                        signal
                        if (
                            setting.MassRanges[0][0]
                            <= 500.015
                            <= setting.MassRanges[0][1]
                        )
                        else np.zeros_like(signal)
                    ).tolist()
                    for setting in trace_settings
                ],
            )

        monkeypatch.setattr(vendor, "GetChromatogramData", mass_filtered_chromatogram)
    source = fixture.source("S1")
    # Discovery reads each inferred event's parent MS1 before grouping ions.
    seeds = read_parent_ms1_observations(
        raw,
        collect_strict_nl_events(
            raw, raw_file=fixture.source_paths["S1"], settings=settings
        ),
        tolerance_ppm=settings.precursor_mz_tolerance_ppm,
    )
    discovery = capture_ms1_peaks(
        raw,
        build_discovery_ions(seeds, settings=settings),
        acquisition_source=source,
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )
    # Both requests observe one channel/peak; retain both NL events on one result.
    assert len(discovery.peaks) == 1
    assert set(located_scan_ids(discovery.peaks[0])) == {
        seed.scan_number for seed in seeds
    }
    (candidate,) = discovery.peaks
    (discovered,) = discovery.physical_bindings
    if not fresh_available:
        # RAW no longer shows the peak under the same acquisition identity.
        source_raw.intensity = np.zeros_like(source_raw.rt)
    result = adapt_current_discovery_sample_subject(
        processing_attempt_id=fixture.processing_attempt_id,
        sample=fixture.sample("S1"),
        acquisition_source=fixture.source(fixture.sample("S1")),
        method_subject=fixture.method_subject,
        peaks=discovery.peaks,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=profile,
    )
    assessment = result.assessment_input.assessment
    assert result.acquisition is not None
    # The peak table's capture conditions name one XIC: its window at peak_mz.
    (acquired,) = result.acquisition.traces
    assert acquired.coordinate.request == XICRequest(
        mz=candidate.peak_mz,
        rt_min=candidate.ms1_search_rt_min,
        rt_max=candidate.ms1_search_rt_max,
        ppm_tol=settings.precursor_mz_tolerance_ppm,
    )
    if not fresh_available:
        # Alignment's own measurement decides admission: a peak it cannot
        # measure does not enter Alignment, and nothing fails.
        assert result.captures == ()
        assert assessment.candidate_peaks == ()
        return
    if not shifted_mass:
        assert assessment.method_result == "SUPPORT"
        assert len(assessment.individual_support_refs) == 2
    # Both candidates select the same MS1 peak, shifted traces included, so the
    # universe holds that peak once.
    assert len(assessment.candidate_peaks) == 1
    (capture,) = result.captures
    # Equal RAW, window, query RT and settings give Discovery's measurement.
    assert capture.physical_evaluation == discovered.physical_evaluation
    binding = next(
        item
        for item in capture.scope.capture_bindings
        if item.candidate_id == candidate.peak_id
    )
    assert binding.physical_evaluation is capture.physical_evaluation
    assert binding.acquisition_source_id == source.acquisition_source_id
    assert binding.sample_key == fixture.sample("S1").sample_name
    integration = physical_peak_observation_integration(capture.physical_evaluation)
    # A candidate that selected an already retained peak supports it.
    (observed,) = assessment.candidate_peaks
    assert capture.selected_hypothesis.integration == integration
    assert (
        observed.integration.area_baseline_corrected
        == integration.area_baseline_corrected
    )
    assert observed.integration.rt_apex_min == integration.rt_apex_min
    batch = acquire_untargeted_cid_nl_sample_trace_batch(
        sample=fixture.sample("S1"),
        acquisition_source=fixture.source(fixture.sample("S1")),
        peaks=discovery.peaks,
        settings=settings,
        raw=raw,
        signal_processing_profile=profile,
    )
    assert batch is not None
    acquisition_calls = tuple(vendor.chromatogram_request_counts)
    reused = adapt_current_discovery_sample_subject(
        processing_attempt_id=fixture.processing_attempt_id,
        sample=fixture.sample("S1"),
        acquisition_source=fixture.source(fixture.sample("S1")),
        method_subject=fixture.method_subject,
        peaks=discovery.peaks,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=profile,
        _preacquired_traces=batch,
    )
    assert reused.acquisition is batch.acquisition
    assert tuple(vendor.chromatogram_request_counts) == acquisition_calls
    assert reused.assessment_input.assessment == assessment
    assert [item.physical_evaluation for item in reused.captures] == [
        item.physical_evaluation for item in result.captures
    ]


@pytest.mark.parametrize(
    ("subject_mz", "batch_subject", "interference_abundance"),
    [
        (500.0, False, 0.5),
        (500.015, True, 0.0),
        (500.015, False, 0.5),
    ],
)
def test_apex_ion_window_is_measured_once_and_adopted_by_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    subject_mz: float,
    batch_subject: bool,
    interference_abundance: float,
) -> None:
    from types import SimpleNamespace

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.discovery.ion_grouping import build_discovery_ions
    from xic_extractor.discovery.ms1_capture import (
        capture_ms1_peaks,
    )
    from xic_extractor.discovery.ms2_events import collect_strict_nl_events
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject,
    )

    fixture = build_sample_adapter_fixture(tmp_path)
    physical_mz = 500.008
    source_raw = _FakeRaw((8.0,))
    source_raw.scans = tuple(
        replace(scan, precursor_mz=500.5, masses=scan.masses + 0.016)
        for scan in source_raw.scans
    )
    raw, vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)

    def mass_filtered_chromatogram(trace_settings, start_scan, end_scan):
        rt = source_raw.rt[start_scan - 1 : end_scan]
        signal = source_raw.intensity[start_scan - 1 : end_scan]
        # The inferred 500.016 window includes both ions. The measured-ion
        # window excludes the coeluting 500.024 interference.
        return SimpleNamespace(
            PositionsArray=[rt.tolist() for _ in trace_settings],
            IntensitiesArray=[
                (
                    signal
                    * sum(
                        abundance
                        for mz, abundance in (
                            (physical_mz, 1.0),
                            (500.024, interference_abundance),
                        )
                        if setting.MassRanges[0][0] <= mz <= setting.MassRanges[0][1]
                    )
                ).tolist()
                for setting in trace_settings
            ],
        )

    monkeypatch.setattr(vendor, "GetChromatogramData", mass_filtered_chromatogram)
    settings = replace(_settings(), neutral_loss_profile=None)
    peak_config = _peak_config(tmp_path)
    profile = SignalProcessingProfile(window_points=15)

    def discover(ms1_centroid):
        return capture_ms1_peaks(
            raw,
            build_discovery_ions(
                collect_strict_nl_events(
                    raw, raw_file=fixture.source_paths["S1"], settings=settings
                ),
                settings=settings,
            ),
            acquisition_source=fixture.source("S1"),
            settings=settings,
            signal_processing_profile=profile,
            ms1_centroid=ms1_centroid,
        )

    unread_discovery = discover(lambda *_: None)
    (unread,) = unread_discovery.peaks
    discovery = discover(
        lambda _rt, low, high: (
            (physical_mz, 1.0) if low <= physical_mz <= high else None
        )
    )
    (candidate,) = discovery.peaks
    (discovered,) = discovery.physical_bindings
    (unread_binding,) = unread_discovery.physical_bindings
    # Discovery measures the peak once more in the measured ion's window, which
    # excludes the coeluting interference the inferred window included. The
    # peak keeps the name of the 500.016 ion XIC that located it.
    assert candidate.peak_id == unread.peak_id
    assert "@mz500.016_" in candidate.peak_id
    assert candidate.peak_mz == pytest.approx(physical_mz)
    assert discovered.physical_evaluation.facts.area_counts_seconds == pytest.approx(
        unread_binding.physical_evaluation.facts.area_counts_seconds
        / (1 + interference_abundance)
    )
    method_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=subject_mz,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    result = adapt_current_discovery_sample_subject(
        processing_attempt_id=fixture.processing_attempt_id,
        sample=fixture.sample("S1"),
        acquisition_source=fixture.source(fixture.sample("S1")),
        method_subject=method_subject,
        peaks=(candidate,),
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=profile,
    )
    assessment = result.assessment_input.assessment
    assert assessment.method_result == "HOLD"
    assert not assessment.individual_support_refs
    assert {e.scan_number for e in assessment.evidence_observations} == {
        scan.scan_number for scan in source_raw.scans
    }
    assert all(
        e.precursor_mz == pytest.approx(500.016)
        for e in assessment.evidence_observations
    )
    # Alignment measures that peak again in the same window and gets the same.
    (capture,) = result.captures
    assert capture.trace.mz == candidate.peak_mz
    assert capture.trace.rt_min == candidate.ms1_search_rt_min
    assert capture.trace.rt_max == candidate.ms1_search_rt_max
    assert capture.physical_evaluation == discovered.physical_evaluation
    assert capture.selected_hypothesis is not None
    assert (
        capture.selected_hypothesis.integration.area_baseline_corrected
        == discovered.physical_evaluation.facts.area_counts_seconds
    )
    assert representative_event(candidate).precursor_mz == pytest.approx(500.016)


def test_adapter_supports_adjacent_physical_peaks_with_interior_scans(
    tmp_path: Path,
) -> None:
    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.discovery.ion_grouping import build_discovery_ions
    from xic_extractor.discovery.ms1_capture import (
        capture_ms1_peaks,
    )
    from xic_extractor.discovery.ms2_events import collect_strict_nl_events

    fixture = build_sample_adapter_fixture(tmp_path)
    source_raw = _FakeRaw((7.8, 8.2))
    # Resolve the valley at the configured Gaussian15 sampling footprint,
    # while keeping both physical intervals adjacent at the shared return.
    source_raw.rt = np.linspace(7.4, 8.7, 241)
    source_raw.scans = tuple(
        replace(scan, scan_number=int(np.argmin(np.abs(source_raw.rt - scan.rt))) + 1)
        for scan in source_raw.scans
    )
    source_raw.intensity = sum(
        height * np.exp(-0.5 * ((source_raw.rt - center) / 0.08) ** 2)
        for height, center in ((200.0, 7.8), (1000.0, 8.2))
    )
    settings = replace(_settings(), neutral_loss_profile=None)
    peak_config = _peak_config(tmp_path)
    profile = SignalProcessingProfile(window_points=15)
    raw, _vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    seeds = collect_strict_nl_events(
        raw, raw_file=fixture.source_paths["S1"], settings=settings
    )
    discovery = capture_ms1_peaks(
        raw,
        build_discovery_ions(seeds, settings=settings),
        acquisition_source=fixture.source("S1"),
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid=lambda *_: None,
    )
    assert len(discovery.peaks) == 2
    result = adapt_current_discovery_sample_subject(
        processing_attempt_id=fixture.processing_attempt_id,
        sample=fixture.sample("S1"),
        acquisition_source=fixture.source(fixture.sample("S1")),
        method_subject=fixture.method_subject,
        peaks=discovery.peaks,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=profile,
    )
    assessment = result.assessment_input.assessment
    left, right = sorted(
        assessment.candidate_peaks,
        key=lambda peak: peak.integration.rt_apex_min,
    )
    assert left.integration.rt_right_min < (right.integration.rt_left_min)
    assert assessment.method_result == "SUPPORT"
    assert assessment.set_relation_refs == ()
    assert {
        support.peak_observation.physical_peak_key
        for support in assessment.individual_support_refs
    } == {
        left.physical_peak_key,
        right.physical_peak_key,
    }
