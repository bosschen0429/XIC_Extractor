import json
from pathlib import Path

PACKET = Path(
    "docs/superpowers/validation/package0_smallest_strangler_contract_v1"
)
BASELINE = Path(
    "docs/superpowers/validation/cid_nl_legacy_baseline_v1"
)


def test_package0_comparator_is_bound_to_canonical_sealed_baseline() -> None:
    contract = _load(PACKET / "contract.json")
    baseline = _load(BASELINE / "baseline_summary.json")

    assert contract["baseline_id"] == baseline["baseline_id"]
    assert contract["canonical_output_inventory_sha256"] == (
        baseline["canonical_artifact_set"]["output_inventory_sha256"]
    )
    assert baseline["canonical_artifact_set"]["publication_status"] == "sealed"
    assert contract["authority"] == {
        "product_authority_granted": False,
        "validation_status": "diagnostic_only",
    }


def test_package0_comparator_membership_is_complete_and_not_hand_picked() -> None:
    comparator = _load(PACKET / "contract.json")["legacy_comparator"]

    assert comparator["sample_identity_cell_count_per_stage"] == (
        comparator["sample_row_count_per_stage"]
    )
    assert comparator["analytical_cell_count_per_stage"] == (
        comparator["sample_row_count_per_stage"]
        * comparator["target_count"]
        * comparator["fields_per_target"]
    )
    assert comparator["column_count_per_stage"] == (
        1 + comparator["target_count"] * comparator["fields_per_target"]
    )
    assert comparator["membership_rule"] == (
        "complete_ordered_sample_x_ordered_header_cartesian_product"
    )


def test_package0_keeps_new_shadow_selections_outside_legacy_authority() -> None:
    contract = _load(PACKET / "contract.json")

    assert contract["legacy_intra_stage_expected_diff"]["successor_diff_authority"] is (
        False
    )
    assert contract["new_shadow_selection_route"] == {
        "legacy_comparator_membership": False,
        "independent_oracle_required": True,
        "product_write_authority": False,
    }


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))
