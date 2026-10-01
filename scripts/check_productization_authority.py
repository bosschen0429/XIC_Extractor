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
from xic_extractor.alignment.quantitative_projection_authority import (  # noqa: E402
    QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS,
    QuantitativeProjectionAuthorityError,
    rehydrate_quantitative_projection_authority_manifest,
    render_quantitative_projection_authority_manifest_bytes,
)

DEFAULT_MANIFEST = ROOT / PRODUCTIZATION_AUTHORITY_MANIFEST_REL
DEFAULT_DEPENDENT_MANIFEST = ROOT / QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_REL

APPROVED_SCOPE_ORDER: tuple[str, ...] = ()
DEPENDENT_MANIFEST_SCHEMA_VERSION = "quantitative_projection_authority_manifest"
DEPENDENT_EFFECT_ID = "untargeted_owner_backfill_canonical_quantitative_projection"
DEPENDENT_HOLD_STATUS = "HOLD_PENDING_VALIDATION"
DEPENDENT_PACKAGE_AUTHORITY_ARTIFACT = (
    "xic_extractor/alignment/data/quantitative_projection_authority.json"
)
DEPENDENT_PACKAGE_AUTHORITY_SHA256 = (
    "26753eb69300ec14a4eb044d21e44cb1351c2360cac7fae9884c3aec9bb6cd54"
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
WRITER_CURRENT_AUTHORITY_FIELDS: tuple[str, ...] = ()
PARKED_BROAD_BACKFILL = "broad_backfill"
RETIRED_SCOPE_IDS = {
    "backfill_expansion_raw_trace_expected_diff_666_cells",
    "backfill_expansion_clean_target_selective_activation_84_cells",
}
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
    _check_explanation_sources(manifest, repo_root, problems)
    return problems


def _check_dependent_manifest(
    manifest: dict[str, Any],
    manifest_raw: bytes,
    problems: list[str],
) -> None:
    if tuple(manifest) != QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS:
        problems.append(
            "dependent authority manifest fields must preserve the exact ordered "
            "contract"
        )
    if manifest.get("schema_version") != DEPENDENT_MANIFEST_SCHEMA_VERSION:
        problems.append("dependent authority manifest schema_version changed")
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
            "writer authority manifest fields/order changed from the frozen contract"
        )
    if manifest.get("schema_version") != "productization_authority_manifest":
        problems.append("writer authority manifest schema_version changed")
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


def _check_explanation_sources(
    manifest: Mapping[str, Any], repo_root: Path, problems: list[str]
) -> None:
    for source in manifest.get("explanation_only_sources", []):
        if not isinstance(source, Mapping):
            continue
        relative = source.get("artifact")
        if not isinstance(relative, str) or not relative:
            problems.append("explanation compact artifact path is missing")
            continue
        path = repo_root / relative
        if not path.is_file():
            problems.append(f"explanation compact artifact is missing: {relative}")
        elif artifact_sha256(path) != source.get("artifact_sha256"):
            problems.append("explanation compact artifact hash does not match manifest")


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
