"""Fit-once source and repair-observation assignment contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
from tests.test_alignment_mode_track_assignment import _scenario
from tests.test_alignment_mode_track_conservative_backfill import (
    _two_peak_assessment,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackContractError,
    ModeTrackFitOnceSampleSource,
    ModeTrackFitOnceSource,
    ModeTrackRepairAssignmentObservation,
    ModeTrackTrajectoryPolicy,
    _mint_mode_track_repair_assignment_observation,
    assign_mode_tracks,
    mint_mode_track_fit_once_source,
    reinforce_mode_track_model_set,
)
from xic_extractor.alignment.mode_track.producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)


def test_repair_owned_physical_observation_is_non_training_assignment_input(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    sample = scenario.fixture.sample("target")
    physical_id = typed_identity_digest(
        "physical-facts.v1:",
        canonical_identity_basis_bytes(
            {"fixture": "mode-track-repair-assignment-observation"}
        ),
    )
    repaired = _mint_mode_track_repair_assignment_observation(
        processing_owners=scenario.fixture.owners,
        sample=sample,
        repaired_member_subject_id="group-member:target",
        physical_observation_id=physical_id,
        apex_rt_min=7.87,
        repair_receipt_id="repair-receipt:target",
        source_observation_id="repair-observation:target",
    )

    batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=sample,
        model_set=scenario.models,
        candidates=(),
        repair_observations=(repaired,),
        trajectory_assessment=scenario.assessable_trajectory,
    )

    assignment = batch.assignments[0]
    assert assignment.state == "uniquely_assigned"
    assert assignment.group_hypothesis_id == scenario.track_a.group_hypothesis_id
    assert assignment.peak_observation_content_id == physical_id
    assert assignment.peak_hypothesis_id is None
    assert assignment.candidate_observation_class == "non_training"
    assert assignment.direct_support_assessment is None
    assert isinstance(
        assignment.candidate_observation,
        ModeTrackRepairAssignmentObservation,
    )
    with pytest.raises(ModeTrackContractError, match="must be minted"):
        replace(assignment.candidate_observation, _mint_token=object())
    assert (
        reinforce_mode_track_model_set(
            processing_owners=scenario.fixture.owners,
            model_set=scenario.models,
            assignment_batch=batch,
        )
        is scenario.models
    )


def test_bootstrap_returns_fit_once_source_closing_existing_values(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2", "s3"),
    )
    sample_assessments = tuple(
        (
            fixture.sample(sample_stem),
            _two_peak_assessment(
                fixture,
                sample_stem=sample_stem,
                supported=("early", "late"),
                scan_number=scan_number,
            ),
        )
        for sample_stem, scan_number in (("s1", 101), ("s2", 201), ("s3", 301))
    )

    family_context, model_set, batches, source = (
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

    assert source.family_context is family_context
    assert source.model_set is model_set
    assert source.initial_assignment_batches == tuple(
        sorted(batches, key=lambda batch: batch.assignment_batch_id)
    )
    assert source.initial_assignment_batch_ids == tuple(
        batch.assignment_batch_id
        for batch in sorted(batches, key=lambda batch: batch.assignment_batch_id)
    )
    assert tuple(item.sample for item in source.sample_sources) == tuple(
        sorted(
            (fixture.sample(stem) for stem in ("s1", "s2", "s3")),
            key=lambda sample: sample.identity.sample_identity_id,
        )
    )
    for sample_source in source.sample_sources:
        batch = next(
            batch
            for batch in batches
            if batch.canonical_sample_identity_id
            == sample_source.canonical_sample_identity_id
        )
        assert sample_source.candidate_observation_ids == tuple(
            sorted(
                candidate.candidate_observation_id
                for assignment in batch.assignments
                if (candidate := assignment.candidate_observation) is not None
            )
        )

    sample_source = source.sample_sources[0]
    with pytest.raises(ModeTrackContractError, match="must be minted"):
        ModeTrackFitOnceSampleSource(
            canonical_sample_identity_id=sample_source.canonical_sample_identity_id,
            acquisition_source_id=sample_source.acquisition_source_id,
            injection_order=sample_source.injection_order,
            candidate_observation_ids=sample_source.candidate_observation_ids,
            trajectory_status=sample_source.trajectory_status,
            trajectory_reason_codes=sample_source.trajectory_reason_codes,
            trajectory_evidence_refs=sample_source.trajectory_evidence_refs,
            sample=sample_source.sample,
            candidates=sample_source.candidates,
            trajectory_assessment=sample_source.trajectory_assessment,
            _mint_token=object(),
        )
    with pytest.raises(ModeTrackContractError, match="must be minted"):
        ModeTrackFitOnceSource(
            fit_once_source_id=source.fit_once_source_id,
            processing_request_id=source.processing_request_id,
            processing_attempt_id=source.processing_attempt_id,
            family_context_id=source.family_context_id,
            model_set_id=source.model_set_id,
            initial_assignment_batch_ids=source.initial_assignment_batch_ids,
            processing_owners=source.processing_owners,
            family_context=source.family_context,
            model_set=source.model_set,
            sample_sources=source.sample_sources,
            initial_assignment_batches=source.initial_assignment_batches,
            _mint_token=object(),
        )


def test_fit_once_source_rejects_batch_from_another_candidate_snapshot(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2", "s3"),
    )
    sample_assessments = tuple(
        (
            fixture.sample(sample_stem),
            _two_peak_assessment(
                fixture,
                sample_stem=sample_stem,
                supported=("early", "late"),
                scan_number=scan_number,
            ),
        )
        for sample_stem, scan_number in (("s1", 101), ("s2", 201), ("s3", 301))
    )
    family_context, model_set, batches, source = (
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
    sample_source = source.sample_sources[0]
    unrelated_batch = assign_mode_tracks(
        processing_owners=fixture.owners,
        sample=sample_source.sample,
        model_set=model_set,
        candidates=(),
        trajectory_assessment=sample_source.trajectory_assessment,
    )
    crossed_batches = tuple(
        unrelated_batch
        if batch.canonical_sample_identity_id
        == sample_source.canonical_sample_identity_id
        else batch
        for batch in batches
    )

    with pytest.raises(ModeTrackContractError, match="candidate snapshot"):
        mint_mode_track_fit_once_source(
            processing_owners=fixture.owners,
            family_context=family_context,
            model_set=model_set,
            sample_sources=source.sample_sources,
            initial_assignment_batches=crossed_batches,
        )
