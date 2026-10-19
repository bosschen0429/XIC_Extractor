import inspect
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_support_policy,
    cid_nl_unique_support,
)
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection import capture as capture_owner
from xic_extractor.peak_detection import search_scope as scope_owner
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    REQUEST_INPUT_KEY,
    CidNlMethodSubjectContractError,
    cid_nl_alignment_subject_worksets,
    cid_nl_method_subject_from_request,
    cid_nl_method_subject_from_target,
    mint_untargeted_cid_nl_method_subject,
    mint_untargeted_cid_nl_method_subject_batch,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    SearchScopeAuthorizationError,
    SearchScopeContractError,
    TargetSearchScope,
    UntargetedSearchScope,
    require_capture_binding,
    scope_allows_evidence_inspection,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    build_processing_request_identity,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


def test_no_raw_scopes_share_capture_observation_and_hypothesis_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "targeted").mkdir()
    (tmp_path / "untargeted").mkdir()
    targeted_fixture = build_processing_identity_no_raw_fixture(
        tmp_path / "targeted",
        sample_stems=("S1",),
    )
    untargeted_fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path / "untargeted",
        sample_roles={"S1": "qc"},
    )
    targeted_subject = cid_nl_method_subject_from_target(targeted_fixture.target)
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
    peak_config = _peak_config(tmp_path)
    rt = np.linspace(7.7, 8.3, 61)
    intensity = 100.0 * np.exp(-0.5 * ((rt - 8.0) / 0.06) ** 2)
    targeted_binding = _capture_binding(
        candidate_id="candidate-a",
        method_subject_id=targeted_subject.method_subject_id,
        sample=targeted_sample,
        acquisition_source_id=targeted_source.acquisition_source_id,
        mz=targeted_subject.precursor_mz,
        ppm_tol=targeted_subject.precursor_tolerance_ppm,
        rt_min=float(rt[0]),
        rt_max=float(rt[-1]),
    )
    untargeted_binding = _capture_binding(
        candidate_id="candidate-a",
        method_subject_id=untargeted_fixture.method_subject.method_subject_id,
        sample=untargeted_sample,
        acquisition_source_id=untargeted_source.acquisition_source_id,
        mz=untargeted_fixture.method_subject.precursor_mz,
        ppm_tol=untargeted_fixture.method_subject.precursor_tolerance_ppm,
        rt_min=float(rt[0]),
        rt_max=float(rt[-1]),
    )
    with pytest.raises(
        SearchScopeContractError,
        match="outside its query RT interval",
    ):
        replace(untargeted_binding, preferred_rt=9.5)
    targeted = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=targeted_fixture.request,
        samples=targeted_fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=peak_config,
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=targeted_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=("candidate-a", "candidate-b"),
        capture_bindings=(targeted_binding,),
        target_snapshot=targeted_fixture.target.snapshot,
        target=targeted_fixture.target,
    )
    untargeted = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=untargeted_fixture.owners.request,
        samples=untargeted_fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=peak_config,
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=untargeted_fixture.method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=("candidate-a", "candidate-b"),
        capture_bindings=(
            untargeted_binding,
            replace(untargeted_binding, candidate_id="candidate-b"),
        ),
    )
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
    targeted_capture = capture_owner.capture_peak_hypotheses(
        scope=targeted,
        candidate_id="candidate-a",
        trace=targeted_trace,
    )
    untargeted_capture = capture_owner.capture_peak_hypotheses(
        scope=untargeted,
        candidate_id="candidate-a",
        trace=untargeted_trace,
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
    )
    untargeted_observation = capture_owner.mint_captured_peak_observation(
        untargeted_capture,
        processing_owners=untargeted_fixture.owners,
        sample=untargeted_sample,
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
        require_capture_binding(targeted, "candidate-b")
    with pytest.raises(SearchScopeAuthorizationError, match="Target capture"):
        capture_owner.capture_peak_hypotheses(
            scope=targeted,
            candidate_id="candidate-b",
            trace=targeted_trace,
        )
    assert require_capture_binding(untargeted, "candidate-b").candidate_id == (
        "candidate-b"
    )

    child_scope = replace(
        untargeted,
        declared_candidate_ids=("candidate-a",),
        capture_bindings=(untargeted_binding,),
    )

    def unexpected_detection(*args, **kwargs):
        pytest.fail("scope changes must not repeat sample-local peak detection")

    with monkeypatch.context() as patch:
        patch.setattr(capture_owner, "detect_ms1_trace_peak", unexpected_detection)
        rebound = capture_owner.rebind_untargeted_capture_scope(
            untargeted_capture, scope=child_scope
        )
    assert rebound.scope is child_scope
    assert rebound.trace is untargeted_capture.trace
    assert rebound.selected_hypothesis is untargeted_capture.selected_hypothesis
    assert (
        capture_owner.mint_captured_peak_observation(
            rebound,
            processing_owners=untargeted_fixture.owners,
            sample=untargeted_sample,
        )
        == untargeted_observation
    )
    for changed in (
        replace(
            child_scope,
            capture_bindings=(replace(untargeted_binding, preferred_rt=8.1),),
        ),
        replace(
            child_scope,
            method_constraints=replace(
                child_scope.method_constraints,
                peak_config=replace(peak_config, smooth_window=17),
            ),
        ),
    ):
        with pytest.raises(
            capture_owner.SampleLocalCaptureError, match="capture question"
        ):
            capture_owner.rebind_untargeted_capture_scope(
                untargeted_capture, scope=changed
            )
    with pytest.raises(capture_owner.SampleLocalCaptureError, match="Untargeted"):
        capture_owner.rebind_untargeted_capture_scope(
            targeted_capture, scope=child_scope
        )


def test_capture_binds_trace_coordinates_to_both_scope_variants(
    tmp_path: Path,
) -> None:
    (tmp_path / "targeted").mkdir()
    (tmp_path / "untargeted").mkdir()
    targeted_fixture = build_processing_identity_no_raw_fixture(
        tmp_path / "targeted",
        sample_stems=("S1",),
    )
    untargeted_fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path / "untargeted",
        sample_roles={"S1": "qc"},
    )
    targeted_subject = cid_nl_method_subject_from_target(targeted_fixture.target)
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
    peak_config = _peak_config(tmp_path)
    rt = np.linspace(7.7, 8.3, 61)
    intensity = 100.0 * np.exp(-0.5 * ((rt - 8.0) / 0.06) ** 2)
    half_tolerance_mz = untargeted_fixture.method_subject.precursor_mz * (
        1.0 + untargeted_fixture.method_subject.precursor_tolerance_ppm / 2_000_000.0
    )
    targeted_binding = _capture_binding(
        candidate_id="candidate-a",
        method_subject_id=targeted_subject.method_subject_id,
        sample=targeted_sample,
        acquisition_source_id=targeted_source.acquisition_source_id,
        mz=targeted_subject.precursor_mz,
        ppm_tol=targeted_subject.precursor_tolerance_ppm,
        rt_min=float(rt[0]),
        rt_max=float(rt[-1]),
    )
    untargeted_binding = _capture_binding(
        candidate_id="candidate-a",
        method_subject_id=untargeted_fixture.method_subject.method_subject_id,
        sample=untargeted_sample,
        acquisition_source_id=untargeted_source.acquisition_source_id,
        mz=half_tolerance_mz,
        ppm_tol=untargeted_fixture.method_subject.precursor_tolerance_ppm,
        rt_min=float(rt[0]),
        rt_max=float(rt[-1]),
    )
    targeted_scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=targeted_fixture.request,
        samples=targeted_fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=peak_config,
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=targeted_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=("candidate-a",),
        capture_bindings=(targeted_binding,),
        target_snapshot=targeted_fixture.target.snapshot,
        target=targeted_fixture.target,
    )
    untargeted_subject = untargeted_fixture.method_subject
    untargeted_scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=untargeted_fixture.owners.request,
        samples=untargeted_fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=peak_config,
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=untargeted_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=("candidate-a",),
        capture_bindings=(untargeted_binding,),
    )
    outside_hint_scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=targeted_fixture.request,
        samples=targeted_fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=peak_config,
        ),
        evidence_providers=targeted_scope.evidence_providers,
        declared_candidate_ids=("candidate-a",),
        capture_bindings=(
            replace(
                targeted_binding,
                query_rt_min=7.0,
                query_rt_max=9.5,
                preferred_rt=9.0,
            ),
        ),
        target_snapshot=targeted_fixture.target.snapshot,
        target=targeted_fixture.target,
    )
    assert outside_hint_scope.capture_bindings[0].preferred_rt == 9.0
    targeted_trace = Trace.from_arrays(
        sample_name=targeted_sample.metadata.sample_name,
        mz=targeted_subject.precursor_mz,
        rt=rt,
        intensity=intensity,
        rt_min=float(rt[0]),
        rt_max=float(rt[-1]),
        ppm_tol=targeted_subject.precursor_tolerance_ppm,
        source="synthetic_scope_coordinate_oracle",
        acquisition_source_id=targeted_source.acquisition_source_id,
    )
    untargeted_trace = Trace.from_arrays(
        sample_name=untargeted_sample.metadata.sample_name,
        mz=half_tolerance_mz,
        rt=rt,
        intensity=intensity,
        rt_min=float(rt[0]),
        rt_max=float(rt[-1]),
        ppm_tol=untargeted_subject.precursor_tolerance_ppm,
        source="synthetic_scope_coordinate_oracle",
        acquisition_source_id=untargeted_source.acquisition_source_id,
    )
    accepted = capture_owner.capture_peak_hypotheses(
        scope=untargeted_scope,
        candidate_id="candidate-a",
        trace=untargeted_trace,
    )
    assert accepted.trace.mz == half_tolerance_mz

    invalid_bindings = (
        (targeted_scope, replace(targeted_trace, mz=targeted_trace.mz + 100.0)),
        (targeted_scope, replace(targeted_trace, ppm_tol=targeted_trace.ppm_tol + 1.0)),
        (targeted_scope, replace(targeted_trace, rt_min=targeted_trace.rt_min + 0.1)),
        (targeted_scope, replace(targeted_trace, rt_max=targeted_trace.rt_max - 0.1)),
        (targeted_scope, replace(targeted_trace, sample_name="different-sample")),
        (
            targeted_scope,
            replace(targeted_trace, acquisition_source_id="different-source"),
        ),
        (targeted_scope, replace(targeted_trace, mz=float("nan"))),
        (targeted_scope, replace(targeted_trace, ppm_tol=float("inf"))),
        (untargeted_scope, replace(untargeted_trace, mz=untargeted_trace.mz + 1.0)),
        (
            untargeted_scope,
            replace(untargeted_trace, ppm_tol=untargeted_trace.ppm_tol + 1.0),
        ),
        (untargeted_scope, replace(untargeted_trace, mz=float("inf"))),
        (untargeted_scope, replace(untargeted_trace, ppm_tol=float("nan"))),
    )
    for scope, trace in invalid_bindings:
        with pytest.raises(
            capture_owner.SampleLocalCaptureError,
            match="trace coordinates",
        ):
            capture_owner.capture_peak_hypotheses(
                scope=scope,
                candidate_id="candidate-a",
                trace=trace,
            )


def test_crossed_scope_candidate_authorization_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "targeted").mkdir()
    (tmp_path / "untargeted").mkdir()
    targeted_fixture = build_processing_identity_no_raw_fixture(
        tmp_path / "targeted",
        sample_stems=("S1",),
    )
    untargeted_fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path / "untargeted",
        sample_roles={"S1": "qc"},
    )
    targeted_subject = cid_nl_method_subject_from_target(targeted_fixture.target)
    peak_config = _peak_config(tmp_path)
    untargeted_sample = untargeted_fixture.sample("S1")
    untargeted_source = resolve_request_acquisition_source(
        processing_owners=untargeted_fixture.owners,
        sample=untargeted_sample,
    )
    untargeted_binding = _capture_binding(
        candidate_id="candidate-b",
        method_subject_id=untargeted_fixture.method_subject.method_subject_id,
        sample=untargeted_sample,
        acquisition_source_id=untargeted_source.acquisition_source_id,
        mz=untargeted_fixture.method_subject.precursor_mz,
        ppm_tol=untargeted_fixture.method_subject.precursor_tolerance_ppm,
        rt_min=7.7,
        rt_max=8.3,
    )
    targeted = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=targeted_fixture.request,
        samples=targeted_fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="targeted_gaussian15",
            peak_config=peak_config,
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=targeted_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=("candidate-a", "candidate-b"),
        capture_bindings=(),
        target_snapshot=targeted_fixture.target.snapshot,
        target=targeted_fixture.target,
    )
    untargeted = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=untargeted_fixture.owners.request,
        samples=untargeted_fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=peak_config,
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=untargeted_fixture.method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=("candidate-b",),
        capture_bindings=(untargeted_binding,),
    )

    assert scope_allows_evidence_inspection(targeted, "candidate-b") is True
    with pytest.raises(SearchScopeAuthorizationError, match="Target capture"):
        require_capture_binding(targeted, "candidate-b")
    with pytest.raises(SearchScopeAuthorizationError, match="not declared"):
        require_capture_binding(untargeted, "candidate-a")
    assert require_capture_binding(untargeted, "candidate-b").candidate_id == (
        "candidate-b"
    )


@pytest.mark.parametrize("query_offset", (0.0, 0.001))
def test_untargeted_batch_scope_uses_exact_candidate_subject_membership(
    tmp_path: Path,
    query_offset: float,
) -> None:
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "qc", "S2": "qc"},
    )
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=243.098,
                    precursor_tolerance_ppm=10.0,
                    neutral_loss_da=fixture.method_subject.neutral_loss_da,
                ),
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=243.099,
                    precursor_tolerance_ppm=10.0,
                    neutral_loss_da=fixture.method_subject.neutral_loss_da,
                ),
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    subject_by_mz = {subject.precursor_mz: subject for subject in subjects}
    memberships = (
        ("candidate-a", subject_by_mz[243.098].method_subject_id),
        ("candidate-b", subject_by_mz[243.099].method_subject_id),
    )
    batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=memberships,
    )
    base_request = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=base_request.acquisition_sources,
        acquisition_bindings=base_request.acquisition_bindings,
        scientific_input_schema_version="untargeted-cid-nl-batch-test",
        processing_algorithm_version=base_request.processing_algorithm_version,
        processing_contract_version=base_request.processing_contract_version,
        scientific_inputs={REQUEST_INPUT_KEY: batch.request_payload()},
    )
    sample = fixture.sample("S1")
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=sample,
    )
    subjects_by_id = {subject.method_subject_id: subject for subject in subjects}
    bindings = tuple(
        _capture_binding(
            candidate_id=candidate_id,
            method_subject_id=method_subject_id,
            sample=sample,
            acquisition_source_id=source.acquisition_source_id,
            mz=subjects_by_id[method_subject_id].precursor_mz + query_offset,
            ppm_tol=subjects_by_id[method_subject_id].precursor_tolerance_ppm,
            rt_min=7.7,
            rt_max=8.3,
        )
        for candidate_id, method_subject_id in memberships
    )
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=request,
        samples=(sample,),
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=_peak_config(tmp_path),
        ),
        evidence_providers=tuple(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=subject.method_subject_id,
            )
            for subject in subjects
        ),
        declared_candidate_ids=("candidate-a", "candidate-b"),
        capture_bindings=bindings,
    )
    assert scope.capture_bindings == bindings
    subject_view = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=request,
        samples=(sample,),
        method_constraints=scope.method_constraints,
        evidence_providers=(scope.evidence_providers[0],),
        declared_candidate_ids=(
            next(
                candidate_id
                for candidate_id, method_subject_id in memberships
                if method_subject_id == scope.evidence_providers[0].method_subject_id
            ),
        ),
        capture_bindings=tuple(
            binding
            for binding in bindings
            if binding.method_subject_id
            == scope.evidence_providers[0].method_subject_id
        ),
    )
    assert len(subject_view.capture_bindings) == 1
    for invalid_provider in (
        replace(subject_view.evidence_providers[0], provider_id="other-provider"),
        replace(subject_view.evidence_providers[0], method_subject_id="other-subject"),
    ):
        with pytest.raises(SearchScopeContractError, match="evidence providers"):
            replace(subject_view, evidence_providers=(invalid_provider,))
    view_binding = subject_view.capture_bindings[0]
    other_binding = next(
        binding
        for binding in bindings
        if binding.candidate_id != view_binding.candidate_id
    )
    universe_samples = [
        {
            "sample_stem": sample.metadata.raw_stem,
            "candidate_ids": [view_binding.candidate_id],
        },
        {"sample_stem": "S2", "candidate_ids": [other_binding.candidate_id]},
    ]
    sample_request = build_processing_request_identity(
        acquisition_sources=request.acquisition_sources,
        acquisition_bindings=request.acquisition_bindings,
        scientific_input_schema_version=request.scientific_input_schema_version,
        processing_algorithm_version=request.processing_algorithm_version,
        processing_contract_version=request.processing_contract_version,
        scientific_inputs={
            REQUEST_INPUT_KEY: batch.request_payload(),
            "current_discovery_candidate_universe": {"samples": universe_samples},
            "alignment_feature_candidate_ids": [item[0] for item in memberships],
        },
    )
    feature_visits = 0

    class CountedFeatures(list):
        def __iter__(self):
            nonlocal feature_visits
            for value in super().__iter__():
                feature_visits += 1
                yield value

    request_inputs = scope_owner._request_inputs(sample_request)
    request_inputs["alignment_feature_candidate_ids"] = CountedFeatures(
        request_inputs["alignment_feature_candidate_ids"]
    )
    with pytest.raises(SearchScopeContractError, match="request subject memberships"):
        replace(scope, request=sample_request)
    assert replace(subject_view, request=sample_request).capture_bindings == (
        view_binding,
    )
    other_sample = fixture.sample("S2")
    other_source = resolve_request_acquisition_source(
        processing_owners=fixture.owners, sample=other_sample
    )
    other_binding = replace(
        other_binding,
        sample_identity_id=other_sample.identity.sample_identity_id,
        sample_name=other_sample.metadata.sample_name,
        acquisition_source_id=other_source.acquisition_source_id,
    )
    prepared_feature_visits = feature_visits
    for _ in range(2):
        assert replace(
            scope,
            request=sample_request,
            samples=(other_sample,),
            declared_candidate_ids=(other_binding.candidate_id,),
            capture_bindings=(other_binding,),
        ).capture_bindings == (other_binding,)
        assert replace(subject_view, request=sample_request).capture_bindings == (
            view_binding,
        )
    assert prepared_feature_visits > 0
    assert feature_visits == prepared_feature_visits
    malformed_request = build_processing_request_identity(
        acquisition_sources=request.acquisition_sources,
        acquisition_bindings=request.acquisition_bindings,
        scientific_input_schema_version=request.scientific_input_schema_version,
        processing_algorithm_version=request.processing_algorithm_version,
        processing_contract_version=request.processing_contract_version,
        scientific_inputs={
            REQUEST_INPUT_KEY: batch.request_payload(),
            "current_discovery_candidate_universe": {
                "samples": [
                    universe_samples[0],
                    {"sample_stem": "S2", "candidate_ids": [None]},
                ]
            },
        },
    )
    assert replace(subject_view, request=malformed_request).capture_bindings == (
        view_binding,
    )
    with pytest.raises(SearchScopeContractError, match="universe is malformed"):
        replace(
            scope,
            request=malformed_request,
            samples=(other_sample,),
            declared_candidate_ids=(other_binding.candidate_id,),
            capture_bindings=(other_binding,),
        )
    # Returning to the full request must restore its complete candidate authority.
    assert replace(scope).capture_bindings == bindings
    with pytest.raises(SearchScopeContractError, match="request subject memberships"):
        replace(
            scope, declared_candidate_ids=tuple(reversed(scope.declared_candidate_ids))
        )
    with pytest.raises(SearchScopeContractError, match="declared candidate order"):
        replace(scope, capture_bindings=tuple(reversed(bindings)))
    rt = np.linspace(7.7, 8.3, 61)
    trace = Trace.from_arrays(
        sample_name=sample.metadata.sample_name,
        mz=view_binding.query_mz,
        rt=rt,
        intensity=100.0 * np.exp(-0.5 * ((rt - 8.0) / 0.06) ** 2),
        rt_min=view_binding.query_rt_min,
        rt_max=view_binding.query_rt_max,
        ppm_tol=view_binding.query_ppm_tol,
        source="synthetic_batch_subject_view",
        acquisition_source_id=source.acquisition_source_id,
    )
    capture = capture_owner.capture_peak_hypotheses(
        scope=subject_view,
        candidate_id=view_binding.candidate_id,
        trace=trace,
    )
    assert capture.trace_group.context_id.startswith("untargeted:")
    for substitution in (
        {"query_mz": bindings[0].query_mz + 0.01},
        {"query_ppm_tol": bindings[0].query_ppm_tol + 1.0},
    ):
        with pytest.raises(SearchScopeContractError, match="bound subject"):
            replace(
                scope,
                capture_bindings=(replace(bindings[0], **substitution), bindings[1]),
            )
    with pytest.raises(
        SearchScopeContractError,
        match="candidate subject membership",
    ):
        replace(
            scope,
            capture_bindings=(
                replace(
                    bindings[0],
                    method_subject_id=memberships[1][1],
                    query_mz=subject_by_mz[243.099].precursor_mz,
                ),
                bindings[1],
            ),
        )


def test_untargeted_batch_scope_accepts_known_comparison_bridged_workset(
    tmp_path: Path,
) -> None:
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={"S1": "qc"},
    )
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=precursor_mz,
                    precursor_tolerance_ppm=10.0,
                    neutral_loss_da=neutral_loss_da,
                )
                for precursor_mz, neutral_loss_da in (
                    (243.098, 116.0474),
                    (243.2, 116.0474),
                    (244.0, 116.0474),
                    (243.0985, 114.0),
                )
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    subject_by_mz_loss = {
        (subject.precursor_mz, subject.neutral_loss_da): subject for subject in subjects
    }
    memberships = tuple(
        (
            candidate_id,
            subject_by_mz_loss[(precursor_mz, neutral_loss_da)].method_subject_id,
        )
        for candidate_id, precursor_mz, neutral_loss_da in (
            ("candidate-a", 243.098, 116.0474),
            ("candidate-b", 243.2, 116.0474),
            ("candidate-c", 244.0, 116.0474),
            ("candidate-d", 243.0985, 114.0),
        )
    )
    batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=memberships,
    )
    base_request = fixture.owners.request
    request = build_processing_request_identity(
        acquisition_sources=base_request.acquisition_sources,
        acquisition_bindings=base_request.acquisition_bindings,
        scientific_input_schema_version="untargeted-cid-nl-workset-test",
        processing_algorithm_version=base_request.processing_algorithm_version,
        processing_contract_version=base_request.processing_contract_version,
        scientific_inputs={REQUEST_INPUT_KEY: batch.request_payload()},
    )
    sample = fixture.sample("S1")
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=sample,
    )
    subjects_by_id = {subject.method_subject_id: subject for subject in subjects}
    bindings = tuple(
        _capture_binding(
            candidate_id=candidate_id,
            method_subject_id=method_subject_id,
            sample=sample,
            acquisition_source_id=source.acquisition_source_id,
            mz=subjects_by_id[method_subject_id].precursor_mz,
            ppm_tol=subjects_by_id[method_subject_id].precursor_tolerance_ppm,
            rt_min=7.7,
            rt_max=8.3,
        )
        for candidate_id, method_subject_id in memberships
    )
    parent_scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=request,
        samples=(sample,),
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=_peak_config(tmp_path),
        ),
        evidence_providers=tuple(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=subject.method_subject_id,
            )
            for subject in subjects
        ),
        declared_candidate_ids=tuple(candidate_id for candidate_id, _ in memberships),
        capture_bindings=bindings,
    )
    workset_subject_ids = {
        subject_by_mz_loss[(243.098, 116.0474)].method_subject_id,
        subject_by_mz_loss[(243.2, 116.0474)].method_subject_id,
    }
    assert not any(
        {subject.method_subject_id for subject in workset} == workset_subject_ids
        for workset in cid_nl_alignment_subject_worksets(subjects)
    )
    bridged_worksets = cid_nl_alignment_subject_worksets(
        subjects,
        comparison_mz_intervals=((243.0, 243.3),),
    )
    assert any(
        {subject.method_subject_id for subject in workset} == workset_subject_ids
        for workset in bridged_worksets
    )
    workset_providers = tuple(
        provider
        for provider in parent_scope.evidence_providers
        if provider.method_subject_id in workset_subject_ids
    )
    workset_bindings = tuple(
        binding
        for binding in bindings
        if binding.method_subject_id in workset_subject_ids
    )
    # The parent owner has already validated and indexed its immutable population.
    # Instrument iteration without changing any values to reject per-child rescans.
    visited = {"subjects": 0, "memberships": 0}

    class CountedTuple(tuple):
        def __new__(cls, values, field):
            result = super().__new__(cls, values)
            result.field = field
            return result

        def __iter__(self):
            for value in super().__iter__():
                visited[self.field] += 1
                yield value

    index = scope_owner._request_batch_capture_index(request)
    pinned = scope_owner.cid_nl_method_subjects_from_request(request)
    parent_subject_ids = index.subject_ids
    parent_subjects = pinned.subjects
    parent_memberships = pinned.candidate_memberships
    object.__setattr__(
        index, "subject_ids", CountedTuple(parent_subject_ids, "subjects")
    )
    object.__setattr__(pinned, "subjects", CountedTuple(parent_subjects, "subjects"))
    object.__setattr__(
        pinned, "candidate_memberships", CountedTuple(parent_memberships, "memberships")
    )
    try:
        workset_scope = replace(
            parent_scope,
            evidence_providers=workset_providers,
            declared_candidate_ids=tuple(
                binding.candidate_id for binding in workset_bindings
            ),
            capture_bindings=workset_bindings,
        )
        for binding in workset_bindings:
            subject = cid_nl_method_subject_from_request(
                request, method_subject_id=binding.method_subject_id
            )
            label, role, pair, source_label = capture_owner._hypothesis_context(
                workset_scope,
                query_mz=binding.query_mz,
                method_subject_id=binding.method_subject_id,
            )
            assert label == f"untargeted:{binding.query_mz:.8g}:{subject.label}"
            assert (role, pair, source_label) == ("Analyte", "", label)
        with pytest.raises(CidNlMethodSubjectContractError, match="not pinned"):
            cid_nl_method_subject_from_request(request, method_subject_id="missing")
    finally:
        object.__setattr__(index, "subject_ids", parent_subject_ids)
        object.__setattr__(pinned, "subjects", parent_subjects)
        object.__setattr__(pinned, "candidate_memberships", parent_memberships)

    assert visited == {"subjects": 0, "memberships": 0}

    assert len(workset_scope.evidence_providers) == 2
    assert len(workset_scope.evidence_providers) < len(parent_scope.evidence_providers)
    assert workset_scope.capture_bindings == workset_bindings

    def providers_for(
        subject_keys: set[tuple[float, float]],
    ) -> tuple[EvidenceProviderBinding, ...]:
        subject_ids = {
            subject_by_mz_loss[subject_key].method_subject_id
            for subject_key in subject_keys
        }
        return tuple(
            provider
            for provider in parent_scope.evidence_providers
            if provider.method_subject_id in subject_ids
        )

    foreign_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=500.0,
        precursor_tolerance_ppm=10.0,
        neutral_loss_da=116.0474,
    )
    invalid_provider_cases = (
        (tuple(reversed(workset_providers)), "exact compatible subject workset"),
        (
            (workset_providers[0], workset_providers[0]),
            "duplicate evidence providers",
        ),
        (
            providers_for({(243.098, 116.0474), (243.0985, 114.0)}),
            "exact compatible subject workset",
        ),
        (
            (
                EvidenceProviderBinding(
                    provider_id=CID_NL_PROVIDER_ID,
                    method_subject_id=foreign_subject.method_subject_id,
                ),
            ),
            "exact compatible subject workset",
        ),
    )
    for invalid_providers, message in invalid_provider_cases:
        with pytest.raises(
            SearchScopeContractError,
            match=message,
        ):
            replace(workset_scope, evidence_providers=invalid_providers)


def test_scope_rejects_provider_not_bound_to_variant_definition(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("S1",))
    method_subject = cid_nl_method_subject_from_target(fixture.target)

    with pytest.raises(SearchScopeContractError, match="evidence provider"):
        TargetSearchScope(
            schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
            request=fixture.request,
            samples=fixture.samples,
            method_constraints=CaptureMethodConstraints(
                boundary_policy="targeted_gaussian15",
                peak_config=_peak_config(tmp_path),
            ),
            evidence_providers=(
                EvidenceProviderBinding(
                    provider_id="caller_authored_provider",
                    method_subject_id=method_subject.method_subject_id,
                ),
            ),
            declared_candidate_ids=("candidate-a",),
            capture_bindings=(),
            target_snapshot=fixture.target.snapshot,
            target=fixture.target,
        )


def test_owner_binding_rejects_observation_from_other_capture_trace(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("S1",))
    sample = fixture.sample("S1")
    method_subject = cid_nl_method_subject_from_target(fixture.target)
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=sample,
    )
    rt = np.linspace(7.7, 8.3, 61)
    intensity = 100.0 * np.exp(-0.5 * ((rt - 8.0) / 0.06) ** 2)
    binding = _capture_binding(
        candidate_id="candidate-a",
        method_subject_id=method_subject.method_subject_id,
        sample=sample,
        acquisition_source_id=source.acquisition_source_id,
        mz=method_subject.precursor_mz,
        ppm_tol=method_subject.precursor_tolerance_ppm,
        rt_min=float(rt[0]),
        rt_max=float(rt[-1]),
    )
    scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=fixture.request,
        samples=fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=_peak_config(tmp_path),
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=(binding.candidate_id,),
        capture_bindings=(binding,),
        target_snapshot=fixture.target.snapshot,
        target=fixture.target,
    )

    def captured(source_name: str):
        return capture_owner.capture_peak_hypotheses(
            scope=scope,
            candidate_id=binding.candidate_id,
            trace=Trace.from_arrays(
                sample_name=sample.metadata.sample_name,
                mz=binding.query_mz,
                rt=rt,
                intensity=intensity,
                rt_min=binding.query_rt_min,
                rt_max=binding.query_rt_max,
                ppm_tol=binding.query_ppm_tol,
                source=source_name,
                acquisition_source_id=source.acquisition_source_id,
            ),
        )

    source_capture = captured("source-capture")
    rebound_capture = captured("rebound-capture")
    observation = capture_owner.mint_captured_peak_observation(
        source_capture,
        processing_owners=fixture.owners,
        sample=sample,
    )
    evidence = cid_nl_evidence(
        fixture,
        "S1",
        scan_number=101,
        scan_rt_min=observation.content.integration.rt_apex_min,
    )
    support = cid_nl_unique_support(
        fixture,
        "S1",
        peak=observation,
        evidence=evidence,
    )
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id="gaussian15:S1:trace-binding",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.5,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=1,
            reason_code=None,
        ),
        candidate_peaks=(observation,),
        evidence_refs=(evidence,),
        individual_support_refs=(support,),
    )
    hypothesis = compose_cid_nl_peak_hypotheses_from_assessment(
        processing_owners=fixture.owners,
        sample=sample,
        assessment=assessment,
    )[0]
    assert rebound_capture.selected_hypothesis is not None
    assert (
        rebound_capture.selected_hypothesis.integration
        == source_capture.selected_hypothesis.integration
    )

    with pytest.raises(
        capture_owner.SampleLocalCaptureError,
        match="captured physical selection",
    ):
        capture_owner.bind_owner_backed_capture_hypothesis(
            rebound_capture,
            observation=observation,
            hypothesis=hypothesis,
        )


@pytest.mark.parametrize("ripple", [False, True])
def test_physical_capture_reuses_owner_evaluation_across_mint_and_bind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ripple: bool,
) -> None:
    import json

    from xic_extractor.peak_detection import physical_peak_evaluation as physical_owner
    from xic_extractor.peak_detection import physical_peak_identity as physical_identity
    from xic_extractor.peak_detection.peak_hypothesis_identity import (
        compose_ms1_observation_peak_hypothesis,
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
    )
    sample = fixture.sample("S1")
    method_subject = fixture.method_subject
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=sample,
    )
    if ripple:
        rt = np.linspace(5.0, 11.0, 601)
        intensity = (
            300.0
            + 12000.0 * np.exp(-0.5 * ((rt - 8.0) / 0.8) ** 2)
            + np.random.default_rng(479).uniform(-150.0, 150.0, rt.size)
        )
    else:
        rt = np.linspace(7.7, 8.3, 61)
        intensity = 100.0 * np.exp(-0.5 * ((rt - 8.0) / 0.06) ** 2)
    profile = SignalProcessingProfile(window_points=15)
    prepared = prepare_physical_signal(rt, intensity, profile=profile)
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id=method_subject.method_subject_id,
        query_rt_min=8.0,
        request_context_rt_start_min=float(rt[0]),
        request_context_rt_end_min=float(rt[-1]),
    )
    binding = replace(
        _capture_binding(
            candidate_id="candidate-a",
            method_subject_id=method_subject.method_subject_id,
            sample=sample,
            acquisition_source_id=source.acquisition_source_id,
            mz=method_subject.precursor_mz,
            ppm_tol=method_subject.precursor_tolerance_ppm,
            rt_min=float(rt[0]),
            rt_max=float(rt[-1]),
        ),
        physical_evaluation=evaluation,
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
                {binding.candidate_id: evaluation},
            )
        },
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=fixture.owners.sample_snapshot,
        sample_resolutions=fixture.samples,
    )
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=request,
        samples=fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=_peak_config(tmp_path),
            signal_processing_profile=profile,
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=(binding.candidate_id,),
        capture_bindings=(binding,),
    )
    trace_hashes = 0
    projections = 0
    evaluation_calls = 0
    real_trace_hash = physical_owner.mint_physical_trace_content_id
    real_projection = capture_owner._project_peak_observation_values
    real_evaluation = capture_owner.evaluate_physical_peak

    def count_trace_hash(*args, **kwargs):
        nonlocal trace_hashes
        trace_hashes += 1
        return real_trace_hash(*args, **kwargs)

    def count_projection(**kwargs):
        nonlocal projections
        projections += 1
        return real_projection(**kwargs)

    def count_evaluation(*args, **kwargs):
        nonlocal evaluation_calls
        evaluation_calls += 1
        return real_evaluation(*args, **kwargs)

    monkeypatch.setattr(
        physical_owner,
        "mint_physical_trace_content_id",
        count_trace_hash,
    )
    monkeypatch.setattr(
        physical_identity,
        "mint_physical_trace_content_id",
        count_trace_hash,
    )
    monkeypatch.setattr(
        capture_owner,
        "mint_physical_trace_content_id",
        count_trace_hash,
    )
    monkeypatch.setattr(
        capture_owner,
        "_project_peak_observation_values",
        count_projection,
    )
    monkeypatch.setattr(capture_owner, "evaluate_physical_peak", count_evaluation)
    capture = capture_owner.capture_peak_hypotheses(
        scope=scope,
        candidate_id=binding.candidate_id,
        trace=Trace.from_arrays(
            sample_name=sample.metadata.sample_name,
            mz=binding.query_mz,
            rt=rt,
            intensity=intensity,
            rt_min=binding.query_rt_min,
            rt_max=binding.query_rt_max,
            ppm_tol=binding.query_ppm_tol,
            source="physical-owner",
            acquisition_source_id=source.acquisition_source_id,
        ),
    )
    original_capture = capture

    def unexpected_evaluation(*args, **kwargs):
        pytest.fail("scope changes must not repeat physical preparation or evaluation")

    with monkeypatch.context() as patch:
        patch.setattr(capture_owner, "prepare_physical_signal", unexpected_evaluation)
        patch.setattr(capture_owner, "evaluate_physical_peak", unexpected_evaluation)
        capture = capture_owner.rebind_untargeted_capture_scope(
            capture, scope=replace(scope)
        )
    assert capture.physical_evaluation is original_capture.physical_evaluation
    assert (
        capture._owner_seal.physical_observation_content
        is original_capture._owner_seal.physical_observation_content
    )
    assert (
        capture._owner_seal.physical_observation_provenance
        is original_capture._owner_seal.physical_observation_provenance
    )
    observation = capture_owner.mint_captured_peak_observation(
        capture,
        processing_owners=owners,
        sample=sample,
    )
    assert capture.selected_hypothesis is not None
    owner_hypothesis = compose_ms1_observation_peak_hypothesis(
        processing_owners=owners,
        sample=sample,
        peak_observation=observation,
    )
    bound = capture_owner.bind_owner_backed_capture_hypothesis(
        capture,
        observation=observation,
        hypothesis=owner_hypothesis,
    )

    assert bound.selected_hypothesis is owner_hypothesis
    assert evaluation.facts is not None
    assert owner_hypothesis.integration.rt_apex_min == evaluation.facts.apex_rt_min
    assert (
        owner_hypothesis.integration.area_ms1_morphology
        == evaluation.facts.area_counts_seconds
    )
    if ripple:
        assert owner_hypothesis.integration.rt_left_min <= 6.8
        assert owner_hypothesis.integration.rt_right_min >= 9.2
    assert trace_hashes == 1
    assert projections == 1
    assert evaluation_calls == 0


def test_untargeted_scope_rejects_targeted_request_variant(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("S1",))
    method_subject = cid_nl_method_subject_from_target(fixture.target)

    with pytest.raises(SearchScopeContractError, match="Untargeted"):
        UntargetedSearchScope(
            schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
            request=fixture.request,
            samples=fixture.samples,
            method_constraints=CaptureMethodConstraints(
                boundary_policy="resolver_interval",
                peak_config=_peak_config(tmp_path),
            ),
            evidence_providers=(
                EvidenceProviderBinding(
                    provider_id=CID_NL_PROVIDER_ID,
                    method_subject_id=method_subject.method_subject_id,
                ),
            ),
            declared_candidate_ids=("candidate-a",),
            capture_bindings=(),
        )


def test_observation_seam_does_not_accept_caller_hypothesis_override() -> None:
    capture_parameters = inspect.signature(
        capture_owner.capture_peak_hypotheses
    ).parameters
    observation_parameters = inspect.signature(
        capture_owner.mint_captured_peak_observation
    ).parameters

    assert set(capture_parameters) == {"scope", "candidate_id", "trace"}
    assert set(observation_parameters) == {
        "capture",
        "processing_owners",
        "sample",
    }


def _capture_binding(
    *,
    candidate_id: str,
    method_subject_id: str,
    sample: ResolvedCanonicalSampleIdentity,
    acquisition_source_id: str,
    mz: float,
    ppm_tol: float,
    rt_min: float,
    rt_max: float,
) -> CaptureCandidateBinding:
    return CaptureCandidateBinding(
        candidate_id=candidate_id,
        method_subject_id=method_subject_id,
        sample_identity_id=sample.identity.sample_identity_id,
        sample_name=sample.metadata.sample_name,
        acquisition_source_id=acquisition_source_id,
        query_mz=mz,
        query_rt_min=rt_min,
        query_rt_max=rt_max,
        query_ppm_tol=ppm_tol,
        preferred_rt=8.0,
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
