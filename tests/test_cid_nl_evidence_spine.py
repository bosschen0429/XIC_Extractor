from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from tests.issue161_no_raw_fixture import (
    issue161_evidence,
    issue161_peak,
    issue161_support_policy,
    issue161_unique_support,
)
from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
from xic_extractor.alignment import cid_nl_evidence_spine as spine_module
from xic_extractor.alignment.cell_observations import (
    CellObservationContractError,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    CidNlEvidenceSpineError,
    build_cid_nl_reference_spine,
)
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelationContractError,
    resolve_dda_cid_nl_candidate_set_relation_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentContractError,
    mint_cid_nl_method_assessment_ref,
    validate_cid_nl_method_assessment_binding,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportContractError,
)


def test_s1_to_s5_truth_table_preserves_orthogonal_axes(tmp_path: Path) -> None:
    fixture, assessments = _truth_table_fixture(tmp_path)
    by_sample = {
        assessment.content.canonical_sample_identity_id: assessment
        for assessment in assessments
    }

    assert by_sample[
        fixture.sample("s1").identity.sample_identity_id
    ].content.method_result == ("SUPPORT")
    assert by_sample[
        fixture.sample("s2").identity.sample_identity_id
    ].content.method_result == ("HOLD")
    assert by_sample[
        fixture.sample("s3").identity.sample_identity_id
    ].content.method_result == ("REJECT")
    assert by_sample[
        fixture.sample("s4").identity.sample_identity_id
    ].content.method_result == ("HOLD")
    s5 = by_sample[fixture.sample("s5").identity.sample_identity_id]
    assert s5.content.technical_state == "unsupported"
    assert s5.content.method_result is None
    assert all(not hasattr(assessment, "peak_hypothesis") for assessment in assessments)

    for assessment in assessments:
        validate_cid_nl_method_assessment_binding(
            assessment,
            processing_owners=fixture.owners,
            sample=next(
                sample
                for sample in fixture.samples
                if sample.identity.sample_identity_id
                == assessment.content.canonical_sample_identity_id
            ),
            target=fixture.target,
        )


def test_set_relation_is_order_neutral_and_never_votes_twice(tmp_path: Path) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("split",))
    left = issue161_peak(
        fixture,
        "split",
        apex_rt=7.9,
        rt_left_min=7.8,
        rt_right_min=8.0,
    )
    right = issue161_peak(
        fixture,
        "split",
        apex_rt=8.1,
        rt_left_min=8.0,
        rt_right_min=8.2,
    )
    evidence = issue161_evidence(
        fixture,
        "split",
        scan_number=44,
        scan_rt_min=8.0,
    )

    forward = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("split"),
        canonical_evidence=evidence,
        candidate_peaks=(left, right),
        policy=issue161_support_policy(),
    )
    reverse = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("split"),
        canonical_evidence=evidence,
        candidate_peaks=(right, left),
        policy=issue161_support_policy(),
    )

    assert forward == reverse
    assert forward.content.relationship == "supports_one_or_more_of"
    assert forward.content.matched_candidate_content_ids == tuple(
        sorted(
            (
                left.content.peak_observation_content_id,
                right.content.peak_observation_content_id,
            )
        )
    )
    assert not hasattr(forward, "individual_support_votes")


def test_zero_buffer_boundary_is_set_level_hold(tmp_path: Path) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("edge",))
    peak = issue161_peak(fixture, "edge")
    evidence = issue161_evidence(
        fixture,
        "edge",
        scan_number=45,
        scan_rt_min=peak.content.integration.rt_left_min,
    )
    relation = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("edge"),
        canonical_evidence=evidence,
        candidate_peaks=(peak,),
        policy=issue161_support_policy(),
    )
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("edge"),
        target=fixture.target,
        detector_invocation_id="gaussian15:edge:v1",
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        candidate_peaks=(peak,),
        evidence_refs=(evidence,),
        set_relation_refs=(relation,),
    )

    assert relation.content.matched_candidate_content_ids == (
        peak.content.peak_observation_content_id,
    )
    assert assessment.content.method_result == "HOLD"
    assert assessment.individual_support_refs == ()


def test_reference_spine_mints_once_and_fans_out_same_cell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, assessments = _truth_table_fixture(tmp_path)
    calls: list[str] = []
    mint_counts = {"hypothesis": 0, "group": 0, "cell": 0}

    def forbidden(*_args, **_kwargs):
        calls.append("writer")
        raise AssertionError("diagnostic evidence spine invoked product output")

    for path in (
        "xic_extractor.extraction.output_dispatch.write_outputs",
        "xic_extractor.output.csv_writers.write_wide_csv",
        "xic_extractor.output.csv_writers.write_long_csv",
        "xic_extractor.output.csv_writers.write_diagnostics_csv",
        "xic_extractor.output.csv_writers.write_score_breakdown_csv",
        "xic_extractor.output.workbook_builder.write_workbook_from_rows",
        "xic_extractor.alignment.tsv_writer.write_alignment_matrix_tsv",
        "xic_extractor.alignment.xlsx_writer.write_alignment_results_xlsx",
        "xic_extractor.alignment.product_matrix.build_product_matrix_rows",
        "xic_extractor.alignment.pipeline_outputs.write_outputs_atomic",
    ):
        monkeypatch.setattr(path, forbidden)

    original_hypothesis = spine_module.compose_cid_nl_peak_hypothesis
    original_group = spine_module.construct_cross_sample_peak_group_hypotheses
    original_cell = spine_module.mint_cell_observation_ref

    def counting_hypothesis(*args, **kwargs):
        mint_counts["hypothesis"] += 1
        return original_hypothesis(*args, **kwargs)

    def counting_group(*args, **kwargs):
        mint_counts["group"] += 1
        return original_group(*args, **kwargs)

    def counting_cell(*args, **kwargs):
        mint_counts["cell"] += 1
        return original_cell(*args, **kwargs)

    monkeypatch.setattr(
        spine_module, "compose_cid_nl_peak_hypothesis", counting_hypothesis
    )
    monkeypatch.setattr(
        spine_module,
        "construct_cross_sample_peak_group_hypotheses",
        counting_group,
    )
    monkeypatch.setattr(spine_module, "mint_cell_observation_ref", counting_cell)

    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=tuple(
            (fixture.sample(sample_stem), assessment)
            for sample_stem, assessment in zip(
                ("s1", "s2", "s3", "s4", "s5"),
                assessments,
                strict=True,
            )
        ),
    )

    assert calls == []
    assert mint_counts == {"hypothesis": 1, "group": 1, "cell": 5}
    assert spine.group is not None
    assert len(spine.support_hypotheses) == 1
    assert len(spine.cells) == 5
    assert len({cell.artifact.evidence_artifact_id for cell in spine.cells}) == 5
    for cell, targeted, untargeted in zip(
        spine.cells,
        spine.targeted_projections,
        spine.untargeted_projections,
        strict=True,
    ):
        assert targeted.cell is cell
        assert untargeted.cell is cell
        assert targeted.group is spine.group
        assert untargeted.group is spine.group
        assert targeted.validation_status == "diagnostic_only"
        assert untargeted.validation_status == "diagnostic_only"
        assert targeted.product_authority == "none"
        assert untargeted.product_authority == "none"

    by_sample = {
        cell.content.canonical_sample_identity_id: cell for cell in spine.cells
    }
    assert by_sample[fixture.sample("s1").identity.sample_identity_id].peak_hypothesis
    assert all(
        by_sample[fixture.sample(stem).identity.sample_identity_id].peak_hypothesis
        is None
        for stem in ("s2", "s3", "s4", "s5")
    )

    forbidden_field_tokens = ("area", "matrix", "writer", "backfill", "counted")
    field_names = {field.name.lower() for field in fields(spine.cells[0].content)}
    assert not any(
        token in field_name
        for field_name in field_names
        for token in forbidden_field_tokens
    )


def test_cell_rejects_hypothesis_from_a_different_assessment_relation_set(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("cross",))
    sample = fixture.sample("cross")
    peak = issue161_peak(fixture, "cross")
    first_evidence = issue161_evidence(
        fixture,
        "cross",
        scan_number=61,
        scan_rt_min=7.99,
    )
    second_evidence = issue161_evidence(
        fixture,
        "cross",
        scan_number=62,
        scan_rt_min=8.01,
    )
    first_support = issue161_unique_support(
        fixture,
        "cross",
        peak=peak,
        evidence=first_evidence,
    )
    second_support = issue161_unique_support(
        fixture,
        "cross",
        peak=peak,
        evidence=second_evidence,
    )
    common = {
        "processing_owners": fixture.owners,
        "sample": sample,
        "target": fixture.target,
        "evaluation_receipt": _completed_receipt(scanned_event_count=1),
        "candidate_peaks": (peak,),
    }
    first_assessment = mint_cid_nl_method_assessment_ref(
        **common,
        detector_invocation_id="gaussian15:cross:first",
        evidence_refs=(first_evidence,),
        individual_support_refs=(first_support,),
    )
    second_assessment = mint_cid_nl_method_assessment_ref(
        **common,
        detector_invocation_id="gaussian15:cross:second",
        evidence_refs=(second_evidence,),
        individual_support_refs=(second_support,),
    )
    second_spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((sample, second_assessment),),
    )
    assert second_spine.group is not None

    with pytest.raises(CellObservationContractError, match="relations"):
        mint_cell_observation_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            group=second_spine.group,
            method_assessment=first_assessment,
            peak_observation=peak,
            peak_hypothesis=second_spine.support_hypotheses[0],
        )


def test_reference_spine_and_cell_content_reject_crossed_manual_reassembly(
    tmp_path: Path,
) -> None:
    fixture, assessments = _truth_table_fixture(tmp_path)
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=tuple(
            (fixture.sample(sample_stem), assessment)
            for sample_stem, assessment in zip(
                ("s1", "s2", "s3", "s4", "s5"),
                assessments,
                strict=True,
            )
        ),
    )

    with pytest.raises(CidNlEvidenceSpineError, match="assessment"):
        replace(spine, assessments=tuple(reversed(spine.assessments)))
    with pytest.raises(CidNlEvidenceSpineError, match="hypotheses"):
        replace(spine, support_hypotheses=())
    with pytest.raises(CellObservationContractError, match="evaluated"):
        replace(
            spine.cells[0].content,
            method_result=None,
            peak_observation_content_id=None,
            peak_hypothesis_id=None,
        )


def test_without_support_assessments_do_not_invent_group_or_cells(
    tmp_path: Path,
) -> None:
    fixture, assessments = _truth_table_fixture(tmp_path)
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=(
            (fixture.sample("s2"), assessments[1]),
            (fixture.sample("s3"), assessments[2]),
        ),
    )

    assert spine.group is None
    assert spine.cells == ()
    assert spine.targeted_projections == ()
    assert spine.untargeted_projections == ()


def test_assessment_tamper_and_crossed_sample_fail_closed(tmp_path: Path) -> None:
    fixture, assessments = _truth_table_fixture(tmp_path)
    support = assessments[0]
    with pytest.raises(CidNlMethodAssessmentContractError):
        replace(
            support,
            content=replace(
                support.content,
                canonical_sample_identity_id=(
                    fixture.sample("s2").identity.sample_identity_id
                ),
            ),
        )
    with pytest.raises(CidNlMethodAssessmentContractError, match="sample"):
        validate_cid_nl_method_assessment_binding(
            support,
            processing_owners=fixture.owners,
            sample=fixture.sample("s2"),
            target=fixture.target,
        )


def test_reorder_is_neutral_and_multiple_scans_form_one_support_hypothesis(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("multi",))
    peak = issue161_peak(fixture, "multi")
    first_evidence = issue161_evidence(
        fixture,
        "multi",
        scan_number=50,
        scan_rt_min=7.99,
    )
    second_evidence = issue161_evidence(
        fixture,
        "multi",
        scan_number=51,
        scan_rt_min=8.01,
    )
    first_support = issue161_unique_support(
        fixture,
        "multi",
        peak=peak,
        evidence=first_evidence,
    )
    second_support = issue161_unique_support(
        fixture,
        "multi",
        peak=peak,
        evidence=second_evidence,
    )
    kwargs = {
        "processing_owners": fixture.owners,
        "sample": fixture.sample("multi"),
        "target": fixture.target,
        "detector_invocation_id": "gaussian15:multi:v1",
        "evaluation_receipt": _completed_receipt(scanned_event_count=2),
        "candidate_peaks": (peak,),
    }
    forward = mint_cid_nl_method_assessment_ref(
        **kwargs,
        evidence_refs=(first_evidence, second_evidence),
        individual_support_refs=(first_support, second_support),
    )
    reverse = mint_cid_nl_method_assessment_ref(
        **kwargs,
        evidence_refs=(second_evidence, first_evidence),
        individual_support_refs=(second_support, first_support),
    )

    assert forward == reverse
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((fixture.sample("multi"), forward),),
    )
    assert len(spine.support_hypotheses) == 1
    support = spine.support_hypotheses[0].evidence.cid_nl_chromatographic_support_ref
    assert support is not None
    assert support.content.strict_nl_scan_count == 2


def test_partial_duplicate_and_cross_attempt_inputs_fail_closed(tmp_path: Path) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("sample",))
    left = issue161_peak(
        fixture,
        "sample",
        apex_rt=7.9,
        rt_left_min=7.8,
        rt_right_min=8.0,
    )
    right = issue161_peak(
        fixture,
        "sample",
        apex_rt=8.1,
        rt_left_min=8.0,
        rt_right_min=8.2,
    )
    evidence = issue161_evidence(
        fixture,
        "sample",
        scan_number=52,
        scan_rt_min=8.0,
    )
    relation = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample"),
        canonical_evidence=evidence,
        candidate_peaks=(left, right),
        policy=issue161_support_policy(),
    )

    with pytest.raises(
        DdaCidNlCandidateSetRelationContractError,
        match="unique",
    ):
        resolve_dda_cid_nl_candidate_set_relation_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample"),
            canonical_evidence=evidence,
            candidate_peaks=(left, left),
            policy=issue161_support_policy(),
        )
    with pytest.raises(
        CidNlMethodAssessmentContractError,
        match="different candidate universe",
    ):
        mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample"),
            target=fixture.target,
            detector_invocation_id="gaussian15:sample:v1",
            evaluation_receipt=_completed_receipt(scanned_event_count=1),
            candidate_peaks=(left,),
            evidence_refs=(evidence,),
            set_relation_refs=(relation,),
        )

    retry = fixture.retry()
    with pytest.raises(CidNlMethodAssessmentContractError, match="attempt"):
        mint_cid_nl_method_assessment_ref(
            processing_owners=retry.owners,
            sample=retry.sample("sample"),
            target=retry.target,
            detector_invocation_id="gaussian15:sample:retry",
            evaluation_receipt=_completed_receipt(scanned_event_count=1),
            candidate_peaks=(left, right),
            evidence_refs=(evidence,),
            set_relation_refs=(relation,),
        )


def test_failed_attempt_cannot_become_reject_or_human_unassessable(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("failed",))
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("failed"),
        target=fixture.target,
        detector_invocation_id="gaussian15:failed:v1",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            acquisition_supported=True,
            search_completed=False,
            scanned_event_count=0,
            reason_code="detector_worker_failed",
        ),
        technical_state="failed",
    )

    assert assessment.content.method_result is None
    assert assessment.content.reason_codes == ("detector_worker_failed",)
    assert "unassessable" not in repr(assessment).lower()


def test_unique_case_rejects_set_relation_and_split_case_rejects_individual_vote(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("sample",))
    peak = issue161_peak(fixture, "sample")
    evidence = issue161_evidence(
        fixture,
        "sample",
        scan_number=49,
        scan_rt_min=8.0,
    )
    with pytest.raises(
        DdaCidNlCandidateSetRelationContractError,
        match="individual support",
    ):
        resolve_dda_cid_nl_candidate_set_relation_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample"),
            canonical_evidence=evidence,
            candidate_peaks=(peak,),
            policy=issue161_support_policy(),
        )

    right = issue161_peak(
        fixture,
        "sample",
        apex_rt=8.1,
        rt_left_min=8.0,
        rt_right_min=8.2,
    )
    with pytest.raises(DdaCidNlPeakSupportContractError, match="ambiguous"):
        issue161_unique_support(
            fixture,
            "sample",
            peak=peak,
            evidence=evidence,
            candidate_peaks=(peak, right),
        )


def _truth_table_fixture(tmp_path: Path):
    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2", "s3", "s4", "s5"),
    )

    s1_peak = issue161_peak(fixture, "s1")
    s1_evidence = issue161_evidence(
        fixture,
        "s1",
        scan_number=41,
        scan_rt_min=8.0,
    )
    s1_support = issue161_unique_support(
        fixture,
        "s1",
        peak=s1_peak,
        evidence=s1_evidence,
    )
    s1 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s1:v1",
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        candidate_peaks=(s1_peak,),
        evidence_refs=(s1_evidence,),
        individual_support_refs=(s1_support,),
    )

    s2_peak = issue161_peak(fixture, "s2")
    s2 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s2"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s2:v1",
        evaluation_receipt=_completed_receipt(scanned_event_count=0),
        candidate_peaks=(s2_peak,),
    )

    s3_evidence = issue161_evidence(
        fixture,
        "s3",
        scan_number=43,
        scan_rt_min=8.0,
    )
    s3 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s3"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s3:v1",
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        evidence_refs=(s3_evidence,),
    )

    s4_left = issue161_peak(
        fixture,
        "s4",
        apex_rt=7.9,
        rt_left_min=7.8,
        rt_right_min=8.0,
    )
    s4_right = issue161_peak(
        fixture,
        "s4",
        apex_rt=8.1,
        rt_left_min=8.0,
        rt_right_min=8.2,
    )
    s4_evidence = issue161_evidence(
        fixture,
        "s4",
        scan_number=44,
        scan_rt_min=8.0,
    )
    s4_relation = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s4"),
        canonical_evidence=s4_evidence,
        candidate_peaks=(s4_left, s4_right),
        policy=issue161_support_policy(),
    )
    s4 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s4"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s4:v1",
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        candidate_peaks=(s4_left, s4_right),
        evidence_refs=(s4_evidence,),
        set_relation_refs=(s4_relation,),
    )

    s5 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s5"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s5:v1",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            acquisition_supported=False,
            search_completed=False,
            scanned_event_count=0,
            reason_code="dda_not_available",
        ),
        technical_state="unsupported",
    )
    return fixture, (s1, s2, s3, s4, s5)


def _completed_receipt(*, scanned_event_count: int) -> CidNlEvaluationReceipt:
    return CidNlEvaluationReceipt(
        query_rt_min=7.5,
        query_rt_max=8.6,
        acquisition_supported=True,
        search_completed=True,
        scanned_event_count=scanned_event_count,
        reason_code=None,
    )
