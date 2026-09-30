from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from xic_extractor.diagnostics.quant_matrix.downstream_impact import (
    validate_quant_matrix_downstream_impact_smoke,
)
from xic_extractor.diagnostics.quant_matrix.report import QUANT_MATRIX_REVIEW_SCHEMA
from xic_extractor.diagnostics.quant_matrix.validation_packet import (
    VALIDATION_EVIDENCE_SCHEMA,
)
from xic_extractor.diagnostics.quant_matrix.version import (
    CELL_PROVENANCE_COLUMNS,
    CELL_PROVENANCE_SCHEMA,
    EXPECTED_DIFF_SUMMARY_COLUMNS,
    EXPECTED_DIFF_SUMMARY_SCHEMA,
    ROW_SUMMARY_COLUMNS,
    ROW_SUMMARY_SCHEMA,
)
from xic_extractor.tabular_io import (
    file_sha256,
    optional_int,
    read_tsv_required,
    write_tsv,
)

PROMOTION_READINESS_SCHEMA = "quant_matrix_promotion_readiness"

PROMOTION_CHECK_COLUMNS = (
    "schema_version",
    "check_id",
    "category",
    "status",
    "severity",
    "evidence",
    "next_action",
)

_LARGE_COHORT_TIERS = {"full_cohort", "large_cohort_validation"}
_ORACLE_TIERS = {"heldout_oracle", "manual_review_oracle"}
_DOWNSTREAM_TIERS = {"downstream_impact_smoke"}
_REQUIRED_SCIENCE_EVIDENCE = {
    "large_cohort_validation": _LARGE_COHORT_TIERS,
    "heldout_oracle_or_manual_review": _ORACLE_TIERS,
    "downstream_impact_smoke": _DOWNSTREAM_TIERS,
}
_REQUIRED_SCIENCE_TIER_PROVENANCE = {
    "full_cohort": (
        "artifact_path",
        "artifact_sha256",
        "cohort_id",
        "raw_run_count",
    ),
    "large_cohort_validation": (
        "artifact_path",
        "artifact_sha256",
        "cohort_id",
    ),
    "heldout_oracle": (
        "artifact_path",
        "artifact_sha256",
        "oracle_packet_id",
    ),
    "manual_review_oracle": (
        "artifact_path",
        "artifact_sha256",
        "review_packet_id",
    ),
    "downstream_impact_smoke": (
        "artifact_path",
        "artifact_sha256",
        "downstream_scope",
    ),
}
_PROVENANCE_FIELDS = (
    "source_artifact_relpath",
    "source_artifact_sha256",
    "source_row_sha256",
    "manifest_sha256",
)


@dataclass(frozen=True)
class ValidationEvidenceParse:
    validation_tiers: dict[str, str]
    blockers: list[str]
    duplicate_tiers: list[str]
    invalid_status: list[str]
    artifact_problems: list[str]


def evaluate_quant_matrix_promotion_readiness(
    *,
    expected_diff_summary_tsv: Path,
    cell_provenance_tsv: Path,
    row_summary_tsv: Path,
    review_summary_json: Path,
    output_dir: Path,
    validation_evidence_json: Path | None = None,
    validation_artifact_root: Path | None = None,
    cell_provenance_contract_summary: Path | None = None,
    cell_provenance_minimal_fixture: Path | None = None,
) -> Mapping[str, Path]:
    expected_diff_rows = read_tsv_required(
        expected_diff_summary_tsv,
        EXPECTED_DIFF_SUMMARY_COLUMNS,
    )
    cell_rows = read_tsv_required(cell_provenance_tsv, CELL_PROVENANCE_COLUMNS)
    row_summary_rows = read_tsv_required(row_summary_tsv, ROW_SUMMARY_COLUMNS)
    review_summary = _read_json(review_summary_json)

    checks: list[dict[str, str]] = []
    blockers: list[str] = []
    _check_expected_diff_summary(expected_diff_rows, checks, blockers)
    _check_cell_provenance(cell_rows, checks, blockers)
    _check_row_summary(row_summary_rows, checks, blockers)
    _check_review_summary(review_summary, cell_rows, checks, blockers)

    validation_evidence = _load_validation_evidence(validation_evidence_json)
    science_result = _evaluate_science_evidence(
        validation_evidence,
        checks,
        validation_evidence_json=validation_evidence_json,
        validation_artifact_root=validation_artifact_root,
        cell_provenance_contract_summary=cell_provenance_contract_summary,
        cell_provenance_minimal_fixture=cell_provenance_minimal_fixture,
    )
    blockers.extend(cast(list[str], science_result["blockers"]))

    contract_correctness_status = (
        "fail"
        if any(
            row["category"] == "contract_correctness" and row["status"] == "fail"
            for row in checks
        )
        else "pass"
    )
    scientific_confidence_status = str(science_result["status"])
    readiness_label, production_ready = _readiness_label(
        contract_correctness_status=contract_correctness_status,
        scientific_confidence_status=scientific_confidence_status,
    )
    may_promote_default_quant_matrix = production_ready

    summary: dict[str, object] = {
        "schema_version": PROMOTION_READINESS_SCHEMA,
        "contract_correctness_status": contract_correctness_status,
        "scientific_confidence_status": scientific_confidence_status,
        "readiness_label": readiness_label,
        "production_ready": production_ready,
        "may_promote_default_quant_matrix": may_promote_default_quant_matrix,
        "missing_science_evidence": science_result["missing_science_evidence"],
        "blockers": _unique(blockers),
        "validation_tiers": science_result["validation_tiers"],
        "input_artifacts": {
            "expected_diff_summary_tsv": str(expected_diff_summary_tsv),
            "cell_provenance_tsv": str(cell_provenance_tsv),
            "row_summary_tsv": str(row_summary_tsv),
            "review_summary_json": str(review_summary_json),
            "validation_evidence_json": (
                str(validation_evidence_json) if validation_evidence_json else ""
            ),
        },
        "authority_statement": (
            "Contract correctness is not scientific confidence. Focused tests and "
            "small-cohort smoke evidence cannot claim production_ready without large "
            "cohort, oracle/manual review, and downstream impact evidence."
        ),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_json = output_dir / "quant_matrix_promotion_readiness_summary.json"
    checks_tsv = output_dir / "quant_matrix_promotion_readiness_checks.tsv"
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    write_tsv(
        checks_tsv,
        checks,
        PROMOTION_CHECK_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    return {"summary_json": summary_json, "checks_tsv": checks_tsv}


def _check_expected_diff_summary(
    rows: Sequence[Mapping[str, str]],
    checks: list[dict[str, str]],
    blockers: list[str],
) -> None:
    if len(rows) != 1:
        blockers.append("contract_expected_diff_not_pass")
        _append_check(
            checks,
            check_id="contract_expected_diff_pass",
            category="contract_correctness",
            status="fail",
            severity="blocker",
            evidence=f"expected exactly one row; got {len(rows)}",
            next_action="Regenerate QuantMatrixVersion expected_diff_summary.tsv.",
        )
        return
    row = rows[0]
    expected_count = optional_int(row.get("expected_diff_count", ""))
    written_count = optional_int(row.get("written_backfill_count", ""))
    unused_count = optional_int(row.get("unused_expected_diff_count", ""))
    passes = (
        row.get("schema_version") == EXPECTED_DIFF_SUMMARY_SCHEMA
        and row.get("acceptance_status") == "pass"
        and expected_count == written_count
        and unused_count == 0
    )
    if not passes:
        blockers.append("contract_expected_diff_not_pass")
    _append_check(
        checks,
        check_id="contract_expected_diff_pass",
        category="contract_correctness",
        status="pass" if passes else "fail",
        severity="blocker",
        evidence=(
            f"acceptance_status={row.get('acceptance_status', '')}; "
            f"expected={row.get('expected_diff_count', '')}; "
            f"written={row.get('written_backfill_count', '')}; "
            f"unused={row.get('unused_expected_diff_count', '')}"
        ),
        next_action=(
            "Expected diff is closed."
            if passes
            else "Fix expected-diff closure before promotion readiness."
        ),
    )


def _check_cell_provenance(
    rows: Sequence[Mapping[str, str]],
    checks: list[dict[str, str]],
    blockers: list[str],
) -> None:
    problems: list[str] = []
    accepted_count = 0
    for row in rows:
        label = f"{row.get('peak_hypothesis_id', '')}/{row.get('sample_stem', '')}"
        if row.get("schema_version") != CELL_PROVENANCE_SCHEMA:
            problems.append(f"{label}: schema_version")
        cell_status = row.get("cell_status", "")
        write_authority = row.get("write_authority", "")
        if cell_status == "accepted_backfill":
            accepted_count += 1
            if write_authority != "TRUE":
                problems.append(f"{label}: accepted_backfill write_authority")
            missing_fields = [field for field in _PROVENANCE_FIELDS if not row[field]]
            if missing_fields:
                problems.append(f"{label}: missing {'/'.join(missing_fields)}")
            invalid_hash_fields = [
                field
                for field in (
                    "source_artifact_sha256",
                    "source_row_sha256",
                    "manifest_sha256",
                )
                if row[field] and not _is_sha256(row[field])
            ]
            if invalid_hash_fields:
                problems.append(
                    f"{label}: invalid {'/'.join(invalid_hash_fields)}",
                )
        elif cell_status == "detected":
            if write_authority != "FALSE":
                problems.append(f"{label}: detected write_authority")
        else:
            problems.append(f"{label}: unsupported cell_status={cell_status}")
    if problems:
        blockers.append("cell_provenance_authority_invalid")
    _append_check(
        checks,
        check_id="contract_cell_provenance_authority",
        category="contract_correctness",
        status="fail" if problems else "pass",
        severity="blocker",
        evidence=(
            f"rows={len(rows)}; accepted_backfill={accepted_count}; "
            f"problems={'; '.join(problems[:5])}"
        ),
        next_action=(
            "Cell provenance authority is closed."
            if not problems
            else "Regenerate cell_provenance.tsv with authoritative accepted "
            "Backfill provenance and non-authoritative detected rows."
        ),
    )


def _check_row_summary(
    rows: Sequence[Mapping[str, str]],
    checks: list[dict[str, str]],
    blockers: list[str],
) -> None:
    problems: list[str] = []
    for row in rows:
        label = row.get("peak_hypothesis_id", "")
        detected_count = optional_int(row.get("detected_count", ""))
        accepted_count = optional_int(row.get("accepted_backfilled_count", ""))
        available_count = optional_int(row.get("quant_available_count", ""))
        if row.get("schema_version") != ROW_SUMMARY_SCHEMA:
            problems.append(f"{label}: schema_version")
        if (
            detected_count is None
            or accepted_count is None
            or available_count is None
            or detected_count + accepted_count != available_count
        ):
            problems.append(f"{label}: count_mismatch")
    if problems:
        blockers.append("row_summary_count_mismatch")
    _append_check(
        checks,
        check_id="contract_row_summary_arithmetic",
        category="contract_correctness",
        status="fail" if problems else "pass",
        severity="blocker",
        evidence=f"rows={len(rows)}; problems={'; '.join(problems[:5])}",
        next_action=(
            "Row-level detected/backfilled arithmetic is closed."
            if not problems
            else "Regenerate row_summary.tsv before promotion readiness."
        ),
    )


def _check_review_summary(
    summary: Mapping[str, Any],
    cell_rows: Sequence[Mapping[str, str]],
    checks: list[dict[str, str]],
    blockers: list[str],
) -> None:
    accepted_count = sum(
        1 for row in cell_rows if row.get("cell_status") == "accepted_backfill"
    )
    detected_count = sum(1 for row in cell_rows if row.get("cell_status") == "detected")
    problems: list[str] = []
    if summary.get("schema_version") != QUANT_MATRIX_REVIEW_SCHEMA:
        problems.append("schema_version")
    if summary.get("validation_label") != "shadow_review":
        problems.append("validation_label")
    if summary.get("accepted_backfill_count") != accepted_count:
        problems.append("accepted_backfill_count")
    if summary.get("detected_count") != detected_count:
        problems.append("detected_count")
    if problems:
        blockers.append("review_summary_not_shadow_review")
    _append_check(
        checks,
        check_id="contract_review_summary_shadow_only",
        category="contract_correctness",
        status="fail" if problems else "pass",
        severity="blocker",
        evidence=f"problems={'; '.join(problems)}",
        next_action=(
            "Review summary is a shadow_review sidecar."
            if not problems
            else "Regenerate QuantMatrix review summary from the same artifacts."
        ),
    )


def _load_validation_evidence(path: Path | None) -> Mapping[str, Any]:
    if path is None:
        return {
            "schema_version": VALIDATION_EVIDENCE_SCHEMA,
            "requested_readiness_label": "",
            "evidence": [],
        }
    return _read_json(path)


def _evaluate_science_evidence(
    evidence: Mapping[str, Any],
    checks: list[dict[str, str]],
    *,
    validation_evidence_json: Path | None,
    validation_artifact_root: Path | None,
    cell_provenance_contract_summary: Path | None,
    cell_provenance_minimal_fixture: Path | None,
) -> dict[str, object]:
    blockers: list[str] = []
    if evidence.get("schema_version") != VALIDATION_EVIDENCE_SCHEMA:
        blockers.append("validation_evidence_schema_invalid")
        _append_check(
            checks,
            check_id="science_validation_evidence_schema",
            category="scientific_confidence",
            status="fail",
            severity="blocker",
            evidence=f"schema_version={evidence.get('schema_version', '')}",
            next_action="Use quant_matrix_validation_evidence.",
        )
        return {
            "status": "fail",
            "missing_science_evidence": list(_REQUIRED_SCIENCE_EVIDENCE),
            "validation_tiers": {},
            "blockers": blockers,
        }

    parsed_evidence = _validation_tiers(
        evidence,
        validation_evidence_json,
        validation_artifact_root=validation_artifact_root,
        cell_provenance_contract_summary=cell_provenance_contract_summary,
        cell_provenance_minimal_fixture=cell_provenance_minimal_fixture,
    )
    validation_tiers = parsed_evidence.validation_tiers
    blockers.extend(parsed_evidence.blockers)
    _append_validation_packet_checks(parsed_evidence, checks)

    missing_science_evidence: list[str] = []
    failed_required = False
    for evidence_id, tiers in _REQUIRED_SCIENCE_EVIDENCE.items():
        status = _required_tier_status(validation_tiers, tiers)
        if status == "missing":
            missing_science_evidence.append(evidence_id)
        if status == "fail":
            failed_required = True
        _append_check(
            checks,
            check_id=f"science_{evidence_id.replace('_validation', '')}",
            category="scientific_confidence",
            status=status,
            severity="blocker",
            evidence=_tier_evidence(validation_tiers, tiers),
            next_action=(
                "Required science evidence is present."
                if status == "pass"
                else f"Provide passing {evidence_id} evidence before "
                "production_ready."
            ),
        )
    _append_auxiliary_tier_checks(validation_tiers, checks)

    status = "pass"
    if blockers or failed_required:
        status = "fail"
    elif missing_science_evidence:
        status = "inconclusive"
    if (
        evidence.get("requested_readiness_label") == "production_ready"
        and status != "pass"
    ):
        blockers.append("insufficient_validation_tier_for_production_ready")
    return {
        "status": status,
        "missing_science_evidence": missing_science_evidence,
        "validation_tiers": validation_tiers,
        "blockers": blockers,
    }


def _validation_tiers(
    evidence: Mapping[str, Any],
    validation_evidence_json: Path | None,
    *,
    validation_artifact_root: Path | None,
    cell_provenance_contract_summary: Path | None,
    cell_provenance_minimal_fixture: Path | None,
) -> ValidationEvidenceParse:
    tiers: dict[str, str] = {}
    blockers: list[str] = []
    duplicate_tiers: list[str] = []
    invalid_status: list[str] = []
    artifact_problems: list[str] = []
    raw_rows = evidence.get("evidence", [])
    if not isinstance(raw_rows, list):
        return ValidationEvidenceParse(
            validation_tiers=tiers,
            blockers=["validation_evidence_rows_invalid"],
            duplicate_tiers=[],
            invalid_status=["evidence must be a list"],
            artifact_problems=[],
        )
    base_dir = validation_evidence_json.parent if validation_evidence_json else None
    for index, row in enumerate(raw_rows, start=1):
        if not isinstance(row, dict):
            invalid_status.append(f"row {index}: not an object")
            continue
        tier = str(row.get("tier", "")).strip()
        status = str(row.get("status", "")).strip()
        if tier:
            if tier in tiers:
                duplicate_tiers.append(tier)
                continue
            tiers[tier] = status
        if status not in {"pass", "fail"}:
            invalid_status.append(f"{tier or f'row {index}'}: status={status}")
        if status == "pass" and tier in _REQUIRED_SCIENCE_TIER_PROVENANCE:
            artifact_problems.extend(
                f"{tier}: {problem}"
                for problem in _required_science_binding_problems(
                    row,
                    base_dir=base_dir,
                    artifact_root=validation_artifact_root,
                    cell_provenance_contract_summary=(
                        cell_provenance_contract_summary
                    ),
                    cell_provenance_minimal_fixture=(
                        cell_provenance_minimal_fixture
                    ),
                )
            )
    if duplicate_tiers:
        blockers.append("validation_evidence_duplicate_tier")
    if invalid_status:
        blockers.append("validation_evidence_status_invalid")
    if artifact_problems:
        blockers.append("validation_evidence_artifact_unbound")
    return ValidationEvidenceParse(
        validation_tiers=tiers,
        blockers=blockers,
        duplicate_tiers=duplicate_tiers,
        invalid_status=invalid_status,
        artifact_problems=artifact_problems,
    )


def _append_validation_packet_checks(
    parsed_evidence: ValidationEvidenceParse,
    checks: list[dict[str, str]],
) -> None:
    duplicate_tiers = parsed_evidence.duplicate_tiers
    invalid_status = parsed_evidence.invalid_status
    artifact_problems = parsed_evidence.artifact_problems
    if duplicate_tiers or invalid_status:
        _append_check(
            checks,
            check_id="science_validation_evidence_unique_tiers",
            category="scientific_confidence",
            status="fail",
            severity="blocker",
            evidence=(
                f"duplicate_tiers={'; '.join(duplicate_tiers)}; "
                f"invalid_status={'; '.join(invalid_status)}"
            ),
            next_action="Fix validation evidence tier uniqueness and statuses.",
        )
    if artifact_problems:
        _append_check(
            checks,
            check_id="science_validation_evidence_artifact_binding",
            category="scientific_confidence",
            status="fail",
            severity="blocker",
            evidence="; ".join(artifact_problems[:5]),
            next_action=(
                "Bind required science evidence rows to existing artifact "
                "relpaths, hashes, and tier-specific provenance."
            ),
        )


def _required_tier_status(
    validation_tiers: Mapping[str, str],
    accepted_tiers: set[str],
) -> str:
    values = [
        validation_tiers[tier] for tier in accepted_tiers if tier in validation_tiers
    ]
    if any(value == "fail" for value in values):
        return "fail"
    if any(value == "pass" for value in values):
        return "pass"
    return "missing"


def _tier_evidence(
    validation_tiers: Mapping[str, str],
    accepted_tiers: set[str],
) -> str:
    values = [
        f"{tier}={validation_tiers[tier]}"
        for tier in sorted(accepted_tiers)
        if tier in validation_tiers
    ]
    return "; ".join(values) if values else "not provided"


def _append_auxiliary_tier_checks(
    validation_tiers: Mapping[str, str],
    checks: list[dict[str, str]],
) -> None:
    for tier in ("focused_tests", "fixture_cohort_smoke"):
        if tier not in validation_tiers:
            continue
        _append_check(
            checks,
            check_id=f"science_auxiliary_{tier}",
            category="scientific_confidence",
            status=validation_tiers[tier],
            severity="info",
            evidence=f"{tier}={validation_tiers[tier]}",
            next_action=(
                "Auxiliary evidence is recorded, but it cannot satisfy "
                "production_ready by itself."
            ),
        )


def _readiness_label(
    *,
    contract_correctness_status: str,
    scientific_confidence_status: str,
) -> tuple[str, bool]:
    if contract_correctness_status != "pass":
        return "contract_failed", False
    if scientific_confidence_status == "pass":
        return "production_ready_candidate_packet", True
    if scientific_confidence_status == "fail":
        return "contract_ready_science_failed", False
    return "contract_ready_science_inconclusive", False


def _append_check(
    rows: list[dict[str, str]],
    *,
    check_id: str,
    category: str,
    status: str,
    severity: str,
    evidence: str,
    next_action: str,
) -> None:
    rows.append(
        {
            "schema_version": PROMOTION_READINESS_SCHEMA,
            "check_id": check_id,
            "category": category,
            "status": status,
            "severity": severity,
            "evidence": evidence,
            "next_action": next_action,
        }
    )


def _read_json(path: Path) -> Mapping[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    return data


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _required_science_binding_problems(
    row: Mapping[str, Any],
    *,
    base_dir: Path | None,
    artifact_root: Path | None,
    cell_provenance_contract_summary: Path | None,
    cell_provenance_minimal_fixture: Path | None,
) -> list[str]:
    tier = str(row.get("tier", "")).strip()
    required_fields = _REQUIRED_SCIENCE_TIER_PROVENANCE[tier]
    problems = [field for field in required_fields if not str(row.get(field, ""))]
    if tier == "full_cohort":
        raw_run_count = optional_int(row.get("raw_run_count", ""))
        if raw_run_count is None or raw_run_count < 85:
            problems.append("raw_run_count must be >=85")
    artifact_relpath = str(row.get("artifact_path", "")).strip()
    artifact_sha256 = str(row.get("artifact_sha256", "")).strip()
    if artifact_sha256 and not _is_sha256(artifact_sha256):
        problems.append("artifact_sha256 must be 64-hex")
    if artifact_relpath and base_dir is not None:
        artifact_path = Path(artifact_relpath)
        if artifact_path.is_absolute() or ".." in artifact_path.parts:
            problems.append("artifact_path must be a relative child path")
        else:
            resolved_base = base_dir.resolve(strict=False)
            resolved_artifact = (base_dir / artifact_path).resolve(strict=False)
            try:
                resolved_artifact.relative_to(resolved_base)
            except ValueError:
                problems.append("artifact_path escapes validation packet")
            if not resolved_artifact.exists():
                problems.append("artifact_path does not exist")
            elif (
                _is_sha256(artifact_sha256)
                and file_sha256(resolved_artifact) != artifact_sha256.upper()
            ):
                problems.append("artifact_sha256 mismatch")
            if tier == "downstream_impact_smoke" and resolved_artifact.exists():
                problems.extend(
                    validate_quant_matrix_downstream_impact_smoke(
                        resolved_artifact,
                        artifact_root=artifact_root,
                        cell_provenance_contract_summary=(
                            cell_provenance_contract_summary
                        ),
                        cell_provenance_minimal_fixture=(
                            cell_provenance_minimal_fixture
                        ),
                    ),
                )
    elif artifact_relpath:
        problems.append("validation evidence path is required for artifact binding")
    return problems


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )
