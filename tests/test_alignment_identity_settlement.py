from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from tests.test_alignment_owner_clustering import _owner
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.drift_evidence import (
    LocalFeatureLandmarkEvidence,
    LocalTrajectoryAssessment,
    LocalTrajectoryEvidenceLookup,
    assess_local_trajectory,
)
from xic_extractor.alignment.identity_settlement import (
    IdentityModelEvidence,
    IdentitySettlementDecision,
    mint_identity_relation_evidence,
    settle_identity_model,
)


@pytest.mark.parametrize(
    ("relation", "component_pattern", "trajectory_status", "model", "state"),
    (
        ("same_identity", "single_component", "assessable", "H1a", "resolved"),
        (
            "same_identity",
            "repeated_multiple_components",
            "assessable",
            "H1b",
            "resolved",
        ),
        (
            "different_identities",
            "repeated_multiple_components",
            "assessable",
            "H2",
            "resolved",
        ),
        ("artifact", "unreliable_feature", "poor_landmarks", "H3", "resolved"),
        (
            "unresolved",
            "repeated_multiple_components",
            "assessable",
            None,
            "scientific_uncertainty",
        ),
        (
            "same_identity",
            "repeated_multiple_components",
            "conflict",
            None,
            "scientific_uncertainty",
        ),
    ),
)
def test_h1a_h1b_h2_h3_truth_table_is_explicit_and_shadow_only(
    tmp_path: Path,
    relation,
    component_pattern,
    trajectory_status,
    model,
    state,
) -> None:
    group, component_partition = _group_and_partition(
        tmp_path,
        component_pattern,
    )
    decision = settle_identity_model(
        IdentityModelEvidence(
            group=group,
            identity_evidence=_identity_evidence(group, relation),
            component_pattern=component_pattern,
            component_partition=component_partition,
            trajectory=_trajectory(group, trajectory_status),
            reason_codes=("preregistered_truth_case",),
        )
    )

    assert decision.model == model
    assert decision.state == state
    assert decision.product_authority == "none"
    assert decision.validation_status == "shadow_ready"
    assert decision.numeric_projection_allowed is False
    assert decision.allow_component_support_or is (model == "H1b")
    assert decision.allow_area_aggregation is (model == "H1b")


def test_repeated_components_do_not_prove_two_identities(tmp_path: Path) -> None:
    group, component_partition = _group_and_partition(
        tmp_path,
        "repeated_multiple_components",
    )
    decision = settle_identity_model(
        IdentityModelEvidence(
            group=group,
            identity_evidence=None,
            component_pattern="repeated_multiple_components",
            component_partition=component_partition,
            trajectory=_trajectory(group, "assessable"),
            reason_codes=("two_components_observed",),
        )
    )

    assert decision.model is None
    assert decision.state == "scientific_uncertainty"
    assert "one_component_model_disfavored" in decision.reason_codes
    assert "repeated_components_do_not_resolve_identity" in decision.reason_codes


def test_repeated_component_pattern_requires_exact_partition_evidence() -> None:
    group = _group()

    with pytest.raises(ValueError, match="owner-bound partition evidence"):
        IdentityModelEvidence(
            group=group,
            identity_evidence=_identity_evidence(group, "same_identity"),
            component_pattern="repeated_multiple_components",
            component_partition=None,
            trajectory=_trajectory(group, "assessable"),
            reason_codes=("unbound_component_pattern",),
        )


@pytest.mark.parametrize("status", ("missing_landmarks", "poor_landmarks"))
def test_missing_or_poor_landmarks_cannot_resolve_same_identity(status) -> None:
    group = _group()
    decision = settle_identity_model(
        IdentityModelEvidence(
            group=group,
            identity_evidence=_identity_evidence(group, "same_identity"),
            component_pattern="single_component",
            component_partition=None,
            trajectory=_trajectory(group, status),
            reason_codes=("direct_identity_but_trajectory_unassessable",),
        )
    )

    assert decision.state == "scientific_uncertainty"
    assert decision.model is None
    assert "trajectory_conflict" not in decision.reason_codes
    assert "identity_resolution_requires_assessable_trajectory" in (
        decision.reason_codes
    )


def test_identity_and_trajectory_evidence_are_bound_to_the_exact_group() -> None:
    group = _group()
    other_group = _group(("sample-x", "sample-y"))
    other_identity = _identity_evidence(other_group, "same_identity")

    with pytest.raises(ValueError, match="different group"):
        IdentityModelEvidence(
            group=group,
            identity_evidence=other_identity,
            component_pattern="single_component",
            component_partition=None,
            trajectory=_trajectory(group, "assessable"),
            reason_codes=("crossed_group_identity",),
        )

    with pytest.raises(ValueError, match="group owner population"):
        IdentityModelEvidence(
            group=group,
            identity_evidence=_identity_evidence(group, "same_identity"),
            component_pattern="single_component",
            component_partition=None,
            trajectory=_trajectory(other_group, "assessable"),
            reason_codes=("crossed_group_trajectory",),
        )


def test_derived_identity_decision_cannot_be_caller_forged(
    tmp_path: Path,
) -> None:
    group, component_partition = _group_and_partition(
        tmp_path,
        "repeated_multiple_components",
    )
    evidence = IdentityModelEvidence(
        group=group,
        identity_evidence=None,
        component_pattern="repeated_multiple_components",
        component_partition=component_partition,
        trajectory=_trajectory(group, "assessable"),
        reason_codes=("unresolved",),
    )

    with pytest.raises(ValueError, match="minted by their owner"):
        IdentitySettlementDecision(
            evidence=evidence,
            model="H1b",
            state="resolved",
            allow_component_support_or=True,
            allow_area_aggregation=True,
            reason_codes=("forged",),
            _mint_token=object(),
        )


def test_no_unvalidated_threshold_or_public_value_fields_enter_model_contract() -> None:
    model_fields = {
        field.name
        for owner in (IdentityModelEvidence, LocalTrajectoryAssessment)
        for field in fields(owner)
    }
    forbidden_fragments = {
        "threshold",
        "tolerance",
        "valley_ratio",
        "width_ratio",
        "matrix_value",
        "selected_area",
        "write_matrix_value",
    }

    assert not any(
        fragment in field_name
        for field_name in model_fields
        for fragment in forbidden_fragments
    )


def _group(sample_stems: tuple[str, str] = ("sample-a", "sample-b")):
    groups = construct_cross_sample_peak_group_hypotheses(
        (
            _owner(sample_stems[0], "a", apex_rt=8.00),
            _owner(sample_stems[1], "b", apex_rt=8.05),
        ),
        config=AlignmentConfig(identity_rt_candidate_window_sec=180.0),
    )
    assert len(groups) == 1
    return groups[0]


def _group_and_partition(tmp_path: Path, component_pattern):
    if component_pattern != "repeated_multiple_components":
        return _group(), None
    from tests.test_alignment_split_lineage import (
        _parent_and_components,
        _partition,
    )

    parent, components, universes, _fixture = _parent_and_components(tmp_path)
    return parent.group_context.group, _partition(parent, components, universes)


def _identity_evidence(group, relation):
    if relation == "unresolved":
        return None
    return mint_identity_relation_evidence(
        group=group,
        relation=relation,
        evidence_refs=(f"oracle:{relation}",),
    )


def _trajectory(group, status):
    sample_stems = tuple(sorted(owner.sample_stem for owner in group.owners))
    retained_stems = sample_stems[:1] if status == "missing_landmarks" else sample_stems
    quality = "poor" if status == "poor_landmarks" else "accepted"
    points = tuple(
        LocalFeatureLandmarkEvidence(
            sample_stem=sample_stem,
            injection_order=index,
            acquisition_source_id=f"acquisition-source:{sample_stem}",
            landmark_id=f"landmark-{index}",
            observed_rt_min=8.0 + index * 0.01,
            local_trajectory_rt_min=8.0,
            rt_drift_delta_min=index * 0.01,
            quality=quality,
            evidence_ref=f"landmark:{sample_stem}",
            exclusion_reason="shape_unstable" if quality == "poor" else "",
        )
        for index, sample_stem in enumerate(retained_stems, start=1)
    )
    conflict_refs = (points[0].evidence_ref,) if status == "conflict" else ()
    return assess_local_trajectory(
        LocalTrajectoryEvidenceLookup(points=points),
        sample_stems,
        conflict_evidence_refs=conflict_refs,
    )
