from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from tools.diagnostics import backfill_evidence_reconciliation_gallery as cli
from xic_extractor.alignment.tsv_writer import (
    ALIGNMENT_CELLS_COLUMNS,
    ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS,
    ALIGNMENT_REVIEW_COLUMNS,
)
from xic_extractor.diagnostics.backfill_shadow_policy import (
    BACKFILL_SHADOW_POLICY_COLUMNS,
)
from xic_extractor.diagnostics.shadow_production_projection import (
    SHADOW_PRODUCTION_PROJECTION_COLUMNS,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery as gallery,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_assets as gallery_assets,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_chain_html as gallery_chain_html,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_counts as gallery_counts,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_detail_cards as gallery_detail_cards,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_detail_drawer as gallery_detail_drawer,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_evidence as gallery_evidence,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_family_pattern as gallery_family_pattern,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_filters as gallery_filters,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_html as gallery_html,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_index_builder as gallery_index_builder,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_index_fields as gallery_index_fields,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_indices as gallery_indices,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_inputs as gallery_inputs,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_models as gallery_models,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_output_rows as gallery_output_rows,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_output_writer as gallery_output_writer,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_overlay_links as gallery_overlay_links,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_page as gallery_page,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_provenance as gallery_provenance,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_ranges as gallery_ranges,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_render_context as gallery_render_context,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_review_modes as gallery_review_modes,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_search as gallery_search,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_shadow_tables as gallery_shadow_tables,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_source_context as gallery_source_context,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_state as gallery_state,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_summary as gallery_summary,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_table_rows as gallery_table_rows,
)
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_target_benchmark as gallery_target_benchmark,
)

EXPECTED_GROUP_COLUMNS = (
    "schema_version",
    "priority_rank",
    "feature_family_id",
    "seed_group_id",
    "seed_group_basis",
    "seed_mz",
    "seed_rt",
    "seed_rt_window",
    "seed_ppm",
    "tag_or_class",
    "product_behavior_state",
    "evidence_authority_state",
    "reconciliation_class",
    "detected_cell_count",
    "rescued_cell_count",
    "provisional_cell_count",
    "top_product_reason",
    "top_support_component",
    "top_blocker",
    "missing_evidence",
    "overlay_png_path",
    "overlay_trace_json_path",
    "source_artifacts",
    "source_warnings",
)


def test_gallery_static_assets_stay_out_of_reconciliation_logic() -> None:
    assert gallery._gallery_css is gallery_assets.gallery_css
    assert gallery._lightbox_html is gallery_assets.lightbox_html
    assert gallery._lightbox_script is gallery_assets.lightbox_script


def test_gallery_models_stay_out_of_reconciliation_orchestrator() -> None:
    assert gallery.ReconciliationGroup is gallery_models.ReconciliationGroup
    assert gallery.ReconciliationIndex is gallery_models.ReconciliationIndex
    assert gallery.RepresentativeCell is gallery_models.RepresentativeCell
    assert gallery.ShadowPolicyCell is gallery_models.ShadowPolicyCell
    assert gallery.ShadowProjectionCell is gallery_models.ShadowProjectionCell
    assert gallery.TargetBenchmarkContext is gallery_models.TargetBenchmarkContext
    assert gallery._ordered_unique([" a ", "a", "", None]) == ("a",)


def test_gallery_html_helpers_stay_out_of_reconciliation_orchestrator(
    tmp_path: Path,
) -> None:
    assert gallery._escape is gallery_html.escape_html
    assert gallery._badge is gallery_html.badge
    assert gallery._href_for_path("javascript:alert(1)", tmp_path / "index.html") == ""


def test_gallery_inputs_stay_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery.load_reconciliation_input_rows
        is gallery_inputs.load_reconciliation_input_rows
    )
    assert gallery._read_required_tsv is gallery_inputs._read_required_tsv
    assert (
        gallery._INPUT_ARTIFACT_LABEL_BY_KEY
        is gallery_inputs._INPUT_ARTIFACT_LABEL_BY_KEY
    )


def test_gallery_index_builder_stays_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery.build_reconciliation_index
        is gallery_index_builder.build_reconciliation_index
    )


def test_gallery_indices_stay_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery._shadow_policy_cell_from_row
        is gallery_indices._shadow_policy_cell_from_row
    )
    assert (
        gallery._shadow_projection_cells_by_group
        is gallery_indices._shadow_projection_cells_by_group
    )
    row = {
        "feature_family_id": "FAM1",
        "seed_group_id": "seed1",
        "sample_stem": "sample1",
        "shadow_policy_decision": "fill_now",
        "decision_reason": "shape supported",
    }
    assert (
        gallery._shadow_policy_cell_from_row(row).shadow_policy_decision == "fill_now"
    )


def test_gallery_render_context_stays_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery._gallery_render_context
        is gallery_render_context._gallery_render_context
    )
    assert gallery._html_scope_notice is gallery_render_context._html_scope_notice


def test_gallery_source_context_stays_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery._cells_by_family_seed_group
        is gallery_source_context._cells_by_family_seed_group
    )
    assert (
        gallery._seed_records_by_family
        is gallery_source_context._seed_records_by_family
    )
    assert (
        gallery._source_hashes_from_input_artifacts
        is gallery_source_context._source_hashes_from_input_artifacts
    )


def test_gallery_evidence_stays_out_of_reconciliation_orchestrator() -> None:
    assert gallery._classify_evidence is gallery_evidence._classify_evidence
    assert gallery._product_behavior is gallery_evidence._product_behavior
    assert gallery._reconciliation_class is gallery_evidence._reconciliation_class


def test_gallery_provenance_stays_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery._input_artifact_links
        is gallery_provenance._input_artifact_links
    )
    assert gallery._source_artifacts_html is gallery_provenance._source_artifacts_html
    assert (
        gallery._write_local_overlay_interpretation_guide
        is gallery_provenance._write_local_overlay_interpretation_guide
    )


def test_gallery_target_benchmark_stays_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery._target_benchmark_summary_text
        is gallery_target_benchmark._target_benchmark_summary_text
    )
    assert (
        gallery._target_benchmark_panel_html
        is gallery_target_benchmark._target_benchmark_panel_html
    )
    assert (
        gallery._target_benchmark_contexts_html
        is gallery_target_benchmark._target_benchmark_contexts_html
    )
    assert (
        gallery._family_target_summary_html
        is gallery_target_benchmark._family_target_summary_html
    )


def test_gallery_overlay_links_stay_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery._missing_overlay_reason_text
        is gallery_overlay_links._missing_overlay_reason_text
    )
    assert (
        gallery._family_pattern_link_html
        is gallery_overlay_links._family_pattern_link_html
    )
    assert gallery._overlay_link_html is gallery_overlay_links._overlay_link_html
    assert (
        gallery._hypothesis_overlay_link_html
        is gallery_overlay_links._hypothesis_overlay_link_html
    )
    assert (
        gallery._seed_overlay_cell_html
        is gallery_overlay_links._seed_overlay_cell_html
    )
    assert (
        gallery._shadow_policy_overlay_link_html
        is gallery_overlay_links._shadow_policy_overlay_link_html
    )
    assert (
        gallery._shadow_projection_overlay_link_html
        is gallery_overlay_links._shadow_projection_overlay_link_html
    )


def test_gallery_review_modes_stay_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery._is_cid_nl_successor_review_group
        is gallery_review_modes._is_cid_nl_successor_review_group
    )
    assert (
        gallery._is_cid_nl_differential_review_group
        is gallery_review_modes._is_cid_nl_differential_review_group
    )
    assert (
        gallery._cid_nl_transition_label
        is gallery_review_modes._cid_nl_transition_label
    )
    assert (
        gallery._is_cid_nl_successor_review_index
        is gallery_review_modes._is_cid_nl_successor_review_index
    )


def test_gallery_filters_stay_out_of_reconciliation_orchestrator() -> None:
    assert gallery._filter_html is gallery_filters._filter_html
    assert (
        gallery._default_visible_family_count
        is gallery_filters._default_visible_family_count
    )
    assert (
        gallery._family_filter_categories
        is gallery_filters._family_filter_categories
    )
    assert gallery._group_filter_categories is gallery_filters._group_filter_categories
    assert gallery._review_category_counts is gallery_filters._review_category_counts


def test_gallery_output_rows_stay_out_of_reconciliation_orchestrator() -> None:
    assert gallery.SCHEMA_VERSION == gallery_output_rows.SCHEMA_VERSION
    assert gallery._group_as_row is gallery_output_rows._group_as_row
    assert gallery._representative_as_row is gallery_output_rows._representative_as_row
    assert gallery._summary is gallery_output_rows._summary
    assert gallery._string_object_mapping is gallery_output_rows._string_object_mapping


def test_gallery_output_writer_stays_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery.write_reconciliation_outputs
        is gallery_output_writer.write_reconciliation_outputs
    )


def test_gallery_page_stays_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery.write_reconciliation_gallery_html
        is gallery_page.write_reconciliation_gallery_html
    )
    assert gallery._gallery_document_title is gallery_page._gallery_document_title
    assert gallery._gallery_hero_copy is gallery_page._gallery_hero_copy


def test_gallery_summary_stays_out_of_reconciliation_orchestrator() -> None:
    assert gallery._summary_html is gallery_summary._summary_html
    assert gallery._decision_legend_html is gallery_summary._decision_legend_html
    assert gallery._summary_item is gallery_summary._summary_item
    assert gallery._string_int_mapping is gallery_summary._string_int_mapping
    assert (
        gallery._activation_summary_text
        is gallery_summary._activation_summary_text
    )
    assert (
        gallery._current_rescue_summary_text
        is gallery_summary._current_rescue_summary_text
    )


def test_gallery_chain_html_stays_out_of_reconciliation_orchestrator() -> None:
    assert gallery._compact_issue_label is gallery_chain_html._compact_issue_label
    assert gallery._compact_product_reason is gallery_chain_html._compact_product_reason
    assert gallery._chain_item_html is gallery_chain_html._chain_item_html
    assert gallery._component_list_html is gallery_chain_html._component_list_html
    assert (
        gallery._secondary_chain_details_html
        is gallery_chain_html._secondary_chain_details_html
    )
    assert (
        gallery._component_summary_text
        is gallery_chain_html._component_summary_text
    )


def test_gallery_index_fields_stay_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery._representative_cells_for_group
        is gallery_index_fields._representative_cells_for_group
    )
    assert gallery._product_cell_state is gallery_index_fields._product_cell_state
    assert gallery._apex_delta_sec is gallery_index_fields._apex_delta_sec
    assert gallery._source_row_key is gallery_index_fields._source_row_key
    assert gallery._top_product_reason is gallery_index_fields._top_product_reason
    assert gallery._tag_or_class is gallery_index_fields._tag_or_class
    assert gallery._first_label is gallery_index_fields._first_label


def test_gallery_search_stays_out_of_reconciliation_orchestrator() -> None:
    assert gallery._search_blob is gallery_search._search_blob
    assert gallery._family_search_blob is gallery_search._family_search_blob
    assert (
        gallery._shadow_projection_search_blob
        is gallery_search._shadow_projection_search_blob
    )


def test_gallery_ranges_stay_out_of_reconciliation_orchestrator() -> None:
    assert gallery._family_seed_summary is gallery_ranges._family_seed_summary
    assert gallery._family_window_summary is gallery_ranges._family_window_summary
    assert gallery._compact_value_range is gallery_ranges._compact_value_range
    assert gallery._compact_text_values is gallery_ranges._compact_text_values
    assert gallery._seed_mz_range is gallery_ranges._seed_mz_range
    assert gallery._seed_rt_range is gallery_ranges._seed_rt_range
    assert gallery._seed_window_range is gallery_ranges._seed_window_range
    assert gallery._numeric_range_text is gallery_ranges._numeric_range_text
    assert gallery._numeric_range_start is gallery_ranges._numeric_range_start
    assert gallery._numeric_range_end is gallery_ranges._numeric_range_end
    assert gallery._parsed_numeric_values is gallery_ranges._parsed_numeric_values


def test_gallery_counts_stay_out_of_reconciliation_orchestrator() -> None:
    assert gallery._counts_html is gallery_counts._counts_html
    assert (
        gallery._cid_nl_successor_counts_html
        is gallery_counts._cid_nl_successor_counts_html
    )
    assert gallery._projection_counts_html is gallery_counts._projection_counts_html
    assert (
        gallery._projection_impact_counts_html
        is gallery_counts._projection_impact_counts_html
    )
    assert gallery._impact_counts_html is gallery_counts._impact_counts_html
    assert gallery._count_pill is gallery_counts._count_pill
    assert (
        gallery._consolidated_counts_html
        is gallery_counts._consolidated_counts_html
    )


def test_gallery_state_stays_out_of_reconciliation_orchestrator() -> None:
    assert gallery._state_html is gallery_state._state_html
    assert gallery._state_aria_label is gallery_state._state_aria_label
    assert gallery._state_html_for_shadow is gallery_state._state_html_for_shadow
    assert (
        gallery._shadow_policy_state_label
        is gallery_state._shadow_policy_state_label
    )
    assert (
        gallery._shadow_policy_chain_title
        is gallery_state._shadow_policy_chain_title
    )
    assert (
        gallery._shadow_policy_chain_subtitle
        is gallery_state._shadow_policy_chain_subtitle
    )
    assert (
        gallery._projection_matrix_state_html
        is gallery_state._projection_matrix_state_html
    )


def test_gallery_family_pattern_stays_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery._family_pattern_state_html
        is gallery_family_pattern._family_pattern_state_html
    )
    assert (
        gallery._family_pattern_status_html
        is gallery_family_pattern._family_pattern_status_html
    )
    assert (
        gallery._family_context_available
        is gallery_family_pattern._family_context_available
    )
    assert (
        gallery._family_anchor_summary_html
        is gallery_family_pattern._family_anchor_summary_html
    )
    assert (
        gallery._family_pattern_issue_html
        is gallery_family_pattern._family_pattern_issue_html
    )


def test_gallery_shadow_tables_stay_out_of_reconciliation_orchestrator() -> None:
    assert (
        gallery._shadow_policy_summary_note_html
        is gallery_shadow_tables._shadow_policy_summary_note_html
    )
    assert (
        gallery._cell_impact_legend_note_html
        is gallery_shadow_tables._cell_impact_legend_note_html
    )
    assert (
        gallery._shadow_projection_summary_note_html
        is gallery_shadow_tables._shadow_projection_summary_note_html
    )
    assert (
        gallery._shadow_policy_cells_html
        is gallery_shadow_tables._shadow_policy_cells_html
    )
    assert (
        gallery._shadow_projection_cells_html
        is gallery_shadow_tables._shadow_projection_cells_html
    )
    assert (
        gallery._shadow_projection_warnings_html
        is gallery_shadow_tables._shadow_projection_warnings_html
    )
    assert (
        gallery._shadow_projection_metric_text
        is gallery_shadow_tables._shadow_projection_metric_text
    )
    assert (
        gallery._shadow_projection_evidence_html
        is gallery_shadow_tables._shadow_projection_evidence_html
    )
    assert (
        gallery._shadow_policy_gap_html
        is gallery_shadow_tables._shadow_policy_gap_html
    )
    assert gallery._shadow_metric_text is gallery_shadow_tables._shadow_metric_text
    assert (
        gallery._shadow_policy_evidence_html
        is gallery_shadow_tables._shadow_policy_evidence_html
    )


def test_gallery_detail_cards_stay_out_of_reconciliation_orchestrator() -> None:
    assert gallery._detail_summary_html is gallery_detail_cards._detail_summary_html
    assert (
        gallery._cid_nl_review_focus_card_html
        is gallery_detail_cards._cid_nl_review_focus_card_html
    )
    assert (
        gallery._cid_nl_discovery_identity_card_html
        is gallery_detail_cards._cid_nl_discovery_identity_card_html
    )
    assert (
        gallery._detail_summary_card_html
        is gallery_detail_cards._detail_summary_card_html
    )
    assert gallery._review_answer_html is gallery_detail_cards._review_answer_html
    assert (
        gallery._review_answer_decision_text
        is gallery_detail_cards._review_answer_decision_text
    )
    assert gallery._support_summary_items is gallery_detail_cards._support_summary_items
    assert gallery._blocker_summary_items is gallery_detail_cards._blocker_summary_items
    assert (
        gallery._visual_summary_subtitle
        is gallery_detail_cards._visual_summary_subtitle
    )
    assert (
        gallery._detail_visual_summary_html
        is gallery_detail_cards._detail_visual_summary_html
    )
    assert (
        gallery._anchor_review_context_html
        is gallery_detail_cards._anchor_review_context_html
    )
    assert (
        gallery._cid_nl_identity_transition_list_html
        is gallery_detail_cards._cid_nl_identity_transition_list_html
    )
    assert (
        gallery._cid_nl_successor_decision_list_html
        is gallery_detail_cards._cid_nl_successor_decision_list_html
    )
    assert (
        gallery._summary_count_list_html
        is gallery_detail_cards._summary_count_list_html
    )
    assert (
        gallery._identity_transition_text
        is gallery_detail_cards._identity_transition_text
    )
    assert (
        gallery._overlay_evidence_notes_html
        is gallery_detail_cards._overlay_evidence_notes_html
    )
    assert (
        gallery._representative_cells_table_html
        is gallery_detail_cards._representative_cells_table_html
    )


def test_gallery_detail_drawer_stays_out_of_reconciliation_orchestrator() -> None:
    assert gallery._details_html is gallery_detail_drawer._details_html


def test_gallery_table_rows_stay_out_of_reconciliation_orchestrator() -> None:
    table_row_aliases = (
        "_table_html",
        "_family_groups",
        "_family_sort_key",
        "_family_tag_html",
        "_family_detail_summary",
        "_top_issue_html",
        "_family_table_row",
        "_consolidated_seed_alias_rows",
        "_consolidated_seed_alias_family",
        "_seed_alias_count_label",
        "_representatives_for_groups",
        "_consolidated_overlay_cell_html",
        "_consolidated_seed_alias_details_html",
        "_consolidated_review_answer_html",
        "_consolidated_overlay_readout",
        "_projection_accept_cells_html",
        "_projection_accept_seed_hint_html",
        "_seed_alias_table_html",
        "_seed_decision_rows",
        "_detail_row_id",
        "_seed_table_row_html",
        "_family_details_html",
        "_seed_issue_text",
        "_seed_detail_summary",
        "_HIGH_SEED_ALIAS_COUNT",
    )
    for name in table_row_aliases:
        assert getattr(gallery, name) is getattr(gallery_table_rows, name)


EXPECTED_REPRESENTATIVE_CELL_COLUMNS = (
    "schema_version",
    "feature_family_id",
    "seed_group_id",
    "representative_roles",
    "sample_stem",
    "cell_status",
    "product_cell_state",
    "shape_similarity",
    "scan_support_score",
    "apex_delta_sec",
    "boundary_overlap",
    "interference_signal",
    "representative_reason",
    "source_row_key",
)

EXPECTED_AUTHORITY_STATES = (
    "product_grade_support",
    "review_only_visual_support",
    "machine_support_no_overlay",
    "dependent_context_only",
    "human_visual_judgment_only",
    "evidence_blocks_backfill",
    "evidence_inconclusive",
    "not_assessable",
)

EXPECTED_RECONCILIATION_CLASSES = (
    "product_accepts_and_product_grade_supports",
    "product_accepts_and_visual_supports",
    "product_rejects_but_product_grade_supports",
    "product_rejects_but_visual_supports",
    "product_accepts_but_evidence_conflicts",
    "product_rejects_and_evidence_blocks",
    "machine_support_no_overlay",
    "evidence_inconclusive",
    "not_assessable_missing_overlay",
    "not_assessable_missing_seed_provenance",
    "not_assessable_join_gap",
)

EXPECTED_RECONCILIATION_CLASS_PRIORITY = (
    "product_rejects_but_product_grade_supports",
    "product_rejects_but_visual_supports",
    "product_accepts_but_evidence_conflicts",
    "not_assessable_missing_overlay",
    "not_assessable_missing_seed_provenance",
    "not_assessable_join_gap",
    "evidence_inconclusive",
    "product_accepts_and_visual_supports",
    "machine_support_no_overlay",
    "product_accepts_and_product_grade_supports",
    "product_rejects_and_evidence_blocks",
)


def test_fixtures_use_real_alignment_writer_columns() -> None:
    assert set(_review_row("FAM001")) == set(ALIGNMENT_REVIEW_COLUMNS)
    assert set(_cell_row("FAM001", "S1", "detected")) == set(ALIGNMENT_CELLS_COLUMNS)
    assert set(_seed_row("FAM001", "S2")) == set(
        ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS,
    )


def test_builds_deterministic_seed_group_from_seed_audit() -> None:
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM001")],
        cell_rows=[
            _cell_row("FAM001", "S1", "detected"),
            _cell_row("FAM001", "S2", "rescued"),
        ],
        seed_audit_rows=[_seed_row("FAM001", "S2")],
    )

    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.feature_family_id == "FAM001"
    assert group.seed_group_basis == "seed_audit"
    assert group.seed_group_id == (
        "seed::FAM001::mz=269.145::rt=10.0000::"
        "window=9.0000-11.0000::ppm=10"
    )
    assert group.detected_cell_count == 1
    assert group.seed_detected_anchor_count == 1
    assert group.rescued_cell_count == 1
    assert group.product_behavior_state == "product_rescued_context_only"


def test_seed_group_indexes_preserve_cell_order_and_overlay_scope() -> None:
    family_cells = (
        _cell_row("FAM_INDEX", "S1", "detected"),
        _cell_row("FAM_INDEX", "S2", "rescued"),
        _cell_row("FAM_INDEX", "S3", "rescued"),
        _cell_row("FAM_INDEX", "S4", "rescued"),
    )
    seed_a = gallery._SeedRecord(
        seed_group_id="seed-a",
        seed_group_basis="seed_audit",
        samples=frozenset({"S2", "S4"}),
    )
    seed_b = gallery._SeedRecord(
        seed_group_id="seed-b",
        seed_group_basis="seed_audit",
        samples=frozenset({"S1"}),
    )

    cells_by_group = gallery._cells_by_family_seed_group(
        cells_by_family={
            "FAM_INDEX": family_cells,
            "FAM_FALLBACK": (_cell_row("FAM_FALLBACK", "S9", "rescued"),),
        },
        seed_records_by_family={"FAM_INDEX": (seed_a, seed_b)},
        family_ids=("FAM_INDEX", "FAM_FALLBACK"),
    )
    overlay_rows = [
        {
            "feature_family_id": "FAM_INDEX",
            "seed_group_id": "",
            "overlay_png_path": "family.png",
        },
        {
            "feature_family_id": "FAM_INDEX",
            "seed_group_id": "seed-b",
            "overlay_png_path": "seed-b.png",
        },
        {
            "feature_family_id": "FAM_INDEX",
            "seed_group_id": "seed-a",
            "overlay_png_path": "seed-a.png",
        },
    ]

    assert [
        row["sample_stem"] for row in cells_by_group[("FAM_INDEX", "seed-a")]
    ] == ["S2", "S4"]
    assert [
        row["sample_stem"] for row in cells_by_group[("FAM_INDEX", "seed-b")]
    ] == ["S1"]
    assert [
        row["sample_stem"]
        for row in cells_by_group[
            ("FAM_FALLBACK", "family_center::FAM_FALLBACK::seed=unknown")
        ]
    ] == ["S9"]
    assert gallery._overlay_rows_by_family_seed_group(overlay_rows)[
        ("FAM_INDEX", "seed-a")
    ][0]["overlay_png_path"] == "seed-a.png"
    assert gallery._legacy_overlay_rows_by_family(overlay_rows)["FAM_INDEX"][0][
        "overlay_png_path"
    ] == "family.png"


def test_non_primary_duplicate_context_is_not_reported_as_primary_backfilled() -> None:
    rescued = _cell_row("FAM_LOSER", "S2", "rescued")
    rescued["primary_matrix_area"] = "1200.0"
    rescued["primary_matrix_area_source"] = "gaussian15_positive_asls_residual"
    result = gallery.build_reconciliation_index(
        review_rows=[
            _review_row(
                "FAM_LOSER",
                include_in_primary_matrix="FALSE",
                row_flags="family_consolidation_loser;duplicate_claim_pressure",
            ),
        ],
        cell_rows=[
            _cell_row("FAM_LOSER", "S1", "detected"),
            rescued,
            _cell_row("FAM_LOSER", "S3", "duplicate_assigned"),
        ],
        seed_audit_rows=[_seed_row("FAM_LOSER", "S2")],
    )

    group = result.groups[0]
    assert group.product_behavior_state == "product_rescued_context_only"
    assert group.reconciliation_class == "evidence_inconclusive"
    assert group.duplicate_assigned_cell_count == 1
    assert group.cell_total_count == 3


def test_review_candidate_area_context_does_not_count_as_matrix_written() -> None:
    family = "FAM_REVIEW_CANDIDATE"
    seed_group_id = (
        "seed::FAM_REVIEW_CANDIDATE::mz=269.145::rt=10.0000::"
        "window=9.0000-11.0000::ppm=10"
    )
    candidate = _cell_row(family, "S_REVIEW", "rescued")
    candidate["production_cell_status"] = "review_rescue"
    candidate["primary_matrix_area"] = "1200.0"
    candidate["primary_matrix_area_source"] = "gaussian15_positive_asls_residual"
    candidate["write_matrix_value"] = "FALSE"

    result = gallery.build_reconciliation_index(
        review_rows=[
            _review_row(
                family,
                include_in_primary_matrix="TRUE",
                detected="2",
                rescued="83",
            ),
        ],
        cell_rows=[
            _cell_row(family, "S_DETECTED", "detected"),
            candidate,
        ],
        seed_audit_rows=[_seed_row(family, "S_REVIEW")],
        retained_gate_rows=[
            {
                "feature_family_id": family,
                "seed_group_id": seed_group_id,
                "evidence_gate_status": "evidence_conflict",
                "recommended_action": "review_product_backfill",
                "support_components": "seed_request_provenance",
                "challenge_blockers": "neighboring_ms1_interference",
                "missing_evidence": "",
                "overlay_family_verdict": (
                    "review_required_neighboring_ms1_interference"
                ),
            },
        ],
    )

    group = result.groups[0]
    assert group.product_behavior_state == "product_rescued_context_only"
    assert group.evidence_authority_state == "evidence_blocks_backfill"
    assert group.reconciliation_class == "product_rejects_and_evidence_blocks"
    assert group.representative_cells[0].product_cell_state == "candidate_context"


def test_detected_zero_families_are_excluded_from_backfill_review_queue() -> None:
    result = gallery.build_reconciliation_index(
        review_rows=[
            _review_row("FAM_SEEDED"),
            _review_row("FAM_ZERO", detected="0", rescued="2"),
        ],
        cell_rows=[
            _cell_row("FAM_SEEDED", "S1", "detected"),
            _cell_row("FAM_SEEDED", "S2", "rescued"),
            _cell_row("FAM_ZERO", "S2", "rescued"),
        ],
        seed_audit_rows=[
            _seed_row("FAM_SEEDED", "S2"),
            _seed_row("FAM_ZERO", "S2"),
        ],
        seed_aware_family_rows=[
            {
                "feature_family_id": "FAM_ZERO",
                "review_classification": "seed_shape_supported_review_candidate",
                "png_paths": "plots/fam-zero.png",
            },
        ],
        candidate_gate_rows=[
            {
                "feature_family_id": "FAM_ZERO",
                "candidate_gate_status": "production_candidate",
                "support_components": "validated_tier2_trace_evidence",
                "challenge_blockers": "",
            },
        ],
    )

    assert [group.feature_family_id for group in result.groups] == ["FAM_SEEDED"]
    assert result.summary["excluded_family_counts"] == {
        "detected_zero_family": 1,
    }


def test_product_grade_and_visual_support_remain_separate() -> None:
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM002")],
        cell_rows=[_cell_row("FAM002", "S2", "rescued")],
        seed_audit_rows=[_seed_row("FAM002", "S2")],
        candidate_gate_rows=[
            {
                "feature_family_id": "FAM002",
                "candidate_gate_status": "production_candidate",
                "support_components": "validated_tier2_trace_evidence",
                "challenge_blockers": "",
            },
        ],
        seed_aware_family_rows=[
            {
                "feature_family_id": "FAM002",
                "review_classification": "seed_shape_supported_review_candidate",
                "review_reason": "seed-specific overlays support MS1 shape",
                "png_paths": "plots/fam002.png",
            },
        ],
    )

    group = result.groups[0]
    assert group.evidence_authority_state == "product_grade_support"
    assert "validated_tier2_trace_evidence" in group.product_grade_support_components
    assert (
        "seed_shape_supported_review_candidate"
        in group.review_only_visual_components
    )
    assert group.reconciliation_class == "product_rejects_but_product_grade_supports"
    assert group.overlay_png_path == "plots/fam002.png"


def test_shift_aware_same_pattern_support_is_review_only_visual_evidence() -> None:
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM_SHIFT")],
        cell_rows=[
            _cell_row("FAM_SHIFT", "S1", "detected"),
            _cell_row("FAM_SHIFT", "S2", "rescued"),
        ],
        seed_audit_rows=[_seed_row("FAM_SHIFT", "S2")],
        shift_aware_same_pattern_rows=[
            {
                "feature_family_id": "FAM_SHIFT",
                "source_family": "FAM_REF",
                "is_reference": "TRUE",
                "shift_basis": "median_shape_correlation",
                "shift_to_reference_sec": "0.0",
                "shape_similarity_to_reference_after_group_shift": "1.000",
            },
            {
                "feature_family_id": "FAM_SHIFT",
                "source_family": "FAM_SHIFT",
                "is_reference": "FALSE",
                "shift_basis": "median_shape_correlation",
                "shift_to_reference_sec": "-2.4",
                "shape_similarity_to_reference_after_group_shift": "0.983",
            },
        ],
    )

    group = result.groups[0]
    assert group.evidence_authority_state == "review_only_visual_support"
    assert group.product_grade_support_components == ()
    assert group.review_only_visual_components == (
        "shift_aware_same_pattern_support_review_only",
    )
    assert group.reconciliation_class == "product_rejects_but_visual_supports"
    assert "source_family_best_shift_summary.tsv" in group.source_artifacts
    assert any("min r=0.983" in note for note in group.overlay_evidence_notes)


def test_shift_aware_standard_peak_gate_is_shadow_visual_support() -> None:
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM_GATE")],
        cell_rows=[
            _cell_row("FAM_GATE", "S1", "detected"),
            _cell_row("FAM_GATE", "S2", "rescued"),
        ],
        seed_audit_rows=[_seed_row("FAM_GATE", "S2")],
        shift_aware_standard_peak_gate_rows=[
            {
                "feature_family_id": "FAM_GATE",
                "standard_peak_gate_call": "standard_peak_gate_supported",
                "standard_peak_gate_reasons": (
                    "shift_aware_same_pattern_supported;"
                    "family_overlay_gaussian_smoothed_standard_peak_supported"
                ),
                "standard_peak_gate_blockers": "",
                "calibration_outcome": "true_positive",
                "min_shape_r_after_best_shift": "0.983",
                "max_abs_shift_sec": "2.40",
            },
        ],
    )

    group = result.groups[0]
    assert group.evidence_authority_state == "review_only_visual_support"
    assert group.product_grade_support_components == ()
    assert (
        "shift_aware_standard_peak_gate_supported_review_only"
        in group.review_only_visual_components
    )
    assert group.reconciliation_class == "product_rejects_but_visual_supports"
    assert "shift_aware_standard_peak_gate_calibration.tsv" in group.source_artifacts
    assert any(
        "standard peak gate=supported" in note
        for note in group.overlay_evidence_notes
    )


def test_activation_delta_promotes_matching_projection_accept_to_product_written(
) -> None:
    family = "FAM_GATE_ACTIVATED"
    seed_group_id = (
        "seed::FAM_GATE_ACTIVATED::mz=269.145::rt=10.0000::"
        "window=9.0000-11.0000::ppm=10"
    )

    result = gallery.build_reconciliation_index(
        review_rows=[_review_row(family)],
        cell_rows=[
            _cell_row(family, "S1", "detected"),
            _cell_row(family, "S2", "rescued"),
        ],
        seed_audit_rows=[_seed_row(family, "S2")],
        shift_aware_standard_peak_gate_rows=[
            {
                "feature_family_id": family,
                "standard_peak_gate_call": "standard_peak_gate_supported",
                "standard_peak_gate_reasons": (
                    "shift_aware_same_pattern_supported;"
                    "family_overlay_gaussian_smoothed_standard_peak_supported"
                ),
                "standard_peak_gate_blockers": "",
                "calibration_outcome": "true_positive",
                "min_shape_r_after_best_shift": "0.983",
                "max_abs_shift_sec": "2.40",
            },
        ],
        shadow_projection_rows=[
            _projection_row(
                family,
                seed_group_id,
                "S2",
                current_status="review_rescue",
                current_written="FALSE",
                shadow_decision="accept",
                projected_written="TRUE",
                reasons="same_peak_reason:shift_aware_standard_peak_gate_supported",
                authority_chain="standard_peak_ms1_pattern_product_authorized",
            ),
        ],
        activation_application_summary_rows=[
            {
                "application_status": "applied",
                "activation_output_mode": "matrix-only",
                "acceptance_status": "pass",
                "decision_rows_total": "1",
                "auto_activate_count": "1",
                "auto_block_count": "0",
                "matrix_cells_written": "1",
                "matrix_cells_blanked": "0",
                "summary_reason": "explicit_activation_sidecar_applied",
            },
        ],
        activation_value_delta_rows=[
            _activation_delta_row(family, "S2", effect="written"),
        ],
    )

    group = result.groups[0]
    assert group.product_behavior_state == "product_primary_backfilled"
    assert group.top_product_reason == "activation_value_delta_written"
    assert group.reconciliation_class == "product_accepts_and_visual_supports"
    assert "activation_application_summary.tsv" in group.source_artifacts
    assert "activation_value_delta.tsv" in group.source_artifacts
    assert result.summary["activation_value_delta_matrix_effect_counts"] == {
        "written": 1,
    }
    assert result.summary["activation_written_projection_group_count"] == 1
    assert result.summary["activation_written_projection_cell_count"] == 1
    assert result.summary["product_behavior_changed"] is True
    assert result.summary["product_behavior_source"] == "activation_value_delta.tsv"


def test_activation_delta_without_projection_accept_does_not_promote_blocked_group(
) -> None:
    family = "FAM_GATE_BLOCKED_WITH_DELTA"
    seed_group_id = (
        "seed::FAM_GATE_BLOCKED_WITH_DELTA::mz=269.145::rt=10.0000::"
        "window=9.0000-11.0000::ppm=10"
    )

    result = gallery.build_reconciliation_index(
        review_rows=[_review_row(family)],
        cell_rows=[
            _cell_row(family, "S1", "detected"),
            _cell_row(family, "S2", "rescued"),
        ],
        seed_audit_rows=[_seed_row(family, "S2")],
        shift_aware_standard_peak_gate_rows=[
            {
                "feature_family_id": family,
                "standard_peak_gate_call": "standard_peak_gate_blocked",
                "standard_peak_gate_reasons": "shift_aware_same_pattern_supported",
                "standard_peak_gate_blockers": (
                    "family_overlay_gaussian_smoothed_peak_not_standard"
                ),
                "calibration_outcome": "true_negative",
                "min_shape_r_after_best_shift": "0.998",
                "max_abs_shift_sec": "0.00",
            },
        ],
        shadow_projection_rows=[
            _projection_row(
                family,
                seed_group_id,
                "S2",
                current_status="review_rescue",
                current_written="FALSE",
                shadow_decision="block",
                projected_written="FALSE",
                reasons="family_overlay_gaussian_smoothed_peak_not_standard",
            ),
        ],
        activation_value_delta_rows=[
            _activation_delta_row(family, "S2", effect="written"),
        ],
    )

    group = result.groups[0]
    assert group.product_behavior_state == "product_rescued_context_only"
    assert group.reconciliation_class == "product_rejects_and_evidence_blocks"
    assert "activation_value_delta.tsv" not in group.source_artifacts
    assert result.summary["activation_value_delta_matrix_effect_counts"] == {
        "written": 1,
    }
    assert result.summary["activation_written_projection_group_count"] == 0
    assert result.summary["product_behavior_changed"] is False


def test_current_projection_accepted_rescue_counts_as_product_written() -> None:
    family = "FAM_ALREADY_WRITTEN"
    seed_group_id = (
        "seed::FAM_ALREADY_WRITTEN::mz=269.145::rt=10.0000::"
        "window=9.0000-11.0000::ppm=10"
    )

    result = gallery.build_reconciliation_index(
        review_rows=[_review_row(family)],
        cell_rows=[
            _cell_row(family, "S1", "detected"),
            _cell_row(family, "S2", "rescued"),
        ],
        seed_audit_rows=[_seed_row(family, "S2")],
        shift_aware_standard_peak_gate_rows=[
            {
                "feature_family_id": family,
                "standard_peak_gate_call": "standard_peak_gate_supported",
                "standard_peak_gate_reasons": (
                    "shift_aware_same_pattern_supported;"
                    "family_overlay_gaussian_smoothed_standard_peak_supported"
                ),
                "standard_peak_gate_blockers": "",
                "calibration_outcome": "true_positive",
                "min_shape_r_after_best_shift": "0.983",
                "max_abs_shift_sec": "2.40",
            },
        ],
        shadow_projection_rows=[
            _projection_row(
                family,
                seed_group_id,
                "S2",
                current_status="accepted_rescue",
                current_written="TRUE",
                shadow_decision="context",
                projected_written="TRUE",
                reasons="already_written_current_matrix",
            ),
        ],
    )

    group = result.groups[0]
    assert group.product_behavior_state == "product_primary_backfilled"
    assert group.top_product_reason == "shadow_projection_current_matrix_written"
    assert group.reconciliation_class == "product_accepts_and_visual_supports"
    assert "shadow_production_projection_cells.tsv" in group.source_artifacts
    assert "activation_value_delta.tsv" not in group.source_artifacts
    assert result.summary["current_written_projection_group_count"] == 1
    assert result.summary["current_written_projection_cell_count"] == 1
    assert result.summary["product_behavior_changed"] is True


def test_shift_aware_standard_peak_gate_blocked_is_evidence_blocker() -> None:
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM_BLOCK")],
        cell_rows=[
            _cell_row("FAM_BLOCK", "S1", "detected"),
            _cell_row("FAM_BLOCK", "S2", "rescued"),
        ],
        seed_audit_rows=[_seed_row("FAM_BLOCK", "S2")],
        shift_aware_standard_peak_gate_rows=[
            {
                "feature_family_id": "FAM_BLOCK",
                "standard_peak_gate_call": "standard_peak_gate_blocked",
                "standard_peak_gate_reasons": "shift_aware_same_pattern_supported",
                "standard_peak_gate_blockers": (
                    "family_overlay_gaussian_smoothed_peak_not_standard"
                ),
                "calibration_outcome": "true_negative",
                "min_shape_r_after_best_shift": "0.998",
                "max_abs_shift_sec": "0.00",
            },
        ],
    )

    group = result.groups[0]
    assert group.evidence_authority_state == "evidence_blocks_backfill"
    assert (
        "shift_aware_standard_peak_gate_blocked"
        in group.blocker_components
    )
    assert any(
        "family_overlay_gaussian_smoothed_peak_not_standard" in note
        for note in group.overlay_evidence_notes
    )


def test_product_authorized_cell_evidence_is_product_grade_support() -> None:
    rescued = _cell_row("FAM_AUTH", "S2", "rescued")
    rescued.update(
        {
            "backfill_ms1_product_authority_status": "product_authorized",
            "backfill_ms1_product_authority_scope": "feature_family_sample",
            "backfill_ms1_product_authority_source": "reviewed_ms1_overlay",
            "backfill_ms1_product_authority_reason": (
                "product_authority_allowlist_and_anchor_own_max_supported"
            ),
        },
    )

    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM_AUTH")],
        cell_rows=[
            _cell_row("FAM_AUTH", "S1", "detected"),
            rescued,
        ],
        seed_audit_rows=[_seed_row("FAM_AUTH", "S2")],
    )

    group = result.groups[0]
    assert group.evidence_authority_state == "product_grade_support"
    assert group.reconciliation_class == "product_rejects_but_product_grade_supports"
    assert group.product_grade_support_components == (
        "product_authorized_ms1_pattern:"
        "product_authority_allowlist_and_anchor_own_max_supported",
    )


def test_stale_candidate_gate_source_hash_fails_closed() -> None:
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM_STALE")],
        cell_rows=[_cell_row("FAM_STALE", "S2", "rescued")],
        seed_audit_rows=[_seed_row("FAM_STALE", "S2")],
        candidate_gate_rows=[
            {
                "feature_family_id": "FAM_STALE",
                "candidate_gate_status": "production_candidate",
                "support_components": "validated_tier2_trace_evidence",
                "challenge_blockers": "",
                "source_review_sha256": "STALE_REVIEW_HASH",
                "source_cell_sha256": "expected_cell_hash",
            },
        ],
        input_artifacts={
            "alignment_review_sha256": "expected_review_hash",
            "alignment_cells_sha256": "expected_cell_hash",
        },
    )

    group = result.groups[0]
    assert group.evidence_authority_state == "not_assessable"
    assert group.reconciliation_class == "not_assessable_join_gap"
    assert group.product_grade_support_components == ()
    assert "stale_candidate_gate_review_sha256_mismatch" in group.missing_evidence
    assert "stale_candidate_gate_review_sha256_mismatch" in group.source_warnings


def test_malformed_candidate_gate_with_blockers_fails_closed() -> None:
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM_BLOCKED")],
        cell_rows=[_cell_row("FAM_BLOCKED", "S2", "rescued")],
        seed_audit_rows=[_seed_row("FAM_BLOCKED", "S2")],
        candidate_gate_rows=[
            {
                "feature_family_id": "FAM_BLOCKED",
                "candidate_gate_status": "production_candidate",
                "support_components": "validated_tier2_trace_evidence",
                "challenge_blockers": "missing_seed_trace",
            },
        ],
    )

    group = result.groups[0]
    assert group.evidence_authority_state == "evidence_blocks_backfill"
    assert group.reconciliation_class == "product_rejects_and_evidence_blocks"
    assert group.product_grade_support_components == ()
    assert group.blocker_components == ("missing_seed_trace",)


def test_retained_gate_machine_support_marks_overlay_not_required(
    tmp_path: Path,
) -> None:
    family = "FAM_STRONG"
    seed_group_id = (
        "seed::FAM_STRONG::mz=269.145::rt=10.0000::window=9.0000-11.0000::ppm=10"
    )
    result = gallery.build_reconciliation_index(
        review_rows=[
            _review_row(
                family,
                identity_decision="production_family",
                include_in_primary_matrix="TRUE",
                row_flags="",
                detected="83",
                rescued="2",
            ),
        ],
        cell_rows=[
            _cell_row(family, "S1", "detected"),
            _cell_row(family, "S2", "detected"),
            _cell_row(family, "S3", "rescued"),
        ],
        seed_audit_rows=[_seed_row(family, "S3")],
        retained_gate_rows=[
            {
                "feature_family_id": family,
                "seed_group_id": seed_group_id,
                "evidence_gate_status": "machine_support_no_overlay",
                "recommended_action": "track_machine_supported_backfill",
                "support_components": (
                    "seed_request_provenance;"
                    "high_detected_anchor_low_rescue_machine_support"
                ),
                "challenge_blockers": "",
                "missing_evidence": "",
            },
        ],
    )

    group = result.groups[0]
    assert group.evidence_authority_state == "machine_support_no_overlay"
    assert group.reconciliation_class == "machine_support_no_overlay"
    assert group.missing_evidence == ()
    assert "high_detected_anchor_low_rescue_machine_support" in (
        group.dependent_context_components
    )

    html_path = tmp_path / "gallery.html"
    gallery.write_reconciliation_gallery_html(html_path, result, output_paths={})
    text = html_path.read_text(encoding="utf-8")
    assert "not required: high detected anchors, low candidate load" in text
    assert "not in supplied overlay batch" not in text


def test_missing_overlay_and_join_gap_fail_closed() -> None:
    result = gallery.build_reconciliation_index(
        review_rows=[
            _review_row("FAM_MISSING_OVERLAY"),
            _review_row("FAM_JOIN_GAP"),
        ],
        cell_rows=[
            _cell_row("FAM_MISSING_OVERLAY", "S1", "rescued"),
            _cell_row("FAM_JOIN_GAP", "S1", "rescued"),
        ],
        seed_audit_rows=[
            _seed_row("FAM_MISSING_OVERLAY", "S1"),
            _seed_row("FAM_JOIN_GAP", "S_NOT_IN_CELLS"),
        ],
        seed_aware_family_rows=[
            {
                "feature_family_id": "FAM_MISSING_OVERLAY",
                "review_classification": "not_assessable",
                "review_reason": "overlay missing",
            },
        ],
    )

    by_family = {group.feature_family_id: group for group in result.groups}
    missing_overlay = by_family["FAM_MISSING_OVERLAY"]
    assert missing_overlay.evidence_authority_state == "not_assessable"
    assert missing_overlay.reconciliation_class == "not_assessable_missing_overlay"
    assert "missing_overlay" in missing_overlay.missing_evidence

    join_gap = by_family["FAM_JOIN_GAP"]
    assert join_gap.evidence_authority_state == "not_assessable"
    assert join_gap.reconciliation_class == "not_assessable_join_gap"
    assert "join_gap_seed_audit_sample_not_in_cells" in join_gap.source_warnings


def test_review_required_overlay_is_human_judgment_not_hard_blocker() -> None:
    result = gallery.build_reconciliation_index(
        review_rows=[
            _review_row(
                "FAM_REVIEW",
                identity_decision="production_family",
                include_in_primary_matrix="TRUE",
                row_flags="backfill_cell_evidence_required",
            ),
        ],
        cell_rows=[
            _cell_row("FAM_REVIEW", "S1", "detected"),
            _cell_row("FAM_REVIEW", "S2", "rescued"),
        ],
        seed_audit_rows=[_seed_row("FAM_REVIEW", "S2")],
        overlay_rows=[
            {
                "feature_family_id": "FAM_REVIEW",
                "seed_group_id": (
                    "seed::FAM_REVIEW::mz=269.145::rt=10.0000::"
                    "window=9.0000-11.0000::ppm=10"
                ),
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": "plots/fam-review.png",
            },
        ],
    )

    group = result.groups[0]
    assert group.evidence_authority_state == "human_visual_judgment_only"
    assert group.reconciliation_class == "evidence_inconclusive"
    assert group.top_blocker == "review_required_neighboring_ms1_interference"
    assert group.overlay_png_path == "plots/fam-review.png"


def test_overlay_metric_notes_render_evidence_chain_without_group_tsv_schema_change(
    tmp_path: Path,
) -> None:
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM_OWNMAX")],
        cell_rows=[
            _cell_row("FAM_OWNMAX", "S1", "detected"),
            _cell_row("FAM_OWNMAX", "S2", "rescued"),
        ],
        seed_audit_rows=[_seed_row("FAM_OWNMAX", "S2")],
        overlay_rows=[
            {
                "feature_family_id": "FAM_OWNMAX",
                "seed_group_id": (
                    "seed::FAM_OWNMAX::mz=269.145::rt=10.0000::"
                    "window=9.0000-11.0000::ppm=10"
                ),
                "family_verdict": "ms1_shape_supports_family_backfill",
                "png_path": "plots/fam-ownmax.png",
                "trace_data_json": "plots/fam-ownmax_trace_data.json",
                "shape_supported_fraction": "0.625",
                "absolute_own_max_shape_supported_fraction": "0.875",
                "absolute_trace_apex_cluster_fraction": "0.75",
                "global_apex_interference_fraction": "0.25",
                "low_selected_peak_dominance_fraction": "0",
            },
        ],
    )

    group = result.groups[0]
    assert "own-max shape support=0.875" in group.overlay_evidence_notes
    assert "absolute apex cluster=0.75" in group.overlay_evidence_notes

    html_path = tmp_path / "gallery.html"
    gallery.write_reconciliation_gallery_html(
        html_path,
        result,
        output_paths={
            "groups_tsv": tmp_path / "groups.tsv",
            "representative_cells_tsv": tmp_path / "representatives.tsv",
            "summary_json": tmp_path / "summary.json",
        },
    )
    text = html_path.read_text(encoding="utf-8")
    assert "overlay evidence metrics" in text
    assert "own-max shape support=0.875" in text
    assert "detected-anchor apex-aligned support=0.625" in text

    paths = gallery.write_reconciliation_outputs(tmp_path / "out", result)
    assert "overlay_evidence_notes" not in _read_header(paths["groups_tsv"])


def test_gallery_notes_include_anchor_peak_own_max_cell_evidence(
    tmp_path: Path,
) -> None:
    trace_json = tmp_path / "fam-anchor_trace_data.json"
    trace_json.write_text(
        json.dumps(
            {
                "family_id": "FAM_ANCHOR",
                "rt_min": 9.0,
                "rt_max": 11.0,
                "evidence_summary": {
                    "family_verdict": "review_required_neighboring_ms1_interference"
                },
                "coherence_rows": [
                    {
                        "sample_stem": "S_SUPPORT",
                        "anchor_peak_rt": "10",
                        "reason": (
                            "family_ms1_overlay_anchor_peak_own_max_shape_supported"
                        ),
                        "shape_correlation_score": "0.94",
                    },
                    {
                        "sample_stem": "S_REVIEW",
                        "anchor_peak_rt": "10",
                        "reason": (
                            "family_ms1_overlay_anchor_peak_shape_below_threshold"
                        ),
                        "shape_correlation_score": "0.40",
                    },
                    {
                        "sample_stem": "S_DUP",
                        "anchor_peak_rt": "10",
                        "reason": "alignment_gap_fill_duplicate_loser",
                        "shape_correlation_score": "",
                    },
                ],
                "traces": [
                    _overlay_trace("S_DET", "detected", 10.0, [0, 300, 1000, 300, 0]),
                    _overlay_trace(
                        "S_SUPPORT",
                        "rescued",
                        10.02,
                        [0, 280, 940, 310, 0],
                    ),
                    _overlay_trace(
                        "S_REVIEW",
                        "rescued",
                        10.03,
                        [900, 500, 80, 500, 920],
                    ),
                    _overlay_trace(
                        "S_DUP",
                        "rescued",
                        10.01,
                        [0, 270, 930, 305, 0],
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM_ANCHOR", detected="1", rescued="3")],
        cell_rows=[
            _cell_row("FAM_ANCHOR", "S_DET", "detected", apex_rt="10.0"),
            _cell_row("FAM_ANCHOR", "S_SUPPORT", "rescued", apex_rt="10.02"),
            _cell_row("FAM_ANCHOR", "S_REVIEW", "rescued", apex_rt="10.03"),
            _cell_row(
                "FAM_ANCHOR",
                "S_DUP",
                "rescued",
                apex_rt="10.01",
                gap_fill_state="not_filled",
                gap_fill_reason="not_requested_duplicate_loser",
            ),
        ],
        seed_audit_rows=[
            _seed_row("FAM_ANCHOR", "S_SUPPORT"),
            _seed_row("FAM_ANCHOR", "S_REVIEW"),
            _seed_row("FAM_ANCHOR", "S_DUP"),
        ],
        overlay_rows=[
            {
                "feature_family_id": "FAM_ANCHOR",
                "seed_group_id": (
                    "seed::FAM_ANCHOR::mz=269.145::rt=10.0000::"
                    "window=9.0000-11.0000::ppm=10"
                ),
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": "plots/fam-anchor.png",
                "trace_data_json": str(trace_json),
            },
        ],
    )

    group = result.groups[0]
    assert "anchor peak RT=10" in group.overlay_evidence_notes
    assert any(
        note.startswith("anchor same-peak candidate support=S_SUPPORT")
        for note in group.overlay_evidence_notes
    )
    assert any(
        note.startswith("anchor same-peak review=S_REVIEW")
        for note in group.overlay_evidence_notes
    )
    assert any(
        "S_DUP:alignment_gap_fill_duplicate_loser" in note
        for note in group.overlay_evidence_notes
    )

    html_path = tmp_path / "gallery.html"
    gallery.write_reconciliation_gallery_html(
        html_path,
        result,
        output_paths={
            "groups_tsv": tmp_path / "groups.tsv",
            "representative_cells_tsv": tmp_path / "representatives.tsv",
            "summary_json": tmp_path / "summary.json",
        },
    )
    text = html_path.read_text(encoding="utf-8")
    assert "anchor own-max shape threshold=0.5" in text
    assert "S_SUPPORT" in text
    assert "alignment_gap_fill_duplicate_loser" in text


def test_gallery_anchor_notes_are_scoped_to_seed_group_cells(tmp_path: Path) -> None:
    trace_json = tmp_path / "fam-seed-scope_trace_data.json"
    trace_json.write_text(
        json.dumps(
            {
                "family_id": "FAM_SEED_SCOPE",
                "rt_min": 9.0,
                "rt_max": 11.0,
                "evidence_summary": {
                    "family_verdict": "review_required_neighboring_ms1_interference"
                },
                "coherence_rows": [
                    {
                        "sample_stem": "S_SEED_A",
                        "anchor_peak_rt": "10",
                        "reason": (
                            "family_ms1_overlay_anchor_peak_own_max_shape_supported"
                        ),
                        "shape_correlation_score": "0.94",
                    },
                    {
                        "sample_stem": "S_SEED_B",
                        "anchor_peak_rt": "10.2",
                        "reason": (
                            "family_ms1_overlay_anchor_peak_own_max_shape_supported"
                        ),
                        "shape_correlation_score": "0.94",
                    },
                ],
                "traces": [
                    _overlay_trace("S_DET", "detected", 10.0, [0, 300, 1000, 300, 0]),
                    _overlay_trace(
                        "S_SEED_A",
                        "rescued",
                        10.02,
                        [0, 280, 940, 310, 0],
                    ),
                    _overlay_trace(
                        "S_SEED_B",
                        "rescued",
                        10.03,
                        [0, 290, 930, 300, 0],
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM_SEED_SCOPE", detected="1", rescued="2")],
        cell_rows=[
            _cell_row("FAM_SEED_SCOPE", "S_DET", "detected", apex_rt="10.0"),
            _cell_row("FAM_SEED_SCOPE", "S_SEED_A", "rescued", apex_rt="10.02"),
            _cell_row("FAM_SEED_SCOPE", "S_SEED_B", "rescued", apex_rt="10.03"),
        ],
        seed_audit_rows=[
            _seed_row(
                "FAM_SEED_SCOPE",
                "S_SEED_A",
                seed_rt="10.0000",
                rt_start="9.0000",
                rt_end="11.0000",
            ),
            _seed_row(
                "FAM_SEED_SCOPE",
                "S_SEED_B",
                seed_rt="10.2000",
                rt_start="9.2000",
                rt_end="11.2000",
            ),
        ],
        overlay_rows=[
            {
                "feature_family_id": "FAM_SEED_SCOPE",
                "seed_group_id": (
                    "seed::FAM_SEED_SCOPE::mz=269.145::rt=10.0000::"
                    "window=9.0000-11.0000::ppm=10"
                ),
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": "plots/fam-seed-scope-a.png",
                "trace_data_json": str(trace_json),
            },
            {
                "feature_family_id": "FAM_SEED_SCOPE",
                "seed_group_id": (
                    "seed::FAM_SEED_SCOPE::mz=269.145::rt=10.2000::"
                    "window=9.2000-11.2000::ppm=10"
                ),
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": "plots/fam-seed-scope-b.png",
                "trace_data_json": str(trace_json),
            },
        ],
    )

    by_seed = {group.seed_rt: group for group in result.groups}
    seed_a_notes = " ".join(by_seed["10.0000"].overlay_evidence_notes)
    seed_b_notes = " ".join(by_seed["10.2000"].overlay_evidence_notes)

    assert "S_SEED_A" in seed_a_notes
    assert "S_SEED_B" not in seed_a_notes
    assert "S_SEED_B" in seed_b_notes
    assert "S_SEED_A" not in seed_b_notes


def test_gallery_anchor_notes_when_old_artifact_lacks_coherence_data(
    tmp_path: Path,
) -> None:
    trace_json = tmp_path / "fam-old_trace_data.json"
    trace_json.write_text(
        json.dumps(
            {
                "family_id": "FAM_OLD",
                "rt_min": 9.0,
                "rt_max": 11.0,
                "evidence_summary": {
                    "family_verdict": "review_required_neighboring_ms1_interference"
                },
                "traces": [
                    _overlay_trace("S_DET", "detected", 10.0, [0, 300, 1000, 300, 0]),
                    _overlay_trace("S_RESC", "rescued", 10.02, [0, 280, 940, 310, 0]),
                ],
            }
        ),
        encoding="utf-8",
    )
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row("FAM_OLD", detected="1", rescued="1")],
        cell_rows=[
            _cell_row("FAM_OLD", "S_DET", "detected", apex_rt="10.0"),
            _cell_row("FAM_OLD", "S_RESC", "rescued", apex_rt="10.02"),
        ],
        seed_audit_rows=[
            _seed_row("FAM_OLD", "S_RESC"),
        ],
        overlay_rows=[
            {
                "feature_family_id": "FAM_OLD",
                "seed_group_id": (
                    "seed::FAM_OLD::mz=269.145::rt=10.0000::window=9.0000-11.0000::ppm=10"
                ),
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": "plots/fam-old.png",
                "trace_data_json": str(trace_json),
            },
        ],
    )
    group = result.groups[0]
    assert "anchor peak evidence=unavailable" in group.overlay_evidence_notes


def test_seed_specific_overlay_rows_do_not_broadcast_across_seed_groups(
    tmp_path: Path,
) -> None:
    family = "FAM_SEED_JOIN"
    seed_b_id = (
        "seed::FAM_SEED_JOIN::mz=269.145::rt=10.5000::"
        "window=9.5000-11.5000::ppm=10"
    )
    result = gallery.build_reconciliation_index(
        review_rows=[_review_row(family, detected="1", rescued="2")],
        cell_rows=[
            _cell_row(family, "S1", "detected"),
            _cell_row(family, "S2", "rescued"),
            _cell_row(family, "S3", "rescued"),
        ],
        seed_audit_rows=[
            _seed_row(family, "S2"),
            _seed_row(
                family,
                "S3",
                seed_rt="10.5000",
                rt_start="9.5000",
                rt_end="11.5000",
            ),
        ],
        overlay_rows=[
            {
                "feature_family_id": family,
                "seed_group_id": "",
                "family_verdict": "ms1_shape_supports_family_backfill",
                "png_path": "plots/fam-legacy.png",
                "absolute_own_max_shape_supported_fraction": "0.9",
            },
            {
                "feature_family_id": family,
                "seed_group_id": seed_b_id,
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": "plots/fam-seed-b.png",
                "absolute_own_max_shape_supported_fraction": "0.1",
            },
        ],
    )

    by_seed = {group.seed_group_id: group for group in result.groups}
    seed_a = next(group for seed, group in by_seed.items() if seed != seed_b_id)
    seed_b = by_seed[seed_b_id]

    assert seed_a.evidence_authority_state == "not_assessable"
    assert seed_a.reconciliation_class == "not_assessable_missing_overlay"
    assert seed_a.top_support_component == "seed_request_provenance"
    assert seed_a.top_blocker == ""
    assert "missing_seed_specific_overlay" in seed_a.missing_evidence
    assert "legacy_family_overlay_context" in seed_a.dependent_context_components
    assert (
        "legacy_family_overlay:ms1_shape_supports_family_backfill"
        in seed_a.dependent_context_components
    )
    assert seed_a.review_only_visual_components == ()
    assert seed_a.overlay_png_path == ""
    assert seed_a.family_pattern_png_path == "plots/fam-legacy.png"
    assert seed_a.family_pattern_verdict == "ms1_shape_supports_family_backfill"
    assert seed_a.overlay_evidence_notes == ()

    assert seed_b.evidence_authority_state == "human_visual_judgment_only"
    assert seed_b.top_support_component == "seed_request_provenance"
    assert seed_b.top_blocker == "review_required_neighboring_ms1_interference"
    assert seed_b.overlay_png_path == "plots/fam-seed-b.png"
    assert seed_b.overlay_evidence_notes == ("own-max shape support=0.1",)

    html_path = tmp_path / "gallery.html"
    gallery.write_reconciliation_gallery_html(
        html_path,
        result,
        output_paths={},
    )
    text = html_path.read_text(encoding="utf-8")
    assert "pattern PNG" in text
    assert "seed 1 PNG" not in text
    assert "H2 family context" in text


def test_html_gallery_marks_family_context_available_from_overlay_fallback(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    index = gallery.ReconciliationIndex(
        groups=(
            _group(
                "FAM_CONTEXT",
                "evidence_inconclusive",
                "human_visual_judgment_only",
                overlay_png_path="plots/fam-context.png",
            ),
        ),
        representative_cells=(),
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        index,
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    family_row = text[
        text.index('class="family-section-row"') :
        text.index('class="seed-decision-row"')
    ]
    assert "context available" in family_row
    assert ">family context</a>" in family_row
    assert "child-row hypothesis PNG is shown only when generated" in family_row
    assert "open hypothesis PNG in the child row" not in family_row
    assert "no context" not in family_row
    assert "no pattern" not in text
    assert "pattern unavailable" not in text


def test_html_gallery_keeps_hypothesis_and_family_context_in_separate_rows(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    plots = tmp_path / "plots"
    plots.mkdir()
    (plots / "fam-context.png").write_bytes(b"family context")
    (plots / "fam-context_hypothesis.png").write_bytes(b"hypothesis")
    index = gallery.ReconciliationIndex(
        groups=(
            _group(
                "FAM_CONTEXT",
                "product_accepts_and_visual_supports",
                "human_visual_judgment_only",
                overlay_png_path="plots/fam-context.png",
            ),
        ),
        representative_cells=(),
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        index,
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    family_row = text[
        text.index('class="family-section-row"') :
        text.index('class="seed-decision-row"')
    ]
    child_row = text[
        text.index('class="seed-decision-row"') : text.index('class="detail-row"')
    ]
    assert ">family context</a>" in family_row
    assert ">hypothesis PNG</a>" in child_row
    assert ">family context</a>" not in child_row
    assert "family context in header" not in child_row


def test_writer_schema_values_and_order_are_stable(tmp_path: Path) -> None:
    assert gallery.GROUP_TSV_COLUMNS == EXPECTED_GROUP_COLUMNS
    assert (
        gallery.REPRESENTATIVE_CELL_TSV_COLUMNS
        == EXPECTED_REPRESENTATIVE_CELL_COLUMNS
    )
    assert gallery.EVIDENCE_AUTHORITY_STATES == EXPECTED_AUTHORITY_STATES
    assert gallery.RECONCILIATION_CLASSES == EXPECTED_RECONCILIATION_CLASSES
    assert (
        gallery.RECONCILIATION_CLASS_PRIORITY
        == EXPECTED_RECONCILIATION_CLASS_PRIORITY
    )

    index = gallery.ReconciliationIndex(
        groups=tuple(
            _group(f"FAM{i:02d}", reconciliation_class, authority_state)
            for i, (reconciliation_class, authority_state) in enumerate(
                zip(
                    EXPECTED_RECONCILIATION_CLASSES,
                    (
                        "product_grade_support",
                        "review_only_visual_support",
                        "product_grade_support",
                        "review_only_visual_support",
                        "evidence_blocks_backfill",
                        "evidence_blocks_backfill",
                        "machine_support_no_overlay",
                        "evidence_inconclusive",
                        "not_assessable",
                        "not_assessable",
                        "not_assessable",
                    ),
                    strict=True,
                ),
                start=1,
            )
        ),
        representative_cells=(
            gallery.RepresentativeCell(
                feature_family_id="FAM03",
                seed_group_id="seed::FAM03::mz=3::rt=3::window=2-4::ppm=10",
                representative_roles=("seed_representative",),
                sample_stem="S1",
                cell_status="rescued",
                product_cell_state="context_only",
                scan_support_score="0.75",
                representative_reason="seed/request representative",
                source_row_key="FAM03::S1",
            ),
        ),
    )

    paths = gallery.write_reconciliation_outputs(tmp_path, index)

    group_rows = _read_tsv(paths["groups_tsv"])
    representative_rows = _read_tsv(paths["representative_cells_tsv"])
    assert _read_header(paths["groups_tsv"]) == list(EXPECTED_GROUP_COLUMNS)
    assert _read_header(paths["representative_cells_tsv"]) == list(
        EXPECTED_REPRESENTATIVE_CELL_COLUMNS,
    )
    assert "backfill_score" not in _read_header(paths["groups_tsv"])
    assert {row["schema_version"] for row in group_rows} == {
        "backfill_evidence_reconciliation_v0",
    }
    assert {row["schema_version"] for row in representative_rows} == {
        "backfill_evidence_reconciliation_v0",
    }
    assert [row["reconciliation_class"] for row in group_rows] == list(
        EXPECTED_RECONCILIATION_CLASS_PRIORITY,
    )
    assert [row["priority_rank"] for row in group_rows] == [
        str(index + 1) for index in range(len(EXPECTED_RECONCILIATION_CLASSES))
    ]
    join_gap_row = next(
        row
        for row in group_rows
        if row["reconciliation_class"] == "not_assessable_join_gap"
    )
    assert (
        join_gap_row["missing_evidence"]
        == "join_gap_seed_audit_sample_not_in_cells"
    )
    assert (
        join_gap_row["source_warnings"]
        == "join_gap_seed_audit_sample_not_in_cells"
    )

    summary = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
    assert summary["schema_version"] == "backfill_evidence_reconciliation_v0"
    assert summary["validation_label"] == "diagnostic_only"
    assert summary["matrix_contract_changed"] is False
    assert summary["product_behavior_changed"] is False
    assert summary["reconciliation_class_counts"]["not_assessable_join_gap"] == 1
    assert summary["missing_evidence_counts"][
        "join_gap_seed_audit_sample_not_in_cells"
    ] == 1


def test_html_gallery_is_table_first_accessible_and_safe(tmp_path: Path) -> None:
    html_path = tmp_path / "out" / "backfill_evidence_reconciliation_gallery.html"
    png_path = tmp_path / "evidence" / "plots" / "fam.png"
    png_path.parent.mkdir(parents=True)
    png_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    review_tsv = tmp_path / "alignment" / "alignment_review.tsv"
    review_tsv.parent.mkdir(parents=True)
    review_tsv.write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    malicious_family = 'FAM<script>alert("x")</script>'
    malicious_seed = (
        f"seed::{malicious_family}::mz=3::rt=3::window=2-4::ppm=10"
    )
    index = gallery.ReconciliationIndex(
        groups=(
            _group(
                malicious_family,
                "product_rejects_but_visual_supports",
                "review_only_visual_support",
                overlay_png_path=str(png_path),
                source_artifacts=("alignment_review.tsv", "alignment_cells.tsv"),
            ),
        ),
        representative_cells=(
            gallery.RepresentativeCell(
                feature_family_id=malicious_family,
                seed_group_id=malicious_seed,
                representative_roles=("seed_representative",),
                sample_stem='S1"><script>alert("sample")</script>',
                cell_status="rescued",
                representative_reason="escaped representative",
                source_row_key='FAM::S1"><script>',
            ),
        ),
        summary={
            "input_artifacts": {
                "alignment_review_tsv": str(review_tsv),
                "targeted_istd_benchmark_summary_tsv": str(review_tsv),
                "source_run_id": 'fixture"><script>alert("run")</script>',
            },
        },
        target_benchmark_contexts=(
            gallery.TargetBenchmarkContext(
                target_label="d3-5-medC",
                role="ISTD",
                active_tag="DNA_dR",
                status="FAIL",
                selected_feature_id="FAM000030",
                targeted_positive_count="8",
                untargeted_positive_count="8",
                coverage_minimum="8",
                failure_modes=("AREA_MISMATCH",),
                note='escaped"><script>alert("target")</script>',
            ),
        ),
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        index,
        output_paths={
            "groups_tsv": tmp_path / "backfill_evidence_reconciliation_groups.tsv",
            "representative_cells_tsv": tmp_path
            / "backfill_evidence_reconciliation_representative_cells.tsv",
            "summary_json": tmp_path / "backfill_evidence_reconciliation_summary.json",
        },
    )

    text = html_path.read_text(encoding="utf-8")
    png_href = os.path.relpath(png_path, html_path.parent).replace("\\", "/")
    review_href = os.path.relpath(review_tsv, html_path.parent).replace("\\", "/")
    assert '<html lang="zh-Hant">' in text
    assert '<header class="gallery-hero" aria-label="gallery introduction">' in text
    assert "Matrix-decision audit surface" in text
    assert "artifact consumer only" in text
    assert "does not write matrix" in text
    assert "Candidate is not a matrix write." in text
    assert "diagnostic_only" in text
    assert (
        "benchmark context only；target benchmark 可作驗收/定位 target context"
        in text
    )
    assert "d3-5-medC" in text
    assert "FAM000030" in text
    assert "AREA_MISMATCH" in text
    assert "&lt;script&gt;alert(&quot;target&quot;)&lt;/script&gt;" in text
    assert (
        "不會修改 alignment matrix、cells、review TSV、workbooks 或 product decisions"
        in text
    )
    assert ".review-table thead th {\n  position: sticky" in text
    assert ".review-table th {\n  position: sticky" not in text
    assert (
        ".review-table th,\n.review-table td {\n"
        "  padding: 9px 10px;\n"
        "  border-bottom-color: var(--line-soft);\n"
        "  text-align: center;\n"
        "  vertical-align: middle;"
        in text
    )
    assert (
        ".seed-cell .seed-summary,\n"
        ".seed-cell .seed-window {\n"
        "  color: var(--red);"
        in text
    )
    assert "max-width: 1090px" in text
    assert "width: 1084px" in text
    assert "margin: 0 auto" in text
    assert ".cell-family," in text
    assert ".cell-state," in text
    main_table_head = text[
        text.index('<table class="review-table"') :
        text.index("</thead>", text.index('<table class="review-table"'))
    ]
    assert '<th scope="col">rank</th>' in text
    assert '<th scope="col">priority</th>' not in text
    assert '<th scope="col">state</th>' in text
    assert '<th scope="col">issue</th>' in text
    assert '<th scope="col">product</th>' not in main_table_head
    assert '<th scope="col">evidence</th>' not in main_table_head
    assert "white-space: nowrap" in text
    assert "<details" in text
    assert '<caption id="galleryTableDescription">' in text
    assert (
        '<th class="cell-family family-context-cell" scope="row" '
        'colspan="4" data-label="family / hypothesis">'
    ) in text
    assert 'data-label="state"' in text
    assert 'data-label="issue"' in text
    assert 'data-label="top issue"' not in text
    assert "impact" in text
    assert (
        'aria-label="NL anchors are family detected required-tag anchors; '
        "Candidate-only is hypothesis candidate cells, not matrix-written; "
        "Dup is family duplicate-assigned cell context; "
        "Review is hypothesis provisional cell context. "
        'These are alignment cell provenance counts, not target benchmark coverage."'
        in text
    )
    assert 'data-label="impact"' in text
    assert "<dt>NL</dt>" in text
    assert "<dt>Candidate-only</dt>" in text
    assert '<th scope="col">chain</th>' in text
    assert 'data-label="chain"' in text
    assert 'data-detail-toggle=' in text
    assert 'aria-expanded="false"' in text
    assert '<tr class="detail-row"' in text
    assert '<td colspan="7">' in text
    assert "Family 是 pattern context" in text
    assert '<label for="categoryFilter">Focus</label>' in text
    assert '<option value="product_rows" selected>Review queue</option>' in text
    projection_filter_option = (
        '<option value="projection_accepts">Projected matrix writes</option>'
    )
    assert projection_filter_option not in text
    assert '<option value="">All rows</option>' in text
    assert '<option value="needs_review">Needs review</option>' in text
    assert (
        '<option value="accepted_supported">Evidence-supported rows</option>' in text
    )
    assert '<option value="debug_rows">Duplicate / audit debug</option>' in text
    assert 'data-result-count data-total-families="1"' in text
    assert "顯示 1 / 1 families" in text
    assert "single-anchor review" in text
    assert '<option value="product_rejects_but_visual_supports">' not in text
    assert 'data-category="needs_review product_rows"' in text
    assert "const focusFilter = document.querySelector('[data-filter-control]')" in text
    assert "const resultCount = document.querySelector('[data-result-count]')" in text
    assert "const sectionRows = Array.from" in text
    assert "row.dataset.category" in text
    assert 'class="artifact-strip"' in text
    assert '<details class="provenance-panel">' in text
    assert 'class="summary-item artifact input-artifacts"' not in text
    assert 'class="family-section-row"' in text
    assert 'class="seed-decision-row"' in text
    assert 'class="seed-table"' not in text
    assert 'class="seed-subdetails"' not in text
    assert "seed / request" in text
    assert "seed-index" not in text
    assert '<span class="seed-summary">1 seed request</span>' not in text
    assert '<span class="seed-summary">m/z 3 · RT 3</span>' in text
    assert '<span class="seed-window">window 2-4</span>' in text
    assert "1 seed · m/z 3 · RT 3" not in text
    assert "detail-hint" not in text
    assert 'class="detail-summary-grid"' in text
    assert "current product / evidence state" in text
    assert "hypothesis evidence / family context" in text
    assert "Single detected NL anchor" in text
    assert "Dup=family duplicate-assigned cell context" in text
    assert 'class="chain-item secondary-chain"' in text
    assert "provenance / benchmark" in text
    assert "seed request, target benchmark, source artifacts" in text
    assert "Hypothesis MS1 evidence" in text
    assert "Optional Candidate MS2 / review context" in text
    assert "representative cells" in text
    assert 'aria-modal="true"' in text
    assert 'aria-describedby="lightboxCaption"' in text
    assert 'class="lightbox-direct"' in text
    assert 'id="lightboxInterpretation"' in text
    assert "link.dataset.lightboxTitle" in text
    assert "link.dataset.lightboxInterpretation" in text
    assert "direct.href = link.href || link.dataset.lightboxSrc" in text
    assert "'.review-table > tbody > tr[data-family-row]'" in text
    assert "setDetailOpen(button, false)" in text
    assert f'data-lightbox-src="{png_href}"' in text
    assert f'href="{png_href}"' in text
    assert f'href="{review_href}"' in text
    assert str(review_tsv) in text
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in text
    assert "<script>alert" not in text
    assert 'S1&quot;&gt;&lt;script&gt;alert(&quot;sample&quot;)&lt;/script&gt;' in text


def test_html_gallery_caps_large_inconclusive_dom_but_keeps_action_rows(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    groups = [
        _group(
            "FAM_ACTION",
            "product_accepts_and_visual_supports",
            "review_only_visual_support",
            overlay_png_path=str(tmp_path / "action.png"),
        ),
        *(
            _group(
                f"FAM_INCON_{index:04d}",
                "evidence_inconclusive",
                "dependent_context_only",
            )
            for index in range(1600)
        ),
    ]
    gallery.write_reconciliation_gallery_html(
        html_path,
        gallery.ReconciliationIndex(groups=tuple(groups), representative_cells=()),
        output_paths={
            "groups_tsv": tmp_path / "groups.tsv",
            "representative_cells_tsv": tmp_path / "representatives.tsv",
            "summary_json": tmp_path / "summary.json",
        },
    )

    text = html_path.read_text(encoding="utf-8")
    assert "HTML 顯示 201 / 1601 groups" in text
    assert "完整機器索引仍在 groups TSV" in text
    assert "FAM_ACTION" in text
    assert "FAM_INCON_0000" in text
    assert "FAM_INCON_0199" in text
    assert "FAM_INCON_0200" not in text


def test_html_gallery_caps_large_missing_seed_dom_but_keeps_action_rows(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    groups = [
        _group(
            "FAM_ACTION",
            "product_accepts_and_visual_supports",
            "review_only_visual_support",
            overlay_png_path=str(tmp_path / "action.png"),
        ),
        *(
            _group(
                f"FAM_MISSING_SEED_{index:04d}",
                "not_assessable_missing_seed_provenance",
                "not_assessable",
            )
            for index in range(1600)
        ),
    ]
    gallery.write_reconciliation_gallery_html(
        html_path,
        gallery.ReconciliationIndex(groups=tuple(groups), representative_cells=()),
        output_paths={
            "groups_tsv": tmp_path / "groups.tsv",
            "representative_cells_tsv": tmp_path / "representatives.tsv",
            "summary_json": tmp_path / "summary.json",
        },
    )

    text = html_path.read_text(encoding="utf-8")
    assert "HTML 顯示 201 / 1601 groups" in text
    assert "低資訊量 rows" in text
    assert "FAM_ACTION" in text
    assert "FAM_MISSING_SEED_0000" in text
    assert "FAM_MISSING_SEED_0199" in text
    assert "FAM_MISSING_SEED_0200" not in text


def test_html_gallery_cap_keeps_projection_accept_rows(tmp_path: Path) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    projection_group = _group(
        "FAM_PROJECTION",
        "evidence_inconclusive",
        "dependent_context_only",
    )
    groups = [
        *(
            _group(
                f"FAM_INCON_{index:04d}",
                "evidence_inconclusive",
                "dependent_context_only",
            )
            for index in range(1600)
        ),
        projection_group,
    ]
    gallery.write_reconciliation_gallery_html(
        html_path,
        gallery.ReconciliationIndex(
            groups=tuple(groups),
            representative_cells=(),
            shadow_projection_cells=(
                gallery.ShadowProjectionCell(
                    feature_family_id="FAM_PROJECTION",
                    seed_group_id=projection_group.seed_group_id,
                    sample_stem="S_PROJECT",
                    current_raw_status="rescued",
                    current_production_status="review_rescue",
                    current_rescue_tier="review_rescue",
                    current_matrix_written=False,
                    current_matrix_value="",
                    current_blank_reason="review_required",
                    current_matrix_source="production_decision_snapshot",
                    review_rescued_cell=True,
                    shadow_decision="accept",
                    shadow_reasons=("product_authorized_same_peak_backfill",),
                    projected_matrix_written=True,
                    projected_matrix_value="123",
                    projection_authority="shadow_projection_only",
                ),
            ),
        ),
        output_paths={
            "groups_tsv": tmp_path / "groups.tsv",
            "representative_cells_tsv": tmp_path / "representatives.tsv",
            "summary_json": tmp_path / "summary.json",
        },
    )

    text = html_path.read_text(encoding="utf-8")
    assert "HTML 顯示 201 / 1601 groups" in text
    assert "FAM_PROJECTION" in text
    assert "S_PROJECT" in text
    assert "projection_accept" in text
    assert "FAM_INCON_0200" not in text


def test_html_gallery_projection_accept_requires_positive_projected_value(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    projection_group = _group(
        "FAM_STALE_PROJECTION",
        "evidence_inconclusive",
        "dependent_context_only",
    )
    groups = [
        *(
            _group(
                f"FAM_INCON_{index:04d}",
                "evidence_inconclusive",
                "dependent_context_only",
            )
            for index in range(1600)
        ),
        projection_group,
    ]
    gallery.write_reconciliation_gallery_html(
        html_path,
        gallery.ReconciliationIndex(
            groups=tuple(groups),
            representative_cells=(),
            shadow_projection_cells=(
                gallery.ShadowProjectionCell(
                    feature_family_id="FAM_STALE_PROJECTION",
                    seed_group_id=projection_group.seed_group_id,
                    sample_stem="S_PROJECT",
                    current_raw_status="rescued",
                    current_production_status="review_rescue",
                    current_rescue_tier="review_rescue",
                    current_matrix_written=False,
                    current_matrix_value="",
                    current_blank_reason="review_required",
                    current_matrix_source="production_decision_snapshot",
                    review_rescued_cell=True,
                    shadow_decision="accept",
                    shadow_reasons=("product_authorized_same_peak_backfill",),
                    projected_matrix_written=True,
                    projected_matrix_value="",
                    projection_authority="shadow_projection_only",
                ),
            ),
        ),
        output_paths={
            "groups_tsv": tmp_path / "groups.tsv",
            "representative_cells_tsv": tmp_path / "representatives.tsv",
            "summary_json": tmp_path / "summary.json",
        },
    )

    text = html_path.read_text(encoding="utf-8")
    assert "HTML 顯示 200 / 1601 groups" in text
    assert "FAM_STALE_PROJECTION" not in text


def test_html_gallery_projection_context_is_not_presented_as_matrix_write(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    group = _group(
        "FAM_REVIEW_ONLY",
        "product_rejects_but_visual_supports",
        "review_only_visual_support",
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        gallery.ReconciliationIndex(
            groups=(group,),
            representative_cells=(),
            shadow_projection_cells=(
                gallery.ShadowProjectionCell(
                    feature_family_id="FAM_REVIEW_ONLY",
                    seed_group_id=group.seed_group_id,
                    sample_stem="S_REVIEW",
                    current_raw_status="rescued",
                    current_production_status="review_rescue",
                    current_rescue_tier="review_rescue",
                    current_matrix_written=False,
                    current_matrix_value="",
                    current_blank_reason="backfill_ms1_pattern_not_supportive",
                    current_matrix_source="production_decision_snapshot",
                    review_rescued_cell=True,
                    shadow_decision="context",
                    shadow_reasons=("identity_supported_review",),
                    projected_matrix_written=False,
                    projected_matrix_value="123",
                    projection_authority="shadow_projection_only",
                    product_authority_chain="",
                ),
            ),
        ),
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    assert "review-only: 1 candidate cell" in text
    assert "結論：未寫入 matrix；1 個 cell 仍是 review-only candidate" in text
    assert "product-authorized standard-peak same-peak chain" in text
    assert "projection: context" not in text
    assert "white-space: nowrap" in text


def test_html_gallery_projection_context_current_write_is_not_review_only(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    group = _group(
        "FAM_ALREADY_WRITTEN",
        "product_accepts_and_visual_supports",
        "review_only_visual_support",
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        gallery.ReconciliationIndex(
            groups=(group,),
            representative_cells=(),
            shadow_projection_cells=(
                gallery.ShadowProjectionCell(
                    feature_family_id="FAM_ALREADY_WRITTEN",
                    seed_group_id=group.seed_group_id,
                    sample_stem="S_CURRENT",
                    current_raw_status="rescued",
                    current_production_status="accepted_rescue",
                    current_rescue_tier="accepted_rescue",
                    current_matrix_written=True,
                    current_matrix_value="123",
                    current_blank_reason="",
                    current_matrix_source="production_decision_snapshot",
                    review_rescued_cell=True,
                    shadow_decision="context",
                    shadow_reasons=("already_written_context",),
                    projected_matrix_written=True,
                    projected_matrix_value="123",
                    projection_authority="shadow_projection_only",
                    product_authority_chain="",
                ),
            ),
        ),
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    assert "already written: 1 current cell" in text
    assert "結論：目前 production snapshot 已寫入 1 個 value" in text
    assert "review-only: 1 candidate cell" not in text


def test_html_gallery_defaults_to_product_rows_not_loser_debug_rows(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    index = gallery.ReconciliationIndex(
        groups=(
            _group(
                "FAM_WIN",
                "product_accepts_and_visual_supports",
                "review_only_visual_support",
                include_in_primary_matrix=True,
                accepted_cell_count=8,
            ),
            _group(
                "FAM_LOSER",
                "evidence_inconclusive",
                "dependent_context_only",
                row_flags="family_consolidation_loser;duplicate_claim_pressure",
                duplicate_assigned_cell_count=8,
            ),
        ),
        representative_cells=(),
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        index,
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    assert "顯示 1 / 2 families" in text
    assert (
        'data-family="FAM_WIN" data-class="product_accepts_and_visual_supports" '
        'data-category="accepted_supported product_rows"'
    ) in text
    assert (
        'data-family="FAM_LOSER" data-class="evidence_inconclusive" '
        'data-category="needs_review debug_rows"'
    ) in text


def test_html_gallery_groups_seed_aliases_under_one_family_section(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    index = gallery.ReconciliationIndex(
        groups=(
            gallery.ReconciliationGroup(
                feature_family_id="FAM_DUP",
                seed_group_id="seed::FAM_DUP::mz=254.097::rt=13.3525::window=10-16::ppm=20",
                seed_group_basis="seed_audit",
                seed_mz="254.097",
                seed_rt="13.3525",
                seed_rt_window="10-16",
                seed_ppm="20",
                product_behavior_state="product_primary_backfilled",
                evidence_authority_state="human_visual_judgment_only",
                reconciliation_class="evidence_inconclusive",
                detected_cell_count=2,
                rescued_cell_count=6,
                top_blocker="review_required_neighboring_ms1_interference",
                overlay_png_path="plots/fam-dup-a.png",
            ),
            gallery.ReconciliationGroup(
                feature_family_id="FAM_DUP",
                seed_group_id="seed::FAM_DUP::mz=254.098::rt=13.1836::window=10-16::ppm=20",
                seed_group_basis="seed_audit",
                seed_mz="254.098",
                seed_rt="13.1836",
                seed_rt_window="10-16",
                seed_ppm="20",
                product_behavior_state="product_primary_backfilled",
                evidence_authority_state="human_visual_judgment_only",
                reconciliation_class="evidence_inconclusive",
                detected_cell_count=2,
                rescued_cell_count=6,
                top_blocker="review_required_neighboring_ms1_interference",
                overlay_png_path="plots/fam-dup-b.png",
            ),
        ),
        representative_cells=(),
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        index,
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    assert text.count('data-family="FAM_DUP"') == 1
    assert "2 seed groups" in text
    assert '<span class="seed-summary">m/z 254.097 · RT 13.3525</span>' in text
    assert '<span class="seed-summary">m/z 254.098 · RT 13.1836</span>' in text
    assert '<span class="seed-window">window 10-16</span>' in text
    assert 'class="family-section-row"' in text
    assert text.count('class="seed-decision-row"') == 2
    assert 'class="seed-table"' not in text
    assert 'class="seed-subdetails"' not in text
    assert 'data-family-section="FAM_DUP"' in text
    assert ">H1</span>" not in text
    assert ">H2</span>" not in text
    assert ">H1 family context<" in text
    assert ">H2 family context<" in text
    assert ">PNG 1<" not in text
    assert ">PNG 2<" not in text
    assert "seed / request" in text
    assert "seed::FAM_DUP::mz=254.097" in text
    assert "seed::FAM_DUP::mz=254.098" in text


def test_html_gallery_collapses_consolidated_seed_aliases_to_one_decision_row(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    groups = tuple(
        gallery.ReconciliationGroup(
            feature_family_id="FAM_ALIAS",
            seed_group_id=(
                f"seed::FAM_ALIAS::mz=249.086::rt={rt}::window={window}::ppm=20"
            ),
            seed_group_basis="seed_audit",
            seed_mz="249.086",
            seed_rt=rt,
            seed_rt_window=window,
            seed_ppm="20",
            product_behavior_state="product_rescued_context_only",
            evidence_authority_state="dependent_context_only",
            reconciliation_class="evidence_inconclusive",
            include_in_primary_matrix=True,
            detected_cell_count=1,
            rescued_cell_count=rescued,
            top_support_component="seed_request_provenance",
            family_evidence="primary_family_consolidated;family_count=4",
        )
        for rt, window, rescued in (
            ("15.2091", "12.2091-18.2091", 2),
            ("15.0063", "12.0063-18.0063", 1),
            ("15.4426", "12.4426-18.4426", 1),
        )
    )
    index = gallery.ReconciliationIndex(
        groups=groups,
        representative_cells=(),
        shadow_projection_cells=(
            gallery.ShadowProjectionCell(
                feature_family_id="FAM_ALIAS",
                seed_group_id=groups[1].seed_group_id,
                sample_stem="S_ACCEPT_ALIAS_2",
                current_raw_status="rescued",
                current_production_status="review_rescue",
                current_rescue_tier="review_rescue",
                current_matrix_written=False,
                current_matrix_value="",
                current_blank_reason="backfill_ms1_pattern_blocked",
                current_matrix_source="production_decision_snapshot",
                review_rescued_cell=True,
                shadow_decision="accept",
                shadow_reasons=("product_authorized_same_peak_backfill",),
                shadow_warnings=("same_peak_multi_claim",),
                projected_matrix_written=True,
                projected_matrix_value="1200",
                projection_authority="shadow_projection_only",
                product_authority_chain=(
                    "MS1:product_authorized:supportive:trace_constellation | "
                    "candidateMS2(optional):product_authorized:supportive:"
                    "sample_boundary_aligned | "
                    "same_peak_reason:"
                    "family_ms1_overlay_anchor_peak_own_max_shape_supported"
                ),
                detected_anchor_count="1",
                request_window_overlap="TRUE",
                local_global_ratio="0.42",
                evidence_gate_status="visual_support",
                overlay_png_path="plots/fam-alias-projection.png",
            ),
        ),
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        index,
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    assert "1 MS1 hypothesis" not in text
    assert "3 seed aliases" in text
    assert "seed aliases collapsed under one product hypothesis" in text
    assert text.count('class="seed-decision-row consolidated-seed-row"') == 1
    assert ">hypothesis H1</span>" not in text
    assert ">seed 1</span>" not in text
    assert "RT 15.0063-15.4426" in text
    assert "no consolidated overlay" in text
    assert "seed::FAM_ALIAS::mz=249.086::rt=15.2091" in text
    assert "seed::FAM_ALIAS::mz=249.086::rt=15.0063" in text
    assert "seed::FAM_ALIAS::mz=249.086::rt=15.4426" in text
    assert 'data-category="needs_review product_rows projection_accepts"' in text
    assert '<option value="projection_accepts">Projected matrix writes</option>' in text
    assert "projection_accept" in text
    assert "projected_new_write" in text
    assert "Projection write cells" in text
    assert "S_ACCEPT_ALIAS_2" in text
    assert "RT 15.0063" in text
    assert "blank</span> -> <span" in text
    assert "product_authorized_same_peak_backfill" in text
    assert "same_peak_multi_claim" in text
    assert "MS1 product rule / optional context chain" in text
    assert "MS1:product_authorized:supportive:trace_constellation" in text


def test_html_gallery_shows_one_main_overlay_for_consolidated_seed_aliases(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    groups = tuple(
        gallery.ReconciliationGroup(
            feature_family_id="FAM_ALIAS_OVERLAY",
            seed_group_id=(
                f"seed::FAM_ALIAS_OVERLAY::mz=287.166::rt={rt}::"
                f"window={window}::ppm=20"
            ),
            seed_group_basis="seed_audit",
            seed_mz="287.166",
            seed_rt=rt,
            seed_rt_window=window,
            seed_ppm="20",
            product_behavior_state="product_primary_backfilled",
            evidence_authority_state="human_visual_judgment_only",
            reconciliation_class="evidence_inconclusive",
            include_in_primary_matrix=True,
            detected_cell_count=2,
            rescued_cell_count=3,
            top_blocker="review_required_neighboring_ms1_interference",
            overlay_png_path=png,
            family_evidence="primary_family_consolidated;family_count=2",
        )
        for rt, window, png in (
            ("10.6501", "7.65008-13.6501", "plots/fam-alias-a.png"),
            ("11.1175", "8.1175-14.1175", "plots/fam-alias-b.png"),
        )
    )
    index = gallery.ReconciliationIndex(groups=groups, representative_cells=())

    gallery.write_reconciliation_gallery_html(
        html_path,
        index,
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    main_row = text[
        text.index('class="seed-decision-row consolidated-seed-row"') :
        text.index('class="detail-row"')
    ]
    assert "family context" in main_row
    assert "2 alias overlays share the same MS1 family context" in main_row
    assert "seed 1 PNG" not in main_row
    assert "seed 2 PNG" not in main_row


def test_html_gallery_explains_high_alias_count_missing_overlay_batch(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    groups = tuple(
        gallery.ReconciliationGroup(
            feature_family_id="FAM_HIGH_ALIAS",
            seed_group_id=(
                f"seed::FAM_HIGH_ALIAS::mz=246.117::rt=15.{index:04d}::"
                f"window=12.{index:04d}-18.{index:04d}::ppm=20"
            ),
            seed_group_basis="seed_audit",
            seed_mz="246.117",
            seed_rt=f"15.{index:04d}",
            seed_rt_window=f"12.{index:04d}-18.{index:04d}",
            seed_ppm="20",
            product_behavior_state="product_primary_backfilled",
            evidence_authority_state="dependent_context_only",
            reconciliation_class="evidence_inconclusive",
            include_in_primary_matrix=True,
            detected_cell_count=26,
            rescued_cell_count=5,
            top_support_component="seed_request_provenance",
            family_evidence="primary_family_consolidated;family_count=5",
        )
        for index in range(1, 6)
    )
    index = gallery.ReconciliationIndex(
        groups=groups,
        representative_cells=(),
        summary={
            "input_artifacts": {
                "overlay_batch_summary_tsvs": ["family_ms1_overlay_batch_summary.tsv"],
            },
        },
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        index,
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    assert "5 seed aliases · high alias count" in text
    assert "Reviewer readout" in text
    assert "not 5 independent peak decisions" in text
    assert "not in supplied overlay batch" in text
    assert "High alias count" in text


def test_html_gallery_marks_shared_family_overlay_without_fake_seed_pngs(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    shared_png = "plots/shared-family.png"
    index = gallery.ReconciliationIndex(
        groups=(
            gallery.ReconciliationGroup(
                feature_family_id="FAM_SHARED",
                seed_group_id=(
                    "seed::FAM_SHARED::mz=254.097::rt=13.3525::window=10-16::ppm=20"
                ),
                seed_group_basis="seed_audit",
                seed_mz="254.097",
                seed_rt="13.3525",
                seed_rt_window="10-16",
                seed_ppm="20",
                product_behavior_state="product_primary_backfilled",
                evidence_authority_state="review_only_visual_support",
                reconciliation_class="product_accepts_and_visual_supports",
                detected_cell_count=2,
                rescued_cell_count=6,
                top_support_component="ms1_shape_supports_family_backfill",
                overlay_png_path=shared_png,
            ),
            gallery.ReconciliationGroup(
                feature_family_id="FAM_SHARED",
                seed_group_id=(
                    "seed::FAM_SHARED::mz=254.098::rt=13.1836::window=10-16::ppm=20"
                ),
                seed_group_basis="seed_audit",
                seed_mz="254.098",
                seed_rt="13.1836",
                seed_rt_window="10-16",
                seed_ppm="20",
                product_behavior_state="product_primary_backfilled",
                evidence_authority_state="review_only_visual_support",
                reconciliation_class="product_accepts_and_visual_supports",
                detected_cell_count=2,
                rescued_cell_count=6,
                top_support_component="ms1_shape_supports_family_backfill",
                overlay_png_path=shared_png,
            ),
        ),
        representative_cells=(),
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        index,
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    assert "shared family context" in text
    assert "not seed-specific" in text
    assert ">PNG 1<" not in text
    assert ">PNG 2<" not in text
    assert "same family context as H1" in text


def test_html_gallery_rejects_dangerous_png_schemes(tmp_path: Path) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    index = gallery.ReconciliationIndex(
        groups=(
            _group(
                "FAM_JS",
                "product_rejects_but_visual_supports",
                "review_only_visual_support",
                overlay_png_path="javascript:alert(1)",
            ),
            _group(
                "FAM_CONTROL",
                "product_rejects_but_visual_supports",
                "review_only_visual_support",
                overlay_png_path="java\nscript:alert(2)",
            ),
            _group(
                "FAM_DATA",
                "product_rejects_but_visual_supports",
                "review_only_visual_support",
                overlay_png_path="data:text/html,<script>alert(3)</script>",
            ),
        ),
        representative_cells=(),
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        index,
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    assert 'data-lightbox-src="' not in text
    assert 'href="javascript:' not in text.lower()
    assert "java\nscript:alert" not in text
    assert "data:text/html" not in text
    assert text.count("overlay artifact not supplied") >= 3


def test_html_gallery_escapes_shadow_projection_product_behavior_chain(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "backfill_evidence_reconciliation_gallery.html"
    family = 'FAM_PROJ"><script>alert("family")</script>'
    group = _group(
        family,
        "product_rejects_but_visual_supports",
        "review_only_visual_support",
    )
    index = gallery.ReconciliationIndex(
        groups=(group,),
        representative_cells=(),
        shadow_projection_cells=(
            gallery.ShadowProjectionCell(
                feature_family_id=family,
                seed_group_id=group.seed_group_id,
                sample_stem='S_PROJ"><script>alert("projection")</script>',
                current_raw_status="rescued",
                current_production_status="review_rescue",
                current_rescue_tier="review_rescue",
                current_matrix_written=False,
                current_matrix_value="",
                current_blank_reason="backfill_ms1_pattern_blocked",
                current_matrix_source="production_decision_snapshot",
                review_rescued_cell=True,
                shadow_decision="accept",
                shadow_reasons=('reason"><script>alert("reason")</script>',),
                shadow_warnings=('warn"><script>alert("warn")</script>',),
                projected_matrix_written=True,
                projected_matrix_value="123",
                projection_authority="shadow_projection_only",
                product_authority_chain=(
                    'MS1:product_authorized:supportive:trace_constellation:'
                    'reason"><script>alert("chain")</script>'
                ),
            ),
        ),
    )

    gallery.write_reconciliation_gallery_html(
        html_path,
        index,
        output_paths={},
    )

    text = html_path.read_text(encoding="utf-8")
    assert "MS1 product rule / optional context chain" in text
    assert "projection_accept" in text
    assert "projected_new_write" in text
    assert "&lt;script&gt;alert(&quot;chain&quot;)&lt;/script&gt;" in text
    assert (
        "S_PROJ&quot;&gt;&lt;script&gt;alert(&quot;projection&quot;)&lt;/script&gt;"
        in text
    )
    assert "&lt;script&gt;alert(&quot;reason&quot;)&lt;/script&gt;" in text
    assert "&lt;script&gt;alert(&quot;warn&quot;)&lt;/script&gt;" in text
    assert "<script>alert" not in text


def test_cli_integrates_shadow_policy_html_without_group_schema_change(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    family = "FAM_SHADOW"
    seed_group_id = (
        "seed::FAM_SHADOW::mz=269.145::rt=10.0000::"
        "window=9.0000-11.0000::ppm=10"
    )
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        [_review_row(family, rescued="3")],
        ALIGNMENT_REVIEW_COLUMNS,
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            _cell_row(family, "S_DET", "detected"),
            _cell_row(
                family,
                "S_FILL",
                "rescued",
                gap_fill_state="gap_fill_rescued",
            ),
            _cell_row(family, "S_WOULD", "rescued"),
            _cell_row(family, "S_BLOCK", "rescued"),
        ],
        ALIGNMENT_CELLS_COLUMNS,
    )
    _write_tsv(
        alignment_dir / "alignment_owner_backfill_seed_audit.tsv",
        [
            _seed_row(family, "S_FILL"),
            _seed_row(family, "S_WOULD"),
            _seed_row(family, "S_BLOCK"),
        ],
        ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS,
    )
    shadow_tsv = alignment_dir / "backfill_shadow_policy_cells.tsv"
    _write_tsv(
        shadow_tsv,
        [
            _shadow_row(
                family,
                seed_group_id,
                "S_FILL",
                current="filled_now",
                decision="fill_now",
                reason="product_already_writes_rescue",
                own_max="0.92",
            ),
            _shadow_row(
                family,
                seed_group_id,
                "S_WOULD",
                current="review_only",
                decision="would_fill_under_ms1_rt_policy",
                reason="ms1_rt_shadow_supported",
                own_max="0.875",
            ),
            _shadow_row(
                family,
                seed_group_id,
                "S_BLOCK",
                current="review_only",
                decision="blocked",
                reason="own_max_shape_at_or_below_threshold",
                own_max="0.5",
            ),
        ],
        BACKFILL_SHADOW_POLICY_COLUMNS,
    )
    output_dir = tmp_path / "out"

    code = cli.main(
        [
            "--alignment-review-tsv",
            str(alignment_dir / "alignment_review.tsv"),
            "--alignment-cells-tsv",
            str(alignment_dir / "alignment_cells.tsv"),
            "--backfill-seed-audit-tsv",
            str(alignment_dir / "alignment_owner_backfill_seed_audit.tsv"),
            "--shadow-policy-cells-tsv",
            str(shadow_tsv),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    assert _read_header(output_dir / "backfill_evidence_reconciliation_groups.tsv") == [
        *EXPECTED_GROUP_COLUMNS,
    ]
    html = (
        output_dir / "backfill_evidence_reconciliation_gallery.html"
    ).read_text(encoding="utf-8")
    assert "MS1+RT shadow policy" in html
    assert 'class="chain-item shadow-policy-chain"' in html
    assert 'class="shadow-policy-table-wrap"' in html
    assert "shadow: fill 1 · would 1 · block 1" in html
    assert "would_fill_under_ms1_rt_policy" in html
    assert "needs_ms2_or_policy" not in html
    assert "own-max=0.875" in html
    assert "S_WOULD" in html
    assert "backfill_shadow_policy_cells.tsv" in html
    summary = json.loads(
        (output_dir / "backfill_evidence_reconciliation_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert summary["product_behavior_changed"] is False
    assert summary["matrix_contract_changed"] is False
    assert summary["input_artifacts"]["shadow_policy_cells_tsv"] == str(shadow_tsv)


def test_cli_integrates_shadow_production_projection_without_group_schema_change(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    family = "FAM_PROJECTION"
    seed_group_id = (
        "seed::FAM_PROJECTION::mz=269.145::rt=10.0000::"
        "window=9.0000-11.0000::ppm=10"
    )
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        [_review_row(family, rescued="2")],
        ALIGNMENT_REVIEW_COLUMNS,
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            _cell_row(family, "S_DET", "detected"),
            _cell_row(family, "S_ACCEPT", "rescued"),
            _cell_row(family, "S_BLOCK", "rescued"),
        ],
        ALIGNMENT_CELLS_COLUMNS,
    )
    _write_tsv(
        alignment_dir / "alignment_owner_backfill_seed_audit.tsv",
        [
            _seed_row(family, "S_ACCEPT"),
            _seed_row(family, "S_BLOCK"),
        ],
        ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS,
    )
    projection_tsv = alignment_dir / "shadow_production_projection_cells.tsv"
    _write_tsv(
        projection_tsv,
        [
            _projection_row(
                family,
                seed_group_id,
                "S_DET",
                current_status="detected",
                current_written="TRUE",
                shadow_decision="context",
                projected_written="TRUE",
                reasons="already_written_current_matrix",
            ),
            _projection_row(
                family,
                seed_group_id,
                "S_ACCEPT",
                current_status="review_rescue",
                current_written="FALSE",
                shadow_decision="accept",
                projected_written="TRUE",
                reasons="request_window_shadow_projection_candidate",
                warnings="same_peak_multi_claim",
                authority_chain=(
                    "MS1:product_authorized:supportive:trace_constellation | "
                    "candidateMS2(optional):product_authorized:partial_support:"
                    "sample_candidate_aligned"
                ),
            ),
            _projection_row(
                family,
                seed_group_id,
                "S_BLOCK",
                current_status="review_rescue",
                current_written="FALSE",
                shadow_decision="block",
                projected_written="FALSE",
                reasons="neighboring_interference_hard_block",
            ),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    overlay_tsv = alignment_dir / "family_ms1_overlay_batch_summary.tsv"
    _write_tsv(
        overlay_tsv,
        [
            {
                "feature_family_id": family,
                "family_verdict": "ms1_shape_supports_family_backfill",
                "png_path": "plots/fam-projection.png",
            },
        ],
        ("feature_family_id", "family_verdict", "png_path"),
    )
    output_dir = tmp_path / "out"

    code = cli.main(
        [
            "--alignment-review-tsv",
            str(alignment_dir / "alignment_review.tsv"),
            "--alignment-cells-tsv",
            str(alignment_dir / "alignment_cells.tsv"),
            "--backfill-seed-audit-tsv",
            str(alignment_dir / "alignment_owner_backfill_seed_audit.tsv"),
            "--shadow-production-projection-cells-tsv",
            str(projection_tsv),
            "--overlay-batch-summary-tsv",
            str(overlay_tsv),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    assert _read_header(output_dir / "backfill_evidence_reconciliation_groups.tsv") == [
        *EXPECTED_GROUP_COLUMNS,
    ]
    html = (
        output_dir / "backfill_evidence_reconciliation_gallery.html"
    ).read_text(encoding="utf-8")
    assert "Shadow production projection" in html
    assert "projection: current 1 · write 1 · block 1 · +1 matrix" in html
    assert "current decision" in html
    assert "projected decision" in html
    assert "same_peak_multi_claim" in html
    assert "MS1 product rule / optional context chain" in html
    assert "projection_accept" in html
    assert "projected_new_write" in html
    assert "MS1:product_authorized:supportive:trace_constellation" in html
    assert "neighboring_interference_hard_block" in html
    assert "shadow_production_projection_cells.tsv" in html
    summary = json.loads(
        (output_dir / "backfill_evidence_reconciliation_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert summary["product_behavior_changed"] is False
    assert summary["matrix_contract_changed"] is False
    assert summary["shadow_projection_decision_counts"] == {
        "accept": 1,
        "block": 1,
        "context": 1,
    }
    assert summary["input_artifacts"]["shadow_projection_cells_tsv"] == str(
        projection_tsv,
    )
    assert summary["input_artifacts"]["backfill_seed_audit_sha256"]
    overlay_hashes = summary["input_artifacts"]["overlay_batch_summary_hashes"]
    assert len(overlay_hashes) == 1
    assert overlay_hashes[0]["path"] == str(overlay_tsv)
    assert overlay_hashes[0]["sha256"]


def test_cli_renders_target_benchmark_context_without_group_schema_change(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    family = "FAM_TARGET"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        [_review_row(family, include_in_primary_matrix="TRUE")],
        ALIGNMENT_REVIEW_COLUMNS,
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            _cell_row(family, "S1", "detected"),
            _cell_row(family, "S2", "rescued"),
        ],
        ALIGNMENT_CELLS_COLUMNS,
    )
    _write_tsv(
        alignment_dir / "alignment_owner_backfill_seed_audit.tsv",
        [_seed_row(family, "S2")],
        ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS,
    )
    benchmark_tsv = alignment_dir / "targeted_istd_benchmark_summary.tsv"
    benchmark_columns = (
        "target_label",
        "role",
        "active_tag",
        "targeted_positive_count",
        "clean_targeted_positive_count",
        "targeted_review_positive_count",
        "targeted_review_count",
        "targeted_negative_count",
        "coverage_denominator_count",
        "targeted_total_count",
        "targeted_mean_rt",
        "candidate_match_count",
        "primary_match_count",
        "primary_feature_ids",
        "selected_feature_id",
        "untargeted_positive_count",
        "coverage_minimum",
        "paired_area_n",
        "log_area_pearson",
        "log_area_spearman",
        "family_mean_rt_delta_min",
        "sample_rt_pair_n",
        "sample_rt_median_abs_delta_min",
        "sample_rt_p95_abs_delta_min",
        "status",
        "failure_modes",
        "targeted_reliability_mode",
        "targeted_reliability_warning_modes",
        "note",
    )
    _write_tsv(
        benchmark_tsv,
        [
            {
                "target_label": "d3-5-medC",
                "role": "ISTD",
                "active_tag": "TRUE",
                "targeted_positive_count": "8",
                "primary_feature_ids": family,
                "selected_feature_id": family,
                "untargeted_positive_count": "8",
                "coverage_minimum": "7",
                "status": "PASS",
                "failure_modes": "",
                "note": "strict gate passed",
            },
        ],
        benchmark_columns,
    )
    output_dir = tmp_path / "out"

    code = cli.main(
        [
            "--alignment-review-tsv",
            str(alignment_dir / "alignment_review.tsv"),
            "--alignment-cells-tsv",
            str(alignment_dir / "alignment_cells.tsv"),
            "--backfill-seed-audit-tsv",
            str(alignment_dir / "alignment_owner_backfill_seed_audit.tsv"),
            "--targeted-istd-benchmark-summary-tsv",
            str(benchmark_tsv),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    assert _read_header(output_dir / "backfill_evidence_reconciliation_groups.tsv") == [
        *EXPECTED_GROUP_COLUMNS,
    ]
    html = (
        output_dir / "backfill_evidence_reconciliation_gallery.html"
    ).read_text(encoding="utf-8")
    assert "Target benchmark" in html
    assert "context only · PASS 1" in html
    assert "target context d3-5-medC PASS" in html
    assert "d3-5-medC PASS · untargeted 8/targeted 8" in html
    assert "benchmark context only" in html
    assert "untargeted 8/targeted 8" in html
    assert '<th scope="col">note</th>' in html
    assert "strict gate passed" in html
    assert "targeted_istd_benchmark_summary.tsv" in html
    summary = json.loads(
        (output_dir / "backfill_evidence_reconciliation_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert summary["target_benchmark_context_counts"] == {"PASS": 1}
    assert summary["input_artifacts"]["targeted_istd_benchmark_summary_tsv"] == str(
        benchmark_tsv,
    )


def test_cli_writes_outputs_without_raw_or_dll_contract(tmp_path: Path, capsys) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        [_review_row("FAM_CLI")],
        ALIGNMENT_REVIEW_COLUMNS,
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [_cell_row("FAM_CLI", "S1", "rescued")],
        ALIGNMENT_CELLS_COLUMNS,
    )
    _write_tsv(
        alignment_dir / "alignment_owner_backfill_seed_audit.tsv",
        [_seed_row("FAM_CLI", "S1")],
        ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS,
    )
    _write_tsv(
        alignment_dir / "alignment_production_candidate_gate.tsv",
        [
            {
                "feature_family_id": "FAM_CLI",
                "candidate_gate_status": "production_candidate",
                "support_components": "validated_tier2_trace_evidence",
                "challenge_blockers": "",
            },
        ],
        (
            "feature_family_id",
            "candidate_gate_status",
            "support_components",
            "challenge_blockers",
        ),
    )
    _write_tsv(
        alignment_dir / "alignment_matrix.tsv",
        [{"Mz": "269.145", "RT": "10.0", "S1": "1200"}],
        ("Mz", "RT", "S1"),
    )
    shift_tsv = alignment_dir / "source_family_best_shift_summary.tsv"
    _write_tsv(
        shift_tsv,
        [
            {
                "feature_family_id": "FAM_CLI",
                "source_family": "FAM_REF",
                "is_reference": "TRUE",
                "shift_basis": "median_shape_correlation",
                "shift_to_reference_sec": "0.0",
                "shape_similarity_to_reference_after_group_shift": "1.000",
            },
        ],
        (
            "feature_family_id",
            "source_family",
            "is_reference",
            "shift_basis",
            "shift_to_reference_sec",
            "shape_similarity_to_reference_after_group_shift",
        ),
    )
    standard_gate_tsv = alignment_dir / "shift_aware_standard_peak_gate_calibration.tsv"
    _write_tsv(
        standard_gate_tsv,
        [
            {
                "feature_family_id": "FAM_CLI",
                "standard_peak_gate_call": "standard_peak_gate_supported",
                "standard_peak_gate_reasons": (
                    "shift_aware_same_pattern_supported;"
                    "family_overlay_gaussian_smoothed_standard_peak_supported"
                ),
                "standard_peak_gate_blockers": "",
                "calibration_outcome": "true_positive",
                "min_shape_r_after_best_shift": "0.990",
                "max_abs_shift_sec": "1.20",
            },
        ],
        (
            "feature_family_id",
            "standard_peak_gate_call",
            "standard_peak_gate_reasons",
            "standard_peak_gate_blockers",
            "calibration_outcome",
            "min_shape_r_after_best_shift",
            "max_abs_shift_sec",
        ),
    )
    output_dir = tmp_path / "out"

    code = cli.main(
        [
            "--alignment-review-tsv",
            str(alignment_dir / "alignment_review.tsv"),
            "--alignment-cells-tsv",
            str(alignment_dir / "alignment_cells.tsv"),
            "--backfill-seed-audit-tsv",
            str(alignment_dir / "alignment_owner_backfill_seed_audit.tsv"),
            "--candidate-gate-tsv",
            str(alignment_dir / "alignment_production_candidate_gate.tsv"),
            "--alignment-matrix-tsv",
            str(alignment_dir / "alignment_matrix.tsv"),
            "--shift-aware-same-pattern-tsv",
            str(shift_tsv),
            "--shift-aware-standard-peak-gate-tsv",
            str(standard_gate_tsv),
            "--output-dir",
            str(output_dir),
            "--source-run-id",
            "fixture-run",
        ],
    )

    assert code == 0
    assert (output_dir / "backfill_evidence_reconciliation_groups.tsv").is_file()
    assert (
        output_dir / "backfill_evidence_reconciliation_representative_cells.tsv"
    ).is_file()
    assert (output_dir / "backfill_evidence_reconciliation_summary.json").is_file()
    assert (output_dir / "backfill_evidence_reconciliation_gallery.html").is_file()
    assert "backfill evidence reconciliation groups TSV" in capsys.readouterr().out
    summary = json.loads(
        (output_dir / "backfill_evidence_reconciliation_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert summary["input_artifacts"]["shift_aware_same_pattern_tsvs"] == [
        str(shift_tsv),
    ]
    assert summary["input_artifacts"]["shift_aware_standard_peak_gate_tsvs"] == [
        str(standard_gate_tsv),
    ]

    with pytest.raises(SystemExit):
        cli._parse_args(["--raw-dir", "RAW", "--dll-dir", "DLL"])
    with pytest.raises(SystemExit) as help_exit:
        cli._parse_args(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "--raw-dir" not in help_text
    assert "--dll-dir" not in help_text
    assert "--shadow-policy-cells-tsv" in help_text
    assert "--shadow-production-projection-cells-tsv" in help_text
    assert "--targeted-istd-benchmark-summary-tsv" in help_text
    assert "--retained-backfill-evidence-gate-tsv" in help_text
    assert "--shift-aware-same-pattern-tsv" in help_text
    assert "--shift-aware-standard-peak-gate-tsv" in help_text


def test_cli_reports_missing_required_inputs(tmp_path: Path, capsys) -> None:
    code = cli.main(
        [
            "--alignment-review-tsv",
            str(tmp_path / "missing_review.tsv"),
            "--alignment-cells-tsv",
            str(tmp_path / "missing_cells.tsv"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert code == 2
    assert "Required TSV not found" in capsys.readouterr().err


def _blank_row(columns: tuple[str, ...]) -> dict[str, str]:
    return {column: "" for column in columns}


def _review_row(
    family: str,
    *,
    identity_decision: str = "provisional_discovery",
    include_in_primary_matrix: str = "FALSE",
    row_flags: str = "single_detected_seed;provisional_retention_candidate",
    detected: str = "1",
    rescued: str = "2",
) -> dict[str, str]:
    row = _blank_row(ALIGNMENT_REVIEW_COLUMNS)
    row.update(
        {
            "feature_family_id": family,
            "group_hypothesis_id": f"{family}::group",
            "public_family_id": family,
            "group_construction_role": "single_detected_seed",
            "group_delivery_role": "review",
            "group_membership_source": "owner_family",
            "family_center_mz": "269.145",
            "family_center_rt": "10.0000",
            "detected_count": detected,
            "accepted_cell_count": str(int(detected) + int(rescued)),
            "accepted_rescue_count": rescued,
            "quantifiable_detected_count": detected,
            "quantifiable_rescue_count": rescued,
            "review_rescue_count": "0",
            "identity_decision": identity_decision,
            "identity_confidence": "review_only",
            "primary_evidence": "owner_backfill_context",
            "identity_reason": "owner_backfill_context",
            "include_in_primary_matrix": include_in_primary_matrix,
            "row_flags": row_flags,
            "reason": "fixture",
        },
    )
    return row


def _cell_row(
    family: str,
    sample: str,
    status: str,
    *,
    scan_support: str = "0.80",
    apex_rt: str = "10.10",
    gap_fill_state: str | None = None,
    gap_fill_reason: str | None = None,
) -> dict[str, str]:
    row = _blank_row(ALIGNMENT_CELLS_COLUMNS)
    resolved_gap_state = (
        gap_fill_state
        if gap_fill_state is not None
        else ("owner_backfill" if status == "rescued" else "observed")
    )
    resolved_gap_reason = (
        gap_fill_reason
        if gap_fill_reason is not None
        else ("owner_backfill" if status == "rescued" else "")
    )
    apex = float(apex_rt) if apex_rt else 10.10
    row.update(
        {
            "feature_family_id": family,
            "group_hypothesis_id": f"{family}::group",
            "public_family_id": family,
            "group_construction_role": "single_detected_seed",
            "group_delivery_role": "review",
            "group_membership_source": "owner_family",
            "gap_fill_state": resolved_gap_state,
            "gap_fill_reason": resolved_gap_reason,
            "sample_stem": sample,
            "status": status,
            "area": "1200.0",
            "primary_matrix_area": "1200.0" if status == "detected" else "",
            "primary_matrix_area_source": "detected" if status == "detected" else "",
            "primary_matrix_area_reason": "observed" if status == "detected" else "",
            "apex_rt": apex_rt,
            "peak_start_rt": f"{apex - 0.08:.4f}",
            "peak_end_rt": f"{apex + 0.08:.4f}",
            "rt_delta_sec": "0",
            "scan_support_score": scan_support,
            "reason": "fixture",
        },
    )
    return row


def _seed_row(
    family: str,
    sample: str,
    *,
    seed_mz: str = "269.145",
    seed_rt: str = "10.0000",
    rt_start: str = "9.0000",
    rt_end: str = "11.0000",
    ppm: str = "10",
) -> dict[str, str]:
    row = _blank_row(ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS)
    row.update(
        {
            "feature_family_id": family,
            "group_hypothesis_id": f"{family}::group",
            "public_family_id": family,
            "group_construction_role": "single_detected_seed",
            "group_delivery_role": "review",
            "group_membership_source": "owner_family",
            "gap_fill_state": "owner_backfill",
            "gap_fill_reason": "owner_backfill",
            "sample_stem": sample,
            "status": "rescued",
            "area": "1200.0",
            "apex_rt": seed_rt,
            "family_center_mz": "269.145",
            "family_center_rt": "10.0000",
            "backfill_seed_mz": seed_mz,
            "backfill_seed_rt": seed_rt,
            "backfill_request_rt_min": rt_start,
            "backfill_request_rt_max": rt_end,
            "backfill_request_ppm": ppm,
            "reason": "fixture",
        },
    )
    return row


def _shadow_row(
    family: str,
    seed_group_id: str,
    sample: str,
    *,
    current: str,
    decision: str,
    reason: str,
    production_gap: str = "",
    own_max: str = "",
) -> dict[str, str]:
    row = _blank_row(BACKFILL_SHADOW_POLICY_COLUMNS)
    row.update(
        {
            "schema_version": "backfill_shadow_policy_v0",
            "feature_family_id": family,
            "seed_group_id": seed_group_id,
            "sample_stem": sample,
            "current_product_cell_state": current,
            "shadow_policy_decision": decision,
            "decision_reason": reason,
            "production_gap": production_gap,
            "diagnostic_authority": "diagnostic_only",
            "seed_mz": "269.145",
            "seed_rt": "10.0000",
            "seed_rt_window": "9.0000-11.0000",
            "detected_cell_count": "1",
            "rescued_cell_count": "3",
            "cell_status": "rescued",
            "evidence_gate_status": "visual_support",
            "overlay_family_verdict": "ms1_shape_supports_family_backfill",
            "own_max_shape_supported_fraction": own_max,
            "absolute_trace_apex_cluster_fraction": "1",
            "support_components": "seed_request_provenance",
            "overlay_png_path": "plots/fam-shadow.png",
        },
    )
    return row


def _projection_row(
    family: str,
    seed_group_id: str,
    sample: str,
    *,
    current_status: str,
    current_written: str,
    shadow_decision: str,
    projected_written: str,
    reasons: str,
    warnings: str = "",
    authority_chain: str = "",
) -> dict[str, str]:
    row = _blank_row(SHADOW_PRODUCTION_PROJECTION_COLUMNS)
    row.update(
        {
            "schema_version": "shadow_production_projection_v1",
            "feature_family_id": family,
            "seed_group_id": seed_group_id,
            "sample_stem": sample,
            "current_raw_status": (
                "detected" if current_status == "detected" else "rescued"
            ),
            "current_production_status": current_status,
            "current_rescue_tier": (
                "review_rescue" if current_status == "review_rescue" else ""
            ),
            "current_matrix_written": current_written,
            "current_matrix_value": "1200" if current_written == "TRUE" else "",
            "current_blank_reason": (
                "" if current_written == "TRUE" else "backfill_ms1_pattern_blocked"
            ),
            "current_matrix_source": "production_decision_snapshot",
            "review_rescued_cell": (
                "TRUE" if current_status == "review_rescue" else "FALSE"
            ),
            "shadow_decision": shadow_decision,
            "shadow_reasons": reasons,
            "shadow_warnings": warnings,
            "projected_matrix_written": projected_written,
            "projected_matrix_value": "1200" if projected_written == "TRUE" else "",
            "projection_authority": "shadow_projection_only",
            "product_authority_chain": authority_chain,
            "seed_mz": "269.145",
            "seed_rt": "10.0000",
            "seed_rt_window": "9.0000-11.0000",
            "detected_anchor_count": "1",
            "rescued_cell_count": "2",
            "request_window_overlap": "TRUE",
            "local_global_ratio": "0.42",
            "cell_status": "rescued",
            "gap_fill_state": "not_filled" if warnings else "owner_backfill",
            "gap_fill_reason": (
                "not_requested_duplicate_loser" if warnings else "owner_backfill"
            ),
            "evidence_gate_status": "visual_support",
            "support_components": "seed_request_provenance",
            "hard_blockers": (
                reasons if shadow_decision == "block" else ""
            ),
            "overlay_verdict": "ms1_shape_supports_family_backfill",
            "overlay_png_path": "plots/fam-projection.png",
        },
    )
    return row


def _activation_delta_row(
    family: str,
    sample: str,
    *,
    effect: str,
    activation_status: str = "auto_activate",
    product_effect: str = "accept_label_or_rescue",
    activated_value: str = "1200",
) -> dict[str, str]:
    return {
        "activation_value_delta_schema_version": (
            "shared_peak_identity_activation_value_delta_v3"
        ),
        "feature_family_id": family,
        "candidate_container_id": family,
        "sample_id": sample,
        "peak_hypothesis_id": family,
        "activation_unit_scope": "peak_hypothesis",
        "activation_status": activation_status,
        "product_effect": product_effect,
        "contract_rule_id": "machine_observed_sufficient_positive_identity",
        "original_matrix_value": "",
        "activated_matrix_value": activated_value,
        "matrix_value_kind": "backfill_activation",
        "matrix_value_source": "activation_values_tsv",
        "matrix_value_source_field": "projected_matrix_value",
        "matrix_value_source_detail": "standard_peak_shadow_projection",
        "matrix_value_source_artifact_schema_version": (
            "shadow_production_projection_v1"
        ),
        "matrix_value_source_artifact_sha256": "fixture",
        "matrix_value_source_row_sha256": "fixture-row",
        "source_cell_status": "rescued",
        "source_cell_area": activated_value,
        "matrix_value_effect": effect,
        "value_changed": "TRUE",
        "activation_reason": (
            "standard_peak_shift_aware_ms1_same_peak_product_authorized"
        ),
    }


def _overlay_trace(
    sample: str,
    status: str,
    apex_rt: float,
    intensity: list[float],
) -> dict[str, object]:
    return {
        "sample_stem": sample,
        "status": status,
        "cell_apex_rt": apex_rt,
        "cell_start_rt": apex_rt - 0.08,
        "cell_end_rt": apex_rt + 0.08,
        "cell_height": max(intensity),
        "local_window_max_intensity": max(intensity),
        "trace_max_intensity": max(intensity),
        "apex_aligned_shape_similarity": 0.0,
        "local_window_to_global_max_ratio": 1.0,
        "local_window_apex_delta_min": 0.0,
        "global_trace_apex_delta_min": 0.0,
        "rt": [apex_rt - 0.2, apex_rt - 0.1, apex_rt, apex_rt + 0.1, apex_rt + 0.2],
        "intensity": intensity,
    }


def _group(
    family: str,
    reconciliation_class: str,
    authority_state: str,
    *,
    overlay_png_path: str = "",
    source_artifacts: tuple[str, ...] = (),
    include_in_primary_matrix: bool = False,
    accepted_cell_count: int = 0,
    row_flags: str = "",
    duplicate_assigned_cell_count: int = 0,
    seed_detected_anchor_count: int = 1,
) -> gallery.ReconciliationGroup:
    missing = (
        ("join_gap_seed_audit_sample_not_in_cells",)
        if reconciliation_class == "not_assessable_join_gap"
        else ()
    )
    return gallery.ReconciliationGroup(
        feature_family_id=family,
        seed_group_id=f"seed::{family}::mz=3::rt=3::window=2-4::ppm=10",
        seed_group_basis="seed_audit",
        seed_mz="3",
        seed_rt="3",
        seed_rt_window="2-4",
        seed_ppm="10",
        tag_or_class="class",
        product_behavior_state=(
            "product_primary_backfilled"
            if reconciliation_class.startswith("product_accepts")
            else "product_rescued_context_only"
        ),
        evidence_authority_state=authority_state,
        reconciliation_class=reconciliation_class,
        include_in_primary_matrix=include_in_primary_matrix,
        row_flags=row_flags,
        accepted_cell_count=accepted_cell_count,
        detected_cell_count=1,
        seed_detected_anchor_count=seed_detected_anchor_count,
        rescued_cell_count=2,
        duplicate_assigned_cell_count=duplicate_assigned_cell_count,
        provisional_cell_count=0,
        top_product_reason="fixture",
        top_support_component=(
            "validated_tier2_trace_evidence"
            if authority_state == "product_grade_support"
            else ""
        ),
        top_blocker=(
            "neighbor_interference_review"
            if authority_state == "evidence_blocks_backfill"
            else ""
        ),
        missing_evidence=missing,
        overlay_png_path=overlay_png_path,
        source_artifacts=source_artifacts,
        source_warnings=missing,
    )


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: tuple[str, ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _read_header(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle, delimiter="\t"))
