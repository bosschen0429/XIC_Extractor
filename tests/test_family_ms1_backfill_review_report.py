import csv
import json
from pathlib import Path

from tools.diagnostics import family_ms1_backfill_review_report as report


def test_report_builds_overlay_limited_review_queue(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "out"
    overlay_dir = tmp_path / "overlay"
    _write_alignment(
        alignment_dir,
        review_rows=[
            _review_row("FAM_SUPPORT", detected=4, rescued=80, accepted=84),
            _review_row("FAM_QUEUE", detected=4, rescued=80, accepted=84),
            _review_row("FAM_QUEUE_LOW", detected=4, rescued=60, accepted=70),
            _review_row("FAM_HIGH", detected=20, rescued=60, accepted=80),
        ],
        cell_rows=[
            *_cells(
                "FAM_SUPPORT",
                detected_heights=(200, 180),
                rescued_heights=(100, 90),
            ),
            *_cells(
                "FAM_QUEUE",
                detected_heights=(150, 140),
                rescued_heights=(80, 70),
            ),
            *_cells(
                "FAM_QUEUE_LOW",
                detected_heights=(100, 95),
                rescued_heights=(100, 100),
            ),
            *_cells("FAM_HIGH", detected_heights=(100,), rescued_heights=(90,)),
        ],
    )
    _write_overlay_json(
        overlay_dir / "support_trace_data.json",
        "FAM_SUPPORT",
        {
            "family_verdict": "ms1_shape_supports_family_backfill",
            "dda_trigger_limited_ms2_support": True,
            "detected_backfilled_count": 84,
            "global_apex_assessable_trace_count": 82,
            "global_apex_assessable_fraction": 0.976,
            "selected_apex_in_trace_window_count": 81,
            "selected_apex_in_trace_window_fraction": 0.964,
            "local_apex_assessable_trace_count": 80,
            "global_apex_interference_count": 1,
            "shape_supported_fraction": 0.9,
            "global_apex_interference_fraction": 0.1,
            "local_apex_supported_count": 79,
            "local_apex_supported_fraction": 0.8,
        },
    )

    code = report.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-dir",
            str(output_dir),
            "--overlay-trace-data-dir",
            str(overlay_dir),
        ]
    )

    assert code == 0
    candidates = _read_tsv(output_dir / "family_ms1_backfill_review_candidates.tsv")
    by_family = {row["feature_family_id"]: row for row in candidates}
    assert set(by_family) == {"FAM_SUPPORT", "FAM_QUEUE", "FAM_QUEUE_LOW"}
    assert (
        by_family["FAM_SUPPORT"]["review_classification"]
        == "ms1_supported_dda_limited_backfill"
    )
    assert by_family["FAM_SUPPORT"]["global_apex_assessable_trace_count"] == "82"
    assert by_family["FAM_SUPPORT"]["selected_apex_in_trace_window_fraction"] == "0.964"
    assert by_family["FAM_SUPPORT"]["global_apex_interference_count"] == "1"
    assert by_family["FAM_QUEUE"]["review_classification"] == (
        "needs_ms1_overlay_high_priority"
    )
    assert by_family["FAM_QUEUE_LOW"]["review_classification"] == (
        "needs_ms1_overlay"
    )
    queue = _read_tsv(output_dir / "family_ms1_backfill_review_queue.tsv")
    assert [row["feature_family_id"] for row in queue] == [
        "FAM_QUEUE",
        "FAM_QUEUE_LOW",
    ]
    assert queue[0]["suggested_rt_min"] == "46.2966"
    assert queue[0]["suggested_rt_max"] == "48.4966"
    assert queue[0]["suggested_output_prefix"] == "fam_queue_ms1_overlay_review"
    assert "--family-id FAM_QUEUE" in queue[0]["suggested_overlay_command_args"]
    assert "--mz 251.165" in queue[0]["suggested_overlay_command_args"]
    assert (output_dir / "family_ms1_backfill_review.json").is_file()
    assert (output_dir / "family_ms1_backfill_review.md").is_file()
    markdown = (output_dir / "family_ms1_backfill_review.md").read_text(
        encoding="utf-8",
    )
    assert "## Review Verdict" in markdown
    assert "## Top Image Queue" in markdown
    assert "`FAM_QUEUE`" in markdown


def test_neighboring_interference_overlay_requires_manual_review(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    overlay_file = tmp_path / "fam_trace_data.json"
    _write_alignment(
        alignment_dir,
        review_rows=[_review_row("FAM_INTERFERE", detected=4, rescued=81, accepted=85)],
        cell_rows=[
            *_cells(
                "FAM_INTERFERE",
                detected_heights=(120, 110),
                rescued_heights=(90, 80),
            ),
        ],
    )
    _write_overlay_json(
        overlay_file,
        "FAM_INTERFERE",
        {
            "family_verdict": "review_required_neighboring_ms1_interference",
            "dda_trigger_limited_ms2_support": True,
            "shape_supported_fraction": 0.67,
            "global_apex_interference_fraction": 0.91,
        },
    )

    result = report.build_review_report(
        alignment_dir=alignment_dir,
        overlay_trace_data_files=(overlay_file,),
    )

    family = result["candidates"][0]
    assert family["review_classification"] == "neighboring_interference_review"
    assert family["recommended_next_action"] == "manual_review_before_gate_change"
    assert family["dda_trigger_limited_ms2_support"] is True


def test_low_assessable_coverage_overlay_requires_manual_review(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    overlay_file = tmp_path / "fam_trace_data.json"
    _write_alignment(
        alignment_dir,
        review_rows=[
            _review_row("FAM_LOW_COVERAGE", detected=5, rescued=80, accepted=85),
        ],
        cell_rows=[
            *_cells(
                "FAM_LOW_COVERAGE",
                detected_heights=(120, 110),
                rescued_heights=(90, 80),
            ),
        ],
    )
    _write_overlay_json(
        overlay_file,
        "FAM_LOW_COVERAGE",
        {
            "family_verdict": "review_required_low_ms1_assessable_coverage",
            "dda_trigger_limited_ms2_support": True,
            "global_apex_assessable_fraction": 0.6,
            "selected_apex_in_trace_window_fraction": 0.56,
        },
    )

    result = report.build_review_report(
        alignment_dir=alignment_dir,
        overlay_trace_data_files=(overlay_file,),
    )

    family = result["candidates"][0]
    assert family["review_classification"] == "low_ms1_assessable_coverage_review"
    assert family["recommended_next_action"] == "manual_review_before_gate_change"
    assert family["global_apex_assessable_fraction"] == 0.6


def test_missing_required_columns_fail_clearly(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    (alignment_dir / "alignment_review.tsv").write_text(
        "feature_family_id\nFAM001\n",
        encoding="utf-8",
    )
    (alignment_dir / "alignment_cells.tsv").write_text(
        "feature_family_id\tsample_stem\tstatus\tarea\theight\n",
        encoding="utf-8",
    )

    code = report.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert code == 2


def _write_alignment(
    alignment_dir: Path,
    *,
    review_rows: list[dict[str, str]],
    cell_rows: list[dict[str, str]],
) -> None:
    alignment_dir.mkdir()
    _write_tsv(alignment_dir / "alignment_review.tsv", review_rows)
    _write_tsv(alignment_dir / "alignment_cells.tsv", cell_rows)


def _review_row(
    family_id: str,
    *,
    detected: int,
    rescued: int,
    accepted: int,
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": "251.165",
        "family_center_rt": "47.3966",
        "detected_count": str(detected),
        "accepted_backfill_count": str(rescued),
        "accepted_cell_count": str(accepted),
        "include_in_primary_matrix": "TRUE",
        "row_flags": "rescue_heavy",
        "primary_evidence": "owner_complete_link",
        "reason": "test",
    }


def _cells(
    family_id: str,
    *,
    detected_heights: tuple[int, ...],
    rescued_heights: tuple[int, ...],
) -> list[dict[str, str]]:
    rows = []
    for index, height in enumerate(detected_heights):
        rows.append(
            {
                "feature_family_id": family_id,
                "sample_stem": f"D{index}",
                "status": "detected",
                "area": str(height * 10),
                "height": str(height),
            }
        )
    for index, height in enumerate(rescued_heights):
        rows.append(
            {
                "feature_family_id": family_id,
                "sample_stem": f"R{index}",
                "status": "backfilled",
                "area": str(height * 10),
                "height": str(height),
            }
        )
    return rows


def _write_overlay_json(
    path: Path,
    family_id: str,
    evidence_summary: dict[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"family_id": family_id, "evidence_summary": evidence_summary}),
        encoding="utf-8",
    )


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = sorted({field for row in rows for field in row})
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))
