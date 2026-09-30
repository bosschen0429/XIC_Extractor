from __future__ import annotations

from pathlib import Path

from xic_extractor.diagnostics.shared_peak_identity_explanation import (
    machine_evidence_support,
    peak_hypothesis_selection,
)


def test_peak_hypothesis_selects_tag_backed_mode_and_blocks_outlier() -> None:
    rows = peak_hypothesis_selection.build_peak_hypothesis_selection_rows(
        rt_mode_rows=[
            _rt_mode_row(
                "FAM011810",
                "BenignfatBC1055_DNA",
                status="mode_supported",
                mode_id="irt_blue_core",
                role="tag_bearing_core",
                tag_status="tag_supported",
                family_class="tag_backed_core_with_outlier_modes",
            ),
            _rt_mode_row(
                "FAM011810",
                "TumorBC2263_DNA",
                status="mode_conflict",
                mode_id="irt_green_core",
                role="non_tag_outlier",
                tag_status="no_tag_observed",
                family_class="tag_backed_core_with_outlier_modes",
            ),
        ],
    )

    by_sample = {row["sample_stem"]: row for row in rows}
    core = by_sample["BenignfatBC1055_DNA"]
    assert core["peak_hypothesis_id"] == "FAM011810::irt_blue_core"
    assert core["peak_hypothesis_status"] == "product_candidate_core"
    assert core["product_unit_scope"] == "mode_level"
    assert core["product_selection_action"] == "select_mode_peak_hypothesis"
    assert core["product_selection_blocker"] == "none"

    outlier = by_sample["TumorBC2263_DNA"]
    assert outlier["peak_hypothesis_id"] == "FAM011810::irt_green_core"
    assert outlier["peak_hypothesis_status"] == "cross_mode_rescue_blocked"
    assert outlier["product_unit_scope"] == "sample_cell"
    assert outlier["product_selection_action"] == "block_cross_mode_rescue"
    assert outlier["product_selection_blocker"] == "cross_mode_rescue"


def test_peak_hypothesis_blocks_multimodal_family_without_unique_core() -> None:
    rows = peak_hypothesis_selection.build_peak_hypothesis_selection_rows(
        rt_mode_rows=[
            _rt_mode_row(
                "FAM011810",
                "TumorBC2263_DNA",
                status="consolidation_no_go",
                mode_id="irt_green_core",
                role="mixed_mode",
                tag_status="family_tag_absent",
                family_class="consolidation_no_go",
                tag_bearing_mode_count="0",
            ),
        ],
    )

    row = rows[0]
    assert row["peak_hypothesis_status"] == "consolidation_no_go"
    assert row["product_unit_scope"] == "candidate_container"
    assert row["product_selection_action"] == "block_family_promotion"
    assert row["product_selection_blocker"] == "consolidation_no_go"


def test_peak_hypothesis_keeps_tailing_as_review_only() -> None:
    rows = peak_hypothesis_selection.build_peak_hypothesis_selection_rows(
        rt_mode_rows=[
            _rt_mode_row(
                "FAM012114",
                "S1",
                status="tailing_confounded",
                mode_id="tailing_mode",
                role="tailing_confounded",
                tag_status="unknown",
                family_class="tailing_confounded",
            ),
        ],
    )

    row = rows[0]
    assert row["peak_hypothesis_status"] == "tailing_review_only"
    assert row["product_selection_action"] == "require_tailing_review"
    assert row["product_selection_blocker"] == "tailing_confounded"


def test_peak_hypothesis_keeps_raw_overlay_split_as_review_only() -> None:
    rows = peak_hypothesis_selection.build_peak_hypothesis_selection_rows(
        rt_mode_rows=[
            _rt_mode_row(
                "FAM019990",
                "TumorBC2312_DNA",
                status="raw_mode_review_only",
                mode_id="raw_mode_1",
                role="raw_split_review",
                tag_status="family_tag_absent",
                family_class="irt_refined_mode_split",
            ),
        ],
    )

    row = rows[0]
    assert row["peak_hypothesis_status"] == "raw_mode_review_only"
    assert row["product_selection_action"] == "require_raw_mode_review"
    assert row["product_selection_blocker"] == "raw_mode_review_only"


def test_peak_hypothesis_keeps_raw_selected_supported_mode_as_review_only() -> None:
    rows = peak_hypothesis_selection.build_peak_hypothesis_selection_rows(
        rt_mode_rows=[
            _rt_mode_row(
                "FAM001473",
                "NormalBC2312_DNA",
                status="mode_supported",
                mode_id="raw_mode_1_19.27min",
                role="tag_bearing_core",
                tag_status="tag_supported",
                family_class="tag_backed_core_with_outlier_modes",
                evidence_level="raw_selected_apex_modes",
            ),
        ],
    )

    row = rows[0]
    assert row["peak_hypothesis_status"] == "raw_mode_review_only"
    assert row["product_unit_scope"] == "review_only"
    assert row["product_selection_action"] == "require_raw_mode_review"
    assert row["product_selection_blocker"] == "raw_mode_review_only"
    assert row["reason"] == "raw_selected_apex_mode_requires_explicit_mode_hypothesis"


def test_peak_hypothesis_writer_matches_machine_support_loader(tmp_path: Path) -> None:
    output = tmp_path / "peak_hypothesis.tsv"
    rows = peak_hypothesis_selection.build_peak_hypothesis_selection_rows(
        rt_mode_rows=[
            _rt_mode_row(
                "FAM011810",
                "TumorBC2263_DNA",
                status="mode_conflict",
                mode_id="irt_green_core",
                role="non_tag_outlier",
                tag_status="no_tag_observed",
                family_class="tag_backed_core_with_outlier_modes",
            ),
        ],
    )

    peak_hypothesis_selection.write_peak_hypothesis_selection_rows(output, rows)

    loaded = machine_evidence_support.load_peak_hypothesis_selection(output)
    assert loaded[("FAM011810", "TumorBC2263_DNA")][
        "peak_hypothesis_status"
    ] == "cross_mode_rescue_blocked"


def _rt_mode_row(
    family_id: str,
    sample_stem: str,
    *,
    status: str,
    mode_id: str,
    role: str,
    tag_status: str,
    family_class: str,
    evidence_level: str = "irt_selected_apex_modes",
    family_mode_count: str = "3",
    tag_bearing_mode_count: str = "1",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "rt_mode_status": status,
        "rt_mode_evidence_level": evidence_level,
        "selected_mode_id": mode_id,
        "selected_mode_role": role,
        "selected_mode_tag_status": tag_status,
        "family_mode_class": family_class,
        "family_mode_count": family_mode_count,
        "tag_bearing_mode_count": tag_bearing_mode_count,
        "selected_mode_cell_count": "2",
        "selected_mode_sample_type_counts": "Tumor:1",
        "selected_mode_status_counts": "rescued:1",
        "raw_selected_rt": "7.93",
        "normalized_selected_rt": "7.81",
        "selected_mode_raw_rt_range_min": "0.1",
        "selected_mode_normalized_rt_range_min": "0.08",
        "family_raw_rt_range_min": "2.1",
        "family_normalized_rt_range_min": "1.9",
        "reason": "unit_test_rt_mode",
        "diagnostic_only": "TRUE",
    }
