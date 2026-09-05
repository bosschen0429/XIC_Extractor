import csv
import json
from pathlib import Path

from tools.diagnostics import targeted_resolver_comparison as gate


def test_targeted_resolver_comparison_passes_stable_istd_area_and_rt(
    tmp_path: Path,
) -> None:
    targets = tmp_path / "targets.csv"
    _write_targets(targets)
    baseline = tmp_path / "baseline.csv"
    candidate = tmp_path / "candidate.csv"
    _write_results(
        baseline,
        [
            ("S1", 100.0, 10.0, 50.0, 8.0),
            ("S2", 110.0, 10.0, 52.0, 8.0),
            ("S3", 120.0, 10.0, 54.0, 8.0),
        ],
    )
    _write_results(
        candidate,
        [
            ("S1", 100.5, 10.001, 51.0, 8.0),
            ("S2", 110.5, 10.001, 52.5, 8.0),
            ("S3", 120.5, 10.001, 54.5, 8.0),
        ],
    )

    outputs, result = gate.run_targeted_resolver_comparison(
        baseline_results_csv=baseline,
        candidate_results_csv=candidate,
        targets_csv=targets,
        output_dir=tmp_path / "gate",
    )

    assert result.overall_status == "PASS"
    assert result.failed_count == 0
    assert outputs.summary_tsv.is_file()
    payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
    assert payload["overall_status"] == "PASS"


def test_targeted_resolver_comparison_fails_when_istd_rsd_regresses(
    tmp_path: Path,
) -> None:
    targets = tmp_path / "targets.csv"
    _write_targets(targets)
    baseline = tmp_path / "baseline.csv"
    candidate = tmp_path / "candidate.csv"
    _write_results(
        baseline,
        [
            ("S1", 100.0, 10.0, 50.0, 8.0),
            ("S2", 100.0, 10.0, 50.0, 8.0),
            ("S3", 100.0, 10.0, 50.0, 8.0),
        ],
    )
    _write_results(
        candidate,
        [
            ("S1", 80.0, 10.0, 50.0, 8.0),
            ("S2", 100.0, 10.0, 50.0, 8.0),
            ("S3", 120.0, 10.0, 50.0, 8.0),
        ],
    )

    _outputs, result = gate.run_targeted_resolver_comparison(
        baseline_results_csv=baseline,
        candidate_results_csv=candidate,
        targets_csv=targets,
        output_dir=tmp_path / "gate",
    )

    row = next(row for row in result.rows if row.target_label == "ISTD_A")
    assert row.status == "FAIL"
    assert "area_rsd_regression" in row.failure_reasons
    assert result.overall_status == "FAIL"


def test_main_writes_outputs_and_returns_failure_code(tmp_path: Path) -> None:
    targets = tmp_path / "targets.csv"
    _write_targets(targets)
    baseline = tmp_path / "baseline.csv"
    candidate = tmp_path / "candidate.csv"
    _write_results(
        baseline,
        [("S1", 100.0, 10.0, 50.0, 8.0), ("S2", 100.0, 10.0, 50.0, 8.0)],
    )
    _write_results(
        candidate,
        [("S1", 100.0, 10.02, 50.0, 8.0), ("S2", 100.0, 10.02, 50.0, 8.0)],
    )
    output_dir = tmp_path / "gate"

    code = gate.main(
        [
            "--baseline-results-csv",
            str(baseline),
            "--candidate-results-csv",
            str(candidate),
            "--targets-csv",
            str(targets),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 1
    assert (output_dir / "targeted_resolver_comparison_rows.tsv").is_file()
    assert (output_dir / "targeted_resolver_comparison_summary.tsv").is_file()
    assert (output_dir / "targeted_resolver_comparison.json").is_file()
    assert (output_dir / "targeted_resolver_comparison.md").is_file()

    rows_tsv = output_dir / "targeted_resolver_comparison_rows.tsv"
    summary_tsv = output_dir / "targeted_resolver_comparison_summary.tsv"
    assert b"\r\n" not in rows_tsv.read_bytes()
    assert b"\r\n" not in summary_tsv.read_bytes()
    assert rows_tsv.read_text(encoding="utf-8").splitlines()[0].split("\t") == [
        "target_label",
        "sample_count",
        "baseline_area_rsd_pct",
        "candidate_area_rsd_pct",
        "area_rsd_delta_pct",
        "rt_median_abs_delta_sec",
        "status",
        "failure_reasons",
    ]
    assert _read_tsv(rows_tsv) == [
        {
            "target_label": "ISTD_A",
            "sample_count": "2",
            "baseline_area_rsd_pct": "0",
            "candidate_area_rsd_pct": "0",
            "area_rsd_delta_pct": "0",
            "rt_median_abs_delta_sec": "1.2",
            "status": "FAIL",
            "failure_reasons": "rt_median_shift_regression",
        },
        {
            "target_label": "ISTD_B",
            "sample_count": "2",
            "baseline_area_rsd_pct": "0",
            "candidate_area_rsd_pct": "0",
            "area_rsd_delta_pct": "0",
            "rt_median_abs_delta_sec": "0",
            "status": "PASS",
            "failure_reasons": "",
        },
    ]
    assert summary_tsv.read_text(encoding="utf-8").splitlines()[0].split("\t") == [
        "overall_status",
        "failed_count",
        "target_count",
        "max_area_rsd_delta_pct",
        "max_rt_median_abs_delta_sec",
        "max_rsd_regression_pct",
        "max_rt_median_shift_sec",
    ]
    assert _read_tsv(summary_tsv) == [
        {
            "overall_status": "FAIL",
            "failed_count": "1",
            "target_count": "2",
            "max_area_rsd_delta_pct": "0",
            "max_rt_median_abs_delta_sec": "1.2",
            "max_rsd_regression_pct": "0.5",
            "max_rt_median_shift_sec": "0.5",
        },
    ]


def _write_targets(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("label", "mz", "is_istd"),
        )
        writer.writeheader()
        writer.writerow({"label": "ISTD_A", "mz": "100.0", "is_istd": "TRUE"})
        writer.writerow({"label": "Analyte", "mz": "101.0", "is_istd": "FALSE"})
        writer.writerow({"label": "ISTD_B", "mz": "102.0", "is_istd": "TRUE"})


def _write_results(
    path: Path,
    rows: list[tuple[str, float, float, float, float]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "SampleName",
                "ISTD_A_Area",
                "ISTD_A_RT",
                "ISTD_B_Area",
                "ISTD_B_RT",
                "Analyte_Area",
                "Analyte_RT",
            ),
        )
        writer.writeheader()
        for sample, istd_a_area, istd_a_rt, istd_b_area, istd_b_rt in rows:
            writer.writerow(
                {
                    "SampleName": sample,
                    "ISTD_A_Area": istd_a_area,
                    "ISTD_A_RT": istd_a_rt,
                    "ISTD_B_Area": istd_b_area,
                    "ISTD_B_RT": istd_b_rt,
                    "Analyte_Area": "999",
                    "Analyte_RT": "1.0",
                }
            )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
