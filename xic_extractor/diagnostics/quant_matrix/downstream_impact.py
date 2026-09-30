from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xic_extractor.alignment.quant_matrix_version import (
    CELL_PROVENANCE_COLUMNS,
    CELL_PROVENANCE_SCHEMA,
    ROW_SUMMARY_COLUMNS,
    ROW_SUMMARY_SCHEMA,
)
from xic_extractor.diagnostics.quant_matrix.fixture_contract import (
    validate_fixture_contract,
)
from xic_extractor.tabular_io import (
    file_sha256,
    numeric_equal,
    optional_float,
    optional_int,
    read_tsv_required,
    read_tsv_with_header,
    write_tsv,
)

DOWNSTREAM_IMPACT_SCHEMA = "quant_matrix_downstream_impact_smoke"
DOWNSTREAM_IMPACT_ROW_SCHEMA = "quant_matrix_downstream_impact_row"
DOWNSTREAM_IMPACT_BUNDLE_KINDS = (
    "contract_fixture",
    "real_quant_matrix_version",
)

DOWNSTREAM_IMPACT_ROW_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "sample_count",
    "detected_count",
    "accepted_backfilled_count",
    "quant_available_count",
    "detected_only_missing_count",
    "quant_matrix_missing_count",
    "missing_cell_reduction_count",
    "backfill_fraction",
    "prevalence_flags",
)


def build_quant_matrix_downstream_impact_smoke(
    *,
    quant_matrix_tsv: Path,
    cell_provenance_tsv: Path,
    row_summary_tsv: Path,
    output_dir: Path,
    downstream_scope: str,
    bundle_kind: str,
) -> Mapping[str, Path]:
    if bundle_kind not in DOWNSTREAM_IMPACT_BUNDLE_KINDS:
        raise ValueError(f"unsupported bundle_kind: {bundle_kind}")
    if not downstream_scope:
        raise ValueError("downstream_scope is required")

    matrix_header, matrix_rows = read_tsv_with_header(quant_matrix_tsv)
    cell_rows = read_tsv_required(cell_provenance_tsv, CELL_PROVENANCE_COLUMNS)
    row_summary_rows = read_tsv_required(row_summary_tsv, ROW_SUMMARY_COLUMNS)

    sample_columns = _sample_columns(matrix_header)
    row_metrics, metrics, problems = _evaluate_downstream_impact(
        sample_columns=sample_columns,
        quant_matrix_rows=matrix_rows,
        cell_provenance_rows=cell_rows,
        row_summary_rows=row_summary_rows,
    )
    status = "pass" if not problems else "fail"

    output_dir.mkdir(parents=True, exist_ok=True)
    rows_tsv = output_dir / "quant_matrix_downstream_impact_rows.tsv"
    write_tsv(
        rows_tsv,
        row_metrics,
        DOWNSTREAM_IMPACT_ROW_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )

    summary_json = output_dir / "quant_matrix_downstream_impact_smoke.json"
    payload: dict[str, Any] = {
        "schema_version": DOWNSTREAM_IMPACT_SCHEMA,
        "downstream_scope": downstream_scope,
        "bundle_kind": bundle_kind,
        "status": status,
        "read_only": True,
        "write_authority": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "detected_claims_unchanged": True,
        "production_promotion_eligible": (
            status == "pass" and bundle_kind == "real_quant_matrix_version"
        ),
        "input_artifacts": {
            "quant_matrix_tsv": str(quant_matrix_tsv),
            "quant_matrix_sha256": file_sha256(quant_matrix_tsv),
            "cell_provenance_tsv": str(cell_provenance_tsv),
            "cell_provenance_sha256": file_sha256(cell_provenance_tsv),
            "row_summary_tsv": str(row_summary_tsv),
            "row_summary_sha256": file_sha256(row_summary_tsv),
        },
        "row_metrics_tsv": rows_tsv.name,
        "row_metrics_tsv_sha256": file_sha256(rows_tsv),
        "metrics": metrics,
        "problems": problems,
        "pass_conditions": {
            "accepted_backfill_values_present": (
                metrics["accepted_backfilled_cell_count"] > 0
            ),
            "missingness_not_worse": metrics["missingness_not_worse"],
            "missing_reduction_equals_accepted_backfills": (
                metrics["missing_cell_reduction_count"]
                == metrics["accepted_backfilled_cell_count"]
            ),
            "row_summary_matches_cell_provenance": (
                metrics["row_summary_matches_cell_provenance"]
            ),
            "every_non_empty_cell_has_provenance": (
                metrics["every_non_empty_cell_has_provenance"]
            ),
            "accepted_backfill_values_are_positive_numeric": (
                metrics["accepted_backfill_values_are_positive_numeric"]
            ),
        },
        "authority_statement": (
            "Downstream smoke evidence only: proves accepted Backfill values "
            "increase numeric matrix availability while preserving detected-only "
            "claims in sidecars. It does not mutate ProductWriter or grant write "
            "authority."
        ),
    }
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {"summary_json": summary_json, "rows_tsv": rows_tsv}


def validate_quant_matrix_downstream_impact_smoke(
    summary_json: Path,
    *,
    artifact_root: Path | None = None,
    cell_provenance_contract_summary: Path | None = None,
    cell_provenance_minimal_fixture: Path | None = None,
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
    if payload.get("schema_version") != DOWNSTREAM_IMPACT_SCHEMA:
        problems.append("downstream impact schema_version mismatch")
    if payload.get("status") != "pass":
        problems.append("downstream impact status must be pass")
    if payload.get("read_only") is not True:
        problems.append("downstream impact read_only must be true")
    if payload.get("write_authority") is not False:
        problems.append("downstream impact write_authority must be false")
    if payload.get("product_writer_changed") is not False:
        problems.append("downstream impact product_writer_changed must be false")
    if payload.get("default_quant_matrix_changed") is not False:
        problems.append("downstream impact default_quant_matrix_changed must be false")
    if payload.get("detected_claims_unchanged") is not True:
        problems.append("downstream impact detected_claims_unchanged must be true")
    if payload.get("bundle_kind") not in DOWNSTREAM_IMPACT_BUNDLE_KINDS:
        problems.append("downstream impact bundle_kind is unsupported")
    if payload.get("bundle_kind") != "real_quant_matrix_version":
        problems.append(
            "downstream impact bundle_kind must be real_quant_matrix_version",
        )
    if payload.get("production_promotion_eligible") is not True:
        problems.append("downstream impact production_promotion_eligible must be true")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        problems.append("downstream impact metrics must be an object")
    else:
        _append_metric_problems(metrics, problems)
    pass_conditions = payload.get("pass_conditions")
    if not isinstance(pass_conditions, dict):
        problems.append("downstream impact pass_conditions must be an object")
    else:
        failed = sorted(
            key for key, value in pass_conditions.items() if value is not True
        )
        if failed:
            problems.append(
                "downstream impact pass_conditions failed: " + ";".join(failed),
            )
    rows = _rows_tsv_binding_problems(summary_json, payload, problems)
    _append_input_artifact_problems(
        summary_json,
        payload,
        rows,
        problems,
        artifact_root=artifact_root,
        cell_provenance_contract_summary=cell_provenance_contract_summary,
        cell_provenance_minimal_fixture=cell_provenance_minimal_fixture,
    )
    return problems


def _evaluate_downstream_impact(
    *,
    sample_columns: Sequence[str],
    quant_matrix_rows: Sequence[Mapping[str, str]],
    cell_provenance_rows: Sequence[Mapping[str, str]],
    row_summary_rows: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, str]], dict[str, Any], list[str]]:
    problems: list[str] = []
    sample_count = len(sample_columns)
    if len(row_summary_rows) != len(quant_matrix_rows):
        problems.append("row_summary row count must match quant_matrix row count")
    row_summary_by_peak = _row_summary_by_peak(row_summary_rows, problems)
    matrix_by_peak = _matrix_by_peak(
        quant_matrix_rows,
        row_summary_rows,
        problems,
    )
    provenance_by_key = _cell_provenance_by_key(cell_provenance_rows, problems)
    computed_counts: dict[str, Counter[str]] = {
        peak: Counter() for peak in row_summary_by_peak
    }

    for key, row in provenance_by_key.items():
        peak_hypothesis_id, sample_stem = key
        if peak_hypothesis_id not in row_summary_by_peak:
            problems.append(f"{peak_hypothesis_id}/{sample_stem}: unknown peak")
            continue
        if sample_stem not in sample_columns:
            problems.append(f"{peak_hypothesis_id}/{sample_stem}: unknown sample")
            continue
        matrix_value = matrix_by_peak[peak_hypothesis_id].get(sample_stem, "")
        if not matrix_value:
            problems.append(
                f"{peak_hypothesis_id}/{sample_stem}: provenance without matrix value",
            )
            continue
        if not numeric_equal(matrix_value, row.get("matrix_value", "")):
            problems.append(f"{peak_hypothesis_id}/{sample_stem}: matrix value drift")
        status = row.get("cell_status", "")
        if row.get("schema_version") != CELL_PROVENANCE_SCHEMA:
            problems.append(f"{peak_hypothesis_id}/{sample_stem}: schema_version")
        if status == "detected":
            if row.get("write_authority") != "FALSE":
                problems.append(
                    f"{peak_hypothesis_id}/{sample_stem}: detected write authority",
                )
            computed_counts[peak_hypothesis_id]["detected"] += 1
        elif status == "accepted_backfill":
            if row.get("write_authority") != "TRUE":
                problems.append(
                    f"{peak_hypothesis_id}/{sample_stem}: backfill write authority",
                )
            if _positive_float(row.get("matrix_value", "")) is None:
                problems.append(
                    f"{peak_hypothesis_id}/{sample_stem}: accepted value not positive",
                )
            computed_counts[peak_hypothesis_id]["accepted_backfill"] += 1
        else:
            problems.append(
                f"{peak_hypothesis_id}/{sample_stem}: unsupported status={status}",
            )

    non_empty_keys = {
        (peak, sample)
        for peak, matrix_row in matrix_by_peak.items()
        for sample in sample_columns
        if matrix_row.get(sample, "")
    }
    if non_empty_keys != set(provenance_by_key):
        problems.append("every non-empty quant matrix cell must have provenance")

    row_metrics: list[dict[str, str]] = []
    detected_total = 0
    accepted_total = 0
    available_total = 0
    row_summary_matches = True
    for row in row_summary_rows:
        peak_hypothesis_id = row.get("peak_hypothesis_id", "")
        if row.get("schema_version") != ROW_SUMMARY_SCHEMA:
            problems.append(f"{peak_hypothesis_id}: row_summary schema_version")
        counts = computed_counts.get(peak_hypothesis_id, Counter())
        detected = counts["detected"]
        accepted = counts["accepted_backfill"]
        available = detected + accepted
        detected_total += detected
        accepted_total += accepted
        available_total += available
        row_values = {
            "detected_count": detected,
            "accepted_backfilled_count": accepted,
            "quant_available_count": available,
            "missing_count": sample_count - available,
        }
        for field, expected in row_values.items():
            if optional_int(row.get(field, "")) != expected:
                row_summary_matches = False
                problems.append(f"{peak_hypothesis_id}: {field} mismatch")
        backfill_fraction = 0.0 if available == 0 else accepted / available
        reported_fraction = optional_float(row.get("backfill_fraction", ""))
        if (
            reported_fraction is None
            or abs(reported_fraction - backfill_fraction) > 1e-6
        ):
            row_summary_matches = False
            problems.append(f"{peak_hypothesis_id}: backfill_fraction mismatch")
        row_metrics.append(
            {
                "schema_version": DOWNSTREAM_IMPACT_ROW_SCHEMA,
                "peak_hypothesis_id": peak_hypothesis_id,
                "sample_count": str(sample_count),
                "detected_count": str(detected),
                "accepted_backfilled_count": str(accepted),
                "quant_available_count": str(available),
                "detected_only_missing_count": str(sample_count - detected),
                "quant_matrix_missing_count": str(sample_count - available),
                "missing_cell_reduction_count": str(accepted),
                "backfill_fraction": f"{backfill_fraction:.6f}",
                "prevalence_flags": row.get("prevalence_flags", ""),
            }
        )

    total_possible = sample_count * len(row_summary_rows)
    detected_only_missing = total_possible - detected_total
    quant_matrix_missing = total_possible - available_total
    missing_reduction = detected_only_missing - quant_matrix_missing
    accepted_values_are_positive = accepted_total > 0 and not any(
        "accepted value not positive" in problem for problem in problems
    )
    metrics: dict[str, Any] = {
        "peak_count": len(row_summary_rows),
        "sample_count": sample_count,
        "detected_cell_count": detected_total,
        "accepted_backfilled_cell_count": accepted_total,
        "quant_available_cell_count": available_total,
        "detected_only_missing_cell_count": detected_only_missing,
        "quant_matrix_missing_cell_count": quant_matrix_missing,
        "missing_cell_reduction_count": missing_reduction,
        "missingness_not_worse": quant_matrix_missing <= detected_only_missing,
        "row_summary_matches_cell_provenance": row_summary_matches,
        "every_non_empty_cell_has_provenance": non_empty_keys == set(provenance_by_key),
        "accepted_backfill_values_are_positive_numeric": accepted_values_are_positive,
    }
    if accepted_total == 0:
        problems.append("downstream smoke requires at least one accepted Backfill cell")
    if missing_reduction != accepted_total:
        problems.append("missing reduction must equal accepted Backfill count")
    return row_metrics, metrics, problems


def _sample_columns(matrix_header: Sequence[str]) -> tuple[str, ...]:
    if len(matrix_header) < 3 or tuple(matrix_header[:2]) != ("Mz", "RT"):
        raise ValueError("quant matrix header must start with Mz, RT")
    return tuple(column for column in matrix_header if column not in {"Mz", "RT"})


def _row_summary_by_peak(
    rows: Sequence[Mapping[str, str]],
    problems: list[str],
) -> dict[str, Mapping[str, str]]:
    result: dict[str, Mapping[str, str]] = {}
    for row in rows:
        peak = row.get("peak_hypothesis_id", "")
        if not peak:
            problems.append("row_summary row missing peak_hypothesis_id")
            continue
        if peak in result:
            problems.append(f"{peak}: duplicate row_summary peak")
            continue
        result[peak] = row
    return result


def _matrix_by_peak(
    matrix_rows: Sequence[Mapping[str, str]],
    row_summary_rows: Sequence[Mapping[str, str]],
    problems: list[str],
) -> dict[str, Mapping[str, str]]:
    result: dict[str, Mapping[str, str]] = {}
    for index, matrix_row in enumerate(matrix_rows):
        if index >= len(row_summary_rows):
            problems.append("quant_matrix has more rows than row_summary")
            continue
        peak = row_summary_rows[index].get("peak_hypothesis_id", "")
        if peak:
            result[peak] = matrix_row
    return result


def _cell_provenance_by_key(
    rows: Sequence[Mapping[str, str]],
    problems: list[str],
) -> dict[tuple[str, str], Mapping[str, str]]:
    result: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        key = (row.get("peak_hypothesis_id", ""), row.get("sample_stem", ""))
        if not all(key):
            problems.append("cell_provenance row missing key")
            continue
        if key in result:
            problems.append(f"{key[0]}/{key[1]}: duplicate cell provenance")
            continue
        result[key] = row
    return result


def _positive_float(value: object) -> float | None:
    parsed = optional_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _append_metric_problems(metrics: Mapping[str, Any], problems: list[str]) -> None:
    required_bool_metrics = (
        "missingness_not_worse",
        "row_summary_matches_cell_provenance",
        "every_non_empty_cell_has_provenance",
        "accepted_backfill_values_are_positive_numeric",
    )
    for field in required_bool_metrics:
        if metrics.get(field) is not True:
            problems.append(f"downstream impact metric {field} must be true")
    accepted_count = optional_int(metrics.get("accepted_backfilled_cell_count", ""))
    missing_reduction = optional_int(metrics.get("missing_cell_reduction_count", ""))
    if accepted_count is None or accepted_count <= 0:
        problems.append("downstream impact accepted_backfilled_cell_count must be >0")
    if missing_reduction is None or missing_reduction != accepted_count:
        problems.append(
            "downstream impact missing_cell_reduction_count must equal "
            "accepted_backfilled_cell_count",
        )


def _rows_tsv_binding_problems(
    summary_json: Path,
    payload: Mapping[str, Any],
    problems: list[str],
) -> tuple[Mapping[str, str], ...] | None:
    relpath = str(payload.get("row_metrics_tsv", "")).strip()
    expected_sha256 = str(payload.get("row_metrics_tsv_sha256", "")).strip()
    if not relpath:
        problems.append("downstream impact row_metrics_tsv is missing")
        return None
    path = Path(relpath)
    if path.is_absolute() or ".." in path.parts:
        problems.append("downstream impact row_metrics_tsv must be packet-relative")
        return None
    rows_tsv = (summary_json.parent / path).resolve(strict=False)
    try:
        rows_tsv.relative_to(summary_json.parent.resolve(strict=False))
    except ValueError:
        problems.append("downstream impact row_metrics_tsv escapes packet")
        return None
    if not rows_tsv.is_file():
        problems.append("downstream impact row_metrics_tsv does not exist")
        return None
    try:
        rows = read_tsv_required(rows_tsv, DOWNSTREAM_IMPACT_ROW_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"downstream impact row_metrics_tsv invalid: {exc}")
        return None
    if not _is_sha256(expected_sha256):
        problems.append("downstream impact row_metrics_tsv_sha256 must be 64-hex")
    elif file_sha256(rows_tsv) != expected_sha256.upper():
        problems.append("downstream impact row_metrics_tsv_sha256 mismatch")
    return rows


def _append_input_artifact_problems(
    summary_json: Path,
    payload: Mapping[str, Any],
    rows: Sequence[Mapping[str, str]] | None,
    problems: list[str],
    *,
    artifact_root: Path | None,
    cell_provenance_contract_summary: Path | None,
    cell_provenance_minimal_fixture: Path | None,
) -> None:
    input_artifacts = payload.get("input_artifacts")
    if not isinstance(input_artifacts, dict):
        problems.append("downstream impact input_artifacts must be an object")
        return
    paths: dict[str, Path] = {}
    for path_field, hash_field in (
        ("quant_matrix_tsv", "quant_matrix_sha256"),
        ("cell_provenance_tsv", "cell_provenance_sha256"),
        ("row_summary_tsv", "row_summary_sha256"),
    ):
        path_value = str(input_artifacts.get(path_field, "")).strip()
        hash_value = str(input_artifacts.get(hash_field, "")).strip()
        if not path_value:
            problems.append(f"downstream impact input_artifacts.{path_field} missing")
            continue
        path = _resolve_input_artifact(
            summary_json,
            Path(path_value),
            artifact_root=artifact_root,
        )
        if path is None or not path.is_file():
            if (
                path_field == "cell_provenance_tsv"
                and cell_provenance_contract_summary is not None
            ):
                _append_externalized_cell_provenance_contract_problems(
                    summary_path=cell_provenance_contract_summary,
                    fixture_path=cell_provenance_minimal_fixture,
                    expected_source_sha256=hash_value,
                    problems=problems,
                )
                continue
            problems.append(
                f"downstream impact input_artifacts.{path_field} does not exist",
            )
            continue
        paths[path_field] = path
        if not _is_sha256(hash_value):
            problems.append(
                f"downstream impact input_artifacts.{hash_field} must be 64-hex",
            )
        elif file_sha256(path) != hash_value.upper():
            problems.append(
                f"downstream impact input_artifacts.{hash_field} mismatch",
            )
    if (
        set(paths)
        != {"quant_matrix_tsv", "cell_provenance_tsv", "row_summary_tsv"}
        or rows is None
    ):
        return
    try:
        matrix_header, matrix_rows = read_tsv_with_header(paths["quant_matrix_tsv"])
        recomputed_rows, recomputed_metrics, recomputed_problems = (
            _evaluate_downstream_impact(
                sample_columns=_sample_columns(matrix_header),
                quant_matrix_rows=matrix_rows,
                cell_provenance_rows=read_tsv_required(
                    paths["cell_provenance_tsv"],
                    CELL_PROVENANCE_COLUMNS,
                ),
                row_summary_rows=read_tsv_required(
                    paths["row_summary_tsv"],
                    ROW_SUMMARY_COLUMNS,
                ),
            )
        )
    except (OSError, ValueError) as exc:
        problems.append(f"downstream impact input artifact recompute failed: {exc}")
        return
    if recomputed_problems:
        problems.append(
            "downstream impact recompute problems: "
            + "; ".join(recomputed_problems[:5]),
        )
    normalized_rows = [
        {column: row.get(column, "") for column in DOWNSTREAM_IMPACT_ROW_COLUMNS}
        for row in rows
    ]
    if normalized_rows != recomputed_rows:
        problems.append("downstream impact row_metrics_tsv does not match inputs")
    if payload.get("metrics") != recomputed_metrics:
        problems.append("downstream impact metrics do not match inputs")


def _append_externalized_cell_provenance_contract_problems(
    *,
    summary_path: Path,
    fixture_path: Path | None,
    expected_source_sha256: str,
    problems: list[str],
) -> None:
    if not summary_path.is_file():
        problems.append("downstream impact cell_provenance summary does not exist")
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"downstream impact cell_provenance summary invalid: {exc}")
        return
    if not isinstance(summary, dict):
        problems.append("downstream impact cell_provenance summary must be an object")
        return
    if summary.get("schema_version") != "quant_matrix_fixture_contract":
        problems.append("downstream impact cell_provenance summary schema mismatch")
    if summary.get("source_sha256") != expected_source_sha256.upper():
        problems.append("downstream impact cell_provenance source_sha256 mismatch")
    resolved_fixture = fixture_path or _contract_fixture_path(summary_path, summary)
    if resolved_fixture is None:
        problems.append("downstream impact cell_provenance minimal fixture missing")
        return
    problems.extend(
        f"downstream impact cell_provenance contract: {problem}"
        for problem in validate_fixture_contract(summary_path, resolved_fixture)
    )


def _contract_fixture_path(
    summary_path: Path,
    summary: Mapping[str, Any],
) -> Path | None:
    fixture = summary.get("minimal_fixture")
    if not isinstance(fixture, dict):
        return None
    relpath = str(fixture.get("path", "")).strip()
    if not relpath:
        return None
    path = Path(relpath)
    if path.is_absolute() or ".." in path.parts:
        return None
    return summary_path.parent / path


def _resolve_input_artifact(
    summary_json: Path,
    path: Path,
    *,
    artifact_root: Path | None,
) -> Path | None:
    if path.is_absolute():
        return path
    root = _infer_repo_root(summary_json)
    candidates = [summary_json.parent / path]
    if artifact_root is not None:
        candidates.append(artifact_root / path)
    if root is not None:
        candidates.append(root / path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1] if candidates else None


def _infer_repo_root(path: Path) -> Path | None:
    resolved = path.resolve(strict=False)
    for parent in (resolved, *resolved.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "AGENTS.md").is_file():
            return parent
    return None


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )
