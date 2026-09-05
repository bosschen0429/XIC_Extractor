import csv
from pathlib import Path

from tools.diagnostics import alignment_parity
from tools.diagnostics.alignment_parity import run_alignment_parity


def test_alignment_parity_accepts_same_primary_rows_and_no_new_active_failures(
    tmp_path: Path,
) -> None:
    baseline_matrix = tmp_path / "baseline_matrix.tsv"
    optimized_matrix = tmp_path / "optimized_matrix.tsv"
    baseline_review = tmp_path / "baseline_review.tsv"
    optimized_review = tmp_path / "optimized_review.tsv"
    baseline_targeted = tmp_path / "baseline_targeted.tsv"
    optimized_targeted = tmp_path / "optimized_targeted.tsv"
    _write(
        baseline_matrix,
        "feature_family_id\tneutral_loss_tag\tS1\nFAM001\tDNA_dR\t10\n",
    )
    _write(
        optimized_matrix,
        "feature_family_id\tneutral_loss_tag\tS1\nFAM001\tDNA_dR\t10.0000000001\n",
    )
    review_text = (
        "feature_family_id\tinclude_in_primary_matrix\tidentity_decision\t"
        "identity_confidence\tprimary_evidence\tidentity_reason\n"
        "FAM001\tTRUE\tproduction_family\thigh\towner_complete_link\t"
        "owner_complete_link\n"
    )
    _write(baseline_review, review_text)
    _write(optimized_review, review_text)
    _write(
        baseline_targeted,
        "target_label\tactive_tag\tstatus\tprimary_match_count\tselected_feature_id\n"
        "dR\tTRUE\tPASS\t1\tFAM001\n",
    )
    _write(
        optimized_targeted,
        "target_label\tactive_tag\tstatus\tprimary_match_count\tselected_feature_id\n"
        "dR\tTRUE\tPASS\t1\tFAM001\n",
    )

    result = run_alignment_parity(
        baseline_matrix_tsv=baseline_matrix,
        optimized_matrix_tsv=optimized_matrix,
        baseline_review_tsv=baseline_review,
        optimized_review_tsv=optimized_review,
        baseline_targeted_summary_tsv=baseline_targeted,
        optimized_targeted_summary_tsv=optimized_targeted,
        output_json=tmp_path / "parity.json",
        output_md=tmp_path / "parity.md",
        numeric_tolerance=1e-6,
    )

    assert result.status == "pass"
    assert (tmp_path / "parity.json").exists()
    assert (tmp_path / "parity.md").exists()


def test_alignment_parity_fails_on_identity_or_new_active_failure(
    tmp_path: Path,
) -> None:
    baseline_matrix = tmp_path / "baseline_matrix.tsv"
    optimized_matrix = tmp_path / "optimized_matrix.tsv"
    baseline_review = tmp_path / "baseline_review.tsv"
    optimized_review = tmp_path / "optimized_review.tsv"
    baseline_targeted = tmp_path / "baseline_targeted.tsv"
    optimized_targeted = tmp_path / "optimized_targeted.tsv"
    _write(
        baseline_matrix,
        "feature_family_id\tneutral_loss_tag\tS1\nFAM001\tDNA_dR\t10\n",
    )
    _write(
        optimized_matrix,
        "feature_family_id\tneutral_loss_tag\tS1\nFAM001\tDNA_dR\t10\n",
    )
    _write(
        baseline_review,
        "feature_family_id\tinclude_in_primary_matrix\tidentity_decision\t"
        "identity_confidence\tprimary_evidence\tidentity_reason\n"
        "FAM001\tTRUE\tproduction_family\thigh\towner_complete_link\t"
        "owner_complete_link\n",
    )
    _write(
        optimized_review,
        "feature_family_id\tinclude_in_primary_matrix\tidentity_decision\t"
        "identity_confidence\tprimary_evidence\tidentity_reason\n"
        "FAM001\tFALSE\tprovisional_discovery\treview\tsingle_sample_local_owner\t"
        "insufficient_detected_identity_support\n",
    )
    _write(
        baseline_targeted,
        "target_label\tactive_tag\tstatus\tprimary_match_count\tselected_feature_id\n"
        "dR\tTRUE\tPASS\t1\tFAM001\n",
    )
    _write(
        optimized_targeted,
        "target_label\tactive_tag\tstatus\tprimary_match_count\tselected_feature_id\n"
        "dR\tTRUE\tFAIL\t0\t\n",
    )

    result = run_alignment_parity(
        baseline_matrix_tsv=baseline_matrix,
        optimized_matrix_tsv=optimized_matrix,
        baseline_review_tsv=baseline_review,
        optimized_review_tsv=optimized_review,
        baseline_targeted_summary_tsv=baseline_targeted,
        optimized_targeted_summary_tsv=optimized_targeted,
    )

    assert result.status == "fail"
    assert any("identity FAM001" in item for item in result.differences)
    assert any("new active FAIL" in item for item in result.differences)


def test_alignment_parity_fails_on_missing_active_targeted_row(
    tmp_path: Path,
) -> None:
    baseline_matrix = tmp_path / "baseline_matrix.tsv"
    optimized_matrix = tmp_path / "optimized_matrix.tsv"
    baseline_targeted = tmp_path / "baseline_targeted.tsv"
    optimized_targeted = tmp_path / "optimized_targeted.tsv"
    _write(
        baseline_matrix,
        "feature_family_id\tneutral_loss_tag\tS1\nFAM001\tDNA_dR\t10\n",
    )
    _write(
        optimized_matrix,
        "feature_family_id\tneutral_loss_tag\tS1\nFAM001\tDNA_dR\t10\n",
    )
    _write(
        baseline_targeted,
        "target_label\tactive_tag\tstatus\tprimary_match_count\tselected_feature_id\n"
        "dR\tTRUE\tPASS\t1\tFAM001\n",
    )
    _write(
        optimized_targeted,
        "target_label\tactive_tag\tstatus\tprimary_match_count\tselected_feature_id\n",
    )

    result = run_alignment_parity(
        baseline_matrix_tsv=baseline_matrix,
        optimized_matrix_tsv=optimized_matrix,
        baseline_targeted_summary_tsv=baseline_targeted,
        optimized_targeted_summary_tsv=optimized_targeted,
    )

    assert result.status == "fail"
    assert any("target labels differ" in item for item in result.differences)


def test_alignment_parity_fails_on_identity_confidence_or_evidence_drift(
    tmp_path: Path,
) -> None:
    baseline_matrix = tmp_path / "baseline_matrix.tsv"
    optimized_matrix = tmp_path / "optimized_matrix.tsv"
    baseline_review = tmp_path / "baseline_review.tsv"
    optimized_review = tmp_path / "optimized_review.tsv"
    targeted = tmp_path / "targeted.tsv"
    _write(
        baseline_matrix,
        "feature_family_id\tneutral_loss_tag\tS1\nFAM001\tDNA_dR\t10\n",
    )
    _write(
        optimized_matrix,
        "feature_family_id\tneutral_loss_tag\tS1\nFAM001\tDNA_dR\t10\n",
    )
    _write(
        baseline_review,
        "feature_family_id\tinclude_in_primary_matrix\tidentity_decision\t"
        "identity_confidence\tprimary_evidence\tidentity_reason\n"
        "FAM001\tTRUE\tproduction_family\thigh\towner_complete_link\t"
        "owner_complete_link\n",
    )
    _write(
        optimized_review,
        "feature_family_id\tinclude_in_primary_matrix\tidentity_decision\t"
        "identity_confidence\tprimary_evidence\tidentity_reason\n"
        "FAM001\tTRUE\tproduction_family\tmedium\tmulti_sample_detected\t"
        "owner_complete_link\n",
    )
    _write(
        targeted,
        "target_label\tactive_tag\tstatus\tprimary_match_count\tselected_feature_id\n"
        "dR\tTRUE\tPASS\t1\tFAM001\n",
    )

    result = run_alignment_parity(
        baseline_matrix_tsv=baseline_matrix,
        optimized_matrix_tsv=optimized_matrix,
        baseline_review_tsv=baseline_review,
        optimized_review_tsv=optimized_review,
        baseline_targeted_summary_tsv=targeted,
        optimized_targeted_summary_tsv=targeted,
    )

    assert result.status == "fail"
    assert any("identity_confidence" in item for item in result.differences)
    assert any("primary_evidence" in item for item in result.differences)


def test_alignment_parity_ignores_non_primary_identity_diagnostic_drift(
    tmp_path: Path,
) -> None:
    baseline_matrix = tmp_path / "baseline_matrix.tsv"
    optimized_matrix = tmp_path / "optimized_matrix.tsv"
    baseline_review = tmp_path / "baseline_review.tsv"
    optimized_review = tmp_path / "optimized_review.tsv"
    targeted = tmp_path / "targeted.tsv"
    _write(
        baseline_matrix,
        "feature_family_id\tneutral_loss_tag\tS1\nFAM_PRIMARY\tDNA_dR\t10\n",
    )
    _write(
        optimized_matrix,
        "feature_family_id\tneutral_loss_tag\tS1\nFAM_PRIMARY\tDNA_dR\t10\n",
    )
    _write(
        baseline_review,
        "feature_family_id\tinclude_in_primary_matrix\tidentity_decision\t"
        "identity_confidence\tprimary_evidence\tidentity_reason\n"
        "FAM_PRIMARY\tTRUE\tproduction_family\thigh\towner_complete_link\t"
        "owner_complete_link\n"
        "FAM_AUDIT\tFALSE\taudit_family\treview\tsingle_sample_local_owner\t"
        "duplicate_claim_pressure\n",
    )
    _write(
        optimized_review,
        "feature_family_id\tinclude_in_primary_matrix\tidentity_decision\t"
        "identity_confidence\tprimary_evidence\tidentity_reason\n"
        "FAM_PRIMARY\tTRUE\tproduction_family\thigh\towner_complete_link\t"
        "owner_complete_link\n"
        "FAM_AUDIT\tFALSE\tprovisional_discovery\treview\tsingle_sample_local_owner\t"
        "single_sample_local_owner\n",
    )
    _write(
        targeted,
        "target_label\tactive_tag\tstatus\tprimary_match_count\tselected_feature_id\n"
        "dR\tTRUE\tPASS\t1\tFAM_PRIMARY\n",
    )

    result = run_alignment_parity(
        baseline_matrix_tsv=baseline_matrix,
        optimized_matrix_tsv=optimized_matrix,
        baseline_review_tsv=baseline_review,
        optimized_review_tsv=optimized_review,
        baseline_targeted_summary_tsv=targeted,
        optimized_targeted_summary_tsv=targeted,
    )

    assert result.status == "pass"


def test_alignment_parity_cli_writes_documented_artifacts(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    optimized_dir = tmp_path / "optimized"
    output_dir = tmp_path / "compare"
    baseline_dir.mkdir()
    optimized_dir.mkdir()
    _write(
        baseline_dir / "alignment_matrix.tsv",
        "feature_family_id\tneutral_loss_tag\tS1\nFAM001\tDNA_dR\t10\n",
    )
    _write(
        optimized_dir / "alignment_matrix.tsv",
        "feature_family_id\tneutral_loss_tag\tS1\nFAM001\tDNA_dR\t10\n",
    )
    review_text = (
        "feature_family_id\tinclude_in_primary_matrix\tidentity_decision\t"
        "identity_confidence\tprimary_evidence\tidentity_reason\n"
        "FAM001\tTRUE\tproduction_family\thigh\towner_complete_link\t"
        "owner_complete_link\n"
    )
    _write(baseline_dir / "alignment_review.tsv", review_text)
    _write(optimized_dir / "alignment_review.tsv", review_text)
    baseline_targeted = tmp_path / "baseline_targeted.tsv"
    optimized_targeted = tmp_path / "optimized_targeted.tsv"
    targeted_text = (
        "target_label\tactive_tag\tstatus\tprimary_match_count\tselected_feature_id\n"
        "dR\tTRUE\tPASS\t1\tFAM001\n"
    )
    _write(baseline_targeted, targeted_text)
    _write(optimized_targeted, targeted_text)

    code = alignment_parity.main(
        [
            "--baseline-alignment-dir",
            str(baseline_dir),
            "--optimized-alignment-dir",
            str(optimized_dir),
            "--baseline-benchmark-summary-tsv",
            str(baseline_targeted),
            "--optimized-benchmark-summary-tsv",
            str(optimized_targeted),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    matrix_parity = output_dir / "matrix_parity.tsv"
    identity_parity = output_dir / "identity_parity.tsv"
    targeted_delta = output_dir / "targeted_benchmark_delta.tsv"
    assert matrix_parity.exists()
    assert identity_parity.exists()
    assert targeted_delta.exists()
    assert (output_dir / "alignment_parity.json").exists()
    assert (output_dir / "alignment_parity.md").exists()
    for artifact_path in (matrix_parity, identity_parity, targeted_delta):
        assert b"\r\n" not in artifact_path.read_bytes()
        assert artifact_path.read_text(encoding="utf-8").splitlines()[0].split(
            "\t"
        ) == ["check", "status", "difference"]
    assert _read_tsv(matrix_parity) == [
        {"check": "primary_matrix_tsv", "status": "PASS", "difference": ""}
    ]
    assert _read_tsv(identity_parity) == [
        {
            "check": "primary_identity_decisions",
            "status": "PASS",
            "difference": "",
        }
    ]
    assert _read_tsv(targeted_delta) == [
        {
            "check": "targeted_istd_benchmark",
            "status": "PASS",
            "difference": "",
        }
    ]


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
