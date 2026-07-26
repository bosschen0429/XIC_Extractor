from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from xic_extractor.peak_detection.hypotheses import (
    IntegrationResult,
    build_peak_hypotheses,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationContractError,
    PeakObservationRef,
    mint_peak_observation_ref,
    validate_peak_observation_binding,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingAttemptIdentity,
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)


@dataclass(frozen=True)
class _IdentityFixture:
    request: ProcessingRequestIdentity
    attempt: ProcessingAttemptIdentity
    sample: ResolvedCanonicalSampleIdentity
    owners: ProcessingOwnerReferences


def test_query_provenance_does_not_change_selected_physical_content(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    narrow_trace = _trace(
        rt=(7.7, 7.9, 8.0, 8.1, 8.3),
        intensity=(0.0, 50.0, 500.0, 50.0, 0.0),
        rt_min=7.7,
        rt_max=8.3,
        acquisition_source_id=_source_id(identity.owners),
    )
    wide_trace = _trace(
        rt=(7.5, 7.7, 7.9, 8.0, 8.1, 8.3, 8.5),
        intensity=(0.0, 0.0, 50.0, 500.0, 50.0, 0.0, 0.0),
        rt_min=7.5,
        rt_max=8.5,
        acquisition_source_id=_source_id(identity.owners),
    )

    narrow = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=narrow_trace,
        integration=_integration(raw_scan_indices=(1, 2, 3)),
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )
    wide = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=wide_trace,
        integration=_integration(raw_scan_indices=(2, 3, 4)),
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )

    assert narrow.content == wide.content
    assert narrow.content.peak_observation_content_id == (
        wide.content.peak_observation_content_id
    )
    assert narrow.generation_provenance != wide.generation_provenance
    assert narrow.artifact.content_identity != wide.artifact.content_identity
    assert narrow.artifact.evidence_artifact_id != wide.artifact.evidence_artifact_id


def test_binding_rejects_sample_and_attempt_substitution(tmp_path: Path) -> None:
    identity = _identity_fixture(tmp_path)
    reference = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=_standard_trace(identity.owners),
        integration=_integration(raw_scan_indices=(1, 2, 3)),
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )
    retry = issue_processing_attempt(identity.request, retry_of=identity.attempt)
    changed_request = build_processing_request_identity(
        acquisition_sources=identity.request.acquisition_sources,
        acquisition_bindings=identity.request.acquisition_bindings,
        scientific_input_schema_version=(
            identity.request.scientific_input_schema_version
        ),
        processing_algorithm_version=identity.request.processing_algorithm_version,
        processing_contract_version=identity.request.processing_contract_version,
        scientific_inputs={"fixture": "changed-synthetic-input"},
    )
    foreign_attempt = issue_processing_attempt(changed_request)
    retry_owners = _processing_owners(identity.request, retry, identity.sample)
    foreign_owners = _processing_owners(
        changed_request,
        foreign_attempt,
        identity.sample,
    )
    other_snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name="Sample B", raw_stem="sample-b"),),
        identity_namespace="study:peak-observation",
    )
    other_sample = resolve_canonical_sample_identity(other_snapshot, "sample-b")

    validate_peak_observation_binding(
        reference,
        processing_owners=identity.owners,
        sample=identity.sample,
    )
    with pytest.raises(
        PeakObservationContractError, match="different processing attempt"
    ):
        validate_peak_observation_binding(
            reference,
            processing_owners=retry_owners,
            sample=identity.sample,
        )
    with pytest.raises(PeakObservationContractError, match="different sample"):
        validate_peak_observation_binding(
            reference,
            processing_owners=identity.owners,
            sample=other_sample,
        )
    with pytest.raises(
        PeakObservationContractError, match="different processing request"
    ):
        validate_peak_observation_binding(
            reference,
            processing_owners=foreign_owners,
            sample=identity.sample,
        )


def test_retry_preserves_content_but_mints_a_distinct_attempt_artifact(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    retry = issue_processing_attempt(identity.request, retry_of=identity.attempt)
    retry_owners = _processing_owners(identity.request, retry, identity.sample)

    first = _mint_standard_reference(identity.owners, identity.sample)
    repeated = _mint_standard_reference(retry_owners, identity.sample)

    assert first.content == repeated.content
    assert first.generation_provenance == repeated.generation_provenance
    assert first.artifact.content_identity == repeated.artifact.content_identity
    assert first.artifact.evidence_artifact_id != repeated.artifact.evidence_artifact_id


def test_selected_signal_mutation_changes_content_and_tampering_fails_closed(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    original = _mint_standard_reference(identity.owners, identity.sample)
    mutated_trace = _trace(
        rt=(7.7, 7.9, 8.0, 8.1, 8.3),
        intensity=(0.0, 50.0, 501.0, 50.0, 0.0),
        rt_min=7.7,
        rt_max=8.3,
        acquisition_source_id=_source_id(identity.owners),
    )

    with pytest.raises(
        PeakObservationContractError,
        match="height_raw|area_raw_counts_seconds",
    ):
        mint_peak_observation_ref(
            processing_owners=identity.owners,
            sample=identity.sample,
            trace=mutated_trace,
            integration=_integration(raw_scan_indices=(1, 2, 3)),
            resolver_mode="local_minimum",
            proposal_sources=("local_minimum",),
        )

    mutated = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=mutated_trace,
        integration=_integration(
            raw_scan_indices=(1, 2, 3),
            apex_intensity=501.0,
            area_raw_counts_seconds=3306.0,
        ),
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )

    assert original.content.peak_observation_content_id != (
        mutated.content.peak_observation_content_id
    )
    assert original.artifact.content_identity != mutated.artifact.content_identity
    with pytest.raises(
        PeakObservationContractError, match="mismatches physical content"
    ):
        replace(
            original.content,
            intensity=(50.0, 501.0, 50.0),
        )


@pytest.mark.parametrize(
    ("field_name", "contradiction", "message"),
    [
        ("height_raw", 999999.0, "height_raw"),
        ("height_smoothed", 999999.0, "height_smoothed"),
        ("raw_apex_rt_min", 7.9, "raw_apex_rt_min"),
        ("rt_apex_min", 7.9, "height_smoothed"),
        ("area_raw_counts_seconds", 999999.0, "area_raw_counts_seconds"),
    ],
)
def test_mint_rejects_integration_facts_that_contradict_the_selected_trace(
    tmp_path: Path,
    field_name: str,
    contradiction: float,
    message: str,
) -> None:
    identity = _identity_fixture(tmp_path)
    integration = replace(
        _integration(raw_scan_indices=(1, 2, 3)),
        **{field_name: contradiction},
    )

    with pytest.raises(PeakObservationContractError, match=message):
        mint_peak_observation_ref(
            processing_owners=identity.owners,
            sample=identity.sample,
            trace=_standard_trace(identity.owners),
            integration=integration,
            resolver_mode="local_minimum",
            proposal_sources=("local_minimum",),
        )


def test_integration_fact_mutation_changes_content_identity(tmp_path: Path) -> None:
    identity = _identity_fixture(tmp_path)
    original = _mint_standard_reference(identity.owners, identity.sample)
    changed_integration = replace(
        _integration(raw_scan_indices=(1, 2, 3)),
        integration_method="alternate_integration",
    )

    changed = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=_standard_trace(identity.owners),
        integration=changed_integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )

    assert original.content.peak_observation_content_id != (
        changed.content.peak_observation_content_id
    )


def test_selection_trace_mutation_changes_provenance_not_physical_content(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    trace = _standard_trace(identity.owners)
    integration = _integration(raw_scan_indices=(1, 2, 3))

    first = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=trace,
        integration=integration,
        selection_intensity=(0.0, 40.0, 500.0, 40.0, 0.0),
        resolver_mode="smoothed",
        proposal_sources=("synthetic_selection_trace",),
    )
    changed = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=trace,
        integration=integration,
        selection_intensity=(0.0, 45.0, 500.0, 45.0, 0.0),
        resolver_mode="smoothed",
        proposal_sources=("synthetic_selection_trace",),
    )

    assert first.content == changed.content
    assert first.generation_provenance.selection_trace_sha256 != (
        changed.generation_provenance.selection_trace_sha256
    )
    assert first.artifact.content_identity != changed.artifact.content_identity


def test_equivalent_numeric_integration_inputs_have_one_content_identity(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    float_integration = replace(
        _integration(raw_scan_indices=(1, 2, 3)),
    )
    integer_integration = replace(
        float_integration,
        height_raw=500,
        height_smoothed=500,
        area_raw_counts_seconds=3300,
    )

    float_ref = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=_standard_trace(identity.owners),
        integration=float_integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )
    integer_ref = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=_standard_trace(identity.owners),
        integration=integer_integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )

    assert integer_integration == float_integration
    assert integer_ref.content == float_ref.content
    assert integer_ref.content.peak_observation_content_id == (
        float_ref.content.peak_observation_content_id
    )
    assert integer_ref.artifact.content_identity == float_ref.artifact.content_identity


def test_mint_rejects_sample_outside_request_and_trace_sample_substitution(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    other = _identity_fixture(
        tmp_path,
        sample_key="sample-b",
        sample_name="Sample B",
    )

    with pytest.raises(PeakObservationContractError, match="processing owner closure"):
        mint_peak_observation_ref(
            processing_owners=identity.owners,
            sample=other.sample,
            trace=_trace(
                rt=(7.7, 7.9, 8.0, 8.1, 8.3),
                intensity=(0.0, 50.0, 500.0, 50.0, 0.0),
                rt_min=7.7,
                rt_max=8.3,
                sample_name="sample-b",
                acquisition_source_id=_source_id(other.owners),
            ),
            integration=_integration(raw_scan_indices=(1, 2, 3)),
            resolver_mode="local_minimum",
            proposal_sources=("local_minimum",),
        )
    with pytest.raises(PeakObservationContractError, match="different sample"):
        mint_peak_observation_ref(
            processing_owners=other.owners,
            sample=other.sample,
            trace=_standard_trace(other.owners),
            integration=_integration(raw_scan_indices=(1, 2, 3)),
            resolver_mode="local_minimum",
            proposal_sources=("local_minimum",),
        )


def test_trace_acquisition_source_must_match_the_request_binding(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    foreign = _identity_fixture(
        tmp_path,
        sample_key="sample-b",
        sample_name="Sample B",
    )
    trace = replace(
        _standard_trace(identity.owners),
        acquisition_source_id=_source_id(foreign.owners),
    )

    with pytest.raises(PeakObservationContractError, match="acquisition source"):
        mint_peak_observation_ref(
            processing_owners=identity.owners,
            sample=identity.sample,
            trace=trace,
            integration=_integration(raw_scan_indices=(1, 2, 3)),
            resolver_mode="local_minimum",
            proposal_sources=("local_minimum",),
        )


def test_contiguous_but_wrong_trace_region_fails_closed(tmp_path: Path) -> None:
    identity = _identity_fixture(tmp_path)

    with pytest.raises(PeakObservationContractError, match="integration RT bounds"):
        mint_peak_observation_ref(
            processing_owners=identity.owners,
            sample=identity.sample,
            trace=_standard_trace(identity.owners),
            integration=_integration(raw_scan_indices=(0, 1, 2)),
            resolver_mode="local_minimum",
            proposal_sources=("local_minimum",),
        )


def test_integration_bounds_outside_the_supplied_trace_fail_closed(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    integration = replace(
        _integration(raw_scan_indices=(0, 1, 2, 3, 4)),
        rt_left_min=-100.0,
        rt_right_min=100.0,
        rt_width_min=200.0,
    )

    with pytest.raises(PeakObservationContractError, match="supplied Trace"):
        mint_peak_observation_ref(
            processing_owners=identity.owners,
            sample=identity.sample,
            trace=_standard_trace(identity.owners),
            integration=integration,
            resolver_mode="local_minimum",
            proposal_sources=("local_minimum",),
        )


def test_owner_boundary_between_scan_points_maps_to_selected_trace(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)
    trace = _standard_trace(identity.owners)
    peak = PeakResult(
        rt=8.0,
        intensity=500.0,
        intensity_smoothed=480.0,
        area=3300.0,
        peak_start=7.94,
        peak_end=8.06,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=8.0,
        selection_apex_intensity=480.0,
        selection_apex_index=2,
        raw_apex_rt=8.0,
        raw_apex_intensity=500.0,
        raw_apex_index=2,
        prominence=450.0,
        proposal_sources=("local_minimum",),
    )
    result = PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=trace.scan_count,
        max_smoothed=480.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    integration = build_peak_hypotheses(
        sample_name=trace.sample_name,
        target_label="test-only-target",
        role="",
        istd_pair="",
        resolver_mode="local_minimum",
        peak_result=result,
        rt=trace.rt,
        intensity=trace.intensity,
    )[0].integration

    reference = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=trace,
        integration=integration,
        selection_intensity=(0.0, 40.0, 480.0, 40.0, 0.0),
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )

    assert integration.raw_scan_indices == (1, 2, 3)
    assert reference.content.rt == (7.9, 8.0, 8.1)
    assert reference.content.integration.rt_left_min == 7.94
    assert reference.content.integration.rt_right_min == 8.06


@pytest.mark.parametrize(
    "field_name",
    [
        "area_baseline_corrected",
        "area_uncertainty",
        "area_uncertainty_formula_version",
        "baseline_residual_mad",
        "area_uncertainty_noise_source",
        "baseline_type",
        "baseline_score",
        "area_ms1_morphology",
        "ms1_morphology_area_source",
        "ms1_morphology_trace_method",
        "ms1_morphology_trace_effective_points",
    ],
)
def test_mint_rejects_derived_integration_fact_mutation(
    tmp_path: Path,
    field_name: str,
) -> None:
    identity = _identity_fixture(tmp_path)
    trace = _standard_trace(identity.owners)
    integration = _integration_with_derived_facts(trace)
    original = getattr(integration, field_name)
    if isinstance(original, str):
        contradiction: object = f"{original}-forged"
    elif isinstance(original, int):
        contradiction = original + 2
    else:
        assert isinstance(original, float)
        contradiction = original + 1.0

    with pytest.raises(PeakObservationContractError, match=field_name):
        mint_peak_observation_ref(
            processing_owners=identity.owners,
            sample=identity.sample,
            trace=trace,
            integration=replace(
                integration,
                **{field_name: contradiction},
            ),
            resolver_mode="local_minimum",
            proposal_sources=("local_minimum",),
        )


@pytest.mark.parametrize(
    ("raw_scan_indices", "message"),
    [
        ((), "non-empty"),
        ((1, 3), "contiguous"),
        ((2, 1), "strictly increasing"),
        ((1, 1), "strictly increasing"),
    ],
)
def test_missing_reordered_or_duplicate_selected_indices_fail_closed(
    tmp_path: Path,
    raw_scan_indices: tuple[int, ...],
    message: str,
) -> None:
    identity = _identity_fixture(tmp_path)

    with pytest.raises(PeakObservationContractError, match=message):
        mint_peak_observation_ref(
            processing_owners=identity.owners,
            sample=identity.sample,
            trace=_standard_trace(identity.owners),
            integration=_integration(raw_scan_indices=raw_scan_indices),
            resolver_mode="local_minimum",
            proposal_sources=("local_minimum",),
        )


def test_proposal_source_reorder_is_canonicalized(tmp_path: Path) -> None:
    identity = _identity_fixture(tmp_path)

    forward = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=_standard_trace(identity.owners),
        integration=_integration(raw_scan_indices=(1, 2, 3)),
        resolver_mode="consensus",
        proposal_sources=("boundary_resolver", "local_minimum"),
    )
    reversed_order = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=_standard_trace(identity.owners),
        integration=_integration(raw_scan_indices=(1, 2, 3)),
        resolver_mode="consensus",
        proposal_sources=("local_minimum", "boundary_resolver"),
    )

    assert forward == reversed_order


def test_resolver_change_preserves_content_but_changes_generation_provenance(
    tmp_path: Path,
) -> None:
    identity = _identity_fixture(tmp_path)

    local_minimum = _mint_standard_reference(identity.owners, identity.sample)
    arbitrated = mint_peak_observation_ref(
        processing_owners=identity.owners,
        sample=identity.sample,
        trace=_standard_trace(identity.owners),
        integration=_integration(raw_scan_indices=(1, 2, 3)),
        resolver_mode="arbitrated",
        proposal_sources=("local_minimum",),
    )

    assert local_minimum.content == arbitrated.content
    assert local_minimum.generation_provenance != arbitrated.generation_provenance
    assert (
        local_minimum.artifact.content_identity != arbitrated.artifact.content_identity
    )


def _identity_fixture(
    tmp_path: Path,
    *,
    sample_key: str = "sample-a",
    sample_name: str = "Sample A",
) -> _IdentityFixture:
    source_path = tmp_path / f"{sample_key}.synthetic"
    source_path.write_bytes(f"synthetic-no-raw-{sample_key}".encode())
    source = build_acquisition_source_identity(
        source_path,
        acquisition_format="synthetic_no_raw",
        observation_namespace=f"synthetic:{sample_key}",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key=sample_key,
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="peak-observation-input-v1",
        processing_algorithm_version="peak-observation-algorithm-v1",
        processing_contract_version="peak-observation-contract-v1",
        scientific_inputs={"fixture": "synthetic-no-raw-peak-observation"},
    )
    snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name=sample_name, raw_stem=sample_key),),
        identity_namespace="study:peak-observation",
    )
    attempt = issue_processing_attempt(request)
    sample = resolve_canonical_sample_identity(snapshot, sample_key)
    return _IdentityFixture(
        request=request,
        attempt=attempt,
        sample=sample,
        owners=_processing_owners(request, attempt, sample),
    )


def _standard_trace(owners: ProcessingOwnerReferences) -> Trace:
    return _trace(
        rt=(7.7, 7.9, 8.0, 8.1, 8.3),
        intensity=(0.0, 50.0, 500.0, 50.0, 0.0),
        rt_min=7.7,
        rt_max=8.3,
        acquisition_source_id=_source_id(owners),
    )


def _mint_standard_reference(
    owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> PeakObservationRef:
    return mint_peak_observation_ref(
        processing_owners=owners,
        sample=sample,
        trace=_standard_trace(owners),
        integration=_integration(raw_scan_indices=(1, 2, 3)),
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )


def _processing_owners(
    request: ProcessingRequestIdentity,
    attempt: ProcessingAttemptIdentity,
    sample: ResolvedCanonicalSampleIdentity,
) -> ProcessingOwnerReferences:
    return compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=sample.snapshot,
        sample_resolutions=(sample,),
    )


def _trace(
    *,
    rt: tuple[float, ...],
    intensity: tuple[float, ...],
    rt_min: float,
    rt_max: float,
    sample_name: str = "sample-a",
    acquisition_source_id: str = "",
) -> Trace:
    return Trace.from_arrays(
        sample_name=sample_name,
        mz=500.0,
        rt=rt,
        intensity=intensity,
        rt_min=rt_min,
        rt_max=rt_max,
        ppm_tol=10.0,
        source="synthetic_characterization",
        acquisition_source_id=acquisition_source_id,
    )


def _source_id(owners: ProcessingOwnerReferences) -> str:
    assert len(owners.request.acquisition_sources) == 1
    return owners.request.acquisition_sources[0].acquisition_source_id


def _integration(
    *,
    raw_scan_indices: tuple[int, ...],
    apex_intensity: float = 500.0,
    area_raw_counts_seconds: float = 3300.0,
) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=7.9,
        rt_apex_min=8.0,
        rt_right_min=8.1,
        raw_apex_rt_min=8.0,
        rt_width_min=0.2,
        height_raw=apex_intensity,
        height_smoothed=apex_intensity,
        area_raw_counts_seconds=area_raw_counts_seconds,
        integration_method="raw_trapezoid",
        boundary_sources=("candidate_interval",),
        raw_scan_indices=raw_scan_indices,
    )


def _integration_with_derived_facts(trace: Trace) -> IntegrationResult:
    peak = PeakResult(
        rt=8.0,
        intensity=500.0,
        intensity_smoothed=500.0,
        area=3300.0,
        peak_start=7.9,
        peak_end=8.1,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=8.0,
        selection_apex_intensity=500.0,
        selection_apex_index=2,
        raw_apex_rt=8.0,
        raw_apex_intensity=500.0,
        raw_apex_index=2,
        prominence=450.0,
        proposal_sources=("local_minimum",),
    )
    result = PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=trace.scan_count,
        max_smoothed=500.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    return build_peak_hypotheses(
        sample_name=trace.sample_name,
        target_label="test-only-target",
        role="",
        istd_pair="",
        resolver_mode="local_minimum",
        peak_result=result,
        rt=trace.rt,
        intensity=trace.intensity,
    )[0].integration
