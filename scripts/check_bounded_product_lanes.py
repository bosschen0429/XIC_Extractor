"""Check bounded non-broad productization lane acceptance.

This checker guards the non-broad lanes that may continue productization work.
It deliberately does not grant ProductWriter authority and does not inspect or
write matrices, workbooks, selected peaks, selected areas, or counted detection.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.check_productization_state import (
    artifact_sha256,
    check_productization_state,
)
from tools.diagnostics.docs_policy import PRODUCTIZATION_STATUS_INDEX_REL

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = (
    ROOT / "docs/superpowers/schemas/bounded_non_broad_product_lanes.json"
)
DEFAULT_ACCEPTANCE = (
    ROOT / "docs/superpowers/validation/bounded_non_broad_lane_acceptance.tsv"
)
DEFAULT_STATUS_INDEX = ROOT / PRODUCTIZATION_STATUS_INDEX_REL
STATUS_INDEX_SOURCE = PRODUCTIZATION_STATUS_INDEX_REL

EXPECTED_LANE_IDS = {
    "targeted_ms1_shape_identity_limited_rescue",
    "sample_metadata_order_projection",
    "review_action_candidate_sidecar",
    "targeted_ms1_shape_identity_broader_targets",
    "sample_metadata_role_value_behavior",
    "review_action_selected_candidate_switch",
    "review_action_manual_boundary_area_writer",
}
EXPECTED_READINESS = {
    "targeted_ms1_shape_identity_limited_rescue": "production_ready",
    "sample_metadata_order_projection": "production_ready",
    "review_action_candidate_sidecar": "production_candidate",
    "targeted_ms1_shape_identity_broader_targets": "blocked",
    "sample_metadata_role_value_behavior": "blocked",
    "review_action_selected_candidate_switch": "parked",
    "review_action_manual_boundary_area_writer": "parked",
}
EXPECTED_CURRENT_SURFACES = {
    "targeted_ms1_shape_identity_limited_rescue",
    "sample_metadata_order_projection",
    "review_action_candidate_sidecar",
}
EXPECTED_PRODUCTION_READY = {
    "targeted_ms1_shape_identity_limited_rescue",
    "sample_metadata_order_projection",
}
EXPECTED_PRODUCTION_CANDIDATE = {"review_action_candidate_sidecar"}
EXPECTED_SCOPE = {
    "targeted_ms1_shape_identity_limited_rescue": {
        "limited_5hmdc_5medc",
        "5-hmdC",
        "5-medC",
    },
    "sample_metadata_order_projection": {"sample_metadata"},
    "review_action_candidate_sidecar": {"review_action_candidate_sidecar"},
}
EXPECTED_EFFECT = {
    "targeted_ms1_shape_identity_limited_rescue": "detected_flagged_only",
    "sample_metadata_order_projection": "no_output_order_projection",
    "review_action_candidate_sidecar": "identity_verification_only",
    "targeted_ms1_shape_identity_broader_targets": "none",
    "sample_metadata_role_value_behavior": "none",
    "review_action_selected_candidate_switch": "none",
    "review_action_manual_boundary_area_writer": "none",
}

BOOLEAN_FIELDS = (
    "current_bounded_surface",
    "grants_new_product_authority",
    "may_feed_product_writer",
    "may_expand_targets",
    "may_enable_gui",
    "may_change_quant_output_beyond_contract",
    "may_change_workbook_beyond_contract",
    "may_change_matrix_beyond_contract",
    "may_change_selected_peak",
    "may_change_selected_area",
    "may_change_counted_detection",
    "review_or_truth_required_before_expansion",
)

RISK_FIELDS = (
    "grants_new_product_authority",
    "may_feed_product_writer",
    "may_expand_targets",
    "may_enable_gui",
    "may_change_quant_output_beyond_contract",
    "may_change_workbook_beyond_contract",
    "may_change_matrix_beyond_contract",
    "may_change_selected_peak",
    "may_change_selected_area",
    "may_change_counted_detection",
)


def check_bounded_product_lanes(
    *,
    schema_path: Path = DEFAULT_SCHEMA,
    acceptance_path: Path = DEFAULT_ACCEPTANCE,
    status_index_path: Path = DEFAULT_STATUS_INDEX,
    repo_root: Path = ROOT,
) -> list[str]:
    problems: list[str] = []
    schema = _read_json(schema_path, problems)
    header, rows = _read_tsv(acceptance_path, problems)
    _, status_rows = _read_tsv(status_index_path, problems)
    problems.extend(
        f"status index: {problem}"
        for problem in check_productization_state(status_index_path=status_index_path)
    )
    if not schema:
        return problems

    status_by_lane = {
        row.get("lane_id", ""): row for row in status_rows if row.get("lane_id")
    }
    _check_schema_rows(schema, header, rows, problems)
    _check_status_alignment(rows, status_by_lane, problems)
    _check_sources(rows, repo_root, status_index_path, problems)
    _check_bounded_rules(schema, rows, problems)
    return problems


def _check_schema_rows(
    schema: Mapping[str, Any],
    header: Sequence[str],
    rows: Sequence[Mapping[str, str]],
    problems: list[str],
) -> None:
    required = schema.get("required_columns")
    if not isinstance(required, list) or not all(
        isinstance(column, str) for column in required
    ):
        problems.append("schema required_columns must be a string list")
        return
    if list(header) != required:
        problems.append("acceptance header must exactly match schema")
    allowed_statuses = set(schema.get("allowed_readiness_statuses", []))
    required_lane_ids = set(schema.get("required_lane_ids", []))
    if required_lane_ids != EXPECTED_LANE_IDS:
        problems.append("schema required_lane_ids drifted")
    lane_ids = [row.get("lane_id", "") for row in rows]
    duplicates = sorted(
        lane_id for lane_id, count in Counter(lane_ids).items() if count > 1
    )
    if duplicates:
        problems.append("duplicate lane_id values: " + ", ".join(duplicates))
    missing = sorted(required_lane_ids - set(lane_ids))
    if missing:
        problems.append("missing required lane_id values: " + ", ".join(missing))
    extra = sorted(set(lane_ids) - required_lane_ids)
    if extra:
        problems.append("unregistered lane_id values: " + ", ".join(extra))
    if set(lane_ids) != EXPECTED_LANE_IDS:
        problems.append("acceptance lane_id set drifted")
    for index, row in enumerate(rows, start=2):
        if row.get("schema_version") != "bounded_non_broad_lane_acceptance":
            problems.append(f"row {index}: invalid schema_version")
        if row.get("readiness_status") not in allowed_statuses:
            problems.append(f"row {index}: invalid readiness_status")
        expected_status = EXPECTED_READINESS.get(row.get("lane_id", ""))
        if expected_status and row.get("readiness_status") != expected_status:
            problems.append(f"row {index}: bounded readiness status drifted")
        for field in BOOLEAN_FIELDS:
            if row.get(field) not in {"TRUE", "FALSE"}:
                problems.append(f"row {index}: {field} must be TRUE or FALSE")


def _check_status_alignment(
    rows: Sequence[Mapping[str, str]],
    status_by_lane: Mapping[str, Mapping[str, str]],
    problems: list[str],
) -> None:
    for index, row in enumerate(rows, start=2):
        lane_id = row.get("lane_id", "")
        status_row = status_by_lane.get(lane_id)
        if status_row is None:
            problems.append(
                f"row {index}: lane missing from productization status index"
            )
            continue
        if row.get("readiness_status") != status_row.get("readiness_status"):
            problems.append(
                f"row {index}: readiness_status disagrees with status index"
            )
        if status_row.get("write_authority") == "TRUE":
            problems.append(f"row {index}: bounded lane points at writer authority")
        if status_row.get("product_authority_scope", ""):
            problems.append(f"row {index}: status index row has authority scope")
        status_risk_fields = (
            "may_touch_matrix",
            "may_change_quant_output",
            "may_change_workbook",
            "may_change_selected_peak",
            "may_change_selected_area",
            "may_change_counted_detection",
        )
        risky_status = [
            field for field in status_risk_fields if status_row.get(field) == "TRUE"
        ]
        if risky_status:
            problems.append(
                f"row {index}: status index row changes product output: "
                + ", ".join(risky_status)
            )


def _check_sources(
    rows: Sequence[Mapping[str, str]],
    repo_root: Path,
    status_index_path: Path,
    problems: list[str],
) -> None:
    for index, row in enumerate(rows, start=2):
        source = row.get("source_status_index", "")
        expected_hash = row.get("source_status_index_sha256", "")
        if source != STATUS_INDEX_SOURCE:
            problems.append(f"row {index}: unexpected source_status_index")
        source_path = (repo_root / source).resolve()
        supplied_status_path = status_index_path.resolve()
        if source_path != supplied_status_path:
            problems.append(
                f"row {index}: source_status_index does not match supplied "
                "status-index"
            )
        if not supplied_status_path.exists():
            problems.append(f"row {index}: source_status_index missing")
            continue
        if artifact_sha256(supplied_status_path) != expected_hash:
            problems.append(f"row {index}: source_status_index_sha256 mismatch")


def _check_bounded_rules(
    schema: Mapping[str, Any],
    rows: Sequence[Mapping[str, str]],
    problems: list[str],
) -> None:
    rules = schema.get("rules")
    if not isinstance(rules, Mapping):
        problems.append("schema rules must be a mapping")
        return
    by_lane = {row.get("lane_id", ""): row for row in rows}
    if set(rules.get("required_lane_ids", [])):
        problems.append("schema rules must not redefine required_lane_ids")
    if set(rules.get("production_ready_lanes", [])) != EXPECTED_PRODUCTION_READY:
        problems.append("schema production_ready_lanes drifted")
    if set(rules.get("production_candidate_lanes", [])) != (
        EXPECTED_PRODUCTION_CANDIDATE
    ):
        problems.append("schema production_candidate_lanes drifted")

    current_surfaces = {
        lane_id
        for lane_id, row in by_lane.items()
        if row.get("current_bounded_surface") == "TRUE"
    }
    if current_surfaces != EXPECTED_CURRENT_SURFACES:
        problems.append("current bounded surfaces drifted")
    for index, row in enumerate(rows, start=2):
        lane_id = row.get("lane_id", "")
        if "backfill" in lane_id or "backfill" in row.get("bounded_lane_class", ""):
            problems.append(f"row {index}: broad Backfill must not appear")
        risky = [field for field in RISK_FIELDS if row.get(field) == "TRUE"]
        if risky:
            problems.append(
                f"row {index}: bounded lane grants forbidden capability: "
                + ", ".join(risky)
            )
        if row.get("readiness_status") in {"blocked", "parked"}:
            if row.get("allowed_effect") != "none":
                problems.append(f"row {index}: blocked/parked lane has effect")
            if row.get("current_bounded_surface") != "FALSE":
                problems.append(f"row {index}: blocked/parked lane is current surface")
            if row.get("allowed_scope"):
                problems.append(f"row {index}: blocked/parked lane has scope")

    ready = set(rules.get("production_ready_lanes", []))
    actual_ready = {
        lane_id
        for lane_id, row in by_lane.items()
        if row.get("readiness_status") == "production_ready"
    }
    if actual_ready != EXPECTED_PRODUCTION_READY or actual_ready != ready:
        problems.append("production_ready lane set drifted")

    candidate = set(rules.get("production_candidate_lanes", []))
    actual_candidate = {
        lane_id
        for lane_id, row in by_lane.items()
        if row.get("readiness_status") == "production_candidate"
    }
    if (
        actual_candidate != EXPECTED_PRODUCTION_CANDIDATE
        or actual_candidate != candidate
    ):
        problems.append("production_candidate lane set drifted")

    _check_targeted_limited(by_lane, rules, problems)
    _check_sample_metadata(by_lane, rules, problems)
    _check_review_action_sidecar(by_lane, rules, problems)


def _check_targeted_limited(
    by_lane: Mapping[str, Mapping[str, str]],
    rules: Mapping[str, Any],
    problems: list[str],
) -> None:
    row = by_lane.get("targeted_ms1_shape_identity_limited_rescue")
    if row is None:
        return
    scope = {part for part in row.get("allowed_scope", "").split(";") if part}
    expected_scope = EXPECTED_SCOPE["targeted_ms1_shape_identity_limited_rescue"]
    if set(rules.get("targeted_limited_scope", [])) != expected_scope:
        problems.append("schema targeted limited scope drifted")
    if scope != expected_scope:
        problems.append("targeted limited scope drifted")
    expected_effect = EXPECTED_EFFECT["targeted_ms1_shape_identity_limited_rescue"]
    if rules.get("targeted_limited_effect") != expected_effect:
        problems.append("schema targeted limited effect drifted")
    if row.get("allowed_effect") != expected_effect:
        problems.append("targeted limited effect drifted")
    if row.get("bounded_lane_class") != "existing_limited_targeted_ms1":
        problems.append("targeted limited class drifted")


def _check_sample_metadata(
    by_lane: Mapping[str, Mapping[str, str]],
    rules: Mapping[str, Any],
    problems: list[str],
) -> None:
    row = by_lane.get("sample_metadata_order_projection")
    if row is None:
        return
    expected_effect = EXPECTED_EFFECT["sample_metadata_order_projection"]
    if rules.get("sample_metadata_effect") != expected_effect:
        problems.append("schema sample metadata effect drifted")
    if row.get("allowed_effect") != expected_effect:
        problems.append("sample metadata effect drifted")
    expected_scope = EXPECTED_SCOPE["sample_metadata_order_projection"]
    if {row.get("allowed_scope", "")} != expected_scope:
        problems.append("sample metadata scope drifted")


def _check_review_action_sidecar(
    by_lane: Mapping[str, Mapping[str, str]],
    rules: Mapping[str, Any],
    problems: list[str],
) -> None:
    row = by_lane.get("review_action_candidate_sidecar")
    if row is None:
        return
    expected_effect = EXPECTED_EFFECT["review_action_candidate_sidecar"]
    if rules.get("review_action_candidate_sidecar_effect") != expected_effect:
        problems.append("schema review action sidecar effect drifted")
    if row.get("allowed_effect") != expected_effect:
        problems.append("review action sidecar effect drifted")
    expected_scope = EXPECTED_SCOPE["review_action_candidate_sidecar"]
    if {row.get("allowed_scope", "")} != expected_scope:
        problems.append("review action sidecar scope drifted")


def _read_json(path: Path, problems: list[str]) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        problems.append(f"could not read {path}: {exc}")
        return None
    except json.JSONDecodeError as exc:
        problems.append(f"invalid json {path}: {exc}")
        return None
    if not isinstance(value, Mapping):
        problems.append(f"json root must be object: {path}")
        return None
    return value


def _read_tsv(
    path: Path,
    problems: list[str],
) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            return list(reader.fieldnames or []), list(reader)
    except OSError as exc:
        problems.append(f"could not read {path}: {exc}")
        return [], []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--acceptance", type=Path, default=DEFAULT_ACCEPTANCE)
    parser.add_argument("--status-index", type=Path, default=DEFAULT_STATUS_INDEX)
    args = parser.parse_args(argv)
    problems = check_bounded_product_lanes(
        schema_path=args.schema,
        acceptance_path=args.acceptance,
        status_index_path=args.status_index,
    )
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("Bounded non-broad product lanes are consistent and fail-closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
