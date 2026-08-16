"""Check productization authority/adjudication artifacts fail closed.

This is a contract guard, not a writer. It validates the current authority
manifest, mechanical adjudication schema, and adjudication index without
touching ProductWriter, matrices, workbooks, selected peaks, or counted
detections.
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_productization_state import artifact_sha256  # noqa: E402
from tools.diagnostics.docs_policy import (  # noqa: E402
    MECHANICAL_ADJUDICATION_INDEX_REL,
    PRODUCTIZATION_AUTHORITY_MANIFEST_REL,
    QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_REL,
)
from xic_extractor.alignment.quantitative_projection_authority import (  # noqa: E402
    QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1,
    QuantitativeProjectionAuthorityError,
    rehydrate_quantitative_projection_authority_manifest,
    render_quantitative_projection_authority_manifest_bytes,
)

DEFAULT_MANIFEST = ROOT / PRODUCTIZATION_AUTHORITY_MANIFEST_REL
DEFAULT_DEPENDENT_MANIFEST = ROOT / QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_REL
DEFAULT_SCHEMA = (
    ROOT / "docs/superpowers/schemas/mechanical_adjudication_schema.v1.json"
)
DEFAULT_INDEX = ROOT / MECHANICAL_ADJUDICATION_INDEX_REL

APPROVED_SCOPE = "backfill_policy_write_ready_rows"
CID_NL_APPROVED_SCOPE = "cid_nl_adopt_ready_feature_inclusion_95_cells"
APPROVED_SCOPE_ORDER = (
    APPROVED_SCOPE,
    CID_NL_APPROVED_SCOPE,
)
APPROVED_SCOPES = frozenset(APPROVED_SCOPE_ORDER)
DEPENDENT_MANIFEST_SCHEMA_VERSION = "quantitative_projection_authority_manifest_v1"
DEPENDENT_EFFECT_ID = "untargeted_owner_backfill_canonical_quantitative_projection_v1"
DEPENDENT_HOLD_STATUS = "HOLD_PENDING_VALIDATION"
DEPENDENT_PACKAGE_AUTHORITY_ARTIFACT = (
    "xic_extractor/alignment/data/issue256_quantitative_projection_authority_v1.json"
)
DEPENDENT_PACKAGE_AUTHORITY_SHA256 = (
    "d35e3783cea171acf67b69bca04c1ba848e07810c11526617bab6087ea2a7e89"
)
WRITER_MANIFEST_FIELDS = (
    "schema_version",
    "status",
    "updated",
    "authority_policy",
    "current_authority",
    "parked_lanes",
    "explanation_only_sources",
    "blocked_or_negative_evidence_scopes",
)
WRITER_AUTHORITY_POLICY_FIELDS = (
    "unregistered_scope_policy",
    "product_writer_allowed_scopes",
    "matrix_write_requires",
    "forbidden_authority_sources",
)
WRITER_CURRENT_AUTHORITY_FIELDS = (
    "backfill",
    "cid_nl_default_activation",
)
PARKED_BROAD_BACKFILL = "broad_backfill"
NEGATIVE_SCOPE_IDS = {
    "all_stability",
    "apex_delta_clean",
    "width_only_clean",
    "shape_margin_clean",
    "shape_clean_reintegration_stable_writer_probe",
    "backfill_expansion_clean_target_selective_activation_84_cells",
}


def check_productization_authority(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    dependent_manifest_path: Path = DEFAULT_DEPENDENT_MANIFEST,
    schema_path: Path = DEFAULT_SCHEMA,
    index_path: Path = DEFAULT_INDEX,
    repo_root: Path = ROOT,
) -> list[str]:
    problems: list[str] = []
    manifest = _read_json(manifest_path, problems)
    dependent_manifest, dependent_manifest_raw = _read_json_bytes(
        dependent_manifest_path,
        problems,
    )
    schema = _read_json(schema_path, problems)
    header, rows = _read_tsv(index_path, problems)
    if not manifest or not dependent_manifest or not schema:
        return problems

    _check_manifest(manifest, problems)
    _check_dependent_manifest(dependent_manifest, dependent_manifest_raw, problems)
    _check_schema_and_index(schema, header, rows, problems)
    _check_index_authority(manifest, rows, problems)
    _check_source_hashes(manifest, rows, repo_root, problems)
    _check_retained_authority_source_hashes(manifest, repo_root, problems)
    return problems


def _check_dependent_manifest(
    manifest: dict[str, Any],
    manifest_raw: bytes,
    problems: list[str],
) -> None:
    if tuple(manifest) != QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1:
        problems.append(
            "dependent authority manifest fields must preserve the exact ordered "
            "V1 contract"
        )
    if manifest.get("schema_version") != DEPENDENT_MANIFEST_SCHEMA_VERSION:
        problems.append("dependent authority manifest schema_version must remain V1")
    if manifest.get("effect_id") != DEPENDENT_EFFECT_ID:
        problems.append("dependent authority manifest effect_id is crossed")
    if manifest.get("status") != DEPENDENT_HOLD_STATUS:
        problems.append(
            "dependent authority manifest must remain HOLD_PENDING_VALIDATION; "
            "ACTIVE is outside this ticket"
        )
    if manifest.get("package_authority_artifact") != (
        DEPENDENT_PACKAGE_AUTHORITY_ARTIFACT
    ):
        problems.append("dependent authority manifest package artifact is crossed")
    if manifest.get("package_authority_sha256") != (DEPENDENT_PACKAGE_AUTHORITY_SHA256):
        problems.append("dependent authority manifest package hash drifted")

    try:
        owner_manifest = rehydrate_quantitative_projection_authority_manifest(manifest)
        canonical_manifest_raw = (
            render_quantitative_projection_authority_manifest_bytes(owner_manifest)
        )
    except QuantitativeProjectionAuthorityError as exc:
        problems.append(f"dependent authority owner replay failed: {exc}")
        return

    if manifest_raw != canonical_manifest_raw:
        problems.append(
            "dependent authority manifest must exactly match owner-rendered "
            "canonical bytes"
        )

    if owner_manifest.schema_version != DEPENDENT_MANIFEST_SCHEMA_VERSION:
        problems.append("owner replay returned an unexpected dependent schema")
    if owner_manifest.effect_id != DEPENDENT_EFFECT_ID:
        problems.append("owner replay returned an unexpected dependent effect")
    if owner_manifest.status != DEPENDENT_HOLD_STATUS:
        problems.append("owner replay did not return the required HOLD status")
    if owner_manifest.package_authority_artifact != (
        DEPENDENT_PACKAGE_AUTHORITY_ARTIFACT
    ):
        problems.append("owner replay returned an unexpected package artifact")
    if owner_manifest.package_authority_sha256 != (DEPENDENT_PACKAGE_AUTHORITY_SHA256):
        problems.append("owner replay returned an unexpected package hash")
    if owner_manifest.allowed_registered_writer_scope_ids != APPROVED_SCOPE_ORDER:
        problems.append(
            "dependent authority manifest writer scopes must equal the existing "
            "two registered scopes in order"
        )
    if (
        owner_manifest.activation_summary_artifact != ""
        or owner_manifest.activation_summary_sha256 != ""
        or owner_manifest.current_effect_cell_count != 0
    ):
        problems.append(
            "dependent HOLD authority must have no activation summary or effect cells"
        )

    package_path = ROOT / owner_manifest.package_authority_artifact
    if not package_path.is_file():
        problems.append(
            "dependent package authority artifact is missing: "
            f"{owner_manifest.package_authority_artifact}"
        )
    elif artifact_sha256(package_path).lower() != (
        owner_manifest.package_authority_sha256
    ):
        problems.append(
            "dependent package authority raw hash does not match owner replay"
        )


def _check_manifest(manifest: Mapping[str, Any], problems: list[str]) -> None:
    if tuple(manifest) != WRITER_MANIFEST_FIELDS:
        problems.append(
            "writer authority manifest fields/order changed from the frozen V1 contract"
        )
    if manifest.get("schema_version") != "productization_authority_manifest_v1":
        problems.append("writer authority manifest schema_version must remain V1")
    if manifest.get("status") != "active":
        problems.append("writer authority manifest status must remain active")
    policy = manifest.get("authority_policy")
    if not isinstance(policy, Mapping):
        problems.append("manifest missing authority_policy object")
        return
    if tuple(policy) != WRITER_AUTHORITY_POLICY_FIELDS:
        problems.append(
            "writer authority policy fields/order changed from the frozen contract"
        )
    if policy.get("unregistered_scope_policy") != "fail_closed":
        problems.append("unregistered_scope_policy must be fail_closed")
    raw_writer_scopes = policy.get("product_writer_allowed_scopes")
    if (
        not isinstance(raw_writer_scopes, list)
        or tuple(raw_writer_scopes) != APPROVED_SCOPE_ORDER
    ):
        problems.append(
            "product_writer_allowed_scopes must preserve the exact two registered "
            f"scopes in order: {APPROVED_SCOPE_ORDER!r}",
        )

    current_authority = manifest.get("current_authority")
    if not isinstance(current_authority, Mapping):
        problems.append("manifest missing current_authority object")
    elif tuple(current_authority) != WRITER_CURRENT_AUTHORITY_FIELDS:
        problems.append(
            "writer current_authority fields/order must remain the exact two "
            "registered lanes"
        )

    backfill = _mapping_path(manifest, "current_authority", "backfill")
    if backfill is None:
        problems.append("manifest missing current_authority.backfill")
    else:
        expected = {
            "candidate_audit_universe_rows": 4613,
            "current_product_authority_rows": 511,
            "detected_flagged_rows": 0,
            "blocked_rows": 4102,
            "authority_scope": APPROVED_SCOPE,
        }
        for key, value in expected.items():
            if backfill.get(key) != value:
                problems.append(f"manifest backfill {key} must be {value!r}")

    cid_nl = _mapping_path(manifest, "current_authority", "cid_nl_default_activation")
    if cid_nl is None:
        problems.append("manifest missing current_authority.cid_nl_default_activation")
    else:
        expected_cid_nl = {
            "current_product_authority_rows": 95,
            "existing_successor_context_rows": 337,
            "omitted_no_target_rows": 27,
            "authority_scope": CID_NL_APPROVED_SCOPE,
        }
        for key, value in expected_cid_nl.items():
            if cid_nl.get(key) != value:
                problems.append(f"manifest CID-NL {key} must be {value!r}")

    parked = _mapping_path(manifest, "parked_lanes", PARKED_BROAD_BACKFILL)
    if parked is None:
        problems.append("manifest missing parked broad_backfill lane")
    else:
        if parked.get("status") != "parked":
            problems.append("broad_backfill status must be parked")
        if parked.get("may_grant_write_authority") is not False:
            problems.append("broad_backfill may_grant_write_authority must be false")

    explanation_sources = manifest.get("explanation_only_sources")
    if not isinstance(explanation_sources, list) or not explanation_sources:
        problems.append("manifest must list explanation_only_sources")
    else:
        for source in explanation_sources:
            if not isinstance(source, Mapping):
                problems.append("explanation_only_sources entries must be objects")
            elif source.get("may_grant_write_authority") is not False:
                problems.append("explanation_only source grants authority")

    negative_scope_ids = {
        scope.get("scope_id")
        for scope in manifest.get("blocked_or_negative_evidence_scopes", [])
        if isinstance(scope, Mapping)
    }
    missing_negative = sorted(NEGATIVE_SCOPE_IDS - negative_scope_ids)
    if missing_negative:
        problems.append(
            "manifest missing negative-evidence scopes: "
            + ", ".join(missing_negative)
        )


def _check_schema_and_index(
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
    missing = sorted(set(required) - set(header))
    if missing:
        problems.append("index missing required columns: " + ", ".join(missing))
    if len(rows) != 4613:
        problems.append(f"index must contain 4613 rows, found {len(rows)}")
    row_ids = [row.get("row_id", "") for row in rows]
    if len(set(row_ids)) != len(row_ids):
        problems.append("index row_id values must be unique")

    decision_counts = Counter(row.get("decision", "") for row in rows)
    if decision_counts != {"write_ready": 511, "evidence_required": 4102}:
        problems.append(f"unexpected decision counts: {dict(decision_counts)}")
    grade_counts = Counter(row.get("evidence_grade", "") for row in rows)
    if grade_counts != {"A": 511, "C": 3015, "D": 1087}:
        problems.append(f"unexpected evidence_grade counts: {dict(grade_counts)}")


def _check_index_authority(
    manifest: Mapping[str, Any],
    rows: Sequence[Mapping[str, str]],
    problems: list[str],
) -> None:
    policy = manifest.get("authority_policy")
    if not isinstance(policy, Mapping):
        problems.append("manifest missing authority_policy object for index check")
        return
    raw_allowed = policy.get("product_writer_allowed_scopes")
    if not isinstance(raw_allowed, list) or not all(
        isinstance(scope, str) for scope in raw_allowed
    ):
        problems.append("manifest product_writer_allowed_scopes must be a string list")
        return
    allowed = set(raw_allowed)
    authority_scope_values: set[str] = set()
    for index, row in enumerate(rows, start=2):
        write_authority = row.get("write_authority")
        scope = row.get("product_authority_scope", "")
        if scope:
            authority_scope_values.add(scope)
        if write_authority == "TRUE":
            _require_row_value(row, index, "decision", "write_ready", problems)
            _require_row_value(
                row,
                index,
                "source_policy_decision",
                "write_ready",
                problems,
            )
            _require_row_value(row, index, "explanation_only", "FALSE", problems)
            _require_row_value(row, index, "may_touch_matrix", "TRUE", problems)
            if scope not in allowed:
                problems.append(f"row {index}: unregistered authority scope {scope!r}")
        elif write_authority == "FALSE":
            _require_row_value(row, index, "may_touch_matrix", "FALSE", problems)
            if scope:
                problems.append(f"row {index}: non-write row has authority scope")
        else:
            problems.append(f"row {index}: invalid write_authority {write_authority!r}")

        if row.get("explanation_only") == "TRUE" and write_authority != "FALSE":
            problems.append(f"row {index}: explanation_only row grants authority")
        if row.get("blockers") and write_authority != "FALSE":
            problems.append(f"row {index}: blocker row grants authority")

    if authority_scope_values != {APPROVED_SCOPE}:
        problems.append(
            "index authority scopes must be exactly "
            f"{APPROVED_SCOPE!r}, found {sorted(authority_scope_values)!r}"
        )
    forbidden = authority_scope_values & (NEGATIVE_SCOPE_IDS | {PARKED_BROAD_BACKFILL})
    if forbidden:
        problems.append("forbidden authority scopes present: " + ", ".join(forbidden))


def _check_source_hashes(
    manifest: Mapping[str, Any],
    rows: Sequence[Mapping[str, str]],
    repo_root: Path,
    problems: list[str],
) -> None:
    if not rows:
        return
    source_hash_sets = {row.get("source_hashes", "") for row in rows}
    if len(source_hash_sets) != 1:
        problems.append("index rows must share one source_hashes value")
        return
    source_hashes = _parse_semicolon_pairs(source_hash_sets.pop())
    backfill = _mapping_path(manifest, "current_authority", "backfill")
    explanation_sources = manifest.get("explanation_only_sources", [])
    if backfill is None or not explanation_sources:
        return
    expected_policy_hash = backfill.get(
        "externalized_source_artifact_sha256",
        backfill.get("artifact_sha256"),
    )
    expected_quality_hash = explanation_sources[0].get(
        "externalized_source_artifact_sha256",
        explanation_sources[0].get("artifact_sha256"),
    )
    if source_hashes.get("policy") != expected_policy_hash:
        problems.append("index policy source hash does not match manifest")
    if source_hashes.get("quality") != expected_quality_hash:
        problems.append("index quality source hash does not match manifest")

    artifacts = {
        "policy": backfill.get(
            "externalized_source_artifact",
            backfill.get("artifact"),
        ),
        "quality": explanation_sources[0].get(
            "externalized_source_artifact",
            explanation_sources[0].get("artifact"),
        ),
    }
    for source_id, relative in artifacts.items():
        if not isinstance(relative, str):
            continue
        path = repo_root / relative
        if path.exists() and artifact_sha256(path) != source_hashes.get(source_id):
            problems.append(f"{source_id} artifact hash does not match index")

    compact_sources = {
        "policy": backfill,
        "quality": explanation_sources[0],
    }
    for source_id, source in compact_sources.items():
        relative = source.get("artifact")
        expected_hash = source.get("artifact_sha256")
        if not isinstance(relative, str) or not relative:
            problems.append(f"{source_id} compact artifact path is missing")
            continue
        if not isinstance(expected_hash, str) or not expected_hash:
            problems.append(f"{source_id} compact artifact hash is missing")
            continue
        path = repo_root / relative
        if not path.is_file():
            problems.append(f"{source_id} compact artifact is missing: {relative}")
            continue
        if artifact_sha256(path) != expected_hash:
            problems.append(
                f"{source_id} compact artifact hash does not match manifest"
            )
    _check_backfill_compact_summary(
        backfill,
        explanation_sources[0],
        rows,
        source_hashes,
        repo_root,
        problems,
    )


def _check_retained_authority_source_hashes(
    manifest: Mapping[str, Any],
    repo_root: Path,
    problems: list[str],
) -> None:
    retained_authorities = {
        "CID-NL": _mapping_path(
            manifest,
            "current_authority",
            "cid_nl_default_activation",
        ),
    }
    artifact_fields = {
        "artifact": "artifact_sha256",
        "acceptance_artifact": "acceptance_artifact_sha256",
        "compact_manifest": "compact_manifest_sha256",
    }
    for label, authority in retained_authorities.items():
        if authority is None:
            continue
        for path_key, sha_key in artifact_fields.items():
            relative = authority.get(path_key)
            expected_hash = authority.get(sha_key)
            if not isinstance(relative, str) or not relative:
                problems.append(f"manifest {label} {path_key} must be a non-empty path")
                continue
            if not isinstance(expected_hash, str) or not expected_hash:
                problems.append(f"manifest {label} {sha_key} must be a non-empty hash")
                continue
            path = repo_root / relative
            if not path.is_file():
                problems.append(f"{label} {path_key} artifact is missing: {relative}")
                continue
            if artifact_sha256(path) != expected_hash:
                problems.append(f"{label} {path_key} hash does not match manifest")


def _check_backfill_compact_summary(
    backfill: Mapping[str, Any],
    explanation_source: Mapping[str, Any],
    rows: Sequence[Mapping[str, str]],
    source_hashes: Mapping[str, str],
    repo_root: Path,
    problems: list[str],
) -> None:
    relative = backfill.get("artifact")
    quality_relative = explanation_source.get("artifact")
    if not isinstance(relative, str) or not relative:
        return
    if quality_relative != relative:
        problems.append("policy and quality compact artifacts must share one summary")
        return
    path = repo_root / relative
    if not path.is_file():
        return
    summary = _read_json(path, problems)
    if not summary:
        return

    current_scope = summary.get("current_scope")
    if not isinstance(current_scope, Mapping):
        problems.append("compact summary current_scope must be an object")
    else:
        _check_compact_summary_value(
            current_scope,
            "candidate_audit_universe_rows",
            backfill.get("candidate_audit_universe_rows"),
            "current_scope",
            problems,
        )
        _check_compact_summary_value(
            current_scope,
            "current_product_authority_rows",
            backfill.get("current_product_authority_rows"),
            "current_scope",
            problems,
        )
        _check_compact_summary_value(
            current_scope,
            "detected_flagged_rows",
            backfill.get("detected_flagged_rows"),
            "current_scope",
            problems,
        )
        _check_compact_summary_value(
            current_scope,
            "blocked_rows",
            backfill.get("blocked_rows"),
            "current_scope",
            problems,
        )
        _check_compact_summary_value(
            current_scope,
            "product_authority_scope",
            backfill.get("authority_scope"),
            "current_scope",
            problems,
        )

    externalized = summary.get("externalized_artifacts")
    if not isinstance(externalized, Mapping):
        problems.append("compact summary externalized_artifacts must be an object")
        return

    expected_rows = backfill.get("candidate_audit_universe_rows")
    if rows and expected_rows != len(rows):
        problems.append("manifest backfill candidate rows must match index row count")

    policy = externalized.get("standard_peak_backfill_policy")
    quality = externalized.get("standard_peak_backfill_policy_quality_explanations")
    acceptance = externalized.get("narrow_product_writer_expected_diff_acceptance")
    _check_compact_summary_artifact(
        label="policy",
        artifact=policy,
        manifest_path=backfill.get("externalized_source_artifact"),
        manifest_sha=backfill.get("externalized_source_artifact_sha256"),
        index_sha=source_hashes.get("policy"),
        expected_rows=expected_rows,
        expected_write_authority=True,
        expected_may_grant_write_authority=None,
        expected_scope=backfill.get("authority_scope"),
        problems=problems,
    )
    _check_compact_summary_artifact(
        label="quality",
        artifact=quality,
        manifest_path=explanation_source.get("externalized_source_artifact"),
        manifest_sha=explanation_source.get("externalized_source_artifact_sha256"),
        index_sha=source_hashes.get("quality"),
        expected_rows=expected_rows,
        expected_write_authority=False,
        expected_may_grant_write_authority=explanation_source.get(
            "may_grant_write_authority",
        ),
        expected_scope=None,
        problems=problems,
    )
    _check_compact_summary_acceptance(
        artifact=acceptance,
        backfill=backfill,
        policy_artifact=policy,
        problems=problems,
    )

    if isinstance(policy, Mapping):
        policy_counts = policy.get("policy_decision_counts")
        if not isinstance(policy_counts, Mapping):
            problems.append("compact summary policy decision counts must be an object")
        else:
            index_policy_counts = Counter(
                row.get("source_policy_decision", "") for row in rows
            )
            expected_counts = {
                "write_ready": index_policy_counts.get("write_ready", 0),
                "detected_flagged": index_policy_counts.get("detected_flagged", 0),
                "blocked": index_policy_counts.get("blocked", 0),
            }
            for decision, expected in expected_counts.items():
                if policy_counts.get(decision) != expected:
                    problems.append(
                        "compact summary policy decision count "
                        f"{decision} does not match index",
                    )


def _check_compact_summary_acceptance(
    *,
    artifact: Any,
    backfill: Mapping[str, Any],
    policy_artifact: Any,
    problems: list[str],
) -> None:
    if not isinstance(artifact, Mapping):
        problems.append("compact summary acceptance artifact must be an object")
        return
    if artifact.get("path") != backfill.get("acceptance_artifact"):
        problems.append("compact summary acceptance path does not match manifest")
    if artifact.get("sha256") != backfill.get("acceptance_artifact_sha256"):
        problems.append("compact summary acceptance sha256 does not match manifest")
    if artifact.get("acceptance_status") != "pass":
        problems.append("compact summary acceptance status must be pass")
    if artifact.get("activation_application_status") != "applied":
        problems.append("compact summary acceptance application status must be applied")
    if artifact.get("expected_scope") != backfill.get("authority_scope"):
        problems.append(
            "compact summary acceptance expected_scope does not match manifest"
        )
    if artifact.get("eligible_audit_row_count") != backfill.get(
        "current_product_authority_rows",
    ):
        problems.append(
            "compact summary acceptance eligible_audit_row_count "
            "does not match manifest"
        )
    if artifact.get("matrix_cells_written") != backfill.get(
        "current_product_authority_rows",
    ):
        problems.append(
            "compact summary acceptance matrix_cells_written does not match manifest"
        )
    if artifact.get("product_surface_changed") != "TRUE":
        problems.append(
            "compact summary acceptance product_surface_changed must be TRUE"
        )
    if isinstance(policy_artifact, Mapping):
        if artifact.get("activation_scope_audit_tsv") != policy_artifact.get("path"):
            problems.append(
                "compact summary acceptance activation_scope_audit_tsv "
                "does not match policy"
            )
        if artifact.get("activation_scope_audit_sha256") != policy_artifact.get(
            "sha256",
        ):
            problems.append(
                "compact summary acceptance activation_scope_audit_sha256 "
                "does not match policy"
            )


def _check_compact_summary_artifact(
    *,
    label: str,
    artifact: Any,
    manifest_path: Any,
    manifest_sha: Any,
    index_sha: str | None,
    expected_rows: Any,
    expected_write_authority: bool,
    expected_may_grant_write_authority: Any,
    expected_scope: Any,
    problems: list[str],
) -> None:
    if not isinstance(artifact, Mapping):
        problems.append(f"compact summary {label} artifact must be an object")
        return
    if artifact.get("path") != manifest_path:
        problems.append(f"compact summary {label} path does not match manifest")
    if artifact.get("sha256") != manifest_sha:
        problems.append(f"compact summary {label} sha256 does not match manifest")
    if index_sha is not None and artifact.get("sha256") != index_sha:
        problems.append(f"compact summary {label} sha256 does not match index")
    if artifact.get("data_rows") != expected_rows:
        problems.append(f"compact summary {label} data_rows does not match manifest")
    if artifact.get("write_authority") is not expected_write_authority:
        problems.append(
            f"compact summary {label} write_authority does not match manifest"
        )
    if (
        expected_may_grant_write_authority is not None
        and artifact.get("may_grant_write_authority")
        is not expected_may_grant_write_authority
    ):
        problems.append(
            f"compact summary {label} may_grant_write_authority "
            "does not match manifest"
        )
    if (
        expected_scope is not None
        and artifact.get("product_authority_scope") != expected_scope
    ):
        problems.append(
            f"compact summary {label} product_authority_scope does not match manifest"
        )


def _check_compact_summary_value(
    mapping: Mapping[str, Any],
    field: str,
    expected: Any,
    label: str,
    problems: list[str],
) -> None:
    if mapping.get(field) != expected:
        problems.append(f"compact summary {label} {field} does not match manifest")


def _mapping_path(
    root: Mapping[str, Any],
    *keys: str,
) -> Mapping[str, Any] | None:
    current: Any = root
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current if isinstance(current, Mapping) else None


def _require_row_value(
    row: Mapping[str, str],
    row_number: int,
    field: str,
    expected: str,
    problems: list[str],
) -> None:
    if row.get(field) != expected:
        problems.append(f"row {row_number}: {field} must be {expected!r}")


class _DuplicateJsonKeyError(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise _DuplicateJsonKeyError(f"duplicate JSON object key: {key!r}")
        parsed[key] = value
    return parsed


def _read_json(path: Path, problems: list[str]) -> dict[str, Any]:
    payload, _ = _read_json_bytes(path, problems)
    return payload


def _read_json_bytes(
    path: Path,
    problems: list[str],
) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        problems.append(f"{path}: cannot read JSON: {exc}")
        return {}, b""
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append(f"{path}: invalid UTF-8 JSON: {exc}")
        return {}, raw
    try:
        payload = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, _DuplicateJsonKeyError) as exc:
        problems.append(f"{path}: invalid JSON: {exc}")
        return {}, raw
    if type(payload) is not dict:
        problems.append(f"{path}: JSON payload must be one object")
        return {}, raw
    return payload, raw


def _read_tsv(
    path: Path,
    problems: list[str],
) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            return tuple(reader.fieldnames or ()), list(reader)
    except OSError as exc:
        problems.append(f"{path}: cannot read TSV: {exc}")
        return (), []


def _parse_semicolon_pairs(value: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in value.split(";"):
        if not part:
            continue
        key, separator, item = part.partition("=")
        if separator:
            parsed[key] = item
    return parsed


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--dependent-manifest",
        type=Path,
        default=DEFAULT_DEPENDENT_MANIFEST,
    )
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    problems = check_productization_authority(
        manifest_path=args.manifest,
        dependent_manifest_path=args.dependent_manifest,
        schema_path=args.schema,
        index_path=args.index,
        repo_root=args.repo_root,
    )
    if problems:
        print("Productization authority contract failed:\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Productization authority contract is fail-closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
