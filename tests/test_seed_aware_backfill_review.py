import csv
import json
import os
import subprocess
import sys
from pathlib import Path

from tools.diagnostics import seed_aware_backfill_review as review


def test_path_style_cli_help_preserves_public_script_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "tools" / "diagnostics" / "seed_aware_backfill_review.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--review-candidates-tsv" in result.stdout
    assert "--overlay-batch-summary-tsv" in result.stdout


def test_seed_shape_supported_candidate_requires_seed_and_clean_overlay(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    _write_review(paths["review"], [_review_row("FAM_SUPPORT")])
    _write_overlay(
        paths["overlay"],
        [
            _overlay_row(
                "FAM_SUPPORT",
                verdict=review.SUPPORT_VERDICT,
                interference="0.10",
                png_path="C:/plots/fam_support.png",
            ),
        ],
    )
    _write_low_rows(paths["low"], [_low_row("FAM_SUPPORT", "S1")])
    _write_seed_audit(paths["seed"], [_seed_row("FAM_SUPPORT", "S1")])

    result = review.build_seed_aware_review(
        review_candidates_tsv=paths["review"],
        overlay_batch_summary_tsv=paths["overlay"],
        low_ms1_rows_tsv=paths["low"],
        backfill_seed_audit_tsv=paths["seed"],
    )

    family = result["families"][0]
    assert family["review_classification"] == (
        "seed_shape_supported_review_candidate"
    )
    assert family["recommended_next_action"] == "keep_as_shadow_gate_candidate"
    assert family["would_withhold_rescued_cells"] == 0
    assert family["png_paths"] == "C:/plots/fam_support.png"


def test_neighboring_interference_wins_over_shape_support(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    _write_review(paths["review"], [_review_row("FAM_NEIGHBOR")])
    _write_overlay(
        paths["overlay"],
        [
            _overlay_row(
                "FAM_NEIGHBOR",
                verdict=review.SUPPORT_VERDICT,
                interference="0.31",
            ),
        ],
    )
    _write_low_rows(paths["low"], [_low_row("FAM_NEIGHBOR", "S1")])
    _write_seed_audit(paths["seed"], [_seed_row("FAM_NEIGHBOR", "S1")])

    result = review.build_seed_aware_review(
        review_candidates_tsv=paths["review"],
        overlay_batch_summary_tsv=paths["overlay"],
        low_ms1_rows_tsv=paths["low"],
        backfill_seed_audit_tsv=paths["seed"],
    )

    family = result["families"][0]
    assert family["review_classification"] == "neighbor_interference_review"
    assert family["would_withhold_rescued_cells"] == 80
    assert "interference" in family["review_reason"]


def test_missing_seed_context_is_reported_before_shape_result(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    _write_review(paths["review"], [_review_row("FAM_NOSEED")])
    _write_overlay(
        paths["overlay"],
        [_overlay_row("FAM_NOSEED", verdict=review.SUPPORT_VERDICT)],
    )
    _write_low_rows(paths["low"], [_low_row("FAM_NOSEED", "S1")])
    _write_seed_audit(paths["seed"], [])

    result = review.build_seed_aware_review(
        review_candidates_tsv=paths["review"],
        overlay_batch_summary_tsv=paths["overlay"],
        low_ms1_rows_tsv=paths["low"],
        backfill_seed_audit_tsv=paths["seed"],
    )

    family = result["families"][0]
    assert family["review_classification"] == "seed_context_missing"
    assert family["recommended_next_action"] == "rerun_alignment_with_seed_audit"


def test_shape_insufficient_when_seed_overlay_does_not_support_shape(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    _write_review(paths["review"], [_review_row("FAM_SHAPE")])
    _write_overlay(
        paths["overlay"],
        [
            _overlay_row(
                "FAM_SHAPE",
                verdict="review_required_uncertain_ms1_shape",
                interference="0.05",
            ),
        ],
    )
    _write_low_rows(paths["low"], [_low_row("FAM_SHAPE", "S1")])
    _write_seed_audit(paths["seed"], [_seed_row("FAM_SHAPE", "S1")])

    result = review.build_seed_aware_review(
        review_candidates_tsv=paths["review"],
        overlay_batch_summary_tsv=paths["overlay"],
        low_ms1_rows_tsv=paths["low"],
        backfill_seed_audit_tsv=paths["seed"],
    )

    family = result["families"][0]
    assert family["review_classification"] == "shape_insufficient_review"
    assert family["would_withhold_rescued_cells"] == 80


def test_not_assessable_when_overlay_has_not_been_generated(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    _write_review(paths["review"], [_review_row("FAM_MISSING_OVERLAY")])
    _write_overlay(paths["overlay"], [])
    _write_low_rows(paths["low"], [_low_row("FAM_MISSING_OVERLAY", "S1")])
    _write_seed_audit(paths["seed"], [_seed_row("FAM_MISSING_OVERLAY", "S1")])

    result = review.build_seed_aware_review(
        review_candidates_tsv=paths["review"],
        overlay_batch_summary_tsv=paths["overlay"],
        low_ms1_rows_tsv=paths["low"],
        backfill_seed_audit_tsv=paths["seed"],
    )

    assert result["families"][0]["review_classification"] == "not_assessable"


def test_not_rescued_heavy_rows_do_not_create_blast_radius(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    _write_review(paths["review"], [_review_row("FAM_SMALL", rescued="5")])
    _write_overlay(
        paths["overlay"],
        [_overlay_row("FAM_SMALL", verdict=review.NEIGHBOR_VERDICT)],
    )
    _write_low_rows(paths["low"], [_low_row("FAM_SMALL", "S1")])
    _write_seed_audit(paths["seed"], [_seed_row("FAM_SMALL", "S1")])

    result = review.build_seed_aware_review(
        review_candidates_tsv=paths["review"],
        overlay_batch_summary_tsv=paths["overlay"],
        low_ms1_rows_tsv=paths["low"],
        backfill_seed_audit_tsv=paths["seed"],
    )

    family = result["families"][0]
    assert family["review_classification"] == "not_rescued_heavy"
    assert family["would_withhold_rescued_cells"] == 0


def test_protected_family_forces_manual_review_in_blast_radius(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    _write_review(paths["review"], [_review_row("FAM_ISTD")])
    _write_overlay(
        paths["overlay"],
        [_overlay_row("FAM_ISTD", verdict=review.NEIGHBOR_VERDICT)],
    )
    _write_low_rows(paths["low"], [_low_row("FAM_ISTD", "S1")])
    _write_seed_audit(paths["seed"], [_seed_row("FAM_ISTD", "S1")])

    result = review.build_seed_aware_review(
        review_candidates_tsv=paths["review"],
        overlay_batch_summary_tsv=paths["overlay"],
        low_ms1_rows_tsv=paths["low"],
        backfill_seed_audit_tsv=paths["seed"],
        protected_family_ids=("FAM_ISTD",),
    )

    family = result["families"][0]
    blast = result["blast_radius"][0]
    assert family["recommended_next_action"] == "manual_review_required"
    assert blast["blast_radius_action"] == "manual_review_required"


def test_outputs_all_review_and_blast_files(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    output_dir = tmp_path / "out"
    _write_review(paths["review"], [_review_row("FAM_SUPPORT")])
    _write_overlay(
        paths["overlay"],
        [_overlay_row("FAM_SUPPORT", verdict=review.SUPPORT_VERDICT)],
    )
    _write_low_rows(paths["low"], [_low_row("FAM_SUPPORT", "S1")])
    _write_seed_audit(paths["seed"], [_seed_row("FAM_SUPPORT", "S1")])

    code = review.main(
        [
            "--review-candidates-tsv",
            str(paths["review"]),
            "--overlay-batch-summary-tsv",
            str(paths["overlay"]),
            "--low-ms1-rows-tsv",
            str(paths["low"]),
            "--backfill-seed-audit-tsv",
            str(paths["seed"]),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    assert (output_dir / "seed_aware_backfill_review_summary.tsv").is_file()
    assert (output_dir / "seed_aware_backfill_review_families.tsv").is_file()
    assert (output_dir / "seed_aware_backfill_review.json").is_file()
    assert (output_dir / "seed_aware_backfill_review.md").is_file()
    assert (output_dir / "seed_aware_backfill_blast_radius.tsv").is_file()
    assert (output_dir / "seed_aware_backfill_blast_radius.md").is_file()
    with (output_dir / "seed_aware_backfill_review_families.tsv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        family_reader = csv.DictReader(handle, delimiter="\t")
        assert family_reader.fieldnames == [
            "feature_family_id",
            "neutral_loss_tag",
            "family_center_mz",
            "family_center_rt",
            "detected_count",
            "accepted_backfill_count",
            "accepted_cell_count",
            "input_review_classification",
            "all_overlay_row_count",
            "seed_overlay_row_count",
            "overlay_row_count",
            "overlay_success_count",
            "overlay_support_count",
            "overlay_neighbor_count",
            "overlay_failed_count",
            "max_global_apex_interference_fraction",
            "min_selected_apex_in_trace_window_fraction",
            "min_global_apex_assessable_fraction",
            "min_shape_supported_fraction",
            "seed_audit_row_count",
            "seed_group_count",
            "seed_rt_span",
            "low_ms1_detail_row_count",
            "protected_family",
            "review_classification",
            "recommended_next_action",
            "review_reason",
            "would_withhold_rescued_cells",
            "png_paths",
            "pdf_paths",
            "row_flags",
            "primary_evidence",
            "reason",
        ]
        family_rows = list(family_reader)
    assert family_rows[0]["protected_family"] == "FALSE"
    assert family_rows[0]["max_global_apex_interference_fraction"] == "0.05"
    with (output_dir / "seed_aware_backfill_blast_radius.tsv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        blast_reader = csv.DictReader(handle, delimiter="\t")
        assert blast_reader.fieldnames == [
            "feature_family_id",
            "family_center_mz",
            "family_center_rt",
            "review_classification",
            "detected_count",
            "accepted_backfill_count",
            "accepted_cell_count",
            "would_withhold_family",
            "would_withhold_rescued_cells",
            "protected_family",
            "blast_radius_action",
            "review_reason",
        ]
        blast_rows = list(blast_reader)
    assert blast_rows[0]["would_withhold_family"] == "FALSE"
    assert blast_rows[0]["protected_family"] == "FALSE"
    payload = json.loads(
        (output_dir / "seed_aware_backfill_review.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["families"][0]["feature_family_id"] == "FAM_SUPPORT"


def test_repeated_overlay_summaries_are_merged(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    second_overlay = tmp_path / "overlay2.tsv"
    _write_review(
        paths["review"],
        [
            _review_row("FAM_SUPPORT"),
            _review_row("FAM_NEIGHBOR"),
        ],
    )
    _write_overlay(
        paths["overlay"],
        [_overlay_row("FAM_SUPPORT", verdict=review.SUPPORT_VERDICT)],
    )
    _write_overlay(
        second_overlay,
        [_overlay_row("FAM_NEIGHBOR", verdict=review.NEIGHBOR_VERDICT)],
    )
    _write_low_rows(
        paths["low"],
        [
            _low_row("FAM_SUPPORT", "S1"),
            _low_row("FAM_NEIGHBOR", "S1"),
        ],
    )
    _write_seed_audit(
        paths["seed"],
        [
            _seed_row("FAM_SUPPORT", "S1"),
            _seed_row("FAM_NEIGHBOR", "S1"),
        ],
    )

    result = review.build_seed_aware_review(
        review_candidates_tsv=paths["review"],
        overlay_batch_summary_tsv=(paths["overlay"], second_overlay),
        low_ms1_rows_tsv=paths["low"],
        backfill_seed_audit_tsv=paths["seed"],
    )

    by_family = {row["feature_family_id"]: row for row in result["families"]}
    assert by_family["FAM_SUPPORT"]["review_classification"] == (
        "seed_shape_supported_review_candidate"
    )
    assert by_family["FAM_NEIGHBOR"]["review_classification"] == (
        "neighbor_interference_review"
    )


def test_seed_specific_overlay_overrides_family_center_context(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path)
    _write_review(paths["review"], [_review_row("FAM_SEED_CONTEXT")])
    _write_overlay(
        paths["overlay"],
        [
            _overlay_row(
                "FAM_SEED_CONTEXT",
                verdict="review_required_low_ms1_assessable_coverage",
                output_prefix="fam_seed_context_ms1_overlay_review",
            ),
            _overlay_row(
                "FAM_SEED_CONTEXT",
                verdict=review.SUPPORT_VERDICT,
                output_prefix="fam_seed_context_seed1_overlay",
            ),
            _overlay_row(
                "FAM_SEED_CONTEXT",
                verdict=review.SUPPORT_VERDICT,
                output_prefix="fam_seed_context_seed2_overlay",
            ),
        ],
    )
    _write_low_rows(paths["low"], [_low_row("FAM_SEED_CONTEXT", "S1")])
    _write_seed_audit(
        paths["seed"],
        [
            _seed_row("FAM_SEED_CONTEXT", "S1"),
            _seed_row("FAM_SEED_CONTEXT", "S2"),
        ],
    )

    result = review.build_seed_aware_review(
        review_candidates_tsv=paths["review"],
        overlay_batch_summary_tsv=paths["overlay"],
        low_ms1_rows_tsv=paths["low"],
        backfill_seed_audit_tsv=paths["seed"],
    )

    family = result["families"][0]
    assert family["all_overlay_row_count"] == 3
    assert family["seed_overlay_row_count"] == 2
    assert family["overlay_row_count"] == 2
    assert family["review_classification"] == (
        "seed_shape_supported_review_candidate"
    )


def test_missing_required_columns_fail_clearly(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    paths["review"].write_text("feature_family_id\nFAM001\n", encoding="utf-8")
    _write_overlay(paths["overlay"], [])
    _write_low_rows(paths["low"], [])
    _write_seed_audit(paths["seed"], [])

    code = review.main(
        [
            "--review-candidates-tsv",
            str(paths["review"]),
            "--overlay-batch-summary-tsv",
            str(paths["overlay"]),
            "--low-ms1-rows-tsv",
            str(paths["low"]),
            "--backfill-seed-audit-tsv",
            str(paths["seed"]),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert code == 2


def _fixture_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "review": tmp_path / "review.tsv",
        "overlay": tmp_path / "overlay.tsv",
        "low": tmp_path / "low.tsv",
        "seed": tmp_path / "seed.tsv",
    }


def _review_row(
    family_id: str,
    *,
    rescued: str = "80",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": "300.0",
        "family_center_rt": "14.5",
        "detected_count": "5",
        "accepted_backfill_count": rescued,
        "accepted_cell_count": str(int(rescued) + 5),
        "review_classification": "low_ms1_assessable_coverage_review",
        "row_flags": "rescue_heavy",
        "primary_evidence": "owner_complete_link",
        "reason": "test",
    }


def _overlay_row(
    family_id: str,
    *,
    verdict: str,
    status: str = "success",
    interference: str = "0.05",
    png_path: str = "",
    output_prefix: str = "fam_overlay",
) -> dict[str, str]:
    return {
        "rank": "1",
        "feature_family_id": family_id,
        "mz": "300.0",
        "rt_min": "13.0",
        "rt_max": "16.0",
        "output_prefix": output_prefix,
        "status": status,
        "family_verdict": verdict,
        "global_apex_interference_fraction": interference,
        "selected_apex_in_trace_window_fraction": "0.95",
        "global_apex_assessable_fraction": "0.90",
        "shape_supported_fraction": "0.80",
        "png_path": png_path,
        "pdf_path": "",
    }


def _low_row(family_id: str, sample: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample,
        "status": "backfilled",
    }


def _seed_row(family_id: str, sample: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample,
        "backfill_seed_mz": "300.0",
        "backfill_seed_rt": "14.5",
        "backfill_request_rt_min": "13.0",
        "backfill_request_rt_max": "16.0",
        "backfill_request_ppm": "10.0",
    }


def _write_review(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(
        path,
        rows,
        review.REVIEW_REQUIRED_COLUMNS
        + ("neutral_loss_tag", "row_flags", "primary_evidence", "reason"),
    )


def _write_overlay(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(
        path,
        rows,
        (
            "rank",
            "feature_family_id",
            "mz",
            "rt_min",
            "rt_max",
            "output_prefix",
            *review.OVERLAY_REQUIRED_COLUMNS[1:],
        ),
    )


def _write_low_rows(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(path, rows, review.LOW_COVERAGE_REQUIRED_COLUMNS)


def _write_seed_audit(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(path, rows, review.SEED_AUDIT_REQUIRED_COLUMNS)


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    fields: tuple[str, ...],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
