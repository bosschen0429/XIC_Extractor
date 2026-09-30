"""Build/check the QuantMatrix default activation dry-run gate.

This default activation replay reruns the manifest-driven QuantMatrixVersion
activation in a temporary directory and compares the result with the
real/candidate QuantMatrix bundle.
It writes only a comparison sidecar and summary. It does not mutate
ProductWriter, default matrix output, workbooks, GUI behavior, selected
peaks/areas, or counted detections.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_quant_matrix_promotion_packet import (
    DEFAULT_OUTPUT_DIR as DEFAULT_PACKET_DIR,
)
from scripts.build_quant_matrix_promotion_packet import (
    resolve_quant_matrix_promotion_packet_real_bundle,
    validate_quant_matrix_promotion_packet,
)
from scripts.build_quant_matrix_real_bundle import (
    DEFAULT_ACCEPTED_BACKFILL_COUNT,
    DEFAULT_DOWNSTREAM_SCOPE,
    DEFAULT_SOURCE_RUN_ID,
)
from scripts.build_quant_matrix_version import run_activation
from xic_extractor.diagnostics.quant_matrix.fixture_contract import (
    validate_fixture_contract,
)
from xic_extractor.diagnostics.quant_matrix.version import (
    EXPECTED_DIFF_SUMMARY_COLUMNS,
)
from xic_extractor.tabular_io import (
    file_sha256,
    optional_int,
    read_tsv_required,
    write_tsv,
)

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ACTIVATION_DRY_RUN_OUTPUT_DIR = Path(
    "docs/superpowers/validation/quant_matrix_default_activation_dry_run",
)
DEFAULT_PROMOTION_PACKET_SUMMARY_JSON = (
    DEFAULT_PACKET_DIR / "quant_matrix_promotion_packet_summary.json"
)

DEFAULT_ACTIVATION_DRY_RUN_SCHEMA = "quant_matrix_default_activation_dry_run"
ACTIVATION_DRY_RUN_COMPARISON_SCHEMA = (
    "quant_matrix_default_activation_dry_run_comparison"
)

COMPARISON_COLUMNS = (
    "schema_version",
    "artifact_label",
    "reference_artifact_path",
    "reference_sha256",
    "dry_run_sha256",
    "sha256_match",
    "meaning",
)

REFERENCE_OUTPUT_LABELS = (
    "quant_matrix",
    "cell_provenance",
    "row_summary",
    "expected_diff_summary",
)


def build_quant_matrix_default_activation_dry_run(
    *,
    output_dir: Path = DEFAULT_ACTIVATION_DRY_RUN_OUTPUT_DIR,
    source_root: Path = ROOT,
    promotion_packet_summary_json: Path = DEFAULT_PROMOTION_PACKET_SUMMARY_JSON,
    expected_source_run_id: str = DEFAULT_SOURCE_RUN_ID,
    expected_downstream_scope: str = DEFAULT_DOWNSTREAM_SCOPE,
    expected_accepted_backfill_count: int = DEFAULT_ACCEPTED_BACKFILL_COUNT,
) -> Mapping[str, Path]:
    promotion_packet_summary = _resolve_source(
        promotion_packet_summary_json,
        source_root=source_root,
    )
    _raise_if_problems(
        "promotion packet check failed",
        validate_quant_matrix_promotion_packet(
            summary_json=promotion_packet_summary,
            source_root=source_root,
            expected_source_run_id=expected_source_run_id,
            expected_downstream_scope=expected_downstream_scope,
            expected_accepted_backfill_count=expected_accepted_backfill_count,
        ),
    )
    packet_payload = _read_json_object(promotion_packet_summary)
    real_bundle_summary = resolve_quant_matrix_promotion_packet_real_bundle(
        promotion_packet_summary,
        source_root=source_root,
    )
    bundle_payload = _read_json_object(real_bundle_summary)
    bundle_paths = _real_bundle_paths(bundle_payload, real_bundle_summary)
    dry_run = _run_activation_dry_run(
        bundle_paths=bundle_paths,
        source_root=source_root,
    )
    comparison_rows = _comparison_rows(
        bundle_paths=bundle_paths,
        activation_outputs=dry_run.outputs,
        source_root=source_root,
    )
    comparison_problems = _comparison_problems(comparison_rows)
    comparison_problems.extend(
        _source_summary_input_hash_problems(
            dry_run.outputs["source_summary"],
            expected_inputs=bundle_paths,
        )
    )
    expected_diff_summary = _expected_diff_summary_status(
        dry_run.outputs["expected_diff_summary"],
    )
    if comparison_problems:
        raise ValueError(
            "default activation dry-run comparison failed: "
            + "; ".join(comparison_problems),
        )
    if expected_diff_summary["acceptance_status"] != "pass":
        raise ValueError("dry-run expected_diff_summary did not pass")

    output_dir.mkdir(parents=True, exist_ok=True)
    comparison_tsv = output_dir / "default_activation_dry_run_comparison.tsv"
    write_tsv(
        comparison_tsv,
        comparison_rows,
        COMPARISON_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    summary_json = output_dir / "quant_matrix_default_activation_dry_run_summary.json"
    _write_summary(
        summary_json,
        output_dir=output_dir,
        source_root=source_root,
        promotion_packet_summary=promotion_packet_summary,
        bundle_payload=bundle_payload,
        packet_payload=packet_payload,
        comparison_tsv=comparison_tsv,
        comparison_rows=comparison_rows,
        expected_diff_summary=expected_diff_summary,
    )
    return {
        "summary_json": summary_json,
        "comparison_tsv": comparison_tsv,
    }


def validate_quant_matrix_default_activation_dry_run(
    *,
    summary_json: Path = DEFAULT_ACTIVATION_DRY_RUN_OUTPUT_DIR
    / "quant_matrix_default_activation_dry_run_summary.json",
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
    if (
        optional_int(payload.get("accepted_backfill_count", ""))
        != expected_accepted_backfill_count
    ):
        problems.append(
            "default activation dry-run accepted_backfill_count mismatch: "
            f"expected {expected_accepted_backfill_count}",
        )
    if payload.get("downstream_scope") != expected_downstream_scope:
        problems.append(
            "default activation dry-run downstream_scope mismatch: "
            f"expected {expected_downstream_scope}",
        )
    if (
        optional_int(payload.get("dry_run_expected_diff_count", ""))
        != expected_accepted_backfill_count
    ):
        problems.append(
            "default activation dry-run expected_diff_count mismatch: "
            f"expected {expected_accepted_backfill_count}",
        )
    if (
        optional_int(payload.get("dry_run_written_backfill_count", ""))
        != expected_accepted_backfill_count
    ):
        problems.append(
            "default activation dry-run written_backfill_count mismatch: "
            f"expected {expected_accepted_backfill_count}",
        )

    inputs = _input_artifacts(payload, source_root=source_root, problems=problems)
    artifacts = _output_artifacts(payload, summary_json.parent, problems)
    promotion_packet_summary = inputs.get("promotion_packet_summary_json")
    comparison_tsv = artifacts.get("comparison_tsv")

    real_bundle_summary: Path | None = None
    if promotion_packet_summary is not None:
        packet_problems = validate_quant_matrix_promotion_packet(
            summary_json=promotion_packet_summary,
            source_root=source_root,
            expected_source_run_id=expected_source_run_id,
            expected_downstream_scope=expected_downstream_scope,
            expected_accepted_backfill_count=expected_accepted_backfill_count,
        )
        problems.extend("promotion packet: " + problem for problem in packet_problems)
        if not packet_problems:
            try:
                real_bundle_summary = resolve_quant_matrix_promotion_packet_real_bundle(
                    promotion_packet_summary,
                    source_root=source_root,
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                problems.append(f"promotion packet real bundle input: {exc}")
    if real_bundle_summary is not None and comparison_tsv is not None:
        bundle_payload = _read_json_object(real_bundle_summary)
        bundle_paths = _real_bundle_paths(bundle_payload, real_bundle_summary)
        try:
            actual_rows = read_tsv_required(comparison_tsv, COMPARISON_COLUMNS)
        except (OSError, ValueError) as exc:
            problems.append(f"default activation comparison TSV: {exc}")
            actual_rows = ()
        normalized_actual = [
            {column: row.get(column, "") for column in COMPARISON_COLUMNS}
            for row in actual_rows
        ]
        dry_run = _run_activation_dry_run(
            bundle_paths=bundle_paths,
            source_root=source_root,
        )
        expected_rows = _comparison_rows(
            bundle_paths=bundle_paths,
            activation_outputs=dry_run.outputs,
            source_root=source_root,
            problems=problems,
        )
        normalized_expected = [
            {column: row.get(column, "") for column in COMPARISON_COLUMNS}
            for row in expected_rows
        ]
        if normalized_actual != normalized_expected:
            problems.append("default activation dry-run comparison TSV is stale")
        problems.extend(_comparison_problems(normalized_expected))
        problems.extend(
            _source_summary_input_hash_problems(
                dry_run.outputs["source_summary"],
                expected_inputs=bundle_paths,
            )
        )
        expected_diff_summary = _expected_diff_summary_status(
            dry_run.outputs["expected_diff_summary"],
        )
        _append_expected_diff_summary_payload_problems(
            payload,
            expected_diff_summary,
            problems,
        )
    return problems


def resolve_quant_matrix_default_activation_dry_run_promotion_packet(
    summary_json: Path,
    *,
    source_root: Path = ROOT,
) -> Path:
    """Resolve the validated dry-run's direct promotion-packet predecessor."""
    payload = _read_json_object(summary_json)
    return _predecessor_input_path(
        payload,
        field="promotion_packet_summary_json",
        source_root=source_root,
        owner="default activation dry-run",
    )


def resolve_quant_matrix_default_activation_dry_run_real_bundle(
    summary_json: Path,
    *,
    source_root: Path = ROOT,
) -> Path:
    """Resolve the real bundle through the dry-run's validated predecessor."""
    promotion_packet_summary = (
        resolve_quant_matrix_default_activation_dry_run_promotion_packet(
            summary_json,
            source_root=source_root,
        )
    )
    return resolve_quant_matrix_promotion_packet_real_bundle(
        promotion_packet_summary,
        source_root=source_root,
    )


class ActivationDryRun:
    def __init__(self, outputs: Mapping[str, Path]) -> None:
        self.outputs = outputs


def _run_activation_dry_run(
    *,
    bundle_paths: Mapping[str, Path],
    source_root: Path,
) -> ActivationDryRun:
    tmpdir = tempfile.TemporaryDirectory()
    # Keep the temporary directory alive until all callers finish reading outputs.
    output_dir = Path(tmpdir.name) / "dry_run_quant_matrix_version"
    outputs = dict(
        run_activation(
            input_quant_matrix_tsv=bundle_paths["baseline_quant_matrix"],
            input_matrix_identity_tsv=bundle_paths["input_matrix_identity"],
            production_acceptance_manifest_tsv=bundle_paths[
                "production_acceptance_manifest"
            ],
            expected_diff_tsv=bundle_paths["expected_diff"],
            output_dir=output_dir,
            manifest_root=source_root,
        )
    )
    dry_run = ActivationDryRun(outputs)
    dry_run._tmpdir = tmpdir  # type: ignore[attr-defined]
    return dry_run


def _comparison_rows(
    *,
    bundle_paths: Mapping[str, Path],
    activation_outputs: Mapping[str, Path],
    source_root: Path,
    problems: list[str] | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    binding_problems = problems if problems is not None else []
    meanings = {
        "quant_matrix": "default numeric matrix candidate is deterministic",
        "cell_provenance": "detected/backfilled provenance sidecar is deterministic",
        "row_summary": "detected/backfilled/available counts are deterministic",
        "expected_diff_summary": "expected-diff closure is deterministic",
    }
    for label in REFERENCE_OUTPUT_LABELS:
        reference_path, reference_sha = _comparison_reference_binding(
            label,
            bundle_paths=bundle_paths,
            source_root=source_root,
            problems=binding_problems,
        )
        dry_run_sha = file_sha256(activation_outputs[label])
        rows.append(
            {
                "schema_version": ACTIVATION_DRY_RUN_COMPARISON_SCHEMA,
                "artifact_label": label,
                "reference_artifact_path": reference_path,
                "reference_sha256": reference_sha,
                "dry_run_sha256": dry_run_sha,
                "sha256_match": "TRUE" if reference_sha == dry_run_sha else "FALSE",
                "meaning": meanings[label],
            }
        )
    return rows


def _comparison_problems(rows: Sequence[Mapping[str, str]]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for row in rows:
        label = row.get("artifact_label", "")
        seen.add(label)
        if row.get("schema_version") != ACTIVATION_DRY_RUN_COMPARISON_SCHEMA:
            problems.append(f"{label}: comparison schema_version mismatch")
        if row.get("sha256_match") != "TRUE":
            problems.append(f"{label}: dry-run sha256 does not match reference")
    missing = sorted(set(REFERENCE_OUTPUT_LABELS) - seen)
    if missing:
        problems.append("missing comparison rows: " + ", ".join(missing))
    return problems


def _comparison_reference_binding_problems(
    rows: Sequence[Mapping[str, str]],
    *,
    bundle_paths: Mapping[str, Path],
    source_root: Path,
) -> list[str]:
    problems: list[str] = []
    by_label = {row.get("artifact_label", ""): row for row in rows}
    for label in REFERENCE_OUTPUT_LABELS:
        row = by_label.get(label)
        if row is None:
            continue
        expected_path, expected_sha = _comparison_reference_binding(
            label,
            bundle_paths=bundle_paths,
            source_root=source_root,
            problems=problems,
        )
        if expected_path and row.get("reference_artifact_path") != expected_path:
            problems.append(f"{label}: comparison reference_artifact_path is stale")
        if expected_sha:
            for field in ("reference_sha256", "dry_run_sha256"):
                if row.get(field) != expected_sha:
                    problems.append(f"{label}: comparison {field} is stale")
    return problems


def _comparison_reference_binding(
    label: str,
    *,
    bundle_paths: Mapping[str, Path],
    source_root: Path,
    problems: list[str],
) -> tuple[str, str]:
    if label != "cell_provenance" or label in bundle_paths:
        path = bundle_paths[label]
        return (
            _source_relpath_existing(path, source_root=source_root),
            file_sha256(path),
        )

    summary = bundle_paths.get("cell_provenance_summary")
    fixture = bundle_paths.get("cell_provenance_minimal_fixture")
    reference = bundle_paths.get("cell_provenance_reference")
    if summary is None:
        problems.append("cell_provenance: externalized summary is missing")
        return "", ""
    if fixture is None:
        problems.append("cell_provenance: externalized minimal fixture is missing")
        return "", ""
    problems.extend(
        f"cell_provenance: {problem}"
        for problem in validate_fixture_contract(summary, fixture)
    )
    try:
        payload = _read_json_object(summary)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        problems.append(f"cell_provenance: {exc}")
        return "", ""
    source_sha = str(payload.get("source_sha256", "")).upper()
    if not _is_sha256(source_sha):
        problems.append("cell_provenance: fixture contract source_sha256 invalid")
        source_sha = ""
    reference_path = (
        _source_relpath_reference(reference, source_root=source_root)
        if reference is not None
        else ""
    )
    return reference_path, source_sha


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


def _expected_diff_summary_status(path: Path) -> dict[str, str]:
    rows = read_tsv_required(path, EXPECTED_DIFF_SUMMARY_COLUMNS)
    if len(rows) != 1:
        raise ValueError("dry-run expected_diff_summary must contain one row")
    return {column: rows[0].get(column, "") for column in EXPECTED_DIFF_SUMMARY_COLUMNS}


def _write_summary(
    summary_json: Path,
    *,
    output_dir: Path,
    source_root: Path,
    promotion_packet_summary: Path,
    bundle_payload: Mapping[str, Any],
    packet_payload: Mapping[str, Any],
    comparison_tsv: Path,
    comparison_rows: Sequence[Mapping[str, str]],
    expected_diff_summary: Mapping[str, str],
) -> None:
    payload = {
        "schema_version": DEFAULT_ACTIVATION_DRY_RUN_SCHEMA,
        "artifact_role": "default_activation_replay",
        "status": "pass",
        "default_activation_dry_run_gate_status": "pass",
        "source_run_id": bundle_payload.get("source_run_id", ""),
        "downstream_scope": bundle_payload.get("downstream_scope", ""),
        "accepted_backfill_count": bundle_payload.get("accepted_backfill_count", 0),
        "promotion_packet_readiness_label": packet_payload.get("readiness_label", ""),
        "dry_run_expected_diff_count": expected_diff_summary.get(
            "expected_diff_count",
            "",
        ),
        "dry_run_written_backfill_count": expected_diff_summary.get(
            "written_backfill_count",
            "",
        ),
        "dry_run_unused_expected_diff_count": expected_diff_summary.get(
            "unused_expected_diff_count",
            "",
        ),
        "dry_run_expected_diff_acceptance_status": expected_diff_summary.get(
            "acceptance_status",
            "",
        ),
        "comparison_row_count": len(comparison_rows),
        "all_reference_outputs_match": all(
            row.get("sha256_match") == "TRUE" for row in comparison_rows
        ),
        "dry_run_only": True,
        "read_only": True,
        "write_authority": False,
        "scorer_ran": False,
        "raw_or_full_cohort_ran": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "default_matrix_files_written": False,
        "workbook_or_gui_changed": False,
        "selected_peak_area_or_counting_changed": False,
        "broad_backfill_unparked": False,
        "may_enter_product_ready_closeout": True,
        "input_artifacts": {
            "promotion_packet_summary_json": _source_relpath(
                promotion_packet_summary,
                source_root=source_root,
            ),
            "promotion_packet_summary_json_sha256": file_sha256(
                promotion_packet_summary,
            ),
        },
        "artifacts": {
            "comparison_tsv": _artifact_record(comparison_tsv, base_dir=output_dir),
        },
        "authority_statement": (
            "Default activation replay gate only. It proves the "
            "manifest-driven default matrix candidate replays to the same "
            "QuantMatrixVersion outputs and expected-diff closure, but it does "
            "not write default ProductWriter outputs or change matrix authority."
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
        ("schema_version", DEFAULT_ACTIVATION_DRY_RUN_SCHEMA),
        ("artifact_role", "default_activation_replay"),
        ("status", "pass"),
        ("default_activation_dry_run_gate_status", "pass"),
        ("promotion_packet_readiness_label", "production_ready_candidate_packet"),
        ("dry_run_expected_diff_acceptance_status", "pass"),
        ("dry_run_unused_expected_diff_count", "0"),
    ):
        if payload.get(field) != expected_text:
            problems.append(f"default activation dry-run {field} mismatch")
    for field, expected_bool in (
        ("all_reference_outputs_match", True),
        ("dry_run_only", True),
        ("read_only", True),
        ("write_authority", False),
        ("scorer_ran", False),
        ("raw_or_full_cohort_ran", False),
        ("product_writer_changed", False),
        ("default_quant_matrix_changed", False),
        ("default_matrix_files_written", False),
        ("workbook_or_gui_changed", False),
        ("selected_peak_area_or_counting_changed", False),
        ("broad_backfill_unparked", False),
        ("may_enter_product_ready_closeout", True),
    ):
        if payload.get(field) is not expected_bool:
            problems.append(
                f"default activation dry-run {field} must be "
                f"{str(expected_bool).lower()}",
            )


def _append_expected_diff_summary_payload_problems(
    payload: Mapping[str, Any],
    expected_diff_summary: Mapping[str, str],
    problems: list[str],
) -> None:
    for summary_field, payload_field in (
        ("expected_diff_count", "dry_run_expected_diff_count"),
        ("written_backfill_count", "dry_run_written_backfill_count"),
        ("unused_expected_diff_count", "dry_run_unused_expected_diff_count"),
        ("acceptance_status", "dry_run_expected_diff_acceptance_status"),
    ):
        if payload.get(payload_field) != expected_diff_summary.get(summary_field, ""):
            problems.append(f"default activation dry-run {payload_field} is stale")


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
    _append_real_bundle_cell_provenance_paths(
        artifacts,
        output_dir=output_dir,
        result=result,
    )
    for label in ("cell_provenance_summary", "cell_provenance_minimal_fixture"):
        raw_entry = artifacts.get(label)
        if isinstance(raw_entry, dict) and label not in result:
            result[label] = _resolve_real_bundle_artifact(
                raw_entry,
                output_dir=output_dir,
                label=label,
            )
    return result


def _append_real_bundle_cell_provenance_paths(
    artifacts: Mapping[str, Any],
    *,
    output_dir: Path,
    result: dict[str, Path],
) -> None:
    raw_entry = artifacts.get("cell_provenance")
    if not isinstance(raw_entry, dict):
        raise ValueError("real bundle cell_provenance artifact is missing")
    resolved = _resolve_real_bundle_artifact_path(
        raw_entry,
        output_dir=output_dir,
        label="cell_provenance",
    )
    result["cell_provenance_reference"] = resolved
    if resolved.is_file():
        result["cell_provenance"] = resolved
        return
    if raw_entry.get("externalized") is not True:
        raise FileNotFoundError(str(resolved))
    summary_relpath = str(raw_entry.get("replacement_or_summary", "")).strip()
    if not summary_relpath:
        raise ValueError("real bundle cell_provenance replacement is missing")
    result["cell_provenance_summary"] = _resolve_real_bundle_artifact(
        {"path": summary_relpath},
        output_dir=output_dir,
        label="cell_provenance_summary",
    )


def _resolve_real_bundle_artifact(
    raw_entry: Mapping[str, Any],
    *,
    output_dir: Path,
    label: str,
) -> Path:
    resolved = _resolve_real_bundle_artifact_path(
        raw_entry,
        output_dir=output_dir,
        label=label,
    )
    if not resolved.is_file():
        raise FileNotFoundError(str(resolved))
    return resolved


def _resolve_real_bundle_artifact_path(
    raw_entry: Mapping[str, Any],
    *,
    output_dir: Path,
    label: str,
) -> Path:
    relpath = str(raw_entry.get("path", "")).strip()
    path = Path(relpath)
    if not relpath or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"real bundle {label} path must be bundle-relative")
    resolved = (output_dir / path).resolve(strict=False)
    try:
        resolved.relative_to(output_dir.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"real bundle {label} path escapes bundle") from exc
    return resolved


def _input_artifacts(
    payload: Mapping[str, Any],
    *,
    source_root: Path,
    problems: list[str],
) -> dict[str, Path]:
    raw = payload.get("input_artifacts")
    if not isinstance(raw, dict):
        problems.append("default activation dry-run input_artifacts must be an object")
        return {}
    result: dict[str, Path] = {}
    for field in ("promotion_packet_summary_json",):
        path_value = str(raw.get(field, "")).strip()
        sha_value = str(raw.get(f"{field}_sha256", "")).strip()
        if not path_value:
            problems.append(f"default activation dry-run {field} is missing")
            continue
        path = _resolve_source(Path(path_value), source_root=source_root)
        result[field] = path
        if not path.is_file():
            problems.append(f"default activation dry-run {field} does not exist")
            continue
        if not _is_sha256(sha_value) or file_sha256(path) != sha_value.upper():
            problems.append(f"default activation dry-run {field}_sha256 mismatch")
    return result


def _predecessor_input_path(
    payload: Mapping[str, Any],
    *,
    field: str,
    source_root: Path,
    owner: str,
) -> Path:
    raw = payload.get("input_artifacts")
    if not isinstance(raw, dict):
        raise ValueError(f"{owner} input_artifacts must be an object")
    path_value = str(raw.get(field, "")).strip()
    if not path_value:
        raise ValueError(f"{owner} {field} is missing")
    path = _resolve_source(Path(path_value), source_root=source_root)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path


def _output_artifacts(
    payload: Mapping[str, Any],
    output_dir: Path,
    problems: list[str],
) -> dict[str, Path]:
    raw = payload.get("artifacts")
    if not isinstance(raw, dict):
        problems.append("default activation dry-run artifacts must be an object")
        return {}
    result: dict[str, Path] = {}
    for label, raw_entry in raw.items():
        if not isinstance(raw_entry, dict):
            problems.append(f"default activation dry-run {label} entry invalid")
            continue
        relpath = str(raw_entry.get("path", "")).strip()
        sha256 = str(raw_entry.get("sha256", "")).strip()
        path = Path(relpath)
        if not relpath or path.is_absolute() or ".." in path.parts:
            problems.append(f"default activation dry-run {label} path invalid")
            continue
        resolved = (output_dir / path).resolve(strict=False)
        try:
            resolved.relative_to(output_dir.resolve(strict=False))
        except ValueError:
            problems.append(f"default activation dry-run {label} path escapes output")
            continue
        result[str(label)] = resolved
        if not resolved.is_file():
            problems.append(f"default activation dry-run {label} does not exist")
            continue
        if not _is_sha256(sha256) or file_sha256(resolved) != sha256.upper():
            problems.append(f"default activation dry-run {label} sha256 mismatch")
    return result


def _artifact_record(path: Path, *, base_dir: Path) -> dict[str, str]:
    return {
        "path": path.resolve(strict=False)
        .relative_to(
            base_dir.resolve(strict=False),
        )
        .as_posix(),
        "sha256": file_sha256(path),
    }


def _source_relpath(path: Path, *, source_root: Path) -> str:
    return _source_relpath_existing(path, source_root=source_root)


def _source_relpath_existing(path: Path, *, source_root: Path) -> str:
    try:
        return (
            path.resolve(strict=True)
            .relative_to(
                source_root.resolve(strict=True),
            )
            .as_posix()
        )
    except ValueError:
        return str(path.resolve(strict=True))


def _source_relpath_reference(path: Path, *, source_root: Path) -> str:
    try:
        return (
            path.resolve(strict=False)
            .relative_to(
                source_root.resolve(strict=True),
            )
            .as_posix()
        )
    except ValueError:
        return str(path.resolve(strict=False))


def _resolve_source(path: Path, *, source_root: Path) -> Path:
    return path if path.is_absolute() else source_root / path


def _read_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    return data


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def _raise_if_problems(label: str, problems: Sequence[str]) -> None:
    if problems:
        raise ValueError(f"{label}: " + "; ".join(problems))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check_only:
        summary_json = args.summary_json or (
            args.output_dir / "quant_matrix_default_activation_dry_run_summary.json"
        )
        problems = validate_quant_matrix_default_activation_dry_run(
            summary_json=summary_json,
            source_root=args.source_root,
            expected_source_run_id=args.expected_source_run_id,
            expected_downstream_scope=args.expected_downstream_scope,
            expected_accepted_backfill_count=args.expected_accepted_backfill_count,
        )
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"default_activation_dry_run_summary_json: {summary_json}")
        print("default_activation_dry_run_status: pass")
        return 0
    try:
        outputs = build_quant_matrix_default_activation_dry_run(
            output_dir=args.output_dir,
            source_root=args.source_root,
            promotion_packet_summary_json=args.promotion_packet_summary_json,
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
        default=DEFAULT_ACTIVATION_DRY_RUN_OUTPUT_DIR,
    )
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument(
        "--promotion-packet-summary-json",
        type=Path,
        default=DEFAULT_PROMOTION_PACKET_SUMMARY_JSON,
    )
    parser.add_argument("--expected-source-run-id", default=DEFAULT_SOURCE_RUN_ID)
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
    parser.add_argument("--summary-json", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
