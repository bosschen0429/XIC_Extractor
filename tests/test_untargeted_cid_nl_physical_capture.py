"""Changed physical queries, requantification and adjacent peak capture."""

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
    bind_physical_capture,
)
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
def test_shared_discovery_query_reuses_retained_peak_and_preserves_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fresh_available: bool,
    precursor_case: str,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.discovery.ion_grouping import build_discovery_ions
    from xic_extractor.discovery.ms1_capture import (
        capture_ms1_peaks,
    )
    from xic_extractor.discovery.ms2_events import collect_strict_nl_events
    from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
    from xic_extractor.discovery.strict_event_receipts import (
        read_parent_ms1_observations,
    )
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
    from xic_extractor.peak_detection import capture as capture_owner
    from xic_extractor.peak_detection.observations import (
        physical_peak_observation_integration,
    )
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        evaluate_physical_peak,
        prepare_physical_signal,
    )
    from xic_extractor.peak_detection.traces import trace_from_xic_request
    from xic_extractor.untargeted_execution.partition import (
        cid_nl_sample_acquisition as adapter,
    )

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
    source = fixture.owners.request.acquisition_sources[0]
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
        ms1_centroid_mz=lambda *_: None,
    )
    # Both requests observe one channel/peak; retain both NL events on one result.
    assert len(discovery.peaks) == 1
    assert set(discovery.peaks[0].event_scan_ids) == {
        seed.scan_number for seed in seeds
    }
    legacy_records = tuple(
        DiscoveryPhysicalFactRecord(
            work_item=record.work_item,
            acquired_trace=record.acquired_trace,
            evaluation=evaluate_physical_peak(
                prepare_physical_signal(
                    record.acquired_trace.trace.rt,
                    record.acquired_trace.trace.intensity,
                    profile=profile,
                ),
                query_rt_min=record.evaluation.question.query_rt_min,
                request_context_rt_start_min=(
                    record.evaluation.question.context_rt_start_min
                ),
                request_context_rt_end_min=(
                    record.evaluation.question.context_rt_end_min
                ),
                selection_policy_version="baseline_peak_contains_query",
            ),
        )
        for record in (
            binding.physical_fact_record for binding in discovery.physical_bindings
        )
    )
    owners = bind_physical_capture(fixture.owners, {}, profile=profile)
    if not fresh_available:
        source_raw.intensity = np.zeros_like(source_raw.rt)
    evaluate = Mock(wraps=evaluate_physical_peak)
    prepare = Mock(wraps=prepare_physical_signal)
    for module in (adapter, capture_owner):
        for name, spy in (
            ("evaluate_physical_peak", evaluate),
            ("prepare_physical_signal", prepare),
        ):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, spy)
    if not fresh_available:
        # A shared unchanged request reuses its result; changed RAW under the
        # same acquisition identity must fail, not silently remeasure it.
        with pytest.raises(ValueError, match="physical capture data"):
            adapt_current_discovery_sample_subject(
                processing_owners=owners,
                sample=fixture.sample("S1"),
                method_subject=fixture.method_subject,
                peaks=discovery.peaks,
                settings=settings,
                peak_config=peak_config,
                raw=raw,
                signal_processing_profile=profile,
                physical_records=legacy_records,
            )
        assert evaluate.call_count == prepare.call_count == 0
        return
    result = adapt_current_discovery_sample_subject(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        peaks=discovery.peaks,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=profile,
        physical_records=legacy_records,
    )
    assessment = result.assessment_input.assessment
    if not shifted_mass:
        assert assessment.content.method_result == (
            "SUPPORT" if fresh_available else "REJECT"
        )
        assert len(assessment.individual_support_refs) == (2 if fresh_available else 0)
    # Both candidates select the same MS1 peak, shifted traces included, so the
    # universe holds that peak once.
    assert len(assessment.candidate_peaks) == (1 if fresh_available else 0)
    assert result.acquisition is not None
    assert len(result.acquisition.traces) == 1
    states = []
    changed_questions = 0
    captures = {capture.candidate_id: capture for capture in result.captures}
    assert len(captures) == 1
    # The shared complete query is unchanged, so Alignment only reads its result.
    assert evaluate.call_count == 0
    assert prepare.call_count == 0
    for record in legacy_records:
        candidate = next(
            item for item in discovery.peaks if item.peak_id == record.peak_id
        )
        acquired = next(
            item
            for item in result.acquisition.traces
            if item.coordinate.request.mz == candidate.peak_mz
        )
        assert (
            acquired.coordinate.request.ppm_tol == settings.precursor_mz_tolerance_ppm
        )
        changed = acquired.coordinate.request.rt_min < record.work_item.xic_rt_min or (
            acquired.coordinate.request.rt_max > record.work_item.xic_rt_max
        )
        changed_questions += changed
        expected = evaluate_physical_peak(
            prepare_physical_signal(
                acquired.trace.rt, acquired.trace.intensity, profile=profile
            ),
            query_rt_min=record.evaluation.question.query_rt_min,
            request_context_rt_start_min=acquired.coordinate.request.rt_min,
            request_context_rt_end_min=acquired.coordinate.request.rt_max,
        )
        assert (expected != record.evaluation) == changed
        capture = captures[record.peak_id]
        assert capture.physical_evaluation == expected
        binding = next(
            item
            for item in capture.scope.capture_bindings
            if item.candidate_id == record.peak_id
        )
        assert binding.source_physical_evaluation is None
        assert binding.physical_evaluation is capture.physical_evaluation
        assert (
            capture.physical_evaluation.question.selection_policy_version
            == "baseline_peak_contains_query"
        )
        assert binding.acquisition_source_id == source.acquisition_source_id
        assert binding.sample_key == fixture.sample("S1").identity.sample_key
        states.append(expected.state)
        replay_binding = replace(
            binding,
            query_mz=record.work_item.xic_mz,
            query_rt_min=record.work_item.xic_rt_min,
            query_rt_max=record.work_item.xic_rt_max,
            query_ppm_tol=record.work_item.xic_ppm_tol,
            physical_evaluation=record.evaluation,
            source_physical_evaluation=None,
            physical_evaluation_mz=record.work_item.xic_mz,
        )
        replay_scope = replace(
            capture.scope,
            capture_bindings=tuple(
                replay_binding if item.candidate_id == record.peak_id else item
                for item in capture.scope.capture_bindings
            ),
        )
        replay = capture_owner.capture_peak_hypotheses(
            scope=replay_scope,
            candidate_id=record.peak_id,
            trace=trace_from_xic_request(
                sample_name=binding.sample_key,
                request=record.acquired_trace.coordinate.request,
                xic_trace=record.acquired_trace.trace,
                source="retained-discovery-physical",
                acquisition_source_id=binding.acquisition_source_id,
            ),
        )
        assert replay.physical_evaluation == record.evaluation
        assert (
            replay.physical_evaluation.question.selection_policy_version
            == "baseline_peak_contains_query"
        )
        if expected.state == "unavailable":
            assert capture.selected_hypothesis is None
            continue
        integration = physical_peak_observation_integration(expected)
        # A candidate that selected an already retained peak supports it.
        (observed,) = (
            item
            for item in assessment.candidate_peaks
            if item.content.integration.rt_apex_min == integration.rt_apex_min
        )
        assert capture.selected_hypothesis.integration == integration
        assert (
            observed.content.integration.area_baseline_corrected
            == integration.area_baseline_corrected
        )
        assert observed.content.integration.rt_apex_min == integration.rt_apex_min
    assert states == (["available"] if fresh_available else ["unavailable"])
    assert changed_questions == 0
    batch = acquire_untargeted_cid_nl_sample_trace_batch(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        peaks=discovery.peaks,
        settings=settings,
        raw=raw,
        signal_processing_profile=profile,
    )
    assert batch is not None
    acquisition_calls = tuple(vendor.chromatogram_request_counts)
    reused = adapt_current_discovery_sample_subject(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        peaks=discovery.peaks,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=profile,
        physical_records=legacy_records,
        _preacquired_traces=batch,
    )
    assert reused.acquisition is batch.acquisition
    assert tuple(vendor.chromatogram_request_counts) == acquisition_calls
    assert reused.assessment_input.assessment == assessment
    assert [item.physical_evaluation for item in reused.captures] == [
        item.physical_evaluation for item in result.captures
    ]
    with pytest.raises(ValueError, match="physical capture data"):
        capture_owner.capture_reacquired_physical_peak(
            scope=capture.scope,
            candidate_id=capture.candidate_id,
            trace=replace(capture.trace, intensity=capture.trace.intensity + 1.0),
        )


@pytest.mark.parametrize(
    ("subject_mz", "batch_subject", "interference_abundance"),
    [
        (500.0, False, 0.5),
        (500.015, True, 0.0),
        (500.015, False, 0.5),
    ],
)
def test_physical_mass_change_preserves_scan_evidence_and_requantifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    subject_mz: float,
    batch_subject: bool,
    interference_abundance: float,
) -> None:
    import json
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
        REQUEST_INPUT_KEY,
        mint_untargeted_cid_nl_method_subject,
        mint_untargeted_cid_nl_method_subject_batch,
    )
    from xic_extractor.peak_detection.search_scope import (
        PHYSICAL_CAPTURE_INPUT_KEY,
        physical_capture_input_payload,
    )
    from xic_extractor.processing_identity import (
        build_processing_request_identity,
        compose_processing_owner_references,
        issue_processing_attempt,
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
    discovery = capture_ms1_peaks(
        raw,
        build_discovery_ions(
            collect_strict_nl_events(
                raw, raw_file=fixture.source_paths["S1"], settings=settings
            ),
            settings=settings,
        ),
        acquisition_source=fixture.owners.request.acquisition_sources[0],
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid_mz=lambda *_: None,
    )
    (original,) = discovery.peaks
    (record,) = discovery.physical_evaluations
    candidate = replace(original, ms1_apex_mz=physical_mz, peak_mz=physical_mz)
    method_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=subject_mz,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    pinned = (
        mint_untargeted_cid_nl_method_subject_batch(
            subjects=(method_subject,),
            candidate_memberships=(
                (candidate.peak_id, method_subject.method_subject_id),
            ),
        )
        if batch_subject
        else method_subject
    )
    old_request = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=old_request.acquisition_sources,
        acquisition_bindings=old_request.acquisition_bindings,
        scientific_input_schema_version=old_request.scientific_input_schema_version,
        processing_algorithm_version=old_request.processing_algorithm_version,
        processing_contract_version=old_request.processing_contract_version,
        scientific_inputs=json.loads(old_request.normalized_scientific_inputs_json)
        | {
            REQUEST_INPUT_KEY: pinned.request_payload(),
            PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(profile),
        },
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(),
        sample_snapshot=fixture.owners.sample_snapshot,
        sample_resolutions=fixture.samples,
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        method_subject=method_subject,
        peaks=(candidate,),
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=profile,
        physical_records=(record,),
    )
    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "HOLD"
    assert not assessment.individual_support_refs
    assert {e.observation.scan_number for e in assessment.evidence_refs} == {
        scan.scan_number for scan in source_raw.scans
    }
    assert all(
        e.observation.precursor_mz == pytest.approx(500.016)
        for e in assessment.evidence_refs
    )
    (capture,) = result.captures
    assert capture.trace.mz == physical_mz
    assert capture.trace.rt_min == record.work_item.xic_rt_min
    assert capture.trace.rt_max == record.work_item.xic_rt_max
    assert capture.selected_hypothesis is not None
    assert record.evaluation.facts is not None
    assert capture.selected_hypothesis.integration.area_baseline_corrected == (
        pytest.approx(
            record.evaluation.facts.area_counts_seconds / (1 + interference_abundance)
        )
    )
    assert (
        candidate.best_event_precursor_mz
        == record.work_item.xic_mz
        == pytest.approx(500.016)
    )


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
        acquisition_source=fixture.owners.request.acquisition_sources[0],
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid_mz=lambda *_: None,
    )
    assert len(discovery.peaks) == 2
    owners = bind_physical_capture(fixture.owners, {}, profile=profile)
    result = adapt_current_discovery_sample_subject(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        peaks=discovery.peaks,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=profile,
        physical_records=discovery.physical_evaluations,
    )
    assessment = result.assessment_input.assessment
    left, right = sorted(
        assessment.candidate_peaks,
        key=lambda peak: peak.content.integration.rt_apex_min,
    )
    assert left.content.integration.rt_right_min < (
        right.content.integration.rt_left_min
    )
    assert assessment.content.method_result == "SUPPORT"
    assert assessment.set_relation_refs == ()
    assert {
        support.content.peak_observation.physical_peak_key
        for support in assessment.individual_support_refs
    } == {
        left.content.physical_peak_key,
        right.content.physical_peak_key,
    }
