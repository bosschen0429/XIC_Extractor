"""TSV and JSON output writing for the reconciliation gallery."""

from __future__ import annotations

import json
from pathlib import Path

from xic_extractor.diagnostics.diagnostic_io import (
    write_tsv,
)

from .backfill_reconciliation_gallery_indices import (
    _activation_value_delta_matrix_effect_counts,
    _group_sort_key,
    _representative_sort_key,
    _shadow_policy_decision_counts,
    _shadow_policy_production_gap_counts,
    _shadow_projection_decision_counts,
    _shadow_projection_matrix_counts,
    _target_benchmark_context_counts,
)
from .backfill_reconciliation_gallery_models import (
    GROUP_TSV_COLUMNS,
    REPRESENTATIVE_CELL_TSV_COLUMNS,
    ReconciliationIndex,
)
from .backfill_reconciliation_gallery_output_rows import (
    _group_as_row,
    _representative_as_row,
    _string_object_mapping,
    _summary,
)


def write_reconciliation_outputs(
    output_dir: Path,
    index: ReconciliationIndex,
) -> dict[str, Path]:
    """Write groups TSV, representative-cells TSV, and summary JSON."""

    output_dir.mkdir(parents=True, exist_ok=True)
    groups = sorted(index.groups, key=_group_sort_key)
    representatives = sorted(index.representative_cells, key=_representative_sort_key)
    group_rows = [
        _group_as_row(group, priority_rank=priority)
        for priority, group in enumerate(groups, start=1)
    ]
    representative_rows = [_representative_as_row(row) for row in representatives]
    summary = _summary(
        groups,
        representatives,
        _string_object_mapping(index.summary.get("input_artifacts")),
    )
    summary.update(
        {
            key: value
            for key, value in index.summary.items()
            if key not in {"group_count", "representative_cell_count"}
        },
    )
    if index.shadow_policy_cells:
        summary["shadow_policy_decision_counts"] = _shadow_policy_decision_counts(
            index.shadow_policy_cells,
        )
        summary["shadow_policy_production_gap_counts"] = (
            _shadow_policy_production_gap_counts(index.shadow_policy_cells)
        )
    if index.shadow_projection_cells:
        summary["shadow_projection_decision_counts"] = (
            _shadow_projection_decision_counts(index.shadow_projection_cells)
        )
        summary["shadow_projection_matrix_counts"] = (
            _shadow_projection_matrix_counts(index.shadow_projection_cells)
        )
    if index.activation_delta_cells:
        summary["activation_value_delta_matrix_effect_counts"] = (
            _activation_value_delta_matrix_effect_counts(index.activation_delta_cells)
        )
    if index.target_benchmark_contexts:
        summary["target_benchmark_context_counts"] = _target_benchmark_context_counts(
            index.target_benchmark_contexts,
        )
    summary["group_count"] = len(groups)
    summary["representative_cell_count"] = len(representatives)

    groups_tsv = output_dir / "backfill_evidence_reconciliation_groups.tsv"
    representative_cells_tsv = (
        output_dir / "backfill_evidence_reconciliation_representative_cells.tsv"
    )
    summary_json = output_dir / "backfill_evidence_reconciliation_summary.json"
    write_tsv(groups_tsv, group_rows, GROUP_TSV_COLUMNS, lineterminator="\n")
    write_tsv(
        representative_cells_tsv,
        representative_rows,
        REPRESENTATIVE_CELL_TSV_COLUMNS,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "groups_tsv": groups_tsv,
        "representative_cells_tsv": representative_cells_tsv,
        "summary_json": summary_json,
    }
