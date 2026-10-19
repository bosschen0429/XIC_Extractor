import csv
import json
from pathlib import Path

import pytest

from scripts.check_productization_state import (
    CONTROL_PLANE_SCHEMA_FIELDS,
    DEFAULT_AUTHORITY_MANIFEST,
    DEFAULT_CONTROL_PLANE,
    DEFAULT_HANDOFF,
    DEFAULT_PRODUCTIZATION_SUMMARY,
    DEFAULT_SCHEMA,
    DEFAULT_STATUS_INDEX,
    EXPECTED_CONTROL_PLANE_SCHEMA_VERSION,
    REQUIRED_LANE_IDS,
    STATUS_INDEX_FIELDS,
    _render_compatibility_index,
    artifact_sha256,
    check_productization_state,
)
from tools.diagnostics.docs_scan import markdown_headings

pytestmark = pytest.mark.product_governance


@pytest.mark.ledger_snapshot
def test_productization_state_index_accepts_current_artifacts() -> None:
    assert check_productization_state() == []


def test_status_schema_matches_index_header() -> None:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    header, rows = _read_tsv(DEFAULT_STATUS_INDEX)

    assert header == schema["required_status_index_columns"]
    assert {row["lane_id"] for row in rows} == set(schema["required_lane_ids"])
    assert len(rows) == len(schema["required_lane_ids"])
    assert DEFAULT_SCHEMA.name == "productization_control_plane_schema.json"
    assert schema["schema_version"] == EXPECTED_CONTROL_PLANE_SCHEMA_VERSION
    assert tuple(schema) == CONTROL_PLANE_SCHEMA_FIELDS
    assert tuple(header) == STATUS_INDEX_FIELDS
    assert tuple(schema["required_lane_ids"]) == REQUIRED_LANE_IDS
    assert len(rows) == len(REQUIRED_LANE_IDS)
    assert schema["required_control_plane_anchors"] == {
        row["lane_id"]: row["control_plane_anchor"] for row in rows
    }


def test_compatibility_index_preserves_exact_unique_status_anchors() -> None:
    text = DEFAULT_CONTROL_PLANE.read_text(encoding="utf-8")
    headings = markdown_headings(text)
    _, rows = _read_tsv(DEFAULT_STATUS_INDEX)

    for row in rows:
        assert headings.count(row["control_plane_anchor"]) == 1
        assert text.count(f"Lane key: `{row['lane_id']}`") == 1


def test_checker_rejects_lane_id_fallback_without_exact_heading(
    tmp_path: Path,
) -> None:
    text = DEFAULT_CONTROL_PLANE.read_text(encoding="utf-8")
    _, rows = _read_tsv(DEFAULT_STATUS_INDEX)
    row = rows[0]
    text = text.replace(
        f"## {row['control_plane_anchor']}",
        f"This prose retains {row['control_plane_anchor']} and {row['lane_id']}.",
        1,
    )
    mutated = tmp_path / "compatibility-index.md"
    mutated.write_text(text, encoding="utf-8")

    problems = _check_with_control_plane(mutated)

    assert any("exact unique Markdown heading" in problem for problem in problems)


@pytest.mark.parametrize(
    "state_assignment",
    (
        "readiness_status=production_candidate",
        "READINESS STATUS = BLOCKED",
    ),
)
def test_checker_rejects_narrative_structured_state_duplication(
    tmp_path: Path,
    state_assignment: str,
) -> None:
    summary = tmp_path / "productization.md"
    summary.write_text(
        DEFAULT_PRODUCTIZATION_SUMMARY.read_text(encoding="utf-8")
        + f"\n{state_assignment}\n",
        encoding="utf-8",
    )

    problems = check_productization_state(productization_summary_path=summary)

    assert any("duplicates mutable structured state" in problem for problem in problems)


@pytest.mark.parametrize(
    "readiness_text",
    (
        "Production_Ready",
        "PRODUCTION_READY",
        "production ready",
        "production-ready",
        "BLOCKED",
        "Parked",
        "FROZEN",
    ),
)
def test_checker_normalizes_narrative_readiness_vocabulary(
    tmp_path: Path,
    readiness_text: str,
) -> None:
    summary = tmp_path / "productization.md"
    summary.write_text(
        DEFAULT_PRODUCTIZATION_SUMMARY.read_text(encoding="utf-8")
        + f"\nCurrent lane is {readiness_text}.\n",
        encoding="utf-8",
    )

    problems = check_productization_state(productization_summary_path=summary)

    assert any("duplicates mutable structured state" in problem for problem in problems)


@pytest.mark.parametrize(
    "mutable_text",
    (
        "Current lane is production_ready.",
        "The current authority contains 511 cells.",
    ),
)
def test_checker_rejects_mutable_state_in_compatibility_lane_registry(
    tmp_path: Path,
    mutable_text: str,
) -> None:
    status_anchor = tmp_path / "productization-status-anchor.md"
    status_anchor.write_text(
        DEFAULT_HANDOFF.read_text(encoding="utf-8") + f"\n{mutable_text}\n",
        encoding="utf-8",
    )

    problems = check_productization_state(handoff_path=status_anchor)

    assert any("mutable" in problem for problem in problems)


@pytest.mark.parametrize(
    "mutable_field",
    (
        "Lane group: product",
        "Readiness status: production candidate",
        "Owner: docs/product/example.md",
        "Current artifact: docs/superpowers/validation/example.json",
        "Artifact sha256: deadbeef",
        "Row count: 1",
        "Write authority: false",
        "Product authority scope: example",
        "May touch matrix: false",
        "May change quant output: false",
        "May change workbook: false",
        "May change selected peak: false",
        "May change selected area: false",
        "May change counted detection: false",
        "Product effect: diagnostic only",
        "Public surface: example",
        "Next checkpoint: promote after review",
        "Expiration condition: replacement is accepted",
        "Notes: mutable narrative state",
    ),
)
def test_checker_derives_narrative_mutable_fields_from_schema(
    tmp_path: Path,
    mutable_field: str,
) -> None:
    status_anchor = tmp_path / "productization-status-anchor.md"
    status_anchor.write_text(
        DEFAULT_HANDOFF.read_text(encoding="utf-8") + f"\n{mutable_field}\n",
        encoding="utf-8",
    )

    problems = check_productization_state(handoff_path=status_anchor)

    assert any("duplicates mutable structured state" in problem for problem in problems)


def test_checker_rejects_unsupported_control_plane_schema(tmp_path: Path) -> None:
    payload = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    payload["schema_version"] = "unsupported_control_plane"
    predecessor = tmp_path / "unsupported_schema.json"
    predecessor.write_text(json.dumps(payload), encoding="utf-8")

    problems = check_productization_state(schema_path=predecessor)

    assert any(
        "unsupported productization control-plane" in problem for problem in problems
    )


@pytest.mark.parametrize("mutation", ("missing", "extra", "reordered"))
def test_checker_rejects_schema_top_level_shape(
    tmp_path: Path,
    mutation: str,
) -> None:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    if mutation == "missing":
        del schema["purpose"]
    elif mutation == "extra":
        schema["future_state_owner"] = "forbidden"
    else:
        schema = {key: schema[key] for key in reversed(tuple(schema))}
    mutated = tmp_path / f"schema-{mutation}.json"
    mutated.write_text(json.dumps(schema), encoding="utf-8")

    problems = check_productization_state(schema_path=mutated)

    assert any("ordered contract" in problem for problem in problems)


def test_checker_rejects_duplicate_schema_json_key(tmp_path: Path) -> None:
    text = DEFAULT_SCHEMA.read_text(encoding="utf-8")
    text = text.replace(
        '  "purpose":',
        '  "schema_version": "productization_control_plane_schema",\n  "purpose":',
        1,
    )
    mutated = tmp_path / "schema-duplicate.json"
    mutated.write_text(text, encoding="utf-8")

    problems = check_productization_state(schema_path=mutated)

    assert any("duplicate JSON object key" in problem for problem in problems)


@pytest.mark.parametrize("mutation", ("missing", "extra", "duplicate", "reordered"))
def test_checker_rejects_non_exact_lane_registry(
    tmp_path: Path,
    mutation: str,
) -> None:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    lane_ids = schema["required_lane_ids"]
    if mutation == "missing":
        lane_ids.pop()
    elif mutation == "extra":
        lane_ids.append("unregistered_future_lane")
    elif mutation == "duplicate":
        lane_ids.append(lane_ids[-1])
    else:
        lane_ids[-2], lane_ids[-1] = lane_ids[-1], lane_ids[-2]
    mutated = tmp_path / f"lanes-{mutation}.json"
    mutated.write_text(json.dumps(schema), encoding="utf-8")

    problems = check_productization_state(schema_path=mutated)

    expected = f"exact ordered {len(REQUIRED_LANE_IDS)}-lane"
    assert any(expected in problem for problem in problems)


@pytest.mark.parametrize("mutation", ("missing", "extra", "duplicate", "reordered"))
def test_checker_rejects_non_exact_anchor_registry(
    tmp_path: Path,
    mutation: str,
) -> None:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    anchors = schema["required_control_plane_anchors"]
    anchor_items = list(anchors.items())
    if mutation == "missing":
        anchors.popitem()
    elif mutation == "extra":
        anchors["unregistered_future_lane"] = "Unregistered Future Lane"
    elif mutation == "duplicate":
        last_lane_id = anchor_items[-1][0]
        anchors[last_lane_id] = anchor_items[-2][1]
    else:
        anchor_items[-2], anchor_items[-1] = anchor_items[-1], anchor_items[-2]
        schema["required_control_plane_anchors"] = dict(anchor_items)
    mutated = tmp_path / f"anchors-{mutation}.json"
    mutated.write_text(json.dumps(schema), encoding="utf-8")

    problems = check_productization_state(schema_path=mutated)

    assert any(
        f"ordered {len(REQUIRED_LANE_IDS)}-anchor" in problem for problem in problems
    )


@pytest.mark.parametrize(
    ("mutation", "expected_problem"),
    (
        ("missing", "authority_rules must be an object"),
        ("extra", "authority_rules keys must exactly match"),
        ("open", "unregistered_scope_policy must be fail_closed"),
        ("scope", "allowed scopes drifted from registered writer lanes"),
        ("boolean", "quality_explanations_cannot_write must be true"),
        ("matrix_requirements", "matrix_write_requires must exactly match"),
        ("forbidden_sources", "forbidden_authority_sources must exactly match"),
    ),
)
def test_checker_rejects_schema_authority_rule_drift(
    tmp_path: Path,
    mutation: str,
    expected_problem: str,
) -> None:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    rules = schema["authority_rules"]
    if mutation == "missing":
        del schema["authority_rules"]
    elif mutation == "extra":
        rules["unimplemented_future_rule"] = True
    elif mutation == "open":
        rules["unregistered_scope_policy"] = "allow"
    elif mutation == "scope":
        rules["allowed_product_writer_scopes"].append("unregistered_writer")
    elif mutation == "matrix_requirements":
        rules["matrix_write_requires"].pop()
    elif mutation == "forbidden_sources":
        rules["forbidden_authority_sources"].pop()
    else:
        rules["quality_explanations_cannot_write"] = False
    mutated = tmp_path / "schema.json"
    mutated.write_text(json.dumps(schema), encoding="utf-8")

    problems = check_productization_state(schema_path=mutated)

    assert any(expected_problem in problem for problem in problems)


@pytest.mark.parametrize(
    "policy_key",
    ("matrix_write_requires", "forbidden_authority_sources"),
)
def test_checker_rejects_weakened_manifest_authority_policy(
    tmp_path: Path,
    policy_key: str,
) -> None:
    authority = json.loads(DEFAULT_AUTHORITY_MANIFEST.read_text(encoding="utf-8"))
    authority["authority_policy"][policy_key] = []
    mutated = tmp_path / "authority.json"
    mutated.write_text(json.dumps(authority), encoding="utf-8")

    problems = check_productization_state(authority_manifest_path=mutated)

    assert f"authority manifest {policy_key} drifted from schema" in problems


def test_checker_rejects_status_row_with_extra_cell(tmp_path: Path) -> None:
    lines = DEFAULT_STATUS_INDEX.read_text(encoding="utf-8").splitlines()
    lines[1] += "\tshadow_value"
    mutated = tmp_path / "productization-status.tsv"
    mutated.write_text("\n".join(lines) + "\n", encoding="utf-8")

    problems = check_productization_state(status_index_path=mutated)

    assert any(
        "row 2: cell cardinality must exactly match schema header" in problem
        for problem in problems
    )


def test_checker_rejects_regenerated_unregistered_control_anchor(
    tmp_path: Path,
) -> None:
    header, rows = _read_tsv(DEFAULT_STATUS_INDEX)
    rows[0]["control_plane_anchor"] = "Backfill is production_ready"
    status_index = tmp_path / "productization-status.tsv"
    with status_index.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    control_plane = tmp_path / "compatibility-index.md"
    control_plane.write_text(_render_compatibility_index(rows), encoding="utf-8")

    problems = check_productization_state(
        status_index_path=status_index,
        control_plane_path=control_plane,
    )

    assert any("stable compatibility contract" in problem for problem in problems)


def test_checker_rejects_state_bearing_compatibility_prose(tmp_path: Path) -> None:
    text = DEFAULT_CONTROL_PLANE.read_text(encoding="utf-8")
    text = text.replace(
        "## Lane anchor index",
        "Lane example remains parked for this run.\n\n## Lane anchor index",
        1,
    )
    mutated = tmp_path / "compatibility-index.md"
    mutated.write_text(text, encoding="utf-8")

    problems = _check_with_control_plane(mutated)

    assert any("closed metadata" in problem for problem in problems)


@pytest.mark.parametrize(
    "readiness_text",
    (
        "quality_explanation_sidecar remains diagnostic_only",
        "quality_explanation_sidecar is blocked",
        "Quality explanation sidecar is production-ready",
        "Quality explanation sidecar — diagnostic_only",
        "Quality explanation sidecar: blocked",
    ),
)
def test_checker_rejects_product_topic_lane_readiness_assignment(
    tmp_path: Path,
    readiness_text: str,
) -> None:
    product_topics = tmp_path / "docs/product"
    product_topics.mkdir(parents=True)
    (product_topics / "backfill.md").write_text(
        f"# Backfill\n\n- {readiness_text}.\n",
        encoding="utf-8",
    )

    problems = check_productization_state(product_topic_dir=product_topics)

    assert any(
        "product topic owner duplicates mutable readiness for "
        "quality_explanation_sidecar" in problem
        for problem in problems
    )


def test_checker_allows_non_lane_artifact_readiness_label(tmp_path: Path) -> None:
    product_topics = tmp_path / "docs/product"
    product_topics.mkdir(parents=True)
    (product_topics / "backfill.md").write_text(
        "# Backfill\n\nThis validation artifact remains diagnostic_only.\n",
        encoding="utf-8",
    )

    problems = check_productization_state(product_topic_dir=product_topics)

    assert not any(
        "product topic owner duplicates mutable readiness" in problem
        for problem in problems
    )


def test_checker_rejects_compatibility_owner_route_drift(tmp_path: Path) -> None:
    text = DEFAULT_CONTROL_PLANE.read_text(encoding="utf-8")
    text = text.replace(
        "../productization/status/productization_authority_manifest.json",
        "../schemas/wrong-authority.json",
        1,
    )
    mutated = tmp_path / "compatibility-index.md"
    mutated.write_text(text, encoding="utf-8")

    problems = _check_with_control_plane(mutated)

    assert any("closed metadata" in problem for problem in problems)


def test_checker_rejects_diagnostic_sidecar_authority(tmp_path: Path) -> None:
    mutated = _mutated_index(
        tmp_path,
        "quality_explanation_sidecar",
        {
            "write_authority": "TRUE",
            "product_authority_scope": "backfill_policy_write_ready_rows",
            "may_touch_matrix": "TRUE",
        },
    )

    problems = _check_with_index(mutated)

    assert any("lane grants authority" in problem for problem in problems)


def test_checker_rejects_non_writer_scope_without_write_flag(tmp_path: Path) -> None:
    mutated = _mutated_index(
        tmp_path,
        "quality_explanation_sidecar",
        {"product_authority_scope": "backfill_policy_write_ready_rows"},
    )

    problems = _check_with_index(mutated)

    assert any("non-writer row has authority scope" in problem for problem in problems)


def test_checker_rejects_non_writer_matrix_touch_without_write_flag(
    tmp_path: Path,
) -> None:
    mutated = _mutated_index(
        tmp_path,
        "quality_explanation_sidecar",
        {
            "write_authority": "FALSE",
            "product_authority_scope": "",
            "may_touch_matrix": "TRUE",
        },
    )

    problems = _check_with_index(mutated)

    assert any("non-writer row may not touch matrix" in problem for problem in problems)


def test_checker_rejects_non_writer_product_output_changes(tmp_path: Path) -> None:
    mutated = _mutated_index(
        tmp_path,
        "quality_explanation_sidecar",
        {
            "may_change_workbook": "TRUE",
            "may_change_selected_peak": "TRUE",
            "may_change_selected_area": "TRUE",
            "may_change_counted_detection": "TRUE",
        },
    )

    problems = _check_with_index(mutated)

    assert any(
        "non-writer row changes product output" in problem for problem in problems
    )


def test_checker_rejects_blank_doc_anchors(tmp_path: Path) -> None:
    mutated = _mutated_index(
        tmp_path,
        "productization_authority_firewall",
        {"handoff_anchor": "", "control_plane_anchor": ""},
    )

    problems = _check_with_index(mutated)

    assert any("handoff anchor is required" in problem for problem in problems)
    assert any("control-plane anchor is required" in problem for problem in problems)


def test_checker_rejects_artifact_hash_drift(tmp_path: Path) -> None:
    mutated = _mutated_index(
        tmp_path,
        "productization_authority_firewall",
        {"artifact_sha256": "0" * 64},
    )

    problems = _check_with_index(mutated)

    assert any("artifact_sha256 mismatch" in problem for problem in problems)


def test_text_artifact_hash_canonicalizes_checkout_line_endings(tmp_path: Path) -> None:
    artifact = tmp_path / "docs/superpowers/validation/artifact.tsv"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"column\nvalue\n")
    expected_hash = artifact_sha256(artifact)

    artifact.write_bytes(b"column\r\nvalue\r\n")

    assert artifact_sha256(artifact) == expected_hash


def test_productization_doc_hash_canonicalizes_checkout_line_endings(
    tmp_path: Path,
) -> None:
    for relative in (
        "docs/superpowers/schemas/artifact.json",
        "docs/superpowers/notes/artifact.md",
    ):
        artifact = tmp_path / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"line one\nline two\n")
        expected_hash = artifact_sha256(artifact)

        artifact.write_bytes(b"line one\r\nline two\r\n")

        assert artifact_sha256(artifact) == expected_hash


def _check_with_index(path: Path) -> list[str]:
    return check_productization_state(
        schema_path=DEFAULT_SCHEMA,
        status_index_path=path,
        authority_manifest_path=DEFAULT_AUTHORITY_MANIFEST,
        handoff_path=DEFAULT_HANDOFF,
        control_plane_path=DEFAULT_CONTROL_PLANE,
    )


def _check_with_control_plane(path: Path) -> list[str]:
    return check_productization_state(control_plane_path=path)


def _mutated_index(
    tmp_path: Path,
    lane_id: str,
    updates: dict[str, str],
) -> Path:
    header, rows = _read_tsv(DEFAULT_STATUS_INDEX)
    for row in rows:
        if row["lane_id"] == lane_id:
            row.update(updates)
            break
    else:
        raise AssertionError(f"lane not found: {lane_id}")
    output = tmp_path / f"{lane_id}.tsv"
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return output


def _read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)
