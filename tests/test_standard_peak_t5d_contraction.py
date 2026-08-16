from __future__ import annotations

import ast
import csv
import json
from pathlib import Path

from scripts import check_backfill_expansion_full_evidence_chain
from scripts.check_backfill_expansion_clean_target_full_chain_replay import (
    validate_backfill_expansion_clean_target_full_chain_replay,
)

_RETIRED_PRODUCT_AUTHORITY_PATHS = (
    "scripts/check_product_ready_preset_publication.py",
    "scripts/build_backfill_expansion_clean_target_selective_product_activation.py",
    "scripts/run_backfill_expansion_full_evidence_chain.py",
    "tools/diagnostics/dna_dr_product_ready_stage_replay.py",
    "tools/diagnostics/standard_peak_activation_scope_audit.py",
    "tools/diagnostics/standard_peak_backfill_chunk_consolidation.py",
    "tools/diagnostics/standard_peak_backfill_machine_pipeline.py",
    "tools/diagnostics/standard_peak_backfill_preset.py",
    "tools/diagnostics/standard_peak_backfill_productization.py",
    "tools/diagnostics/standard_peak_heldout_oracle_results.py",
    "tools/diagnostics/standard_peak_heldout_trace_oracle.py",
    "tools/diagnostics/standard_peak_ms1_authority_bundle.py",
    "tools/diagnostics/standard_peak_policy_observed_oracle.py",
    "tools/diagnostics/standard_peak_reintegration_stability_audit.py",
    "tools/diagnostics/standard_peak_shadow_activation_inputs.py",
    "xic_extractor/diagnostics/product_ready_preset_publication_check.py",
    "xic_extractor/diagnostics/standard_peak_activation_scope_audit.py",
    "xic_extractor/diagnostics/standard_peak_backfill_chunk_consolidation.py",
    "xic_extractor/diagnostics/standard_peak_backfill_productization.py",
    "xic_extractor/diagnostics/standard_peak_heldout_trace_oracle.py",
    "xic_extractor/diagnostics/standard_peak_ms1_authority_bundle.py",
    "xic_extractor/diagnostics/standard_peak_policy_observed_oracle.py",
    "xic_extractor/diagnostics/standard_peak_reintegration_stability_audit.py",
    "xic_extractor/diagnostics/standard_peak_shadow_activation_inputs.py",
)
_RETIRED_PRODUCT_AUTHORITY_MODULES = tuple(
    path.removesuffix(".py").replace("/", ".")
    for path in _RETIRED_PRODUCT_AUTHORITY_PATHS
)
_PRODUCTION_SOURCE_ROOTS = ("gui", "scripts", "tools", "xic_extractor")


def test_retired_standard_peak_product_authority_modules_are_absent() -> None:
    root = Path(__file__).resolve().parents[1]

    assert tuple(
        path for path in _RETIRED_PRODUCT_AUTHORITY_PATHS if (root / path).exists()
    ) == ()


def test_production_sources_do_not_import_or_invoke_retired_modules() -> None:
    root = Path(__file__).resolve().parents[1]
    referrers: list[tuple[str, int, str]] = []
    for source_root in _PRODUCTION_SOURCE_ROOTS:
        for path in sorted((root / source_root).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                candidates: tuple[str, ...] = ()
                if isinstance(node, ast.Import):
                    candidates = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    prefix = node.module or ""
                    candidates = (prefix,) + tuple(
                        f"{prefix}.{alias.name}" if prefix else alias.name
                        for alias in node.names
                    )
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    normalized = node.value.replace("\\", "/")
                    if any(
                        module in node.value
                        for module in _RETIRED_PRODUCT_AUTHORITY_MODULES
                    ) or any(
                        retired_path in normalized
                        for retired_path in _RETIRED_PRODUCT_AUTHORITY_PATHS
                    ):
                        candidates = (node.value,)
                for candidate in candidates:
                    if any(
                        candidate == module or candidate.startswith(f"{module}.")
                        for module in _RETIRED_PRODUCT_AUTHORITY_MODULES
                    ):
                        referrers.append(
                            (
                                path.relative_to(root).as_posix(),
                                node.lineno,
                                candidate,
                            )
                        )
                    elif any(
                        retired_path in candidate.replace("\\", "/")
                        for retired_path in _RETIRED_PRODUCT_AUTHORITY_PATHS
                    ):
                        referrers.append(
                            (
                                path.relative_to(root).as_posix(),
                                node.lineno,
                                candidate,
                            )
                        )

    assert referrers == []


def test_t5d_does_not_invent_a_hold_eligibility_owner() -> None:
    root = Path(__file__).resolve().parents[1]

    assert not (
        root
        / "xic_extractor/diagnostics/standard_peak_hold_diagnostic.py"
    ).exists()


def test_historical_84_cell_packet_is_validation_only() -> None:
    assert validate_backfill_expansion_clean_target_full_chain_replay() == []
    assert not hasattr(
        check_backfill_expansion_full_evidence_chain,
        "build_backfill_expansion_full_evidence_chain",
    )

    root = Path(__file__).resolve().parents[1]
    authority_manifest = json.loads(
        (
            root
            / "docs/superpowers/schemas/productization_authority_manifest.v1.json"
        ).read_text(encoding="utf-8")
    )
    retired_scope = "backfill_expansion_clean_target_selective_activation_84_cells"
    assert retired_scope not in authority_manifest["authority_policy"][
        "product_writer_allowed_scopes"
    ]
    assert retired_scope in {
        row["scope_id"]
        for row in authority_manifest["blocked_or_negative_evidence_scopes"]
    }


def test_sp001_through_sp055_have_one_final_t5d_disposition() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = (
        root
        / "docs/superpowers/validation/standard_peak_t5d_contraction_v1"
        / "capability_disposition_v1.tsv"
    )
    with manifest.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle, delimiter="\t"))

    expected_ids = {f"SP-{index:03d}" for index in range(1, 56)}
    assert {row["rule_id"] for row in rows} == expected_ids
    assert len(rows) == len(expected_ids)
    assert {row["final_disposition"] for row in rows} <= {
        "owner_input",
        "diagnostic_oracle",
        "diagnostic_renderer",
        "delete",
    }
    for row in rows:
        if row["final_disposition"] == "delete":
            assert row["retained_locator"] == "deleted_by_t5d"
        else:
            assert (root / row["retained_locator"]).exists(), row["rule_id"]

    by_id = {row["rule_id"]: row for row in rows}
    assert by_id["SP-040"] == {
        "rule_id": "SP-040",
        "final_disposition": "delete",
        "retained_locator": "deleted_by_t5d",
        "rationale": "ownerless clean-target selective writer retired",
    }
    for rule_id in (
        "SP-025",
        "SP-029",
        "SP-031",
        "SP-037",
        "SP-039",
        "SP-047",
        "SP-050",
        "SP-052",
        "SP-054",
        "SP-055",
    ):
        assert by_id[rule_id]["final_disposition"] == "delete"
