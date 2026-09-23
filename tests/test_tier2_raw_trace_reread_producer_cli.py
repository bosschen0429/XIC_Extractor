from __future__ import annotations

import json
from pathlib import Path

from tools.diagnostics import provisional_backfill_candidate_gate as gate_cli
from tools.diagnostics import tier2_raw_trace_reread_producer as producer_cli


def test_cli_produces_sidecar_manifest_pair_consumed_by_gate(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "producer"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    alignment_dir.mkdir()
    raw_dir.mkdir()
    dll_dir.mkdir()
    _write_alignment_artifacts(alignment_dir)
    for sample_stem in ("Seed_A", "Rescue_A", "Rescue_B"):
        (raw_dir / f"{sample_stem}.raw").write_text("raw", encoding="utf-8")

    outputs = producer_cli.run_producer(
        alignment_dir=alignment_dir,
        output_dir=output_dir,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        expected_sample_count=3,
        trace_loader=_passing_trace_loader,
        producer_command="pytest cli producer",
        generated_at_utc="2026-05-29T00:00:00Z",
        python_executable=".venv\\Scripts\\python.exe",
    )

    assert outputs["trace_evidence"].name == "alignment_tier2_trace_evidence.tsv"
    assert outputs["raw_manifest"].name == "alignment_tier2_raw_manifest.tsv"
    assert outputs["summary"].name == "alignment_tier2_trace_evidence_summary.json"

    gate_output = gate_cli.run_gate(
        alignment_dir=alignment_dir,
        output_dir=tmp_path / "gate",
        tier2_trace_evidence_tsv=outputs["trace_evidence"],
        tier2_raw_manifest_tsv=outputs["raw_manifest"],
    )
    rows = _read_tsv(gate_output["tsv"])
    summary = json.loads(gate_output["json"].read_text(encoding="utf-8"))
    producer_summary = json.loads(outputs["summary"].read_text(encoding="utf-8"))

    assert rows[0]["candidate_gate_status"] == "audit"
    assert rows[0]["tier2_evidence_available"] == "FALSE"
    assert "tier2_diagnostic_only" in rows[0]["challenge_blockers"]
    assert summary["tier2_candidate_subset_count"] == 1
    assert producer_summary["readiness_label"] == "diagnostic_only"
    assert producer_summary["producer_version"] == "raw_trace_reread_tier2"
    assert (
        producer_summary["criteria_version"]
        == "tier2_trace_identity_diagnostic"
    )
    assert producer_summary["source_expected_sample_count"] == 3
    assert producer_summary["candidate_subset_count"] == 1
    assert producer_summary["rows_evaluated"] == 1
    assert producer_summary["evidence_status_counts"] == {"inconclusive": 1}
    assert producer_summary["positive_support_count"] == 0
    assert producer_summary["validated_count"] == 0
    assert producer_summary["inconclusive_count"] == 1


def test_cli_missing_raw_emits_inconclusive_sidecar_row(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "producer"
    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    alignment_dir.mkdir()
    raw_dir.mkdir()
    dll_dir.mkdir()
    _write_alignment_artifacts(alignment_dir)
    for sample_stem in ("Rescue_A", "Rescue_B"):
        (raw_dir / f"{sample_stem}.raw").write_text("raw", encoding="utf-8")

    outputs = producer_cli.run_producer(
        alignment_dir=alignment_dir,
        output_dir=output_dir,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        expected_sample_count=3,
        trace_loader=_missing_seed_trace_loader,
        producer_command="pytest cli producer",
        generated_at_utc="2026-05-29T00:00:00Z",
        python_executable=".venv\\Scripts\\python.exe",
    )
    rows = _read_tsv(outputs["trace_evidence"])
    manifest_rows = _read_tsv(outputs["raw_manifest"])

    assert rows[0]["evidence_status"] == "inconclusive"
    assert rows[0]["raw_trace_reread_status"] == "inconclusive"
    assert "raw_unavailable" in rows[0]["challenge_blockers"]
    assert {row["sample_stem"] for row in manifest_rows} == {
        "Seed_A",
        "Rescue_A",
        "Rescue_B",
    }
    seed_manifest = next(row for row in manifest_rows if row["sample_stem"] == "Seed_A")
    assert seed_manifest["raw_file_size_bytes"] == ""


def _write_alignment_artifacts(alignment_dir: Path) -> None:
    review_columns = (
        "feature_family_id",
        "neutral_loss_tag",
        "family_center_mz",
        "family_center_rt",
        "include_in_primary_matrix",
        "identity_decision",
        "identity_confidence",
        "identity_reason",
        "primary_evidence",
        "quantifiable_detected_count",
        "quantifiable_backfill_count",
        "review_backfill_count",
        "duplicate_assigned_count",
        "ambiguous_ms1_owner_count",
        "row_flags",
    )
    review_row = {
        "feature_family_id": "FAM001",
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": "242.114",
        "family_center_rt": "8.000",
        "include_in_primary_matrix": "FALSE",
        "identity_decision": "provisional_discovery",
        "identity_confidence": "review",
        "identity_reason": "candidate",
        "primary_evidence": "owner_complete_link",
        "quantifiable_detected_count": "1",
        "quantifiable_backfill_count": "2",
        "review_backfill_count": "0",
        "duplicate_assigned_count": "0",
        "ambiguous_ms1_owner_count": "0",
        "row_flags": "single_detected_seed;provisional_retention_candidate",
    }
    _write_tsv(alignment_dir / "alignment_review.tsv", (review_row,), review_columns)

    cell_columns = (
        "feature_family_id",
        "sample_stem",
        "status",
        "area",
        "apex_rt",
        "height",
        "peak_start_rt",
        "peak_end_rt",
        "rt_delta_sec",
        "trace_quality",
        "scan_support_score",
        "reason",
    )
    cell_rows = (
        _cell("Seed_A", "detected", "8.000", "7.960", "8.040"),
        _cell("Rescue_A", "backfilled", "8.020", "7.980", "8.060"),
        _cell("Rescue_B", "backfilled", "8.030", "7.990", "8.070"),
    )
    _write_tsv(alignment_dir / "alignment_cells.tsv", cell_rows, cell_columns)
    _write_tsv(
        alignment_dir / "alignment_matrix.tsv",
        ({"feature_family_id": "FAM001"},),
        ("feature_family_id",),
    )


def _cell(
    sample_stem: str,
    status: str,
    apex_rt: str,
    start_rt: str,
    end_rt: str,
) -> dict[str, str]:
    return {
        "feature_family_id": "FAM001",
        "sample_stem": sample_stem,
        "status": status,
        "area": "1000",
        "apex_rt": apex_rt,
        "height": "500",
        "peak_start_rt": start_rt,
        "peak_end_rt": end_rt,
        "rt_delta_sec": "0",
        "trace_quality": "ok",
        "scan_support_score": "1.0",
        "reason": "",
    }


def _passing_trace_loader(
    _sample_stem: str,
    _mz: float,
    rt_min: float,
    rt_max: float,
    _ppm_tolerance: float,
) -> tuple[object, object]:
    step = (rt_max - rt_min) / 20.0
    rt = tuple(rt_min + step * index for index in range(21))
    center = (rt_min + rt_max) / 2.0
    intensity = tuple(1000.0 - abs(value - center) * 10000.0 for value in rt)
    return rt, intensity


def _missing_seed_trace_loader(
    sample_stem: str,
    _mz: float,
    _rt_min: float,
    _rt_max: float,
    _ppm_tolerance: float,
) -> tuple[object, object]:
    if sample_stem == "Seed_A":
        raise FileNotFoundError("missing seed raw")
    return _passing_trace_loader(sample_stem, _mz, _rt_min, _rt_max, _ppm_tolerance)


def _write_tsv(
    path: Path,
    rows: tuple[dict[str, str], ...],
    columns: tuple[str, ...],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\t".join(columns) + "\n")
        for row in rows:
            handle.write("\t".join(row.get(column, "") for column in columns) + "\n")


def _read_tsv(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
