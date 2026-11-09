from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import xic_extractor.extraction.target_extraction as target_extraction
from tests.target_extraction_fixtures import (
    _candidate,
    _peak_result,
)
from tests.targeted_fixtures import ScopedMs2WindowReader
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence
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
                selection_apex_index=7,
                raw_apex_rt=3.0,
                raw_apex_intensity=90.0,
                raw_apex_index=7,
                prominence=65.0,
                region_scan_count=5,
                region_duration_min=2.0,
                region_edge_ratio=3.0 / 65.0,
                region_trace_continuity=1.0,
                proposal_sources=("physical_segment",),
                physical_segment=universe.segments[0],
            ),
            PeakCandidate(
                peak=PeakResult(1.0, 60.0, 45.0, 600.0, 0.0, 2.0),
                selection_apex_rt=1.0,
                selection_apex_intensity=45.0,
                selection_apex_index=3,
                raw_apex_rt=1.0,
                raw_apex_intensity=60.0,
                raw_apex_index=3,
                prominence=35.0,
                region_scan_count=5,
                region_duration_min=2.0,
                region_edge_ratio=3.0 / 35.0,
                region_trace_continuity=1.0,
                proposal_sources=("physical_segment",),
                physical_segment=universe.segments[1],
            ),
        ),
        n_points=11,
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

    assert result == PeakCandidatesResult(status, (), 11, None, 0)


def test_physical_candidate_projection_preserves_empty_universe(
    physical_candidate_input,
) -> None:
    prepared, universe = physical_candidate_input

    result = physical_candidates_result(prepared, replace(universe, segments=()))

    assert result == PeakCandidatesResult("PEAK_NOT_FOUND", (), 11, 75.0, 0)


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
        profile=profile,
        rt=(-1.0, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0),
        raw=(10.0, 11.0, 45.0, 60.0, 45.0, 21.0, 60.0, 90.0, 60.0, 14.0, 10.0),
        state="available",
        reason="available",
    )
    # Both segments have acquired flanks and multiple supported core samples.
    prepared = PreparedSignal(
        projection=projection,
        profile=profile,
        rt=(-1.0, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0),
        raw=(10.0, 11.0, 45.0, 60.0, 45.0, 21.0, 60.0, 90.0, 60.0, 14.0, 10.0),
        baseline=(10.0,) * 11,
        smoothed=(10.0, 12.0, 35.0, 45.0, 35.0, 13.0, 55.0, 75.0, 55.0, 11.0, 10.0),
        positive_signal=(0.0, 2.0, 25.0, 35.0, 25.0, 3.0, 45.0, 65.0, 45.0, 1.0, 0.0),
    )
    segments = (
        PhysicalSegment(
            start_index=5,
            end_index=10,
            apex_index=7,
            left_rt_min=2.0,
            right_rt_min=4.0,
            apex_rt_min=3.0,
            height=65.0,
            scan_support_count=5,
            area_counts_seconds=1200.0,
            segment_class="isolated_peak",
            boundary_stop_reason="baseline_return",
            evidence_sources=("owner_segment",),
        ),
        PhysicalSegment(
            start_index=1,
            end_index=6,
            apex_index=3,
            left_rt_min=0.0,
            right_rt_min=2.0,
            apex_rt_min=1.0,
            height=35.0,
            scan_support_count=5,
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
            prepared=projection,
            context_rt_start_min=-1.0,
            context_rt_end_min=5.0,
            state="available",
            reason="available",
            segments=segments,
        ),
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
    return build_extraction_config(parsed, values, tmp_path / "settings.csv", tmp_path)


@pytest.mark.parametrize("paired", [False, True])
@pytest.mark.parametrize("anchor_count", [1, 2])
@pytest.mark.parametrize("neutral_loss", [None, 116.0474])
@pytest.mark.parametrize("paired_reference_count", [0, 2])
def test_batch_applies_sample_drift_once(
    tmp_path, paired, anchor_count, neutral_loss, paired_reference_count
):
    from xic_extractor.extraction.result_models import FileResult, RunOutput
    from xic_extractor.extraction.targeted_batch_selection import (
        complete_targeted_candidate_batch,
    )
    from xic_extractor.peak_detection.targeted_context import TargetedBatchContext

    config = _strategy_config(tmp_path)
    analyte = replace(
        _intent_target(10.0),
        label="analyte",
        mz=500.0,
        is_istd=False,
        istd_pair="standard-0" if paired else "",
        neutral_loss_da=neutral_loss,
    )
    standards = [
        replace(
            analyte,
            label=f"standard-{i}",
            mz=600.0 + i * 100,
            is_istd=True,
            istd_pair="",
            expected_rt_min=5.0 + i * 2,
        )
        for i in range(anchor_count)
    ]
    paired_references = [
        replace(
            analyte,
            label=f"paired-{i}",
            mz=800.0 + i * 100,
            istd_pair="standard-0",
            expected_rt_min=3.0 + i * 3,
        )
        for i in range(paired_reference_count)
    ]
    rt = np.linspace(0, 18, 1801)

    class Raw(ScopedMs2WindowReader):
        def __init__(self, intensity):
            self.intensity = intensity

        def acquisition_rt_bounds(self):
            return 0.0, 18.0

        def extract_xic(self, *args):
            return rt, self.intensity

        def iter_ms2_scans(self, low, high):
            return iter(())

    files = []
    # The unshifted sample links standards to their declared RTs. In the
    # shifted sample the distractor exposes either double displacement or a
    # method-frame drift diluted by already sample-local paired references.
    for sample, drift in (("reference", 0.0), ("shifted", 2.0)):
        results = {}
        for target in [*standards, *paired_references, analyte]:
            center = target.expected_rt_min + drift
            intensity = 10 + 1000 * np.exp(-(((rt - center) / 0.08) ** 2))
            if target is analyte:
                distractor_offset = -1 if paired_reference_count else 2
                intensity += 1000 * np.exp(
                    -(((rt - center - distractor_offset) / 0.08) ** 2)
                )

            target_extraction.extract_one_target(
                Raw(intensity),
                config,
                sample,
                target,
                reference_rt=5.0 + drift if target.istd_pair else None,
                paired_target=standards[0] if target.istd_pair else None,
                istd_rt_in_this_sample=(5.0 + drift if target.istd_pair else None),
                sample_drift=drift if not target.is_istd else 0.0,
                results=results,
                diagnostics=[],
                defer_selection=not target.is_istd,
            )
        files.append(FileResult(sample, results))
    batch = TargetedBatchContext(
        tuple(
            result.targeted_candidate_assessment.population_context()
            for file in files
            for result in file.results.values()
            if result.targeted_candidate_assessment is not None
        )
    )
    assert [
        batch.reference_rt(batch.population_by_cell[(sample, "analyte")])
        for sample in ("reference", "shifted")
    ] == pytest.approx([10.0, 12.0], abs=0.02)
    completed = complete_targeted_candidate_batch(RunOutput(files, []), config)
    assert [f.results["analyte"].reported_rt for f in completed.file_results] == (
        pytest.approx([10.0, 12.0], abs=0.02)
    )
    for target in paired_references:
        assert [
            f.results[target.label].reported_rt for f in completed.file_results
        ] == pytest.approx(
            [target.expected_rt_min, target.expected_rt_min + 2], abs=0.02
        )


@pytest.mark.parametrize("expected_rt", [2.0, 8.0])
def test_expected_rt_does_not_crop_ms1_candidates_without_ms2(tmp_path, expected_rt):
    target = _intent_target(expected_rt)
    config = _strategy_config(tmp_path)
    rt = np.linspace(0, 12, 601)
    intensity = 10 + 1000 * np.exp(-(((rt - 8) / 0.12) ** 2))
    requests = []

    class Raw(ScopedMs2WindowReader):
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
    from xic_extractor.discovery.ms2_events import discovery_events_from_scan
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
        return discovery_events_from_scan(*args, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.ms2_events.discovery_events_from_scan", counted
    )
    prepared = prepare_targeted_method_seeds(window, [target, target])
    assert len(calls) == 1
    products = _discovery_method_products(window, target, prepared["dR"])
    seeds = discovery_events_from_scan(
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

    class Raw(ScopedMs2WindowReader):
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
    from xic_extractor.discovery.ms2_events import discovery_events_from_scan
    from xic_extractor.discovery.strict_event_receipts import (
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
        for seed in discovery_events_from_scan(
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

    class Raw(ScopedMs2WindowReader):
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


def test_precursor_scoped_ms2_keeps_every_target_evidence_route(tmp_path):
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata, Ms2Window

    target = _intent_target(8.0)
    product = target.mz - target.neutral_loss_da

    def event(number, rt, selected, *, mono=None):
        scan = Ms2Scan(
            number,
            rt,
            selected,
            np.array([product, selected - target.neutral_loss_da]),
            np.array([1000.0, 900.0]),
            1000.0,
            Ms2PrecursorMetadata(
                monoisotopic_mz=mono,
                charge=1,
                isolation_width_da=1.6,
                isolation_offset_da=0.0,
                master_scan_number=number - 1,
            ),
        )
        return Ms2ScanEvent(scan, None, number)

    events = (
        event(7, 7.98, target.mz),
        event(9, 8.0, target.mz + 0.5),
        event(11, 8.02, target.mz + 1.00335, mono=target.mz),
        event(13, 8.04, 400.2),
    )
    rt = np.linspace(0, 12, 1201)
    reads = []

    class Raw(ScopedMs2WindowReader):
        def acquisition_rt_bounds(self):
            return 0.0, 12.0

        def extract_xic(self, mz, low, high, ppm):
            return rt, 10 + 1000 * np.exp(-(((rt - 8) / 0.12) ** 2))

        def iter_ms2_scans(self, low, high):
            reads.append((low, high))
            return iter(events)

        def read_ms1_mass_windows(self, requests):
            return (None,) * len(requests)

    config = _strategy_config(tmp_path)

    def extract(sample_window):
        results = {}
        target_extraction.extract_one_target(
            Raw(),
            config,
            "sample",
            target,
            reference_rt=8.0,
            sample_window=sample_window,
            results=results,
            diagnostics=[],
            scoring_context_factory=build_scoring_context_factory(config=config),
        )
        return results[target.label]

    unscoped = extract(Ms2Window("sample", 0.0, 12.0, events))
    scoped = extract(None)

    assert reads == [(0.0, 12.0)]
    assert {p.precursor_mz_basis for p in unscoped.cid_nl_products} == {
        "scan_precursor",
        "scan_monoisotopic",
        "product_plus_neutral_loss",
    }
    assert scoped.cid_nl_products == unscoped.cid_nl_products
    assert scoped.cid_nl_evaluation_receipt == unscoped.cid_nl_evaluation_receipt
    assert scoped.selected_cid_nl_supported == unscoped.selected_cid_nl_supported


@pytest.mark.parametrize("neighbour_loss_match", [False, True])
def test_target_peak_evidence_does_not_reward_missing_or_foreign_ms2(
    tmp_path, neighbour_loss_match
):
    from xic_extractor.ms2_scan_models import Ms2Window

    target = _intent_target(8.0)
    config = _strategy_config(tmp_path)
    rt = np.linspace(0, 12, 1201)
    intensity = (
        10
        + 200 * np.exp(-(((rt - 2) / 0.12) ** 2))
        + 1000 * np.exp(-(((rt - 8) / 0.12) ** 2))
    )
    precursor = target.mz - 0.015 if neighbour_loss_match else target.mz
    product = precursor - target.neutral_loss_da if neighbour_loss_match else 110.0
    scan = Ms2Scan(7, 8.0, precursor, np.array([product]), np.array([1000.0]), 1000.0)
    window = Ms2Window("sample", 0.0, 12.0, (Ms2ScanEvent(scan, None, 7),))

    class Raw(ScopedMs2WindowReader):
        def acquisition_rt_bounds(self):
            return 0.0, 12.0

        def extract_xic(self, mz, low, high, ppm):
            return rt, intensity

    results = {}
    target_extraction.extract_one_target(
        Raw(),
        config,
        "sample",
        target,
        reference_rt=8.0,
        sample_window=window,
        results=results,
        diagnostics=[],
        scoring_context_factory=build_scoring_context_factory(config=config),
    )
    result = results[target.label]
    # A missing DDA observation is not a chemical advantage over the reference
    # peak; a neighbouring precursor's class loss is not this target's evidence.
    assert result.reported_rt == pytest.approx(8.0, abs=0.03)
    assert not result.selected_cid_nl_supported
    assert result.candidate_ms2_evidence is not None
    assert not result.candidate_ms2_evidence.nl_match
    assert result.candidate_ms2_evidence.trace.product_point_count == 0
    assert result.candidate_ms2_evidence.best_product_evidence is None
    assert result.nl_token != "OK"


@pytest.mark.parametrize("local_ms2", [False, True])
def test_istd_reference_body_competes_with_remote_nl_peak(tmp_path, local_ms2):
    target = _intent_target(23.5)
    config = _strategy_config(tmp_path)
    rt = np.linspace(0.0, 30.0, 3001)
    intensity = (
        10.0
        + 1200.0 * np.exp(-0.5 * ((rt - 9.7) / 0.08) ** 2)
        + 1000.0 * np.exp(-0.5 * ((rt - 23.0) / 0.10) ** 2)
    )
    scans = [
        Ms2Scan(
            1,
            9.7,
            target.mz,
            np.array([target.mz - target.neutral_loss_da]),
            np.array([1000.0]),
            1000.0,
        )
    ]
    if local_ms2:
        scans.append(
            Ms2Scan(2, 23.0, target.mz, np.array([110.0]), np.array([1000.0]), 1000.0)
        )

    class Raw(ScopedMs2WindowReader):
        def acquisition_rt_bounds(self):
            return 0.0, 30.0

        def extract_xic(self, *args):
            return rt, intensity

        def iter_ms2_scans(self, *args):
            return iter(Ms2ScanEvent(scan, None, scan.scan_number) for scan in scans)

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
    # The measured reference body, not the distant class-loss observation, is
    # this standard's identity. Selecting it must not manufacture missing NL.
    assert result.reported_rt == pytest.approx(23.0, abs=0.03)
    assert not result.selected_cid_nl_supported
    assert result.candidate_ms2_evidence.nl_status == (
        "NL_FAIL" if local_ms2 else "NO_MS2"
    )
    assert any(
        region.chemical_support and abs(region.segment.apex_rt_min - 9.7) < 0.03
        for region in result.targeted_candidate_assessment.population_context().regions
    )
    assert result.context_admissible_candidate_indexes is not None
    assert [
        result.candidate_physical_evaluations[index].integration.rt_apex_min
        for index in result.context_admissible_candidate_indexes
    ] == pytest.approx([23.0], abs=0.03)


@pytest.mark.parametrize(
    "scenario",
    ["weak_reference", "chemical_competition", "unrelated_dominant_body", "real_drift"],
)
def test_istd_reference_selection_is_not_nearest_apex_always(
    physical_candidate_input, scenario
):
    from xic_extractor.peak_detection.targeted_context import (
        TargetedBatchContext,
        TargetedPopulationContext,
        describe_targeted_region,
    )

    prepared, universe = physical_candidate_input
    unconfirmed, confirmed = (
        describe_targeted_region(
            prepared.prepared,
            segment,
            measurement_available=True,
            chemical_support=segment.apex_rt_min == 1.0,
        )
        for segment in universe.segments
    )
    reference = 3.5
    if scenario in {"weak_reference", "unrelated_dominant_body"}:
        factor = 0.01 if scenario == "weak_reference" else 1000.0
        unconfirmed = replace(
            unconfirmed,
            segment=replace(
                unconfirmed.segment,
                left_rt_min=12.0,
                apex_rt_min=13.0,
                right_rt_min=14.0,
                height=unconfirmed.segment.height * factor,
                area_counts_seconds=unconfirmed.segment.area_counts_seconds * factor,
            ),
            acquired_rt_start_min=12.0,
            acquired_rt_end_min=14.0,
            body_support=unconfirmed.body_support * factor,
        )
        reference = 13.0 if scenario == "weak_reference" else 1.0
    regions = (confirmed,) if scenario == "real_drift" else (unconfirmed, confirmed)
    if scenario == "real_drift":
        reference = 30.0
    # Adjacent physical bodies have overlapping reference-distance ranges:
    # positive chemistry can still choose the farther apex. A tiny near body
    # cannot displace a confirmed drifting standard; global MS1 dominance is
    # likewise not permission to erase a smaller confirmed reference peak.
    for ordered in (regions, regions[::-1]):
        population = TargetedPopulationContext(
            sample_name="sample",
            target_label="ISTD",
            mz=258.1085,
            ppm_tol=20.0,
            reference_rt_min=reference,
            regions=ordered,
        )
        selected = TargetedBatchContext((population,)).select(population, is_istd=True)
        assert selected.selected_region == confirmed


@pytest.mark.parametrize("is_istd", [True, False])
def test_reference_istd_keeps_broad_peak_instead_of_clean_remote_nl_peak(
    tmp_path, is_istd
):
    from xic_extractor.peak_detection.physical_peak_codec import (
        deserialize_physical_peak_evaluation,
        serialize_physical_peak_evaluation,
    )

    target = replace(_intent_target(15.5), is_istd=is_istd)
    config = _strategy_config(tmp_path)
    rt = np.linspace(0.0, 24.0, 2401)
    rng = np.random.default_rng(479)
    intensity = (
        300.0
        + 12000.0 * np.exp(-0.5 * ((rt - 17.5) / 0.8) ** 2)
        + 3000.0 * np.exp(-0.5 * ((rt - 10.9) / 0.06) ** 2)
        + rng.uniform(-150.0, 150.0, rt.size)
    )
    scans = tuple(
        Ms2Scan(
            index + 1,
            apex,
            target.mz,
            np.array([target.mz - target.neutral_loss_da]),
            np.array([1000.0]),
            1000.0,
        )
        for index, apex in enumerate((10.9, 17.3, 17.5, 17.7))
    )

    class Raw(ScopedMs2WindowReader):
        def acquisition_rt_bounds(self):
            return 0.0, 24.0

        def extract_xic(self, *args):
            return rt, intensity

        def iter_ms2_scans(self, *args):
            return iter(Ms2ScanEvent(scan, None, scan.scan_number) for scan in scans)

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
    # Poor local relief cannot erase the reference-supported broad body in
    # either Targeted role. The clean 10.9-minute peak is not a substitute.
    assert result.reported_rt == pytest.approx(17.5, abs=0.05)
    assert result.selected_cid_nl_supported
    selected = next(
        evaluation
        for evaluation in result.candidate_physical_evaluations
        if evaluation.integration is not None
        and evaluation.integration.rt_apex_min == result.reported_rt
    )
    restored = deserialize_physical_peak_evaluation(
        serialize_physical_peak_evaluation(
            rt,
            intensity,
            profile=selected.retained_prepared_signal.profile,
            evaluation=selected,
        )
    )[-1]
    assert restored.integration == selected.integration
    if is_istd:
        assert target_extraction.credible_istd_anchor_rt(result) == result.reported_rt
        assert (
            restored.question.selection_policy_version
            == "reference_localized_contour_peak"
        )
