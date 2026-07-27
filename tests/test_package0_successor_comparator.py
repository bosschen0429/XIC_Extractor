import json
from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.package0_successor_comparator import (
    Package0ComparatorError,
    Package0SuccessorObservation,
    TargetedAdapterParityReceipt,
    assert_targeted_adapter_parity,
    validate_package0_successor_observation,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    build_acquisition_source_identity,
    build_processing_request_identity,
)

PACKET = Path(
    "docs/superpowers/validation/package0_smallest_strangler_contract_v1"
)


def test_cli_gui_adapter_and_successor_receipts_match_frozen_package0_gate(
    tmp_path: Path,
) -> None:
    contract = json.loads((PACKET / "contract.json").read_text(encoding="utf-8"))
    observation = _observation(contract)
    cli = _adapter_receipt(tmp_path, "cli", contract)
    gui = replace(cli, adapter_name="gui")

    parity_digest = assert_targeted_adapter_parity(cli, gui)
    comparator_digest = validate_package0_successor_observation(
        contract,
        observation,
        cli=cli,
        gui=gui,
    )

    assert len(parity_digest) == 64
    assert len(comparator_digest) == 64


def test_equal_adapter_gate_rejects_scientific_or_delivery_difference(
    tmp_path: Path,
) -> None:
    contract = json.loads((PACKET / "contract.json").read_text(encoding="utf-8"))
    cli = _adapter_receipt(tmp_path, "cli", contract)
    gui = replace(
        cli,
        adapter_name="gui",
        scientific_results_sha256="F" * 64,
    )

    with pytest.raises(Package0ComparatorError, match="scientific_results"):
        assert_targeted_adapter_parity(cli, gui)


def test_successor_gate_rejects_output_or_membership_difference(tmp_path: Path) -> None:
    contract = json.loads((PACKET / "contract.json").read_text(encoding="utf-8"))
    cli = _adapter_receipt(tmp_path, "cli", contract)
    gui = replace(cli, adapter_name="gui")
    observation = replace(
        _observation(contract),
        ordered_final_header_sha256="F" * 64,
    )

    with pytest.raises(Package0ComparatorError, match="ordered_final_header"):
        validate_package0_successor_observation(
            contract,
            observation,
            cli=cli,
            gui=gui,
        )


def test_successor_gate_rejects_partial_package0_contract(tmp_path: Path) -> None:
    contract = json.loads((PACKET / "contract.json").read_text(encoding="utf-8"))
    contract["delivery_scope"] = {
        **contract["delivery_scope"],
        "issue_149_completion": "partial",
    }
    cli = _adapter_receipt(tmp_path, "cli", contract)

    with pytest.raises(Package0ComparatorError, match="formally complete"):
        validate_package0_successor_observation(
            contract,
            _observation(contract),
            cli=cli,
            gui=replace(cli, adapter_name="gui"),
        )


def _observation(contract) -> Package0SuccessorObservation:
    comparator = contract["legacy_comparator"]
    return Package0SuccessorObservation(
        baseline_id=contract["baseline_id"],
        canonical_output_inventory_sha256=contract[
            "canonical_output_inventory_sha256"
        ],
        canonical_artifact_path_sha256=comparator[
            "canonical_artifact_path_sha256"
        ],
        ordered_sample_sha256=comparator["ordered_sample_sha256"],
        ordered_final_header_sha256=comparator["ordered_final_header_sha256"],
        baseline_stage_xic_results_sha256=comparator[
            "baseline_stage_xic_results_sha256"
        ],
        final_stage_xic_results_sha256=comparator[
            "final_stage_xic_results_sha256"
        ],
    )


def _adapter_receipt(
    tmp_path: Path,
    adapter_name: str,
    contract,
) -> TargetedAdapterParityReceipt:
    path = tmp_path / "source.fixture"
    path.write_bytes(b"same-normalized-source-set")
    source = build_acquisition_source_identity(
        path,
        acquisition_format="fixture",
        observation_namespace="package0:no-raw",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="SampleA",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="targeted_adapter_contract_v1",
        processing_algorithm_version="targeted_adapter_contract_v1",
        processing_contract_version="targeted_adapter_contract_v1",
        scientific_inputs={"neutral_loss_da": 116.0474},
    )
    comparator = contract["legacy_comparator"]
    return TargetedAdapterParityReceipt(
        adapter_name=adapter_name,
        processing_request_id=request.processing_request_id,
        attempt_semantics_sha256="A" * 64,
        lineage_sha256="B" * 64,
        scientific_results_sha256="C" * 64,
        delivery_projection_sha256="D" * 64,
        baseline_stage_xic_results_sha256=comparator[
            "baseline_stage_xic_results_sha256"
        ],
        final_stage_xic_results_sha256=comparator[
            "final_stage_xic_results_sha256"
        ],
    )
