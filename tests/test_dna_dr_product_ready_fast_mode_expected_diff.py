from __future__ import annotations

from pathlib import Path

from tools.diagnostics import dna_dr_product_ready_fast_mode_expected_diff as diff


def test_expected_diff_marks_public_output_delta_diagnostic_only(
    tmp_path: Path,
) -> None:
    exact = tmp_path / "exact"
    candidate = tmp_path / "candidate"
    _write_alignment_output(exact)
    _write_alignment_output(
        candidate,
        matrix_area="15",
        cell_status="backfilled",
        cell_area="150",
        review_identity="fast_review",
    )

    packet = diff.build_expected_diff_packet(
        exact_dir=exact,
        candidates={"ms1_index": candidate},
    )

    result = packet["candidates"]["ms1_index"]
    assert result["result_status"] == "diagnostic_only"
    assert "public_output_hash_diff" in result["decision_reasons"]
    assert "cell_status_or_authority_delta" in result["decision_reasons"]
    assert result["matrix_diff"]["common_numeric_cell_count"] == 1
    assert result["cell_evidence_diff"]["status_delta_counts"]["status"] == 1


def test_expected_diff_allows_candidate_only_with_hash_parity_and_speedup(
    tmp_path: Path,
) -> None:
    exact = tmp_path / "exact"
    candidate = tmp_path / "candidate"
    _write_alignment_output(exact, owner_backfill_sec=10.0)
    _write_alignment_output(candidate, owner_backfill_sec=4.0)

    packet = diff.build_expected_diff_packet(
        exact_dir=exact,
        candidates={"hybrid": candidate},
    )

    result = packet["candidates"]["hybrid"]
    assert result["result_status"] == "fast_mode_candidate"
    assert result["public_hashes"]["all_match"] is True
    assert (
        result["timing_diff"]["stage_delta"]["alignment.owner_backfill"]["delta_sec"]
        == -6.0
    )


def test_expected_diff_requires_target_stage_timing_for_candidate(
    tmp_path: Path,
) -> None:
    exact = tmp_path / "exact"
    candidate = tmp_path / "candidate"
    _write_alignment_output(exact, owner_backfill_sec=10.0)
    _write_alignment_output(candidate, owner_backfill_sec=4.0)
    (candidate / "timing.json").unlink()

    packet = diff.build_expected_diff_packet(
        exact_dir=exact,
        candidates={"hybrid": candidate},
    )

    result = packet["candidates"]["hybrid"]
    assert result["result_status"] == "diagnostic_only"
    assert "target_stage_timing_missing" in result["decision_reasons"]


def _write_alignment_output(
    directory: Path,
    *,
    matrix_area: str = "10",
    cell_status: str = "detected",
    cell_area: str = "100",
    review_identity: str = "exact_review",
    owner_backfill_sec: float = 10.0,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "alignment_matrix.tsv").write_text(
        "Mz\tRT\tSampleA\n"
        f"100.0\t5.0\t{matrix_area}\n",
        encoding="utf-8",
    )
    (directory / "alignment_matrix_identity.tsv").write_text(
        "identity_schema_version\tmatrix_row_index\tMz\tRT\tpeak_hypothesis_id\t"
        "row_identity_basis\tsplit_evaluation_status\tprojection_status\t"
        "source_feature_family_ids\tsource_feature_family_count\tcenter_mz_basis\t"
        "center_rt_basis\tcenter_weight_basis\taccepted_cell_count\t"
        "accepted_sample_count\tevidence_status\tparent_peak_hypothesis_id\t"
        "child_peak_hypothesis_ids\n"
        "v1\t1\t100.0\t5.0\tP1\tbasis\tcomplete\tnot_projection\tF1\t1\t"
        "mz\trt\tweight\t1\t1\tcomplete\t\t\n",
        encoding="utf-8",
    )
    (directory / "alignment_review.tsv").write_text(
        "feature_family_id\tidentity_decision\tidentity_confidence\t"
        "primary_evidence\tidentity_reason\tinclude_in_primary_matrix\trow_flags\t"
        "reason\tdetected_count\tabsent_count\tunchecked_count\t"
        "duplicate_assigned_count\tambiguous_ms1_owner_count\t"
        "quantifiable_detected_count\tquantifiable_backfill_count\t"
        "accepted_cell_count\taccepted_backfill_count\treview_backfill_count\n"
        f"F1\t{review_identity}\treview\tanchor\treason\tTRUE\tflags\treason\t"
        "1\t0\t0\t0\t0\t1\t0\t1\t0\t0\n",
        encoding="utf-8",
    )
    (directory / "alignment_backfill_cell_evidence.tsv").write_text(
        "schema_version\tfeature_family_id\tsample_stem\tstatus\t"
        "production_cell_status\tbackfill_tier\twrite_matrix_value\t"
        "include_in_primary_matrix\tidentity_decision\tarea\tprimary_matrix_area\t"
        "apex_rt\theight\tpeak_start_rt\tpeak_end_rt\n"
        f"v1\tF1\tSampleA\t{cell_status}\t{cell_status}\ttier\tTRUE\tTRUE\t"
        f"id\t{cell_area}\t{cell_area}\t5.0\t20\t4.9\t5.1\n",
        encoding="utf-8",
    )
    (directory / "timing.json").write_text(
        "{"
        '"records": ['
        '{"stage": "alignment.owner_backfill", '
        f'"elapsed_sec": {owner_backfill_sec}}}'
        "]"
        "}",
        encoding="utf-8",
    )
