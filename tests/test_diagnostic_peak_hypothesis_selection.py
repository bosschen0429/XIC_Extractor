from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
    processing_identity_candidate_set,
    processing_identity_hypothesis,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesisAuthorityError
from xic_extractor.peak_detection.selection_decision import (
    DiagnosticPeakHypothesisSelectionError,
    canonical_diagnostic_peak_hypothesis_selection_bytes,
    mint_diagnostic_peak_hypothesis_selection_ref,
    resolve_diagnostic_peak_hypothesis_selection,
    selection_decision_from_hypothesis,
)


def test_unique_candidate_externalizes_only_from_discovery_receipt(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-a",)
    )
    hypothesis = processing_identity_hypothesis(
        fixture,
        "sample-a",
        apex_rt=8.0,
        scan_number=42,
    )
    candidate_set = processing_identity_candidate_set(
        fixture, "sample-a", (hypothesis,)
    )

    selection = mint_diagnostic_peak_hypothesis_selection_ref(
        candidate_set=candidate_set
    )

    assert selection.status == "selected"
    assert selection.selected_hypothesis_id == hypothesis.hypothesis_id
    assert selection.candidate_hypothesis_ids == (hypothesis.hypothesis_id,)
    assert selection.candidate_set_artifact_id == (
        candidate_set.artifact.evidence_artifact_id
    )
    assert selection.artifact.upstream_artifact_ids == (
        candidate_set.artifact.evidence_artifact_id,
    )
    assert canonical_diagnostic_peak_hypothesis_selection_bytes(selection).endswith(
        b"\n"
    )
    assert (
        resolve_diagnostic_peak_hypothesis_selection(selection, candidate_set)
        is hypothesis
    )
    with pytest.raises(PeakHypothesisAuthorityError, match="diagnostic-only"):
        selection_decision_from_hypothesis(hypothesis)


def test_candidate_reorder_is_identity_neutral_and_ambiguity_cannot_select(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-a",)
    )
    first = processing_identity_hypothesis(
        fixture, "sample-a", apex_rt=8.0, scan_number=42
    )
    second = processing_identity_hypothesis(
        fixture, "sample-a", apex_rt=8.3, scan_number=52
    )

    forward_set = processing_identity_candidate_set(
        fixture, "sample-a", (first, second)
    )
    reverse_set = processing_identity_candidate_set(
        fixture, "sample-a", (second, first)
    )
    forward = mint_diagnostic_peak_hypothesis_selection_ref(candidate_set=forward_set)
    reverse = mint_diagnostic_peak_hypothesis_selection_ref(candidate_set=reverse_set)

    assert forward == reverse
    assert forward.status == "ambiguous"
    assert forward.selected_hypothesis_id is None
    assert resolve_diagnostic_peak_hypothesis_selection(forward, reverse_set) is None


def test_complete_empty_and_failure_remain_distinct_terminal_facts(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-a",)
    )
    empty_set = processing_identity_candidate_set(fixture, "sample-a", ())
    failed_set = processing_identity_candidate_set(
        fixture,
        "sample-a",
        (),
        failure_reason="discovery_worker_failed",
    )

    missing = mint_diagnostic_peak_hypothesis_selection_ref(candidate_set=empty_set)
    failed = mint_diagnostic_peak_hypothesis_selection_ref(candidate_set=failed_set)

    assert missing.status == "no_candidate"
    assert failed.status == "failed"
    assert failed.reasons == ("discovery_worker_failed",)
    assert missing.artifact.content_identity != failed.artifact.content_identity
    assert resolve_diagnostic_peak_hypothesis_selection(missing, empty_set) is None


def test_retry_binds_selection_to_exact_attempt_receipt(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-a",)
    )
    retry_fixture = fixture.retry()
    first_hypothesis = processing_identity_hypothesis(
        fixture,
        "sample-a",
        apex_rt=8.0,
        scan_number=42,
    )
    retry_hypothesis = processing_identity_hypothesis(
        retry_fixture,
        "sample-a",
        apex_rt=8.0,
        scan_number=42,
    )
    first_set = processing_identity_candidate_set(
        fixture, "sample-a", (first_hypothesis,)
    )
    retry_set = processing_identity_candidate_set(
        retry_fixture,
        "sample-a",
        (retry_hypothesis,),
    )
    first = mint_diagnostic_peak_hypothesis_selection_ref(candidate_set=first_set)

    assert first_set.candidate_hypothesis_ids == retry_set.candidate_hypothesis_ids
    assert first_set.artifact.content_identity != retry_set.artifact.content_identity
    with pytest.raises(
        DiagnosticPeakHypothesisSelectionError,
        match="candidate-set receipt",
    ):
        resolve_diagnostic_peak_hypothesis_selection(first, retry_set)


def test_receipt_substitution_and_direct_selection_tamper_fail_closed(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
    )
    hypothesis = processing_identity_hypothesis(
        fixture,
        "sample-a",
        apex_rt=8.0,
        scan_number=42,
    )
    foreign = processing_identity_hypothesis(
        fixture,
        "sample-b",
        apex_rt=8.0,
        scan_number=52,
    )
    candidate_set = processing_identity_candidate_set(
        fixture, "sample-a", (hypothesis,)
    )
    foreign_set = processing_identity_candidate_set(fixture, "sample-b", (foreign,))
    selection = mint_diagnostic_peak_hypothesis_selection_ref(
        candidate_set=candidate_set
    )

    with pytest.raises(
        DiagnosticPeakHypothesisSelectionError,
        match="candidate-set receipt",
    ):
        resolve_diagnostic_peak_hypothesis_selection(selection, foreign_set)
    with pytest.raises(
        DiagnosticPeakHypothesisSelectionError,
        match="unique candidate",
    ):
        replace(selection, selected_hypothesis_id=None)
    with pytest.raises(
        DiagnosticPeakHypothesisSelectionError,
        match="immutable artifact",
    ):
        replace(selection, reasons=("caller_override",))
