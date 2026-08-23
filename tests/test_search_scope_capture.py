from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
from xic_extractor.config import ExtractionConfig
from xic_extractor.peak_detection import capture as capture_owner
from xic_extractor.peak_detection.integration import integrate_area_counts_seconds
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    EvidenceProviderBinding,
    SearchScopeAuthorizationError,
    TargetSearchScope,
    UntargetedSearchScope,
    require_capture_candidate,
    scope_allows_evidence_inspection,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import resolve_request_acquisition_source


def test_no_raw_scopes_share_capture_observation_and_hypothesis_owner(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("S1",))
    provider = EvidenceProviderBinding(
        provider_id="synthetic_declared_evidence",
    )
    targeted = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=fixture.request,
        samples=fixture.samples,
        evidence_providers=(provider,),
        declared_candidate_ids=("candidate-a", "candidate-b"),
        capture_candidate_ids=("candidate-a",),
        target_snapshot=fixture.target.snapshot,
        target=fixture.target,
    )
    untargeted = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=fixture.request,
        samples=fixture.samples,
        evidence_providers=(provider,),
        declared_candidate_ids=("candidate-a", "candidate-b"),
        capture_candidate_ids=("candidate-a", "candidate-b"),
        method_context_id="synthetic-cid-nl",
    )
    sample = fixture.sample("S1")
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=sample,
    )
    rt = np.asarray([7.7, 7.8, 7.9, 8.0, 8.1, 8.2, 8.3])
    intensity = np.asarray([0.0, 10.0, 50.0, 100.0, 50.0, 10.0, 0.0])
    trace = Trace.from_arrays(
        sample_name=sample.metadata.sample_name,
        mz=fixture.target.definition.target.mz,
        rt=rt,
        intensity=intensity,
        rt_min=float(rt[0]),
        rt_max=float(rt[-1]),
        ppm_tol=fixture.target.definition.target.ppm_tol,
        source="synthetic_scope_oracle",
        acquisition_source_id=source.acquisition_source_id,
    )
    peak_config = _peak_config(tmp_path)

    targeted_capture = capture_owner.capture_peak_hypotheses(
        scope=targeted,
        candidate_id="candidate-a",
        trace=trace,
        peak_config=peak_config,
        preferred_rt=8.0,
        peak_finder=_synthetic_peak_finder,
        resolver_mode=peak_config.resolver_mode,
    )
    untargeted_capture = capture_owner.capture_peak_hypotheses(
        scope=untargeted,
        candidate_id="candidate-a",
        trace=trace,
        peak_config=peak_config,
        preferred_rt=8.0,
        peak_finder=_synthetic_peak_finder,
        resolver_mode=peak_config.resolver_mode,
    )

    assert targeted_capture.selected_hypothesis is not None
    assert untargeted_capture.selected_hypothesis is not None
    assert targeted_capture.selected_hypothesis.evidence.common is not None
    assert untargeted_capture.selected_hypothesis.evidence.common is not None
    assert (
        targeted_capture.selected_hypothesis.integration
        == untargeted_capture.selected_hypothesis.integration
    )
    targeted_common = asdict(targeted_capture.selected_hypothesis.evidence.common)
    untargeted_common = asdict(untargeted_capture.selected_hypothesis.evidence.common)
    for workflow_field in ("source", "source_id", "provenance"):
        targeted_common.pop(workflow_field)
        untargeted_common.pop(workflow_field)
    assert targeted_common == untargeted_common

    targeted_observation = capture_owner.mint_captured_peak_observation(
        targeted_capture,
        processing_owners=fixture.owners,
        sample=sample,
        resolver_mode=peak_config.resolver_mode,
    )
    untargeted_observation = capture_owner.mint_captured_peak_observation(
        untargeted_capture,
        processing_owners=fixture.owners,
        sample=sample,
        resolver_mode=peak_config.resolver_mode,
    )
    assert targeted_observation.content == untargeted_observation.content
    assert (
        targeted_observation.generation_provenance
        == untargeted_observation.generation_provenance
    )
    assert scope_allows_evidence_inspection(targeted, "candidate-b") is True
    with pytest.raises(SearchScopeAuthorizationError, match="Target capture"):
        require_capture_candidate(targeted, "candidate-b")
    with pytest.raises(SearchScopeAuthorizationError, match="Target capture"):
        capture_owner.capture_peak_hypotheses(
            scope=targeted,
            candidate_id="candidate-b",
            trace=trace,
            peak_config=peak_config,
            preferred_rt=8.0,
            peak_finder=_synthetic_peak_finder,
            resolver_mode=peak_config.resolver_mode,
        )
    assert require_capture_candidate(untargeted, "candidate-b") == "candidate-b"


def test_crossed_scope_candidate_authorization_fails_closed(tmp_path: Path) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("S1",))
    provider = EvidenceProviderBinding(
        provider_id="synthetic_declared_evidence",
    )
    targeted = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=fixture.request,
        samples=fixture.samples,
        evidence_providers=(provider,),
        declared_candidate_ids=("candidate-a", "candidate-b"),
        capture_candidate_ids=("candidate-a",),
        target_snapshot=fixture.target.snapshot,
        target=fixture.target,
    )
    untargeted = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=fixture.request,
        samples=fixture.samples,
        evidence_providers=(provider,),
        declared_candidate_ids=("candidate-b",),
        capture_candidate_ids=("candidate-b",),
        method_context_id="synthetic-cid-nl",
    )

    assert scope_allows_evidence_inspection(targeted, "candidate-b") is True
    with pytest.raises(SearchScopeAuthorizationError, match="Target capture"):
        require_capture_candidate(targeted, "candidate-b")
    with pytest.raises(SearchScopeAuthorizationError, match="not declared"):
        require_capture_candidate(untargeted, "candidate-a")
    assert require_capture_candidate(untargeted, "candidate-b") == "candidate-b"


def _synthetic_peak_finder(
    rt: np.ndarray,
    intensity: np.ndarray,
    _config: ExtractionConfig,
    **_kwargs: object,
) -> PeakDetectionResult:
    area = integrate_area_counts_seconds(intensity, rt, 1, 6)
    peak = PeakResult(
        rt=8.0,
        intensity=100.0,
        intensity_smoothed=100.0,
        area=area,
        peak_start=7.8,
        peak_end=8.2,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=8.0,
        selection_apex_intensity=100.0,
        selection_apex_index=3,
        raw_apex_rt=8.0,
        raw_apex_intensity=100.0,
        raw_apex_index=3,
        prominence=90.0,
        proposal_sources=("synthetic",),
        source_apex_rank=1,
    )
    return PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=len(rt),
        max_smoothed=100.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )


def _peak_config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "unused.csv",
        diagnostics_csv=tmp_path / "unused-diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
    )
