from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from xic_extractor.alignment.backfill_scope import (
    PREDICATE_VERSION,
    backfill_features_for_sample,
    backfill_request_sample_stems,
    read_family_allowlist_tsv,
    select_backfill_features,
)
from xic_extractor.alignment.config import AlignmentConfig


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


def test_allowlist_reader(tmp_path: Path) -> None:
    allowlist = tmp_path / "families.tsv"
    allowlist.write_text("feature_family_id\nFAM001\nFAM002\n", encoding="utf-8")
    assert read_family_allowlist_tsv(
        allowlist,
        family_id_column="feature_family_id",
    ) == frozenset({"FAM001", "FAM002"})



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
            SimpleNamespace(sample_stem=sample, owner_area=owner_area)
            for sample in owners
        ),
    )
