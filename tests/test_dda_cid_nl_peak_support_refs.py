from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    CidNlChromatographicSupportContractError,
    compose_cid_nl_chromatographic_support_ref,
    validate_cid_nl_chromatographic_support_binding,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceContractError,
    CidNlEvidenceRef,
    mint_cid_nl_evidence_ref,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportContractError,
    DdaCidNlPeakSupportPolicy,
    canonical_dda_cid_nl_peak_support_bytes,
    resolve_dda_cid_nl_peak_support_ref,
    validate_dda_cid_nl_peak_support_binding,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult, PeakHypothesis
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    mint_peak_observation_ref,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    PeakHypothesisContractError,
    compose_cid_nl_peak_hypothesis,
    validate_cid_nl_peak_hypothesis_binding,
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

_PRECURSOR_MZ = 500.0
_NEUTRAL_LOSS_DA = 116.0474
_PRODUCT_MZ = _PRECURSOR_MZ - _NEUTRAL_LOSS_DA


@dataclass(frozen=True)
class _IdentityFixture:
    request: ProcessingRequestIdentity
    attempt: ProcessingAttemptIdentity
    sample: ResolvedCanonicalSampleIdentity
    owners: ProcessingOwnerReferences


def test_unique_dda_event_resolves_to_one_physical_peak_and_closes_lineage(
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

    assert support.content.canonical_sample_identity_id == (
        fixture.sample.identity.sample_identity_id
    )
    assert support.content.dda_scan_number == 42
    assert support.content.cid_nl_evidence_content_id == (
        evidence.artifact.content_identity.artifact_content_id
    )
    assert support.content.peak_observation_content_id == (
        peak.content.peak_observation_content_id
    )
    assert support.content.relationship == "strict_nl_inside_ms1_peak_scope"
    assert support.artifact.upstream_artifact_ids == tuple(
        sorted(
            (
                evidence.artifact.evidence_artifact_id,
                peak.artifact.evidence_artifact_id,
            )
        )
    )
    assert canonical_dda_cid_nl_peak_support_bytes(support)

    closed = compose_processing_owner_references(
        request=fixture.request,
        attempt=fixture.attempt,
        sample_snapshot=fixture.sample.snapshot,
        sample_resolutions=(fixture.sample,),
        evidence_artifacts=(support.artifact, peak.artifact, evidence.artifact),
    )
    assert support.artifact in closed.evidence_artifacts


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


def test_validation_rejects_missing_candidate_universe_member(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    evidence = _evidence(fixture.owners, fixture.sample)
    matching = _peak(fixture.owners, fixture.sample)
    nonmatching = _peak(
        fixture.owners,
        fixture.sample,
        mz=600.0,
        apex_intensity=300.0,
    )
    support = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        candidate_peaks=(matching, nonmatching),
        policy=_policy(),
    )

    with pytest.raises(DdaCidNlPeakSupportContractError, match="candidate universe"):
        validate_dda_cid_nl_peak_support_binding(
            support,
            processing_owners=fixture.owners,
            sample=fixture.sample,
            canonical_evidence=evidence,
            candidate_peaks=(matching,),
        )


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

    assert original.artifact.content_identity != scan_mutated.artifact.content_identity
    assert original.artifact.content_identity != peak_mutated.artifact.content_identity


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

    assert first.content.peak_observation_content_id == (
        changed.content.peak_observation_content_id
    )
    assert first.content.candidate_peak_content_ids != (
        changed.content.candidate_peak_content_ids
    )
    assert first.artifact.content_identity != changed.artifact.content_identity


def test_non_strict_neutral_loss_cannot_mint_support(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    observed_loss = _NEUTRAL_LOSS_DA + 0.01
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


def test_sample_request_and_attempt_substitution_fail_closed(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path, sample_key="sample-a")
    foreign = _identity_fixture(tmp_path, sample_key="sample-b")
    evidence = _evidence(fixture.owners, fixture.sample)
    peak = _peak(fixture.owners, fixture.sample)
    support = _resolve(fixture, evidence, peak)

    retry = issue_processing_attempt(fixture.request, retry_of=fixture.attempt)
    retry_owners = _processing_owners(fixture.request, retry, fixture.sample)
    with pytest.raises(
        DdaCidNlPeakSupportContractError,
        match="different processing attempt",
    ):
        validate_dda_cid_nl_peak_support_binding(
            support,
            processing_owners=retry_owners,
            sample=fixture.sample,
            canonical_evidence=evidence,
            candidate_peaks=(peak,),
        )

    with pytest.raises(DdaCidNlPeakSupportContractError, match="different sample"):
        validate_dda_cid_nl_peak_support_binding(
            support,
            processing_owners=fixture.owners,
            sample=foreign.sample,
            canonical_evidence=evidence,
            candidate_peaks=(peak,),
        )

    with pytest.raises(
        DdaCidNlPeakSupportContractError,
        match="different processing request",
    ):
        validate_dda_cid_nl_peak_support_binding(
            support,
            processing_owners=foreign.owners,
            sample=foreign.sample,
            canonical_evidence=evidence,
            candidate_peaks=(peak,),
        )


def test_evidence_and_peak_substitution_fail_closed(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    evidence = _evidence(fixture.owners, fixture.sample, scan_number=42)
    other_evidence = _evidence(fixture.owners, fixture.sample, scan_number=43)
    peak = _peak(fixture.owners, fixture.sample, apex_intensity=500.0)
    other_peak = _peak(fixture.owners, fixture.sample, apex_intensity=501.0)
    support = _resolve(fixture, evidence, peak)

    with pytest.raises(DdaCidNlPeakSupportContractError, match="evidence"):
        validate_dda_cid_nl_peak_support_binding(
            support,
            processing_owners=fixture.owners,
            sample=fixture.sample,
            canonical_evidence=other_evidence,
            candidate_peaks=(peak,),
        )
    with pytest.raises(DdaCidNlPeakSupportContractError, match="peak observation"):
        validate_dda_cid_nl_peak_support_binding(
            support,
            processing_owners=fixture.owners,
            sample=fixture.sample,
            canonical_evidence=evidence,
            candidate_peaks=(other_peak,),
        )


def test_retry_preserves_support_content_but_mints_distinct_artifact(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    retry = issue_processing_attempt(fixture.request, retry_of=fixture.attempt)
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
    assert first.artifact.content_identity == retried.artifact.content_identity
    assert first.artifact.evidence_artifact_id != retried.artifact.evidence_artifact_id


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
        member.dda_scan_number for member in forward.content.scan_supports
    ) == (
        42,
        43,
    )
    assert forward.content.strict_nl_scan_count == 2
    assert forward.content.chromatographic_support_event_count == 1
    assert forward.artifact.upstream_artifact_ids == tuple(
        sorted(
            (
                first.artifact.evidence_artifact_id,
                second.artifact.evidence_artifact_id,
            )
        )
    )


def test_missing_or_duplicate_scan_support_fails_closed(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    peak = _peak(fixture.owners, fixture.sample)
    support = _resolve(fixture, _evidence(fixture.owners, fixture.sample), peak)

    with pytest.raises(CidNlChromatographicSupportContractError, match="missing"):
        compose_cid_nl_chromatographic_support_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            scan_support_refs=(),
        )
    with pytest.raises(CidNlChromatographicSupportContractError, match="duplicate"):
        compose_cid_nl_chromatographic_support_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            scan_support_refs=(support, support),
        )


def test_mixed_peak_or_candidate_universe_cannot_form_one_event(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    first_peak = _peak(fixture.owners, fixture.sample, apex_intensity=500.0)
    second_peak = _peak(fixture.owners, fixture.sample, apex_intensity=501.0)
    first = _resolve(
        fixture,
        _evidence(fixture.owners, fixture.sample, scan_number=42),
        first_peak,
    )
    mixed_peak = _resolve(
        fixture,
        _evidence(fixture.owners, fixture.sample, scan_number=43),
        second_peak,
    )

    with pytest.raises(
        CidNlChromatographicSupportContractError,
        match="physical MS1 peak",
    ):
        compose_cid_nl_chromatographic_support_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            scan_support_refs=(first, mixed_peak),
        )

    nonmatching = _peak(
        fixture.owners,
        fixture.sample,
        mz=600.0,
        apex_intensity=300.0,
    )
    different_universe = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=_evidence(
            fixture.owners,
            fixture.sample,
            scan_number=43,
        ),
        candidate_peaks=(first_peak, nonmatching),
        policy=_policy(),
    )
    with pytest.raises(
        CidNlChromatographicSupportContractError,
        match="candidate universe",
    ):
        compose_cid_nl_chromatographic_support_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            scan_support_refs=(first, different_universe),
        )


def test_mixed_support_policy_cannot_form_one_event(tmp_path: Path) -> None:
    fixture = _identity_fixture(tmp_path)
    peak = _peak(fixture.owners, fixture.sample)
    first = _resolve(
        fixture,
        _evidence(fixture.owners, fixture.sample, scan_number=42),
        peak,
    )
    changed_policy = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=_evidence(
            fixture.owners,
            fixture.sample,
            scan_number=43,
        ),
        candidate_peaks=(peak,),
        policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.02,
            neutral_loss_tolerance_ppm=20.0,
        ),
    )

    with pytest.raises(
        CidNlChromatographicSupportContractError,
        match="support policy",
    ):
        compose_cid_nl_chromatographic_support_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            scan_support_refs=(first, changed_policy),
        )


def test_chromatographic_event_binding_rejects_missing_and_owner_substitution(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    foreign = _identity_fixture(tmp_path, sample_key="sample-b")
    peak = _peak(fixture.owners, fixture.sample)
    first = _resolve(
        fixture,
        _evidence(fixture.owners, fixture.sample, scan_number=42),
        peak,
    )
    second = _resolve(
        fixture,
        _evidence(fixture.owners, fixture.sample, scan_number=43),
        peak,
    )
    event = compose_cid_nl_chromatographic_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        scan_support_refs=(first, second),
    )

    with pytest.raises(
        CidNlChromatographicSupportContractError,
        match="support set",
    ):
        validate_cid_nl_chromatographic_support_binding(
            event,
            processing_owners=fixture.owners,
            sample=fixture.sample,
            scan_support_refs=(first,),
        )

    retry = issue_processing_attempt(fixture.request, retry_of=fixture.attempt)
    retry_owners = _processing_owners(fixture.request, retry, fixture.sample)
    with pytest.raises(
        CidNlChromatographicSupportContractError,
        match="processing attempt",
    ):
        validate_cid_nl_chromatographic_support_binding(
            event,
            processing_owners=retry_owners,
            sample=fixture.sample,
            scan_support_refs=(first, second),
        )

    with pytest.raises(CidNlChromatographicSupportContractError, match="sample"):
        validate_cid_nl_chromatographic_support_binding(
            event,
            processing_owners=fixture.owners,
            sample=foreign.sample,
            scan_support_refs=(first, second),
        )
    with pytest.raises(
        CidNlChromatographicSupportContractError,
        match="processing request",
    ):
        validate_cid_nl_chromatographic_support_binding(
            event,
            processing_owners=foreign.owners,
            sample=foreign.sample,
            scan_support_refs=(first, second),
        )


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

    assert first.artifact.content_identity != changed.artifact.content_identity


def test_retry_preserves_chromatographic_event_content_but_not_artifact(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    retry = issue_processing_attempt(fixture.request, retry_of=fixture.attempt)
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
    assert (
        first_event.artifact.content_identity == retry_event.artifact.content_identity
    )
    assert first_event.artifact.evidence_artifact_id != (
        retry_event.artifact.evidence_artifact_id
    )


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
    assert hypothesis.hypothesis_id.startswith("xic.peak-hypothesis.v1:")
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
    assert hypothesis.artifact is not None
    assert hypothesis.artifact.upstream_artifact_ids == tuple(
        sorted(
            (
                peak.artifact.evidence_artifact_id,
                event.artifact.evidence_artifact_id,
            )
        )
    )


def test_cid_nl_hypothesis_retry_preserves_content_not_exact_artifact(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    retry = issue_processing_attempt(fixture.request, retry_of=fixture.attempt)
    retry_owners = _processing_owners(fixture.request, retry, fixture.sample)

    first_peak = _peak(fixture.owners, fixture.sample)
    first_event = _event(
        fixture.owners,
        fixture.sample,
        first_peak,
        scan_numbers=(42, 43),
    )
    retry_peak = _peak(retry_owners, fixture.sample)
    retry_event = _event(
        retry_owners,
        fixture.sample,
        retry_peak,
        scan_numbers=(43, 42),
    )
    first = compose_cid_nl_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        peak_observation=first_peak,
        chromatographic_support=first_event,
    )
    retried = compose_cid_nl_peak_hypothesis(
        processing_owners=retry_owners,
        sample=fixture.sample,
        peak_observation=retry_peak,
        chromatographic_support=retry_event,
    )

    assert first.hypothesis_id == retried.hypothesis_id
    assert first.artifact is not None
    assert retried.artifact is not None
    assert first.artifact.content_identity == retried.artifact.content_identity
    assert first.artifact.evidence_artifact_id != retried.artifact.evidence_artifact_id


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
    with pytest.raises(PeakHypothesisContractError, match="physical MS1 peak"):
        compose_cid_nl_peak_hypothesis(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            peak_observation=changed_peak,
            chromatographic_support=first_event,
        )


def test_cid_nl_hypothesis_missing_and_stale_applicability_fail_closed(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    peak = _peak(fixture.owners, fixture.sample)
    event = _event(fixture.owners, fixture.sample, peak)

    with pytest.raises(PeakHypothesisContractError, match="peak observation"):
        compose_cid_nl_peak_hypothesis(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            peak_observation=None,  # type: ignore[arg-type]
            chromatographic_support=event,
        )
    with pytest.raises(PeakHypothesisContractError, match="chromatographic support"):
        compose_cid_nl_peak_hypothesis(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            peak_observation=peak,
            chromatographic_support=None,  # type: ignore[arg-type]
        )

    retry = issue_processing_attempt(fixture.request, retry_of=fixture.attempt)
    retry_owners = _processing_owners(fixture.request, retry, fixture.sample)
    retry_peak = _peak(retry_owners, fixture.sample)
    with pytest.raises(PeakHypothesisContractError, match="processing attempt"):
        compose_cid_nl_peak_hypothesis(
            processing_owners=retry_owners,
            sample=fixture.sample,
            peak_observation=retry_peak,
            chromatographic_support=event,
        )


def test_cid_nl_hypothesis_binding_rejects_event_and_owner_substitution(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path)
    foreign = _identity_fixture(tmp_path, sample_key="sample-b")
    peak = _peak(fixture.owners, fixture.sample)
    event = _event(fixture.owners, fixture.sample, peak, scan_numbers=(42, 43))
    substituted_event = _event(
        fixture.owners,
        fixture.sample,
        peak,
        scan_numbers=(42, 44),
    )
    hypothesis = compose_cid_nl_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        peak_observation=peak,
        chromatographic_support=event,
    )

    with pytest.raises(PeakHypothesisContractError, match="binding"):
        validate_cid_nl_peak_hypothesis_binding(
            hypothesis,
            processing_owners=fixture.owners,
            sample=fixture.sample,
            peak_observation=peak,
            chromatographic_support=substituted_event,
        )
    with pytest.raises(PeakHypothesisContractError, match="sample"):
        validate_cid_nl_peak_hypothesis_binding(
            hypothesis,
            processing_owners=fixture.owners,
            sample=foreign.sample,
            peak_observation=peak,
            chromatographic_support=event,
        )


def test_owner_backed_hypothesis_rejects_legacy_context_and_identity_tamper(
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

    with pytest.raises(PeakHypothesisContractError, match="Targeted context"):
        replace(hypothesis, target_label="control")
    with pytest.raises(PeakHypothesisContractError, match="hypothesis_id"):
        replace(hypothesis, hypothesis_id=f"xic.peak-hypothesis.v1:{'0' * 64}")


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


def test_cid_nl_evidence_mint_rejects_sample_outside_request_closure(
    tmp_path: Path,
) -> None:
    fixture = _identity_fixture(tmp_path, sample_key="sample-a")
    foreign = _identity_fixture(tmp_path, sample_key="sample-b")

    with pytest.raises(CidNlEvidenceContractError, match="outside"):
        _evidence(fixture.owners, foreign.sample)


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
) -> PeakObservationRef:
    trace = Trace.from_arrays(
        sample_name=sample.metadata.raw_stem,
        mz=mz,
        rt=(7.7, 7.9, 8.0, 8.1, 8.3),
        intensity=(0.0, 50.0, apex_intensity, 50.0, 0.0),
        rt_min=7.7,
        rt_max=8.3,
        ppm_tol=10.0,
        source="synthetic_characterization",
    )
    integration = IntegrationResult(
        rt_left_min=7.9,
        rt_apex_min=8.0,
        rt_right_min=8.1,
        raw_apex_rt_min=8.0,
        rt_width_min=0.2,
        height_raw=apex_intensity,
        height_smoothed=apex_intensity - 20.0,
        area_raw_counts_seconds=42.0,
        integration_method="raw_trapezoid",
        boundary_sources=("candidate_interval",),
        area_baseline_corrected=40.0,
        baseline_type="asls",
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
        scientific_input_schema_version="dda-cid-nl-peak-support-input-v1",
        processing_algorithm_version="dda-cid-nl-peak-support-algorithm-v1",
        processing_contract_version="dda-cid-nl-peak-support-contract-v1",
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
    attempt = issue_processing_attempt(request)
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
