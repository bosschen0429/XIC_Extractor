import csv
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

import xic_extractor.alignment.pipeline as pipeline_module
import xic_extractor.alignment.pipeline_outputs as pipeline_outputs_module
from tests.alignment_pipeline_helpers import FakeRawOpener
from tests.alignment_pipeline_helpers import matrix as _matrix
from tests.alignment_pipeline_helpers import (
    patch_owner_pipeline_to_matrix as _patch_owner_pipeline_to_matrix,
)
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from tests.alignment_pipeline_helpers import write_batch as _write_batch
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.html_report import write_alignment_review_html
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
from xic_extractor.alignment.xlsx_writer import write_alignment_results_xlsx


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


@pytest.mark.parametrize(
    ("renderer_name", "relative_path"),
    (
        ("review", "review/review.tsv"),
        ("matrix", "matrix/matrix.tsv"),
        ("identity", "identity/identity.tsv"),
        ("backfill", "backfill/evidence.tsv"),
        ("xlsx", "xlsx/results.xlsx"),
        ("html", "html/review.html"),
    ),
    ids=("review-tsv", "matrix-tsv", "identity-tsv", "backfill-tsv", "xlsx", "html"),
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
        "xlsx": lambda path: write_alignment_results_xlsx(
            path,
            matrix,
            metadata={},
            alignment_config=config,
            production_decisions=wrong_decisions,  # type: ignore[arg-type]
        ),
        "html": lambda path: write_alignment_review_html(
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

    assert outputs.workbook is not None
    workbook = load_workbook(outputs.workbook, read_only=True, data_only=True)
    try:
        matrix_rows = list(workbook["Matrix"].iter_rows(values_only=True))
        review_rows = list(workbook["Review"].iter_rows(values_only=True))
        audit_rows = list(workbook["Audit"].iter_rows(values_only=True))
    finally:
        workbook.close()
    matrix_result = dict(zip(matrix_rows[0], matrix_rows[1], strict=True))
    assert matrix_result["Sample_A"] == pytest.approx(987.654)
    assert matrix_result["Sample_B"] == pytest.approx(987.654)
    review = dict(zip(review_rows[0], review_rows[1], strict=True))
    assert review["detected_count"] == 0
    assert review["rescued_count"] == 0
    assert review["absent_count"] == 2
    audit = [dict(zip(audit_rows[0], row, strict=True)) for row in audit_rows[1:]]
    assert {row["raw_status"] for row in audit} == {"absent"}
    assert {row["production_status"] for row in audit} == {"detected"}
    assert {row["write_matrix_value"] for row in audit} == {True}

    assert outputs.review_html is not None
    html = outputs.review_html.read_text(encoding="utf-8")
    assert (
        '<div class="row"><div>detected</div><div class="track">'
        '<div class="bar" style="width:0.0%"></div></div><div>0</div></div>' in html
    )
    assert (
        '<div class="row"><div>absent</div><div class="track">'
        '<div class="bar" style="width:100.0%"></div></div><div>2</div></div>' in html
    )


def test_run_alignment_mints_one_decision_set_for_activated_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = 0

    def counted_build_production_decisions(*args, **kwargs):
        nonlocal calls
        calls += 1
        return build_production_decisions(*args, **kwargs)

    monkeypatch.setattr(
        pipeline_module,
        "build_production_decisions",
        counted_build_production_decisions,
    )

    outputs = _run_minimal_alignment(tmp_path, monkeypatch)

    assert outputs.workbook is not None
    assert outputs.review_html is not None
    assert outputs.matrix_tsv is not None
    assert calls == 1


def _divergent_owner_decisions(
    matrix,
    config: AlignmentConfig,
) -> ProductionDecisionSet:
    rebuilt = build_production_decisions(matrix, config)
    return ProductionDecisionSet(
        cells={
            key: replace(
                decision,
                raw_status="absent",
                production_status="detected",
                rescue_tier="",
                write_matrix_value=True,
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


def _run_minimal_alignment(
    tmp_path: Path,
    monkeypatch,
) -> pipeline_module.AlignmentRunOutputs:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    _patch_owner_pipeline_to_matrix(monkeypatch)
    return pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
    )
