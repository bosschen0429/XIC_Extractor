"""Build a CID-NL activation adopt/hold decision from accepted copy evidence."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.diagnostics.cid_nl_activation_copy_acceptance import (
    DEFAULT_OUTPUT_DIR as DEFAULT_ACCEPTANCE_DIR,
)
from tools.diagnostics.cid_nl_activation_copy_candidate import (
    DEFAULT_EXPECTED_DIFF_CONTRACTS,
    DEFAULT_FORBIDDEN_TRANSITION_TSVS,
    EXPECTED_DIFF_COLUMNS,
    FORBIDDEN_TRANSITION_COLUMNS,
    VALUE_DELTA_COLUMNS,
)
from tools.diagnostics.cid_nl_activation_copy_candidate import (
    DEFAULT_OUTPUT_DIR as DEFAULT_ACTIVATION_COPY_DIR,
)
from xic_extractor.tabular_io import read_tsv_required, text_value, write_tsv

SCHEMA_VERSION = "cid_nl_activation_adopt_gate"
DEFAULT_REVIEW_DIR = Path(
    "output/validation/cid_nl_default_activation_gallery_review",
)
DEFAULT_GATE_DIR = DEFAULT_REVIEW_DIR / "feature_inclusion_gate"
DEFAULT_FEATURE_GATE_SUMMARY_TSV = (
    DEFAULT_GATE_DIR / "cid_nl_feature_inclusion_gate_summary.tsv"
)
DEFAULT_ACTIVATION_COPY_SUMMARY_TSV = (
    DEFAULT_ACTIVATION_COPY_DIR / "cid_nl_activation_copy_candidate_summary.tsv"
)
DEFAULT_ACCEPTANCE_SUMMARY_TSV = (
    DEFAULT_ACCEPTANCE_DIR / "cid_nl_activation_copy_acceptance_summary.tsv"
)
DEFAULT_VALUE_DELTA_TSV = (
    DEFAULT_ACTIVATION_COPY_DIR / "cid_nl_activation_copy_value_delta.tsv"
)
DEFAULT_OUTPUT_DIR = DEFAULT_REVIEW_DIR / "activation_adopt_gate"
EXPECTED_CANDIDATE_CELL_COUNT = 147
EXPECTED_CONTRACT_CELL_COUNT = 95
EXPECTED_CANDIDATE_TRANSITION_COUNT = 20
EXPECTED_PRIMARY_EXPECTED_DIFF_CELL_COUNT = 73
EXPECTED_AGENT_RESOLVED_EXPECTED_DIFF_CELL_COUNT = 9
EXPECTED_MANUAL_RESOLVED_EXPECTED_DIFF_CELL_COUNT = 13
EXPECTED_AGENT_HOLD_CELL_COUNT = 24
EXPECTED_MANUAL_HOLD_CELL_COUNT = 0
EXPECTED_USER_REVIEW_CELL_COUNT = 0
EXPECTED_BLOCKED_CANDIDATE_CELL_COUNT = 28
EXPECTED_EXISTING_SUCCESSOR_CONTEXT_CELL_COUNT = 337
EXPECTED_OMITTED_NO_TARGET_CELL_COUNT = 27

FEATURE_GATE_SUMMARY_COLUMNS = (
    "overall_status",
    "validation_label",
    "candidate_cell_count",
    "expected_diff_cell_count",
    "agent_resolved_expected_diff_contract_cell_count",
    "manual_resolved_expected_diff_contract_cell_count",
    "agent_resolved_hold_cell_count",
    "manual_resolved_hold_cell_count",
    "user_review_cell_count",
    "blocked_candidate_cell_count",
    "existing_successor_context_cell_count",
    "omitted_no_target_cell_count",
    "product_writer_changed",
    "default_quant_matrix_changed",
    "candidate_rows_are_matrix_rows",
)
ACTIVATION_COPY_SUMMARY_COLUMNS = (
    "activation_copy_status",
    "validation_label",
    "candidate_contract_cell_count",
    "changed_matrix_cell_count",
    "candidate_transition_count",
    "product_writer_changed",
    "default_quant_matrix_changed",
    "workbook_gui_changed",
    "candidate_rows_are_matrix_rows",
)
ACCEPTANCE_SUMMARY_COLUMNS = (
    "acceptance_status",
    "validation_label",
    "contract_cell_count",
    "value_delta_cell_count",
    "matrix_changed_cell_count",
    "candidate_transition_count",
    "forbidden_overlap_count",
    "unexpected_matrix_change_count",
    "missing_matrix_change_count",
    "product_writer_changed",
    "default_quant_matrix_changed",
    "workbook_gui_changed",
    "candidate_rows_are_matrix_rows",
    "production_ready",
    "hard_fail_reasons",
    "next_action",
)
SUMMARY_COLUMNS = (
    "schema_version",
    "validation_label",
    "adopt_gate_status",
    "recommended_action",
    "contract_cell_count",
    "changed_matrix_cell_count",
    "candidate_transition_count",
    "primary_expected_diff_cell_count",
    "agent_resolved_expected_diff_cell_count",
    "manual_resolved_expected_diff_cell_count",
    "agent_hold_cell_count",
    "manual_hold_cell_count",
    "user_review_cell_count",
    "blocked_candidate_cell_count",
    "existing_successor_context_cell_count",
    "omitted_no_target_cell_count",
    "forbidden_overlap_count",
    "unexpected_matrix_change_count",
    "missing_matrix_change_count",
    "product_writer_changed",
    "default_quant_matrix_changed",
    "workbook_gui_changed",
    "candidate_rows_are_matrix_rows",
    "production_ready",
    "activation_bundle_adopt_ready",
    "hard_fail_reasons",
    "authority_statement",
)
MANIFEST_COLUMNS = (
    "schema_version",
    "transition_key",
    "contract_source",
    "contract_cell_count",
    "source_peak_hypothesis_id",
    "successor_peak_hypothesis_id",
    "successor_product_mz",
    "successor_neutral_loss_tag",
    "expected_product_effect",
    "product_authority_effect",
    "authority_gate",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        payload = build_activation_adopt_gate(
            expected_diff_contract_tsvs=tuple(
                args.expected_diff_contract_tsv or DEFAULT_EXPECTED_DIFF_CONTRACTS
            ),
            forbidden_transition_tsvs=tuple(
                args.forbidden_transition_tsv or DEFAULT_FORBIDDEN_TRANSITION_TSVS
            ),
            feature_gate_summary_tsv=args.feature_gate_summary_tsv,
            activation_copy_summary_tsv=args.activation_copy_summary_tsv,
            acceptance_summary_tsv=args.acceptance_summary_tsv,
            value_delta_tsv=args.value_delta_tsv,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"CID-NL activation adopt gate: {payload['summary_tsv']}")
    print(f"CID-NL activation adopt manifest: {payload['manifest_tsv']}")
    if args.require_pass and payload["adopt_gate_status"] != "adopt_ready":
        return 2
    return 0


def build_activation_adopt_gate(
    *,
    expected_diff_contract_tsvs: Sequence[Path],
    forbidden_transition_tsvs: Sequence[Path],
    feature_gate_summary_tsv: Path,
    activation_copy_summary_tsv: Path,
    acceptance_summary_tsv: Path,
    value_delta_tsv: Path,
    output_dir: Path,
) -> dict[str, Any]:
    contract_rows = _read_contract_rows(expected_diff_contract_tsvs)
    value_delta_rows = read_tsv_required(value_delta_tsv, VALUE_DELTA_COLUMNS)
    feature_summary = _single_row(
        feature_gate_summary_tsv,
        FEATURE_GATE_SUMMARY_COLUMNS,
    )
    copy_summary = _single_row(
        activation_copy_summary_tsv,
        ACTIVATION_COPY_SUMMARY_COLUMNS,
    )
    acceptance_summary = _single_row(
        acceptance_summary_tsv,
        ACCEPTANCE_SUMMARY_COLUMNS,
    )
    forbidden_overlap = _forbidden_overlap(contract_rows, forbidden_transition_tsvs)
    manifest_rows = _manifest_rows(contract_rows)
    hard_fail_reasons = _hard_fail_reasons(
        contract_rows=contract_rows,
        manifest_rows=manifest_rows,
        value_delta_rows=value_delta_rows,
        feature_summary=feature_summary,
        copy_summary=copy_summary,
        acceptance_summary=acceptance_summary,
        forbidden_overlap=forbidden_overlap,
    )
    summary = _summary_payload(
        contract_rows=contract_rows,
        manifest_rows=manifest_rows,
        feature_summary=feature_summary,
        copy_summary=copy_summary,
        acceptance_summary=acceptance_summary,
        forbidden_overlap_count=len(forbidden_overlap),
        hard_fail_reasons=hard_fail_reasons,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_tsv = output_dir / "cid_nl_activation_adopt_gate_summary.tsv"
    summary_json = output_dir / "cid_nl_activation_adopt_gate_summary.json"
    manifest_tsv = output_dir / "cid_nl_activation_adopt_manifest.tsv"
    write_tsv(summary_tsv, [summary], SUMMARY_COLUMNS)
    write_tsv(manifest_tsv, manifest_rows, MANIFEST_COLUMNS)
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **summary,
        "summary_tsv": str(summary_tsv),
        "summary_json": str(summary_json),
        "manifest_tsv": str(manifest_tsv),
    }


def _read_contract_rows(paths: Sequence[Path]) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in paths:
        source = _contract_source(path)
        for row in read_tsv_required(path, EXPECTED_DIFF_COLUMNS):
            key = (
                text_value(row.get("successor_peak_hypothesis_id")),
                text_value(row.get("sample_stem")),
                text_value(row.get("transition_key")),
            )
            if key in seen:
                raise ValueError(
                    "duplicate expected-diff contract row: "
                    f"{key[0]}|{key[1]}|{key[2]}",
                )
            seen.add(key)
            copied = dict(row)
            copied["_contract_source"] = source
            rows.append(copied)
    if not rows:
        raise ValueError("expected-diff contract is empty")
    return tuple(rows)


def _contract_source(path: Path) -> str:
    name = path.name
    if "manual_resolved" in name:
        return "manual_resolved"
    if "agent_resolved" in name:
        return "agent_resolved"
    return "primary_supported"


def _single_row(path: Path, columns: Sequence[str]) -> dict[str, str]:
    rows = read_tsv_required(path, columns)
    if len(rows) != 1:
        raise ValueError(f"{path}: expected exactly one summary row, got {len(rows)}")
    return dict(rows[0])


def _forbidden_overlap(
    contract_rows: Sequence[Mapping[str, str]],
    forbidden_transition_tsvs: Sequence[Path],
) -> set[str]:
    contract_keys = {text_value(row.get("transition_key")) for row in contract_rows}
    forbidden: set[str] = set()
    for path in forbidden_transition_tsvs:
        for row in read_tsv_required(path, FORBIDDEN_TRANSITION_COLUMNS):
            key = text_value(row.get("transition_key"))
            if key:
                forbidden.add(key)
    return contract_keys & forbidden


def _hard_fail_reasons(
    *,
    contract_rows: Sequence[Mapping[str, str]],
    manifest_rows: Sequence[Mapping[str, Any]],
    value_delta_rows: Sequence[Mapping[str, str]],
    feature_summary: Mapping[str, str],
    copy_summary: Mapping[str, str],
    acceptance_summary: Mapping[str, str],
    forbidden_overlap: set[str],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if text_value(feature_summary.get("overall_status")) != "pass":
        reasons.append("feature_gate_not_pass")
    if text_value(copy_summary.get("activation_copy_status")) != "pass":
        reasons.append("activation_copy_not_pass")
    if text_value(acceptance_summary.get("acceptance_status")) != "pass":
        reasons.append("acceptance_not_pass")
    if text_value(feature_summary.get("user_review_cell_count")) != "0":
        reasons.append("user_review_not_resolved")
    if forbidden_overlap:
        reasons.append("forbidden_transition_overlap")
    if text_value(acceptance_summary.get("forbidden_overlap_count")) != "0":
        reasons.append("acceptance_forbidden_overlap")
    if text_value(acceptance_summary.get("unexpected_matrix_change_count")) != "0":
        reasons.append("unexpected_matrix_change")
    if text_value(acceptance_summary.get("missing_matrix_change_count")) != "0":
        reasons.append("missing_matrix_change")
    if text_value(acceptance_summary.get("production_ready")).upper() != "FALSE":
        reasons.append("acceptance_overclaims_production_ready")
    for key in ("product_writer_changed", "default_quant_matrix_changed"):
        if _truthy(feature_summary.get(key)) or _truthy(copy_summary.get(key)):
            reasons.append(f"{key}_before_adopt_gate")
        if _truthy(acceptance_summary.get(key)):
            reasons.append(f"{key}_before_adopt_gate")
    if _truthy(copy_summary.get("workbook_gui_changed")) or _truthy(
        acceptance_summary.get("workbook_gui_changed"),
    ):
        reasons.append("workbook_gui_changed_before_adopt_gate")
    if _truthy(feature_summary.get("candidate_rows_are_matrix_rows")) or _truthy(
        copy_summary.get("candidate_rows_are_matrix_rows"),
    ):
        reasons.append("candidate_rows_treated_as_matrix_rows")
    if _truthy(acceptance_summary.get("candidate_rows_are_matrix_rows")):
        reasons.append("candidate_rows_treated_as_matrix_rows")

    contract_count = len(contract_rows)
    source_counts = Counter(
        text_value(row["_contract_source"]) for row in contract_rows
    )
    if _int(feature_summary, "candidate_cell_count") != EXPECTED_CANDIDATE_CELL_COUNT:
        reasons.append("candidate_cell_count_drift")
    if contract_count != EXPECTED_CONTRACT_CELL_COUNT:
        reasons.append("adopt_contract_cell_count_drift")
    if len(manifest_rows) != EXPECTED_CANDIDATE_TRANSITION_COUNT:
        reasons.append("candidate_transition_count_drift")
    for source, expected, reason in (
        (
            "primary_supported",
            EXPECTED_PRIMARY_EXPECTED_DIFF_CELL_COUNT,
            "primary_expected_diff_count_drift",
        ),
        (
            "agent_resolved",
            EXPECTED_AGENT_RESOLVED_EXPECTED_DIFF_CELL_COUNT,
            "agent_resolved_expected_diff_count_drift",
        ),
        (
            "manual_resolved",
            EXPECTED_MANUAL_RESOLVED_EXPECTED_DIFF_CELL_COUNT,
            "manual_resolved_expected_diff_count_drift",
        ),
    ):
        if source_counts[source] != expected:
            reasons.append(reason)
    for summary, field, expected, reason in (
        (
            feature_summary,
            "expected_diff_cell_count",
            EXPECTED_PRIMARY_EXPECTED_DIFF_CELL_COUNT,
            "feature_primary_expected_diff_count_drift",
        ),
        (
            feature_summary,
            "agent_resolved_expected_diff_contract_cell_count",
            EXPECTED_AGENT_RESOLVED_EXPECTED_DIFF_CELL_COUNT,
            "feature_agent_expected_diff_count_drift",
        ),
        (
            feature_summary,
            "manual_resolved_expected_diff_contract_cell_count",
            EXPECTED_MANUAL_RESOLVED_EXPECTED_DIFF_CELL_COUNT,
            "feature_manual_expected_diff_count_drift",
        ),
        (
            feature_summary,
            "agent_resolved_hold_cell_count",
            EXPECTED_AGENT_HOLD_CELL_COUNT,
            "agent_hold_count_drift",
        ),
        (
            feature_summary,
            "manual_resolved_hold_cell_count",
            EXPECTED_MANUAL_HOLD_CELL_COUNT,
            "manual_hold_count_drift",
        ),
        (
            feature_summary,
            "user_review_cell_count",
            EXPECTED_USER_REVIEW_CELL_COUNT,
            "user_review_count_drift",
        ),
        (
            feature_summary,
            "blocked_candidate_cell_count",
            EXPECTED_BLOCKED_CANDIDATE_CELL_COUNT,
            "blocked_candidate_count_drift",
        ),
        (
            feature_summary,
            "existing_successor_context_cell_count",
            EXPECTED_EXISTING_SUCCESSOR_CONTEXT_CELL_COUNT,
            "existing_successor_context_count_drift",
        ),
        (
            feature_summary,
            "omitted_no_target_cell_count",
            EXPECTED_OMITTED_NO_TARGET_CELL_COUNT,
            "omitted_no_target_count_drift",
        ),
        (
            copy_summary,
            "candidate_transition_count",
            EXPECTED_CANDIDATE_TRANSITION_COUNT,
            "copy_transition_count_drift",
        ),
        (
            acceptance_summary,
            "candidate_transition_count",
            EXPECTED_CANDIDATE_TRANSITION_COUNT,
            "acceptance_transition_count_drift",
        ),
    ):
        if _int(summary, field) != expected:
            reasons.append(reason)

    value_delta_count = len(value_delta_rows)
    if value_delta_count != contract_count:
        reasons.append("value_delta_contract_count_mismatch")
    for summary, field, reason in (
        (copy_summary, "candidate_contract_cell_count", "copy_contract_count_mismatch"),
        (copy_summary, "changed_matrix_cell_count", "copy_changed_count_mismatch"),
        (
            acceptance_summary,
            "contract_cell_count",
            "acceptance_contract_count_mismatch",
        ),
        (
            acceptance_summary,
            "value_delta_cell_count",
            "acceptance_value_delta_count_mismatch",
        ),
        (
            acceptance_summary,
            "matrix_changed_cell_count",
            "acceptance_matrix_change_count_mismatch",
        ),
    ):
        if _int(summary, field) != contract_count:
            reasons.append(reason)

    delta_keys = _contract_keys(value_delta_rows)
    contract_keys = _contract_keys(contract_rows)
    if delta_keys != contract_keys:
        reasons.append("value_delta_key_set_mismatch")
    for row in value_delta_rows:
        if text_value(row.get("original_matrix_value")):
            reasons.append("value_delta_original_not_blank")
        if text_value(row.get("value_changed")) != "TRUE":
            reasons.append("value_delta_not_changed")
        if text_value(row.get("product_authority_effect")) != (
            "diagnostic_only_no_authority_change"
        ):
            reasons.append("value_delta_product_authority_changed")
    for row in contract_rows:
        if text_value(row.get("authority_gate")) != (
            "candidate_only_expected_diff_required_no_product_write"
        ):
            reasons.append("contract_authority_gate_drift")
        if text_value(row.get("product_authority_effect")) != (
            "diagnostic_only_no_authority_change"
        ):
            reasons.append("contract_product_authority_changed")
    return tuple(sorted(set(reasons)))


def _manifest_rows(
    contract_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in contract_rows:
        key = (
            text_value(row["_contract_source"]),
            text_value(row["transition_key"]),
        )
        grouped[key].append(row)
    rows: list[dict[str, Any]] = []
    for (source, transition_key), items in sorted(grouped.items()):
        first = items[0]
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "transition_key": transition_key,
                "contract_source": source,
                "contract_cell_count": len(items),
                "source_peak_hypothesis_id": first["source_peak_hypothesis_id"],
                "successor_peak_hypothesis_id": first["successor_peak_hypothesis_id"],
                "successor_product_mz": first["successor_product_mz"],
                "successor_neutral_loss_tag": first["successor_neutral_loss_tag"],
                "expected_product_effect": first["expected_product_effect"],
                "product_authority_effect": first["product_authority_effect"],
                "authority_gate": first["authority_gate"],
            }
        )
    return rows


def _summary_payload(
    *,
    contract_rows: Sequence[Mapping[str, str]],
    manifest_rows: Sequence[Mapping[str, Any]],
    feature_summary: Mapping[str, str],
    copy_summary: Mapping[str, str],
    acceptance_summary: Mapping[str, str],
    forbidden_overlap_count: int,
    hard_fail_reasons: Sequence[str],
) -> dict[str, Any]:
    source_counts = Counter(
        text_value(row["_contract_source"]) for row in contract_rows
    )
    status = "adopt_ready" if not hard_fail_reasons else "hold"
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_label": "production_candidate_activation_adopt_gate",
        "adopt_gate_status": status,
        "recommended_action": (
            "prepare_explicit_default_activation_change"
            if status == "adopt_ready"
            else "hold_and_review_gate_failures"
        ),
        "contract_cell_count": len(contract_rows),
        "changed_matrix_cell_count": _int(
            acceptance_summary,
            "matrix_changed_cell_count",
        ),
        "candidate_transition_count": len(manifest_rows),
        "primary_expected_diff_cell_count": source_counts["primary_supported"],
        "agent_resolved_expected_diff_cell_count": source_counts["agent_resolved"],
        "manual_resolved_expected_diff_cell_count": source_counts["manual_resolved"],
        "agent_hold_cell_count": _int(
            feature_summary,
            "agent_resolved_hold_cell_count",
        ),
        "manual_hold_cell_count": _int(
            feature_summary,
            "manual_resolved_hold_cell_count",
        ),
        "user_review_cell_count": _int(feature_summary, "user_review_cell_count"),
        "blocked_candidate_cell_count": _int(
            feature_summary,
            "blocked_candidate_cell_count",
        ),
        "existing_successor_context_cell_count": _int(
            feature_summary,
            "existing_successor_context_cell_count",
        ),
        "omitted_no_target_cell_count": _int(
            feature_summary,
            "omitted_no_target_cell_count",
        ),
        "forbidden_overlap_count": forbidden_overlap_count,
        "unexpected_matrix_change_count": _int(
            acceptance_summary,
            "unexpected_matrix_change_count",
        ),
        "missing_matrix_change_count": _int(
            acceptance_summary,
            "missing_matrix_change_count",
        ),
        "product_writer_changed": (
            _truthy(feature_summary.get("product_writer_changed"))
            or _truthy(copy_summary.get("product_writer_changed"))
            or _truthy(acceptance_summary.get("product_writer_changed"))
        ),
        "default_quant_matrix_changed": (
            _truthy(feature_summary.get("default_quant_matrix_changed"))
            or _truthy(copy_summary.get("default_quant_matrix_changed"))
            or _truthy(acceptance_summary.get("default_quant_matrix_changed"))
        ),
        "workbook_gui_changed": _truthy(copy_summary.get("workbook_gui_changed"))
        or _truthy(acceptance_summary.get("workbook_gui_changed")),
        "candidate_rows_are_matrix_rows": (
            _truthy(feature_summary.get("candidate_rows_are_matrix_rows"))
            or _truthy(copy_summary.get("candidate_rows_are_matrix_rows"))
            or _truthy(acceptance_summary.get("candidate_rows_are_matrix_rows"))
        ),
        "production_ready": _truthy(acceptance_summary.get("production_ready")),
        "activation_bundle_adopt_ready": status == "adopt_ready",
        "hard_fail_reasons": ";".join(hard_fail_reasons),
        "authority_statement": (
            "Adopt-ready means the 95-cell validation-copy expected diff is "
            "coherent enough to prepare an explicit public activation change. "
            "This gate does not mutate ProductWriter, the default matrix, "
            "workbook, GUI, selected peak/area, or Backfill authority."
        ),
    }


def _contract_keys(
    rows: Sequence[Mapping[str, str]],
) -> set[tuple[str, str, str]]:
    return {
        (
            text_value(row.get("successor_peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
            text_value(row.get("transition_key")),
        )
        for row in rows
    }


def _truthy(value: object) -> bool:
    return text_value(value).upper() == "TRUE"


def _int(row: Mapping[str, str], key: str) -> int:
    value = text_value(row.get(key))
    return int(value) if value else 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-diff-contract-tsv",
        type=Path,
        action="append",
    )
    parser.add_argument(
        "--forbidden-transition-tsv",
        type=Path,
        action="append",
    )
    parser.add_argument(
        "--feature-gate-summary-tsv",
        type=Path,
        default=DEFAULT_FEATURE_GATE_SUMMARY_TSV,
    )
    parser.add_argument(
        "--activation-copy-summary-tsv",
        type=Path,
        default=DEFAULT_ACTIVATION_COPY_SUMMARY_TSV,
    )
    parser.add_argument(
        "--acceptance-summary-tsv",
        type=Path,
        default=DEFAULT_ACCEPTANCE_SUMMARY_TSV,
    )
    parser.add_argument("--value-delta-tsv", type=Path, default=DEFAULT_VALUE_DELTA_TSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
