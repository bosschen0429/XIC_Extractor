from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.diagnostics import alignment_health_packet as health


def test_alignment_health_packet_reports_alignment_sentinels(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "health"
    alignment_dir.mkdir()
    _write_required_alignment_artifacts(alignment_dir)
    _write_cell_evidence(alignment_dir)

    outputs = health.build_alignment_health_packet(
        alignment_dir=alignment_dir,
        output_dir=output_dir,
        sentinel_limit=5,
    )

    packet = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    metrics = packet["summary_metrics"]
    assert metrics["review_family_count"] == 2
    assert metrics["matrix_sample_column_count"] == 2
    assert metrics["unresolved_count_total"] == 2
    sentinel = packet["sentinel_rows"][0]
    assert sentinel["feature_id"] == "FAM_BAD"
    assert sentinel["issue_class"] == (
        "owner_ambiguity;"
        "accepted_backfill_dependency;not_searched_pressure;consolidation_loser;"
        "owner_pressure_flag"
    )
    assert sentinel["recommended_action"] == "inspect_owner_assignment"
    assert sentinel["cell_status_counts"] == "backfilled:1;unresolved:1"
    sentinel_tsv = outputs.sentinels_tsv.read_text(encoding="utf-8")
    assert "FAM_BAD" in sentinel_tsv
    assert "owner_ambiguity" in sentinel_tsv


def test_alignment_health_packet_allows_missing_optional_artifacts(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "health"
    alignment_dir.mkdir()
    _write_required_alignment_artifacts(alignment_dir)

    outputs = health.build_alignment_health_packet(
        alignment_dir=alignment_dir,
        output_dir=output_dir,
    )

    packet = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    metrics = packet["summary_metrics"]
    assert metrics["cell_evidence_row_count"] == 0
    assert outputs.summary_tsv.is_file()


def test_alignment_health_packet_requires_review_columns(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "health"
    alignment_dir.mkdir()
    (alignment_dir / "alignment_review.tsv").write_text(
        "feature_id\nFAM_BAD\n",
        encoding="utf-8",
    )
    (alignment_dir / "alignment_matrix.tsv").write_text(
        "Mz\tRT\tSampleA\n100\t5\t10\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required columns"):
        health.build_alignment_health_packet(
            alignment_dir=alignment_dir,
            output_dir=output_dir,
        )


def _write_required_alignment_artifacts(alignment_dir: Path) -> None:
    (alignment_dir / "alignment_review.tsv").write_text(
        "feature_id\tmatrix_row_index\tdetected_count\tunresolved_count\t"
        "not_searched_count\taccepted_cell_count\t"
        "accepted_backfill_count\trow_flags\treason\n"
        "FAM_BAD\t1\t4\t2\t1\t2\t1\t"
        "family_consolidation_loser;ambiguous_ms1_owner_pressure\tneeds review\n"
        "FAM_OK\t2\t5\t0\t0\t5\t0\t\tok\n",
        encoding="utf-8",
    )
    (alignment_dir / "alignment_matrix.tsv").write_text(
        "Mz\tRT\tSampleA\tSampleB\n100\t5\t10\t\n101\t6\t\t20\n",
        encoding="utf-8",
    )


def _write_cell_evidence(alignment_dir: Path) -> None:
    (alignment_dir / "alignment_backfill_cell_evidence.tsv").write_text(
        "feature_id\tsample_stem\tstatus\n"
        "FAM_BAD\tSampleA\tbackfilled\n"
        "FAM_BAD\tSampleB\tunresolved\n"
        "FAM_OK\tSampleA\tdetected\n",
        encoding="utf-8",
    )
