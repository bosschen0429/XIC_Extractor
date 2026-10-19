from __future__ import annotations

from types import SimpleNamespace

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.family_compatibility import (
    loose_compatible_primary_family,
)


def test_loose_compatible_allows_product_precursor_shift() -> None:
    config = AlignmentConfig()
    left = _family("F1", mz=500.0, product_mz=384.0)
    right = _family("F2", mz=500.02, product_mz=384.02)

    assert loose_compatible_primary_family(left, right, config)


def test_loose_compatible_rejects_rt_mz_loss_and_review_only_drift() -> None:
    config = AlignmentConfig()
    base = _family("F1")

    assert not loose_compatible_primary_family(
        base,
        _family("F2", mz=base.family_center_mz + 1.0),
        config,
    )
    assert not loose_compatible_primary_family(
        base,
        _family("F2", rt=base.family_center_rt + 10.0),
        config,
    )
    assert not loose_compatible_primary_family(
        base,
        _family("F2", loss=120.0),
        config,
    )
    assert not loose_compatible_primary_family(
        base,
        _family("F2", review_only=True),
        config,
    )


def _family(
    family_id: str,
    *,
    tag: str = "DNA_dR",
    mz: float = 500.0,
    rt: float = 8.5,
    product_mz: float = 384.0,
    loss: float = 116.0,
    review_only: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        feature_family_id=family_id,
        neutral_loss_tag=tag,
        family_center_mz=mz,
        family_center_rt=rt,
        family_product_mz=product_mz,
        family_observed_neutral_loss_da=loss,
        review_only=review_only,
    )
