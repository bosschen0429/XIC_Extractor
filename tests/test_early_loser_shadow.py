from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.early_loser_shadow import (
    EARLY_LOSER_SHADOW_SCHEMA_VERSION,
    build_early_loser_shadow_proposal,
    compare_early_loser_shadow_with_final_authority,
)
from xic_extractor.alignment.early_loser_shadow_writer import (
    write_early_loser_shadow_json,
)
from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.owner_clustering import OwnerAlignedFeature
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    SampleLocalMS1Owner,
)


def test_shadow_accepts_transitive_two_stage_final_lineage() -> None:
    loser, middle, winner = _proposal_features()
    proposal = _proposal((loser, middle, winner))
    final = _matrix(
        (
            _loser(loser, winner="GROUP-MIDDLE"),
            _loser(middle, winner="GROUP-WINNER"),
            _winner(winner),
        )
    )

    report = compare_early_loser_shadow_with_final_authority(proposal, final)
    rows = {row.source_group_hypothesis_id: row for row in report.rows}

    assert report.verdict == "GO"
    assert report.activation_allowed is False
    assert report.confirmed_loser_count == 1
    assert report.late_loser_count == 1
    assert rows["GROUP-LOSER"].proposal_lineage == (
        "GROUP-LOSER",
        "GROUP-MIDDLE",
    )
    assert rows["GROUP-LOSER"].final_lineage == (
        "GROUP-LOSER",
        "GROUP-MIDDLE",
        "GROUP-WINNER",
    )
    assert rows["GROUP-LOSER"].comparison_status == "confirmed_loser"
    assert rows["GROUP-MIDDLE"].comparison_status == "late_loser"
    assert report.baseline_workload.requested_feature_count == 3
    assert report.proposed_workload.requested_feature_count == 2
    assert report.projected_feature_reduction == 1
    assert report.baseline_workload.requested_cell_count == 8
    assert report.proposed_workload.requested_cell_count == 4
    assert report.projected_cell_reduction == 4
    assert report.baseline_workload.logical_xic_request_count == 8
    assert report.proposed_workload.logical_xic_request_count == 5
    assert report.projected_logical_xic_request_reduction == 3
    assert report.actual_savings_claim.startswith("unavailable:")
    assert all(
        surface.observed_shadow_diff_count == 0
        for surface in report.expected_diff_surfaces
    )


def test_shadow_is_order_deterministic() -> None:
    loser, middle, winner = _proposal_features()
    final = _matrix(
        (
            _winner(winner),
            _loser(middle, winner="GROUP-WINNER"),
            _loser(loser, winner="GROUP-MIDDLE"),
        )
    )

    forward = compare_early_loser_shadow_with_final_authority(
        _proposal((loser, middle, winner)),
        final,
    )
    reversed_report = compare_early_loser_shadow_with_final_authority(
        _proposal((winner, middle, loser)),
        final,
    )

    assert forward == reversed_report


def test_shadow_rejects_final_lineage_cycle() -> None:
    loser, middle, winner = _proposal_features()
    proposal = _proposal((loser, middle, winner))
    final = _matrix(
        (
            _loser(loser, winner="GROUP-MIDDLE"),
            _loser(middle, winner="GROUP-LOSER"),
            _winner(winner),
        )
    )

    with pytest.raises(ValueError, match="contains a cycle"):
        compare_early_loser_shadow_with_final_authority(proposal, final)


def test_shadow_rejects_missing_final_winner_reference() -> None:
    loser, middle, winner = _proposal_features()
    proposal = _proposal((loser, middle, winner))
    final = _matrix(
        (
            _loser(loser, winner="GROUP-MISSING"),
            _winner(middle),
            _winner(winner),
        )
    )

    with pytest.raises(ValueError, match="references missing winner"):
        compare_early_loser_shadow_with_final_authority(proposal, final)


def test_shadow_rejects_final_loser_terminating_at_non_winner() -> None:
    loser, middle, winner = _proposal_features()
    proposal = _proposal((loser, middle, winner))
    final = _matrix(
        (
            _loser(loser, winner="GROUP-MIDDLE"),
            middle,
            _winner(winner),
        )
    )

    with pytest.raises(ValueError, match="terminates at non-winner"):
        compare_early_loser_shadow_with_final_authority(proposal, final)


def test_shadow_rejects_final_winner_without_self_reference() -> None:
    loser, middle, winner = _proposal_features()
    proposal = _proposal((loser, middle, winner))
    malformed_winner = replace(
        _winner(middle),
        consolidation_winner_group_hypothesis_id="GROUP-WINNER",
    )
    final = _matrix(
        (
            _loser(loser, winner="GROUP-MIDDLE"),
            malformed_winner,
            _winner(winner),
        )
    )

    with pytest.raises(ValueError, match="must self-reference its winner"):
        compare_early_loser_shadow_with_final_authority(proposal, final)


def test_shadow_kills_false_loser_proposal() -> None:
    loser, middle, winner = _proposal_features()
    proposal = _proposal((loser, middle, winner))
    final = _matrix((loser, _winner(middle), winner))

    report = compare_early_loser_shadow_with_final_authority(proposal, final)

    assert report.verdict == "KILL"
    assert report.false_loser_count == 1
    assert report.winner_mismatch_count == 0


def test_shadow_kills_terminal_winner_mismatch() -> None:
    loser, middle, winner = _proposal_features()
    proposal = _proposal((loser, middle, winner))
    final = _matrix(
        (
            _loser(loser, winner="GROUP-WINNER"),
            middle,
            _winner(winner),
        )
    )

    report = compare_early_loser_shadow_with_final_authority(proposal, final)

    assert report.verdict == "KILL"
    assert report.false_loser_count == 0
    assert report.winner_mismatch_count == 1


@pytest.mark.parametrize("projected_reduction", (0, -1))
def test_shadow_kills_insufficient_projected_decision_value(
    projected_reduction: int,
) -> None:
    loser, middle, winner = _proposal_features()
    proposal = _proposal((loser, middle, winner))
    baseline = proposal.baseline_workload
    proposal = replace(
        proposal,
        proposed_workload=replace(
            baseline,
            requested_feature_count=(
                baseline.requested_feature_count - projected_reduction
            ),
            requested_cell_count=(
                baseline.requested_cell_count - projected_reduction
            ),
            logical_xic_request_count=(
                baseline.logical_xic_request_count - projected_reduction
            ),
        ),
    )
    final = _matrix(
        (
            _loser(loser, winner="GROUP-MIDDLE"),
            _winner(middle),
            _winner(winner),
        )
    )

    report = compare_early_loser_shadow_with_final_authority(proposal, final)

    assert report.verdict == "KILL"
    assert "insufficient positive" in report.verdict_reason


def test_shadow_kills_all_survivor_zero_decision_value() -> None:
    survivor = _feature("SURVIVOR", ("sample-a",), mz=800.0)

    report = compare_early_loser_shadow_with_final_authority(
        _proposal((survivor,)),
        _matrix((survivor,)),
    )

    assert report.verdict == "KILL"
    assert "all-survivor" in report.verdict_reason


def test_shadow_preserves_owner_unresolved_state() -> None:
    unresolved = replace(
        _feature("UNRESOLVED", ("sample-u",), mz=800.0),
        review_only=True,
    )

    proposal = _proposal((unresolved,))
    report = compare_early_loser_shadow_with_final_authority(
        proposal,
        _matrix((unresolved,)),
    )

    assert proposal.rows[0].proposal_status == "unresolved"
    assert report.proposed_unresolved_count == 1
    assert report.verdict == "REDESIGN"


def test_shadow_is_no_write_and_renders_complete_expected_diff(tmp_path) -> None:
    loser, middle, winner = _proposal_features()
    source = (loser, middle, winner)
    source_snapshot = tuple(source)
    proposal = _proposal(source)
    final = _matrix(
        (
            _loser(loser, winner="GROUP-MIDDLE"),
            _loser(middle, winner="GROUP-WINNER"),
            _winner(winner),
        )
    )
    final_snapshot = replace(final)

    report = compare_early_loser_shadow_with_final_authority(proposal, final)
    output = write_early_loser_shadow_json(tmp_path / "shadow.json", report)
    rendered = json.loads(output.read_text(encoding="utf-8"))

    assert source == source_snapshot
    assert final == final_snapshot
    assert rendered["schema_version"] == EARLY_LOSER_SHADOW_SCHEMA_VERSION
    assert rendered["authority"].startswith("diagnostic_projection_only")
    assert rendered["activation_allowed"] is False
    assert rendered["run_id"] == "test-early-loser-shadow"
    assert rendered["backfill_scope"] == "full-audit"
    assert len(rendered["source_owner_snapshot_sha256"]) == 64
    assert len(rendered["alignment_config_sha256"]) == 64
    assert len(rendered["request_universe_sha256"]) == 64
    assert len(rendered["final_authority_projection_sha256"]) == 64
    assert "does_not_bind_matrix_cells_or_values" in rendered[
        "durable_binding_scope"
    ]
    canonical_rows = json.dumps(
        rendered["rows"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    assert rendered["final_authority_projection_sha256"] == hashlib.sha256(
        (canonical_rows + "\n").encode("utf-8")
    ).hexdigest()
    assert {item["surface"] for item in rendered["expected_diff_surfaces"]} == {
        "survivors_losers",
        "winner_identity",
        "selected_observations",
        "missing_extra_cells",
        "status_reason",
        "areas",
        "matrix_identity",
        "matrix_value",
        "review_population",
        "rollback",
    }


def test_shadow_bindings_change_with_each_bound_input() -> None:
    loser, middle, winner = _proposal_features()
    features = (loser, middle, winner)
    baseline = _proposal(features)
    source_mutation = _proposal(
        (replace(loser, evidence="mutated-owner-evidence"), middle, winner)
    )
    sample_order = ("sample-a", "sample-b", "sample-c", "sample-d")
    config_mutation = build_early_loser_shadow_proposal(
        features,
        run_id="test-early-loser-shadow",
        sample_order=sample_order,
        raw_sample_stems=frozenset(sample_order),
        alignment_config=AlignmentConfig(
            owner_backfill_min_detected_samples=1,
            preferred_ppm=19.0,
        ),
    )
    extended_order = (*sample_order, "sample-extra")
    request_mutation = build_early_loser_shadow_proposal(
        features,
        run_id="test-early-loser-shadow",
        sample_order=extended_order,
        raw_sample_stems=frozenset(extended_order),
        alignment_config=AlignmentConfig(owner_backfill_min_detected_samples=1),
    )

    assert (
        source_mutation.source_owner_snapshot_sha256
        != baseline.source_owner_snapshot_sha256
    )
    assert config_mutation.alignment_config_sha256 != baseline.alignment_config_sha256
    assert request_mutation.request_universe_sha256 != baseline.request_universe_sha256

    first_final = _matrix(
        (
            _loser(loser, winner="GROUP-MIDDLE"),
            _loser(middle, winner="GROUP-WINNER"),
            _winner(winner),
        )
    )
    changed_final = _matrix(
        (
            _loser(loser, winner="GROUP-WINNER"),
            _loser(middle, winner="GROUP-WINNER"),
            _winner(winner),
        )
    )
    first_report = compare_early_loser_shadow_with_final_authority(
        baseline,
        first_final,
    )
    changed_report = compare_early_loser_shadow_with_final_authority(
        baseline,
        changed_final,
    )

    assert (
        first_report.final_authority_projection_sha256
        != changed_report.final_authority_projection_sha256
    )


def _proposal(
    features: tuple[OwnerAlignedFeature, ...],
):
    sample_order = tuple(
        sorted({owner.sample_stem for feature in features for owner in feature.owners})
    )
    return build_early_loser_shadow_proposal(
        features,
        run_id="test-early-loser-shadow",
        sample_order=sample_order,
        raw_sample_stems=frozenset(sample_order),
        alignment_config=AlignmentConfig(owner_backfill_min_detected_samples=1),
    )


def _proposal_features() -> tuple[OwnerAlignedFeature, ...]:
    return (
        _feature("LOSER", ("sample-a",), mz=500.0000, rt=8.00),
        _feature(
            "MIDDLE",
            ("sample-b", "sample-c"),
            mz=500.0005,
            rt=8.02,
        ),
        _feature("WINNER", ("sample-d",), mz=700.0, rt=12.0),
    )


def _matrix(features: tuple[OwnerAlignedFeature, ...]) -> AlignmentMatrix:
    sample_order = tuple(
        sorted({owner.sample_stem for feature in features for owner in feature.owners})
    )
    return AlignmentMatrix(clusters=features, cells=(), sample_order=sample_order)


def _loser(feature: OwnerAlignedFeature, *, winner: str) -> OwnerAlignedFeature:
    return replace(
        feature,
        review_only=True,
        consolidation_state="primary_loser",
        consolidation_winner_group_hypothesis_id=winner,
        consolidation_source_group_hypothesis_id=feature.group_hypothesis_id,
    )


def _winner(feature: OwnerAlignedFeature) -> OwnerAlignedFeature:
    return replace(
        feature,
        consolidation_state="primary_winner",
        consolidation_winner_group_hypothesis_id=feature.group_hypothesis_id,
        consolidation_source_group_hypothesis_id=feature.group_hypothesis_id,
    )


def _feature(
    suffix: str,
    sample_stems: tuple[str, ...],
    *,
    mz: float,
    rt: float = 8.0,
) -> OwnerAlignedFeature:
    return OwnerAlignedFeature(
        feature_family_id=f"FAM-{suffix}",
        neutral_loss_tag="NL116",
        family_center_mz=mz,
        family_center_rt=rt,
        family_product_mz=384.0,
        family_observed_neutral_loss_da=116.0,
        has_anchor=True,
        owners=tuple(
            _owner(suffix, sample_stem, mz=mz, rt=rt)
            for sample_stem in sample_stems
        ),
        evidence="single_sample_local_owner",
        group_hypothesis_id=f"GROUP-{suffix}",
        public_family_id=f"FAM-{suffix}",
    )


def _owner(
    suffix: str,
    sample_stem: str,
    *,
    mz: float,
    rt: float,
) -> SampleLocalMS1Owner:
    event = IdentityEvent(
        candidate_id=f"{sample_stem}#{suffix}",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        neutral_loss_tag="NL116",
        precursor_mz=mz,
        product_mz=384.0,
        observed_neutral_loss_da=116.0,
        seed_rt=rt,
        evidence_score=80,
        seed_event_count=2,
    )
    return SampleLocalMS1Owner(
        owner_id=f"OWNER-{sample_stem}-{suffix}",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        precursor_mz=mz,
        owner_apex_rt=rt,
        owner_peak_start_rt=rt - 0.05,
        owner_peak_end_rt=rt + 0.05,
        owner_area=100.0,
        owner_height=10.0,
        primary_identity_event=event,
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="test_owner",
    )
