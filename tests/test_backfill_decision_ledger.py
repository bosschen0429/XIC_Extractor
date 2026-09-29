from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.alignment_pipeline_helpers import candidate_row, write_csv
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.csv_io import (
    read_discovery_batch_index,
    read_discovery_candidates_csv,
)
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
    _read_backfill_evidence,
    _read_request_proxy,
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
from xic_extractor.discovery.models import (
    DISCOVERY_CANDIDATE_COLUMNS,
    DiscoverySettings,
    NeutralLossProfile,
)
from xic_extractor.extraction.provisional_run_manifest import (
    ProvisionalRunRuntimeConfiguration,
    write_provisional_run_manifest,
)
from xic_extractor.processing_identity import build_acquisition_source_identity

_REVISION = "a" * 40


def test_decision_ledger_counts_only_persisted_discovery_candidates(
    tmp_path: Path,
) -> None:
    from xic_extractor.diagnostics.timing import TimingRecorder
    from xic_extractor.discovery.pipeline import _write_dual_csvs

    artifact = _artifact(tmp_path / "artifact")
    discovery = artifact / "workflow" / "discovery"
    sample_dir = discovery / "Sample_A"
    batch = read_discovery_batch_index(discovery / "discovery_batch_index.csv")
    receipt = batch.candidate_receipts["Sample_A"]
    assert receipt is not None
    (feature,) = read_discovery_candidates_csv(sample_dir / "discovery_candidates.csv")
    support = replace(
        feature, candidate_id="Sample_A#3@mz500_p384", best_ms2_scan_id=3,
        seed_scan_ids=(3,), seed_event_count=1, ms1_peak_found=False,
        ms1_feature_row_id="", discovery_candidate_state="review_only_orphan_nl",
        precursor_mz_basis="scan_precursor",
    )
    inferred = replace(
        support, candidate_id="Sample_A#4@mz500_p384", best_ms2_scan_id=4,
        seed_scan_ids=(4, 5), seed_event_count=2,
        precursor_mz_basis="product_plus_neutral_loss",
    )
    output = _write_dual_csvs(
        sample_dir, (feature, support, inferred),
        timing_recorder=TimingRecorder.disabled("discovery"), sample_stem="Sample_A",
    )
    persisted = read_discovery_candidates_csv(output.candidates_csv)
    receipt = mint_discovery_candidate_universe_receipt(
        sample_stem="Sample_A", acquisition_source=receipt.acquisition_source,
        candidate_csv=output.candidates_csv, candidates=persisted,
    )
    _write_batch_index(discovery / "discovery_batch_index.csv", receipt)
    _write_current_manifest(artifact)
    result = build_backfill_decision_ledger(
        artifact_root=artifact, output_dir=tmp_path / "ledger",
        expected_code_revision=_REVISION,
    )
    with result.ledger_tsv.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle, delimiter="\t"))
    candidate_ids = {
        row["candidate_id"] for row in rows if row["record_type"] == "candidate"
    }
    assert candidate_ids == {
        feature.candidate_id, support.candidate_id,
    }
    layer = result.summary["candidate_layer"]
    assert layer["candidate_count"] == 2
    assert layer["multi_seed_candidate_count"] == 1
    assert layer["display_family_excess_candidate_count"] == 1


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
        "physical_feature_identity_scope": "owner_feature_backed_states_only",
        "excluded_non_feature_backed_populated_identity_count": 0,
        "unique_physical_feature_count": 1,
        "duplicate_physical_feature_count": 0,
        "multi_seed_candidate_count": 1,
        "display_family_multi_candidate_group_count": 0,
        "display_family_excess_candidate_count": 0,
        "display_family_multi_best_scan_group_count": 0,
        "physical_feature_identity_uniqueness_status": (
            "owner_parser_invariant_accepted_feature_backed_states"
        ),
        "duplicate_physical_feature_count_basis": (
            "owner_parser_rejects_duplicate_sample_tag_feature_identity_"
            "in_feature_backed_states"
        ),
        "pairing_verdict": (
            "candidate_layer_dedupe_defect_not_reproduced_"
            "feature_backed_owner_invariant_accepted"
        ),
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
    assert first.summary["request_identity_and_cost"]["backfill_scope"] == "full-audit"
    assert first.summary["request_identity_and_cost"]["skipped_feature_count"] == 0
    assert first.summary["hypothesis_verdict"]["overall"] == (
        "candidate_layer_not_reproduced_downstream_inconclusive"
    )
    assert first.summary["decision"]["status"] == (
        "observed_evidence_supports_priority"
    )
    assert first.summary["decision"]["value_priority"] == (
        "early_loser_settlement_shadow_before_exact_request_coalescing"
    )
    assert first.summary["artifact"]["manifest_rehydration_status"] == (
        "current_owner_rehydrated"
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


def test_decision_ledger_rejects_owner_resealed_duplicate_physical_feature_id(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path / "artifact")
    candidate_csv = (
        artifact / "workflow" / "discovery" / "Sample_A" / "discovery_candidates.csv"
    )
    original_candidate = read_discovery_candidates_csv(
        candidate_csv,
    )[0]
    duplicate_row = candidate_row("Sample_A")
    duplicate_row.update(
        {
            "candidate_id": "Sample_A#3@mz500_p384",
            "feature_family_id": "Sample_A@F0002",
            "best_ms2_scan_id": "3",
            "seed_scan_ids": "3;4",
        }
    )
    duplicate_only_csv = tmp_path / "duplicate_only.csv"
    write_csv(duplicate_only_csv, DISCOVERY_CANDIDATE_COLUMNS, [duplicate_row])
    duplicate_candidate = read_discovery_candidates_csv(
        duplicate_only_csv,
    )[0]
    write_csv(
        candidate_csv,
        DISCOVERY_CANDIDATE_COLUMNS,
        [candidate_row("Sample_A"), duplicate_row],
    )

    batch_index = artifact / "workflow" / "discovery" / "discovery_batch_index.csv"
    batch = read_discovery_batch_index(batch_index)
    original_receipt = batch.candidate_receipts["Sample_A"]
    assert original_receipt is not None
    resealed_receipt = mint_discovery_candidate_universe_receipt(
        sample_stem="Sample_A",
        acquisition_source=original_receipt.acquisition_source,
        candidate_csv=candidate_csv,
        candidates=(original_candidate, duplicate_candidate),
    )
    _rewrite_batch_receipt(batch_index, resealed_receipt)

    with pytest.raises(ValueError, match="duplicate ms1_feature_row_id"):
        _write_current_manifest(artifact)
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "output",
            expected_code_revision=_REVISION,
        )


def test_decision_ledger_excludes_non_feature_backed_ids_from_uniqueness_claim(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path / "artifact")
    candidate_csv = (
        artifact / "workflow" / "discovery" / "Sample_A" / "discovery_candidates.csv"
    )
    first_row = candidate_row("Sample_A")
    first_row["discovery_candidate_state"] = "review_only_ambiguous_coisolation"
    second_row = dict(first_row)
    second_row.update(
        {
            "candidate_id": "Sample_A#3@mz500_p384",
            "feature_family_id": "Sample_A@F0002",
            "best_ms2_scan_id": "3",
            "seed_scan_ids": "3;4",
        }
    )
    write_csv(candidate_csv, DISCOVERY_CANDIDATE_COLUMNS, [first_row, second_row])
    candidates = read_discovery_candidates_csv(
        candidate_csv,
    )

    batch_index = artifact / "workflow" / "discovery" / "discovery_batch_index.csv"
    batch = read_discovery_batch_index(batch_index)
    original_receipt = batch.candidate_receipts["Sample_A"]
    assert original_receipt is not None
    resealed_receipt = mint_discovery_candidate_universe_receipt(
        sample_stem="Sample_A",
        acquisition_source=original_receipt.acquisition_source,
        candidate_csv=candidate_csv,
        candidates=candidates,
    )
    _rewrite_batch_receipt(batch_index, resealed_receipt)

    _write_current_manifest(artifact)
    outputs = build_backfill_decision_ledger(
        artifact_root=artifact,
        output_dir=tmp_path / "output",
        expected_code_revision=_REVISION,
    )

    candidate_layer = outputs.summary["candidate_layer"]
    assert candidate_layer["populated_physical_feature_count"] == 0
    assert candidate_layer["unique_physical_feature_count"] == 0
    assert candidate_layer["duplicate_physical_feature_count"] == 0
    assert candidate_layer["excluded_non_feature_backed_populated_identity_count"] == 2


def test_decision_ledger_honors_declared_candidate_locator_and_relocates_only_attempt(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path / "artifact")
    default_path = (
        artifact / "workflow" / "discovery" / "Sample_A" / "discovery_candidates.csv"
    )
    declared_path = artifact / "owner-located" / "candidates.csv"
    declared_path.parent.mkdir()
    default_path.replace(declared_path)
    batch_index = artifact / "workflow" / "discovery" / "discovery_batch_index.csv"
    _rewrite_batch_candidate_locator(
        batch_index,
        "../../owner-located/candidates.csv",
    )

    _write_current_manifest(artifact)
    build_backfill_decision_ledger(
        artifact_root=artifact,
        output_dir=tmp_path / "declared-output",
        expected_code_revision=_REVISION,
    )

    declared_path.replace(default_path)
    attempt_path = (
        artifact.with_name(f"{artifact.name}.attempt")
        / "workflow"
        / "discovery"
        / "Sample_A"
        / "discovery_candidates.csv"
    )
    _rewrite_batch_candidate_locator(batch_index, str(attempt_path))
    _write_current_manifest(artifact)
    build_backfill_decision_ledger(
        artifact_root=artifact,
        output_dir=tmp_path / "attempt-output",
        expected_code_revision=_REVISION,
    )

    _rewrite_batch_candidate_locator(batch_index, "missing/candidates.csv")
    with pytest.raises(ValueError, match="required artifact file is missing"):
        _write_current_manifest(artifact)
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "missing-output",
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
        _write_current_manifest(artifact)
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
        _write_current_manifest(artifact)
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
        _write_current_manifest(artifact)
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
        _write_current_manifest(artifact)
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
        _write_current_manifest(artifact)
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
        _write_current_manifest(artifact)
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "enum",
            expected_code_revision=_REVISION,
        )


def test_decision_ledger_derives_inconclusive_priority_without_losers(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path / "artifact")
    review = artifact / "workflow" / "alignment" / "alignment_review.tsv"
    rows = _read_tsv_rows(review)
    rows[0]["consolidation_state"] = "not_consolidated"
    rows[0]["consolidation_winner_group_hypothesis_id"] = ""
    _write_tsv(review, _REVIEW_COLUMNS, rows)

    _write_current_manifest(artifact)
    result = build_backfill_decision_ledger(
        artifact_root=artifact,
        output_dir=tmp_path / "output",
        expected_code_revision=_REVISION,
    )

    assert result.summary["decision"]["status"] == (
        "inconclusive_no_observed_losers_or_exact_request_identity"
    )
    assert result.summary["decision"]["value_priority"] == (
        "inconclusive_no_optimization_priority"
    )
    assert result.summary["decision"]["reason"] == (
        "The artifact contains no primary_loser rows, and exact request "
        "equivalence is unavailable; it cannot prioritize either optimization."
    )


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("missing", "references missing winner"),
        ("non_winner", "references non-winner group"),
        ("winner_not_self", "must reference itself"),
        ("cycle", "cyclic winner chain"),
    ],
)
def test_decision_ledger_rejects_invalid_consolidation_relationships(
    tmp_path: Path,
    case: str,
    expected: str,
) -> None:
    artifact = _artifact(tmp_path / case)
    review = artifact / "workflow" / "alignment" / "alignment_review.tsv"
    rows = _read_tsv_rows(review)
    if case == "missing":
        rows[0]["consolidation_winner_group_hypothesis_id"] = "missing"
    elif case == "non_winner":
        rows[1]["consolidation_state"] = "not_consolidated"
        rows[1]["consolidation_winner_group_hypothesis_id"] = ""
    elif case == "winner_not_self":
        rows[1]["consolidation_winner_group_hypothesis_id"] = "group-1"
    else:
        rows[1]["consolidation_state"] = "primary_loser"
        rows[1]["consolidation_winner_group_hypothesis_id"] = "group-1"
    _write_tsv(review, _REVIEW_COLUMNS, rows)

    with pytest.raises(ValueError, match=expected):
        _write_current_manifest(artifact)
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "output",
            expected_code_revision=_REVISION,
        )


def test_decision_ledger_accepts_loser_chain_to_terminal_winner(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path / "artifact")
    review = artifact / "workflow" / "alignment" / "alignment_review.tsv"
    rows = _read_tsv_rows(review)
    rows[1]["consolidation_state"] = "primary_loser"
    rows[1]["consolidation_winner_group_hypothesis_id"] = "group-3"
    rows.append({column: "" for column in _REVIEW_COLUMNS})
    rows[2].update(
        {
            "feature_family_id": "group-3",
            "group_hypothesis_id": "group-3",
            "public_family_id": "group-3",
            "consolidation_state": "primary_winner",
            "consolidation_winner_group_hypothesis_id": "group-3",
            "event_cluster_ids": "owner-projection-3",
            "event_member_count": "1",
            "quantifiable_backfill_count": "0",
            "review_backfill_count": "0",
        }
    )
    _write_tsv(review, _REVIEW_COLUMNS, rows)
    timing_path = artifact / "timing.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    timing["records"][1]["metrics"].update(
        {
            "input_feature_count": 3,
            "backfill_feature_count": 3,
        }
    )
    timing_path.write_text(json.dumps(timing), encoding="utf-8")

    _write_current_manifest(artifact)
    result = build_backfill_decision_ledger(
        artifact_root=artifact,
        output_dir=tmp_path / "output",
        expected_code_revision=_REVISION,
    )

    assert result.summary["cross_sample_and_settlement"]["consolidation_counts"] == {
        "primary_loser": 2,
        "primary_winner": 1,
    }


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf")])
def test_decision_ledger_rejects_nonfinite_elapsed_values(
    tmp_path: Path,
    nonfinite: float,
) -> None:
    artifact = _artifact(tmp_path / "timing")
    timing_path = artifact / "timing.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    timing["records"][-1]["elapsed_sec"] = nonfinite
    timing_path.write_text(json.dumps(timing), encoding="utf-8")
    with pytest.raises(ValueError, match="finite nonnegative number"):
        _write_current_manifest(artifact)
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "timing-output",
            expected_code_revision=_REVISION,
        )

    artifact = _artifact(tmp_path / "heartbeat")
    (artifact / "heartbeat.json").write_text(
        json.dumps({"elapsed_sec": nonfinite}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="heartbeat elapsed_sec"):
        _write_current_manifest(artifact)
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "heartbeat-output",
            expected_code_revision=_REVISION,
        )


@pytest.mark.parametrize(
    ("scope", "input_count", "backfill_count", "skipped_count", "expected"),
    [
        ("selected-families", 2, 1, 1, "requires a complete full-audit"),
        ("full-audit", 2, 1, 1, "requires a complete full-audit"),
        ("full-audit", 2, 1, 0, "feature counts do not close"),
    ],
)
def test_decision_ledger_rejects_partial_or_incomplete_backfill_scope(
    tmp_path: Path,
    scope: str,
    input_count: int,
    backfill_count: int,
    skipped_count: int,
    expected: str,
) -> None:
    artifact = _artifact(tmp_path / "artifact")
    timing_path = artifact / "timing.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    scope_metrics = timing["records"][1]["metrics"]
    scope_metrics.update(
        {
            "backfill_scope": scope,
            "input_feature_count": input_count,
            "backfill_feature_count": backfill_count,
            "skipped_feature_count": skipped_count,
        }
    )
    timing_path.write_text(json.dumps(timing), encoding="utf-8")

    with pytest.raises(ValueError, match=expected):
        _write_current_manifest(artifact)
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "output",
            expected_code_revision=_REVISION,
        )


def test_decision_ledger_rejects_missing_backfill_scope_metric(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path / "artifact")
    timing_path = artifact / "timing.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    del timing["records"][1]["metrics"]["skipped_feature_count"]
    timing_path.write_text(json.dumps(timing), encoding="utf-8")

    with pytest.raises(ValueError, match="skipped_feature_count"):
        _write_current_manifest(artifact)
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "output",
            expected_code_revision=_REVISION,
        )


def test_decision_ledger_rejects_backfill_scope_review_universe_mismatch(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path / "artifact")
    timing_path = artifact / "timing.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    scope_metrics = timing["records"][1]["metrics"]
    scope_metrics.update(
        {
            "input_feature_count": 3,
            "backfill_feature_count": 3,
        }
    )
    timing_path.write_text(json.dumps(timing), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match alignment review"):
        _write_current_manifest(artifact)
        build_backfill_decision_ledger(
            artifact_root=artifact,
            output_dir=tmp_path / "output",
            expected_code_revision=_REVISION,
        )


def test_backfill_projection_rejects_cross_sample_candidate_substitution(
    tmp_path: Path,
) -> None:
    path = tmp_path / "backfill.tsv"
    _write_tsv(
        path,
        _BACKFILL_COLUMNS,
        [
            {
                "schema_version": ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
                "group_hypothesis_id": "group-1",
                "sample_stem": "Sample_B",
                "source_candidate_id": "candidate-a",
            }
        ],
    )

    with pytest.raises(ValueError, match="belongs to sample 'Sample_A'"):
        _read_backfill_evidence(
            path,
            known_group_ids=frozenset({"group-1"}),
            candidate_samples={
                "candidate-a": "Sample_A",
                "candidate-b": "Sample_B",
            },
            known_sample_stems=frozenset({"Sample_A", "Sample_B"}),
        )


def test_backfill_projection_accepts_declared_zero_candidate_sample(
    tmp_path: Path,
) -> None:
    path = tmp_path / "backfill.tsv"
    _write_tsv(
        path,
        _BACKFILL_COLUMNS,
        [
            {
                "schema_version": ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
                "group_hypothesis_id": "group-1",
                "sample_stem": "Sample_B",
                "source_candidate_id": "",
            }
        ],
    )

    census = _read_backfill_evidence(
        path,
        known_group_ids=frozenset({"group-1"}),
        candidate_samples={"candidate-a": "Sample_A"},
        known_sample_stems=frozenset({"Sample_A", "Sample_B"}),
    )

    assert census.row_count == 1
    assert census.candidate_group_ids == {}


def test_backfill_projection_does_not_infer_assignment_without_group(
    tmp_path: Path,
) -> None:
    path = tmp_path / "backfill.tsv"
    _write_tsv(
        path,
        _BACKFILL_COLUMNS,
        [
            {
                "schema_version": ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
                "group_hypothesis_id": "",
                "public_family_id": "",
                "sample_stem": "Sample_A",
                "source_candidate_id": "candidate-a",
            }
        ],
    )

    census = _read_backfill_evidence(
        path,
        known_group_ids=frozenset({"group-1"}),
        candidate_samples={"candidate-a": "Sample_A"},
        known_sample_stems=frozenset({"Sample_A"}),
    )

    assert census.row_count == 1
    assert census.candidate_group_ids == {}


def test_request_proxy_rejects_unknown_sample_and_is_order_neutral(
    tmp_path: Path,
) -> None:
    unknown_path = tmp_path / "unknown.tsv"
    _write_tsv(
        unknown_path,
        _PROVENANCE_COLUMNS,
        [
            {
                "schema_version": PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
                "group_hypothesis_id": "group-1",
                "sample_stem": "unknown",
                "backfill_seed_mz": "500.0000",
                "backfill_seed_rt": "8.5000",
            }
        ],
    )
    with pytest.raises(ValueError, match="unknown sample_stem"):
        _read_request_proxy(
            unknown_path,
            known_group_ids=frozenset({"group-1"}),
            known_sample_stems=frozenset({"Sample_A"}),
        )

    rows = [
        {
            "schema_version": PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
            "group_hypothesis_id": group_id,
            "sample_stem": "Sample_A",
            "backfill_seed_mz": "500.0000",
            "backfill_seed_rt": "8.5000",
        }
        for group_id in ("group-2", "group-2", "group-1")
    ]
    first_path = tmp_path / "first.tsv"
    second_path = tmp_path / "second.tsv"
    _write_tsv(first_path, _PROVENANCE_COLUMNS, rows)
    _write_tsv(second_path, _PROVENANCE_COLUMNS, list(reversed(rows)))
    kwargs = {
        "known_group_ids": frozenset({"group-1", "group-2"}),
        "known_sample_stems": frozenset({"Sample_A"}),
    }

    first = _read_request_proxy(first_path, **kwargs)
    second = _read_request_proxy(second_path, **kwargs)

    assert first == second
    assert first.projected_duplicate_count == 2
    assert first.per_group_duplicate_count == {"group-2": 2}


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
                "feature_family_id": "group-1",
                "group_hypothesis_id": "group-1",
                "public_family_id": "group-1",
                "consolidation_state": "primary_loser",
                "consolidation_winner_group_hypothesis_id": "group-2",
                "event_cluster_ids": "owner-projection-1",
                "event_member_count": "1",
                "quantifiable_backfill_count": "2",
                "review_backfill_count": "3",
            },
            {
                "feature_family_id": "group-2",
                "group_hypothesis_id": "group-2",
                "public_family_id": "group-2",
                "consolidation_state": "primary_winner",
                "consolidation_winner_group_hypothesis_id": "group-2",
                "event_cluster_ids": "owner-projection-2",
                "event_member_count": "1",
                "quantifiable_backfill_count": "0",
                "review_backfill_count": "0",
            },
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
    _write_current_manifest(root)
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
                            "backfill_scope": "full-audit",
                            "input_feature_count": 2,
                            "backfill_feature_count": 2,
                            "skipped_feature_count": 0,
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


def _write_current_manifest(root: Path) -> None:
    alignment = root / "workflow" / "alignment"
    retained_metadata = root / "inputs" / "sample_metadata.tsv"
    retained_metadata.write_text(
        "sample_stem\tinjection_order\nSample_A\t1\n", encoding="utf-8",
    )
    artifacts = {
        "provisional_matrix": alignment / "provisional_alignment_matrix.tsv",
        "provisional_identity": alignment / "provisional_row_identity.tsv",
        "provisional_cell_provenance": alignment / "provisional_cell_provenance.tsv",
        "provisional_summary": alignment / "provisional_matrix_summary.json",
    }
    for path in (*artifacts.values(), alignment / "alignment_matrix.tsv",
                 alignment / "alignment_matrix_identity.tsv"):
        if not path.exists():
            path.write_text(path.name + "\n", encoding="utf-8")
    write_provisional_run_manifest(
        root / "run_manifest.json", root=root, code_revision=_REVISION,
        preset_name="dna_dr",
        discovery_settings=DiscoverySettings(neutral_loss_profiles=(
            NeutralLossProfile(tag="DNA_dR", neutral_loss_da=116.0474),
        )),
        alignment_config=AlignmentConfig(),
        runtime=ProvisionalRunRuntimeConfiguration(
            sample_count=1, raw_workers=3, raw_xic_batch_size=16,
            owner_build_xic_backend="raw_superwindow",
            owner_backfill_xic_backend="raw",
            owner_backfill_window_strategy="super-window",
            owner_backfill_superwindow_span_factor=2,
        ),
        retained_sample_metadata=retained_metadata,
        discovery_batch_index=(
            root / "workflow" / "discovery" / "discovery_batch_index.csv"
        ),
        artifacts=artifacts,
    )


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
        "candidate_count": str(receipt.candidate_count),
        "high_count": str(receipt.candidate_count),
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
    if fieldnames == _BACKFILL_COLUMNS:
        rows = [{**_owner_valid_backfill_row(), **row} for row in rows]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _owner_valid_backfill_row() -> dict[str, str]:
    return {
        "schema_version": ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
        "feature_family_id": "group-1",
        "group_hypothesis_id": "group-1",
        "public_family_id": "group-1",
        "sample_stem": "Sample_A",
        "status": "detected",
        "production_cell_status": "detected",
        "backfill_tier": "",
        "write_matrix_value": "TRUE",
        "include_in_primary_matrix": "TRUE",
        "identity_decision": "primary",
        "row_flags": "backfill_cell_evidence_required",
        "area": "10",
        "primary_matrix_area": "10",
        "primary_matrix_area_source": "gaussian15_positive_asls_residual",
        "primary_matrix_area_reason": "",
        "apex_rt": "8.5",
        "height": "5",
        "peak_start_rt": "8.4",
        "peak_end_rt": "8.6",
        "rt_delta_sec": "0",
        "trace_quality": "owner_backfill",
        "scan_support_score": "1",
        "gap_fill_state": "gap_fill_detected",
        "gap_fill_reason": "group_centered_query_detected",
        "backfill_evidence_reason": "owner_backfill_supported",
        "source_candidate_id": "",
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": "500",
        "family_center_rt": "8.5",
        "region_shadow_verdict": "",
        "reason": "owner-centered MS1 backfill",
    }


def _rewrite_batch_candidate_locator(path: Path, candidate_csv: str) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        rows = list(reader)
    assert len(rows) == 1
    rows[0]["candidate_csv"] = candidate_csv
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _rewrite_batch_receipt(
    path: Path,
    receipt: DiscoveryCandidateUniverseReceipt,
) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        rows = list(reader)
    assert len(rows) == 1
    rows[0].update(
        {
            "candidate_count": "2",
            "high_count": "2",
            **discovery_receipt_index_fields(receipt),
        }
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
