from __future__ import annotations

import csv
from pathlib import Path

import pytest

from xic_extractor.alignment.shared_peak_identity_explanation import (
    machine_evidence_loaders,
)
from xic_extractor.alignment.shared_peak_identity_explanation.machine_artifacts import (
    load_machine_matches,
)
from xic_extractor.alignment.shared_peak_identity_explanation.oracle import (
    load_manual_oracle,
)
from xic_extractor.alignment.shared_peak_identity_explanation.schema import (
    ORACLE_COLUMNS,
)


def test_machine_evidence_loaders_handle_none_path() -> None:
    assert machine_evidence_loaders.load_cwt_shape_evidence(None) == {}
    assert machine_evidence_loaders.load_tier2_trace_evidence(None) == {}
    assert machine_evidence_loaders.load_candidate_ms2_pattern_evidence(None) == {}
    assert machine_evidence_loaders.load_ms1_pattern_coherence_evidence(None) == {}
    assert machine_evidence_loaders.load_qc_ms1_pattern_reference_evidence(None) == {}
    assert machine_evidence_loaders.load_matrix_rt_drift_policy_evidence(None) == {}
    assert machine_evidence_loaders.load_sample_negative_evidence(None) == {}
    assert machine_evidence_loaders.load_rt_mode_evidence(None) == {}
    assert machine_evidence_loaders.load_peak_hypothesis_selection(None) == {}


def test_machine_evidence_loaders_load_data(tmp_path: Path) -> None:
    cwt_path = tmp_path / "cwt.tsv"
    _write_tsv(
        cwt_path,
        machine_evidence_loaders.CWT_SHAPE_REQUIRED_COLUMNS,
        [
            {col: "1" for col in machine_evidence_loaders.CWT_SHAPE_REQUIRED_COLUMNS}
            | {"feature_family_id": "FAM1", "sample_stem": "S1"}
        ],
    )
    loaded_cwt = machine_evidence_loaders.load_cwt_shape_evidence(cwt_path)
    assert ("FAM1", "S1") in loaded_cwt

    tier2_path = tmp_path / "tier2.tsv"
    _write_tsv(
        tier2_path,
        machine_evidence_loaders.TIER2_TRACE_REQUIRED_COLUMNS,
        [
            {col: "1" for col in machine_evidence_loaders.TIER2_TRACE_REQUIRED_COLUMNS}
            | {"feature_family_id": "FAM1"}
        ],
    )
    loaded_tier2 = machine_evidence_loaders.load_tier2_trace_evidence(tier2_path)
    assert "FAM1" in loaded_tier2


def test_machine_loader_preserves_ambiguous_source_row_ids(tmp_path: Path) -> None:
    oracle_path = _oracle(tmp_path, sample_id="Sample_A")
    review_path = _review(tmp_path)
    cells_path = _cells(
        tmp_path,
        [
            {"feature_family_id": "FAMTEST", "sample_stem": "Sample_A"},
            {"feature_family_id": "FAMTEST", "sample_stem": "Sample_A"},
        ],
    )
    rows = load_manual_oracle(oracle_path)

    matches = load_machine_matches(
        oracle_rows=rows,
        alignment_review_tsv=review_path,
        alignment_cells_tsv=cells_path,
    )

    sample_matches = [
        match for match in matches["FAMTEST|Sample_A"] if match.sample_level
    ]
    assert [match.source_row_id for match in sample_matches] == [
        "alignment_cells.tsv:1",
        "alignment_cells.tsv:2",
    ]


def test_machine_loader_keeps_candidate_gate_family_context(tmp_path: Path) -> None:
    oracle_path = _oracle(tmp_path, sample_id="Sample_A")
    review_path = _review(tmp_path)
    cells_path = _cells(
        tmp_path,
        [{"feature_family_id": "FAMTEST", "sample_stem": "Sample_A"}],
    )
    gate_path = _candidate_gate(tmp_path)
    rows = load_manual_oracle(oracle_path)

    matches = load_machine_matches(
        oracle_rows=rows,
        alignment_review_tsv=review_path,
        alignment_cells_tsv=cells_path,
        candidate_gate_tsv=gate_path,
    )

    roles = {match.source_role for match in matches["FAMTEST|Sample_A"]}
    assert "candidate_gate_family_context" in roles
    assert all(
        not match.sample_level
        for match in matches["FAMTEST|Sample_A"]
        if match.source_role == "candidate_gate_family_context"
    )


def test_oracle_loader_rejects_duplicate_oracle_row_id(tmp_path: Path) -> None:
    path = _oracle(tmp_path, sample_id="Sample_A", duplicate=True)

    with pytest.raises(ValueError, match="duplicate oracle_row_id"):
        load_manual_oracle(path)


def _oracle(tmp_path: Path, *, sample_id: str, duplicate: bool = False) -> Path:
    path = tmp_path / "oracle.tsv"
    row = {
        "oracle_schema_version": "shared_peak_identity_manual_oracle",
        "oracle_row_id": f"FAMTEST|{sample_id}",
        "feature_family_id": "FAMTEST",
        "sample_id": sample_id,
        "manual_label": "pass",
        "manual_label_source": "direct_eic_ms2_review",
        "manual_confidence": "high",
        "manual_scope": "reviewed_cell",
        "manual_scope_rule_id": "",
        "manual_reason_tags": "rt_close;shape_complete;pattern_similar",
        "reviewed_eic": "TRUE",
        "reviewed_ms2_pattern": "TRUE",
        "reviewed_nl_or_product_pattern": "TRUE",
        "reviewed_intensity_opportunity": "TRUE",
        "dda_opportunity_basis": "observed",
        "related_family_id": "",
        "manual_review_note": "",
        "manual_review_source": "pytest",
        "manual_reviewed_at": "2026-05-29",
    }
    _write_tsv(path, ORACLE_COLUMNS, [row, row] if duplicate else [row])
    return path


def _review(tmp_path: Path) -> Path:
    path = tmp_path / "alignment_review.tsv"
    _write_tsv(
        path,
        ("feature_family_id", "identity_decision", "identity_reason", "row_flags"),
        [
            {
                "feature_family_id": "FAMTEST",
                "identity_decision": "provisional_discovery",
                "identity_reason": "pytest",
                "row_flags": "single_detected_seed",
            }
        ],
    )
    return path


def _cells(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    path = tmp_path / "alignment_cells.tsv"
    fields = (
        "feature_family_id",
        "sample_stem",
        "status",
        "apex_rt",
        "peak_start_rt",
        "peak_end_rt",
        "rt_delta_sec",
        "trace_quality",
        "scan_support_score",
        "reason",
    )
    normalized = [
        {
            **{
                "status": "backfilled",
                "apex_rt": "1.0",
                "peak_start_rt": "0.9",
                "peak_end_rt": "1.1",
                "rt_delta_sec": "0",
                "trace_quality": "owner_backfill",
                "scan_support_score": "1",
                "reason": "pytest",
            },
            **row,
        }
        for row in rows
    ]
    _write_tsv(path, fields, normalized)
    return path


def _candidate_gate(tmp_path: Path) -> Path:
    path = tmp_path / "alignment_production_candidate_gate.tsv"
    _write_tsv(
        path,
        (
            "feature_family_id",
            "candidate_gate_status",
            "recommended_action",
            "challenge_blockers",
            "dependent_context",
        ),
        [
            {
                "feature_family_id": "FAMTEST",
                "candidate_gate_status": "audit",
                "recommended_action": "review",
                "challenge_blockers": "tier2_diagnostic_only",
                "dependent_context": "raw_trace_reread_1",
            }
        ],
    )
    return path


def _write_tsv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
