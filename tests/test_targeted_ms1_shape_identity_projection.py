from __future__ import annotations

from pathlib import Path

import pytest

from xic_extractor.extraction.targeted_ms1_shape_identity_projection import (
    TargetedMs1ShapeIdentitySupport,
    load_targeted_ms1_shape_identity_supports,
    targeted_ms1_shape_identity_supports_from_rows,
)
from xic_extractor.output.targeted_product_model import (
    OWN_MAX_SAME_PEAK_SUPPORT_REASON,
)


def test_support_rows_accept_supported_inside_window() -> None:
    supports = targeted_ms1_shape_identity_supports_from_rows(
        [
            _row(
                sample_name="TumorBC2294_DNA",
                target_name="5-hmdC",
                supported="TRUE",
                target_window_status="candidate_inside_target_window",
            )
        ]
    )

    assert supports == (
        TargetedMs1ShapeIdentitySupport(
            sample_name="TumorBC2294_DNA",
            target_name="5-hmdC",
        ),
    )


def test_support_rows_fail_closed_for_non_support() -> None:
    rows = [
        _row(
            sample_name="missing-token",
            target_name="5-hmdC",
            support_reason="",
        ),
        _row(
            sample_name="outside-window",
            target_name="5-hmdC",
            target_window_status="candidate_outside_target_window",
        ),
        _row(
            sample_name="not-supported",
            target_name="5-hmdC",
            supported="FALSE",
            same_peak_status="own_max_same_peak_not_supported",
        ),
        _row(
            sample_name="strong-competing",
            target_name="5-hmdC",
            competing_peak_status="strong_competing_peak_observed_diagnostic",
        ),
    ]

    assert targeted_ms1_shape_identity_supports_from_rows(rows) == ()


def test_shape_identity_support_loader_requires_evidence_bearing_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "targeted_ms1_shape_identity.tsv"
    minimal_columns = (
        "schema_version",
        "validation_label",
        "decision_authority",
        "sample_name",
        "target_name",
        "target_role",
        "target_window_status",
        "own_max_same_peak_status",
        "own_max_same_peak_supported",
        "own_max_same_peak_support_reason",
    )
    path.write_text(
        "\t".join(minimal_columns)
        + "\n"
        + "\t".join(_row()[column] for column in minimal_columns)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required columns"):
        load_targeted_ms1_shape_identity_supports(path)


def test_shape_identity_support_loader_rejects_duplicate_supported_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "targeted_ms1_shape_identity.tsv"
    path.write_text(
        "\t".join(_row().keys())
        + "\n"
        + "\t".join(_row(sample_name="S1", target_name="5-hmdC").values())
        + "\n"
        + "\t".join(_row(sample_name="S1", target_name="5-hmdC").values())
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate targeted MS1 shape identity"):
        load_targeted_ms1_shape_identity_supports(path)


def test_limited_default_policy_accepts_hmdc_and_medc_supports() -> None:
    supports = targeted_ms1_shape_identity_supports_from_rows(
        [
            _row(sample_name="S1", target_name="5-hmdC"),
            _row(sample_name="S2", target_name="5-medC"),
        ],
        activation_policy="limited_5hmdc_5medc",
    )

    assert [(support.sample_name, support.target_name) for support in supports] == [
        ("S1", "5-hmdC"),
        ("S2", "5-medC"),
    ]


def test_limited_default_policy_rejects_supported_rows_outside_scope() -> None:
    with pytest.raises(ValueError, match="outside limited_5hmdc_5medc scope"):
        targeted_ms1_shape_identity_supports_from_rows(
            [_row(sample_name="S1", target_name="5-fC")],
            activation_policy="limited_5hmdc_5medc",
        )


def test_shape_identity_support_loader_rejects_unknown_activation_policy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "targeted_ms1_shape_identity.tsv"
    path.write_text(
        "\t".join(_row().keys())
        + "\n"
        + "\t".join(_row(sample_name="S1", target_name="5-hmdC").values())
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported targeted MS1 shape identity"):
        load_targeted_ms1_shape_identity_supports(
            path,
            activation_policy="broad_default",
        )


def _row(
    *,
    sample_name: str = "S1",
    target_name: str = "5-hmdC",
    supported: str = "TRUE",
    same_peak_status: str = "own_max_same_peak_supported",
    target_window_status: str = "candidate_inside_target_window",
    support_reason: str = OWN_MAX_SAME_PEAK_SUPPORT_REASON,
    competing_peak_status: str = "no_competing_peak_observed",
) -> dict[str, str]:
    return {
        "schema_version": "targeted_ms1_shape_identity",
        "validation_label": "diagnostic_only",
        "decision_authority": "diagnostic_only_no_product_write",
        "sample_name": sample_name,
        "target_name": target_name,
        "target_role": "analyte",
        "paired_istd": "d3-5-hmdC",
        "source_row_id": f"{sample_name}|{target_name}",
        "candidate_state": "NL_FAIL",
        "reference_source": "representative_counted_reference:Ref|detected_clean",
        "candidate_rt_min": "9.12",
        "reference_rt_min": "9.11",
        "candidate_anchor_rt_delta_min": "0.01",
        "paired_istd_rt_min": "9.03",
        "candidate_pair_rt_delta_min": "0.09",
        "target_window_status": target_window_status,
        "own_max_same_peak_status": same_peak_status,
        "own_max_same_peak_supported": supported,
        "own_max_same_peak_support_reason": support_reason,
        "own_max_same_peak_similarity": "0.95",
        "own_max_compared_point_count": "165",
        "strongest_peak_rt_min": "9.12",
        "strongest_peak_own_max_ratio": "1",
        "strongest_competing_peak_rt_min": "",
        "strongest_competing_peak_own_max_ratio": "",
        "competing_peak_status": competing_peak_status,
        "reason": (
            "diagnostic_only;candidate_inside_target_window;"
            "own_max_same_peak_supported;no_competing_peak_observed"
        ),
    }
