from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from tools.diagnostics import provisional_backfill_candidate_gate as gate_cli


def test_cli_writes_sidecar_and_summary_without_mutating_matrix(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")
    matrix_path = alignment_dir / "alignment_matrix.tsv"
    before_hash = _sha256_file(matrix_path)
    output_dir = tmp_path / "gate"

    code = gate_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    assert _sha256_file(matrix_path) == before_hash
    rows = _read_tsv(output_dir / "alignment_production_candidate_gate.tsv")
    by_id = {row["feature_family_id"]: row for row in rows}
    assert set(by_id) == {"FAM_CAND"}
    assert by_id["FAM_CAND"]["candidate_gate_status"] == "keep_provisional"
    assert by_id["FAM_CAND"]["support_components"] == ""
    assert "missing_positive_tier2_support" in by_id["FAM_CAND"][
        "challenge_blockers"
    ]
    assert by_id["FAM_CAND"]["source_matrix_sha256"] == before_hash
    payload = json.loads(
        (output_dir / "alignment_production_candidate_gate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["readiness_label"] == "diagnostic_only"
    assert payload["production_ready"] is False
    assert payload["matrix_contract_changed"] is False
    assert payload["production_candidate_count"] == 0
    assert payload["row_count"] == 1
    assert payload["source_review_artifact"] == str(
        alignment_dir / "alignment_review.tsv"
    )
    assert payload["source_cell_artifact"] == str(
        alignment_dir / "alignment_cells.tsv"
    )
    assert payload["source_matrix_artifact"] == str(matrix_path)
    assert payload["source_matrix_sha256"] == before_hash


def test_cli_accepts_valid_tier2_sidecar(tmp_path: Path) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")
    matrix_path = alignment_dir / "alignment_matrix.tsv"
    before_hash = _sha256_file(matrix_path)
    output_dir = tmp_path / "gate"
    raw_manifest_path = _write_raw_manifest(tmp_path)
    source_context = gate_cli.source_context_for_artifacts(
        review_path=alignment_dir / "alignment_review.tsv",
        cell_path=alignment_dir / "alignment_cells.tsv",
        matrix_path=matrix_path,
    )
    review_rows = _read_tsv(alignment_dir / "alignment_review.tsv")
    candidate_rows = [
        row for row in review_rows if gate_cli.is_candidate_gate_scope(row)
    ]
    sidecar_path = _write_tier2_sidecar(
        tmp_path,
        family_id="FAM_CAND",
        candidate_rows=candidate_rows,
        source_context=source_context,
        raw_manifest_path=raw_manifest_path,
    )

    code = gate_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-dir",
            str(output_dir),
            "--tier2-trace-evidence-tsv",
            str(sidecar_path),
            "--tier2-raw-manifest-tsv",
            str(raw_manifest_path),
        ],
    )

    assert code == 0
    assert _sha256_file(matrix_path) == before_hash
    rows = _read_tsv(output_dir / "alignment_production_candidate_gate.tsv")
    by_id = {row["feature_family_id"]: row for row in rows}
    assert by_id["FAM_CAND"]["candidate_gate_status"] == "production_candidate"
    assert (
        by_id["FAM_CAND"]["support_components"]
        == "validated_tier2_trace_evidence"
    )
    payload = json.loads(
        (output_dir / "alignment_production_candidate_gate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["tier2_trace_evidence_artifact"] == str(sidecar_path)
    assert payload["tier2_raw_manifest_artifact"] == str(raw_manifest_path)
    assert payload["tier2_candidate_subset_count"] == 1
    assert payload["production_candidate_count"] == 1
    assert payload["production_ready"] is False
    assert payload["matrix_contract_changed"] is False


@pytest.mark.parametrize(
    "review_backfill_count",
    ("", "garbage", "NaN", "0.5", "-1"),
    ids=("empty", "nonnumeric", "nonfinite", "fractional", "negative"),
)
def test_cli_keeps_invalid_review_backfill_count_out_of_production_candidates(
    tmp_path: Path,
    review_backfill_count: str,
) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")
    review_path = alignment_dir / "alignment_review.tsv"
    review_rows = _read_tsv(review_path)
    for row in review_rows:
        if row["feature_family_id"] == "FAM_CAND":
            row["review_backfill_count"] = review_backfill_count
    _write_tsv(review_path, review_rows)

    matrix_path = alignment_dir / "alignment_matrix.tsv"
    output_dir = tmp_path / "gate"
    raw_manifest_path = _write_raw_manifest(tmp_path)
    source_context = gate_cli.source_context_for_artifacts(
        review_path=review_path,
        cell_path=alignment_dir / "alignment_cells.tsv",
        matrix_path=matrix_path,
    )
    candidate_rows = [
        row for row in review_rows if gate_cli.is_candidate_gate_scope(row)
    ]
    sidecar_path = _write_tier2_sidecar(
        tmp_path,
        family_id="FAM_CAND",
        candidate_rows=candidate_rows,
        source_context=source_context,
        raw_manifest_path=raw_manifest_path,
    )

    code = gate_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-dir",
            str(output_dir),
            "--tier2-trace-evidence-tsv",
            str(sidecar_path),
            "--tier2-raw-manifest-tsv",
            str(raw_manifest_path),
        ],
    )

    assert code == 0
    rows = _read_tsv(output_dir / "alignment_production_candidate_gate.tsv")
    family = next(row for row in rows if row["feature_family_id"] == "FAM_CAND")
    assert family["candidate_gate_status"] == "audit"
    assert (
        "backfill_owner_rehydration_unavailable"
        in family["challenge_blockers"]
    )


def test_cli_stale_tier2_hash_emits_machine_readable_blocker(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")
    output_dir = tmp_path / "gate"
    raw_manifest_path = _write_raw_manifest(tmp_path)
    source_context = gate_cli.source_context_for_artifacts(
        review_path=alignment_dir / "alignment_review.tsv",
        cell_path=alignment_dir / "alignment_cells.tsv",
        matrix_path=alignment_dir / "alignment_matrix.tsv",
    )
    review_rows = _read_tsv(alignment_dir / "alignment_review.tsv")
    candidate_rows = [
        row for row in review_rows if gate_cli.is_candidate_gate_scope(row)
    ]
    sidecar_path = _write_tier2_sidecar(
        tmp_path,
        family_id="FAM_CAND",
        candidate_rows=candidate_rows,
        source_context=source_context,
        raw_manifest_path=raw_manifest_path,
        source_review_sha256="0" * 64,
    )

    code = gate_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-dir",
            str(output_dir),
            "--tier2-trace-evidence-tsv",
            str(sidecar_path),
            "--tier2-raw-manifest-tsv",
            str(raw_manifest_path),
        ],
    )

    assert code == 0
    rows = _read_tsv(output_dir / "alignment_production_candidate_gate.tsv")
    by_id = {row["feature_family_id"]: row for row in rows}
    assert by_id["FAM_CAND"]["candidate_gate_status"] == "audit"
    assert "source_hash_mismatch" in by_id["FAM_CAND"]["challenge_blockers"]


def test_cli_consumes_diagnostic_sidecar_as_diagnostic_only(tmp_path: Path) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")
    output_dir = tmp_path / "gate"
    raw_manifest_path = _write_raw_manifest(tmp_path)
    source_context = gate_cli.source_context_for_artifacts(
        review_path=alignment_dir / "alignment_review.tsv",
        cell_path=alignment_dir / "alignment_cells.tsv",
        matrix_path=alignment_dir / "alignment_matrix.tsv",
    )
    review_rows = _read_tsv(alignment_dir / "alignment_review.tsv")
    candidate_rows = [
        row for row in review_rows if gate_cli.is_candidate_gate_scope(row)
    ]
    sidecar_path = _write_tier2_sidecar(
        tmp_path,
        family_id="FAM_CAND",
        candidate_rows=candidate_rows,
        source_context=source_context,
        raw_manifest_path=raw_manifest_path,
        criteria_version="tier2_trace_identity_diagnostic",
        producer_version="raw_trace_reread_tier2",
        evidence_status="inconclusive",
        support_component="",
        raw_trace_reread_status="pass",
        coherence_status="inconclusive",
        challenge_blockers="",
        dependent_context=(
            "neighbor_interference_not_assessed;"
            "raw_trace_reread;"
            "rescued_coherence"
        ),
    )

    code = gate_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-dir",
            str(output_dir),
            "--tier2-trace-evidence-tsv",
            str(sidecar_path),
            "--tier2-raw-manifest-tsv",
            str(raw_manifest_path),
        ],
    )

    assert code == 0
    rows = _read_tsv(output_dir / "alignment_production_candidate_gate.tsv")
    by_id = {row["feature_family_id"]: row for row in rows}
    assert by_id["FAM_CAND"]["candidate_gate_status"] == "audit"
    assert by_id["FAM_CAND"]["support_components"] == ""
    assert "tier2_diagnostic_only" in by_id["FAM_CAND"]["challenge_blockers"]
    payload = json.loads(
        (output_dir / "alignment_production_candidate_gate.json").read_text(
            encoding="utf-8",
        )
    )
    assert payload["production_candidate_count"] == 0
    assert payload["production_ready"] is False


def test_cli_requires_tier2_sidecar_and_manifest_together(
    tmp_path: Path,
    capsys,
) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")
    sidecar_path = tmp_path / "alignment_tier2_trace_evidence.tsv"
    sidecar_path.write_text("", encoding="utf-8")

    code = gate_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--tier2-trace-evidence-tsv",
            str(sidecar_path),
        ],
    )

    assert code == 2
    stderr = capsys.readouterr().err
    assert (
        "tier2_trace_evidence_tsv and tier2_raw_manifest_tsv "
        "must be supplied together"
    ) in stderr


def test_cli_defaults_output_dir_to_alignment_dir(tmp_path: Path) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")

    code = gate_cli.main(["--alignment-dir", str(alignment_dir)])

    assert code == 0
    assert (alignment_dir / "alignment_production_candidate_gate.tsv").is_file()
    assert (alignment_dir / "alignment_production_candidate_gate.json").is_file()


def test_cli_reports_missing_required_artifact(
    tmp_path: Path,
    capsys,
) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()

    code = gate_cli.main(["--alignment-dir", str(alignment_dir)])

    assert code == 2
    stderr = capsys.readouterr().err
    assert f"Required TSV not found: {alignment_dir / 'alignment_review.tsv'}" in stderr


def test_cli_reports_missing_required_columns(
    tmp_path: Path,
    capsys,
) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    _write_tsv(alignment_dir / "alignment_review.tsv", [{"feature_family_id": "FAM"}])
    _write_tsv(alignment_dir / "alignment_cells.tsv", [{"feature_family_id": "FAM"}])
    _write_tsv(alignment_dir / "alignment_matrix.tsv", [{"feature_family_id": "FAM"}])

    code = gate_cli.main(["--alignment-dir", str(alignment_dir)])

    assert code == 2
    stderr = capsys.readouterr().err
    assert "missing required columns" in stderr
    assert "identity_decision" in stderr


def test_cli_rejects_review_without_review_backfill_count(
    tmp_path: Path,
    capsys,
) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")
    review_path = alignment_dir / "alignment_review.tsv"
    rows = _read_tsv(review_path)
    for row in rows:
        row.pop("review_backfill_count")
    _write_tsv(review_path, rows)

    code = gate_cli.main(["--alignment-dir", str(alignment_dir)])

    assert code == 2
    assert "review_backfill_count" in capsys.readouterr().err


def _write_raw_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "alignment_tier2_raw_manifest.tsv"
    path.write_text(
        (
            "sample_stem\traw_file_path\traw_file_size_bytes\t"
            "raw_file_mtime_utc\traw_reader_runtime\tpython_executable\tdll_dir\n"
            "S1\tC:\\Xcalibur\\data\\S1.raw\t123\t"
            "2026-05-29T00:00:00Z\tpythonnet\t.venv\\Scripts\\python.exe\t"
            "C:\\Xcalibur\\system\\programs\n"
        ),
        encoding="utf-8",
    )
    return path


def _write_tier2_sidecar(
    tmp_path: Path,
    *,
    family_id: str,
    candidate_rows: list[dict[str, str]],
    source_context,
    raw_manifest_path: Path,
    source_review_sha256: str | None = None,
    criteria_version: str = "tier2_trace_identity_rescued_coherence",
    producer_version: str = "raw_trace_reread_tier2",
    evidence_status: str = "validated",
    support_component: str = "validated_tier2_trace_evidence",
    raw_trace_reread_status: str = "pass",
    coherence_status: str = "pass",
    challenge_blockers: str = "",
    dependent_context: str = "",
) -> Path:
    subset = gate_cli.tier2_candidate_subset_signature(candidate_rows)
    row = {
        "feature_family_id": family_id,
        "evidence_status": evidence_status,
        "support_component": support_component,
        "criteria_version": criteria_version,
        "producer_version": producer_version,
        "raw_trace_reread_status": raw_trace_reread_status,
        "seed_apex_rt": "8.000",
        "tier2_apex_rt": "8.100",
        "apex_delta_sec": "6.0",
        "scan_support_score": "0.80",
        "trace_scan_count": "8",
        "boundary_start_rt": "7.950",
        "boundary_end_rt": "8.050",
        "boundary_width_sec": "6.0",
        "neighbor_interference_ratio": "0.10",
        "rescued_cell_count_checked": "2",
        "rescued_cell_count_supported": "2",
        "rescued_apex_rt_span_sec": "6.0",
        "rescued_boundary_overlap_min": "0.80",
        "coherence_status": coherence_status,
        "challenge_blockers": challenge_blockers,
        "dependent_context": dependent_context,
        "source_alignment_review_sha256": (
            source_review_sha256 or source_context.review_sha256
        ),
        "source_alignment_cells_sha256": source_context.cell_sha256,
        "source_raw_manifest_sha256": _sha256_file(raw_manifest_path),
        "source_candidate_subset_sha256": subset.sha256,
        "source_candidate_subset_count": str(subset.count),
        "source_expected_sample_count": "8",
        "raw_reader_runtime": "pythonnet",
        "python_executable": ".venv\\Scripts\\python.exe",
        "dll_dir": "C:\\Xcalibur\\system\\programs",
        "producer_command": "synthetic-test-fixture",
        "generated_at_utc": "2026-05-29T00:00:00Z",
        "scan_availability_score": "1.0",
        "trace_apex_intensity": "1000",
        "trace_baseline_noise": "10",
        "trace_signal_to_noise_proxy": "20",
        "trace_apex_prominence_score": "0.75",
        "scan_support_basis": "scan_count_only",
        "seed_rescued_boundary_overlap_min": "0.80",
        "rescued_pairwise_boundary_overlap_min": "0.85",
        "family_consensus_boundary_overlap_min": "0.82",
        "seed_rescued_apex_span_sec": "6.0",
        "rescued_only_apex_span_sec": "3.0",
        "neighbor_interference_status": "not_assessed",
    }
    columns = list(row)
    path = tmp_path / "alignment_tier2_trace_evidence.tsv"
    path.write_text(
        "\n".join(("\t".join(columns), "\t".join(row[column] for column in columns)))
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_alignment_run(path: Path) -> Path:
    path.mkdir(parents=True)
    _write_tsv(
        path / "alignment_review.tsv",
        [
            _review_row(
                "FAM_KEEP",
                flags="single_detected_seed",
                detected=1,
                rescued=1,
            ),
            _review_row(
                "FAM_CAND",
                flags="single_detected_seed;provisional_retention_candidate",
                detected=1,
                rescued=2,
            ),
            _review_row(
                "FAM_PRIMARY",
                include="TRUE",
                decision="production_family",
                reason="owner_complete_link",
                flags="",
                detected=2,
                rescued=0,
            ),
        ],
    )
    _write_tsv(
        path / "alignment_cells.tsv",
        [
            _cell_row("FAM_KEEP", "S1", "detected"),
            _cell_row("FAM_KEEP", "S2", "backfilled"),
            _cell_row("FAM_CAND", "S1", "detected"),
            _cell_row("FAM_CAND", "S2", "backfilled"),
            _cell_row("FAM_CAND", "S3", "backfilled"),
            _cell_row("FAM_PRIMARY", "S1", "detected"),
            _cell_row("FAM_PRIMARY", "S2", "detected"),
        ],
    )
    _write_tsv(
        path / "alignment_matrix.tsv",
        [{"Mz": "269.145", "RT": "10.0000", "S1": "100", "S2": "90"}],
    )
    return path


def _review_row(
    family_id: str,
    *,
    flags: str,
    detected: int,
    rescued: int,
    include: str = "FALSE",
    decision: str = "provisional_discovery",
    reason: str = "insufficient_detected_identity_support",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "neutral_loss_tag": "DNA_dR",
        "include_in_primary_matrix": include,
        "identity_decision": decision,
        "identity_confidence": "review",
        "identity_reason": reason,
        "primary_evidence": "owner_complete_link",
        "quantifiable_detected_count": str(detected),
        "quantifiable_backfill_count": str(rescued),
        "review_backfill_count": "0",
        "accepted_backfill_count": str(rescued),
        "duplicate_assigned_count": "0",
        "ambiguous_ms1_owner_count": "0",
        "row_flags": flags,
        "family_evidence": "owner_complete_link;owner_count=1",
    }


def _cell_row(family_id: str, sample: str, status: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample,
        "status": status,
        "area": "100",
        "apex_rt": "8.00",
        "height": "50",
        "peak_start_rt": "7.95",
        "peak_end_rt": "8.05",
        "rt_delta_sec": "0.0",
        "trace_quality": "owner_backfill" if status == "backfilled" else "clean",
        "scan_support_score": "0.8",
        "reason": status,
    }


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()
