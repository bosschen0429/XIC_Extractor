"""Validate ProductionAcceptanceManifest v1 schema and TSV artifacts.

This is a fail-closed contract checker. It defines and validates the only
Backfill artifact shape that may grant per-cell write_authority=true in a
future ProductWriter activation phase. It does not write ProductWriter outputs,
matrices, workbooks, selected peaks/areas, counted detections, GUI state, or
default extraction behavior.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, final

from xic_extractor.alignment.quant_matrix_version import (
    PRODUCTION_ACCEPTANCE_COLUMNS,
    QuantMatrixVersionRows,
    build_quant_matrix_version_rows,
    production_acceptance_manifest_sha256,
)
from xic_extractor.tabular_io import (
    file_sha256,
    read_tsv_with_header_snapshot,
)

SCHEMA_VERSION = "production_acceptance_manifest_schema_v1"
MANIFEST_SCHEMA_VERSION = "production_acceptance_manifest_v1"
ACCEPTANCE_CONTRACT_VERSION = "production_acceptance_manifest_contract_v1"

REQUIRED_COLUMNS = PRODUCTION_ACCEPTANCE_COLUMNS

ACCEPT_BASIC = "accept_basic_backfill"
ACCEPT_STRICT = "accept_strict_backfill"
REQUIRE_REVIEW = "require_review"
REJECT_BACKFILL = "reject_backfill"
NOT_EVALUATED = "not_evaluated"
ACCEPTANCE_DECISIONS = {
    ACCEPT_BASIC,
    ACCEPT_STRICT,
    REQUIRE_REVIEW,
    REJECT_BACKFILL,
    NOT_EVALUATED,
}
WRITE_DECISIONS = {ACCEPT_BASIC, ACCEPT_STRICT}

ACCEPTANCE_BASES = {
    "machine_basic",
    "machine_strict",
    "manual_review",
    "external_oracle",
    "not_applicable",
}
TRUTH_STATUSES = {
    "not_truth_claimed",
    "manual_negative",
    "external_truth_positive",
    "external_truth_negative",
    "unresolved",
}
DOUBLET_STATUSES = {
    "no_doublet_claim",
    "not_evaluated",
    "right_reference_blocked",
    "unclear_reference_blocked",
    "unresolved_blocked",
}
DOUBLET_BLOCKED_STATUSES = {
    "right_reference_blocked",
    "unclear_reference_blocked",
    "unresolved_blocked",
}
REFERENCE_SIDES = {"left", "right", "unclear", "unresolved", "not_applicable"}
BLOCKED_REFERENCE_SIDES = {"right", "unclear", "unresolved"}
REPORT_ONLY_RISK_RULE_IDS = {"low_seed_support", "high_backfill_dependency"}
FORBIDDEN_WRITE_SOURCE_TOKENS = (
    "lockbox_shadow_automation",
    "shadow_report",
    "gallery",
    "candidate",
)
_CHECKED_MANIFEST_MINT_TOKEN = object()


class ProductionAcceptanceManifestError(ValueError):
    """Raised when a manifest snapshot cannot mint Matrix authority."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


@final
@dataclass(frozen=True, slots=True)
class CheckedProductionAcceptanceManifest:
    """One immutable manifest byte snapshot checked by the authority owner."""

    manifest_path: Path
    repo_root: Path
    rows: tuple[Mapping[str, str], ...]
    canonical_manifest_sha256: str
    manifest_file_sha256: str
    _mint_token: InitVar[object]

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("CheckedProductionAcceptanceManifest is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _CHECKED_MANIFEST_MINT_TOKEN:
            raise ProductionAcceptanceManifestError(
                ("checked manifest must be minted by production_acceptance",)
            )
        if not isinstance(self.rows, tuple) or any(
            not isinstance(row, MappingProxyType) for row in self.rows
        ):
            raise ProductionAcceptanceManifestError(
                ("checked manifest rows must be deeply immutable",)
            )
        for field, value in (
            ("canonical_manifest_sha256", self.canonical_manifest_sha256),
            ("manifest_file_sha256", self.manifest_file_sha256),
        ):
            if not _is_sha256(value):
                raise ProductionAcceptanceManifestError(
                    (f"{field} must be a SHA-256 digest",)
                )


def check_production_acceptance_manifest_schema(
    *,
    schema_path: Path,
) -> list[str]:
    problems: list[str] = []
    schema = _read_json(schema_path, problems, "production acceptance schema")
    if not schema:
        return problems
    if schema.get("schema_version") != SCHEMA_VERSION:
        problems.append("production acceptance schema_version mismatch")
    if schema.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        problems.append("production acceptance manifest_schema_version mismatch")
    if schema.get("acceptance_contract_version") != ACCEPTANCE_CONTRACT_VERSION:
        problems.append("production acceptance contract version mismatch")
    if schema.get("required_columns") != list(REQUIRED_COLUMNS):
        problems.append("production acceptance required_columns mismatch")
    if schema.get("allowed_acceptance_decisions") != sorted(ACCEPTANCE_DECISIONS):
        problems.append("production acceptance decisions mismatch")
    if schema.get("allowed_acceptance_basis") != sorted(ACCEPTANCE_BASES):
        problems.append("production acceptance basis mismatch")
    if schema.get("allowed_truth_statuses") != sorted(TRUTH_STATUSES):
        problems.append("production acceptance truth statuses mismatch")
    if schema.get("allowed_doublet_statuses") != sorted(DOUBLET_STATUSES):
        problems.append("production acceptance doublet statuses mismatch")
    if schema.get("allowed_reference_sides") != sorted(REFERENCE_SIDES):
        problems.append("production acceptance reference sides mismatch")
    if schema.get("report_only_risk_rule_ids") != sorted(REPORT_ONLY_RISK_RULE_IDS):
        problems.append("production acceptance report-only risks mismatch")
    _check_schema_authority_rules(schema, problems)
    return problems


def check_production_acceptance_manifest(
    *,
    manifest_path: Path,
    schema_path: Path,
    repo_root: Path,
) -> list[str]:
    problems = check_production_acceptance_manifest_schema(schema_path=schema_path)
    problems.extend(
        check_production_acceptance_manifest_authority(
            manifest_path=manifest_path,
            repo_root=repo_root,
        )
    )
    return problems


def check_production_acceptance_manifest_authority(
    *,
    manifest_path: Path,
    repo_root: Path,
) -> list[str]:
    """Validate every rule that may grant per-cell Matrix write authority."""

    _header, _rows, _file_sha, problems = _read_and_check_manifest_snapshot(
        manifest_path=manifest_path,
        repo_root=repo_root,
    )
    return problems


def load_checked_production_acceptance_manifest(
    *,
    manifest_path: Path,
    repo_root: Path,
) -> CheckedProductionAcceptanceManifest:
    """Read once, validate once, and mint the exact rows Matrix may consume."""

    _header, rows, manifest_file_sha256, problems = (
        _read_and_check_manifest_snapshot(
            manifest_path=manifest_path,
            repo_root=repo_root,
        )
    )
    if problems:
        raise ProductionAcceptanceManifestError(problems)
    canonical_manifest_sha256 = production_acceptance_manifest_sha256(rows)
    return CheckedProductionAcceptanceManifest(
        manifest_path=manifest_path.resolve(),
        repo_root=repo_root.resolve(),
        rows=tuple(MappingProxyType(dict(row)) for row in rows),
        canonical_manifest_sha256=canonical_manifest_sha256,
        manifest_file_sha256=manifest_file_sha256,
        _mint_token=_CHECKED_MANIFEST_MINT_TOKEN,
    )


def build_quant_matrix_version_from_checked_manifest(
    *,
    matrix_header: Sequence[str],
    input_quant_matrix_rows: Sequence[Mapping[str, str]],
    input_matrix_identity_rows: Sequence[Mapping[str, str]],
    checked_manifest: CheckedProductionAcceptanceManifest,
    expected_diff_rows: Sequence[Mapping[str, str]],
) -> QuantMatrixVersionRows:
    """Enter the pure Matrix renderer through an owner-minted authority receipt."""

    if not isinstance(checked_manifest, CheckedProductionAcceptanceManifest):
        raise ProductionAcceptanceManifestError(
            ("Matrix activation requires a checked manifest snapshot",)
        )
    return build_quant_matrix_version_rows(
        matrix_header=matrix_header,
        input_quant_matrix_rows=input_quant_matrix_rows,
        input_matrix_identity_rows=input_matrix_identity_rows,
        production_acceptance_rows=checked_manifest.rows,
        expected_diff_rows=expected_diff_rows,
    )


def _read_and_check_manifest_snapshot(
    *,
    manifest_path: Path,
    repo_root: Path,
) -> tuple[tuple[str, ...], list[dict[str, str]], str, list[str]]:
    problems: list[str] = []
    try:
        header, rows, manifest_file_sha256 = read_tsv_with_header_snapshot(
            manifest_path,
            exact_columns=REQUIRED_COLUMNS,
        )
    except (OSError, ValueError) as exc:
        return (), [], "", [
            f"could not read production acceptance manifest: {exc}"
        ]
    expected_manifest_sha = production_acceptance_manifest_sha256(rows)
    _check_manifest_rows(rows, expected_manifest_sha, repo_root, problems)
    return header, rows, manifest_file_sha256, problems


def _check_schema_authority_rules(
    schema: Mapping[str, Any],
    problems: list[str],
) -> None:
    rules = schema.get("authority_rules")
    if not isinstance(rules, Mapping):
        problems.append("production acceptance authority_rules must be an object")
        return
    expected = {
        "primary_key": ["peak_hypothesis_id", "sample_stem"],
        "family_id_is_context_only": True,
        "phase2_writes_default_matrix": False,
        "accepted_decisions_require_write_authority": True,
        "non_acceptance_decisions_must_not_write": True,
        "manual_negative_hard_stop": True,
        "right_unclear_unresolved_doublet_hard_stop": True,
        "shadow_only_rows_must_not_write": True,
        "accepted_value_cannot_be_naked_alignment_area": True,
        "direct_shadow_report_gallery_candidate_sources_cannot_write": True,
        "source_artifact_paths_must_stay_within_repo_root": True,
        "source_artifact_hashes_must_match_files": True,
        "accepted_quant_value_must_be_finite_non_negative": True,
        "backfill_fraction_must_match_counts": True,
    }
    for key, expected_value in expected.items():
        if rules.get(key) != expected_value:
            problems.append(f"production acceptance authority_rules.{key} drifted")


def _check_manifest_rows(
    rows: Sequence[Mapping[str, str]],
    expected_manifest_sha: str,
    repo_root: Path,
    problems: list[str],
) -> None:
    primary_counts = Counter(
        (row.get("peak_hypothesis_id", ""), row.get("sample_stem", ""))
        for row in rows
        if row.get("peak_hypothesis_id", "") and row.get("sample_stem", "")
    )
    duplicate_keys = sorted(key for key, count in primary_counts.items() if count > 1)
    for peak_hypothesis_id, sample_stem in duplicate_keys:
        problems.append(
            "duplicate primary key: "
            f"peak_hypothesis_id={peak_hypothesis_id} sample_stem={sample_stem}",
        )
    for row_number, row in enumerate(rows, start=2):
        _check_manifest_row(
            row,
            row_number,
            expected_manifest_sha,
            repo_root,
            problems,
        )


def _check_manifest_row(
    row: Mapping[str, str],
    row_number: int,
    expected_manifest_sha: str,
    repo_root: Path,
    problems: list[str],
) -> None:
    if row.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        problems.append(f"row {row_number}: schema_version mismatch")
    if row.get("acceptance_contract_version") != ACCEPTANCE_CONTRACT_VERSION:
        problems.append(f"row {row_number}: acceptance_contract_version mismatch")

    decision = row.get("acceptance_decision", "")
    basis = row.get("acceptance_basis", "")
    truth_status = row.get("truth_status", "")
    doublet_status = row.get("doublet_status", "")
    reference_side = row.get("reference_side", "")
    if decision not in ACCEPTANCE_DECISIONS:
        problems.append(f"row {row_number}: invalid acceptance_decision")
    if basis not in ACCEPTANCE_BASES:
        problems.append(f"row {row_number}: invalid acceptance_basis")
    if truth_status not in TRUTH_STATUSES:
        problems.append(f"row {row_number}: invalid truth_status")
    if doublet_status not in DOUBLET_STATUSES:
        problems.append(f"row {row_number}: invalid doublet_status")
    if reference_side not in REFERENCE_SIDES:
        problems.append(f"row {row_number}: invalid reference_side")

    bools = _bool_fields(row, row_number, problems)
    shadow_only = bools.get("shadow_only") is True
    write_authority = bools.get("write_authority") is True
    matrix_write_allowed = bools.get("matrix_write_allowed") is True
    accepted = decision in WRITE_DECISIONS

    if shadow_only and write_authority:
        problems.append(
            f"row {row_number}: shadow_only row cannot grant write authority",
        )
    if shadow_only and matrix_write_allowed:
        problems.append(f"row {row_number}: shadow_only row cannot write matrix")
    if matrix_write_allowed and not write_authority:
        problems.append(
            f"row {row_number}: matrix_write_allowed requires write_authority",
        )
    if write_authority and not matrix_write_allowed:
        problems.append(
            f"row {row_number}: write_authority requires matrix_write_allowed",
        )
    if write_authority and not accepted:
        problems.append(
            f"row {row_number}: non-acceptance decision cannot grant write authority",
        )
    if accepted and (not write_authority or not matrix_write_allowed):
        problems.append(
            f"row {row_number}: accepted row must grant explicit write authority",
        )

    _check_primary_key(row, row_number, accepted, write_authority, problems)
    _check_basis(row_number, decision, basis, problems)
    _check_counts(row, row_number, problems)
    resolved_source_paths = _check_hashes_and_paths(
        row,
        row_number,
        expected_manifest_sha,
        repo_root,
        problems,
    )
    _check_write_blockers(
        row,
        row_number,
        accepted,
        write_authority,
        resolved_source_artifact_relpath=resolved_source_paths.get(
            "source_artifact_relpath"
        ),
        problems=problems,
    )


def _bool_fields(
    row: Mapping[str, str],
    row_number: int,
    problems: list[str],
) -> dict[str, bool | None]:
    result: dict[str, bool | None] = {}
    for field in ("shadow_only", "write_authority", "matrix_write_allowed"):
        value = row.get(field, "")
        if value == "TRUE":
            result[field] = True
        elif value == "FALSE":
            result[field] = False
        else:
            result[field] = None
            problems.append(f"row {row_number}: {field} must be TRUE or FALSE")
    return result


def _check_primary_key(
    row: Mapping[str, str],
    row_number: int,
    accepted: bool,
    write_authority: bool,
    problems: list[str],
) -> None:
    if not row.get("sample_stem"):
        problems.append(f"row {row_number}: missing sample_stem")
    if accepted or write_authority:
        if not row.get("peak_hypothesis_id"):
            problems.append(f"row {row_number}: missing peak_hypothesis_id")
            problems.append(
                f"row {row_number}: accepted row must have a formal primary key",
            )
    if not row.get("feature_family_id"):
        problems.append(f"row {row_number}: feature_family_id context is required")


def _check_basis(
    row_number: int,
    decision: str,
    basis: str,
    problems: list[str],
) -> None:
    if decision == ACCEPT_BASIC and basis != "machine_basic":
        problems.append(f"row {row_number}: accept_basic requires machine_basic")
    if decision == ACCEPT_STRICT and basis not in {
        "machine_strict",
        "manual_review",
        "external_oracle",
    }:
        problems.append(f"row {row_number}: accept_strict basis mismatch")
    if basis == "manual_review" and decision != ACCEPT_STRICT:
        problems.append(f"row {row_number}: manual_review must be strict")
    if decision in {REQUIRE_REVIEW, REJECT_BACKFILL, NOT_EVALUATED} and (
        basis not in {"not_applicable", "external_oracle"}
    ):
        problems.append(f"row {row_number}: non-write basis must be not_applicable")


def _check_counts(
    row: Mapping[str, str],
    row_number: int,
    problems: list[str],
) -> None:
    detected = _non_negative_int(row.get("detected_count", ""))
    backfilled = _non_negative_int(row.get("backfilled_count", ""))
    available = _non_negative_int(row.get("quant_available_count", ""))
    missing = _non_negative_int(row.get("missing_count", ""))
    if detected is None:
        problems.append(f"row {row_number}: invalid detected_count")
    if backfilled is None:
        problems.append(f"row {row_number}: invalid backfilled_count")
    if available is None:
        problems.append(f"row {row_number}: invalid quant_available_count")
    if missing is None:
        problems.append(f"row {row_number}: invalid missing_count")
    if (
        detected is not None
        and backfilled is not None
        and available is not None
        and detected + backfilled != available
    ):
        problems.append(f"row {row_number}: quant_available_count mismatch")
    fraction = _fraction(row.get("backfill_fraction", ""))
    if fraction is None:
        problems.append(f"row {row_number}: invalid backfill_fraction")
    elif backfilled is not None and available is not None:
        expected_fraction = 0.0 if available == 0 else backfilled / available
        if abs(fraction - expected_fraction) > 1e-6:
            problems.append(f"row {row_number}: backfill_fraction mismatch")


def _check_hashes_and_paths(
    row: Mapping[str, str],
    row_number: int,
    expected_manifest_sha: str,
    repo_root: Path,
    problems: list[str],
) -> dict[str, str]:
    resolved_source_paths: dict[str, str] = {}
    for field in (
        "doublet_source_sha256",
        "source_artifact_sha256",
        "source_row_sha256",
        "manifest_sha256",
    ):
        value = row.get(field, "")
        if not _is_sha256(value):
            problems.append(f"row {row_number}: {field} is required")
    manifest_sha = row.get("manifest_sha256")
    if manifest_sha and manifest_sha != expected_manifest_sha:
        problems.append(f"row {row_number}: manifest_sha256 mismatch")
    for field in ("doublet_source_relpath", "source_artifact_relpath"):
        value = row.get(field, "")
        if not value:
            problems.append(f"row {row_number}: {field} is required")
            continue
        path = _resolve_source_path(
            repo_root=repo_root,
            relative_path=value,
            field=field,
            row_number=row_number,
            problems=problems,
        )
        if path is None:
            continue
        resolved_source_paths[field] = path.relative_to(
            repo_root.resolve()
        ).as_posix()
        _check_source_artifact_hash(
            path=path,
            relative_path=value,
            hash_field=field.replace("_relpath", "_sha256"),
            row=row,
            row_number=row_number,
            problems=problems,
        )
    return resolved_source_paths


def _resolve_source_path(
    *,
    repo_root: Path,
    relative_path: str,
    field: str,
    row_number: int,
    problems: list[str],
) -> Path | None:
    normalized_path = relative_path.replace("\\", "/")
    path = Path(normalized_path)
    windows_path = PureWindowsPath(relative_path)
    if path.is_absolute() or windows_path.drive:
        problems.append(f"row {row_number}: {field} must be relative")
        return None
    if ".." in PurePosixPath(normalized_path).parts:
        problems.append(f"row {row_number}: {field} must stay within repo_root")
        return None
    root = repo_root.resolve()
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        problems.append(f"row {row_number}: {field} must stay within repo_root")
        return None
    return resolved


def _check_source_artifact_hash(
    *,
    path: Path,
    relative_path: str,
    hash_field: str,
    row: Mapping[str, str],
    row_number: int,
    problems: list[str],
) -> None:
    if not path.exists():
        problems.append(f"row {row_number}: {relative_path} source artifact missing")
        return
    expected_hash = row.get(hash_field, "")
    if _is_sha256(expected_hash) and file_sha256(path) != expected_hash:
        problems.append(f"row {row_number}: {hash_field} mismatch")


def _check_write_blockers(
    row: Mapping[str, str],
    row_number: int,
    accepted: bool,
    write_authority: bool,
    resolved_source_artifact_relpath: str | None,
    problems: list[str],
) -> None:
    if not (accepted or write_authority):
        return
    hard_blockers = _tokens(row.get("hard_blocker_rule_ids", ""))
    if hard_blockers:
        problems.append(
            f"row {row_number}: hard blocker cannot grant write authority",
        )
    if row.get("truth_status") == "manual_negative":
        problems.append(
            f"row {row_number}: manual_negative cannot grant write authority",
        )
    if (
        row.get("doublet_status") in DOUBLET_BLOCKED_STATUSES
        or row.get("reference_side") in BLOCKED_REFERENCE_SIDES
        or row.get("doublet_allowed") != "TRUE"
    ):
        problems.append(
            f"row {row_number}: doublet state cannot grant write authority",
        )
    quant_value = row.get("quant_value", "")
    if not quant_value:
        problems.append(f"row {row_number}: quant_value is required")
    elif _finite_non_negative_float(quant_value) is None:
        problems.append(
            f"row {row_number}: quant_value must be finite non-negative",
        )
    if row.get("quant_value_source") == "alignment_cells_area_only" or row.get(
        "matrix_area_source",
    ) == "alignment_cells_area_only":
        problems.append(
            f"row {row_number}: naked alignment_cells area cannot grant authority",
        )
    if any(
        _is_forbidden_write_source_path(source_path)
        for source_path in (
            row.get("source_artifact_relpath", ""),
            resolved_source_artifact_relpath or "",
        )
    ):
        problems.append(
            f"row {row_number}: direct shadow/report/gallery/candidate source "
            "cannot grant write authority",
        )
    strict_risks = _tokens(row.get("triggered_risk_rule_ids", "")) - (
        REPORT_ONLY_RISK_RULE_IDS
    )
    if strict_risks and not _tokens(row.get("closure_rule_ids", "")):
        problems.append(f"row {row_number}: strict risk requires closure_rule_ids")


def _is_forbidden_write_source_path(value: str) -> bool:
    normalized_path = value.replace("\\", "/").casefold()
    return any(token in normalized_path for token in FORBIDDEN_WRITE_SOURCE_TOKENS)


def _read_json(path: Path, problems: list[str], label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        problems.append(f"could not read {label}: {exc}")
        return {}
    except json.JSONDecodeError as exc:
        problems.append(f"invalid {label} JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        problems.append(f"{label} must be a JSON object")
        return {}
    return payload


def _tokens(value: str) -> set[str]:
    return {token.strip() for token in value.split(";") if token.strip()}


def _non_negative_int(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _fraction(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if 0.0 <= parsed <= 1.0 else None


def _finite_non_negative_float(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


__all__ = (
    "ACCEPTANCE_DECISIONS",
    "CheckedProductionAcceptanceManifest",
    "ProductionAcceptanceManifestError",
    "REQUIRED_COLUMNS",
    "build_quant_matrix_version_from_checked_manifest",
    "check_production_acceptance_manifest",
    "check_production_acceptance_manifest_authority",
    "check_production_acceptance_manifest_schema",
    "load_checked_production_acceptance_manifest",
)
