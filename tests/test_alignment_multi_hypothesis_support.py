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
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_bootstrap import (
    bootstrap_cid_nl_initial_population,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
    validate_cid_nl_assessment_hypothesis_closure,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    mint_cid_nl_method_assessment_ref,
)


def test_one_assessment_preserves_two_individually_supported_peaks(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("dual",))
    sample = fixture.sample("dual")
    left = cid_nl_peak(
        fixture,
        "dual",
        apex_rt=7.8,
        rt_left_min=7.7,
        rt_right_min=7.9,
    )
    right = cid_nl_peak(
        fixture,
        "dual",
        apex_rt=8.2,
        rt_left_min=8.1,
        rt_right_min=8.3,
    )
    peaks = (left, right)
    left_evidence = cid_nl_evidence(
        fixture,
        "dual",
        scan_number=101,
        scan_rt_min=7.8,
    )
    right_evidence = cid_nl_evidence(
        fixture,
        "dual",
        scan_number=102,
        scan_rt_min=8.2,
    )
    left_support = cid_nl_unique_support(
        fixture,
        "dual",
        peak=left,
        evidence=left_evidence,
        candidate_peaks=peaks,
    )
    right_support = cid_nl_unique_support(
        fixture,
        "dual",
        peak=right,
        evidence=right_evidence,
        candidate_peaks=peaks,
    )
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id="gaussian15:dual",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.5,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=2,
            reason_code=None,
        ),
        candidate_peaks=peaks,
        evidence_refs=(left_evidence, right_evidence),
        individual_support_refs=(left_support, right_support),
    )

    hypotheses = compose_cid_nl_peak_hypotheses_from_assessment(
        processing_owners=fixture.owners,
        sample=sample,
        assessment=assessment,
    )

    assert len(hypotheses) == 2
    assert {
        hypothesis.peak_observation_refs[0].content.peak_observation_content_id
        for hypothesis in hypotheses
    } == {
        left.content.peak_observation_content_id,
        right.content.peak_observation_content_id,
    }
    for hypothesis in hypotheses:
        validate_cid_nl_assessment_hypothesis_closure(
            sample=sample,
            assessment=assessment,
            hypothesis=hypothesis,
        )
        owner = owner_from_cid_nl_assessment_support(
            sample=sample,
            assessment=assessment,
            hypothesis=hypothesis,
        )
        assert owner.owner_id == hypothesis.hypothesis_id

    grouping_scope, _candidates, _drift = bootstrap_cid_nl_initial_population(
        processing_owners=fixture.owners,
        sample_assessments=((sample, assessment),),
        alignment_config=AlignmentConfig(),
    )

    assert sorted(group.owner_ids for group in grouping_scope.groups) == sorted(
        (hypothesis.hypothesis_id,) for hypothesis in hypotheses
    )


def test_identity_membership_is_geometry_not_group_center_reclassification(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("early", "late"),
    )
    sample_assessments = tuple(
        (
            fixture.sample(sample_stem),
            _assessment_with_observation_only_peak(
                fixture,
                sample_stem=sample_stem,
                direct_apex_rt=direct_apex_rt,
                observed_apex_rt=observed_apex_rt,
                scan_number=scan_number,
            ),
        )
        for sample_stem, direct_apex_rt, observed_apex_rt, scan_number in (
            ("early", 7.80, 8.40, 201),
            ("late", 7.82, 8.88, 202),
        )
    )

    grouping_scope, candidates, _drift = bootstrap_cid_nl_initial_population(
        processing_owners=fixture.owners,
        sample_assessments=sample_assessments,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )

    direct_ids = frozenset(
        candidate.peak_hypothesis_id
        for candidate in candidates
        if candidate.direct_support_assessment is not None
    )
    observation_ids = frozenset(
        candidate.peak_hypothesis_id
        for candidate in candidates
        if candidate.direct_support_assessment is None
    )
    assert len(direct_ids) == len(observation_ids) == 2
    assert {frozenset(group.owner_ids) for group in grouping_scope.groups} == {
        direct_ids,
        observation_ids,
    }


def _assessment_with_observation_only_peak(
    fixture,
    *,
    sample_stem: str,
    direct_apex_rt: float,
    observed_apex_rt: float,
    scan_number: int,
):
    sample = fixture.sample(sample_stem)
    direct_peak = cid_nl_peak(
        fixture,
        sample_stem,
        apex_rt=direct_apex_rt,
        rt_left_min=direct_apex_rt - 0.1,
        rt_right_min=direct_apex_rt + 0.1,
    )
    observed_peak = cid_nl_peak(
        fixture,
        sample_stem,
        apex_rt=observed_apex_rt,
        rt_left_min=observed_apex_rt - 0.1,
        rt_right_min=observed_apex_rt + 0.1,
    )
    peaks = (direct_peak, observed_peak)
    evidence = cid_nl_evidence(
        fixture,
        sample_stem,
        scan_number=scan_number,
        scan_rt_min=direct_apex_rt,
    )
    support = cid_nl_unique_support(
        fixture,
        sample_stem,
        peak=direct_peak,
        evidence=evidence,
        candidate_peaks=peaks,
    )
    return mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id=f"gaussian15:{sample_stem}",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=9.2,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=1,
            reason_code=None,
        ),
        candidate_peaks=peaks,
        evidence_refs=(evidence,),
        individual_support_refs=(support,),
    )
