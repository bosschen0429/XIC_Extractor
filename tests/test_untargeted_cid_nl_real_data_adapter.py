from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _multi_tag_settings,
    _open_bound_raw,
    _peak_config,
    _settings,
    bind_physical_capture,
)
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_bootstrap import (
    bootstrap_cid_nl_initial_population,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    bind_retained_peak_hypotheses_to_assessment_universe,
    compose_peak_hypotheses_from_assessment_universe,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.untargeted_execution.partition.cid_nl_acquisition import (
    CidNlAcquisitionError,
    UntargetedCidNlBackfillTarget,
    acquire_untargeted_cid_nl_backfill_trace_batch,
    acquire_untargeted_cid_nl_sample_trace_batch,
    adapt_current_discovery_sample_subject,
    backfill_untargeted_cid_nl_sample_subject,
)


@pytest.mark.parametrize(
    ("fresh_available", "precursor_case"),
    [
        (True, "direct"),
        (False, "direct"),
        (True, "shifted"),
        (False, "shifted"),
        (False, "inferred"),
    ],
)
def test_changed_physical_query_evaluates_once_and_preserves_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fresh_available: bool,
    precursor_case: str,
) -> None:
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.discovery.grouping import group_discovery_seeds
    from xic_extractor.discovery.ms1_capture import (
        capture_ms1_candidates,
    )
    from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
    from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
    from xic_extractor.peak_detection import capture as capture_owner
    from xic_extractor.peak_detection.observations import (
        physical_peak_observation_integration,
    )
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        evaluate_physical_peak,
        prepare_physical_signal,
    )
    from xic_extractor.peak_detection.search_scope import (
        PHYSICAL_CAPTURE_INPUT_KEY,
        physical_capture_input_payload,
    )
    from xic_extractor.peak_detection.traces import trace_from_xic_request
    from xic_extractor.processing_identity import (
        build_processing_request_identity,
        compose_processing_owner_references,
        issue_processing_attempt,
    )
    from xic_extractor.untargeted_execution.partition import (
        cid_nl_acquisition as adapter,
    )

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
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
        seed_rt_gap_min=0.01,
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

        monkeypatch.setattr(
            type(raw),
            "iter_ms2_scan_subset",
            lambda self, lo, hi, ids: (
                event
                for event in source_raw.iter_ms2_scans(lo, hi)
                if event.scan_number in ids
            ),
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
    seeds = collect_strict_nl_seeds(
        raw, raw_file=fixture.source_paths["S1"], settings=settings
    )
    discovery = capture_ms1_candidates(
        raw,
        group_discovery_seeds(seeds, settings=settings),
        acquisition_source=source,
        run_spec_id="processing-run:" + "0" * 64,
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid_mz=lambda *_: None,
    )
    assert len(discovery.candidates) == 2
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
                selection_subject_id=record.evaluation.question.selection_subject_id,
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
        for record in discovery.physical_evaluations
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
            PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(
                profile,
                {r.candidate_id: r.evaluation for r in legacy_records},
            )
        },
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=fixture.owners.sample_snapshot,
        sample_resolutions=fixture.samples,
    )
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
    result = adapt_current_discovery_sample_subject(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=discovery.candidates,
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
    assert len(result.acquisition.traces) == (1 if precursor_case == "direct" else 2)
    states = []
    changed_questions = 0
    captures = {capture.candidate_id: capture for capture in result.captures}
    assert len(captures) == 2
    # A candidate that selected an already selected peak on its own shifted
    # trace reads that peak once more on the first trace.
    shared_reads = 1 if shifted_mass and fresh_available else 0
    assert evaluate.call_count == 2 + shared_reads
    assert prepare.call_count == 2 + shared_reads
    for record in legacy_records:
        candidate = next(
            item
            for item in discovery.candidates
            if item.candidate_id == record.candidate_id
        )
        acquired = next(
            item
            for item in result.acquisition.traces
            if item.coordinate.request.mz == candidate.precursor_mz
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
            selection_subject_id=record.evaluation.question.selection_subject_id,
            query_rt_min=record.evaluation.question.query_rt_min,
            request_context_rt_start_min=acquired.coordinate.request.rt_min,
            request_context_rt_end_min=acquired.coordinate.request.rt_max,
        )
        assert (expected != record.evaluation) == changed
        capture = captures[record.candidate_id]
        assert capture.physical_evaluation == expected
        binding = next(
            item
            for item in capture.scope.capture_bindings
            if item.candidate_id == record.candidate_id
        )
        assert binding.source_physical_evaluation is record.evaluation
        assert binding.physical_evaluation is capture.physical_evaluation
        assert (
            binding.source_physical_evaluation.question.selection_policy_version
            == "baseline_peak_contains_query"
        )
        assert (
            capture.physical_evaluation.question.selection_policy_version
            == "baseline_peak_contains_query"
        )
        assert binding.acquisition_source_id == source.acquisition_source_id
        assert (
            binding.sample_identity_id
            == fixture.sample("S1").identity.sample_identity_id
        )
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
                replay_binding if item.candidate_id == record.candidate_id else item
                for item in capture.scope.capture_bindings
            ),
        )
        replay = capture_owner.capture_peak_hypotheses(
            scope=replay_scope,
            candidate_id=record.candidate_id,
            trace=trace_from_xic_request(
                sample_name=binding.sample_name,
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
    assert states == (["available"] * 2 if fresh_available else ["unavailable"] * 2)
    assert changed_questions == 2
    batch = acquire_untargeted_cid_nl_sample_trace_batch(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        candidates=discovery.candidates,
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
        candidates=discovery.candidates,
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
    if precursor_case == "inferred":
        from xic_extractor.untargeted_execution.partition.workers import (
            _backfill_support_context,
        )

        assert assessment.content.reason_codes == (
            "qualifying_cid_nl_without_usable_ms1_peak",
        )
        support = _backfill_support_context(result)
        # The group search finds a peak in its own window. It still needs the
        # measured parent evidence from the empty initial capture to qualify it.
        source_raw.intensity = 1000.0 * np.exp(
            -0.5 * ((source_raw.rt - center) / 0.035) ** 2
        )
        recovery = backfill_untargeted_cid_nl_sample_subject(
            processing_owners=owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            group_center_rt=center,
            group_center_mz=500.0,
            support_policy=support.support_policy,
            peak_config=peak_config,
            raw=raw,
            initial_assessment=support.initial_assessment,
            settings=settings,
            signal_processing_profile=profile,
        )
        assert recovery.assessment.content.method_result == "SUPPORT"
        assert len(recovery.assessment.candidate_peaks) == 1
        assert len(recovery.assessment.evidence_refs) == 2
    with pytest.raises(ValueError, match="trace_content_id"):
        capture_owner.capture_reacquired_physical_peak(
            scope=capture.scope,
            candidate_id=capture.candidate_id,
            trace=replace(capture.trace, intensity=capture.trace.intensity + 1.0),
        )
    with pytest.raises(
        capture_owner.SampleLocalCaptureError, match="coordinates mismatch"
    ):
        capture_owner.capture_reacquired_physical_peak(
            scope=capture.scope,
            candidate_id=capture.candidate_id,
            trace=replace(
                capture.trace,
                rt_min=capture.trace.rt_min - 0.01,
                acquisition_source_id="xic.acquisition-source:" + "0" * 64,
            ),
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
    from xic_extractor.discovery.grouping import group_discovery_seeds
    from xic_extractor.discovery.ms1_capture import (
        capture_ms1_candidates,
    )
    from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
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

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
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
    discovery = capture_ms1_candidates(
        raw,
        group_discovery_seeds(
            collect_strict_nl_seeds(
                raw, raw_file=fixture.source_paths["S1"], settings=settings
            ),
            settings=settings,
        ),
        acquisition_source=fixture.owners.request.acquisition_sources[0],
        run_spec_id="processing-run:" + "0" * 64,
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid_mz=lambda *_: None,
    )
    (original,) = discovery.candidates
    (record,) = discovery.physical_evaluations
    candidate = replace(original, ms1_apex_mz=physical_mz)
    method_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=subject_mz,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    pinned = (
        mint_untargeted_cid_nl_method_subject_batch(
            subjects=(method_subject,),
            candidate_memberships=(
                (candidate.candidate_id, method_subject.method_subject_id),
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
            PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(
                profile, {record.candidate_id: record.evaluation}
            ),
        },
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=fixture.owners.sample_snapshot,
        sample_resolutions=fixture.samples,
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        method_subject=method_subject,
        candidates=(candidate,),
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
    assert candidate.precursor_mz == record.work_item.xic_mz == pytest.approx(500.016)


def test_adapter_preserves_complete_multiscan_evidence_without_target_identity(
    tmp_path: Path,
) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.98, 8.02))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(physical_records[c.candidate_id] for c in (candidates)),
    )

    assessment = result.assessment_input.assessment
    assert assessment.content.method_subject.subject_kind == "untargeted_precursor"
    assert assessment.content.target_definition_id is None
    assert assessment.content.method_result == "SUPPORT"
    assert tuple(
        sorted(
            evidence.observation.scan_number for evidence in assessment.evidence_refs
        )
    ) == tuple(sorted(candidates[0].seed_scan_ids))
    assert len(result.assessment_input.discovery_projections) == 2
    assert result.acquisition is not None
    assert result.acquisition.logical_xic_request_count == 1
    assert result.acquisition.xic_batch_count == 1
    assert result.acquisition.adapter_chromatogram_call_count == 1
    assert vendor_raw.chromatogram_request_counts == [1]


def test_adapter_retains_completed_relation_before_later_evidence_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical_records = {}
    from xic_extractor.untargeted_execution.partition import cid_nl_acquisition

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
    source_raw = _FakeRaw((7.98, 8.02))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    raw, _vendor_raw = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    observer = cid_nl_acquisition._PartitionWorkObserver(fixture.owners)
    resolve_relations = cid_nl_acquisition.resolve_cid_nl_method_relations
    successful_relations = []

    def fail_later_relation(**kwargs):
        if successful_relations:
            raise RuntimeError("later relation resolution failed")
        resolved = resolve_relations(**kwargs)
        successful_relations.append(resolved)
        return resolved

    monkeypatch.setattr(
        cid_nl_acquisition, "resolve_cid_nl_method_relations", fail_later_relation
    )
    with pytest.raises(RuntimeError, match="later relation resolution failed"):
        adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=candidates,
            settings=settings,
            peak_config=peak_config,
            raw=raw,
            _observer=observer,
            signal_processing_profile=TEST_PROFILE,
            physical_records=tuple(
                physical_records[c.candidate_id] for c in (candidates)
            ),
        )

    (((support,), set_relations),) = successful_relations
    assert set_relations == ()
    retained = observer.artifact_closure()
    retained_ids = {artifact.evidence_artifact_id for artifact in retained}
    assert support.artifact in retained
    assert set(support.artifact.upstream_artifact_ids).issubset(retained_ids)
    assert all(
        artifact.producer_processing_request_id
        == fixture.owners.request.processing_request_id
        and artifact.producer_processing_attempt_id
        == fixture.owners.attempt.processing_attempt_id
        for artifact in retained
    )
    assert observer.initial_acquisition is not None


def test_adapter_preserves_strict_scans_within_mixed_candidate(
    tmp_path: Path,
) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.98, 8.0, 8.02))
    strict_scan_ids = tuple(scan.scan_number for scan in source_raw.scans[:2])
    inferred_scan = replace(
        source_raw.scans[2],
        precursor_mz=source_raw.scans[2].precursor_mz + 0.5,
    )
    source_raw.scans = (*source_raw.scans[:2], inferred_scan)
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    (candidate,) = candidates
    assert candidate.precursor_mz_basis == "mixed"
    assert set(candidate.seed_scan_ids) == {
        *strict_scan_ids,
        inferred_scan.scan_number,
    }
    raw, _vendor_raw = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)

    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(physical_records[c.candidate_id] for c in (candidates)),
    )

    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "SUPPORT"
    assert result.strict_scan_count == len(strict_scan_ids)
    assert tuple(
        sorted(
            evidence.observation.scan_number
            for evidence in assessment.evidence_refs
            if evidence.observation.precursor_mz_basis == "scan_precursor"
        )
    ) == tuple(sorted(strict_scan_ids))
    assert {
        evidence.observation.scan_number
        for evidence in assessment.evidence_refs
        if evidence.observation.precursor_mz_basis == "product_plus_neutral_loss"
    } == {inferred_scan.scan_number}


def test_adapter_preserves_strict_support_after_ms1_merge_of_inferred_scan(
    tmp_path: Path,
) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.98, 8.0, 8.02))
    first, inferred, representative = source_raw.scans
    source_raw.scans = (
        replace(
            first,
            precursor_mz=first.precursor_mz + 0.005,
            masses=first.masses + 0.005,
        ),
        replace(
            inferred,
            precursor_mz=inferred.precursor_mz + 0.5,
            masses=inferred.masses + 0.009,
            intensities=np.asarray((700.0, 200.0)),
        ),
        replace(representative, intensities=np.asarray((900.0, 100.0))),
    )
    settings = replace(_settings(), neutral_loss_profile=None, seed_rt_gap_min=0.01)
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    (candidate,) = candidates
    assert candidate.precursor_mz_basis == "mixed"
    assert candidate.best_ms2_scan_id == representative.scan_number
    assert set(candidate.seed_scan_ids) == {
        first.scan_number,
        inferred.scan_number,
        representative.scan_number,
    }
    inferred_product_mz = float(source_raw.scans[1].masses[0])
    assert (
        abs(inferred_product_mz - candidate.product_mz) / candidate.product_mz * 1e6
        > settings.product_mz_tolerance_ppm
    )
    raw, _vendor_raw = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)

    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(physical_records[c.candidate_id] for c in (candidates)),
    )

    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "SUPPORT"
    assert result.strict_scan_count == 2
    assert {
        evidence.observation.scan_number
        for evidence in assessment.evidence_refs
        if evidence.observation.precursor_mz_basis == "scan_precursor"
    } == {first.scan_number, representative.scan_number}


@pytest.mark.parametrize("ms1_present", [True, False])
def test_inferred_precursor_retains_physical_peak_without_strict_nl_support(
    tmp_path: Path,
    ms1_present: bool,
) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.98, 8.0, 8.02))
    source_raw.scans = tuple(
        replace(scan, precursor_mz=scan.precursor_mz + 0.5) for scan in source_raw.scans
    )
    if not ms1_present:
        source_raw.intensity = np.zeros_like(source_raw.intensity)
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    assert candidates
    assert all(c.precursor_mz_basis == "product_plus_neutral_loss" for c in candidates)
    raw, _vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(physical_records[c.candidate_id] for c in (candidates)),
    )
    assessment = result.assessment_input.assessment
    assert result.strict_scan_count == 0
    if ms1_present:
        assert assessment.content.method_result == "HOLD"
        assert not assessment.individual_support_refs
        assert len(assessment.candidate_peaks) == 1
        assert {ref.observation.scan_number for ref in assessment.evidence_refs} == {
            scan.scan_number for scan in source_raw.scans
        }
        assert all(
            ref.observation.precursor_mz_basis == "product_plus_neutral_loss"
            and ref.observation.scan_precursor_mz == 500.5
            and ref.observation.precursor_mz == 500.0
            for ref in assessment.evidence_refs
        )
    else:
        assert assessment.content.technical_state == "not_evaluated"
        assert assessment.content.method_result is None
        assert assessment.content.reason_codes == (
            "no_search_eligible_discovery_input",
        )
        assert assessment.content.evaluation_receipt.search_completed is False
        assert assessment.content.evaluation_receipt.scanned_event_count == 0
        assert not assessment.candidate_peaks
        assert not assessment.individual_support_refs
        assert result.acquisition is None


@pytest.mark.parametrize("reverse_products", [False, True])
@pytest.mark.parametrize("parent_supported", [False, True])
@pytest.mark.parametrize("composite", [False, True])
def test_adapter_resolves_one_event_from_same_scan_loss_product_alternatives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reverse_products: bool,
    parent_supported: bool,
    composite: bool,
) -> None:
    physical_records = {}
    import json
    from types import SimpleNamespace

    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        REQUEST_INPUT_KEY,
        CidNlComposition,
        mint_untargeted_cid_nl_method_subject,
    )
    from xic_extractor.processing_identity import (
        build_processing_request_identity,
        compose_processing_owner_references,
        issue_processing_attempt,
    )

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
    source_raw = _FakeRaw((8.0,))
    scan = source_raw.scans[0]
    # The weaker product's parent window contains one ion. The stronger
    # product's shifted window contains both ions and cannot supply support.
    products = (383.9526, 383.9536)
    intensities = (11900.0, 12000.0) if parent_supported else (12000.0, 11900.0)
    if composite:
        products += (367.9577, 367.9587)
        intensities += intensities
    if reverse_products:
        products = products[::-1]
        intensities = intensities[::-1]
    source_raw.scans = (
        replace(
            scan,
            precursor_mz=500.5,
            precursor_metadata=Ms2PrecursorMetadata(
                charge=1,
                isolation_width_da=1.6,
                isolation_offset_da=0.0,
                master_scan_number=scan.scan_number - 1,
            ),
            masses=np.asarray(products),
            intensities=np.asarray(intensities),
            base_peak=12000.0,
        ),
    )
    settings = (
        replace(_multi_tag_settings(), tag_combine_mode="intersection")
        if composite
        else _settings()
    )
    if composite:
        subject = mint_untargeted_cid_nl_method_subject(
            precursor_mz=500.0,
            precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
            composition=CidNlComposition(
                tags=tuple(
                    (profile.tag, profile.neutral_loss_da)
                    for profile in settings.neutral_loss_profiles
                ),
                combine_mode=settings.tag_combine_mode,
            ),
        )
        old_request = fixture.owners.request
        request = build_processing_request_identity(
            acquisition_sources=old_request.acquisition_sources,
            acquisition_bindings=old_request.acquisition_bindings,
            scientific_input_schema_version=old_request.scientific_input_schema_version,
            processing_algorithm_version=old_request.processing_algorithm_version,
            processing_contract_version=old_request.processing_contract_version,
            scientific_inputs=json.loads(old_request.normalized_scientific_inputs_json)
            | {REQUEST_INPUT_KEY: subject.request_payload()},
        )
        fixture = replace(
            fixture,
            method_subject=subject,
            owners=compose_processing_owner_references(
                request=request,
                attempt=issue_processing_attempt(request),
                sample_snapshot=fixture.owners.sample_snapshot,
                sample_resolutions=fixture.samples,
            ),
        )
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    raw, vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    # Supply only the vendor boundary; RAW metadata parsing and the batched
    # parent mass-window owner remain active.
    original_filter = vendor.GetFilterForScanNumber

    def scan_filter(number):
        result = original_filter(number)
        if number == scan.scan_number:
            reaction = result.Filter.Reactions[0]
            reaction.IsolationWidth = 1.6
            reaction.IsolationWidthOffset = 0.0
        return result

    monkeypatch.setattr(vendor, "GetFilterForScanNumber", scan_filter)
    vendor.trailer_labels = ["Charge State:", "Master Scan Number:"]
    monkeypatch.setattr(
        vendor,
        "GetTrailerExtraInformation",
        lambda number: SimpleNamespace(
            Labels=("Charge State:", "Master Scan Number:"),
            Values=(1, number - 1),
        ),
        raising=False,
    )
    monkeypatch.setattr(raw._thermo, "ms1_order", "Ms1", raising=False)
    parent_reads = []

    def parent_centroids(number, _profile):
        parent_reads.append(number)
        return SimpleNamespace(
            Masses=[500.0, 500.0105] if parent_supported else [],
            Intensities=[1000.0, 500.0] if parent_supported else [],
        )

    monkeypatch.setattr(vendor, "GetCentroidStream", parent_centroids, raising=False)
    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    try:
        result = adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=candidates[::-1] if reverse_products else candidates,
            settings=settings,
            peak_config=peak_config,
            raw=raw,
            signal_processing_profile=TEST_PROFILE,
            physical_records=tuple(
                physical_records[c.candidate_id]
                for c in (candidates[::-1] if reverse_products else candidates)
            ),
        )
    finally:
        raw.__exit__(None, None, None)
    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == (
        "SUPPORT" if parent_supported else "HOLD"
    )
    assert result.strict_scan_count == int(parent_supported)
    assert len(assessment.candidate_peaks) == 1
    event_count = 2 if composite else 1
    assert len(assessment.evidence_refs) == event_count
    products_by_loss = {
        ref.observation.configured_neutral_loss_da: ref.observation.product_mz
        for ref in assessment.evidence_refs
    }
    assert products_by_loss == (
        {116.0474: 383.9526, 132.0423: 367.9577} if composite else {116.0474: 383.9526}
    )
    for ref in assessment.evidence_refs:
        evidence = ref.observation
        assert evidence.scan_number == scan.scan_number
        assert evidence.precursor_mz_basis == "product_plus_neutral_loss"
        assert evidence.scan_precursor_mz == 500.5
        assert evidence.precursor_mz == pytest.approx(500.0)
        assert evidence.observed_loss_error_ppm is None
    assert len(assessment.individual_support_refs) == (
        event_count if parent_supported else 0
    )
    assert parent_reads == [scan.scan_number - 1]
    if parent_supported:
        for ref in assessment.individual_support_refs:
            relation = ref.content
            assert relation.precursor_mz == 500.0
            assert relation.observed_loss_error_ppm == pytest.approx(0.0, abs=1e-8)


@pytest.mark.parametrize("reverse_products", [False, True])
def test_scan_event_prefers_independent_direct_loss_over_stronger_inference(
    tmp_path: Path, reverse_products: bool
) -> None:
    from xic_extractor.discovery.ms2_seeds import discovery_seeds_from_scan
    from xic_extractor.discovery.strict_seed_receipts import (
        resolve_discovery_scan_events,
    )

    source_raw = _FakeRaw((8.0,))
    products = (383.9526, 383.9826)
    intensities = (11900.0, 12000.0)
    if reverse_products:
        products = products[::-1]
        intensities = intensities[::-1]
    scan = replace(
        source_raw.scans[0],
        masses=np.asarray(products),
        intensities=np.asarray(intensities),
        base_peak=12000.0,
    )
    settings = _settings()
    seeds = discovery_seeds_from_scan(
        scan, raw_file=tmp_path / "S1.RAW", settings=settings
    )
    assert len(seeds) == 2
    assert {seed.precursor_mz_basis for seed in seeds} == {
        "scan_precursor",
        "product_plus_neutral_loss",
    }
    forward = resolve_discovery_scan_events(seeds, settings=settings)
    reverse = resolve_discovery_scan_events(seeds[::-1], settings=settings)
    assert forward == reverse
    (event,) = forward
    assert event.precursor_mz_basis == "scan_precursor"
    assert event.product_mz == 383.9526
    assert event.product_intensity == 11900.0
    assert event.assess_precursor_loss(settings.nl_tolerance_ppm).supports_nl


@pytest.mark.parametrize("cached", [False, True])
def test_inferred_precursor_can_gain_support_from_measured_parent_ms1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached: bool
) -> None:
    physical_records = {}
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
    source_raw = _FakeRaw((7.98, 8.0, 8.02))
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
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    raw, _vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    monkeypatch.setattr(
        type(raw),
        "iter_ms2_scan_subset",
        lambda self, lo, hi, ids: (
            event
            for event in source_raw.iter_ms2_scans(lo, hi)
            if event.scan_number in ids
        ),
    )

    def read_parent_windows(self, requests):
        from xic_extractor.precursor_evidence import Ms1PrecursorObservation

        return tuple(
            Ms1PrecursorObservation(
                scan_number=number,
                rt_min=float(source_raw.rt[number - 1]),
                centroid_mzs=(500.001,),
            )
            for number, _low, _high in requests
        )

    monkeypatch.setattr(
        type(raw), "read_ms1_mass_windows", read_parent_windows, raising=False
    )
    if cached:
        from xic_extractor.untargeted_execution.acquisition_cache import (
            CachedUntargetedRaw,
        )

        raw = CachedUntargetedRaw(
            raw, source=raw.acquisition_source, cache_dir=tmp_path / "raw-facts"
        )
    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(physical_records[c.candidate_id] for c in (candidates)),
    )
    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "SUPPORT"
    assert result.strict_scan_count == 3
    assert len(assessment.candidate_peaks) == 1
    assert len(assessment.individual_support_refs) == 3
    for evidence in assessment.evidence_refs:
        assert evidence.observation.precursor_mz_basis == "product_plus_neutral_loss"
        assert evidence.observation.precursor_mz == 500.0
        assert evidence.observation.observed_loss_error_ppm is None
    for relation in assessment.individual_support_refs:
        assert relation.content.precursor_mz == 500.001
        assert relation.content.observed_loss_error_ppm == pytest.approx(
            0.001 / 116.0474 * 1e6
        )


@pytest.mark.parametrize("moved_field", ["precursor_mz", "product_mz"])
def test_inferred_precursor_off_its_scan_receipt_is_rejected(
    tmp_path: Path, moved_field: str
) -> None:
    # An inferred precursor stays product + loss; its apex MS1 reading is only
    # reported. A persisted precursor or product off the scan's receipt breaks
    # the strict seed reconstruction.
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.98, 8.0, 8.02))
    source_raw.scans = tuple(
        replace(scan, precursor_mz=scan.precursor_mz + 0.5) for scan in source_raw.scans
    )
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = tuple(
        replace(
            candidate,
            **{moved_field: getattr(candidate, moved_field) * (1 + 7e-6)},
        )
        for candidate in _current_candidates(
            source_raw,
            raw_file=fixture.source_paths["S1"],
            settings=settings,
            physical_records=physical_records,
        )
    )
    assert all(c.precursor_mz_basis == "product_plus_neutral_loss" for c in candidates)
    raw, _vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)

    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )

    def adapt():
        return adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=candidates,
            settings=settings,
            peak_config=peak_config,
            raw=raw,
            signal_processing_profile=TEST_PROFILE,
            physical_records=tuple(
                physical_records[c.candidate_id] for c in (candidates)
            ),
        )

    with pytest.raises(CidNlAcquisitionError, match="seed is not unique"):
        adapt()


def test_merged_inferred_member_outside_final_mass_window_is_not_support(
    tmp_path: Path,
) -> None:
    from xic_extractor.discovery.strict_seed_receipts import (
        read_complete_discovery_scan_seeds,
    )

    source_raw = _FakeRaw((7.98, 8.0, 8.02))
    source_raw.scans = tuple(
        replace(
            scan,
            precursor_mz=scan.precursor_mz + 0.5,
            masses=scan.masses + offset,
            intensities=np.asarray((800.0 + index * 100.0, 200.0)),
        )
        for index, (scan, offset) in enumerate(
            zip(source_raw.scans, (-0.012, -0.005, 0.002), strict=True)
        )
    )
    settings = replace(_settings(), neutral_loss_profile=None, seed_rt_gap_min=0.01)
    (candidate,) = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    assert candidate.precursor_mz_basis == "product_plus_neutral_loss"
    assert len(candidate.seed_scan_ids) == 3
    seeds = read_complete_discovery_scan_seeds(
        source_raw,
        candidates=(candidate,),
        settings=settings,
        include_inferred=True,
    )
    assert {scan.scan_number for scan, _ in seeds} == {
        scan.scan_number for scan in source_raw.scans[1:]
    }


@pytest.mark.parametrize(
    ("scan_rts", "shifts", "supported_scan_count"),
    [
        ((7.98, 8.0, 8.02, 8.04), (0.0, 0.0, 0.008, 0.008), 4),
        ((7.98, 8.0, 8.05, 8.07), (0.0, 0.0, 0.0085, 0.0112), 3),
    ],
    ids=["product-beyond-tolerance", "precursor-beyond-final-window"],
)
def test_shared_apex_merge_keeps_each_absorbed_scan_receipt(
    tmp_path: Path,
    scan_rts: tuple[float, ...],
    shifts: tuple[float, ...],
    supported_scan_count: int,
) -> None:
    from xic_extractor.discovery.strict_seed_receipts import (
        read_complete_discovery_scan_seeds,
    )

    source_raw = _FakeRaw(scan_rts)
    source_raw.intensity = 1000.0 * np.exp(
        -0.5 * ((source_raw.rt - sum(scan_rts) / len(scan_rts)) / 0.06) ** 2
    )
    # The later scans read the same MS1 peak with a precursor and product
    # shifted by scatter; their product lies beyond 20 ppm of the earlier one.
    source_raw.scans = tuple(
        replace(
            scan,
            precursor_mz=scan.precursor_mz + shift,
            masses=scan.masses + shift,
        )
        for scan, shift in zip(source_raw.scans, shifts, strict=True)
    )
    settings = replace(_settings(), neutral_loss_profile=None, seed_rt_gap_min=0.03)
    (candidate,) = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    assert len(candidate.seed_scan_ids) == 4

    seeds = read_complete_discovery_scan_seeds(
        source_raw,
        candidates=(candidate,),
        settings=settings,
    )

    # An absorbed scan outside the final precursor window still reconstructs
    # against its own receipt but cannot support this candidate's mass.
    assert [scan.scan_number for scan, _ in seeds] == list(
        candidate.seed_scan_ids[:supported_scan_count]
    )


@pytest.mark.parametrize("variant", ["missing", "duplicate", "parse_error"])
def test_declared_strict_scan_subset_rejects_non_exact_events(variant: str) -> None:
    from types import SimpleNamespace

    from xic_extractor.discovery.strict_seed_receipts import (
        StrictSeedReceiptError,
        _read_exact_scans,
    )
    from xic_extractor.raw_reader import Ms2ScanEvent

    candidate = SimpleNamespace(
        candidate_id="candidate-1",
        seed_scan_ids=(11, 12),
        rt_seed_min=8.0,
        rt_seed_max=8.2,
    )

    def scan(number):
        return SimpleNamespace(scan_number=number)

    if variant == "missing":
        events = (Ms2ScanEvent(scan(11), None, 11),)
    elif variant == "duplicate":
        events = (
            Ms2ScanEvent(scan(11), None, 11),
            Ms2ScanEvent(scan(11), None, 11),
            Ms2ScanEvent(scan(12), None, 12),
        )
    else:
        events = (
            Ms2ScanEvent(None, "malformed scan", 11),
            Ms2ScanEvent(scan(12), None, 12),
        )

    class _SubsetRaw:
        def iter_ms2_scan_subset(self, _rt_min, _rt_max, _scan_numbers):
            return iter(events)

    with pytest.raises(StrictSeedReceiptError, match="not exact"):
        _read_exact_scans(_SubsetRaw(), candidate)


def test_scan_seed_enumeration_runs_once_per_declared_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xic_extractor.discovery import strict_seed_receipts

    source_raw = _FakeRaw((7.98, 8.0, 8.02))
    settings = _settings()
    (candidate,) = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    assert len(candidate.seed_scan_ids) > 1

    enumerated: list[int] = []
    enumerate_seeds = strict_seed_receipts.discovery_seeds_from_scan

    def counted(scan, **kwargs):
        enumerated.append(scan.scan_number)
        return enumerate_seeds(scan, **kwargs)

    monkeypatch.setattr(strict_seed_receipts, "discovery_seeds_from_scan", counted)

    # Candidates declaring the same scan share one enumeration, and the
    # candidate-specific receipts stay identical to the single-candidate read.
    repeated = strict_seed_receipts.read_complete_discovery_scan_seeds(
        source_raw,
        candidates=(candidate, candidate),
        settings=settings,
    )
    assert sorted(enumerated) == sorted(set(candidate.seed_scan_ids))

    enumerated.clear()
    single = strict_seed_receipts.read_complete_discovery_scan_seeds(
        source_raw,
        candidates=(candidate,),
        settings=settings,
    )
    assert [scan.scan_number for scan, _ in repeated] == [
        scan.scan_number for scan, _ in single
    ]
    assert [seed for _, seed in repeated] == [seed for _, seed in single]


def test_sample_trace_batch_is_acquired_once_and_reused_by_subject_adapters(
    tmp_path: Path,
) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.8, 9.2))
    source_raw.rt = np.linspace(7.0, 10.0, 1201)
    source_raw.intensity = sum(
        1000.0 * np.exp(-0.5 * ((source_raw.rt - apex) / 0.035) ** 2)
        for apex in (7.8, 9.2)
    )
    source_raw.scans = tuple(
        replace(scan, scan_number=int(np.argmin(abs(source_raw.rt - scan.rt))) + 1)
        for scan in source_raw.scans
    )
    settings = replace(_settings(), rt_min=7.0, rt_max=10.0)
    peak_config = _peak_config(tmp_path)
    first, second = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    candidates = tuple(sorted((first, second), key=lambda item: item.candidate_id))
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    batch = acquire_untargeted_cid_nl_sample_trace_batch(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        candidates=candidates,
        settings=settings,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
    )

    assert batch is not None
    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    results = tuple(
        adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=(candidate,),
            settings=settings,
            peak_config=peak_config,
            raw=raw,
            _preacquired_traces=batch,
            signal_processing_profile=TEST_PROFILE,
            physical_records=tuple(
                physical_records[c.candidate_id] for c in ((candidate,))
            ),
        )
        for candidate in candidates
    )

    assert batch.candidate_ids == tuple(
        candidate.candidate_id for candidate in candidates
    )
    assert all(result.acquisition is batch.acquisition for result in results)
    assert [result.source_candidate_ids for result in results] == [
        (candidate.candidate_id,) for candidate in candidates
    ]
    assert vendor_raw.chromatogram_request_counts == [3]


def test_elution_context_shares_acquisition_without_widening_capture(
    tmp_path: Path,
) -> None:
    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
    source = _FakeRaw((7.8,))
    source.intensity += 700.0 * np.exp(-0.5 * ((source.rt - 8.5) / 0.035) ** 2)
    candidate = _current_candidates(
        source,
        raw_file=fixture.source_paths["S1"],
        settings=_settings(),
    )[0]
    duplicate = replace(candidate, candidate_id=candidate.candidate_id + ":duplicate")
    raw, vendor = _open_bound_raw(fixture, "S1", source, tmp_path=tmp_path)
    batch = acquire_untargeted_cid_nl_sample_trace_batch(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        candidates=tuple(
            sorted((candidate, duplicate), key=lambda item: item.candidate_id)
        ),
        settings=_settings(),
        raw=raw,
        signal_processing_profile=SignalProcessingProfile(),
    )
    assert batch is not None
    assert vendor.chromatogram_request_counts == [2]
    assert len(batch.context_trace_indices) == len(batch.elution_contexts) == 1
    assert [
        peak.apex_rt for peak in batch.elution_contexts[0].landmarks
    ] == pytest.approx([7.8, 8.5], abs=0.005)
    original = batch.acquired_trace(candidate.candidate_id)
    assert original.coordinate.request.rt_min == candidate.ms1_search_rt_min
    assert original.coordinate.request.rt_max == candidate.ms1_search_rt_max
    first_scan, last_scan = source.scan_window_for_request(original.coordinate.request)
    mask = slice(first_scan - 1, last_scan)
    np.testing.assert_array_equal(original.trace.rt, source.rt[mask])
    np.testing.assert_array_equal(original.trace.intensity, source.intensity[mask])
    assert original is batch.acquired_trace(duplicate.candidate_id)
    assert max(original.trace.rt) < 8.5


@pytest.mark.parametrize(
    "sample,query_rt,expected_apex,width,tolerance",
    [
        ("S1", 7.8, 7.8, 0.035, None),
        ("S2", 8.5, 8.5, 0.035, None),
        ("S1", 7.55, 7.8, 0.1, 12.0),
    ],
    ids=["first-sample", "second-sample", "query-containing-peak"],
)
def test_backfill_uses_sample_pattern_location_without_claiming_method_support(
    tmp_path: Path,
    sample: str,
    query_rt: float,
    expected_apex: float,
    width: float,
    tolerance: float | None,
) -> None:
    import json

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
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

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={sample: "study_sample"}, thermo_raw_sources=True
    )
    profile = SignalProcessingProfile()
    original = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=original.acquisition_sources,
        acquisition_bindings=original.acquisition_bindings,
        scientific_input_schema_version=original.scientific_input_schema_version,
        processing_algorithm_version=original.processing_algorithm_version,
        processing_contract_version=original.processing_contract_version,
        scientific_inputs=json.loads(original.normalized_scientific_inputs_json)
        | {PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(profile, {})},
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=fixture.owners.sample_snapshot,
        sample_resolutions=fixture.samples,
    )
    source = _FakeRaw((7.8, 8.5))
    if tolerance is not None:
        source.rt = np.linspace(6.5, 9.5, 1201)
    source.intensity = 450.0 * np.exp(
        -0.5 * ((source.rt - 7.8) / width) ** 2
    ) + 1200.0 * np.exp(-0.5 * ((source.rt - 8.5) / 0.035) ** 2)
    raw, _ = _open_bound_raw(fixture, sample, source, tmp_path=tmp_path)
    query_basis = (
        "trajectory_observed"
        if query_rt == expected_apex
        else "calibrated_trajectory_fit"
    )
    target = UntargetedCidNlBackfillTarget(
        "same-group",
        fixture.method_subject,
        500.0,
        8.1,
        query_rt=query_rt,
        query_rt_basis=query_basis,
        query_rt_tolerance_sec=tolerance,
    )
    config = _peak_config(tmp_path)
    batch = acquire_untargeted_cid_nl_backfill_trace_batch(
        processing_owners=owners,
        sample=fixture.sample(sample),
        backfill_requests=(target,),
        peak_config=config,
        raw=raw,
    )
    assert batch.targets[0].center_rt == 8.1
    assert batch.targets[0].sample_query_rt == query_rt
    assert batch.requests[0].rt_min == pytest.approx(
        query_rt - config.nl_rt_anchor_half_window_min
    )
    result = backfill_untargeted_cid_nl_sample_subject(
        processing_owners=owners,
        sample=fixture.sample(sample),
        method_subject=fixture.method_subject,
        group_center_mz=500.0,
        group_center_rt=8.1,
        group_hypothesis_id="same-group",
        query_rt=query_rt,
        query_rt_basis=query_basis,
        query_rt_tolerance_sec=tolerance,
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.5, neutral_loss_tolerance_ppm=20.0
        ),
        peak_config=config,
        raw=raw,
        signal_processing_profile=profile,
        _preacquired_traces=batch,
    )
    assert result.group_hypothesis_id == "same-group"
    assert result.query_rt == query_rt
    assert result.query_rt_basis == query_basis
    assessment = result.assessment
    assert assessment.content.method_result == "HOLD"
    assert not assessment.individual_support_refs
    assert len(assessment.candidate_peaks) == 1
    assert assessment.candidate_peaks[
        0
    ].content.integration.rt_apex_min == pytest.approx(expected_apex, abs=0.005)
    if tolerance is not None:
        evaluation = result.capture_evidence[0].physical_evaluation
        assert evaluation.facts is not None
        assert evaluation.question.search_rt_window == pytest.approx((7.35, 7.75))
        assert evaluation.facts.apex_rt_min > 7.75
        assert evaluation.facts.left_rt_min < query_rt < evaluation.facts.right_rt_min
    from xic_extractor.alignment.backfill.cid_nl_planning import (
        decide_cid_nl_backfill_revisit,
    )

    assert (
        decide_cid_nl_backfill_revisit(
            assessment, observed_rt=expected_apex, apex_tolerance_sec=2.0
        )
        == "reuse"
    )
    other_peak = 8.5 if expected_apex == 7.8 else 7.8
    assert (
        decide_cid_nl_backfill_revisit(
            assessment, observed_rt=other_peak, apex_tolerance_sec=2.0
        )
        == "search"
    )


def test_recovery_batch_canonicalizes_shared_group_queries_without_merging_centers(
    tmp_path: Path,
) -> None:
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.8, 8.2))
    source_raw.intensity = 450.0 * np.exp(
        -0.5 * ((source_raw.rt - 7.8) / 0.035) ** 2
    ) + 1200.0 * np.exp(-0.5 * ((source_raw.rt - 8.2) / 0.035) ** 2)
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    batch = acquire_untargeted_cid_nl_backfill_trace_batch(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        backfill_requests=(
            UntargetedCidNlBackfillTarget(
                "group-b", fixture.method_subject, 500.0, 8.2
            ),
            UntargetedCidNlBackfillTarget(
                "group-a", fixture.method_subject, 500.0, 7.8
            ),
            UntargetedCidNlBackfillTarget(
                "group-c", fixture.method_subject, 500.0, 8.2
            ),
            UntargetedCidNlBackfillTarget(
                "group-d", fixture.method_subject, 500.012, 8.2
            ),
        ),
        peak_config=_peak_config(tmp_path),
        raw=raw,
    )

    assert batch.targets == (
        UntargetedCidNlBackfillTarget("group-a", fixture.method_subject, 500.0, 7.8),
        UntargetedCidNlBackfillTarget("group-b", fixture.method_subject, 500.0, 8.2),
        UntargetedCidNlBackfillTarget("group-c", fixture.method_subject, 500.0, 8.2),
        UntargetedCidNlBackfillTarget("group-d", fixture.method_subject, 500.012, 8.2),
    )
    assert batch.acquisition.logical_xic_request_count == 3
    assert vendor_raw.chromatogram_request_counts == [3]
    first = batch.acquired_trace(
        group_hypothesis_id="group-a",
        request=batch.requests[0],
    )
    second = batch.acquired_trace(
        group_hypothesis_id="group-b",
        request=batch.requests[1],
    )
    assert first.coordinate.request == batch.requests[0]
    assert second.coordinate.request == batch.requests[1]
    assert first.coordinate.request.rt_min < second.coordinate.request.rt_min
    assert (
        batch.acquired_trace(
            group_hypothesis_id="group-a",
            request=batch.requests[0],
        )
        is first
    )
    assert (
        batch.acquired_trace(group_hypothesis_id="group-c", request=batch.requests[2])
        is second
    )
    distinct_mass = batch.acquired_trace(
        group_hypothesis_id="group-d", request=batch.requests[3]
    )
    assert second.coordinate.request.mz == 500.0
    assert distinct_mass.coordinate.request.mz == 500.012
    assert distinct_mass.coordinate.request.rt_min == second.coordinate.request.rt_min


def test_adapter_receipt_covers_candidate_search_beyond_seed_window(
    tmp_path: Path,
) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.99,))
    source_raw.intensity = 1000.0 * np.exp(-0.5 * ((source_raw.rt - 8.10) / 0.035) ** 2)
    settings = replace(_settings(), neutral_loss_profile=None, rt_max=8.0)
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.best_seed_rt <= settings.rt_max
    assert candidate.ms1_apex_rt is not None
    assert candidate.ms1_apex_rt > settings.rt_max
    assert candidate.ms1_apex_rt <= candidate.ms1_search_rt_max
    raw, _vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(physical_records[c.candidate_id] for c in (candidates)),
    )

    receipt = result.assessment_input.assessment.content.evaluation_receipt
    assert receipt.query_rt_min == min(
        settings.rt_min,
        candidate.ms1_search_rt_min,
    )
    assert receipt.query_rt_max == max(
        settings.rt_max,
        candidate.ms1_search_rt_max,
    )


def test_adapter_closes_qualifying_ms2_without_ms1_peak_as_reject(
    tmp_path: Path,
) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidate = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )[0]
    orphan = _without_ms1_peak(candidate)
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=(orphan,),
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(physical_records[c.candidate_id] for c in ((orphan,))),
    )

    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "REJECT"
    assert assessment.candidate_peaks == ()
    assert len(assessment.evidence_refs) == 1
    assert len(result.assessment_input.discovery_projections) == 1
    assert result.acquisition is None
    assert vendor_raw.chromatogram_request_counts == []


def test_adapter_closes_empty_subject_universe_as_not_evaluated_without_xic(
    tmp_path: Path,
) -> None:
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((8.0,))
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    fixture = replace(fixture, owners=bind_physical_capture(fixture.owners, {}))
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=(),
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
    )

    assessment = result.assessment_input.assessment
    assert assessment.content.technical_state == "not_evaluated"
    assert assessment.content.method_result is None
    assert assessment.content.reason_codes == ("no_search_eligible_discovery_input",)
    assert assessment.content.evaluation_receipt.search_completed is False
    assert assessment.content.evaluation_receipt.scanned_event_count == 0
    assert result.assessment_input.discovery_projections == ()
    assert result.acquisition is None
    assert vendor_raw.chromatogram_request_counts == []


def test_auto_range_empty_subject_has_no_fabricated_query_and_needs_no_raw(
    tmp_path: Path,
) -> None:
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
    fixture = replace(fixture, owners=bind_physical_capture(fixture.owners, {}))
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=(),
        settings=replace(_settings(), rt_min=None, rt_max=None),
        peak_config=_peak_config(tmp_path),
        raw=None,
        signal_processing_profile=TEST_PROFILE,
    )
    assessment = result.assessment_input.assessment
    receipt = assessment.content.evaluation_receipt
    assert assessment.content.technical_state == "not_evaluated"
    assert (receipt.query_rt_min, receipt.query_rt_max) == (None, None)
    assert receipt.search_completed is False and receipt.scanned_event_count == 0
    assert result.acquisition is None and not result.captures


def test_auto_range_evaluated_receipt_uses_current_raw_bounds(tmp_path: Path) -> None:
    physical_records = {}
    from types import SimpleNamespace

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    raw, vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    vendor.RunHeaderEx = SimpleNamespace(StartTime=7.4, EndTime=75.0)
    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=replace(settings, rt_min=None, rt_max=None),
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(physical_records[c.candidate_id] for c in (candidates)),
    )
    receipt = result.assessment_input.assessment.content.evaluation_receipt
    assert receipt.search_completed is True
    assert (receipt.query_rt_min, receipt.query_rt_max) == (7.4, 75.0)


def test_adapter_uses_orphan_support_without_giving_it_feature_standing(
    tmp_path: Path,
) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.8, 8.2))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    feature, support_source = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    support = _without_ms1_peak(support_source)
    raw, _vendor_raw = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)

    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=(feature,),
        support_candidates=(support,),
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(physical_records[c.candidate_id] for c in ((feature,))),
    )

    assert result.source_candidate_ids == (feature.candidate_id,)
    assert {capture.candidate_id for capture in result.captures} == {
        feature.candidate_id
    }
    assert len(result.assessment_input.assessment.candidate_peaks) == 1
    assert result.strict_scan_count == 2
    assert len(result.assessment_input.discovery_projections) == 2


def test_adapter_does_not_read_raw_for_orphan_only_support(tmp_path: Path) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    support = _without_ms1_peak(
        _current_candidates(
            source_raw,
            raw_file=fixture.source_paths["S1"],
            settings=settings,
            physical_records=physical_records,
        )[0]
    )

    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=(),
        support_candidates=(support,),
        settings=settings,
        peak_config=_peak_config(tmp_path),
        raw=None,
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(physical_records[c.candidate_id] for c in (())),
    )

    assert result.assessment_input.assessment.content.technical_state == (
        "not_evaluated"
    )
    assert result.assessment_input.discovery_projections == ()
    assert result.captures == ()


@pytest.mark.parametrize("include_direct_candidate", [False, True])
def test_adapter_requires_raw_to_preserve_inferred_physical_observations(
    tmp_path: Path,
    include_direct_candidate: bool,
) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    direct = _current_candidates(
        _FakeRaw((8.0,)),
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )[0]
    inferred = replace(
        direct,
        candidate_id=f"{direct.candidate_id}:inferred",
        precursor_mz_basis="product_plus_neutral_loss",
    )
    candidates = (direct, inferred) if include_direct_candidate else (inferred,)

    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    with pytest.raises(
        CidNlAcquisitionError,
        match="requires an owner-opened RAW adapter",
    ):
        adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=candidates,
            settings=settings,
            peak_config=peak_config,
            raw=None,
            signal_processing_profile=TEST_PROFILE,
            physical_records=tuple(physical_records.values()),
        )


def test_adapter_supports_adjacent_physical_peaks_with_interior_scans(
    tmp_path: Path,
) -> None:
    import json

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.discovery.grouping import group_discovery_seeds
    from xic_extractor.discovery.ms1_capture import (
        capture_ms1_candidates,
    )
    from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
    from xic_extractor.peak_detection.search_scope import (
        PHYSICAL_CAPTURE_INPUT_KEY,
        physical_capture_input_payload,
    )
    from xic_extractor.processing_identity import (
        build_processing_request_identity,
        compose_processing_owner_references,
        issue_processing_attempt,
    )

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
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
    seeds = collect_strict_nl_seeds(
        raw, raw_file=fixture.source_paths["S1"], settings=settings
    )
    discovery = capture_ms1_candidates(
        raw,
        group_discovery_seeds(seeds, settings=settings),
        acquisition_source=fixture.owners.request.acquisition_sources[0],
        run_spec_id="processing-run:" + "0" * 64,
        settings=settings,
        signal_processing_profile=profile,
        ms1_centroid_mz=lambda *_: None,
    )
    assert len(discovery.candidates) == 2
    old_request = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=old_request.acquisition_sources,
        acquisition_bindings=old_request.acquisition_bindings,
        scientific_input_schema_version=old_request.scientific_input_schema_version,
        processing_algorithm_version=old_request.processing_algorithm_version,
        processing_contract_version=old_request.processing_contract_version,
        scientific_inputs=json.loads(old_request.normalized_scientific_inputs_json)
        | {
            PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(
                profile,
                {r.candidate_id: r.evaluation for r in discovery.physical_evaluations},
            )
        },
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=fixture.owners.sample_snapshot,
        sample_resolutions=fixture.samples,
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=discovery.candidates,
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
    assert left.content.integration.rt_right_min == (
        right.content.integration.rt_left_min
    )
    assert assessment.content.method_result == "SUPPORT"
    assert assessment.set_relation_refs == ()
    assert {
        support.content.peak_observation_content_id
        for support in assessment.individual_support_refs
    } == {
        left.content.peak_observation_content_id,
        right.content.peak_observation_content_id,
    }


def test_adapter_preserves_supported_and_observation_only_physical_peaks(
    tmp_path: Path,
) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.8, 8.35))
    source_raw.scans = (
        source_raw.scans[0],
        replace(source_raw.scans[1], precursor_mz=500.5),
    )
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )
    assert len(candidates) == 2
    raw, _vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(physical_records[c.candidate_id] for c in (candidates)),
    )

    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "SUPPORT"
    assert len(assessment.candidate_peaks) == 2
    assert len(assessment.individual_support_refs) == 1
    # Only the recorded precursor supplies NL support.
    assert result.strict_scan_count == 1
    hypotheses = compose_peak_hypotheses_from_assessment_universe(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        assessment=assessment,
    )
    assert {item.hypothesis_method for item in hypotheses} == {
        "dda_cid_nl",
        "ms1_physical_observation",
    }
    rebound = bind_retained_peak_hypotheses_to_assessment_universe(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        assessment=assessment,
        retained_hypotheses=hypotheses,
    )
    assert all(
        rebound_by_id is source_by_id
        for rebound_by_id, source_by_id in zip(rebound, hypotheses, strict=True)
    )
    grouping_scope, candidates, _drift = bootstrap_cid_nl_initial_population(
        processing_owners=fixture.owners,
        sample_assessments=((fixture.sample("S1"), assessment),),
        alignment_config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
    )
    assert sorted(group.owner_ids for group in grouping_scope.groups) == sorted(
        (hypothesis.hypothesis_id,) for hypothesis in hypotheses
    )
    direct_support = {
        candidate.peak_hypothesis.hypothesis_method: (
            candidate.direct_support_assessment
        )
        for candidate in candidates
    }
    assert direct_support == {
        "dda_cid_nl": assessment,
        "ms1_physical_observation": None,
    }


def test_adapter_rejects_discovery_candidate_receipt_drift(tmp_path: Path) -> None:
    physical_records = {}
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidate = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=physical_records,
    )[0]
    assert candidate.ms1_apex_rt is not None
    drifted = replace(candidate, ms1_apex_rt=candidate.ms1_apex_rt + 0.1)
    raw, _vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    fixture = replace(
        fixture, owners=bind_physical_capture(fixture.owners, physical_records)
    )
    with pytest.raises(
        CidNlAcquisitionError,
        match="apex_rt_min mismatches current receipt",
    ):
        adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=(drifted,),
            settings=settings,
            peak_config=peak_config,
            raw=raw,
            signal_processing_profile=TEST_PROFILE,
            physical_records=tuple(
                physical_records[c.candidate_id] for c in ((drifted,))
            ),
        )


@pytest.mark.parametrize("physical_mz_offset", [0.005, 0.012])
def test_recovery_revisits_one_hold_cell_without_minting_support(
    tmp_path: Path,
    physical_mz_offset: float,
) -> None:
    import json

    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        canonical_cid_nl_method_assessment_bytes,
    )

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((8.0,))
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    fixture = replace(fixture, owners=bind_physical_capture(fixture.owners, {}))
    result = backfill_untargeted_cid_nl_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        group_center_rt=8.0,
        group_center_mz=fixture.method_subject.precursor_mz + physical_mz_offset,
        group_hypothesis_id="group-physical",
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01,
            neutral_loss_tolerance_ppm=20.0,
        ),
        peak_config=_peak_config(tmp_path),
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
    )

    assert result.assessment.content.technical_state == "evaluated"
    assert result.group_hypothesis_id == "group-physical"
    assert result.assessment.content.method_subject is fixture.method_subject
    assert result.assessment.content.evaluation_receipt.physical_query_mz == (
        fixture.method_subject.precursor_mz + physical_mz_offset
    )
    payload = json.loads(
        canonical_cid_nl_method_assessment_bytes(result.assessment.content)
    )
    assert payload["content"]["evaluation_receipt"]["physical_query_mz"] == (
        fixture.method_subject.precursor_mz + physical_mz_offset
    )
    assert result.acquisition.traces[0].coordinate.request.mz == pytest.approx(
        fixture.method_subject.precursor_mz + physical_mz_offset
    )
    assert result.assessment.candidate_peaks[0].content.mz == pytest.approx(
        fixture.method_subject.precursor_mz + physical_mz_offset
    )
    assert result.assessment.content.method_result == "HOLD"
    assert result.assessment.content.evaluation_receipt.search_completed is True
    assert result.assessment.content.evaluation_receipt.scanned_event_count == 0
    assert len(result.assessment.candidate_peaks) == 1
    assert result.assessment.evidence_refs == ()
    assert result.backfill_input is not None
    assert result.acquisition.logical_xic_request_count == 2
    assert vendor_raw.chromatogram_request_counts == [2]


def _without_ms1_peak(candidate: DiscoveryCandidate) -> DiscoveryCandidate:
    return replace(
        candidate,
        ms1_peak_found=False,
        ms1_apex_rt=None,
        ms1_area=None,
        ms1_peak_rt_start=None,
        ms1_peak_rt_end=None,
        ms1_height=None,
        ms1_seed_delta_min=None,
        discovery_candidate_state="review_only_orphan_nl",
        ms1_feature_row_id="",
    )


def test_strict_replay_retains_two_recorded_precursors_from_one_scan(tmp_path):
    from xic_extractor.discovery.strict_seed_receipts import (
        read_complete_discovery_scan_seeds,
    )
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata

    source_raw = _FakeRaw((7.98, 8.02))
    source_raw.scans = tuple(
        replace(
            scan,
            precursor_metadata=Ms2PrecursorMetadata(
                monoisotopic_mz=scan.precursor_mz - 1.0035,
                charge=1,
                isolation_width_da=1.6,
            ),
            masses=np.concatenate((scan.masses, scan.masses - 1.0035)),
            intensities=np.concatenate((scan.intensities, scan.intensities)),
        )
        for scan in source_raw.scans
    )
    settings = _settings()
    candidates = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    assert len(candidates) == 2
    seeds = read_complete_discovery_scan_seeds(
        source_raw, candidates=candidates, settings=settings
    )
    assert len(seeds) == 4
    assert {seed.precursor_mz_basis for _, seed in seeds} == {
        "scan_precursor",
        "scan_monoisotopic",
    }
    assert all(
        seed.precursor_metadata == scan.precursor_metadata for scan, seed in seeds
    )
    assert all(
        seed.supports_direct_nl == (seed.precursor_mz_basis == "scan_precursor")
        for _, seed in seeds
    )


def test_physical_backfill_receipt_covers_recovered_peak_outside_apex_window(
    tmp_path: Path,
) -> None:
    import json

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
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

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    profile = SignalProcessingProfile(window_points=15)
    old = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=old.acquisition_sources,
        acquisition_bindings=old.acquisition_bindings,
        scientific_input_schema_version=old.scientific_input_schema_version,
        processing_algorithm_version=old.processing_algorithm_version,
        processing_contract_version=old.processing_contract_version,
        scientific_inputs=json.loads(old.normalized_scientific_inputs_json)
        | {PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(profile, {})},
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=fixture.owners.sample_snapshot,
        sample_resolutions=fixture.samples,
    )
    fixture = replace(fixture, owners=owners)
    source_raw = _FakeRaw(())
    source_raw.rt = np.linspace(6.0, 10.0, 801)
    sigma = np.where(source_raw.rt < 7.4, 0.04, 0.2)
    source_raw.intensity = (
        100.0
        + 1000.0 * np.exp(-0.5 * ((source_raw.rt - 7.4) / sigma) ** 2)
        + np.random.default_rng(0).normal(0.0, 0.01, len(source_raw.rt))
    )
    raw, _vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    result = backfill_untargeted_cid_nl_sample_subject(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        group_center_rt=8.0,
        group_center_mz=500.0,
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01,
            neutral_loss_tolerance_ppm=20.0,
        ),
        peak_config=replace(_peak_config(tmp_path), nl_rt_anchor_half_window_min=0.5),
        raw=raw,
        signal_processing_profile=profile,
    )

    evaluation = result.capture_evidence[0].physical_evaluation
    assert evaluation.state == "available" and evaluation.facts is not None
    assert evaluation.question.search_rt_window == (7.5, 8.5)
    assert evaluation.facts.apex_rt_min < 7.5
    assert evaluation.facts.left_rt_min < 8.0 < evaluation.facts.right_rt_min
    receipt = result.assessment.content.evaluation_receipt
    assert (receipt.query_rt_min, receipt.query_rt_max) == (7.0, 9.0)
    assert len(result.assessment.candidate_peaks) == 1
    assert result.assessment.candidate_peaks[0].content.integration.rt_apex_min == (
        evaluation.facts.apex_rt_min
    )


@pytest.mark.parametrize(
    "query_gate", [(None, True), (12.0, True), (30.0, True), (None, False)]
)
@pytest.mark.parametrize("target_present", [True, False])
@pytest.mark.parametrize("target_center", [8.25, 8.45])
def test_physical_backfill_context_is_not_search_permission(
    tmp_path: Path,
    target_present: bool,
    target_center: float,
    query_gate: tuple[float | None, bool],
) -> None:
    import json

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
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

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
    profile = SignalProcessingProfile(window_points=15)
    old = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=old.acquisition_sources,
        acquisition_bindings=old.acquisition_bindings,
        scientific_input_schema_version=old.scientific_input_schema_version,
        processing_algorithm_version=old.processing_algorithm_version,
        processing_contract_version=old.processing_contract_version,
        scientific_inputs=json.loads(old.normalized_scientific_inputs_json)
        | {PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(profile, {})},
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=fixture.owners.sample_snapshot,
        sample_resolutions=fixture.samples,
    )
    fixture = replace(fixture, owners=owners)
    source_raw = _FakeRaw(())
    source_raw.rt = np.linspace(6.0, 10.0, 801)
    source_raw.intensity = 5000.0 * np.exp(-0.5 * ((source_raw.rt - 8.85) / 0.04) ** 2)
    if target_present:
        source_raw.intensity += 1000.0 * np.exp(
            -0.5 * ((source_raw.rt - target_center) / 0.06) ** 2
        )
    raw, vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    config = replace(_peak_config(tmp_path), nl_rt_anchor_half_window_min=0.5)
    target = UntargetedCidNlBackfillTarget(
        "bounded-group", fixture.method_subject, 500.0, 8.0
    )
    batch = acquire_untargeted_cid_nl_backfill_trace_batch(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        backfill_requests=(target, replace(target, group_hypothesis_id="same-query")),
        peak_config=config,
        raw=raw,
    )
    assert vendor.chromatogram_request_counts == [2]
    assert batch.requests[0].rt_min == 7.5 and batch.requests[0].rt_max == 8.5
    assert batch.context_requests[0].rt_min == 7.0
    assert batch.context_requests[0].rt_max == 9.0
    kwargs = dict(
        processing_owners=owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        group_center_rt=8.0,
        group_center_mz=500.0,
        group_hypothesis_id="bounded-group",
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01,
            neutral_loss_tolerance_ppm=20.0,
        ),
        peak_config=config,
        signal_processing_profile=profile,
    )
    batched = backfill_untargeted_cid_nl_sample_subject(
        **kwargs,
        raw=None,
        _preacquired_traces=batch,
    )
    assert vendor.chromatogram_request_counts == [2]
    direct = backfill_untargeted_cid_nl_sample_subject(
        **kwargs,
        raw=raw,
    )
    assert vendor.chromatogram_request_counts == [2, 2]
    first = batched.capture_evidence[0].physical_evaluation
    assert first == direct.capture_evidence[0].physical_evaluation
    tolerance, reliable = query_gate
    gated_kwargs = kwargs | {
        "query_rt_tolerance_sec": tolerance,
        "query_rt_reliable": reliable,
    }
    gated = backfill_untargeted_cid_nl_sample_subject(
        **gated_kwargs,
        raw=None,
        _preacquired_traces=batch,
    )
    gated_direct = backfill_untargeted_cid_nl_sample_subject(
        **gated_kwargs,
        raw=raw,
    )
    gate_result = gated.capture_evidence[0].physical_evaluation
    assert gate_result == gated_direct.capture_evidence[0].physical_evaluation
    if not reliable or tolerance == 12.0:
        assert gate_result.state == "unavailable"
        assert gate_result.facts is None
        assert not gated.assessment.candidate_peaks
        assert gate_result.reason == (
            "sample_localization_unreliable"
            if not reliable
            else "no_peak_in_sample_local_window"
            if target_present
            else "peak_not_found"
        )
    elif target_present:
        assert gate_result.state == "available"
        assert gate_result.facts is not None and first.facts is not None
        assert gate_result.facts.area_counts_seconds == first.facts.area_counts_seconds
        assert gate_result.facts.apex_rt_min == first.facts.apex_rt_min
    expected_window = (7.5, 8.5) if target_present and target_center == 8.45 else None
    assert first.question.search_rt_window == expected_window
    receipt = batched.assessment.content.evaluation_receipt
    expected_receipt = (7.0, 9.0) if expected_window is not None else (7.5, 8.5)
    assert (receipt.query_rt_min, receipt.query_rt_max) == expected_receipt
    if target_present:
        assert first.state == "available" and first.facts is not None
        assert first.facts.apex_rt_min == pytest.approx(target_center, abs=0.02)
        if target_center == 8.45:
            assert first.facts.right_rt_min > 8.5
            assert batched.capture_evidence[0].trace.rt_min == 7.0
        else:
            from xic_extractor.peak_detection.physical_peak_evaluation import (
                evaluate_physical_peak,
                prepare_physical_signal,
            )
            from xic_extractor.peak_detection.physical_peak_models import (
                PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
            )

            trace = batched.capture_evidence[0].trace
            assert trace.rt_min == 7.5 and trace.rt_max == 8.5
            original = evaluate_physical_peak(
                prepare_physical_signal(trace.rt, trace.intensity, profile=profile),
                selection_subject_id=fixture.method_subject.method_subject_id,
                query_rt_min=8.0,
                selection_policy_version=PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
            )
            assert original.facts is not None
            assert first.facts.area_counts_seconds == original.facts.area_counts_seconds
            assert first.facts.left_rt_min == original.facts.left_rt_min
            assert first.facts.right_rt_min == original.facts.right_rt_min
        # A measured MS1 peak alone still cannot assert method SUPPORT.
        assert batched.assessment.content.method_result != "SUPPORT"
    else:
        assert first.state == "unavailable" and first.facts is None
        assert not batched.assessment.candidate_peaks


@pytest.mark.parametrize("monoisotopic_recorded", [True, False])
def test_composite_backfill_resolves_one_event_per_scan_loss_across_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    monoisotopic_recorded: bool,
) -> None:
    import json
    from types import SimpleNamespace

    from xic_extractor.peak_detection.cid_nl_method_subject import (
        REQUEST_INPUT_KEY,
        CidNlComposition,
        mint_untargeted_cid_nl_method_subject,
    )
    from xic_extractor.processing_identity import (
        build_processing_request_identity,
        compose_processing_owner_references,
        issue_processing_attempt,
    )

    settings = _multi_tag_settings()
    subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=20.0,
        composition=CidNlComposition(
            tags=tuple(
                (profile.tag, profile.neutral_loss_da)
                for profile in settings.neutral_loss_profiles
            ),
            combine_mode=settings.tag_combine_mode,
        ),
    )
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
    old_request = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=old_request.acquisition_sources,
        acquisition_bindings=old_request.acquisition_bindings,
        scientific_input_schema_version=old_request.scientific_input_schema_version,
        processing_algorithm_version=old_request.processing_algorithm_version,
        processing_contract_version=old_request.processing_contract_version,
        scientific_inputs=json.loads(old_request.normalized_scientific_inputs_json)
        | {REQUEST_INPUT_KEY: subject.request_payload()},
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=fixture.owners.sample_snapshot,
        sample_resolutions=fixture.samples,
    )
    # Selected and recorded monoisotopic ions sit 15 ppm either side of the
    # subject; each passes the per-loss NL relation for both configured losses.
    source_raw = _FakeRaw((8.0,))
    scan = source_raw.scans[0]
    source_raw.scans = (
        replace(
            scan,
            precursor_mz=499.9925,
            masses=np.asarray((500.0 - 116.0474, 500.0 - 132.0423)),
            intensities=np.asarray((800.0, 700.0)),
            base_peak=800.0,
        ),
    )
    raw, vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    original_filter = vendor.GetFilterForScanNumber

    def scan_filter(number):
        result = original_filter(number)
        if number == scan.scan_number:
            reaction = result.Filter.Reactions[0]
            reaction.IsolationWidth = 1.6
            reaction.IsolationWidthOffset = 0.0
        return result

    monkeypatch.setattr(vendor, "GetFilterForScanNumber", scan_filter)
    labels = ("Charge State:", "Master Scan Number:")
    values: tuple[object, ...] = ("1", str(scan.scan_number - 1))
    if monoisotopic_recorded:
        labels = ("Monoisotopic M/Z:", *labels)
        values = ("500.0075", *values)
    vendor.trailer_labels = list(labels)
    monkeypatch.setattr(
        vendor,
        "GetTrailerExtraInformation",
        lambda _number: SimpleNamespace(Labels=labels, Values=values),
        raising=False,
    )
    owners = bind_physical_capture(owners, {})
    try:
        result = backfill_untargeted_cid_nl_sample_subject(
            processing_owners=owners,
            sample=fixture.sample("S1"),
            method_subject=subject,
            group_center_rt=8.0,
            group_center_mz=500.0,
            support_policy=DdaCidNlPeakSupportPolicy(
                precursor_tolerance_da=0.01, neutral_loss_tolerance_ppm=20.0
            ),
            peak_config=_peak_config(tmp_path),
            raw=raw,
            settings=settings,
            signal_processing_profile=TEST_PROFILE,
        )
    finally:
        raw.__exit__(None, None, None)

    assessment = result.assessment
    assert assessment.content.method_result == "SUPPORT"
    assert len(assessment.candidate_peaks) == 1
    assert sorted(
        (
            ref.observation.scan_number,
            ref.observation.configured_neutral_loss_da,
            ref.observation.product_mz,
        )
        for ref in assessment.evidence_refs
    ) == [
        (scan.scan_number, 116.0474, pytest.approx(500.0 - 116.0474)),
        (scan.scan_number, 132.0423, pytest.approx(500.0 - 132.0423)),
    ]


@pytest.mark.parametrize("preserve_configured_losses", [False, True])
def test_same_scan_absorbed_product_keeps_exact_canonical_seed(
    tmp_path: Path, preserve_configured_losses: bool
) -> None:
    import json

    from xic_extractor.discovery.strict_seed_receipts import (
        read_complete_discovery_scan_seeds,
    )

    source_raw = _FakeRaw((8.0,), scan_precursor_mz=255.15606689453125)
    canonical_product = 139.09368896484375
    absorbed_product = 139.09832763671875
    source_raw.scans = (
        replace(
            source_raw.scans[0],
            masses=np.asarray((canonical_product, absorbed_product)),
            intensities=np.asarray((800.0, 200.0)),
        ),
    )
    settings = _settings()
    (candidate,) = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    assert candidate.product_mz == canonical_product
    evidence = json.loads(candidate.tag_evidence_json)["DNA_dR"]
    assert (
        evidence["scan_receipts"][str(candidate.best_ms2_scan_id)]["product_mz"]
        == absorbed_product
    )

    seeds = read_complete_discovery_scan_seeds(
        source_raw,
        candidates=(candidate,),
        settings=settings,
        include_inferred=True,
        preserve_configured_losses=preserve_configured_losses,
    )
    expected_products = (
        {canonical_product, absorbed_product}
        if preserve_configured_losses
        else {canonical_product}
    )
    assert {seed.product_mz for _, seed in seeds} == expected_products
    assert all(seed.precursor_mz == seed.product_mz + 116.0474 for _, seed in seeds)


@pytest.mark.parametrize("preserve_configured_losses", [False, True])
def test_duplicate_exact_canonical_seed_is_rejected(
    tmp_path: Path, monkeypatch, preserve_configured_losses: bool
) -> None:
    from xic_extractor.discovery import strict_seed_receipts

    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    (candidate,) = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.RAW",
        settings=settings,
    )
    enumerate_seeds = strict_seed_receipts.discovery_seeds_from_scan

    def duplicated(scan, **kwargs):
        seeds = enumerate_seeds(scan, **kwargs)
        return seeds + seeds

    monkeypatch.setattr(strict_seed_receipts, "discovery_seeds_from_scan", duplicated)
    with pytest.raises(strict_seed_receipts.StrictSeedReceiptError, match="not unique"):
        strict_seed_receipts.read_complete_discovery_scan_seeds(
            source_raw,
            candidates=(candidate,),
            settings=settings,
            preserve_configured_losses=preserve_configured_losses,
        )


@pytest.mark.parametrize("side", [-1, 1])
def test_backfill_containing_peak_closes_assessment_outside_apex_window(
    tmp_path: Path,
    side: int,
) -> None:
    import json

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.peak_detection.capture import (
        capture_physical_backfill_observation,
    )
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlEvaluationReceipt,
        CidNlMethodAssessmentContractError,
        mint_cid_nl_method_assessment_ref,
    )
    from xic_extractor.peak_detection.search_scope import (
        PHYSICAL_CAPTURE_INPUT_KEY,
        physical_capture_input_payload,
    )
    from xic_extractor.peak_detection.traces import Trace
    from xic_extractor.processing_identity import (
        build_processing_request_identity,
        compose_processing_owner_references,
        issue_processing_attempt,
        resolve_request_acquisition_source,
    )

    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    profile = SignalProcessingProfile(window_points=15)
    old = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=old.acquisition_sources,
        acquisition_bindings=old.acquisition_bindings,
        scientific_input_schema_version=old.scientific_input_schema_version,
        processing_algorithm_version=old.processing_algorithm_version,
        processing_contract_version=old.processing_contract_version,
        scientific_inputs=json.loads(old.normalized_scientific_inputs_json)
        | {PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(profile, {})},
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=fixture.owners.sample_snapshot,
        sample_resolutions=fixture.samples,
    )
    fixture = replace(fixture, owners=owners)

    sample = fixture.sample("S1")
    source = resolve_request_acquisition_source(processing_owners=owners, sample=sample)
    rt = np.linspace(7.0, 9.0, 401)
    offset = side * (rt - 8.0)
    intensity = 1000.0 * np.exp(
        -0.5 * ((offset - 0.6) / np.where(offset < 0.6, 0.30, 0.06)) ** 2
    )
    peak = capture_physical_backfill_observation(
        processing_owners=owners,
        sample=sample,
        method_subject=fixture.method_subject,
        trace=Trace(
            sample_name="S1",
            mz=500.0,
            rt=rt,
            intensity=intensity,
            rt_min=7.0,
            rt_max=9.0,
            ppm_tol=fixture.method_subject.precursor_tolerance_ppm,
            acquisition_source_id=source.acquisition_source_id,
        ),
        query_rt_min=8.0,
        physical_mz=500.0,
        profile=profile,
        resolver_mode="local_minimum",
        search_rt_window=(7.5, 8.5),
    )
    assert peak is not None
    evaluation = peak.physical_evaluation
    assert evaluation is not None and evaluation.facts is not None
    assert not 7.5 <= evaluation.facts.apex_rt_min <= 8.5
    assert evaluation.facts.left_rt_min < 8.0 < evaluation.facts.right_rt_min
    receipt = CidNlEvaluationReceipt(
        query_rt_min=7.5,
        query_rt_max=8.5,
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01,
            neutral_loss_tolerance_ppm=20.0,
        ),
        acquisition_supported=True,
        search_completed=True,
        scanned_event_count=0,
        reason_code=None,
    )
    kwargs = dict(
        processing_owners=owners,
        sample=sample,
        method_subject=fixture.method_subject,
        detector_invocation_id="untargeted-cid-nl-backfill",
        evaluation_receipt=receipt,
    )
    assessment = mint_cid_nl_method_assessment_ref(**kwargs, candidate_peaks=(peak,))
    assert assessment.content.method_result == "HOLD"
    assert assessment.content.evaluation_receipt == receipt
    assert assessment.candidate_peaks[0].content.integration.area_ms1_morphology == (
        evaluation.facts.area_counts_seconds
    )
    # Ordinary observations cannot use the physical Backfill owner's selection.
    with pytest.raises(CidNlMethodAssessmentContractError, match="candidate peak apex"):
        mint_cid_nl_method_assessment_ref(
            **kwargs,
            candidate_peaks=(replace(peak, physical_evaluation=None),),
        )
    # The physical question itself must still belong to the queried interval.
    kwargs["evaluation_receipt"] = replace(receipt, query_rt_min=8.1, query_rt_max=8.2)
    with pytest.raises(CidNlMethodAssessmentContractError, match="outside"):
        mint_cid_nl_method_assessment_ref(**kwargs, candidate_peaks=(peak,))
