from dataclasses import dataclass

from xic_extractor.extraction.targeted_product_projection import (
    build_targeted_product_projection,
    paired_istd_identity_samples,
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


_COHERENT = ("ms1_peak_present", "ms1_coherent", "trace_coherent")


def _analyte(
    *,
    cid_nl: bool,
    identity: bool = False,
    review=(),
    support=_COHERENT,
    backfill=False,
) -> object:
    return build_targeted_product_projection(
        TargetedProductContext(role="Analyte", neutral_loss_required=True),
        rt=9.1,
        area=2.0e5,
        confidence="LOW",
        nl_status="OK" if cid_nl else "NL_FAIL",
        cid_nl_supported=cid_nl,
        paired_istd_identity=identity,
        backfill_supported=backfill,
        support_reasons=support,
        review_reasons=("plausible_nl_dropout_review", *review),
    )


def _istd(*, cid_nl: bool = True, backfill: bool = False) -> object:
    return build_targeted_product_projection(
        TargetedProductContext(
            role="ISTD", expected_present=True, neutral_loss_required=True
        ),
        rt=9.0,
        area=5.0e6,
        confidence="HIGH",
        nl_status="OK" if cid_nl else "NL_FAIL",
        cid_nl_supported=cid_nl,
        backfill_supported=backfill,
        support_reasons=_COHERENT,
    )


def test_paired_istd_identity_counts_an_analyte_without_its_own_nl() -> None:
    assert _analyte(cid_nl=False).counted_detection is False
    projection = _analyte(cid_nl=False, identity=True)
    assert (projection.counted_detection, projection.product_state) == (
        True,
        "detected_clean",
    )
    assert "paired_istd_identity" in projection.support_reasons
    # Identity resolves missing NL only; a shape concern still asks for review.
    flagged = _analyte(cid_nl=False, identity=True, review=("trace_morphology_review",))
    assert (flagged.counted_detection, flagged.review_reasons) == (
        True,
        ("trace_morphology_review",),
    )


@dataclass(frozen=True)
class _Result:
    reported_rt: float | None
    targeted_product_projection: object


def test_identity_needs_a_counted_istd_at_the_batch_offset() -> None:
    confirmed = {"C1": 0.10, "C2": 0.12, "C3": 0.08}
    analyte = {s: _Result(9.0 + d, _analyte(cid_nl=True)) for s, d in confirmed.items()}
    istd = {s: _Result(9.0, _istd()) for s in confirmed}
    for sample, offset, pair in (
        ("near", 0.15, _istd()),
        ("backfilled_istd", 0.11, _istd(cid_nl=False, backfill=True)),
        ("uncounted_istd", 0.10, _istd(cid_nl=False)),
        ("off_offset", 0.50, _istd()),
    ):
        analyte[sample] = _Result(9.0 + offset, _analyte(cid_nl=False))
        istd[sample] = _Result(9.0, pair)
    # Backfill relocated this peak after the first capture missed it; its
    # acceptance already qualified the recovered peak's shape.
    analyte["relocated"] = _Result(
        9.11, _analyte(cid_nl=False, support=("ms1_peak_present",), backfill=True)
    )
    analyte["incoherent"] = _Result(9.11, _analyte(cid_nl=False, support=()))
    istd["relocated"] = istd["incoherent"] = _Result(9.0, _istd())

    assert paired_istd_identity_samples(analyte, istd) == {
        "near",
        "backfilled_istd",
        "relocated",
    }
    # One confirmed sample gives no offset to agree with.
    lone = {s: r for s, r in analyte.items() if s in {"C1", "near"}}
    assert paired_istd_identity_samples(lone, istd) == frozenset()
