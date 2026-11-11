import pytest

from tests.duplicate_quantification_fixtures import (
    publish_owner_run,
)
from xic_extractor.alignment_explanation.published_cells import (
    PublishedCell,
    read_published_cells,
)
from xic_extractor.diagnostics.duplicate_quantification import (
    summarize_duplicate_quantification,
)


def _cell(row, sample, apex, *, direct=True, mz=300.0):
    return PublishedCell(
        row=row, row_mz=mz, sample=sample, direct=direct, apex_rt_min=apex
    )


def test_duplicate_quantification_classifies_row_pairs_by_direct_detections():
    cells = (
        # Rows 1/2: complementary direct detections, each backfilled onto the other.
        _cell(1, "s1", 10.0),
        _cell(1, "s2", 10.01, direct=False),
        _cell(2, "s2", 10.0),
        _cell(2, "s1", 10.02, direct=False),
        # Rows 3/4: separate peaks in s3; row 4's s4 Backfill lands on row 3's.
        _cell(3, "s3", 12.0, mz=400.0),
        _cell(4, "s3", 12.5, mz=400.0),
        _cell(3, "s4", 12.0, mz=400.0),
        _cell(4, "s4", 12.01, direct=False, mz=400.0),
        # Rows 5/6: both directly detect one apex.
        _cell(5, "s5", 14.0, mz=500.0),
        _cell(6, "s5", 14.0, mz=500.001),
        # Different m/z or apex: not duplicates.
        _cell(7, "s5", 14.0, mz=500.1),
        _cell(8, "s5", 14.2, mz=500.0),
    )
    summary = summarize_duplicate_quantification(cells)

    assert summary["duplicate_cell_pairs"] == 4
    assert summary["duplicate_row_pairs"] == 3
    assert summary["row_pair_categories"] == {
        "complementary_split": 1,
        "distinct_peaks_shared_backfill": 1,
        "view_duplicate": 1,
    }
    assert summary["cell_pair_kinds"] == {"BD": 3, "DD": 1}


# Machine is what Workbench Untargeted publishes: no backfill ledger.
@pytest.mark.parametrize(
    "level", ("machine", "validation-minimal", "validation", "debug")
)
def test_duplicate_quantification_reads_published_output_levels(
    tmp_path, monkeypatch, level
):
    directory = publish_owner_run(tmp_path, monkeypatch, level)

    def forbidden(*args, **kwargs):
        raise AssertionError("scalar cell reading decoded cold scientific details")

    monkeypatch.setattr(
        "xic_extractor.alignment_explanation.reader._Report.cells", forbidden
    )
    cells = read_published_cells(directory)

    assert {(cell.sample, cell.direct, cell.apex_rt_min) for cell in cells} == {
        ("S1", True, 8.0),
        ("S2", True, 8.0),
    }
    assert all(cell.value is not None and cell.value > 0 for cell in cells)
