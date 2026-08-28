"""No-RAW contract tests for the Untargeted pre-cell projection."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import xic_extractor.alignment.cross_sample_reconciliation as reconciliation
import xic_extractor.alignment.projection.untarget.pre_cell as projection_owner
from tests.test_backfill_observation_repair import _RepairFixture
from tests.test_cross_sample_reconciliation import _scenario
from tests.test_finalized_scientific_alignment import _finalization_inputs
from xic_extractor.alignment.backfill import (
    close_observation_repair,
    mint_observation_repair_subject,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleSuccessorApplication,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    reconcile_observation_repair,
)
from xic_extractor.alignment.mode_track.contracts import (
    mint_accepted_mode_track_refinement,
)
from xic_extractor.alignment.projection.untarget import (
    GroupCellSubject,
    UntargetPreCellProjection,
    UntargetProjectionContractError,
    UntargetRowKey,
    project_untarget_pre_cell,
)
from xic_extractor.alignment.result import (
    FinalizedScientificAlignment,
    finalize_scientific_alignment,
)
from xic_extractor.processing_identity import build_processing_request_identity


def _finalized_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    scope, captures, receipt = _finalization_inputs(tmp_path, monkeypatch)
    return finalize_scientific_alignment(
        scope=scope,
        captures=captures,
        final_cross_sample_receipt=receipt,
    )


def _two_group_lineaged_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> FinalizedScientificAlignment:
    scope, captures, _ = _finalization_inputs(tmp_path / "alignment", monkeypatch)
    fixture, context, repair = _scenario(
        tmp_path / "receipt",
        multi_member=True,
        changed=True,
    )
    member_ids = tuple(
        sorted(member.member_subject_id for member in fixture.group.member_subjects)
    )
    partition = tuple((member_id,) for member_id in member_ids)

    def accepting_proposal(*, trajectory_input, successor_member_subject_ids):
        return mint_accepted_mode_track_refinement(
            trajectory_input=trajectory_input,
            successor_member_subject_ids=successor_member_subject_ids,
        )

    monkeypatch.setattr(
        reconciliation,
        "propose_mode_track_successor_partition",
        accepting_proposal,
    )
    receipt = reconcile_observation_repair(
        context=context,
        repair_receipt=repair,
        mode_track_successor_member_subject_ids=partition,
    )
    alignment = finalize_scientific_alignment(
        scope=scope,
        captures=captures,
        final_cross_sample_receipt=receipt,
    )
    assert len(alignment.final_population) == 2
    assert len(alignment.lineage) == 2
    return alignment


def _projection_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    scope, captures, _ = _finalization_inputs(tmp_path / "alignment", monkeypatch)
    fixture, context, _ = _scenario(tmp_path / "receipt", multi_member=True)
    return scope, captures, fixture, context


def _finalized_with_disposition(
    *,
    scope,
    captures,
    fixture: _RepairFixture,
    context,
    disposition: str,
) -> FinalizedScientificAlignment:
    assert fixture.other_member is not None
    group = mint_cross_sample_group_identity(
        processing_request_id=fixture.group.processing_request_id,
        member_subjects=fixture.group.member_subjects,
        disposition=disposition,  # type: ignore[arg-type]
    )
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=(group,),
        successors=(group,),
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )
    group_context = replace(
        context,
        population=(group,),
        application=application,
    )
    subject = mint_observation_repair_subject(
        request_identity=fixture.observation.request_identity,
        logical_request=fixture.observation.logical_request,
        predecessor_group=group,
        member_subject=fixture.member,
        sample_identity=fixture.sample_identity,
        successor_application=application,
    )
    repair = close_observation_repair(subject, fixture.result)
    receipt = reconcile_observation_repair(
        context=group_context,
        repair_receipt=repair,
    )
    return finalize_scientific_alignment(
        scope=scope,
        captures=captures,
        final_cross_sample_receipt=receipt,
    )


def test_entry_projects_exact_rows_and_complete_group_sample_subjects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _finalized_alignment(tmp_path, monkeypatch)

    projection = project_untarget_pre_cell(alignment)

    assert isinstance(projection, UntargetPreCellProjection)
    assert projection.finalized_alignment is alignment
    assert projection.processing_request is alignment.processing_request
    assert projection.processing_request_id == alignment.processing_request_id
    assert projection.final_population == alignment.final_population
    assert projection.lineage == alignment.lineage
    assert len(projection.row_keys) == len(alignment.final_population)
    assert len(projection.cell_subjects) == (
        len(alignment.final_population) * len(alignment.canonical_samples)
    )
    assert {
        (
            cell.group.group_hypothesis_id,
            cell.canonical_sample.identity.sample_identity_id,
        )
        for cell in projection.cell_subjects
    } == {
        (group.group_hypothesis_id, sample.identity.sample_identity_id)
        for group in alignment.final_population
        for sample in alignment.canonical_samples
    }
    assert all(
        cell.member_subject is None
        or cell.member_subject in cell.group.member_subjects
        for cell in projection.cell_subjects
    )


def test_entry_projects_complete_group_sample_population_for_two_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _two_group_lineaged_alignment(tmp_path, monkeypatch)

    projection = project_untarget_pre_cell(alignment)

    assert len(projection.row_keys) == len(alignment.final_population)
    assert len(projection.cell_subjects) == (
        len(alignment.final_population) * len(alignment.canonical_samples)
    )
    assert {
        (
            cell.group.group_hypothesis_id,
            cell.canonical_sample.identity.sample_identity_id,
        )
        for cell in projection.cell_subjects
    } == {
        (group.group_hypothesis_id, sample.identity.sample_identity_id)
        for group in alignment.final_population
        for sample in alignment.canonical_samples
    }


def test_entry_is_deterministic_for_reversed_capture_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, captures, receipt = _finalization_inputs(tmp_path, monkeypatch)
    forward = finalize_scientific_alignment(
        scope=scope,
        captures=captures,
        final_cross_sample_receipt=receipt,
    )
    reversed_alignment = finalize_scientific_alignment(
        scope=scope,
        captures=tuple(reversed(captures)),
        final_cross_sample_receipt=receipt,
    )

    assert project_untarget_pre_cell(forward) == project_untarget_pre_cell(
        reversed_alignment
    )


def test_each_closed_disposition_gets_a_row_and_every_sample_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, captures, fixture, context = _projection_inputs(tmp_path, monkeypatch)

    for disposition in ("established", "provisional", "unresolved", "rejected"):
        alignment = _finalized_with_disposition(
            scope=scope,
            captures=captures,
            fixture=fixture,
            context=context,
            disposition=disposition,
        )
        projection = project_untarget_pre_cell(alignment)

        assert projection.final_population[0].disposition == disposition
        assert len(projection.row_keys) == 1
        assert len(projection.cell_subjects) == len(alignment.canonical_samples)
        assert all(
            isinstance(subject, GroupCellSubject)
            for subject in projection.cell_subjects
        )


def test_row_and_subject_contracts_reject_basis_and_crossed_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _finalized_alignment(tmp_path, monkeypatch)
    projection = project_untarget_pre_cell(alignment)
    row_key = projection.row_keys[0]
    subject = projection.cell_subjects[0]

    with pytest.raises(UntargetProjectionContractError, match="basis"):
        replace(row_key, basis_bytes=b"{}")

    other_sample = next(
        sample
        for sample in alignment.canonical_samples
        if sample.identity.sample_identity_id
        != subject.canonical_sample.identity.sample_identity_id
    )
    with pytest.raises(UntargetProjectionContractError, match="sample|member"):
        replace(subject, canonical_sample=other_sample)


def test_entry_rejects_crossed_processing_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _finalized_alignment(tmp_path, monkeypatch)
    crossed_inputs = json.loads(
        alignment.processing_request.normalized_scientific_inputs_json
    )
    crossed_inputs["projection_crossed_request"] = True
    crossed_request = build_processing_request_identity(
        acquisition_sources=alignment.processing_request.acquisition_sources,
        acquisition_bindings=alignment.processing_request.acquisition_bindings,
        scientific_input_schema_version=(
            alignment.processing_request.scientific_input_schema_version
        ),
        processing_algorithm_version=(
            alignment.processing_request.processing_algorithm_version
        ),
        processing_contract_version=(
            alignment.processing_request.processing_contract_version
        ),
        scientific_inputs=crossed_inputs,
    )
    object.__setattr__(alignment, "processing_request", crossed_request)

    with pytest.raises(UntargetProjectionContractError, match="request"):
        project_untarget_pre_cell(alignment)


def test_entry_rejects_receipt_crossed_processing_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _finalized_alignment(tmp_path, monkeypatch)
    crossed_inputs = json.loads(
        alignment.processing_request.normalized_scientific_inputs_json
    )
    crossed_inputs["projection_receipt_crossed_request"] = True
    crossed_request = build_processing_request_identity(
        acquisition_sources=alignment.processing_request.acquisition_sources,
        acquisition_bindings=alignment.processing_request.acquisition_bindings,
        scientific_input_schema_version=(
            alignment.processing_request.scientific_input_schema_version
        ),
        processing_algorithm_version=(
            alignment.processing_request.processing_algorithm_version
        ),
        processing_contract_version=(
            alignment.processing_request.processing_contract_version
        ),
        scientific_inputs=crossed_inputs,
    )
    assert (
        alignment.final_cross_sample_receipt.processing_request_id
        != crossed_request.processing_request_id
    )
    object.__setattr__(alignment.scope, "request", crossed_request)
    object.__setattr__(alignment, "processing_request", crossed_request)

    with pytest.raises(UntargetProjectionContractError, match="request"):
        project_untarget_pre_cell(alignment)


def test_entry_revalidates_owner_group_member_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _finalized_alignment(tmp_path, monkeypatch)
    group = alignment.final_population[0]
    assert len(group.member_subjects) > 1
    original_id = group.group_hypothesis_id
    original_basis = group.basis_bytes
    object.__setattr__(
        group,
        "member_subjects",
        tuple(reversed(group.member_subjects)),
    )
    assert group.group_hypothesis_id == original_id
    assert group.basis_bytes == original_basis

    with pytest.raises(UntargetProjectionContractError, match="group|member"):
        project_untarget_pre_cell(alignment)


def test_entry_rejects_row_id_collision_with_unequal_basis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _two_group_lineaged_alignment(tmp_path, monkeypatch)
    monkeypatch.setattr(
        projection_owner,
        "_untarget_row_key_id",
        lambda _basis: "xic.untarget-row-key.v1:forced-collision",
    )

    with pytest.raises(UntargetProjectionContractError, match="collision"):
        project_untarget_pre_cell(alignment)


def test_entry_revalidates_nested_member_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _finalized_alignment(tmp_path, monkeypatch)
    group = alignment.final_population[0]
    member = group.member_subjects[0]
    original_group_id = group.group_hypothesis_id
    original_group_basis = group.basis_bytes
    object.__setattr__(member, "canonical_sample_identity_id", "forged-sample")
    assert group.group_hypothesis_id == original_group_id
    assert group.basis_bytes == original_group_basis

    with pytest.raises(UntargetProjectionContractError, match="member|group"):
        project_untarget_pre_cell(alignment)


def test_entry_revalidates_nested_lineage_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _two_group_lineaged_alignment(tmp_path, monkeypatch)
    edge = alignment.lineage[0]
    original_edge_id = edge.lineage_edge_id
    object.__setattr__(edge, "basis_bytes", b"forged-lineage-basis")
    assert edge.lineage_edge_id == original_edge_id

    with pytest.raises(UntargetProjectionContractError, match="lineage"):
        project_untarget_pre_cell(alignment)


def test_row_identity_basis_is_closed_to_request_and_group_identity_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _finalized_alignment(tmp_path, monkeypatch)
    projection = project_untarget_pre_cell(alignment)
    row_key = projection.row_keys[0]
    group = projection.final_population[0]

    assert json.loads(row_key.basis_bytes.decode("utf-8")) == {
        "group_hypothesis_id": group.group_hypothesis_id,
        "group_identity_basis_hex": group.basis_bytes.hex(),
        "processing_request_id": alignment.processing_request_id,
        "schema_version": "untarget_row_key_v1",
    }
    assert row_key.group_identity_basis_bytes == group.basis_bytes


def test_entry_rejects_incomplete_group_sample_population(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _finalized_alignment(tmp_path, monkeypatch)
    original = projection_owner._build_cell_subjects
    calls = 0

    def missing_one(**kwargs):
        nonlocal calls
        calls += 1
        subjects = original(**kwargs)
        return subjects[:-1] if calls == 1 else subjects

    monkeypatch.setattr(projection_owner, "_build_cell_subjects", missing_one)

    with pytest.raises(UntargetProjectionContractError, match="Group×sample"):
        project_untarget_pre_cell(alignment)
    assert calls >= 2


def test_projection_values_are_immutable_and_direct_result_mint_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alignment = _finalized_alignment(tmp_path, monkeypatch)
    projection = project_untarget_pre_cell(alignment)

    with pytest.raises(FrozenInstanceError):
        projection.row_keys = ()  # type: ignore[misc]
    with pytest.raises(UntargetProjectionContractError, match="minted"):
        UntargetPreCellProjection(
            finalized_alignment=alignment,
            processing_request=alignment.processing_request,
            canonical_samples=alignment.canonical_samples,
            final_population=alignment.final_population,
            row_keys=projection.row_keys,
            cell_subjects=projection.cell_subjects,
            lineage=alignment.lineage,
            _mint_token=object(),
        )


def test_entry_rejects_non_finalized_input() -> None:
    with pytest.raises(UntargetProjectionContractError, match="Finalized"):
        project_untarget_pre_cell(object())  # type: ignore[arg-type]


def test_public_row_key_is_typed_and_keeps_group_basis() -> None:
    assert hasattr(UntargetRowKey, "group_identity_basis_bytes")
