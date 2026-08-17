import hashlib
import json
from pathlib import Path

import pytest

from xic_extractor.package0_successor_comparator import (
    SuccessorComparatorError,
    assert_targeted_adapter_parity,
    mint_targeted_adapter_parity_receipt,
    validate_successor_output,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    build_acquisition_source_identity,
    build_processing_request_identity,
)

PACKET = Path(
    "docs/superpowers/validation/package0_smallest_strangler_contract_v1"
)


def test_cli_gui_and_actual_successor_output_match_synthetic_frozen_gate(
    tmp_path: Path,
) -> None:
    contract, membership, output_root = _synthetic_gate(tmp_path)
    request = _request(tmp_path)
    cli = _adapter_receipt("cli", request, output_root)
    gui = _adapter_receipt("gui", request, output_root)

    parity_digest = assert_targeted_adapter_parity(cli, gui)
    comparator_digest = validate_successor_output(
        contract,
        membership,
        output_root=output_root,
        cli=cli,
        gui=gui,
    )

    assert len(parity_digest) == 64
    assert len(comparator_digest) == 64


def test_equal_adapter_gate_rejects_actual_scientific_byte_difference(
    tmp_path: Path,
) -> None:
    _contract, _membership, output_root = _synthetic_gate(tmp_path)
    request = _request(tmp_path)
    cli = _adapter_receipt("cli", request, output_root)
    gui = mint_targeted_adapter_parity_receipt(
        adapter_name="gui",
        processing_request=request,
        attempt_semantics=b"attempt-semantics:v1",
        lineage=b"lineage:v1",
        scientific_results=b"different-scientific-results",
        delivery_projection=b"delivery-projection:v1",
        baseline_stage_xic_results=(
            output_root / "baseline/output/xic_results.csv"
        ),
        final_stage_xic_results=output_root / "final/output/xic_results.csv",
    )

    with pytest.raises(SuccessorComparatorError, match="scientific_results"):
        assert_targeted_adapter_parity(cli, gui)


def test_successor_gate_rejects_drift_in_produced_output_bytes(
    tmp_path: Path,
) -> None:
    contract, membership, output_root = _synthetic_gate(tmp_path)
    request = _request(tmp_path)
    cli = _adapter_receipt("cli", request, output_root)
    gui = _adapter_receipt("gui", request, output_root)
    (output_root / "final/output/xic_results.csv").write_text(
        "SampleName,Area\nSample A,999\n",
        encoding="utf-8",
    )

    with pytest.raises(SuccessorComparatorError, match="successor differs"):
        validate_successor_output(
            contract,
            membership,
            output_root=output_root,
            cli=cli,
            gui=gui,
        )


def test_successor_gate_rejects_membership_or_path_escape(tmp_path: Path) -> None:
    contract, membership, output_root = _synthetic_gate(tmp_path)
    request = _request(tmp_path)
    cli = _adapter_receipt("cli", request, output_root)
    changed_membership = {
        **membership,
        "ordered_final_header": ["SampleName", "DifferentArea"],
    }

    with pytest.raises(SuccessorComparatorError, match="ordered_final_header"):
        validate_successor_output(
            contract,
            changed_membership,
            output_root=output_root,
            cli=cli,
            gui=_adapter_receipt("gui", request, output_root),
        )

    escaped_membership = {
        **membership,
        "canonical_artifact_paths": sorted(
            [*membership["canonical_artifact_paths"], "../outside.csv"]
        ),
    }
    with pytest.raises(SuccessorComparatorError, match="normalized relative"):
        validate_successor_output(
            contract,
            escaped_membership,
            output_root=output_root,
            cli=cli,
            gui=_adapter_receipt("gui", request, output_root),
        )


def test_successor_gate_rejects_partial_package0_contract(tmp_path: Path) -> None:
    contract, membership, output_root = _synthetic_gate(tmp_path)
    contract["delivery_scope"] = {
        **contract["delivery_scope"],
        "issue_149_completion": "partial",
    }
    request = _request(tmp_path)
    cli = _adapter_receipt("cli", request, output_root)

    with pytest.raises(SuccessorComparatorError, match="formally complete"):
        validate_successor_output(
            contract,
            membership,
            output_root=output_root,
            cli=cli,
            gui=_adapter_receipt("gui", request, output_root),
        )


def test_real_successor_evidence_remains_explicitly_not_evaluated() -> None:
    contract = json.loads((PACKET / "contract.json").read_text(encoding="utf-8"))

    assert contract["successor_observation"] == {
        "status": "not_evaluated",
        "reason": "successor_output_belongs_to_a_later_package",
        "required_input": "actual_output_root_and_cli_gui_owner_receipts",
    }


def _synthetic_gate(tmp_path: Path) -> tuple[dict, dict, Path]:
    output_root = tmp_path / "successor-output"
    exact_files = {
        "baseline/output/xic_results.csv": b"SampleName,Area\nSample A,1\n",
        "final/output/xic_results.csv": b"SampleName,Area\nSample A,2\n",
        "summary.tsv": b"status\tvalue\nsynthetic\tpass\n",
    }
    for relative_path, exact_bytes in exact_files.items():
        path = output_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(exact_bytes)
    paths = sorted(exact_files)
    output_artifacts = [
        {
            "path": path,
            "sha256": hashlib.sha256(exact_files[path]).hexdigest().upper(),
            "size_bytes": len(exact_files[path]),
        }
        for path in paths
    ]
    membership = {
        "schema_version": "package0_legacy_membership_receipt_v1",
        "ordered_sample_identities": ["Sample A"],
        "ordered_final_header": ["SampleName", "Area"],
        "canonical_artifact_paths": paths,
    }
    contract = {
        "authority": {
            "product_authority_granted": False,
            "validation_status": "diagnostic_only",
        },
        "baseline_id": "synthetic-package0-gate-v1",
        "canonical_output_inventory_sha256": _canonical_sha256(output_artifacts),
        "delivery_scope": {
            "issue_149_completion": "complete",
            "implemented": ["actual_successor_output_comparator"],
            "deferred": [],
        },
        "legacy_comparator": {
            "canonical_artifact_path_sha256": _ordered_sha256(paths),
            "ordered_sample_sha256": _ordered_sha256(["Sample A"]),
            "ordered_final_header_sha256": _ordered_sha256(
                ["SampleName", "Area"]
            ),
            "baseline_stage_xic_results_sha256": hashlib.sha256(
                exact_files["baseline/output/xic_results.csv"]
            )
            .hexdigest()
            .upper(),
            "final_stage_xic_results_sha256": hashlib.sha256(
                exact_files["final/output/xic_results.csv"]
            )
            .hexdigest()
            .upper(),
        },
    }
    return contract, membership, output_root


def _request(tmp_path: Path):
    path = tmp_path / "source.fixture"
    path.write_bytes(b"same-normalized-source-set")
    source = build_acquisition_source_identity(
        path,
        acquisition_format="fixture",
        observation_namespace="package0:no-raw",
    )
    return build_processing_request_identity(
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


def _adapter_receipt(adapter_name: str, request, output_root: Path):
    return mint_targeted_adapter_parity_receipt(
        adapter_name=adapter_name,
        processing_request=request,
        attempt_semantics=b"attempt-semantics:v1",
        lineage=b"lineage:v1",
        scientific_results=b"scientific-results:v1",
        delivery_projection=b"delivery-projection:v1",
        baseline_stage_xic_results=(
            output_root / "baseline/output/xic_results.csv"
        ),
        final_stage_xic_results=output_root / "final/output/xic_results.csv",
    )


def _ordered_sha256(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest().upper()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest().upper()
