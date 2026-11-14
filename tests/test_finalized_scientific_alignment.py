"""No-RAW contract tests for the shared scientific-alignment finalizer."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.result.scientific_alignment as finalizer_owner
from tests.cell_observations_fixtures import _owner_handoff
from tests.cross_sample_association_lineage_fixtures import _final_receipt
from tests.owner_peak_fixtures import measured_peak
from xic_extractor.alignment.result import (
    FinalizedScientificAlignment,
    FinalizedScientificAlignmentContractError,
    ObservedSearchSubject,
    SearchedNoObservationSubject,
    finalize_scientific_alignment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import CID_NL_PROVIDER_ID
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    compose_ms1_observation_peak_hypothesis,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    EvidenceProviderBinding,
    TargetSearchScope,
)


def test_finalizer_closes_exact_core_with_terminal_subject_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, terminals, receipt, handoff, predecessor_cells = _finalization_inputs(
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
        terminal_subjects=terminals,
        grouping_result=receipt,
        cell_observation_handoff=handoff,
        predecessor_cell_observations=predecessor_cells,
    )

    assert isinstance(result, FinalizedScientificAlignment)
    assert result.canonical_samples == tuple(
        sorted(
            scope.samples,
            key=lambda sample: sample.sample_name,
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
    scope, terminals, receipt, handoff, predecessor_cells = _finalization_inputs(
        tmp_path, monkeypatch
    )

    forward = finalize_scientific_alignment(
        scope=scope,
        terminal_subjects=terminals,
        grouping_result=receipt,
        cell_observation_handoff=handoff,
        predecessor_cell_observations=predecessor_cells,
    )
    reversed_result = finalize_scientific_alignment(
        scope=scope,
        terminal_subjects=tuple(reversed(terminals)),
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
    scope, terminals, receipt, handoff, predecessor_cells = _finalization_inputs(
        tmp_path, monkeypatch
    )
    if case == "missing":
        invalid = terminals[:-1]
    elif case == "duplicate":
        invalid = terminals + (terminals[0],)

    with pytest.raises(
        FinalizedScientificAlignmentContractError,
        match="terminal",
    ):
        finalize_scientific_alignment(
            scope=scope,
            terminal_subjects=invalid,
            grouping_result=receipt,
            cell_observation_handoff=handoff,
            predecessor_cell_observations=predecessor_cells,
        )


def test_finalizer_rejects_incomplete_cell_observation_source_population(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, terminals, receipt, handoff, predecessor_cells = _finalization_inputs(
        tmp_path, monkeypatch
    )

    with pytest.raises(
        FinalizedScientificAlignmentContractError,
        match="incomplete",
    ):
        finalize_scientific_alignment(
            scope=scope,
            terminal_subjects=terminals,
            grouping_result=receipt,
            cell_observation_handoff=handoff,
            predecessor_cell_observations=predecessor_cells[:-1],
        )


def _finalization_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    del monkeypatch
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture, spine, handoff = _owner_handoff(tmp_path)
    receipt = _final_receipt(
        SimpleNamespace(context=SimpleNamespace(samples=fixture.samples)),
        predecessors=handoff.coverage.groups,
        successors=handoff.final_population,
    )
    method_subject = spine.cells[0].group_context.method_subject

    candidate_ids = tuple(f"candidate-{sample.raw_stem}" for sample in fixture.samples)
    scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        samples=fixture.samples,
        evidence_providers=(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=method_subject.method_subject_id,
            ),
        ),
        declared_candidate_ids=candidate_ids,
        capture_bindings=tuple(
            CaptureCandidateBinding(
                candidate_id=candidate_id,
                method_subject_id=method_subject.method_subject_id,
                sample_key=sample.sample_name,
            )
            for candidate_id, sample in zip(candidate_ids, fixture.samples, strict=True)
        ),
        target_snapshot=fixture.target.snapshot,
        target=fixture.target,
    )
    observed_sample = fixture.samples[0]
    terminals = (
        ObservedSearchSubject(
            candidate_id=candidate_ids[0],
            sample_key=observed_sample.sample_name,
            hypothesis=compose_ms1_observation_peak_hypothesis(
                peak_observation=measured_peak(
                    mz=method_subject.precursor_mz,
                    apex_rt=8.0,
                    sample_key=observed_sample.sample_name,
                )
            ),
        ),
        *(
            SearchedNoObservationSubject(
                candidate_id=candidate_id, sample_key=sample.sample_name
            )
            for candidate_id, sample in zip(
                candidate_ids[1:], fixture.samples[1:], strict=True
            )
        ),
    )
    return scope, terminals, receipt, handoff, spine.cells
