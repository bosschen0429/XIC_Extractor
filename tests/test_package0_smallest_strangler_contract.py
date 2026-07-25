import hashlib
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


def test_package0_declares_partial_issue_scope() -> None:
    contract = _load(PACKET / "contract.json")

    assert contract["delivery_scope"] == {
        "issue_149_completion": "partial",
        "implemented": [
            "legacy_comparator_membership",
            "shared_headless_targeted_service",
        ],
        "deferred": [
            "canonical_acquisition_source_identity",
            "canonical_processing_request_identity",
            "processing_attempt_identity",
            "evidence_artifact_identity",
            "delivery_revision_identity",
            "delivery_seal_identity",
        ],
    }


def test_package0_comparator_membership_is_complete_and_not_hand_picked() -> None:
    contract = _load(PACKET / "contract.json")
    comparator = contract["legacy_comparator"]
    receipt = _load(PACKET / contract["membership_receipt"])
    samples = receipt["ordered_sample_identities"]
    header = receipt["ordered_final_header"]

    assert len(samples) == comparator["sample_row_count_per_stage"]
    assert len(header) == comparator["column_count_per_stage"]
    assert comparator["sample_identity_cell_count_per_stage"] == len(samples)
    assert comparator["analytical_cell_count_per_stage"] == (
        len(samples) * (len(header) - 1)
    )
    assert comparator["column_count_per_stage"] == (
        1 + comparator["target_count"] * comparator["fields_per_target"]
    )
    assert comparator["membership_rule"] == (
        "complete_ordered_sample_x_ordered_header_cartesian_product"
    )
    assert _ordered_sha256(samples) == comparator["ordered_sample_sha256"]
    assert _ordered_sha256(header) == comparator["ordered_final_header_sha256"]


def test_package0_receipt_freezes_artifacts_and_public_surface_owners() -> None:
    contract = _load(PACKET / "contract.json")
    receipt = _load(PACKET / contract["membership_receipt"])

    artifact_paths = receipt["canonical_artifact_paths"]
    assert len(artifact_paths) == 12
    assert _ordered_sha256(artifact_paths) == (
        contract["legacy_comparator"]["canonical_artifact_path_sha256"]
    )
    assert receipt["public_surface_owners"] == {
        "cli_entrypoint": "scripts/run_extraction.py",
        "csv_writer_owner": "xic_extractor/output/csv_writers.py",
        "gui_entrypoint": "gui/workers/pipeline_worker.py",
        "method_manifest_writer": "xic_extractor/output/method_manifest.py",
        "workbook_writer": "xic_extractor/output/excel_pipeline.py",
    }
    assert all(
        Path(path).is_file()
        for path in receipt["public_surface_owners"].values()
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


def _ordered_sha256(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest().upper()
