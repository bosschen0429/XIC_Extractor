"""No-RAW 85RAW trial counters for normal-peak backfill activation."""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.diagnostics.diagnostic_io import (
    bool_value,
    format_diagnostic_value,
    optional_float,
    read_tsv_required,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "backfill_peakhypothesis_85raw_activation_trial_v1"
POLICY_ID = "same_peak_normal_peak_override"

NORMAL_DECISION_REQUIRED_COLUMNS = (
    "peak_hypothesis_id",
    "sample_stem",
    "raw85_matched_peak_hypothesis_id",
    "raw85_include_in_primary_matrix",
    "raw85_consolidation_state",
    "manual_same_peak_verdict",
    "same_peak_verdict",
    "same_peak_verdict_source",
    "normal_peak_decision",
    "normal_peak_backfill_required",
    "normal_peak_decision_blockers",
)

MANUAL_VERDICT_REQUIRED_COLUMNS = (
    "sample_stem",
    "source_peak_hypothesis_id",
    "raw85_matched_peak_hypothesis_id",
    "raw85_include_in_primary_matrix",
    "raw85_consolidation_state",
    "reviewer_verdict",
)

TRIAL_COLUMNS = (
    "schema_version",
    "source_run_id",
    "policy_id",
    "source_peak_hypothesis_id",
    "sample_stem",
    "raw85_matched_peak_hypothesis_id",
    "raw85_include_in_primary_matrix",
    "raw85_consolidation_state",
    "manual_same_peak_verdict",
    "same_peak_verdict",
    "same_peak_verdict_source",
    "normal_peak_decision",
    "normal_peak_backfill_required",
    "current_public_matrix_written",
    "current_public_matrix_value",
    "trial_action",
    "matrix_diff_expected",
    "trial_blockers",
)


@dataclass(frozen=True)
class ActivationTrialIndex:
    trial_rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


@dataclass(frozen=True)
class ActivationTrialOutputs:
    trial_tsv: Path
    summary_json: Path


def read_normal_peak_decision_rows(path: Path) -> tuple[dict[str, str], ...]:
    return read_tsv_required(path, NORMAL_DECISION_REQUIRED_COLUMNS)


def read_manual_verdict_rows(path: Path) -> tuple[dict[str, str], ...]:
    return read_tsv_required(path, MANUAL_VERDICT_REQUIRED_COLUMNS)


def build_activation_trial_index(
    *,
    current_85raw_artifact_dir: Path,
    normal_peak_decision_rows: Sequence[Mapping[str, Any]],
    manual_verdict_rows: Sequence[Mapping[str, Any]],
    source_run_id: str = "",
) -> ActivationTrialIndex:
    artifact_paths = _artifact_paths(current_85raw_artifact_dir)
    metadata = _read_json(artifact_paths["metadata"])
    timing = _read_json(artifact_paths["timing"])
    matrix_lookup = _public_matrix_values_for_targets(
        matrix_tsv=artifact_paths["matrix"],
        matrix_identity_tsv=artifact_paths["identity"],
        targets=_raw85_targets(normal_peak_decision_rows),
    )
    manual_by_key = _manual_by_source_key(manual_verdict_rows)
    trial_rows = tuple(
        _trial_row(
            row,
            manual_row=manual_by_key.get(_source_key(row), {}),
            matrix_lookup=matrix_lookup,
            source_run_id=source_run_id,
        )
        for row in normal_peak_decision_rows
    )
    summary = _summary(
        trial_rows=trial_rows,
        normal_peak_decision_rows=normal_peak_decision_rows,
        manual_verdict_rows=manual_verdict_rows,
        matrix_lookup=matrix_lookup,
        metadata=metadata,
        timing=timing,
        source_run_id=source_run_id,
    )
    return ActivationTrialIndex(trial_rows=trial_rows, summary=summary)


def write_activation_trial_outputs(
    output_dir: Path,
    index: ActivationTrialIndex,
) -> ActivationTrialOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    trial_tsv = output_dir / "backfill_peakhypothesis_85raw_activation_trial.tsv"
    summary_json = (
        output_dir / "backfill_peakhypothesis_85raw_activation_trial_summary.json"
    )
    write_tsv(
        trial_tsv,
        index.trial_rows,
        TRIAL_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(index.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return ActivationTrialOutputs(trial_tsv=trial_tsv, summary_json=summary_json)


def _artifact_paths(artifact_dir: Path) -> dict[str, Path]:
    paths = {
        "matrix": artifact_dir / "alignment_matrix.tsv",
        "identity": artifact_dir / "alignment_matrix_identity.tsv",
        "metadata": artifact_dir / "alignment_run_metadata.json",
        "timing": artifact_dir / "timing.json",
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("; ".join(missing))
    return paths


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        text_value(row.get("peak_hypothesis_id")),
        text_value(row.get("sample_stem")),
    )


def _manual_source_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        text_value(row.get("source_peak_hypothesis_id")),
        text_value(row.get("sample_stem")),
    )


def _manual_by_source_key(
    manual_verdict_rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    keyed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in manual_verdict_rows:
        key = _manual_source_key(row)
        if key in keyed:
            raise ValueError(f"duplicate manual verdict key: {key}")
        keyed[key] = row
    return keyed


def _raw85_targets(
    normal_peak_decision_rows: Sequence[Mapping[str, Any]],
) -> set[tuple[str, str]]:
    return {
        (
            text_value(row.get("raw85_matched_peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
        )
        for row in normal_peak_decision_rows
        if text_value(row.get("raw85_matched_peak_hypothesis_id"))
        and text_value(row.get("sample_stem"))
    }


def _public_matrix_values_for_targets(
    *,
    matrix_tsv: Path,
    matrix_identity_tsv: Path,
    targets: set[tuple[str, str]],
) -> dict[str, Any]:
    identity_by_index: dict[int, str] = {}
    with matrix_identity_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"matrix_row_index", "peak_hypothesis_id"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                f"{matrix_identity_tsv}: missing required columns: {sorted(missing)}"
            )
        for row in reader:
            index = _positive_int(row.get("matrix_row_index"))
            peak_hypothesis_id = text_value(row.get("peak_hypothesis_id"))
            if index > 0 and peak_hypothesis_id:
                identity_by_index[index] = peak_hypothesis_id

    values: dict[tuple[str, str], str] = {}
    sample_columns: tuple[str, ...]
    row_count = 0
    with matrix_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = tuple(reader.fieldnames or ())
        if "Mz" not in fieldnames or "RT" not in fieldnames:
            raise ValueError(f"{matrix_tsv}: expected public Mz/RT matrix")
        sample_columns = tuple(
            column for column in fieldnames if column not in {"Mz", "RT"}
        )
        target_peak_ids = {peak_id for peak_id, _sample in targets}
        for row_count, row in enumerate(reader, start=1):
            peak_hypothesis_id = identity_by_index.get(row_count, "")
            if peak_hypothesis_id not in target_peak_ids:
                continue
            for sample in sample_columns:
                key = (peak_hypothesis_id, sample)
                if key in targets:
                    values[key] = text_value(row.get(sample))
    return {
        "values": values,
        "sample_count": len(sample_columns),
        "matrix_row_count": row_count,
    }


def _trial_row(
    row: Mapping[str, Any],
    *,
    manual_row: Mapping[str, Any],
    matrix_lookup: Mapping[str, Any],
    source_run_id: str,
) -> dict[str, Any]:
    raw85_peak_hypothesis_id = text_value(row.get("raw85_matched_peak_hypothesis_id"))
    sample_stem = text_value(row.get("sample_stem"))
    matrix_values = matrix_lookup["values"]
    current_value = matrix_values.get((raw85_peak_hypothesis_id, sample_stem), "")
    current_written = bool(current_value)
    blockers = _trial_blockers(row, manual_row)
    required = _required_backfill(row)
    if blockers or not required:
        action = "blocked"
        matrix_diff_expected = False
    elif current_written:
        action = "already_primary_matrix_written"
        matrix_diff_expected = False
    else:
        action = "would_write_normal_peak_override"
        matrix_diff_expected = True
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "policy_id": POLICY_ID,
        "source_peak_hypothesis_id": text_value(row.get("peak_hypothesis_id")),
        "sample_stem": sample_stem,
        "raw85_matched_peak_hypothesis_id": raw85_peak_hypothesis_id,
        "raw85_include_in_primary_matrix": _bool_text(
            row.get("raw85_include_in_primary_matrix")
        ),
        "raw85_consolidation_state": _state(row, manual_row),
        "manual_same_peak_verdict": _manual_verdict(row, manual_row),
        "same_peak_verdict": _same_peak_verdict(row, manual_row),
        "same_peak_verdict_source": _same_peak_verdict_source(row, manual_row),
        "normal_peak_decision": text_value(row.get("normal_peak_decision")),
        "normal_peak_backfill_required": required,
        "current_public_matrix_written": current_written,
        "current_public_matrix_value": current_value,
        "trial_action": action,
        "matrix_diff_expected": matrix_diff_expected,
        "trial_blockers": ";".join(blockers),
    }


def _trial_blockers(
    row: Mapping[str, Any],
    manual_row: Mapping[str, Any],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if text_value(row.get("normal_peak_decision_blockers")):
        blockers.append("normal_peak_decision_blockers_present")
    if text_value(row.get("normal_peak_decision")) != "require_backfill":
        blockers.append("normal_peak_decision_not_required")
    if bool_value(row.get("normal_peak_backfill_required")) is not True:
        blockers.append("normal_peak_backfill_not_required")
    if _same_peak_verdict(row, manual_row) != "same_peak_supported":
        blockers.append("same_peak_conflict")
    if not text_value(row.get("raw85_matched_peak_hypothesis_id")):
        blockers.append("missing_raw85_peak_hypothesis_id")
    return tuple(blockers)


def _manual_verdict(row: Mapping[str, Any], manual_row: Mapping[str, Any]) -> str:
    return text_value(
        manual_row.get("reviewer_verdict")
        or row.get("manual_same_peak_verdict")
    )


def _same_peak_verdict(row: Mapping[str, Any], manual_row: Mapping[str, Any]) -> str:
    return text_value(
        manual_row.get("reviewer_verdict")
        or row.get("same_peak_verdict")
        or row.get("manual_same_peak_verdict")
    )


def _same_peak_verdict_source(
    row: Mapping[str, Any],
    manual_row: Mapping[str, Any],
) -> str:
    if text_value(manual_row.get("reviewer_verdict")):
        return "manual_review"
    return text_value(row.get("same_peak_verdict_source")) or (
        "legacy_manual_same_peak_verdict"
        if text_value(row.get("manual_same_peak_verdict"))
        else ""
    )


def _state(row: Mapping[str, Any], manual_row: Mapping[str, Any]) -> str:
    return text_value(
        manual_row.get("raw85_consolidation_state")
        or row.get("raw85_consolidation_state")
    )


def _required_backfill(row: Mapping[str, Any]) -> bool:
    return (
        text_value(row.get("normal_peak_decision")) == "require_backfill"
        and bool_value(row.get("normal_peak_backfill_required")) is True
    )


def _summary(
    *,
    trial_rows: Sequence[Mapping[str, Any]],
    normal_peak_decision_rows: Sequence[Mapping[str, Any]],
    manual_verdict_rows: Sequence[Mapping[str, Any]],
    matrix_lookup: Mapping[str, Any],
    metadata: Mapping[str, Any],
    timing: Mapping[str, Any],
    source_run_id: str,
) -> dict[str, Any]:
    actions = Counter(text_value(row.get("trial_action")) for row in trial_rows)
    states = Counter(
        text_value(row.get("raw85_consolidation_state")) for row in trial_rows
    )
    same_peak = Counter(
        _same_peak_verdict(row, {}) for row in normal_peak_decision_rows
    )
    trial_blocker_count = sum(
        1 for row in trial_rows if text_value(row.get("trial_blockers"))
    )
    same_peak_conflict_count = sum(
        1 for row in trial_rows if _same_peak_verdict(row, {}) != "same_peak_supported"
    )
    expected_matrix_diff_count = actions["would_write_normal_peak_override"]
    hard_fail_reasons = _hard_fail_reasons(
        normal_peak_decision_rows=normal_peak_decision_rows,
        manual_verdict_rows=manual_verdict_rows,
        same_peak_conflict_count=same_peak_conflict_count,
        trial_blocker_count=trial_blocker_count,
    )
    stages = _stage_elapsed(timing)
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "policy_id": POLICY_ID,
        "validation_mode": "artifact_only_no_raw",
        "product_behavior_changed": False,
        "matrix_contract_changed": False,
        "trial_status": "pass" if not hard_fail_reasons else "fail",
        "normal_peak_decision_row_count": len(normal_peak_decision_rows),
        "manual_verdict_row_count": len(manual_verdict_rows),
        "normal_peak_required_count": sum(
            1 for row in normal_peak_decision_rows if _required_backfill(row)
        ),
        "nonstandard_blocked_count": sum(
            1
            for row in normal_peak_decision_rows
            if text_value(row.get("normal_peak_decision"))
            == "review_only_nonstandard_peak"
        ),
        "normal_peak_blocked_count": sum(
            1
            for row in normal_peak_decision_rows
            if text_value(row.get("normal_peak_decision")) == "blocked"
        ),
        "same_peak_supported_count": same_peak["same_peak_supported"],
        "same_peak_conflict_count": same_peak_conflict_count,
        "primary_loser_count": states["primary_loser"],
        "primary_winner_count": states["primary_winner"],
        "consolidation_override_count": states["primary_loser"],
        "already_primary_matrix_written_count": actions[
            "already_primary_matrix_written"
        ],
        "expected_matrix_diff_count": expected_matrix_diff_count,
        "matrix_diff_count": expected_matrix_diff_count,
        "unexpected_diff_count": 0 if not hard_fail_reasons else trial_blocker_count,
        "unexpected_diff_basis": "artifact_only_projection_no_matrix_written",
        "candidate_count": _candidate_count(timing),
        "sample_count": matrix_lookup["sample_count"],
        "matrix_row_count": matrix_lookup["matrix_row_count"],
        "output_level": text_value(metadata.get("output_level")),
        "backfill_scope": text_value(metadata.get("backfill_scope")),
        "audit_evidence_mode": text_value(metadata.get("audit_evidence_mode")),
        "owner_backfill_elapsed_sec": stages.get("alignment.owner_backfill", 0.0),
        "build_matrix_elapsed_sec": stages.get("alignment.build_matrix", 0.0),
        # Retained to preserve the persisted v1 summary schema. Current runs
        # report 0.0 because these stages were retired by Issue #302. Remove
        # the keys only with a versioned v2 schema migration.
        "claim_registry_elapsed_sec": stages.get("alignment.claim_registry", 0.0),
        "primary_consolidation_elapsed_sec": stages.get(
            "alignment.primary_consolidation",
            0.0,
        ),
        "write_outputs_elapsed_sec": stages.get("alignment.write_outputs", 0.0),
        "hard_fail_reasons": ";".join(hard_fail_reasons),
        "next_action": (
            "implement_normal_peak_override_activation_transfer"
            if not hard_fail_reasons
            else "review_85raw_activation_trial_blockers"
        ),
    }
    return summary


def _hard_fail_reasons(
    *,
    normal_peak_decision_rows: Sequence[Mapping[str, Any]],
    manual_verdict_rows: Sequence[Mapping[str, Any]],
    same_peak_conflict_count: int,
    trial_blocker_count: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not normal_peak_decision_rows:
        reasons.append("normal_peak_decisions_missing")
    if not manual_verdict_rows and any(
        not _same_peak_verdict(row, {}) for row in normal_peak_decision_rows
    ):
        reasons.append("same_peak_verdicts_missing")
    if same_peak_conflict_count:
        reasons.append("same_peak_conflict")
    if trial_blocker_count:
        reasons.append("trial_rows_blocked")
    return tuple(reasons)


def _candidate_count(timing: Mapping[str, Any]) -> int:
    for record in timing.get("records", []):
        if record.get("stage") == "alignment.read_candidates":
            metrics = record.get("metrics") or {}
            return int(metrics.get("candidate_count") or 0)
    return 0


def _stage_elapsed(timing: Mapping[str, Any]) -> dict[str, float]:
    stages: dict[str, float] = {}
    for record in timing.get("records", []):
        stage = text_value(record.get("stage"))
        if not stage:
            continue
        elapsed = optional_float(record.get("elapsed_sec"))
        if elapsed is None:
            continue
        stages[stage] = elapsed
    return stages


def _positive_int(value: object) -> int:
    try:
        parsed = int(text_value(value))
    except ValueError:
        return -1
    return parsed if parsed > 0 else -1


def _bool_text(value: object) -> str:
    parsed = bool_value(value)
    if parsed is True:
        return "TRUE"
    if parsed is False:
        return "FALSE"
    return text_value(value)
