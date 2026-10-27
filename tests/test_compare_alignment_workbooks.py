from pathlib import Path

from openpyxl import Workbook

from scripts import compare_alignment_workbooks as compare_cli
from scripts.compare_alignment_workbooks import compare_alignment_workbooks


def test_compare_alignment_workbooks_compares_matrix_only(tmp_path: Path) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    _workbook(left, matrix_rows=(("feature_id", "S1"), ("FAM001", 10.0)))
    _workbook(
        right,
        matrix_rows=(("feature_id", "S1"), ("FAM001", 10.0)),
        review_value="different review text",
    )

    result = compare_alignment_workbooks(left, right)

    assert result.matched is True
    assert result.differences == ()


def test_compare_alignment_workbooks_reports_matrix_difference(tmp_path: Path) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    _workbook(left, matrix_rows=(("feature_id", "S1"), ("FAM001", 10.0)))
    _workbook(right, matrix_rows=(("feature_id", "S1"), ("FAM001", 12.0)))

    result = compare_alignment_workbooks(left, right)

    assert result.matched is False
    assert result.differences
    assert "Matrix!R2C2" in result.differences[0]


def test_compare_alignment_workbooks_cli_writes_artifacts_on_failure(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    output_tsv = tmp_path / "compare.tsv"
    output_report = tmp_path / "compare.txt"
    _workbook(left, matrix_rows=(("feature_id", "S1"), ("FAM001", 10.0)))
    _workbook(right, matrix_rows=(("feature_id", "S1"), ("FAM001", 12.0)))

    code = compare_cli.main(
        [
            str(left),
            str(right),
            "--output-tsv",
            str(output_tsv),
            "--output-report",
            str(output_report),
        ]
    )

    assert code == 1
    assert b"\r\n" not in output_tsv.read_bytes()
    tsv_lines = output_tsv.read_text(encoding="utf-8").splitlines()
    assert tsv_lines[0] == "status\tdifference"
    assert tsv_lines[1].startswith("FAIL\tMatrix!R2C2")
    assert "Matrix!R2C2" in output_report.read_text(encoding="utf-8")


def _workbook(
    path: Path,
    *,
    matrix_rows: tuple[tuple[object, ...], ...],
    review_value: str = "review",
) -> None:
    workbook = Workbook()
    matrix = workbook.active
    matrix.title = "Matrix"
    for row in matrix_rows:
        matrix.append(row)
    review = workbook.create_sheet("Review")
    review.append(("note", review_value))
    workbook.save(path)
