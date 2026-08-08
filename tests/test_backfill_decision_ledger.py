from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tests.alignment_pipeline_helpers import candidate_row, write_csv
from xic_extractor.alignment.csv_io import read_discovery_candidates_csv
from xic_extractor.alignment.provisional_matrix import (
    PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
)
from xic_extractor.alignment.tsv_writer import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
)
from xic_extractor.diagnostics.backfill_decision_ledger import (
    _BACKFILL_COLUMNS,
    _PROVENANCE_COLUMNS,
    _REVIEW_COLUMNS,
    _read_timing,
    build_backfill_decision_ledger,
)
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    DiscoveryCandidateUniverseReceipt,
    discovery_receipt_index_fields,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.discovery.models import DISCOVERY_CANDIDATE_COLUMNS
from xic_extractor.processing_identity import build_acquisition_source_identity

_REVISION = "a" * 40


def test_decision_ledger_is_deterministic_and_preserves_authority_ceiling(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path / "artifact")

    first = build_backfill_decision_ledger(
        artifact_root=artifact,
        output_dir=tmp_path / "first",
        expected_code_revision=_REVISION,
    )
    second = build_backfill_decision_ledger(
        artifact_root=artifact,
        output_dir=tmp_path / "second",
        expected_code_revision=_REVISION,
    )

    assert first.ledger_tsv.read_bytes() == second.ledger_tsv.read_bytes()
    assert first.summary_json.read_bytes() == second.summary_json.read_bytes()
    assert first.summary["candidate_layer"] == {
        "sample_count": 1,
        "candidate_count": 1,
        "populated_physical_feature_count": 1,
        "unique_physical_feature_count": 1,
        "duplicate_physical_feature_count": 0,
        "multi_seed_candidate_count": 1,
        "display_family_multi_candidate_group_count": 0,
        "display_family_excess_candidate_count": 0,
        "display_family_multi_best_scan_group_count": 0,
        "pairing_verdict": "candidate_layer_dedupe_defect_not_reproduced",
        "claim_ceiling": (
            "owner_bound_candidate_fields; strict scan content not replayed"
        ),
    }
    assert (
        first.summary["request_identity_and_cost"]["exact_logical_request_identity"]
        == "unavailable"
    )
    assert (
        first.summary["request_identity_and_cost"][
            "display_request_proxy_duplicate_count"
        ]
        == 1
    )
    assert first.summary["request_identity_and_cost"]["timing_schema_status"] == (
        "unavailable_legacy_unversioned_timing_projection"
    )
    assert first.summary["hypothesis_verdict"]["overall"] == (
        "candidate_layer_not_reproduced_downstream_inconclusive"
    )
    assert first.summary["artifact"]["manifest_rehydration_status"] == (
        "unavailable_legacy_schema_rejected_by_current_manifest_owner"
    )
    assert set(first.summary["diagnostic_layers"]) == {
        "A_same_candidate_other_ms2_evidence",
        "B_same_peak_distinct_scientific_hypotheses",
        "C_alignment_owner_consolidation",
        "D_cross_sample_winner_loser_settlement",
        "E_exact_request_and_batch_reuse",
    }
    ledger_text = first.ledger_tsv.read_text(encoding="utf-8")
    assert "unavailable_missing_owner_assignment_payload" in ledger_text
    assert "unavailable_no_owner_request_identity" in ledger_text


def test_decision_ledger_rejects_candidate_artifact_mutation(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path / "artifact")
    candidate_csv = (
        artifact / "workflow" / "discovery" / "Sample_A" / "discovery_candidates.csv"
    )
    original = candidate_csv.read_text(encoding="utf-8")
    candidate_csv.write_text(original.replace("1;2", "1;2;3"), encoding="utf-8")

    with pytest.raises(ValueError, match="owner-bound candidate artifact"):
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "output",
            expected_code_revision=_REVISION,
        )


def test_decision_ledger_rejects_unknown_projection_group(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path / "artifact")
    evidence = (
        artifact / "workflow" / "alignment" / "alignment_backfill_cell_evidence.tsv"
    )
    _write_tsv(
        evidence,
        _BACKFILL_COLUMNS,
        [
            {
                "schema_version": ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
                "group_hypothesis_id": "unknown",
                "sample_stem": "Sample_A",
                "source_candidate_id": "Sample_A#1@mz500_p384",
            }
        ],
    )

    with pytest.raises(ValueError, match="unknown group_hypothesis_id"):
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "output",
            expected_code_revision=_REVISION,
        )


def test_decision_ledger_rejects_negative_cost_metric(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path / "artifact")
    timing = json.loads((artifact / "timing.json").read_text(encoding="utf-8"))
    timing["records"][-1]["metrics"]["point_count"] = -1
    (artifact / "timing.json").write_text(
        json.dumps(timing),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="point_count must be a nonnegative integer"):
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "output",
            expected_code_revision=_REVISION,
        )


def test_decision_ledger_rejects_missing_owner_timing_and_wrong_schema(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path / "missing-owner")
    timing_path = artifact / "timing.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    timing["records"] = [
        record
        for record in timing["records"]
        if record["stage"] != "alignment.build_owners.sample_complete"
    ]
    timing_path.write_text(json.dumps(timing), encoding="utf-8")
    with pytest.raises(ValueError, match="sample universe does not match Discovery"):
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "missing-owner-output",
            expected_code_revision=_REVISION,
        )

    partial = _artifact(tmp_path / "partial-owner") / "timing.json"
    with pytest.raises(
        ValueError,
        match=r"missing=\['Sample_B'\]",
    ):
        _read_timing(
            partial,
            expected_sample_stems=("Sample_A", "Sample_B"),
        )

    duplicate_payload = json.loads(partial.read_text(encoding="utf-8"))
    duplicate_payload["records"].insert(1, duplicate_payload["records"][0].copy())
    partial.write_text(json.dumps(duplicate_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate sample_complete sample_stem"):
        _read_timing(
            partial,
            expected_sample_stems=("Sample_A", "Sample_B"),
        )

    artifact = _artifact(tmp_path / "wrong-schema")
    evidence = (
        artifact / "workflow" / "alignment" / "alignment_backfill_cell_evidence.tsv"
    )
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
            "unknown_backfill_schema",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="schema_version must be"):
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "wrong-schema-output",
            expected_code_revision=_REVISION,
        )


def test_decision_ledger_rejects_duplicate_header_and_unknown_enum(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path / "artifact")
    review = artifact / "workflow" / "alignment" / "alignment_review.tsv"
    text = review.read_text(encoding="utf-8")
    review.write_text(
        text.replace(
            "feature_family_id\t", "feature_family_id\tfeature_family_id\t", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate columns"):
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "duplicate",
            expected_code_revision=_REVISION,
        )

    artifact = _artifact(tmp_path / "enum-artifact")
    review = artifact / "workflow" / "alignment" / "alignment_review.tsv"
    review.write_text(
        review.read_text(encoding="utf-8").replace("primary_loser", "primary_lsoer", 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported consolidation_state"):
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "enum",
            expected_code_revision=_REVISION,
        )


def _artifact(root: Path) -> Path:
    discovery = root / "workflow" / "discovery"
    sample_dir = discovery / "Sample_A"
    sample_dir.mkdir(parents=True)
    candidate_csv = sample_dir / "discovery_candidates.csv"
    write_csv(
        candidate_csv,
        DISCOVERY_CANDIDATE_COLUMNS,
        [candidate_row("Sample_A")],
    )
    candidates = read_discovery_candidates_csv(
        candidate_csv,
        require_successor_columns=True,
    )
    source_path = root / "inputs" / "Sample_A.synthetic"
    source_path.parent.mkdir()
    source_path.write_bytes(b"no-raw-synthetic-source")
    source = build_acquisition_source_identity(
        source_path,
        acquisition_format="synthetic_no_raw",
        observation_namespace="test:backfill-decision-ledger",
    )
    receipt = mint_discovery_candidate_universe_receipt(
        sample_stem="Sample_A",
        acquisition_source=source,
        candidate_csv=candidate_csv,
        candidates=candidates,
    )
    _write_batch_index(discovery / "discovery_batch_index.csv", receipt)

    alignment = root / "workflow" / "alignment"
    alignment.mkdir(parents=True)
    _write_tsv(
        alignment / "alignment_review.tsv",
        _REVIEW_COLUMNS,
        [
            {
                "group_hypothesis_id": "group-1",
                "consolidation_state": "primary_loser",
                "consolidation_winner_group_hypothesis_id": "group-2",
                "event_cluster_ids": "owner-projection-1",
                "event_member_count": "1",
                "quantifiable_rescue_count": "2",
                "review_rescue_count": "3",
            }
        ],
    )
    _write_tsv(
        alignment / "alignment_backfill_cell_evidence.tsv",
        _BACKFILL_COLUMNS,
        [
            {
                "schema_version": ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
                "group_hypothesis_id": "group-1",
                "sample_stem": "Sample_A",
                "source_candidate_id": "Sample_A#1@mz500_p384",
            }
        ],
    )
    _write_tsv(
        alignment / "provisional_cell_provenance.tsv",
        _PROVENANCE_COLUMNS,
        [
            {
                "schema_version": PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
                "group_hypothesis_id": "group-1",
                "sample_stem": "Sample_A",
                "backfill_seed_mz": "500.0000",
                "backfill_seed_rt": "8.5000",
            },
            {
                "schema_version": PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
                "group_hypothesis_id": "group-1",
                "sample_stem": "Sample_A",
                "backfill_seed_mz": "500.0000",
                "backfill_seed_rt": "8.5000",
            },
        ],
    )
    (root / "run_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "untargeted_provisional_matrix_run_v1",
                "code_revision": _REVISION,
                "preset_name": "synthetic",
                "effective_configuration": {},
                "runtime": {},
                "inputs": {},
                "artifacts": {},
                "validation_status": "diagnostic_only",
                "product_authority": False,
                "row_identity_status": "provisional",
                "identity_settlement_status": "not_run",
            }
        ),
        encoding="utf-8",
    )
    (root / "heartbeat.json").write_text("{}", encoding="utf-8")
    (root / "timing.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "stage": "alignment.build_owners.sample_complete",
                        "sample_stem": "Sample_A",
                        "metrics": {"assignment_count": 1, "owner_count": 1},
                    },
                    {
                        "stage": "alignment.backfill_scope",
                        "metrics": {
                            "request_target_count": 2,
                            "extract_request_count": 2,
                        },
                    },
                    {
                        "stage": "alignment.owner_backfill",
                        "elapsed_sec": 1.25,
                        "metrics": {
                            "extract_xic_count": 2,
                            "extract_xic_batch_count": 1,
                            "raw_chromatogram_call_count": 1,
                            "point_count": 20,
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return root


def _write_batch_index(
    path: Path,
    receipt: DiscoveryCandidateUniverseReceipt,
) -> None:
    fieldnames = (
        "schema_version",
        "sample_stem",
        "raw_file",
        "candidate_csv",
        "review_csv",
        "candidate_count",
        "high_count",
        "medium_count",
        "low_count",
        *DISCOVERY_BATCH_RECEIPT_COLUMNS,
    )
    row = {
        "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        "sample_stem": "Sample_A",
        "raw_file": "inputs/Sample_A.synthetic",
        "candidate_csv": "Sample_A/discovery_candidates.csv",
        "review_csv": "",
        "candidate_count": "1",
        "high_count": "1",
        "medium_count": "0",
        "low_count": "0",
        **discovery_receipt_index_fields(receipt),
    }
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
