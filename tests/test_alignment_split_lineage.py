from __future__ import annotations

import ast
import inspect
from dataclasses import fields
from pathlib import Path

import pytest

import xic_extractor.alignment.identity_settlement as identity_settlement_module
from tests.issue161_no_raw_fixture import issue161_peak
from tests.test_alignment_conservative_backfill import (
    _recovery_universe,
    _spine,
    _study_fixture,
)
from xic_extractor.alignment.conservative_backfill import (
    assess_conservative_backfill_group,
)
from xic_extractor.alignment.drift_evidence import (
    LocalFeatureLandmarkEvidence,
    LocalTrajectoryEvidenceLookup,
    assess_local_trajectory,
)
from xic_extractor.alignment.identity_settlement import (
    IdentityModelEvidence,
    SplitComponentEvidence,
    SplitLineageDecision,
    aggregate_same_identity_components,
    assess_split_lineage,
    mint_identity_relation_evidence,
    mint_split_component_evidence,
    settle_identity_model,
)


def test_split_lifecycle_preserves_evidence_and_blocks_only_candidate_onward(
    tmp_path: Path,
) -> None:
    parent, components, universes, _fixture = _parent_and_components(tmp_path)
    uncertain = _settlement(parent, relation="unresolved")

    watch = assess_split_lineage(
        parent_group=parent,
        components=components,
        candidate_universes=universes,
        lifecycle_state="split_watch",
        identity_settlement=uncertain,
        evidence_refs=("split:visual-review",),
    )
    candidate = assess_split_lineage(
        parent_group=parent,
        components=components,
        candidate_universes=universes,
        lifecycle_state="split_candidate",
        identity_settlement=uncertain,
        evidence_refs=("split:repeated-components",),
    )

    assert watch.blocks_parent_backfill is False
    assert watch.blocks_parent_numeric_projection is False
    assert watch.parent_superseded is False
    assert watch.children == ()
    assert watch.components == components
    assert candidate.blocks_parent_backfill is True
    assert candidate.blocks_parent_numeric_projection is True
    assert candidate.parent_superseded is False
    assert candidate.children == ()
    assert candidate.components == components
    assert candidate.product_authority == "none"
    assert candidate.numeric_projection_allowed is False


def test_split_supported_creates_exact_children_and_recomputes_s(
    tmp_path: Path,
) -> None:
    parent, components, universes, _fixture = _parent_and_components(tmp_path)
    same_identity = _settlement(parent, relation="same_identity")

    decision = assess_split_lineage(
        parent_group=parent,
        components=components,
        candidate_universes=universes,
        lifecycle_state="split_supported",
        identity_settlement=same_identity,
        evidence_refs=("split:resolved-same-identity",),
    )

    assert parent.direct_support_count_s == 2
    assert parent.minimum_support_k == 2
    assert parent.may_attempt_recovery is True
    assert decision.parent_superseded is True
    assert decision.blocks_parent_backfill is True
    assert decision.blocks_parent_numeric_projection is True
    assert tuple(child.component_id for child in decision.children) == (
        "component-a",
        "component-b",
    )
    assert tuple(child.direct_support_count_s for child in decision.children) == (1, 1)
    assert tuple(child.minimum_support_k for child in decision.children) == (2, 2)
    assert not any(
        child.meets_direct_support_threshold for child in decision.children
    )
    assert all(
        child.parent_group_hypothesis_id
        == parent.group_context.group.group_hypothesis_id
        for child in decision.children
    )
    assert len({child.child_group_hypothesis_id for child in decision.children}) == 2


def test_split_supported_rejects_unresolved_identity(tmp_path: Path) -> None:
    parent, components, universes, _fixture = _parent_and_components(tmp_path)

    with pytest.raises(ValueError, match="resolved H1b or H2"):
        assess_split_lineage(
            parent_group=parent,
            components=components,
            candidate_universes=universes,
            lifecycle_state="split_supported",
            identity_settlement=_settlement(parent, relation="unresolved"),
            evidence_refs=("split:insufficient",),
        )


def test_physical_observation_cannot_transfer_between_components(
    tmp_path: Path,
) -> None:
    parent, components, universes, _fixture = _parent_and_components(tmp_path)
    duplicated = (
        components[0],
        mint_split_component_evidence(
            parent_group=parent,
            candidate_universes=universes,
            component_id="component-b",
            peak_observations=(components[0].peak_observations[0],),
            direct_support_cells=(components[0].direct_support_cells[0],),
            membership_evidence_refs=("membership:adversarial-transfer",),
        ),
    )

    with pytest.raises(ValueError, match="physical peak cannot be transferred"):
        assess_split_lineage(
            parent_group=parent,
            components=duplicated,
            candidate_universes=universes,
            lifecycle_state="split_watch",
            identity_settlement=_settlement(parent, relation="unresolved"),
            evidence_refs=("split:adversarial-transfer",),
        )


def test_component_observations_reject_crossed_target_and_attempt(
    tmp_path: Path,
) -> None:
    parent, components, universes, fixture = _parent_and_components(tmp_path)
    other_target_parent = assess_conservative_backfill_group(
        _spine(
            fixture,
            support_apex_by_sample={"s1": 8.40, "s2": 8.45},
            invocation="issue163-crossed-target",
            target=fixture.other_target,
        ).cells,
        sample_population=fixture.samples,
    )
    retry = fixture.retry()
    retry_parent = assess_conservative_backfill_group(
        _spine(
            retry,
            support_apex_by_sample={"s1": 8.40, "s2": 8.45},
            invocation="issue163-crossed-attempt",
        ).cells,
        sample_population=retry.samples,
    )

    for crossed_parent in (other_target_parent, retry_parent):
        crossed_peak = crossed_parent.support_trace_refs[0]
        with pytest.raises(ValueError, match="exact parent candidate universe"):
            _append_observation(
                parent,
                universes,
                components,
                crossed_peak,
            )


def test_candidate_universe_alone_does_not_mint_component_membership(
    tmp_path: Path,
) -> None:
    parent, components, universes, _fixture = _parent_and_components(
        tmp_path,
        add_second_s1_peak=True,
    )

    with pytest.raises(ValueError, match="membership_evidence_refs"):
        mint_split_component_evidence(
            parent_group=parent,
            candidate_universes=universes,
            component_id="component-c",
            peak_observations=(components[1].peak_observations[-1],),
            direct_support_cells=(),
            membership_evidence_refs=(),
        )


def test_duplicate_candidate_universes_for_one_sample_are_rejected(
    tmp_path: Path,
) -> None:
    parent, components, universes, fixture = _parent_and_components(
        tmp_path,
        add_second_s1_peak=True,
    )
    alternate_peak = issue161_peak(
        fixture,
        "s1",
        apex_rt=8.40,
        rt_left_min=8.30,
        rt_right_min=8.50,
        height=125.0,
    )
    conflicting = _recovery_universe(
        fixture,
        (parent,),
        "s1",
        (alternate_peak,),
    )

    for duplicate_universes in (
        (universes[0], universes[0]),
        (universes[0], conflicting),
    ):
        with pytest.raises(ValueError, match="unique by sample"):
            assess_split_lineage(
                parent_group=parent,
                components=components,
                candidate_universes=duplicate_universes,
                lifecycle_state="split_watch",
                identity_settlement=_settlement(parent, relation="unresolved"),
                evidence_refs=("split:duplicate-universe",),
            )


def test_split_lineage_decision_cannot_be_caller_forged(tmp_path: Path) -> None:
    parent, components, universes, _fixture = _parent_and_components(tmp_path)

    with pytest.raises(ValueError, match="minted by their owner"):
        SplitLineageDecision(
            lifecycle_state="split_supported",
            parent_group=parent,
            identity_settlement=_settlement(parent, relation="unresolved"),
            components=components,
            candidate_universes=universes,
            evidence_refs=("split:forged",),
            parent_superseded=True,
            blocks_parent_backfill=True,
            blocks_parent_numeric_projection=True,
            children=(),
            _mint_token=object(),
        )


def test_same_identity_or_and_area_aggregation_require_resolved_h1b(
    tmp_path: Path,
) -> None:
    parent, components, universes, _fixture = _parent_and_components(
        tmp_path,
        add_second_s1_peak=True,
    )
    same_identity = _settlement(parent, relation="same_identity")
    unresolved = _settlement(parent, relation="unresolved")
    two_identities = _settlement(parent, relation="different_identities")

    supported_same_identity = assess_split_lineage(
        parent_group=parent,
        components=components,
        candidate_universes=universes,
        lifecycle_state="split_supported",
        identity_settlement=same_identity,
        evidence_refs=("split:resolved-same-identity",),
    )
    candidate_unresolved = assess_split_lineage(
        parent_group=parent,
        components=components,
        candidate_universes=universes,
        lifecycle_state="split_candidate",
        identity_settlement=unresolved,
        evidence_refs=("split:unresolved",),
    )
    supported_two_identities = assess_split_lineage(
        parent_group=parent,
        components=components,
        candidate_universes=universes,
        lifecycle_state="split_supported",
        identity_settlement=two_identities,
        evidence_refs=("split:resolved-two-identities",),
    )

    summary = aggregate_same_identity_components(supported_same_identity)

    assert summary is not None
    assert summary.cid_nl_support is True
    assert summary.component_ids == ("component-a", "component-b")
    assert summary.component_observations == tuple(
        observation
        for component in components
        for observation in component.peak_observations
    )
    sample_s1 = (
        components[0]
        .peak_observations[0]
        .content.canonical_sample_identity_id
    )
    expected_s1 = sum(
        observation.content.integration.area_raw_counts_seconds
        for component in components
        for observation in component.peak_observations
        if observation.content.canonical_sample_identity_id == sample_s1
    )
    assert dict(summary.area_by_sample)[sample_s1] == expected_s1
    assert aggregate_same_identity_components(candidate_unresolved) is None
    assert aggregate_same_identity_components(supported_two_identities) is None
    assert summary.product_authority == "none"


def test_shadow_split_contract_has_no_writer_or_selected_value_fields() -> None:
    owner_fields = {
        field.name
        for field in fields(SplitComponentEvidence)
    }
    assert owner_fields.isdisjoint(
        {
            "matrix_value",
            "selected_area",
            "write_matrix_value",
            "writer",
            "activation_vote",
        }
    )


def test_identity_settlement_module_has_no_writer_or_public_projection_import() -> None:
    tree = ast.parse(inspect.getsource(identity_settlement_module))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)

    forbidden = (
        "xic_extractor.alignment.matrix",
        "xic_extractor.alignment.matrix_identity",
        "xic_extractor.alignment.pipeline_outputs",
        "xic_extractor.alignment.product_matrix",
        "xic_extractor.alignment.production_decisions",
        "xic_extractor.alignment.tsv_writer",
        "xic_extractor.alignment.xlsx_writer",
        "xic_extractor.output",
    )
    assert not any(
        module_name.startswith(forbidden) for module_name in imported_modules
    )


def _parent_and_components(
    tmp_path: Path,
    *,
    add_second_s1_peak: bool = False,
):
    fixture = _study_fixture(tmp_path, ("s1", "s2", "s3"))
    spine = _spine(
        fixture,
        support_apex_by_sample={"s1": 8.00, "s2": 8.20},
        invocation="issue163-lineage",
    )
    parent = assess_conservative_backfill_group(
        spine.cells,
        sample_population=fixture.samples,
    )
    support_cells = {
        cell.content.canonical_sample_identity_id: cell
        for cell in spine.cells
        if cell.content.method_result == "SUPPORT"
    }
    s1 = fixture.sample("s1").identity.sample_identity_id
    s2 = fixture.sample("s2").identity.sample_identity_id
    cell_s1 = support_cells[s1]
    cell_s2 = support_cells[s2]
    assert cell_s1.peak_observation is not None
    assert cell_s2.peak_observation is not None
    component_b_peaks = [cell_s2.peak_observation]
    candidate_universes = ()
    if add_second_s1_peak:
        second_s1_peak = issue161_peak(
            fixture,
            "s1",
            apex_rt=8.20,
            rt_left_min=8.10,
            rt_right_min=8.30,
            height=250.0,
        )
        component_b_peaks.append(second_s1_peak)
        candidate_universes = (
            _recovery_universe(
                fixture,
                (parent,),
                "s1",
                (cell_s1.peak_observation, second_s1_peak),
            ),
        )
    components = (
        mint_split_component_evidence(
            parent_group=parent,
            candidate_universes=candidate_universes,
            component_id="component-a",
            peak_observations=(cell_s1.peak_observation,),
            direct_support_cells=(cell_s1,),
            membership_evidence_refs=("membership:component-a",),
        ),
        mint_split_component_evidence(
            parent_group=parent,
            candidate_universes=candidate_universes,
            component_id="component-b",
            peak_observations=tuple(component_b_peaks),
            direct_support_cells=(cell_s2,),
            membership_evidence_refs=("membership:component-b",),
        ),
    )
    return parent, components, candidate_universes, fixture


def _settlement(parent, *, relation):
    group = parent.group_context.group
    sample_stems = tuple(sorted(owner.sample_stem for owner in group.owners))
    landmarks = tuple(
        LocalFeatureLandmarkEvidence(
            sample_stem=sample_stem,
            injection_order=index,
            landmark_id=f"landmark-{index}",
            observed_rt_min=8.0 + index * 0.01,
            local_trajectory_rt_min=8.0,
            rt_drift_delta_min=index * 0.01,
            quality="accepted",
            evidence_ref=f"landmark:{sample_stem}",
        )
        for index, sample_stem in enumerate(sample_stems, start=1)
    )
    return settle_identity_model(
        IdentityModelEvidence(
            group=group,
            identity_evidence=(
                None
                if relation == "unresolved"
                else mint_identity_relation_evidence(
                    group=group,
                    relation=relation,
                    evidence_refs=(f"oracle:{relation}",),
                )
            ),
            component_pattern="repeated_multiple_components",
            trajectory=assess_local_trajectory(
                LocalTrajectoryEvidenceLookup(points=landmarks),
                sample_stems,
            ),
            reason_codes=("repeated_components",),
        )
    )


def _append_observation(parent, candidate_universes, components, observation):
    return (
        components[0],
        mint_split_component_evidence(
            parent_group=parent,
            candidate_universes=candidate_universes,
            component_id=components[1].component_id,
            peak_observations=(*components[1].peak_observations, observation),
            direct_support_cells=components[1].direct_support_cells,
            membership_evidence_refs=components[1].membership_evidence_refs,
        ),
    )
