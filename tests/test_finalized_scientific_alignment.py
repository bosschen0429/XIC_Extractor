"""No-RAW contract tests for the shared scientific-alignment finalizer."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import numpy as np
import pytest

import tests.test_alignment_owner_clustering as owner_fixture
import tests.test_backfill_observation_repair as repair_fixture
from tests.issue175_no_raw_fixture import build_issue175_no_raw_fixture
from tests.test_cross_sample_reconciliation import _scenario
from tests.test_search_scope_capture import _capture_binding, _peak_config
from xic_extractor.alignment.cross_sample_reconciliation import (
    reconcile_observation_repair,
)
from xic_extractor.alignment.result import (
    FinalizedScientificAlignment,
    FinalizedScientificAlignmentContractError,
    ObservedSearchSubject,
    SearchedNoObservationSubject,
    finalize_scientific_alignment,
)
from xic_extractor.peak_detection.capture import capture_peak_hypotheses
from xic_extractor.peak_detection.cid_nl_method_subject import CID_NL_PROVIDER_ID
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    UntargetedSearchScope,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    build_processing_request_identity,
    resolve_request_acquisition_source,
)


def test_finalizer_closes_exact_core_with_terminal_subject_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, captures, receipt = _finalization_inputs(tmp_path, monkeypatch)

    result = finalize_scientific_alignment(
        scope=scope,
        captures=captures,
        final_cross_sample_receipt=receipt,
    )

    assert isinstance(result, FinalizedScientificAlignment)
    assert result.processing_request is scope.request
    assert result.processing_request_id == scope.request.processing_request_id
    assert result.canonical_samples == tuple(
        sorted(
            scope.samples,
            key=lambda sample: sample.identity.sample_identity_id,
        )
    )
    assert result.final_cross_sample_receipt is receipt
    assert result.final_population == receipt.final_population
    assert result.final_application == receipt.final_application
    assert result.lineage == receipt.final_application.lineage
    assert result.repair_receipt is receipt.repair_receipt
    assert {subject.variant for subject in result.terminal_subjects} == {
        "observed",
        "searched_no_observation",
    }
    assert any(
        isinstance(subject, ObservedSearchSubject)
        for subject in result.terminal_subjects
    )
    assert any(
        isinstance(subject, SearchedNoObservationSubject)
        for subject in result.terminal_subjects
    )


def test_finalizer_is_deterministic_and_results_are_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, captures, receipt = _finalization_inputs(tmp_path, monkeypatch)

    forward = finalize_scientific_alignment(
        scope=scope,
        captures=captures,
        final_cross_sample_receipt=receipt,
    )
    reversed_result = finalize_scientific_alignment(
        scope=scope,
        captures=tuple(reversed(captures)),
        final_cross_sample_receipt=receipt,
    )

    assert forward == reversed_result
    with pytest.raises(FrozenInstanceError):
        forward.terminal_subjects = ()  # type: ignore[misc]


@pytest.mark.parametrize("case", ["missing", "duplicate"])
def test_finalizer_rejects_inexact_terminal_population(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    scope, captures, receipt = _finalization_inputs(tmp_path, monkeypatch)
    if case == "missing":
        invalid = captures[:-1]
    elif case == "duplicate":
        invalid = captures + (captures[0],)

    with pytest.raises(
        FinalizedScientificAlignmentContractError,
        match="capture|terminal",
    ):
        finalize_scientific_alignment(
            scope=scope,
            captures=invalid,
            final_cross_sample_receipt=receipt,
        )


def test_finalizer_rejects_inconclusive_capture_as_no_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, captures, receipt = _finalization_inputs(tmp_path, monkeypatch)
    binding = scope.capture_bindings[0]
    empty_trace = Trace.from_arrays(
        sample_name=binding.sample_name,
        mz=binding.query_mz,
        rt=np.asarray((), dtype=float),
        intensity=np.asarray((), dtype=float),
        rt_min=binding.query_rt_min,
        rt_max=binding.query_rt_max,
        ppm_tol=binding.query_ppm_tol,
        source="finalized_scientific_alignment_empty_trace",
        acquisition_source_id=binding.acquisition_source_id,
    )
    incomplete = capture_peak_hypotheses(
        scope=scope,
        candidate_id=binding.candidate_id,
        trace=empty_trace,
    )
    assert incomplete.detection.status == "empty_trace"

    with pytest.raises(
        FinalizedScientificAlignmentContractError,
        match="not a closed searched-no-observation terminal",
    ):
        finalize_scientific_alignment(
            scope=scope,
            captures=(incomplete, captures[1]),
            final_cross_sample_receipt=receipt,
        )


def test_finalizer_rejects_crossed_scope_and_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, captures, receipt = _finalization_inputs(tmp_path, monkeypatch)
    equivalent_but_crossed_scope = replace(scope)

    with pytest.raises(
        FinalizedScientificAlignmentContractError,
        match="SearchScope",
    ):
        finalize_scientific_alignment(
            scope=equivalent_but_crossed_scope,
            captures=captures,
            final_cross_sample_receipt=receipt,
        )

    crossed_inputs = json.loads(scope.request.normalized_scientific_inputs_json)
    crossed_inputs["crossed_request"] = True
    crossed_request = build_processing_request_identity(
        acquisition_sources=scope.request.acquisition_sources,
        acquisition_bindings=scope.request.acquisition_bindings,
        scientific_input_schema_version=(
            scope.request.scientific_input_schema_version
        ),
        processing_algorithm_version=scope.request.processing_algorithm_version,
        processing_contract_version=scope.request.processing_contract_version,
        scientific_inputs=crossed_inputs,
    )
    crossed_request_scope = replace(scope, request=crossed_request)
    with pytest.raises(
        FinalizedScientificAlignmentContractError,
        match="processing request",
    ):
        finalize_scientific_alignment(
            scope=crossed_request_scope,
            captures=(),
            final_cross_sample_receipt=receipt,
        )


def test_finalized_values_cannot_be_forged_by_direct_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, captures, receipt = _finalization_inputs(tmp_path, monkeypatch)
    result = finalize_scientific_alignment(
        scope=scope,
        captures=captures,
        final_cross_sample_receipt=receipt,
    )
    observed = next(
        subject
        for subject in result.terminal_subjects
        if isinstance(subject, ObservedSearchSubject)
    )

    with pytest.raises(
        FinalizedScientificAlignmentContractError,
        match="minted by the scientific alignment finalizer",
    ):
        ObservedSearchSubject(
            candidate_id=observed.candidate_id,
            canonical_sample_identity_id=observed.canonical_sample_identity_id,
            trace_group_id=observed.trace_group_id,
            hypothesis=observed.hypothesis,
            _mint_token=object(),
        )

    with pytest.raises(
        FinalizedScientificAlignmentContractError,
        match="minted by the scientific alignment finalizer",
    ):
        FinalizedScientificAlignment(
            scope=result.scope,
            processing_request=result.processing_request,
            canonical_samples=result.canonical_samples,
            terminal_subjects=result.terminal_subjects,
            final_cross_sample_receipt=result.final_cross_sample_receipt,
            _mint_token=object(),
        )


def _finalization_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    scope_path = tmp_path / "scope"
    scope_path.mkdir()
    fixture = build_issue175_no_raw_fixture(
        scope_path,
        sample_roles={"sample-b": "qc", "sample-c": "study_sample"},
    )
    request = fixture.owners.request
    monkeypatch.setattr(
        owner_fixture,
        "_TEST_PROCESSING_REQUEST_ID",
        request.processing_request_id,
    )
    monkeypatch.setattr(
        repair_fixture,
        "mint_sample_metadata_snapshot",
        lambda *_args, **_kwargs: fixture.samples[0].snapshot,
    )
    _, context, repair = _scenario(
        tmp_path / "reconciliation",
        multi_member=True,
    )
    receipt = reconcile_observation_repair(
        context=context,
        repair_receipt=repair,
    )

    rt = np.linspace(7.7, 8.3, 61)
    intensities = (
        100.0 * np.exp(-0.5 * ((rt - 8.0) / 0.06) ** 2),
        np.zeros_like(rt),
    )
    candidate_ids = ("candidate-b", "candidate-c")
    bindings = []
    traces = []
    for candidate_id, sample, intensity in zip(
        candidate_ids,
        fixture.samples,
        intensities,
        strict=True,
    ):
        source = resolve_request_acquisition_source(
            processing_owners=fixture.owners,
            sample=sample,
        )
        bindings.append(
            _capture_binding(
                candidate_id=candidate_id,
                sample=sample,
                acquisition_source_id=source.acquisition_source_id,
                mz=fixture.method_subject.precursor_mz,
                ppm_tol=fixture.method_subject.precursor_tolerance_ppm,
                rt_min=float(rt[0]),
                rt_max=float(rt[-1]),
            )
        )
        traces.append(
            Trace.from_arrays(
                sample_name=sample.metadata.sample_name,
                mz=fixture.method_subject.precursor_mz,
                rt=rt,
                intensity=intensity,
                rt_min=float(rt[0]),
                rt_max=float(rt[-1]),
                ppm_tol=fixture.method_subject.precursor_tolerance_ppm,
                source="finalized_scientific_alignment_test",
                acquisition_source_id=source.acquisition_source_id,
            )
        )
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=request,
        samples=fixture.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=_peak_config(tmp_path),
        ),
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=fixture.method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=candidate_ids,
        capture_bindings=tuple(bindings),
    )
    captures = tuple(
        capture_peak_hypotheses(
            scope=scope,
            candidate_id=candidate_id,
            trace=trace,
        )
        for candidate_id, trace in zip(candidate_ids, traces, strict=True)
    )
    assert captures[0].selected_hypothesis is not None
    assert captures[1].selected_hypothesis is None
    return scope, captures, receipt
