"""Sample adapter evidence, search receipts and acquisition reuse."""

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
from tests.untargeted_cid_nl_real_data_adapter_fixtures import (
    _without_ms1_peak,
    build_sample_adapter_fixture,
    build_sample_adapter_scenario,
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
from xic_extractor.untargeted_execution.partition.cid_nl_raw_source import (
    CidNlAcquisitionError,
)
from xic_extractor.untargeted_execution.partition.cid_nl_sample_acquisition import (
    acquire_untargeted_cid_nl_sample_trace_batch,
    adapt_current_discovery_sample_subject,
)


def test_adapter_preserves_complete_multiscan_evidence_without_target_identity(
    tmp_path: Path,
) -> None:
    scenario = build_sample_adapter_scenario(tmp_path)
    source_raw = _FakeRaw((7.98, 8.02))

    candidates = scenario.discover(source_raw)
    raw, vendor_raw = scenario.open_raw(source_raw)

    scenario.bind_physical_capture()
    result = scenario.adapt(candidates, raw=raw)

    assessment = result.assessment_input.assessment
    assert assessment.content.method_subject.subject_kind == "untargeted_precursor"
    assert assessment.content.method_subject.target is None
    assert assessment.content.method_result == "SUPPORT"
    assert tuple(
        sorted(
            evidence.observation.scan_number for evidence in assessment.evidence_refs
        )
    ) == tuple(sorted(candidates[0].seed_scan_ids))
    assert len(result.assessment_input.assessment.evidence_refs) == 2
    assert result.acquisition is not None
    assert result.acquisition.logical_xic_request_count == 1
    assert result.acquisition.xic_batch_count == 1
    assert result.acquisition.adapter_chromatogram_call_count == 1
    assert vendor_raw.chromatogram_request_counts == [1]


def test_adapter_retains_acquisition_evidence_before_later_relation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xic_extractor.untargeted_execution.partition import cid_nl_sample_acquisition

    scenario = build_sample_adapter_scenario(tmp_path)
    source_raw = _FakeRaw((7.98, 8.02))

    candidates = scenario.discover(source_raw)
    raw, _vendor_raw = scenario.open_raw(source_raw)
    scenario.bind_physical_capture()
    resolve_relations = cid_nl_sample_acquisition.resolve_cid_nl_method_relations
    successful_relations = []

    def fail_later_relation(**kwargs):
        if successful_relations:
            raise RuntimeError("later relation resolution failed")
        resolved = resolve_relations(**kwargs)
        successful_relations.append(resolved)
        return resolved

    monkeypatch.setattr(
        cid_nl_sample_acquisition,
        "resolve_cid_nl_method_relations",
        fail_later_relation,
    )
    with pytest.raises(RuntimeError, match="later relation resolution failed"):
        scenario.adapt(candidates, raw=raw)

    (((support,), set_relations),) = successful_relations
    assert set_relations == ()
    assert support.content.evidence.scan_number in candidates[0].seed_scan_ids


def test_adapter_preserves_strict_scans_within_mixed_candidate(
    tmp_path: Path,
) -> None:
    scenario = build_sample_adapter_scenario(tmp_path)
    source_raw = _FakeRaw((7.98, 8.0, 8.02))
    strict_scan_ids = tuple(scan.scan_number for scan in source_raw.scans[:2])
    inferred_scan = replace(
        source_raw.scans[2],
        precursor_mz=source_raw.scans[2].precursor_mz + 0.5,
    )
    source_raw.scans = (*source_raw.scans[:2], inferred_scan)

    candidates = scenario.discover(source_raw)
    (candidate,) = candidates
    assert candidate.precursor_mz_basis == "mixed"
    assert set(candidate.seed_scan_ids) == {
        *strict_scan_ids,
        inferred_scan.scan_number,
    }
    raw, _vendor_raw = scenario.open_raw(source_raw)

    scenario.bind_physical_capture()
    result = scenario.adapt(candidates, raw=raw)

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


def test_adapter_keeps_direct_support_separate_from_same_peak_inferred_evidence(
    tmp_path: Path,
) -> None:
    scenario = build_sample_adapter_scenario(tmp_path)
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
    scenario.settings = replace(
        _settings(), neutral_loss_profile=None, seed_rt_gap_min=0.01
    )

    candidates = scenario.discover(source_raw)
    # One physical peak retains every event without promoting inference to strict NL.
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
        > scenario.settings.product_mz_tolerance_ppm
    )
    raw, _vendor_raw = scenario.open_raw(source_raw)

    scenario.bind_physical_capture()
    result = scenario.adapt(candidates, raw=raw)

    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "SUPPORT"
    assert result.strict_scan_count == 2
    assert {
        evidence.observation.scan_number
        for evidence in assessment.evidence_refs
        if evidence.observation.precursor_mz_basis == "scan_precursor"
    } == {first.scan_number, representative.scan_number}
    assert {
        evidence.observation.scan_number
        for evidence in assessment.evidence_refs
        if evidence.observation.precursor_mz_basis == "product_plus_neutral_loss"
    } == {inferred.scan_number}


@pytest.mark.parametrize("ms1_present", [True, False])
def test_inferred_precursor_retains_physical_peak_without_strict_nl_support(
    tmp_path: Path,
    ms1_present: bool,
) -> None:
    scenario = build_sample_adapter_scenario(tmp_path)
    source_raw = _FakeRaw((7.98, 8.0, 8.02))
    source_raw.scans = tuple(
        replace(scan, precursor_mz=scan.precursor_mz + 0.5) for scan in source_raw.scans
    )
    if not ms1_present:
        source_raw.intensity = np.zeros_like(source_raw.intensity)

    candidates = scenario.discover(source_raw)
    assert candidates
    assert all(c.precursor_mz_basis == "product_plus_neutral_loss" for c in candidates)
    raw, _vendor = scenario.open_raw(source_raw)
    scenario.bind_physical_capture()
    result = scenario.adapt(candidates, raw=raw)
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

    scenario = build_sample_adapter_scenario(tmp_path)
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
    scenario.settings = (
        replace(_multi_tag_settings(), tag_combine_mode="intersection")
        if composite
        else _settings()
    )
    if composite:
        subject = mint_untargeted_cid_nl_method_subject(
            precursor_mz=500.0,
            precursor_tolerance_ppm=scenario.settings.precursor_mz_tolerance_ppm,
            composition=CidNlComposition(
                tags=tuple(
                    (profile.tag, profile.neutral_loss_da)
                    for profile in scenario.settings.neutral_loss_profiles
                ),
                combine_mode=scenario.settings.tag_combine_mode,
            ),
        )
        old_request = scenario.fixture.owners.request
        request = build_processing_request_identity(
            acquisition_sources=old_request.acquisition_sources,
            acquisition_bindings=old_request.acquisition_bindings,
            scientific_input_schema_version=old_request.scientific_input_schema_version,
            processing_algorithm_version=old_request.processing_algorithm_version,
            processing_contract_version=old_request.processing_contract_version,
            scientific_inputs=json.loads(old_request.normalized_scientific_inputs_json)
            | {REQUEST_INPUT_KEY: subject.request_payload()},
        )
        scenario.fixture = replace(
            scenario.fixture,
            method_subject=subject,
            owners=compose_processing_owner_references(
                request=request,
                attempt=issue_processing_attempt(),
                sample_snapshot=scenario.fixture.owners.sample_snapshot,
                sample_resolutions=scenario.fixture.samples,
            ),
        )

    candidates = scenario.discover(source_raw)
    raw, vendor = scenario.open_raw(source_raw)
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
    scenario.bind_physical_capture()
    try:
        result = scenario.adapt(
            candidates[::-1] if reverse_products else candidates, raw=raw
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


@pytest.mark.parametrize("cached", [False, True])
def test_inferred_precursor_can_gain_support_from_measured_parent_ms1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached: bool
) -> None:
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata

    scenario = build_sample_adapter_scenario(tmp_path)
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

    candidates = scenario.discover(source_raw)
    raw, _vendor = scenario.open_raw(source_raw)
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
    scenario.bind_physical_capture()
    result = scenario.adapt(candidates, raw=raw)
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

    scenario = build_sample_adapter_scenario(tmp_path)
    source_raw = _FakeRaw((7.98, 8.0, 8.02))
    source_raw.scans = tuple(
        replace(scan, precursor_mz=scan.precursor_mz + 0.5) for scan in source_raw.scans
    )

    candidates = tuple(
        replace(
            candidate,
            **{moved_field: getattr(candidate, moved_field) * (1 + 7e-6)},
        )
        for candidate in scenario.discover(source_raw)
    )
    assert all(c.precursor_mz_basis == "product_plus_neutral_loss" for c in candidates)
    raw, _vendor = scenario.open_raw(source_raw)

    scenario.bind_physical_capture()

    def adapt():
        return scenario.adapt(candidates, raw=raw)

    with pytest.raises(CidNlAcquisitionError, match="seed is not unique"):
        adapt()


def test_sample_trace_batch_is_acquired_once_and_reused_by_subject_adapters(
    tmp_path: Path,
) -> None:
    scenario = build_sample_adapter_scenario(tmp_path)
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
    scenario.settings = replace(_settings(), rt_min=7.0, rt_max=10.0)

    first, second = scenario.discover(source_raw)
    candidates = tuple(sorted((first, second), key=lambda item: item.candidate_id))
    raw, vendor_raw = scenario.open_raw(source_raw)

    batch = acquire_untargeted_cid_nl_sample_trace_batch(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("S1"),
        candidates=candidates,
        settings=scenario.settings,
        raw=raw,
        signal_processing_profile=TEST_PROFILE,
    )

    assert batch is not None
    scenario.bind_physical_capture()
    results = tuple(
        scenario.adapt((candidate,), raw=raw, _preacquired_traces=batch)
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

    fixture = build_sample_adapter_fixture(tmp_path)
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


def test_adapter_receipt_covers_candidate_search_beyond_seed_window(
    tmp_path: Path,
) -> None:
    scenario = build_sample_adapter_scenario(tmp_path)
    source_raw = _FakeRaw((7.99,))
    source_raw.intensity = 1000.0 * np.exp(-0.5 * ((source_raw.rt - 8.10) / 0.06) ** 2)
    scenario.settings = replace(_settings(), neutral_loss_profile=None, rt_max=8.0)

    candidates = scenario.discover(source_raw)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.best_seed_rt <= scenario.settings.rt_max
    assert candidate.ms1_apex_rt is not None
    assert candidate.ms1_apex_rt > scenario.settings.rt_max
    assert candidate.ms1_apex_rt <= candidate.ms1_search_rt_max
    raw, _vendor_raw = scenario.open_raw(source_raw)

    scenario.bind_physical_capture()
    result = scenario.adapt(candidates, raw=raw)

    receipt = result.assessment_input.assessment.content.evaluation_receipt
    assert receipt.query_rt_min == min(
        scenario.settings.rt_min,
        candidate.ms1_search_rt_min,
    )
    assert receipt.query_rt_max == max(
        scenario.settings.rt_max,
        candidate.ms1_search_rt_max,
    )


def test_adapter_closes_qualifying_ms2_without_ms1_peak_as_reject(
    tmp_path: Path,
) -> None:
    scenario = build_sample_adapter_scenario(tmp_path)
    source_raw = _FakeRaw((8.0,))

    candidate = scenario.discover(source_raw)[0]
    orphan = _without_ms1_peak(candidate)
    raw, vendor_raw = scenario.open_raw(source_raw)

    scenario.bind_physical_capture()
    result = scenario.adapt((orphan,), raw=raw)

    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "REJECT"
    assert assessment.candidate_peaks == ()
    assert len(assessment.evidence_refs) == 1
    assert len(result.assessment_input.assessment.evidence_refs) == 1
    assert result.acquisition is None
    assert vendor_raw.chromatogram_request_counts == []


def test_adapter_closes_empty_subject_universe_as_not_evaluated_without_xic(
    tmp_path: Path,
) -> None:
    fixture = build_sample_adapter_fixture(tmp_path)
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
    assert result.assessment_input.assessment.evidence_refs == ()
    assert result.acquisition is None
    assert vendor_raw.chromatogram_request_counts == []


def test_auto_range_empty_subject_has_no_fabricated_query_and_needs_no_raw(
    tmp_path: Path,
) -> None:
    fixture = build_sample_adapter_fixture(tmp_path)
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
    assert receipt.search_completed is False
    assert result.acquisition is None and not result.captures


def test_auto_range_evaluated_receipt_uses_current_raw_bounds(tmp_path: Path) -> None:
    from types import SimpleNamespace

    scenario = build_sample_adapter_scenario(tmp_path)
    source_raw = _FakeRaw((8.0,))

    candidates = scenario.discover(source_raw)
    raw, vendor = scenario.open_raw(source_raw)
    vendor.RunHeaderEx = SimpleNamespace(StartTime=7.4, EndTime=75.0)
    scenario.bind_physical_capture()
    result = scenario.adapt(
        candidates,
        settings=replace(scenario.settings, rt_min=None, rt_max=None),
        raw=raw,
    )
    receipt = result.assessment_input.assessment.content.evaluation_receipt
    assert receipt.search_completed is True
    assert (receipt.query_rt_min, receipt.query_rt_max) == (7.4, 75.0)


def test_adapter_uses_orphan_support_without_giving_it_feature_standing(
    tmp_path: Path,
) -> None:
    scenario = build_sample_adapter_scenario(tmp_path)
    source_raw = _FakeRaw((7.8, 8.2))

    feature, support_source = scenario.discover(source_raw)
    support = _without_ms1_peak(support_source)
    raw, _vendor_raw = scenario.open_raw(source_raw)

    scenario.bind_physical_capture()
    result = scenario.adapt((feature,), support_candidates=(support,), raw=raw)

    assert result.source_candidate_ids == (feature.candidate_id,)
    assert {capture.candidate_id for capture in result.captures} == {
        feature.candidate_id
    }
    assert len(result.assessment_input.assessment.candidate_peaks) == 1
    assert result.strict_scan_count == 2
    assert len(result.assessment_input.assessment.evidence_refs) == 2


def test_adapter_does_not_read_raw_for_orphan_only_support(tmp_path: Path) -> None:
    physical_records = {}
    fixture = build_sample_adapter_fixture(tmp_path)
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
    assert result.assessment_input.assessment.evidence_refs == ()
    assert result.captures == ()


@pytest.mark.parametrize("include_direct_candidate", [False, True])
def test_adapter_requires_raw_to_preserve_inferred_physical_observations(
    tmp_path: Path,
    include_direct_candidate: bool,
) -> None:
    scenario = build_sample_adapter_scenario(tmp_path)

    direct = scenario.discover(_FakeRaw((8.0,)))[0]
    inferred = replace(
        direct,
        candidate_id=f"{direct.candidate_id}:inferred",
        precursor_mz_basis="product_plus_neutral_loss",
    )
    candidates = (direct, inferred) if include_direct_candidate else (inferred,)

    scenario.bind_physical_capture()
    with pytest.raises(
        CidNlAcquisitionError,
        match="requires an owner-opened RAW adapter",
    ):
        adapt_current_discovery_sample_subject(
            processing_owners=scenario.fixture.owners,
            sample=scenario.fixture.sample("S1"),
            method_subject=scenario.fixture.method_subject,
            candidates=candidates,
            settings=scenario.settings,
            peak_config=scenario.peak_config,
            raw=None,
            signal_processing_profile=TEST_PROFILE,
            physical_records=tuple(scenario.physical_records.values()),
        )


def test_adapter_preserves_supported_and_observation_only_physical_peaks(
    tmp_path: Path,
) -> None:
    scenario = build_sample_adapter_scenario(tmp_path)
    source_raw = _FakeRaw((7.8, 8.35))
    source_raw.scans = (
        source_raw.scans[0],
        replace(source_raw.scans[1], precursor_mz=500.5),
    )

    candidates = scenario.discover(source_raw)
    assert len(candidates) == 2
    raw, _vendor_raw = scenario.open_raw(source_raw)

    scenario.bind_physical_capture()
    result = scenario.adapt(candidates, raw=raw)

    assessment = result.assessment_input.assessment
    assert assessment.content.method_result == "SUPPORT"
    assert len(assessment.candidate_peaks) == 2
    assert len(assessment.individual_support_refs) == 1
    # Only the recorded precursor supplies NL support.
    assert result.strict_scan_count == 1
    hypotheses = compose_peak_hypotheses_from_assessment_universe(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("S1"),
        assessment=assessment,
    )
    assert {item.hypothesis_method for item in hypotheses} == {
        "dda_cid_nl",
        "ms1_physical_observation",
    }
    rebound = bind_retained_peak_hypotheses_to_assessment_universe(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("S1"),
        assessment=assessment,
        retained_hypotheses=hypotheses,
    )
    assert all(
        rebound_by_id is source_by_id
        for rebound_by_id, source_by_id in zip(rebound, hypotheses, strict=True)
    )
    grouping_scope, candidates, _drift = bootstrap_cid_nl_initial_population(
        processing_owners=scenario.fixture.owners,
        sample_assessments=((scenario.fixture.sample("S1"), assessment),),
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
    scenario = build_sample_adapter_scenario(tmp_path)
    source_raw = _FakeRaw((8.0,))

    candidate = scenario.discover(source_raw)[0]
    assert candidate.ms1_apex_rt is not None
    drifted = replace(candidate, ms1_apex_rt=candidate.ms1_apex_rt + 0.1)
    raw, _vendor_raw = scenario.open_raw(source_raw)

    scenario.bind_physical_capture()
    with pytest.raises(
        CidNlAcquisitionError,
        match="apex_rt_min mismatches current receipt",
    ):
        scenario.adapt((drifted,), raw=raw)
