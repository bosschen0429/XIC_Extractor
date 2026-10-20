"""Daily diagnostic packet for row-completion confidence artifacts."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.diagnostics.row_completion_confidence_schema import (
    DISAGREEMENT_COLUMNS,
    NO_AUTHORITY_STATEMENT,
    PRODUCT_GATE_NO_AUTHORITY_STATEMENT,
    SCHEMA_VERSION,
    SENTINEL_COLUMNS,
    SUMMARY_COLUMNS,
    build_artifact_manifest,
)
from xic_extractor.tabular_io import (
    file_sha256,
    numeric_equal,
    read_tsv_required,
    read_tsv_with_header,
    text_value,
    write_tsv,
)

_SENTINEL_REQUIRED_COLUMNS = (
    "rank",
    "feature_family_id",
    "issue_class",
    "severity_score",
    "recommended_action",
    "reason",
)
_REQUIRED_METRIC_PATH_KEYS = (
    "current/alignment_review.tsv",
    "current/alignment_matrix.tsv",
    "current/alignment_matrix_identity.tsv",
    "current/alignment_backfill_cell_evidence.tsv",
    "current/alignment_owner_backfill_seed_audit.tsv",
    "current_health/alignment_health_summary.json",
    "current_health/alignment_health_family_sentinels.tsv",
)


@dataclass(frozen=True)
class RowCompletionOutputs:
    summary_json: Path
    summary_tsv: Path
    sentinels_tsv: Path
    disagreements_tsv: Path
    report_md: Path


@dataclass(frozen=True)
class _DailyPacket:
    payload: dict[str, object]
    summary_rows: tuple[dict[str, object], ...]
    sentinel_rows: tuple[dict[str, object], ...]
    disagreement_rows: tuple[dict[str, object], ...]


def build_daily_confidence_packet(
    *,
    current_alignment_dir: Path,
    current_health_dir: Path,
    output_dir: Path,
    baseline_alignment_dir: Path | None = None,
    run_id: str = "row_completion_confidence",
    generation_context: str = "unknown",
    gate_mode: str = "diagnostic",
) -> RowCompletionOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_gate_mode = _normalize_gate_mode(gate_mode)
    packet = _daily_packet(
        current_alignment_dir=current_alignment_dir,
        current_health_dir=current_health_dir,
        baseline_alignment_dir=baseline_alignment_dir,
        run_id=run_id,
        generation_context=generation_context,
        gate_mode=resolved_gate_mode,
    )
    return _write_outputs(packet=packet, output_dir=output_dir)


def _daily_packet(
    *,
    current_alignment_dir: Path,
    current_health_dir: Path,
    baseline_alignment_dir: Path | None,
    run_id: str,
    generation_context: str,
    gate_mode: str,
) -> _DailyPacket:
    required_paths = _required_paths(
        current_alignment_dir=current_alignment_dir,
        current_health_dir=current_health_dir,
        baseline_alignment_dir=baseline_alignment_dir,
    )
    manifest_root = _manifest_root(required_paths.values())
    manifest_result = build_artifact_manifest(
        required_paths,
        root=manifest_root,
        run_id=run_id,
        generation_context=generation_context,
    )
    if not manifest_result.run_ok or manifest_result.manifest is None:
        return _missing_evidence_packet(
            run_id=run_id,
            generation_context=generation_context,
            gate_mode=gate_mode,
            baseline_binding=_baseline_binding(baseline_alignment_dir),
            reason=manifest_result.reason,
        )

    try:
        health_summary_path = required_paths[
            "current_health/alignment_health_summary.json"
        ]
        health_sentinels_path = required_paths[
            "current_health/alignment_health_family_sentinels.tsv"
        ]
        health_summary = json.loads(health_summary_path.read_text(encoding="utf-8"))
        sentinel_source_rows = read_tsv_required(
            health_sentinels_path,
            _SENTINEL_REQUIRED_COLUMNS,
        )
        _validate_sentinel_rows(sentinel_source_rows)
        summary_metrics, row_flag_counts = _validated_health_metrics(health_summary)
        drift_count = _selected_value_drift_count(
            current_matrix_tsv=required_paths["current/alignment_matrix.tsv"],
            current_identity_tsv=required_paths[
                "current/alignment_matrix_identity.tsv"
            ],
            baseline_matrix_tsv=required_paths.get("baseline/alignment_matrix.tsv"),
            baseline_identity_tsv=required_paths.get(
                "baseline/alignment_matrix_identity.tsv",
            ),
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return _missing_evidence_packet(
            run_id=run_id,
            generation_context=generation_context,
            gate_mode=gate_mode,
            baseline_binding=_baseline_binding(baseline_alignment_dir),
            reason=str(exc),
        )

    baseline_binding = _baseline_binding(baseline_alignment_dir)
    gate_ok = True
    validation_tier = "diagnostic_only"
    review_utility = "stable"
    production_safety = "stable"
    authority_decision = "no_control_plane_change"
    status = "PASS"
    missing_evidence_code = ""
    manual_review_required = False
    product_gate_eligible = False
    if gate_mode == "product_gate" and baseline_alignment_dir is None:
        gate_ok = False
        validation_tier = "inconclusive"
        production_safety = "inconclusive"
        review_utility = "inconclusive"
        authority_decision = "baseline_required_for_product_gate"
        status = "INCONCLUSIVE"
        missing_evidence_code = "baseline_current_unbound"
        manual_review_required = True
    elif gate_mode == "product_gate":
        validation_tier = "shadow_ready"
        product_gate_eligible = True
    if drift_count > 0:
        gate_ok = gate_mode == "diagnostic"
        validation_tier = (
            "diagnostic_only" if gate_mode == "diagnostic" else "inconclusive"
        )
        production_safety = "inconclusive"
        review_utility = "inconclusive"
        authority_decision = "expected_diff_required"
        status = "FAIL"
        missing_evidence_code = "product_gate_required"
        manual_review_required = True
        product_gate_eligible = False
    elif gate_mode == "product_gate" and baseline_alignment_dir is not None:
        authority_decision = "shadow_gate_pass_no_control_plane_change"
    authority_statement = _no_authority_statement(gate_mode)

    manifest_dict = manifest_result.manifest.to_dict()
    summary_rows = (
        _summary_row(
            run_id=run_id,
            metric_name="duplicate_only_family_count",
            current_value=row_flag_counts.get("duplicate_only", 0),
            baseline_value="",
            delta="",
            direction="stable",
            evidence_source="alignment_health_summary.json",
            artifact_path=health_summary_path,
            root=manifest_root,
            reason="health packet row_flag_counts.duplicate_only",
            manifest_dict=manifest_dict,
            authority_statement=authority_statement,
        ),
        _summary_row(
            run_id=run_id,
            metric_name="zero_present_family_count",
            current_value=row_flag_counts.get("zero_present", 0),
            baseline_value="",
            delta="",
            direction="stable",
            evidence_source="alignment_health_summary.json",
            artifact_path=health_summary_path,
            root=manifest_root,
            reason="health packet row_flag_counts.zero_present",
            manifest_dict=manifest_dict,
            authority_statement=authority_statement,
        ),
        _summary_row(
            run_id=run_id,
            metric_name="high_backfill_dependency_count",
            current_value=row_flag_counts.get("high_backfill_dependency", 0),
            baseline_value="",
            delta="",
            direction="stable",
            evidence_source="alignment_health_summary.json",
            artifact_path=health_summary_path,
            root=manifest_root,
            reason="health packet row_flag_counts.high_backfill_dependency",
            manifest_dict=manifest_dict,
            authority_statement=authority_statement,
        ),
        _summary_row(
            run_id=run_id,
            metric_name="accepted_backfill_count",
            current_value=summary_metrics.get("accepted_backfill_count_total", 0),
            baseline_value="",
            delta="",
            direction="stable",
            evidence_source="alignment_health_summary.json",
            artifact_path=health_summary_path,
            root=manifest_root,
            reason="health packet accepted_backfill_count_total",
            manifest_dict=manifest_dict,
            authority_statement=authority_statement,
        ),
        _summary_row(
            run_id=run_id,
            metric_name="review_backfill_count",
            current_value=summary_metrics.get("review_backfill_count_total", 0),
            baseline_value="",
            delta="",
            direction="stable",
            evidence_source="alignment_health_summary.json",
            artifact_path=health_summary_path,
            root=manifest_root,
            reason="health packet review_backfill_count_total",
            manifest_dict=manifest_dict,
            authority_statement=authority_statement,
        ),
        _summary_row(
            run_id=run_id,
            metric_name="selected_value_drift",
            current_value=drift_count,
            baseline_value=0 if baseline_alignment_dir is not None else "",
            delta=drift_count if baseline_alignment_dir is not None else "",
            direction=(
                "unknown"
                if gate_mode == "product_gate" and baseline_alignment_dir is None
                else "increase"
                if drift_count > 0
                else "stable"
            ),
            evidence_source="alignment_matrix.tsv",
            artifact_path=required_paths["current/alignment_matrix.tsv"],
            root=manifest_root,
            reason=(
                "baseline non-empty matrix cells changed"
                if drift_count > 0
                else "baseline selected matrix cells unchanged"
                if baseline_alignment_dir is not None
                else "baseline required for product gate"
                if gate_mode == "product_gate"
                else "no baseline supplied"
            ),
            manifest_dict=manifest_dict,
            authority_statement=authority_statement,
            status=(
                "FAIL"
                if drift_count > 0
                else "INCONCLUSIVE"
                if gate_mode == "product_gate" and baseline_alignment_dir is None
                else "PASS"
            ),
        ),
    )

    sentinel_rows: tuple[dict[str, object], ...] = tuple(
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "rank": text_value(row.get("rank")),
            "case_id": _case_id(row),
            "lane": "daily_artifact",
            "case_type": text_value(row.get("issue_class")),
            "feature_family_id": text_value(row.get("feature_family_id")),
            "sample_stem": "",
            "production_safety_status": production_safety,
            "review_utility_status": review_utility,
            "issue_class": text_value(row.get("issue_class")),
            "severity_score": text_value(row.get("severity_score")),
            "evidence_source": "alignment_health_family_sentinels.tsv",
            "recommended_action": text_value(row.get("recommended_action")),
            "requires_manual_review": "TRUE",
            "reason": text_value(row.get("reason")),
        }
        for row in sentinel_source_rows
    )
    disagreement_rows = (_external_reviewer_signal_row(run_id=run_id),)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "generation_context": generation_context,
        "lane": "daily_artifact",
        "gate_mode": gate_mode,
        "validation_tier": validation_tier,
        "baseline_binding": baseline_binding,
        "run_ok": True,
        "gate_ok": gate_ok,
        "status": status,
        "manual_review_required": manual_review_required,
        "production_ready": False,
        "product_gate_eligible": product_gate_eligible,
        "production_safety": production_safety,
        "review_utility": review_utility,
        "authority_decision": authority_decision,
        "missing_evidence_code": missing_evidence_code,
        "external_reviewer_signal": "not_available",
        "no_authority_statement": authority_statement,
        "input_artifact_manifest": manifest_dict,
        "metrics": {
            "duplicate_only_family_count": row_flag_counts.get("duplicate_only", 0),
            "zero_present_family_count": row_flag_counts.get("zero_present", 0),
            "high_backfill_dependency_count": row_flag_counts.get(
                "high_backfill_dependency",
                0,
            ),
            "accepted_backfill_count": summary_metrics.get(
                "accepted_backfill_count_total",
                0,
            ),
            "review_backfill_count": summary_metrics.get(
                "review_backfill_count_total",
                0,
            ),
            "selected_value_drift": drift_count,
        },
    }
    return _DailyPacket(
        payload=payload,
        summary_rows=summary_rows,
        sentinel_rows=sentinel_rows,
        disagreement_rows=disagreement_rows,
    )


def _write_outputs(*, packet: _DailyPacket, output_dir: Path) -> RowCompletionOutputs:
    summary_json = output_dir / "row_completion_confidence_summary.json"
    summary_tsv = output_dir / "row_completion_confidence_summary.tsv"
    sentinels_tsv = output_dir / "row_completion_sentinels.tsv"
    legacy_sentinels_tsv = output_dir / "row_completion_family_sentinels.tsv"
    disagreements_tsv = output_dir / "row_completion_disagreements.tsv"
    report_md = output_dir / "row_completion_confidence_report.md"

    summary_json.write_text(
        json.dumps(packet.payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_tsv(summary_tsv, packet.summary_rows, SUMMARY_COLUMNS, lineterminator="\n")
    write_tsv(
        sentinels_tsv,
        packet.sentinel_rows,
        SENTINEL_COLUMNS,
        lineterminator="\n",
    )
    write_tsv(
        legacy_sentinels_tsv,
        packet.sentinel_rows,
        SENTINEL_COLUMNS,
        lineterminator="\n",
    )
    write_tsv(
        disagreements_tsv,
        packet.disagreement_rows,
        DISAGREEMENT_COLUMNS,
        lineterminator="\n",
    )
    report_md.write_text(_report_text(packet.payload), encoding="utf-8")
    return RowCompletionOutputs(
        summary_json=summary_json,
        summary_tsv=summary_tsv,
        sentinels_tsv=sentinels_tsv,
        disagreements_tsv=disagreements_tsv,
        report_md=report_md,
    )


def _required_paths(
    *,
    current_alignment_dir: Path,
    current_health_dir: Path,
    baseline_alignment_dir: Path | None,
) -> dict[str, Path]:
    paths = {
        key: value
        for key, value in {
            "current/alignment_review.tsv": (
                current_alignment_dir / "alignment_review.tsv"
            ),
            "current/alignment_matrix.tsv": (
                current_alignment_dir / "alignment_matrix.tsv"
            ),
            "current/alignment_matrix_identity.tsv": (
                current_alignment_dir / "alignment_matrix_identity.tsv"
            ),
            "current/alignment_backfill_cell_evidence.tsv": (
                current_alignment_dir / "alignment_backfill_cell_evidence.tsv"
            ),
            "current/alignment_owner_backfill_seed_audit.tsv": (
                current_alignment_dir / "alignment_owner_backfill_seed_audit.tsv"
            ),
            "current_health/alignment_health_summary.json": (
                current_health_dir / "alignment_health_summary.json"
            ),
            "current_health/alignment_health_family_sentinels.tsv": (
                current_health_dir / "alignment_health_family_sentinels.tsv"
            ),
        }.items()
    }
    if baseline_alignment_dir is not None:
        paths["baseline/alignment_matrix.tsv"] = (
            baseline_alignment_dir / "alignment_matrix.tsv"
        )
        paths["baseline/alignment_matrix_identity.tsv"] = (
            baseline_alignment_dir / "alignment_matrix_identity.tsv"
        )
    return paths


def _normalize_gate_mode(gate_mode: str) -> str:
    normalized = text_value(gate_mode).strip().lower().replace("-", "_")
    if normalized in {"", "diagnostic"}:
        return "diagnostic"
    if normalized == "product_gate":
        return "product_gate"
    raise ValueError(f"unsupported row-completion confidence gate_mode: {gate_mode!r}")


def _baseline_binding(baseline_alignment_dir: Path | None) -> str:
    return (
        "baseline_supplied"
        if baseline_alignment_dir is not None
        else "no_baseline_supplied"
    )


def _no_authority_statement(gate_mode: str) -> str:
    if gate_mode == "product_gate":
        return PRODUCT_GATE_NO_AUTHORITY_STATEMENT
    return NO_AUTHORITY_STATEMENT


def _manifest_root(paths: Iterable[Path]) -> Path:
    resolved = [str(Path(path).resolve()) for path in paths]
    return Path(os.path.commonpath(resolved))


def _selected_value_drift_count(
    *,
    current_matrix_tsv: Path,
    current_identity_tsv: Path,
    baseline_matrix_tsv: Path | None,
    baseline_identity_tsv: Path | None,
) -> int:
    if baseline_matrix_tsv is None:
        return 0
    if baseline_identity_tsv is None:
        raise ValueError("baseline alignment_matrix_identity.tsv is required")
    current_header, current_rows = read_tsv_with_header(
        current_matrix_tsv,
        encoding="utf-8-sig",
    )
    baseline_header, baseline_rows = read_tsv_with_header(
        baseline_matrix_tsv,
        encoding="utf-8-sig",
    )
    current_identity_header, current_identity_rows = read_tsv_with_header(
        current_identity_tsv,
        encoding="utf-8-sig",
    )
    baseline_identity_header, baseline_identity_rows = read_tsv_with_header(
        baseline_identity_tsv,
        encoding="utf-8-sig",
    )
    current_sample_columns = _matrix_sample_columns(current_header)
    baseline_sample_columns = _matrix_sample_columns(baseline_header)
    drift_count = 0
    drift_count += abs(len(current_rows) - len(baseline_rows))
    drift_count += len(set(current_sample_columns) ^ set(baseline_sample_columns))
    drift_count += _matrix_anchor_drift_count(
        current_header=current_header,
        current_rows=current_rows,
        baseline_header=baseline_header,
        baseline_rows=baseline_rows,
    )
    drift_count += _ordered_rows_drift_count(
        current_header=current_identity_header,
        current_rows=current_identity_rows,
        baseline_header=baseline_identity_header,
        baseline_rows=baseline_identity_rows,
    )
    sample_columns = [
        column for column in current_sample_columns if column in baseline_sample_columns
    ]
    for row_index, (current_row, baseline_row) in enumerate(
        zip(current_rows, baseline_rows, strict=False),
        start=1,
    ):
        if row_index > len(current_rows) or row_index > len(baseline_rows):
            break
        for column in sample_columns:
            current_value = text_value(current_row.get(column))
            baseline_value = text_value(baseline_row.get(column))
            if bool(current_value) != bool(baseline_value):
                drift_count += 1
                continue
            if not current_value and not baseline_value:
                continue
            if not numeric_equal(current_value, baseline_value):
                drift_count += 1
    return drift_count


def _matrix_sample_columns(header: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(column for column in header if column not in {"Mz", "RT"})


def _matrix_anchor_drift_count(
    *,
    current_header: tuple[str, ...],
    current_rows: list[dict[str, str]],
    baseline_header: tuple[str, ...],
    baseline_rows: list[dict[str, str]],
) -> int:
    drift_count = 0
    for column in ("Mz", "RT"):
        current_has_column = column in current_header
        baseline_has_column = column in baseline_header
        if current_has_column != baseline_has_column:
            drift_count += 1
            continue
        if not current_has_column or not baseline_has_column:
            continue
        for current_row, baseline_row in zip(
            current_rows,
            baseline_rows,
            strict=False,
        ):
            if not numeric_equal(
                text_value(current_row.get(column)),
                text_value(baseline_row.get(column)),
            ):
                drift_count += 1
    return drift_count


def _ordered_rows_drift_count(
    *,
    current_header: tuple[str, ...],
    current_rows: list[dict[str, str]],
    baseline_header: tuple[str, ...],
    baseline_rows: list[dict[str, str]],
) -> int:
    drift_count = 0
    if current_header != baseline_header:
        drift_count += 1
    compare_header = (
        current_header
        if current_header == baseline_header
        else tuple(column for column in current_header if column in baseline_header)
    )
    drift_count += abs(len(current_rows) - len(baseline_rows))
    for current_row, baseline_row in zip(current_rows, baseline_rows, strict=False):
        current_signature = tuple(
            text_value(current_row.get(column)) for column in compare_header
        )
        baseline_signature = tuple(
            text_value(baseline_row.get(column)) for column in compare_header
        )
        if current_signature != baseline_signature:
            drift_count += 1
    return drift_count


def _validated_health_metrics(
    health_summary: object,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    if not isinstance(health_summary, Mapping):
        raise ValueError("alignment_health_summary.json top-level must be an object")
    summary_metrics = health_summary.get("summary_metrics", {})
    if not isinstance(summary_metrics, Mapping):
        raise ValueError(
            "alignment_health_summary.json summary_metrics must be an object",
        )
    row_flag_counts = summary_metrics.get("row_flag_counts", {})
    if not isinstance(row_flag_counts, Mapping):
        raise ValueError(
            "alignment_health_summary.json row_flag_counts must be an object",
        )
    return summary_metrics, row_flag_counts


def _validate_sentinel_rows(rows: tuple[dict[str, str], ...]) -> None:
    for row in rows:
        rank = text_value(row.get("rank"))
        try:
            int(rank)
        except ValueError as exc:
            raise ValueError(
                f"alignment_health_family_sentinels.tsv rank is not numeric: {rank!r}",
            ) from exc


def _summary_row(
    *,
    run_id: str,
    metric_name: str,
    current_value: object,
    baseline_value: object,
    delta: object,
    direction: str,
    evidence_source: str,
    artifact_path: Path,
    root: Path,
    reason: str,
    manifest_dict: dict[str, object],
    authority_statement: str = NO_AUTHORITY_STATEMENT,
    status: str = "PASS",
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "lane": "daily_artifact",
        "metric_name": metric_name,
        "status": status,
        "current_value": current_value,
        "baseline_value": baseline_value,
        "delta": delta,
        "direction": direction,
        "evidence_source": evidence_source,
        "artifact_relpath": artifact_path.resolve()
        .relative_to(root.resolve())
        .as_posix(),
        "artifact_sha256": file_sha256(artifact_path),
        "reason": reason,
        "missing_evidence_code": "",
        "input_artifact_manifest": json.dumps(manifest_dict, sort_keys=True),
        "no_authority_statement": authority_statement,
    }


def _missing_evidence_packet(
    *,
    run_id: str,
    generation_context: str,
    gate_mode: str,
    baseline_binding: str,
    reason: str,
) -> _DailyPacket:
    authority_statement = _no_authority_statement(gate_mode)
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "generation_context": generation_context,
        "lane": "daily_artifact",
        "gate_mode": gate_mode,
        "validation_tier": (
            "diagnostic_only" if gate_mode == "diagnostic" else "inconclusive"
        ),
        "baseline_binding": baseline_binding,
        "run_ok": False,
        "gate_ok": False,
        "status": "INCONCLUSIVE",
        "manual_review_required": True,
        "production_ready": False,
        "product_gate_eligible": False,
        "production_safety": "inconclusive",
        "review_utility": "inconclusive",
        "authority_decision": "no_control_plane_change",
        "missing_evidence_code": "metric_source_unavailable",
        "external_reviewer_signal": "not_available",
        "no_authority_statement": authority_statement,
        "input_artifact_manifest": None,
        "reason": reason,
    }
    summary_rows = tuple(
        _missing_summary_row(
            run_id=run_id,
            artifact_relpath=artifact_relpath,
            reason=reason,
            authority_statement=authority_statement,
        )
        for artifact_relpath in _REQUIRED_METRIC_PATH_KEYS
    )
    return _DailyPacket(
        payload=payload,
        summary_rows=summary_rows,
        sentinel_rows=(),
        disagreement_rows=(_external_reviewer_signal_row(run_id=run_id),),
    )


def _missing_summary_row(
    *,
    run_id: str,
    artifact_relpath: str,
    reason: str,
    authority_statement: str,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "lane": "daily_artifact",
        "metric_name": "metric_source_unavailable",
        "status": "INCONCLUSIVE",
        "current_value": "",
        "baseline_value": "",
        "delta": "",
        "direction": "unknown",
        "evidence_source": artifact_relpath,
        "artifact_relpath": artifact_relpath,
        "artifact_sha256": "",
        "reason": reason,
        "missing_evidence_code": "metric_source_unavailable",
        "input_artifact_manifest": "",
        "no_authority_statement": authority_statement,
    }


def _external_reviewer_signal_row(*, run_id: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "disagreement_id": "external_reviewer_signal",
        "external_tool": "not_available",
        "external_run_id": "",
        "mapping_status": "not_available",
        "sample_id": "",
        "sample_stem": "",
        "feature_family_id": "",
        "external_feature_id": "",
        "mz_delta": "",
        "rt_delta_min": "",
        "classification": "not_available",
        "reason": "external_reviewer_signal=not_available",
    }


def _report_text(payload: dict[str, object]) -> str:
    metrics_obj = payload.get("metrics", {})
    metrics: Mapping[str, object] = (
        metrics_obj if isinstance(metrics_obj, Mapping) else {}
    )
    gate_mode = text_value(payload.get("gate_mode")) or "diagnostic"
    baseline_binding = text_value(payload.get("baseline_binding"))
    drift_note = (
        "not baseline-compared"
        if baseline_binding == "no_baseline_supplied"
        else "baseline compared"
    )
    return (
        "# Row Completion Confidence Daily Report\n\n"
        f"- Run ID: {payload['run_id']}\n"
        f"- Gate Mode: {gate_mode}\n"
        f"- Validation Tier: {payload['validation_tier']}\n"
        f"- Baseline Binding: {baseline_binding or 'unknown'}\n"
        f"- Run OK: {payload['run_ok']}\n"
        f"- Gate OK: {payload['gate_ok']}\n"
        f"- Missing Evidence Code: {payload['missing_evidence_code']}\n"
        f"- Production Safety: {payload['production_safety']}\n"
        f"- Review Utility: {payload['review_utility']}\n"
        f"- Authority Decision: {payload['authority_decision']}\n"
        f"- External Reviewer Signal: {payload['external_reviewer_signal']}\n\n"
        "## Metric Snapshot\n\n"
        "- duplicate_only_family_count: "
        f"{metrics.get('duplicate_only_family_count', '')}\n"
        f"- zero_present_family_count: {metrics.get('zero_present_family_count', '')}\n"
        "- high_backfill_dependency_count: "
        f"{metrics.get('high_backfill_dependency_count', '')}\n"
        f"- accepted_backfill_count: {metrics.get('accepted_backfill_count', '')}\n"
        f"- review_backfill_count: {metrics.get('review_backfill_count', '')}\n"
        f"- selected_value_drift: {metrics.get('selected_value_drift', '')}"
        f" ({drift_note})\n\n"
        "## Authority Statement\n\n"
        f"{payload['no_authority_statement']}\n"
    )


def _case_id(row: dict[str, str]) -> str:
    rank = text_value(row.get("rank")) or "0"
    family = text_value(row.get("feature_family_id")) or "family"
    safe_family = "".join(ch if ch.isalnum() else "_" for ch in family)
    return f"ROWCONF{int(rank):03d}_{safe_family}"
