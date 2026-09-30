"""Build/check explicit default QuantMatrix product activation outputs.

This activation gate writes the default numeric quant matrix from the validated
ProductionAcceptanceManifest and expected-diff contract. It does not run a
scorer, read RAW/full_cohort, change workbooks/GUI behavior, or alter selected
peaks/areas/counting.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_quant_matrix_product_ready_closeout import (
    DEFAULT_PRODUCT_READY_CLOSEOUT_OUTPUT_DIR,
    resolve_quant_matrix_product_ready_closeout_real_bundle,
    validate_quant_matrix_product_ready_closeout,
)
from scripts.build_quant_matrix_real_bundle import (
    DEFAULT_ACCEPTED_BACKFILL_COUNT,
    DEFAULT_DOWNSTREAM_SCOPE,
    DEFAULT_SOURCE_RUN_ID,
)
from scripts.build_quant_matrix_version import run_activation
from xic_extractor.diagnostics.quant_matrix.fixture_contract import (
    validate_fixture_contract,
    write_cell_provenance_contract,
)
from xic_extractor.diagnostics.quant_matrix.version import EXPECTED_DIFF_SUMMARY_COLUMNS
from xic_extractor.tabular_io import (
    file_sha256,
    optional_int,
    read_tsv_required,
    read_tsv_with_header,
    write_tsv,
)

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PRODUCT_ACTIVATION_OUTPUT_DIR = Path(
    "docs/superpowers/validation/quant_matrix_default_product_activation",
)
DEFAULT_PRODUCT_READY_CLOSEOUT_SUMMARY_JSON = (
    DEFAULT_PRODUCT_READY_CLOSEOUT_OUTPUT_DIR
    / "quant_matrix_product_ready_closeout_summary.json"
)

DEFAULT_PRODUCT_ACTIVATION_SCHEMA = "quant_matrix_default_product_activation"
OUTPUT_COMPARISON_SCHEMA = "quant_matrix_default_product_activation_check"
PRODUCT_AUTHORITY_SCOPE = "backfill_policy_write_ready_rows"

CHECK_COLUMNS = (
    "schema_version",
    "check_id",
    "status",
    "source",
    "meaning",
)

REFERENCE_OUTPUT_LABELS = (
    "quant_matrix",
    "row_summary",
    "expected_diff_summary",
)
DEFAULT_OUTPUT_LABELS = REFERENCE_OUTPUT_LABELS + (
    "source_summary",
    "cell_provenance_summary",
    "cell_provenance_minimal_fixture",
)


def build_quant_matrix_default_product_activation(
    *,
    output_dir: Path = DEFAULT_PRODUCT_ACTIVATION_OUTPUT_DIR,
    source_root: Path = ROOT,
    product_ready_closeout_summary_json: Path = (
        DEFAULT_PRODUCT_READY_CLOSEOUT_SUMMARY_JSON
    ),
    expected_source_run_id: str = DEFAULT_SOURCE_RUN_ID,
    expected_downstream_scope: str = DEFAULT_DOWNSTREAM_SCOPE,
    expected_accepted_backfill_count: int = DEFAULT_ACCEPTED_BACKFILL_COUNT,
) -> Mapping[str, Path]:
    closeout_summary = _resolve_source(
        product_ready_closeout_summary_json,
        source_root=source_root,
    )
    _raise_if_problems(
        "product ready closeout check failed",
        validate_quant_matrix_product_ready_closeout(
            summary_json=closeout_summary,
            source_root=source_root,
            expected_source_run_id=expected_source_run_id,
            expected_downstream_scope=expected_downstream_scope,
            expected_accepted_backfill_count=expected_accepted_backfill_count,
        ),
    )
    closeout_payload = _read_json_object(closeout_summary)
    real_bundle_summary = resolve_quant_matrix_product_ready_closeout_real_bundle(
        closeout_summary,
        source_root=source_root,
    )
    bundle_payload = _read_json_object(real_bundle_summary)
    _raise_if_problems(
        "activation inputs mismatch",
        _source_identity_problems(
            bundle_payload,
            closeout_payload,
            expected_source_run_id=expected_source_run_id,
            expected_downstream_scope=expected_downstream_scope,
            expected_accepted_backfill_count=expected_accepted_backfill_count,
        ),
    )
    bundle_paths = _real_bundle_paths(bundle_payload, real_bundle_summary)

    output_dir.mkdir(parents=True, exist_ok=True)
    default_output_dir = output_dir / "default_output"
    activation_outputs = run_activation(
        input_quant_matrix_tsv=bundle_paths["baseline_quant_matrix"],
        input_matrix_identity_tsv=bundle_paths["input_matrix_identity"],
        production_acceptance_manifest_tsv=bundle_paths[
            "production_acceptance_manifest"
        ],
        expected_diff_tsv=bundle_paths["expected_diff"],
        output_dir=default_output_dir,
        manifest_root=source_root,
    )
    _rewrite_source_summary_paths(activation_outputs["source_summary"])
    expected_diff_summary = _expected_diff_summary_status(
        activation_outputs["expected_diff_summary"],
    )
    _raise_if_problems(
        "expected-diff summary failed",
        _expected_diff_payload_problems(
            expected_diff_summary,
            expected_accepted_backfill_count=expected_accepted_backfill_count,
        ),
    )
    _raise_if_problems(
        "default output reference comparison failed",
        _reference_output_problems(
            activation_outputs=activation_outputs,
            reference_paths=bundle_paths,
        ),
    )
    _raise_if_problems(
        "source summary input hashes failed",
        _source_summary_input_hash_problems(
            activation_outputs["source_summary"],
            expected_inputs=bundle_paths,
        ),
    )
    _raise_if_problems(
        "cell provenance completeness failed",
        _cell_provenance_completeness_problems(
            quant_matrix_tsv=activation_outputs["quant_matrix"],
            cell_provenance_tsv=activation_outputs["cell_provenance"],
        ),
    )

    cell_counts = _cell_counts(activation_outputs["cell_provenance"])
    fixture_contract_outputs = _write_fixture_contract_outputs(
        activation_outputs=activation_outputs,
        default_output_dir=default_output_dir,
    )
    check_rows = _check_rows(
        activation_outputs=activation_outputs,
        reference_paths=bundle_paths,
        expected_diff_summary=expected_diff_summary,
    )
    checks_tsv = output_dir / "default_product_activation_checks.tsv"
    write_tsv(
        checks_tsv,
        check_rows,
        CHECK_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    summary_json = output_dir / "quant_matrix_default_product_activation_summary.json"
    _write_summary(
        summary_json,
        output_dir=output_dir,
        source_root=source_root,
        closeout_summary=closeout_summary,
        bundle_payload=bundle_payload,
        closeout_payload=closeout_payload,
        bundle_paths=bundle_paths,
        activation_outputs=activation_outputs,
        fixture_contract_outputs=fixture_contract_outputs,
        checks_tsv=checks_tsv,
        check_rows=check_rows,
        expected_diff_summary=expected_diff_summary,
        cell_counts=cell_counts,
    )
    return {
        "summary_json": summary_json,
        "checks_tsv": checks_tsv,
        "cell_provenance_summary_json": fixture_contract_outputs[
            "cell_provenance_summary"
        ],
        "cell_provenance_minimal_fixture": fixture_contract_outputs[
            "cell_provenance_minimal_fixture"
        ],
        **activation_outputs,
    }


def validate_quant_matrix_default_product_activation(
    *,
    summary_json: Path = DEFAULT_PRODUCT_ACTIVATION_OUTPUT_DIR
    / "quant_matrix_default_product_activation_summary.json",
    source_root: Path = ROOT,
    expected_source_run_id: str = DEFAULT_SOURCE_RUN_ID,
    expected_downstream_scope: str = DEFAULT_DOWNSTREAM_SCOPE,
    expected_accepted_backfill_count: int = DEFAULT_ACCEPTED_BACKFILL_COUNT,
) -> list[str]:
    problems: list[str] = []
    try:
        payload = _read_json_object(summary_json)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]

    _append_expected_field_problems(payload, problems)
    if payload.get("source_run_id") != expected_source_run_id:
        problems.append(
            "default product activation source_run_id mismatch: "
            f"expected {expected_source_run_id}",
        )
    if payload.get("downstream_scope") != expected_downstream_scope:
        problems.append(
            "default product activation downstream_scope mismatch: "
            f"expected {expected_downstream_scope}",
        )
    if (
        optional_int(payload.get("accepted_backfill_count", ""))
        != expected_accepted_backfill_count
    ):
        problems.append(
            "default product activation accepted_backfill_count mismatch: "
            f"expected {expected_accepted_backfill_count}",
        )
    for payload_field in ("expected_diff_count", "written_backfill_count"):
        if optional_int(payload.get(payload_field, "")) != (
            expected_accepted_backfill_count
        ):
            problems.append(
                f"default product activation {payload_field} mismatch: "
                f"expected {expected_accepted_backfill_count}",
            )
    if optional_int(payload.get("unused_expected_diff_count", "")) != 0:
        problems.append(
            "default product activation unused_expected_diff_count must be 0",
        )

    inputs = _input_artifacts(payload, source_root=source_root, problems=problems)
    artifacts = _output_artifacts(payload, summary_json.parent, problems)
    closeout_summary = inputs.get("product_ready_closeout_summary_json")

    bundle_payload: Mapping[str, Any] | None = None
    bundle_paths: dict[str, Path] | None = None
    if closeout_summary is not None:
        closeout_problems = validate_quant_matrix_product_ready_closeout(
            summary_json=closeout_summary,
            source_root=source_root,
            expected_source_run_id=expected_source_run_id,
            expected_downstream_scope=expected_downstream_scope,
            expected_accepted_backfill_count=expected_accepted_backfill_count,
        )
        problems.extend(
            "product ready closeout: " + problem for problem in closeout_problems
        )
        if not closeout_problems:
            try:
                closeout_payload = _read_json_object(closeout_summary)
                real_bundle_summary = (
                    resolve_quant_matrix_product_ready_closeout_real_bundle(
                        closeout_summary,
                        source_root=source_root,
                    )
                )
                bundle_payload = _read_json_object(real_bundle_summary)
                bundle_paths = _real_bundle_paths(
                    bundle_payload,
                    real_bundle_summary,
                )
                problems.extend(
                    _source_identity_problems(
                        bundle_payload,
                        closeout_payload,
                        expected_source_run_id=expected_source_run_id,
                        expected_downstream_scope=expected_downstream_scope,
                        expected_accepted_backfill_count=(
                            expected_accepted_backfill_count
                        ),
                    )
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                problems.append(f"product ready closeout chain: {exc}")

    if bundle_paths is not None:
        missing_outputs = sorted(
            label for label in DEFAULT_OUTPUT_LABELS if label not in artifacts
        )
        if missing_outputs:
            problems.append(
                "default product activation missing output artifacts: "
                + ", ".join(missing_outputs),
            )
        if all(label in artifacts for label in DEFAULT_OUTPUT_LABELS):
            problems.extend(
                _rerun_output_problems(
                    artifacts=artifacts,
                    bundle_paths=bundle_paths,
                    source_root=source_root,
                )
            )
            problems.extend(
                _reference_output_problems(
                    activation_outputs=artifacts,
                    reference_paths=bundle_paths,
                )
            )
            problems.extend(
                _source_summary_input_hash_problems(
                    artifacts["source_summary"],
                    expected_inputs=bundle_paths,
                )
            )
            problems.extend(
                _fixture_contract_problems(
                    summary_json=artifacts["cell_provenance_summary"],
                    fixture_tsv=artifacts["cell_provenance_minimal_fixture"],
                    expected_accepted_backfill_count=(expected_accepted_backfill_count),
                )
            )
            expected_diff_summary = _expected_diff_summary_status(
                artifacts["expected_diff_summary"],
            )
            problems.extend(
                _expected_diff_payload_problems(
                    expected_diff_summary,
                    expected_accepted_backfill_count=(expected_accepted_backfill_count),
                )
            )
            _append_summary_payload_stale_problems(
                payload,
                expected_diff_summary=expected_diff_summary,
                cell_counts=_cell_counts_from_contract(
                    artifacts["cell_provenance_summary"],
                ),
                problems=problems,
            )
            expected_checks = _check_rows(
                activation_outputs=artifacts,
                reference_paths=bundle_paths,
                expected_diff_summary=expected_diff_summary,
            )
            checks_tsv = artifacts.get("checks_tsv")
            if checks_tsv is not None:
                try:
                    actual_checks = read_tsv_required(checks_tsv, CHECK_COLUMNS)
                except (OSError, ValueError) as exc:
                    problems.append(f"default product activation checks TSV: {exc}")
                    actual_checks = ()
                normalized_actual = [
                    {column: row.get(column, "") for column in CHECK_COLUMNS}
                    for row in actual_checks
                ]
                if normalized_actual != expected_checks:
                    problems.append(
                        "default product activation checks TSV is stale",
                    )
            problems.extend(_failed_check_problems(expected_checks))
    return problems


def _real_bundle_paths(
    payload: Mapping[str, Any],
    summary_json: Path,
) -> dict[str, Path]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("real bundle artifacts must be an object")
    output_dir = summary_json.parent
    labels = (
        "baseline_quant_matrix",
        "input_matrix_identity",
        "production_acceptance_manifest",
        "expected_diff",
        "quant_matrix",
        "row_summary",
        "expected_diff_summary",
        "source_summary",
    )
    result: dict[str, Path] = {}
    for label in labels:
        raw_entry = artifacts.get(label)
        if not isinstance(raw_entry, dict):
            raise ValueError(f"real bundle {label} artifact is missing")
        relpath = str(raw_entry.get("path", "")).strip()
        path = Path(relpath)
        if not relpath or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"real bundle {label} path must be bundle-relative")
        resolved = (output_dir / path).resolve(strict=False)
        try:
            resolved.relative_to(output_dir.resolve(strict=False))
        except ValueError as exc:
            raise ValueError(f"real bundle {label} path escapes bundle") from exc
        if not resolved.is_file():
            raise FileNotFoundError(str(resolved))
        result[label] = resolved
    return result


def _source_identity_problems(
    bundle_payload: Mapping[str, Any],
    closeout_payload: Mapping[str, Any],
    *,
    expected_source_run_id: str,
    expected_downstream_scope: str,
    expected_accepted_backfill_count: int,
) -> list[str]:
    problems: list[str] = []
    expected_pairs: tuple[tuple[str, object], ...] = (
        ("source_run_id", expected_source_run_id),
        ("downstream_scope", expected_downstream_scope),
        ("accepted_backfill_count", expected_accepted_backfill_count),
    )
    for field, expected in expected_pairs:
        if bundle_payload.get(field) != expected:
            problems.append(f"real bundle {field} mismatch")
        if closeout_payload.get(field) != expected:
            problems.append(f"product ready closeout {field} mismatch")
        if bundle_payload.get(field) != closeout_payload.get(field):
            problems.append(f"real bundle and closeout {field} differ")
    return problems


def _expected_diff_summary_status(path: Path) -> dict[str, str]:
    rows = read_tsv_required(path, EXPECTED_DIFF_SUMMARY_COLUMNS)
    if len(rows) != 1:
        raise ValueError("expected_diff_summary must contain exactly one row")
    return {column: rows[0].get(column, "") for column in EXPECTED_DIFF_SUMMARY_COLUMNS}


def _expected_diff_payload_problems(
    expected_diff_summary: Mapping[str, str],
    *,
    expected_accepted_backfill_count: int,
) -> list[str]:
    problems: list[str] = []
    if expected_diff_summary.get("acceptance_status") != "pass":
        problems.append("expected_diff_summary acceptance_status must be pass")
    for field in ("expected_diff_count", "written_backfill_count"):
        if optional_int(expected_diff_summary.get(field, "")) != (
            expected_accepted_backfill_count
        ):
            problems.append(
                f"expected_diff_summary {field} mismatch: "
                f"expected {expected_accepted_backfill_count}",
            )
    if optional_int(expected_diff_summary.get("unused_expected_diff_count", "")) != 0:
        problems.append("expected_diff_summary unused_expected_diff_count must be 0")
    return problems


def _source_summary_input_hash_problems(
    source_summary_tsv: Path,
    *,
    expected_inputs: Mapping[str, Path],
) -> list[str]:
    rows = read_tsv_required(source_summary_tsv, ("schema_version",))
    if len(rows) != 1:
        return ["source_summary must contain exactly one row"]
    row = rows[0]
    expected_hashes = {
        "input_quant_matrix_sha256": file_sha256(
            expected_inputs["baseline_quant_matrix"],
        ),
        "input_matrix_identity_sha256": file_sha256(
            expected_inputs["input_matrix_identity"],
        ),
        "production_acceptance_manifest_sha256": file_sha256(
            expected_inputs["production_acceptance_manifest"],
        ),
        "expected_diff_sha256": file_sha256(expected_inputs["expected_diff"]),
    }
    problems: list[str] = []
    for field, expected in expected_hashes.items():
        if row.get(field) != expected:
            problems.append(f"source_summary {field} mismatch")
    return problems


def _reference_output_problems(
    *,
    activation_outputs: Mapping[str, Path],
    reference_paths: Mapping[str, Path],
) -> list[str]:
    problems: list[str] = []
    for label in REFERENCE_OUTPUT_LABELS:
        if file_sha256(activation_outputs[label]) != file_sha256(
            reference_paths[label],
        ):
            problems.append(
                f"{label} does not match real/candidate QuantMatrix bundle reference"
            )
    return problems


def _rerun_output_problems(
    *,
    artifacts: Mapping[str, Path],
    bundle_paths: Mapping[str, Path],
    source_root: Path,
) -> list[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        rerun_outputs = dict(
            run_activation(
                input_quant_matrix_tsv=bundle_paths["baseline_quant_matrix"],
                input_matrix_identity_tsv=bundle_paths["input_matrix_identity"],
                production_acceptance_manifest_tsv=bundle_paths[
                    "production_acceptance_manifest"
                ],
                expected_diff_tsv=bundle_paths["expected_diff"],
                output_dir=Path(tmpdir) / "activation_rerun",
                manifest_root=source_root,
            )
        )
        problems: list[str] = []
        for label in REFERENCE_OUTPUT_LABELS:
            if file_sha256(artifacts[label]) != file_sha256(rerun_outputs[label]):
                problems.append(f"{label} does not match rerun activation")
        summary_json = artifacts.get("cell_provenance_summary")
        if summary_json is None:
            problems.append("cell_provenance summary missing for rerun comparison")
        else:
            problems.extend(
                _cell_provenance_rerun_problems(
                    rerun_outputs["cell_provenance"],
                    summary_json,
                )
            )
        return problems


def _cell_provenance_rerun_problems(
    rerun_cell_provenance_tsv: Path,
    summary_json: Path,
) -> list[str]:
    try:
        payload = _read_json_object(summary_json)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"cell_provenance summary invalid for rerun comparison: {exc}"]
    source_sha = str(payload.get("source_sha256", "")).upper()
    if file_sha256(rerun_cell_provenance_tsv) != source_sha:
        return ["cell_provenance does not match rerun activation"]
    return []


def _cell_provenance_completeness_problems(
    *,
    quant_matrix_tsv: Path,
    cell_provenance_tsv: Path,
) -> list[str]:
    matrix_header, matrix_rows = read_tsv_with_header(quant_matrix_tsv)
    sample_columns = tuple(
        column for column in matrix_header if column not in {"Mz", "RT"}
    )
    non_empty_matrix_cells = sum(
        1 for row in matrix_rows for sample in sample_columns if row.get(sample, "")
    )
    provenance_rows = read_tsv_required(
        cell_provenance_tsv,
        ("peak_hypothesis_id", "sample_stem", "cell_status", "matrix_value"),
    )
    if len(provenance_rows) != non_empty_matrix_cells:
        return [
            "cell_provenance row count does not match non-empty quant matrix cells",
        ]
    allowed_statuses = {"detected", "accepted_backfill"}
    invalid_statuses = sorted(
        {
            row.get("cell_status", "")
            for row in provenance_rows
            if row.get("cell_status", "") not in allowed_statuses
        }
    )
    if invalid_statuses:
        return ["cell_provenance contains invalid cell_status values"]
    return []


def _cell_provenance_check_problems(
    activation_outputs: Mapping[str, Path],
) -> list[str]:
    cell_provenance = activation_outputs.get("cell_provenance")
    if cell_provenance is not None:
        return _cell_provenance_completeness_problems(
            quant_matrix_tsv=activation_outputs["quant_matrix"],
            cell_provenance_tsv=cell_provenance,
        )
    summary_json = activation_outputs.get("cell_provenance_summary")
    fixture_tsv = activation_outputs.get("cell_provenance_minimal_fixture")
    if summary_json is None or fixture_tsv is None:
        return ["cell_provenance contract artifacts missing"]
    return validate_fixture_contract(summary_json, fixture_tsv)


def _cell_counts(cell_provenance_tsv: Path) -> dict[str, str]:
    rows = read_tsv_required(
        cell_provenance_tsv,
        ("cell_status", "matrix_value"),
    )
    detected_count = sum(1 for row in rows if row.get("cell_status") == "detected")
    accepted_count = sum(
        1 for row in rows if row.get("cell_status") == "accepted_backfill"
    )
    return {
        "detected_cell_count": str(detected_count),
        "accepted_backfill_cell_count": str(accepted_count),
        "quant_available_cell_count": str(detected_count + accepted_count),
    }


def _cell_counts_from_contract(summary_json: Path) -> dict[str, str]:
    payload = _read_json_object(summary_json)
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        return {}
    raw_cell_counts = counts.get("cell_status")
    if not isinstance(raw_cell_counts, dict):
        return {}
    detected_count = optional_int(raw_cell_counts.get("detected", "")) or 0
    accepted_count = optional_int(raw_cell_counts.get("accepted_backfill", "")) or 0
    return {
        "detected_cell_count": str(detected_count),
        "accepted_backfill_cell_count": str(accepted_count),
        "quant_available_cell_count": str(detected_count + accepted_count),
    }


def _check_rows(
    *,
    activation_outputs: Mapping[str, Path],
    reference_paths: Mapping[str, Path],
    expected_diff_summary: Mapping[str, str],
) -> list[dict[str, str]]:
    rows = [
        _check_row(
            "product_ready_closeout_validated",
            "pass",
            "product_ready_closeout_summary_json",
            "Product Ready closeout validates before default output activation.",
        ),
        _check_row(
            "real_bundle_validated",
            "pass",
            "quant_matrix_real_bundle_summary_json",
            "Real 511-cell QuantMatrix bundle validates before activation.",
        ),
        _check_row(
            "expected_diff_closed",
            "pass"
            if expected_diff_summary.get("acceptance_status") == "pass"
            and expected_diff_summary.get("unused_expected_diff_count") == "0"
            else "fail",
            "expected_diff_summary.tsv",
            "Expected diff writes accepted Backfill values with zero unused rows.",
        ),
    ]
    for label in REFERENCE_OUTPUT_LABELS:
        rows.append(
            _check_row(
                f"{label}_matches_real_quant_matrix_bundle_reference",
                "pass"
                if file_sha256(activation_outputs[label])
                == file_sha256(reference_paths[label])
                else "fail",
                label,
                f"{label} default output matches the real/candidate QuantMatrix "
                "bundle reference hash.",
            )
        )
    rows.append(
        _check_row(
            "source_summary_input_hashes_match",
            "pass"
            if not _source_summary_input_hash_problems(
                activation_outputs["source_summary"],
                expected_inputs=reference_paths,
            )
            else "fail",
            "source_summary.tsv",
            "Source summary hashes bind the baseline matrix, identity, manifest, "
            "and expected diff.",
        )
    )
    rows.append(
        _check_row(
            "cell_provenance_completes_quant_matrix",
            "pass"
            if not _cell_provenance_check_problems(activation_outputs)
            else "fail",
            "cell_provenance_summary.json",
            "Every non-empty default quant matrix cell has provenance or a "
            "tracked provenance contract.",
        )
    )
    return rows


def _check_row(
    check_id: str,
    status: str,
    source: str,
    meaning: str,
) -> dict[str, str]:
    return {
        "schema_version": OUTPUT_COMPARISON_SCHEMA,
        "check_id": check_id,
        "status": status,
        "source": source,
        "meaning": meaning,
    }


def _failed_check_problems(rows: Sequence[Mapping[str, str]]) -> list[str]:
    problems: list[str] = []
    for row in rows:
        if row.get("status") == "pass":
            continue
        check_id = row.get("check_id", "")
        if check_id.endswith("_matches_real_quant_matrix_bundle_reference"):
            label = check_id.removesuffix("_matches_real_quant_matrix_bundle_reference")
            problems.append(
                f"{label} does not match real/candidate QuantMatrix bundle reference"
            )
        else:
            problems.append(f"default product activation check failed: {check_id}")
    return problems


def _write_summary(
    summary_json: Path,
    *,
    output_dir: Path,
    source_root: Path,
    closeout_summary: Path,
    bundle_payload: Mapping[str, Any],
    closeout_payload: Mapping[str, Any],
    bundle_paths: Mapping[str, Path],
    activation_outputs: Mapping[str, Path],
    fixture_contract_outputs: Mapping[str, Path],
    checks_tsv: Path,
    check_rows: Sequence[Mapping[str, str]],
    expected_diff_summary: Mapping[str, str],
    cell_counts: Mapping[str, str],
) -> None:
    payload = {
        "schema_version": DEFAULT_PRODUCT_ACTIVATION_SCHEMA,
        "artifact_role": "default_product_activation",
        "status": "pass",
        "activation_label": "product_ready_default_matrix_activated",
        "source_run_id": bundle_payload.get("source_run_id", ""),
        "downstream_scope": bundle_payload.get("downstream_scope", ""),
        "accepted_backfill_count": bundle_payload.get("accepted_backfill_count", 0),
        "product_ready_closeout_label": closeout_payload.get("closeout_label", ""),
        "expected_diff_count": expected_diff_summary.get("expected_diff_count", ""),
        "written_backfill_count": expected_diff_summary.get(
            "written_backfill_count",
            "",
        ),
        "unused_expected_diff_count": expected_diff_summary.get(
            "unused_expected_diff_count",
            "",
        ),
        **cell_counts,
        "all_reference_outputs_match": not _reference_output_problems(
            activation_outputs=activation_outputs,
            reference_paths=bundle_paths,
        ),
        "check_count": len(check_rows),
        "read_only": False,
        "write_authority": True,
        "product_authority_scope": PRODUCT_AUTHORITY_SCOPE,
        "scorer_ran": False,
        "raw_or_full_cohort_ran": False,
        "product_writer_changed": True,
        "default_quant_matrix_changed": True,
        "default_matrix_files_written": True,
        "workbook_or_gui_changed": False,
        "selected_peak_area_or_counting_changed": False,
        "broad_backfill_unparked": False,
        "accepted_backfill_values_are_detection": False,
        "input_artifacts": {
            "product_ready_closeout_summary_json": _source_relpath(
                closeout_summary,
                source_root=source_root,
            ),
            "product_ready_closeout_summary_json_sha256": file_sha256(
                closeout_summary,
            ),
        },
        "artifacts": {
            "checks_tsv": _artifact_record(checks_tsv, base_dir=output_dir),
            **{
                label: _artifact_record(path, base_dir=output_dir)
                for label, path in activation_outputs.items()
                if label != "cell_provenance"
            },
            "cell_provenance": _externalized_fixture_source_entry(
                activation_outputs["cell_provenance"],
                summary_path=fixture_contract_outputs["cell_provenance_summary"],
                output_dir=output_dir,
                source_root=source_root,
            ),
            "cell_provenance_summary": _artifact_record(
                fixture_contract_outputs["cell_provenance_summary"],
                base_dir=output_dir,
            )
            | {"retention_decision": "keep_summary"},
            "cell_provenance_minimal_fixture": _artifact_record(
                fixture_contract_outputs["cell_provenance_minimal_fixture"],
                base_dir=output_dir,
            )
            | {"retention_decision": "keep_minimal_fixture"},
        },
        "authority_statement": (
            "The default quant matrix is explicitly activated from the current "
            "511-cell ProductionAcceptanceManifest and expected-diff contract. "
            "Accepted Backfill values are quantification values in the default "
            "matrix, not detections or truth claims; detected-only claims remain "
            "reconstructable from cell_provenance."
        ),
    }
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _append_expected_field_problems(
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    for field, expected_text in (
        ("schema_version", DEFAULT_PRODUCT_ACTIVATION_SCHEMA),
        ("artifact_role", "default_product_activation"),
        ("status", "pass"),
        ("activation_label", "product_ready_default_matrix_activated"),
        ("product_authority_scope", PRODUCT_AUTHORITY_SCOPE),
        ("unused_expected_diff_count", "0"),
    ):
        if payload.get(field) != expected_text:
            problems.append(f"default product activation {field} mismatch")
    for field, expected_bool in (
        ("all_reference_outputs_match", True),
        ("read_only", False),
        ("write_authority", True),
        ("scorer_ran", False),
        ("raw_or_full_cohort_ran", False),
        ("product_writer_changed", True),
        ("default_quant_matrix_changed", True),
        ("default_matrix_files_written", True),
        ("workbook_or_gui_changed", False),
        ("selected_peak_area_or_counting_changed", False),
        ("broad_backfill_unparked", False),
        ("accepted_backfill_values_are_detection", False),
    ):
        if payload.get(field) is not expected_bool:
            problems.append(
                f"default product activation {field} must be "
                f"{str(expected_bool).lower()}",
            )


def _append_summary_payload_stale_problems(
    payload: Mapping[str, Any],
    *,
    expected_diff_summary: Mapping[str, str],
    cell_counts: Mapping[str, str],
    problems: list[str],
) -> None:
    for summary_field, payload_field in (
        ("expected_diff_count", "expected_diff_count"),
        ("written_backfill_count", "written_backfill_count"),
        ("unused_expected_diff_count", "unused_expected_diff_count"),
    ):
        if payload.get(payload_field) != expected_diff_summary.get(summary_field, ""):
            problems.append(f"default product activation {payload_field} is stale")
    for field, value in cell_counts.items():
        if payload.get(field) != value:
            problems.append(f"default product activation {field} is stale")


def _input_artifacts(
    payload: Mapping[str, Any],
    *,
    source_root: Path,
    problems: list[str],
) -> dict[str, Path]:
    raw = payload.get("input_artifacts")
    if not isinstance(raw, dict):
        problems.append("default product activation input_artifacts must be an object")
        return {}
    result: dict[str, Path] = {}
    for field in ("product_ready_closeout_summary_json",):
        path_value = str(raw.get(field, "")).strip()
        sha_value = str(raw.get(f"{field}_sha256", "")).strip()
        if not path_value:
            problems.append(f"default product activation {field} is missing")
            continue
        path = _resolve_source(Path(path_value), source_root=source_root)
        result[field] = path
        if not path.is_file():
            problems.append(f"default product activation {field} does not exist")
            continue
        if file_sha256(path) != sha_value.upper():
            problems.append(f"default product activation {field}_sha256 mismatch")
    return result


def _output_artifacts(
    payload: Mapping[str, Any],
    output_dir: Path,
    problems: list[str],
) -> dict[str, Path]:
    raw = payload.get("artifacts")
    if not isinstance(raw, dict):
        problems.append("default product activation artifacts must be an object")
        return {}
    result: dict[str, Path] = {}
    for label, raw_entry in raw.items():
        if not isinstance(raw_entry, dict):
            problems.append(f"default product activation {label} entry invalid")
            continue
        relpath = str(raw_entry.get("path", "")).strip()
        sha256 = str(raw_entry.get("sha256", "")).strip()
        if raw_entry.get("externalized") is True:
            _append_externalized_fixture_artifact_problems(
                str(label),
                raw_entry,
                output_dir=output_dir,
                problems=problems,
            )
            continue
        path = Path(relpath)
        if not relpath or path.is_absolute() or ".." in path.parts:
            problems.append(f"default product activation {label} path invalid")
            continue
        resolved = (output_dir / path).resolve(strict=False)
        try:
            resolved.relative_to(output_dir.resolve(strict=False))
        except ValueError:
            problems.append(f"default product activation {label} path escapes output")
            continue
        result[str(label)] = resolved
        if not resolved.is_file():
            problems.append(f"default product activation {label} does not exist")
            continue
        if file_sha256(resolved) != sha256.upper():
            problems.append(f"{label} artifact sha256 mismatch")
    return result


def _artifact_record(path: Path, *, base_dir: Path) -> dict[str, str]:
    return {
        "path": _relpath(path, base_dir),
        "sha256": file_sha256(path),
    }


def _write_fixture_contract_outputs(
    *,
    activation_outputs: Mapping[str, Path],
    default_output_dir: Path,
) -> dict[str, Path]:
    summary_json = default_output_dir / "cell_provenance_summary.json"
    fixture_tsv = default_output_dir / "cell_provenance_minimal_fixture.tsv"
    write_cell_provenance_contract(
        activation_outputs["cell_provenance"],
        summary_json,
        fixture_tsv,
        source_relpath="default_output/cell_provenance.tsv",
    )
    return {
        "cell_provenance_summary": summary_json,
        "cell_provenance_minimal_fixture": fixture_tsv,
    }


def _externalized_fixture_source_entry(
    path: Path,
    *,
    summary_path: Path,
    output_dir: Path,
    source_root: Path,
) -> dict[str, object]:
    sha256 = file_sha256(path)
    externalized_path = _maybe_externalize_generated_artifact(
        path,
        output_dir=output_dir,
    )
    return {
        "path": _relpath(path, output_dir),
        "sha256": sha256,
        "externalized": True,
        "externalized_path": _source_relpath(
            externalized_path,
            source_root=source_root,
        ),
        "replacement_or_summary": _relpath(summary_path, output_dir),
        "retention_decision": "externalize",
    }


def _externalized_validation_artifact_path(path: Path, *, output_dir: Path) -> Path:
    relpath = path.relative_to(output_dir)
    return (
        ROOT / "local_validation_artifacts/externalized_superpowers_validation/"
        "quant_matrix_default_product_activation" / relpath
    )


def _maybe_externalize_generated_artifact(path: Path, *, output_dir: Path) -> Path:
    externalized_path = _externalized_validation_artifact_path(
        path,
        output_dir=output_dir,
    )
    if output_dir.resolve(strict=False) != (
        ROOT / DEFAULT_PRODUCT_ACTIVATION_OUTPUT_DIR
    ).resolve(strict=False):
        return externalized_path
    externalized_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, externalized_path)
    path.unlink()
    return externalized_path


def _append_externalized_fixture_artifact_problems(
    label: str,
    raw_entry: Mapping[str, Any],
    *,
    output_dir: Path,
    problems: list[str],
) -> None:
    summary_path = output_dir / str(raw_entry.get("replacement_or_summary", ""))
    if not summary_path.is_file():
        problems.append(f"{label}: replacement summary missing")
        return
    try:
        payload = _read_json_object(summary_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        problems.append(f"{label}: replacement summary invalid: {exc}")
        return
    if payload.get("schema_version") != "quant_matrix_fixture_contract":
        problems.append(f"{label}: replacement summary schema mismatch")
    if payload.get("source_relpath") != raw_entry.get("path"):
        problems.append(f"{label}: replacement summary source_relpath mismatch")
    if payload.get("source_sha256") != str(raw_entry.get("sha256", "")).upper():
        problems.append(f"{label}: replacement summary source_sha256 mismatch")
    fixture = payload.get("minimal_fixture")
    if isinstance(fixture, dict):
        fixture_tsv = summary_path.parent / str(fixture.get("path", ""))
        problems.extend(
            f"{label}: {problem}"
            for problem in validate_fixture_contract(summary_path, fixture_tsv)
        )
    else:
        problems.append(f"{label}: replacement summary missing minimal_fixture")


def _fixture_contract_problems(
    *,
    summary_json: Path,
    fixture_tsv: Path,
    expected_accepted_backfill_count: int,
) -> list[str]:
    problems = [
        f"cell_provenance summary: {problem}"
        for problem in validate_fixture_contract(summary_json, fixture_tsv)
    ]
    if problems:
        return problems
    payload = _read_json_object(summary_json)
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        return ["cell_provenance summary: counts missing"]
    cell_counts = counts.get("cell_status")
    if not isinstance(cell_counts, dict):
        return ["cell_provenance summary: cell_status counts missing"]
    accepted = optional_int(cell_counts.get("accepted_backfill", ""))
    if accepted != expected_accepted_backfill_count:
        return ["cell_provenance summary: accepted_backfill count mismatch"]
    return []


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


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def _resolve_source(path: Path, *, source_root: Path) -> Path:
    return path if path.is_absolute() else source_root / path


def _source_relpath(path: Path, *, source_root: Path) -> str:
    try:
        return (
            path.resolve(strict=False)
            .relative_to(
                source_root.resolve(strict=False),
            )
            .as_posix()
        )
    except ValueError:
        return str(path)


def _relpath(path: Path, base_dir: Path) -> str:
    try:
        return (
            path.resolve(strict=False)
            .relative_to(
                base_dir.resolve(strict=False),
            )
            .as_posix()
        )
    except ValueError:
        return str(path)


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


def _raise_if_problems(label: str, problems: Sequence[str]) -> None:
    if problems:
        raise ValueError(label + ": " + "; ".join(problems))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check_only:
        summary_json = (
            args.output_dir / "quant_matrix_default_product_activation_summary.json"
        )
        problems = validate_quant_matrix_default_product_activation(
            summary_json=summary_json,
            source_root=args.source_root,
            expected_source_run_id=args.expected_source_run_id,
            expected_downstream_scope=args.expected_downstream_scope,
            expected_accepted_backfill_count=args.expected_accepted_backfill_count,
        )
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 2
        print(f"default_product_activation_summary_json: {summary_json}")
        print("default_product_activation_status: pass")
        return 0
    try:
        outputs = build_quant_matrix_default_product_activation(
            output_dir=args.output_dir,
            source_root=args.source_root,
            product_ready_closeout_summary_json=(
                args.product_ready_closeout_summary_json
            ),
            expected_source_run_id=args.expected_source_run_id,
            expected_downstream_scope=args.expected_downstream_scope,
            expected_accepted_backfill_count=args.expected_accepted_backfill_count,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_PRODUCT_ACTIVATION_OUTPUT_DIR,
    )
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument(
        "--product-ready-closeout-summary-json",
        type=Path,
        default=DEFAULT_PRODUCT_READY_CLOSEOUT_SUMMARY_JSON,
    )
    parser.add_argument(
        "--expected-source-run-id",
        default=DEFAULT_SOURCE_RUN_ID,
    )
    parser.add_argument(
        "--expected-downstream-scope",
        default=DEFAULT_DOWNSTREAM_SCOPE,
    )
    parser.add_argument(
        "--expected-accepted-backfill-count",
        type=int,
        default=DEFAULT_ACCEPTED_BACKFILL_COUNT,
    )
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
