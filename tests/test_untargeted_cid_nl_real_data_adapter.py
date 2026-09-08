from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.test_targeted_real_data_adapter import (
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
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
)
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackContractError,
    ModeTrackTrajectoryPolicy,
    mint_mode_track_support_anchor,
)
from xic_extractor.alignment.mode_track.producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.extraction.untargeted_cid_nl_real_data_adapter import (
    UntargetedCidNlRealDataAdapterError,
    acquire_untargeted_cid_nl_sample_trace_batch,
    adapt_current_discovery_sample_subject,
    recover_untargeted_cid_nl_sample_subject,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
)


@pytest.mark.parametrize("fresh_available", [True, False])
def test_changed_physical_query_evaluates_once_and_preserves_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_available: bool
) -> None:
    import json
    from unittest.mock import Mock

    from xic_extractor.discovery.grouping import group_discovery_seeds
    from xic_extractor.discovery.ms1_backfill import (
        _backfill_ms1_candidates_with_acquisition,
    )
    from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
    from xic_extractor.extraction import untargeted_cid_nl_real_data_adapter as adapter
    from xic_extractor.peak_detection import capture as capture_owner
    from xic_extractor.peak_detection.evidence_facts import (
        evaluate_physical_peak,
        prepare_physical_signal,
    )
    from xic_extractor.peak_detection.observations import (
        physical_peak_observation_integration,
    )
    from xic_extractor.peak_detection.search_scope import (
        PHYSICAL_CAPTURE_INPUT_KEY,
        physical_capture_input_payload,
    )
    from xic_extractor.peak_detection.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.processing_identity import (
        build_processing_request_identity,
        compose_processing_owner_references,
        issue_processing_attempt,
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
        )
        for rt in (center - 0.25, center + 0.05)
    )
    source_raw.intensity = 250.0 + 1000.0 * np.exp(
        -0.5 * ((source_raw.rt - center) / 0.15) ** 2
    )
    settings = replace(
        _settings(),
        neutral_loss_profile=None,
        seed_rt_gap_min=0.01,
        rt_min=12.8,
        rt_max=13.4,
    )
    peak_config = _peak_config(tmp_path)
    profile = SignalProcessingProfile(window_points=15)
    raw, _vendor = _open_bound_raw(fixture, "S1", source_raw, tmp_path=tmp_path)
    source = fixture.owners.request.acquisition_sources[0]
    seeds = collect_strict_nl_seeds(
        raw, raw_file=fixture.source_paths["S1"], settings=settings
    )
    discovery = _backfill_ms1_candidates_with_acquisition(
        raw,
        group_discovery_seeds(seeds, settings=settings),
        acquisition_source=source,
        run_spec_id="processing-run.v1:" + "0" * 64,
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
        physical_records=discovery.physical_evaluations,
    )
    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == (
        "SUPPORT" if fresh_available else "REJECT"
    )
    assert len(assessment.candidate_peaks) == (1 if fresh_available else 0)
    assert len(assessment.individual_support_refs) == (2 if fresh_available else 0)
    assert result.acquisition is not None
    (acquired,) = result.acquisition.traces
    states = []
    changed_questions = 0
    captures = {capture.candidate_id: capture for capture in result.captures}
    assert len(captures) == 2
    for record in discovery.physical_evaluations:
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
        assert binding.acquisition_source_id == source.acquisition_source_id
        assert (
            binding.sample_identity_id
            == fixture.sample("S1").identity.sample_identity_id
        )
        states.append(expected.state)
        if expected.state == "unavailable":
            assert capture.selected_hypothesis is None
            continue
        observed = assessment.candidate_peaks[0]
        integration = physical_peak_observation_integration(expected)
        assert capture.selected_hypothesis.integration == integration
        assert (
            observed.content.integration.area_baseline_corrected
            == integration.area_baseline_corrected
        )
        assert observed.content.integration.rt_apex_min == integration.rt_apex_min
    assert states == (["available"] * 2 if fresh_available else ["unavailable"] * 2)
    assert changed_questions == 2
    assert evaluate.call_count == 2
    assert prepare.call_count == 2
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
                acquisition_source_id="xic.acquisition-source.v1:" + "0" * 64,
            ),
        )


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
        assert assessment.content.method_result != "SUPPORT"
        assert not assessment.candidate_peaks
        assert not assessment.individual_support_refs


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


def test_adapter_closes_empty_subject_universe_as_hold_without_xic(
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
    assert assessment.content.method_result == "HOLD"
    assert assessment.content.reason_codes == ("insufficient_method_evidence",)
    assert result.assessment_input.discovery_projections == ()
    assert result.acquisition is None
    assert vendor_raw.chromatogram_request_counts == []


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
        UntargetedCidNlRealDataAdapterError,
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

    from xic_extractor.discovery.grouping import group_discovery_seeds
    from xic_extractor.discovery.ms1_backfill import (
        _backfill_ms1_candidates_with_acquisition,
    )
    from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
    from xic_extractor.peak_detection.search_scope import (
        PHYSICAL_CAPTURE_INPUT_KEY,
        physical_capture_input_payload,
    )
    from xic_extractor.peak_detection.signal_processing_profile import (
        SignalProcessingProfile,
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
    source_raw.rt = np.linspace(7.4, 8.7, 40)
    source_raw.scans = tuple(
        replace(scan, scan_number=int(np.argmin(np.abs(source_raw.rt - scan.rt))) + 1)
        for scan in source_raw.scans
    )
    source_raw.intensity = sum(
        height * np.exp(-0.5 * ((source_raw.rt - center) / 0.06) ** 2)
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
        run_spec_id="processing-run.v1:" + "0" * 64,
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
    family_context, model_set, batches, _source = (
        bootstrap_mode_tracks_from_cid_nl_support(
            processing_owners=fixture.owners,
            sample_assessments=((fixture.sample("S1"), assessment),),
            alignment_config=AlignmentConfig(),
            trajectory_policy=ModeTrackTrajectoryPolicy(
                trajectory_policy_version="anchor-trajectory-v1",
                band_policy_version="fixed-band-v1",
                prediction_half_width_min=0.1,
                max_interpolation_gap=0,
                max_extrapolation_gap=0,
            ),
        )
    )
    assert len(family_context.mode_tracks) == 2
    assert sum(len(track.anchors) for track in model_set.tracks) == 1
    assert {item.candidate_observation_class for item in batches[0].assignments} == {
        "direct_support",
        "non_training",
    }
    assert {item.state for item in batches[0].assignments} == {"uniquely_assigned"}
    observation_only = next(
        item
        for item in hypotheses
        if item.hypothesis_method == "ms1_physical_observation"
    )
    observation_track = next(
        track
        for track in family_context.mode_tracks
        if observation_only.hypothesis_id in track.owner_ids
    )
    with pytest.raises(ModeTrackContractError):
        mint_mode_track_support_anchor(
            processing_owners=fixture.owners,
            track=observation_track,
            sample=fixture.sample("S1"),
            direct_support_assessment=assessment,
            peak_hypothesis=observation_only,
        )


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
        UntargetedCidNlRealDataAdapterError,
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


def test_recovery_revisits_one_hold_cell_without_minting_support(
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

    result = recover_untargeted_cid_nl_sample_subject(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        method_subject=fixture.method_subject,
        group_center_rt=8.0,
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01,
            neutral_loss_tolerance_ppm=20.0,
        ),
        peak_config=_peak_config(tmp_path),
        raw=raw,
    )

    assert result.assessment.content.method_result == "HOLD"
    assert len(result.assessment.candidate_peaks) == 1
    assert result.assessment.evidence_refs == ()
    assert result.recovery_input is not None
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
