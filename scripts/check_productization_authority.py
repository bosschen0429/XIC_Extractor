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
)

DEFAULT_MANIFEST = ROOT / PRODUCTIZATION_AUTHORITY_MANIFEST_REL

APPROVED_SCOPE_ORDER: tuple[str, ...] = ()
WRITER_MANIFEST_FIELDS = (
    "schema_version",
    "status",
    "updated",
    "authority_policy",
    "current_authority",
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
    repo_root: Path = ROOT,
) -> list[str]:
    problems: list[str] = []
    manifest = _read_json(manifest_path, problems)
    if not manifest:
        return problems

    _check_manifest(manifest, problems)
    _check_explanation_sources(manifest, repo_root, problems)
    return problems


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
            "manifest missing negative-evidence scopes: " + ", ".join(missing_negative)
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
    try:
        raw = path.read_bytes()
    except OSError as exc:
        problems.append(f"{path}: cannot read JSON: {exc}")
        return {}
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append(f"{path}: invalid UTF-8 JSON: {exc}")
        return {}
    try:
        payload = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, _DuplicateJsonKeyError) as exc:
        problems.append(f"{path}: invalid JSON: {exc}")
        return {}
    if type(payload) is not dict:
        problems.append(f"{path}: JSON payload must be one object")
        return {}
    return payload


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    problems = check_productization_authority(
        manifest_path=args.manifest,
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
