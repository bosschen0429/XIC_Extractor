from __future__ import annotations

import csv
from pathlib import Path

from scripts import build_backfill_expansion_clean_target_selective_product_activation
from scripts.check_backfill_expansion_clean_target_full_chain_replay import (
    validate_backfill_expansion_clean_target_full_chain_replay,
)

_RETIRED_PRODUCT_AUTHORITY_PATHS = (
    "scripts/check_product_ready_preset_publication.py",
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


def test_retired_standard_peak_product_authority_modules_are_absent() -> None:
    root = Path(__file__).resolve().parents[1]

    assert tuple(
        path for path in _RETIRED_PRODUCT_AUTHORITY_PATHS if (root / path).exists()
    ) == ()


def test_t5d_does_not_invent_a_hold_eligibility_owner() -> None:
    root = Path(__file__).resolve().parents[1]

    assert not (
        root
        / "xic_extractor/diagnostics/standard_peak_hold_diagnostic.py"
    ).exists()


def test_registered_84_cell_scope_remains_active_owner_support() -> None:
    assert validate_backfill_expansion_clean_target_full_chain_replay() == []
    assert (
        build_backfill_expansion_clean_target_selective_product_activation.EXPECTED_COUNTS[
            "projected_pass_cell_count"
        ]
        == 84
    )
    assert (
        build_backfill_expansion_clean_target_selective_product_activation.EXPECTED_COUNTS[
            "written_backfill_count"
        ]
        == 84
    )


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
        "final_disposition": "owner_input",
        "retained_locator": (
            "scripts/build_backfill_expansion_clean_target_selective_product_activation.py"
        ),
        "rationale": (
            "registered 84-cell scope remains active under the existing manifest and "
            "primary writer"
        ),
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
