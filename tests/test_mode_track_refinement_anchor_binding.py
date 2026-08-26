"""Anchor identity boundary tests for proposal-only ModeTrack refinement."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_trajectory_input_rejects_anchor_minted_for_another_group(
    tmp_path: Path,
) -> None:
    from tests.test_alignment_mode_track_assignment import _scenario
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        classify_cross_sample_initial_neighborhood_edge,
        construct_cross_sample_peak_group_hypotheses,
        validate_cross_sample_peak_group_identity,
    )
    from xic_extractor.alignment.mode_track import (
        ModeTrackEvidenceProfile,
        ModeTrackRefinementContractError,
        mint_mode_track_trajectory_input,
    )
    from xic_extractor.alignment.mode_track.assignment import (
        mint_mode_track_support_anchor,
    )

    scenario = _scenario(tmp_path)
    predecessor = scenario.track_a_spine.group
    original_anchor = scenario.track_a_spine.anchors[0]
    owners = predecessor.owners
    anchor_owner = next(
        owner
        for owner in owners
        if owner.owner_id == original_anchor.peak_hypothesis_id
    )
    alternate_group = construct_cross_sample_peak_group_hypotheses(
        (anchor_owner,),
        config=AlignmentConfig(),
    )[0]
    alternate_anchor = mint_mode_track_support_anchor(
        processing_owners=scenario.fixture.owners,
        track=alternate_group,
        sample=original_anchor.sample,
        direct_support_assessment=original_anchor.direct_support_assessment,
        peak_hypothesis=original_anchor.peak_hypothesis,
    )
    identity = validate_cross_sample_peak_group_identity(
        predecessor,
        require_canonical=True,
    )
    edges = tuple(
        classify_cross_sample_initial_neighborhood_edge(
            left_owner=owners[left_index],
            right_owner=owners[right_index],
            context="complete_link",
            config=AlignmentConfig(),
        )
        for left_index in range(len(owners))
        for right_index in range(left_index + 1, len(owners))
    )

    with pytest.raises(ModeTrackRefinementContractError, match="canonical owner"):
        mint_mode_track_trajectory_input(
            predecessors=(identity,),
            owners=owners,
            classified_edges=edges,
            anchors=(alternate_anchor,),
            injection_order={
                anchor.sample.metadata.raw_stem: anchor.injection_order
                for anchor in scenario.track_a_spine.anchors
            },
            evidence_profile=ModeTrackEvidenceProfile(
                "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
            ),
            budget=CrossSampleNeighborhoodBudget(
                max_members=86,
                max_eligible_edges=3655,
            ),
        )
