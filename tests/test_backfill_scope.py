from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

from xic_extractor.alignment.backfill_scope import (
    PREDICATE_VERSION,
    SKIPPED_EVIDENCE_LEDGER_COLUMNS,
    backfill_features_for_sample,
    backfill_request_sample_stems,
    read_family_allowlist_tsv,
    select_backfill_features,
    write_skipped_evidence_ledger_tsv,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupMemberSubject,
    mint_cross_sample_group_identity,
)
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)

_REQUEST_ID = typed_identity_digest(
    "xic.processing-request.v1:",
    canonical_identity_basis_bytes({"schema_version": "backfill-scope-test"}),
)


def test_production_equivalent_skips_only_isolated_single_detected_family() -> None:
    result = select_backfill_features(
        (_feature("FAM001"),),
        sample_order=("S1", "S2", "S3"),
        raw_sample_stems=frozenset({"S1", "S2", "S3"}),
        alignment_config=AlignmentConfig(),
        scope="production-equivalent",
    )

    assert result.features == ()
    assert [row.sample_stem for row in result.skipped] == ["S2", "S3"]
    assert {row.skip_reason for row in result.skipped} == {
        "single_detected_no_consolidation_candidate"
    }
    assert result.skipped[0].predicate_version == PREDICATE_VERSION


def test_production_equivalent_keeps_single_detected_consolidation_candidate() -> None:
    left = _feature("FAM001", owners=("S1",), rt=8.50)
    right = _feature("FAM002", owners=("S2",), rt=8.52)

    result = select_backfill_features(
        (left, right),
        sample_order=("S1", "S2", "S3"),
        raw_sample_stems=frozenset({"S1", "S2", "S3"}),
        alignment_config=AlignmentConfig(),
        scope="production-equivalent",
    )

    assert result.features == (left, right)
    assert result.skipped == ()


def test_production_equivalent_keeps_confirmed_single_detected_family() -> None:
    feature = _feature("FAM_CONFIRM", owners=("S1",), confirm=True, owner_area=1.0)

    result = select_backfill_features(
        (feature,),
        sample_order=("S1", "S2", "S3"),
        raw_sample_stems=frozenset({"S1", "S2", "S3"}),
        alignment_config=AlignmentConfig(),
        scope="production-equivalent",
    )

    assert result.features == (feature,)
    assert result.skipped == ()


def test_selected_families_is_diagnostic_and_uses_allowlist() -> None:
    kept = _feature("FAM_KEEP", owners=("S1", "S2"))
    skipped = _feature("FAM_SKIP", owners=("S1", "S2"))

    result = select_backfill_features(
        (kept, skipped),
        sample_order=("S1", "S2", "S3"),
        raw_sample_stems=frozenset({"S1", "S2", "S3"}),
        alignment_config=AlignmentConfig(),
        scope="selected-families",
        selected_family_ids=frozenset({"FAM_KEEP"}),
    )

    assert result.features == (kept,)
    assert {row.feature_family_id for row in result.skipped} == {"FAM_SKIP"}
    assert {row.skip_reason for row in result.skipped} == {
        "not_in_selected_family_allowlist"
    }


def test_full_audit_preserves_feature_tuple_and_emits_no_ledger() -> None:
    feature = _feature("FAM001", review_only=True)

    result = select_backfill_features(
        (feature,),
        sample_order=("S1", "S2"),
        raw_sample_stems=frozenset({"S1", "S2"}),
        alignment_config=AlignmentConfig(),
        scope="full-audit",
    )

    assert result.features == (feature,)
    assert result.skipped == ()


def test_backfill_request_sample_stems_matches_legacy_order_for_full_audit() -> None:
    feature = _feature("FAM001", owners=("S1",), confirm=False)

    samples = backfill_request_sample_stems(
        feature,
        sample_order=("S1", "S2", "S3"),
        raw_sample_stems=frozenset({"S1", "S2", "S3"}),
        alignment_config=AlignmentConfig(),
    )

    assert samples == ("S2", "S3")


def test_backfill_features_for_sample_matches_request_predicate() -> None:
    feature_a = _feature("FAM_A", owners=("S1",))
    feature_b = _feature("FAM_B", owners=("S2",))

    assert tuple(
        feature.feature_family_id
        for feature in backfill_features_for_sample(
            (feature_a, feature_b),
            sample_stem="S1",
            sample_order=("S1", "S2"),
            raw_sample_stems=frozenset({"S1", "S2"}),
            alignment_config=AlignmentConfig(),
        )
    ) == ("FAM_B",)


def test_allowlist_reader_and_ledger_writer(tmp_path: Path) -> None:
    allowlist = tmp_path / "families.tsv"
    allowlist.write_text("feature_family_id\nFAM001\nFAM002\n", encoding="utf-8")
    assert read_family_allowlist_tsv(
        allowlist,
        family_id_column="feature_family_id",
    ) == frozenset({"FAM001", "FAM002"})

    result = select_backfill_features(
        (_feature("FAM001"),),
        sample_order=("S1", "S2"),
        raw_sample_stems=frozenset({"S1", "S2"}),
        alignment_config=AlignmentConfig(),
        scope="production-equivalent",
    )
    path = write_skipped_evidence_ledger_tsv(
        tmp_path / "skipped_evidence_ledger.tsv",
        (row for row in result.skipped),
    )
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert b"\n" in raw
    with path.open(newline="", encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        assert header == list(SKIPPED_EVIDENCE_LEDGER_COLUMNS)
        handle.seek(0)
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert rows[0]["feature_family_id"] == "FAM001"
    assert rows[0]["family_center_mz"] == "500.0"
    assert rows[0]["predicate_version"] == PREDICATE_VERSION
    assert rows[0]["raw_xic_requests_skipped"] == "1"
    assert rows[0]["would_emit_in_full_audit"] == str(
        result.skipped[0].would_emit_in_full_audit
    )


def _feature(
    family_id: str,
    *,
    owners: tuple[str, ...] = ("S1",),
    mz: float = 500.0,
    rt: float = 8.5,
    product_mz: float = 384.0,
    review_only: bool = False,
    confirm: bool = False,
    owner_area: float = 100.0,
) -> SimpleNamespace:
    members = tuple(_member(family_id, sample) for sample in owners)
    identity = mint_cross_sample_group_identity(
        processing_request_id=_REQUEST_ID,
        member_subjects=members,
    )
    return SimpleNamespace(
        feature_family_id=family_id,
        neutral_loss_tag="DNA_dR",
        family_center_mz=mz,
        family_center_rt=rt,
        family_product_mz=product_mz,
        family_observed_neutral_loss_da=116.0,
        review_only=review_only,
        confirm_local_owners_with_backfill=confirm,
        backfill_seed_centers=(),
        owners=tuple(
            SimpleNamespace(
                sample_stem=sample,
                owner_area=owner_area,
                cross_sample_member_subject=member,
            )
            for sample, member in zip(owners, members, strict=True)
        ),
        group_hypothesis_id=identity.group_hypothesis_id,
        processing_request_id=identity.processing_request_id,
        member_subjects=identity.member_subjects,
        identity_basis_bytes=identity.basis_bytes,
        disposition=identity.disposition,
        cross_sample_state="active",
    )


def _member(family_id: str, sample: str) -> CrossSampleGroupMemberSubject:
    sample_id = f"xic.sample.v1:backfill-scope-{sample}"
    subject_id = f"xic.peak-observation-content.v1:backfill-scope-{family_id}-{sample}"
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": _REQUEST_ID,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": "physical_observation", "id": subject_id},
        }
    )
    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest("xic.group-member-subject.v1:", basis),
        processing_request_id=_REQUEST_ID,
        canonical_sample_identity_id=sample_id,
        subject_kind="physical_observation",
        subject_id=subject_id,
        physical_observation_ids=(subject_id,),
        basis_bytes=basis,
    )
