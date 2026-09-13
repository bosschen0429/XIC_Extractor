"""Build reconciliation gallery indexes from loaded artifact rows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from xic_extractor.diagnostics.diagnostic_io import (
    bool_value,
    text_value,
)

from .backfill_reconciliation_gallery_evidence import (
    _classify_evidence,
    _product_behavior,
    _reconciliation_class,
)
from .backfill_reconciliation_gallery_index_fields import (
    _first_label,
    _representative_cells_for_group,
    _tag_or_class,
    _top_product_reason,
)
from .backfill_reconciliation_gallery_indices import (
    _activation_application_summary,
    _activation_delta_cell_from_row,
    _activation_value_delta_matrix_effect_counts,
    _activation_written_projection_cell_count,
    _activation_written_projection_group_keys,
    _current_matrix_written_projection_cell_count,
    _current_matrix_written_projection_group_keys,
    _group_sort_key,
    _representative_sort_key,
    _shadow_policy_cell_from_row,
    _shadow_policy_decision_counts,
    _shadow_policy_production_gap_counts,
    _shadow_projection_cell_from_row,
    _shadow_projection_decision_counts,
    _shadow_projection_matrix_counts,
    _target_benchmark_context_counts,
    _target_benchmark_context_from_row,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    ReconciliationIndex,
    RepresentativeCell,
    _ordered_unique,
)
from .backfill_reconciliation_gallery_output_rows import (
    _summary,
)
from .backfill_reconciliation_gallery_source_context import (
    _candidate_family_ids,
    _cells_by_family_seed_group,
    _cells_for_seed_record,
    _count_cells,
    _count_provisional,
    _fallback_seed_record,
    _first_by_family,
    _first_by_family_and_seed_group,
    _group_by_family,
    _int_text,
    _legacy_overlay_rows_by_family,
    _overlay_rows_by_family_seed_group,
    _seed_detected_anchor_count,
    _seed_records_by_family,
    _seed_samples_by_family,
    _source_hashes_from_input_artifacts,
)


def build_reconciliation_index(
    *,
    review_rows: Iterable[Mapping[str, str]],
    cell_rows: Iterable[Mapping[str, str]],
    alignment_matrix_rows: Iterable[Mapping[str, str]] = (),
    seed_audit_rows: Iterable[Mapping[str, str]] = (),
    overlay_rows: Iterable[Mapping[str, str]] = (),
    shift_aware_same_pattern_rows: Iterable[Mapping[str, str]] = (),
    shift_aware_standard_peak_gate_rows: Iterable[Mapping[str, str]] = (),
    seed_aware_family_rows: Iterable[Mapping[str, str]] = (),
    seed_aware_summary_rows: Iterable[Mapping[str, str]] = (),
    candidate_gate_rows: Iterable[Mapping[str, str]] = (),
    retained_gate_rows: Iterable[Mapping[str, str]] = (),
    tier2_trace_evidence_rows: Iterable[Mapping[str, str]] = (),
    shadow_policy_rows: Iterable[Mapping[str, str]] = (),
    shadow_projection_rows: Iterable[Mapping[str, str]] = (),
    activation_application_summary_rows: Iterable[Mapping[str, str]] = (),
    activation_value_delta_rows: Iterable[Mapping[str, str]] = (),
    target_benchmark_rows: Iterable[Mapping[str, str]] = (),
    input_artifacts: Mapping[str, object] | None = None,
) -> ReconciliationIndex:
    """Return deterministic reconciliation groups, representative cells, and summary."""

    reviews = [dict(row) for row in review_rows]
    cells = [dict(row) for row in cell_rows]
    matrices = [dict(row) for row in alignment_matrix_rows]
    seeds = [dict(row) for row in seed_audit_rows]
    overlays = [dict(row) for row in overlay_rows]
    shift_aware = [dict(row) for row in shift_aware_same_pattern_rows]
    standard_peak_gate = [dict(row) for row in shift_aware_standard_peak_gate_rows]
    seed_aware = [dict(row) for row in seed_aware_family_rows]
    seed_aware_summary = [dict(row) for row in seed_aware_summary_rows]
    candidates = [dict(row) for row in candidate_gate_rows]
    retained_gate = [dict(row) for row in retained_gate_rows]
    tier2 = [dict(row) for row in tier2_trace_evidence_rows]
    shadow_policy_cells = tuple(
        _shadow_policy_cell_from_row(row) for row in shadow_policy_rows
    )
    shadow_projection_cells = tuple(
        _shadow_projection_cell_from_row(row) for row in shadow_projection_rows
    )
    activation_application_summary = [
        dict(row) for row in activation_application_summary_rows
    ]
    activation_delta_cells = tuple(
        _activation_delta_cell_from_row(row) for row in activation_value_delta_rows
    )
    activated_projection_group_keys = _activation_written_projection_group_keys(
        shadow_projection_cells,
        activation_delta_cells,
    )
    current_written_projection_group_keys = (
        _current_matrix_written_projection_group_keys(shadow_projection_cells)
    )
    target_contexts = tuple(
        context
        for row in target_benchmark_rows
        if (context := _target_benchmark_context_from_row(row)) is not None
    )

    reviews_by_family = _first_by_family(reviews)
    cells_by_family = _group_by_family(cells)
    matrix_families = {text_value(row.get("feature_family_id")) for row in matrices}
    seed_records_by_family = _seed_records_by_family(seeds)
    seed_samples_by_family = _seed_samples_by_family(seeds)
    family_ids, excluded_family_counts = _candidate_family_ids(
        reviews=reviews,
        cells=cells,
        seeds=seeds,
        seed_aware=seed_aware,
        seed_aware_summary=seed_aware_summary,
        candidates=candidates,
    )
    cells_by_seed_group = _cells_by_family_seed_group(
        cells_by_family=cells_by_family,
        seed_records_by_family=seed_records_by_family,
        family_ids=family_ids,
    )
    overlay_rows_by_seed_group = _overlay_rows_by_family_seed_group(overlays)
    legacy_overlay_rows_by_family = _legacy_overlay_rows_by_family(overlays)
    shift_aware_by_family = _group_by_family(shift_aware)
    standard_peak_gate_by_family = _group_by_family(standard_peak_gate)
    seed_aware_by_family = _first_by_family(seed_aware)
    candidate_by_family = _first_by_family(candidates)
    retained_gate_by_group = _first_by_family_and_seed_group(retained_gate)
    source_hashes = _source_hashes_from_input_artifacts(input_artifacts or {})
    tier2_families = {
        text_value(row.get("feature_family_id"))
        for row in tier2
        if row.get("feature_family_id")
    }

    groups: list[ReconciliationGroup] = []
    representatives: list[RepresentativeCell] = []
    for family in sorted(family_ids):
        seed_records = seed_records_by_family.get(
            family,
            (_fallback_seed_record(family),),
        )
        sorted_seed_records = sorted(
            seed_records,
            key=lambda record: record.seed_group_id,
        )
        for seed_record in sorted_seed_records:
            review = reviews_by_family.get(family, {})
            family_cells = tuple(cells_by_family.get(family, ()))
            group_cells = cells_by_seed_group.get(
                (family, seed_record.seed_group_id),
                _cells_for_seed_record(family_cells, seed_record),
            )
            evidence = _classify_evidence(
                family=family,
                seed_record=seed_record,
                family_cells=family_cells,
                group_cells=group_cells,
                has_matrix_context=family in matrix_families,
                seed_samples=seed_samples_by_family.get(family, frozenset()),
                overlay_rows=overlay_rows_by_seed_group.get(
                    (family, seed_record.seed_group_id),
                    (),
                ),
                shift_aware_same_pattern_rows=shift_aware_by_family.get(
                    family,
                    (),
                ),
                shift_aware_standard_peak_gate_rows=(
                    standard_peak_gate_by_family.get(family, ())
                ),
                legacy_overlay_rows=legacy_overlay_rows_by_family.get(
                    family,
                    (),
                ),
                seed_aware_row=seed_aware_by_family.get(family, {}),
                candidate_gate_row=candidate_by_family.get(family, {}),
                retained_gate_row=retained_gate_by_group.get(
                    (family, seed_record.seed_group_id),
                    {},
                ),
                source_hashes=source_hashes,
                has_tier2_trace_evidence=family in tier2_families,
            )
            product_behavior = _product_behavior(review, family_cells)
            current_written_by_projection = (
                family,
                seed_record.seed_group_id,
            ) in current_written_projection_group_keys
            activated_by_delta = (
                family,
                seed_record.seed_group_id,
            ) in activated_projection_group_keys
            if current_written_by_projection or activated_by_delta:
                product_behavior = "product_primary_backfilled"
            product_reason = _top_product_reason(review)
            if activated_by_delta:
                product_reason = "activation_value_delta_written"
            elif current_written_by_projection:
                product_reason = "shadow_projection_current_matrix_written"
            source_artifacts = tuple(evidence["source_artifacts"])
            if current_written_by_projection or activated_by_delta:
                product_sources = ["shadow_production_projection_cells.tsv"]
                if activated_by_delta:
                    product_sources.extend(
                        (
                            "activation_application_summary.tsv",
                            "activation_value_delta.tsv",
                        ),
                    )
                source_artifacts = tuple(
                    _ordered_unique(
                        (
                            *source_artifacts,
                            *product_sources,
                        ),
                    ),
                )
            seed_count_cells = group_cells or family_cells
            representative_cells = _representative_cells_for_group(
                family=family,
                seed_group_id=seed_record.seed_group_id,
                product_behavior_state=product_behavior,
                evidence=evidence,
                group_cells=group_cells or family_cells,
                seed_record=seed_record,
            )
            group = ReconciliationGroup(
                feature_family_id=family,
                seed_group_id=seed_record.seed_group_id,
                seed_group_basis=seed_record.seed_group_basis,
                seed_mz=seed_record.seed_mz,
                seed_rt=seed_record.seed_rt,
                seed_rt_window=seed_record.seed_rt_window,
                seed_ppm=seed_record.ppm,
                tag_or_class=_tag_or_class(
                    review,
                    seed_aware_by_family.get(family, {}),
                ),
                product_behavior_state=product_behavior,
                evidence_authority_state=evidence["authority_state"],
                reconciliation_class=_reconciliation_class(
                    product_behavior,
                    evidence["authority_state"],
                    tuple(evidence["missing_evidence"]),
                    tuple(evidence["source_warnings"]),
                ),
                include_in_primary_matrix=bool_value(
                    review.get("include_in_primary_matrix"),
                )
                is True,
                identity_decision=text_value(review.get("identity_decision")),
                row_flags=text_value(review.get("row_flags")),
                family_evidence=text_value(review.get("family_evidence")),
                accepted_cell_count=_int_text(review.get("accepted_cell_count")),
                detected_cell_count=_count_cells(family_cells, "detected"),
                rescued_cell_count=_count_cells(seed_count_cells, "rescued"),
                provisional_cell_count=_count_provisional(seed_count_cells),
                seed_detected_anchor_count=_seed_detected_anchor_count(
                    family_cells,
                    seed_record=seed_record,
                    seed_records=sorted_seed_records,
                ),
                duplicate_assigned_cell_count=_count_cells(
                    family_cells,
                    "duplicate_assigned",
                ),
                cell_total_count=len(family_cells),
                top_product_reason=product_reason,
                top_support_component=_first_label(
                    evidence["product_grade_support_components"]
                    or evidence["review_only_visual_components"]
                    or evidence["dependent_context_components"],
                ),
                top_blocker=_first_label(evidence["blocker_components"]),
                missing_evidence=tuple(evidence["missing_evidence"]),
                overlay_png_path=text_value(evidence["overlay_png_path"]),
                overlay_trace_json_path=text_value(evidence["overlay_trace_json_path"]),
                family_pattern_png_path=text_value(evidence["family_pattern_png_path"]),
                family_pattern_trace_json_path=text_value(
                    evidence["family_pattern_trace_json_path"],
                ),
                family_pattern_verdict=text_value(evidence["family_pattern_verdict"]),
                overlay_evidence_notes=tuple(evidence["overlay_evidence_notes"]),
                source_artifacts=source_artifacts,
                source_warnings=tuple(evidence["source_warnings"]),
                product_grade_support_components=tuple(
                    evidence["product_grade_support_components"],
                ),
                review_only_visual_components=tuple(
                    evidence["review_only_visual_components"],
                ),
                dependent_context_components=tuple(evidence["dependent_context_components"]),
                blocker_components=tuple(evidence["blocker_components"]),
                representative_cells=representative_cells,
            )
            groups.append(group)
            representatives.extend(representative_cells)

    groups = sorted(groups, key=_group_sort_key)
    representatives = sorted(representatives, key=_representative_sort_key)
    summary = _summary(groups, representatives, input_artifacts or {})
    summary["excluded_family_counts"] = dict(excluded_family_counts)
    summary["shadow_policy_decision_counts"] = _shadow_policy_decision_counts(
        shadow_policy_cells,
    )
    summary["shadow_policy_production_gap_counts"] = (
        _shadow_policy_production_gap_counts(shadow_policy_cells)
    )
    summary["shadow_projection_decision_counts"] = (
        _shadow_projection_decision_counts(shadow_projection_cells)
    )
    summary["shadow_projection_matrix_counts"] = _shadow_projection_matrix_counts(
        shadow_projection_cells,
    )
    summary["activation_application_summary"] = _activation_application_summary(
        activation_application_summary,
    )
    summary["activation_value_delta_matrix_effect_counts"] = (
        _activation_value_delta_matrix_effect_counts(activation_delta_cells)
    )
    summary["activation_written_projection_group_count"] = len(
        activated_projection_group_keys,
    )
    summary["activation_written_projection_cell_count"] = (
        _activation_written_projection_cell_count(
            shadow_projection_cells,
            activation_delta_cells,
        )
    )
    summary["current_written_projection_group_count"] = len(
        current_written_projection_group_keys,
    )
    summary["current_written_projection_cell_count"] = (
        _current_matrix_written_projection_cell_count(shadow_projection_cells)
    )
    summary["activation_delta_view"] = (
        "activated_matrix"
        if activation_delta_cells
        else "not_supplied"
    )
    summary["product_behavior_changed"] = bool(
        activated_projection_group_keys or current_written_projection_group_keys,
    )
    product_behavior_sources: list[str] = []
    if current_written_projection_group_keys:
        product_behavior_sources.append("shadow_production_projection_cells.tsv")
    if activated_projection_group_keys:
        product_behavior_sources.append("activation_value_delta.tsv")
    summary["product_behavior_source"] = ";".join(product_behavior_sources)
    summary["target_benchmark_context_counts"] = _target_benchmark_context_counts(
        target_contexts,
    )
    return ReconciliationIndex(
        groups=tuple(groups),
        representative_cells=tuple(representatives),
        shadow_policy_cells=shadow_policy_cells,
        shadow_projection_cells=shadow_projection_cells,
        activation_delta_cells=activation_delta_cells,
        target_benchmark_contexts=target_contexts,
        summary=summary,
    )
