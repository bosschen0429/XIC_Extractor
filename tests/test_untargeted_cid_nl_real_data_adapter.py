from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    _current_candidates,
    _FakeRaw,
    _open_bound_raw,
    _peak_config,
    _settings,
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


@pytest.mark.parametrize("fresh_available", [True, False])
@pytest.mark.parametrize("shifted_mass", [True, False])
def test_changed_physical_query_evaluates_once_and_preserves_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fresh_available: bool,
    shifted_mass: bool,
) -> None:
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.discovery.grouping import group_discovery_seeds
    from xic_extractor.discovery.ms1_backfill import (
        _backfill_ms1_candidates_with_acquisition,
    )
    from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
    from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
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
    discovery = _backfill_ms1_candidates_with_acquisition(
        raw,
        group_discovery_seeds(seeds, settings=settings),
        acquisition_source=source,
        run_spec_id="processing-run:" + "0" * 64,
        settings=settings,
        peak_config=peak_config,
        signal_processing_profile=profile,
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
    assert len(result.acquisition.traces) == (2 if shifted_mass else 1)
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
    from xic_extractor.discovery.ms1_backfill import (
        _backfill_ms1_candidates_with_acquisition,
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
    discovery = _backfill_ms1_candidates_with_acquisition(
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
        peak_config=peak_config,
        signal_processing_profile=profile,
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
    assert assessment.content.method_result == "SUPPORT"
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
        peak_config=peak_config,
    )
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
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
        peak_config=peak_config,
    )
    raw, _vendor_raw = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
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
        peak_config=peak_config,
    )
    (candidate,) = candidates
    assert candidate.precursor_mz_basis == "mixed"
    assert set(candidate.seed_scan_ids) == {
        *strict_scan_ids,
        inferred_scan.scan_number,
    }
    raw, _vendor_raw = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)

    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
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
        peak_config=peak_config,
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

    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
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
def test_inferred_precursor_support_requires_a_physical_ms1_peak(
    tmp_path: Path,
    ms1_present: bool,
) -> None:
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
        peak_config=peak_config,
    )
    assert candidates
    assert all(c.precursor_mz_basis == "product_plus_neutral_loss" for c in candidates)
    raw, _vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )
    assessment = result.assessment_input.assessment
    assert result.strict_scan_count == 0
    if ms1_present:
        assert assessment.content.method_result == "SUPPORT"
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


@pytest.mark.parametrize("moved_field", ["precursor_mz", "product_mz"])
def test_inferred_precursor_off_its_scan_receipt_is_rejected(
    tmp_path: Path, moved_field: str
) -> None:
    # An inferred precursor stays product + loss; its apex MS1 reading is only
    # reported. A persisted precursor or product off the scan's receipt breaks
    # the strict seed reconstruction.
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
            peak_config=peak_config,
        )
    )
    assert all(c.precursor_mz_basis == "product_plus_neutral_loss" for c in candidates)
    raw, _vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)

    def adapt():
        return adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=candidates,
            settings=settings,
            peak_config=peak_config,
            raw=raw,
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
        peak_config=_peak_config(tmp_path),
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
        peak_config=_peak_config(tmp_path),
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
        peak_config=_peak_config(tmp_path),
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
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.98, 8.02))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    first = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        peak_config=peak_config,
    )[0]
    second = replace(first, candidate_id=f"{first.candidate_id}:second")
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
    )

    assert batch is not None
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
    assert vendor_raw.chromatogram_request_counts == [2]


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
        peak_config=peak_config,
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

    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
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
        peak_config=peak_config,
    )[0]
    orphan = _without_ms1_peak(candidate)
    raw, vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=(orphan,),
        settings=settings,
        peak_config=peak_config,
        raw=raw,
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

    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=(),
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        raw=raw,
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


def test_adapter_uses_orphan_support_without_giving_it_feature_standing(
    tmp_path: Path,
) -> None:
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
        peak_config=peak_config,
    )
    support = _without_ms1_peak(support_source)
    raw, _vendor_raw = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)

    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=(feature,),
        support_candidates=(support,),
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    assert result.source_candidate_ids == (feature.candidate_id,)
    assert {capture.candidate_id for capture in result.captures} == {
        feature.candidate_id
    }
    assert len(result.assessment_input.assessment.candidate_peaks) == 1
    assert result.strict_scan_count == 2
    assert len(result.assessment_input.discovery_projections) == 2


def test_adapter_does_not_read_raw_for_orphan_only_support(tmp_path: Path) -> None:
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
            peak_config=_peak_config(tmp_path),
        )[0]
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
        peak_config=peak_config,
    )[0]
    inferred = replace(
        direct,
        candidate_id=f"{direct.candidate_id}:inferred",
        precursor_mz_basis="product_plus_neutral_loss",
    )
    candidates = (direct, inferred) if include_direct_candidate else (inferred,)

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
        )


def test_adapter_supports_adjacent_physical_peaks_with_interior_scans(
    tmp_path: Path,
) -> None:
    import json

    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.discovery.grouping import group_discovery_seeds
    from xic_extractor.discovery.ms1_backfill import (
        _backfill_ms1_candidates_with_acquisition,
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
    discovery = _backfill_ms1_candidates_with_acquisition(
        raw,
        group_discovery_seeds(seeds, settings=settings),
        acquisition_source=fixture.owners.request.acquisition_sources[0],
        run_spec_id="processing-run:" + "0" * 64,
        settings=settings,
        peak_config=peak_config,
        signal_processing_profile=profile,
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
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "study_sample"},
        thermo_raw_sources=True,
    )
    source_raw = _FakeRaw((7.8, 8.2))
    source_raw.intensity = sum(
        1000.0 * np.exp(-0.5 * ((source_raw.rt - center) / 0.035) ** 2)
        for center in (7.8, 8.35)
    )
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        peak_config=peak_config,
    )
    assert len(candidates) == 2
    raw, _vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    result = adapt_current_discovery_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "SUPPORT"
    assert len(assessment.candidate_peaks) == 2
    assert len(assessment.individual_support_refs) == 1
    assert result.strict_scan_count == sum(len(c.seed_scan_ids) for c in candidates)
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
        peak_config=peak_config,
    )[0]
    assert candidate.ms1_apex_rt is not None
    drifted = replace(candidate, ms1_apex_rt=candidate.ms1_apex_rt + 0.1)
    raw, _vendor_raw = _open_bound_raw(
        fixture,
        "S1",
        source_raw,
        tmp_path=tmp_path,
    )

    with pytest.raises(
        CidNlAcquisitionError,
        match="rt_apex_min mismatches current receipt",
    ):
        adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=(drifted,),
            settings=settings,
            peak_config=peak_config,
            raw=raw,
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
    assert result.acquisition.logical_xic_request_count == 1
    assert vendor_raw.chromatogram_request_counts == [1]


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
        peak_config=_peak_config(tmp_path),
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
