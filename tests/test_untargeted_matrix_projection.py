"""Completed Feature x Sample projection, output identity and route contracts."""

from __future__ import annotations

import json
from collections import Counter
from copy import copy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import xic_extractor.alignment.projection.untarget.identity as projection_owner
import xic_extractor.alignment.result.scientific_alignment as finalizer_owner
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
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.alignment.cell_decision.owner import (
    NoWriteCellDecision,
    decide_group_cell_population,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    mint_cross_sample_group_identity,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.projection.untarget import (
    UntargetMatrixProjection,
    UntargetProjectionContractError,
    project_untarget_matrix,
    validate_untarget_matrix_projection,
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
        detector_invocation_id=(f"gaussian15:{sample.lookup_token}:not-evaluated"),
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
    fixture = build_untargeted_cid_nl_no_raw_fixture(
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
        sample_assessments=tuple(zip(fixture.samples, assessments, strict=True)),
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
                height=200.0,
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
        grouping_result=receipt,
        cell_observation_handoff=handoff,
        predecessor_cell_observations=predecessor_cells,
    )
    return scope, captures, receipt, alignment


def _completed_projection(tmp_path, monkeypatch, *, disposition, numeric=False):
    if numeric:
        from tests.test_alignment_cell_decision import _numeric_peak

        monkeypatch.setattr(
            "tests.test_untargeted_cid_nl_golden_path.cid_nl_peak", _numeric_peak
        )
    _, _, _, alignment = _untarget_projection_inputs(
        tmp_path,
        monkeypatch,
        group_mode="merged" if disposition == "established" else "single",
        disposition=disposition,
    )
    decisions = decide_group_cell_population(alignment)
    return alignment, decisions, project_untarget_matrix(alignment, decisions)


@pytest.mark.parametrize("group_mode", ["single", "two", "merged"])
def test_completed_values_project_to_exact_group_sample_positions(
    tmp_path, monkeypatch, group_mode
):
    from tests.test_alignment_cell_decision import _numeric_peak

    monkeypatch.setattr(
        "tests.test_untargeted_cid_nl_golden_path.cid_nl_peak", _numeric_peak
    )
    _, _, _, alignment = _untarget_projection_inputs(
        tmp_path, monkeypatch, group_mode=group_mode
    )
    answers = decide_group_cell_population(alignment)
    projection = project_untarget_matrix(alignment, answers)
    assert projection.finalized_alignment is alignment
    assert projection.lineage is alignment.lineage
    assert [row.row_key.group for row in projection.rows] == sorted(
        alignment.final_population, key=lambda group: group.group_hypothesis_id
    )
    observations = {
        (item.group_hypothesis_id, item.canonical_sample_identity_id): item
        for item in alignment.cell_observations
    }
    assert len(projection.decisions) == len(observations)
    numeric_by_sample = {}
    for row in projection.rows:
        assert (
            tuple(d.subject.canonical_sample for d in row.decisions)
            == alignment.canonical_samples
        )
        for decision in row.decisions:
            subject = decision.subject
            evidence = observations[
                subject.group_hypothesis_id,
                subject.canonical_sample.identity.sample_identity_id,
            ]
            assert decision.evidence is evidence
            assert subject.row_key is row.row_key
            payload, integration = answers[evidence.cell_observation_id]
            assert decision.payload is payload
            assert decision.selected_integration is integration
            if subject.member_subject is None:
                assert decision.variant == "unavailable"
                assert integration is None
            else:
                assert decision.variant == "numeric"
                assert integration is evidence.integrations[0]
                numeric_by_sample[subject.canonical_sample.metadata.raw_stem] = (
                    decision.value
                )
        counts = Counter(d.variant for d in row.decisions)
        assert {item.variant: item.count for item in row.counts.by_variant} == {
            item.variant: counts[item.variant] for item in row.counts.by_variant
        }
    if group_mode != "single":
        assert numeric_by_sample["sample-c"] == pytest.approx(
            2 * numeric_by_sample["sample-b"]
        )
    validate_untarget_matrix_projection(projection)
    with pytest.raises(FrozenInstanceError):
        projection.rows = ()


@pytest.mark.parametrize(
    "disposition", ["established", "provisional", "unresolved", "rejected"]
)
def test_disposition_projects_row_route_and_complete_counts(
    tmp_path, monkeypatch, disposition
):
    alignment, answers, projection = _completed_projection(
        tmp_path, monkeypatch, disposition=disposition, numeric=True
    )
    (row,) = projection.rows
    assert row.route.reason == disposition
    assert row.route.destination == (
        "matrix" if disposition == "established" else "archive"
    )
    assert row.counts.total_cells == len(alignment.canonical_samples)
    assert row.counts.numeric_cells == (2 if disposition == "established" else 1)
    assert len(projection.included_rows) == int(disposition == "established")
    assert len(projection.archive_rows) == int(disposition != "established")
    reordered = project_untarget_matrix(
        alignment, dict(reversed(tuple(answers.items())))
    )
    assert projection == reordered
    assert tuple(d.decision_id for d in projection.decisions) == tuple(
        d.decision_id for d in reordered.decisions
    )


def test_projection_is_deterministic_for_reversed_capture_order(tmp_path, monkeypatch):
    _, captures, _, forward = _untarget_projection_inputs(tmp_path, monkeypatch)
    reverse = finalizer_owner.finalize_scientific_alignment(
        scope=forward.scope,
        captures=tuple(reversed(captures)),
        grouping_result=forward.grouping_result,
        cell_observation_handoff=forward.cell_observation_handoff,
        predecessor_cell_observations=_predecessor_cells(forward),
    )
    assert project_untarget_matrix(forward, decide_group_cell_population(forward)) == (
        project_untarget_matrix(reverse, decide_group_cell_population(reverse))
    )


def test_row_and_subject_identities_reject_basis_and_crossed_sample(
    tmp_path, monkeypatch
):
    alignment, _, projection = _completed_projection(
        tmp_path, monkeypatch, disposition="established"
    )
    row = projection.rows[0]
    subject = row.decisions[0].subject
    group = row.row_key.group
    assert json.loads(row.row_key.basis_bytes) == {
        "group_hypothesis_id": group.group_hypothesis_id,
        "group_identity_basis_hex": group.basis_bytes.hex(),
        "processing_request_id": alignment.processing_request_id,
        "schema_version": "untarget_row_key",
    }
    assert row.row_key.group_identity_basis_bytes == group.basis_bytes
    with pytest.raises(UntargetProjectionContractError, match="basis"):
        replace(row.row_key, basis_bytes=b"{}")
    with pytest.raises(UntargetProjectionContractError, match="sample|member"):
        replace(subject, canonical_sample=alignment.canonical_samples[1])


@pytest.mark.parametrize("variant", ["missing", "extra", "crossed"])
def test_projection_requires_answers_for_the_exact_observation_population(
    tmp_path, monkeypatch, variant
):
    alignment, answers, _ = _completed_projection(
        tmp_path / "owner", monkeypatch, disposition="established"
    )
    changed = dict(answers)
    if variant == "missing":
        changed.pop(next(iter(changed)))
    elif variant == "extra":
        changed["other-observation"] = next(iter(changed.values()))
    else:
        _, changed, _ = _completed_projection(
            tmp_path / "other", monkeypatch, disposition="established"
        )
    with pytest.raises(UntargetProjectionContractError, match="missing|extra|crossed"):
        project_untarget_matrix(alignment, changed)


def test_explicit_projection_audit_rejects_substituted_answer(tmp_path, monkeypatch):
    _, _, projection = _completed_projection(
        tmp_path, monkeypatch, disposition="established"
    )
    row = copy(projection.rows[0])
    decision = copy(row.decisions[0])
    object.__setattr__(decision, "payload", NoWriteCellDecision("unavailable"))
    object.__setattr__(row, "decisions", (decision,) + row.decisions[1:])
    audited = copy(projection)
    object.__setattr__(audited, "rows", (row,))
    with pytest.raises(UntargetProjectionContractError, match="substituted|basis"):
        validate_untarget_matrix_projection(audited)


def test_projection_cannot_be_directly_minted(tmp_path, monkeypatch):
    alignment, _, projection = _completed_projection(
        tmp_path, monkeypatch, disposition="established"
    )
    with pytest.raises(UntargetProjectionContractError, match="must be minted"):
        UntargetMatrixProjection(
            finalized_alignment=alignment, rows=projection.rows, _mint_token=object()
        )


def test_projection_rejects_row_id_collision_with_unequal_basis(tmp_path, monkeypatch):
    alignment = _two_group_lineaged_alignment(tmp_path, monkeypatch)
    monkeypatch.setattr(
        projection_owner,
        "_untarget_row_key_id",
        lambda _: "xic.untarget-row-key:forced-collision",
    )
    with pytest.raises(UntargetProjectionContractError, match="collision"):
        project_untarget_matrix(alignment, decide_group_cell_population(alignment))
