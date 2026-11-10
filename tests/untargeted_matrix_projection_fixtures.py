"""Helpers shared by test_untargeted_matrix_projection and other test modules."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import xic_extractor.alignment.result.scientific_alignment as finalizer_owner
from tests.cell_observations_fixtures import (
    _coverage_for_groups,
    _receipt_fixture,
)
from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_peak,
    cid_nl_support_policy,
    cid_nl_unique_support,
)
from tests.cross_sample_association_lineage_fixtures import (
    _final_receipt as _lineage_final_receipt,
)
from tests.group_fixtures import group_from_members
from tests.search_scope_capture_fixtures import _capture_binding, _peak_config
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
from xic_extractor.alignment.projection.untarget import (
    project_untarget_matrix,
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
    # CID-NL composition leaves unsearched samples out of the spine; their
    # coverage positions carry no discovery input.
    spine = build_cid_nl_reference_spine(
        method_subject=fixture.method_subject,
        sample_assessments=(
            (
                fixture.samples[0],
                _support_input(
                    fixture,
                    fixture.samples[0].raw_stem,
                    scan_number=90,
                    height=100.0,
                ).assessment,
            ),
        ),
    )
    predecessor = spine.group
    predecessor_groups = (predecessor,)
    predecessor_cells = spine.cells
    successors = predecessor_groups
    if group_mode in {"two", "merged"}:
        right_spine = build_cid_nl_reference_spine(
            method_subject=fixture.method_subject,
            sample_assessments=(
                (
                    fixture.samples[1],
                    _support_input(
                        fixture,
                        fixture.samples[1].raw_stem,
                        scan_number=92,
                        height=200.0,
                    ).assessment,
                ),
            ),
        )
        right = right_spine.group
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
                group_from_members(
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
            group_from_members(
                member_subjects=group.member_subjects,
                disposition=disposition,  # type: ignore[arg-type]
            )
            for group in successors
        )
    cell_fixture = fixture
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
        source = fixture.source(sample)
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
                sample_name=sample.sample_name,
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
        method_subjects=fixture.method_subject,
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
        from tests.alignment_cell_decision_fixtures import _numeric_peak

        monkeypatch.setattr(
            "tests.untargeted_matrix_projection_fixtures.cid_nl_peak", _numeric_peak
        )
    _, _, _, alignment = _untarget_projection_inputs(
        tmp_path,
        monkeypatch,
        group_mode="merged" if disposition == "established" else "single",
        disposition=disposition,
    )
    decisions = decide_group_cell_population(alignment)
    return alignment, decisions, project_untarget_matrix(alignment, decisions)


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
        sample=fixture.sample(sample_stem),
        acquisition_source=fixture.source(fixture.sample(sample_stem)),
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
