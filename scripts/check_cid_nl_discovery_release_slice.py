"""Check the CID-NL Discovery release slice contract.

This is a no-RAW release-slice guard for the currently activated 95-cell
CID-NL Discovery scope. It does not write product outputs or expand authority.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_cid_nl_default_product_activation import (  # noqa: E402
    CHECK_COLUMNS,
    COMPACT_MANIFEST_COLUMNS,
    DEFAULT_DOCS_DIR,
    DISCOVERY_DEFAULT_EFFECT,
    FEATURE_INCLUSION_AUTHORITY_BASIS,
    LEGACY_PROVENANCE_STATUS,
    LEGACY_QUANT_MATRIX_EFFECT,
    LOW_PREVALENCE_FEATURE_POLICY,
    PRODUCT_AUTHORITY_SCOPE,
    SOURCE_SUCCESSOR_IDENTITY_SCOPE,
    validate_cid_nl_default_product_activation,
)
from scripts.check_bounded_product_lanes import (
    check_bounded_product_lanes,  # noqa: E402
)
from scripts.check_cid_nl_default_activation_universe_closure import (  # noqa: E402
    check_cid_nl_default_activation_universe_closure,
)
from scripts.check_cid_nl_discovery_full_scope_classification import (  # noqa: E402
    check_cid_nl_discovery_full_scope_classification,
)
from scripts.check_productization_authority import (  # noqa: E402
    check_productization_authority,
)
from scripts.check_productization_state import check_productization_state  # noqa: E402
from tools.diagnostics.docs_policy import (  # noqa: E402
    CONTROL_PLANE_PATH,
    PRODUCTIZATION_STATUS_ANCHOR_PATH,
)
from xic_extractor.tabular_io import read_tsv_required, text_value  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SUMMARY_JSON = DEFAULT_DOCS_DIR / "cid_nl_default_product_activation_summary.json"
CHECKS_TSV = DEFAULT_DOCS_DIR / "cid_nl_default_product_activation_checks.tsv"
COMPACT_MANIFEST_TSV = (
    DEFAULT_DOCS_DIR / "cid_nl_default_product_activation_manifest.tsv"
)
ROADMAP = ROOT / "docs/product/discovery.md"
HANDOFF = ROOT / PRODUCTIZATION_STATUS_ANCHOR_PATH
CONTROL_PLANE = ROOT / CONTROL_PLANE_PATH


def check_cid_nl_discovery_release_slice(
    *,
    summary_json: Path = SUMMARY_JSON,
    checks_tsv: Path = CHECKS_TSV,
    compact_manifest_tsv: Path = COMPACT_MANIFEST_TSV,
    roadmap: Path = ROADMAP,
    handoff: Path = HANDOFF,
    control_plane: Path = CONTROL_PLANE,
) -> list[str]:
    problems: list[str] = []
    problems.extend(
        validate_cid_nl_default_product_activation(summary_json=summary_json)
    )
    payload = _read_json_object(summary_json, problems)
    if payload:
        _check_summary(payload, problems)
    _check_checks_tsv(checks_tsv, problems)
    _check_compact_manifest(compact_manifest_tsv, problems)
    _check_docs(
        roadmap=roadmap,
        handoff=handoff,
        problems=problems,
    )
    for problem in check_cid_nl_discovery_full_scope_classification():
        problems.append(f"full_scope_classification: {problem}")
    for problem in check_cid_nl_default_activation_universe_closure():
        problems.append(f"full_cohort_universe_closure: {problem}")
    for problem in check_productization_state(control_plane_path=control_plane):
        problems.append(f"productization_state: {problem}")
    for problem in check_productization_authority():
        problems.append(f"productization_authority: {problem}")
    for problem in check_bounded_product_lanes():
        problems.append(f"bounded_product_lanes: {problem}")
    return problems


def _check_summary(payload: Mapping[str, Any], problems: list[str]) -> None:
    expected: dict[str, object] = {
        "schema_version": "cid_nl_default_product_activation_v1",
        "status": "pass",
        "activation_label": "product_ready_default_matrix_activated",
        "validation_label": "product_ready_cid_nl_default_activation",
        "product_lane": "cid_nl_discovery",
        "product_scope_kind": "discovery_default_activation",
        "product_authority_scope": PRODUCT_AUTHORITY_SCOPE,
        "default_activation_effect": DISCOVERY_DEFAULT_EFFECT,
        "feature_inclusion_authority_basis": FEATURE_INCLUSION_AUTHORITY_BASIS,
        "matrix_row_universe_policy": "discovery_expanded_85raw_alignment_rows",
        "low_prevalence_feature_policy": LOW_PREVALENCE_FEATURE_POLICY,
        "source_successor_identity_scope": SOURCE_SUCCESSOR_IDENTITY_SCOPE,
        "accepted_discovery_cell_count": 95,
        "written_discovery_cell_count": "95",
        "candidate_transition_count": 20,
        "expected_diff_count": "95",
        "unused_expected_diff_count": "0",
        "existing_successor_context_cell_count": 337,
        "omitted_no_target_cell_count": 27,
        "product_writer_changed": True,
        "default_quant_matrix_changed": True,
        "workbook_or_gui_changed": False,
        "selected_peak_area_or_counting_changed": False,
        "backfill_writer_authority_changed": False,
        "cid_nl_ms2_direct_productwriter_authority": False,
        "candidate_rows_are_matrix_rows": False,
        "raw_or_85raw_ran": False,
        "legacy_quant_matrix_effect": LEGACY_QUANT_MATRIX_EFFECT,
        "legacy_provenance_status": LEGACY_PROVENANCE_STATUS,
    }
    for field, expected_value in expected.items():
        if payload.get(field) != expected_value:
            problems.append(
                f"summary {field} mismatch: expected {expected_value!r}, "
                f"found {payload.get(field)!r}"
            )

    source_counts = payload.get("source_contract_counts")
    if source_counts != {
        "agent_resolved": 9,
        "manual_resolved": 13,
        "primary_supported": 73,
    }:
        problems.append("summary source_contract_counts mismatch")

    matrix_delta = payload.get("matrix_delta_summary")
    if not isinstance(matrix_delta, Mapping) or matrix_delta.get("status") != "pass":
        problems.append("matrix_delta_summary must pass")
    elif matrix_delta.get("changed_cell_count") != 95:
        problems.append("matrix_delta_summary changed_cell_count must be 95")

    provenance = payload.get("cell_provenance_summary")
    if not isinstance(provenance, Mapping) or provenance.get("status") != "pass":
        problems.append("cell_provenance_summary must pass")
    elif provenance.get("accepted_discovery_cell_count") != 95:
        problems.append("accepted_discovery_cell_count in provenance must be 95")

    successor_evidence = payload.get("successor_self_evidence_summary")
    if (
        not isinstance(successor_evidence, Mapping)
        or successor_evidence.get("status") != "pass"
        or successor_evidence.get("checked_cell_count") != 95
        or successor_evidence.get("problem_count") != 0
    ):
        problems.append("successor_self_evidence_summary must pass")

    terminology = text_value(payload.get("terminology_statement"))
    if "not Backfill product scope" not in terminology:
        problems.append("terminology_statement must separate Discovery from Backfill")
    row_universe = text_value(payload.get("row_universe_statement"))
    if "Low-prevalence features are allowed" not in row_universe:
        problems.append("row_universe_statement must allow sparse untargeted rows")
    identity = text_value(payload.get("identity_statement"))
    identity_anchor = (
        "Source/successor m/z or RT similarity is not the feature-inclusion gate"
    )
    if identity_anchor not in identity:
        problems.append(
            "identity_statement must separate identity review from feature inclusion"
        )


def _check_checks_tsv(path: Path, problems: list[str]) -> None:
    try:
        rows = read_tsv_required(path, CHECK_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"checks_tsv: {exc}")
        return
    failed = [row["check_id"] for row in rows if row.get("status") != "pass"]
    if failed:
        problems.append("failed release checks: " + ", ".join(failed))
    check_ids = {row.get("check_id", "") for row in rows}
    required_ids = {
        "discovery_terminology_boundary",
        "successor_self_evidence_contract",
        "matrix_row_universe_policy",
        "source_successor_identity_scope",
    }
    missing = sorted(required_ids - check_ids)
    if missing:
        problems.append("checks_tsv missing required ids: " + ";".join(missing))


def _check_compact_manifest(path: Path, problems: list[str]) -> None:
    try:
        rows = read_tsv_required(path, COMPACT_MANIFEST_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"compact_manifest: {exc}")
        return
    if len(rows) != 20:
        problems.append(
            f"compact manifest must contain 20 transitions, found {len(rows)}"
        )
    cell_count = sum(_int(row.get("contract_cell_count")) for row in rows)
    if cell_count != 95:
        problems.append(
            "compact manifest contract_cell_count sum must be 95, "
            f"found {cell_count}"
        )
    source_counts = Counter(row.get("contract_source", "") for row in rows)
    expected_sources = {
        "agent_resolved": 2,
        "manual_resolved": 4,
        "primary_supported": 14,
    }
    if source_counts != expected_sources:
        problems.append(
            "compact manifest source row counts mismatch: "
            f"{dict(source_counts)}"
        )
    for index, row in enumerate(rows, start=2):
        if row.get("default_activation_effect") != DISCOVERY_DEFAULT_EFFECT:
            problems.append(f"manifest row {index}: default_activation_effect mismatch")
        if row.get("legacy_quant_matrix_effect") != LEGACY_QUANT_MATRIX_EFFECT:
            problems.append(
                f"manifest row {index}: legacy_quant_matrix_effect mismatch"
            )
        if row.get("product_authority_scope") != PRODUCT_AUTHORITY_SCOPE:
            problems.append(f"manifest row {index}: product_authority_scope mismatch")


def _check_docs(
    *,
    roadmap: Path,
    handoff: Path,
    problems: list[str],
) -> None:
    required = {
        roadmap: [
            "CID-NL Discovery is a bounded accepted slice",
            "reopen broad Backfill",
        ],
        handoff: [
            "cid_nl_default_product_activation_v1",
            "broad_backfill_autowrite",
        ],
    }
    for path, anchors in required.items():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{path}: cannot read: {exc}")
            continue
        missing = [anchor for anchor in anchors if anchor not in text]
        if missing:
            problems.append(f"{path}: missing anchors: {', '.join(missing)}")


def _read_json_object(path: Path, problems: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        problems.append(f"{path}: cannot read JSON: {exc}")
        return {}
    except json.JSONDecodeError as exc:
        problems.append(f"{path}: invalid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        problems.append(f"{path}: expected JSON object")
        return {}
    return payload


def _int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-json", type=Path, default=SUMMARY_JSON)
    parser.add_argument("--checks-tsv", type=Path, default=CHECKS_TSV)
    parser.add_argument(
        "--compact-manifest-tsv",
        type=Path,
        default=COMPACT_MANIFEST_TSV,
    )
    parser.add_argument("--roadmap", type=Path, default=ROADMAP)
    parser.add_argument("--handoff", type=Path, default=HANDOFF)
    parser.add_argument("--control-plane", type=Path, default=CONTROL_PLANE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    problems = check_cid_nl_discovery_release_slice(
        summary_json=args.summary_json,
        checks_tsv=args.checks_tsv,
        compact_manifest_tsv=args.compact_manifest_tsv,
        roadmap=args.roadmap,
        handoff=args.handoff,
        control_plane=args.control_plane,
    )
    if problems:
        print("CID-NL Discovery release slice failed:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("CID-NL Discovery release slice is stable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
