from xic_extractor.diagnostics.duplicate_quantification import (
    PublishedCell,
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
