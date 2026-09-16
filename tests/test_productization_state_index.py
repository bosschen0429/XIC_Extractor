import csv
import json
from pathlib import Path

import pytest

from scripts.check_productization_state import (
    CONTROL_PLANE_SCHEMA_FIELDS_V3,
    DEFAULT_AUTHORITY_MANIFEST,
    DEFAULT_CONTROL_PLANE,
    DEFAULT_HANDOFF,
    DEFAULT_PRODUCTIZATION_SUMMARY,
    DEFAULT_SCHEMA,
    DEFAULT_STATUS_INDEX,
    DEPENDENT_EFFECT_LANE_AUTHORITY_FIELDS_V1,
    DEPENDENT_EFFECT_LANE_ID,
    DEPENDENT_PACKAGE_AUTHORITY_ARTIFACT,
    DEPENDENT_PACKAGE_AUTHORITY_SHA256,
    EXPECTED_CONTROL_PLANE_SCHEMA_VERSION,
    EXPECTED_HOLD_ROW,
    REQUIRED_LANE_IDS_V3,
    STATUS_INDEX_FIELDS_V1,
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
    assert DEFAULT_SCHEMA.name == "productization_control_plane_schema.v3.json"
    assert schema["schema_version"] == EXPECTED_CONTROL_PLANE_SCHEMA_VERSION
    assert tuple(schema) == CONTROL_PLANE_SCHEMA_FIELDS_V3
    assert tuple(header) == STATUS_INDEX_FIELDS_V1
    assert tuple(schema["required_lane_ids"]) == REQUIRED_LANE_IDS_V3
    assert len(rows) == len(REQUIRED_LANE_IDS_V3) == 15
    assert schema["required_control_plane_anchors"] == {
        row["lane_id"]: row["control_plane_anchor"] for row in rows
    }


@pytest.mark.ledger_snapshot
def test_dependent_effect_lane_is_exact_hold_package_binding() -> None:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    _, rows = _read_tsv(DEFAULT_STATUS_INDEX)
    dependent_rule = schema["dependent_effect_lane_authority"][DEPENDENT_EFFECT_LANE_ID]
    row = next(item for item in rows if item["lane_id"] == DEPENDENT_EFFECT_LANE_ID)

    assert tuple(dependent_rule) == DEPENDENT_EFFECT_LANE_AUTHORITY_FIELDS_V1
    assert row == EXPECTED_HOLD_ROW
    assert row["current_artifact"] == DEPENDENT_PACKAGE_AUTHORITY_ARTIFACT
    assert row["artifact_sha256"] == DEPENDENT_PACKAGE_AUTHORITY_SHA256
    assert artifact_sha256(Path(__file__).parents[1] / row["current_artifact"]) == (
        DEPENDENT_PACKAGE_AUTHORITY_SHA256
    )
    assert row["readiness_status"] == "production_candidate"
    assert row["write_authority"] == "FALSE"
    assert row["product_authority_scope"] == ""
    assert row["row_count"] == "0"


@pytest.mark.ledger_snapshot
def test_only_revalidated_default_scope_has_writer_authority() -> None:
    _, rows = _read_tsv(DEFAULT_STATUS_INDEX)
    authority_rows = [row for row in rows if row["write_authority"] == "TRUE"]

    assert {row["lane_id"] for row in authority_rows} == {
        "backfill_current_write_ready_scope",
    }
    authority_by_lane = {row["lane_id"]: row for row in authority_rows}

    backfill = authority_by_lane["backfill_current_write_ready_scope"]
    assert backfill["current_artifact"] == (
        "docs/superpowers/validation/quant_matrix_default_product_activation_v1/"
        "quant_matrix_default_product_activation_summary.json"
    )
    assert backfill["product_authority_scope"] == "backfill_policy_write_ready_rows"
    assert int(backfill["row_count"]) > 0
    assert backfill["may_touch_matrix"] == "TRUE"
    assert backfill["may_change_quant_output"] == "TRUE"
    assert backfill["may_change_workbook"] == "FALSE"
    assert backfill["may_change_selected_peak"] == "FALSE"
    assert backfill["may_change_selected_area"] == "FALSE"
    assert backfill["may_change_counted_detection"] == "FALSE"
    assert backfill["product_effect"] == "default_quant_matrix_product_output"
    assert backfill["public_surface"] == "quant_matrix_default_product_activation_v1"

    cid_nl = next(
        row for row in rows if row["lane_id"] == "cid_nl_default_product_activation_v1"
    )
    assert cid_nl["current_artifact"] == (
        "docs/superpowers/validation/cid_nl_default_product_activation_v1/"
        "cid_nl_default_product_activation_summary.json"
    )
    assert cid_nl["readiness_status"] == "blocked"
    assert cid_nl["write_authority"] == "FALSE"
    assert cid_nl["product_authority_scope"] == ""
    assert cid_nl["row_count"] == "95"
    assert cid_nl["may_touch_matrix"] == "FALSE"
    assert cid_nl["may_change_quant_output"] == "FALSE"
    assert cid_nl["may_change_workbook"] == "FALSE"
    assert cid_nl["may_change_selected_peak"] == "FALSE"
    assert cid_nl["may_change_selected_area"] == "FALSE"
    assert cid_nl["may_change_counted_detection"] == "FALSE"
    assert cid_nl["product_effect"] == "none"
    assert cid_nl["public_surface"] == "cid_nl_default_product_activation_v1"


def test_checker_rejects_stale_backfill_writer_row_count(tmp_path: Path) -> None:
    mutated = _mutated_index(
        tmp_path,
        "backfill_current_write_ready_scope",
        {"row_count": "510"},
    )

    problems = _check_with_index(mutated)

    assert (
        "Backfill writer row_count does not match owner-checked membership"
        in problems
    )

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


def test_checker_rejects_predecessor_control_plane_schema() -> None:
    predecessor = (
        DEFAULT_SCHEMA.parent / "productization_control_plane_schema.v2.json"
    )

    problems = check_productization_state(schema_path=predecessor)

    assert any(
        "unsupported productization control-plane" in problem
        for problem in problems
    )


@pytest.mark.parametrize("mutation", ("missing", "extra", "reordered"))
def test_checker_rejects_v3_schema_top_level_shape(
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

    assert any("ordered V3 contract" in problem for problem in problems)


def test_checker_rejects_duplicate_v3_schema_json_key(tmp_path: Path) -> None:
    text = DEFAULT_SCHEMA.read_text(encoding="utf-8")
    text = text.replace(
        '  "purpose":',
        '  "schema_version": "productization_control_plane_schema_v3",\n  "purpose":',
        1,
    )
    mutated = tmp_path / "schema-duplicate.json"
    mutated.write_text(text, encoding="utf-8")

    problems = check_productization_state(schema_path=mutated)

    assert any("duplicate JSON object key" in problem for problem in problems)


@pytest.mark.parametrize(
    ("mutation", "expected_problem"),
    (
        ("missing", "rule fields must exactly match"),
        ("extra", "rule fields must exactly match"),
        ("reordered", "rule fields must exactly match"),
        ("crossed", "rule values must exactly match"),
        ("type", "rule values must exactly match"),
    ),
)
def test_checker_rejects_dependent_effect_rule_drift(
    tmp_path: Path,
    mutation: str,
    expected_problem: str,
) -> None:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    rule = schema["dependent_effect_lane_authority"][DEPENDENT_EFFECT_LANE_ID]
    if mutation == "missing":
        del rule["may_add_write_rows"]
    elif mutation == "extra":
        rule["future_authority"] = True
    elif mutation == "reordered":
        schema["dependent_effect_lane_authority"][DEPENDENT_EFFECT_LANE_ID] = {
            key: rule[key] for key in reversed(tuple(rule))
        }
    elif mutation == "crossed":
        rule["authority_manifest_schema_version"] = rule[
            "package_authority_schema_version"
        ]
    else:
        rule["requires_registered_writer_membership"] = 1
    mutated = tmp_path / f"dependent-rule-{mutation}.json"
    mutated.write_text(json.dumps(schema), encoding="utf-8")

    problems = check_productization_state(schema_path=mutated)

    assert any(expected_problem in problem for problem in problems)


@pytest.mark.parametrize("mutation", ("missing", "extra", "duplicate", "reordered"))
def test_checker_rejects_non_exact_v3_lane_registry(
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

    expected = f"exact ordered {len(REQUIRED_LANE_IDS_V3)}-lane"
    assert any(expected in problem for problem in problems)


@pytest.mark.parametrize("mutation", ("missing", "extra", "duplicate", "reordered"))
def test_checker_rejects_non_exact_v3_anchor_registry(
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

    assert any("ordered 15-anchor" in problem for problem in problems)


def test_checker_rejects_crossed_or_active_dependent_status_row(
    tmp_path: Path,
) -> None:
    crossed = _mutated_index(
        tmp_path,
        DEPENDENT_EFFECT_LANE_ID,
        {
            "current_artifact": (
                "docs/superpowers/schemas/productization_authority_manifest.v1.json"
            ),
            "artifact_sha256": artifact_sha256(DEFAULT_AUTHORITY_MANIFEST),
            "readiness_status": "production_ready",
        },
    )

    problems = _check_with_index(crossed)

    assert any("exact 23-field HOLD contract" in problem for problem in problems)
    assert any("ACTIVE transition is outside" in problem for problem in problems)


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
        rules["allowed_product_writer_scopes"].pop()
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


def test_checker_rejects_product_topic_mutable_authority_scope(
    tmp_path: Path,
) -> None:
    product_topics = tmp_path / "docs/product"
    product_topics.mkdir(parents=True)
    (product_topics / "backfill.md").write_text(
        "# Backfill\n\nCurrent scope: backfill_policy_write_ready_rows.\n",
        encoding="utf-8",
    )

    problems = check_productization_state(product_topic_dir=product_topics)

    assert any(
        "docs/product/backfill.md: product topic owner duplicates mutable "
        "authority scope: backfill_policy_write_ready_rows" in problem
        for problem in problems
    )


@pytest.mark.parametrize(
    "readiness_text",
    (
        "broad_backfill_autowrite remains parked",
        "park_broad_backfill is blocked",
        "Broad auto-write from the full candidate universe is production-ready",
        "Broad auto-write — parked",
        "Broad Backfill: blocked",
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
        "broad_backfill_autowrite" in problem
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
        "../schemas/productization_authority_manifest.v1.json",
        "../schemas/wrong-authority.json",
        1,
    )
    mutated = tmp_path / "compatibility-index.md"
    mutated.write_text(text, encoding="utf-8")

    problems = _check_with_control_plane(mutated)

    assert any("closed metadata" in problem for problem in problems)


def test_schemas_readme_lists_quant_matrix_version_schema() -> None:
    text = (Path(__file__).parents[1] / "docs/superpowers/schemas/README.md").read_text(
        encoding="utf-8",
    )

    assert "quant_matrix_version_schema.v1.json" in text
    assert "cell_provenance" in text
    assert "row_summary" in text
    assert "expected-diff" in text


def test_schemas_readme_lists_quant_matrix_review_report_schema() -> None:
    text = (Path(__file__).parents[1] / "docs/superpowers/schemas/README.md").read_text(
        encoding="utf-8",
    )

    assert "quant_matrix_review_report_schema.v1.json" in text
    assert "review rows" in text
    assert "summary JSON" in text
    assert "HTML report" in text
    assert "does not grant ProductWriter or matrix authority" in text


def test_schemas_readme_lists_quant_matrix_promotion_readiness_schema() -> None:
    text = (Path(__file__).parents[1] / "docs/superpowers/schemas/README.md").read_text(
        encoding="utf-8",
    )

    assert "quant_matrix_promotion_readiness_schema.v1.json" in text
    assert "readiness summary JSON" in text
    assert "checks" in text
    assert "contract correctness from scientific confidence" in text
    assert "focused tests and 8RAW smoke evidence cannot claim" in text
    assert "artifact-bound large-cohort" in text


def test_schemas_readme_lists_quant_matrix_validation_evidence_schema() -> None:
    text = (Path(__file__).parents[1] / "docs/superpowers/schemas/README.md").read_text(
        encoding="utf-8",
    )

    assert "quant_matrix_validation_evidence_schema.v1.json" in text
    assert "artifact-bound" in text
    assert "source artifact paths/hashes" in text
    assert "`write_authority=false`" in text


def test_schemas_readme_lists_quant_matrix_promotion_packet_v2_schema() -> None:
    text = (Path(__file__).parents[1] / "docs/superpowers/schemas/README.md").read_text(
        encoding="utf-8",
    )

    assert "quant_matrix_promotion_packet_v2_schema.v1.json" in text
    assert "`production_ready_candidate_packet`" in text
    assert "large-cohort" in text
    assert "heldout-oracle" in text
    assert "real downstream-impact smoke" in text
    assert "default matrix authority unchanged" in text


def test_schemas_readme_lists_quant_matrix_default_activation_dry_run_schema() -> None:
    text = (Path(__file__).parents[1] / "docs/superpowers/schemas/README.md").read_text(
        encoding="utf-8",
    )

    assert "quant_matrix_default_activation_dry_run_schema.v1.json" in text
    assert "temporary directory" in text
    assert "expected-diff summary hashes" in text
    assert "comparison/summary artifacts" in text
    assert "ProductWriter defaults and default matrix outputs remain unchanged" in text


def test_schemas_readme_lists_quant_matrix_product_ready_closeout_schema() -> None:
    text = (Path(__file__).parents[1] / "docs/superpowers/schemas/README.md").read_text(
        encoding="utf-8",
    )

    assert "quant_matrix_product_ready_closeout_schema.v1.json" in text
    assert "`product_ready_default_matrix_candidate`" in text
    assert "separate explicit ProductWriter activation commit" in text
    assert "default outputs or matrix authority" in text


def test_schemas_readme_lists_quant_matrix_default_product_activation_schema() -> None:
    text = (Path(__file__).parents[1] / "docs/superpowers/schemas/README.md").read_text(
        encoding="utf-8",
    )

    assert "quant_matrix_default_product_activation_schema.v1.json" in text
    assert "`default_output/quant_matrix.tsv`" in text
    assert "current\n  rule-selected Backfill membership" in text
    assert "quantification values, not detections or truth claims" in text


def test_checker_rejects_parked_broad_backfill_authority(tmp_path: Path) -> None:
    mutated = _mutated_index(
        tmp_path,
        "broad_backfill_autowrite",
        {
            "write_authority": "TRUE",
            "product_authority_scope": "backfill_policy_write_ready_rows",
            "may_touch_matrix": "TRUE",
        },
    )

    problems = _check_with_index(mutated)

    assert any("parked lane grants authority" in problem for problem in problems)
    assert any(
        "broad_backfill_autowrite must stay parked" in problem for problem in problems
    )


def test_checker_rejects_diagnostic_sidecar_authority(tmp_path: Path) -> None:
    mutated = _mutated_index(
        tmp_path,
        "quality_explanation_sidecar_v1",
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
        "quality_explanation_sidecar_v1",
        {"product_authority_scope": "backfill_policy_write_ready_rows"},
    )

    problems = _check_with_index(mutated)

    assert any("non-writer row has authority scope" in problem for problem in problems)


def test_checker_rejects_non_writer_matrix_touch_without_write_flag(
    tmp_path: Path,
) -> None:
    mutated = _mutated_index(
        tmp_path,
        "review_action_candidate_sidecar_v1",
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
        "quality_explanation_sidecar_v1",
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
        "productization_authority_firewall_v1",
        {"handoff_anchor": "", "control_plane_anchor": ""},
    )

    problems = _check_with_index(mutated)

    assert any("handoff anchor is required" in problem for problem in problems)
    assert any("control-plane anchor is required" in problem for problem in problems)


def test_checker_rejects_artifact_hash_drift(tmp_path: Path) -> None:
    mutated = _mutated_index(
        tmp_path,
        "productization_authority_firewall_v1",
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
