from __future__ import annotations

from pathlib import Path

from tests.test_shared_peak_identity_loaders import (
    _candidate_gate,
    _cells,
    _oracle,
    _review,
)
from xic_extractor.diagnostics.shared_peak_identity_explanation import (
    machine_artifacts,
)
from xic_extractor.diagnostics.shared_peak_identity_explanation.assembler import (
    assemble_evidence_vectors,
)
from xic_extractor.diagnostics.shared_peak_identity_explanation.oracle import (
    load_manual_oracle,
)


def test_assembler_emits_source_rows_and_candidate_gate_context(tmp_path: Path) -> None:
    oracle_rows = load_manual_oracle(_oracle(tmp_path, sample_id="Sample_A"))
    matches = machine_artifacts.load_machine_matches(
        oracle_rows=oracle_rows,
        alignment_review_tsv=_review(tmp_path),
        alignment_cells_tsv=_cells(
            tmp_path,
            [{"feature_family_id": "FAMTEST", "sample_stem": "Sample_A"}],
        ),
        candidate_gate_tsv=_candidate_gate(tmp_path),
    )

    evidence = assemble_evidence_vectors(oracle_rows, matches)

    assert len(evidence) >= 3
    assert {row["source_role"] for row in evidence} >= {
        "selected_peak",
        "rescued_cell",
        "candidate_gate_family_context",
    }
    assert all(row["source_artifact_sha256"] for row in evidence)


def test_assembler_uses_manual_only_row_for_sentinel(tmp_path: Path) -> None:
    oracle_rows = load_manual_oracle(
        Path("docs/superpowers/fixtures/shared_peak_identity_manual_oracle.tsv")
    )
    context = [row for row in oracle_rows if row.sample_id == "__family_context__"]

    evidence = assemble_evidence_vectors(context, {})

    assert evidence[0]["machine_current_label"] == "not_applicable"
    assert evidence[0]["source_role"] == "manual_oracle"
