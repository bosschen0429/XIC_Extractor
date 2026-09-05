from __future__ import annotations

from pathlib import Path

from tests.issue161_no_raw_fixture import (
    issue161_evidence,
    issue161_peak,
    issue161_support_policy,
    issue161_unique_support,
)
from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
    validate_cid_nl_assessment_hypothesis_closure,
)
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackTrajectoryPolicy,
    reinforce_mode_track_model_set,
)
from xic_extractor.alignment.mode_track.producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    mint_cid_nl_method_assessment_ref,
)


def test_one_assessment_preserves_two_individually_supported_peaks(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("dual",))
    sample = fixture.sample("dual")
    left = issue161_peak(
        fixture,
        "dual",
        apex_rt=7.8,
        rt_left_min=7.7,
        rt_right_min=7.9,
    )
    right = issue161_peak(
        fixture,
        "dual",
        apex_rt=8.2,
        rt_left_min=8.1,
        rt_right_min=8.3,
    )
    peaks = (left, right)
    left_evidence = issue161_evidence(
        fixture,
        "dual",
        scan_number=101,
        scan_rt_min=7.8,
    )
    right_evidence = issue161_evidence(
        fixture,
        "dual",
        scan_number=102,
        scan_rt_min=8.2,
    )
    left_support = issue161_unique_support(
        fixture,
        "dual",
        peak=left,
        evidence=left_evidence,
        candidate_peaks=peaks,
    )
    right_support = issue161_unique_support(
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
        detector_invocation_id="gaussian15:dual:v1",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.5,
            support_policy=issue161_support_policy(),
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

    family_context, model_set, batches, _source = (
        bootstrap_mode_tracks_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=((sample, assessment),),
        alignment_config=AlignmentConfig(),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
        )
    )

    assert len(family_context.mode_tracks) == 2
    assert len(model_set.tracks) == 2
    assert len(batches) == 1
    assert tuple(item.state for item in batches[0].assignments) == (
        "uniquely_assigned",
        "uniquely_assigned",
    )
    assert len({item.group_hypothesis_id for item in batches[0].assignments}) == 2


def test_identity_membership_is_geometry_not_group_center_reclassification(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(
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

    family_context, model_set, batches, _source = (
        bootstrap_mode_tracks_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=sample_assessments,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
        )
    )

    assert len(family_context.mode_tracks) == 2
    assert len(model_set.tracks) == 2
    assert tuple(len(batch.assignments) for batch in batches) == (2, 2)
    assert {
        assignment.state
        for batch in batches
        for assignment in batch.assignments
    } == {"uniquely_assigned"}
    assert all(
        assignment.peak_hypothesis_id
        in next(
            track.owner_ids
            for track in family_context.mode_tracks
            if track.group_hypothesis_id == assignment.group_hypothesis_id
        )
        for batch in batches
        for assignment in batch.assignments
    )
    observation_assignments = tuple(
        assignment
        for batch in batches
        for assignment in batch.assignments
        if assignment.candidate_observation_class == "non_training"
    )
    assert len(observation_assignments) == 2
    assert all(
        assignment.reason_codes == ("existing_group_owner_observation",)
        for assignment in observation_assignments
    )
    assert all(
        reinforce_mode_track_model_set(
            processing_owners=fixture.owners,
            model_set=model_set,
            assignment_batch=batch,
        )
        is model_set
        for batch in batches
    )


def _assessment_with_observation_only_peak(
    fixture,
    *,
    sample_stem: str,
    direct_apex_rt: float,
    observed_apex_rt: float,
    scan_number: int,
):
    sample = fixture.sample(sample_stem)
    direct_peak = issue161_peak(
        fixture,
        sample_stem,
        apex_rt=direct_apex_rt,
        rt_left_min=direct_apex_rt - 0.1,
        rt_right_min=direct_apex_rt + 0.1,
    )
    observed_peak = issue161_peak(
        fixture,
        sample_stem,
        apex_rt=observed_apex_rt,
        rt_left_min=observed_apex_rt - 0.1,
        rt_right_min=observed_apex_rt + 0.1,
    )
    peaks = (direct_peak, observed_peak)
    evidence = issue161_evidence(
        fixture,
        sample_stem,
        scan_number=scan_number,
        scan_rt_min=direct_apex_rt,
    )
    support = issue161_unique_support(
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
        detector_invocation_id=f"gaussian15:{sample_stem}:v1",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=9.2,
            support_policy=issue161_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=1,
            reason_code=None,
        ),
        candidate_peaks=peaks,
        evidence_refs=(evidence,),
        individual_support_refs=(support,),
    )
