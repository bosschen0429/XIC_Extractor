from pathlib import Path

from openpyxl import Workbook

from scripts.compare_workbooks import COMPARE_SHEETS, compare_workbooks


def test_compare_workbooks_uses_review_workflow_sheet_order() -> None:
    assert COMPARE_SHEETS == (
        "Overview",
        "Review Queue",
        "XIC Results",
        "Summary",
        "Targets",
        "Diagnostics",
        "Run Metadata",
    )


def test_compare_workbooks_accepts_identical_workbooks(tmp_path: Path) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    _write_workbook(left)
    _write_workbook(right)

    result = compare_workbooks(left, right)

    assert result.matched
    assert result.differences == []


def test_compare_workbooks_rejects_changed_analytical_value(tmp_path: Path) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    _write_workbook(left, area=100.0)
    _write_workbook(right, area=110.0)

    result = compare_workbooks(left, right)

    assert not result.matched
    assert any(
        "XIC Results" in diff and "100" in diff and "110" in diff
        for diff in result.differences
    )


def test_compare_workbooks_compares_overview_sheet(tmp_path: Path) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    _write_workbook(left, overview_review_items=1)
    _write_workbook(right, overview_review_items=2)

    result = compare_workbooks(left, right)

    assert not result.matched
    assert any("Overview" in diff for diff in result.differences)


def test_compare_workbooks_ignores_generated_at_metadata(tmp_path: Path) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    _write_workbook(left, generated_at="2026-05-03T00:00:00Z")
    _write_workbook(right, generated_at="2026-05-04T00:00:00Z")

    result = compare_workbooks(left, right)

    assert result.matched
    assert result.differences == []


def test_compare_workbooks_ignores_runtime_and_output_path_metadata(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    _write_workbook(
        left,
        elapsed_seconds="1.0",
        output_path="C:\\old\\xic_results.xlsx",
    )
    _write_workbook(
        right,
        elapsed_seconds="2.0",
        output_path="D:\\new\\xic_results.xlsx",
    )

    result = compare_workbooks(left, right)

    assert result.matched
    assert result.differences == []


def test_compare_workbooks_ignores_manifest_location_and_hash_metadata(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    _write_workbook(
        left,
        method_manifest_path="C:\\old\\method_manifest.json",
        method_manifest_sha256="a" * 64,
    )
    _write_workbook(
        right,
        method_manifest_path="D:\\new\\method_manifest.json",
        method_manifest_sha256="b" * 64,
    )

    result = compare_workbooks(left, right)

    assert result.matched
    assert result.differences == []


def test_compare_workbooks_compares_manifest_schema_metadata(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    _write_workbook(left, method_manifest_schema="method_manifest")
    _write_workbook(right, method_manifest_schema="unsupported_method_manifest")

    result = compare_workbooks(left, right)

    assert not result.matched
    assert any("method_manifest" in diff for diff in result.differences)


def test_compare_workbooks_ignores_sheet_order(tmp_path: Path) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    _write_workbook(
        left,
        sheet_order=(
            "Overview",
            "Review Queue",
            "XIC Results",
            "Summary",
            "Targets",
            "Diagnostics",
            "Run Metadata",
        ),
    )
    _write_workbook(
        right,
        sheet_order=(
            "Run Metadata",
            "Diagnostics",
            "Targets",
            "Summary",
            "XIC Results",
            "Review Queue",
            "Overview",
        ),
    )

    result = compare_workbooks(left, right)

    assert result.matched
    assert result.differences == []


def test_compare_workbooks_compares_score_breakdown_when_present(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left.xlsx"
    right = tmp_path / "right.xlsx"
    _write_workbook(left, include_score_breakdown=True, score=0.9)
    _write_workbook(right, include_score_breakdown=True, score=0.8)

    result = compare_workbooks(left, right)

    assert not result.matched
    assert any("Score Breakdown" in diff for diff in result.differences)


def _write_workbook(
    path: Path,
    *,
    area: float = 100.0,
    generated_at: str = "2026-05-03T00:00:00Z",
    elapsed_seconds: str = "1.0",
    output_path: str = "C:\\output\\xic_results.xlsx",
    method_manifest_schema: str = "method_manifest",
    method_manifest_path: str = "C:\\output\\method_manifest.json",
    method_manifest_sha256: str = "a" * 64,
    include_score_breakdown: bool = False,
    score: float = 0.9,
    overview_review_items: int = 1,
    sheet_order: tuple[str, ...] = (
        "Overview",
        "Review Queue",
        "XIC Results",
        "Summary",
        "Targets",
        "Diagnostics",
        "Run Metadata",
    ),
) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    for name in sheet_order:
        ws = wb.create_sheet(name)
        if name == "Overview":
            ws.append(["Metric", "Value"])
            ws.append(["Review Items", overview_review_items])
        elif name == "XIC Results":
            ws.append(["Sample", "Target", "Area"])
            ws.append(["SampleA", "Analyte", area])
        elif name == "Summary":
            ws.append(["Target", "Detected"])
            ws.append(["Analyte", 1])
        elif name == "Review Queue":
            ws.append(["Priority", "Sample", "Target", "Issue"])
        elif name == "Targets":
            ws.append(["label", "mz"])
            ws.append(["Analyte", 258.1085])
        elif name == "Diagnostics":
            ws.append(["Sample", "Issue"])
        elif name == "Run Metadata":
            ws.append(["key", "value"])
            ws.append(["generated_at", generated_at])
            ws.append(["elapsed_seconds", elapsed_seconds])
            ws.append(["output_path", output_path])
            ws.append(["method_manifest_schema", method_manifest_schema])
            ws.append(["method_manifest_path", method_manifest_path])
            ws.append(["method_manifest_sha256", method_manifest_sha256])
            ws.append(["resolver_mode", "legacy_savgol"])
    if include_score_breakdown:
        ws = wb.create_sheet("Score Breakdown")
        ws.append(["Sample", "Target", "Score"])
        ws.append(["SampleA", "Analyte", score])
    wb.save(path)
