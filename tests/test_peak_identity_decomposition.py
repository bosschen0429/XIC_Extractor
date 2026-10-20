"""No-RAW characterization of observation, hypothesis, and evidence boundaries.

The tuple helpers in this file are deliberately test-local comparison probes.
They are not owner APIs, persisted identifiers, or proposed serialization
contracts.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import fake_acquisition_source
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.discovery.delta_mass_patterns import match_delta_mass_pattern
from xic_extractor.discovery.detected_features import DetectedFeature
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    DiscoverySeed,
    DiscoverySettings,
    NeutralLossProfile,
)
from xic_extractor.discovery.ms1_backfill import (
    _backfill_ms1_candidates_with_acquisition,
)
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence
from xic_extractor.peak_detection.hypotheses import (
    IntegrationResult,
    PeakHypothesis,
    build_peak_hypotheses,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.traces import (
    Trace,
    targeted_trace_group,
    untargeted_trace_group,
)
from xic_extractor.xic_models import XICRequest, XICTrace

_RAW_FILE = Path("synthetic/SampleA.raw")
_NEUTRAL_LOSS_DA = 116.0474


def test_one_ms1_observation_can_have_distinct_workflow_hypotheses() -> None:
    trace = _trace()
    candidate = _peak_candidate()
    peak_result = _peak_result(candidate)
    targeted_group = targeted_trace_group(
        trace,
        target_label="KnownAnalyte",
        resolver_mode="local_minimum",
        neutral_loss_tag="DNA_dR",
    )
    untargeted_group = untargeted_trace_group(
        trace,
        family_id="FAM000123",
        neutral_loss_tag="DNA_dR",
    )

    targeted = build_peak_hypotheses(
        analysis_mode="targeted",
        sample_name="SampleA",
        target_label="KnownAnalyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="local_minimum",
        peak_result=peak_result,
        trace_group=targeted_group,
    )[0]
    untargeted_context = build_peak_hypotheses(
        analysis_mode="untargeted",
        sample_name="SampleA",
        target_label="UnknownFeature",
        role="",
        istd_pair="",
        resolver_mode="local_minimum",
        peak_result=peak_result,
        trace_group=untargeted_group,
    )[0]

    assert _ms1_trace_probe(targeted_group.primary_trace) == _ms1_trace_probe(
        untargeted_group.primary_trace
    )
    assert _integration_probe(targeted) == _integration_probe(untargeted_context)
    assert targeted.trace_group_id != untargeted_context.trace_group_id
    assert targeted.hypothesis_id != untargeted_context.hypothesis_id
    assert untargeted_group.analysis_mode == "untargeted"
    assert untargeted_context.analysis_mode == "untargeted"


def test_ms2_evidence_changes_without_changing_ms1_integration_or_id() -> None:
    trace = _trace()
    candidate = _peak_candidate()
    peak_result = _peak_result(candidate)
    trace_group = targeted_trace_group(
        trace,
        target_label="KnownAnalyte",
        resolver_mode="local_minimum",
    )

    supported = build_peak_hypotheses(
        analysis_mode="targeted",
        sample_name="SampleA",
        target_label="KnownAnalyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="local_minimum",
        peak_result=peak_result,
        candidate_ms2_evidence={candidate: _ms2_evidence(nl_match=True)},
        trace_group=trace_group,
    )[0]
    unsupported = build_peak_hypotheses(
        analysis_mode="targeted",
        sample_name="SampleA",
        target_label="KnownAnalyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="local_minimum",
        peak_result=peak_result,
        candidate_ms2_evidence={candidate: _ms2_evidence(nl_match=False)},
        trace_group=trace_group,
    )[0]

    assert supported.hypothesis_id == unsupported.hypothesis_id
    assert _integration_probe(supported) == _integration_probe(unsupported)
    assert supported.evidence.nl_match is True
    assert unsupported.evidence.nl_match is False
    assert supported.evidence.nl_status == "OK"
    assert unsupported.evidence.nl_status == "NL_FAIL"
    assert supported.evidence.best_loss_ppm == pytest.approx(1.0)
    assert unsupported.evidence.best_loss_ppm is None


def test_cid_nl_seed_changes_ms1_generation_window_before_peak_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _discovery_settings(ms1_search_padding_min=0.20)
    first_group = group_discovery_seeds(
        (_seed(scan_number=10, rt=7.90),),
        settings=settings,
    )[0]
    second_group = group_discovery_seeds(
        (_seed(scan_number=20, rt=8.10),),
        settings=settings,
    )[0]
    raw = _FakeXicSource()

    def _same_physical_peak(
        rt: np.ndarray,
        intensity: np.ndarray,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
    ) -> PeakDetectionResult:
        del rt, intensity, config, preferred_rt, strict_preferred_rt
        peak = PeakResult(
            rt=8.00,
            intensity=500.0,
            intensity_smoothed=480.0,
            area=42.0,
            peak_start=7.95,
            peak_end=8.05,
        )
        return PeakDetectionResult(
            status="OK",
            peak=peak,
            n_points=3,
            max_smoothed=480.0,
            n_prominent_peaks=1,
        )

    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        _same_physical_peak,
    )

    first = _backfill_ms1_candidates_with_acquisition(
        raw,
        (first_group,),
        acquisition_source=raw.acquisition_source,
        settings=settings,
        peak_config=_peak_config(),
    ).candidates[0]
    second = _backfill_ms1_candidates_with_acquisition(
        raw,
        (second_group,),
        acquisition_source=raw.acquisition_source,
        settings=settings,
        peak_config=_peak_config(),
    ).candidates[0]

    assert raw.requests == [
        (500.0, pytest.approx(7.70), pytest.approx(8.10), 20.0),
        (500.0, pytest.approx(7.90), pytest.approx(8.30), 20.0),
    ]
    assert _discovery_ms1_peak_probe(first) == _discovery_ms1_peak_probe(second)
    assert first.candidate_id != second.candidate_id
    assert first.ms1_feature_row_id == second.ms1_feature_row_id


def test_pt_hypothesis_composes_four_ms1_feature_observations() -> None:
    features = (
        _detected_feature("base", 500.0, 10.000),
        _detected_feature("plus1", 501.002111, 10.001),
        _detected_feature("plus2", 502.002271, 10.002),
        _detected_feature("plus4", 504.005213, 10.003),
    )

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.position_count == 4
    assert (
        candidate.base_feature_id,
        candidate.pt_plus_1_feature_id,
        candidate.pt_plus_2_feature_id,
        candidate.pt_plus_4_feature_id,
    ) == ("base", "plus1", "plus2", "plus4")


def _trace() -> Trace:
    return Trace.from_arrays(
        sample_name="SampleA",
        mz=500.0,
        rt=[7.8, 7.9, 8.0, 8.1, 8.2],
        intensity=[0.0, 50.0, 500.0, 50.0, 0.0],
        rt_min=7.8,
        rt_max=8.2,
        ppm_tol=10.0,
        source="synthetic_characterization",
    )


def _peak_candidate() -> PeakCandidate:
    peak = PeakResult(
        rt=8.0,
        intensity=500.0,
        intensity_smoothed=480.0,
        area=42.0,
        peak_start=7.9,
        peak_end=8.1,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=8.0,
        selection_apex_intensity=480.0,
        selection_apex_index=2,
        raw_apex_rt=8.0,
        raw_apex_intensity=500.0,
        raw_apex_index=2,
        prominence=450.0,
        proposal_sources=("synthetic_characterization",),
        source_apex_rank=1,
    )


def _peak_result(candidate: PeakCandidate) -> PeakDetectionResult:
    return PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=5,
        max_smoothed=480.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )


def _ms2_evidence(*, nl_match: bool) -> CandidateMS2Evidence:
    return CandidateMS2Evidence(
        ms2_present=True,
        nl_match=nl_match,
        nl_status="OK" if nl_match else "NL_FAIL",
        trigger_scan_count=1,
        strict_nl_scan_count=1 if nl_match else 0,
        best_loss_ppm=1.0 if nl_match else None,
        best_scan_rt=8.0,
        best_product_base_ratio=0.3,
        alignment_source="region",
    )


def _ms1_trace_probe(trace: Trace) -> tuple[object, ...]:
    return (
        trace.sample_name,
        trace.mz,
        tuple(trace.rt.tolist()),
        tuple(trace.intensity.tolist()),
        trace.rt_min,
        trace.rt_max,
        trace.ppm_tol,
    )


def _integration_probe(hypothesis: PeakHypothesis) -> IntegrationResult:
    return hypothesis.integration


def _discovery_settings(*, ms1_search_padding_min: float) -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", _NEUTRAL_LOSS_DA),
        ms1_search_padding_min=ms1_search_padding_min,
    )


def _seed(*, scan_number: int, rt: float) -> DiscoverySeed:
    return DiscoverySeed(
        raw_file=_RAW_FILE,
        sample_stem="SampleA",
        scan_number=scan_number,
        rt=rt,
        precursor_mz=500.0,
        product_mz=500.0 - _NEUTRAL_LOSS_DA,
        product_intensity=10_000.0,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=_NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=_NEUTRAL_LOSS_DA,
        observed_loss_error_ppm=0.0,
    )


def _peak_config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("raw"),
        dll_dir=Path("dll"),
        output_csv=Path("output/xic_results.csv"),
        diagnostics_csv=Path("output/xic_diagnostics.csv"),
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
    )


class _FakeXicSource:
    def __init__(self) -> None:
        self.rt = np.asarray([7.95, 8.00, 8.05])
        self.intensity = np.asarray([20.0, 500.0, 20.0])
        self.acquisition_source = fake_acquisition_source()
        self.raw_chromatogram_call_count = 0
        self.requests: list[tuple[float, float, float, float]] = []

    def extract_xic_many(
        self,
        requests: Sequence[XICRequest],
    ) -> tuple[XICTrace, ...]:
        for request in requests:
            self.requests.append(
                (request.mz, request.rt_min, request.rt_max, request.ppm_tol)
            )
        return tuple(XICTrace.from_arrays(self.rt, self.intensity) for _ in requests)

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return 0, len(self.rt) - 1


def _discovery_ms1_peak_probe(
    candidate: DiscoveryCandidate,
) -> tuple[object, ...]:
    return (
        candidate.precursor_mz,
        candidate.ms1_apex_rt,
        candidate.ms1_peak_rt_start,
        candidate.ms1_peak_rt_end,
        candidate.ms1_height,
        candidate.ms1_area,
    )


def _detected_feature(feature_id: str, mz: float, rt: float) -> DetectedFeature:
    return DetectedFeature(
        sample_stem="SampleA",
        feature_id=feature_id,
        source_feature_row_id=None,
        mz=mz,
        rt_apex_min=rt,
        rt_start_min=rt - 0.05,
        rt_end_min=rt + 0.05,
        height=1000.0,
        area=500.0,
        trace_quality="GOOD",
        shape_score=0.90,
        source="synthetic_characterization",
    )
