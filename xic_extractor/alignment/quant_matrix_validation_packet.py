from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from csv import DictReader
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment.quant_matrix_downstream_impact import (
    validate_quant_matrix_downstream_impact_smoke,
)
from xic_extractor.tabular_io import file_sha256, read_tsv_required, write_tsv

VALIDATION_EVIDENCE_SCHEMA = "quant_matrix_validation_evidence_v1"
VALIDATION_PACKET_SUMMARY_SCHEMA = "quant_matrix_validation_evidence_packet_summary_v1"
PROMOTION_READINESS_SCHEMA = "quant_matrix_promotion_readiness_v1"

VALIDATION_EVIDENCE_ROW_COLUMNS = (
    "schema_version",
    "tier",
    "status",
    "artifact_path",
    "artifact_sha256",
    "source_artifact_path",
    "source_artifact_sha256",
    "cohort_id",
    "raw_run_count",
    "oracle_packet_id",
    "review_packet_id",
    "downstream_scope",
    "evidence_note",
)

VALIDATION_EVIDENCE_ALLOWED_TIERS = (
    "full_cohort",
    "large_cohort_validation",
    "heldout_oracle",
    "manual_review_oracle",
    "downstream_impact_smoke",
    "focused_tests",
    "fixture_cohort_smoke",
)

REQUIRED_SCIENCE_EVIDENCE = {
    "large_cohort_validation": ("full_cohort", "large_cohort_validation"),
    "heldout_oracle_or_manual_review": (
        "heldout_oracle",
        "manual_review_oracle",
    ),
    "downstream_impact_smoke": ("downstream_impact_smoke",),
}

TIER_REQUIRED_METADATA = {
    "full_cohort": ("cohort_id", "raw_run_count"),
    "large_cohort_validation": ("cohort_id",),
    "heldout_oracle": ("oracle_packet_id",),
    "manual_review_oracle": ("review_packet_id",),
    "downstream_impact_smoke": ("downstream_scope",),
}


@dataclass(frozen=True)
class ValidationEvidenceArtifact:
    tier: str
    status: str
    source_artifact: Path
    cohort_id: str = ""
    raw_run_count: int | None = None
    oracle_packet_id: str = ""
    review_packet_id: str = ""
    downstream_scope: str = ""
    evidence_note: str = ""


def build_quant_matrix_validation_evidence_packet(
    *,
    output_dir: Path,
    evidence_artifacts: Sequence[ValidationEvidenceArtifact],
    packet_id: str,
    requested_readiness_label: str = "production_ready",
    source_root: Path | None = None,
) -> Mapping[str, Path]:
    rows = _evidence_rows(
        output_dir=output_dir,
        evidence_artifacts=evidence_artifacts,
        source_root=source_root,
    )
    status_by_requirement = _required_science_status(rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_json = output_dir / "quant_matrix_validation_evidence_v1.json"
    evidence_rows_tsv = output_dir / "quant_matrix_validation_evidence_rows.tsv"
    summary_json = output_dir / "quant_matrix_validation_evidence_summary.json"

    evidence_payload: dict[str, Any] = {
        "schema_version": VALIDATION_EVIDENCE_SCHEMA,
        "packet_id": packet_id,
        "requested_readiness_label": requested_readiness_label,
        "read_only": True,
        "write_authority": False,
        "evidence": rows,
        "required_science_evidence_status": status_by_requirement,
        "authority_statement": (
            "Validation packet only: artifact-bound evidence for promotion "
            "readiness; does not mutate ProductWriter, quant matrix defaults, "
            "workbook, GUI, selected peaks, selected areas, or counted detections."
        ),
    }
    evidence_json.write_text(
        json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_tsv(
        evidence_rows_tsv,
        rows,
        VALIDATION_EVIDENCE_ROW_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )

    summary = {
        "schema_version": VALIDATION_PACKET_SUMMARY_SCHEMA,
        "validation_evidence_schema": VALIDATION_EVIDENCE_SCHEMA,
        "packet_id": packet_id,
        "requested_readiness_label": requested_readiness_label,
        "read_only": True,
        "write_authority": False,
        "evidence_row_count": len(rows),
        "required_science_evidence_status": status_by_requirement,
        "missing_science_evidence": [
            requirement
            for requirement, status in status_by_requirement.items()
            if status == "missing"
        ],
        "failed_science_evidence": [
            requirement
            for requirement, status in status_by_requirement.items()
            if status == "fail"
        ],
        "validation_evidence_json": evidence_json.relative_to(output_dir).as_posix(),
        "validation_evidence_json_sha256": file_sha256(evidence_json),
        "validation_evidence_rows_tsv": (
            evidence_rows_tsv.relative_to(output_dir).as_posix()
        ),
        "validation_evidence_rows_tsv_sha256": file_sha256(evidence_rows_tsv),
    }
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "validation_evidence_json": evidence_json,
        "validation_evidence_rows_tsv": evidence_rows_tsv,
        "summary_json": summary_json,
    }


def validate_quant_matrix_validation_evidence_packet(
    validation_evidence_json: Path,
    *,
    source_root: Path | None = None,
    cell_provenance_contract_summary: Path | None = None,
    cell_provenance_minimal_fixture: Path | None = None,
) -> list[str]:
    problems: list[str] = []
    try:
        payload = json.loads(validation_evidence_json.read_text(encoding="utf-8"))
    except OSError as exc:
        return [str(exc)]
    except json.JSONDecodeError as exc:
        return [f"{validation_evidence_json}: invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return [f"{validation_evidence_json}: expected JSON object"]
    if payload.get("schema_version") != VALIDATION_EVIDENCE_SCHEMA:
        problems.append("schema_version must be quant_matrix_validation_evidence_v1")
    if payload.get("read_only") is not True:
        problems.append("read_only must be true")
    if payload.get("write_authority") is not False:
        problems.append("write_authority must be false")
    rows = payload.get("evidence")
    if not isinstance(rows, list):
        problems.append("evidence must be a list")
        return problems

    seen_tiers: set[str] = set()
    base_dir = validation_evidence_json.parent
    for index, raw_row in enumerate(rows, start=1):
        if not isinstance(raw_row, dict):
            problems.append(f"row {index}: expected object")
            continue
        row = {str(key): value for key, value in raw_row.items()}
        tier = _text(row.get("tier"))
        status = _text(row.get("status"))
        if tier in seen_tiers:
            problems.append(f"{tier}: duplicate tier")
        seen_tiers.add(tier)
        if tier not in VALIDATION_EVIDENCE_ALLOWED_TIERS:
            problems.append(f"{tier or f'row {index}'}: unsupported tier")
        if status not in {"pass", "fail"}:
            problems.append(f"{tier or f'row {index}'}: unsupported status={status}")
        for field in TIER_REQUIRED_METADATA.get(tier, ()):
            if not _text(row.get(field)):
                problems.append(f"{tier}: missing {field}")
        _append_artifact_binding_problems(
            row,
            base_dir=base_dir,
            problems=problems,
            label=tier or f"row {index}",
            source_root=source_root,
            cell_provenance_contract_summary=cell_provenance_contract_summary,
            cell_provenance_minimal_fixture=cell_provenance_minimal_fixture,
        )
        _append_source_binding_problems(
            row,
            problems,
            label=tier or f"row {index}",
            validation_evidence_json=validation_evidence_json,
            source_root=source_root,
        )
    normalized_rows = [
        {str(key): _text(value) for key, value in row.items()}
        for row in rows
        if isinstance(row, dict)
    ]
    status_by_requirement = _required_science_status(normalized_rows)
    if payload.get("required_science_evidence_status") != status_by_requirement:
        problems.append("required_science_evidence_status mismatch")
    _append_rows_tsv_problems(validation_evidence_json, normalized_rows, problems)
    _append_summary_problems(
        validation_evidence_json,
        status_by_requirement=status_by_requirement,
        problems=problems,
    )
    _append_readiness_fixture_problems(
        validation_evidence_json,
        status_by_requirement=status_by_requirement,
        status_by_tier={
            row["tier"]: row["status"]
            for row in normalized_rows
            if row.get("tier") and row.get("status")
        },
        problems=problems,
    )
    return problems


def _evidence_rows(
    *,
    output_dir: Path,
    evidence_artifacts: Sequence[ValidationEvidenceArtifact],
    source_root: Path | None,
) -> list[dict[str, str]]:
    seen_tiers: set[str] = set()
    rows: list[dict[str, str]] = []
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for artifact in evidence_artifacts:
        source = _resolve_source_for_build(artifact.source_artifact, source_root)
        _validate_artifact_request(artifact, seen_tiers, source=source)
        source_sha256 = file_sha256(source)
        copied = _copy_artifact(source, artifacts_dir, artifact.tier)
        row = {
            "schema_version": VALIDATION_EVIDENCE_SCHEMA,
            "tier": artifact.tier,
            "status": artifact.status,
            "artifact_path": copied.relative_to(output_dir).as_posix(),
            "artifact_sha256": file_sha256(copied),
            "source_artifact_path": _source_artifact_path_value(
                source,
                source_root,
            ),
            "source_artifact_sha256": source_sha256,
            "cohort_id": artifact.cohort_id,
            "raw_run_count": (
                str(artifact.raw_run_count)
                if artifact.raw_run_count is not None
                else ""
            ),
            "oracle_packet_id": artifact.oracle_packet_id,
            "review_packet_id": artifact.review_packet_id,
            "downstream_scope": artifact.downstream_scope,
            "evidence_note": artifact.evidence_note,
        }
        rows.append(row)
    return rows


def _validate_artifact_request(
    artifact: ValidationEvidenceArtifact,
    seen_tiers: set[str],
    *,
    source: Path,
) -> None:
    if artifact.tier in seen_tiers:
        raise ValueError(f"duplicate validation tier: {artifact.tier}")
    seen_tiers.add(artifact.tier)
    if artifact.tier not in VALIDATION_EVIDENCE_ALLOWED_TIERS:
        raise ValueError(f"unsupported validation tier: {artifact.tier}")
    if artifact.status not in {"pass", "fail"}:
        raise ValueError(f"unsupported validation status: {artifact.status}")
    if not source.is_file():
        raise FileNotFoundError(str(source))
    for field in TIER_REQUIRED_METADATA.get(artifact.tier, ()):
        value = getattr(artifact, field)
        if value in ("", None):
            raise ValueError(f"{artifact.tier}: missing {field}")
    if artifact.tier == "full_cohort" and (
        artifact.raw_run_count is None or artifact.raw_run_count < 85
    ):
        raise ValueError("full_cohort requires raw_run_count >= 85")


def _copy_artifact(source: Path, artifacts_dir: Path, tier: str) -> Path:
    destination = artifacts_dir / f"{_safe_filename(tier)}__{source.name}"
    if source.resolve(strict=True) != destination.resolve(strict=False):
        shutil.copy2(source, destination)
    if tier == "downstream_impact_smoke":
        _copy_downstream_impact_bundle(source, destination)
    return destination


def _copy_downstream_impact_bundle(source_summary: Path, copied_summary: Path) -> None:
    try:
        payload = json.loads(source_summary.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        message = f"{source_summary}: invalid downstream summary: {exc}"
        raise ValueError(message) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{source_summary}: downstream summary must be an object")
    row_metrics = _text(payload.get("row_metrics_tsv"))
    if not row_metrics:
        raise ValueError(f"{source_summary}: row_metrics_tsv is missing")
    row_path = Path(row_metrics)
    if row_path.is_absolute() or ".." in row_path.parts:
        raise ValueError(
            f"{source_summary}: row_metrics_tsv must be a relative child path",
        )
    source_row_metrics = (source_summary.parent / row_path).resolve(strict=False)
    copied_row_metrics = (copied_summary.parent / row_path).resolve(strict=False)
    try:
        copied_row_metrics.relative_to(copied_summary.parent.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(
            f"{source_summary}: row_metrics_tsv escapes copied packet",
        ) from exc
    if not source_row_metrics.is_file():
        raise FileNotFoundError(str(source_row_metrics))
    copied_row_metrics.parent.mkdir(parents=True, exist_ok=True)
    if source_row_metrics.resolve(strict=True) != copied_row_metrics.resolve(
        strict=False,
    ):
        shutil.copy2(source_row_metrics, copied_row_metrics)


def _required_science_status(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, str]:
    status_by_tier = {row["tier"]: row["status"] for row in rows}
    result: dict[str, str] = {}
    for requirement, tiers in REQUIRED_SCIENCE_EVIDENCE.items():
        values = [status_by_tier[tier] for tier in tiers if tier in status_by_tier]
        if any(value == "pass" for value in values):
            result[requirement] = "pass"
        elif any(value == "fail" for value in values):
            result[requirement] = "fail"
        else:
            result[requirement] = "missing"
    return result


def _append_rows_tsv_problems(
    validation_evidence_json: Path,
    json_rows: Sequence[Mapping[str, str]],
    problems: list[str],
) -> None:
    rows_tsv = (
        validation_evidence_json.parent / "quant_matrix_validation_evidence_rows.tsv"
    )
    if not rows_tsv.is_file():
        problems.append("quant_matrix_validation_evidence_rows.tsv is missing")
        return
    try:
        tsv_rows = read_tsv_required(rows_tsv, VALIDATION_EVIDENCE_ROW_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"{rows_tsv.name}: {exc}")
        return
    normalized_json_rows = [
        {column: row.get(column, "") for column in VALIDATION_EVIDENCE_ROW_COLUMNS}
        for row in json_rows
    ]
    normalized_tsv_rows = [
        {column: row.get(column, "") for column in VALIDATION_EVIDENCE_ROW_COLUMNS}
        for row in tsv_rows
    ]
    if normalized_tsv_rows != normalized_json_rows:
        problems.append("quant_matrix_validation_evidence_rows.tsv mismatch")


def _append_summary_problems(
    validation_evidence_json: Path,
    *,
    status_by_requirement: Mapping[str, str],
    problems: list[str],
) -> None:
    summary_json = validation_evidence_json.parent / (
        "quant_matrix_validation_evidence_summary.json"
    )
    if not summary_json.is_file():
        problems.append("quant_matrix_validation_evidence_summary.json is missing")
        return
    try:
        summary = json.loads(summary_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"{summary_json.name}: {exc}")
        return
    if not isinstance(summary, dict):
        problems.append(f"{summary_json.name}: expected JSON object")
        return
    if summary.get("schema_version") != VALIDATION_PACKET_SUMMARY_SCHEMA:
        problems.append("summary schema_version mismatch")
    if summary.get("validation_evidence_schema") != VALIDATION_EVIDENCE_SCHEMA:
        problems.append("summary validation_evidence_schema mismatch")
    if summary.get("read_only") is not True:
        problems.append("summary read_only must be true")
    if summary.get("write_authority") is not False:
        problems.append("summary write_authority must be false")
    expected_missing = [
        requirement
        for requirement, status in status_by_requirement.items()
        if status == "missing"
    ]
    expected_failed = [
        requirement
        for requirement, status in status_by_requirement.items()
        if status == "fail"
    ]
    if summary.get("required_science_evidence_status") != dict(
        status_by_requirement,
    ):
        problems.append("summary required_science_evidence_status mismatch")
    if summary.get("missing_science_evidence") != expected_missing:
        problems.append("summary missing_science_evidence mismatch")
    if summary.get("failed_science_evidence") != expected_failed:
        problems.append("summary failed_science_evidence mismatch")
    _append_summary_hash_problem(
        summary,
        field="validation_evidence_json",
        hash_field="validation_evidence_json_sha256",
        packet_dir=validation_evidence_json.parent,
        expected_path=validation_evidence_json,
        problems=problems,
    )
    _append_summary_hash_problem(
        summary,
        field="validation_evidence_rows_tsv",
        hash_field="validation_evidence_rows_tsv_sha256",
        packet_dir=validation_evidence_json.parent,
        expected_path=(
            validation_evidence_json.parent
            / "quant_matrix_validation_evidence_rows.tsv"
        ),
        problems=problems,
    )


def _append_summary_hash_problem(
    summary: Mapping[str, object],
    *,
    field: str,
    hash_field: str,
    packet_dir: Path,
    expected_path: Path,
    problems: list[str],
) -> None:
    relpath = _text(summary.get(field))
    expected_hash = _text(summary.get(hash_field))
    if not relpath:
        problems.append(f"summary {field} is missing")
        return
    if Path(relpath).is_absolute() or ".." in Path(relpath).parts:
        problems.append(f"summary {field} must be a packet-relative child path")
        return
    resolved = (packet_dir / relpath).resolve(strict=False)
    if resolved != expected_path.resolve(strict=False):
        problems.append(f"summary {field} points to unexpected artifact")
    if not resolved.is_file():
        problems.append(f"summary {field} does not exist")
        return
    if not _is_sha256(expected_hash):
        problems.append(f"summary {hash_field} must be 64-hex")
    elif file_sha256(resolved) != expected_hash.upper():
        problems.append(f"summary {hash_field} mismatch")


def _append_readiness_fixture_problems(
    validation_evidence_json: Path,
    *,
    status_by_requirement: Mapping[str, str],
    status_by_tier: Mapping[str, str],
    problems: list[str],
) -> None:
    fixture_dir = validation_evidence_json.parent / "readiness_integration_fixture"
    if not fixture_dir.exists():
        return
    readiness_summary = fixture_dir / "readiness" / (
        "quant_matrix_promotion_readiness_summary.json"
    )
    readiness_checks = fixture_dir / "readiness" / (
        "quant_matrix_promotion_readiness_checks.tsv"
    )
    if not readiness_summary.is_file():
        problems.append("readiness fixture summary is missing")
        return
    if not readiness_checks.is_file():
        problems.append("readiness fixture checks TSV is missing")
        return
    inputs_dir = fixture_dir / "inputs"
    fixture_inputs = {
        "expected_diff_summary_tsv": inputs_dir / "expected_diff_summary.tsv",
        "cell_provenance_tsv": inputs_dir / "cell_provenance.tsv",
        "row_summary_tsv": inputs_dir / "row_summary.tsv",
        "review_summary_json": inputs_dir / "quant_matrix_review_summary.json",
    }
    for label, path in fixture_inputs.items():
        if not path.is_file():
            problems.append(f"readiness fixture {label} is missing")
            return
    _append_readiness_fixture_rerun_problems(
        validation_evidence_json,
        fixture_inputs=fixture_inputs,
        readiness_summary=readiness_summary,
        readiness_checks=readiness_checks,
        problems=problems,
    )
    try:
        summary = json.loads(readiness_summary.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"{readiness_summary.name}: {exc}")
        return
    if not isinstance(summary, dict):
        problems.append("readiness fixture summary must be a JSON object")
        return
    expected_missing = [
        requirement
        for requirement, status in status_by_requirement.items()
        if status == "missing"
    ]
    all_required_pass = all(
        status == "pass" for status in status_by_requirement.values()
    )
    if summary.get("schema_version") != PROMOTION_READINESS_SCHEMA:
        problems.append("readiness fixture schema_version mismatch")
    if summary.get("validation_tiers") != dict(status_by_tier):
        problems.append("readiness fixture validation_tiers mismatch")
    if summary.get("missing_science_evidence") != expected_missing:
        problems.append("readiness fixture missing_science_evidence mismatch")
    if summary.get("production_ready") is not all_required_pass:
        problems.append("readiness fixture production_ready mismatch")
    if summary.get("may_promote_default_quant_matrix") is not all_required_pass:
        problems.append("readiness fixture may_promote_default_quant_matrix mismatch")
    _append_readiness_checks_problems(
        readiness_checks,
        status_by_requirement=status_by_requirement,
        problems=problems,
    )


def _append_readiness_fixture_rerun_problems(
    validation_evidence_json: Path,
    *,
    fixture_inputs: Mapping[str, Path],
    readiness_summary: Path,
    readiness_checks: Path,
    problems: list[str],
) -> None:
    from xic_extractor.alignment.quant_matrix_promotion import (
        evaluate_quant_matrix_promotion_readiness,
    )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            outputs = evaluate_quant_matrix_promotion_readiness(
                expected_diff_summary_tsv=fixture_inputs["expected_diff_summary_tsv"],
                cell_provenance_tsv=fixture_inputs["cell_provenance_tsv"],
                row_summary_tsv=fixture_inputs["row_summary_tsv"],
                review_summary_json=fixture_inputs["review_summary_json"],
                validation_evidence_json=validation_evidence_json,
                output_dir=Path(tmpdir),
            )
            expected_summary = _json_without_input_artifacts(outputs["summary_json"])
            actual_summary = _json_without_input_artifacts(readiness_summary)
            if actual_summary != expected_summary:
                problems.append("readiness fixture summary is stale")
            expected_checks = outputs["checks_tsv"].read_text(encoding="utf-8")
            actual_checks = readiness_checks.read_text(encoding="utf-8")
            if actual_checks != expected_checks:
                problems.append("readiness fixture checks TSV is stale")
    except (OSError, ValueError) as exc:
        problems.append(f"readiness fixture rerun failed: {exc}")


def _json_without_input_artifacts(path: Path) -> object:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = dict(data)
        data.pop("input_artifacts", None)
    return data


def _append_readiness_checks_problems(
    readiness_checks: Path,
    *,
    status_by_requirement: Mapping[str, str],
    problems: list[str],
) -> None:
    expected_check_ids = {
        "large_cohort_validation": "science_large_cohort",
        "heldout_oracle_or_manual_review": "science_heldout_oracle_or_manual_review",
        "downstream_impact_smoke": "science_downstream_impact_smoke",
    }
    try:
        with readiness_checks.open(newline="", encoding="utf-8") as handle:
            rows = list(DictReader(handle, delimiter="\t"))
    except OSError as exc:
        problems.append(f"{readiness_checks.name}: {exc}")
        return
    status_by_check = {row.get("check_id", ""): row.get("status", "") for row in rows}
    for requirement, check_id in expected_check_ids.items():
        if status_by_check.get(check_id) != status_by_requirement[requirement]:
            problems.append(f"readiness fixture {check_id} status mismatch")


def _append_artifact_binding_problems(
    row: Mapping[str, object],
    *,
    base_dir: Path,
    problems: list[str],
    label: str,
    source_root: Path | None,
    cell_provenance_contract_summary: Path | None,
    cell_provenance_minimal_fixture: Path | None,
) -> None:
    relpath = _text(row.get("artifact_path"))
    expected_sha256 = _text(row.get("artifact_sha256"))
    if not relpath:
        problems.append(f"{label}: missing artifact_path")
        return
    if Path(relpath).is_absolute() or ".." in Path(relpath).parts:
        problems.append(f"{label}: artifact_path must be a relative child path")
        return
    artifact = (base_dir / relpath).resolve(strict=False)
    try:
        artifact.relative_to(base_dir.resolve(strict=False))
    except ValueError:
        problems.append(f"{label}: artifact_path escapes validation packet")
        return
    if not artifact.is_file():
        problems.append(f"{label}: artifact_path does not exist")
        return
    if not _is_sha256(expected_sha256):
        problems.append(f"{label}: artifact_sha256 must be 64-hex")
    elif file_sha256(artifact) != expected_sha256.upper():
        problems.append(f"{label}: artifact_sha256 mismatch")
    if label == "downstream_impact_smoke":
        problems.extend(
            validate_quant_matrix_downstream_impact_smoke(
                artifact,
                artifact_root=source_root,
                cell_provenance_contract_summary=(
                    cell_provenance_contract_summary
                ),
                cell_provenance_minimal_fixture=(
                    cell_provenance_minimal_fixture
                ),
            )
        )


def _append_source_binding_problems(
    row: Mapping[str, object],
    problems: list[str],
    *,
    label: str,
    validation_evidence_json: Path,
    source_root: Path | None,
) -> None:
    source = _text(row.get("source_artifact_path"))
    expected_sha256 = _text(row.get("source_artifact_sha256"))
    if not source:
        problems.append(f"{label}: missing source_artifact_path")
        return
    source_path = _resolve_source_for_validation(
        Path(source),
        validation_evidence_json=validation_evidence_json,
        source_root=source_root,
    )
    if not source_path.is_file():
        problems.append(f"{label}: source_artifact_path does not exist")
        return
    if not _is_sha256(expected_sha256):
        problems.append(f"{label}: source_artifact_sha256 must be 64-hex")
    elif file_sha256(source_path) != expected_sha256.upper():
        problems.append(f"{label}: source_artifact_sha256 mismatch")


def _safe_filename(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)


def _resolve_source_for_build(source: Path, source_root: Path | None) -> Path:
    if source.is_absolute() or source_root is None:
        return source
    return source_root / source


def _source_artifact_path_value(source: Path, source_root: Path | None) -> str:
    resolved = source.resolve(strict=True)
    if source_root is not None:
        try:
            return resolved.relative_to(source_root.resolve(strict=True)).as_posix()
        except ValueError:
            pass
    return str(resolved)


def _resolve_source_for_validation(
    source: Path,
    *,
    validation_evidence_json: Path,
    source_root: Path | None,
) -> Path:
    if source.is_absolute():
        return source
    root = source_root or _infer_repo_root(validation_evidence_json)
    if root is None:
        return source
    return root / source


def _infer_repo_root(path: Path) -> Path | None:
    resolved = path.resolve(strict=False)
    for parent in (resolved, *resolved.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "AGENTS.md").is_file():
            return parent
    return None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )
