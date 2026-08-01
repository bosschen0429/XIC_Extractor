import csv
import json
from pathlib import Path

import pytest

from scripts.check_productization_state import (
    DEFAULT_AUTHORITY_MANIFEST,
    DEFAULT_CONTROL_PLANE,
    DEFAULT_HANDOFF,
    DEFAULT_PRODUCTIZATION_SUMMARY,
    DEFAULT_SCHEMA,
    DEFAULT_STATUS_INDEX,
    EXPECTED_CONTROL_PLANE_SCHEMA_VERSION,
    _render_compatibility_index,
    artifact_sha256,
    check_productization_state,
)
from tools.diagnostics.docs_scan import markdown_headings


def test_productization_state_index_accepts_current_artifacts() -> None:
    assert check_productization_state() == []


def test_status_schema_matches_index_header() -> None:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    header, rows = _read_tsv(DEFAULT_STATUS_INDEX)

    assert header == schema["required_status_index_columns"]
    assert {row["lane_id"] for row in rows} == set(schema["required_lane_ids"])
    assert len(rows) == len(schema["required_lane_ids"])
    assert DEFAULT_SCHEMA.name == "productization_control_plane_schema.v2.json"
    assert schema["schema_version"] == EXPECTED_CONTROL_PLANE_SCHEMA_VERSION
    assert schema["required_control_plane_anchors"] == {
        row["lane_id"]: row["control_plane_anchor"] for row in rows
    }


def test_registered_default_scopes_have_writer_authority() -> None:
    _, rows = _read_tsv(DEFAULT_STATUS_INDEX)
    authority_rows = [row for row in rows if row["write_authority"] == "TRUE"]

    assert {row["lane_id"] for row in authority_rows} == {
        "backfill_current_write_ready_scope",
        "cid_nl_default_product_activation_v1",
        "backfill_expansion_clean_target_selective_product_activation_v1",
    }
    authority_by_lane = {row["lane_id"]: row for row in authority_rows}

    backfill = authority_by_lane["backfill_current_write_ready_scope"]
    assert backfill["current_artifact"] == (
        "docs/superpowers/validation/quant_matrix_default_product_activation_v1/"
        "quant_matrix_default_product_activation_summary.json"
    )
    assert backfill["product_authority_scope"] == "backfill_policy_write_ready_rows"
    assert backfill["row_count"] == "511"
    assert backfill["may_touch_matrix"] == "TRUE"
    assert backfill["may_change_quant_output"] == "TRUE"
    assert backfill["may_change_workbook"] == "FALSE"
    assert backfill["may_change_selected_peak"] == "FALSE"
    assert backfill["may_change_selected_area"] == "FALSE"
    assert backfill["may_change_counted_detection"] == "FALSE"
    assert backfill["product_effect"] == "default_quant_matrix_product_output"
    assert backfill["public_surface"] == "quant_matrix_default_product_activation_v1"

    cid_nl = authority_by_lane["cid_nl_default_product_activation_v1"]
    assert cid_nl["current_artifact"] == (
        "docs/superpowers/validation/cid_nl_default_product_activation_v1/"
        "cid_nl_default_product_activation_summary.json"
    )
    assert cid_nl["product_authority_scope"] == (
        "cid_nl_adopt_ready_feature_inclusion_95_cells"
    )
    assert cid_nl["row_count"] == "95"
    assert cid_nl["may_touch_matrix"] == "TRUE"
    assert cid_nl["may_change_quant_output"] == "TRUE"
    assert cid_nl["may_change_workbook"] == "FALSE"
    assert cid_nl["may_change_selected_peak"] == "FALSE"
    assert cid_nl["may_change_selected_area"] == "FALSE"
    assert cid_nl["may_change_counted_detection"] == "FALSE"
    assert cid_nl["product_effect"] == "cid_nl_default_quant_matrix_product_output"
    assert cid_nl["public_surface"] == "cid_nl_default_product_activation_v1"

    expansion = next(
        row
        for row in rows
        if row["lane_id"] == "backfill_expansion_default_product_activation_v1"
    )
    assert expansion["current_artifact"] == (
        "docs/superpowers/validation/"
        "backfill_expansion_default_product_activation_v1/"
        "backfill_expansion_default_product_activation_summary.json"
    )
    assert expansion["readiness_status"] == "production_candidate"
    assert expansion["write_authority"] == "FALSE"
    assert expansion["product_authority_scope"] == ""
    assert expansion["row_count"] == "666"
    assert expansion["may_touch_matrix"] == "FALSE"
    assert expansion["may_change_quant_output"] == "FALSE"
    assert expansion["may_change_workbook"] == "FALSE"
    assert expansion["may_change_selected_peak"] == "FALSE"
    assert expansion["may_change_selected_area"] == "FALSE"
    assert expansion["may_change_counted_detection"] == "FALSE"
    assert expansion["product_effect"] == "candidate_replay_no_public_write"
    assert (
        expansion["public_surface"]
        == "backfill_expansion_default_product_activation_v1"
    )
    assert "374/666 pass and 292 held" in expansion["notes"]

    selective = authority_by_lane[
        "backfill_expansion_clean_target_selective_product_activation_v1"
    ]
    assert selective["current_artifact"] == (
        "docs/superpowers/validation/"
        "backfill_expansion_clean_target_selective_product_activation_v1/"
        "backfill_expansion_clean_target_selective_product_activation_summary.json"
    )
    assert selective["product_authority_scope"] == (
        "backfill_expansion_clean_target_selective_activation_84_cells"
    )
    assert selective["row_count"] == "84"
    assert selective["may_touch_matrix"] == "TRUE"
    assert selective["may_change_quant_output"] == "TRUE"
    assert selective["may_change_workbook"] == "FALSE"
    assert selective["may_change_selected_peak"] == "FALSE"
    assert selective["may_change_selected_area"] == "FALSE"
    assert selective["may_change_counted_detection"] == "FALSE"
    assert selective["product_effect"] == (
        "write_backfill_expansion_clean_target_selective_default_cell"
    )
    assert (
        selective["public_surface"]
        == "backfill_expansion_clean_target_selective_product_activation_v1"
    )
    assert "28 projected-held clean-target cells" in selective["notes"]


def test_peak_choice_lockbox_status_points_to_shadow_automation_design() -> None:
    _, rows = _read_tsv(DEFAULT_STATUS_INDEX)
    row = next(
        item for item in rows if item["lane_id"] == "peak_choice_truth_lockbox_v1"
    )

    assert (
        row["current_artifact"]
        == "docs/superpowers/validation/lockbox_shadow_automation_experiment_v1.json"
    )
    assert row["public_surface"] == "lockbox_shadow_automation_experiment_v1"
    assert row["product_effect"] == "shadow_only_contract_adapter_manifest"
    assert row["row_count"] == "72"
    assert "shadow_scoring_contract_adapter_v1_ready" in row["notes"]
    assert (
        "53 owner-clean Gaussian15 cases as non-authoritative accept challenges"
        in (row["notes"])
    )
    assert "6 manual negative controls as reject hard stops" in row["notes"]
    assert row["write_authority"] == "FALSE"


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
        DEFAULT_SCHEMA.parent / "productization_control_plane_schema.v1.json"
    )

    problems = check_productization_state(schema_path=predecessor)

    assert any(
        "unsupported productization control-plane" in problem
        for problem in problems
    )


@pytest.mark.parametrize(
    ("mutation", "expected_problem"),
    (
        ("missing", "authority_rules must be an object"),
        ("extra", "authority_rules keys must exactly match"),
        ("open", "unregistered_scope_policy must be fail_closed"),
        ("scope", "allowed scopes drifted from registered writer lanes"),
        ("boolean", "quality_explanations_cannot_write must be true"),
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
    else:
        rules["quality_explanations_cannot_write"] = False
    mutated = tmp_path / "schema.json"
    mutated.write_text(json.dumps(schema), encoding="utf-8")

    problems = check_productization_state(schema_path=mutated)

    assert any(expected_problem in problem for problem in problems)


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


def test_checker_rejects_product_topic_mutable_structured_count(
    tmp_path: Path,
) -> None:
    product_topics = tmp_path / "docs/product"
    product_topics.mkdir(parents=True)
    (product_topics / "backfill.md").write_text(
        "# Backfill\n\nThe current writer owns 511 matrix cells.\n",
        encoding="utf-8",
    )

    problems = check_productization_state(product_topic_dir=product_topics)

    assert any(
        "docs/product/backfill.md: product topic owner duplicates mutable "
        "structured row/cell count: 511" in problem
        for problem in problems
    )


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
    assert "current\n  511-cell authority contract" in text
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


def test_checker_rejects_review_truth_or_recovery_authority(tmp_path: Path) -> None:
    for lane_id in (
        "review_packet_workflow_v1",
        "peak_choice_truth_lockbox_v1",
        "missing_overlay_evidence_recovery_v1",
    ):
        mutated = _mutated_index(
            tmp_path,
            lane_id,
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
        "review_packet_workflow_v1",
        {"product_authority_scope": "backfill_policy_write_ready_rows"},
    )

    problems = _check_with_index(mutated)

    assert any("non-writer row has authority scope" in problem for problem in problems)


def test_checker_rejects_non_writer_product_output_changes(tmp_path: Path) -> None:
    mutated = _mutated_index(
        tmp_path,
        "peak_choice_truth_lockbox_v1",
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
        "mechanical_adjudication_contract_v1",
        {"handoff_anchor": "", "control_plane_anchor": ""},
    )

    problems = _check_with_index(mutated)

    assert any("handoff anchor is required" in problem for problem in problems)
    assert any("control-plane anchor is required" in problem for problem in problems)


def test_checker_rejects_artifact_hash_drift(tmp_path: Path) -> None:
    mutated = _mutated_index(
        tmp_path,
        "mechanical_adjudication_contract_v1",
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
