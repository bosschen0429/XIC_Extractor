"""Build/check the real/candidate QuantMatrix bundle.

This adapter is no-RAW and does not run a scorer. It binds the current
511-cell standard-peak authority replay into the
manifest/version/review/downstream/readiness contracts:
ProductionAcceptanceManifest, QuantMatrixVersion, review sidecar, downstream
impact smoke, and contract-only readiness.

It does not mutate ProductWriter, workbook, GUI, selected peak/area, counted
detection, broad Backfill authority, or default extraction behavior.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_quant_matrix_version import run_activation
from scripts.check_production_acceptance_manifest import (
    REQUIRED_COLUMNS as ACCEPTANCE_COLUMNS,
)
from scripts.check_production_acceptance_manifest import (
    check_production_acceptance_manifest,
    production_acceptance_manifest_sha256,
)
from xic_extractor.alignment.quant_matrix_downstream_impact import (
    build_quant_matrix_downstream_impact_smoke,
    validate_quant_matrix_downstream_impact_smoke,
)
from xic_extractor.alignment.quant_matrix_fixture_contract import (
    validate_fixture_contract,
    write_cell_provenance_contract,
    write_review_rows_contract,
)
from xic_extractor.alignment.quant_matrix_promotion import (
    evaluate_quant_matrix_promotion_readiness,
)
from xic_extractor.alignment.quant_matrix_report import (
    build_quant_matrix_review_report,
)
from xic_extractor.alignment.quant_matrix_version import (
    CELL_PROVENANCE_COLUMNS,
    EXPECTED_DIFF_COLUMNS,
    EXPECTED_DIFF_SUMMARY_COLUMNS,
)
from xic_extractor.tabular_io import (
    file_sha256,
    numeric_equal,
    optional_int,
    read_tsv_required,
    read_tsv_with_header,
    write_tsv,
)

ROOT = Path(__file__).resolve().parents[1]
REAL_BUNDLE_SCHEMA = "quant_matrix_real_bundle"
DEFAULT_SOURCE_RUN_DIR = (
    ROOT
    / "output/productization_realdata_seed_guard_85raw_20260617/"
    "generated_policy_policy_observed_oracle_no_raw_productization"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "docs/superpowers/validation/quant_matrix_real_bundle"
)
DEFAULT_RENDERED_REVIEW_DIR = (
    ROOT
    / "local_validation_artifacts/externalized_superpowers_validation/"
    "quant_matrix_real_bundle/review"
)
DEFAULT_DOWNSTREAM_SCOPE = "current_511_authority_replay"
DEFAULT_SOURCE_RUN_ID = (
    "seed-guard-realdata-85raw-generated-policy-policy-observed-oracle-20260617"
)
DEFAULT_ACCEPTED_BACKFILL_COUNT = 511
EXTERNALIZED_ARTIFACT_SUMMARY_SCHEMA = "externalized_validation_artifact_summary"

_TRUE = "TRUE"
_FALSE = "FALSE"


@dataclass(frozen=True)
class SourceRunPaths:
    activation_inputs_summary: Path
    activation_values: Path
    seed_guard_decisions: Path
    activation_value_delta: Path
    activated_matrix: Path
    matrix_identity: Path


def build_quant_matrix_real_bundle(
    *,
    source_run_dir: Path = DEFAULT_SOURCE_RUN_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    repo_root: Path = ROOT,
    downstream_scope: str = DEFAULT_DOWNSTREAM_SCOPE,
    rendered_review_dir: Path | None = None,
) -> Mapping[str, Path]:
    source_paths = _source_run_paths(source_run_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered_review_dir = _resolve_rendered_review_dir(
        output_dir=output_dir,
        rendered_review_dir=rendered_review_dir,
    )
    inputs_dir = output_dir / "inputs"
    sources_dir = output_dir / "source_artifacts"
    quant_dir = output_dir / "quant_matrix_version"
    review_dir = output_dir / "review"
    downstream_dir = output_dir / "downstream_impact"
    readiness_dir = output_dir / "readiness"
    for directory in (
        inputs_dir,
        sources_dir,
        quant_dir,
        review_dir,
        rendered_review_dir,
        downstream_dir,
        readiness_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    copied_sources = _copy_source_artifacts(
        source_paths=source_paths,
        sources_dir=sources_dir,
    )
    seed_rows = read_tsv_required(
        source_paths.seed_guard_decisions,
        (
            "peak_hypothesis_id",
            "sample_scope",
            "pre_backfill_matrix_path",
            "detected_count",
            "seed_floor",
        ),
    )
    baseline_source = _baseline_matrix_source(seed_rows, repo_root=repo_root)
    baseline_quant_matrix = inputs_dir / "baseline_quant_matrix.tsv"
    input_matrix_identity = inputs_dir / "alignment_matrix_identity.tsv"
    shutil.copy2(baseline_source, baseline_quant_matrix)
    shutil.copy2(source_paths.matrix_identity, input_matrix_identity)

    activation_value_rows = read_tsv_required(
        source_paths.activation_values,
        (
            "peak_hypothesis_id",
            "feature_family_id",
            "sample_stem",
            "projected_matrix_value",
            "source_row_sha256",
        ),
    )
    delta_rows = _written_delta_rows(
        read_tsv_required(
            source_paths.activation_value_delta,
            (
                "peak_hypothesis_id",
                "sample_id",
                "activated_matrix_value",
                "matrix_value_effect",
                "value_changed",
            ),
        )
    )
    values_by_key = _rows_by_key(
        activation_value_rows,
        peak_field="peak_hypothesis_id",
        sample_field="sample_stem",
        label="activation value",
    )
    seed_by_key = _rows_by_key(
        seed_rows,
        peak_field="peak_hypothesis_id",
        sample_field="sample_scope",
        label="seed guard",
    )
    delta_by_key = _rows_by_key(
        delta_rows,
        peak_field="peak_hypothesis_id",
        sample_field="sample_id",
        label="activation delta",
    )
    if set(values_by_key) != set(delta_by_key):
        raise ValueError("activation values and written deltas have different keys")
    if set(values_by_key) != set(seed_by_key):
        raise ValueError("activation values and seed guard decisions differ")

    matrix_header, baseline_rows = read_tsv_with_header(baseline_quant_matrix)
    _activated_header, activated_rows = read_tsv_with_header(
        source_paths.activated_matrix,
    )
    if tuple(matrix_header) != tuple(_activated_header):
        raise ValueError("baseline and activated matrix headers differ")
    sample_columns = _sample_columns(matrix_header)
    identity_rows = read_tsv_required(
        input_matrix_identity,
        (
            "matrix_row_index",
            "peak_hypothesis_id",
            "source_feature_family_ids",
        ),
    )
    identity_by_peak = _identity_by_peak(identity_rows, row_count=len(baseline_rows))
    row_counts = _row_counts(
        matrix_header=matrix_header,
        activated_rows=activated_rows,
        identity_by_peak=identity_by_peak,
        written_keys=set(values_by_key),
    )
    _validate_source_values(
        baseline_rows=baseline_rows,
        activated_rows=activated_rows,
        identity_by_peak=identity_by_peak,
        sample_columns=sample_columns,
        delta_by_key=delta_by_key,
        values_by_key=values_by_key,
    )

    manifest = inputs_dir / "production_acceptance_manifest.tsv"
    manifest_rows = _manifest_rows(
        values_by_key=values_by_key,
        seed_by_key=seed_by_key,
        row_counts=row_counts,
        source_artifact_relpath=_repo_relpath(
            copied_sources["activation_values"],
            repo_root=repo_root,
        ),
        source_artifact_sha256=file_sha256(copied_sources["activation_values"]),
        doublet_source_relpath=_repo_relpath(
            copied_sources["seed_guard_decisions"],
            repo_root=repo_root,
        ),
        doublet_source_sha256=file_sha256(copied_sources["seed_guard_decisions"]),
    )
    manifest_sha = production_acceptance_manifest_sha256(manifest_rows)
    for row in manifest_rows:
        row["manifest_sha256"] = manifest_sha
    write_tsv(
        manifest,
        manifest_rows,
        ACCEPTANCE_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )

    expected_diff = inputs_dir / "expected_diff.tsv"
    expected_diff_rows = _expected_diff_rows(
        baseline_rows=baseline_rows,
        identity_by_peak=identity_by_peak,
        delta_by_key=delta_by_key,
    )
    write_tsv(
        expected_diff,
        expected_diff_rows,
        EXPECTED_DIFF_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )

    activation_outputs = run_activation(
        input_quant_matrix_tsv=baseline_quant_matrix,
        input_matrix_identity_tsv=input_matrix_identity,
        production_acceptance_manifest_tsv=manifest,
        expected_diff_tsv=expected_diff,
        output_dir=quant_dir,
        manifest_root=repo_root,
    )
    _rewrite_source_summary_paths(activation_outputs["source_summary"])
    review_outputs = build_quant_matrix_review_report(
        quant_matrix_tsv=activation_outputs["quant_matrix"],
        cell_provenance_tsv=activation_outputs["cell_provenance"],
        row_summary_tsv=activation_outputs["row_summary"],
        source_summary_tsv=activation_outputs["source_summary"],
        output_dir=review_dir,
        html_path=rendered_review_dir / "quant_matrix_review_report.html",
    )
    fixture_contract_outputs = _write_fixture_contract_outputs(
        activation_outputs=activation_outputs,
        review_outputs=review_outputs,
        quant_dir=quant_dir,
        review_dir=review_dir,
    )
    _rewrite_review_artifact_paths(
        summary_json=review_outputs["summary_json"],
        html_path=review_outputs["html"],
        paths={
            "quant_matrix_tsv": activation_outputs["quant_matrix"],
            "cell_provenance_tsv": activation_outputs["cell_provenance"],
            "row_summary_tsv": activation_outputs["row_summary"],
            "source_summary_tsv": activation_outputs["source_summary"],
            "production_acceptance_manifest_tsv": manifest,
        },
    )
    review_html_summary = _write_externalized_review_html_summary(
        output_dir=output_dir,
        rendered_html=review_outputs["html"],
        repo_root=repo_root,
    )
    downstream_outputs = build_quant_matrix_downstream_impact_smoke(
        quant_matrix_tsv=activation_outputs["quant_matrix"],
        cell_provenance_tsv=activation_outputs["cell_provenance"],
        row_summary_tsv=activation_outputs["row_summary"],
        output_dir=downstream_dir,
        downstream_scope=downstream_scope,
        bundle_kind="real_quant_matrix_version",
    )
    _rewrite_downstream_artifact_paths(
        downstream_outputs["summary_json"],
        paths={
            "quant_matrix_tsv": activation_outputs["quant_matrix"],
            "cell_provenance_tsv": activation_outputs["cell_provenance"],
            "row_summary_tsv": activation_outputs["row_summary"],
        },
    )
    readiness_outputs = evaluate_quant_matrix_promotion_readiness(
        expected_diff_summary_tsv=activation_outputs["expected_diff_summary"],
        cell_provenance_tsv=activation_outputs["cell_provenance"],
        row_summary_tsv=activation_outputs["row_summary"],
        review_summary_json=review_outputs["summary_json"],
        output_dir=readiness_dir,
    )
    _rewrite_readiness_artifact_paths(
        readiness_outputs["summary_json"],
        paths={
            "expected_diff_summary_tsv": activation_outputs["expected_diff_summary"],
            "cell_provenance_tsv": activation_outputs["cell_provenance"],
            "row_summary_tsv": activation_outputs["row_summary"],
            "review_summary_json": review_outputs["summary_json"],
        },
    )

    summary_json = output_dir / "quant_matrix_real_bundle_summary.json"
    summary = _summary_payload(
        output_dir=output_dir,
        source_run_dir=source_run_dir,
        source_paths=source_paths,
        copied_sources=copied_sources,
        baseline_quant_matrix=baseline_quant_matrix,
        input_matrix_identity=input_matrix_identity,
        production_acceptance_manifest=manifest,
        expected_diff=expected_diff,
        activation_outputs=activation_outputs,
        review_outputs=review_outputs,
        review_html_summary=review_html_summary,
        downstream_outputs=downstream_outputs,
        readiness_outputs=readiness_outputs,
        fixture_contract_outputs=fixture_contract_outputs,
        accepted_backfill_count=len(manifest_rows),
        source_run_id=_source_run_id(source_paths.activation_inputs_summary),
        downstream_scope=downstream_scope,
        repo_root=repo_root,
    )
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    outputs = {
        "summary_json": summary_json,
        "baseline_quant_matrix": baseline_quant_matrix,
        "input_matrix_identity": input_matrix_identity,
        "production_acceptance_manifest": manifest,
        "expected_diff": expected_diff,
        "quant_matrix": activation_outputs["quant_matrix"],
        "cell_provenance": activation_outputs["cell_provenance"],
        "cell_provenance_summary_json": fixture_contract_outputs[
            "cell_provenance_summary"
        ],
        "cell_provenance_minimal_fixture": fixture_contract_outputs[
            "cell_provenance_minimal_fixture"
        ],
        "row_summary": activation_outputs["row_summary"],
        "expected_diff_summary": activation_outputs["expected_diff_summary"],
        "source_summary": activation_outputs["source_summary"],
        "review_rows_summary_json": fixture_contract_outputs[
            "review_rows_summary"
        ],
        "review_rows_minimal_fixture": fixture_contract_outputs[
            "review_rows_minimal_fixture"
        ],
        "review_summary_json": review_outputs["summary_json"],
        "downstream_impact_summary_json": downstream_outputs["summary_json"],
        "readiness_summary_json": readiness_outputs["summary_json"],
    }
    if review_html_summary is not None:
        outputs["review_html_summary_json"] = review_html_summary
    return outputs


def validate_quant_matrix_real_bundle(
    *,
    summary_json: Path = DEFAULT_OUTPUT_DIR / "quant_matrix_real_bundle_summary.json",
    repo_root: Path = ROOT,
    expected_source_run_id: str = DEFAULT_SOURCE_RUN_ID,
    expected_downstream_scope: str = DEFAULT_DOWNSTREAM_SCOPE,
    expected_accepted_backfill_count: int = DEFAULT_ACCEPTED_BACKFILL_COUNT,
) -> list[str]:
    problems: list[str] = []
    try:
        payload = json.loads(summary_json.read_text(encoding="utf-8"))
    except OSError as exc:
        return [str(exc)]
    except json.JSONDecodeError as exc:
        return [f"{summary_json}: invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return [f"{summary_json}: expected JSON object"]
    if payload.get("schema_version") != REAL_BUNDLE_SCHEMA:
        problems.append("real bundle schema_version mismatch")
    for field, expected in (
        ("status", "pass"),
        ("artifact_role", "real_quant_matrix_bundle"),
        ("bundle_kind", "real_quant_matrix_version"),
        ("validation_status", "contract_ready_science_inconclusive"),
    ):
        if payload.get(field) != expected:
            problems.append(f"real bundle {field} mismatch")
    if payload.get("source_run_id") != expected_source_run_id:
        problems.append(
            "real bundle source_run_id mismatch: "
            f"expected {expected_source_run_id}",
        )
    if payload.get("downstream_scope") != expected_downstream_scope:
        problems.append(
            "real bundle downstream_scope mismatch: "
            f"expected {expected_downstream_scope}",
        )
    if (
        optional_int(payload.get("accepted_backfill_count", ""))
        != expected_accepted_backfill_count
    ):
        problems.append(
            "real bundle accepted_backfill_count mismatch: "
            f"expected {expected_accepted_backfill_count}",
        )
    for field in (
        "read_only",
        "scorer_ran",
        "raw_or_full_cohort_ran",
        "product_writer_changed",
        "default_quant_matrix_changed",
        "broad_backfill_unparked",
    ):
        expected_bool = field == "read_only"
        if payload.get(field) is not expected_bool:
            problems.append(
                f"real bundle {field} must be {str(expected_bool).lower()}",
            )
    artifact_paths = _artifact_paths_from_summary(
        payload,
        summary_json=summary_json,
        repo_root=repo_root,
        problems=problems,
    )
    manifest = artifact_paths.get("production_acceptance_manifest")
    if manifest is not None:
        problems.extend(
            f"production_acceptance_manifest: {problem}"
            for problem in check_production_acceptance_manifest(
                manifest_path=manifest,
                repo_root=repo_root,
            )
        )
    downstream = artifact_paths.get("downstream_impact_summary_json")
    if downstream is not None:
        problems.extend(
            validate_quant_matrix_downstream_impact_smoke(
                downstream,
                cell_provenance_contract_summary=artifact_paths.get(
                    "cell_provenance_summary",
                ),
                cell_provenance_minimal_fixture=artifact_paths.get(
                    "cell_provenance_minimal_fixture",
                ),
            )
        )
    expected_summary = artifact_paths.get("expected_diff_summary")
    if expected_summary is not None:
        _append_expected_diff_problems(expected_summary, payload, problems)
    _append_fixture_contract_artifact_problems(
        artifact_paths,
        payload,
        problems,
        summary_label="cell_provenance_summary",
        fixture_label="cell_provenance_minimal_fixture",
        count_column="cell_status",
        count_value="accepted_backfill",
        expected_count=expected_accepted_backfill_count,
    )
    _append_fixture_contract_artifact_problems(
        artifact_paths,
        payload,
        problems,
        summary_label="review_rows_summary",
        fixture_label="review_rows_minimal_fixture",
        count_column="report_authority",
        count_value="review_only",
        expected_count=None,
    )
    readiness = artifact_paths.get("readiness_summary_json")
    if readiness is not None:
        _append_readiness_problems(readiness, problems)
    return problems


def _source_run_paths(source_run_dir: Path) -> SourceRunPaths:
    activation_dir = source_run_dir / "standard_peak_activation_inputs"
    activated_dir = source_run_dir / "activated_matrix"
    return SourceRunPaths(
        activation_inputs_summary=(
            activation_dir / "standard_peak_activation_inputs.tsv"
        ),
        activation_values=activation_dir / "standard_peak_activation_values.tsv",
        seed_guard_decisions=activation_dir / "seed_guard_decisions.tsv",
        activation_value_delta=activated_dir / "activation_value_delta.tsv",
        activated_matrix=activated_dir / "alignment_matrix.tsv",
        matrix_identity=activated_dir / "alignment_matrix_identity.tsv",
    )


def _copy_source_artifacts(
    *,
    source_paths: SourceRunPaths,
    sources_dir: Path,
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for label, source in (
        ("activation_inputs_summary", source_paths.activation_inputs_summary),
        ("activation_values", source_paths.activation_values),
        ("seed_guard_decisions", source_paths.seed_guard_decisions),
        ("activation_value_delta", source_paths.activation_value_delta),
    ):
        if not source.is_file():
            raise FileNotFoundError(str(source))
        destination = sources_dir / source.name
        shutil.copy2(source, destination)
        result[label] = destination
    return result


def _baseline_matrix_source(
    seed_rows: Sequence[Mapping[str, str]],
    *,
    repo_root: Path,
) -> Path:
    paths = {
        row.get("pre_backfill_matrix_path", "").strip()
        for row in seed_rows
        if row.get("pre_backfill_matrix_path", "").strip()
    }
    if len(paths) != 1:
        raise ValueError(
            "seed guard decisions must point to exactly one baseline matrix",
        )
    value = next(iter(paths))
    path = Path(value)
    resolved = path if path.is_absolute() else repo_root / path
    if not resolved.is_file():
        raise FileNotFoundError(str(resolved))
    return resolved


def _written_delta_rows(
    rows: Sequence[Mapping[str, str]],
) -> tuple[Mapping[str, str], ...]:
    return tuple(
        row
        for row in rows
        if row.get("matrix_value_effect") == "written"
        and row.get("value_changed") == _TRUE
    )


def _rows_by_key(
    rows: Sequence[Mapping[str, str]],
    *,
    peak_field: str,
    sample_field: str,
    label: str,
) -> dict[tuple[str, str], Mapping[str, str]]:
    result: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        key = (row.get(peak_field, ""), row.get(sample_field, ""))
        if not all(key):
            raise ValueError(f"{label} row missing key")
        if key in result:
            raise ValueError(f"duplicate {label} key: {key[0]}/{key[1]}")
        result[key] = row
    return result


def _sample_columns(matrix_header: Sequence[str]) -> tuple[str, ...]:
    if len(matrix_header) < 3 or tuple(matrix_header[:2]) != ("Mz", "RT"):
        raise ValueError("quant matrix header must start with Mz, RT")
    return tuple(column for column in matrix_header if column not in {"Mz", "RT"})


def _identity_by_peak(
    rows: Sequence[Mapping[str, str]],
    *,
    row_count: int,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        peak = row.get("peak_hypothesis_id", "")
        index = optional_int(row.get("matrix_row_index", ""))
        if not peak or index is None:
            raise ValueError("matrix identity row missing peak/index")
        if index < 1 or index > row_count:
            raise ValueError(f"matrix_row_index out of range: {index}")
        if peak in result and result[peak] != index:
            raise ValueError(f"duplicate peak_hypothesis_id: {peak}")
        result[peak] = index
    return result


def _row_counts(
    *,
    matrix_header: Sequence[str],
    activated_rows: Sequence[Mapping[str, str]],
    identity_by_peak: Mapping[str, int],
    written_keys: set[tuple[str, str]],
) -> dict[str, Mapping[str, str]]:
    sample_columns = _sample_columns(matrix_header)
    written_by_peak = Counter(peak for peak, _sample in written_keys)
    result: dict[str, Mapping[str, str]] = {}
    for peak, row_index in identity_by_peak.items():
        matrix_row = activated_rows[row_index - 1]
        available = sum(1 for sample in sample_columns if matrix_row.get(sample, ""))
        backfilled = written_by_peak[peak]
        detected = available - backfilled
        if detected < 0:
            raise ValueError(f"{peak}: accepted Backfill exceeds available cells")
        missing = len(sample_columns) - available
        fraction = 0.0 if available == 0 else backfilled / available
        result[peak] = {
            "detected_count": str(detected),
            "backfilled_count": str(backfilled),
            "quant_available_count": str(available),
            "missing_count": str(missing),
            "backfill_fraction": f"{fraction:.6f}",
            "prevalence_flags": _prevalence_flags(
                detected=detected,
                backfilled=backfilled,
                available=available,
            ),
        }
    return result


def _prevalence_flags(
    *,
    detected: int,
    backfilled: int,
    available: int,
) -> str:
    flags: list[str] = []
    if detected < 4:
        flags.append("low_seed_support")
    if available and backfilled > detected:
        flags.append("high_backfill_dependency")
    return ";".join(flags)


def _validate_source_values(
    *,
    baseline_rows: Sequence[Mapping[str, str]],
    activated_rows: Sequence[Mapping[str, str]],
    identity_by_peak: Mapping[str, int],
    sample_columns: Sequence[str],
    delta_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    values_by_key: Mapping[tuple[str, str], Mapping[str, str]],
) -> None:
    for key, delta in delta_by_key.items():
        peak, sample = key
        if sample not in sample_columns:
            raise ValueError(f"{peak}/{sample}: sample missing from matrix")
        row_index = identity_by_peak.get(peak)
        if row_index is None:
            raise ValueError(f"{peak}/{sample}: peak missing from matrix identity")
        baseline_value = baseline_rows[row_index - 1].get(sample, "")
        original_value = delta.get("original_matrix_value", "")
        if baseline_value != original_value:
            raise ValueError(f"{peak}/{sample}: baseline/original value mismatch")
        if baseline_value:
            raise ValueError(f"{peak}/{sample}: baseline is already populated")
        activated_value = delta.get("activated_matrix_value", "")
        projected_value = values_by_key[key].get("projected_matrix_value", "")
        matrix_value = activated_rows[row_index - 1].get(sample, "")
        if not numeric_equal(activated_value, projected_value):
            raise ValueError(f"{peak}/{sample}: projected value mismatch")
        if not numeric_equal(activated_value, matrix_value):
            raise ValueError(f"{peak}/{sample}: activated matrix value mismatch")


def _manifest_rows(
    *,
    values_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    seed_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    row_counts: Mapping[str, Mapping[str, str]],
    source_artifact_relpath: str,
    source_artifact_sha256: str,
    doublet_source_relpath: str,
    doublet_source_sha256: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key in sorted(values_by_key):
        peak, sample = key
        value_row = values_by_key[key]
        seed_row = seed_by_key[key]
        counts = row_counts[peak]
        flags = str(counts["prevalence_flags"])
        rows.append(
            {
                "schema_version": "production_acceptance_manifest",
                "peak_hypothesis_id": peak,
                "sample_stem": sample,
                "feature_family_id": value_row.get("feature_family_id", ""),
                "acceptance_decision": "accept_basic_backfill",
                "acceptance_basis": "machine_basic",
                "truth_status": "not_truth_claimed",
                "shadow_only": _FALSE,
                "write_authority": _TRUE,
                "matrix_write_allowed": _TRUE,
                "quant_value": value_row.get("projected_matrix_value", ""),
                "quant_value_source": value_row.get(
                    "projected_matrix_value_source",
                    "standard_peak_shadow_projection",
                ),
                "matrix_area_source": "gaussian_smoothed_standard_peak_projection",
                "detected_count": counts["detected_count"],
                "backfilled_count": counts["backfilled_count"],
                "quant_available_count": counts["quant_available_count"],
                "missing_count": counts["missing_count"],
                "backfill_fraction": counts["backfill_fraction"],
                "prevalence_flags": flags,
                "hard_blocker_rule_ids": "",
                "triggered_risk_rule_ids": flags,
                "closure_rule_ids": _closure_rule_ids(flags),
                "decision_reason": _decision_reason(seed_row),
                "next_evidence_needed": "",
                "doublet_status": "no_doublet_claim",
                "reference_side": "not_applicable",
                "doublet_allowed": _TRUE,
                "doublet_source_relpath": doublet_source_relpath,
                "doublet_source_sha256": doublet_source_sha256,
                "source_artifact_relpath": source_artifact_relpath,
                "source_artifact_sha256": source_artifact_sha256,
                "source_row_sha256": value_row.get("source_row_sha256", "").upper(),
                "manifest_sha256": "",
                "acceptance_contract_version": (
                    "production_acceptance_manifest_contract"
                ),
            }
        )
    return rows


def _closure_rule_ids(prevalence_flags: str) -> str:
    labels = ["current_511_policy_write_ready"]
    if prevalence_flags:
        labels.append("prevalence_risk_report_only")
    return ";".join(labels)


def _decision_reason(seed_row: Mapping[str, str]) -> str:
    reason = seed_row.get("decision_reason", "").strip()
    if reason:
        return f"current_511_authority_replay:{reason}"
    return "current_511_authority_replay"


def _expected_diff_rows(
    *,
    baseline_rows: Sequence[Mapping[str, str]],
    identity_by_peak: Mapping[str, int],
    delta_by_key: Mapping[tuple[str, str], Mapping[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key in sorted(delta_by_key):
        peak, sample = key
        row_index = identity_by_peak[peak]
        rows.append(
            {
                "schema_version": "quant_matrix_version_expected_diff",
                "peak_hypothesis_id": peak,
                "sample_stem": sample,
                "baseline_value": baseline_rows[row_index - 1].get(sample, ""),
                "activated_value": delta_by_key[key].get("activated_matrix_value", ""),
                "expected_matrix_effect": "write_accepted_backfill",
                "expected_reason": "current_511_authority_replay",
            }
        )
    return rows


def _source_run_id(activation_inputs_summary: Path) -> str:
    rows = read_tsv_required(activation_inputs_summary, ("source_run_id",))
    if len(rows) != 1:
        raise ValueError("activation input summary must have exactly one row")
    return rows[0].get("source_run_id", "")


def _summary_payload(
    *,
    output_dir: Path,
    source_run_dir: Path,
    source_paths: SourceRunPaths,
    copied_sources: Mapping[str, Path],
    baseline_quant_matrix: Path,
    input_matrix_identity: Path,
    production_acceptance_manifest: Path,
    expected_diff: Path,
    activation_outputs: Mapping[str, Path],
    review_outputs: Mapping[str, Path],
    review_html_summary: Path | None,
    downstream_outputs: Mapping[str, Path],
    readiness_outputs: Mapping[str, Path],
    fixture_contract_outputs: Mapping[str, Path],
    accepted_backfill_count: int,
    source_run_id: str,
    downstream_scope: str,
    repo_root: Path,
) -> dict[str, Any]:
    artifacts = {
        "baseline_quant_matrix": baseline_quant_matrix,
        "input_matrix_identity": input_matrix_identity,
        "production_acceptance_manifest": production_acceptance_manifest,
        "expected_diff": expected_diff,
        "quant_matrix": activation_outputs["quant_matrix"],
        "row_summary": activation_outputs["row_summary"],
        "expected_diff_summary": activation_outputs["expected_diff_summary"],
        "source_summary": activation_outputs["source_summary"],
        "review_summary_json": review_outputs["summary_json"],
        "downstream_impact_summary_json": downstream_outputs["summary_json"],
        "downstream_impact_rows_tsv": downstream_outputs["rows_tsv"],
        "readiness_summary_json": readiness_outputs["summary_json"],
        "readiness_checks_tsv": readiness_outputs["checks_tsv"],
        "cell_provenance_summary": fixture_contract_outputs[
            "cell_provenance_summary"
        ],
        "cell_provenance_minimal_fixture": fixture_contract_outputs[
            "cell_provenance_minimal_fixture"
        ],
        "review_rows_summary": fixture_contract_outputs["review_rows_summary"],
        "review_rows_minimal_fixture": fixture_contract_outputs[
            "review_rows_minimal_fixture"
        ],
    }
    artifact_entries: dict[str, dict[str, Any]] = {
        label: _artifact_entry(path, output_dir=output_dir)
        for label, path in artifacts.items()
    }
    artifact_entries["cell_provenance"] = _externalized_fixture_source_entry(
        activation_outputs["cell_provenance"],
        summary_path=fixture_contract_outputs["cell_provenance_summary"],
        output_dir=output_dir,
        repo_root=repo_root,
    )
    artifact_entries["review_rows"] = _externalized_fixture_source_entry(
        review_outputs["review_rows"],
        summary_path=fixture_contract_outputs["review_rows_summary"],
        output_dir=output_dir,
        repo_root=repo_root,
    )
    artifact_entries["cell_provenance_summary"]["retention_decision"] = (
        "keep_summary"
    )
    artifact_entries["cell_provenance_minimal_fixture"]["retention_decision"] = (
        "keep_minimal_fixture"
    )
    artifact_entries["review_rows_summary"]["retention_decision"] = "keep_summary"
    artifact_entries["review_rows_minimal_fixture"]["retention_decision"] = (
        "keep_minimal_fixture"
    )
    if review_html_summary is None:
        artifact_entries["review_html"] = _artifact_entry(
            review_outputs["html"],
            output_dir=output_dir,
        )
    else:
        artifact_entries["review_html"] = _externalized_review_html_entry(
            output_dir=output_dir,
            rendered_html=review_outputs["html"],
            review_html_summary=review_html_summary,
            repo_root=repo_root,
        )
        artifact_entries["review_html_summary_json"] = _artifact_entry(
            review_html_summary,
            output_dir=output_dir,
        )
    return {
        "schema_version": REAL_BUNDLE_SCHEMA,
        "artifact_role": "real_quant_matrix_bundle",
        "status": "pass",
        "bundle_kind": "real_quant_matrix_version",
        "validation_status": "contract_ready_science_inconclusive",
        "source_run_id": source_run_id,
        "source_run_dir": _display_path(source_run_dir, base_dir=repo_root),
        "accepted_backfill_count": accepted_backfill_count,
        "downstream_scope": downstream_scope,
        "read_only": True,
        "scorer_ran": False,
        "raw_or_full_cohort_ran": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "broad_backfill_unparked": False,
        "may_promote_default_quant_matrix": False,
        "authority_statement": (
            "Real/candidate QuantMatrix bundle only. It proves the current "
            "511 accepted Backfill cells can pass the manifest/version/review/"
            "downstream contracts, but it does not change ProductWriter defaults."
        ),
        "source_artifacts": {
            label: _artifact_entry(path, output_dir=output_dir)
            for label, path in copied_sources.items()
        },
        "original_source_artifacts": {
            "activation_inputs_summary": _original_entry(
                source_paths.activation_inputs_summary,
                repo_root=repo_root,
            ),
            "activation_values": _original_entry(
                source_paths.activation_values,
                repo_root=repo_root,
            ),
            "seed_guard_decisions": _original_entry(
                source_paths.seed_guard_decisions,
                repo_root=repo_root,
            ),
            "activation_value_delta": _original_entry(
                source_paths.activation_value_delta,
                repo_root=repo_root,
            ),
            "activated_matrix": _original_entry(
                source_paths.activated_matrix,
                repo_root=repo_root,
            ),
            "matrix_identity": _original_entry(
                source_paths.matrix_identity,
                repo_root=repo_root,
            ),
        },
        "artifacts": artifact_entries,
    }


def _artifact_entry(path: Path, *, output_dir: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(output_dir).as_posix(),
        "sha256": file_sha256(path),
    }


def _resolve_rendered_review_dir(
    *,
    output_dir: Path,
    rendered_review_dir: Path | None,
) -> Path:
    if rendered_review_dir is not None:
        return rendered_review_dir
    if output_dir.resolve(strict=False) == DEFAULT_OUTPUT_DIR.resolve(strict=False):
        return DEFAULT_RENDERED_REVIEW_DIR
    return output_dir / "review"


def _write_fixture_contract_outputs(
    *,
    activation_outputs: Mapping[str, Path],
    review_outputs: Mapping[str, Path],
    quant_dir: Path,
    review_dir: Path,
) -> dict[str, Path]:
    cell_summary = quant_dir / "cell_provenance_summary.json"
    cell_fixture = quant_dir / "cell_provenance_minimal_fixture.tsv"
    review_summary = review_dir / "quant_matrix_review_rows_summary.json"
    review_fixture = review_dir / "quant_matrix_review_rows_minimal_fixture.tsv"
    write_cell_provenance_contract(
        activation_outputs["cell_provenance"],
        cell_summary,
        cell_fixture,
        source_relpath="quant_matrix_version/cell_provenance.tsv",
    )
    write_review_rows_contract(
        review_outputs["review_rows"],
        review_summary,
        review_fixture,
        source_relpath="review/quant_matrix_review_rows.tsv",
    )
    return {
        "cell_provenance_summary": cell_summary,
        "cell_provenance_minimal_fixture": cell_fixture,
        "review_rows_summary": review_summary,
        "review_rows_minimal_fixture": review_fixture,
    }


def _externalized_fixture_source_entry(
    path: Path,
    *,
    summary_path: Path,
    output_dir: Path,
    repo_root: Path,
) -> dict[str, Any]:
    sha256 = file_sha256(path)
    externalized_path = _maybe_externalize_generated_artifact(
        path,
        output_dir=output_dir,
    )
    return {
        "path": path.relative_to(output_dir).as_posix(),
        "sha256": sha256,
        "externalized": True,
        "externalized_path": _display_path(
            externalized_path,
            base_dir=repo_root,
        ),
        "replacement_or_summary": summary_path.relative_to(output_dir).as_posix(),
        "retention_decision": "externalize",
    }


def _externalized_validation_artifact_path(path: Path, *, output_dir: Path) -> Path:
    relpath = path.relative_to(output_dir)
    return (
        ROOT
        / "local_validation_artifacts/externalized_superpowers_validation/"
        "quant_matrix_real_bundle"
        / relpath
    )


def _maybe_externalize_generated_artifact(path: Path, *, output_dir: Path) -> Path:
    externalized_path = _externalized_validation_artifact_path(
        path,
        output_dir=output_dir,
    )
    if output_dir.resolve(strict=False) != DEFAULT_OUTPUT_DIR.resolve(strict=False):
        return externalized_path
    externalized_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, externalized_path)
    path.unlink()
    return externalized_path


def _write_externalized_review_html_summary(
    *,
    output_dir: Path,
    rendered_html: Path,
    repo_root: Path,
) -> Path | None:
    original_html = output_dir / "review/quant_matrix_review_report.html"
    if rendered_html.resolve(strict=False) == original_html.resolve(strict=False):
        return None
    if original_html.exists():
        original_html.unlink()
    summary_path = output_dir / "review/quant_matrix_review_report_summary.json"
    summary = {
        "schema_version": EXTERNALIZED_ARTIFACT_SUMMARY_SCHEMA,
        "artifact_id": "quant_matrix_real_bundle.review_html",
        "original_path": _display_path(original_html, base_dir=repo_root),
        "externalized_path": _display_path(rendered_html, base_dir=repo_root),
        "generated_file_name": rendered_html.name,
        "size_bytes": rendered_html.stat().st_size,
        "line_count": _text_line_count(rendered_html),
        "sha256": file_sha256(rendered_html),
        "source_builder_command": (
            "uv run python scripts/build_quant_matrix_real_bundle.py"
        ),
        "generated_by": "scripts/build_quant_matrix_real_bundle.py",
        "purpose": "review-only rendered HTML for human inspection",
        "retention_decision": "externalize",
        "may_touch_matrix": False,
        "may_grant_product_authority": False,
        "product_writer_changed": False,
        "matrix_authority_changed": False,
        "created_or_updated": "2026-06-19",
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return summary_path


def _externalized_review_html_entry(
    *,
    output_dir: Path,
    rendered_html: Path,
    review_html_summary: Path,
    repo_root: Path,
) -> dict[str, Any]:
    return {
        "path": "review/quant_matrix_review_report.html",
        "sha256": file_sha256(rendered_html),
        "externalized": True,
        "externalized_path": _display_path(rendered_html, base_dir=repo_root),
        "replacement_or_summary": review_html_summary.relative_to(
            output_dir,
        ).as_posix(),
        "retention_decision": "externalize",
    }


def _text_line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _original_entry(path: Path, *, repo_root: Path) -> dict[str, str]:
    return {
        "path": _display_path(path, base_dir=repo_root),
        "sha256": file_sha256(path),
    }


def _rewrite_source_summary_paths(source_summary_tsv: Path) -> None:
    from xic_extractor.alignment.quant_matrix_version import SOURCE_SUMMARY_COLUMNS

    rows = list(read_tsv_required(source_summary_tsv, SOURCE_SUMMARY_COLUMNS))
    path_fields = (
        "input_quant_matrix_tsv",
        "input_matrix_identity_tsv",
        "production_acceptance_manifest_tsv",
        "expected_diff_tsv",
    )
    rewritten = []
    for row in rows:
        item = dict(row)
        for field in path_fields:
            item[field] = _display_path(
                Path(item[field]),
                base_dir=source_summary_tsv.parent,
            )
        rewritten.append(item)
    write_tsv(
        source_summary_tsv,
        rewritten,
        SOURCE_SUMMARY_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )


def _rewrite_review_artifact_paths(
    *,
    summary_json: Path,
    html_path: Path,
    paths: Mapping[str, Path],
) -> None:
    payload = _read_json_object(summary_json)
    input_artifacts = payload.get("input_artifacts")
    if not isinstance(input_artifacts, dict):
        raise ValueError("review summary input_artifacts must be an object")
    replacements: dict[str, str] = {}
    for field, path in paths.items():
        old_value = str(input_artifacts.get(field, ""))
        new_value = _display_path(path, base_dir=summary_json.parent)
        replacements[old_value] = new_value
        input_artifacts[field] = new_value
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    html = html_path.read_text(encoding="utf-8")
    for old_value, new_value in replacements.items():
        if old_value:
            html = html.replace(old_value, new_value)
    html_path.write_text(html, encoding="utf-8")


def _rewrite_downstream_artifact_paths(
    summary_json: Path,
    *,
    paths: Mapping[str, Path],
) -> None:
    payload = _read_json_object(summary_json)
    input_artifacts = payload.get("input_artifacts")
    if not isinstance(input_artifacts, dict):
        raise ValueError("downstream summary input_artifacts must be an object")
    for field, path in paths.items():
        input_artifacts[field] = _display_path(path, base_dir=summary_json.parent)
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _rewrite_readiness_artifact_paths(
    summary_json: Path,
    *,
    paths: Mapping[str, Path],
) -> None:
    payload = _read_json_object(summary_json)
    input_artifacts = payload.get("input_artifacts")
    if not isinstance(input_artifacts, dict):
        raise ValueError("readiness summary input_artifacts must be an object")
    for field, path in paths.items():
        input_artifacts[field] = _display_path(path, base_dir=summary_json.parent)
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    return data


def _display_path(path: Path, *, base_dir: Path) -> str:
    return Path(os.path.relpath(path.resolve(strict=False), base_dir)).as_posix()


def _artifact_paths_from_summary(
    payload: Mapping[str, Any],
    *,
    summary_json: Path,
    repo_root: Path,
    problems: list[str],
) -> dict[str, Path]:
    output_dir = summary_json.parent
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        problems.append("real bundle artifacts must be an object")
        return {}
    resolved: dict[str, Path] = {}
    for label, raw_entry in artifacts.items():
        if not isinstance(raw_entry, dict):
            problems.append(f"{label}: artifact entry must be an object")
            continue
        if raw_entry.get("externalized") is True:
            _append_externalized_artifact_problems(
                str(label),
                raw_entry,
                output_dir=output_dir,
                repo_root=repo_root,
                problems=problems,
            )
            continue
        relpath = str(raw_entry.get("path", "")).strip()
        sha256 = str(raw_entry.get("sha256", "")).strip()
        path = _resolve_child(output_dir, relpath, problems, label=label)
        if path is None:
            continue
        resolved[str(label)] = path
        if not path.is_file():
            problems.append(f"{label}: artifact missing")
            continue
        if not _is_sha256(sha256):
            problems.append(f"{label}: sha256 must be 64-hex")
        elif file_sha256(path) != sha256.upper():
            problems.append(f"{label}: sha256 mismatch")
    _append_source_artifact_copy_problems(
        payload,
        output_dir=output_dir,
        problems=problems,
    )
    _append_original_source_artifact_problems(
        payload,
        repo_root=repo_root,
        problems=problems,
    )
    return resolved


def _append_externalized_artifact_problems(
    label: str,
    raw_entry: Mapping[str, Any],
    *,
    output_dir: Path,
    repo_root: Path,
    problems: list[str],
) -> None:
    relpath = str(raw_entry.get("path", "")).strip()
    sha256 = str(raw_entry.get("sha256", "")).strip()
    if not relpath:
        problems.append(f"{label}: externalized artifact path is missing")
        return
    if Path(relpath).is_absolute() or ".." in Path(relpath).parts:
        problems.append(f"{label}: externalized artifact path must be output-relative")
    if not _is_sha256(sha256):
        problems.append(f"{label}: externalized artifact sha256 must be 64-hex")
    summary_relpath = str(raw_entry.get("replacement_or_summary", "")).strip()
    summary_path = _resolve_child(
        output_dir,
        summary_relpath,
        problems,
        label=f"{label} replacement summary",
    )
    if summary_path is None:
        return
    if not summary_path.is_file():
        problems.append(f"{label}: replacement summary missing")
        return
    try:
        summary = _read_json_object(summary_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        problems.append(f"{label}: replacement summary invalid: {exc}")
        return
    if summary.get("schema_version") == "quant_matrix_fixture_contract":
        _append_externalized_fixture_contract_problems(
            label,
            raw_entry,
            summary,
            summary_path=summary_path,
            problems=problems,
        )
        return
    expected_original = _display_path(output_dir / relpath, base_dir=repo_root)
    if summary.get("original_path") != expected_original:
        problems.append(f"{label}: replacement summary original_path mismatch")
    if summary.get("sha256") != sha256.upper():
        problems.append(f"{label}: replacement summary sha256 mismatch")
    if summary.get("retention_decision") != "externalize":
        problems.append(f"{label}: replacement summary retention_decision mismatch")
    if summary.get("may_grant_product_authority") is not False:
        problems.append(f"{label}: replacement summary must not grant authority")


def _append_externalized_fixture_contract_problems(
    label: str,
    raw_entry: Mapping[str, Any],
    summary: Mapping[str, Any],
    *,
    summary_path: Path,
    problems: list[str],
) -> None:
    if summary.get("source_relpath") != raw_entry.get("path"):
        problems.append(f"{label}: fixture contract source_relpath mismatch")
    if summary.get("source_sha256") != str(raw_entry.get("sha256", "")).upper():
        problems.append(f"{label}: fixture contract source_sha256 mismatch")
    if summary.get("may_grant_product_authority") is not False:
        problems.append(f"{label}: fixture contract must not grant authority")
    fixture = summary.get("minimal_fixture")
    if not isinstance(fixture, dict):
        problems.append(f"{label}: fixture contract missing minimal_fixture")
        return
    fixture_path = summary_path.parent / str(fixture.get("path", ""))
    problems.extend(
        f"{label}: {problem}"
        for problem in validate_fixture_contract(summary_path, fixture_path)
    )


def _append_source_artifact_copy_problems(
    payload: Mapping[str, Any],
    *,
    output_dir: Path,
    problems: list[str],
) -> None:
    source_artifacts = payload.get("source_artifacts")
    if not isinstance(source_artifacts, dict):
        problems.append("real bundle source_artifacts must be an object")
        return
    for label, raw_entry in source_artifacts.items():
        if not isinstance(raw_entry, dict):
            problems.append(f"{label}: source artifact entry must be an object")
            continue
        relpath = str(raw_entry.get("path", "")).strip()
        sha256 = str(raw_entry.get("sha256", "")).strip()
        path = _resolve_child(output_dir, relpath, problems, label=str(label))
        if path is None or not path.is_file():
            problems.append(f"{label}: source artifact copy missing")
            continue
        if not _is_sha256(sha256) or file_sha256(path) != sha256.upper():
            problems.append(f"{label}: source artifact copy sha256 mismatch")


def _append_original_source_artifact_problems(
    payload: Mapping[str, Any],
    *,
    repo_root: Path,
    problems: list[str],
) -> None:
    original_sources = payload.get("original_source_artifacts")
    if not isinstance(original_sources, dict):
        problems.append("real bundle original_source_artifacts must be an object")
        return
    for label, raw_entry in original_sources.items():
        if not isinstance(raw_entry, dict):
            problems.append(f"{label}: original source entry must be an object")
            continue
        value = str(raw_entry.get("path", "")).strip()
        sha256 = str(raw_entry.get("sha256", "")).strip()
        if not value:
            problems.append(f"{label}: original source path missing")
            continue
        path = Path(value)
        resolved = path if path.is_absolute() else repo_root / path
        if not _is_sha256(sha256):
            problems.append(f"{label}: original source sha256 mismatch")
            continue
        if not resolved.is_file():
            # Original run paths are provenance. Required packaged copies have
            # already been checked and make the bundle independently replayable.
            continue
        if file_sha256(resolved) != sha256.upper():
            problems.append(f"{label}: original source sha256 mismatch")


def _resolve_child(
    root: Path,
    relpath: str,
    problems: list[str],
    *,
    label: str,
) -> Path | None:
    path = Path(relpath)
    if not relpath or path.is_absolute() or ".." in path.parts:
        problems.append(f"{label}: artifact path must be bundle-relative")
        return None
    resolved = (root / path).resolve(strict=False)
    try:
        resolved.relative_to(root.resolve(strict=False))
    except ValueError:
        problems.append(f"{label}: artifact path escapes bundle")
        return None
    return resolved


def _append_expected_diff_problems(
    expected_summary: Path,
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    try:
        rows = read_tsv_required(expected_summary, EXPECTED_DIFF_SUMMARY_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"expected_diff_summary: {exc}")
        return
    if len(rows) != 1:
        problems.append("expected_diff_summary must have exactly one row")
        return
    row = rows[0]
    accepted_count = optional_int(payload.get("accepted_backfill_count", ""))
    if row.get("schema_version") != "quant_matrix_version_expected_diff_summary":
        problems.append("expected_diff_summary schema_version mismatch")
    if row.get("acceptance_status") != "pass":
        problems.append("expected_diff_summary acceptance_status mismatch")
    if optional_int(row.get("written_backfill_count", "")) != accepted_count:
        problems.append("expected_diff_summary written count mismatch")
    if optional_int(row.get("unused_expected_diff_count", "")) != 0:
        problems.append("expected_diff_summary unused count mismatch")


def _append_cell_provenance_problems(
    cell_provenance: Path,
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    try:
        rows = read_tsv_required(cell_provenance, CELL_PROVENANCE_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"cell_provenance: {exc}")
        return
    accepted_count = sum(
        1 for row in rows if row.get("cell_status") == "accepted_backfill"
    )
    if accepted_count != optional_int(payload.get("accepted_backfill_count", "")):
        problems.append("cell_provenance accepted_backfill_count mismatch")


def _append_fixture_contract_artifact_problems(
    artifact_paths: Mapping[str, Path],
    payload: Mapping[str, Any],
    problems: list[str],
    *,
    summary_label: str,
    fixture_label: str,
    count_column: str,
    count_value: str,
    expected_count: int | None,
) -> None:
    summary_path = artifact_paths.get(summary_label)
    fixture_path = artifact_paths.get(fixture_label)
    if summary_path is None:
        problems.append(f"{_contract_label(summary_label)}: artifact missing")
        return
    if fixture_path is None:
        problems.append(f"{_contract_label(fixture_label)}: artifact missing")
        return
    contract_problems = validate_fixture_contract(summary_path, fixture_path)
    for problem in contract_problems:
        label = (
            _contract_label(fixture_label)
            if "minimal fixture" in problem
            else _contract_label(summary_label)
        )
        problems.append(f"{label}: {problem}")
    if contract_problems:
        return
    try:
        contract = _read_json_object(summary_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        problems.append(f"{summary_label}: {exc}")
        return
    counts = contract.get("counts")
    if not isinstance(counts, dict):
        problems.append(f"{summary_label}: counts missing")
        return
    raw_column_counts = counts.get(count_column)
    if not isinstance(raw_column_counts, dict):
        problems.append(f"{summary_label}: {count_column} counts missing")
        return
    observed = optional_int(raw_column_counts.get(count_value, ""))
    if expected_count is not None and observed != expected_count:
        problems.append(f"{summary_label}: {count_value} count mismatch")
    if count_column == "report_authority":
        source_row_count = optional_int(contract.get("source_row_count", ""))
        if observed != source_row_count:
            problems.append(f"{summary_label}: review_only row count mismatch")
    if payload.get("may_promote_default_quant_matrix") is not False:
        problems.append(f"{summary_label}: payload promotion flag must stay false")


def _contract_label(label: str) -> str:
    return {
        "cell_provenance_summary": "cell_provenance summary",
        "cell_provenance_minimal_fixture": "cell_provenance minimal fixture",
        "review_rows_summary": "review rows summary",
        "review_rows_minimal_fixture": "review rows minimal fixture",
    }.get(label, label)


def _append_readiness_problems(readiness: Path, problems: list[str]) -> None:
    try:
        payload = json.loads(readiness.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"readiness summary: {exc}")
        return
    if not isinstance(payload, dict):
        problems.append("readiness summary must be an object")
        return
    if payload.get("readiness_label") != "contract_ready_science_inconclusive":
        problems.append("readiness summary label mismatch")
    if payload.get("production_ready") is not False:
        problems.append("readiness summary production_ready must be false")
    if payload.get("may_promote_default_quant_matrix") is not False:
        problems.append("readiness may_promote_default_quant_matrix must be false")


def _repo_relpath(path: Path, *, repo_root: Path) -> str:
    return (
        path.resolve(strict=False)
        .relative_to(repo_root.resolve(strict=False))
        .as_posix()
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check_only:
        summary_json = args.summary_json or (
            args.output_dir / "quant_matrix_real_bundle_summary.json"
        )
        problems = validate_quant_matrix_real_bundle(
            summary_json=summary_json,
            repo_root=args.repo_root,
            expected_source_run_id=args.expected_source_run_id,
            expected_downstream_scope=args.expected_downstream_scope,
            expected_accepted_backfill_count=args.expected_accepted_backfill_count,
        )
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"real_bundle_summary_json: {summary_json}")
        print("real_bundle_status: pass")
        return 0
    try:
        outputs = build_quant_matrix_real_bundle(
            source_run_dir=args.source_run_dir,
            output_dir=args.output_dir,
            repo_root=args.repo_root,
            downstream_scope=args.downstream_scope,
            rendered_review_dir=args.rendered_review_dir,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run-dir", type=Path, default=DEFAULT_SOURCE_RUN_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--downstream-scope", default=DEFAULT_DOWNSTREAM_SCOPE)
    parser.add_argument("--rendered-review-dir", type=Path)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--summary-json", type=Path)
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
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
