from pathlib import Path

from tests.test_alignment_xlsx_writer import sample_alignment_matrix, sample_cell
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.html_report import write_alignment_review_html
from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.production_decisions import (
    ProductionDecisionSet,
    build_production_decisions,
)


def _production_decisions(matrix: AlignmentMatrix) -> ProductionDecisionSet:
    return build_production_decisions(
        matrix,
        AlignmentConfig(),
    )


def test_alignment_review_html_contains_visual_summary_not_full_cell_table(
    tmp_path: Path,
):
    matrix = sample_alignment_matrix()
    path = write_alignment_review_html(
        tmp_path / "review_report.html",
        matrix,
        production_decisions=_production_decisions(matrix),
    )

    html = path.read_text(encoding="utf-8")
    assert "Alignment Review" in html
    assert "Detected / Rescued / Ambiguous" in html
    assert "Ownership pressure" in html
    assert "alignment_cells.tsv" not in html
    assert "<table" not in html.lower()


def test_alignment_review_html_reports_duplicate_ownership_pressure(
    tmp_path: Path,
):
    base = sample_alignment_matrix()
    matrix = AlignmentMatrix(
        clusters=base.clusters,
        sample_order=("s1", "s2"),
        cells=(
            sample_cell("s1", "FAM000001", "detected", 100.0),
            sample_cell("s2", "FAM000001", "duplicate_assigned", 200.0),
        ),
    )

    path = write_alignment_review_html(
        tmp_path / "review.html",
        matrix,
        production_decisions=_production_decisions(matrix),
    )

    html = path.read_text(encoding="utf-8")
    assert "duplicate_assigned" in html
    assert "1 duplicate-assigned cells" in html
