import csv
import math
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from tests.alignment_pipeline_helpers import owner_cell
from xic_extractor.alignment.cell_decision.owner import ProductionCellDecision
from xic_extractor.alignment.untargeted_finalized_matrix import (
    AlignmentMatrix,
    FinalizedUntargetMatrixRow,
)
from xic_extractor.output.alignment.tsv_writer import ALIGNMENT_CELLS_COLUMNS
from xic_extractor.peak_detection.hypotheses import IntegrationResult

REVIEW_COLUMNS = [
    "feature_id",
    "matrix_row_index",
    "matrix_mz",
    "matrix_rt",
    "center_mz_basis",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "family_product_mz",
    "family_observed_neutral_loss_da",
    "physical_peak_keys",
    "detected_count",
    "not_searched_count",
    "unresolved_count",
    "present_rate",
    "identity_reason",
    "accepted_cell_count",
    "accepted_backfill_count",
    "include_in_primary_matrix",
    "row_flags",
    "membership_risk_flags",
    "warning",
    "reason",
]


def test_write_alignment_review_tsv_columns_counts_rates_and_reason(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    matrix = _matrix(
        ("sample-a", "sample-b", "sample-c", "sample-d"),
        _row(
            owner_cell(area=10.0),
            owner_cell("backfilled", area=20.0),
            owner_cell(None),
            owner_cell(area=30.0),
            member_count=1,
        ),
    )

    path = write_alignment_review_tsv(tmp_path / "alignment_review.tsv", matrix)
    rows = _read_tsv(path)

    assert list(rows[0]) == REVIEW_COLUMNS
    assert {
        key: value
        for key, value in rows[0].items()
        if key
        not in {
            "matrix_row_index",
            "matrix_mz",
            "matrix_rt",
            "center_mz_basis",
        }
    } == {
        "membership_risk_flags": "",
        "feature_id": "ALN000001",
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": "500.123",
        "family_center_rt": "8.49",
        "family_product_mz": "384.076",
        "family_observed_neutral_loss_da": "116.047",
        "physical_peak_keys": "ALN000001#peak-0",
        "detected_count": "2",
        "not_searched_count": "0",
        "unresolved_count": "0",
        "present_rate": "0.75",
        "identity_reason": "test_route",
        "accepted_cell_count": "3",
        "accepted_backfill_count": "1",
        "include_in_primary_matrix": "TRUE",
        "row_flags": "finalized_route:matrix;cross_sample_owner_arithmetic_mean",
        "warning": "",
        "reason": "anchor family; 3/4 present; 1 MS1 backfilled",
    }


def test_write_alignment_review_tsv_warning_precedence(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    not_searched = owner_cell(None, no_write="not_searched")
    matrix = _matrix(
        ("sample-a", "sample-b"),
        _row(owner_cell(area=1.0), not_searched, has_anchor=False),
        _row(not_searched, not_searched, feature_id="ALN000002"),
        _row(
            owner_cell("backfilled", area=1.0),
            owner_cell(None),
            feature_id="ALN000003",
        ),
        _row(owner_cell(area=1.0), owner_cell(None), feature_id="ALN000004"),
        _row(
            owner_cell(area=1.0),
            owner_cell("backfilled", area=1.0),
            feature_id="ALN000005",
        ),
    )

    rows = _read_tsv(write_alignment_review_tsv(tmp_path / "review.tsv", matrix))

    assert [row["warning"] for row in rows] == [
        "no_anchor",
        "high_not_searched",
        "high_backfill_dependency",
        "",
        "",
    ]
    assert [row["not_searched_count"] for row in rows] == ["1", "2", "0", "0", "0"]


def test_review_uses_physical_members_and_owner_counts_not_evidence_text(tmp_path):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    matrix = _matrix(
        ("direct", "backfill-a", "backfill-b"),
        _row(
            owner_cell(area=10.0),
            owner_cell("backfilled", area=20.0),
            owner_cell("backfilled", area=30.0),
            member_count=3,
        ),
    )
    (row,) = _read_tsv(write_alignment_review_tsv(tmp_path / "review.tsv", matrix))
    assert row["physical_peak_keys"] == (
        "ALN000001#peak-0;ALN000001#peak-1;ALN000001#peak-2"
    )
    assert row["warning"] == ""
    assert row["reason"] == "anchor family; 3/3 present; 2 MS1 backfilled"


def test_write_alignment_review_tsv_reports_unresolved_cells(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    matrix = _matrix(
        ("sample-a", "sample-b", "sample-c"),
        _row(
            owner_cell(area=100.0),
            owner_cell(None, no_write="unresolved"),
            owner_cell(None),
        ),
    )

    (row,) = _read_tsv(write_alignment_review_tsv(tmp_path / "review.tsv", matrix))

    assert row["detected_count"] == "1"
    assert row["unresolved_count"] == "1"
    assert row["present_rate"] == "0.333333"
    assert row["reason"] == "anchor family; 1/3 present; 0 MS1 backfilled; 1 unresolved"


def test_write_alignment_matrix_tsv_writes_owner_values_and_blanks_no_write_cells(
    tmp_path: Path,
):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    samples = (
        "detected",
        "small",
        "large",
        "backfilled",
        "not-quantifiable",
        "not-searched",
        "unresolved",
        "no-observation",
    )
    matrix = _matrix(
        samples,
        _row(
            owner_cell(area=1234.567),
            owner_cell(area=0.00123456),
            owner_cell(area=123456789.0),
            owner_cell("backfilled", area=25.0),
            owner_cell(area=None),
            owner_cell(None, no_write="not_searched"),
            owner_cell(None, no_write="unresolved"),
            owner_cell(None),
        ),
    )

    (row,) = _read_tsv(write_alignment_matrix_tsv(tmp_path / "matrix.tsv", matrix))

    assert list(row) == ["Mz", "RT", *samples]
    assert row["Mz"] == "500.1230"
    assert row["RT"] == "8.49"
    assert row["detected"] == "1.23457e+03"
    assert row["small"] == "1.23456e-03"
    assert row["large"] == "1.23457e+08"
    assert row["backfilled"] == "2.50000e+01"
    assert float(row["small"]) == float(f"{0.00123456:.6g}")
    assert [row[sample] for sample in samples[4:]] == ["", "", "", ""]


def test_alignment_matrix_mz_format_preserves_identity_coordinate_contract(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.alignment.matrix_output_contract import (
        matrix_review_rows,
    )
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = _two_sample_matrix(center_mz=500.123456)

    matrix_row = _read_tsv(
        write_alignment_matrix_tsv(tmp_path / "alignment_matrix.tsv", matrix)
    )[0]
    review_row = _read_tsv(
        write_alignment_review_tsv(tmp_path / "alignment_review.tsv", matrix)
    )[0]

    assert matrix_row["Mz"] == "500.1235"
    assert review_row["matrix_mz"] == "500.123456"
    assert review_row["family_center_mz"] == "500.123456"
    matrix_review_rows(
        matrix_header=tuple(matrix_row),
        matrix_rows=(matrix_row,),
        review_columns=tuple(review_row),
        review_rows=(review_row,),
    )


def test_alignment_matrix_mz_format_is_display_only_for_small_mz(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = _two_sample_matrix(center_mz=5.123456)

    matrix_row = _read_tsv(
        write_alignment_matrix_tsv(tmp_path / "alignment_matrix.tsv", matrix)
    )[0]
    identity_row = _read_tsv(
        write_alignment_review_tsv(tmp_path / "alignment_review.tsv", matrix)
    )[0]

    assert matrix_row["Mz"] == "5.1235"
    assert identity_row["matrix_mz"] == "5.123456"


def test_alignment_matrix_display_collision_does_not_merge_identity_rows(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = _display_collision_matrix()

    matrix_rows = _read_tsv(
        write_alignment_matrix_tsv(tmp_path / "alignment_matrix.tsv", matrix)
    )
    identity_rows = _read_tsv(
        write_alignment_review_tsv(tmp_path / "alignment_review.tsv", matrix)
    )

    assert [row["Mz"] for row in matrix_rows] == ["500.1234", "500.1234"]
    assert [row["matrix_mz"] for row in identity_rows] == [
        "500.123441",
        "500.123449",
    ]
    assert [row["feature_id"] for row in identity_rows] == [
        "ALN000001",
        "ALN000002",
    ]


@pytest.mark.parametrize(
    "mutation",
    (
        "duplicate",
        "missing",
        "extra",
        "missing_column",
        "unknown_column",
    ),
)
def test_alignment_matrix_identity_rejects_review_census_and_schema_mismatch(
    tmp_path: Path,
    mutation: str,
) -> None:
    from xic_extractor.output.alignment.matrix_output_contract import (
        matrix_review_rows,
    )
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = _display_collision_matrix()
    matrix_rows = _read_tsv(
        write_alignment_matrix_tsv(tmp_path / "alignment_matrix.tsv", matrix)
    )
    identity_rows = _read_tsv(
        write_alignment_review_tsv(tmp_path / "alignment_review.tsv", matrix)
    )
    matrix_header = tuple(matrix_rows[0])
    identity_columns = tuple(identity_rows[0])

    matrix_review_rows(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        review_columns=identity_columns,
        review_rows=identity_rows,
    )

    if mutation == "duplicate":
        identity_rows[1] = dict(identity_rows[0])
    elif mutation == "missing":
        identity_rows.pop()
    elif mutation == "extra":
        identity_rows.append(dict(identity_rows[-1]))
    elif mutation == "missing_column":
        identity_columns = tuple(
            column for column in identity_columns if column != "center_mz_basis"
        )
    else:
        identity_columns = (*identity_columns, "unknown_identity_field")

    with pytest.raises(ValueError, match="Review"):
        matrix_review_rows(
            matrix_header=matrix_header,
            matrix_rows=matrix_rows,
            review_columns=identity_columns,
            review_rows=identity_rows,
        )


def test_alignment_matrix_identity_requires_current_schema() -> None:
    from tests.matrix_review_fixtures import current_matrix_review_row
    from xic_extractor.output.alignment.matrix_output_contract import (
        MATRIX_REVIEW_COLUMNS,
        matrix_review_rows,
    )

    matrix_header = ("Mz", "RT", "sample-a")
    matrix_rows = ({"Mz": "500.1230", "RT": "8.49", "sample-a": "100"},)
    identity = current_matrix_review_row(
        index=1,
        mz="500.123",
        rt="8.49",
        feature_id="ALN000001",
    )
    matrix_review_rows(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        review_columns=MATRIX_REVIEW_COLUMNS,
        review_rows=(identity,),
    )
    for columns in (
        tuple(c for c in MATRIX_REVIEW_COLUMNS if c != "center_mz_basis"),
        (*MATRIX_REVIEW_COLUMNS, "unknown_identity_field"),
    ):
        with pytest.raises(ValueError, match="Review.*fields|row fields"):
            matrix_review_rows(
                matrix_header=matrix_header,
                matrix_rows=matrix_rows,
                review_columns=columns,
                review_rows=(identity,),
            )


@pytest.mark.parametrize(
    "mutation",
    (
        "identity_overflow",
        "identity_missing_key",
        "identity_truncated",
        "matrix_overflow",
        "matrix_missing_key",
        "matrix_truncated",
    ),
)
def test_alignment_matrix_identity_rejects_malformed_serialized_row_shapes(
    mutation: str,
) -> None:
    from tests.matrix_review_fixtures import current_matrix_review_row
    from xic_extractor.output.alignment.matrix_output_contract import (
        MATRIX_REVIEW_COLUMNS,
        matrix_review_rows,
    )

    matrix_header = ("Mz", "RT", "sample-a")
    matrix_row: dict[object, object] = {
        "Mz": "500.1230",
        "RT": "8.49",
        "sample-a": "100",
    }
    identity_row: dict[object, object] = current_matrix_review_row(
        index=1,
        mz="500.123",
        rt="8.49",
        feature_id="ALN000001",
    )
    if mutation == "identity_overflow":
        identity_row[None] = ["overflow"]
    elif mutation == "identity_missing_key":
        identity_row.pop("center_mz_basis")
    elif mutation == "identity_truncated":
        identity_row["center_mz_basis"] = None
    elif mutation == "matrix_overflow":
        matrix_row[None] = ["overflow"]
    elif mutation == "matrix_missing_key":
        matrix_row.pop("sample-a")
    else:
        matrix_row["sample-a"] = None

    with pytest.raises(ValueError, match="row fields|missing cells"):
        matrix_review_rows(
            matrix_header=matrix_header,
            matrix_rows=(matrix_row,),
            review_columns=MATRIX_REVIEW_COLUMNS,
            review_rows=(identity_row,),
        )


def test_alignment_matrix_binding_uses_exact_persisted_rt_and_header_tokens(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.alignment.matrix_output_contract import (
        matrix_review_rows,
    )
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = _matrix(
        ("+sample", "safe"),
        _row(owner_cell(area=100.0), owner_cell(area=110.0), center_rt=8.49123456),
    )
    matrix_path = write_alignment_matrix_tsv(tmp_path / "matrix.tsv", matrix)
    identity_path = write_alignment_review_tsv(tmp_path / "identity.tsv", matrix)

    with matrix_path.open(encoding="utf-8", newline="") as handle:
        matrix_reader = csv.DictReader(handle, delimiter="\t")
        matrix_header = tuple(matrix_reader.fieldnames or ())
        matrix_rows = list(matrix_reader)
    with identity_path.open(encoding="utf-8", newline="") as handle:
        identity_reader = csv.DictReader(handle, delimiter="\t")
        identity_header = tuple(identity_reader.fieldnames or ())
        identity_rows = list(identity_reader)

    assert matrix_header == ("Mz", "RT", "'+sample", "safe")
    assert matrix_rows[0]["RT"] == identity_rows[0]["matrix_rt"] == "8.49123"
    matrix_review_rows(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        review_columns=identity_header,
        review_rows=identity_rows,
    )


@pytest.mark.parametrize("center_mz", [math.nan, math.inf, -math.inf])
def test_alignment_matrix_mz_format_preserves_nonfinite_blank_semantics(
    tmp_path: Path,
    center_mz: float,
) -> None:
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = _two_sample_matrix(center_mz=center_mz)

    row = _read_tsv(write_alignment_matrix_tsv(tmp_path / "matrix.tsv", matrix))[0]

    assert row["Mz"] == ""


@pytest.mark.parametrize(
    "area_source",
    [
        "trapezoid_counts_seconds_on_canonical_signal",
        "trapezoid_counts_seconds_on_observed_censored_signal",
    ],
)
def test_alignment_writers_preserve_physical_owner_area_source(
    tmp_path: Path,
    area_source: str,
) -> None:
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_cells_tsv,
        write_alignment_matrix_tsv,
    )

    integration = _integration(
        raw_area=66.6,
        asls_area=None,
        morphology_area=66.6,
        morphology_area_source=area_source,
    )
    matrix = _matrix(
        ("sample-a", "sample-b"),
        _row(
            owner_cell(area=66.6, integration=integration, source=area_source),
            owner_cell(area=77.7),
        ),
    )

    rows = _read_tsv(write_alignment_cells_tsv(tmp_path / "cells.tsv", matrix))

    assert rows[0]["area"] == "66.6"
    assert rows[0]["primary_matrix_area"] == "66.6"
    assert rows[0]["primary_matrix_area_source"] == area_source
    assert rows[0]["primary_matrix_area_reason"] == ""
    values = _read_tsv(write_alignment_matrix_tsv(tmp_path / "matrix.tsv", matrix))
    assert values[0]["sample-a"] == "6.66000e+01"
    assert values[0]["sample-b"] == "7.77000e+01"


def test_write_alignment_matrix_tsv_excludes_archived_rows(
    tmp_path: Path,
):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = _matrix(
        ("sample-a", "sample-b"),
        _row(owner_cell(area=100.0), owner_cell(area=110.0)),
        _row(
            owner_cell("backfilled", area=200.0),
            owner_cell(None),
            feature_id="ALN000002",
            has_anchor=False,
            destination="archive",
        ),
        _row(
            owner_cell(None, no_write="unresolved"),
            owner_cell(None),
            feature_id="ALN000003",
            destination="archive",
        ),
    )

    rows = _read_tsv(write_alignment_matrix_tsv(tmp_path / "matrix.tsv", matrix))

    assert [(row["Mz"], row["RT"]) for row in rows] == [("500.1230", "8.49")]
    assert rows[0]["sample-a"] == "1.00000e+02"
    assert rows[0]["sample-b"] == "1.10000e+02"


def test_write_alignment_review_tsv_maps_product_rows(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.alignment.tsv_writer import (
        MATRIX_REVIEW_COLUMNS,
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = _two_sample_matrix()

    matrix_rows = _read_tsv(write_alignment_matrix_tsv(tmp_path / "matrix.tsv", matrix))
    identity_rows = _read_tsv(
        write_alignment_review_tsv(tmp_path / "alignment_review.tsv", matrix)
    )

    assert list(identity_rows[0]) == REVIEW_COLUMNS
    assert len(identity_rows) == len(matrix_rows) == 1
    assert {key: identity_rows[0][key] for key in MATRIX_REVIEW_COLUMNS} == {
        "feature_id": "ALN000001",
        "matrix_row_index": "1",
        "matrix_mz": "500.123",
        "matrix_rt": matrix_rows[0]["RT"],
        "center_mz_basis": "cross_sample_owner_arithmetic_mean",
        "accepted_cell_count": "2",
    }


def test_write_alignment_matrix_rejects_duplicate_source_candidate_writes(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    cells = (owner_cell(area=100.0), owner_cell(area=110.0))
    matrix = _matrix(
        ("sample-a", "sample-b"),
        _row(*cells, candidates=(("sample-a", "shared#1"),)),
        _row(*cells, feature_id="ALN000002", candidates=(("sample-a", "shared#1"),)),
    )

    with pytest.raises(ValueError, match="source peak cannot contribute"):
        write_alignment_matrix_tsv(tmp_path / "matrix.tsv", matrix)


def test_write_alignment_review_tsv_reports_archived_row_route(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    matrix = _matrix(
        ("sample-a",),
        _row(
            owner_cell("backfilled", area=200.0),
            has_anchor=False,
            destination="archive",
        ),
    )

    (row,) = _read_tsv(write_alignment_review_tsv(tmp_path / "review.tsv", matrix))

    assert row["include_in_primary_matrix"] == "FALSE"
    assert row["identity_reason"] == "test_route"
    assert row["accepted_cell_count"] == "1"
    assert row["accepted_backfill_count"] == "1"
    assert row["row_flags"] == (
        "finalized_route:archive;cross_sample_owner_arithmetic_mean"
    )


def test_debug_tsvs_write_cells_and_status_matrix(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_cells_tsv,
        write_alignment_status_matrix_tsv,
    )

    samples = ("sample-a", "sample-b", "sample-c", "sample-d", "sample-e")
    matrix = _matrix(
        samples,
        _row(
            owner_cell(area=10.0),
            owner_cell(None, no_write="not_searched"),
            owner_cell(None, no_write="rejected_observation"),
            owner_cell("backfilled", area=8.0),
            owner_cell(area=None),
            candidates=(("sample-a", "sample-a#1"),),
        ),
    )

    cells = _read_tsv(write_alignment_cells_tsv(tmp_path / "cells.tsv", matrix))
    (status,) = _read_tsv(
        write_alignment_status_matrix_tsv(tmp_path / "status.tsv", matrix)
    )

    assert list(cells[0]) == list(ALIGNMENT_CELLS_COLUMNS)
    assert [row["status"] for row in cells] == [
        "detected",
        "not_searched",
        "rejected_observation",
        "backfilled",
        "detected",
    ]
    assert [status[sample] for sample in samples] == [row["status"] for row in cells]
    assert cells[0]["area"] == "10"
    assert cells[0]["primary_matrix_area"] == "10"
    assert cells[0]["primary_matrix_area_source"] == "gaussian15_positive_asls_residual"
    assert cells[0]["primary_matrix_area_reason"] == ""
    assert cells[0]["source_candidate_id"] == "sample-a#1"
    assert cells[1]["reason"] == "not_searched"
    assert cells[1]["primary_matrix_area_source"] == ""
    assert cells[4]["area"] == ""
    assert cells[4]["reason"] == "not_quantifiable"
    assert cells[4]["primary_matrix_area_reason"] == "missing_ms1_morphology_area"
    assert not any(column.startswith("region_") for column in cells[0])


def test_backfill_cell_evidence_tsv_is_compact_and_backfill_scoped(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.alignment.tsv_writer import (
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        write_alignment_backfill_cell_evidence_tsv,
    )

    matrix = _matrix(
        ("sample-a", "sample-b", "sample-c"),
        _row(
            owner_cell(area=10.0),
            owner_cell("backfilled", area=8.0),
            owner_cell(None, no_write="not_searched"),
            candidates=(("sample-a", "sample-a#1"),),
        ),
        _row(
            owner_cell(area=20.0),
            owner_cell(None),
            owner_cell(None),
            feature_id="ALN000002",
        ),
    )

    rows = _read_tsv(
        write_alignment_backfill_cell_evidence_tsv(
            tmp_path / "alignment_backfill_cell_evidence.tsv", matrix
        )
    )

    assert list(rows[0]) == list(ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS)
    assert [(row["feature_id"], row["sample_stem"], row["status"]) for row in rows] == [
        ("ALN000001", "sample-a", "detected"),
        ("ALN000001", "sample-b", "backfilled"),
    ]
    assert rows[0]["source_candidate_id"] == "sample-a#1"
    assert rows[1]["primary_matrix_area"] == "8"
    assert rows[1]["include_in_primary_matrix"] == "TRUE"
    assert rows[1]["identity_decision"] == "finalized_matrix"


def test_tsv_writers_escape_formula_like_text(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = _matrix(
        ("=sample", "+sample-2"),
        _row(
            owner_cell(area=10.0),
            owner_cell(area=20.0),
            feature_id="=cluster",
            neutral_loss_tag="+NL",
            candidates=(("=sample", "@candidate"), ("+sample-2", "@candidate-2")),
        ),
    )

    review = _read_tsv(write_alignment_review_tsv(tmp_path / "review.tsv", matrix))
    matrix_rows = _read_tsv(write_alignment_matrix_tsv(tmp_path / "matrix.tsv", matrix))

    assert review[0]["feature_id"] == "'=cluster"
    assert review[0]["neutral_loss_tag"] == "'+NL"
    assert "'=sample" in matrix_rows[0]


def test_private_write_tsv_preserves_lf_formula_and_format_contract(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.alignment import tsv_writer

    path = tsv_writer._write_tsv(
        tmp_path / "nested" / "alignment.tsv",
        ("=formula_header", "float_value", "blank"),
        [{"=formula_header": "=formula_value", "float_value": 100.0}],
    )

    assert path.read_text(encoding="utf-8") == (
        "'=formula_header\tfloat_value\tblank\n'=formula_value\t100\t\n"
    )

    empty_path = tsv_writer._write_tsv(
        tmp_path / "empty" / "alignment.tsv",
        ("family_id", "value"),
        [],
    )
    assert empty_path.read_text(encoding="utf-8") == "family_id\tvalue\n"


def test_private_write_tsv_accepts_generator_rows(tmp_path: Path) -> None:
    from xic_extractor.output.alignment import tsv_writer

    rows = ({"name": f"row-{index}", "value": index} for index in range(3))

    path = tsv_writer._write_tsv(tmp_path / "streamed.tsv", ("name", "value"), rows)

    assert path.read_bytes() == b"name\tvalue\nrow-0\t0\nrow-1\t1\nrow-2\t2\n"


def test_advisory_signal_quality_is_sample_scoped_and_matrix_unchanged(tmp_path):
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_cells_tsv,
        write_alignment_matrix_tsv,
    )
    from xic_extractor.peak_detection.signal_quality import SignalQuality

    def cell(area: float, signal_to_noise: float | None) -> ProductionCellDecision:
        integration = _integration(raw_area=area, asls_area=area)
        if signal_to_noise is not None:
            integration = replace(
                integration,
                signal_quality=SignalQuality(
                    "available",
                    noise_sigma=10.0,
                    signal_to_noise=signal_to_noise,
                    low_signal_to_noise=signal_to_noise < 10 / 1.4826,
                ),
            )
        return owner_cell(area=area, integration=integration)

    before = _matrix(("S1", "S2"), _row(cell(1000.0, None), cell(2000.0, None)))
    after = _matrix(("S1", "S2"), _row(cell(1000.0, 20.0), cell(2000.0, 2.0)))
    first = write_alignment_matrix_tsv(tmp_path / "before.tsv", before)
    second = write_alignment_matrix_tsv(tmp_path / "after.tsv", after)
    assert first.read_bytes() == second.read_bytes()
    rows = _read_tsv(write_alignment_cells_tsv(tmp_path / "review-cells.tsv", after))
    assert [row["sample_stem"] for row in rows] == ["S1", "S2"]
    assert [row["low_signal_to_noise"] for row in rows] == ["FALSE", "TRUE"]
    assert [row["signal_to_noise"] for row in rows] == ["20", "2"]


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _matrix(
    sample_order: tuple[str, ...], *rows: FinalizedUntargetMatrixRow
) -> AlignmentMatrix:
    return AlignmentMatrix(clusters=rows, sample_order=sample_order)


def _row(
    *cells: ProductionCellDecision,
    feature_id: str = "ALN000001",
    candidates: tuple[tuple[str, str], ...] = (),
    center_mz: float = 500.123,
    center_rt: float = 8.49,
    neutral_loss_tag: str = "DNA_dR",
    has_anchor: bool = True,
    member_count: int = 0,
    destination: Literal["matrix", "archive"] = "matrix",
) -> FinalizedUntargetMatrixRow:
    return FinalizedUntargetMatrixRow(
        feature_id=feature_id,
        group_hypothesis_id=feature_id,
        destination=destination,
        family_center_mz=center_mz,
        family_center_rt=center_rt,
        physical_peak_keys=tuple(
            f"{feature_id}#peak-{index}" for index in range(member_count)
        ),
        family_product_mz=384.076,
        family_observed_neutral_loss_da=116.047,
        neutral_loss_tag=neutral_loss_tag,
        has_anchor=has_anchor,
        route_reason="test_route",
        cells=cells,
        source_candidate_ids=candidates,
    )


def _two_sample_matrix(*, center_mz: float = 500.123) -> AlignmentMatrix:
    return _matrix(
        ("sample-a", "sample-b"),
        _row(owner_cell(area=100.0), owner_cell(area=110.0), center_mz=center_mz),
    )


def _display_collision_matrix() -> AlignmentMatrix:
    return _matrix(
        ("sample-a", "sample-b"),
        _row(owner_cell(area=100.0), owner_cell(area=110.0), center_mz=500.123441),
        _row(
            owner_cell(area=120.0),
            owner_cell(area=130.0),
            feature_id="ALN000002",
            center_mz=500.123449,
        ),
    )


def _integration(
    *,
    raw_area: float,
    asls_area: float | None,
    baseline_type: str = "asls",
    morphology_area: float | None = None,
    morphology_area_source: str = "gaussian15_positive_asls_residual",
) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=8.4,
        rt_apex_min=8.49,
        rt_right_min=8.6,
        raw_apex_rt_min=8.49,
        rt_width_min=0.2,
        height_raw=100.0,
        height_smoothed=100.0,
        area_raw_counts_seconds=raw_area,
        area_baseline_corrected=asls_area,
        baseline_type=baseline_type,
        boundary_sources=("test",),
        area_ms1_morphology=morphology_area,
        ms1_morphology_area_source=(
            morphology_area_source if morphology_area is not None else ""
        ),
    )
