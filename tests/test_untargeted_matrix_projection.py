"""Completed Feature x Sample projection, output identity and route contracts."""

from __future__ import annotations

from collections import Counter
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import xic_extractor.alignment.result.scientific_alignment as finalizer_owner
from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_peak,
    cid_nl_support_policy,
    cid_nl_unique_support,
)
from tests.test_cell_observations import (
    _coverage_for_groups,
    _receipt_fixture,
)
from tests.test_cross_sample_association_lineage import (
    _final_receipt as _lineage_final_receipt,
)
from tests.test_search_scope_capture import _capture_binding, _peak_config
from tests.untargeted_cid_nl_no_raw_fixture import (
    UntargetedCidNlNoRawFixture,
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.alignment.cell_decision.owner import (
    decide_group_cell_population,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cid_nl_inputs import UntargetedCidNlAssessmentInput
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.projection.untarget import (
    UntargetProjectionContractError,
    project_untarget_matrix,
)
from xic_extractor.alignment.result import (
    FinalizedScientificAlignment,
)
from xic_extractor.peak_detection.capture import capture_peak_hypotheses
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
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
    # CID-NL composition leaves unsearched samples out of the spine; their
    # coverage positions carry no discovery input.
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        method_subject=fixture.method_subject,
        sample_assessments=(
            (
                fixture.samples[0],
                _support_input(
                    fixture,
                    fixture.samples[0].lookup_token,
                    scan_number=90,
                    height=100.0,
                ).assessment,
            ),
        ),
    )
    predecessor = spine.group.membership
    predecessor_groups = (predecessor,)
    predecessor_cells = spine.cells
    successors = predecessor_groups
    if group_mode in {"two", "merged"}:
        right_spine = build_cid_nl_reference_spine(
            processing_owners=fixture.owners,
            method_subject=fixture.method_subject,
            sample_assessments=(
                (
                    fixture.samples[1],
                    _support_input(
                        fixture,
                        fixture.samples[1].lookup_token,
                        scan_number=92,
                        height=200.0,
                    ).assessment,
                ),
            ),
        )
        right = right_spine.group.membership
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
                    member_subjects=tuple(
                        sorted(
                            (
                                member
                                for group in predecessor_groups
                                for member in group.member_subjects
                            ),
                            key=lambda member: member.sample_key,
                        )
                    ),
                ),
            )
    if disposition is not None:
        successors = tuple(
            mint_cross_sample_group_identity(
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
        predecessor_cells=predecessor_cells,
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
            "tests.test_untargeted_matrix_projection.cid_nl_peak", _numeric_peak
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
        "tests.test_untargeted_matrix_projection.cid_nl_peak", _numeric_peak
    )
    _, _, _, alignment = _untarget_projection_inputs(
        tmp_path, monkeypatch, group_mode=group_mode
    )
    answers = decide_group_cell_population(alignment)
    projection = project_untarget_matrix(alignment, answers)
    assert projection.finalized_alignment is alignment
    assert projection.lineage is alignment.lineage
    assert [row.group for row in projection.rows] == sorted(
        alignment.final_population, key=lambda group: group.group_hypothesis_id
    )
    observations = {
        (item.group_hypothesis_id, item.sample_key): item
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
                subject.canonical_sample.identity.sample_key,
            ]
            assert decision.evidence is evidence
            assert subject.group is row.group
            payload, integration = answers[evidence.key]
            assert decision.payload is payload
            assert decision.selected_integration is integration
            if subject.member_subject is None:
                assert decision.variant == "not_searched"
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
        group, sample_key = next(iter(changed))
        changed[group, sample_key + "-other"] = changed.pop((group, sample_key))
    with pytest.raises(UntargetProjectionContractError, match="missing|extra|crossed"):
        project_untarget_matrix(alignment, changed)


def _support_input(
    fixture: UntargetedCidNlNoRawFixture,
    sample_stem: str,
    *,
    scan_number: int,
    height: float,
) -> UntargetedCidNlAssessmentInput:
    peak = cid_nl_peak(fixture, sample_stem, height=height)
    evidence = cid_nl_evidence(
        fixture,
        sample_stem,
        scan_number=scan_number,
        scan_rt_min=8.0,
    )
    support = cid_nl_unique_support(
        fixture,
        sample_stem,
        peak=peak,
        evidence=evidence,
    )
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        method_subject=fixture.method_subject,
        detector_invocation_id=f"gaussian15:{sample_stem}",
        evaluation_receipt=_completed_receipt(),
        candidate_peaks=(peak,),
        evidence_refs=(evidence,),
        individual_support_refs=(support,),
    )
    return UntargetedCidNlAssessmentInput(
        sample=fixture.sample(sample_stem),
        assessment=assessment,
    )


def _completed_receipt() -> CidNlEvaluationReceipt:
    return CidNlEvaluationReceipt(
        query_rt_min=7.5,
        query_rt_max=8.6,
        support_policy=cid_nl_support_policy(),
        acquisition_supported=True,
        search_completed=True,
        reason_code=None,
    )
