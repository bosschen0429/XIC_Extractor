import csv
import math
from pathlib import Path
from types import SimpleNamespace
from typing import TypeGuard

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.models import AlignmentCluster
from xic_extractor.alignment.owner_group_delivery import GROUP_REVIEW_PROJECTION_COLUMNS
from xic_extractor.alignment.production_decisions import (
    ProductionDecisionSet,
    build_production_decisions,
)
from xic_extractor.alignment.tsv_writer import ALIGNMENT_CELLS_COLUMNS
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.integration_audit import CellIntegrationAuditSummary

REVIEW_COLUMNS = [
    "feature_family_id",
    *GROUP_REVIEW_PROJECTION_COLUMNS,
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "family_product_mz",
    "family_observed_neutral_loss_da",
    "has_anchor",
    "event_cluster_count",
    "event_cluster_ids",
    "event_member_count",
    "detected_count",
    "absent_count",
    "unchecked_count",
    "duplicate_assigned_count",
    "ambiguous_ms1_owner_count",
    "present_rate",
    "identity_decision",
    "identity_confidence",
    "primary_evidence",
    "identity_reason",
    "quantifiable_detected_count",
    "quantifiable_backfill_count",
    "accepted_cell_count",
    "accepted_backfill_count",
    "review_backfill_count",
    "include_in_primary_matrix",
    "row_flags",
    "representative_samples",
    "family_evidence",
    "warning",
    "reason",
]


def test_write_alignment_review_tsv_columns_counts_rates_and_reason(tmp_path: Path):
    from xic_extractor.alignment.tsv_writer import write_alignment_review_tsv

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
    assert rows[0] == {
        "feature_family_id": "ALN000001",
        "group_hypothesis_id": "ALN000001",
        "public_family_id": "ALN000001",
        "group_construction_role": "successor_constructor",
        "group_delivery_role": "owner_aligned_feature_compatibility_facade",
        "group_membership_source": "owner_aligned_feature_successor_projection",
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": "500.123",
        "family_center_rt": "8.49",
        "family_product_mz": "384.076",
        "family_observed_neutral_loss_da": "116.047",
        "has_anchor": "TRUE",
        "event_cluster_count": "1",
        "event_cluster_ids": "ALN000001",
        "event_member_count": "1",
        "detected_count": "2",
        "absent_count": "1",
        "unchecked_count": "0",
        "duplicate_assigned_count": "0",
        "ambiguous_ms1_owner_count": "0",
        "present_rate": "0.75",
        "identity_decision": "production_family",
        "identity_confidence": "medium",
        "primary_evidence": "multi_sample_detected",
        "identity_reason": "multi_sample_detected",
        "quantifiable_detected_count": "2",
        "quantifiable_backfill_count": "1",
        "accepted_cell_count": "3",
        "accepted_backfill_count": "1",
        "review_backfill_count": "0",
        "include_in_primary_matrix": "TRUE",
        "row_flags": "",
        "representative_samples": "sample-a;sample-b;sample-d",
        "family_evidence": "",
        "warning": "",
        "reason": "anchor family; 3/4 present; 1 MS1 backfilled",
    }


def test_write_alignment_review_tsv_warning_precedence(tmp_path: Path):
    from xic_extractor.alignment.tsv_writer import write_alignment_review_tsv

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(cluster_id="ALN000001", has_anchor=False),
            _cluster(cluster_id="ALN000002", has_anchor=True),
            _cluster(cluster_id="ALN000003", has_anchor=True),
            _cluster(cluster_id="ALN000004", has_anchor=True),
            _cluster(
                cluster_id="ALN000005",
                has_anchor=True,
                fold_evidence="owner_complete_link;owner_count=2",
            ),
            _cluster(
                cluster_id="ALN000006",
                has_anchor=True,
                fold_evidence="owner_complete_link;owner_count=2",
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


def test_write_alignment_review_tsv_reports_duplicate_assigned_cells(tmp_path: Path):
    from xic_extractor.alignment.tsv_writer import write_alignment_review_tsv

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
    from xic_extractor.alignment.tsv_writer import write_alignment_review_tsv

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
    assert rows[0]["absent_count"] == "1"
    assert rows[0]["unchecked_count"] == "0"
    assert rows[0]["duplicate_assigned_count"] == "1"
    assert rows[0]["present_rate"] == "0.5"
    assert "1 duplicate-assigned" in rows[0]["reason"]


def test_write_alignment_review_tsv_reports_ambiguous_ms1_owner_cells(
    tmp_path: Path,
):
    from xic_extractor.alignment.tsv_writer import write_alignment_review_tsv

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


def test_write_alignment_review_tsv_reports_folded_clusters(tmp_path: Path):
    from xic_extractor.alignment.tsv_writer import write_alignment_review_tsv

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                has_anchor=True,
                member_count=2,
                folded_cluster_ids=("ALN000002", "ALN000003"),
                folded_member_count=5,
                folded_sample_fill_count=1,
                fold_evidence=(
                    "cid_nl_only;max_mz_ppm=2;max_rt_sec=1;min_shared_detected=4"
                ),
            ),
        ),
        cells=(
            _cell("sample-a", "detected", area=10.0, candidate_id="sample-a#1"),
            _cell("sample-b", "backfilled", area=20.0),
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

    assert rows[0]["event_cluster_count"] == "3"
    assert rows[0]["event_cluster_ids"] == "ALN000001;ALN000002;ALN000003"
    assert rows[0]["event_member_count"] == "7"
    assert rows[0]["family_evidence"].startswith("cid_nl_only;")
    assert rows[0]["reason"] == (
        "anchor family; 2/2 present; 1 MS1 backfilled; merged 3 event clusters"
    )


def test_write_alignment_matrix_tsv_blanks_missing_and_invalid_areas(tmp_path: Path):
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(fold_evidence="owner_complete_link;owner_count=2"),),
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
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(fold_evidence="owner_complete_link;owner_count=2"),),
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
    from xic_extractor.alignment.matrix_output_contract import (
        validate_matrix_identity_rows,
    )
    from xic_extractor.alignment.tsv_writer import (
        write_alignment_matrix_identity_tsv,
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                center_mz=500.123456,
                fold_evidence="owner_complete_link;owner_count=2",
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
        write_alignment_matrix_identity_tsv(
            tmp_path / "alignment_matrix_identity.tsv",
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
    assert identity_row["Mz"] == "500.123456"
    assert review_row["family_center_mz"] == "500.123456"
    validate_matrix_identity_rows(
        matrix_header=tuple(matrix_row), matrix_rows=(matrix_row,),
        identity_columns=tuple(identity_row), identity_rows=(identity_row,),
    )


def test_alignment_matrix_mz_format_is_display_only_for_small_mz(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import (
        write_alignment_matrix_identity_tsv,
        write_alignment_matrix_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                center_mz=5.123456,
                fold_evidence="owner_complete_link;owner_count=2",
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
        write_alignment_matrix_identity_tsv(
            tmp_path / "alignment_matrix_identity.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )[0]

    assert matrix_row["Mz"] == "5.1235"
    assert identity_row["Mz"] == "5.123456"


def test_alignment_matrix_display_collision_does_not_merge_identity_rows(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import (
        write_alignment_matrix_identity_tsv,
        write_alignment_matrix_tsv,
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
        write_alignment_matrix_identity_tsv(
            tmp_path / "alignment_matrix_identity.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert [row["Mz"] for row in matrix_rows] == ["500.1234", "500.1234"]
    assert [row["Mz"] for row in identity_rows] == [
        "500.123441",
        "500.123449",
    ]
    assert [row["peak_hypothesis_id"] for row in identity_rows] == [
        "ALN000001",
        "ALN000002",
    ]


@pytest.mark.parametrize(
    "mutation",
    (
        "duplicate",
        "missing",
        "extra",
        "reordered",
        "matrix_swap",
        "cross_bound",
        "missing_column",
        "unknown_column",
    ),
)
def test_alignment_matrix_identity_binding_rejects_every_row_substitution(
    tmp_path: Path,
    mutation: str,
) -> None:
    from xic_extractor.alignment.matrix_output_contract import (
        validate_matrix_identity_rows,
    )
    from xic_extractor.alignment.tsv_writer import (
        write_alignment_matrix_identity_tsv,
        write_alignment_matrix_tsv,
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
        write_alignment_matrix_identity_tsv(
            tmp_path / "alignment_matrix_identity.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )
    matrix_header = tuple(matrix_rows[0])
    identity_columns = tuple(identity_rows[0])

    validate_matrix_identity_rows(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        identity_columns=identity_columns,
        identity_rows=identity_rows,
    )

    if mutation == "duplicate":
        identity_rows[1] = dict(identity_rows[0])
    elif mutation == "missing":
        identity_rows.pop()
    elif mutation == "extra":
        identity_rows.append(dict(identity_rows[-1]))
    elif mutation == "reordered":
        identity_rows.reverse()
    elif mutation == "matrix_swap":
        matrix_rows.reverse()
    elif mutation == "missing_column":
        identity_columns = tuple(
            column for column in identity_columns if column != "evidence_status"
        )
    elif mutation == "unknown_column":
        identity_columns = (*identity_columns, "unknown_identity_field")
    else:
        first_id = identity_rows[0]["peak_hypothesis_id"]
        identity_rows[0]["peak_hypothesis_id"] = identity_rows[1]["peak_hypothesis_id"]
        identity_rows[1]["peak_hypothesis_id"] = first_id

    with pytest.raises(ValueError, match="identity"):
        validate_matrix_identity_rows(
            matrix_header=matrix_header,
            matrix_rows=matrix_rows,
            identity_columns=identity_columns,
            identity_rows=identity_rows,
        )


def test_alignment_matrix_identity_requires_current_schema_and_binding() -> None:
    from tests.matrix_identity_fixtures import current_matrix_identity_row
    from xic_extractor.alignment.matrix_output_contract import (
        ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
        MATRIX_ROW_BINDING_COLUMN,
        validate_matrix_identity_rows,
    )

    matrix_header = ("Mz", "RT", "sample-a")
    matrix_rows = ({"Mz": "500.1230", "RT": "8.49", "sample-a": "100"},)
    identity = current_matrix_identity_row(
        index=1, mz="500.123", rt="8.49", peak_hypothesis_id="ALN000001",
        sample_values={"sample-a": "100"},
    )
    validate_matrix_identity_rows(
        matrix_header=matrix_header, matrix_rows=matrix_rows,
        identity_columns=ALIGNMENT_MATRIX_IDENTITY_COLUMNS, identity_rows=(identity,),
    )
    for schema in (
        "", "unknown_identity_schema",
        "unsupported_matrix_identity",
    ):
        with pytest.raises(ValueError, match="identity schema"):
            validate_matrix_identity_rows(
                matrix_header=matrix_header, matrix_rows=matrix_rows,
                identity_columns=ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
                identity_rows=({**identity, "identity_schema_version": schema},),
            )
    for columns in (
        tuple(
            c for c in ALIGNMENT_MATRIX_IDENTITY_COLUMNS
            if c != MATRIX_ROW_BINDING_COLUMN
        ),
        (*ALIGNMENT_MATRIX_IDENTITY_COLUMNS, "unknown_identity_field"),
    ):
        with pytest.raises(ValueError, match="identity columns"):
            validate_matrix_identity_rows(
                matrix_header=matrix_header, matrix_rows=matrix_rows,
                identity_columns=columns, identity_rows=(identity,),
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
    from tests.matrix_identity_fixtures import current_matrix_identity_row
    from xic_extractor.alignment.matrix_output_contract import (
        ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
        validate_matrix_identity_rows,
    )

    matrix_header = ("Mz", "RT", "sample-a")
    matrix_row: dict[object, object] = {
        "Mz": "500.1230",
        "RT": "8.49",
        "sample-a": "100",
    }
    identity_row: dict[object, object] = current_matrix_identity_row(
        index=1,
        mz="500.123",
        rt="8.49",
        peak_hypothesis_id="ALN000001",
        sample_values={"sample-a": "100"},
    )
    if mutation == "identity_overflow":
        identity_row[None] = ["overflow"]
    elif mutation == "identity_missing_key":
        identity_row.pop("evidence_status")
    elif mutation == "identity_truncated":
        identity_row["evidence_status"] = None
    elif mutation == "matrix_overflow":
        matrix_row[None] = ["overflow"]
    elif mutation == "matrix_missing_key":
        matrix_row.pop("sample-a")
    else:
        matrix_row["sample-a"] = None

    with pytest.raises(ValueError, match="row fields|missing cells"):
        validate_matrix_identity_rows(
            matrix_header=matrix_header,
            matrix_rows=(matrix_row,),
            identity_columns=ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
            identity_rows=(identity_row,),
        )


def test_alignment_matrix_binding_uses_exact_persisted_rt_and_header_tokens(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.matrix_output_contract import (
        validate_matrix_identity_rows,
    )
    from xic_extractor.alignment.tsv_writer import (
        write_alignment_matrix_identity_tsv,
        write_alignment_matrix_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                center_rt=8.49123456,
                fold_evidence="owner_complete_link;owner_count=2",
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
    identity_path = write_alignment_matrix_identity_tsv(
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
    assert matrix_rows[0]["RT"] == identity_rows[0]["RT"] == "8.49123"
    validate_matrix_identity_rows(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        identity_columns=identity_header,
        identity_rows=identity_rows,
    )


def test_alignment_matrix_mz_binding_accepts_only_current_text() -> None:
    from xic_extractor.alignment.matrix_output_contract import (
        matrix_mz_binding_matches,
    )

    assert matrix_mz_binding_matches("500.1235", "500.123456")
    assert not matrix_mz_binding_matches("500.123", "500.123")
    assert not matrix_mz_binding_matches("500.1234", "500.123456")
    assert not matrix_mz_binding_matches("not-an-mz", "500.123456")
    assert not matrix_mz_binding_matches("500.1235", "not-an-mz")


@pytest.mark.parametrize("center_mz", [math.nan, math.inf, -math.inf])
def test_alignment_matrix_mz_format_preserves_nonfinite_blank_semantics(
    tmp_path: Path,
    center_mz: float,
) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                center_mz=center_mz,
                fold_evidence="owner_complete_link;owner_count=2",
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
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(fold_evidence="owner_complete_link;owner_count=2"),),
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
    from xic_extractor.alignment.tsv_writer import (
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
        clusters=(_cluster(fold_evidence="owner_complete_link;owner_count=2"),),
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
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

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
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

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


def test_write_alignment_matrix_tsv_excludes_rows_without_accepted_cells(
    tmp_path: Path,
):
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                cluster_id="ALN000001",
                fold_evidence="owner_complete_link;owner_count=2",
            ),
            _cluster(cluster_id="ALN000002", has_anchor=False, fold_evidence=""),
            _cluster(
                cluster_id="ALN000003",
                fold_evidence="owner_complete_link;owner_count=2",
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


def test_write_alignment_matrix_identity_tsv_maps_product_rows(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import (
        ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
        write_alignment_matrix_identity_tsv,
        write_alignment_matrix_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(_cluster(fold_evidence="owner_complete_link;owner_count=2"),),
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
        write_alignment_matrix_identity_tsv(
            tmp_path / "alignment_matrix_identity.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert list(identity_rows[0]) == list(ALIGNMENT_MATRIX_IDENTITY_COLUMNS)
    assert len(identity_rows) == len(matrix_rows) == 1
    row_binding = identity_rows[0]["matrix_row_binding_sha256"]
    assert len(row_binding) == 64
    assert set(row_binding) <= set("0123456789abcdef")
    assert {
        key: value
        for key, value in identity_rows[0].items()
        if key != "matrix_row_binding_sha256"
    } == {
        "identity_schema_version": "untargeted_peak_hypothesis_matrix_identity",
        "matrix_row_index": "1",
        "Mz": "500.123",
        "RT": matrix_rows[0]["RT"],
        "peak_hypothesis_id": "ALN000001",
        "row_identity_basis": "no_split_peak_hypothesis",
        "split_evaluation_status": "complete_no_product_ready_split",
        "projection_status": "not_projection",
        "source_feature_family_ids": "ALN000001",
        "source_feature_family_count": "1",
        "center_mz_basis": "source_family_center_mz",
        "center_rt_basis": "accepted_cell_area_weighted_apex_rt",
        "center_weight_basis": "primary_matrix_area",
        "accepted_cell_count": "2",
        "accepted_sample_count": "2",
        "evidence_status": "product_matrix_identity_complete",
    }


def test_write_alignment_matrix_rejects_family_projection_identity(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(
            SimpleNamespace(
                feature_family_id="ALN000001",
                neutral_loss_tag="DNA_dR",
                family_center_mz=500.123,
                family_center_rt=8.49,
                family_product_mz=384.076,
                family_observed_neutral_loss_da=116.047,
                has_anchor=True,
                event_cluster_ids=("ALN000001",),
                event_member_count=1,
                evidence="owner_complete_link;owner_count=2",
                row_identity_basis="family_projection_no_split_evidence",
                split_evaluation_status="incomplete_scope",
                projection_status="family_projection",
            ),
        ),
        cells=(
            _cell("sample-a", "detected", area=100.0),
            _cell("sample-b", "detected", area=110.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    with pytest.raises(ValueError, match="family_projection_no_split_evidence"):
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )


def test_write_alignment_matrix_identity_tsv_records_split_hypothesis(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_identity_tsv

    matrix = AlignmentMatrix(
        clusters=(
            SimpleNamespace(
                feature_family_id="PH_SPLIT_A",
                neutral_loss_tag="DNA_dR",
                family_center_mz=500.123,
                family_center_rt=8.49,
                family_product_mz=384.076,
                family_observed_neutral_loss_da=116.047,
                has_anchor=True,
                event_cluster_ids=("PH_SPLIT_A",),
                event_member_count=1,
                evidence="owner_complete_link;owner_count=2",
                peak_hypothesis_id="PH_SPLIT_A",
                row_identity_basis="split_peak_hypothesis",
                split_evaluation_status="complete_product_ready_split",
                projection_status="not_projection",
                source_feature_family_ids=("FAM_PARENT",),
            ),
        ),
        cells=(
            _cell("sample-a", "detected", cluster_id="PH_SPLIT_A", area=100.0),
            _cell("sample-b", "detected", cluster_id="PH_SPLIT_A", area=110.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    rows = _read_tsv(
        write_alignment_matrix_identity_tsv(
            tmp_path / "alignment_matrix_identity.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert rows[0]["peak_hypothesis_id"] == "PH_SPLIT_A"
    assert rows[0]["row_identity_basis"] == "split_peak_hypothesis"
    assert rows[0]["split_evaluation_status"] == "complete_product_ready_split"
    assert rows[0]["source_feature_family_ids"] == "FAM_PARENT"


def test_write_alignment_matrix_uses_explicit_peak_hypotheses_as_rows(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import (
        write_alignment_matrix_identity_tsv,
        write_alignment_matrix_tsv,
    )

    parent = SimpleNamespace(
        feature_family_id="FAM_PARENT",
        neutral_loss_tag="DNA_dR",
        family_center_mz=500.123,
        family_center_rt=8.49,
        family_product_mz=384.076,
        family_observed_neutral_loss_da=116.047,
        has_anchor=True,
        event_cluster_ids=("FAM_PARENT",),
        event_member_count=2,
        evidence="owner_complete_link;owner_count=2",
        peak_hypotheses=(
            SimpleNamespace(
                peak_hypothesis_id="FAM_PARENT::early",
                row_identity_basis="split_peak_hypothesis",
                split_evaluation_status="complete_product_ready_split",
                projection_status="not_projection",
                source_feature_family_ids=("FAM_PARENT",),
                sample_stems=("sample-a",),
                center_mz_basis="peak_hypothesis_accepted_cells",
                center_rt_basis="peak_hypothesis_accepted_cells",
            ),
            SimpleNamespace(
                peak_hypothesis_id="FAM_PARENT::late",
                row_identity_basis="split_peak_hypothesis",
                split_evaluation_status="complete_product_ready_split",
                projection_status="not_projection",
                source_feature_family_ids=("FAM_PARENT",),
                sample_stems=("sample-b",),
                center_mz_basis="peak_hypothesis_accepted_cells",
                center_rt_basis="peak_hypothesis_accepted_cells",
            ),
        ),
    )
    matrix = AlignmentMatrix(
        clusters=(parent,),
        cells=(
            _cell("sample-a", "detected", cluster_id="FAM_PARENT", area=100.0),
            _cell("sample-b", "detected", cluster_id="FAM_PARENT", area=110.0),
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
        write_alignment_matrix_identity_tsv(
            tmp_path / "alignment_matrix_identity.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )

    assert list(matrix_rows[0]) == ["Mz", "RT", "sample-a", "sample-b"]
    assert [(row["sample-a"], row["sample-b"]) for row in matrix_rows] == [
        ("1.00000e+02", ""),
        ("", "1.10000e+02"),
    ]
    assert [row["peak_hypothesis_id"] for row in identity_rows] == [
        "FAM_PARENT::early",
        "FAM_PARENT::late",
    ]
    assert [row["row_identity_basis"] for row in identity_rows] == [
        "split_peak_hypothesis",
        "split_peak_hypothesis",
    ]
    assert {row["source_feature_family_ids"] for row in identity_rows} == {
        "FAM_PARENT",
    }


def test_write_alignment_matrix_rejects_split_hypothesis_without_identity(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    parent = SimpleNamespace(
        feature_family_id="FAM_PARENT",
        neutral_loss_tag="DNA_dR",
        family_center_mz=500.123,
        family_center_rt=8.49,
        family_product_mz=384.076,
        family_observed_neutral_loss_da=116.047,
        has_anchor=True,
        event_cluster_ids=("FAM_PARENT",),
        event_member_count=2,
        evidence="owner_complete_link;owner_count=2",
        peak_hypotheses=(
            SimpleNamespace(
                peak_hypothesis_id="",
                row_identity_basis="split_peak_hypothesis",
                split_evaluation_status="complete_product_ready_split",
                projection_status="not_projection",
                source_feature_family_ids=("FAM_PARENT",),
                sample_stems=("sample-a",),
            ),
        ),
    )
    matrix = AlignmentMatrix(
        clusters=(parent,),
        cells=(
            _cell("sample-a", "detected", cluster_id="FAM_PARENT", area=100.0),
            _cell("sample-b", "detected", cluster_id="FAM_PARENT", area=110.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    with pytest.raises(ValueError, match="requires peak_hypothesis_id"):
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )


def test_write_alignment_matrix_rejects_duplicate_split_hypothesis_id(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = _split_writer_matrix(
        (
            _split_hypothesis("FAM_PARENT::same", ("sample-a",)),
            _split_hypothesis("FAM_PARENT::same", ("sample-b",)),
        )
    )

    with pytest.raises(ValueError, match="duplicate product peak_hypothesis_id"):
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )


def test_write_alignment_matrix_rejects_overlapping_split_sample_claims(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = _split_writer_matrix(
        (
            _split_hypothesis("FAM_PARENT::early", ("sample-a",)),
            _split_hypothesis("FAM_PARENT::late", ("sample-a", "sample-b")),
        )
    )

    with pytest.raises(ValueError, match="claimed by multiple peak_hypothesis_id"):
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )


def test_write_alignment_matrix_rejects_unassigned_split_product_cell(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = _split_writer_matrix(
        (_split_hypothesis("FAM_PARENT::early", ("sample-a",)),)
    )

    with pytest.raises(ValueError, match="not assigned to split hypothesis"):
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )


def test_write_alignment_matrix_rejects_split_hypothesis_missing_source_family(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = _split_writer_matrix(
        (
            _split_hypothesis(
                "FAM_PARENT::early",
                ("sample-a",),
                source_feature_family_ids=None,
            ),
            _split_hypothesis(
                "FAM_PARENT::late",
                ("sample-b",),
                source_feature_family_ids=None,
            ),
        )
    )

    with pytest.raises(ValueError, match="requires source_feature_family_ids"):
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )


def test_write_alignment_matrix_rejects_multi_family_product_identity(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(
            SimpleNamespace(
                feature_family_id="PH_COLLAPSED",
                neutral_loss_tag="DNA_dR",
                family_center_mz=500.123,
                family_center_rt=8.49,
                family_product_mz=384.076,
                family_observed_neutral_loss_da=116.047,
                has_anchor=True,
                event_cluster_ids=("PH_COLLAPSED",),
                event_member_count=2,
                evidence="owner_complete_link;owner_count=2",
                source_feature_family_ids=("FAM_A", "FAM_B"),
            ),
        ),
        cells=(
            _cell("sample-a", "detected", cluster_id="PH_COLLAPSED", area=100.0),
            _cell("sample-b", "detected", cluster_id="PH_COLLAPSED", area=110.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    with pytest.raises(ValueError, match="exactly one source_feature_family_id"):
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )


def test_write_alignment_matrix_rejects_duplicate_source_candidate_writes(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                cluster_id="ALN000001",
                fold_evidence="owner_complete_link;owner_count=2",
            ),
            _cluster(
                cluster_id="ALN000002",
                fold_evidence="owner_complete_link;owner_count=2",
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
    from xic_extractor.alignment.tsv_writer import write_alignment_review_tsv

    matrix = AlignmentMatrix(
        clusters=(_cluster(has_anchor=False, fold_evidence=""),),
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

    assert rows[0]["identity_decision"] == "audit_family"
    assert rows[0]["identity_confidence"] == "review"
    assert rows[0]["primary_evidence"] == "none"
    assert rows[0]["identity_reason"] == "rescue_only_blocked"
    assert rows[0]["quantifiable_detected_count"] == "0"
    assert rows[0]["quantifiable_backfill_count"] == "1"
    assert rows[0]["accepted_cell_count"] == "0"
    assert rows[0]["accepted_backfill_count"] == "0"
    assert rows[0]["review_backfill_count"] == "1"
    assert rows[0]["include_in_primary_matrix"] == "FALSE"
    assert rows[0]["row_flags"] == "rescue_only;rescue_only_review"


def test_one_detected_provisional_retention_stays_out_of_primary_matrix(
    tmp_path: Path,
):
    from xic_extractor.alignment.tsv_writer import (
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                fold_evidence="owner_complete_link;owner_count=1",
            ),
        ),
        cells=(
            _cell("sample-a", "detected", area=100.0, candidate_id="sample-a#1"),
            _cell("sample-b", "backfilled", area=90.0),
            _cell("sample-c", "backfilled", area=80.0),
        ),
        sample_order=("sample-a", "sample-b", "sample-c"),
    )

    review_rows = _read_tsv(
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

    assert list(review_rows[0]) == REVIEW_COLUMNS
    assert review_rows[0]["identity_decision"] == "provisional_discovery"
    assert review_rows[0]["include_in_primary_matrix"] == "FALSE"
    assert review_rows[0]["quantifiable_detected_count"] == "1"
    assert review_rows[0]["quantifiable_backfill_count"] == "2"
    assert set(review_rows[0]["row_flags"].split(";")) == {
        "single_detected_seed",
        "rescue_heavy",
        "rescue_only_review",
    }
    assert matrix_rows == []


def test_direct_tier2_review_token_does_not_change_matrix_writer(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.production_candidate_gate import (
        evaluate_production_candidate_gate,
        source_context_for_artifacts,
    )
    from xic_extractor.alignment.tsv_writer import (
        write_alignment_cells_tsv,
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    matrix = AlignmentMatrix(
        clusters=(
            _cluster(
                fold_evidence="owner_complete_link;owner_count=1",
            ),
        ),
        cells=(
            _cell("sample-a", "detected", area=100.0, candidate_id="sample-a#1"),
            _cell("sample-b", "backfilled", area=90.0),
            _cell("sample-c", "backfilled", area=80.0),
        ),
        sample_order=("sample-a", "sample-b", "sample-c"),
    )
    review_path = write_alignment_review_tsv(
        tmp_path / "alignment_review.tsv",
        matrix,
        production_decisions=_production_decisions(matrix),
    )
    cells_path = write_alignment_cells_tsv(
        tmp_path / "alignment_cells.tsv",
        matrix,
    )
    matrix_path = write_alignment_matrix_tsv(
        tmp_path / "alignment_matrix.tsv",
        matrix,
        production_decisions=_production_decisions(matrix),
    )
    review_rows = _read_tsv(review_path)
    cell_rows = _read_tsv(cells_path)

    sidecar_review_row = {
        **review_rows[0],
        "independent_tier2_support_components": ("validated_tier2_trace_evidence"),
    }
    decision = evaluate_production_candidate_gate(
        sidecar_review_row,
        cell_rows,
        source_context=source_context_for_artifacts(
            review_path=review_path,
            cell_path=cells_path,
            matrix_path=matrix_path,
        ),
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.support_components == ()
    assert decision.challenge_blockers == ("not_retention_candidate",)
    assert _read_tsv(matrix_path) == []


def test_write_alignment_status_matrix_tsv_preserves_duplicate_assigned(
    tmp_path: Path,
):
    from xic_extractor.alignment.tsv_writer import write_alignment_status_matrix_tsv

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
    from xic_extractor.alignment.tsv_writer import (
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
                region=True,
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
    assert cells[0]["region_candidate_count"] == "2"
    assert cells[0]["region_selected_proposal_sources"] == (
        "local_minimum;centwave_cwt"
    )
    assert cells[0]["region_local_mixture_diagnostic"] == "one_envelope_supported"
    assert cells[0]["region_decision_status"] == "evaluated"
    assert cells[0]["region_decision_class"] == "merge_suggested"
    assert cells[0]["region_product_action"] == "safe_merge_eligible"
    assert cells[0]["region_promotion_reason"] == ("adjacent_wis_local_minimum_merge")
    assert cells[0]["region_baseline_method"] == "asls"
    assert status[0]["sample-a"] == "detected"
    assert status[0]["sample-b"] == "unchecked"


def test_backfill_cell_evidence_tsv_is_compact_and_backfill_scoped(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.backfill_evidence_contract import (
        BackfillEvidenceOwnerBindings,
        EvidenceClaimState,
    )
    from xic_extractor.alignment.backfill_evidence_tsv import (
        lookup_backfill_evidence_tsv,
    )
    from xic_extractor.alignment.tsv_writer import (
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
                region=True,
            ),
            _cell("sample-b", "backfilled", cluster_id="ALN000001", area=8.0),
            _cell("sample-c", "unchecked", cluster_id="ALN000001"),
            _cell(
                "sample-a",
                "detected",
                cluster_id="ALN000002",
                area=20.0,
                candidate_id="sample-a#2",
                region=True,
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
    assert {row["feature_family_id"] for row in rows} == {"ALN000001"}
    assert {row["sample_stem"] for row in rows} == {"sample-a", "sample-b"}
    backfilled = next(row for row in rows if row["status"] == "backfilled")
    assert backfilled["production_cell_status"] in {
        "accepted_backfill",
        "review_backfill",
    }
    assert backfilled["primary_matrix_area"] == "8"
    assert "region_candidate_count" not in rows[0]

    result = lookup_backfill_evidence_tsv(
        evidence_path,
        feature_family_id="ALN000001",
        sample_stem="sample-a",
        owner_bindings=BackfillEvidenceOwnerBindings(
            known_group_ids=frozenset(),
            known_sample_stems=frozenset({"sample-a", "sample-b"}),
            candidate_samples={"sample-a#1": "sample-a"},
        ),
    )
    assert len(result.records) == 1
    assert result.records[0].group_hypothesis_id == ""
    assert result.records[0].public_family_id == ""
    assert result.claim("record_content").state is EvidenceClaimState.STRUCTURE_BOUND
    assert result.claim("cross_artifact_binding").state is (
        EvidenceClaimState.UNAVAILABLE
    )


def test_alignment_cells_tsv_can_emit_without_region_audit(tmp_path: Path) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_cells_tsv

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
    assert region_columns
    assert all(cells[0][column] == "" for column in region_columns)


def test_write_alignment_owner_backfill_seed_audit_tsv_is_sidecar(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import (
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
    assert audit[0]["feature_family_id"] == "ALN000001"
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
    from xic_extractor.alignment.tsv_writer import (
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

    assert review[0]["feature_family_id"] == "'=cluster"
    assert review[0]["neutral_loss_tag"] == "'+NL"
    assert review[0]["representative_samples"] == "'=sample;+sample-2"
    assert "'=sample" in matrix_rows[0]


def test_private_write_tsv_preserves_lf_formula_and_format_contract(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment import tsv_writer

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
    from xic_extractor.alignment import tsv_writer

    rows = ({"name": f"row-{index}", "value": index} for index in range(3))

    path = tsv_writer._write_tsv(tmp_path / "streamed.tsv", ("name", "value"), rows)

    assert path.read_bytes() == b"name\tvalue\nrow-0\t0\nrow-1\t1\nrow-2\t2\n"


def _production_decisions(
    matrix: AlignmentMatrix,
    alignment_config: AlignmentConfig | None = None,
) -> ProductionDecisionSet:
    return build_production_decisions(
        matrix,
        alignment_config or AlignmentConfig(),
    )


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
    folded_cluster_ids: tuple[str, ...] = (),
    folded_member_count: int = 0,
    folded_sample_fill_count: int = 0,
    fold_evidence: str = "",
) -> AlignmentCluster:
    return AlignmentCluster(
        cluster_id=cluster_id,
        neutral_loss_tag=neutral_loss_tag,
        cluster_center_mz=center_mz,
        cluster_center_rt=center_rt,
        cluster_product_mz=384.076,
        cluster_observed_neutral_loss_da=116.047,
        has_anchor=has_anchor,
        members=tuple(
            SimpleNamespace(candidate_id=f"{cluster_id}#member-{index}")
            for index in range(member_count)
        ),
        anchor_members=(),
        folded_cluster_ids=folded_cluster_ids,
        folded_member_count=folded_member_count,
        folded_sample_fill_count=folded_sample_fill_count,
        fold_evidence=fold_evidence,
    )


def _display_collision_matrix() -> AlignmentMatrix:
    return AlignmentMatrix(
        clusters=(
            _cluster(
                cluster_id="ALN000001",
                center_mz=500.123441,
                fold_evidence="owner_complete_link;owner_count=2",
            ),
            _cluster(
                cluster_id="ALN000002",
                center_mz=500.123449,
                fold_evidence="owner_complete_link;owner_count=2",
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


def _split_writer_matrix(
    peak_hypotheses: tuple[SimpleNamespace, ...],
) -> AlignmentMatrix:
    parent = SimpleNamespace(
        feature_family_id="FAM_PARENT",
        neutral_loss_tag="DNA_dR",
        family_center_mz=500.123,
        family_center_rt=8.49,
        family_product_mz=384.076,
        family_observed_neutral_loss_da=116.047,
        has_anchor=True,
        event_cluster_ids=("FAM_PARENT",),
        event_member_count=2,
        evidence="owner_complete_link;owner_count=2",
        peak_hypotheses=peak_hypotheses,
    )
    return AlignmentMatrix(
        clusters=(parent,),
        cells=(
            _cell("sample-a", "detected", cluster_id="FAM_PARENT", area=100.0),
            _cell("sample-b", "detected", cluster_id="FAM_PARENT", area=110.0),
        ),
        sample_order=("sample-a", "sample-b"),
    )


def _split_hypothesis(
    peak_hypothesis_id: str,
    sample_stems: tuple[str, ...],
    *,
    source_feature_family_ids: tuple[str, ...] | None = ("FAM_PARENT",),
) -> SimpleNamespace:
    values: dict[str, object] = {
        "peak_hypothesis_id": peak_hypothesis_id,
        "row_identity_basis": "split_peak_hypothesis",
        "split_evaluation_status": "complete_product_ready_split",
        "projection_status": "not_projection",
        "sample_stems": sample_stems,
    }
    if source_feature_family_ids is not None:
        values["source_feature_family_ids"] = source_feature_family_ids
    return SimpleNamespace(**values)


def _cell(
    sample_stem: str,
    status,
    *,
    cluster_id: str = "ALN000001",
    area: float | None = None,
    candidate_id: str | None = None,
    apex_rt: float = 8.49,
    trace_quality: str | None = None,
    region: bool = False,
    integration: bool = False,
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
        region_candidate_count=2 if region else None,
        region_selected_proposal_sources=(
            ("local_minimum", "centwave_cwt") if region else ()
        ),
        region_shadow_status="evaluated" if region else "",
        region_shadow_verdict="merge_suggested" if region else "",
        region_merge_suggestion_source=(
            "adjacent_wis_local_minimum_merge" if region else ""
        ),
        region_area_ratio=1.04 if region else None,
        region_selected_interval_count=2 if region else None,
        region_selected_interval_gap_max_min=0.04 if region else None,
        region_local_mixture_diagnostic=("one_envelope_supported" if region else ""),
        region_local_mixture_reason=(
            "adjacent intervals support one envelope" if region else ""
        ),
        region_review_reason="same envelope" if region else "",
        region_decision_status="evaluated" if region else "",
        region_decision_class="merge_suggested" if region else "",
        region_product_action="safe_merge_eligible" if region else "",
        region_promotion_reason=("adjacent_wis_local_minimum_merge" if region else ""),
        region_baseline_method="asls" if region else "",
        integration_audit=(
            CellIntegrationAuditSummary(
                raw_area=area,
                area_baseline_corrected=8.0,
                area_uncertainty=2.0,
                area_uncertainty_formula_version="baseline_residual_mad",
                baseline_residual_mad=0.5,
                area_uncertainty_noise_source="asls_residual",
                baseline_type="asls",
                baseline_score=0.8,
                uncertainty_fraction=0.2,
                baseline_fraction=0.8,
                integration_scan_count=5,
            )
            if integration
            else None
        ),
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
