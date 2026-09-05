from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_cid_nl_default_product_activation import (
    build_cid_nl_default_product_activation,
)
from scripts.check_production_acceptance_manifest import (
    REQUIRED_COLUMNS as ACCEPTANCE_COLUMNS,
)
from scripts.check_production_acceptance_manifest import (
    production_acceptance_manifest_sha256,
)
from tools.diagnostics import cid_nl_feature_inclusion_gate as feature_gate
from xic_extractor.alignment.quant_matrix_version import CELL_PROVENANCE_COLUMNS
from xic_extractor.tabular_io import read_tsv_required


def test_builds_cid_nl_default_activation_from_adopt_ready_bundle(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path)

    payload = build_cid_nl_default_product_activation(
        output_dir=tmp_path / "out",
        docs_dir=tmp_path / "docs",
        source_root=tmp_path,
        expected_diff_contract_tsvs=(paths["contract"],),
        adopt_summary_json=paths["adopt_summary"],
        successor_authority_manifest_tsv=paths["successor_manifest"],
        input_quant_matrix_tsv=paths["matrix"],
        input_matrix_identity_tsv=paths["identity"],
        value_delta_tsv=paths["value_delta"],
        expected_contract_cell_count=2,
        expected_transition_count=1,
        expected_existing_successor_context_cell_count=3,
        expected_omitted_no_target_cell_count=1,
    )

    assert payload["status"] == "pass"
    assert payload["activation_label"] == "product_ready_default_matrix_activated"
    assert payload["product_lane"] == "cid_nl_discovery"
    assert payload["product_scope_kind"] == "discovery_default_activation"
    assert payload["default_activation_effect"] == "write_cid_nl_discovery_default_cell"
    assert payload["feature_inclusion_authority_basis"] == (
        "successor_self_evidence_manifest_expected_diff_and_cid_nl_tag"
    )
    assert payload["matrix_row_universe_policy"] == (
        "discovery_expanded_full_cohort_alignment_rows"
    )
    assert payload["low_prevalence_feature_policy"] == (
        "allowed_for_untargeted_downstream_filter"
    )
    assert payload["source_successor_identity_scope"] == (
        "identity_review_only_not_feature_inclusion_blocker"
    )
    assert payload["accepted_discovery_cell_count"] == 2
    assert payload["accepted_backfill_count"] == 2
    assert payload["candidate_transition_count"] == 1
    assert payload["written_discovery_cell_count"] == "2"
    assert payload["legacy_quant_matrix_effect"] == "write_accepted_backfill"
    assert payload["legacy_provenance_status"] == "accepted_backfill"
    assert payload["product_writer_changed"] is True
    assert payload["default_quant_matrix_changed"] is True
    assert payload["default_matrix_files_written"] is True
    assert payload["workbook_or_gui_changed"] is False
    assert payload["selected_peak_area_or_counting_changed"] is False
    assert payload["backfill_writer_authority_changed"] is False
    assert payload["cid_nl_ms2_direct_productwriter_authority"] is False
    assert payload["candidate_rows_are_matrix_rows"] is False
    assert payload["full_matrix_retention"] == "externalized_output_only"
    assert payload["successor_self_evidence_summary"] == {
        "status": "pass",
        "checked_cell_count": 2,
        "unique_successor_sample_count": 2,
        "problem_count": 0,
        "missing_manifest_count": 0,
        "duplicate_manifest_key_count": 0,
        "duplicate_contract_key_count": 0,
        "successor_tag_missing_count": 0,
        "authority_flag_mismatch_count": 0,
        "quant_mismatch_count": 0,
        "provenance_missing_count": 0,
        "basis": "successor_self_evidence_manifest_expected_diff_and_cid_nl_tag",
    }

    matrix = _read_tsv(tmp_path / "out" / "default_output" / "quant_matrix.tsv")
    assert matrix[0]["SampleA"] == "111"
    assert matrix[0]["SampleB"] == "222"
    assert matrix[1]["SampleA"] == "999"

    provenance = read_tsv_required(
        tmp_path / "out" / "default_output" / "cell_provenance.tsv",
        CELL_PROVENANCE_COLUMNS,
    )
    accepted = {
        (row["peak_hypothesis_id"], row["sample_stem"])
        for row in provenance
        if row["cell_status"] == "accepted_backfill"
    }
    assert accepted == {("FAM_NEW", "SampleA"), ("FAM_NEW", "SampleB")}
    assert {
        row["value_source"]
        for row in provenance
        if row["cell_status"] == "accepted_backfill"
    } == {"ProductionAcceptanceManifest"}

    manifest = _read_tsv(
        tmp_path / "out" / "inputs" / "cid_nl_default_product_activation_manifest.tsv"
    )
    assert len(manifest) == 2
    assert {row["peak_hypothesis_id"] for row in manifest} == {"FAM_NEW"}
    assert all(
        "cid_nl_default_product_activation_v1" in row["closure_rule_ids"]
        for row in manifest
    )

    compact = _read_tsv(
        tmp_path / "docs" / "cid_nl_default_product_activation_manifest.tsv"
    )
    assert compact == [
        {
            "schema_version": "cid_nl_default_product_activation_v1",
            "transition_key": "FAM_OLD->FAM_NEW",
            "contract_source": "primary_supported",
            "contract_cell_count": "2",
            "source_peak_hypothesis_id": "FAM_OLD",
            "successor_peak_hypothesis_id": "FAM_NEW",
            "successor_product_mz": "184.113",
            "successor_neutral_loss_tag": "DNA_dR",
            "default_activation_effect": "write_cid_nl_discovery_default_cell",
            "legacy_quant_matrix_effect": "write_accepted_backfill",
            "product_authority_scope": "cid_nl_adopt_ready_feature_inclusion_95_cells",
        }
    ]
    readme = (tmp_path / "docs" / "README.md").read_text(encoding="utf-8")
    assert "Accepted Discovery default writes" in readme
    assert "Terminology boundary" in readme
    assert "Row-universe boundary" in readme
    assert "Feature-inclusion boundary" in readme

    checks = _read_tsv(
        tmp_path / "docs" / "cid_nl_default_product_activation_checks.tsv"
    )
    check_ids = {row["check_id"] for row in checks}
    assert "successor_self_evidence_contract" in check_ids
    assert "matrix_row_universe_policy" in check_ids
    assert "source_successor_identity_scope" in check_ids


def test_default_activation_refuses_hold_adopt_summary(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path, adopt_status="hold")

    with pytest.raises(ValueError, match="adopt gate is not adopt_ready"):
        build_cid_nl_default_product_activation(
            output_dir=tmp_path / "out",
            docs_dir=tmp_path / "docs",
            source_root=tmp_path,
            expected_diff_contract_tsvs=(paths["contract"],),
            adopt_summary_json=paths["adopt_summary"],
            successor_authority_manifest_tsv=paths["successor_manifest"],
            input_quant_matrix_tsv=paths["matrix"],
            input_matrix_identity_tsv=paths["identity"],
            value_delta_tsv=paths["value_delta"],
            expected_contract_cell_count=2,
            expected_transition_count=1,
            expected_existing_successor_context_cell_count=3,
            expected_omitted_no_target_cell_count=1,
        )


def test_default_activation_refuses_missing_manifest_authority(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path, manifest_samples=("SampleA",))

    with pytest.raises(ValueError, match="missing successor authority manifest row"):
        build_cid_nl_default_product_activation(
            output_dir=tmp_path / "out",
            docs_dir=tmp_path / "docs",
            source_root=tmp_path,
            expected_diff_contract_tsvs=(paths["contract"],),
            adopt_summary_json=paths["adopt_summary"],
            successor_authority_manifest_tsv=paths["successor_manifest"],
            input_quant_matrix_tsv=paths["matrix"],
            input_matrix_identity_tsv=paths["identity"],
            value_delta_tsv=paths["value_delta"],
            expected_contract_cell_count=2,
            expected_transition_count=1,
            expected_existing_successor_context_cell_count=3,
            expected_omitted_no_target_cell_count=1,
        )


def test_default_activation_requires_successor_tag_for_feature_inclusion(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path, successor_neutral_loss_tag="")

    with pytest.raises(ValueError, match="missing successor_neutral_loss_tag"):
        build_cid_nl_default_product_activation(
            output_dir=tmp_path / "out",
            docs_dir=tmp_path / "docs",
            source_root=tmp_path,
            expected_diff_contract_tsvs=(paths["contract"],),
            adopt_summary_json=paths["adopt_summary"],
            successor_authority_manifest_tsv=paths["successor_manifest"],
            input_quant_matrix_tsv=paths["matrix"],
            input_matrix_identity_tsv=paths["identity"],
            value_delta_tsv=paths["value_delta"],
            expected_contract_cell_count=2,
            expected_transition_count=1,
            expected_existing_successor_context_cell_count=3,
            expected_omitted_no_target_cell_count=1,
        )


def _write_fixture(
    tmp_path: Path,
    *,
    adopt_status: str = "adopt_ready",
    manifest_samples: tuple[str, ...] = ("SampleA", "SampleB"),
    successor_neutral_loss_tag: str = "DNA_dR",
) -> dict[str, Path]:
    matrix = tmp_path / "alignment_matrix.tsv"
    identity = tmp_path / "alignment_matrix_identity.tsv"
    contract = tmp_path / "contract.tsv"
    value_delta = tmp_path / "value_delta.tsv"
    manifest = tmp_path / "successor_authority_manifest.tsv"
    adopt_summary = tmp_path / "adopt_summary.json"

    _write_tsv(
        matrix,
        ("Mz", "RT", "SampleA", "SampleB"),
        [
            {"Mz": "300.1605", "RT": "22.2", "SampleA": "", "SampleB": ""},
            {"Mz": "301.165", "RT": "22.4", "SampleA": "999", "SampleB": ""},
        ],
    )
    _write_tsv(
        identity,
        (
            "matrix_row_index",
            "Mz",
            "RT",
            "peak_hypothesis_id",
            "source_feature_family_ids",
        ),
        [
            _identity_row(1, "FAM_NEW", "300.1605", "22.2"),
            _identity_row(2, "FAM_KEEP", "301.165", "22.4"),
        ],
    )
    contract_rows = [
        _contract_row(
            "SampleA",
            "111",
            successor_neutral_loss_tag=successor_neutral_loss_tag,
        ),
        _contract_row(
            "SampleB",
            "222",
            successor_neutral_loss_tag=successor_neutral_loss_tag,
        ),
    ]
    _write_tsv(contract, feature_gate.EXPECTED_DIFF_COLUMNS, contract_rows)
    _write_tsv(
        value_delta,
        (
            "schema_version",
            "transition_key",
            "sample_stem",
            "source_peak_hypothesis_id",
            "successor_peak_hypothesis_id",
            "matrix_row_index",
            "source_mz",
            "source_rt",
            "successor_mz",
            "successor_rt",
            "successor_product_mz",
            "successor_neutral_loss_tag",
            "original_matrix_value",
            "activated_copy_value",
            "candidate_quant_value",
            "value_changed",
            "authority_gate",
            "product_authority_effect",
            "expected_product_effect",
        ),
        [_value_delta_row(row) for row in contract_rows],
    )
    manifest_rows = [
        _acceptance_row(tmp_path, sample, "111" if sample == "SampleA" else "222")
        for sample in manifest_samples
    ]
    manifest_sha = production_acceptance_manifest_sha256(manifest_rows)
    for row in manifest_rows:
        row["manifest_sha256"] = manifest_sha
    _write_tsv(manifest, ACCEPTANCE_COLUMNS, manifest_rows)
    adopt_summary.write_text(
        json.dumps(
            {
                "activation_bundle_adopt_ready": adopt_status == "adopt_ready",
                "adopt_gate_status": adopt_status,
                "candidate_transition_count": 1,
                "changed_matrix_cell_count": 2,
                "contract_cell_count": 2,
                "default_quant_matrix_changed": False,
                "existing_successor_context_cell_count": 3,
                "forbidden_overlap_count": 0,
                "missing_matrix_change_count": 0,
                "omitted_no_target_cell_count": 1,
                "product_writer_changed": False,
                "production_ready": False,
                "unexpected_matrix_change_count": 0,
                "workbook_gui_changed": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "matrix": matrix,
        "identity": identity,
        "contract": contract,
        "value_delta": value_delta,
        "successor_manifest": manifest,
        "adopt_summary": adopt_summary,
    }


def _identity_row(index: int, peak_id: str, mz: str, rt: str) -> dict[str, str]:
    return {
        "matrix_row_index": str(index),
        "Mz": mz,
        "RT": rt,
        "peak_hypothesis_id": peak_id,
        "source_feature_family_ids": peak_id,
    }


def _contract_row(
    sample: str,
    value: str,
    *,
    successor_neutral_loss_tag: str = "DNA_dR",
) -> dict[str, str]:
    return {
        "schema_version": feature_gate.SCHEMA_VERSION,
        "expected_diff_contract_status": "expected_diff_design_candidate",
        "transition_key": "FAM_OLD->FAM_NEW",
        "sample_stem": sample,
        "source_peak_hypothesis_id": "FAM_OLD",
        "successor_peak_hypothesis_id": "FAM_NEW",
        "source_mz": "243.099",
        "source_rt": "23.66",
        "source_product_mz": "127.052",
        "source_neutral_loss_tag": "DNA_dR",
        "source_identity_decision": "audit_family",
        "successor_mz": "300.1605",
        "successor_rt": "22.2",
        "successor_product_mz": "184.113",
        "successor_neutral_loss_tag": successor_neutral_loss_tag,
        "successor_identity_decision": "production_family",
        "candidate_quant_value": value,
        "legacy_successor_matrix_effect": "write_accepted_backfill",
        "legacy_successor_write_authority": "TRUE",
        "legacy_successor_matrix_write_allowed": "TRUE",
        "legacy_input_resolution_status": "write_ready_blank",
        "feature_inclusion_review_status": (
            "candidate_feature_inclusion_supported_by_current_overlay"
        ),
        "identity_authority_status": "expected_diff_required_before_identity_authority",
        "authority_gate": "candidate_only_expected_diff_required_no_product_write",
        "product_authority_effect": "diagnostic_only_no_authority_change",
        "expected_product_effect": "candidate_cell_expected_diff_design_only",
        "guardrail_flag": "",
        "trace_data_json": "trace.json",
    }


def _value_delta_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "schema_version": "cid_nl_activation_copy_candidate_v1",
        "transition_key": row["transition_key"],
        "sample_stem": row["sample_stem"],
        "source_peak_hypothesis_id": row["source_peak_hypothesis_id"],
        "successor_peak_hypothesis_id": row["successor_peak_hypothesis_id"],
        "matrix_row_index": "1",
        "source_mz": row["source_mz"],
        "source_rt": row["source_rt"],
        "successor_mz": row["successor_mz"],
        "successor_rt": row["successor_rt"],
        "successor_product_mz": row["successor_product_mz"],
        "successor_neutral_loss_tag": row["successor_neutral_loss_tag"],
        "original_matrix_value": "",
        "activated_copy_value": row["candidate_quant_value"],
        "candidate_quant_value": row["candidate_quant_value"],
        "value_changed": "TRUE",
        "authority_gate": row["authority_gate"],
        "product_authority_effect": "diagnostic_only_no_authority_change",
        "expected_product_effect": row["expected_product_effect"],
    }


def _acceptance_row(tmp_path: Path, sample: str, value: str) -> dict[str, str]:
    source = _write_source(tmp_path, "sources/evidence.tsv", "cell\tFAM_NEW\n")
    doublet = _write_source(tmp_path, "sources/doublet.tsv", "doublet\tFAM_NEW\n")
    return {
        "schema_version": "production_acceptance_manifest_v1",
        "peak_hypothesis_id": "FAM_NEW",
        "sample_stem": sample,
        "feature_family_id": "FAM_NEW",
        "acceptance_decision": "accept_basic_backfill",
        "acceptance_basis": "machine_basic",
        "truth_status": "not_truth_claimed",
        "shadow_only": "FALSE",
        "write_authority": "TRUE",
        "matrix_write_allowed": "TRUE",
        "quant_value": value,
        "quant_value_source": "standard_peak_shadow_projection",
        "matrix_area_source": "gaussian_smoothed_standard_peak_projection",
        "detected_count": "0",
        "backfilled_count": "1",
        "quant_available_count": "1",
        "missing_count": "1",
        "backfill_fraction": "1.000000",
        "prevalence_flags": "high_backfill_dependency",
        "hard_blocker_rule_ids": "",
        "triggered_risk_rule_ids": "high_backfill_dependency",
        "closure_rule_ids": "current_511_policy_write_ready",
        "decision_reason": "fixture",
        "next_evidence_needed": "",
        "doublet_status": "no_doublet_claim",
        "reference_side": "not_applicable",
        "doublet_allowed": "TRUE",
        "doublet_source_relpath": _relpath(doublet, tmp_path),
        "doublet_source_sha256": _sha256(doublet),
        "source_artifact_relpath": _relpath(source, tmp_path),
        "source_artifact_sha256": _sha256(source),
        "source_row_sha256": "A" * 64,
        "manifest_sha256": "",
        "acceptance_contract_version": "production_acceptance_manifest_contract_v1",
    }


def _write_source(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _relpath(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fieldnames} for row in rows
        )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
