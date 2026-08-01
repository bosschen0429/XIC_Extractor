"""Check the machine-readable productization status index.

This is a control-plane consistency guard. It validates the current lane status
index against the authority manifest, compatibility lane registry, and
control-plane anchors without touching ProductWriter, matrices, workbooks,
selected peaks, or counted detections. The default anchor is not the active
handoff for every branch; active branch handoffs are ignored local files such
as docs/superpowers/handoffs/current/ACTIVE.local.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diagnostics import docs_scan as _docs_scan  # noqa: E402
from tools.diagnostics.docs_policy import (  # noqa: E402
    CONTROL_PLANE_PATH,
    PRODUCTIZATION_AUTHORITY_MANIFEST_REL,
    PRODUCTIZATION_CONTROL_PLANE_SOURCE_COPY_LOCATOR_NOTE_TITLE,
    PRODUCTIZATION_CONTROL_PLANE_SOURCE_COPY_NOTE_TITLE,
    PRODUCTIZATION_SCHEMA_REL,
    PRODUCTIZATION_STATUS_ANCHOR_PATH,
    PRODUCTIZATION_STATUS_INDEX_REL,
)

DEFAULT_SCHEMA = ROOT / PRODUCTIZATION_SCHEMA_REL
DEFAULT_STATUS_INDEX = ROOT / PRODUCTIZATION_STATUS_INDEX_REL
DEFAULT_AUTHORITY_MANIFEST = ROOT / PRODUCTIZATION_AUTHORITY_MANIFEST_REL
DEFAULT_PRODUCTIZATION_STATUS_ANCHOR = ROOT / PRODUCTIZATION_STATUS_ANCHOR_PATH
DEFAULT_HANDOFF = DEFAULT_PRODUCTIZATION_STATUS_ANCHOR
DEFAULT_CONTROL_PLANE = ROOT / CONTROL_PLANE_PATH
PRODUCTIZATION_SUMMARY_REL = "docs/product/productization.md"
DEFAULT_PRODUCTIZATION_SUMMARY = ROOT / PRODUCTIZATION_SUMMARY_REL
EXPECTED_CONTROL_PLANE_SCHEMA_VERSION = "productization_control_plane_schema_v2"

WRITER_LANE_AUTHORITY = {
    "backfill_current_write_ready_scope": {
        "authority_key": "backfill",
        "scope": "backfill_policy_write_ready_rows",
    },
    "cid_nl_default_product_activation_v1": {
        "authority_key": "cid_nl_default_activation",
        "scope": "cid_nl_adopt_ready_feature_inclusion_95_cells",
    },
    "backfill_expansion_clean_target_selective_product_activation_v1": {
        "authority_key": "backfill_expansion_clean_target_selective_activation",
        "scope": "backfill_expansion_clean_target_selective_activation_84_cells",
    },
}
WRITE_FORBIDDEN_STATUSES = {
    "parked",
    "blocked",
    "diagnostic_only",
    "frozen",
    "out_of_scope",
}
NON_AUTHORITY_LANE_GROUPS = {"review", "truth", "evidence"}
AUTHORITY_RULE_KEYS = {
    "unregistered_scope_policy",
    "allowed_product_writer_scopes",
    "parked_blocked_diagnostic_frozen_out_of_scope_must_not_write",
    "quality_explanations_cannot_write",
    "review_truth_or_recovery_artifacts_cannot_write",
    "handoff_and_control_plane_anchor_required",
}
AUTHORITY_TRUE_RULES = AUTHORITY_RULE_KEYS - {
    "unregistered_scope_policy",
    "allowed_product_writer_scopes",
}
PRODUCT_TOPIC_BY_LANE_GROUP = {
    "backfill": "backfill.md",
    "calibration": "instrument-qc-calibration.md",
    "cid_nl": "discovery.md",
    "gui": "untargeted-gui.md",
    "review": "review-roundtrip.md",
    "review_action": "review-roundtrip.md",
    "sample_metadata": "sample-metadata-qc.md",
    "targeted_ms1": "targeted-selection.md",
    "truth": "review-roundtrip.md",
}
ADDITIONAL_PRODUCTIZATION_NARRATIVE_OWNER_PATHS = (
    "docs/superpowers/plans/2026-06-19-backfill-quant-matrix-productization-roadmap.md",
    "docs/superpowers/validation/evidence_chain_source_of_truth.md",
)
READINESS_IDENTITY_STOP_WORDS = {
    "a",
    "and",
    "are",
    "for",
    "is",
    "only",
    "remain",
    "remains",
    "the",
    "v1",
}
CANONICAL_TEXT_HASH_EXTENSIONS = {
    ".csv",
    ".html",
    ".json",
    ".md",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}


def check_productization_state(
    *,
    schema_path: Path = DEFAULT_SCHEMA,
    status_index_path: Path = DEFAULT_STATUS_INDEX,
    authority_manifest_path: Path = DEFAULT_AUTHORITY_MANIFEST,
    handoff_path: Path = DEFAULT_HANDOFF,
    control_plane_path: Path = DEFAULT_CONTROL_PLANE,
    productization_summary_path: Path = DEFAULT_PRODUCTIZATION_SUMMARY,
    product_topic_dir: Path | None = None,
    repo_root: Path = ROOT,
) -> list[str]:
    problems: list[str] = []
    schema = _read_json(schema_path, problems)
    authority = _read_json(authority_manifest_path, problems)
    header, rows = _read_tsv(status_index_path, problems)
    handoff_text = _read_text(handoff_path, problems)
    control_plane_text = _read_text(control_plane_path, problems)
    productization_summary_text = _read_text(
        productization_summary_path,
        problems,
    )
    if not schema or not authority:
        return problems

    authority_rules = _check_schema_authority_rules(schema, problems)
    _check_schema_and_rows(schema, header, rows, problems)
    _check_authority_boundaries(authority_rules, authority, rows, problems)
    _check_artifacts(rows, repo_root, problems)
    _check_doc_anchors(rows, handoff_text, control_plane_text, problems)
    _check_compatibility_index(rows, control_plane_text, problems)
    _check_narrative_authority_partition(
        schema,
        rows,
        productization_summary_text,
        control_plane_text,
        handoff_text,
        problems,
    )
    _check_product_topic_authority_partition(
        schema,
        authority,
        rows,
        product_topic_dir or (repo_root / "docs/product"),
        repo_root,
        problems,
    )
    return problems


def _check_schema_authority_rules(
    schema: Mapping[str, Any],
    problems: list[str],
) -> Mapping[str, Any]:
    raw_rules = schema.get("authority_rules")
    if not isinstance(raw_rules, Mapping):
        problems.append("schema authority_rules must be an object")
        return {}
    actual_keys = set(raw_rules)
    if actual_keys != AUTHORITY_RULE_KEYS:
        problems.append(
            "schema authority_rules keys must exactly match the enforced contract; "
            f"missing={sorted(AUTHORITY_RULE_KEYS - actual_keys)!r}, "
            f"extra={sorted(actual_keys - AUTHORITY_RULE_KEYS)!r}"
        )
    if raw_rules.get("unregistered_scope_policy") != "fail_closed":
        problems.append(
            "schema authority_rules unregistered_scope_policy must be fail_closed"
        )
    raw_scopes = raw_rules.get("allowed_product_writer_scopes")
    if (
        not isinstance(raw_scopes, list)
        or not raw_scopes
        or not all(isinstance(scope, str) and scope for scope in raw_scopes)
        or len(raw_scopes) != len(set(raw_scopes))
    ):
        problems.append(
            "schema authority_rules allowed_product_writer_scopes must be a "
            "non-empty unique string list"
        )
    for rule in sorted(AUTHORITY_TRUE_RULES):
        if raw_rules.get(rule) is not True:
            problems.append(f"schema authority_rules {rule} must be true")
    return raw_rules


def _check_schema_and_rows(
    schema: Mapping[str, Any],
    header: Sequence[str],
    rows: Sequence[Mapping[str, str]],
    problems: list[str],
) -> None:
    if schema.get("schema_version") != EXPECTED_CONTROL_PLANE_SCHEMA_VERSION:
        problems.append(
            "unsupported productization control-plane schema_version; expected "
            f"{EXPECTED_CONTROL_PLANE_SCHEMA_VERSION}"
        )
    required = schema.get("required_status_index_columns")
    if not isinstance(required, list) or not all(
        isinstance(column, str) for column in required
    ):
        problems.append("schema required_status_index_columns must be a string list")
        return
    if list(header) != required:
        problems.append("status index header must exactly match schema")
    allowed_statuses = set(schema.get("allowed_readiness_statuses", []))
    required_lane_ids = set(schema.get("required_lane_ids", []))
    raw_anchor_contract = schema.get("required_control_plane_anchors")
    if (
        not isinstance(raw_anchor_contract, Mapping)
        or not raw_anchor_contract
        or not all(
            isinstance(lane_id, str)
            and isinstance(anchor, str)
            and lane_id
            and anchor
            for lane_id, anchor in (
                raw_anchor_contract.items()
                if isinstance(raw_anchor_contract, Mapping)
                else ()
            )
        )
    ):
        problems.append(
            "schema required_control_plane_anchors must be a non-empty string map"
        )
        anchor_contract: Mapping[str, str] = {}
    else:
        anchor_contract = raw_anchor_contract
        if set(anchor_contract) != required_lane_ids:
            problems.append(
                "schema required_control_plane_anchors must exactly cover "
                "required_lane_ids"
            )
        duplicate_anchors = sorted(
            anchor
            for anchor, count in Counter(anchor_contract.values()).items()
            if count > 1
        )
        if duplicate_anchors:
            problems.append(
                "schema required_control_plane_anchors contains duplicates: "
                + ", ".join(duplicate_anchors)
            )
    lane_ids = [row.get("lane_id", "") for row in rows]
    duplicates = sorted(
        lane_id for lane_id, count in Counter(lane_ids).items() if count > 1
    )
    if duplicates:
        problems.append("duplicate lane_id values: " + ", ".join(duplicates))
    missing = sorted(required_lane_ids - set(lane_ids))
    if missing:
        problems.append("missing required lane_id values: " + ", ".join(missing))
    extra = sorted(set(lane_ids) - required_lane_ids)
    if extra:
        problems.append("unregistered lane_id values: " + ", ".join(extra))
    for index, row in enumerate(rows, start=2):
        unexpected_columns = set(row) - set(required)
        if unexpected_columns or any(value is None for value in row.values()):
            problems.append(
                f"row {index}: cell cardinality must exactly match schema header"
            )
        if row.get("schema_version") != "productization_status_index_v1":
            problems.append(f"row {index}: invalid schema_version")
        if row.get("readiness_status") not in allowed_statuses:
            problems.append(
                f"row {index}: invalid readiness_status {row.get('readiness_status')!r}"
            )
        lane_id = row.get("lane_id", "")
        expected_anchor = anchor_contract.get(lane_id)
        if expected_anchor and row.get("control_plane_anchor") != expected_anchor:
            problems.append(
                f"row {index}: control_plane_anchor violates the stable "
                f"compatibility contract for {lane_id}"
            )
        for field in (
            "write_authority",
            "may_touch_matrix",
            "may_change_quant_output",
            "may_change_workbook",
            "may_change_selected_peak",
            "may_change_selected_area",
            "may_change_counted_detection",
        ):
            if row.get(field) not in {"TRUE", "FALSE"}:
                problems.append(f"row {index}: {field} must be TRUE or FALSE")


def _check_authority_boundaries(
    authority_rules: Mapping[str, Any],
    authority: Mapping[str, Any],
    rows: Sequence[Mapping[str, str]],
    problems: list[str],
) -> None:
    policy = authority.get("authority_policy")
    current = _mapping_path(authority, "current_authority", "backfill")
    if not isinstance(policy, Mapping) or not isinstance(current, Mapping):
        problems.append("authority manifest missing policy/current backfill")
        return
    if policy.get("unregistered_scope_policy") != authority_rules.get(
        "unregistered_scope_policy"
    ):
        problems.append("authority manifest unregistered scope policy drifted")
    raw_schema_scopes = authority_rules.get("allowed_product_writer_scopes", [])
    schema_scopes = {
        scope for scope in raw_schema_scopes if isinstance(scope, str) and scope
    }
    allowed_scopes = set(policy.get("product_writer_allowed_scopes", []))
    if allowed_scopes != schema_scopes:
        problems.append("authority manifest allowed scopes drifted from schema")
    expected_scopes = {item["scope"] for item in WRITER_LANE_AUTHORITY.values()}
    if schema_scopes != expected_scopes:
        problems.append("schema allowed scopes drifted from registered writer lanes")
    authority_rows = [row for row in rows if row.get("write_authority") == "TRUE"]
    writer_lane_ids = {row.get("lane_id", "") for row in authority_rows}
    expected_writer_lane_ids = set(WRITER_LANE_AUTHORITY)
    if writer_lane_ids != expected_writer_lane_ids:
        problems.append(
            "status index writer lanes mismatch: "
            f"expected {sorted(expected_writer_lane_ids)!r}, "
            f"found {sorted(writer_lane_ids)!r}",
        )
    for row in authority_rows:
        lane_id = row.get("lane_id", "")
        expected = WRITER_LANE_AUTHORITY.get(lane_id)
        if expected is None:
            problems.append(f"unregistered writer lane: {lane_id}")
            continue
        if row.get("product_authority_scope") != expected["scope"]:
            problems.append("writer row has unexpected product_authority_scope")
        current_scope = _mapping_path(
            authority,
            "current_authority",
            expected["authority_key"],
        )
        if not isinstance(current_scope, Mapping):
            problems.append(f"authority manifest missing {expected['authority_key']}")
            continue
        if row.get("row_count") != str(
            current_scope.get("current_product_authority_rows")
        ):
            problems.append("writer row_count does not match authority manifest")
        if row.get("may_touch_matrix") != "TRUE":
            problems.append("writer row must explicitly mark matrix touch")

    for index, row in enumerate(rows, start=2):
        status = row.get("readiness_status", "")
        lane_group = row.get("lane_group", "")
        write = row.get("write_authority") == "TRUE"
        scope = row.get("product_authority_scope", "")
        output_change_flags = {
            field: row.get(field)
            for field in (
                "may_change_quant_output",
                "may_change_workbook",
                "may_change_selected_peak",
                "may_change_selected_area",
                "may_change_counted_detection",
            )
        }
        output_change = any(value == "TRUE" for value in output_change_flags.values())
        is_writer_lane = row.get("lane_id") in WRITER_LANE_AUTHORITY
        if not is_writer_lane:
            if row.get("may_touch_matrix") == "TRUE":
                problems.append(f"row {index}: non-writer row may not touch matrix")
            if scope:
                problems.append(f"row {index}: non-writer row has authority scope")
            if output_change:
                changed = sorted(
                    field
                    for field, value in output_change_flags.items()
                    if value == "TRUE"
                )
                problems.append(
                    f"row {index}: non-writer row changes product output: "
                    + ", ".join(changed)
                )
        if status in WRITE_FORBIDDEN_STATUSES and (
            write or scope or row.get("may_touch_matrix") == "TRUE" or output_change
        ):
            problems.append(f"row {index}: {status} lane grants authority")
        if lane_group in NON_AUTHORITY_LANE_GROUPS and (
            write or scope or row.get("may_touch_matrix") == "TRUE" or output_change
        ):
            problems.append(f"row {index}: {lane_group} lane grants authority")
        if scope and scope not in allowed_scopes:
            problems.append(f"row {index}: unregistered authority scope {scope!r}")
        if row.get("lane_id") == "broad_backfill_autowrite":
            if status != "parked" or write or scope:
                problems.append(
                    "broad_backfill_autowrite must stay parked/no authority"
                )
        if row.get("lane_id") == "quality_explanation_sidecar_v1":
            if status != "diagnostic_only" or write or scope:
                problems.append(
                    "quality_explanation_sidecar_v1 must be diagnostic only"
                )


def _check_artifacts(
    rows: Sequence[Mapping[str, str]],
    repo_root: Path,
    problems: list[str],
) -> None:
    for index, row in enumerate(rows, start=2):
        artifact = row.get("current_artifact", "")
        expected_hash = row.get("artifact_sha256", "")
        if not artifact or artifact.startswith("vault:"):
            continue
        path = repo_root / artifact
        if not path.exists():
            problems.append(f"row {index}: artifact does not exist: {artifact}")
            continue
        if not expected_hash:
            problems.append(f"row {index}: artifact_sha256 is required")
        elif artifact_sha256(path) != expected_hash:
            problems.append(f"row {index}: artifact_sha256 mismatch for {artifact}")


def _check_doc_anchors(
    rows: Sequence[Mapping[str, str]],
    handoff_text: str,
    control_plane_text: str,
    problems: list[str],
) -> None:
    control_headings = Counter(_docs_scan.markdown_headings(control_plane_text))
    declared_control_anchors = [row.get("control_plane_anchor", "") for row in rows]
    duplicate_declared_anchors = sorted(
        anchor
        for anchor, count in Counter(declared_control_anchors).items()
        if anchor and count > 1
    )
    for anchor in duplicate_declared_anchors:
        problems.append(f"control-plane anchor is assigned to multiple rows: {anchor}")
    for index, row in enumerate(rows, start=2):
        lane_id = row.get("lane_id", "")
        handoff_anchor = row.get("handoff_anchor", "")
        control_anchor = row.get("control_plane_anchor", "")
        if not handoff_anchor:
            problems.append(f"row {index}: handoff anchor is required")
        if not control_anchor:
            problems.append(f"row {index}: control-plane anchor is required")
        if (
            handoff_anchor
            and handoff_anchor not in handoff_text
            and lane_id not in handoff_text
        ):
            problems.append(f"row {index}: handoff anchor not found")
        if control_anchor and control_headings[control_anchor] != 1:
            problems.append(
                f"row {index}: control-plane anchor must be an exact unique "
                f"Markdown heading (found {control_headings[control_anchor]})"
            )
        lane_key = f"Lane key: `{lane_id}`"
        if lane_id and control_plane_text.count(lane_key) != 1:
            problems.append(
                f"row {index}: compatibility index must contain exactly one "
                f"stable lane key"
            )


def _check_compatibility_index(
    rows: Sequence[Mapping[str, str]],
    control_plane_text: str,
    problems: list[str],
) -> None:
    """Require the compatibility surface to stay a closed generated index."""

    expected = _render_compatibility_index(rows)
    if control_plane_text != expected:
        problems.append(
            f"{CONTROL_PLANE_PATH}: compatibility index must exactly match the "
            "closed metadata, authority routes, historical pointer, and lane blocks"
        )


def _render_compatibility_index(rows: Sequence[Mapping[str, str]]) -> str:
    lines = [
        "# Productization compatibility index",
        "",
        "Doc placement: formal_repo_doc",
        "Doc kind: manifest",
        "Doc lifecycle: active",
        "Repo owner: docs/product/productization.md",
        "Doc exit rule: retain while stable control-plane path references exist.",
        "",
        (
            "This path is a compatibility lookup surface. It preserves stable lane "
            "keys and"
        ),
        (
            "historical anchor names; it does not own mutable lane state, counts, "
            "authority,"
        ),
        "promotion decisions, or product rules.",
        "",
        (
            "When a hook or older referrer says to update the productization control "
            "plane,"
        ),
        "treat that phrase as an instruction to consult this index and update the",
        "applicable authority route. Modify this file only when a stable lane key or",
        "owner route changes.",
        "",
        "## Authority routes",
        "",
        "- [Lane-state index](../validation/productization_status_index_v1.tsv)",
        (
            "- [Writer-authority manifest]"
            "(../schemas/productization_authority_manifest.v1.json)"
        ),
        (
            "- [Structured control schema]"
            f"(../schemas/{Path(PRODUCTIZATION_SCHEMA_REL).name})"
        ),
        (
            "- [Readiness and promotion contract]"
            "(../../agent/product-validation-contract.md)"
        ),
        "- [Stable capability map](../../product/productization.md)",
        "",
        "## Historical source",
        "",
        "The exact retired body is retained privately under",
        f"`source_repo_path:{CONTROL_PLANE_PATH}`.",
        "Vault note title:",
        f"`{PRODUCTIZATION_CONTROL_PLANE_SOURCE_COPY_NOTE_TITLE}`.",
        "Its searchable locator note is",
        f"`{PRODUCTIZATION_CONTROL_PLANE_SOURCE_COPY_LOCATOR_NOTE_TITLE}`; the",
        "locator carries the stable `source_repo_path:` key while the source note "
        "stays",
        "byte-for-byte identical to the retired body.",
        "The checked retirement transaction is recorded in the",
        (
            "[retirement evidence packet](../file-management/docs-cleanup/"
            "2026-08-01-r11-d0-productization-control-plane-retirement-evidence.json)."
        ),
        "",
        "## Lane anchor index",
        "",
    ]
    routes = (
        "Routes: [state](../validation/productization_status_index_v1.tsv), "
        "[writer authority](../schemas/productization_authority_manifest.v1.json), "
        "[stable capability](../../product/productization.md)."
    )
    for row in rows:
        lines.extend(
            (
                f"### {row.get('control_plane_anchor', '')}",
                "",
                f"Lane key: `{row.get('lane_id', '')}`",
                "",
                routes,
                "",
            )
        )
    return "\n".join(lines)


def _contains_normalized_phrase(text: str, phrase: str) -> bool:
    text_tokens = tuple(re.findall(r"[a-z0-9]+", text.casefold()))
    phrase_tokens = tuple(re.findall(r"[a-z0-9]+", phrase.casefold()))
    if not phrase_tokens or len(phrase_tokens) > len(text_tokens):
        return False
    width = len(phrase_tokens)
    return any(
        text_tokens[index : index + width] == phrase_tokens
        for index in range(len(text_tokens) - width + 1)
    )


def _contains_structured_field_assignment(text: str, field_name: str) -> bool:
    """Return whether narrative text assigns a schema-derived mutable field."""

    tokens = re.findall(r"[a-z0-9]+", field_name.casefold())
    if not tokens:
        return False
    field_pattern = r"[\s_-]+".join(re.escape(token) for token in tokens)
    return (
        re.search(
            rf"^\s*(?:[-*]\s*)?(?:\*\*|`)?{field_pattern}"
            rf"(?:\*\*|`)?\s*[:=]",
            text,
            re.I | re.M,
        )
        is not None
    )


def _check_narrative_authority_partition(
    schema: Mapping[str, Any],
    rows: Sequence[Mapping[str, str]],
    productization_summary_text: str,
    control_plane_text: str,
    status_anchor_text: str,
    problems: list[str],
) -> None:
    """Reject mutable structured state copied into narrative routing surfaces."""

    narrative_surfaces = {
        PRODUCTIZATION_SUMMARY_REL: productization_summary_text,
        CONTROL_PLANE_PATH: "\n".join(
            line
            for line in control_plane_text.splitlines()
            if not line.lstrip().startswith("#")
        ),
        PRODUCTIZATION_STATUS_ANCHOR_PATH: status_anchor_text,
    }
    forbidden_literals = {
        row.get("product_authority_scope", "")
        for row in rows
        if row.get("product_authority_scope", "")
    }
    stable_or_routing_fields = {
        "schema_version",
        "lane_id",
        "control_plane_anchor",
        "handoff_anchor",
    }
    required_columns = schema.get("required_status_index_columns", [])
    mutable_field_names = {
        column
        for column in required_columns
        if isinstance(column, str)
        and column
        and column not in stable_or_routing_fields
    }
    forbidden_literals.update(
        status
        for status in schema.get("allowed_readiness_statuses", [])
        if isinstance(status, str) and status
    )
    for path, text in narrative_surfaces.items():
        for field_name in sorted(mutable_field_names):
            if _contains_structured_field_assignment(text, field_name):
                problems.append(
                    f"{path}: narrative surface duplicates mutable structured "
                    f"state or authority: {field_name}"
                )
        for literal in sorted(forbidden_literals):
            if _contains_normalized_phrase(text, literal):
                problems.append(
                    f"{path}: narrative surface duplicates mutable structured "
                    f"state or authority: {literal}"
                )
        if re.search(r"\b\d+\s+(?:accepted\s+)?(?:rows?|cells?)\b", text, re.I):
            problems.append(
                f"{path}: narrative surface duplicates a mutable row/cell count"
            )


def _structured_integer_values(value: object) -> set[str]:
    values: set[str] = set()
    if isinstance(value, bool):
        return values
    if isinstance(value, int):
        values.add(str(value))
    elif isinstance(value, Mapping):
        for item in value.values():
            values.update(_structured_integer_values(item))
    elif isinstance(value, list):
        for item in value:
            values.update(_structured_integer_values(item))
    return values


def _duplicates_structured_count(text: str, count: str) -> bool:
    tokens = tuple(re.findall(r"[a-z0-9]+", text.casefold()))
    for index, token in enumerate(tokens):
        if token != count:
            continue
        if any(
            item in {"row", "rows", "cell", "cells"}
            for item in tokens[index + 1 : index + 4]
        ):
            return True
    return False


def _narrative_units(text: str) -> tuple[str, ...]:
    """Join wrapped prose while keeping bullets and headings as unit boundaries."""

    units: list[str] = []
    current: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                units.append(" ".join(current))
                current = []
            continue
        starts_unit = line.startswith(("#", "- ", "* ", "|"))
        if starts_unit and current:
            units.append(" ".join(current))
            current = []
        current.append(line)
    if current:
        units.append(" ".join(current))
    return tuple(units)


def _readiness_assignment(
    text: str,
    row: Mapping[str, str],
    readiness_statuses: Sequence[str],
) -> str | None:
    identity_text = " ".join(
        row.get(field, "")
        for field in ("lane_id", "control_plane_anchor", "handoff_anchor")
    )
    readiness_tokens = {
        token
        for status in readiness_statuses
        for token in re.findall(r"[a-z0-9]+", status.casefold())
    }
    identity_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", identity_text.casefold())
        if token not in READINESS_IDENTITY_STOP_WORDS
        and token not in readiness_tokens
        and len(token) > 1
    }
    if len(identity_tokens) < 2:
        return None
    for unit in _narrative_units(text):
        unit_tokens = re.findall(r"[a-z0-9]+", unit.casefold())
        for status in readiness_statuses:
            status_tokens = re.findall(r"[a-z0-9]+", status.casefold())
            width = len(status_tokens)
            for index in range(len(unit_tokens) - width + 1):
                if unit_tokens[index : index + width] != status_tokens:
                    continue
                preceding = unit_tokens[max(0, index - 5) : index]
                has_assignment_verb = bool(set(preceding) & {
                    "are",
                    "is",
                    "remain",
                    "remains",
                    "stay",
                    "stays",
                })
                local_identity_count = len(
                    identity_tokens
                    & set(unit_tokens[max(0, index - 12) : index])
                )
                adjacent_identity_count = len(
                    identity_tokens
                    & set(unit_tokens[max(0, index - 4) : index])
                )
                if (
                    has_assignment_verb and local_identity_count >= 2
                ) or adjacent_identity_count >= 2:
                    return status
    return None


def _check_product_topic_authority_partition(
    schema: Mapping[str, Any],
    authority: Mapping[str, Any],
    rows: Sequence[Mapping[str, str]],
    product_topic_dir: Path,
    repo_root: Path,
    problems: list[str],
) -> None:
    """Keep mutable structured counts/scopes/readiness out of topic owners."""

    if not product_topic_dir.is_dir():
        return
    structured_counts = _structured_integer_values(authority)
    authority_scopes = {
        row.get("product_authority_scope", "")
        for row in rows
        if row.get("product_authority_scope", "")
    }
    readiness_statuses = [
        status
        for status in schema.get("allowed_readiness_statuses", [])
        if isinstance(status, str) and status
    ]
    topic_paths = [(path, False) for path in sorted(product_topic_dir.glob("*.md"))]
    for rel_path in ADDITIONAL_PRODUCTIZATION_NARRATIVE_OWNER_PATHS:
        path = repo_root / rel_path
        if path.is_file():
            topic_paths.append((path, True))
    for path, accepts_all_lane_groups in topic_paths:
        text = _read_text(path, problems)
        try:
            rel_path = path.relative_to(repo_root).as_posix()
        except ValueError:
            rel_path = f"docs/product/{path.name}"
        for scope in sorted(authority_scopes):
            if _contains_normalized_phrase(text, scope):
                problems.append(
                    f"{rel_path}: product topic owner duplicates mutable "
                    f"authority scope: {scope}"
                )
        for count in sorted(structured_counts, key=lambda item: (len(item), item)):
            if _duplicates_structured_count(text, count):
                problems.append(
                    f"{rel_path}: product topic owner duplicates mutable "
                    f"structured row/cell count: {count}"
                )
        for row in rows:
            if not accepts_all_lane_groups and (
                PRODUCT_TOPIC_BY_LANE_GROUP.get(row.get("lane_group", ""))
                != path.name
            ):
                continue
            duplicated_status = _readiness_assignment(
                text,
                row,
                readiness_statuses,
            )
            if duplicated_status is not None:
                problems.append(
                    f"{rel_path}: product topic owner duplicates mutable "
                    f"readiness for {row.get('lane_id', '')}: {duplicated_status}"
                )


def _mapping_path(root: Mapping[str, Any], *keys: str) -> Mapping[str, Any] | None:
    current: Any = root
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current if isinstance(current, Mapping) else None


def _read_json(path: Path, problems: list[str]) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        problems.append(f"failed to read {path}: {exc}")
    except json.JSONDecodeError as exc:
        problems.append(f"invalid JSON {path}: {exc}")
    return {}


def _read_tsv(
    path: Path,
    problems: list[str],
) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            return list(reader.fieldnames or []), list(reader)
    except OSError as exc:
        problems.append(f"failed to read {path}: {exc}")
        return [], []


def _read_text(path: Path, problems: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        problems.append(f"failed to read {path}: {exc}")
        return ""


def artifact_sha256(path: Path) -> str:
    """Hash productization artifacts independently of checkout line endings."""

    normalized_path = f"/{path.as_posix()}"
    if path.suffix.lower() in CANONICAL_TEXT_HASH_EXTENSIONS and (
        "/docs/superpowers/" in normalized_path
        or "/output/productization_realdata_seed_guard_85raw_20260617/"
        "generated_policy_quality_explained_no_raw_productization/"
        in normalized_path
    ):
        data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        return hashlib.sha256(data).hexdigest().upper()
    return _sha256(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--status-index", type=Path, default=DEFAULT_STATUS_INDEX)
    parser.add_argument(
        "--authority-manifest",
        type=Path,
        default=DEFAULT_AUTHORITY_MANIFEST,
    )
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--control-plane", type=Path, default=DEFAULT_CONTROL_PLANE)
    args = parser.parse_args(argv)
    problems = check_productization_state(
        schema_path=args.schema,
        status_index_path=args.status_index,
        authority_manifest_path=args.authority_manifest,
        handoff_path=args.handoff,
        control_plane_path=args.control_plane,
    )
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("Productization state index is consistent and fail-closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
