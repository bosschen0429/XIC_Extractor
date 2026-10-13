from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import xic_extractor.extraction.target_extraction as target_extraction
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence
from xic_extractor.extraction.ms2_selection import selected_candidate_ms2_evidence
from xic_extractor.extraction.result_models import ExtractionResult
from xic_extractor.extraction.scoring_factory import build_scoring_context_factory
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
from xic_extractor.output.targeted_product_model import (
    TargetedProductProjection,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidatesResult,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
    answer_selected_physical_candidate,
    physical_candidates_result,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalSegment,
    PhysicalSegmentUniverse,
    PreparedSignal,
    PreparedSignalProjection,
    PreparedSignalResult,
)

_OPEN_RAW_SEAM = "xic_extractor.extraction.target_extraction.open_raw"


def test_selected_candidate_builds_missing_ms2_evidence() -> None:
    candidate = _candidate()
    evidence = _evidence("NO_MS2")
    calls: list[PeakCandidate] = []

    def _builder(value: PeakCandidate) -> CandidateMS2Evidence:
        calls.append(value)
        return evidence

    selected = selected_candidate_ms2_evidence(candidate, {}, _builder)

    assert selected is evidence
    assert calls == [candidate]


def test_selected_candidate_prefers_existing_ms2_cache() -> None:
    # The legacy finder deliberately points elsewhere. Formal Targeted must
    # select from the shared physical universe and carry that owner's apex.
    candidate = _candidate(rt=8.75, peak_start=8.7, peak_end=8.8)
    calls: list[PeakCandidate] = []

    def _builder(value: PeakCandidate) -> CandidateMS2Evidence:
        calls.append(value)
        return _evidence("NL_FAIL")

    cached = _evidence("NO_MS2")
    selected = selected_candidate_ms2_evidence(candidate, {candidate: cached}, _builder)

    assert selected is cached
    assert calls == []


def test_credible_istd_anchor_rt_uses_selected_ms1_reported_rt() -> None:
    candidate = _candidate(rt=9.2)
    result = ExtractionResult(
        peak_result=_peak_result(candidate),
        nl=None,
        target_label="ISTD",
        role="ISTD",
    )

    assert target_extraction.credible_istd_anchor_rt(result) == 9.2


def test_credible_istd_anchor_rt_rejects_missing_or_hard_quality_peak() -> None:
    flagged = _candidate(rt=9.2, quality_flags=("too_broad",))
    no_peak = PeakDetectionResult(
        status="PEAK_NOT_FOUND",
        peak=None,
        n_points=5,
        max_smoothed=10.0,
        n_prominent_peaks=0,
        candidates=(),
    )

    assert (
        target_extraction.credible_istd_anchor_rt(
            ExtractionResult(peak_result=_peak_result(flagged), nl=None)
        )
        is None
    )
    assert (
        target_extraction.credible_istd_anchor_rt(
            ExtractionResult(peak_result=no_peak, nl=None)
        )
        is None
    )


def test_credible_istd_anchor_rt_rejects_not_counted_projection() -> None:
    candidate = _candidate(rt=9.2)
    result = ExtractionResult(
        peak_result=_peak_result(candidate),
        nl=None,
        target_label="ISTD",
        role="ISTD",
        targeted_product_projection=TargetedProductProjection(
            product_state="not_counted",
            counted_detection=False,
            review_state="review_required",
            projection_reason=(
                "decision: not_counted; not_counted: selected_envelope_boundary_defer"
            ),
        ),
    )

    assert target_extraction.credible_istd_anchor_rt(result) is None


def test_physical_candidate_projection_preserves_owner_values_and_segment_order(
    physical_candidate_input,
) -> None:
    prepared, universe = physical_candidate_input

    result = physical_candidates_result(prepared, universe)

    assert result == PeakCandidatesResult(
        status="OK",
        candidates=(
            PeakCandidate(
                peak=PeakResult(3.0, 90.0, 75.0, 1200.0, 2.0, 4.0),
                selection_apex_rt=3.0,
                selection_apex_intensity=75.0,
                selection_apex_index=4,
                raw_apex_rt=3.0,
                raw_apex_intensity=90.0,
                raw_apex_index=4,
                prominence=65.0,
                region_scan_count=3,
                region_duration_min=2.0,
                region_edge_ratio=3.0 / 65.0,
                region_trace_continuity=1.0,
                proposal_sources=("physical_segment:segment:later",),
                physical_segment=universe.segments[0],
            ),
            PeakCandidate(
                peak=PeakResult(1.0, 60.0, 45.0, 600.0, 0.0, 2.0),
                selection_apex_rt=1.0,
                selection_apex_intensity=45.0,
                selection_apex_index=2,
                raw_apex_rt=1.0,
                raw_apex_intensity=60.0,
                raw_apex_index=2,
                prominence=35.0,
                region_scan_count=3,
                region_duration_min=2.0,
                region_edge_ratio=3.0 / 35.0,
                region_trace_continuity=1.0,
                proposal_sources=("physical_segment:segment:earlier",),
                physical_segment=universe.segments[1],
            ),
        ),
        n_points=7,
        max_smoothed=75.0,
        n_prominent_peaks=2,
    )


@pytest.mark.parametrize(
    ("state", "reason", "prepared_missing", "status"),
    [
        ("unavailable", "no_signal_above_baseline", False, "NO_SIGNAL"),
        ("unavailable", "insufficient_trace_points", False, "PEAK_NOT_FOUND"),
        ("failed", "context_unavailable", False, "PEAK_NOT_FOUND"),
        ("available", "available", True, "PEAK_NOT_FOUND"),
    ],
)
def test_physical_candidate_projection_preserves_unavailable_status(
    physical_candidate_input,
    state,
    reason,
    prepared_missing,
    status,
) -> None:
    prepared, universe = physical_candidate_input
    if prepared_missing:
        prepared = replace(prepared, prepared=None)
    universe = replace(universe, state=state, reason=reason)

    result = physical_candidates_result(prepared, universe)

    assert result == PeakCandidatesResult(status, (), 7, None, 0)


def test_physical_candidate_projection_preserves_empty_universe(
    physical_candidate_input,
) -> None:
    prepared, universe = physical_candidate_input

    result = physical_candidates_result(prepared, replace(universe, segments=()))

    assert result == PeakCandidatesResult("PEAK_NOT_FOUND", (), 7, 75.0, 0)


@pytest.mark.parametrize("missing", [True, False])
def test_selected_physical_answer_rejects_unbound_candidate(
    physical_candidate_input,
    missing,
) -> None:
    prepared, universe = physical_candidate_input
    candidate = physical_candidates_result(prepared, universe).candidates[0]
    segment = (
        None
        if missing
        else replace(universe.segments[0], physical_segment_id="another_universe")
    )

    with pytest.raises(
        ValueError,
        match="^selected Targeted candidate must carry its physical segment$",
    ):
        answer_selected_physical_candidate(
            prepared,
            universe,
            replace(candidate, physical_segment=segment),
            selection_subject_id="target",
            acquisition_rt_window=(0.0, 4.0),
            selection_rt_window=(0.0, 4.0),
        )


@pytest.mark.parametrize("neutral_loss", [None, 116.0474])
@pytest.mark.parametrize("offset", [0.2, -0.15, 0.0])
@pytest.mark.parametrize("pair_label", ["ISTD", "d3-Analyte"])
def test_current_pair_reference_preserves_method_offset_for_all_lanes(
    neutral_loss,
    offset,
    pair_label,
) -> None:
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=10.0 + offset - 1.0,
        rt_max=10.0 + offset + 1.0,
        ppm_tol=20.0,
        neutral_loss_da=neutral_loss,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=False,
        istd_pair=pair_label,
    )
    paired = replace(
        target, label=pair_label, rt_min=9.0, rt_max=11.0, is_istd=True, istd_pair=""
    )
    reference = target_extraction.current_target_reference_rt(
        target,
        reference_rt=10.5,
        paired_target=paired,
    )
    assert reference == pytest.approx(10.5 + offset)
    assert target_extraction.current_target_reference_rt(
        target,
        reference_rt=None,
        paired_target=paired,
        sample_drift=0.3,
    ) == pytest.approx(10.3 + offset)
    assert target_extraction.current_target_reference_rt(
        target,
        reference_rt=None,
        paired_target=paired,
    ) == pytest.approx(10.0 + offset)


@pytest.fixture
def physical_candidate_input() -> tuple[PreparedSignalResult, PhysicalSegmentUniverse]:
    profile = DEFAULT_SIGNAL_PROCESSING_PROFILE
    projection = PreparedSignalProjection(
        schema_version="prepared_signal",
        trace_content_id="trace:test",
        profile_id=profile.profile_id,
        prepared_signal_id="prepared:test",
        trace_point_count=7,
        state="available",
        reason="available",
    )
    # Both test segments have acquired flanks outside their intervals.
    prepared = PreparedSignal(
        projection=projection,
        profile=profile,
        rt=(-1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
        raw=(10.0, 11.0, 60.0, 21.0, 90.0, 14.0, 10.0),
        baseline=(10.0,) * 7,
        smoothed=(10.0, 12.0, 45.0, 13.0, 75.0, 11.0, 10.0),
        positive_signal=(0.0, 2.0, 35.0, 3.0, 65.0, 1.0, 0.0),
    )
    segments = (
        PhysicalSegment(
            physical_segment_id="segment:later",
            start_index=3,
            end_index=6,
            apex_index=4,
            left_rt_min=2.0,
            right_rt_min=4.0,
            apex_rt_min=3.0,
            height=65.0,
            scan_support_count=3,
            area_counts_seconds=1200.0,
            segment_class="isolated_peak",
            boundary_stop_reason="baseline_return",
            evidence_sources=("owner_segment",),
        ),
        PhysicalSegment(
            physical_segment_id="segment:earlier",
            start_index=1,
            end_index=4,
            apex_index=2,
            left_rt_min=0.0,
            right_rt_min=2.0,
            apex_rt_min=1.0,
            height=35.0,
            scan_support_count=3,
            area_counts_seconds=600.0,
            segment_class="low_scan_segment",
            boundary_stop_reason="baseline_return",
            evidence_sources=("owner_segment",),
        ),
    )
    return (
        PreparedSignalResult(projection, prepared, ("trace_evaluation",)),
        PhysicalSegmentUniverse(
            schema_version="physical_segment_universe",
            prepared_signal_id=projection.prepared_signal_id,
            context_rt_start_min=-1.0,
            context_rt_end_min=5.0,
            state="available",
            reason="available",
            segments=segments,
            segment_universe_id="universe:test",
        ),
    )


def _candidate(
    *,
    rt: float = 10.0,
    area: float = 1000.0,
    peak_start: float | None = None,
    peak_end: float | None = None,
    quality_flags: tuple[str, ...] = (),
    merge_note: str = "",
) -> PeakCandidate:
    return PeakCandidate(
        peak=PeakResult(
            rt=rt,
            intensity=100.0,
            intensity_smoothed=100.0,
            area=area,
            peak_start=rt - 0.1 if peak_start is None else peak_start,
            peak_end=rt + 0.1 if peak_end is None else peak_end,
        ),
        selection_apex_rt=rt,
        selection_apex_intensity=100.0,
        selection_apex_index=1,
        raw_apex_rt=rt,
        raw_apex_intensity=100.0,
        raw_apex_index=1,
        prominence=50.0,
        quality_flags=quality_flags,
        merge_note=merge_note,
    )


def _peak_result(candidate: PeakCandidate) -> PeakDetectionResult:
    return PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=5,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )


def _evidence(nl_status: str) -> CandidateMS2Evidence:
    return CandidateMS2Evidence(
        ms2_present=nl_status != "NO_MS2",
        nl_match=nl_status in {"OK", "WARN"},
        nl_status=nl_status,
        trigger_scan_count=1 if nl_status != "NO_MS2" else 0,
        strict_nl_scan_count=1 if nl_status in {"OK", "WARN"} else 0,
        best_loss_ppm=None,
        best_scan_rt=None,
        best_product_base_ratio=None,
        alignment_source="none",
    )


def _intent_target(expected_rt=2.0):
    from xic_extractor.configuration.targets import parse_target_rows

    return parse_target_rows(
        [
            dict(
                label="ISTD",
                mz="258.1085",
                expected_rt_min=str(expected_rt),
                cid_nl_tag="dR",
                role="istd",
                istd_pair="",
            )
        ]
    )[0]


def _strategy_config(tmp_path):
    from xic_extractor.configuration.settings import (
        build_extraction_config,
        resolve_settings,
    )

    values, parsed, _ = resolve_settings(
        {"data_dir": str(tmp_path), "dll_dir": str(tmp_path)}
    )
    return build_extraction_config(
        parsed, values, tmp_path / "settings.csv", tmp_path, "test"
    )


@pytest.mark.parametrize("expected_rt", [2.0, 8.0])
def test_expected_rt_does_not_crop_ms1_candidates_without_ms2(tmp_path, expected_rt):
    target = _intent_target(expected_rt)
    config = _strategy_config(tmp_path)
    rt = np.linspace(0, 12, 601)
    intensity = 10 + 1000 * np.exp(-(((rt - 8) / 0.12) ** 2))
    requests = []

    class Raw:
        def acquisition_rt_bounds(self):
            return 0.0, 12.0

        def extract_xic(self, mz, low, high, ppm):
            requests.append((mz, low, high, ppm))
            return rt, intensity

        def iter_ms2_scans(self, low, high):
            return iter(())

    results = {}
    target_extraction.extract_one_target(
        Raw(),
        config,
        "sample",
        target,
        reference_rt=None,
        results=results,
        diagnostics=[],
        scoring_context_factory=build_scoring_context_factory(config=config),
    )
    result = results[target.label]
    assert requests == [(target.mz, 0.0, 12.0, target.ppm_tol)]
    assert result.cid_nl_evaluation_receipt.search_completed
    assert result.cid_nl_evaluation_receipt.scanned_event_count == 0
    assert result.cid_nl_products == ()
    assert result.candidate_physical_evaluations
    assert any(
        abs(item.integration.rt_apex_min - 8) < 0.1
        for item in result.candidate_physical_evaluations
        if item.integration is not None
    )
    assert not result.cid_nl_relations.supported_candidate_indexes


@pytest.mark.parametrize("precursor_offset", [0.0, 0.5])
def test_targeted_products_use_discovery_seeds_once_per_tag(
    monkeypatch, precursor_offset
):
    from xic_extractor.discovery.ms2_seeds import discovery_seeds_from_scan
    from xic_extractor.extraction.targeted_peak_selection import (
        _discovery_method_products,
        discovery_settings_for_tag,
        prepare_targeted_method_seeds,
    )
    from xic_extractor.ms2_scan_models import Ms2Window

    target = _intent_target()
    scan = Ms2Scan(
        7,
        8.0,
        target.mz + precursor_offset,
        np.array([target.mz - target.neutral_loss_da]),
        np.array([1000.0]),
        1000.0,
    )
    window = Ms2Window("sample", 0.0, 12.0, (Ms2ScanEvent(scan, None, 7),))
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return discovery_seeds_from_scan(*args, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.ms2_seeds.discovery_seeds_from_scan", counted
    )
    prepared = prepare_targeted_method_seeds(window, [target, target])
    assert len(calls) == 1
    products = _discovery_method_products(window, target, prepared["dR"])
    seeds = discovery_seeds_from_scan(
        scan, raw_file=Path("sample"), settings=discovery_settings_for_tag("dR")
    )
    assert [
        (p.precursor_mz, p.product_mz, p.precursor_mz_basis, p.observed_loss_error_ppm)
        for p in products
    ] == [
        (s.precursor_mz, s.product_mz, s.precursor_mz_basis, s.observed_loss_error_ppm)
        for s in seeds
    ]
    assert all(p.scan_number == 7 for p in products)

    if precursor_offset:
        assert products
        assert all(
            p.precursor_mz_basis == "product_plus_neutral_loss" for p in products
        )
        assert all(p.observed_loss_error_ppm is None for p in products)


@pytest.mark.parametrize(
    ("precursor_offset", "event_rt", "expected_rt", "supported"),
    [(0.0, 8.0, 8.0, True), (-0.07, 8.0, 2.0, False), (-0.07, 2.0, None, False)],
)
def test_target_identity_does_not_promote_coisolated_mass_compatibility(
    tmp_path, precursor_offset, event_rt, expected_rt, supported
):
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata, Ms2Window
    from xic_extractor.precursor_evidence import Ms1PrecursorObservation

    target = replace(_intent_target(2.0), is_istd=False, istd_pair="ISTD")
    config = _strategy_config(tmp_path)
    rt = np.linspace(0, 12, 1201)
    intensity = (
        10
        + 800 * np.exp(-(((rt - 2) / 0.12) ** 2))
        + 1000 * np.exp(-(((rt - 8) / 0.12) ** 2))
    )
    scan = Ms2Scan(
        7,
        event_rt,
        target.mz + precursor_offset,
        np.array([target.mz - target.neutral_loss_da]),
        np.array([1000.0]),
        1000.0,
        Ms2PrecursorMetadata(
            charge=1,
            isolation_width_da=1.6,
            isolation_offset_da=0.0,
            master_scan_number=6,
        ),
    )
    window = Ms2Window("sample", 0.0, 12.0, (Ms2ScanEvent(scan, None, 7),))

    class Raw:
        def acquisition_rt_bounds(self):
            return 0.0, 12.0

        def extract_xic(self, mz, low, high, ppm):
            return rt, intensity

        def read_ms1_mass_windows(self, requests):
            return tuple(
                Ms1PrecursorObservation(6, event_rt - 0.01, (target.mz,))
                for _ in requests
            )

    raw = Raw()
    seeds = target_extraction.prepare_sample_method_seeds(raw, window, [target])
    results = {}
    target_extraction.extract_one_target(
        raw,
        config,
        "sample",
        target,
        reference_rt=2.0,
        sample_window=window,
        method_seeds=seeds["dR"],
        results=results,
        diagnostics=[],
        scoring_context_factory=build_scoring_context_factory(config=config),
    )
    result = results[target.label]
    if expected_rt is not None:
        assert result.reported_rt == pytest.approx(expected_rt, abs=0.03)
    assert result.selected_cid_nl_supported is supported
    assert bool(result.cid_nl_relations.supported_candidate_indexes) is supported
    # The inferred observation remains inspectable, rather than being erased.
    assert len(result.cid_nl_products) == 1
    product = result.cid_nl_products[0]
    assert product.assess_precursor_loss(target.neutral_loss_da, 20).supports_nl
    assert product.precursor_mz_basis == (
        "scan_precursor" if supported else "product_plus_neutral_loss"
    )


def test_targeted_scan_resolution_keeps_recorded_support_over_precise_inference():
    from xic_extractor.discovery.ms2_seeds import discovery_seeds_from_scan
    from xic_extractor.discovery.strict_seed_receipts import (
        resolve_discovery_scan_events,
    )
    from xic_extractor.extraction.targeted_peak_selection import (
        discovery_settings_for_tag,
    )
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
    from xic_extractor.precursor_evidence import Ms1PrecursorObservation

    scan = Ms2Scan(
        7,
        8.0,
        500.0,
        np.array([383.9516, 383.9826]),
        np.array([11900.0, 12000.0]),
        12000.0,
        Ms2PrecursorMetadata(
            charge=1,
            isolation_width_da=1.6,
            isolation_offset_da=0.0,
            master_scan_number=6,
        ),
    )
    settings = discovery_settings_for_tag("dR")
    seeds = tuple(
        replace(
            seed,
            ms1_precursor_observation=Ms1PrecursorObservation(
                6, 7.99, (seed.precursor_mz,)
            ),
        )
        if seed.precursor_mz_basis == "product_plus_neutral_loss"
        else seed
        for seed in discovery_seeds_from_scan(
            scan, raw_file=Path("sample"), settings=settings
        )
    )
    assert len(seeds) == 2
    (discovery_event,) = resolve_discovery_scan_events(seeds, settings=settings)
    assert discovery_event.precursor_mz_basis == "product_plus_neutral_loss"
    for events in (seeds, seeds[::-1]):
        (target_event,) = resolve_discovery_scan_events(
            events,
            settings=settings,
            precursor_reference_requirement="recorded_precursor",
        )
        assert target_event.precursor_mz_basis == "scan_precursor"
        assert target_event.product_mz == 383.9516


def test_shared_scan_preserves_each_targets_recorded_precursor_support(tmp_path):
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata, Ms2Window

    targets = [
        replace(_intent_target(8.0), label=label, mz=mz)
        for label, mz in (("selected", 301.00335), ("monoisotopic", 300.0))
    ]
    scan = Ms2Scan(
        7,
        8.0,
        targets[0].mz,
        np.array([t.mz - t.neutral_loss_da for t in targets]),
        np.array([1000.0, 900.0]),
        1000.0,
        Ms2PrecursorMetadata(
            monoisotopic_mz=300.0,
            charge=1,
            isolation_width_da=3.0,
            isolation_offset_da=0.0,
            master_scan_number=6,
        ),
    )
    window = Ms2Window("sample", 0.0, 12.0, (Ms2ScanEvent(scan, None, 7),))
    rt = np.linspace(0, 12, 1201)

    class Raw:
        def acquisition_rt_bounds(self):
            return 0.0, 12.0

        def extract_xic(self, mz, low, high, ppm):
            return rt, 10 + 1000 * np.exp(-(((rt - 8) / 0.12) ** 2))

    raw = Raw()
    prepared = target_extraction.prepare_sample_method_seeds(raw, window, targets)
    config = _strategy_config(tmp_path)
    results = {}
    for target in targets:
        target_extraction.extract_one_target(
            raw,
            config,
            "sample",
            target,
            reference_rt=8.0,
            sample_window=window,
            method_seeds=prepared["dR"],
            results=results,
            diagnostics=[],
            scoring_context_factory=build_scoring_context_factory(config=config),
        )
        result = results[target.label]
        assert result.selected_cid_nl_supported
        assert result.reported_rt == pytest.approx(8.0, abs=0.03)
        assert {p.precursor_mz for p in result.cid_nl_products} == {target.mz}
