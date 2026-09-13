"""Backfill evidence reconciliation indexes and gallery rendering."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_assets as _gallery_assets,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_chain_html as _gallery_chain_html,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_counts as _gallery_counts,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_detail_cards as _gallery_detail_cards,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_detail_drawer as _gallery_detail_drawer,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_evidence as _gallery_evidence,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_family_pattern as _gallery_family_pattern,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_filters as _gallery_filters,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_html as _gallery_html,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_index_builder as _gallery_index_builder,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_index_fields as _gallery_index_fields,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_indices as _gallery_indices,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_inputs as _gallery_inputs,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_models as _gallery_models,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_output_rows as _gallery_output_rows,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_output_writer as _gallery_output_writer,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_overlay_links as _gallery_overlay_links,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_page as _gallery_page,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_provenance as _gallery_provenance,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_ranges as _gallery_ranges,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_render_context as _gallery_render_context_module,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_review_modes as _gallery_review_modes,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_search as _gallery_search,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_shadow_tables as _gallery_shadow_tables,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_source_context as _gallery_source_context,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_state as _gallery_state,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_summary as _gallery_summary,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_table_rows as _gallery_table_rows,
)
from xic_inspector.reconciliation import (
    backfill_reconciliation_gallery_target_benchmark as _gallery_target_benchmark,
)

from .backfill_reconciliation_gallery_indices import (
    _activation_written_cell_keys as _activation_written_cell_keys,
)
from .backfill_reconciliation_gallery_indices import (
    _is_activation_matrix_write as _is_activation_matrix_write,
)
from .backfill_reconciliation_gallery_indices import (
    _is_current_backfill_matrix_write as _is_current_backfill_matrix_write,
)
from .backfill_reconciliation_gallery_indices import (
    _representatives_by_group as _representatives_by_group,
)
from .backfill_reconciliation_gallery_indices import (
    _shadow_policy_cells_by_family as _shadow_policy_cells_by_family,
)
from .backfill_reconciliation_gallery_indices import (
    _shadow_policy_cells_by_group as _shadow_policy_cells_by_group,
)
from .backfill_reconciliation_gallery_indices import (
    _shadow_projection_accept_group_keys as _shadow_projection_accept_group_keys,
)
from .backfill_reconciliation_gallery_indices import (
    _shadow_projection_cells_by_family as _shadow_projection_cells_by_family,
)
from .backfill_reconciliation_gallery_indices import (
    _shadow_projection_cells_by_group as _shadow_projection_cells_by_group,
)
from .backfill_reconciliation_gallery_indices import (
    _target_benchmark_contexts_by_family as _target_benchmark_contexts_by_family,
)
from .backfill_reconciliation_gallery_inputs import (
    load_reconciliation_input_rows,
)
from .backfill_reconciliation_gallery_models import (
    EVIDENCE_AUTHORITY_STATES as EVIDENCE_AUTHORITY_STATES,
)
from .backfill_reconciliation_gallery_models import (
    RECONCILIATION_CLASS_PRIORITY as RECONCILIATION_CLASS_PRIORITY,
)
from .backfill_reconciliation_gallery_models import (
    RECONCILIATION_CLASSES as RECONCILIATION_CLASSES,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationOutputs,
)
from .backfill_reconciliation_gallery_models import (
    _SeedRecord as _SeedRecord,
)
from .backfill_reconciliation_gallery_render_context import (
    _html_priority_group as _html_priority_group,
)
from .backfill_reconciliation_gallery_render_context import (
    _html_render_groups as _html_render_groups,
)

_read_required_tsv = _gallery_inputs._read_required_tsv
_input_artifact_summary = _gallery_inputs._input_artifact_summary
_input_artifact_hashes = _gallery_inputs._input_artifact_hashes
build_reconciliation_index = (
    _gallery_index_builder.build_reconciliation_index
)
write_reconciliation_outputs = (
    _gallery_output_writer.write_reconciliation_outputs
)
write_reconciliation_gallery_html = (
    _gallery_page.write_reconciliation_gallery_html
)
_gallery_document_title = _gallery_page._gallery_document_title
_gallery_hero_copy = _gallery_page._gallery_hero_copy
GROUP_TSV_COLUMNS = _gallery_models.GROUP_TSV_COLUMNS
REPRESENTATIVE_CELL_TSV_COLUMNS = _gallery_models.REPRESENTATIVE_CELL_TSV_COLUMNS
ReconciliationGroup = _gallery_models.ReconciliationGroup
ReconciliationIndex = _gallery_models.ReconciliationIndex
_gallery_css = _gallery_assets.gallery_css
_lightbox_html = _gallery_assets.lightbox_html
_lightbox_script = _gallery_assets.lightbox_script
_gallery_render_context = _gallery_render_context_module._gallery_render_context
_html_scope_notice = _gallery_render_context_module._html_scope_notice
_REQUIRED_ALIGNMENT_REVIEW_COLUMNS = (
    _gallery_inputs._REQUIRED_ALIGNMENT_REVIEW_COLUMNS
)
_REQUIRED_ALIGNMENT_CELLS_COLUMNS = (
    _gallery_inputs._REQUIRED_ALIGNMENT_CELLS_COLUMNS
)
_REQUIRED_ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS = (
    _gallery_inputs._REQUIRED_ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS
)
_REQUIRED_RETAINED_BACKFILL_GATE_COLUMNS = (
    _gallery_inputs._REQUIRED_RETAINED_BACKFILL_GATE_COLUMNS
)
_REQUIRED_SHIFT_AWARE_STANDARD_PEAK_GATE_COLUMNS = (
    _gallery_inputs._REQUIRED_SHIFT_AWARE_STANDARD_PEAK_GATE_COLUMNS
)
_REQUIRED_TARGETED_ISTD_BENCHMARK_SUMMARY_COLUMNS = (
    _gallery_inputs._REQUIRED_TARGETED_ISTD_BENCHMARK_SUMMARY_COLUMNS
)
_INPUT_ARTIFACT_LABEL_BY_KEY = _gallery_inputs._INPUT_ARTIFACT_LABEL_BY_KEY
_escape = _gallery_html.escape_html
_badge = _gallery_html.badge
_escape_attr = _gallery_html.escape_attr
_href_for_path = _gallery_html.href_for_path
RepresentativeCell = _gallery_models.RepresentativeCell
ShadowPolicyCell = _gallery_models.ShadowPolicyCell
ShadowProjectionCell = _gallery_models.ShadowProjectionCell
TargetBenchmarkContext = _gallery_models.TargetBenchmarkContext
_ordered_unique = _gallery_models._ordered_unique
_shadow_policy_cell_from_row = _gallery_indices._shadow_policy_cell_from_row
_source_hashes_from_input_artifacts = (
    _gallery_source_context._source_hashes_from_input_artifacts
)
_first_by_family = _gallery_source_context._first_by_family
_first_by_family_and_seed_group = (
    _gallery_source_context._first_by_family_and_seed_group
)
_group_by_family = _gallery_source_context._group_by_family
_cells_by_family_seed_group = _gallery_source_context._cells_by_family_seed_group
_overlay_rows_by_family_seed_group = (
    _gallery_source_context._overlay_rows_by_family_seed_group
)
_legacy_overlay_rows_by_family = (
    _gallery_source_context._legacy_overlay_rows_by_family
)
_overlay_rows_for_seed_group = _gallery_source_context._overlay_rows_for_seed_group
_legacy_overlay_rows = _gallery_source_context._legacy_overlay_rows
_candidate_family_ids = _gallery_source_context._candidate_family_ids
_detected_family_ids = _gallery_source_context._detected_family_ids
_seed_records_by_family = _gallery_source_context._seed_records_by_family
_seed_samples_by_family = _gallery_source_context._seed_samples_by_family
_seed_group_id = _gallery_source_context._seed_group_id
_fallback_seed_record = _gallery_source_context._fallback_seed_record
_cells_for_seed_record = _gallery_source_context._cells_for_seed_record
_seed_detected_anchor_count = _gallery_source_context._seed_detected_anchor_count
_count_cells = _gallery_source_context._count_cells
_count_provisional = _gallery_source_context._count_provisional
_int_text = _gallery_source_context._int_text
SHIFT_AWARE_SAME_PATTERN_SUPPORT_MIN_R = (
    _gallery_evidence.SHIFT_AWARE_SAME_PATTERN_SUPPORT_MIN_R
)
SHIFT_AWARE_SAME_PATTERN_CONFLICT_MAX_R = (
    _gallery_evidence.SHIFT_AWARE_SAME_PATTERN_CONFLICT_MAX_R
)
_HUMAN_REVIEW_PREFIXES = _gallery_evidence._HUMAN_REVIEW_PREFIXES
_HUMAN_REVIEW_TOKENS = _gallery_evidence._HUMAN_REVIEW_TOKENS
_ANCHOR_SHAPE_SUPPORTED_REASON = _gallery_evidence._ANCHOR_SHAPE_SUPPORTED_REASON
_ANCHOR_SHAPE_REVIEW_REASON = _gallery_evidence._ANCHOR_SHAPE_REVIEW_REASON
_classify_evidence = _gallery_evidence._classify_evidence
_overlay_evidence_notes = _gallery_evidence._overlay_evidence_notes
_shift_aware_standard_peak_gate_components = (
    _gallery_evidence._shift_aware_standard_peak_gate_components
)
_shift_aware_same_pattern_evidence = (
    _gallery_evidence._shift_aware_same_pattern_evidence
)
_product_authority_components = _gallery_evidence._product_authority_components
_append_product_authority_component = (
    _gallery_evidence._append_product_authority_component
)
_anchor_peak_overlay_notes = _gallery_evidence._anchor_peak_overlay_notes
_existing_path_from_text = _gallery_evidence._existing_path_from_text
_compact_note_items = _gallery_evidence._compact_note_items
_is_stale_or_join_token = _gallery_evidence._is_stale_or_join_token
_is_human_review_token = _gallery_evidence._is_human_review_token
_candidate_gate_source_warnings = _gallery_evidence._candidate_gate_source_warnings
_product_behavior = _gallery_evidence._product_behavior
_cell_writes_primary_matrix = _gallery_evidence._cell_writes_primary_matrix
_cell_has_primary_area_context = _gallery_evidence._cell_has_primary_area_context
_reconciliation_class = _gallery_evidence._reconciliation_class
_first_path = _gallery_evidence._first_path
OVERLAY_INTERPRETATION_GUIDE_PATH = (
    _gallery_provenance.OVERLAY_INTERPRETATION_GUIDE_PATH
)
_interpretation_guide_callout_html = (
    _gallery_provenance._interpretation_guide_callout_html
)
_write_local_overlay_interpretation_guide = (
    _gallery_provenance._write_local_overlay_interpretation_guide
)
_artifact_links = _gallery_provenance._artifact_links
_input_artifact_links = _gallery_provenance._input_artifact_links
_source_artifacts_html = _gallery_provenance._source_artifacts_html
_input_artifact_paths_by_label = _gallery_provenance._input_artifact_paths_by_label
_input_artifact_path_rows = _gallery_provenance._input_artifact_path_rows
_compact_counts_text = _gallery_target_benchmark._compact_counts_text
_target_benchmark_summary_text = (
    _gallery_target_benchmark._target_benchmark_summary_text
)
_target_benchmark_panel_html = (
    _gallery_target_benchmark._target_benchmark_panel_html
)
_family_target_summary_html = _gallery_target_benchmark._family_target_summary_html
_target_benchmark_compact_summary = (
    _gallery_target_benchmark._target_benchmark_compact_summary
)
_target_benchmark_contexts_html = (
    _gallery_target_benchmark._target_benchmark_contexts_html
)
_target_coverage_text = _gallery_target_benchmark._target_coverage_text
_target_benchmark_supplied = _gallery_target_benchmark._target_benchmark_supplied
_is_cid_nl_successor_review_group = (
    _gallery_review_modes._is_cid_nl_successor_review_group
)
_is_cid_nl_differential_review_group = (
    _gallery_review_modes._is_cid_nl_differential_review_group
)
_cid_nl_transition_label = _gallery_review_modes._cid_nl_transition_label
_is_cid_nl_successor_review_index = (
    _gallery_review_modes._is_cid_nl_successor_review_index
)
_missing_overlay_status_html = _gallery_overlay_links._missing_overlay_status_html
_overlay_not_required_status_html = (
    _gallery_overlay_links._overlay_not_required_status_html
)
_overlay_not_required_by_gate = _gallery_overlay_links._overlay_not_required_by_gate
_missing_overlay_reason_text = _gallery_overlay_links._missing_overlay_reason_text
_overlay_batch_supplied = _gallery_overlay_links._overlay_batch_supplied
_family_pattern_link_html = _gallery_overlay_links._family_pattern_link_html
_family_overlay_links = _gallery_overlay_links._family_overlay_links
_overlay_link_html = _gallery_overlay_links._overlay_link_html
_path_overlay_link_html = _gallery_overlay_links._path_overlay_link_html
_hypothesis_overlay_path = _gallery_overlay_links._hypothesis_overlay_path
_hypothesis_overlay_link_html = _gallery_overlay_links._hypothesis_overlay_link_html
_overlay_href_context = _gallery_overlay_links._overlay_href_context
_seed_overlay_cell_html = _gallery_overlay_links._seed_overlay_cell_html
_shadow_projection_overlay_link_html = (
    _gallery_overlay_links._shadow_projection_overlay_link_html
)
_shadow_policy_overlay_link_html = (
    _gallery_overlay_links._shadow_policy_overlay_link_html
)
_REVIEW_CATEGORY_LABELS = _gallery_filters._REVIEW_CATEGORY_LABELS
_DEFAULT_FILTER_CATEGORY = _gallery_filters._DEFAULT_FILTER_CATEGORY
_REVIEW_FILTER_LABELS = _gallery_filters._REVIEW_FILTER_LABELS
_REVIEW_CATEGORY_SUMMARY_LABELS = _gallery_filters._REVIEW_CATEGORY_SUMMARY_LABELS
_REVIEW_CATEGORY_BY_CLASS = _gallery_filters._REVIEW_CATEGORY_BY_CLASS
_filter_html = _gallery_filters._filter_html
_default_visible_family_count = _gallery_filters._default_visible_family_count
_review_category = _gallery_filters._review_category
_family_filter_categories = _gallery_filters._family_filter_categories
_group_filter_categories = _gallery_filters._group_filter_categories
_debug_only_group = _gallery_filters._debug_only_group
_shadow_projection_filter_categories = (
    _gallery_filters._shadow_projection_filter_categories
)
_review_category_counts = _gallery_filters._review_category_counts
SCHEMA_VERSION = _gallery_output_rows.SCHEMA_VERSION
_group_as_row = _gallery_output_rows._group_as_row
_representative_as_row = _gallery_output_rows._representative_as_row
_summary = _gallery_output_rows._summary
_string_object_mapping = _gallery_output_rows._string_object_mapping
_string_int_mapping = _gallery_summary._string_int_mapping
_activation_summary_text = _gallery_summary._activation_summary_text
_current_rescue_summary_text = _gallery_summary._current_rescue_summary_text
_summary_html = _gallery_summary._summary_html
_decision_legend_html = _gallery_summary._decision_legend_html
_summary_item = _gallery_summary._summary_item
_count_token_prefix = _gallery_summary._count_token_prefix
_compact_issue_label = _gallery_chain_html._compact_issue_label
_compact_product_reason = _gallery_chain_html._compact_product_reason
_component_summary_text = _gallery_chain_html._component_summary_text
_chain_item_html = _gallery_chain_html._chain_item_html
_component_list_html = _gallery_chain_html._component_list_html
_secondary_chain_details_html = _gallery_chain_html._secondary_chain_details_html
_representative_cells_for_group = (
    _gallery_index_fields._representative_cells_for_group
)
_product_cell_state = _gallery_index_fields._product_cell_state
_apex_delta_sec = _gallery_index_fields._apex_delta_sec
_source_row_key = _gallery_index_fields._source_row_key
_top_product_reason = _gallery_index_fields._top_product_reason
_tag_or_class = _gallery_index_fields._tag_or_class
_first_label = _gallery_index_fields._first_label
_search_blob = _gallery_search._search_blob
_family_search_blob = _gallery_search._family_search_blob
_shadow_projection_search_blob = _gallery_search._shadow_projection_search_blob
_family_seed_summary = _gallery_ranges._family_seed_summary
_family_window_summary = _gallery_ranges._family_window_summary
_compact_value_range = _gallery_ranges._compact_value_range
_compact_text_values = _gallery_ranges._compact_text_values
_seed_mz_range = _gallery_ranges._seed_mz_range
_seed_rt_range = _gallery_ranges._seed_rt_range
_seed_window_range = _gallery_ranges._seed_window_range
_numeric_range_text = _gallery_ranges._numeric_range_text
_numeric_range_start = _gallery_ranges._numeric_range_start
_numeric_range_end = _gallery_ranges._numeric_range_end
_parsed_numeric_values = _gallery_ranges._parsed_numeric_values
_counts_html = _gallery_counts._counts_html
_cid_nl_successor_counts_html = _gallery_counts._cid_nl_successor_counts_html
_projection_counts_html = _gallery_counts._projection_counts_html
_projection_impact_counts_html = _gallery_counts._projection_impact_counts_html
_impact_counts_html = _gallery_counts._impact_counts_html
_count_pill = _gallery_counts._count_pill
_consolidated_counts_html = _gallery_counts._consolidated_counts_html
_state_html = _gallery_state._state_html
_state_aria_label = _gallery_state._state_aria_label
_state_html_for_shadow = _gallery_state._state_html_for_shadow
_shadow_policy_state_label = _gallery_state._shadow_policy_state_label
_shadow_policy_chain_title = _gallery_state._shadow_policy_chain_title
_shadow_policy_chain_subtitle = _gallery_state._shadow_policy_chain_subtitle
_projection_matrix_state_html = _gallery_state._projection_matrix_state_html
_family_pattern_state_html = _gallery_family_pattern._family_pattern_state_html
_family_pattern_status_html = _gallery_family_pattern._family_pattern_status_html
_family_context_available = _gallery_family_pattern._family_context_available
_family_anchor_summary_html = _gallery_family_pattern._family_anchor_summary_html
_family_pattern_issue_html = _gallery_family_pattern._family_pattern_issue_html
_shadow_policy_summary_note_html = (
    _gallery_shadow_tables._shadow_policy_summary_note_html
)
_cell_impact_legend_note_html = _gallery_shadow_tables._cell_impact_legend_note_html
_shadow_projection_summary_note_html = (
    _gallery_shadow_tables._shadow_projection_summary_note_html
)
_shadow_policy_cells_html = _gallery_shadow_tables._shadow_policy_cells_html
_shadow_policy_intro_text = _gallery_shadow_tables._shadow_policy_intro_text
_shadow_projection_cells_html = _gallery_shadow_tables._shadow_projection_cells_html
_shadow_projection_warnings_html = (
    _gallery_shadow_tables._shadow_projection_warnings_html
)
_shadow_projection_metric_text = (
    _gallery_shadow_tables._shadow_projection_metric_text
)
_shadow_projection_evidence_html = (
    _gallery_shadow_tables._shadow_projection_evidence_html
)
_shadow_policy_gap_html = _gallery_shadow_tables._shadow_policy_gap_html
_shadow_metric_text = _gallery_shadow_tables._shadow_metric_text
_shadow_policy_evidence_html = _gallery_shadow_tables._shadow_policy_evidence_html
_detail_summary_html = _gallery_detail_cards._detail_summary_html
_cid_nl_review_focus_card_html = (
    _gallery_detail_cards._cid_nl_review_focus_card_html
)
_cid_nl_discovery_identity_card_html = (
    _gallery_detail_cards._cid_nl_discovery_identity_card_html
)
_detail_summary_card_html = _gallery_detail_cards._detail_summary_card_html
_review_answer_html = _gallery_detail_cards._review_answer_html
_review_answer_decision_text = _gallery_detail_cards._review_answer_decision_text
_support_summary_items = _gallery_detail_cards._support_summary_items
_blocker_summary_items = _gallery_detail_cards._blocker_summary_items
_visual_summary_subtitle = _gallery_detail_cards._visual_summary_subtitle
_detail_visual_summary_html = _gallery_detail_cards._detail_visual_summary_html
_anchor_review_context_html = _gallery_detail_cards._anchor_review_context_html
_cid_nl_identity_transition_list_html = (
    _gallery_detail_cards._cid_nl_identity_transition_list_html
)
_cid_nl_successor_decision_list_html = (
    _gallery_detail_cards._cid_nl_successor_decision_list_html
)
_summary_count_list_html = _gallery_detail_cards._summary_count_list_html
_identity_transition_text = _gallery_detail_cards._identity_transition_text
_overlay_evidence_notes_html = _gallery_detail_cards._overlay_evidence_notes_html
_representative_cells_table_html = (
    _gallery_detail_cards._representative_cells_table_html
)
_details_html = _gallery_detail_drawer._details_html
_table_html = _gallery_table_rows._table_html
_family_groups = _gallery_table_rows._family_groups
_family_sort_key = _gallery_table_rows._family_sort_key
_family_tag_html = _gallery_table_rows._family_tag_html
_family_detail_summary = _gallery_table_rows._family_detail_summary
_top_issue_html = _gallery_table_rows._top_issue_html
_family_table_row = _gallery_table_rows._family_table_row
_consolidated_seed_alias_rows = _gallery_table_rows._consolidated_seed_alias_rows
_consolidated_seed_alias_family = _gallery_table_rows._consolidated_seed_alias_family
_seed_alias_count_label = _gallery_table_rows._seed_alias_count_label
_representatives_for_groups = _gallery_table_rows._representatives_for_groups
_consolidated_overlay_cell_html = _gallery_table_rows._consolidated_overlay_cell_html
_consolidated_seed_alias_details_html = (
    _gallery_table_rows._consolidated_seed_alias_details_html
)
_consolidated_review_answer_html = _gallery_table_rows._consolidated_review_answer_html
_consolidated_overlay_readout = _gallery_table_rows._consolidated_overlay_readout
_projection_accept_cells_html = _gallery_table_rows._projection_accept_cells_html
_projection_accept_seed_hint_html = (
    _gallery_table_rows._projection_accept_seed_hint_html
)
_seed_alias_table_html = _gallery_table_rows._seed_alias_table_html
_seed_decision_rows = _gallery_table_rows._seed_decision_rows
_detail_row_id = _gallery_table_rows._detail_row_id
_seed_table_row_html = _gallery_table_rows._seed_table_row_html
_family_details_html = _gallery_table_rows._family_details_html
_seed_issue_text = _gallery_table_rows._seed_issue_text
_seed_detail_summary = _gallery_table_rows._seed_detail_summary
_HIGH_SEED_ALIAS_COUNT = _gallery_table_rows._HIGH_SEED_ALIAS_COUNT


def run_reconciliation_gallery(
    *,
    alignment_review_tsv: Path,
    alignment_cells_tsv: Path,
    output_dir: Path,
    alignment_matrix_tsv: Path | None = None,
    backfill_seed_audit_tsv: Path | None = None,
    overlay_batch_summary_tsvs: Sequence[Path] = (),
    shift_aware_same_pattern_tsvs: Sequence[Path] = (),
    shift_aware_standard_peak_gate_tsvs: Sequence[Path] = (),
    seed_aware_family_tsv: Path | None = None,
    seed_aware_summary_tsv: Path | None = None,
    candidate_gate_tsv: Path | None = None,
    retained_backfill_gate_tsv: Path | None = None,
    tier2_trace_evidence_tsv: Path | None = None,
    shadow_policy_cells_tsv: Path | None = None,
    shadow_projection_cells_tsv: Path | None = None,
    activation_application_summary_tsv: Path | None = None,
    activation_value_delta_tsv: Path | None = None,
    targeted_istd_benchmark_summary_tsv: Path | None = None,
    source_run_id: str = "",
) -> ReconciliationOutputs:
    """Load existing artifacts, write reconciliation indexes, and render HTML."""

    input_rows = load_reconciliation_input_rows(
        alignment_review_tsv=alignment_review_tsv,
        alignment_cells_tsv=alignment_cells_tsv,
        alignment_matrix_tsv=alignment_matrix_tsv,
        backfill_seed_audit_tsv=backfill_seed_audit_tsv,
        overlay_batch_summary_tsvs=overlay_batch_summary_tsvs,
        shift_aware_same_pattern_tsvs=shift_aware_same_pattern_tsvs,
        shift_aware_standard_peak_gate_tsvs=shift_aware_standard_peak_gate_tsvs,
        seed_aware_family_tsv=seed_aware_family_tsv,
        seed_aware_summary_tsv=seed_aware_summary_tsv,
        candidate_gate_tsv=candidate_gate_tsv,
        retained_backfill_gate_tsv=retained_backfill_gate_tsv,
        tier2_trace_evidence_tsv=tier2_trace_evidence_tsv,
        shadow_policy_cells_tsv=shadow_policy_cells_tsv,
        shadow_projection_cells_tsv=shadow_projection_cells_tsv,
        activation_application_summary_tsv=activation_application_summary_tsv,
        activation_value_delta_tsv=activation_value_delta_tsv,
        targeted_istd_benchmark_summary_tsv=targeted_istd_benchmark_summary_tsv,
        source_run_id=source_run_id,
    )
    index = build_reconciliation_index(
        review_rows=input_rows.review_rows,
        cell_rows=input_rows.cell_rows,
        alignment_matrix_rows=input_rows.matrix_rows,
        seed_audit_rows=input_rows.seed_audit_rows,
        overlay_rows=input_rows.overlay_rows,
        shift_aware_same_pattern_rows=input_rows.shift_aware_rows,
        shift_aware_standard_peak_gate_rows=input_rows.standard_peak_gate_rows,
        seed_aware_family_rows=input_rows.seed_aware_family_rows,
        seed_aware_summary_rows=input_rows.seed_aware_summary_rows,
        candidate_gate_rows=input_rows.candidate_gate_rows,
        retained_gate_rows=input_rows.retained_gate_rows,
        tier2_trace_evidence_rows=input_rows.tier2_trace_evidence_rows,
        shadow_policy_rows=input_rows.shadow_policy_rows,
        shadow_projection_rows=input_rows.shadow_projection_rows,
        activation_application_summary_rows=(
            input_rows.activation_application_summary_rows
        ),
        activation_value_delta_rows=input_rows.activation_value_delta_rows,
        target_benchmark_rows=input_rows.target_benchmark_rows,
        input_artifacts=input_rows.input_artifacts,
    )
    paths = write_reconciliation_outputs(output_dir, index)
    gallery_html = output_dir / "backfill_evidence_reconciliation_gallery.html"
    write_reconciliation_gallery_html(gallery_html, index, output_paths=paths)
    return ReconciliationOutputs(
        groups_tsv=paths["groups_tsv"],
        representative_cells_tsv=paths["representative_cells_tsv"],
        summary_json=paths["summary_json"],
        gallery_html=gallery_html,
    )
