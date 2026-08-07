from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.backfill_scope import (
    REQUEST_PLAN_VERSION,
    SkippedEvidenceRecord,
    write_skipped_evidence_ledger_tsv,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.debug_writer import (
    write_ambiguous_ms1_owners_tsv,
    write_event_to_ms1_owner_tsv,
    write_owner_edge_evidence_tsv,
)
from xic_extractor.alignment.edge_scoring import OwnerEdgeEvidence
from xic_extractor.alignment.html_report import write_alignment_review_html
from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.ms1_index_source import (
    OwnerBackfillXicBackend,
    OwnerBuildXicBackend,
)
from xic_extractor.alignment.output_levels import (
    AlignmentOutputLevel,
    artifact_names_for_output_level,
)
from xic_extractor.alignment.owner_backfill import OwnerBackfillCandidateAuditRow
from xic_extractor.alignment.ownership import OwnershipBuildResult
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.alignment.provisional_matrix import (
    build_provisional_matrix_projection,
    write_provisional_identity_tsv,
    write_provisional_matrix_tsv,
    write_provisional_provenance_tsv,
    write_provisional_summary_json,
)
from xic_extractor.alignment.tsv_writer import (
    write_alignment_backfill_cell_evidence_tsv,
    write_alignment_cell_integration_audit_tsv,
    write_alignment_cells_tsv,
    write_alignment_matrix_identity_tsv,
    write_alignment_matrix_tsv,
    write_alignment_owner_backfill_candidate_audit_tsv,
    write_alignment_owner_backfill_seed_audit_tsv,
    write_alignment_review_tsv,
    write_alignment_status_matrix_tsv,
)
from xic_extractor.alignment.xlsx_writer import write_alignment_results_xlsx
from xic_extractor.config import ExtractionConfig


@dataclass(frozen=True)
class AlignmentRunOutputs:
    workbook: Path | None = None
    review_html: Path | None = None
    review_tsv: Path | None = None
    matrix_tsv: Path | None = None
    matrix_identity_tsv: Path | None = None
    backfill_cell_evidence_tsv: Path | None = None
    cells_tsv: Path | None = None
    integration_audit_tsv: Path | None = None
    backfill_seed_audit_tsv: Path | None = None
    backfill_candidate_audit_tsv: Path | None = None
    status_matrix_tsv: Path | None = None
    event_to_owner_tsv: Path | None = None
    ambiguous_owners_tsv: Path | None = None
    edge_evidence_tsv: Path | None = None
    skipped_evidence_ledger_tsv: Path | None = None
    run_metadata_json: Path | None = None
    identity_coherence_output_dir: Path | None = None
    provisional_matrix_tsv: Path | None = None
    provisional_identity_tsv: Path | None = None
    provisional_cell_provenance_tsv: Path | None = None
    provisional_summary_json: Path | None = None


def output_paths(
    output_dir: Path,
    *,
    output_level: AlignmentOutputLevel,
    emit_alignment_cells: bool,
    emit_alignment_status_matrix: bool,
    emit_alignment_integration_audit: bool = False,
    emit_alignment_backfill_seed_audit: bool = False,
    emit_alignment_backfill_candidate_audit: bool = False,
    emit_skipped_evidence_ledger: bool = False,
    emit_provisional_matrix: bool = False,
) -> AlignmentRunOutputs:
    artifacts = set(artifact_names_for_output_level(output_level))
    if emit_alignment_cells:
        artifacts.add("alignment_cells.tsv")
    if emit_alignment_status_matrix:
        artifacts.add("alignment_matrix_status.tsv")
    return AlignmentRunOutputs(
        workbook=(
            output_dir / "alignment_results.xlsx"
            if "alignment_results.xlsx" in artifacts
            else None
        ),
        review_html=(
            output_dir / "review_report.html"
            if "review_report.html" in artifacts
            else None
        ),
        review_tsv=(
            output_dir / "alignment_review.tsv"
            if "alignment_review.tsv" in artifacts
            else None
        ),
        matrix_tsv=(
            output_dir / "alignment_matrix.tsv"
            if "alignment_matrix.tsv" in artifacts
            else None
        ),
        matrix_identity_tsv=(
            output_dir / "alignment_matrix_identity.tsv"
            if "alignment_matrix_identity.tsv" in artifacts
            else None
        ),
        cells_tsv=(
            output_dir / "alignment_cells.tsv"
            if "alignment_cells.tsv" in artifacts
            else None
        ),
        backfill_cell_evidence_tsv=(
            output_dir / "alignment_backfill_cell_evidence.tsv"
            if "alignment_backfill_cell_evidence.tsv" in artifacts
            else None
        ),
        integration_audit_tsv=(
            output_dir / "alignment_cell_integration_audit.tsv"
            if emit_alignment_integration_audit
            else None
        ),
        backfill_seed_audit_tsv=(
            output_dir / "alignment_owner_backfill_seed_audit.tsv"
            if emit_alignment_backfill_seed_audit
            else None
        ),
        backfill_candidate_audit_tsv=(
            output_dir / "alignment_owner_backfill_candidate_audit.tsv"
            if emit_alignment_backfill_candidate_audit
            else None
        ),
        status_matrix_tsv=(
            output_dir / "alignment_matrix_status.tsv"
            if "alignment_matrix_status.tsv" in artifacts
            else None
        ),
        event_to_owner_tsv=(
            output_dir / "event_to_ms1_owner.tsv"
            if "event_to_ms1_owner.tsv" in artifacts
            else None
        ),
        ambiguous_owners_tsv=(
            output_dir / "ambiguous_ms1_owners.tsv"
            if "ambiguous_ms1_owners.tsv" in artifacts
            else None
        ),
        edge_evidence_tsv=(
            output_dir / "owner_edge_evidence.tsv"
            if "owner_edge_evidence.tsv" in artifacts
            else None
        ),
        skipped_evidence_ledger_tsv=(
            output_dir / "skipped_evidence_ledger.tsv"
            if emit_skipped_evidence_ledger
            else None
        ),
        run_metadata_json=(
            output_dir / "alignment_run_metadata.json"
            if emit_skipped_evidence_ledger
            else None
        ),
        provisional_matrix_tsv=(
            output_dir / "provisional_alignment_matrix.tsv"
            if emit_provisional_matrix
            else None
        ),
        provisional_identity_tsv=(
            output_dir / "provisional_row_identity.tsv"
            if emit_provisional_matrix
            else None
        ),
        provisional_cell_provenance_tsv=(
            output_dir / "provisional_cell_provenance.tsv"
            if emit_provisional_matrix
            else None
        ),
        provisional_summary_json=(
            output_dir / "provisional_matrix_summary.json"
            if emit_provisional_matrix
            else None
        ),
    )


def alignment_metadata(
    *,
    discovery_batch_index: Path,
    raw_dir: Path,
    dll_dir: Path,
    owner_backfill_xic_backend: OwnerBackfillXicBackend,
    owner_build_xic_backend: OwnerBuildXicBackend = "raw",
    output_level: AlignmentOutputLevel,
    peak_config: ExtractionConfig,
    owner_backfill_window_strategy: str = "exact",
    owner_backfill_superwindow_span_factor: int = 2,
    backfill_scope: str = "full-audit",
    output_scope: str = "full-audit",
    selected_family_count: int = 0,
    selected_family_source: str = "",
    request_plan_version: str = REQUEST_PLAN_VERSION,
    audit_evidence_mode: str = "full",
    requested_audit_evidence_mode: str = "auto",
    heavy_audit_enabled: bool = True,
    audit_evidence_mode_reason: str = "",
    scope_warning: str = "",
    skipped_evidence_predicate_version: str = "",
) -> dict[str, str]:
    return {
        "schema_version": "alignment-results-v3",
        "discovery_batch_index": str(discovery_batch_index),
        "raw_dir": str(raw_dir),
        "dll_dir": str(dll_dir),
        "owner_build_xic_backend": owner_build_xic_backend,
        "owner_backfill_xic_backend": owner_backfill_xic_backend,
        "owner_backfill_window_strategy": owner_backfill_window_strategy,
        "owner_backfill_superwindow_span_factor": str(
            owner_backfill_superwindow_span_factor,
        ),
        "output_level": output_level,
        "resolver_mode": peak_config.resolver_mode,
        "baseline_audit_method": peak_config.baseline_audit_method,
        "baseline_integration_method": peak_config.baseline_integration_method,
        "backfill_scope": backfill_scope,
        "output_scope": output_scope,
        "selected_family_count": str(selected_family_count),
        "selected_family_source": selected_family_source,
        "request_plan_version": request_plan_version,
        "audit_evidence_mode": audit_evidence_mode,
        "requested_audit_evidence_mode": requested_audit_evidence_mode,
        "heavy_audit_enabled": str(heavy_audit_enabled),
        "audit_evidence_mode_reason": audit_evidence_mode_reason,
        "scope_warning": scope_warning,
        "skipped_evidence_predicate_version": skipped_evidence_predicate_version,
        "cross_sample_peak_group_policy": "cross_sample_peak_group_hypothesis_v1",
        "public_family_id_policy": "fam_compatibility_id",
        "group_delivery_policy": "owner_group_delivery_successor_projection_v1",
        "gap_fill_policy": "missing_observation_gap_fill_v1",
        "legacy_owner_backfill_role": (
            "owner_backfill_as_gap_fill_materialization"
        ),
        "pre_backfill_projection_policy": (
            "pre_backfill_successor_projection_required_when_enabled"
        ),
        "matrix_value_policy": "gaussian15_positive_asls_residual_primary",
    }


def write_outputs_atomic(
    outputs: AlignmentRunOutputs,
    matrix: AlignmentMatrix,
    *,
    metadata: dict[str, str],
    ownership: OwnershipBuildResult,
    alignment_config: AlignmentConfig,
    edge_evidence: Sequence[OwnerEdgeEvidence] = (),
    skipped_evidence: Sequence[SkippedEvidenceRecord] = (),
    owner_backfill_candidate_audit_rows: Sequence[
        OwnerBackfillCandidateAuditRow
    ] = (),
    baseline_integration_method: str = "asls",
    baseline_audit_method: str = "",
) -> None:
    output_paths_and_writers: list[tuple[Path, Callable[[Path], Path]]] = []
    provisional_projection = None
    provisional_output_paths = (
        outputs.provisional_matrix_tsv,
        outputs.provisional_identity_tsv,
        outputs.provisional_cell_provenance_tsv,
        outputs.provisional_summary_json,
    )
    if any(path is not None for path in provisional_output_paths):
        if any(path is None for path in provisional_output_paths):
            raise ValueError("provisional matrix outputs must be emitted atomically")
        provisional_projection = build_provisional_matrix_projection(matrix)
    production_decisions = None
    if (
        outputs.review_tsv is not None
        or outputs.matrix_tsv is not None
        or outputs.matrix_identity_tsv is not None
        or outputs.backfill_cell_evidence_tsv is not None
    ):
        production_decisions = build_production_decisions(matrix, alignment_config)
    if outputs.workbook is not None:
        output_paths_and_writers.append(
            (
                outputs.workbook,
                lambda path: write_alignment_results_xlsx(
                    path,
                    matrix,
                    metadata=metadata,
                    alignment_config=alignment_config,
                ),
            ),
        )
    if outputs.review_html is not None:
        output_paths_and_writers.append(
            (
                outputs.review_html,
                lambda path: write_alignment_review_html(path, matrix),
            ),
        )
    if outputs.matrix_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.matrix_tsv,
                lambda path: write_alignment_matrix_tsv(
                    path,
                    matrix,
                    alignment_config=alignment_config,
                    production_decisions=production_decisions,
                ),
            ),
        )
    if outputs.matrix_identity_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.matrix_identity_tsv,
                lambda path: write_alignment_matrix_identity_tsv(
                    path,
                    matrix,
                    alignment_config=alignment_config,
                    production_decisions=production_decisions,
                ),
            ),
        )
    if outputs.review_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.review_tsv,
                lambda path: write_alignment_review_tsv(
                    path,
                    matrix,
                    alignment_config=alignment_config,
                    production_decisions=production_decisions,
                ),
            ),
        )
    if outputs.cells_tsv is not None:
        output_paths_and_writers.append(
            (outputs.cells_tsv, lambda path: write_alignment_cells_tsv(path, matrix)),
        )
    if outputs.backfill_cell_evidence_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.backfill_cell_evidence_tsv,
                lambda path: write_alignment_backfill_cell_evidence_tsv(
                    path,
                    matrix,
                    alignment_config=alignment_config,
                    production_decisions=production_decisions,
                ),
            ),
        )
    if outputs.integration_audit_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.integration_audit_tsv,
                lambda path: write_alignment_cell_integration_audit_tsv(
                    path,
                    matrix,
                    baseline_integration_method=baseline_integration_method,
                    baseline_audit_method=baseline_audit_method,
                ),
            ),
        )
    if outputs.backfill_seed_audit_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.backfill_seed_audit_tsv,
                lambda path: write_alignment_owner_backfill_seed_audit_tsv(
                    path,
                    matrix,
                ),
            ),
        )
    if outputs.backfill_candidate_audit_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.backfill_candidate_audit_tsv,
                lambda path: write_alignment_owner_backfill_candidate_audit_tsv(
                    path,
                    owner_backfill_candidate_audit_rows,
                ),
            ),
        )
    if outputs.status_matrix_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.status_matrix_tsv,
                lambda path: write_alignment_status_matrix_tsv(path, matrix),
            )
        )
    if outputs.event_to_owner_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.event_to_owner_tsv,
                lambda path: write_event_to_ms1_owner_tsv(path, ownership.assignments),
            ),
        )
    if outputs.ambiguous_owners_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.ambiguous_owners_tsv,
                lambda path: write_ambiguous_ms1_owners_tsv(
                    path,
                    ownership.ambiguous_records,
                ),
            ),
        )
    if outputs.edge_evidence_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.edge_evidence_tsv,
                lambda path: write_owner_edge_evidence_tsv(path, edge_evidence),
            ),
        )
    if outputs.skipped_evidence_ledger_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.skipped_evidence_ledger_tsv,
                lambda path: write_skipped_evidence_ledger_tsv(path, skipped_evidence),
            ),
        )
    if outputs.run_metadata_json is not None:
        output_paths_and_writers.append(
            (
                outputs.run_metadata_json,
                lambda path: _write_metadata_json(path, metadata),
            ),
        )
    if outputs.provisional_matrix_tsv is not None:
        assert provisional_projection is not None
        assert outputs.provisional_identity_tsv is not None
        assert outputs.provisional_cell_provenance_tsv is not None
        assert outputs.provisional_summary_json is not None
        output_paths_and_writers.extend(
            (
                (
                    outputs.provisional_matrix_tsv,
                    lambda path: write_provisional_matrix_tsv(
                        path,
                        provisional_projection,
                    ),
                ),
                (
                    outputs.provisional_identity_tsv,
                    lambda path: write_provisional_identity_tsv(
                        path,
                        provisional_projection,
                    ),
                ),
                (
                    outputs.provisional_cell_provenance_tsv,
                    lambda path: write_provisional_provenance_tsv(
                        path,
                        provisional_projection,
                    ),
                ),
                (
                    outputs.provisional_summary_json,
                    lambda path: write_provisional_summary_json(
                        path,
                        provisional_projection,
                    ),
                ),
            )
        )

    temp_paths = [
        _temp_path(final_path) for final_path, _writer in output_paths_and_writers
    ]
    backup_paths = [
        _backup_path(final_path) for final_path, _writer in output_paths_and_writers
    ]
    backups: list[tuple[Path, Path]] = []
    replaced_paths: list[Path] = []
    try:
        for final_path, writer in output_paths_and_writers:
            temp_path = _temp_path(final_path)
            writer(temp_path)
        for final_path, _writer in output_paths_and_writers:
            backup_path = _backup_path(final_path)
            backup_path.unlink(missing_ok=True)
            if final_path.exists():
                final_path.replace(backup_path)
                backups.append((final_path, backup_path))
        for final_path, _writer in output_paths_and_writers:
            temp_path = _temp_path(final_path)
            temp_path.replace(final_path)
            replaced_paths.append(final_path)
        for _final_path, backup_path in backups:
            with suppress(OSError):
                backup_path.unlink(missing_ok=True)
    except Exception:
        for final_path in replaced_paths:
            final_path.unlink(missing_ok=True)
        for final_path, backup_path in reversed(backups):
            if backup_path.exists():
                final_path.unlink(missing_ok=True)
                backup_path.replace(final_path)
        for temp_path in temp_paths:
            temp_path.unlink(missing_ok=True)
        for backup_path in backup_paths:
            backup_path.unlink(missing_ok=True)
        raise


def _temp_path(final_path: Path) -> Path:
    return final_path.with_name(f"{final_path.name}.tmp")


def _write_metadata_json(path: Path, metadata: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _backup_path(final_path: Path) -> Path:
    return final_path.with_name(f"{final_path.name}.bak")
