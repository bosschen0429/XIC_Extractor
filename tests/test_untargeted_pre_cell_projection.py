"""No-RAW contract tests for the Untargeted pre-cell projection."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import xic_extractor.alignment.projection.untarget.pre_cell as projection_owner
import xic_extractor.alignment.result.scientific_alignment as finalizer_owner
from tests.issue175_no_raw_fixture import build_issue175_no_raw_fixture
from tests.test_cell_observations import (
    _coverage_for_groups,
    _receipt_fixture,
)
from tests.test_cid_nl_evidence_spine import _completed_receipt
from tests.test_cross_sample_association_lineage import (
    _final_receipt as _lineage_final_receipt,
)
from tests.test_search_scope_capture import _capture_binding, _peak_config
from tests.test_untargeted_cid_nl_golden_path import (
    _support_input,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cross_sample import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    mint_cross_sample_group_identity,
    validate_cross_sample_peak_group_identity,
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
)
from xic_extractor.peak_detection.capture import capture_peak_hypotheses
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    mint_cid_nl_method_assessment_ref,
)
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


def _finalized_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _scope, _captures, _receipt, alignment = _untarget_projection_inputs(
        tmp_path, monkeypatch
    )
    return alignment


def _two_group_lineaged_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> FinalizedScientificAlignment:
    _scope, _captures, _receipt, alignment = _untarget_projection_inputs(
        tmp_path / "alignment",
        monkeypatch,
        group_mode="two",
    )
    assert len(alignment.final_population) == 2
    return alignment


def _predecessor_cells(base: FinalizedScientificAlignment):
    predecessor_cells = []
    seen = set()
    for cell in base.cell_observations:
        for predecessor_cell in cell.predecessor_cells:
            if id(predecessor_cell) not in seen:
                seen.add(id(predecessor_cell))
                predecessor_cells.append(predecessor_cell)
    return tuple(predecessor_cells)


def _not_evaluated_assessment(fixture, sample):
    receipt = replace(
        _completed_receipt(scanned_event_count=0),
        search_completed=False,
        reason_code="search_not_evaluated",
    )
    return mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        method_subject=fixture.method_subject,
        detector_invocation_id=(
            f"gaussian15:{sample.lookup_token}:not-evaluated"
        ),
        evaluation_receipt=receipt,
        technical_state="not_evaluated",
    )


def _untarget_projection_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    group_mode: str = "single",
    disposition: str | None = None,
):
    """Build the pre-existing Untarget projection fixture without re-owning result."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    scope_path = tmp_path / "scope"
    scope_path.mkdir()
    fixture = build_issue175_no_raw_fixture(
        scope_path,
        sample_roles={"sample-b": "qc", "sample-c": "study_sample"},
    )
    request = fixture.owners.request
    assessments = (
        _support_input(
            fixture,
            fixture.samples[0].lookup_token,
            scan_number=90,
            height=100.0,
        ).assessment,
        _not_evaluated_assessment(fixture, fixture.samples[1]),
    )
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        method_subject=fixture.method_subject,
        sample_assessments=tuple(
            zip(fixture.samples, assessments, strict=True)
        ),
    )
    predecessor = validate_cross_sample_peak_group_identity(
        spine.group,
        require_canonical=True,
    )
    predecessor_groups = (predecessor,)
    predecessor_cells = spine.cells
    successors = predecessor_groups
    if group_mode in {"two", "merged"}:
        right_assessments = (
            _not_evaluated_assessment(fixture, fixture.samples[0]),
            _support_input(
                fixture,
                fixture.samples[1].lookup_token,
                scan_number=92,
                height=100.0,
            ).assessment,
        )
        right_spine = build_cid_nl_reference_spine(
            processing_owners=fixture.owners,
            method_subject=fixture.method_subject,
            sample_assessments=tuple(
                zip(fixture.samples, right_assessments, strict=True)
            ),
        )
        right = validate_cross_sample_peak_group_identity(
            right_spine.group,
            require_canonical=True,
        )
        predecessor_groups = tuple(
            sorted(
                (predecessor, right),
                key=lambda group: group.group_hypothesis_id,
            )
        )
        predecessor_cells = spine.cells + right_spine.cells
        if group_mode == "two":
            successors = predecessor_groups
        else:
            successors = (
                mint_cross_sample_group_identity(
                    processing_request_id=request.processing_request_id,
                    member_subjects=tuple(
                        sorted(
                            (
                                member
                                for group in predecessor_groups
                                for member in group.member_subjects
                            ),
                            key=lambda member: member.member_subject_id,
                        )
                    ),
                ),
            )
    if disposition is not None:
        successors = tuple(
            mint_cross_sample_group_identity(
                processing_request_id=group.processing_request_id,
                member_subjects=group.member_subjects,
                disposition=disposition,  # type: ignore[arg-type]
            )
            for group in successors
        )
    cell_fixture = SimpleNamespace(
        owners=fixture.owners,
        samples=fixture.samples,
        request=fixture.owners.request,
    )
    receipt = _lineage_final_receipt(
        _receipt_fixture(cell_fixture, fixture.samples),
        predecessors=predecessor_groups,
        successors=successors,
    )
    coverage = _coverage_for_groups(
        cell_fixture,
        predecessor_groups,
        fixture.samples,
        planned=True,
    )
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
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
        candidate_ids, fixture.samples, intensities, strict=True
    ):
        source = resolve_request_acquisition_source(
            processing_owners=fixture.owners,
            sample=sample,
        )
        bindings.append(
            _capture_binding(
                candidate_id=candidate_id,
                method_subject_id=fixture.method_subject.method_subject_id,
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
                source="untarget_projection_fixture",
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
        capture_peak_hypotheses(scope=scope, candidate_id=item, trace=trace)
        for item, trace in zip(candidate_ids, traces, strict=True)
    )
    alignment = finalizer_owner.finalize_scientific_alignment(
        scope=scope,
        captures=captures,
        final_cross_sample_receipt=receipt,
        cell_observation_handoff=handoff,
        predecessor_cell_observations=predecessor_cells,
    )
    return scope, captures, receipt, alignment


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
    _scope, _captures, receipt, forward = _untarget_projection_inputs(
        tmp_path, monkeypatch
    )
    reversed_alignment = finalizer_owner.finalize_scientific_alignment(
        scope=forward.scope,
        captures=tuple(reversed(_captures)),
        final_cross_sample_receipt=forward.final_cross_sample_receipt,
        cell_observation_handoff=forward.cell_observation_handoff,
        predecessor_cell_observations=_predecessor_cells(forward),
    )

    assert project_untarget_pre_cell(forward) == project_untarget_pre_cell(
        reversed_alignment
    )


def test_each_closed_disposition_gets_a_row_and_every_sample_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for disposition in ("established", "provisional", "unresolved", "rejected"):
        _scope, _captures, _receipt, alignment = _untarget_projection_inputs(
            tmp_path / disposition,
            monkeypatch,
            group_mode="merged",
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
    _scope, _captures, _receipt, alignment = _untarget_projection_inputs(
        tmp_path,
        monkeypatch,
        group_mode="merged",
    )
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
    _scope, _captures, _receipt, alignment = _untarget_projection_inputs(
        tmp_path,
        monkeypatch,
        group_mode="merged",
    )
    edge = alignment.final_cross_sample_receipt.post_mt0_application.lineage[0]
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
