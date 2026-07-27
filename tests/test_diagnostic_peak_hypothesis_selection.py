from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from xic_extractor.config import Target
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    mint_target_list_snapshot,
    resolve_target_definition,
    target_list_snapshot_payload,
)
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    compose_cid_nl_chromatographic_support_ref,
)
from xic_extractor.peak_detection.cid_nl_evidence import mint_cid_nl_evidence_ref
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
    resolve_dda_cid_nl_peak_support_ref,
)
from xic_extractor.peak_detection.hypotheses import (
    IntegrationResult,
    PeakHypothesis,
    PeakHypothesisAuthorityError,
)
from xic_extractor.peak_detection.observations import mint_peak_observation_ref
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    compose_cid_nl_peak_hypothesis,
)
from xic_extractor.peak_detection.selection_decision import (
    DiagnosticPeakHypothesisSelectionError,
    canonical_diagnostic_peak_hypothesis_selection_bytes,
    mint_diagnostic_peak_hypothesis_selection_ref,
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
class _Fixture:
    request: ProcessingRequestIdentity
    attempt: ProcessingAttemptIdentity
    sample: ResolvedCanonicalSampleIdentity
    target: ResolvedTargetDefinition
    owners: ProcessingOwnerReferences


def test_unique_candidate_externalizes_without_product_authority(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    hypothesis = _hypothesis(fixture, apex_rt=8.0, scan_number=42)

    selection = mint_diagnostic_peak_hypothesis_selection_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        target=fixture.target,
        candidate_hypotheses=(hypothesis,),
    )

    assert selection.status == "selected"
    assert selection.selected_hypothesis_id == hypothesis.hypothesis_id
    assert selection.candidate_hypothesis_ids == (hypothesis.hypothesis_id,)
    assert selection.reasons == ("unique_owner_backed_candidate",)
    assert selection.artifact.semantic_contract.validation_status == "diagnostic_only"
    assert selection.artifact.upstream_artifact_ids == (
        hypothesis.artifact.evidence_artifact_id,
    )
    assert canonical_diagnostic_peak_hypothesis_selection_bytes(selection).endswith(
        b"\n"
    )
    with pytest.raises(PeakHypothesisAuthorityError, match="diagnostic-only"):
        selection_decision_from_hypothesis(hypothesis)


def test_candidate_order_is_canonical_and_ambiguity_cannot_select(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    first = _hypothesis(fixture, apex_rt=8.0, scan_number=42)
    second = _hypothesis(fixture, apex_rt=8.3, scan_number=43)

    forward = mint_diagnostic_peak_hypothesis_selection_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        target=fixture.target,
        candidate_hypotheses=(first, second),
    )
    reversed_order = mint_diagnostic_peak_hypothesis_selection_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        target=fixture.target,
        candidate_hypotheses=(second, first),
    )

    assert forward == reversed_order
    assert forward.status == "ambiguous"
    assert forward.selected_hypothesis_id is None
    assert forward.reasons == ("multiple_owner_backed_candidates",)
    assert forward.candidate_hypothesis_ids == tuple(
        sorted((first.hypothesis_id, second.hypothesis_id))
    )


def test_no_candidate_and_failure_remain_distinct_terminal_facts(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    missing = mint_diagnostic_peak_hypothesis_selection_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        target=fixture.target,
        candidate_hypotheses=(),
    )
    failed = mint_diagnostic_peak_hypothesis_selection_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        target=fixture.target,
        candidate_hypotheses=(),
        failure_reason="discovery_worker_failed",
    )

    assert missing.status == "no_candidate"
    assert missing.reasons == ("no_owner_backed_candidate",)
    assert failed.status == "failed"
    assert failed.reasons == ("discovery_worker_failed",)
    assert missing.artifact.content_identity != failed.artifact.content_identity


def test_retry_preserves_selection_content_but_not_attempt_artifact(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    retry_attempt = issue_processing_attempt(fixture.request, retry_of=fixture.attempt)
    retry_fixture = replace(
        fixture,
        attempt=retry_attempt,
        owners=_owners(
            fixture.request,
            retry_attempt,
            fixture.sample,
            fixture.target,
        ),
    )
    first_hypothesis = _hypothesis(fixture, apex_rt=8.0, scan_number=42)
    retry_hypothesis = _hypothesis(retry_fixture, apex_rt=8.0, scan_number=42)

    first = mint_diagnostic_peak_hypothesis_selection_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        target=fixture.target,
        candidate_hypotheses=(first_hypothesis,),
    )
    retried = mint_diagnostic_peak_hypothesis_selection_ref(
        processing_owners=retry_fixture.owners,
        sample=retry_fixture.sample,
        target=retry_fixture.target,
        candidate_hypotheses=(retry_hypothesis,),
    )

    assert first.candidate_hypothesis_ids == retried.candidate_hypothesis_ids
    assert first.artifact.content_identity == retried.artifact.content_identity
    assert first.artifact.evidence_artifact_id != retried.artifact.evidence_artifact_id


def test_attempt_duplicate_and_direct_state_bypass_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    hypothesis = _hypothesis(fixture, apex_rt=8.0, scan_number=42)
    retry_attempt = issue_processing_attempt(fixture.request, retry_of=fixture.attempt)
    retry_fixture = replace(
        fixture,
        attempt=retry_attempt,
        owners=_owners(
            fixture.request,
            retry_attempt,
            fixture.sample,
            fixture.target,
        ),
    )
    retry_hypothesis = _hypothesis(retry_fixture, apex_rt=8.0, scan_number=42)

    with pytest.raises(DiagnosticPeakHypothesisSelectionError, match="duplicate"):
        mint_diagnostic_peak_hypothesis_selection_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            target=fixture.target,
            candidate_hypotheses=(hypothesis, hypothesis),
        )
    with pytest.raises(
        DiagnosticPeakHypothesisSelectionError,
        match="different processing attempt",
    ):
        mint_diagnostic_peak_hypothesis_selection_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            target=fixture.target,
            candidate_hypotheses=(retry_hypothesis,),
        )

    selection = mint_diagnostic_peak_hypothesis_selection_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        target=fixture.target,
        candidate_hypotheses=(hypothesis,),
    )
    with pytest.raises(
        DiagnosticPeakHypothesisSelectionError, match="unique candidate"
    ):
        replace(selection, selected_hypothesis_id=None)
    with pytest.raises(
        DiagnosticPeakHypothesisSelectionError, match="immutable artifact"
    ):
        replace(selection, reasons=("caller_override",))


def _fixture(tmp_path: Path) -> _Fixture:
    source_path = tmp_path / "sample-a.synthetic"
    source_path.write_bytes(b"synthetic-no-raw-targeted-selection")
    source = build_acquisition_source_identity(
        source_path,
        acquisition_format="synthetic_no_raw",
        observation_namespace="synthetic:sample-a",
    )
    target_value = Target(
        label="Analyte",
        mz=_PRECURSOR_MZ,
        rt_min=7.5,
        rt_max=8.6,
        ppm_tol=20.0,
        neutral_loss_da=_NEUTRAL_LOSS_DA,
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
    )
    target_snapshot = mint_target_list_snapshot(
        (target_value,),
        identity_namespace="target-list:package2-no-raw",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="sample-a",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="package2-selection-input-v1",
        processing_algorithm_version="package2-selection-algorithm-v1",
        processing_contract_version="package2-selection-contract-v1",
        scientific_inputs={
            "fixture": "package2-no-raw-selection",
            "target_list_snapshot": target_list_snapshot_payload(target_snapshot),
        },
    )
    sample_snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name="sample-a", raw_stem="sample-a"),),
        identity_namespace="study:package2-no-raw",
    )
    sample = resolve_canonical_sample_identity(sample_snapshot, "sample-a")
    target = resolve_target_definition(target_snapshot, "Analyte")
    attempt = issue_processing_attempt(request)
    return _Fixture(
        request=request,
        attempt=attempt,
        sample=sample,
        target=target,
        owners=_owners(request, attempt, sample, target),
    )


def _owners(
    request: ProcessingRequestIdentity,
    attempt: ProcessingAttemptIdentity,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
) -> ProcessingOwnerReferences:
    return compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=sample.snapshot,
        sample_resolutions=(sample,),
        target_snapshot=target.snapshot,
    )


def _hypothesis(
    fixture: _Fixture,
    *,
    apex_rt: float,
    scan_number: int,
) -> PeakHypothesis:
    peak = _peak(fixture, apex_rt=apex_rt)
    evidence = mint_cid_nl_evidence_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        scan_number=scan_number,
        precursor_mz=_PRECURSOR_MZ,
        product_mz=_PRODUCT_MZ,
        configured_neutral_loss_da=_NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=_NEUTRAL_LOSS_DA,
        observed_loss_error_ppm=0.0,
        scan_rt_min=apex_rt,
        product_intensity=400.0,
    )
    support = resolve_dda_cid_nl_peak_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        candidate_peaks=(peak,),
        policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01,
            neutral_loss_tolerance_ppm=20.0,
        ),
    )
    event = compose_cid_nl_chromatographic_support_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        scan_support_refs=(support,),
    )
    return compose_cid_nl_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        peak_observation=peak,
        chromatographic_support=event,
    )


def _peak(fixture: _Fixture, *, apex_rt: float):
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=fixture.sample,
    )
    rt = (apex_rt - 0.3, apex_rt - 0.1, apex_rt, apex_rt + 0.1, apex_rt + 0.3)
    trace = Trace.from_arrays(
        sample_name=fixture.sample.metadata.raw_stem,
        mz=_PRECURSOR_MZ,
        rt=rt,
        intensity=(0.0, 50.0, 500.0, 50.0, 0.0),
        rt_min=rt[0],
        rt_max=rt[-1],
        ppm_tol=10.0,
        source="synthetic_characterization",
        acquisition_source_id=source.acquisition_source_id,
    )
    integration = IntegrationResult(
        rt_left_min=apex_rt - 0.1,
        rt_apex_min=apex_rt,
        rt_right_min=apex_rt + 0.1,
        raw_apex_rt_min=apex_rt,
        rt_width_min=0.2,
        height_raw=500.0,
        height_smoothed=500.0,
        area_raw_counts_seconds=3300.0,
        integration_method="raw_trapezoid",
        boundary_sources=("candidate_interval",),
        raw_scan_indices=(1, 2, 3),
    )
    return mint_peak_observation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        trace=trace,
        integration=integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )
