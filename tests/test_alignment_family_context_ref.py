from __future__ import annotations

import json
from dataclasses import fields

import pytest

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.mode_track.family_context import (
    FamilyContextRef,
    canonical_family_context_ref_bytes,
    mint_family_context_ref,
)


def _track(
    group_hypothesis_id: str,
    *,
    center_mz: float = 500.0,
    center_rt: float = 8.0,
) -> CrossSamplePeakGroupHypothesis:
    return CrossSamplePeakGroupHypothesis(
        group_hypothesis_id=group_hypothesis_id,
        public_family_id="display-family",
        owner_ids=(f"owner:{group_hypothesis_id}",),
        event_ids=(f"event:{group_hypothesis_id}",),
        event_member_count=1,
        group_center_mz=center_mz,
        group_center_rt=center_rt,
    )


def test_family_context_ref_is_internal_canonical_and_order_independent() -> None:
    first = _track("track-a", center_rt=8.0)
    second = _track("track-b", center_rt=8.4)

    forward = mint_family_context_ref(
        mz_interval=(499.9, 500.1),
        rt_interval=(7.5, 8.8),
        context_policy_version="mode-track-family-context-v1",
        source_lineage_refs=("source-packet:abc", "trace-query:def"),
        mode_tracks=(first, second),
    )
    reversed_input = mint_family_context_ref(
        mz_interval=(499.9, 500.1),
        rt_interval=(7.5, 8.8),
        context_policy_version="mode-track-family-context-v1",
        source_lineage_refs=("trace-query:def", "source-packet:abc"),
        mode_tracks=(second, first),
    )

    assert forward == reversed_input
    assert forward.group_hypothesis_ids == ("track-a", "track-b")
    assert forward.mode_tracks == (first, second)
    assert forward.validation_status == "shadow_ready"
    assert forward.product_authority == "none"
    assert canonical_family_context_ref_bytes(forward) == (
        canonical_family_context_ref_bytes(reversed_input)
    )

    payload = json.loads(canonical_family_context_ref_bytes(forward))
    assert payload["schema_version"] == "family_context_ref_v1"
    assert payload["group_hypothesis_ids"] == ["track-a", "track-b"]
    assert "display-family" not in canonical_family_context_ref_bytes(forward).decode()
    assert "method_subject" not in canonical_family_context_ref_bytes(forward).decode()


@pytest.mark.parametrize("mode_tracks", ((), (_track("track-a"), _track("track-b"))))
def test_family_context_ref_allows_zero_or_multiple_mode_tracks(mode_tracks) -> None:
    context = mint_family_context_ref(
        mz_interval=(499.9, 500.1),
        rt_interval=(7.5, 8.8),
        context_policy_version="mode-track-family-context-v1",
        source_lineage_refs=("source-packet:abc",),
        mode_tracks=mode_tracks,
    )

    assert len(context.group_hypothesis_ids) == len(mode_tracks)


def test_family_context_ref_keeps_same_display_family_tracks_distinct() -> None:
    context = mint_family_context_ref(
        mz_interval=(499.9, 500.1),
        rt_interval=(7.5, 8.8),
        context_policy_version="mode-track-family-context-v1",
        source_lineage_refs=("source-packet:abc",),
        mode_tracks=(_track("track-a"), _track("track-b")),
    )

    assert context.group_hypothesis_ids == ("track-a", "track-b")
    assert {track.public_family_id for track in context.mode_tracks} == {
        "display-family"
    }


def test_family_context_ref_rejects_duplicate_or_out_of_context_tracks() -> None:
    with pytest.raises(ValueError, match="duplicate group_hypothesis_id"):
        mint_family_context_ref(
            mz_interval=(499.9, 500.1),
            rt_interval=(7.5, 8.8),
            context_policy_version="mode-track-family-context-v1",
            source_lineage_refs=("source-packet:abc",),
            mode_tracks=(_track("track-a"), _track("track-a")),
        )

    with pytest.raises(ValueError, match="outside the Family context RT interval"):
        mint_family_context_ref(
            mz_interval=(499.9, 500.1),
            rt_interval=(7.5, 8.8),
            context_policy_version="mode-track-family-context-v1",
            source_lineage_refs=("source-packet:abc",),
            mode_tracks=(_track("track-a", center_rt=9.0),),
        )


def test_family_context_ref_rejects_tracks_that_share_an_owner() -> None:
    shared_owner = "owner:shared-physical-hypothesis"
    first = CrossSamplePeakGroupHypothesis(
        group_hypothesis_id="track-a",
        public_family_id="display-family",
        owner_ids=(shared_owner,),
        event_ids=("event:a",),
        event_member_count=1,
        group_center_mz=500.0,
        group_center_rt=8.0,
    )
    second = CrossSamplePeakGroupHypothesis(
        group_hypothesis_id="track-b",
        public_family_id="display-family",
        owner_ids=(shared_owner,),
        event_ids=("event:b",),
        event_member_count=1,
        group_center_mz=500.0,
        group_center_rt=8.4,
    )

    with pytest.raises(ValueError, match="ModeTracks must not share owner ids"):
        mint_family_context_ref(
            mz_interval=(499.9, 500.1),
            rt_interval=(7.5, 8.8),
            context_policy_version="mode-track-family-context-v1",
            source_lineage_refs=("source-packet:abc",),
            mode_tracks=(first, second),
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"mz_interval": (500.1, 499.9)}, "mz_interval"),
        ({"rt_interval": (8.8, 7.5)}, "rt_interval"),
        ({"context_policy_version": ""}, "context_policy_version"),
        ({"source_lineage_refs": ()}, "source_lineage_refs"),
        (
            {"source_lineage_refs": ("source-packet:abc", "source-packet:abc")},
            "source_lineage_refs",
        ),
    ),
)
def test_family_context_ref_rejects_malformed_identity_inputs(kwargs, message) -> None:
    arguments = {
        "mz_interval": (499.9, 500.1),
        "rt_interval": (7.5, 8.8),
        "context_policy_version": "mode-track-family-context-v1",
        "source_lineage_refs": ("source-packet:abc",),
        "mode_tracks": (),
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError, match=message):
        mint_family_context_ref(**arguments)


def test_family_context_ref_cannot_be_caller_forged_or_gain_public_fields() -> None:
    with pytest.raises(TypeError, match="_mint_token"):
        FamilyContextRef(
            family_context_id="family-context:forged",
            mz_interval=(499.9, 500.1),
            rt_interval=(7.5, 8.8),
            context_policy_version="mode-track-family-context-v1",
            source_lineage_refs=("source-packet:abc",),
            mode_tracks=(),
            group_hypothesis_ids=(),
        )

    field_names = {field.name for field in fields(FamilyContextRef)}
    assert "method_subject_id" not in field_names
    assert "public_family_id" not in field_names
    assert "feature_family_id" not in field_names
    assert "display_label" not in field_names
    assert "matrix_value" not in field_names
    assert "selected_area" not in field_names


def test_family_context_ref_identity_changes_with_exact_context_or_lineage() -> None:
    base = mint_family_context_ref(
        mz_interval=(499.9, 500.1),
        rt_interval=(7.5, 8.8),
        context_policy_version="mode-track-family-context-v1",
        source_lineage_refs=("source-packet:abc",),
        mode_tracks=(_track("track-a"),),
    )
    changed_lineage = mint_family_context_ref(
        mz_interval=(499.9, 500.1),
        rt_interval=(7.5, 8.8),
        context_policy_version="mode-track-family-context-v1",
        source_lineage_refs=("source-packet:changed",),
        mode_tracks=(_track("track-a"),),
    )
    changed_membership = mint_family_context_ref(
        mz_interval=(499.9, 500.1),
        rt_interval=(7.5, 8.8),
        context_policy_version="mode-track-family-context-v1",
        source_lineage_refs=("source-packet:abc",),
        mode_tracks=(_track("track-a"), _track("track-b")),
    )

    assert base.family_context_id != changed_lineage.family_context_id
    assert base.family_context_id != changed_membership.family_context_id
