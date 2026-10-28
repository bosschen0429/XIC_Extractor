from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from xic_extractor.configuration.models import Target
from xic_extractor.extraction.result_assembly import build_extraction_result
from xic_extractor.extraction.result_models import ExtractionResult
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    compose_cid_nl_chromatographic_support_ref,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceRef,
    mint_cid_nl_evidence_ref,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportContractError,
    DdaCidNlPeakSupportPolicy,
    resolve_dda_cid_nl_peak_support_ref,
)
from xic_extractor.peak_detection.hypotheses import (
    IntegrationResult,
    PeakHypothesis,
    PeakHypothesisAuthorityError,
)
from xic_extractor.peak_detection.models import PeakDetectionResult
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    mint_peak_observation_ref,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    compose_cid_nl_peak_hypothesis,
)
from xic_extractor.peak_detection.selection_decision import (
    selection_decision_from_hypothesis,
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
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)

_PRECURSOR_MZ = 500.0
_NEUTRAL_LOSS_DA = 116.0474
_PRODUCT_MZ = _PRECURSOR_MZ - _NEUTRAL_LOSS_DA


@dataclass(frozen=True)
class _IdentityFixture:
    request: ProcessingRequestIdentity
    attempt: ProcessingAttemptIdentity
    sample: ResolvedCanonicalSampleIdentity
    owners: ProcessingOwnerReferences


def test_unique_dda_event_resolves_to_one_physical_peak(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    evidence = _evidence(fixture.owners, fixture.sample)
    peak = _peak(fixture.owners, fixture.sample)

    support = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        candidate_peaks=(peak,),
        policy=_policy(),
    )

    assert support.content.sample_key == (fixture.sample.identity.sample_key)
    assert support.content.dda_scan_number == 42
    assert support.content.evidence == evidence.observation
    assert support.content.peak_observation == peak.content
    assert support.content.relationship == "strict_nl_inside_ms1_peak_scope"


def test_candidate_reorder_is_neutral(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    evidence = _evidence(fixture.owners, fixture.sample)
    matching = _peak(fixture.owners, fixture.sample)
    nonmatching = _peak(
        fixture.owners,
        fixture.sample,
        mz=600.0,
        apex_intensity=300.0,
    )

    forward = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        candidate_peaks=(matching, nonmatching),
        policy=_policy(),
    )
    reverse = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        candidate_peaks=(nonmatching, matching),
        policy=_policy(),
    )

    assert forward == reverse


def test_missing_peak_match_fails_closed(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    evidence = _evidence(fixture.owners, fixture.sample, scan_rt_min=8.25)
    peak = _peak(fixture.owners, fixture.sample)

    with pytest.raises(DdaCidNlPeakSupportContractError, match="missing"):
        resolve_dda_cid_nl_peak_support_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            canonical_evidence=evidence,
            candidate_peaks=(peak,),
            policy=_policy(),
        )


def test_dda_matching_uses_integration_bounds_not_padded_trace_points(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    peak = _peak(
        fixture.owners,
        fixture.sample,
        rt_left_min=7.94,
        rt_right_min=8.06,
    )
    outside_evidence = _evidence(
        fixture.owners,
        fixture.sample,
        scan_number=43,
        scan_rt_min=8.08,
    )

    with pytest.raises(DdaCidNlPeakSupportContractError, match="missing"):
        resolve_dda_cid_nl_peak_support_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            canonical_evidence=outside_evidence,
            candidate_peaks=(peak,),
            policy=_policy(),
        )

    inside_evidence = _evidence(fixture.owners, fixture.sample, scan_rt_min=8.0)
    support = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=inside_evidence,
        candidate_peaks=(peak,),
        policy=_policy(),
    )
    assert support.content.peak_rt_min == pytest.approx(7.94)
    assert support.content.peak_rt_max == pytest.approx(8.06)


def test_multiple_matching_peaks_are_ambiguous(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    evidence = _evidence(fixture.owners, fixture.sample)
    first = _peak(fixture.owners, fixture.sample, apex_intensity=500.0)
    second = _peak(fixture.owners, fixture.sample, apex_intensity=501.0)

    with pytest.raises(DdaCidNlPeakSupportContractError, match="ambiguous"):
        resolve_dda_cid_nl_peak_support_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            canonical_evidence=evidence,
            candidate_peaks=(first, second),
            policy=_policy(),
        )


def test_duplicate_candidate_reference_is_ambiguous(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    evidence = _evidence(fixture.owners, fixture.sample)
    peak = _peak(fixture.owners, fixture.sample)

    with pytest.raises(DdaCidNlPeakSupportContractError, match="ambiguous"):
        resolve_dda_cid_nl_peak_support_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            canonical_evidence=evidence,
            candidate_peaks=(peak, peak),
            policy=_policy(),
        )


def test_scan_and_peak_mutation_invalidate_support_content(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    original_evidence = _evidence(fixture.owners, fixture.sample, scan_number=42)
    changed_scan = _evidence(fixture.owners, fixture.sample, scan_number=43)
    original_peak = _peak(fixture.owners, fixture.sample, apex_intensity=500.0)
    changed_peak = _peak(fixture.owners, fixture.sample, apex_intensity=501.0)

    original = _resolve(fixture, original_evidence, original_peak)
    scan_mutated = _resolve(fixture, changed_scan, original_peak)
    peak_mutated = _resolve(fixture, original_evidence, changed_peak)

    assert original.content != scan_mutated.content
    assert original.content != peak_mutated.content


def test_nonselected_candidate_mutation_invalidates_bound_universe(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    evidence = _evidence(fixture.owners, fixture.sample)
    selected = _peak(fixture.owners, fixture.sample)
    first_nonmatch = _peak(
        fixture.owners,
        fixture.sample,
        mz=600.0,
        apex_intensity=300.0,
    )
    changed_nonmatch = _peak(
        fixture.owners,
        fixture.sample,
        mz=600.0,
        apex_intensity=301.0,
    )

    first = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        candidate_peaks=(selected, first_nonmatch),
        policy=_policy(),
    )
    changed = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        candidate_peaks=(selected, changed_nonmatch),
        policy=_policy(),
    )

    assert first.content.peak_observation == (changed.content.peak_observation)
    assert first.content.candidate_peaks != (changed.content.candidate_peaks)
    assert first.content != changed.content


def test_non_strict_neutral_loss_cannot_mint_support(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    observed_loss = _NEUTRAL_LOSS_DA + 0.02
    observed_error_ppm = (
        abs(observed_loss - _NEUTRAL_LOSS_DA) / _NEUTRAL_LOSS_DA * 1_000_000.0
    )
    evidence = _evidence(
        fixture.owners,
        fixture.sample,
        product_mz=_PRECURSOR_MZ - observed_loss,
        observed_neutral_loss_da=observed_loss,
        observed_loss_error_ppm=observed_error_ppm,
    )

    with pytest.raises(DdaCidNlPeakSupportContractError, match="strict"):
        _resolve(fixture, evidence, _peak(fixture.owners, fixture.sample))


def test_cid_nl_support_accepts_loss_inside_ion_window(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    observed_loss = _NEUTRAL_LOSS_DA * (1.0 + 40.0e-6)
    evidence = _evidence(
        fixture.owners,
        fixture.sample,
        product_mz=_PRECURSOR_MZ - observed_loss,
        observed_neutral_loss_da=observed_loss,
        observed_loss_error_ppm=40.0,
    )

    support = _resolve(fixture, evidence, _peak(fixture.owners, fixture.sample))

    assert support.content.observed_loss_error_ppm == pytest.approx(40.0)
    assert (
        support.content.observed_loss_error_da < support.content.neutral_loss_window_da
    )


def test_retry_preserves_support_content(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    retry = issue_processing_attempt(retry_of=fixture.attempt)
    retry_owners = _processing_owners(fixture.request, retry, fixture.sample)

    first_evidence = _evidence(fixture.owners, fixture.sample)
    first_peak = _peak(fixture.owners, fixture.sample)
    retried_evidence = _evidence(retry_owners, fixture.sample)
    retried_peak = _peak(retry_owners, fixture.sample)
    first = _resolve(fixture, first_evidence, first_peak)
    retried = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=retry_owners,
        sample=fixture.sample,
        canonical_evidence=retried_evidence,
        candidate_peaks=(retried_peak,),
        policy=_policy(),
    )

    assert first.content == retried.content


def test_repeated_dda_scans_compose_one_order_neutral_chromatographic_event(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    peak = _peak(fixture.owners, fixture.sample)
    first = _resolve(
        fixture,
        _evidence(fixture.owners, fixture.sample, scan_number=42, scan_rt_min=7.98),
        peak,
    )
    second = _resolve(
        fixture,
        _evidence(fixture.owners, fixture.sample, scan_number=43, scan_rt_min=8.02),
        peak,
    )

    forward = compose_cid_nl_chromatographic_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        scan_support_refs=(first, second),
    )
    reverse = compose_cid_nl_chromatographic_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        scan_support_refs=(second, first),
    )

    assert forward == reverse
    assert tuple(
        member.support.dda_scan_number for member in forward.content.scan_supports
    ) == (
        42,
        43,
    )
    assert forward.content.strict_nl_scan_count == 2
    assert forward.content.chromatographic_support_event_count == 1


def test_equivalent_numeric_policy_inputs_have_equal_support(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    peak = _peak(fixture.owners, fixture.sample)
    evidence = _evidence(fixture.owners, fixture.sample)
    integer_policy = DdaCidNlPeakSupportPolicy(
        precursor_tolerance_da=1,
        neutral_loss_tolerance_ppm=20,
    )
    float_policy = DdaCidNlPeakSupportPolicy(
        precursor_tolerance_da=1.0,
        neutral_loss_tolerance_ppm=20.0,
    )

    integer_ref = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        candidate_peaks=(peak,),
        policy=integer_policy,
    )
    float_ref = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        candidate_peaks=(peak,),
        policy=float_policy,
    )

    assert type(integer_policy.precursor_tolerance_da) is float
    assert type(integer_policy.neutral_loss_tolerance_ppm) is float
    assert integer_ref.content == float_ref.content


def test_signed_zero_support_content_is_equal(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    reference = _resolve(
        fixture,
        _evidence(fixture.owners, fixture.sample),
        _peak(fixture.owners, fixture.sample),
    )
    signed_zero_content = replace(
        reference.content,
        precursor_delta_da=-0.0,
        observed_loss_error_ppm=-0.0,
    )
    signed_zero_reference = replace(reference, content=signed_zero_content)

    assert math.copysign(1.0, signed_zero_content.precursor_delta_da) == 1.0
    assert math.copysign(1.0, signed_zero_content.observed_loss_error_ppm) == 1.0
    assert signed_zero_reference == reference
    assert signed_zero_reference.content == reference.content


def test_scan_mutation_invalidates_chromatographic_event_content(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    peak = _peak(fixture.owners, fixture.sample)
    first = compose_cid_nl_chromatographic_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        scan_support_refs=(
            _resolve(
                fixture,
                _evidence(fixture.owners, fixture.sample, scan_number=42),
                peak,
            ),
        ),
    )
    changed = compose_cid_nl_chromatographic_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        scan_support_refs=(
            _resolve(
                fixture,
                _evidence(fixture.owners, fixture.sample, scan_number=43),
                peak,
            ),
        ),
    )

    assert first.content != changed.content


def test_retry_preserves_chromatographic_event_content(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    retry = issue_processing_attempt(retry_of=fixture.attempt)
    retry_owners = _processing_owners(fixture.request, retry, fixture.sample)
    first_peak = _peak(fixture.owners, fixture.sample)
    retry_peak = _peak(retry_owners, fixture.sample)

    first_event = compose_cid_nl_chromatographic_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        scan_support_refs=tuple(
            _resolve(
                fixture,
                _evidence(fixture.owners, fixture.sample, scan_number=scan_number),
                first_peak,
            )
            for scan_number in (42, 43)
        ),
    )
    retry_event = compose_cid_nl_chromatographic_support_ref(
        processing_owners=retry_owners,
        sample=fixture.sample,
        scan_support_refs=tuple(
            resolve_dda_cid_nl_peak_support_ref(
                processing_owners=retry_owners,
                sample=fixture.sample,
                canonical_evidence=_evidence(
                    retry_owners,
                    fixture.sample,
                    scan_number=scan_number,
                ),
                candidate_peaks=(retry_peak,),
                policy=_policy(),
            )
            for scan_number in (42, 43)
        ),
    )

    assert first_event.content == retry_event.content


def test_cid_nl_event_composes_the_existing_peak_hypothesis_owner(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    peak = _peak(fixture.owners, fixture.sample)
    event = _event(fixture.owners, fixture.sample, peak, scan_numbers=(42, 43))

    hypothesis = compose_cid_nl_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        peak_observation=peak,
        chromatographic_support=event,
    )

    assert isinstance(hypothesis, PeakHypothesis)
    assert hypothesis.hypothesis_id == (
        "sample-a|dda_cid_nl|500.0|8.0|500.0|7.9|8.1|3|42:None|43:None"
    )
    assert hypothesis.hypothesis_method == "dda_cid_nl"
    assert hypothesis.peak_observation_refs == (peak,)
    assert hypothesis.evidence.cid_nl_chromatographic_support_ref == event
    assert hypothesis.integration == peak.content.integration
    assert hypothesis.trace_group_id == ""
    assert hypothesis.target_label == ""
    assert hypothesis.role == ""
    assert hypothesis.istd_pair == ""
    assert hypothesis.analysis_mode == "workflow_neutral"
    assert hypothesis.audit.selected is False


def test_cid_nl_hypothesis_peak_or_event_mutation_invalidates_identity(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    first_peak = _peak(fixture.owners, fixture.sample, apex_intensity=500.0)
    first_event = _event(fixture.owners, fixture.sample, first_peak)
    changed_peak = _peak(fixture.owners, fixture.sample, apex_intensity=501.0)
    changed_event = _event(fixture.owners, fixture.sample, changed_peak)

    first = compose_cid_nl_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        peak_observation=first_peak,
        chromatographic_support=first_event,
    )
    changed = compose_cid_nl_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        peak_observation=changed_peak,
        chromatographic_support=changed_event,
    )

    assert first.hypothesis_id != changed.hypothesis_id


def test_owner_backed_hypothesis_cannot_enter_selection_or_targeted_product(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    peak = _peak(fixture.owners, fixture.sample)
    event = _event(fixture.owners, fixture.sample, peak)
    hypothesis = compose_cid_nl_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        peak_observation=peak,
        chromatographic_support=event,
    )

    with pytest.raises(PeakHypothesisAuthorityError, match="diagnostic-only"):
        selection_decision_from_hypothesis(hypothesis)
    with pytest.raises(PeakHypothesisAuthorityError, match="diagnostic-only"):
        build_extraction_result(
            peak_result=PeakDetectionResult(
                status="PEAK_NOT_FOUND",
                peak=None,
                n_points=0,
                max_smoothed=None,
                n_prominent_peaks=0,
            ),
            nl_result=None,
            candidate_ms2_evidence=None,
            target=Target(
                label="control",
                mz=_PRECURSOR_MZ,
                rt_min=7.5,
                rt_max=8.5,
                ppm_tol=10.0,
                neutral_loss_da=_NEUTRAL_LOSS_DA,
                nl_ppm_warn=20.0,
                nl_ppm_max=50.0,
                is_istd=False,
                istd_pair="",
            ),
            candidate=None,
            scoring_context_builder=None,
            selected_hypothesis=hypothesis,
        )
    with pytest.raises(PeakHypothesisAuthorityError, match="diagnostic-only"):
        ExtractionResult(
            peak_result=PeakDetectionResult(
                status="PEAK_NOT_FOUND",
                peak=None,
                n_points=0,
                max_smoothed=None,
                n_prominent_peaks=0,
            ),
            nl=None,
            selected_hypothesis=hypothesis,
        )


def _resolve(
    fixture: _IdentityFixture,
    evidence: CidNlEvidenceRef,
    peak: PeakObservationRef,
):
    return resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        candidate_peaks=(peak,),
        policy=_policy(),
    )


def _event(
    owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    peak: PeakObservationRef,
    *,
    scan_numbers: tuple[int, ...] = (42,),
):
    return compose_cid_nl_chromatographic_support_ref(
        processing_owners=owners,
        sample=sample,
        scan_support_refs=tuple(
            resolve_dda_cid_nl_peak_support_ref(
                processing_owners=owners,
                sample=sample,
                canonical_evidence=_evidence(
                    owners,
                    sample,
                    scan_number=scan_number,
                ),
                candidate_peaks=(peak,),
                policy=_policy(),
            )
            for scan_number in scan_numbers
        ),
    )


def _policy() -> DdaCidNlPeakSupportPolicy:
    return DdaCidNlPeakSupportPolicy(
        precursor_tolerance_da=0.01,
        neutral_loss_tolerance_ppm=20.0,
    )


def _evidence(
    owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    *,
    scan_number: int = 42,
    scan_rt_min: float = 8.0,
    precursor_mz: float = _PRECURSOR_MZ,
    product_mz: float = _PRODUCT_MZ,
    observed_neutral_loss_da: float = _NEUTRAL_LOSS_DA,
    observed_loss_error_ppm: float = 0.0,
) -> CidNlEvidenceRef:
    return mint_cid_nl_evidence_ref(
        processing_owners=owners,
        sample=sample,
        scan_number=scan_number,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        configured_neutral_loss_da=_NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=observed_neutral_loss_da,
        observed_loss_error_ppm=observed_loss_error_ppm,
        scan_rt_min=scan_rt_min,
        product_intensity=400.0,
    )


def _peak(
    owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    *,
    mz: float = _PRECURSOR_MZ,
    apex_intensity: float = 500.0,
    rt_left_min: float = 7.9,
    rt_right_min: float = 8.1,
) -> PeakObservationRef:
    acquisition_source = resolve_request_acquisition_source(
        processing_owners=owners,
        sample=sample,
    )
    trace = Trace.from_arrays(
        sample_name=sample.metadata.raw_stem,
        mz=mz,
        rt=(7.7, 7.9, 8.0, 8.1, 8.3),
        intensity=(0.0, 50.0, apex_intensity, 50.0, 0.0),
        rt_min=7.7,
        rt_max=8.3,
        ppm_tol=10.0,
        source="synthetic_characterization",
        acquisition_source_id=acquisition_source.acquisition_source_id,
    )
    integration = IntegrationResult(
        rt_left_min=rt_left_min,
        rt_apex_min=8.0,
        rt_right_min=rt_right_min,
        raw_apex_rt_min=8.0,
        rt_width_min=rt_right_min - rt_left_min,
        height_raw=apex_intensity,
        height_smoothed=apex_intensity,
        area_raw_counts_seconds=6.0 * (apex_intensity + 50.0),
        integration_method="raw_trapezoid",
        boundary_sources=("candidate_interval",),
        raw_scan_indices=(1, 2, 3),
    )
    return mint_peak_observation_ref(
        processing_owners=owners,
        sample=sample,
        trace=trace,
        integration=integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )


def _identity_fixture(
    tmp_path: Path,
    *,
    sample_key: str = "sample-a",
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
        scientific_input_schema_version="dda-cid-nl-peak-support-input",
        processing_algorithm_version="dda-cid-nl-peak-support-algorithm",
        processing_contract_version="dda-cid-nl-peak-support-contract",
        scientific_inputs={
            "fixture": "synthetic-no-raw-dda-cid-nl-peak-support",
            "precursor_tolerance_da": 0.01,
            "neutral_loss_tolerance_ppm": 20.0,
        },
    )
    snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name=sample_key, raw_stem=sample_key),),
        identity_namespace="study:dda-cid-nl-peak-support",
    )
    attempt = issue_processing_attempt()
    sample = resolve_canonical_sample_identity(snapshot, sample_key)
    return _IdentityFixture(
        request=request,
        attempt=attempt,
        sample=sample,
        owners=_processing_owners(request, attempt, sample),
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


def test_cid_nl_hypothesis_producer_keeps_event_and_sample_binding(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    peak = _peak(fixture.owners, fixture.sample)
    first_event = _event(fixture.owners, fixture.sample, peak, scan_numbers=(42, 43))
    second_event = _event(fixture.owners, fixture.sample, peak, scan_numbers=(42, 44))
    hypotheses = tuple(
        compose_cid_nl_peak_hypothesis(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            peak_observation=peak,
            chromatographic_support=event,
        )
        for event in (first_event, second_event)
    )
    assert hypotheses[0].hypothesis_id != hypotheses[1].hypothesis_id
