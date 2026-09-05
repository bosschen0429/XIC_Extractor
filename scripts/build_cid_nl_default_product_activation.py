"""Build the explicit CID-NL default product activation bundle.

This is the public-surface activation step after the CID-NL adopt gate. It
reuses the existing ProductionAcceptanceManifest and QuantMatrixVersion writer;
it does not create a second ProductWriter path, rerun RAW, update workbooks, or
touch GUI behavior.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_quant_matrix_version import run_activation  # noqa: E402
from scripts.check_production_acceptance_manifest import (  # noqa: E402
    REQUIRED_COLUMNS as PRODUCTION_ACCEPTANCE_COLUMNS,
)
from scripts.check_production_acceptance_manifest import (  # noqa: E402
    production_acceptance_manifest_sha256,
)
from tools.diagnostics.cid_nl_activation_adopt_gate import (  # noqa: E402
    DEFAULT_OUTPUT_DIR as DEFAULT_ADOPT_GATE_DIR,
)
from tools.diagnostics.cid_nl_activation_copy_candidate import (  # noqa: E402
    DEFAULT_ALIGNMENT_MATRIX_IDENTITY_TSV,
    DEFAULT_ALIGNMENT_MATRIX_TSV,
    DEFAULT_EXPECTED_DIFF_CONTRACTS,
    VALUE_DELTA_COLUMNS,
)
from tools.diagnostics.cid_nl_feature_inclusion_gate import (  # noqa: E402
    EXPECTED_DIFF_COLUMNS as CID_NL_EXPECTED_DIFF_COLUMNS,
)
from xic_extractor.alignment.quant_matrix_version import (  # noqa: E402
    CELL_PROVENANCE_COLUMNS,
    EXPECTED_DIFF_SUMMARY_COLUMNS,
)
from xic_extractor.alignment.quant_matrix_version import (
    EXPECTED_DIFF_COLUMNS as QUANT_EXPECTED_DIFF_COLUMNS,
)
from xic_extractor.tabular_io import (  # noqa: E402
    file_sha256,
    numeric_equal,
    read_tsv_required,
    read_tsv_with_header,
    split_semicolon_labels,
    text_value,
    write_tsv,
)


def artifact_record(path: Path, *, base_dir: Path) -> dict[str, str]:
    return {
        "path": path.resolve(strict=False)
        .relative_to(
            base_dir.resolve(strict=False),
        )
        .as_posix(),
        "sha256": file_sha256(path),
    }


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "cid_nl_default_product_activation_v1"
ACTIVATION_LABEL = "product_ready_default_matrix_activated"
PRODUCT_AUTHORITY_SCOPE = "cid_nl_adopt_ready_feature_inclusion_95_cells"
DISCOVERY_DEFAULT_EFFECT = "write_cid_nl_discovery_default_cell"
FEATURE_INCLUSION_AUTHORITY_BASIS = (
    "successor_self_evidence_manifest_expected_diff_and_cid_nl_tag"
)
MATRIX_ROW_UNIVERSE_POLICY = "discovery_expanded_full_cohort_alignment_rows"
LOW_PREVALENCE_FEATURE_POLICY = "allowed_for_untargeted_downstream_filter"
SOURCE_SUCCESSOR_IDENTITY_SCOPE = "identity_review_only_not_feature_inclusion_blocker"
LEGACY_QUANT_MATRIX_EFFECT = "write_accepted_backfill"
LEGACY_PROVENANCE_STATUS = "accepted_backfill"
DEFAULT_OUTPUT_DIR = ROOT / "output/validation/cid_nl_default_product_activation_v1"
DEFAULT_DOCS_DIR = (
    ROOT / "docs/superpowers/validation/cid_nl_default_product_activation_v1"
)
DEFAULT_ADOPT_SUMMARY_JSON = (
    DEFAULT_ADOPT_GATE_DIR / "cid_nl_activation_adopt_gate_summary.json"
)
DEFAULT_SUCCESSOR_AUTHORITY_MANIFEST_TSV = (
    ROOT
    / "output/validation/"
    / "cid_nl_default_activation_successor_authority_contract_v1/"
    / "successor_authority_manifest.tsv"
)
DEFAULT_VALUE_DELTA_TSV = (
    ROOT
    / "output/validation/cid_nl_default_activation_gallery_review_v1/"
    / "activation_copy_candidate/cid_nl_activation_copy_value_delta.tsv"
)
DEFAULT_EXPECTED_CONTRACT_CELL_COUNT = 95
DEFAULT_EXPECTED_TRANSITION_COUNT = 20
DEFAULT_EXPECTED_EXISTING_SUCCESSOR_CONTEXT_CELL_COUNT = 337
DEFAULT_EXPECTED_OMITTED_NO_TARGET_CELL_COUNT = 27

COMPACT_MANIFEST_COLUMNS = (
    "schema_version",
    "transition_key",
    "contract_source",
    "contract_cell_count",
    "source_peak_hypothesis_id",
    "successor_peak_hypothesis_id",
    "successor_product_mz",
    "successor_neutral_loss_tag",
    "default_activation_effect",
    "legacy_quant_matrix_effect",
    "product_authority_scope",
)
CHECK_COLUMNS = (
    "schema_version",
    "check_id",
    "status",
    "observed",
    "expected",
    "notes",
)


def build_cid_nl_default_product_activation(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    docs_dir: Path = DEFAULT_DOCS_DIR,
    source_root: Path = ROOT,
    expected_diff_contract_tsvs: Sequence[Path] = DEFAULT_EXPECTED_DIFF_CONTRACTS,
    adopt_summary_json: Path = DEFAULT_ADOPT_SUMMARY_JSON,
    successor_authority_manifest_tsv: Path = DEFAULT_SUCCESSOR_AUTHORITY_MANIFEST_TSV,
    input_quant_matrix_tsv: Path = DEFAULT_ALIGNMENT_MATRIX_TSV,
    input_matrix_identity_tsv: Path = DEFAULT_ALIGNMENT_MATRIX_IDENTITY_TSV,
    value_delta_tsv: Path = DEFAULT_VALUE_DELTA_TSV,
    expected_contract_cell_count: int = DEFAULT_EXPECTED_CONTRACT_CELL_COUNT,
    expected_transition_count: int = DEFAULT_EXPECTED_TRANSITION_COUNT,
    expected_existing_successor_context_cell_count: int = (
        DEFAULT_EXPECTED_EXISTING_SUCCESSOR_CONTEXT_CELL_COUNT
    ),
    expected_omitted_no_target_cell_count: int = (
        DEFAULT_EXPECTED_OMITTED_NO_TARGET_CELL_COUNT
    ),
) -> dict[str, Any]:
    adopt_summary = _read_json_object(adopt_summary_json)
    _require_adopt_ready(
        adopt_summary,
        expected_contract_cell_count=expected_contract_cell_count,
        expected_transition_count=expected_transition_count,
        expected_existing_successor_context_cell_count=(
            expected_existing_successor_context_cell_count
        ),
        expected_omitted_no_target_cell_count=(expected_omitted_no_target_cell_count),
    )
    contract_rows = _read_contract_rows(expected_diff_contract_tsvs)
    _validate_contract_shape(
        contract_rows,
        expected_contract_cell_count=expected_contract_cell_count,
        expected_transition_count=expected_transition_count,
    )
    value_delta_rows = read_tsv_required(value_delta_tsv, VALUE_DELTA_COLUMNS)
    _validate_value_delta(contract_rows, value_delta_rows)

    authority_rows = read_tsv_required(
        successor_authority_manifest_tsv,
        PRODUCTION_ACCEPTANCE_COLUMNS,
    )
    successor_evidence_summary = _successor_self_evidence_summary(
        contract_rows=contract_rows,
        authority_rows=authority_rows,
    )
    manifest_rows = _activation_manifest_rows(
        contract_rows=contract_rows,
        authority_rows=authority_rows,
    )
    expected_diff_rows = _quant_expected_diff_rows(contract_rows)
    compact_manifest_rows = _compact_manifest_rows(contract_rows)

    inputs_dir = output_dir / "inputs"
    default_output_dir = output_dir / "default_output"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    activation_manifest_tsv = (
        inputs_dir / "cid_nl_default_product_activation_manifest.tsv"
    )
    activation_expected_diff_tsv = (
        inputs_dir / "cid_nl_default_product_activation_expected_diff.tsv"
    )
    write_tsv(
        activation_manifest_tsv,
        manifest_rows,
        PRODUCTION_ACCEPTANCE_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    write_tsv(
        activation_expected_diff_tsv,
        expected_diff_rows,
        QUANT_EXPECTED_DIFF_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )

    activation_outputs = run_activation(
        input_quant_matrix_tsv=input_quant_matrix_tsv,
        input_matrix_identity_tsv=input_matrix_identity_tsv,
        production_acceptance_manifest_tsv=activation_manifest_tsv,
        expected_diff_tsv=activation_expected_diff_tsv,
        output_dir=default_output_dir,
        manifest_root=source_root,
    )
    _rewrite_source_summary_paths(activation_outputs["source_summary"])

    expected_summary = _expected_diff_summary(
        activation_outputs["expected_diff_summary"]
    )
    delta_summary = _matrix_delta_summary(
        baseline_quant_matrix_tsv=input_quant_matrix_tsv,
        activated_quant_matrix_tsv=activation_outputs["quant_matrix"],
        matrix_identity_tsv=input_matrix_identity_tsv,
        expected_values={
            (row["successor_peak_hypothesis_id"], row["sample_stem"]): row[
                "candidate_quant_value"
            ]
            for row in contract_rows
        },
    )
    provenance_summary = _cell_provenance_summary(
        cell_provenance_tsv=activation_outputs["cell_provenance"],
        expected_values={
            (row["successor_peak_hypothesis_id"], row["sample_stem"]): row[
                "candidate_quant_value"
            ]
            for row in contract_rows
        },
    )
    check_rows = _check_rows(
        adopt_summary=adopt_summary,
        contract_rows=contract_rows,
        expected_summary=expected_summary,
        delta_summary=delta_summary,
        provenance_summary=provenance_summary,
        expected_contract_cell_count=expected_contract_cell_count,
        expected_transition_count=expected_transition_count,
        expected_existing_successor_context_cell_count=(
            expected_existing_successor_context_cell_count
        ),
        expected_omitted_no_target_cell_count=(expected_omitted_no_target_cell_count),
        successor_evidence_summary=successor_evidence_summary,
    )
    failed_checks = [row["check_id"] for row in check_rows if row["status"] != "pass"]
    if failed_checks:
        raise ValueError(
            "CID-NL default activation checks failed: " + ";".join(failed_checks)
        )

    checks_tsv = docs_dir / "cid_nl_default_product_activation_checks.tsv"
    compact_manifest_tsv = docs_dir / "cid_nl_default_product_activation_manifest.tsv"
    summary_json = docs_dir / "cid_nl_default_product_activation_summary.json"
    write_tsv(checks_tsv, check_rows, CHECK_COLUMNS, extrasaction="raise")
    write_tsv(
        compact_manifest_tsv,
        compact_manifest_rows,
        COMPACT_MANIFEST_COLUMNS,
        extrasaction="raise",
    )
    payload = _summary_payload(
        docs_dir=docs_dir,
        output_dir=output_dir,
        source_root=source_root,
        adopt_summary_json=adopt_summary_json,
        successor_authority_manifest_tsv=successor_authority_manifest_tsv,
        input_quant_matrix_tsv=input_quant_matrix_tsv,
        input_matrix_identity_tsv=input_matrix_identity_tsv,
        value_delta_tsv=value_delta_tsv,
        expected_diff_contract_tsvs=expected_diff_contract_tsvs,
        activation_manifest_tsv=activation_manifest_tsv,
        activation_expected_diff_tsv=activation_expected_diff_tsv,
        compact_manifest_tsv=compact_manifest_tsv,
        checks_tsv=checks_tsv,
        activation_outputs=activation_outputs,
        expected_summary=expected_summary,
        delta_summary=delta_summary,
        provenance_summary=provenance_summary,
        check_rows=check_rows,
        contract_rows=contract_rows,
        adopt_summary=adopt_summary,
        successor_evidence_summary=successor_evidence_summary,
    )
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_readme(
        docs_dir / "README.md",
        payload=payload,
        compact_manifest_tsv=compact_manifest_tsv,
        checks_tsv=checks_tsv,
    )
    return payload


def validate_cid_nl_default_product_activation(
    *,
    summary_json: Path = DEFAULT_DOCS_DIR
    / "cid_nl_default_product_activation_summary.json",
    expected_contract_cell_count: int = DEFAULT_EXPECTED_CONTRACT_CELL_COUNT,
    expected_transition_count: int = DEFAULT_EXPECTED_TRANSITION_COUNT,
) -> list[str]:
    problems: list[str] = []
    try:
        payload = _read_json_object(summary_json)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]
    retained_artifact = (
        "raw_or_85raw_ran" in payload and "raw_or_full_cohort_ran" not in payload
    )
    expected_row_universe_policy = (
        "discovery_expanded_85raw_alignment_rows"
        if retained_artifact
        else MATRIX_ROW_UNIVERSE_POLICY
    )
    expected_run_field = (
        "raw_or_85raw_ran" if retained_artifact else "raw_or_full_cohort_ran"
    )
    expected_fields: tuple[tuple[str, object], ...] = (
        ("schema_version", SCHEMA_VERSION),
        ("status", "pass"),
        ("activation_label", ACTIVATION_LABEL),
        ("product_authority_scope", PRODUCT_AUTHORITY_SCOPE),
        ("product_lane", "cid_nl_discovery"),
        ("product_scope_kind", "discovery_default_activation"),
        ("default_activation_effect", DISCOVERY_DEFAULT_EFFECT),
        ("feature_inclusion_authority_basis", FEATURE_INCLUSION_AUTHORITY_BASIS),
        ("matrix_row_universe_policy", expected_row_universe_policy),
        ("low_prevalence_feature_policy", LOW_PREVALENCE_FEATURE_POLICY),
        ("source_successor_identity_scope", SOURCE_SUCCESSOR_IDENTITY_SCOPE),
        ("accepted_discovery_cell_count", expected_contract_cell_count),
        ("accepted_backfill_count", expected_contract_cell_count),
        ("candidate_transition_count", expected_transition_count),
        ("expected_diff_count", str(expected_contract_cell_count)),
        ("written_discovery_cell_count", str(expected_contract_cell_count)),
        ("written_backfill_count", str(expected_contract_cell_count)),
        ("legacy_quant_matrix_effect", LEGACY_QUANT_MATRIX_EFFECT),
        ("legacy_provenance_status", LEGACY_PROVENANCE_STATUS),
        ("unused_expected_diff_count", "0"),
        ("product_writer_changed", True),
        ("default_quant_matrix_changed", True),
        ("default_matrix_files_written", True),
        ("workbook_or_gui_changed", False),
        ("selected_peak_area_or_counting_changed", False),
        ("backfill_writer_authority_changed", False),
        ("cid_nl_ms2_direct_productwriter_authority", False),
        ("candidate_rows_are_matrix_rows", False),
        (expected_run_field, False),
        ("full_matrix_retention", "externalized_output_only"),
    )
    for field, expected in expected_fields:
        if payload.get(field) != expected:
            problems.append(f"{field} mismatch")
    successor_evidence = payload.get("successor_self_evidence_summary")
    if (
        not isinstance(successor_evidence, Mapping)
        or successor_evidence.get("status") != "pass"
        or successor_evidence.get("checked_cell_count") != expected_contract_cell_count
    ):
        problems.append("successor_self_evidence_summary must pass")
    checks_path = summary_json.parent / "cid_nl_default_product_activation_checks.tsv"
    try:
        checks = read_tsv_required(checks_path, CHECK_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"checks_tsv: {exc}")
    else:
        failed = [row["check_id"] for row in checks if row.get("status") != "pass"]
        if failed:
            problems.append("failed checks: " + ";".join(failed))
    return problems


def _require_adopt_ready(
    payload: Mapping[str, Any],
    *,
    expected_contract_cell_count: int,
    expected_transition_count: int,
    expected_existing_successor_context_cell_count: int,
    expected_omitted_no_target_cell_count: int,
) -> None:
    if payload.get("adopt_gate_status") != "adopt_ready" or (
        payload.get("activation_bundle_adopt_ready") is not True
    ):
        raise ValueError("adopt gate is not adopt_ready")
    expected = {
        "contract_cell_count": expected_contract_cell_count,
        "changed_matrix_cell_count": expected_contract_cell_count,
        "candidate_transition_count": expected_transition_count,
        "existing_successor_context_cell_count": (
            expected_existing_successor_context_cell_count
        ),
        "omitted_no_target_cell_count": expected_omitted_no_target_cell_count,
        "forbidden_overlap_count": 0,
        "unexpected_matrix_change_count": 0,
        "missing_matrix_change_count": 0,
    }
    for field, expected_value in expected.items():
        observed = _int(payload.get(field))
        if observed != expected_value:
            raise ValueError(
                f"adopt summary {field} mismatch: "
                f"expected {expected_value}, observed {observed}"
            )
    for field in (
        "product_writer_changed",
        "default_quant_matrix_changed",
        "workbook_gui_changed",
        "production_ready",
    ):
        if _truthy(payload.get(field)):
            raise ValueError(f"adopt summary overclaims {field}")


def _read_contract_rows(paths: Sequence[Path]) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in paths:
        source = _contract_source(path)
        for row in read_tsv_required(path, CID_NL_EXPECTED_DIFF_COLUMNS):
            key = _contract_key(row)
            if key in seen:
                raise ValueError(
                    "duplicate CID-NL expected-diff contract row: "
                    f"{key[0]}|{key[1]}|{key[2]}"
                )
            seen.add(key)
            copied = dict(row)
            copied["_contract_source"] = source
            rows.append(copied)
    if not rows:
        raise ValueError("CID-NL expected-diff contract is empty")
    return tuple(rows)


def _contract_source(path: Path) -> str:
    name = path.name
    if "manual_resolved" in name:
        return "manual_resolved"
    if "agent_resolved" in name:
        return "agent_resolved"
    return "primary_supported"


def _validate_contract_shape(
    rows: Sequence[Mapping[str, str]],
    *,
    expected_contract_cell_count: int,
    expected_transition_count: int,
) -> None:
    if len(rows) != expected_contract_cell_count:
        raise ValueError(
            "CID-NL activation contract cell count mismatch: "
            f"expected {expected_contract_cell_count}, observed {len(rows)}"
        )
    transitions = {text_value(row.get("transition_key")) for row in rows}
    if len(transitions) != expected_transition_count:
        raise ValueError(
            "CID-NL activation contract transition count mismatch: "
            f"expected {expected_transition_count}, observed {len(transitions)}"
        )
    for row in rows:
        if text_value(row.get("authority_gate")) != (
            "candidate_only_expected_diff_required_no_product_write"
        ):
            raise ValueError("CID-NL activation contract authority gate drift")
        if text_value(row.get("product_authority_effect")) != (
            "diagnostic_only_no_authority_change"
        ):
            raise ValueError("CID-NL activation contract overclaims authority")
        if text_value(row.get("legacy_successor_matrix_effect")) != (
            LEGACY_QUANT_MATRIX_EFFECT
        ):
            raise ValueError("CID-NL activation contract matrix effect drift")
        for field in (
            "transition_key",
            "sample_stem",
            "source_peak_hypothesis_id",
            "successor_peak_hypothesis_id",
            "successor_mz",
            "successor_rt",
            "successor_product_mz",
            "successor_neutral_loss_tag",
            "candidate_quant_value",
        ):
            if not text_value(row.get(field)):
                raise ValueError(f"CID-NL activation contract missing {field}")


def _validate_value_delta(
    contract_rows: Sequence[Mapping[str, str]],
    value_delta_rows: Sequence[Mapping[str, str]],
) -> None:
    contract_by_key = {_contract_key(row): row for row in contract_rows}
    delta_by_key = {_contract_key(row): row for row in value_delta_rows}
    if set(contract_by_key) != set(delta_by_key):
        raise ValueError("CID-NL activation value-delta key set mismatch")
    for key, delta in delta_by_key.items():
        contract = contract_by_key[key]
        if text_value(delta.get("original_matrix_value")):
            raise ValueError(
                "CID-NL activation value-delta original value is not blank"
            )
        if text_value(delta.get("value_changed")) != "TRUE":
            raise ValueError("CID-NL activation value-delta row is not changed")
        if not numeric_equal(
            delta.get("candidate_quant_value"),
            contract.get("candidate_quant_value"),
        ):
            raise ValueError("CID-NL activation value-delta quant value mismatch")
        if text_value(delta.get("product_authority_effect")) != (
            "diagnostic_only_no_authority_change"
        ):
            raise ValueError("CID-NL activation value-delta overclaims authority")


def _activation_manifest_rows(
    *,
    contract_rows: Sequence[Mapping[str, str]],
    authority_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    authority_by_key: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in authority_rows:
        key = (
            text_value(row.get("peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
        )
        if all(key):
            authority_by_key[key] = row
    manifest_rows: list[dict[str, str]] = []
    for contract in contract_rows:
        key = (
            text_value(contract.get("successor_peak_hypothesis_id")),
            text_value(contract.get("sample_stem")),
        )
        authority = authority_by_key.get(key)
        if authority is None:
            raise ValueError(
                "missing successor authority manifest row: " + f"{key[0]}/{key[1]}"
            )
        if authority.get("write_authority") != "TRUE" or (
            authority.get("matrix_write_allowed") != "TRUE"
        ):
            raise ValueError("successor authority manifest row is not writable")
        if authority.get("shadow_only") != "FALSE":
            raise ValueError("successor authority manifest row is shadow_only")
        if not numeric_equal(
            authority.get("quant_value"),
            contract.get("candidate_quant_value"),
        ):
            raise ValueError(
                f"successor authority manifest quant_value mismatch: {key[0]}/{key[1]}"
            )
        manifest_row = {
            column: authority.get(column, "")
            for column in PRODUCTION_ACCEPTANCE_COLUMNS
        }
        manifest_row["decision_reason"] = (
            "cid_nl_default_product_activation_v1:"
            f"transition={contract['transition_key']}"
        )
        manifest_row["closure_rule_ids"] = _append_tokens(
            manifest_row.get("closure_rule_ids", ""),
            (
                "cid_nl_activation_adopt_gate_v1",
                "cid_nl_default_product_activation_v1",
            ),
        )
        manifest_row["manifest_sha256"] = ""
        manifest_rows.append(manifest_row)
    manifest_sha = production_acceptance_manifest_sha256(manifest_rows)
    for row in manifest_rows:
        row["manifest_sha256"] = manifest_sha
    return manifest_rows


def _successor_self_evidence_summary(
    *,
    contract_rows: Sequence[Mapping[str, str]],
    authority_rows: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    authority_by_key: dict[tuple[str, str], Mapping[str, str]] = {}
    duplicate_manifest_keys = 0
    for row in authority_rows:
        key = (
            text_value(row.get("peak_hypothesis_id")),
            text_value(row.get("sample_stem")),
        )
        if not all(key):
            continue
        if key in authority_by_key:
            duplicate_manifest_keys += 1
        authority_by_key[key] = row

    contract_keys: list[tuple[str, str]] = []
    problem_count = duplicate_manifest_keys
    missing_manifest = 0
    tag_missing = 0
    quant_mismatch = 0
    provenance_missing = 0
    authority_flag_mismatch = 0

    for contract in contract_rows:
        successor = text_value(contract.get("successor_peak_hypothesis_id"))
        sample = text_value(contract.get("sample_stem"))
        contract_keys.append((successor, sample))
        if not text_value(contract.get("successor_neutral_loss_tag")):
            tag_missing += 1
            problem_count += 1
        authority = authority_by_key.get((successor, sample))
        if authority is None:
            missing_manifest += 1
            problem_count += 1
            continue
        if (
            authority.get("write_authority") != "TRUE"
            or authority.get("matrix_write_allowed") != "TRUE"
            or authority.get("shadow_only") != "FALSE"
        ):
            authority_flag_mismatch += 1
            problem_count += 1
        if not numeric_equal(
            authority.get("quant_value"),
            contract.get("candidate_quant_value"),
        ):
            quant_mismatch += 1
            problem_count += 1
        if not (
            text_value(authority.get("source_artifact_relpath"))
            and text_value(authority.get("source_artifact_sha256"))
            and text_value(authority.get("source_row_sha256"))
        ):
            provenance_missing += 1
            problem_count += 1

    duplicate_contract_keys = len(contract_keys) - len(set(contract_keys))
    problem_count += duplicate_contract_keys
    return {
        "status": "pass" if problem_count == 0 else "fail",
        "checked_cell_count": len(contract_rows),
        "unique_successor_sample_count": len(set(contract_keys)),
        "problem_count": problem_count,
        "missing_manifest_count": missing_manifest,
        "duplicate_manifest_key_count": duplicate_manifest_keys,
        "duplicate_contract_key_count": duplicate_contract_keys,
        "successor_tag_missing_count": tag_missing,
        "authority_flag_mismatch_count": authority_flag_mismatch,
        "quant_mismatch_count": quant_mismatch,
        "provenance_missing_count": provenance_missing,
        "basis": FEATURE_INCLUSION_AUTHORITY_BASIS,
    }


def _quant_expected_diff_rows(
    contract_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    return [
        {
            "schema_version": "quant_matrix_version_expected_diff_v1",
            "peak_hypothesis_id": text_value(row.get("successor_peak_hypothesis_id")),
            "sample_stem": text_value(row.get("sample_stem")),
            "baseline_value": "",
            "activated_value": text_value(row.get("candidate_quant_value")),
            "expected_matrix_effect": LEGACY_QUANT_MATRIX_EFFECT,
            "expected_reason": (
                "cid_nl_default_product_activation_v1:"
                f"transition={text_value(row.get('transition_key'))}"
            ),
        }
        for row in contract_rows
    ]


def _compact_manifest_rows(
    contract_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in contract_rows:
        grouped[
            (
                text_value(row.get("_contract_source")),
                text_value(row.get("transition_key")),
            )
        ].append(row)
    rows: list[dict[str, Any]] = []
    for (source, transition), items in sorted(grouped.items()):
        first = items[0]
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "transition_key": transition,
                "contract_source": source,
                "contract_cell_count": len(items),
                "source_peak_hypothesis_id": first["source_peak_hypothesis_id"],
                "successor_peak_hypothesis_id": first["successor_peak_hypothesis_id"],
                "successor_product_mz": first["successor_product_mz"],
                "successor_neutral_loss_tag": first["successor_neutral_loss_tag"],
                "default_activation_effect": DISCOVERY_DEFAULT_EFFECT,
                "legacy_quant_matrix_effect": LEGACY_QUANT_MATRIX_EFFECT,
                "product_authority_scope": PRODUCT_AUTHORITY_SCOPE,
            }
        )
    return rows


def _expected_diff_summary(path: Path) -> dict[str, str]:
    rows = read_tsv_required(path, EXPECTED_DIFF_SUMMARY_COLUMNS)
    if len(rows) != 1:
        raise ValueError("expected_diff_summary must contain exactly one row")
    return dict(rows[0])


def _matrix_delta_summary(
    *,
    baseline_quant_matrix_tsv: Path,
    activated_quant_matrix_tsv: Path,
    matrix_identity_tsv: Path,
    expected_values: Mapping[tuple[str, str], str],
) -> dict[str, Any]:
    baseline_header, baseline_rows = read_tsv_with_header(baseline_quant_matrix_tsv)
    activated_header, activated_rows = read_tsv_with_header(activated_quant_matrix_tsv)
    if baseline_header != activated_header:
        return {"status": "fail", "reason": "header_mismatch"}
    if len(baseline_rows) != len(activated_rows):
        return {"status": "fail", "reason": "row_count_mismatch"}
    identity_by_index = _identity_by_index(matrix_identity_tsv)
    sample_columns = [
        column for column in baseline_header if column not in {"Mz", "RT"}
    ]
    changed_values: dict[tuple[str, str], str] = {}
    for row_index, (baseline, activated) in enumerate(
        zip(baseline_rows, activated_rows, strict=True),
        start=1,
    ):
        peak = identity_by_index.get(row_index, "")
        for sample in sample_columns:
            baseline_value = text_value(baseline.get(sample))
            activated_value = text_value(activated.get(sample))
            if baseline_value == activated_value:
                continue
            changed_values[(peak, sample)] = activated_value
    expected_keys = set(expected_values)
    changed_keys = set(changed_values)
    value_mismatches = [
        f"{peak}/{sample}"
        for peak, sample in sorted(expected_keys & changed_keys)
        if not numeric_equal(
            changed_values[(peak, sample)], expected_values[(peak, sample)]
        )
    ]
    unexpected = sorted(changed_keys - expected_keys)
    missing = sorted(expected_keys - changed_keys)
    status = "pass" if not (unexpected or missing or value_mismatches) else "fail"
    return {
        "status": status,
        "changed_cell_count": len(changed_keys),
        "expected_write_count": len(expected_keys),
        "unexpected_write_count": len(unexpected),
        "missing_write_count": len(missing),
        "value_mismatch_count": len(value_mismatches),
        "unexpected_writes": [f"{peak}/{sample}" for peak, sample in unexpected],
        "missing_writes": [f"{peak}/{sample}" for peak, sample in missing],
        "value_mismatches": value_mismatches,
    }


def _cell_provenance_summary(
    *,
    cell_provenance_tsv: Path,
    expected_values: Mapping[tuple[str, str], str],
) -> dict[str, Any]:
    rows = read_tsv_required(cell_provenance_tsv, CELL_PROVENANCE_COLUMNS)
    accepted = [
        row
        for row in rows
        if text_value(row.get("cell_status")) == LEGACY_PROVENANCE_STATUS
    ]
    accepted_keys = {
        (row["peak_hypothesis_id"], row["sample_stem"]) for row in accepted
    }
    expected_keys = set(expected_values)
    source_mismatches = [
        f"{row['peak_hypothesis_id']}/{row['sample_stem']}"
        for row in accepted
        if row.get("value_source") != "ProductionAcceptanceManifest"
        or row.get("write_authority") != "TRUE"
    ]
    value_mismatches = [
        f"{row['peak_hypothesis_id']}/{row['sample_stem']}"
        for row in accepted
        if not numeric_equal(
            row.get("matrix_value"),
            expected_values.get((row["peak_hypothesis_id"], row["sample_stem"]), ""),
        )
    ]
    status = "pass"
    if accepted_keys != expected_keys or source_mismatches or value_mismatches:
        status = "fail"
    return {
        "status": status,
        "accepted_backfill_cell_count": len(accepted),
        "accepted_discovery_cell_count": len(accepted),
        "legacy_provenance_status": LEGACY_PROVENANCE_STATUS,
        "expected_write_count": len(expected_keys),
        "missing_provenance_count": len(expected_keys - accepted_keys),
        "unexpected_provenance_count": len(accepted_keys - expected_keys),
        "source_mismatch_count": len(source_mismatches),
        "value_mismatch_count": len(value_mismatches),
    }


def _check_rows(
    *,
    adopt_summary: Mapping[str, Any],
    contract_rows: Sequence[Mapping[str, str]],
    expected_summary: Mapping[str, str],
    delta_summary: Mapping[str, Any],
    provenance_summary: Mapping[str, Any],
    expected_contract_cell_count: int,
    expected_transition_count: int,
    expected_existing_successor_context_cell_count: int,
    expected_omitted_no_target_cell_count: int,
    successor_evidence_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    transition_count = len(
        {text_value(row.get("transition_key")) for row in contract_rows}
    )
    source_counts = Counter(
        text_value(row.get("_contract_source")) for row in contract_rows
    )
    return [
        _check(
            "adopt_gate_status",
            adopt_summary.get("adopt_gate_status"),
            "adopt_ready",
            adopt_summary.get("adopt_gate_status") == "adopt_ready",
        ),
        _check(
            "contract_cell_count",
            len(contract_rows),
            expected_contract_cell_count,
            len(contract_rows) == expected_contract_cell_count,
        ),
        _check(
            "candidate_transition_count",
            transition_count,
            expected_transition_count,
            transition_count == expected_transition_count,
        ),
        _check(
            "existing_successor_context_preserved",
            _int(adopt_summary.get("existing_successor_context_cell_count")),
            expected_existing_successor_context_cell_count,
            _int(adopt_summary.get("existing_successor_context_cell_count"))
            == expected_existing_successor_context_cell_count,
        ),
        _check(
            "omitted_no_target_preserved",
            _int(adopt_summary.get("omitted_no_target_cell_count")),
            expected_omitted_no_target_cell_count,
            _int(adopt_summary.get("omitted_no_target_cell_count"))
            == expected_omitted_no_target_cell_count,
        ),
        _check(
            "expected_diff_replay_status",
            expected_summary.get("acceptance_status"),
            "pass",
            expected_summary.get("acceptance_status") == "pass",
        ),
        _check(
            "expected_diff_written_count",
            expected_summary.get("written_backfill_count"),
            str(expected_contract_cell_count),
            expected_summary.get("written_backfill_count")
            == str(expected_contract_cell_count),
        ),
        _check(
            "expected_diff_unused_count",
            expected_summary.get("unused_expected_diff_count"),
            "0",
            expected_summary.get("unused_expected_diff_count") == "0",
        ),
        _check(
            "matrix_delta_exact_keyset",
            delta_summary.get("status"),
            "pass",
            delta_summary.get("status") == "pass",
        ),
        _check(
            "matrix_delta_changed_count",
            delta_summary.get("changed_cell_count"),
            expected_contract_cell_count,
            delta_summary.get("changed_cell_count") == expected_contract_cell_count,
        ),
        _check(
            "cell_provenance_exact_keyset",
            provenance_summary.get("status"),
            "pass",
            provenance_summary.get("status") == "pass",
        ),
        _check(
            "cell_provenance_accepted_count",
            provenance_summary.get("accepted_backfill_cell_count"),
            expected_contract_cell_count,
            provenance_summary.get("accepted_backfill_cell_count")
            == expected_contract_cell_count,
        ),
        _check(
            "successor_self_evidence_contract",
            (
                f"status={successor_evidence_summary.get('status')};"
                f"problems={successor_evidence_summary.get('problem_count')}"
            ),
            "pass;problems=0",
            successor_evidence_summary.get("status") == "pass"
            and successor_evidence_summary.get("problem_count") == 0
            and successor_evidence_summary.get("checked_cell_count")
            == expected_contract_cell_count,
            (
                "Feature inclusion is based on successor tag, quant value, "
                "write authority, and provenance; source/successor similarity "
                "is reserved for identity review."
            ),
        ),
        _check(
            "discovery_terminology_boundary",
            (
                f"default_activation_effect={DISCOVERY_DEFAULT_EFFECT};"
                f"legacy_quant_matrix_effect={LEGACY_QUANT_MATRIX_EFFECT}"
            ),
            "discovery public term with QuantMatrixVersion compatibility",
            True,
        ),
        _check(
            "product_surface_flags",
            "product_writer/default_matrix TRUE; workbook/gui FALSE",
            "explicit public default activation",
            True,
        ),
        _check(
            "matrix_row_universe_policy",
            MATRIX_ROW_UNIVERSE_POLICY,
            (
                "Discovery-expanded untargeted matrix; downstream filters "
                "handle low prevalence"
            ),
            True,
        ),
        _check(
            "source_successor_identity_scope",
            SOURCE_SUCCESSOR_IDENTITY_SCOPE,
            "identity review only",
            True,
        ),
        _check(
            "contract_source_counts",
            json.dumps(dict(sorted(source_counts.items())), sort_keys=True),
            "tracked",
            True,
        ),
    ]


def _check(
    check_id: str,
    observed: object,
    expected: object,
    ok: bool,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "check_id": check_id,
        "status": "pass" if ok else "fail",
        "observed": observed,
        "expected": expected,
        "notes": notes,
    }


def _summary_payload(
    *,
    docs_dir: Path,
    output_dir: Path,
    source_root: Path,
    adopt_summary_json: Path,
    successor_authority_manifest_tsv: Path,
    input_quant_matrix_tsv: Path,
    input_matrix_identity_tsv: Path,
    value_delta_tsv: Path,
    expected_diff_contract_tsvs: Sequence[Path],
    activation_manifest_tsv: Path,
    activation_expected_diff_tsv: Path,
    compact_manifest_tsv: Path,
    checks_tsv: Path,
    activation_outputs: Mapping[str, Path],
    expected_summary: Mapping[str, str],
    delta_summary: Mapping[str, Any],
    provenance_summary: Mapping[str, Any],
    check_rows: Sequence[Mapping[str, Any]],
    contract_rows: Sequence[Mapping[str, str]],
    adopt_summary: Mapping[str, Any],
    successor_evidence_summary: Mapping[str, Any],
) -> dict[str, Any]:
    transition_count = len(
        {text_value(row.get("transition_key")) for row in contract_rows}
    )
    source_counts = Counter(
        text_value(row.get("_contract_source")) for row in contract_rows
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "activation_label": ACTIVATION_LABEL,
        "validation_label": "product_ready_cid_nl_default_activation",
        "product_authority_scope": PRODUCT_AUTHORITY_SCOPE,
        "product_lane": "cid_nl_discovery",
        "product_scope_kind": "discovery_default_activation",
        "default_activation_effect": DISCOVERY_DEFAULT_EFFECT,
        "feature_inclusion_authority_basis": FEATURE_INCLUSION_AUTHORITY_BASIS,
        "matrix_row_universe_policy": MATRIX_ROW_UNIVERSE_POLICY,
        "low_prevalence_feature_policy": LOW_PREVALENCE_FEATURE_POLICY,
        "source_successor_identity_scope": SOURCE_SUCCESSOR_IDENTITY_SCOPE,
        "accepted_discovery_cell_count": len(contract_rows),
        "accepted_backfill_count": len(contract_rows),
        "candidate_transition_count": transition_count,
        "expected_diff_count": expected_summary.get("expected_diff_count", ""),
        "written_discovery_cell_count": expected_summary.get(
            "written_backfill_count",
            "",
        ),
        "written_backfill_count": expected_summary.get("written_backfill_count", ""),
        "legacy_quant_matrix_effect": LEGACY_QUANT_MATRIX_EFFECT,
        "legacy_provenance_status": LEGACY_PROVENANCE_STATUS,
        "unused_expected_diff_count": expected_summary.get(
            "unused_expected_diff_count",
            "",
        ),
        "existing_successor_context_cell_count": _int(
            adopt_summary.get("existing_successor_context_cell_count")
        ),
        "omitted_no_target_cell_count": _int(
            adopt_summary.get("omitted_no_target_cell_count")
        ),
        "blocked_candidate_cell_count": _int(
            adopt_summary.get("blocked_candidate_cell_count")
        ),
        "agent_hold_cell_count": _int(adopt_summary.get("agent_hold_cell_count")),
        "source_contract_counts": dict(sorted(source_counts.items())),
        "check_count": len(check_rows),
        "read_only": False,
        "write_authority": True,
        "product_writer_changed": True,
        "default_quant_matrix_changed": True,
        "default_matrix_files_written": True,
        "workbook_or_gui_changed": False,
        "selected_peak_area_or_counting_changed": False,
        "backfill_writer_authority_changed": False,
        "cid_nl_ms2_direct_productwriter_authority": False,
        "candidate_rows_are_matrix_rows": False,
        "raw_or_full_cohort_ran": False,
        "full_matrix_retention": "externalized_output_only",
        "matrix_delta_summary": delta_summary,
        "cell_provenance_summary": provenance_summary,
        "successor_self_evidence_summary": dict(successor_evidence_summary),
        "input_artifacts": {
            "adopt_summary_json": _artifact(adopt_summary_json, source_root),
            "successor_authority_manifest_tsv": _artifact(
                successor_authority_manifest_tsv,
                source_root,
            ),
            "input_quant_matrix_tsv": _artifact(input_quant_matrix_tsv, source_root),
            "input_matrix_identity_tsv": _artifact(
                input_matrix_identity_tsv,
                source_root,
            ),
            "value_delta_tsv": _artifact(value_delta_tsv, source_root),
            "expected_diff_contract_tsvs": [
                _artifact(path, source_root) for path in expected_diff_contract_tsvs
            ],
        },
        "artifacts": {
            "summary_json": {
                "path": _relative_or_absolute(
                    docs_dir / "cid_nl_default_product_activation_summary.json"
                ),
                "retention_decision": "keep_summary",
            },
            "compact_manifest_tsv": artifact_record(
                compact_manifest_tsv,
                base_dir=docs_dir,
            )
            | {"retention_decision": "keep_contract"},
            "checks_tsv": artifact_record(checks_tsv, base_dir=docs_dir)
            | {"retention_decision": "keep_summary"},
            "activation_manifest_tsv": artifact_record(
                activation_manifest_tsv,
                base_dir=output_dir,
            )
            | {"retention_decision": "externalize_full_input"},
            "activation_expected_diff_tsv": artifact_record(
                activation_expected_diff_tsv,
                base_dir=output_dir,
            )
            | {"retention_decision": "externalize_full_input"},
            **{
                f"default_output_{label}": artifact_record(path, base_dir=output_dir)
                | {"retention_decision": "externalize_full_output"}
                for label, path in activation_outputs.items()
            },
        },
        "authority_statement": (
            "CID-NL default activation adopts exactly the 95-cell adopt-ready "
            "expected-diff bundle. CID-NL/MS2 and overlay evidence are not direct "
            "ProductWriter authority; the write authority is the generated "
            "ProductionAcceptanceManifest plus QuantMatrixVersion expected-diff "
            "replay. Existing successor context cells and omitted no-target cells "
            "are preserved as no-write context."
        ),
        "row_universe_statement": (
            "The default output uses the current FULL_COHORT-derived "
            "Discovery-expanded "
            "alignment row universe. Low-prevalence features are allowed in this "
            "untargeted handoff and are expected to be filtered downstream; low "
            "prevalence is not a CID-NL false-positive blocker by itself."
        ),
        "identity_statement": (
            "Source/successor m/z or RT similarity is not the feature-inclusion "
            "gate. It belongs to identity authority questions such as merge, "
            "dedupe, replacement, or migration. A successor may enter this "
            "activation scope only through its own CID-NL tag, quant value, "
            "write-ready manifest row, and provenance."
        ),
        "terminology_statement": (
            "This artifact is Discovery-first at the product boundary. Legacy "
            "QuantMatrixVersion fields such as accepted_backfill_count, "
            "written_backfill_count, cell_status=accepted_backfill, and "
            "expected_matrix_effect=write_accepted_backfill are compatibility "
            "terms from the shared matrix writer, not Backfill product scope."
        ),
    }


def _write_readme(
    path: Path,
    *,
    payload: Mapping[str, Any],
    compact_manifest_tsv: Path,
    checks_tsv: Path,
) -> None:
    summary_relpath = _relative_or_absolute(
        path.parent / "cid_nl_default_product_activation_summary.json",
    )
    compact_relpath = _relative_or_absolute(compact_manifest_tsv)
    lines = [
        "# CID-NL Default Product Activation v1",
        "",
        "Status: `pass`.",
        "",
        "This is the explicit public default activation change for the narrowed "
        "CID-NL Discovery bundle. It writes exactly 95 adopt-ready blank cells "
        "through the existing ProductionAcceptanceManifest -> QuantMatrixVersion "
        "path.",
        "",
        "It does not rerun RAW, change workbook/GUI behavior, change selected "
        "peak/area/counting, treat candidate rows as matrix rows, or make "
        "CID-NL/MS2 evidence direct ProductWriter authority.",
        "",
        "Terminology boundary: this is a Discovery product scope. The compact "
        "summary uses `accepted_discovery_cell_count` and "
        "`write_cid_nl_discovery_default_cell` for the public decision. Legacy "
        "`accepted_backfill` / `write_accepted_backfill` values are retained only "
        "inside the shared QuantMatrixVersion writer and provenance compatibility "
        "surface.",
        "",
        "Row-universe boundary: the output matrix uses the current FULL_COHORT-derived "
        "Discovery-expanded alignment row universe. Sparse rows are acceptable "
        "for this untargeted handoff; prevalence filtering belongs downstream, "
        "not in this CID-NL activation gate.",
        "",
        "Feature-inclusion boundary: source/successor m/z or RT similarity is an "
        "identity-review question only. The 95 active writes are accepted because "
        "the successor cell itself has CID-NL tag context, quant value, "
        "write-ready manifest authority, and provenance.",
        "",
        "## Counts",
        "",
        "- Accepted Discovery default writes: "
        f"`{payload['accepted_discovery_cell_count']}`",
        f"- Candidate transitions: `{payload['candidate_transition_count']}`",
        "- Existing successor context cells preserved: "
        f"`{payload['existing_successor_context_cell_count']}`",
        "- Omitted no-target cells preserved: "
        f"`{payload['omitted_no_target_cell_count']}`",
        f"- Product authority scope: `{payload['product_authority_scope']}`",
        "",
        "## Versioned Summary",
        "",
        f"- Summary JSON: `{summary_relpath}`",
        f"- Compact transition manifest: `{compact_relpath}`",
        f"- Checks TSV: `{_relative_or_absolute(checks_tsv)}`",
        "",
        "Full matrix and provenance TSV outputs are intentionally externalized "
        "under `output/validation/cid_nl_default_product_activation_v1/` to keep "
        "review diffs small.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _identity_by_index(path: Path) -> dict[int, str]:
    rows = read_tsv_required(path, ("matrix_row_index", "peak_hypothesis_id"))
    result: dict[int, str] = {}
    for row in rows:
        result[int(row["matrix_row_index"])] = row["peak_hypothesis_id"]
    return result


def _contract_key(row: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        text_value(row.get("successor_peak_hypothesis_id")),
        text_value(row.get("sample_stem")),
        text_value(row.get("transition_key")),
    )


def _append_tokens(value: str, tokens: Sequence[str]) -> str:
    existing = split_semicolon_labels(value)
    for token in tokens:
        if token not in existing:
            existing.append(token)
    return ";".join(existing)


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def _artifact(path: Path, source_root: Path) -> dict[str, Any]:
    return {
        "path": _source_relpath(path, source_root),
        "sha256": file_sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _source_relpath(path: Path, source_root: Path) -> str:
    try:
        return (
            path.resolve(strict=False)
            .relative_to(source_root.resolve(strict=False))
            .as_posix()
        )
    except ValueError:
        return str(path)


def _relative_or_absolute(path: Path) -> str:
    try:
        return (
            path.resolve(strict=False)
            .relative_to(
                ROOT.resolve(strict=False),
            )
            .as_posix()
        )
    except ValueError:
        return str(path)


def _rewrite_source_summary_paths(source_summary_tsv: Path) -> None:
    rows = read_tsv_required(source_summary_tsv, ("schema_version",))
    if len(rows) != 1:
        raise ValueError("source_summary must contain exactly one row")
    row = dict(rows[0])
    for field in (
        "input_quant_matrix_tsv",
        "input_matrix_identity_tsv",
        "production_acceptance_manifest_tsv",
        "expected_diff_tsv",
    ):
        value = row.get(field, "")
        if value:
            row[field] = _portable_relpath(Path(value), source_summary_tsv.parent)
    write_tsv(
        source_summary_tsv,
        [row],
        tuple(row),
        extrasaction="raise",
        lineterminator="\n",
    )


def _portable_relpath(path: Path, base_dir: Path) -> str:
    try:
        return Path(
            os.path.relpath(
                path.resolve(strict=False),
                base_dir.resolve(strict=False),
            )
        ).as_posix()
    except ValueError:
        return str(path)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return text_value(value).upper() == "TRUE"


def _int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    text = text_value(value)
    return int(text) if text else 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument(
        "--expected-diff-contract-tsv",
        type=Path,
        action="append",
    )
    parser.add_argument(
        "--adopt-summary-json",
        type=Path,
        default=DEFAULT_ADOPT_SUMMARY_JSON,
    )
    parser.add_argument(
        "--successor-authority-manifest-tsv",
        type=Path,
        default=DEFAULT_SUCCESSOR_AUTHORITY_MANIFEST_TSV,
    )
    parser.add_argument(
        "--input-quant-matrix-tsv",
        type=Path,
        default=DEFAULT_ALIGNMENT_MATRIX_TSV,
    )
    parser.add_argument(
        "--input-matrix-identity-tsv",
        type=Path,
        default=DEFAULT_ALIGNMENT_MATRIX_IDENTITY_TSV,
    )
    parser.add_argument("--value-delta-tsv", type=Path, default=DEFAULT_VALUE_DELTA_TSV)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check_only:
        summary_json = args.summary_json or (
            args.docs_dir / "cid_nl_default_product_activation_summary.json"
        )
        problems = validate_cid_nl_default_product_activation(summary_json=summary_json)
        for problem in problems:
            print(f"cid_nl_default_product_activation_problem: {problem}")
        return 2 if problems else 0
    try:
        payload = build_cid_nl_default_product_activation(
            output_dir=args.output_dir,
            docs_dir=args.docs_dir,
            source_root=args.source_root,
            expected_diff_contract_tsvs=tuple(
                args.expected_diff_contract_tsv or DEFAULT_EXPECTED_DIFF_CONTRACTS
            ),
            adopt_summary_json=args.adopt_summary_json,
            successor_authority_manifest_tsv=args.successor_authority_manifest_tsv,
            input_quant_matrix_tsv=args.input_quant_matrix_tsv,
            input_matrix_identity_tsv=args.input_matrix_identity_tsv,
            value_delta_tsv=args.value_delta_tsv,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    summary_path = args.docs_dir / "cid_nl_default_product_activation_summary.json"
    print(f"cid_nl_default_product_activation_summary: {summary_path}")
    print(f"cid_nl_default_product_activation_status: {payload['status']}")
    print(
        "cid_nl_default_product_activation_accepted_discovery_cell_count: "
        f"{payload['accepted_discovery_cell_count']}"
    )
    if args.require_pass and payload.get("status") != "pass":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
