import csv
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.pipeline_outputs as pipeline_outputs_module
from tests.alignment_pipeline_helpers import matrix as _matrix
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.pipeline_outputs import output_paths
from xic_extractor.alignment.production_decisions import (
    ProductionDecisionSet,
    build_production_decisions,
)
from xic_extractor.alignment.tsv_writer import (
    write_alignment_backfill_cell_evidence_tsv,
    write_alignment_matrix_identity_tsv,
    write_alignment_matrix_tsv,
    write_alignment_review_tsv,
)


def test_output_transaction_requires_owner_minted_decisions_before_writes(
    tmp_path: Path,
) -> None:
    outputs = output_paths(
        tmp_path,
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    matrix = _matrix(("Sample_A", "Sample_B"))
    config = AlignmentConfig()

    with pytest.raises(TypeError, match="production_decisions"):
        pipeline_outputs_module.write_outputs_atomic(
            outputs,
            matrix,
            metadata={},
            ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
            alignment_config=config,
        )
    with pytest.raises(TypeError, match="production_decisions must be owner-minted"):
        pipeline_outputs_module.write_outputs_atomic(
            outputs,
            matrix,
            metadata={},
            ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
            alignment_config=config,
            production_decisions=object(),  # type: ignore[arg-type]
        )

    assert not any(
        path.exists() for path in outputs.__dict__.values() if isinstance(path, Path)
    )


def test_matrix_only_output_plan_omits_unselected_provenance(
    tmp_path: Path,
) -> None:
    outputs = output_paths(
        tmp_path / "out",
        output_level="production",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    matrix = _matrix(("Sample_A", "Sample_B"))
    config = AlignmentConfig()

    pipeline_outputs_module.write_outputs_atomic(
        outputs,
        matrix,
        metadata={},
        ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
    )

    assert outputs.matrix_tsv is not None
    assert outputs.matrix_tsv.exists()
    assert outputs.owner_backfill_peak_fact_provenance_tsv is None
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == [
        "alignment_matrix.tsv"
    ]


@pytest.mark.parametrize(
    ("renderer_name", "relative_path"),
    (
        ("review", "review/review.tsv"),
        ("matrix", "matrix/matrix.tsv"),
        ("identity", "identity/identity.tsv"),
        ("backfill", "backfill/evidence.tsv"),
    ),
    ids=("review-tsv", "matrix-tsv", "identity-tsv", "backfill-tsv"),
)
def test_activated_renderers_reject_wrong_decision_type_before_writes(
    tmp_path: Path,
    renderer_name: str,
    relative_path: str,
) -> None:
    matrix = _matrix(("Sample_A", "Sample_B"))
    config = AlignmentConfig()
    wrong_decisions = {}  # falsey input exercised the retired rebuild fallback
    renderers = {
        "review": lambda path: write_alignment_review_tsv(
            path,
            matrix,
            alignment_config=config,
            production_decisions=wrong_decisions,  # type: ignore[arg-type]
        ),
        "matrix": lambda path: write_alignment_matrix_tsv(
            path,
            matrix,
            alignment_config=config,
            production_decisions=wrong_decisions,  # type: ignore[arg-type]
        ),
        "identity": lambda path: write_alignment_matrix_identity_tsv(
            path,
            matrix,
            alignment_config=config,
            production_decisions=wrong_decisions,  # type: ignore[arg-type]
        ),
        "backfill": lambda path: write_alignment_backfill_cell_evidence_tsv(
            path,
            matrix,
            production_decisions=wrong_decisions,  # type: ignore[arg-type]
        ),
    }

    path = tmp_path / relative_path
    with pytest.raises(TypeError, match="production_decisions must be owner-minted"):
        renderers[renderer_name](path)
    assert not path.parent.exists()


def test_activated_renderers_use_supplied_owner_values_statuses_and_counts(
    tmp_path: Path,
) -> None:
    matrix = _matrix(("Sample_A", "Sample_B"))
    config = AlignmentConfig()
    supplied = _divergent_owner_decisions(matrix, config)
    outputs = output_paths(
        tmp_path,
        output_level="machine",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )

    pipeline_outputs_module.write_outputs_atomic(
        outputs,
        matrix,
        metadata={},
        ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
        alignment_config=config,
        production_decisions=supplied,
    )

    assert outputs.matrix_tsv is not None
    with outputs.matrix_tsv.open(encoding="utf-8", newline="") as handle:
        matrix_row = next(csv.DictReader(handle, delimiter="\t"))
    assert float(matrix_row["Sample_A"]) == pytest.approx(987.654)
    assert float(matrix_row["Sample_B"]) == pytest.approx(987.654)
    assert outputs.review_tsv is not None
    with outputs.review_tsv.open(encoding="utf-8", newline="") as handle:
        review_row = next(csv.DictReader(handle, delimiter="\t"))
    assert review_row["detected_count"] == "0"
    assert review_row["absent_count"] == "2"
    assert review_row["representative_samples"] == ""



def test_a_withheld_owner_decision_leaves_the_matrix_cell_empty(
    tmp_path: Path,
) -> None:
    """write_matrix_value=False must withhold the value, not just pick another."""
    matrix = _matrix(("Sample_A", "Sample_B"))
    config = AlignmentConfig()
    supplied = _divergent_owner_decisions(matrix, config, write_matrix_value=False)
    outputs = output_paths(
        tmp_path,
        output_level="machine",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )

    pipeline_outputs_module.write_outputs_atomic(
        outputs,
        matrix,
        metadata={},
        ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
        alignment_config=config,
        production_decisions=supplied,
    )

    assert outputs.matrix_tsv is not None
    with outputs.matrix_tsv.open(encoding="utf-8", newline="") as handle:
        matrix_row = next(csv.DictReader(handle, delimiter="	"))
    assert matrix_row["Sample_A"] == ""
    assert matrix_row["Sample_B"] == ""


def _divergent_owner_decisions(
    matrix,
    config: AlignmentConfig,
    *,
    write_matrix_value: bool = True,
) -> ProductionDecisionSet:
    rebuilt = build_production_decisions(matrix, config)
    return ProductionDecisionSet(
        cells={
            key: replace(
                decision,
                raw_status="absent",
                production_status="detected",
                backfill_tier="",
                write_matrix_value=write_matrix_value,
                matrix_value=987.654,
                blank_reason="",
            )
            for key, decision in rebuilt.cells.items()
        },
        rows={
            key: replace(
                decision,
                include_in_primary_matrix=True,
                quantifiable_detected_count=2,
                accepted_cell_count=2,
            )
            for key, decision in rebuilt.rows.items()
        },
    )
