from __future__ import annotations

from pathlib import Path

import pytest

from tests.issue161_no_raw_fixture import (
    issue161_evidence,
    issue161_peak,
    issue161_support_policy,
    issue161_unique_support,
)
from tests.package2_no_raw_fixture import (
    Package2NoRawFixture,
    build_package2_no_raw_fixture,
)
from xic_extractor.alignment import conservative_backfill as backfill
from xic_extractor.alignment import untargeted_cid_nl_golden_path as golden_path
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.identity_coherence.mode_track_assignment import (
    ModeTrackTrajectoryPolicy,
)
from xic_extractor.alignment.identity_coherence.mode_track_producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentRef,
    mint_cid_nl_method_assessment_ref,
)


def test_mode_track_facts_split_mixed_assessments_without_m1_cells(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )

    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )

    assert len(facts) == 2
    by_center = {item.group.group_center_rt: item for item in facts}
    assert len(by_center) == 2
    assert {
        (
            item.sample_count_n,
            item.minimum_support_k,
            item.direct_support_count_s,
            item.group_frozen,
            item.may_attempt_recovery,
        )
        for item in facts
    } == {(3, 2, 2, True, True)}
    assert {len(item.direct_support_assignments) for item in facts} == {2}
    assert {len(item.exact_owner_candidate_assignments) for item in facts} == {1}
    assert all(item.product_authority == "none" for item in facts)
    assert all(not hasattr(item, "source_cells") for item in facts)


def test_mode_track_support_votes_come_from_assessment_closures_only(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )

    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )

    for item in facts:
        assert len(item.group.owner_ids) == 3
        assert len(item.direct_support_sample_ids) == 2
        assert item.direct_support_count_s == 2
        assert {
            assignment.canonical_sample_identity_id
            for assignment in item.direct_support_assignments
        } == set(item.direct_support_sample_ids)
        assert all(
            assignment.candidate_observation_class == "direct_support"
            for assignment in item.direct_support_assignments
        )
        assert all(
            assignment.candidate_observation_class == "non_training"
            and assignment.reason_codes == ("existing_group_owner_observation",)
            and assignment.peak_hypothesis_id in item.group.owner_ids
            for assignment in item.exact_owner_candidate_assignments
        )


def test_mode_track_facts_are_order_invariant_and_retain_exact_assignments(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )

    forward = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )
    reversed_input = _assess(
        fixture=fixture,
        sample_assessments=tuple(reversed(sample_assessments)),
        model_set=model_set,
        batches=tuple(reversed(batches)),
    )

    assert reversed_input == forward
    assert all(
        candidate.group_hypothesis_id == item.group.group_hypothesis_id
        for item in forward
        for candidate in item.exact_owner_candidate_assignments
    )


def test_mode_track_facts_reject_partial_sample_batch_population(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )

    with pytest.raises(
        backfill.ConservativeBackfillContractError,
        match="complete sample population",
    ):
        _assess(
            fixture=fixture,
            sample_assessments=sample_assessments,
            model_set=model_set,
            batches=batches[:-1],
        )


def test_mode_track_groups_activate_independently_of_exact_owner_candidates(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path,
        support_pattern=(
            ("s1", ("early",), 101),
            ("s2", ("early", "late"), 201),
            ("s3", ("early",), 301),
        ),
    )

    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )
    early, late = sorted(facts, key=lambda item: item.group.group_center_rt)

    assert (early.direct_support_count_s, early.minimum_support_k) == (3, 2)
    assert early.may_attempt_recovery is True
    assert early.exact_owner_candidate_assignments == ()
    assert (late.direct_support_count_s, late.minimum_support_k) == (1, 2)
    assert late.may_attempt_recovery is False
    assert len(late.exact_owner_candidate_assignments) == 2
    assert late.reason_codes == ("direct_support_threshold_not_met",)


def test_mode_track_projection_prepares_only_m2_eligible_exact_owner_cells(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path
    )
    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )

    build_projection = getattr(
        golden_path,
        "build_untargeted_cid_nl_mode_track_projection",
    )
    projection = build_projection(
        processing_owners=fixture.owners,
        mode_track_facts=facts,
        source_artifact_relpath="output/m9/mode_track_source.json",
    )

    assert projection.backfill_decisions == ()
    assert len(projection.input_quant_matrix_rows) == 2
    assert {
        row["peak_hypothesis_id"]
        for row in projection.input_matrix_identity_rows
    } == {item.group.group_hypothesis_id for item in facts}
    assert len(projection.pending_acceptance_rows) == 2
    assert len(projection.expected_diff_rows) == 2
    assert all(
        row["peak_hypothesis_id"]
        in {item.group.group_hypothesis_id for item in facts}
        and row["acceptance_decision"] == "accept_basic_backfill"
        and row["write_authority"] == "TRUE"
        for row in projection.pending_acceptance_rows
    )
    assert projection.product_authority == "none_until_checked_manifest"


def test_mode_track_projection_does_not_publish_below_threshold_candidates(
    tmp_path: Path,
) -> None:
    fixture, sample_assessments, model_set, batches = _mixed_mode_track_fixture(
        tmp_path,
        support_pattern=(
            ("s1", ("early",), 101),
            ("s2", ("early", "late"), 201),
            ("s3", ("early",), 301),
        ),
    )
    facts = _assess(
        fixture=fixture,
        sample_assessments=sample_assessments,
        model_set=model_set,
        batches=batches,
    )

    projection = getattr(
        golden_path,
        "build_untargeted_cid_nl_mode_track_projection",
    )(
        processing_owners=fixture.owners,
        mode_track_facts=facts,
        source_artifact_relpath="output/m9/mode_track_source.json",
    )

    assert projection.pending_acceptance_rows == ()
    assert projection.expected_diff_rows == ()


def _assess(
    *,
    fixture: Package2NoRawFixture,
    sample_assessments: tuple[
        tuple[object, CidNlMethodAssessmentRef],
        ...,
    ],
    model_set,
    batches,
):
    operation = getattr(
        backfill,
        "assess_mode_track_conservative_backfill_groups",
    )
    return operation(
        processing_owners=fixture.owners,
        model_set=model_set,
        assignment_batches=batches,
        sample_assessments=sample_assessments,
    )


def _mixed_mode_track_fixture(
    tmp_path: Path,
    *,
    support_pattern: tuple[
        tuple[str, tuple[str, ...], int],
        ...,
    ] = (
        ("s1", ("early",), 101),
        ("s2", ("early", "late"), 201),
        ("s3", ("late",), 301),
    ),
):
    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2", "s3"),
        sample_roles={
            "s1": "study_sample",
            "s2": "study_sample",
            "s3": "study_sample",
        },
    )
    sample_assessments = tuple(
        (
            fixture.sample(sample_stem),
            _two_peak_assessment(
                fixture,
                sample_stem=sample_stem,
                supported=supported,
                scan_number=scan_number,
            ),
        )
        for sample_stem, supported, scan_number in support_pattern
    )
    _, model_set, batches = bootstrap_mode_tracks_from_cid_nl_support(
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
    assert len(model_set.tracks) == 2
    assert tuple(len(batch.assignments) for batch in batches) == (2, 2, 2)
    return fixture, sample_assessments, model_set, batches


def _two_peak_assessment(
    fixture: Package2NoRawFixture,
    *,
    sample_stem: str,
    supported: tuple[str, ...],
    scan_number: int,
) -> CidNlMethodAssessmentRef:
    sample = fixture.sample(sample_stem)
    peaks = {
        "early": issue161_peak(
            fixture,
            sample_stem,
            apex_rt=7.8,
            rt_left_min=7.7,
            rt_right_min=7.9,
        ),
        "late": issue161_peak(
            fixture,
            sample_stem,
            apex_rt=8.4,
            rt_left_min=8.3,
            rt_right_min=8.5,
        ),
    }
    candidate_peaks = (peaks["early"], peaks["late"])
    evidence_refs = tuple(
        issue161_evidence(
            fixture,
            sample_stem,
            scan_number=scan_number + index,
            scan_rt_min=peaks[name].content.integration.rt_apex_min,
        )
        for index, name in enumerate(supported)
    )
    support_refs = tuple(
        issue161_unique_support(
            fixture,
            sample_stem,
            peak=peaks[name],
            evidence=evidence,
            candidate_peaks=candidate_peaks,
        )
        for name, evidence in zip(supported, evidence_refs, strict=True)
    )
    return mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id=f"gaussian15:{sample_stem}:m9-red",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.7,
            support_policy=issue161_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=len(supported),
            reason_code=None,
        ),
        candidate_peaks=candidate_peaks,
        evidence_refs=evidence_refs,
        individual_support_refs=support_refs,
    )
