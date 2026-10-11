from xic_extractor.extraction.targeted_product_projection import (
    build_targeted_product_projection,
)
from xic_extractor.output.targeted_product_model import (
    TargetedProductContext,
)


def test_projection_counts_istd_dropout_without_legacy_nl_authority() -> None:
    projection = build_targeted_product_projection(
        TargetedProductContext(role="ISTD", expected_present=True),
        rt=11.9888,
        area=1.55e7,
        confidence="VERY_LOW",
        nl_status="NL_FAIL",
        support_reasons=("ms1_peak_present", "trace_coherent"),
        review_reasons=("plausible_dda_nl_dropout",),
    )

    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert projection.review_state == "flagged"
    assert "plausible_dda_nl_dropout" in projection.review_reasons
    assert projection.legacy_evidence["confidence"] == "VERY_LOW"
    assert projection.legacy_evidence["nl_status"] == "NL_FAIL"
    assert projection.legacy_authority_status == "evidence_only"


def test_same_legacy_tokens_can_project_to_different_product_counts() -> None:
    shared = {
        "rt": 11.9888,
        "area": 1.55e7,
        "confidence": "VERY_LOW",
        "nl_status": "NL_FAIL",
        "support_reasons": ("ms1_peak_present", "trace_coherent"),
        "review_reasons": ("plausible_dda_nl_dropout",),
    }

    istd_projection = build_targeted_product_projection(
        TargetedProductContext(role="ISTD", expected_present=True),
        **shared,
    )
    analyte_projection = build_targeted_product_projection(
        TargetedProductContext(role="Analyte", expected_present=False),
        **shared,
        not_counted_reasons=("analyte_nl_fail_requires_policy",),
    )

    assert istd_projection.counted_detection is True
    assert istd_projection.product_state == "detected_flagged"
    assert analyte_projection.counted_detection is False
    assert analyte_projection.product_state == "not_counted"
    assert "analyte_nl_fail_requires_policy" in analyte_projection.not_counted_reasons


def test_projection_blocks_when_ms1_peak_is_not_positive() -> None:
    projection = build_targeted_product_projection(
        TargetedProductContext(role="ISTD", expected_present=True),
        rt=11.9888,
        area=0.0,
        confidence="HIGH",
        nl_status="OK",
        support_reasons=("ms1_peak_present",),
    )

    assert projection.counted_detection is False
    assert projection.product_state == "not_counted"
    assert "missing_positive_ms1_peak" in projection.not_counted_reasons


def test_expected_istd_without_required_nl_is_not_confirmed() -> None:
    projection = build_targeted_product_projection(
        TargetedProductContext(
            role="ISTD", expected_present=True, neutral_loss_required=True
        ),
        rt=8.5,
        area=1234.5,
        confidence="HIGH",
        nl_status="NO_MS2",
        support_reasons=("ms1_peak_present",),
        review_reasons=("plausible_dda_nl_dropout",),
    )
    assert projection.counted_detection is False
    assert "cid_nl_method_not_supported" in projection.not_counted_reasons


def test_backfill_label_alone_does_not_confirm_istd() -> None:
    projection = build_targeted_product_projection(
        TargetedProductContext(
            role="ISTD", expected_present=True, neutral_loss_required=True
        ),
        rt=8.5,
        area=1234.5,
        confidence="HIGH",
        nl_status="NO_MS2",
        review_reasons=("BACKFILL_ACCEPTED",),
    )
    assert projection.counted_detection is False


def test_backfill_does_not_override_unresolved_identity_or_missing_quantity() -> None:
    for area, conflicts in ((0.0, ()), (1234.5, ("unresolved_identity",))):
        projection = build_targeted_product_projection(
            TargetedProductContext(
                role="ISTD", expected_present=True, neutral_loss_required=True
            ),
            rt=8.5,
            area=area,
            confidence="HIGH",
            nl_status="NO_MS2",
            backfill_supported=True,
            conflict_reasons=conflicts,
        )
        assert projection.counted_detection is False
