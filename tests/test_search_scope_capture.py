import inspect
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

from tests.issue175_no_raw_fixture import build_issue175_no_raw_fixture
from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
from xic_extractor.config import ExtractionConfig
from xic_extractor.peak_detection import capture as capture_owner
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    cid_nl_method_subject_from_target,
)
from xic_extractor.peak_detection.integration import integrate_area_counts_seconds
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    SearchScopeAuthorizationError,
    SearchScopeContractError,
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
    (tmp_path / "targeted").mkdir()
    (tmp_path / "untargeted").mkdir()
    targeted_fixture = build_package2_no_raw_fixture(
        tmp_path / "targeted",
        sample_stems=("S1",),
    )
    untargeted_fixture = build_issue175_no_raw_fixture(
        tmp_path / "untargeted",
        sample_roles={"S1": "qc"},
    )
    targeted_subject = cid_nl_method_subject_from_target(targeted_fixture.target)
    targeted = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=targeted_fixture.request,
        samples=targeted_fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=targeted_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=("candidate-a", "candidate-b"),
        capture_candidate_ids=("candidate-a",),
        target_snapshot=targeted_fixture.target.snapshot,
        target=targeted_fixture.target,
    )
    untargeted = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=untargeted_fixture.owners.request,
        samples=untargeted_fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=untargeted_fixture.method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=("candidate-a", "candidate-b"),
        capture_candidate_ids=("candidate-a", "candidate-b"),
    )
    targeted_sample = targeted_fixture.sample("S1")
    targeted_source = resolve_request_acquisition_source(
        processing_owners=targeted_fixture.owners,
        sample=targeted_sample,
    )
    untargeted_sample = untargeted_fixture.sample("S1")
    untargeted_source = resolve_request_acquisition_source(
        processing_owners=untargeted_fixture.owners,
        sample=untargeted_sample,
    )
    rt = np.asarray([7.7, 7.8, 7.9, 8.0, 8.1, 8.2, 8.3])
    intensity = np.asarray([0.0, 10.0, 50.0, 100.0, 50.0, 10.0, 0.0])
    targeted_trace = Trace.from_arrays(
        sample_name=targeted_sample.metadata.sample_name,
        mz=targeted_fixture.target.definition.target.mz,
        rt=rt,
        intensity=intensity,
        rt_min=float(rt[0]),
        rt_max=float(rt[-1]),
        ppm_tol=targeted_fixture.target.definition.target.ppm_tol,
        source="synthetic_scope_oracle",
        acquisition_source_id=targeted_source.acquisition_source_id,
    )
    untargeted_trace = Trace.from_arrays(
        sample_name=untargeted_sample.metadata.sample_name,
        mz=untargeted_fixture.method_subject.precursor_mz,
        rt=rt,
        intensity=intensity,
        rt_min=float(rt[0]),
        rt_max=float(rt[-1]),
        ppm_tol=untargeted_fixture.method_subject.precursor_tolerance_ppm,
        source="synthetic_scope_oracle",
        acquisition_source_id=untargeted_source.acquisition_source_id,
    )
    peak_config = _peak_config(tmp_path)

    targeted_capture = capture_owner.capture_peak_hypotheses(
        scope=targeted,
        candidate_id="candidate-a",
        trace=targeted_trace,
        peak_config=peak_config,
        preferred_rt=8.0,
        peak_finder=_synthetic_peak_finder,
        resolver_mode=peak_config.resolver_mode,
    )
    untargeted_capture = capture_owner.capture_peak_hypotheses(
        scope=untargeted,
        candidate_id="candidate-a",
        trace=untargeted_trace,
        peak_config=peak_config,
        preferred_rt=8.0,
        peak_finder=_synthetic_peak_finder,
        resolver_mode=peak_config.resolver_mode,
    )

    with pytest.raises(
        capture_owner.SampleLocalCaptureError,
        match="issued by the shared capture owner",
    ):
        replace(
            targeted_capture,
            selected_hypothesis=untargeted_capture.selected_hypothesis,
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
        processing_owners=targeted_fixture.owners,
        sample=targeted_sample,
        resolver_mode=peak_config.resolver_mode,
    )
    untargeted_observation = capture_owner.mint_captured_peak_observation(
        untargeted_capture,
        processing_owners=untargeted_fixture.owners,
        sample=untargeted_sample,
        resolver_mode=peak_config.resolver_mode,
    )
    assert targeted_observation.content.mz == untargeted_observation.content.mz
    assert targeted_observation.content.rt == untargeted_observation.content.rt
    assert (
        targeted_observation.content.intensity
        == untargeted_observation.content.intensity
    )
    assert (
        targeted_observation.content.integration
        == untargeted_observation.content.integration
    )
    assert scope_allows_evidence_inspection(targeted, "candidate-b") is True
    with pytest.raises(SearchScopeAuthorizationError, match="Target capture"):
        require_capture_candidate(targeted, "candidate-b")
    with pytest.raises(SearchScopeAuthorizationError, match="Target capture"):
        capture_owner.capture_peak_hypotheses(
            scope=targeted,
            candidate_id="candidate-b",
            trace=targeted_trace,
            peak_config=peak_config,
            preferred_rt=8.0,
            peak_finder=_synthetic_peak_finder,
            resolver_mode=peak_config.resolver_mode,
        )
    assert require_capture_candidate(untargeted, "candidate-b") == "candidate-b"


def test_crossed_scope_candidate_authorization_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "targeted").mkdir()
    (tmp_path / "untargeted").mkdir()
    targeted_fixture = build_package2_no_raw_fixture(
        tmp_path / "targeted",
        sample_stems=("S1",),
    )
    untargeted_fixture = build_issue175_no_raw_fixture(
        tmp_path / "untargeted",
        sample_roles={"S1": "qc"},
    )
    targeted_subject = cid_nl_method_subject_from_target(targeted_fixture.target)
    targeted = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=targeted_fixture.request,
        samples=targeted_fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="targeted_gaussian15",
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=targeted_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=("candidate-a", "candidate-b"),
        capture_candidate_ids=("candidate-a",),
        target_snapshot=targeted_fixture.target.snapshot,
        target=targeted_fixture.target,
    )
    untargeted = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=untargeted_fixture.owners.request,
        samples=untargeted_fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=untargeted_fixture.method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=("candidate-b",),
        capture_candidate_ids=("candidate-b",),
    )

    assert scope_allows_evidence_inspection(targeted, "candidate-b") is True
    with pytest.raises(SearchScopeAuthorizationError, match="Target capture"):
        require_capture_candidate(targeted, "candidate-b")
    with pytest.raises(SearchScopeAuthorizationError, match="not declared"):
        require_capture_candidate(untargeted, "candidate-a")
    assert require_capture_candidate(untargeted, "candidate-b") == "candidate-b"


def test_scope_rejects_provider_not_bound_to_variant_definition(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("S1",))
    method_subject = cid_nl_method_subject_from_target(fixture.target)

    with pytest.raises(SearchScopeContractError, match="evidence provider"):
        TargetSearchScope(
            schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
            request=fixture.request,
            samples=fixture.samples,
            method_constraints=CaptureMethodConstraints(
                boundary_policy="targeted_gaussian15",
            ),
            evidence_providers=(
                EvidenceProviderBinding(
                    provider_id="caller_authored_provider",
                    method_subject_id=method_subject.method_subject_id,
                ),
            ),
            declared_candidate_ids=("candidate-a",),
            capture_candidate_ids=("candidate-a",),
            target_snapshot=fixture.target.snapshot,
            target=fixture.target,
        )


def test_untargeted_scope_rejects_targeted_request_variant(tmp_path: Path) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("S1",))
    method_subject = cid_nl_method_subject_from_target(fixture.target)

    with pytest.raises(SearchScopeContractError, match="Untargeted"):
        UntargetedSearchScope(
            schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
            request=fixture.request,
            samples=fixture.samples,
            method_constraints=CaptureMethodConstraints(
                boundary_policy="resolver_interval",
            ),
            evidence_providers=(
                EvidenceProviderBinding(
                    provider_id=CID_NL_PROVIDER_ID,
                    method_subject_id=method_subject.method_subject_id,
                ),
            ),
            declared_candidate_ids=("candidate-a",),
            capture_candidate_ids=("candidate-a",),
        )


def test_observation_seam_does_not_accept_caller_hypothesis_override() -> None:
    assert "hypothesis" not in inspect.signature(
        capture_owner.mint_captured_peak_observation
    ).parameters


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
