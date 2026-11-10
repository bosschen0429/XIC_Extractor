import csv
import math
from pathlib import Path
from typing import Literal, TypeGuard

import pytest

from tests.alignment_pipeline_helpers import finalized_writer_decisions
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.production_decisions import (
    ProductionDecisionSet,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
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
    "unchecked_count",
    "duplicate_assigned_count",
    "ambiguous_ms1_owner_count",
    "present_rate",
    "identity_decision",
    "identity_reason",
    "accepted_cell_count",
    "accepted_backfill_count",
    "include_in_primary_matrix",
    "row_flags",
    "membership_risk_flags",
    "membership_risks_json",
    "warning",
    "reason",
]


def test_write_alignment_review_tsv_columns_counts_rates_and_reason(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(has_anchor=True, member_count=1),),
        cells=(
            _cell("sample-a", "detected", area=10.0, candidate_id="sample-a#1"),
            _cell("sample-b", "backfilled", area=20.0),
            _cell("sample-c", "absent"),
            _cell("sample-d", "detected", area=30.0, candidate_id="sample-d#1"),
        ),
        sample_order=("sample-a", "sample-b", "sample-c", "sample-d"),
    )

    path = write_alignment_review_tsv(
        tmp_path / "alignment_review.tsv",
        matrix,
        production_decisions=_production_decisions(matrix),
    )
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
        "membership_risks_json": "[]",
        "feature_id": "ALN000001",
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": "500.123",
        "family_center_rt": "8.49",
        "family_product_mz": "384.076",
        "family_observed_neutral_loss_da": "116.047",
        "physical_peak_keys": "ALN000001#peak-0",
        "detected_count": "2",
        "unchecked_count": "0",
        "duplicate_assigned_count": "0",
        "ambiguous_ms1_owner_count": "0",
        "present_rate": "0.75",
        "identity_decision": "finalized_matrix",
        "identity_reason": "test_route",
        "accepted_cell_count": "3",
        "accepted_backfill_count": "1",
        "include_in_primary_matrix": "TRUE",
        "row_flags": "finalized_route:matrix",
        "warning": "",
        "reason": "anchor family; 3/4 present; 1 MS1 backfilled",
    }


def test_write_alignment_review_tsv_warning_precedence(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(cluster_id="ALN000001", has_anchor=False),
            _cluster(cluster_id="ALN000002", has_anchor=True),
            _cluster(cluster_id="ALN000003", has_anchor=True),
            _cluster(cluster_id="ALN000004", has_anchor=True),
            _cluster(
                cluster_id="ALN000005",
                has_anchor=True,
            ),
            _cluster(
                cluster_id="ALN000006",
                has_anchor=True,
            ),
        ),
        cells=(
            _cell("sample-a", "detected", cluster_id="ALN000001", area=1.0),
            _cell("sample-b", "unchecked", cluster_id="ALN000001"),
            _cell("sample-a", "unchecked", cluster_id="ALN000002"),
            _cell("sample-b", "unchecked", cluster_id="ALN000002"),
            _cell("sample-a", "backfilled", cluster_id="ALN000003", area=1.0),
            _cell("sample-b", "absent", cluster_id="ALN000003"),
            _cell("sample-a", "detected", cluster_id="ALN000004", area=1.0),
            _cell("sample-b", "absent", cluster_id="ALN000004"),
            _cell("sample-a", "detected", cluster_id="ALN000005", area=1.0),
            _cell("sample-b", "backfilled", cluster_id="ALN000005", area=1.0),
            _cell("sample-a", "backfilled", cluster_id="ALN000006", area=1.0),
            _cell("sample-b", "absent", cluster_id="ALN000006"),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    rows = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert [row["warning"] for row in rows] == [
        "no_anchor",
        "high_unchecked",
        "high_backfill_dependency",
        "",
        "",
        "high_backfill_dependency",
    ]


def test_review_uses_physical_members_and_owner_counts_not_evidence_text(tmp_path):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(member_count=3),),
        cells=(
            _cell("direct", "detected", area=10.0),
            _cell("backfill-a", "backfilled", area=20.0),
            _cell("backfill-b", "backfilled", area=30.0),
        ),
        sample_order=("direct", "backfill-a", "backfill-b"),
    )
    (row,) = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )
    assert row["physical_peak_keys"] == (
        "ALN000001#peak-0;ALN000001#peak-1;ALN000001#peak-2"
    )
    assert (
        not {"event_cluster_count", "event_cluster_ids", "event_member_count"}
        & row.keys()
    )
    assert row["warning"] == ""
    assert row["reason"] == "anchor family; 3/3 present; 2 MS1 backfilled"


def test_write_alignment_review_tsv_reports_duplicate_assigned_cells(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(has_anchor=False),),
        cells=(
            _cell("sample-a", "duplicate_assigned"),
            _cell("sample-b", "duplicate_assigned"),
            _cell("sample-c", "unchecked"),
        ),
        sample_order=("sample-a", "sample-b", "sample-c"),
    )

    rows = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert rows[0]["reason"] == (
        "no anchor; 0/3 present; 0 MS1 backfilled; 2 duplicate-assigned"
    )


def test_write_alignment_review_tsv_counts_duplicate_assigned_separately(
    tmp_path: Path,
):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(has_anchor=True),),
        cells=(
            _cell("sample-a", "detected", area=100.0),
            _cell("sample-b", "backfilled", area=90.0),
            _cell("sample-c", "duplicate_assigned"),
            _cell("sample-d", "absent"),
        ),
        sample_order=("sample-a", "sample-b", "sample-c", "sample-d"),
    )

    rows = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert rows[0]["detected_count"] == "1"
    assert rows[0]["unchecked_count"] == "0"
    assert rows[0]["duplicate_assigned_count"] == "1"
    assert rows[0]["present_rate"] == "0.5"
    assert "1 duplicate-assigned" in rows[0]["reason"]


def test_write_alignment_review_tsv_reports_ambiguous_ms1_owner_cells(
    tmp_path: Path,
):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(has_anchor=True),),
        cells=(
            _cell("sample-a", "detected", area=100.0),
            _cell("sample-b", "ambiguous_ms1_owner"),
            _cell("sample-c", "absent"),
        ),
        sample_order=("sample-a", "sample-b", "sample-c"),
    )

    rows = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert rows[0]["detected_count"] == "1"
    assert rows[0]["present_rate"] == "0.333333"
    assert "1 ambiguous MS1 owner" in rows[0]["reason"]


def test_write_alignment_matrix_tsv_blanks_missing_and_invalid_areas(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=(
            _cell("detected-positive", "detected", area=1234.567),
            _cell("detected-positive-2", "detected", area=2345.0),
            _cell("rescued-positive", "backfilled", area=25.0),
            _cell("absent-positive", "absent", area=30.0),
            _cell("unchecked-positive", "unchecked", area=40.0),
            _cell("none", "detected", area=None),
            _cell("zero", "detected", area=0.0),
            _cell("negative", "detected", area=-1.0),
            _cell("nan", "detected", area=math.nan),
        ),
        sample_order=(
            "detected-positive",
            "detected-positive-2",
            "rescued-positive",
            "absent-positive",
            "unchecked-positive",
            "none",
            "zero",
            "negative",
            "nan",
        ),
    )

    rows = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert list(rows[0]) == [
        "Mz",
        "RT",
        "detected-positive",
        "detected-positive-2",
        "rescued-positive",
        "absent-positive",
        "unchecked-positive",
        "none",
        "zero",
        "negative",
        "nan",
    ]
    assert rows[0]["Mz"] == "500.1230"
    assert rows[0]["RT"] == "8.49"
    assert rows[0]["detected-positive"] == "1.23457e+03"
    assert rows[0]["detected-positive-2"] == "2.34500e+03"
    assert rows[0]["rescued-positive"] == "2.50000e+01"
    assert rows[0]["absent-positive"] == ""
    assert rows[0]["unchecked-positive"] == ""
    assert rows[0]["none"] == ""
    assert rows[0]["zero"] == ""
    assert rows[0]["negative"] == ""
    assert rows[0]["nan"] == ""


def test_write_alignment_matrix_tsv_uses_stable_numeric_text_without_value_drift(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=(
            _cell("small", "detected", area=0.00123456),
            _cell("large", "detected", area=123456789.0),
            _cell("blank", "absent", area=42.0),
        ),
        sample_order=("small", "large", "blank"),
    )

    row = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )[0]

    assert row["Mz"] == "500.1230"
    assert row["RT"] == "8.49"
    assert row["small"] == "1.23456e-03"
    assert row["large"] == "1.23457e+08"
    assert row["blank"] == ""
    assert float(row["small"]) == float(f"{0.00123456:.6g}")
    assert float(row["large"]) == float(f"{123456789.0:.6g}")


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

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                center_mz=500.123456,
            ),
        ),
        cells=(
            _cell("sample-a", "detected", area=100.0),
            _cell("sample-b", "detected", area=110.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    matrix_row = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "alignment_matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )[0]
    identity_row = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "alignment_review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )[0]
    review_row = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "alignment_review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )[0]

    assert matrix_row["Mz"] == "500.1235"
    assert identity_row["matrix_mz"] == "500.123456"
    assert review_row["family_center_mz"] == "500.123456"
    matrix_review_rows(
        matrix_header=tuple(matrix_row),
        matrix_rows=(matrix_row,),
        review_columns=tuple(identity_row),
        review_rows=(identity_row,),
    )


def test_alignment_matrix_mz_format_is_display_only_for_small_mz(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                center_mz=5.123456,
            ),
        ),
        cells=(
            _cell("sample-a", "detected", area=100.0),
            _cell("sample-b", "detected", area=110.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    matrix_row = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "alignment_matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )[0]
    identity_row = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "alignment_review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
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
        write_alignment_matrix_tsv(
            tmp_path / "alignment_matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )
    identity_rows = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "alignment_review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
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
        write_alignment_matrix_tsv(
            tmp_path / "alignment_matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )
    identity_rows = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "alignment_review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
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

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                center_rt=8.49123456,
            ),
        ),
        cells=(
            _cell("+sample", "detected", area=100.0, apex_rt=8.49123456),
            _cell("safe", "detected", area=110.0, apex_rt=8.49123456),
        ),
        sample_order=("+sample", "safe"),
    )
    matrix_path = write_alignment_matrix_tsv(
        tmp_path / "matrix.tsv",
        matrix,
        production_decisions=_production_decisions(matrix),
    )
    identity_path = write_alignment_review_tsv(
        tmp_path / "identity.tsv",
        matrix,
        production_decisions=_production_decisions(matrix),
    )

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

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                center_mz=center_mz,
            ),
        ),
        cells=(
            _cell("sample-a", "detected", area=100.0),
            _cell("sample-b", "detected", area=110.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    row = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )[0]

    assert row["Mz"] == ""


def test_write_alignment_matrix_tsv_prefers_ms1_morphology_area(
    tmp_path: Path,
):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=(
            _cell(
                "sample-a",
                "detected",
                area=10.0,
                selected_integration=_integration(
                    raw_area=77.7,
                    asls_area=55.5,
                    morphology_area=66.6,
                ),
            ),
            _cell("sample-b", "detected", area=20.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    rows = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert list(rows[0]) == [
        "Mz",
        "RT",
        "sample-a",
        "sample-b",
    ]
    assert rows[0]["sample-a"] == "6.66000e+01"
    assert rows[0]["sample-b"] == "2.00000e+01"


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
    matrix = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=(
            _cell(
                "sample-a",
                "detected",
                area=66.6,
                selected_integration=integration,
            ),
            _cell("sample-b", "detected", area=77.7),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    rows = _read_tsv(write_alignment_cells_tsv(tmp_path / "cells.tsv", matrix))

    assert rows[0]["area"] == "66.6"
    assert rows[0]["primary_matrix_area"] == "66.6"
    assert rows[0]["primary_matrix_area_source"] == area_source
    assert rows[0]["primary_matrix_area_reason"] == ""
    values = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )
    assert values[0]["sample-a"] == "6.66000e+01"
    assert values[0]["sample-b"] == "7.77000e+01"


def test_write_alignment_matrix_tsv_blanks_duplicate_assigned_cells(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=(
            _cell(
                "sample-a",
                "duplicate_assigned",
                area=100.0,
                trace_quality="assigned_duplicate",
            ),
            _cell("sample-b", "detected", area=200.0),
            _cell("sample-c", "detected", area=300.0),
        ),
        sample_order=("sample-a", "sample-b", "sample-c"),
    )

    rows = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert rows[0]["sample-a"] == ""
    assert rows[0]["sample-b"] == "2.00000e+02"
    assert rows[0]["sample-c"] == "3.00000e+02"


def test_write_alignment_matrix_tsv_blanks_ambiguous_ms1_owner_cells(
    tmp_path: Path,
):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=(
            _cell(
                "sample-a",
                "ambiguous_ms1_owner",
                area=100.0,
                trace_quality="ambiguous_ms1_owner",
            ),
            _cell("sample-b", "detected", area=200.0),
            _cell("sample-c", "detected", area=300.0),
        ),
        sample_order=("sample-a", "sample-b", "sample-c"),
    )

    rows = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert rows[0]["sample-a"] == ""
    assert rows[0]["sample-b"] == "2.00000e+02"
    assert rows[0]["sample-c"] == "3.00000e+02"


def test_write_alignment_matrix_tsv_excludes_archived_rows(
    tmp_path: Path,
):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                cluster_id="ALN000001",
            ),
            _cluster(
                cluster_id="ALN000002",
                has_anchor=False,
                destination="archive",
            ),
            _cluster(
                cluster_id="ALN000003",
                destination="archive",
            ),
        ),
        cells=(
            _cell("sample-a", "detected", cluster_id="ALN000001", area=100.0),
            _cell("sample-b", "detected", cluster_id="ALN000001", area=110.0),
            _cell("sample-a", "backfilled", cluster_id="ALN000002", area=200.0),
            _cell(
                "sample-a",
                "duplicate_assigned",
                cluster_id="ALN000003",
                area=300.0,
            ),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    rows = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

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

    matrix = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=(
            _cell("sample-a", "detected", area=100.0),
            _cell("sample-b", "detected", area=110.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    matrix_rows = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )
    identity_rows = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "alignment_review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
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

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                cluster_id="ALN000001",
            ),
            _cluster(
                cluster_id="ALN000002",
            ),
        ),
        cells=(
            _cell(
                "sample-a",
                "detected",
                cluster_id="ALN000001",
                area=100.0,
                candidate_id="shared#1",
            ),
            _cell("sample-b", "detected", cluster_id="ALN000001", area=110.0),
            _cell(
                "sample-a",
                "detected",
                cluster_id="ALN000002",
                area=120.0,
                candidate_id="shared#1",
            ),
            _cell("sample-b", "detected", cluster_id="ALN000002", area=130.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    with pytest.raises(ValueError, match="source peak cannot contribute"):
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )


def test_write_alignment_review_tsv_includes_production_decision_columns(
    tmp_path: Path,
):
    from xic_extractor.output.alignment.tsv_writer import write_alignment_review_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(has_anchor=False, destination="archive"),),
        cells=(_cell("sample-a", "backfilled", area=200.0),),
        sample_order=("sample-a",),
    )

    rows = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert rows[0]["identity_decision"] == "finalized_archive"
    assert rows[0]["identity_reason"] == "test_route"
    assert rows[0]["accepted_cell_count"] == "1"
    assert rows[0]["accepted_backfill_count"] == "1"
    assert rows[0]["include_in_primary_matrix"] == "FALSE"
    assert rows[0]["row_flags"] == "finalized_route:archive"


def test_write_alignment_status_matrix_tsv_preserves_duplicate_assigned(
    tmp_path: Path,
):
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_status_matrix_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=(
            _cell(
                "sample-a",
                "duplicate_assigned",
                area=None,
                trace_quality="assigned_duplicate",
            ),
        ),
        sample_order=("sample-a",),
    )

    rows = _read_tsv(write_alignment_status_matrix_tsv(tmp_path / "status.tsv", matrix))

    assert rows[0]["sample-a"] == "duplicate_assigned"


def test_debug_tsvs_write_cells_and_status_matrix(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_cells_tsv,
        write_alignment_status_matrix_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=(
            _cell(
                "sample-a",
                "detected",
                area=10.0,
                candidate_id="sample-a#1",
            ),
            _cell("sample-b", "unchecked"),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    cells = _read_tsv(write_alignment_cells_tsv(tmp_path / "cells.tsv", matrix))
    status = _read_tsv(
        write_alignment_status_matrix_tsv(tmp_path / "status.tsv", matrix)
    )

    assert list(cells[0]) == list(ALIGNMENT_CELLS_COLUMNS)
    assert cells[0]["status"] == "detected"
    assert cells[0]["area"] == "10"
    assert cells[0]["primary_matrix_area"] == "10"
    assert cells[0]["primary_matrix_area_source"] == "gaussian15_positive_asls_residual"
    assert cells[0]["primary_matrix_area_reason"] == ""
    assert cells[0]["source_candidate_id"] == "sample-a#1"
    assert not any(column.startswith("region_") for column in cells[0])
    assert set(cells[0]).isdisjoint(
        {
            "backfill_ms2_trigger_scan_count",
            "backfill_strict_nl_scan_count",
            "backfill_ms2_trace_strength",
            "backfill_dda_missing_nl_policy_status",
            "backfill_family_ms2_required_tag_status",
            "backfill_evidence_reason",
        }
    )
    assert status[0]["sample-a"] == "detected"
    assert status[0]["sample-b"] == "unchecked"


def test_backfill_cell_evidence_tsv_is_compact_and_backfill_scoped(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.backfill.evidence_contract import (
        BackfillEvidenceOwnerBindings,
        EvidenceClaimState,
    )
    from xic_extractor.diagnostics.backfill_evidence.tsv import (
        lookup_backfill_evidence_tsv,
    )
    from xic_extractor.output.alignment.tsv_writer import (
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        write_alignment_backfill_cell_evidence_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(cluster_id="ALN000001"),
            _cluster(cluster_id="ALN000002"),
        ),
        cells=(
            _cell(
                "sample-a",
                "detected",
                cluster_id="ALN000001",
                area=10.0,
                candidate_id="sample-a#1",
            ),
            _cell("sample-b", "backfilled", cluster_id="ALN000001", area=8.0),
            _cell("sample-c", "unchecked", cluster_id="ALN000001"),
            _cell(
                "sample-a",
                "detected",
                cluster_id="ALN000002",
                area=20.0,
                candidate_id="sample-a#2",
            ),
        ),
        sample_order=("sample-a", "sample-b", "sample-c"),
    )

    evidence_path = write_alignment_backfill_cell_evidence_tsv(
        tmp_path / "alignment_backfill_cell_evidence.tsv",
        matrix,
        production_decisions=_production_decisions(matrix),
    )
    rows = _read_tsv(evidence_path)

    assert list(rows[0]) == list(ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS)
    assert {row["feature_id"] for row in rows} == {"ALN000001"}
    assert {row["sample_stem"] for row in rows} == {"sample-a", "sample-b"}
    backfilled = next(row for row in rows if row["status"] == "backfilled")
    assert backfilled["production_cell_status"] in {
        "accepted_backfill",
        "review_backfill",
    }
    assert backfilled["primary_matrix_area"] == "8"
    assert "region_candidate_count" not in rows[0]
    assert "region_shadow_verdict" not in rows[0]
    assert "backfill_evidence_reason" not in rows[0]

    result = lookup_backfill_evidence_tsv(
        evidence_path,
        feature_id="ALN000001",
        sample_stem="sample-a",
        owner_bindings=BackfillEvidenceOwnerBindings(
            known_feature_ids=frozenset({"ALN000001"}),
            known_sample_stems=frozenset({"sample-a", "sample-b"}),
            candidate_samples={"sample-a#1": "sample-a"},
        ),
    )
    assert len(result.records) == 1
    assert result.records[0].feature_id == "ALN000001"
    assert result.claim("record_content").state is EvidenceClaimState.STRUCTURE_BOUND
    assert result.claim("cross_artifact_binding").state is (
        EvidenceClaimState.OWNER_REHYDRATED
    )


def test_alignment_cells_tsv_has_no_region_audit_columns(tmp_path: Path) -> None:
    from xic_extractor.output.alignment.tsv_writer import write_alignment_cells_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=(
            _cell(
                "sample-a",
                "detected",
                area=10.0,
                candidate_id="sample-a#1",
            ),
        ),
        sample_order=("sample-a",),
    )

    cells = _read_tsv(write_alignment_cells_tsv(tmp_path / "cells.tsv", matrix))
    region_columns = [column for column in cells[0] if column.startswith("region_")]

    assert cells[0]["status"] == "detected"
    assert region_columns == []


def test_write_alignment_owner_backfill_seed_audit_tsv_is_sidecar(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.alignment.tsv_writer import (
        ALIGNMENT_BACKFILL_SEED_AUDIT_COLUMNS,
        ALIGNMENT_CELLS_COLUMNS,
        write_alignment_backfill_seed_audit_tsv,
        write_alignment_cells_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=(
            _cell(
                "sample-a",
                "backfilled",
                area=10.0,
                backfill_seed=True,
            ),
            _cell("sample-b", "backfilled", area=8.0),
            _cell("sample-c", "detected", area=9.0, backfill_seed=True),
        ),
        sample_order=("sample-a", "sample-b", "sample-c"),
    )

    cells = _read_tsv(write_alignment_cells_tsv(tmp_path / "cells.tsv", matrix))
    audit = _read_tsv(
        write_alignment_backfill_seed_audit_tsv(
            tmp_path / "alignment_owner_backfill_seed_audit.tsv",
            matrix,
        )
    )

    assert list(cells[0]) == list(ALIGNMENT_CELLS_COLUMNS)
    assert list(audit[0]) == list(ALIGNMENT_BACKFILL_SEED_AUDIT_COLUMNS)
    assert len(audit) == 1
    assert audit[0]["feature_id"] == "ALN000001"
    assert audit[0]["sample_stem"] == "sample-a"
    assert audit[0]["status"] == "backfilled"
    assert audit[0]["neutral_loss_tag"] == "DNA_dR"
    assert audit[0]["backfill_seed_mz"] == "500.222"
    assert audit[0]["backfill_seed_rt"] == "8.55"
    assert audit[0]["backfill_request_rt_min"] == "5.55"
    assert audit[0]["backfill_request_rt_max"] == "11.55"
    assert audit[0]["backfill_request_ppm"] == "20"
    assert audit[0]["backfill_apex_delta_sec"] == "-3.6"


def test_tsv_writers_escape_formula_like_text(tmp_path: Path):
    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                cluster_id="=cluster",
                neutral_loss_tag="+NL",
            ),
        ),
        cells=(
            _cell(
                "=sample",
                "detected",
                cluster_id="=cluster",
                area=10.0,
                candidate_id="@candidate",
            ),
            _cell(
                "+sample-2",
                "detected",
                cluster_id="=cluster",
                area=20.0,
                candidate_id="@candidate-2",
            ),
        ),
        sample_order=("=sample", "+sample-2"),
    )

    review = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )
    matrix_rows = _read_tsv(
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

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


def _production_decisions(matrix: AlignmentMatrix) -> ProductionDecisionSet:
    return finalized_writer_decisions(matrix)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _cluster(
    *,
    cluster_id: str = "ALN000001",
    center_mz: float = 500.123,
    center_rt: float = 8.49,
    neutral_loss_tag: str = "DNA_dR",
    has_anchor: bool = True,
    member_count: int = 0,
    destination: Literal["matrix", "archive"] = "matrix",
) -> FinalizedUntargetMatrixRow:
    return FinalizedUntargetMatrixRow(
        feature_id=cluster_id,
        group_hypothesis_id=cluster_id,
        destination=destination,
        family_center_mz=center_mz,
        family_center_rt=center_rt,
        physical_peak_keys=tuple(
            f"{cluster_id}#peak-{index}" for index in range(member_count)
        ),
        family_product_mz=384.076,
        family_observed_neutral_loss_da=116.047,
        neutral_loss_tag=neutral_loss_tag,
        has_anchor=has_anchor,
    )


def _display_collision_matrix() -> AlignmentMatrix:
    return AlignmentMatrix(
        clusters=(
            _cluster(
                cluster_id="ALN000001",
                center_mz=500.123441,
            ),
            _cluster(
                cluster_id="ALN000002",
                center_mz=500.123449,
            ),
        ),
        cells=(
            _cell("sample-a", "detected", cluster_id="ALN000001", area=100.0),
            _cell("sample-b", "detected", cluster_id="ALN000001", area=110.0),
            _cell("sample-a", "detected", cluster_id="ALN000002", area=120.0),
            _cell("sample-b", "detected", cluster_id="ALN000002", area=130.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )


def _cell(
    sample_stem: str,
    status,
    *,
    cluster_id: str = "ALN000001",
    area: float | None = None,
    candidate_id: str | None = None,
    apex_rt: float = 8.49,
    trace_quality: str | None = None,
    backfill_seed: bool = False,
    selected_integration: IntegrationResult | None = None,
) -> AlignedCell:
    if (
        selected_integration is None
        and status in {"detected", "backfilled"}
        and _positive_area(area)
    ):
        selected_integration = _integration(
            raw_area=area,
            asls_area=area,
            morphology_area=area,
        )
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=cluster_id,
        status=status,
        area=area,
        apex_rt=apex_rt if area is not None else None,
        height=100.0 if area is not None else None,
        peak_start_rt=8.4 if area is not None else None,
        peak_end_rt=8.6 if area is not None else None,
        rt_delta_sec=0.0 if area is not None else None,
        trace_quality=(
            trace_quality
            if trace_quality is not None
            else ("clean" if area is not None else "unchecked")
        ),
        scan_support_score=0.8 if area is not None else None,
        source_candidate_id=candidate_id,
        reason="cell reason",
        selected_integration=selected_integration,
        backfill_seed_mz=500.222 if backfill_seed else None,
        backfill_seed_rt=8.55 if backfill_seed else None,
        backfill_request_rt_min=5.55 if backfill_seed else None,
        backfill_request_rt_max=11.55 if backfill_seed else None,
        backfill_request_ppm=20.0 if backfill_seed else None,
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


def _positive_area(value: float | None) -> TypeGuard[float]:
    return (
        value is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def test_advisory_signal_quality_is_sample_scoped_and_matrix_unchanged(tmp_path):
    from dataclasses import replace

    from xic_extractor.output.alignment.tsv_writer import (
        write_alignment_cells_tsv,
        write_alignment_matrix_tsv,
    )
    from xic_extractor.peak_detection.signal_quality import SignalQuality

    cells = tuple(
        _cell(sid, "detected", area=value)
        for sid, value in (("S1", 1000.0), ("S2", 2000.0))
    )
    before = AlignmentMatrix(
        clusters=(_cluster(),),
        cells=cells,
        sample_order=("S1", "S2"),
    )
    after = replace(
        before,
        cells=tuple(
            replace(
                cell,
                selected_integration=replace(
                    cell.selected_integration,
                    signal_quality=SignalQuality(
                        "available",
                        noise_sigma=10.0,
                        signal_to_noise=ratio,
                        low_signal_to_noise=ratio < 10 / 1.4826,
                    ),
                ),
            )
            for cell, ratio in zip(cells, (20.0, 2.0), strict=True)
        ),
    )
    first = write_alignment_matrix_tsv(
        tmp_path / "before.tsv",
        before,
        production_decisions=_production_decisions(before),
    )
    second = write_alignment_matrix_tsv(
        tmp_path / "after.tsv",
        after,
        production_decisions=_production_decisions(after),
    )
    assert first.read_bytes() == second.read_bytes()
    rows = _read_tsv(write_alignment_cells_tsv(tmp_path / "review-cells.tsv", after))
    assert [row["sample_stem"] for row in rows] == ["S1", "S2"]
    assert [row["low_signal_to_noise"] for row in rows] == ["FALSE", "TRUE"]
    assert [row["signal_to_noise"] for row in rows] == ["20", "2"]
