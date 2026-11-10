import csv
from pathlib import Path
from typing import Literal

from tests.alignment_pipeline_helpers import owner_cell
from xic_extractor.alignment.cell_decision.owner import ProductionCellDecision
from xic_extractor.alignment.untargeted_finalized_matrix import (
    AlignmentMatrix,
    FinalizedUntargetMatrixRow,
)
from xic_extractor.output.alignment.tsv_writer import (
    write_alignment_matrix_tsv,
    write_alignment_review_tsv,
)

FORBIDDEN_PRIMARY_STATUSES = {
    "detected",
    "backfilled",
    "not_searched",
    "searched_no_observation",
    "unresolved",
    "rejected_observation",
    "not_quantifiable",
    "unavailable",
}


def test_primary_outputs_hide_status_strings_and_keep_audit_reasons(
    tmp_path: Path,
):
    matrix = AlignmentMatrix(
        clusters=(
            _feature(
                "FAM001",
                owner_cell(area=100.0),
                owner_cell("backfilled", area=90.0),
                owner_cell(area=115.0),
            ),
            _feature(
                "FAM_PROVISIONAL",
                owner_cell(area=85.0),
                owner_cell("backfilled", area=75.0),
                owner_cell(None),
                has_anchor=False,
                destination="archive",
            ),
            _feature(
                "FAM002",
                owner_cell("backfilled", area=80.0),
                owner_cell(None),
                owner_cell(None),
                has_anchor=False,
                destination="archive",
            ),
            _feature(
                "FAM003",
                owner_cell(None, no_write="not_searched"),
                owner_cell(None, no_write="unresolved"),
                owner_cell(None),
                destination="archive",
            ),
        ),
        sample_order=("s1", "s2", "s3"),
    )

    tsv_rows = _read_tsv(
        write_alignment_matrix_tsv(tmp_path / "alignment_matrix.tsv", matrix)
    )
    assert list(tsv_rows[0]) == ["Mz", "RT", "s1", "s2", "s3"]
    assert [(row["Mz"], row["RT"]) for row in tsv_rows] == [("500.1230", "8.49")]
    assert tsv_rows[0]["s1"] == "1.00000e+02"
    assert tsv_rows[0]["s2"] == "9.00000e+01"
    assert tsv_rows[0]["s3"] == "1.15000e+02"
    assert FORBIDDEN_PRIMARY_STATUSES.isdisjoint(
        value for row in tsv_rows for value in row.values()
    )

    identity_rows = _read_tsv(
        write_alignment_review_tsv(tmp_path / "alignment_review.tsv", matrix)
    )
    matrix_projection = [row for row in identity_rows if row["matrix_row_index"]]
    assert len(matrix_projection) == len(tsv_rows)
    assert matrix_projection[0]["matrix_row_index"] == "1"
    assert matrix_projection[0]["feature_id"] == "FAM001"
    assert len(identity_rows) == len(matrix.clusters)


def _feature(
    feature_id: str,
    *cells: ProductionCellDecision,
    has_anchor: bool = True,
    destination: Literal["matrix", "archive"] = "matrix",
) -> FinalizedUntargetMatrixRow:
    return FinalizedUntargetMatrixRow(
        feature_id=feature_id,
        group_hypothesis_id=feature_id,
        destination=destination,
        family_center_mz=500.123,
        family_center_rt=8.49,
        physical_peak_keys=(),
        family_product_mz=384.076,
        family_observed_neutral_loss_da=116.047,
        neutral_loss_tag="DNA_dR",
        has_anchor=has_anchor,
        cells=cells,
    )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
