from __future__ import annotations

import csv
import json
from pathlib import Path

from openpyxl import Workbook

from tools.diagnostics import targeted_istd_benchmark as benchmark
from tools.diagnostics import targeted_istd_benchmark_loaders as loaders
from tools.diagnostics import targeted_istd_benchmark_writers as writers
from tools.diagnostics.targeted_istd_benchmark_models import (
    BenchmarkOutputs,
    BenchmarkSummary,
    BenchmarkThresholds,
    TargetedPoint,
    TargetedReliabilityPoint,
)
from tools.diagnostics.targeted_istd_benchmark_summary import (
    _benchmark_points,
    _reliability_summary,
)


def test_benchmark_classifies_pass_miss_split_and_inactive_tag(tmp_path: Path):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("pass_istd", 100.0, 10.0, 11.0, 50.0, 116.0474),
            _target("miss_istd", 200.0, 20.0, 21.0, 80.0, 116.0474),
            _target("split_istd", 300.0, 30.0, 31.0, 180.0, 116.0474),
            _target("rna_istd", 400.0, 40.0, 41.0, 268.0, 132.0423),
        ],
        samples=("S1", "S2", "S3", "S4"),
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            _review_row("FAM_PASS", 100.0, 10.5, 50.0, 116.0474, True),
            _review_row("FAM_SPLIT_A", 300.0, 30.4, 180.0, 116.0474, True),
            _review_row("FAM_SPLIT_B", 300.0, 30.6, 180.0, 116.0474, True),
            _review_row("FAM_RNA", 400.0, 40.5, 268.0, 132.0423, True),
        ],
        matrix_rows=[
            _matrix_row("FAM_PASS", (100.0, 1000.0, 10000.0, 100000.0)),
            _matrix_row("FAM_SPLIT_A", (1.0, 2.0, 3.0, 4.0)),
            _matrix_row("FAM_SPLIT_B", (1.0, 2.0, 3.0, 4.0)),
            _matrix_row("FAM_RNA", (1.0, 2.0, 3.0, 4.0)),
        ],
        cell_rows=[
            _cell_row("FAM_PASS", "S1", 10.50, 100.0),
            _cell_row("FAM_PASS", "S2", 10.51, 1000.0),
            _cell_row("FAM_PASS", "S3", 10.52, 10000.0),
            _cell_row("FAM_PASS", "S4", 10.53, 100000.0),
        ],
    )

    _outputs, summaries = benchmark.run_targeted_istd_benchmark(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=tmp_path / "benchmark",
    )

    by_label = {row.target_label: row for row in summaries}
    assert by_label["pass_istd"].status == "PASS"
    assert round(by_label["pass_istd"].log_area_pearson or 0.0, 12) == 1.0
    assert round(by_label["pass_istd"].log_area_spearman or 0.0, 12) == 1.0
    assert by_label["miss_istd"].failure_modes == ("MISS",)
    assert by_label["split_istd"].failure_modes == ("SPLIT",)
    assert by_label["rna_istd"].active_tag is False
    assert by_label["rna_istd"].failure_modes == ("FALSE_POSITIVE_TAG",)


def test_benchmark_reads_clean_product_matrix_identity_sidecar(tmp_path: Path):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    _write_targeted_workbook(
        targeted,
        targets=[_target("pass_istd", 100.0, 10.0, 11.0, 50.0, 116.0474)],
        samples=("S1", "S2", "S3", "S4"),
    )
    _write_clean_alignment_run(
        alignment,
        review_rows=[_review_row("FAM_PASS", 100.0, 10.5, 50.0, 116.0474, True)],
        identity_rows=[_identity_row("FAM_PASS", 1, 100.0, 10.5)],
        matrix_rows=[
            {
                "Mz": "100.0000",
                "RT": "10.5000",
                "S1": "100.0",
                "S2": "1000.0",
                "S3": "10000.0",
                "S4": "100000.0",
            },
        ],
        cell_rows=[
            _cell_row("FAM_PASS", "S1", 10.50, 100.0),
            _cell_row("FAM_PASS", "S2", 10.51, 1000.0),
            _cell_row("FAM_PASS", "S3", 10.52, 10000.0),
            _cell_row("FAM_PASS", "S4", 10.53, 100000.0),
        ],
    )

    _outputs, summaries = benchmark.run_targeted_istd_benchmark(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=tmp_path / "benchmark",
    )

    assert summaries[0].status == "PASS"
    assert summaries[0].selected_feature_id == "FAM_PASS"
    assert summaries[0].paired_area_n == 4


def test_review_positive_reliability_never_enters_clean_benchmark_points() -> None:
    points = (
        TargetedPoint("S1", "d3-A", "ISTD", 9.0, 100.0, "OK", "HIGH", ""),
        TargetedPoint(
            "S2",
            "d3-A",
            "ISTD",
            9.1,
            110.0,
            "NL_FAIL",
            "VERY_LOW",
            "",
        ),
    )
    reliability = {
        ("S1", "d3-A"): TargetedReliabilityPoint(
            "S1",
            "d3-A",
            "benchmark_eligible",
        ),
        ("S2", "d3-A"): TargetedReliabilityPoint(
            "S2",
            "d3-A",
            "targeted_review_positive",
            ("plausible_nl_dropout",),
        ),
    }

    reliability_summary = _reliability_summary(
        points,
        reliability=reliability,
        strict_targeted_reliability=False,
    )
    benchmark_points = _benchmark_points(
        points,
        reliability=reliability,
        strict_targeted_reliability=False,
    )

    assert [point.sample_stem for point in reliability_summary.clean_points] == ["S1"]
    assert reliability_summary.review_positive_count == 1
    assert [point.sample_stem for point in benchmark_points] == ["S1"]


def test_benchmark_treats_selected_r_tag_istd_as_active_when_configured(
    tmp_path: Path,
) -> None:
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    _write_targeted_workbook(
        targeted,
        targets=[_target("rna_istd", 400.0, 40.0, 41.0, 268.0, 132.0423)],
        samples=("S1", "S2", "S3", "S4"),
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            _review_row("FAM_RNA", 400.0, 40.5, 268.0, 132.0423, True),
        ],
        matrix_rows=[
            _matrix_row("FAM_RNA", (10.0, 100.0, 1000.0, 10000.0)),
        ],
        cell_rows=[
            _cell_row("FAM_RNA", "S1", 40.51, 10.0),
            _cell_row("FAM_RNA", "S2", 40.52, 100.0),
            _cell_row("FAM_RNA", "S3", 40.53, 1000.0),
            _cell_row("FAM_RNA", "S4", 40.54, 10000.0),
        ],
    )

    _outputs, summaries = benchmark.run_targeted_istd_benchmark(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=tmp_path / "benchmark",
        thresholds=benchmark.BenchmarkThresholds(
            additional_active_neutral_loss_das=(132.0423,),
        ),
    )

    assert summaries[0].active_tag is True
    assert summaries[0].status == "PASS"


def test_main_writes_outputs_and_returns_one_when_gate_fails(tmp_path: Path):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "benchmark"
    _write_targeted_workbook(
        targeted,
        targets=[_target("miss_istd", 200.0, 20.0, 21.0, 80.0, 116.0474)],
        samples=("S1", "S2", "S3"),
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            _review_row("FAM_DECOY", 500.0, 50.0, 384.0, 116.0474, True),
        ],
        matrix_rows=[
            _matrix_row("FAM_DECOY", (1.0, 2.0, 3.0), samples=("S1", "S2", "S3")),
        ],
        cell_rows=[
            _cell_row("FAM_DECOY", "S1", 50.0, 1.0),
        ],
        samples=("S1", "S2", "S3"),
    )

    code = benchmark.main(
        [
            "--targeted-workbook",
            str(targeted),
            "--alignment-dir",
            str(alignment),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 1
    assert (output_dir / "targeted_istd_benchmark_summary.tsv").exists()
    payload = json.loads(
        (output_dir / "targeted_istd_benchmark.json").read_text(encoding="utf-8"),
    )
    assert payload["overall_status"] == "FAIL"
    assert payload["active_fail_count"] == 1


def test_writer_reuses_overall_counts_for_json_and_markdown(tmp_path: Path) -> None:
    outputs = BenchmarkOutputs(
        summary_tsv=tmp_path / "targeted_istd_benchmark_summary.tsv",
        matches_tsv=tmp_path / "targeted_istd_benchmark_matches.tsv",
        json_path=tmp_path / "targeted_istd_benchmark.json",
        markdown_path=tmp_path / "targeted_istd_benchmark.md",
    )

    writers.write_benchmark_outputs(
        outputs,
        summaries=(
            _benchmark_summary(
                "ActiveFail",
                status="FAIL",
                active_tag=True,
                failure_modes=("FALSE_POSITIVE_TAG",),
            ),
            _benchmark_summary(
                "WarningOnly",
                status="PASS",
                active_tag=True,
                warning_modes=("targeted_review_positive",),
            ),
            _benchmark_summary("InactiveWarn", status="WARN", active_tag=False),
        ),
        matches=(),
        thresholds=BenchmarkThresholds(),
    )

    payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
    assert payload["overall_status"] == "FAIL"
    assert payload["fail_count"] == 1
    assert payload["warn_count"] == 2
    assert payload["active_fail_count"] == 1
    assert payload["active_warn_count"] == 1
    assert payload["false_positive_tag_count"] == 1
    assert "Overall status: FAIL" in outputs.markdown_path.read_text(encoding="utf-8")


def test_main_reports_missing_required_workbook_column(
    tmp_path: Path,
    capsys,
):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    _write_targeted_workbook(
        targeted,
        targets=[_target("d3-5-medC", 245.0, 11.0, 13.0, 129.0, 116.0474)],
        samples=("S1", "S2", "S3"),
        omit_target_columns={"NL (Da)"},
    )
    _write_alignment_run(alignment, review_rows=[], matrix_rows=[], cell_rows=[])

    code = benchmark.main(
        [
            "--targeted-workbook",
            str(targeted),
            "--alignment-dir",
            str(alignment),
            "--output-dir",
            str(tmp_path / "benchmark"),
        ],
    )

    assert code == 2
    assert "NL (Da)" in capsys.readouterr().err


def test_provisional_discovery_candidate_is_not_primary_hit(tmp_path: Path):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    _write_targeted_workbook(
        targeted,
        targets=[_target("d3-5-medC", 245.0, 11.0, 13.0, 129.0, 116.0474)],
        samples=("S1", "S2", "S3"),
    )
    row = _review_row("FAM_PROV", 245.0, 12.0, 129.0, 116.0474, True)
    row["identity_decision"] = "provisional_discovery"
    _write_alignment_run(
        alignment,
        review_rows=[row],
        matrix_rows=[
            _matrix_row(
                "FAM_PROV",
                (10.0, 100.0, 1000.0),
                samples=("S1", "S2", "S3"),
            ),
        ],
        cell_rows=[
            _cell_row("FAM_PROV", "S1", 12.00, 10.0),
            _cell_row("FAM_PROV", "S2", 12.01, 100.0),
            _cell_row("FAM_PROV", "S3", 12.02, 1000.0),
        ],
        samples=("S1", "S2", "S3"),
    )

    _outputs, summaries = benchmark.run_targeted_istd_benchmark(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=tmp_path / "benchmark",
    )

    assert summaries[0].primary_match_count == 0
    assert summaries[0].failure_modes == ("MISS",)


def test_sample_normalization_maps_qc_underscore_names(tmp_path: Path):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    _write_targeted_workbook(
        targeted,
        targets=[_target("d3-5-medC", 245.0, 11.0, 13.0, 129.0, 116.0474)],
        samples=("QC_1", "QC_2", "QC_3"),
    )
    _write_alignment_run(
        alignment,
        review_rows=[_review_row("FAM001", 245.0, 12.0, 129.0, 116.0474, True)],
        matrix_rows=[
            _matrix_row(
                "FAM001",
                (10.0, 100.0, 1000.0),
                samples=("QC1", "QC2", "QC3"),
            ),
        ],
        cell_rows=[
            _cell_row("FAM001", "QC1", 12.00, 10.0),
            _cell_row("FAM001", "QC2", 12.01, 100.0),
            _cell_row("FAM001", "QC3", 12.02, 1000.0),
        ],
        samples=("QC1", "QC2", "QC3"),
    )

    _outputs, summaries = benchmark.run_targeted_istd_benchmark(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=tmp_path / "benchmark",
    )

    assert summaries[0].paired_area_n == 3


def test_alignment_matrix_normalizes_sample_columns_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    matrix_path = tmp_path / "alignment_matrix.tsv"
    _write_tsv(
        matrix_path,
        [
            _matrix_row("FAM001", (10.0, 100.0), samples=("QC_1", "S2")),
            _matrix_row("FAM002", (20.0, 200.0), samples=("QC_1", "S2")),
            _matrix_row("FAM003", (30.0, 300.0), samples=("QC_1", "S2")),
        ],
        (
            "feature_family_id",
            "neutral_loss_tag",
            "family_center_mz",
            "family_center_rt",
            "QC_1",
            "S2",
        ),
    )
    normalized: list[str] = []
    original_normalize = loaders._normalize_sample_id

    def counted_normalize(sample_id: str) -> str:
        normalized.append(sample_id)
        return original_normalize(sample_id)

    monkeypatch.setattr(loaders, "_normalize_sample_id", counted_normalize)

    matrix = loaders.read_alignment_matrix(matrix_path)

    assert normalized == ["QC_1", "S2"]
    assert matrix.sample_stems == frozenset({"QC1", "S2"})
    assert matrix.areas_by_family["FAM002"] == {"QC1": 20.0, "S2": 200.0}


def test_active_istd_can_pass_with_primary_isotope_shift_fallback(tmp_path: Path):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    isotope_shift = benchmark.ISOTOPE_SHIFT_DA
    _write_targeted_workbook(
        targeted,
        targets=[_target("d4_istd", 300.0, 23.0, 24.0, 184.0, 116.0474)],
        samples=("S1", "S2", "S3", "S4"),
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            _review_row(
                "FAM_M1",
                300.0 + isotope_shift,
                23.5,
                184.0 + isotope_shift,
                116.0474,
                True,
            ),
            _review_row(
                "FAM_MINUS_DECOY",
                300.0 - isotope_shift + 0.002,
                23.5,
                184.0 - isotope_shift + 0.002,
                116.0474,
                True,
            ),
        ],
        matrix_rows=[
            _matrix_row("FAM_M1", (10.0, 100.0, 1000.0, 10000.0)),
            _matrix_row("FAM_MINUS_DECOY", (1.0, 2.0, 3.0, 4.0)),
        ],
        cell_rows=[
            _cell_row("FAM_M1", "S1", 23.51, 10.0),
            _cell_row("FAM_M1", "S2", 23.52, 100.0),
            _cell_row("FAM_M1", "S3", 23.53, 1000.0),
            _cell_row("FAM_M1", "S4", 23.54, 10000.0),
            _cell_row("FAM_MINUS_DECOY", "S1", 23.51, 1.0),
            _cell_row("FAM_MINUS_DECOY", "S2", 23.52, 2.0),
            _cell_row("FAM_MINUS_DECOY", "S3", 23.53, 3.0),
            _cell_row("FAM_MINUS_DECOY", "S4", 23.54, 4.0),
        ],
    )

    outputs, summaries = benchmark.run_targeted_istd_benchmark(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=tmp_path / "benchmark",
    )

    assert summaries[0].status == "PASS"
    assert summaries[0].selected_feature_id == "FAM_M1"
    matches = _read_tsv(outputs.matches_tsv)
    assert matches[0]["match_type"] == "isotope_shift"
    assert abs(float(matches[0]["mass_shift_da"]) - isotope_shift) < 1e-4


def test_strict_reliability_excludes_targeted_review_rows_from_area_gate(
    tmp_path: Path,
) -> None:
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    reliability_json = tmp_path / "targeted_peak_reliability.json"
    _write_targeted_workbook(
        targeted,
        targets=[_target("d3-N6-medA", 300.0, 10.0, 11.0, 184.0, 116.0474)],
        samples=("S1", "S2", "S3", "S4"),
    )
    _write_alignment_run(
        alignment,
        review_rows=[_review_row("FAM_MEDA", 300.0, 10.5, 184.0, 116.0474, True)],
        matrix_rows=[
            _matrix_row("FAM_MEDA", (10.0, 100.0, 1000.0, 1.0)),
        ],
        cell_rows=[
            _cell_row("FAM_MEDA", "S1", 10.51, 10.0),
            _cell_row("FAM_MEDA", "S2", 10.52, 100.0),
            _cell_row("FAM_MEDA", "S3", 10.53, 1000.0),
            _cell_row("FAM_MEDA", "S4", 10.54, 1.0),
        ],
    )
    _write_reliability_json(
        reliability_json,
        [
            _reliability_row("S1", "d3-N6-medA", "benchmark_eligible"),
            _reliability_row("S2", "d3-N6-medA", "benchmark_eligible"),
            _reliability_row("S3", "d3-N6-medA", "benchmark_eligible"),
            _reliability_row("S4", "d3-N6-medA", "targeted_review"),
        ],
    )

    outputs, summaries = benchmark.run_targeted_istd_benchmark(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=tmp_path / "benchmark",
        targeted_reliability_json=reliability_json,
        strict_targeted_reliability=True,
    )

    summary = summaries[0]
    assert summary.targeted_positive_count == 4
    assert summary.clean_targeted_positive_count == 3
    assert summary.targeted_review_count == 1
    assert summary.coverage_denominator_count == 3
    assert summary.paired_area_n == 3
    assert summary.failure_modes == ()
    assert summary.targeted_reliability_warning_modes == (
        "TARGETED_REVIEW_EVIDENCE",
    )
    payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
    assert payload["overall_status"] == "WARN"
    rows = _read_tsv(outputs.summary_tsv)
    assert rows[0]["clean_targeted_positive_count"] == "3"
    assert rows[0]["targeted_reliability_warning_modes"] == (
        "TARGETED_REVIEW_EVIDENCE"
    )


def test_strict_reliability_tracks_review_positive_rows_separately(
    tmp_path: Path,
) -> None:
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    reliability_json = tmp_path / "targeted_peak_reliability.json"
    samples = ("S1", "S2", "S3", "S4", "S5")
    _write_targeted_workbook(
        targeted,
        targets=[_target("8-oxodG", 300.0, 10.0, 11.0, 184.0, 116.0474)],
        samples=samples,
    )
    _write_alignment_run(
        alignment,
        review_rows=[_review_row("FAM_8OXO", 300.0, 10.5, 184.0, 116.0474, True)],
        matrix_rows=[
            _matrix_row(
                "FAM_8OXO",
                (10.0, 100.0, 1000.0, 1.0, 10000.0),
                samples=samples,
            ),
        ],
        cell_rows=[
            _cell_row("FAM_8OXO", "S1", 10.51, 10.0),
            _cell_row("FAM_8OXO", "S2", 10.52, 100.0),
            _cell_row("FAM_8OXO", "S3", 10.53, 1000.0),
            _cell_row("FAM_8OXO", "S4", 10.54, 1.0),
            _cell_row("FAM_8OXO", "S5", 10.55, 10000.0),
        ],
        samples=samples,
    )
    _write_reliability_json(
        reliability_json,
        [
            _reliability_row("S1", "8-oxodG", "benchmark_eligible"),
            _reliability_row("S2", "8-oxodG", "benchmark_eligible"),
            _reliability_row("S3", "8-oxodG", "benchmark_eligible"),
            _reliability_row(
                "S4",
                "8-oxodG",
                "targeted_review_positive",
                risk_reasons=[
                    "low_confidence",
                    "plausible_nl_dropout",
                    "product_outside_diagnostic_window",
                ],
            ),
            _reliability_row("S5", "8-oxodG", "targeted_review"),
        ],
    )

    outputs, summaries = benchmark.run_targeted_istd_benchmark(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=tmp_path / "benchmark",
        targeted_reliability_json=reliability_json,
        strict_targeted_reliability=True,
    )

    summary = summaries[0]
    assert summary.targeted_positive_count == 5
    assert summary.clean_targeted_positive_count == 3
    assert summary.targeted_review_positive_count == 1
    assert summary.targeted_review_count == 1
    assert summary.coverage_denominator_count == 3
    assert summary.paired_area_n == 3
    assert summary.failure_modes == ()
    assert summary.targeted_reliability_warning_modes == (
        "TARGETED_REVIEW_POSITIVE_EVIDENCE",
        "TARGETED_REVIEW_POSITIVE_REASON:product_outside_diagnostic_window",
        "TARGETED_REVIEW_EVIDENCE",
    )

    payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
    rows = payload["summaries"]
    assert rows[0]["targeted_review_positive_count"] == 1
    tsv_rows = _read_tsv(outputs.summary_tsv)
    assert tsv_rows[0]["targeted_review_positive_count"] == "1"


def test_strict_reliability_reports_inconclusive_when_clean_samples_are_too_few(
    tmp_path: Path,
) -> None:
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    reliability_json = tmp_path / "targeted_peak_reliability.json"
    _write_targeted_workbook(
        targeted,
        targets=[_target("d3-N6-medA", 300.0, 10.0, 11.0, 184.0, 116.0474)],
        samples=("S1", "S2", "S3", "S4"),
    )
    _write_alignment_run(
        alignment,
        review_rows=[_review_row("FAM_MEDA", 300.0, 10.5, 184.0, 116.0474, True)],
        matrix_rows=[
            _matrix_row("FAM_MEDA", (10.0, 100.0, 1000.0, 10000.0)),
        ],
        cell_rows=[
            _cell_row("FAM_MEDA", "S1", 10.51, 10.0),
            _cell_row("FAM_MEDA", "S2", 10.52, 100.0),
            _cell_row("FAM_MEDA", "S3", 10.53, 1000.0),
            _cell_row("FAM_MEDA", "S4", 10.54, 10000.0),
        ],
    )
    _write_reliability_json(
        reliability_json,
        [
            _reliability_row("S1", "d3-N6-medA", "benchmark_eligible"),
            _reliability_row("S2", "d3-N6-medA", "targeted_review"),
            _reliability_row("S3", "d3-N6-medA", "targeted_review"),
            _reliability_row("S4", "d3-N6-medA", "targeted_review"),
        ],
    )

    _outputs, summaries = benchmark.run_targeted_istd_benchmark(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=tmp_path / "benchmark",
        targeted_reliability_json=reliability_json,
        strict_targeted_reliability=True,
    )

    summary = summaries[0]
    assert summary.status == "WARN"
    assert summary.failure_modes == ()
    assert "TARGETED_RELIABILITY_INCONCLUSIVE" in (
        summary.targeted_reliability_warning_modes
    )
    assert summary.paired_area_n == 1


def test_main_accepts_strict_reliability_json(tmp_path: Path) -> None:
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "benchmark"
    reliability_json = tmp_path / "targeted_peak_reliability.json"
    _write_targeted_workbook(
        targeted,
        targets=[_target("d3-N6-medA", 300.0, 10.0, 11.0, 184.0, 116.0474)],
        samples=("S1", "S2", "S3", "S4"),
    )
    _write_alignment_run(
        alignment,
        review_rows=[_review_row("FAM_MEDA", 300.0, 10.5, 184.0, 116.0474, True)],
        matrix_rows=[
            _matrix_row("FAM_MEDA", (10.0, 100.0, 1000.0, 1.0)),
        ],
        cell_rows=[
            _cell_row("FAM_MEDA", "S1", 10.51, 10.0),
            _cell_row("FAM_MEDA", "S2", 10.52, 100.0),
            _cell_row("FAM_MEDA", "S3", 10.53, 1000.0),
            _cell_row("FAM_MEDA", "S4", 10.54, 1.0),
        ],
    )
    _write_reliability_json(
        reliability_json,
        [
            _reliability_row("S1", "d3-N6-medA", "benchmark_eligible"),
            _reliability_row("S2", "d3-N6-medA", "benchmark_eligible"),
            _reliability_row("S3", "d3-N6-medA", "benchmark_eligible"),
            _reliability_row("S4", "d3-N6-medA", "targeted_review"),
        ],
    )

    code = benchmark.main(
        [
            "--targeted-workbook",
            str(targeted),
            "--alignment-dir",
            str(alignment),
            "--output-dir",
            str(output_dir),
            "--targeted-reliability-json",
            str(reliability_json),
            "--strict-targeted-reliability",
        ],
    )

    assert code == 0
    payload = json.loads(
        (output_dir / "targeted_istd_benchmark.json").read_text(encoding="utf-8"),
    )
    assert payload["overall_status"] == "WARN"


def _target(
    label: str,
    mz: float,
    rt_min: float,
    rt_max: float,
    product: float,
    nl: float,
) -> dict[str, object]:
    return {
        "Label": label,
        "Role": "ISTD",
        "ISTD Pair": None,
        "m/z": mz,
        "RT min": rt_min,
        "RT max": rt_max,
        "ppm tol": 20.0,
        "NL (Da)": nl,
        "Expected product m/z": product,
        "NL ppm warn": 20.0,
        "NL ppm max": 50.0,
    }


def _write_targeted_workbook(
    path: Path,
    *,
    targets: list[dict[str, object]],
    samples: tuple[str, ...],
    omit_target_columns: set[str] | None = None,
) -> None:
    omit_target_columns = omit_target_columns or set()
    workbook = Workbook()
    targets_sheet = workbook.active
    targets_sheet.title = "Targets"
    target_header = [
        "Label",
        "Role",
        "ISTD Pair",
        "m/z",
        "RT min",
        "RT max",
        "ppm tol",
        "NL (Da)",
        "Expected product m/z",
        "NL ppm warn",
        "NL ppm max",
    ]
    target_header = [
        column for column in target_header if column not in omit_target_columns
    ]
    targets_sheet.append(target_header)
    for target in targets:
        targets_sheet.append([target.get(column) for column in target_header])

    results = workbook.create_sheet("XIC Results")
    results.append(
        [
            "SampleName",
            "Group",
            "Target",
            "Role",
            "ISTD Pair",
            "RT",
            "Area",
            "NL",
            "Int",
            "PeakStart",
            "PeakEnd",
            "PeakWidth",
            "Confidence",
            "Reason",
        ]
    )
    for sample_index, sample in enumerate(samples, start=1):
        for target_index, target in enumerate(targets):
            rt = float(str(target["RT min"])) + 0.5 + sample_index * 0.01
            area = 10.0 ** sample_index
            results.append(
                [
                    sample if target_index == 0 else None,
                    "QC",
                    target["Label"],
                    "ISTD",
                    None,
                    rt,
                    area,
                    "ok",
                    1000,
                    rt - 0.05,
                    rt + 0.05,
                    0.1,
                    "HIGH",
                    "",
                ]
            )
    workbook.save(path)


def _write_alignment_run(
    path: Path,
    *,
    review_rows: list[dict[str, object]],
    matrix_rows: list[dict[str, object]],
    cell_rows: list[dict[str, object]],
    samples: tuple[str, ...] = ("S1", "S2", "S3", "S4"),
) -> None:
    path.mkdir(parents=True)
    _write_tsv(path / "alignment_review.tsv", review_rows, REVIEW_COLUMNS)
    matrix_columns = (
        "feature_family_id",
        "neutral_loss_tag",
        "family_center_mz",
        "family_center_rt",
        *samples,
    )
    _write_tsv(path / "alignment_matrix.tsv", matrix_rows, matrix_columns)
    _write_tsv(path / "alignment_cells.tsv", cell_rows, CELL_COLUMNS)


def _write_clean_alignment_run(
    path: Path,
    *,
    review_rows: list[dict[str, object]],
    identity_rows: list[dict[str, object]],
    matrix_rows: list[dict[str, object]],
    cell_rows: list[dict[str, object]],
    samples: tuple[str, ...] = ("S1", "S2", "S3", "S4"),
) -> None:
    path.mkdir(parents=True)
    _write_tsv(path / "alignment_review.tsv", review_rows, REVIEW_COLUMNS)
    _write_tsv(path / "alignment_matrix.tsv", matrix_rows, ("Mz", "RT", *samples))
    _write_tsv(
        path / "alignment_matrix_identity.tsv",
        identity_rows,
        IDENTITY_COLUMNS,
    )
    _write_tsv(path / "alignment_cells.tsv", cell_rows, CELL_COLUMNS)


REVIEW_COLUMNS = (
    "feature_family_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "family_product_mz",
    "family_observed_neutral_loss_da",
    "include_in_primary_matrix",
    "identity_decision",
)

CELL_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "area",
    "apex_rt",
)


IDENTITY_COLUMNS = (
    "identity_schema_version",
    "matrix_row_index",
    "Mz",
    "RT",
    "peak_hypothesis_id",
    "row_identity_basis",
    "split_evaluation_status",
    "projection_status",
    "source_feature_family_ids",
    "source_feature_family_count",
    "center_mz_basis",
    "center_rt_basis",
    "center_weight_basis",
    "accepted_cell_count",
    "accepted_sample_count",
    "evidence_status",
)


def _review_row(
    family_id: str,
    mz: float,
    rt: float,
    product: float,
    nl: float,
    primary: bool,
) -> dict[str, object]:
    return {
        "feature_family_id": family_id,
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": mz,
        "family_center_rt": rt,
        "family_product_mz": product,
        "family_observed_neutral_loss_da": nl,
        "include_in_primary_matrix": str(primary).upper(),
    }


def _identity_row(
    family_id: str,
    matrix_row_index: int,
    mz: float,
    rt: float,
) -> dict[str, object]:
    return {
        "identity_schema_version": (
            "untargeted_peak_hypothesis_matrix_identity"
        ),
        "matrix_row_index": matrix_row_index,
        "Mz": mz,
        "RT": rt,
        "peak_hypothesis_id": f"PH_{family_id}",
        "row_identity_basis": "no_split_peak_hypothesis",
        "split_evaluation_status": "complete_no_product_ready_split",
        "projection_status": "not_projection",
        "source_feature_family_ids": family_id,
        "source_feature_family_count": 1,
        "center_mz_basis": "accepted_cells",
        "center_rt_basis": "accepted_cells",
        "center_weight_basis": "accepted_cell_area",
        "accepted_cell_count": 4,
        "accepted_sample_count": 4,
        "evidence_status": "product_matrix_identity_complete",
    }


def _matrix_row(
    family_id: str,
    values: tuple[float, ...],
    *,
    samples: tuple[str, ...] = ("S1", "S2", "S3", "S4"),
) -> dict[str, object]:
    row: dict[str, object] = {
        "feature_family_id": family_id,
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": "",
        "family_center_rt": "",
    }
    for sample, value in zip(samples, values, strict=True):
        row[sample] = value
    return row


def _cell_row(
    family_id: str,
    sample: str,
    apex_rt: float,
    area: float,
) -> dict[str, object]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample,
        "status": "detected",
        "area": area,
        "apex_rt": apex_rt,
    }


def _benchmark_summary(
    target_label: str,
    *,
    status: str,
    active_tag: bool,
    failure_modes: tuple[str, ...] = (),
    warning_modes: tuple[str, ...] = (),
) -> BenchmarkSummary:
    return BenchmarkSummary(
        target_label=target_label,
        role="ISTD",
        active_tag=active_tag,
        neutral_loss_da=116.0474,
        target_mz=245.0,
        target_rt_min=10.0,
        target_rt_max=11.0,
        targeted_positive_count=3,
        targeted_total_count=3,
        targeted_mean_rt=10.5,
        candidate_match_count=1,
        primary_match_count=1,
        primary_feature_ids=("FAM001",),
        selected_feature_id="FAM001",
        untargeted_positive_count=3,
        coverage_minimum=3,
        paired_area_n=3,
        log_area_pearson=0.95,
        log_area_spearman=0.96,
        family_mean_rt_delta_min=0.01,
        sample_rt_pair_n=3,
        sample_rt_median_abs_delta_min=0.01,
        sample_rt_p95_abs_delta_min=0.02,
        status=status,
        failure_modes=failure_modes,
        note="",
        targeted_reliability_warning_modes=warning_modes,
    )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _reliability_row(
    sample: str,
    target: str,
    state: str,
    *,
    risk_reasons: list[str] | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "sample_name": sample,
        "target_label": target,
        "reliability_state": state,
    }
    if risk_reasons is not None:
        row["risk_reasons"] = risk_reasons
    return row


def _write_reliability_json(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "overall_status": "WARN",
                "rows": rows,
                "summaries": [],
            }
        ),
        encoding="utf-8",
    )


def _write_tsv(
    path: Path,
    rows: list[dict[str, object]],
    fieldnames: tuple[str, ...],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
