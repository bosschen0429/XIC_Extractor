"""Helpers shared by test_cid_nl_evidence_spine and other test modules."""

from __future__ import annotations

from pathlib import Path

from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_peak,
    cid_nl_support_policy,
    cid_nl_unique_support,
)
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    resolve_dda_cid_nl_candidate_set_relation_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    mint_cid_nl_method_assessment_ref,
)


def _truth_table_fixture(tmp_path: Path):
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2", "s3", "s4", "s5"),
    )

    s1_peak = cid_nl_peak(fixture, "s1")
    s1_evidence = cid_nl_evidence(
        fixture,
        "s1",
        scan_number=41,
        scan_rt_min=8.0,
    )
    s1_support = cid_nl_unique_support(
        fixture,
        "s1",
        peak=s1_peak,
        evidence=s1_evidence,
    )
    s1 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s1",
        evaluation_receipt=_completed_receipt(),
        candidate_peaks=(s1_peak,),
        evidence_refs=(s1_evidence,),
        individual_support_refs=(s1_support,),
    )

    s2_peak = cid_nl_peak(fixture, "s2")
    s2 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s2"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s2",
        evaluation_receipt=_completed_receipt(),
        candidate_peaks=(s2_peak,),
    )

    s3_evidence = cid_nl_evidence(
        fixture,
        "s3",
        scan_number=43,
        scan_rt_min=8.0,
    )
    s3 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s3"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s3",
        evaluation_receipt=_completed_receipt(),
        evidence_refs=(s3_evidence,),
    )

    s4_left = cid_nl_peak(
        fixture,
        "s4",
        apex_rt=7.9,
        rt_left_min=7.8,
        rt_right_min=8.0,
    )
    s4_right = cid_nl_peak(
        fixture,
        "s4",
        apex_rt=8.1,
        rt_left_min=8.0,
        rt_right_min=8.2,
    )
    s4_evidence = cid_nl_evidence(
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
        policy=cid_nl_support_policy(),
    )
    s4 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s4"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s4",
        evaluation_receipt=_completed_receipt(),
        candidate_peaks=(s4_left, s4_right),
        evidence_refs=(s4_evidence,),
        set_relation_refs=(s4_relation,),
    )

    s5 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s5"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s5",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=False,
            search_completed=False,
            reason_code="dda_not_available",
        ),
        technical_state="unsupported",
    )
    return fixture, (s1, s2, s3, s4, s5)


def _completed_receipt() -> CidNlEvaluationReceipt:
    return CidNlEvaluationReceipt(
        query_rt_min=7.5,
        query_rt_max=8.6,
        support_policy=cid_nl_support_policy(),
        acquisition_supported=True,
        search_completed=True,
        reason_code=None,
    )
