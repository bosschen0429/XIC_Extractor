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
from xic_extractor.alignment.identity_coherence.mode_track_assignment import (
    ModeTrackTrajectoryPolicy,
)
from xic_extractor.alignment.identity_coherence.mode_track_producer import (
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

    family_context, model_set, batches = bootstrap_mode_tracks_from_cid_nl_support(
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

    assert len(family_context.mode_tracks) == 2
    assert len(model_set.tracks) == 2
    assert len(batches) == 1
    assert tuple(item.state for item in batches[0].assignments) == (
        "uniquely_assigned",
        "uniquely_assigned",
    )
    assert len({item.group_hypothesis_id for item in batches[0].assignments}) == 2
