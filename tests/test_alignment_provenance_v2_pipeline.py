from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_owner_backfill_peak_fact_provenance import (
    _column,
    _cross_typed_id,
    _detected_cell,
    _live_available_inputs,
    _production_feature,
    _read_projection_rows,
    _recompute_cell_projection_id,
    _reseal_projection_content_id,
    _write_projection_rows,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    compose_owner_backfill_peak_fact_provenance,
    read_owner_backfill_peak_fact_provenance_tsv,
    write_owner_backfill_peak_fact_provenance_tsv,
)
from xic_extractor.alignment.pipeline import _owner_backfill_peak_fact_provenance
from xic_extractor.alignment.primary_consolidation import (
    consolidate_primary_family_rows,
)
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)


def test_pipeline_v2_persists_and_replays_source_to_winner_lineage(
    tmp_path: Path,
) -> None:
    live, matrix, config = _moved_final_matrix(tmp_path)
    decisions = build_production_decisions(matrix, config)

    batch = _owner_backfill_peak_fact_provenance(
        (live.observation,),
        matrix=matrix,
        alignment_config=config,
        production_decisions=decisions,
        feature_order=("WINNER",),
        sample_order=matrix.sample_order,
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    physical = next(row for row in batch.rows if row.row_kind == "physical_question")
    cell = next(row for row in batch.rows if row.row_kind == "cell_projection")
    assert (physical.group_hypothesis_id, physical.public_family_id) == (
        live.stage.group_hypothesis_id,
        live.stage.public_family_id,
    )
    assert (cell.group_hypothesis_id, cell.public_family_id) == (
        "WINNER",
        "WINNER",
    )
    assert (
        cell.consolidation_state,
        cell.consolidation_winner_group_hypothesis_id,
        cell.consolidation_source_group_hypothesis_id,
    ) == (
        "moved_to_primary_winner",
        "WINNER",
        live.stage.group_hypothesis_id,
    )
    assert cell.selected_work_unit_key == physical.work_unit_key
    assert cell.selected_fact_content_id == physical.fact_content_id

    path = write_owner_backfill_peak_fact_provenance_tsv(
        tmp_path / "moved-v2.tsv",
        batch,
    )
    composed = compose_owner_backfill_peak_fact_provenance(
        read_owner_backfill_peak_fact_provenance_tsv(path),
        live_observation_by_work={live.stage.work_unit_key: live.observation},
        live_extraction_by_work={live.stage.work_unit_key: live.extraction},
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        live_matrix=matrix,
        alignment_config=config,
        production_decisions=decisions,
    )
    assert composed.physical_claims[0].physical_claim == "verified"
    assert composed.cell_claims[0].cell_projection_claim == "verified"


def test_pipeline_v2_accepts_pre_backfill_winner_with_multi_source_lineage(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    source_group_id = live.stage.group_hypothesis_id
    winner = replace(
        live.observation.cell,
        consolidation_state="primary_winner",
        consolidation_winner_group_hypothesis_id=source_group_id,
        consolidation_source_group_hypothesis_id=(
            f"{source_group_id};SOURCE-GROUP-2"
        ),
    )
    matrix = AlignmentMatrix(
        clusters=(_production_feature(live.stage),),
        cells=(
            winner,
            _detected_cell("detected-1", live.evaluation.integration, stage=live.stage),
            _detected_cell("detected-2", live.evaluation.integration, stage=live.stage),
        ),
        sample_order=(live.stage.sample_stem, "detected-1", "detected-2"),
    )
    config = AlignmentConfig()
    decisions = build_production_decisions(matrix, config)

    batch = _owner_backfill_peak_fact_provenance(
        (live.observation,),
        matrix=matrix,
        alignment_config=config,
        production_decisions=decisions,
        feature_order=(source_group_id,),
        sample_order=matrix.sample_order,
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    cell = next(row for row in batch.rows if row.row_kind == "cell_projection")
    assert cell.group_hypothesis_id == source_group_id
    assert cell.consolidation_state == "primary_winner"
    assert cell.consolidation_source_group_hypothesis_id == (
        f"{source_group_id};SOURCE-GROUP-2"
    )
    path = write_owner_backfill_peak_fact_provenance_tsv(
        tmp_path / "pre-backfill-winner-v2.tsv",
        batch,
    )
    composed = compose_owner_backfill_peak_fact_provenance(
        read_owner_backfill_peak_fact_provenance_tsv(path),
        live_observation_by_work={live.stage.work_unit_key: live.observation},
        live_extraction_by_work={live.stage.work_unit_key: live.extraction},
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        live_matrix=matrix,
        alignment_config=config,
        production_decisions=decisions,
    )
    assert composed.physical_claims[0].physical_claim == "verified"
    assert composed.cell_claims[0].cell_projection_claim == "verified"


def test_pipeline_v2_replays_lineage_minted_by_primary_consolidation(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    source = live.observation.cell
    source_duplicate = replace(
        source,
        status="duplicate_assigned",
        reason=("duplicate MS1 peak claim; winner=WINNER; original_status=rescued"),
    )
    detected = tuple(
        replace(
            _detected_cell(sample, live.evaluation.integration, stage=live.stage),
            cluster_id="WINNER",
            group_hypothesis_id="WINNER",
            public_family_id="WINNER",
        )
        for sample in ("detected-1", "detected-2")
    )
    pre_consolidation = AlignmentMatrix(
        clusters=(live.feature, _winner_feature(live.stage, "WINNER")),
        cells=(source_duplicate, *detected),
        sample_order=(live.stage.sample_stem, "detected-1", "detected-2"),
    )
    config = AlignmentConfig()

    matrix = consolidate_primary_family_rows(pre_consolidation, config)
    moved = next(
        cell
        for cell in matrix.cells
        if cell.sample_stem == "sample-b" and cell.status == "rescued"
    )
    assert (
        moved.consolidation_state,
        moved.consolidation_source_group_hypothesis_id,
        moved.consolidation_winner_group_hypothesis_id,
    ) == (
        "moved_to_primary_winner",
        live.stage.group_hypothesis_id,
        "WINNER",
    )
    decisions = build_production_decisions(matrix, config)
    batch = _owner_backfill_peak_fact_provenance(
        (live.observation,),
        matrix=matrix,
        alignment_config=config,
        production_decisions=decisions,
        feature_order=(live.observation.cell.cluster_id, "WINNER"),
        sample_order=matrix.sample_order,
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    path = write_owner_backfill_peak_fact_provenance_tsv(
        tmp_path / "primary-consolidation-v2.tsv",
        batch,
    )

    composed = compose_owner_backfill_peak_fact_provenance(
        read_owner_backfill_peak_fact_provenance_tsv(path),
        live_observation_by_work={live.stage.work_unit_key: live.observation},
        live_extraction_by_work={live.stage.work_unit_key: live.extraction},
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        live_matrix=matrix,
        alignment_config=config,
        production_decisions=decisions,
    )
    assert composed.physical_claims[0].physical_claim == "verified"
    assert composed.cell_claims[0].cell_projection_claim == "verified"


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            {"consolidation_source_group_hypothesis_id": "OTHER-SOURCE"},
            "final written rescued cells require exact provenance coverage",
        ),
        (
            {"consolidation_winner_group_hypothesis_id": "OTHER-WINNER"},
            "final written rescued cells require exact provenance coverage",
        ),
    ),
)
def test_pipeline_v2_fails_closed_for_crossed_consolidation_lineage(
    tmp_path: Path,
    mutation: dict[str, str],
    message: str,
) -> None:
    live, matrix, config = _moved_final_matrix(tmp_path)
    moved = next(cell for cell in matrix.cells if cell.sample_stem == "sample-b")
    crossed = replace(moved, **mutation)
    crossed_matrix = replace(
        matrix,
        cells=tuple(crossed if cell is moved else cell for cell in matrix.cells),
    )

    with pytest.raises(ValueError, match=message):
        _owner_backfill_peak_fact_provenance(
            (live.observation,),
            matrix=crossed_matrix,
            alignment_config=config,
            production_decisions=build_production_decisions(crossed_matrix, config),
            feature_order=("WINNER",),
            sample_order=crossed_matrix.sample_order,
            signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        )


def test_pipeline_v2_does_not_use_area_equality_as_work_or_fact_fallback(
    tmp_path: Path,
) -> None:
    live, matrix, config = _moved_final_matrix(tmp_path)
    moved = next(cell for cell in matrix.cells if cell.sample_stem == "sample-b")
    assert moved.selected_integration is not None
    crossed_integration = replace(
        moved.selected_integration,
        boundary_sources=(*moved.selected_integration.boundary_sources, "crossed-fact"),
    )
    assert crossed_integration.area_ms1_morphology == (
        moved.selected_integration.area_ms1_morphology
    )
    crossed = replace(moved, selected_integration=crossed_integration)
    crossed_matrix = replace(
        matrix,
        cells=tuple(crossed if cell is moved else cell for cell in matrix.cells),
    )

    with pytest.raises(
        ValueError,
        match="selected work unit has no exact final Matrix cell",
    ):
        _owner_backfill_peak_fact_provenance(
            (live.observation,),
            matrix=crossed_matrix,
            alignment_config=config,
            production_decisions=build_production_decisions(crossed_matrix, config),
            feature_order=("WINNER",),
            sample_order=crossed_matrix.sample_order,
            signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        )


@pytest.mark.parametrize(
    "field",
    ("selected_work_unit_key", "selected_fact_content_id"),
)
def test_pipeline_v2_strict_reader_rejects_crossed_work_or_fact_binding(
    tmp_path: Path,
    field: str,
) -> None:
    live, matrix, config = _moved_final_matrix(tmp_path)
    batch = _owner_backfill_peak_fact_provenance(
        (live.observation,),
        matrix=matrix,
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
        feature_order=("WINNER",),
        sample_order=matrix.sample_order,
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    path = write_owner_backfill_peak_fact_provenance_tsv(
        tmp_path / f"crossed-{field}.tsv",
        batch,
    )
    header, rows = _read_projection_rows(path)
    cell = rows[2]
    if field == "selected_work_unit_key":
        cell[_column(field)] = f"crossed:{cell[_column(field)]}"
    else:
        cell[_column(field)] = _cross_typed_id(cell[_column(field)])
    _recompute_cell_projection_id(cell)
    _reseal_projection_content_id(cell)
    _write_projection_rows(path, header, rows)

    with pytest.raises(
        ValueError,
        match="cell projection lacks its selected physical fact",
    ):
        read_owner_backfill_peak_fact_provenance_tsv(path)


def test_pipeline_v2_rejects_two_legal_final_cells_for_one_selected_work(
    tmp_path: Path,
) -> None:
    live, matrix, config = _moved_final_matrix(tmp_path)
    first = next(cell for cell in matrix.cells if cell.sample_stem == "sample-b")
    second = replace(
        first,
        cluster_id="WINNER-2",
        group_hypothesis_id="WINNER-2",
        public_family_id="WINNER-2",
        consolidation_winner_group_hypothesis_id="WINNER-2",
    )
    second_feature = _winner_feature(live.stage, "WINNER-2")
    second_detected = tuple(
        replace(
            cell,
            cluster_id="WINNER-2",
            group_hypothesis_id="WINNER-2",
            public_family_id="WINNER-2",
        )
        for cell in matrix.cells
        if cell.status == "detected"
    )
    two_winner_matrix = replace(
        matrix,
        clusters=(*matrix.clusters, second_feature),
        cells=(*matrix.cells, second, *second_detected),
    )
    decisions = build_production_decisions(two_winner_matrix, config)
    assert decisions.cell("WINNER", live.stage.sample_stem).write_matrix_value
    assert decisions.cell("WINNER-2", live.stage.sample_stem).write_matrix_value

    with pytest.raises(
        ValueError,
        match="selected work unit maps to multiple final Matrix cells",
    ):
        _owner_backfill_peak_fact_provenance(
            (live.observation,),
            matrix=two_winner_matrix,
            alignment_config=config,
            production_decisions=decisions,
            feature_order=("WINNER", "WINNER-2"),
            sample_order=two_winner_matrix.sample_order,
            signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        )


def test_pipeline_v2_ignores_multiple_legal_nonwriting_cells_for_one_work(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    exact = live.observation.cell
    moved = replace(
        exact,
        cluster_id="WINNER",
        group_hypothesis_id="WINNER",
        public_family_id="WINNER",
        consolidation_state="moved_to_primary_winner",
        consolidation_winner_group_hypothesis_id="WINNER",
        consolidation_source_group_hypothesis_id=live.stage.group_hypothesis_id,
    )
    matrix = AlignmentMatrix(
        clusters=(live.feature, _winner_feature(live.stage, "WINNER")),
        cells=(exact, moved),
        sample_order=(live.stage.sample_stem,),
    )
    config = AlignmentConfig()
    decisions = build_production_decisions(matrix, config)
    assert all(not decision.write_matrix_value for decision in decisions.cells.values())

    batch = _owner_backfill_peak_fact_provenance(
        (live.observation,),
        matrix=matrix,
        alignment_config=config,
        production_decisions=decisions,
        feature_order=(live.stage.public_family_id, "WINNER"),
        sample_order=matrix.sample_order,
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    assert tuple(row.row_kind for row in batch.rows) == (
        "run_profile",
        "physical_question",
    )


def test_pipeline_v2_requires_reverse_coverage_for_every_written_rescued_cell(
    tmp_path: Path,
) -> None:
    _live, matrix, config = _moved_final_matrix(tmp_path)

    with pytest.raises(
        ValueError,
        match="final written rescued cells require exact provenance coverage",
    ):
        _owner_backfill_peak_fact_provenance(
            (),
            matrix=matrix,
            alignment_config=config,
            production_decisions=build_production_decisions(matrix, config),
            feature_order=("WINNER",),
            sample_order=matrix.sample_order,
            signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        )


def test_pipeline_v2_ignores_loser_with_same_integration_but_different_lineage(
    tmp_path: Path,
) -> None:
    selected, matrix, config = _moved_final_matrix(tmp_path / "selected")
    loser = _live_available_inputs(tmp_path / "loser", feature_id="LOSER")
    assert selected.observation.facts is not None
    assert loser.observation.facts is not None
    assert loser.observation.facts.integration == selected.observation.facts.integration

    batch = _owner_backfill_peak_fact_provenance(
        (selected.observation, loser.observation),
        matrix=matrix,
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
        feature_order=("WINNER", "LOSER"),
        sample_order=matrix.sample_order,
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    physical_rows = tuple(
        row for row in batch.rows if row.row_kind == "physical_question"
    )
    cell_rows = tuple(row for row in batch.rows if row.row_kind == "cell_projection")
    assert len(physical_rows) == 2
    assert len(cell_rows) == 1
    assert cell_rows[0].selected_work_unit_key == selected.stage.work_unit_key


def _moved_final_matrix(tmp_path: Path):
    live = _live_available_inputs(tmp_path)
    source = live.observation.cell
    moved = replace(
        source,
        cluster_id="WINNER",
        group_hypothesis_id="WINNER",
        public_family_id="WINNER",
        consolidation_state="moved_to_primary_winner",
        consolidation_winner_group_hypothesis_id="WINNER",
        consolidation_source_group_hypothesis_id=live.stage.group_hypothesis_id,
    )
    detected = tuple(
        replace(
            _detected_cell(sample, live.evaluation.integration, stage=live.stage),
            cluster_id="WINNER",
            group_hypothesis_id="WINNER",
            public_family_id="WINNER",
        )
        for sample in ("detected-1", "detected-2")
    )
    matrix = AlignmentMatrix(
        clusters=(_winner_feature(live.stage, "WINNER"),),
        cells=(moved, *detected),
        sample_order=(live.stage.sample_stem, "detected-1", "detected-2"),
    )
    return live, matrix, AlignmentConfig()


def _winner_feature(stage, winner_id: str):
    source = _production_feature(stage)
    return SimpleNamespace(
        **{
            **vars(source),
            "feature_family_id": winner_id,
            "group_hypothesis_id": winner_id,
            "public_family_id": winner_id,
        }
    )
