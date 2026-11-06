"""Backfill localization, shared acquisition and bounded physical search."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _FakeRaw,
    _multi_tag_settings,
    _open_bound_raw,
    _peak_config,
    bind_physical_capture,
)
from tests.untargeted_cid_nl_real_data_adapter_fixtures import (
    build_sample_adapter_fixture,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.untargeted_execution.partition.cid_nl_backfill_acquisition import (
    UntargetedCidNlBackfillTarget,
    acquire_untargeted_cid_nl_backfill_trace_batch,
    backfill_untargeted_cid_nl_sample_subject,
)


@pytest.mark.parametrize(
    "sample,query_rt,expected_apex,width,tolerance",
    [
        ("S1", 7.8, 7.8, 0.035, None),
        ("S2", 8.5, 8.5, 0.035, None),
        ("S1", 7.55, 7.8, 0.15, 12.0),
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

    fixture = build_sample_adapter_fixture(tmp_path, sample_stem=sample)
    profile = SignalProcessingProfile()
    original = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=original.acquisition_sources,
        acquisition_bindings=original.acquisition_bindings,
        scientific_input_schema_version=original.scientific_input_schema_version,
        processing_algorithm_version=original.processing_algorithm_version,
        processing_contract_version=original.processing_contract_version,
        scientific_inputs=json.loads(original.normalized_scientific_inputs_json)
        | {PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(profile)},
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(),
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
        receipt = assessment.content.evaluation_receipt
        assert receipt.query_rt_min <= evaluation.facts.left_rt_min
        assert evaluation.facts.right_rt_min <= receipt.query_rt_max
    from xic_extractor.alignment.backfill.cid_nl_evidence import (
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
    fixture = build_sample_adapter_fixture(tmp_path)
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


@pytest.mark.parametrize("physical_mz_offset", [0.005, 0.012])
def test_recovery_revisits_one_hold_cell_without_minting_support(
    tmp_path: Path,
    physical_mz_offset: float,
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
    assert result.acquisition.traces[0].coordinate.request.mz == pytest.approx(
        fixture.method_subject.precursor_mz + physical_mz_offset
    )
    assert result.assessment.candidate_peaks[0].content.mz == pytest.approx(
        fixture.method_subject.precursor_mz + physical_mz_offset
    )
    assert result.assessment.content.method_result == "HOLD"
    assert result.assessment.content.evaluation_receipt.search_completed is True
    assert len(result.assessment.candidate_peaks) == 1
    assert result.assessment.evidence_refs == ()
    assert result.backfill_input is not None
    assert result.acquisition.logical_xic_request_count == 2
    assert vendor_raw.chromatogram_request_counts == [2]


def test_physical_backfill_does_not_choose_among_clipped_tail_candidates(
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

    fixture = build_sample_adapter_fixture(tmp_path)
    profile = SignalProcessingProfile(window_points=15)
    old = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=old.acquisition_sources,
        acquisition_bindings=old.acquisition_bindings,
        scientific_input_schema_version=old.scientific_input_schema_version,
        processing_algorithm_version=old.processing_algorithm_version,
        processing_contract_version=old.processing_contract_version,
        scientific_inputs=json.loads(old.normalized_scientific_inputs_json)
        | {PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(profile)},
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(),
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
    # The acquired window contains only the declining tail. Its two clipped
    # candidates are ambiguous, not authority to select one or invent an apex
    # outside the observed window. The ordinary wider-query case above covers
    # receipt propagation when an outside-apex-window peak is actually eligible.
    assert evaluation.state == "unavailable"
    assert evaluation.reason == "ambiguous_segment_candidates"
    assert evaluation.facts is None and evaluation.integration is None
    receipt = result.assessment.content.evaluation_receipt
    assert (receipt.query_rt_min, receipt.query_rt_max) == (7.5, 8.5)
    assert result.assessment.candidate_peaks == ()


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

    fixture = build_sample_adapter_fixture(tmp_path)
    profile = SignalProcessingProfile(window_points=15)
    old = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=old.acquisition_sources,
        acquisition_bindings=old.acquisition_bindings,
        scientific_input_schema_version=old.scientific_input_schema_version,
        processing_algorithm_version=old.processing_algorithm_version,
        processing_contract_version=old.processing_contract_version,
        scientific_inputs=json.loads(old.normalized_scientific_inputs_json)
        | {PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(profile)},
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(),
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
    if reliable and tolerance == 12.0:
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
    fixture = build_sample_adapter_fixture(tmp_path)
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
        attempt=issue_processing_attempt(),
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

    fixture = build_sample_adapter_fixture(tmp_path)
    profile = SignalProcessingProfile(window_points=15)
    old = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=old.acquisition_sources,
        acquisition_bindings=old.acquisition_bindings,
        scientific_input_schema_version=old.scientific_input_schema_version,
        processing_algorithm_version=old.processing_algorithm_version,
        processing_contract_version=old.processing_contract_version,
        scientific_inputs=json.loads(old.normalized_scientific_inputs_json)
        | {PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(profile)},
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(),
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
