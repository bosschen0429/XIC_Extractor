"""Consolidate chunked standard-peak machine pipeline outputs."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.diagnostics.diagnostic_io import (
    file_sha256,
    format_diagnostic_value,
    read_tsv_required,
    text_value,
    write_tsv,
)
from xic_extractor.diagnostics.shadow_production_projection import (
    SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    run_shadow_production_projection,
)
from xic_extractor.diagnostics.standard_peak_backfill_productization import (
    StandardPeakBackfillProductizationOutputs,
    run_standard_peak_backfill_productization,
)

SCHEMA_VERSION = "standard_peak_backfill_chunk_consolidation_v0"
SOURCE_MATRIX_REPUBLISHER_RETIRED_MESSAGE = (
    "Standard Peak source-Matrix republication is retired; keep diagnostic "
    "formal output as a sidecar"
)
FORMAL_PRODUCT_FILENAMES = (
    "alignment_matrix.tsv",
    "alignment_matrix_identity.tsv",
    "activation_hypothesis_identity.tsv",
    "activation_value_delta.tsv",
    "activation_application_summary.tsv",
    "standard_peak_formal_product_manifest.json",
)
_SHADOW_PROJECTION_MERGE_KEY_COLUMNS = (
    "peak_hypothesis_id",
    "activation_unit_scope",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
)
_SHADOW_PROJECTION_ACCEPT_CONFLICT_COLUMNS = (
    "projected_matrix_value",
    "projection_authority",
    "product_authority_chain",
)

SUMMARY_COLUMNS = (
    "schema_version",
    "source_run_id",
    "status",
    "chunk_count",
    "queue_row_count",
    "covered_queue_row_count",
    "duplicate_queue_rank_count",
    "missing_queue_rank_count",
    "coverage_status",
    "merged_shadow_projection_cells_tsv",
    "merged_shadow_projection_row_count",
    "productization_summary_json",
    "activated_alignment_matrix_tsv",
    "activation_value_delta_tsv",
    "matrix_cells_written",
    "activation_acceptance_status",
    "activation_application_status",
    "reconciliation_gallery_html",
    "formal_product_output_dir",
    "formal_product_manifest_json",
    "published_alignment_output_dir",
    "published_alignment_manifest_json",
    "status_reasons",
)


@dataclass(frozen=True)
class StandardPeakChunkConsolidationOutputs:
    summary_tsv: Path
    summary_json: Path
    status: str
    merged_shadow_projection_cells_tsv: Path
    productization: StandardPeakBackfillProductizationOutputs
    formal_product_output_dir: Path | None = None
    formal_product_manifest_json: Path | None = None
    published_alignment_output_dir: Path | None = None
    published_alignment_manifest_json: Path | None = None


def run_standard_peak_backfill_chunk_consolidation(
    *,
    machine_pipeline_summary_jsons: Sequence[Path],
    alignment_matrix_tsv: Path,
    alignment_matrix_identity_tsv: Path,
    alignment_review_tsv: Path,
    output_dir: Path,
    source_run_id: str = "",
    review_queue_tsv: Path | None = None,
    write_gallery: bool = False,
    alignment_cells_tsv: Path | None = None,
    backfill_seed_audit_tsv: Path | None = None,
    retained_backfill_gate_tsv: Path | None = None,
    gallery_output_dir: Path | None = None,
    emit_formal_product_output: bool = False,
    publish_to_source_alignment_output: bool = False,
    formal_product_output_dir: Path | None = None,
    publish_alignment_matrix_tsv: Path | None = None,
    publish_alignment_matrix_identity_tsv: Path | None = None,
) -> StandardPeakChunkConsolidationOutputs:
    """Merge chunk projection cells and run one matrix-only activation pass."""

    publication_targets_supplied = (
        publish_alignment_matrix_tsv is not None
        or publish_alignment_matrix_identity_tsv is not None
    )
    if publish_to_source_alignment_output or publication_targets_supplied:
        raise ValueError(SOURCE_MATRIX_REPUBLISHER_RETIRED_MESSAGE)
    if not machine_pipeline_summary_jsons:
        raise ValueError("at least one machine pipeline summary JSON is required")

    output_dir.mkdir(parents=True, exist_ok=True)
    chunk_summaries = tuple(
        _load_chunk_summary(path) for path in machine_pipeline_summary_jsons
    )
    coverage = _coverage_summary(chunk_summaries, review_queue_tsv=review_queue_tsv)
    raw_status_reasons = coverage.get("status_reasons")
    status_reasons = (
        list(raw_status_reasons) if isinstance(raw_status_reasons, tuple) else []
    )
    for chunk in chunk_summaries:
        if text_value(chunk.get("status")) != "pass":
            status_reasons.append(
                "chunk_not_pass:" + text_value(chunk.get("_summary_json")),
            )

    overlay_tsvs = tuple(
        Path(text_value(chunk.get("overlay_batch_summary_tsv")))
        for chunk in chunk_summaries
        if text_value(chunk.get("overlay_batch_summary_tsv"))
    )
    gate_tsvs = tuple(
        Path(text_value(chunk.get("shift_aware_standard_peak_gate_tsv")))
        for chunk in chunk_summaries
        if text_value(chunk.get("shift_aware_standard_peak_gate_tsv"))
    )
    authority_tsvs = tuple(
        Path(text_value(chunk.get("authorized_ms1_pattern_tsv")))
        for chunk in chunk_summaries
        if text_value(chunk.get("authorized_ms1_pattern_tsv"))
    )
    merged_shadow_tsv = output_dir / "consolidated_shadow_projection_cells.tsv"
    shadow_rows: Sequence[Mapping[str, str]]
    if authority_tsvs and len(authority_tsvs) == len(chunk_summaries):
        if retained_backfill_gate_tsv is None:
            status_reasons.append("global_shadow_projection_requires_retained_gate")
            shadow_rows = ()
            write_tsv(
                merged_shadow_tsv,
                shadow_rows,
                SHADOW_PRODUCTION_PROJECTION_COLUMNS,
                formatter=format_diagnostic_value,
                lineterminator="\n",
            )
        elif alignment_cells_tsv is None:
            status_reasons.append("global_shadow_projection_requires_alignment_cells")
            shadow_rows = ()
            write_tsv(
                merged_shadow_tsv,
                shadow_rows,
                SHADOW_PRODUCTION_PROJECTION_COLUMNS,
                formatter=format_diagnostic_value,
                lineterminator="\n",
            )
        else:
            shadow_outputs = run_shadow_production_projection(
                alignment_review_tsv=alignment_review_tsv,
                alignment_cells_tsv=alignment_cells_tsv,
                retained_gate_tsv=retained_backfill_gate_tsv,
                output_dir=output_dir / "global_shadow_projection",
                alignment_matrix_tsv=alignment_matrix_tsv,
                alignment_matrix_identity_tsv=alignment_matrix_identity_tsv,
                overlay_batch_summary_tsvs=overlay_tsvs,
                ms1_pattern_coherence_tsvs=authority_tsvs,
                source_run_id=source_run_id,
            )
            shadow_rows = read_tsv_required(
                shadow_outputs.tsv,
                SHADOW_PRODUCTION_PROJECTION_COLUMNS,
            )
            write_tsv(
                merged_shadow_tsv,
                shadow_rows,
                SHADOW_PRODUCTION_PROJECTION_COLUMNS,
                formatter=format_diagnostic_value,
                lineterminator="\n",
            )
    else:
        shadow_rows = _merge_shadow_projection_rows(chunk_summaries)
        write_tsv(
            merged_shadow_tsv,
            shadow_rows,
            SHADOW_PRODUCTION_PROJECTION_COLUMNS,
            formatter=format_diagnostic_value,
            lineterminator="\n",
        )
    productization_dir = output_dir / "standard_peak_productization"
    productization = run_standard_peak_backfill_productization(
        shadow_projection_cells_tsv=merged_shadow_tsv,
        alignment_matrix_tsv=alignment_matrix_tsv,
        alignment_matrix_identity_tsv=alignment_matrix_identity_tsv,
        alignment_review_tsv=alignment_review_tsv,
        output_dir=productization_dir,
        source_run_id=source_run_id,
        write_gallery=write_gallery,
        alignment_cells_tsv=alignment_cells_tsv,
        backfill_seed_audit_tsv=backfill_seed_audit_tsv,
        overlay_batch_summary_tsvs=overlay_tsvs,
        shift_aware_standard_peak_gate_tsvs=gate_tsvs,
        retained_backfill_gate_tsv=retained_backfill_gate_tsv,
        gallery_output_dir=gallery_output_dir,
    )
    product_summary = _load_json_mapping(productization.summary_json)
    if productization.status != "pass":
        status_reasons.append("productization_not_pass")
    if emit_formal_product_output:
        if review_queue_tsv is None:
            status_reasons.append("formal_product_output_requires_review_queue")
        elif text_value(coverage.get("coverage_status")) != "complete":
            status_reasons.append(
                "formal_product_output_requires_complete_queue_coverage",
            )

    formal_output_dir: Path | None = None
    formal_manifest_json: Path | None = None
    published_alignment_output_dir: Path | None = None
    published_alignment_manifest_json: Path | None = None
    if emit_formal_product_output and not status_reasons:
        formal_target_dir = formal_product_output_dir or output_dir / (
            "formal_product_output"
        )
        if _is_noop_formal_product_output(
            productization=productization,
            product_summary=product_summary,
        ):
            _clear_stale_formal_product_output(
                output_dir=formal_target_dir,
                source_alignment_matrix_tsv=alignment_matrix_tsv,
            )
        else:
            formal_output_dir, formal_manifest_json = _emit_formal_product_output(
                productization=productization,
                product_summary=product_summary,
                output_dir=formal_target_dir,
                source_alignment_matrix_tsv=alignment_matrix_tsv,
                source_run_id=source_run_id,
                coverage=coverage,
            )
    elif emit_formal_product_output:
        blocked_formal_output_dir = (
            formal_product_output_dir or output_dir / "formal_product_output"
        )
        _clear_stale_formal_product_output(
            output_dir=blocked_formal_output_dir,
            source_alignment_matrix_tsv=alignment_matrix_tsv,
        )

    status = "pass" if not status_reasons else "fail"
    summary = _summary_row(
        source_run_id=source_run_id,
        status=status,
        status_reasons=status_reasons,
        chunk_count=len(chunk_summaries),
        coverage=coverage,
        merged_shadow_tsv=merged_shadow_tsv,
        merged_shadow_row_count=len(shadow_rows),
        productization=productization,
        product_summary=product_summary,
        formal_product_output_dir=formal_output_dir,
        formal_product_manifest_json=formal_manifest_json,
        published_alignment_output_dir=published_alignment_output_dir,
        published_alignment_manifest_json=published_alignment_manifest_json,
    )
    summary_tsv = output_dir / "standard_peak_backfill_chunk_consolidation_summary.tsv"
    summary_json = (
        output_dir / "standard_peak_backfill_chunk_consolidation_summary.json"
    )
    write_tsv(
        summary_tsv,
        (summary,),
        SUMMARY_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return StandardPeakChunkConsolidationOutputs(
        summary_tsv=summary_tsv,
        summary_json=summary_json,
        status=status,
        merged_shadow_projection_cells_tsv=merged_shadow_tsv,
        productization=productization,
        formal_product_output_dir=formal_output_dir,
        formal_product_manifest_json=formal_manifest_json,
        published_alignment_output_dir=published_alignment_output_dir,
        published_alignment_manifest_json=published_alignment_manifest_json,
    )


def _is_noop_formal_product_output(
    *,
    productization: StandardPeakBackfillProductizationOutputs,
    product_summary: Mapping[str, object],
) -> bool:
    return (
        productization.status == "pass"
        and productization.activated_matrix_tsv is None
        and text_value(product_summary.get("selected_activation_row_count")) == "0"
    )


def _emit_formal_product_output(
    *,
    productization: StandardPeakBackfillProductizationOutputs,
    product_summary: Mapping[str, object],
    output_dir: Path,
    source_alignment_matrix_tsv: Path,
    source_run_id: str,
    coverage: Mapping[str, object],
) -> tuple[Path, Path]:
    if productization.status != "pass":
        raise ValueError("formal product output requires passing productization")
    if productization.activated_matrix_tsv is None:
        raise ValueError("formal product output requires activated matrix TSV")
    activated_dir = productization.activated_matrix_tsv.parent
    source_dir = source_alignment_matrix_tsv.parent.resolve()
    if output_dir.resolve() == source_dir:
        raise ValueError(
            "formal product output must not overwrite the source alignment dir",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}
    for name in FORMAL_PRODUCT_FILENAMES:
        if name == "standard_peak_formal_product_manifest.json":
            continue
        source = activated_dir / name
        if not source.exists():
            raise FileNotFoundError(str(source))
        target = output_dir / name
        if target.resolve() == source_alignment_matrix_tsv.resolve():
            raise ValueError(
                "formal product output must not overwrite source "
                "alignment_matrix.tsv",
            )
        shutil.copy2(source, target)
        copied[name] = file_sha256(target, uppercase=False)
    activation_inputs_summary = _load_activation_inputs_summary(product_summary)
    manifest = {
        "schema_version": "standard_peak_formal_product_output_v1",
        "source_run_id": source_run_id,
        "status": "pass",
        "product_output_role": "standard_peak_backfill_formal_product_output",
        "activation_output_mode": text_value(
            product_summary.get("activation_output_mode"),
        ),
        "activation_decision_scope": text_value(
            activation_inputs_summary.get("activation_decision_scope"),
        ),
        "must_not_regress_basis": text_value(
            activation_inputs_summary.get("must_not_regress_basis"),
        ),
        "standard_peak_gate_status": text_value(
            activation_inputs_summary.get("standard_peak_gate_status"),
        ),
        "productization_summary_json": str(productization.summary_json),
        "source_alignment_matrix_tsv": str(source_alignment_matrix_tsv),
        "source_shadow_projection_tsv": text_value(
            product_summary.get("source_shadow_projection_tsv"),
        ),
        "source_shadow_projection_sha256": text_value(
            activation_inputs_summary.get("source_shadow_projection_sha256"),
        ),
        "formal_product_output_dir": str(output_dir),
        "coverage_status": text_value(coverage.get("coverage_status")),
        "queue_row_count": text_value(coverage.get("queue_row_count")),
        "covered_queue_row_count": text_value(
            coverage.get("covered_queue_row_count"),
        ),
        "missing_queue_rank_count": text_value(
            coverage.get("missing_queue_rank_count"),
        ),
        "duplicate_queue_rank_count": text_value(
            coverage.get("duplicate_queue_rank_count"),
        ),
        "artifact_sha256": copied,
        "product_matrix_tsv": str(output_dir / "alignment_matrix.tsv"),
        "matrix_identity_tsv": str(output_dir / "alignment_matrix_identity.tsv"),
        "activation_value_delta_tsv": str(output_dir / "activation_value_delta.tsv"),
        "activation_application_summary_tsv": str(
            output_dir / "activation_application_summary.tsv",
        ),
        "activation_hypothesis_identity_tsv": str(
            output_dir / "activation_hypothesis_identity.tsv",
        ),
        "selected_activation_row_count": text_value(
            product_summary.get("selected_activation_row_count"),
        ),
        "matrix_cells_written": text_value(product_summary.get("matrix_cells_written")),
        "activation_value_delta_written_count": text_value(
            product_summary.get("activation_value_delta_written_count"),
        ),
        "skipped_non_standard_reason_count": text_value(
            product_summary.get("skipped_non_standard_reason_count"),
        ),
    }
    manifest_json = output_dir / "standard_peak_formal_product_manifest.json"
    manifest_json.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return output_dir, manifest_json


def _publish_formal_product_output_to_alignment_output(
    *,
    formal_product_output_dir: Path,
    formal_product_manifest_json: Path,
    publish_alignment_matrix_tsv: Path,
    publish_alignment_matrix_identity_tsv: Path,
    source_run_id: str,
) -> tuple[Path, Path]:
    raise ValueError(SOURCE_MATRIX_REPUBLISHER_RETIRED_MESSAGE)


def _verify_manifest_hash(
    expected_hashes: Mapping[object, object],
    name: str,
    path: Path,
) -> None:
    expected = text_value(expected_hashes.get(name))
    if not expected:
        raise ValueError(f"formal product manifest missing hash for {name}")
    actual = file_sha256(path, uppercase=False)
    if actual.lower() != expected.lower():
        raise ValueError(f"formal product hash mismatch for {name}")


def _backup_once(path: Path, suffix: str) -> None:
    backup = _backup_path(path, suffix)
    if backup.exists():
        return
    shutil.copy2(path, backup)


def _backup_path(path: Path, suffix: str) -> Path:
    return path.with_name(f"{path.stem}.{suffix}{path.suffix}")


def _clear_stale_formal_product_output(
    *,
    output_dir: Path,
    source_alignment_matrix_tsv: Path,
) -> None:
    if not output_dir.exists():
        return
    source_dir = source_alignment_matrix_tsv.parent.resolve()
    if output_dir.resolve() == source_dir:
        return
    for name in FORMAL_PRODUCT_FILENAMES:
        target = output_dir / name
        if not target.exists() or not target.is_file():
            continue
        if target.resolve() == source_alignment_matrix_tsv.resolve():
            continue
        target.unlink()


def _load_activation_inputs_summary(
    product_summary: Mapping[str, object],
) -> dict[str, object]:
    activation_inputs_dir = text_value(
        product_summary.get("standard_peak_activation_inputs_dir"),
    )
    if not activation_inputs_dir:
        return {}
    summary_json = (
        Path(activation_inputs_dir) / "standard_peak_activation_inputs_summary.json"
    )
    if not summary_json.exists():
        return {}
    return _load_json_mapping(summary_json)


def _load_chunk_summary(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    summary = _load_json_mapping(path)
    summary["_summary_json"] = str(path)
    required: tuple[str, ...] = (
        "status",
        "start_rank",
        "overlay_selected_row_count",
    )
    if text_value(summary.get("projection_mode")) == "deferred_to_consolidation":
        required = (*required, "authorized_ms1_pattern_tsv")
    else:
        required = (*required, "shadow_projection_cells_tsv")
    missing = [key for key in required if not text_value(summary.get(key))]
    if missing:
        raise ValueError(f"{path}: missing summary fields: {', '.join(missing)}")
    return summary


def _coverage_summary(
    chunks: Sequence[Mapping[str, object]],
    *,
    review_queue_tsv: Path | None,
) -> dict[str, object]:
    rank_counts: Counter[int] = Counter()
    for chunk in chunks:
        for rank in _chunk_overlay_ranks(chunk):
            rank_counts[rank] += 1

    queue_row_count = 0
    missing_count = 0
    coverage_status = "not_checked"
    status_reasons: list[str] = []
    if review_queue_tsv is not None:
        queue_rows = read_tsv_required(review_queue_tsv, ("feature_family_id",))
        queue_row_count = len(queue_rows)
        missing = [
            rank for rank in range(1, queue_row_count + 1) if rank_counts[rank] == 0
        ]
        missing_count = len(missing)
        coverage_status = "complete" if missing_count == 0 else "incomplete"
        if missing:
            preview = ",".join(str(rank) for rank in missing[:10])
            suffix = f";+{len(missing) - 10} more" if len(missing) > 10 else ""
            status_reasons.append(f"queue_coverage_missing:{preview}{suffix}")

    duplicate_count = sum(1 for count in rank_counts.values() if count > 1)
    if duplicate_count:
        status_reasons.append(f"queue_coverage_duplicate_ranks:{duplicate_count}")
    covered_count = sum(1 for count in rank_counts.values() if count > 0)
    return {
        "queue_row_count": queue_row_count,
        "covered_queue_row_count": covered_count,
        "duplicate_queue_rank_count": duplicate_count,
        "missing_queue_rank_count": missing_count,
        "coverage_status": coverage_status,
        "status_reasons": tuple(status_reasons),
    }


def _chunk_overlay_ranks(chunk: Mapping[str, object]) -> tuple[int, ...]:
    overlay_tsv_text = text_value(chunk.get("overlay_batch_summary_tsv"))
    if overlay_tsv_text:
        overlay_tsv = Path(overlay_tsv_text)
        if overlay_tsv.exists():
            overlay_rows = read_tsv_required(overlay_tsv, ("rank",))
            return tuple(
                _positive_int(row.get("rank"), "rank") for row in overlay_rows
            )
    start = _positive_int(chunk.get("start_rank"), "start_rank")
    selected = _positive_int(
        chunk.get("overlay_selected_row_count"),
        "overlay_selected_row_count",
    )
    return tuple(range(start, start + selected))


def _merge_shadow_projection_rows(
    chunks: Sequence[Mapping[str, object]],
) -> tuple[dict[str, str], ...]:
    selected: dict[tuple[str, ...], dict[str, str]] = {}
    order: list[tuple[str, ...]] = []
    for chunk in chunks:
        shadow_tsv = Path(text_value(chunk.get("shadow_projection_cells_tsv")))
        rows = read_tsv_required(shadow_tsv, SHADOW_PRODUCTION_PROJECTION_COLUMNS)
        for row in rows:
            projection_row = dict(row)
            key = _shadow_projection_merge_key(projection_row)
            existing = selected.get(key)
            if existing is None:
                selected[key] = projection_row
                order.append(key)
                continue
            selected[key] = _select_shadow_projection_row(
                existing,
                projection_row,
                key=key,
            )
    return tuple(selected[key] for key in order)


def _shadow_projection_merge_key(row: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        text_value(row.get(column))
        for column in _SHADOW_PROJECTION_MERGE_KEY_COLUMNS
    )


def _select_shadow_projection_row(
    existing: dict[str, str],
    candidate: dict[str, str],
    *,
    key: tuple[str, ...],
) -> dict[str, str]:
    _raise_for_conflicting_shadow_projection_rows(existing, candidate, key=key)
    if _shadow_projection_row_rank(candidate) > _shadow_projection_row_rank(existing):
        return candidate
    return existing


def _raise_for_conflicting_shadow_projection_rows(
    left: Mapping[str, str],
    right: Mapping[str, str],
    *,
    key: tuple[str, ...],
) -> None:
    left_accepts = _is_product_affecting_projection_row(left)
    right_accepts = _is_product_affecting_projection_row(right)
    if left_accepts and right_accepts:
        conflict_columns = [
            column
            for column in _SHADOW_PROJECTION_ACCEPT_CONFLICT_COLUMNS
            if text_value(left.get(column)) != text_value(right.get(column))
        ]
        if conflict_columns:
            raise ValueError(
                "conflicting accepted shadow projection rows for "
                f"{_shadow_projection_key_label(key)}: "
                + ",".join(conflict_columns),
            )
    if left_accepts == right_accepts:
        return
    blocked = right if left_accepts else left
    if text_value(blocked.get("shadow_decision")) == "block":
        raise ValueError(
            "conflicting accepted/blocked shadow projection rows for "
            f"{_shadow_projection_key_label(key)}",
        )


def _is_product_affecting_projection_row(row: Mapping[str, str]) -> bool:
    return (
        text_value(row.get("shadow_decision")) == "accept"
        and text_value(row.get("projected_matrix_written")) == "TRUE"
    )


def _shadow_projection_row_rank(row: Mapping[str, str]) -> tuple[int, int]:
    if _is_product_affecting_projection_row(row):
        return (4, _row_specificity(row))
    if text_value(row.get("current_matrix_written")) == "TRUE":
        return (3, _row_specificity(row))
    if text_value(row.get("shadow_decision")) == "block":
        return (2, _row_specificity(row))
    if text_value(row.get("missing_evidence")) == "missing_overlay_evidence":
        return (0, _row_specificity(row))
    return (1, _row_specificity(row))


def _row_specificity(row: Mapping[str, str]) -> int:
    return sum(1 for value in row.values() if text_value(value))


def _shadow_projection_key_label(key: Sequence[str]) -> str:
    return "/".join(
        f"{column}={value or '<blank>'}"
        for column, value in zip(_SHADOW_PROJECTION_MERGE_KEY_COLUMNS, key)
    )


def _summary_row(
    *,
    source_run_id: str,
    status: str,
    status_reasons: Sequence[str],
    chunk_count: int,
    coverage: Mapping[str, object],
    merged_shadow_tsv: Path,
    merged_shadow_row_count: int,
    productization: StandardPeakBackfillProductizationOutputs,
    product_summary: Mapping[str, object],
    formal_product_output_dir: Path | None,
    formal_product_manifest_json: Path | None,
    published_alignment_output_dir: Path | None,
    published_alignment_manifest_json: Path | None,
) -> dict[str, str]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "status": status,
        "chunk_count": str(chunk_count),
        "queue_row_count": text_value(coverage.get("queue_row_count")),
        "covered_queue_row_count": text_value(
            coverage.get("covered_queue_row_count"),
        ),
        "duplicate_queue_rank_count": text_value(
            coverage.get("duplicate_queue_rank_count"),
        ),
        "missing_queue_rank_count": text_value(
            coverage.get("missing_queue_rank_count"),
        ),
        "coverage_status": text_value(coverage.get("coverage_status")),
        "merged_shadow_projection_cells_tsv": str(merged_shadow_tsv),
        "merged_shadow_projection_row_count": str(merged_shadow_row_count),
        "productization_summary_json": str(productization.summary_json),
        "activated_alignment_matrix_tsv": _path_text(
            productization.activated_matrix_tsv,
        ),
        "activation_value_delta_tsv": _path_text(
            productization.activation_value_delta_tsv,
        ),
        "matrix_cells_written": text_value(
            product_summary.get("matrix_cells_written"),
        ),
        "activation_acceptance_status": text_value(
            product_summary.get("activation_acceptance_status"),
        ),
        "activation_application_status": text_value(
            product_summary.get("activation_application_status"),
        ),
        "reconciliation_gallery_html": _path_text(
            productization.reconciliation_gallery_html,
        ),
        "formal_product_output_dir": _path_text(formal_product_output_dir),
        "formal_product_manifest_json": _path_text(formal_product_manifest_json),
        "published_alignment_output_dir": _path_text(published_alignment_output_dir),
        "published_alignment_manifest_json": _path_text(
            published_alignment_manifest_json,
        ),
        "status_reasons": ";".join(status_reasons),
    }


def _load_json_mapping(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _positive_int(value: object, field: str) -> int:
    try:
        parsed = int(text_value(value))
    except ValueError as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if parsed < 1:
        raise ValueError(f"invalid {field}: {value!r}")
    return parsed


def _path_text(path: Path | None) -> str:
    return "" if path is None else str(path)
