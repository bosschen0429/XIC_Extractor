from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_alignment_conservative_backfill import _assessment
from tests.test_alignment_mode_track_assignment import (
    _different_request_same_sources,
    _scenario,
)
from xic_extractor.alignment.identity_coherence import mode_track_assignment
from xic_extractor.alignment.identity_coherence.family_context import (
    mint_family_context_ref,
)
from xic_extractor.alignment.identity_coherence.mode_track_assignment import (
    ModeTrackContractError,
    assign_mode_tracks,
    canonical_mode_track_assignment_batch_bytes,
    fit_mode_track_model_set,
    reinforce_mode_track_model_set,
)
from xic_extractor.processing_identity import resolve_request_acquisition_source


def _validator():
    validator = getattr(
        mode_track_assignment,
        "validate_mode_track_assignment_batch_for_consumption",
        None,
    )
    assert validator is not None, "M8 lacks a public pure consumer validator"
    return validator


def _batch_for_case(tmp_path: Path, case: str):
    scenario = _scenario(
        tmp_path,
        prediction_half_width_min=0.26 if case == "ambiguous" else 0.1,
    )
    if case == "not_observed":
        candidates = ()
    else:
        apex = {
            "uniquely_assigned": 7.87,
            "ambiguous": 8.12,
            "out_of_model": 8.55,
            "technical_unassessable": 7.87,
        }[case]
        candidates = (scenario.candidate(apex, direct_support=False),)
    trajectory = (
        scenario.missing_trajectory
        if case == "technical_unassessable"
        else scenario.assessable_trajectory
    )
    batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=candidates,
        trajectory_assessment=trajectory,
    )
    return scenario, batch


@pytest.mark.parametrize(
    ("case", "expected_state"),
    (
        ("uniquely_assigned", "uniquely_assigned"),
        ("ambiguous", "ambiguous_multiple_tracks"),
        ("out_of_model", "out_of_model"),
        ("not_observed", "not_observed"),
        ("technical_unassessable", "technical_unassessable"),
    ),
)
def test_consumer_validator_accepts_all_states_and_exact_replay(
    tmp_path: Path,
    case: str,
    expected_state: str,
) -> None:
    scenario, batch = _batch_for_case(tmp_path, case)
    assert tuple(item.state for item in batch.assignments) == (expected_state,)

    validator = _validator()
    original_bytes = canonical_mode_track_assignment_batch_bytes(batch)
    for _ in range(2):
        result = validator(
            processing_owners=scenario.fixture.owners,
            model_set=scenario.models,
            assignment_batch=batch,
        )
        assert result is None
        assert canonical_mode_track_assignment_batch_bytes(batch) == original_bytes


def test_direct_support_validation_is_pure_and_does_not_reinforce(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(scenario.candidate(7.87, direct_support=True),),
        trajectory_assessment=scenario.assessable_trajectory,
    )
    original_model = scenario.models

    result = _validator()(
        processing_owners=scenario.fixture.owners,
        model_set=scenario.models,
        assignment_batch=batch,
    )
    assert result is None
    assert scenario.models is original_model


@pytest.mark.parametrize(
    "case",
    (
        "same_sources_new_request",
        "sibling_attempt",
        "different_model",
        "different_family",
        "different_policy",
        "stale_sample",
        "stale_acquisition",
        "stale_injection",
        "foreign_equal_content_support",
        "forged_assignment_id",
        "forged_batch_id",
        "duplicate_hypothesis_and_physical_wrapper",
        "partial_tuple",
        "malformed_tuple",
    ),
)
def test_consumer_validator_rejects_crossed_stale_or_forged_closure(
    tmp_path: Path,
    case: str,
) -> None:
    scenario = _scenario(tmp_path)
    direct = scenario.candidate(7.87, direct_support=True)
    batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(direct,),
        trajectory_assessment=scenario.assessable_trajectory,
    )
    owners = scenario.fixture.owners
    models = scenario.models

    if case == "same_sources_new_request":
        owners = _different_request_same_sources(scenario.fixture)
    elif case == "sibling_attempt":
        owners = scenario.fixture.retry().owners
    elif case == "different_model":
        models = fit_mode_track_model_set(
            processing_owners=owners,
            family_context=scenario.family_context,
            direct_support_anchors=scenario.track_a_spine.anchors,
            policy=scenario.policy,
        )
    elif case == "different_family":
        family = mint_family_context_ref(
            mz_interval=scenario.family_context.mz_interval,
            rt_interval=scenario.family_context.rt_interval,
            context_policy_version=scenario.family_context.context_policy_version,
            source_lineage_refs=("synthetic:different-family",),
            mode_tracks=scenario.family_context.mode_tracks,
        )
        models = fit_mode_track_model_set(
            processing_owners=owners,
            family_context=family,
            direct_support_anchors=scenario.track_a_spine.anchors
            + scenario.track_b_spine.anchors,
            policy=scenario.policy,
        )
    elif case == "different_policy":
        models = fit_mode_track_model_set(
            processing_owners=owners,
            family_context=scenario.family_context,
            direct_support_anchors=scenario.track_a_spine.anchors
            + scenario.track_b_spine.anchors,
            policy=replace(scenario.policy, prediction_half_width_min=0.11),
        )
    elif case == "stale_sample":
        object.__setattr__(batch, "sample", scenario.fixture.sample("anchor-1"))
    elif case == "stale_acquisition":
        stale = resolve_request_acquisition_source(
            processing_owners=owners, sample=scenario.fixture.sample("anchor-1")
        )
        object.__setattr__(batch, "acquisition_source", stale)
    elif case == "stale_injection":
        object.__setattr__(batch, "injection_order", 99)
    elif case == "foreign_equal_content_support":
        sibling = scenario.fixture.retry()
        foreign = _assessment(
            sibling,
            "target",
            support_apex=7.87,
            scan_number=9001,
            invocation="candidate:7.87000:True",
        )
        assert foreign.content == direct.direct_support_assessment.content
        assert foreign.artifact != direct.direct_support_assessment.artifact
        object.__setattr__(direct, "direct_support_assessment", foreign)
    elif case == "forged_assignment_id":
        object.__setattr__(batch.assignments[0], "assignment_id", "forged")
    elif case == "forged_batch_id":
        object.__setattr__(batch, "assignment_batch_id", "forged")
    elif case == "duplicate_hypothesis_and_physical_wrapper":
        object.__setattr__(batch, "assignments", batch.assignments * 2)
    elif case == "partial_tuple":
        object.__setattr__(batch, "assignments", ())
    elif case == "malformed_tuple":
        object.__setattr__(batch, "assignments", list(batch.assignments))

    with pytest.raises(ModeTrackContractError):
        _validator()(processing_owners=owners, model_set=models, assignment_batch=batch)


def test_reinforcement_uses_the_same_consumer_validation_closure(
    tmp_path: Path,
) -> None:
    scenario, batch = _batch_for_case(tmp_path, "not_observed")
    object.__setattr__(batch, "sample", scenario.fixture.sample("anchor-1"))

    with pytest.raises(ModeTrackContractError):
        reinforce_mode_track_model_set(
            processing_owners=scenario.fixture.owners,
            model_set=scenario.models,
            assignment_batch=batch,
        )


def test_consumer_validator_is_exported() -> None:
    assert (
        "validate_mode_track_assignment_batch_for_consumption"
        in mode_track_assignment.__all__
    )
