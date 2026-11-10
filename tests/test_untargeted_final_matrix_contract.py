import csv
from pathlib import Path
from typing import Literal

from tests.alignment_pipeline_helpers import finalized_writer_decisions
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.production_decisions import (
    ProductionDecisionSet,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
    FinalizedUntargetMatrixRow,
)
from xic_extractor.output.alignment.tsv_writer import (
    write_alignment_matrix_tsv,
    write_alignment_review_tsv,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult

FORBIDDEN_PRIMARY_STATUSES = {
    "detected",
    "backfilled",
    "accepted_backfill",
    "review_backfill",
    "rejected_backfill",
    "duplicate_assigned",
    "ambiguous_ms1_owner",
    "absent",
    "unchecked",
}


def test_primary_outputs_hide_status_strings_and_keep_audit_reasons(
    tmp_path: Path,
):
    matrix = AlignmentMatrix(
        clusters=(
            _feature("FAM001"),
            _feature("FAM_PROVISIONAL", has_anchor=False, destination="archive"),
            _feature("FAM002", has_anchor=False, destination="archive"),
            _feature("FAM003", destination="archive"),
        ),
        sample_order=("s1", "s2", "s3"),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "backfilled", 90.0),
            _cell(
                "s3",
                "FAM001",
                "detected",
                110.0,
                selected_integration=_integration(
                    raw_area=125.0,
                    asls_area=105.0,
                    morphology_area=115.0,
                ),
            ),
            _cell("s1", "FAM_PROVISIONAL", "detected", 85.0),
            _cell("s2", "FAM_PROVISIONAL", "backfilled", 75.0),
            _cell("s1", "FAM002", "backfilled", 80.0),
            _cell("s2", "FAM002", "absent", None),
            _cell("s1", "FAM003", "duplicate_assigned", 70.0),
            _cell("s2", "FAM003", "ambiguous_ms1_owner", None),
        ),
    )

    matrix_tsv = write_alignment_matrix_tsv(
        tmp_path / "alignment_matrix.tsv",
        matrix,
        production_decisions=_production_decisions(matrix),
    )
    tsv_rows = _read_tsv(matrix_tsv)
    assert list(tsv_rows[0]) == ["Mz", "RT", "s1", "s2", "s3"]
    assert [(row["Mz"], row["RT"]) for row in tsv_rows] == [("500.1230", "8.49")]
    assert tsv_rows[0]["s1"] == "1.00000e+02"
    assert tsv_rows[0]["s2"] == "9.00000e+01"
    assert tsv_rows[0]["s3"] == "1.15000e+02"
    assert FORBIDDEN_PRIMARY_STATUSES.isdisjoint(
        value for row in tsv_rows for value in row.values()
    )

    identity_rows = _read_tsv(
        write_alignment_review_tsv(
            tmp_path / "alignment_review.tsv",
            matrix,
            production_decisions=_production_decisions(matrix),
        )
    )
    matrix_projection = [row for row in identity_rows if row["matrix_row_index"]]
    assert len(matrix_projection) == len(tsv_rows)
    assert matrix_projection[0]["matrix_row_index"] == "1"
    assert matrix_projection[0]["feature_id"] == "FAM001"
    assert len(identity_rows) == len(matrix.clusters)


def _production_decisions(matrix: AlignmentMatrix) -> ProductionDecisionSet:
    return finalized_writer_decisions(matrix)


def _feature(
    feature_id: str,
    *,
    has_anchor: bool = True,
    group_hypothesis_id: str = "",
    destination: Literal["matrix", "archive"] = "matrix",
) -> FinalizedUntargetMatrixRow:
    return FinalizedUntargetMatrixRow(
        feature_id=feature_id,
        group_hypothesis_id=group_hypothesis_id,
        destination=destination,
        family_center_mz=500.123,
        family_center_rt=8.49,
        physical_peak_keys=("OWN-s1-000001",),
        family_product_mz=384.076,
        family_observed_neutral_loss_da=116.047,
        neutral_loss_tag="DNA_dR",
        has_anchor=has_anchor,
    )


def _cell(
    sample_stem: str,
    cluster_id: str,
    status: str,
    area: float | None,
    *,
    selected_integration: IntegrationResult | None = None,
) -> AlignedCell:
    if (
        selected_integration is None
        and status in {"detected", "backfilled"}
        and area is not None
        and area > 0
    ):
        selected_integration = _integration(
            raw_area=area,
            asls_area=area,
            morphology_area=area,
        )
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=cluster_id,
        status=status,  # type: ignore[arg-type]
        area=area,
        apex_rt=8.49 if area is not None else None,
        height=100.0 if area is not None else None,
        peak_start_rt=8.4 if area is not None else None,
        peak_end_rt=8.6 if area is not None else None,
        rt_delta_sec=0.0 if area is not None else None,
        trace_quality="clean" if area is not None else status,
        scan_support_score=0.8 if area is not None else None,
        source_candidate_id=f"{sample_stem}#1" if status == "detected" else None,
        reason=(
            "duplicate MS1 peak claim; winner=FAM001; original_status=detected"
            if status == "duplicate_assigned"
            else status
        ),
        selected_integration=selected_integration,
    )


def _integration(
    *,
    raw_area: float,
    asls_area: float | None,
    baseline_type: str = "asls",
    morphology_area: float | None = None,
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
            "gaussian15_positive_asls_residual" if morphology_area is not None else ""
        ),
    )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
