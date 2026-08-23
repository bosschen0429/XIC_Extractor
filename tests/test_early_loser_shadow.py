from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupMemberSubject,
    mint_cross_sample_group_identity,
)
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
from xic_extractor.alignment.pre_backfill_consolidation import (
    consolidate_pre_backfill_identity_families,
)
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)

_TEST_PROCESSING_REQUEST_ID = typed_identity_digest(
    "xic.processing-request.v1:",
    canonical_identity_basis_bytes({"schema_version": "early-loser-shadow-test"}),
)


def test_shadow_accepts_canonical_pre_backfill_successor_lineage() -> None:
    loser, middle, winner = _proposal_features()
    proposal = _proposal((loser, middle, winner))
    final = _pre_backfill_matrix((loser, middle, winner))

    report = compare_early_loser_shadow_with_final_authority(proposal, final)
    rows = {row.source_group_hypothesis_id: row for row in report.rows}
    successor_id = rows[
        loser.group_hypothesis_id
    ].proposal_terminal_winner_group_hypothesis_id

    assert report.verdict == "GO"
    assert report.activation_allowed is False
    assert report.confirmed_loser_count == 1
    assert report.late_loser_count == 0
    assert rows[loser.group_hypothesis_id].proposal_lineage == (
        loser.group_hypothesis_id,
        successor_id,
    )
    assert rows[loser.group_hypothesis_id].final_lineage == (
        loser.group_hypothesis_id,
        successor_id,
    )
    assert rows[loser.group_hypothesis_id].comparison_status == "confirmed_loser"
    assert rows[middle.group_hypothesis_id].comparison_status == "retained_survivor"
    assert successor_id.startswith("xic.cross-sample-group.v1:")
    assert successor_id not in {loser.group_hypothesis_id, middle.group_hypothesis_id}
    assert report.baseline_workload.requested_feature_count == 3
    assert report.proposed_workload.requested_feature_count == 2
    assert report.projected_feature_reduction == 1
    assert report.actual_savings_claim.startswith("unavailable:")
    assert all(
        surface.observed_shadow_diff_count == 0
        for surface in report.expected_diff_surfaces
    )


def test_shadow_is_order_deterministic() -> None:
    loser, middle, winner = _proposal_features()
    final = _pre_backfill_matrix((loser, middle, winner))

    forward = compare_early_loser_shadow_with_final_authority(
        _proposal((loser, middle, winner)),
        final,
    )
    reversed_report = compare_early_loser_shadow_with_final_authority(
        _proposal((winner, middle, loser)),
        final,
    )

    assert forward == reversed_report


def test_shadow_rejects_final_without_canonical_successor_lineage() -> None:
    loser, middle, winner = _proposal_features()
    proposal = _proposal((loser, middle, winner))
    final = _matrix((loser, middle, winner))

    with pytest.raises(ValueError, match="missing source group"):
        compare_early_loser_shadow_with_final_authority(proposal, final)


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


def test_shadow_excludes_ambiguous_render_only_source_from_identity_lookup() -> None:
    ambiguous = replace(
        _feature("AMBIGUOUS", ("sample-u",), mz=800.0),
        group_hypothesis_id="",
        processing_request_id="",
        member_subjects=(),
        identity_basis_bytes=b"",
        cross_sample_state="render_only",
        review_only=True,
        identity_conflict=True,
    )

    proposal = _proposal((ambiguous,))
    report = compare_early_loser_shadow_with_final_authority(
        proposal,
        _matrix(()),
    )

    assert proposal.rows[0].source_group_hypothesis_id == "FAM-AMBIGUOUS"
    assert proposal.rows[0].proposal_status == "unresolved"
    assert report.rows[0].comparison_status == "unresolved"
    assert report.rows[0].final_lineage == ()


def test_shadow_is_no_write_and_renders_complete_expected_diff(tmp_path) -> None:
    loser, middle, winner = _proposal_features()
    source = (loser, middle, winner)
    source_snapshot = tuple(source)
    proposal = _proposal(source)
    final = _pre_backfill_matrix(source)
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

    first_final = _pre_backfill_matrix(features)
    changed_final = _matrix((loser, middle, winner))
    first_report = compare_early_loser_shadow_with_final_authority(
        baseline,
        first_final,
    )
    with pytest.raises(ValueError, match="missing source group"):
        compare_early_loser_shadow_with_final_authority(baseline, changed_final)
    assert len(first_report.final_authority_projection_sha256) == 64


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


def _pre_backfill_matrix(
    features: tuple[OwnerAlignedFeature, ...],
) -> AlignmentMatrix:
    return _matrix(
        consolidate_pre_backfill_identity_families(
            features,
            config=AlignmentConfig(owner_backfill_min_detected_samples=1),
        )
    )


def _feature(
    suffix: str,
    sample_stems: tuple[str, ...],
    *,
    mz: float,
    rt: float = 8.0,
) -> OwnerAlignedFeature:
    identity = _identity(suffix, sample_stems)
    return OwnerAlignedFeature(
        feature_family_id=f"FAM-{suffix}",
        neutral_loss_tag="NL116",
        family_center_mz=mz,
        family_center_rt=rt,
        family_product_mz=384.0,
        family_observed_neutral_loss_da=116.0,
        has_anchor=True,
        owners=tuple(
            _owner(suffix, sample_stem, member=member, mz=mz, rt=rt)
            for sample_stem, member in zip(
                sample_stems,
                identity.member_subjects,
                strict=True,
            )
        ),
        evidence="single_sample_local_owner",
        group_hypothesis_id=identity.group_hypothesis_id,
        public_family_id=f"FAM-{suffix}",
        processing_request_id=identity.processing_request_id,
        member_subjects=identity.member_subjects,
        identity_basis_bytes=identity.basis_bytes,
        disposition=identity.disposition,
    )


def _identity(suffix: str, sample_stems: tuple[str, ...]):
    return mint_cross_sample_group_identity(
        processing_request_id=_TEST_PROCESSING_REQUEST_ID,
        member_subjects=tuple(
            _member(suffix, sample_stem) for sample_stem in sample_stems
        ),
    )


def _member(
    suffix: str,
    sample_stem: str,
) -> CrossSampleGroupMemberSubject:
    subject_id = f"xic.peak-observation-content.v1:early-shadow-{suffix}-{sample_stem}"
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": _TEST_PROCESSING_REQUEST_ID,
            "canonical_sample_identity_id": (
                f"xic.sample.v1:early-shadow-{sample_stem}"
            ),
            "subject": {"kind": "physical_observation", "id": subject_id},
        }
    )
    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest("xic.group-member-subject.v1:", basis),
        processing_request_id=_TEST_PROCESSING_REQUEST_ID,
        canonical_sample_identity_id=f"xic.sample.v1:early-shadow-{sample_stem}",
        subject_kind="physical_observation",
        subject_id=subject_id,
        physical_observation_ids=(subject_id,),
        basis_bytes=basis,
    )


def _owner(
    suffix: str,
    sample_stem: str,
    *,
    member: CrossSampleGroupMemberSubject,
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
        cross_sample_member_subject=member,
    )
