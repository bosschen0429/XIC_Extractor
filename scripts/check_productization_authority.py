"""Check current productization authority artifacts fail closed.

This is a contract guard, not a writer. It validates the current authority
manifests and their retained compact evidence without touching ProductWriter,
matrices, workbooks, selected peaks, or counted detections.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_productization_state import artifact_sha256  # noqa: E402
from tools.diagnostics.docs_policy import (  # noqa: E402
    PRODUCTIZATION_AUTHORITY_MANIFEST_REL,
    QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_REL,
)
from xic_extractor.alignment.production_acceptance import (  # noqa: E402
    ProductionAcceptanceManifestError,
    load_checked_production_acceptance_manifest,
)
from xic_extractor.alignment.quant_matrix_version import (  # noqa: E402
    accepted_production_acceptance_rows_by_key,
)
from xic_extractor.alignment.quantitative_projection_authority import (  # noqa: E402
    QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1,
    QuantitativeProjectionAuthorityError,
    rehydrate_quantitative_projection_authority_manifest,
    render_quantitative_projection_authority_manifest_bytes,
)
from xic_extractor.alignment.shared_peak_identity_explanation.schema import (  # noqa: E402
    ACTIVATION_VALUE_INPUT_COLUMNS,
)
from xic_extractor.tabular_io import read_tsv_with_header_snapshot  # noqa: E402

DEFAULT_MANIFEST = ROOT / PRODUCTIZATION_AUTHORITY_MANIFEST_REL
DEFAULT_DEPENDENT_MANIFEST = ROOT / QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_REL

APPROVED_SCOPE = "backfill_policy_write_ready_rows"
APPROVED_SCOPE_ORDER = (APPROVED_SCOPE,)
APPROVED_SCOPES = frozenset(APPROVED_SCOPE_ORDER)
DEPENDENT_MANIFEST_SCHEMA_VERSION = "quantitative_projection_authority_manifest_v1"
DEPENDENT_EFFECT_ID = "untargeted_owner_backfill_canonical_quantitative_projection_v1"
DEPENDENT_HOLD_STATUS = "HOLD_PENDING_VALIDATION"
DEPENDENT_PACKAGE_AUTHORITY_ARTIFACT = (
    "xic_extractor/alignment/data/quantitative_projection_authority_v1.json"
)
DEPENDENT_PACKAGE_AUTHORITY_SHA256 = (
    "15b74bc79dc9501210acd5f761c86b4a7128db83e74a62bf3e97b7beb1572ed5"
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
)
PARKED_BROAD_BACKFILL = "broad_backfill"
RETIRED_SCOPE_IDS = {
    "backfill_expansion_raw_trace_expected_diff_666_cells",
    "backfill_expansion_clean_target_selective_activation_84_cells",
}
CURRENT_BACKFILL_ACCEPTANCE_REL = (
    "docs/superpowers/validation/quant_matrix_real_bundle_v1/inputs/"
    "production_acceptance_manifest.tsv"
)
CURRENT_BACKFILL_ACTIVATION_VALUES_REL = (
    "docs/superpowers/validation/quant_matrix_real_bundle_v1/source_artifacts/"
    "standard_peak_activation_values.tsv"
)
NEGATIVE_SCOPE_IDS = {
    "all_stability",
    "apex_delta_clean",
    "width_only_clean",
    "shape_margin_clean",
    "shape_clean_reintegration_stable_writer_probe",
} | RETIRED_SCOPE_IDS


def check_productization_authority(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    dependent_manifest_path: Path = DEFAULT_DEPENDENT_MANIFEST,
    repo_root: Path = ROOT,
) -> list[str]:
    problems: list[str] = []
    manifest = _read_json(manifest_path, problems)
    dependent_manifest, dependent_manifest_raw = _read_json_bytes(
        dependent_manifest_path,
        problems,
    )
    if not manifest or not dependent_manifest:
        return problems

    _check_manifest(manifest, problems)
    _check_dependent_manifest(dependent_manifest, dependent_manifest_raw, problems)
    _check_backfill_authority_sources(manifest, repo_root, problems)
    try:
        checked_acceptance = load_checked_production_acceptance_manifest(
            manifest_path=repo_root / CURRENT_BACKFILL_ACCEPTANCE_REL,
            repo_root=repo_root,
        )
    except ProductionAcceptanceManifestError as exc:
        problems.extend(
            "current Backfill production acceptance: " + problem
            for problem in exc.problems
        )
    else:
        _check_current_backfill_membership(
            checked_acceptance.rows,
            repo_root,
            problems,
        )
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
            "registered writer scopes in order"
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
            "product_writer_allowed_scopes must preserve the exact registered "
            f"scopes in order: {APPROVED_SCOPE_ORDER!r}",
        )

    current_authority = manifest.get("current_authority")
    if not isinstance(current_authority, Mapping):
        problems.append("manifest missing current_authority object")
    elif tuple(current_authority) != WRITER_CURRENT_AUTHORITY_FIELDS:
        problems.append(
            "writer current_authority fields/order must remain the exact registered "
            "lanes"
        )

    backfill = _mapping_path(manifest, "current_authority", "backfill")
    if backfill is None:
        problems.append("manifest missing current_authority.backfill")
    elif backfill.get("authority_scope") != APPROVED_SCOPE:
        problems.append(
            f"manifest backfill authority_scope must be {APPROVED_SCOPE!r}"
        )

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

    negative_scopes = {
        scope.get("scope_id"): scope
        for scope in manifest.get("blocked_or_negative_evidence_scopes", [])
        if isinstance(scope, Mapping)
    }
    negative_scope_ids = set(negative_scopes)
    missing_negative = sorted(NEGATIVE_SCOPE_IDS - negative_scope_ids)
    if missing_negative:
        problems.append(
            "manifest missing negative-evidence scopes: "
            + ", ".join(missing_negative)
        )
    for scope_id in RETIRED_SCOPE_IDS:
        scope = negative_scopes.get(scope_id)
        if scope is not None and scope.get("status") != "retired":
            problems.append(f"{scope_id} status must be retired")


def _check_backfill_authority_sources(
    manifest: Mapping[str, Any],
    repo_root: Path,
    problems: list[str],
) -> None:
    backfill = _mapping_path(manifest, "current_authority", "backfill")
    explanation_sources = manifest.get("explanation_only_sources", [])
    if backfill is None or not explanation_sources:
        return

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
        repo_root,
        problems,
    )


def _check_backfill_compact_summary(
    backfill: Mapping[str, Any],
    explanation_source: Mapping[str, Any],
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
            "product_authority_scope",
            backfill.get("authority_scope"),
            "current_scope",
            problems,
        )

    externalized = summary.get("externalized_artifacts")
    if not isinstance(externalized, Mapping):
        problems.append("compact summary externalized_artifacts must be an object")
        return

    policy = externalized.get("standard_peak_backfill_policy")
    quality = externalized.get("standard_peak_backfill_policy_quality_explanations")
    acceptance = externalized.get("narrow_product_writer_expected_diff_acceptance")
    _check_compact_summary_artifact(
        label="policy",
        artifact=policy,
        manifest_path=backfill.get("externalized_source_artifact"),
        manifest_sha=backfill.get("externalized_source_artifact_sha256"),
        index_sha=None,
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
        index_sha=None,
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
def _check_current_backfill_membership(
    acceptance_rows: Sequence[Mapping[str, str]],
    repo_root: Path,
    problems: list[str],
) -> None:
    """Reconcile owner-checked acceptance with the retained activation rows."""

    try:
        _header, activation_rows, _sha = read_tsv_with_header_snapshot(
            repo_root / CURRENT_BACKFILL_ACTIVATION_VALUES_REL,
            exact_columns=ACTIVATION_VALUE_INPUT_COLUMNS,
        )
    except (OSError, ValueError) as exc:
        problems.append(f"current Backfill membership cannot be read: {exc}")
        return

    try:
        acceptance_by_key = accepted_production_acceptance_rows_by_key(
            acceptance_rows
        )
    except ValueError as exc:
        problems.append(f"current Backfill writable membership is invalid: {exc}")
        return
    activation_by_key = {
        (row["peak_hypothesis_id"], row["sample_stem"]): row
        for row in activation_rows
    }
    membership_matches = (
        len(acceptance_by_key) == len(activation_rows)
        == len(activation_by_key)
        and acceptance_by_key.keys() == activation_by_key.keys()
    )
    if membership_matches:
        membership_matches = all(
            acceptance["feature_family_id"] == activation["feature_family_id"]
            and acceptance["quant_value"] == activation["projected_matrix_value"]
            and acceptance["source_row_sha256"].upper()
            == activation["source_row_sha256"].upper()
            for key, acceptance in acceptance_by_key.items()
            for activation in (activation_by_key[key],)
        )
    if not membership_matches:
        problems.append(
            "current Backfill owner-checked membership does not match retained "
            "activation values"
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


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--dependent-manifest",
        type=Path,
        default=DEFAULT_DEPENDENT_MANIFEST,
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    problems = check_productization_authority(
        manifest_path=args.manifest,
        dependent_manifest_path=args.dependent_manifest,
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
