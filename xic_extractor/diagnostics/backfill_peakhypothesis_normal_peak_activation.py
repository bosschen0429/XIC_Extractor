"""End-to-end normal-peak PeakHypothesis backfill activation.

The module keeps matrix mutation owned by product_activation. It only
orchestrates the existing normal-peak decision, FULL COHORT transfer, activation
bridge, matrix-only application, and post-application acceptance gates.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment.shared_peak_identity_explanation import (
    product_activation,
)
from xic_extractor.diagnostics import (
    backfill_peakhypothesis_activation_acceptance,
    backfill_peakhypothesis_activation_bridge,
    backfill_peakhypothesis_activation_transfer,
    backfill_peakhypothesis_activation_trial,
    backfill_peakhypothesis_normal_peak_decision,
)
from xic_extractor.diagnostics.diagnostic_io import (
    format_diagnostic_value,
    read_tsv_required,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "backfill_peakhypothesis_normal_peak_activation_v1"

SUMMARY_COLUMNS = (
    "schema_version",
    "source_run_id",
    "validation_scope",
    "normal_peak_activation_status",
    "normal_peak_decision_source",
    "normal_peak_decision_row_count",
    "required_backfill_count",
    "standard_blocked_count",
    "review_only_nonstandard_count",
    "transfer_status",
    "transfer_promotion_row_count",
    "activation_decision_count",
    "changed_matrix_cell_count",
    "activation_acceptance_status",
    "hard_fail_reasons",
    "normal_peak_decisions_tsv",
    "activation_transfer_tsv",
    "activation_decisions_tsv",
    "activated_alignment_matrix_tsv",
    "activation_value_delta_tsv",
    "activation_acceptance_tsv",
    "matrix_contract_changed",
    "product_behavior_changed",
    "next_action",
)


@dataclass(frozen=True)
class NormalPeakActivationOutputs:
    summary_tsv: Path
    summary_json: Path
    status: str
    normal_peak_decisions_tsv: Path | None = None
    activation_transfer_tsv: Path | None = None
    activation_decisions_tsv: Path | None = None
    activated_alignment_matrix_tsv: Path | None = None
    activation_value_delta_tsv: Path | None = None
    activation_acceptance_tsv: Path | None = None


def run_normal_peak_activation(
    *,
    output_dir: Path,
    alignment_matrix_tsv: Path,
    alignment_matrix_identity_tsv: Path,
    alignment_review_tsv: Path,
    promotion_cells_tsv: Path | None = None,
    full_cohort_acceptance_gate_tsv: Path | None = None,
    full_cohort_manual_verdict_tsv: Path | None = None,
    machine_shape_evidence_tsv: Path | None = None,
    normal_peak_decisions_tsv: Path | None = None,
    activation_trial_tsv: Path | None = None,
    current_full_cohort_artifact_dir: Path | None = None,
    source_run_id: str = "",
    validation_scope: str = "full_cohort_current_writer_matrix_diff",
) -> NormalPeakActivationOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    decision_rows, decision_path, decision_source = _normal_peak_decisions(
        output_dir=output_dir,
        promotion_cells_tsv=promotion_cells_tsv,
        full_cohort_acceptance_gate_tsv=full_cohort_acceptance_gate_tsv,
        full_cohort_manual_verdict_tsv=full_cohort_manual_verdict_tsv,
        machine_shape_evidence_tsv=machine_shape_evidence_tsv,
        normal_peak_decisions_tsv=normal_peak_decisions_tsv,
        source_run_id=source_run_id,
    )
    decision_counts = _decision_counts(decision_rows)
    required_decisions = _required_normal_peak_rows(decision_rows)
    standard_blocked = _standard_blocked_rows(decision_rows)
    if standard_blocked:
        summary = _summary_row(
            source_run_id=source_run_id,
            validation_scope=validation_scope,
            status="fail",
            decision_source=decision_source,
            decision_counts=decision_counts,
            hard_fail_reasons=("standard_normal_peak_blocked",),
            normal_peak_decisions_tsv=decision_path,
            next_action="review_standard_normal_peak_blockers",
        )
        return _write_outputs(output_dir, summary)

    if not required_decisions:
        summary = _summary_row(
            source_run_id=source_run_id,
            validation_scope=validation_scope,
            status="pass",
            decision_source=decision_source,
            decision_counts=decision_counts,
            hard_fail_reasons=(),
            normal_peak_decisions_tsv=decision_path,
            next_action="no_normal_peak_backfill_required",
        )
        return _write_outputs(output_dir, summary)

    trial_path = _activation_trial_path(
        output_dir=output_dir,
        activation_trial_tsv=activation_trial_tsv,
        current_full_cohort_artifact_dir=current_full_cohort_artifact_dir,
        normal_peak_decision_rows=decision_rows,
        full_cohort_manual_verdict_tsv=full_cohort_manual_verdict_tsv,
        source_run_id=source_run_id,
    )
    transfer_outputs, transfer_summary = _activation_transfer(
        output_dir=output_dir,
        normal_peak_decisions_tsv=decision_path,
        required_decisions=required_decisions,
        activation_trial_tsv=trial_path,
        source_run_id=source_run_id,
    )
    if transfer_summary.get("transfer_status") != "pass":
        summary = _summary_row(
            source_run_id=source_run_id,
            validation_scope=validation_scope,
            status="fail",
            decision_source=decision_source,
            decision_counts=decision_counts,
            hard_fail_reasons=_split_semicolon(
                text_value(transfer_summary.get("hard_fail_reasons")),
            )
            or ("activation_transfer_failed",),
            normal_peak_decisions_tsv=decision_path,
            activation_transfer_tsv=transfer_outputs.transfer_tsv,
            transfer_summary=transfer_summary,
            next_action="review_full_cohort_activation_transfer_blockers",
        )
        return _write_outputs(output_dir, summary)

    promotion_rows = _read_tsv_any(transfer_outputs.promotion_cells_tsv)
    bridge_outputs, bridge_summary = _activation_bridge(
        output_dir=output_dir,
        promotion_rows=promotion_rows,
        promotion_cells_tsv=transfer_outputs.promotion_cells_tsv,
        alignment_matrix_tsv=alignment_matrix_tsv,
        alignment_matrix_identity_tsv=alignment_matrix_identity_tsv,
        source_run_id=source_run_id,
    )
    hard_bridge_reasons = _split_semicolon(
        text_value(bridge_summary.get("hard_fail_reasons")),
    )
    if hard_bridge_reasons and hard_bridge_reasons != [
        "activation_acceptance_requires_matrix_diff_validation"
    ]:
        summary = _summary_row(
            source_run_id=source_run_id,
            validation_scope=validation_scope,
            status="fail",
            decision_source=decision_source,
            decision_counts=decision_counts,
            hard_fail_reasons=tuple(hard_bridge_reasons),
            normal_peak_decisions_tsv=decision_path,
            activation_transfer_tsv=transfer_outputs.transfer_tsv,
            activation_decisions_tsv=bridge_outputs.activation_decisions_tsv,
            transfer_summary=transfer_summary,
            bridge_summary=bridge_summary,
            next_action=text_value(bridge_summary.get("next_action"))
            or "review_activation_bridge_failures",
        )
        return _write_outputs(output_dir, summary)

    decision_rows_for_apply = _read_tsv_any(bridge_outputs.activation_decisions_tsv)
    if not decision_rows_for_apply:
        summary = _summary_row(
            source_run_id=source_run_id,
            validation_scope=validation_scope,
            status="pass",
            decision_source=decision_source,
            decision_counts=decision_counts,
            hard_fail_reasons=(),
            normal_peak_decisions_tsv=decision_path,
            activation_transfer_tsv=transfer_outputs.transfer_tsv,
            activation_decisions_tsv=bridge_outputs.activation_decisions_tsv,
            transfer_summary=transfer_summary,
            bridge_summary=bridge_summary,
            next_action="normal_peak_backfill_already_present_or_no_missing_cells",
        )
        return _write_outputs(output_dir, summary)

    apply_outputs = product_activation.apply_activation_to_alignment_matrix_outputs(
        activation_decisions_tsv=bridge_outputs.activation_decisions_tsv,
        activation_acceptance_tsv=bridge_outputs.activation_acceptance_tsv,
        activation_values_tsv=transfer_outputs.promotion_cells_tsv,
        alignment_matrix_tsv=alignment_matrix_tsv,
        alignment_matrix_identity_tsv=alignment_matrix_identity_tsv,
        alignment_review_tsv=alignment_review_tsv,
        output_dir=output_dir / "activated_matrix",
        require_acceptance_pass=False,
    )
    applied_keys = {
        (
            text_value(row.get("peak_hypothesis_id")),
            text_value(row.get("sample_id")),
        )
        for row in decision_rows_for_apply
    }
    applied_promotions = tuple(
        row
        for row in promotion_rows
        if (
            text_value(row.get("peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
        )
        in applied_keys
    )
    acceptance_index = (
        backfill_peakhypothesis_activation_acceptance.build_activation_acceptance(
            promotion_rows=applied_promotions,
            activation_decision_rows=decision_rows_for_apply,
            preflight_rows=_read_tsv_any(
                bridge_outputs.activation_matrix_preflight_tsv,
            ),
            application_summary_rows=_read_tsv_any(apply_outputs.summary_tsv),
            value_delta_rows=_read_tsv_any(apply_outputs.value_delta_tsv),
            input_matrix_rows=_read_tsv_any(alignment_matrix_tsv),
            input_identity_rows=_read_tsv_any(alignment_matrix_identity_tsv),
            output_matrix_rows=_read_tsv_any(apply_outputs.matrix_tsv),
            output_identity_rows=_read_tsv_any(apply_outputs.matrix_identity_tsv),
            source_run_id=source_run_id,
            validation_scope=validation_scope,
        )
    )
    write_acceptance = (
        backfill_peakhypothesis_activation_acceptance.write_activation_acceptance_outputs
    )
    acceptance_outputs = write_acceptance(
        output_dir / "activation_acceptance",
        acceptance_index,
    )
    acceptance_status = acceptance_index.acceptance_row["acceptance_status"]
    hard_fail_reasons = _split_semicolon(
        acceptance_index.acceptance_row["hard_fail_reasons"],
    )
    summary = _summary_row(
        source_run_id=source_run_id,
        validation_scope=validation_scope,
        status=acceptance_status,
        decision_source=decision_source,
        decision_counts=decision_counts,
        hard_fail_reasons=tuple(hard_fail_reasons),
        normal_peak_decisions_tsv=decision_path,
        activation_transfer_tsv=transfer_outputs.transfer_tsv,
        activation_decisions_tsv=bridge_outputs.activation_decisions_tsv,
        activated_alignment_matrix_tsv=apply_outputs.matrix_tsv,
        activation_value_delta_tsv=apply_outputs.value_delta_tsv,
        activation_acceptance_tsv=acceptance_outputs.acceptance_tsv,
        transfer_summary=transfer_summary,
        bridge_summary=bridge_summary,
        acceptance_summary=acceptance_index.summary,
        product_behavior_changed=acceptance_status == "pass"
        and text_value(acceptance_index.acceptance_row["changed_matrix_cell_count"])
        != "0",
        next_action=acceptance_index.acceptance_row["next_action"],
    )
    return _write_outputs(output_dir, summary)


def _normal_peak_decisions(
    *,
    output_dir: Path,
    promotion_cells_tsv: Path | None,
    full_cohort_acceptance_gate_tsv: Path | None,
    full_cohort_manual_verdict_tsv: Path | None,
    machine_shape_evidence_tsv: Path | None,
    normal_peak_decisions_tsv: Path | None,
    source_run_id: str,
) -> tuple[tuple[dict[str, str], ...], Path, str]:
    if normal_peak_decisions_tsv is not None:
        return (
            read_tsv_required(
                normal_peak_decisions_tsv,
                backfill_peakhypothesis_normal_peak_decision.DECISION_COLUMNS,
            ),
            normal_peak_decisions_tsv,
            "provided_normal_peak_decisions_tsv",
        )
    missing = [
        label
        for label, path in (
            ("promotion_cells_tsv", promotion_cells_tsv),
            ("full_cohort_acceptance_gate_tsv", full_cohort_acceptance_gate_tsv),
            ("machine_shape_evidence_tsv", machine_shape_evidence_tsv),
        )
        if path is None
    ]
    if missing:
        raise ValueError(
            "normal peak decision generation missing inputs: " + ", ".join(missing),
        )
    assert promotion_cells_tsv is not None
    assert full_cohort_acceptance_gate_tsv is not None
    assert machine_shape_evidence_tsv is not None
    build_decision_index = (
        backfill_peakhypothesis_normal_peak_decision.build_normal_peak_decision_index
    )
    index = build_decision_index(
        promotion_rows=read_tsv_required(
            promotion_cells_tsv,
            backfill_peakhypothesis_normal_peak_decision.PROMOTION_REQUIRED_COLUMNS,
        ),
        full_cohort_acceptance_gate_rows=read_tsv_required(
            full_cohort_acceptance_gate_tsv,
            backfill_peakhypothesis_normal_peak_decision.FULL_COHORT_SLICE_REQUIRED_COLUMNS,
        ),
        manual_verdict_rows=read_tsv_required(
            full_cohort_manual_verdict_tsv,
            backfill_peakhypothesis_normal_peak_decision.MANUAL_VERDICT_REQUIRED_COLUMNS,
        )
        if full_cohort_manual_verdict_tsv is not None
        else (),
        machine_shape_rows=read_tsv_required(
            machine_shape_evidence_tsv,
            backfill_peakhypothesis_normal_peak_decision.MACHINE_SHAPE_REQUIRED_COLUMNS,
        ),
        source_run_id=source_run_id,
    )
    outputs = (
        backfill_peakhypothesis_normal_peak_decision.write_normal_peak_decision_outputs(
            output_dir / "normal_peak_decision", index
        )
    )
    return (
        tuple(dict(row) for row in index.rows),
        outputs.decisions_tsv,
        (
            "generated_from_promotion_full_cohort_manual_machine_evidence"
            if full_cohort_manual_verdict_tsv is not None
            else "generated_from_promotion_full_cohort_machine_evidence"
        ),
    )


def _activation_trial_path(
    *,
    output_dir: Path,
    activation_trial_tsv: Path | None,
    current_full_cohort_artifact_dir: Path | None,
    normal_peak_decision_rows: Sequence[Mapping[str, Any]],
    full_cohort_manual_verdict_tsv: Path | None,
    source_run_id: str,
) -> Path:
    if activation_trial_tsv is not None:
        return activation_trial_tsv
    if current_full_cohort_artifact_dir is None:
        raise ValueError(
            "normal peak activation requires --activation-trial-tsv or "
            "--current-full_cohort-artifact-dir",
        )
    index = backfill_peakhypothesis_activation_trial.build_activation_trial_index(
        current_full_cohort_artifact_dir=current_full_cohort_artifact_dir,
        normal_peak_decision_rows=normal_peak_decision_rows,
        manual_verdict_rows=(
            backfill_peakhypothesis_activation_trial.read_manual_verdict_rows(
                full_cohort_manual_verdict_tsv
            )
            if full_cohort_manual_verdict_tsv is not None
            else ()
        ),
        source_run_id=source_run_id,
    )
    outputs = backfill_peakhypothesis_activation_trial.write_activation_trial_outputs(
        output_dir / "activation_trial", index
    )
    return outputs.trial_tsv


def _activation_transfer(
    *,
    output_dir: Path,
    normal_peak_decisions_tsv: Path,
    required_decisions: Sequence[Mapping[str, Any]],
    activation_trial_tsv: Path,
    source_run_id: str,
) -> tuple[
    backfill_peakhypothesis_activation_transfer.ActivationTransferOutputs,
    dict[str, Any],
]:
    source_artifact_sha256 = (
        backfill_peakhypothesis_activation_transfer.input_bundle_sha256(
            normal_peak_decisions_tsv,
            activation_trial_tsv,
        )
    )
    index = backfill_peakhypothesis_activation_transfer.build_activation_transfer_index(
        normal_peak_decision_rows=required_decisions,
        activation_trial_rows=(
            backfill_peakhypothesis_activation_transfer.read_activation_trial_rows(
                activation_trial_tsv
            )
        ),
        source_artifact_sha256=source_artifact_sha256,
        source_run_id=source_run_id,
    )
    outputs = (
        backfill_peakhypothesis_activation_transfer.write_activation_transfer_outputs(
            output_dir / "activation_transfer", index
        )
    )
    return outputs, index.summary


def _activation_bridge(
    *,
    output_dir: Path,
    promotion_rows: Sequence[Mapping[str, Any]],
    promotion_cells_tsv: Path,
    alignment_matrix_tsv: Path,
    alignment_matrix_identity_tsv: Path,
    source_run_id: str,
) -> tuple[
    backfill_peakhypothesis_activation_bridge.ActivationBridgeOutputs,
    dict[str, Any],
]:
    index = backfill_peakhypothesis_activation_bridge.build_activation_bridge(
        promotion_rows,
        public_matrix_rows=_read_tsv_any(alignment_matrix_tsv),
        matrix_identity_rows=_read_tsv_any(alignment_matrix_identity_tsv),
        source_run_id=source_run_id,
    )
    outputs = backfill_peakhypothesis_activation_bridge.write_activation_bridge_outputs(
        output_dir / "activation_bridge",
        index,
    )
    # Keep the argument visible in the signature; it documents the provenance
    # TSV used as activation_values.tsv by matrix-only application.
    _ = promotion_cells_tsv
    return outputs, index.summary


def _decision_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    decisions = Counter(text_value(row.get("normal_peak_decision")) for row in rows)
    return {
        "row_count": len(rows),
        "required_backfill_count": decisions.get("require_backfill", 0),
        "review_only_nonstandard_count": decisions.get(
            "review_only_nonstandard_peak",
            0,
        ),
        "standard_blocked_count": len(_standard_blocked_rows(rows)),
    }


def _required_normal_peak_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        row
        for row in rows
        if text_value(row.get("normal_peak_decision")) == "require_backfill"
        and text_value(row.get("normal_peak_backfill_required")).upper() == "TRUE"
        and not text_value(row.get("normal_peak_decision_blockers"))
    )


def _standard_blocked_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        row
        for row in rows
        if text_value(row.get("area_policy")) == "standard_assessable_area"
        and text_value(row.get("normal_peak_decision")) != "require_backfill"
    )


def _summary_row(
    *,
    source_run_id: str,
    validation_scope: str,
    status: str,
    decision_source: str,
    decision_counts: Mapping[str, int],
    hard_fail_reasons: Sequence[str],
    normal_peak_decisions_tsv: Path,
    activation_transfer_tsv: Path | None = None,
    activation_decisions_tsv: Path | None = None,
    activated_alignment_matrix_tsv: Path | None = None,
    activation_value_delta_tsv: Path | None = None,
    activation_acceptance_tsv: Path | None = None,
    transfer_summary: Mapping[str, Any] | None = None,
    bridge_summary: Mapping[str, Any] | None = None,
    acceptance_summary: Mapping[str, Any] | None = None,
    product_behavior_changed: bool = False,
    next_action: str,
) -> dict[str, Any]:
    transfer_summary = transfer_summary or {}
    bridge_summary = bridge_summary or {}
    acceptance_summary = acceptance_summary or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "validation_scope": validation_scope,
        "normal_peak_activation_status": status,
        "normal_peak_decision_source": decision_source,
        "normal_peak_decision_row_count": decision_counts.get("row_count", 0),
        "required_backfill_count": decision_counts.get("required_backfill_count", 0),
        "standard_blocked_count": decision_counts.get("standard_blocked_count", 0),
        "review_only_nonstandard_count": decision_counts.get(
            "review_only_nonstandard_count",
            0,
        ),
        "transfer_status": text_value(transfer_summary.get("transfer_status")),
        "transfer_promotion_row_count": text_value(
            transfer_summary.get("promotion_row_count"),
        ),
        "activation_decision_count": text_value(
            bridge_summary.get("activation_decision_row_count"),
        ),
        "changed_matrix_cell_count": text_value(
            acceptance_summary.get("changed_matrix_cell_count"),
        ),
        "activation_acceptance_status": text_value(
            acceptance_summary.get("acceptance_status"),
        ),
        "hard_fail_reasons": ";".join(hard_fail_reasons),
        "normal_peak_decisions_tsv": _path_text(normal_peak_decisions_tsv),
        "activation_transfer_tsv": _path_text(activation_transfer_tsv),
        "activation_decisions_tsv": _path_text(activation_decisions_tsv),
        "activated_alignment_matrix_tsv": _path_text(activated_alignment_matrix_tsv),
        "activation_value_delta_tsv": _path_text(activation_value_delta_tsv),
        "activation_acceptance_tsv": _path_text(activation_acceptance_tsv),
        "matrix_contract_changed": False,
        "product_behavior_changed": product_behavior_changed,
        "next_action": next_action,
    }


def _write_outputs(
    output_dir: Path,
    summary: Mapping[str, Any],
) -> NormalPeakActivationOutputs:
    summary_tsv = output_dir / "backfill_peakhypothesis_normal_peak_activation.tsv"
    summary_json = (
        output_dir / "backfill_peakhypothesis_normal_peak_activation_summary.json"
    )
    write_tsv(
        summary_tsv,
        [summary],
        SUMMARY_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(
            {key: _json_value(value) for key, value in summary.items()},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return NormalPeakActivationOutputs(
        summary_tsv=summary_tsv,
        summary_json=summary_json,
        status=text_value(summary.get("normal_peak_activation_status")),
        normal_peak_decisions_tsv=_path_or_none(
            summary.get("normal_peak_decisions_tsv"),
        ),
        activation_transfer_tsv=_path_or_none(summary.get("activation_transfer_tsv")),
        activation_decisions_tsv=_path_or_none(summary.get("activation_decisions_tsv")),
        activated_alignment_matrix_tsv=_path_or_none(
            summary.get("activated_alignment_matrix_tsv"),
        ),
        activation_value_delta_tsv=_path_or_none(
            summary.get("activation_value_delta_tsv"),
        ),
        activation_acceptance_tsv=_path_or_none(
            summary.get("activation_acceptance_tsv"),
        ),
    )


def _read_tsv_any(path: Path) -> tuple[dict[str, str], ...]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle, delimiter="\t"))


def _split_semicolon(value: str) -> list[str]:
    return [part for part in value.split(";") if part]


def _path_text(path: Path | None) -> str:
    return "" if path is None else str(path)


def _path_or_none(value: object) -> Path | None:
    text = text_value(value)
    return Path(text) if text else None


def _json_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    text = text_value(value)
    if text in {"TRUE", "FALSE"}:
        return text == "TRUE"
    try:
        return int(text)
    except ValueError:
        return text
