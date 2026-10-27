"""No-RAW contract tests for the shared scientific-alignment finalizer."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import xic_extractor.alignment.result.scientific_alignment as finalizer_owner
from tests.test_cell_observations import _owner_handoff
from tests.test_cross_sample_association_lineage import _final_receipt
from tests.test_search_scope_capture import _capture_binding, _peak_config
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
    TargetSearchScope,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    resolve_request_acquisition_source,
)


def test_finalizer_closes_exact_core_with_terminal_subject_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, captures, receipt, handoff, predecessor_cells = _finalization_inputs(
        tmp_path, monkeypatch
    )
    from xic_extractor.alignment.cell_observation import observations as cell_owner

    compose_cells = cell_owner.compose_final_group_cell_observation_population
    composed_populations = []

    def record_composition(**kwargs):
        cells = compose_cells(**kwargs)
        composed_populations.append(cells)
        return cells

    for owner in (cell_owner, finalizer_owner):
        monkeypatch.setattr(
            owner, "compose_final_group_cell_observation_population", record_composition
        )

    result = finalize_scientific_alignment(
        scope=scope,
        captures=captures,
        grouping_result=receipt,
        cell_observation_handoff=handoff,
        predecessor_cell_observations=predecessor_cells,
    )

    assert isinstance(result, FinalizedScientificAlignment)
    assert result.processing_request is scope.request
    assert result.canonical_samples == tuple(
        sorted(
            scope.samples,
            key=lambda sample: sample.identity.sample_key,
        )
    )
    assert result.grouping_result is receipt
    assert result.final_population == receipt.final_population
    assert result.partition_transition == receipt.partition_transition
    assert result.lineage == receipt.partition_transition.lineage
    assert result.cell_observation_handoff is handoff
    assert len(composed_populations) == 1
    assert result.cell_observations is composed_populations[0]
    assert len(result.cell_observations) == len(result.final_population) * len(
        result.canonical_samples
    )
    assert all(
        cell.position_associations
        and len(cell.position_associations) == len(cell.predecessor_cells)
        for cell in result.cell_observations
    )
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
    scope, captures, receipt, handoff, predecessor_cells = _finalization_inputs(
        tmp_path, monkeypatch
    )

    forward = finalize_scientific_alignment(
        scope=scope,
        captures=captures,
        grouping_result=receipt,
        cell_observation_handoff=handoff,
        predecessor_cell_observations=predecessor_cells,
    )
    reversed_result = finalize_scientific_alignment(
        scope=scope,
        captures=tuple(reversed(captures)),
        grouping_result=receipt,
        cell_observation_handoff=handoff,
        predecessor_cell_observations=tuple(reversed(predecessor_cells)),
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
    scope, captures, receipt, handoff, predecessor_cells = _finalization_inputs(
        tmp_path, monkeypatch
    )
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
            grouping_result=receipt,
            cell_observation_handoff=handoff,
            predecessor_cell_observations=predecessor_cells,
        )


def test_finalizer_rejects_incomplete_cell_observation_source_population(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, captures, receipt, handoff, predecessor_cells = _finalization_inputs(
        tmp_path, monkeypatch
    )

    with pytest.raises(
        FinalizedScientificAlignmentContractError,
        match="incomplete",
    ):
        finalize_scientific_alignment(
            scope=scope,
            captures=captures,
            grouping_result=receipt,
            cell_observation_handoff=handoff,
            predecessor_cell_observations=predecessor_cells[:-1],
        )


def test_finalizer_rejects_inconclusive_capture_as_no_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, captures, receipt, handoff, predecessor_cells = _finalization_inputs(
        tmp_path, monkeypatch
    )
    binding = scope.capture_bindings[0]
    empty_trace = Trace.from_arrays(
        sample_name=binding.sample_key,
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
            grouping_result=receipt,
            cell_observation_handoff=handoff,
            predecessor_cell_observations=predecessor_cells,
        )


def test_finalizer_rejects_short_window_capture_as_no_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, captures, receipt, handoff, predecessor_cells = _finalization_inputs(
        tmp_path, monkeypatch
    )
    original = captures[0].trace
    rt = np.linspace(original.rt_min, original.rt_max, 4)
    short_trace = Trace.from_arrays(
        sample_name=original.sample_name,
        mz=original.mz,
        rt=rt,
        intensity=np.ones_like(rt),
        rt_min=original.rt_min,
        rt_max=original.rt_max,
        ppm_tol=original.ppm_tol,
        source="finalized_scientific_alignment_short_window",
        acquisition_source_id=original.acquisition_source_id,
    )
    incomplete = capture_peak_hypotheses(
        scope=scope,
        candidate_id=captures[0].candidate_id,
        trace=short_trace,
    )
    assert incomplete.detection.status == "not_detected"
    assert incomplete.detection.result is not None
    assert incomplete.detection.result.status == "WINDOW_TOO_SHORT"

    with pytest.raises(
        FinalizedScientificAlignmentContractError,
        match="not a closed searched-no-observation terminal",
    ):
        finalize_scientific_alignment(
            scope=scope,
            captures=(incomplete, captures[1]),
            grouping_result=receipt,
            cell_observation_handoff=handoff,
            predecessor_cell_observations=predecessor_cells,
        )


def _finalization_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    del monkeypatch
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture, spine, handoff = _owner_handoff(tmp_path)
    request = fixture.owners.request
    receipt = _final_receipt(
        SimpleNamespace(context=SimpleNamespace(processing_owners=fixture.owners)),
        predecessors=handoff.coverage.group_identities,
        successors=handoff.final_population,
    )
    method_subject = spine.cells[0].group_context.method_subject

    rt = np.linspace(7.7, 8.3, 61)
    intensities = tuple(
        100.0 * np.exp(-0.5 * ((rt - 8.0) / 0.06) ** 2)
        if index == 0
        else np.zeros_like(rt)
        for index, _sample in enumerate(fixture.samples)
    )
    candidate_ids = tuple(
        f"candidate-{sample.lookup_token}" for sample in fixture.samples
    )
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
                method_subject_id=method_subject.method_subject_id,
                sample=sample,
                acquisition_source_id=source.acquisition_source_id,
                mz=method_subject.precursor_mz,
                ppm_tol=method_subject.precursor_tolerance_ppm,
                rt_min=float(rt[0]),
                rt_max=float(rt[-1]),
            )
        )
        traces.append(
            Trace.from_arrays(
                sample_name=sample.metadata.sample_name,
                mz=method_subject.precursor_mz,
                rt=rt,
                intensity=intensity,
                rt_min=float(rt[0]),
                rt_max=float(rt[-1]),
                ppm_tol=method_subject.precursor_tolerance_ppm,
                source="finalized_scientific_alignment_test",
                acquisition_source_id=source.acquisition_source_id,
            )
        )
    scope = TargetSearchScope(
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
                method_subject_id=method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=candidate_ids,
        capture_bindings=tuple(bindings),
        target_snapshot=fixture.target.snapshot,
        target=fixture.target,
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
    assert all(capture.selected_hypothesis is None for capture in captures[1:])
    return scope, captures, receipt, handoff, spine.cells
