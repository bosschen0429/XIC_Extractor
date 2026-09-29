from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest

from tests.test_alignment_owner_backfill import _feature, _peak_config
from xic_extractor.alignment.backfill_peak_fact import (
    BackfillExtractionResult,
    build_backfill_result,
)
from xic_extractor.alignment.backfill_peak_fact import (
    BackfillPeakFactObservation as LiveBackfillPeakFactObservation,
)
from xic_extractor.alignment.backfill_peak_fact_provenance import (
    _PROVENANCE_ROWS_PER_CHUNK,
    BACKFILL_AUTHORITY_CELL_FIELDS,
    BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS,
    BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION,
    BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION,
    SELECTED_INTEGRATION_AUTHORITY_FIELDS,
    BackfillCellProjection,
    BackfillPeakFactObservation,
    BackfillPeakFactProvenanceBatch,
    BackfillPeakFactStageProjection,
    _iter_backfill_peak_fact_provenance_chunks,
    backfill_authority_cell_projection,
    backfill_cell_lineage_matches,
    backfill_cell_projection,
    backfill_peak_fact_stage_projection_from_observation,
    build_backfill_peak_fact_provenance_batch,
    compose_backfill_peak_fact_provenance,
    read_backfill_peak_fact_provenance_tsv,
    render_backfill_peak_fact_provenance_tsv,
    selected_integration_authority_projection,
    write_backfill_peak_fact_provenance_tsv,
)
from xic_extractor.alignment.backfill_request_plan import (
    mint_backfill_request_identity,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import (
    AlignedCell,
    AlignmentMatrix,
    AlignmentRowLike,
)
from xic_extractor.alignment.production_decisions import (
    ProductionCellDecision,
    ProductionDecisionSet,
    build_production_decisions,
    production_cell_decision_projection,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.xic_models import XICRequest, XICTrace


@dataclass(frozen=True, slots=True)
class _OwnerBearingMutationCase:
    owner_class: str
    expected_replay_error: str


_V4_OWNER_BEARING_CLASS_CENSUS = (
    "trace",
    "prepared_projection",
    "universe",
    "question",
    "answer_facts",
    "alignment_stage_binding",
    "selected_cell_content_authority",
    "production_decision",
    "writer_final_cell_projection",
)

_OWNER_BEARING_MUTATION_CASES = (
    _OwnerBearingMutationCase("trace", "trace_content_id"),
    _OwnerBearingMutationCase(
        "prepared_projection",
        "persisted physical row differs from live owner replay",
    ),
    _OwnerBearingMutationCase(
        "universe",
        "persisted physical row differs from live owner replay",
    ),
    _OwnerBearingMutationCase(
        "question",
        "persisted physical row differs from live owner replay",
    ),
    _OwnerBearingMutationCase(
        "answer_facts",
        "persisted physical row differs from live owner replay",
    ),
    _OwnerBearingMutationCase(
        "alignment_stage_binding",
        "live Alignment stage projection crosses persisted work",
    ),
    _OwnerBearingMutationCase(
        "selected_cell_content_authority",
        "persisted cell projection differs from live owner replay",
    ),
    _OwnerBearingMutationCase(
        "production_decision",
        "persisted cell projection differs from live owner replay",
    ),
    _OwnerBearingMutationCase(
        "writer_final_cell_projection",
        "persisted cell projection differs from live owner replay",
    ),
)


def test_writer_round_trips_exact_61_column_three_kind_projection(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    stage = live.stage
    extraction = live.extraction
    evaluation = live.evaluation
    facts = evaluation.facts
    assert facts is not None
    cell = live.observation.cell
    authority = backfill_authority_cell_projection(
        stage=stage,
        cell=cell,
        profile_id=DEFAULT_SIGNAL_PROCESSING_PROFILE.profile_id,
        selected_fact_content_id=facts.fact_content_id,
        physical_answer_id=facts.physical_answer_id,
        physical_segment_id=facts.physical_segment_id,
    )
    production = production_cell_decision_projection(
        ProductionCellDecision(
            feature_family_id=cell.cluster_id,
            sample_stem=cell.sample_stem,
            raw_status=cell.status,
            production_status="accepted_backfill",
            backfill_tier="accepted_backfill",
            write_matrix_value=True,
            matrix_value=cell.matrix_area,
            blank_reason="",
        ),
        cell=cell,
        config=AlignmentConfig(),
        owner_backfill_cell_content_id=authority.owner_backfill_cell_content_id,
    )
    batch = build_backfill_peak_fact_provenance_batch(
        (BackfillPeakFactObservation(stage, evaluation),),
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        cell_projections=(
            backfill_cell_projection(
                authority_cell=authority,
                production_decision=production,
            ),
        ),
    )
    path = write_backfill_peak_fact_provenance_tsv(
        tmp_path / "provenance.tsv",
        batch,
    )

    assert len(BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS) == 61
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert b"\r" not in path.read_bytes()
    with path.open(encoding="utf-8", newline="") as handle:
        raw_rows = list(csv.DictReader(handle, delimiter="\t"))
    assert tuple(raw_rows[0]) == BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS
    assert [row["row_kind"] for row in raw_rows] == [
        "run_profile",
        "physical_question",
        "cell_projection",
    ]
    assert raw_rows[0]["schema_version"] == (
        BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION
    )
    assert raw_rows[0]["work_unit_key"] == ""
    assert raw_rows[1]["segment_candidate_ids"].startswith("[")
    assert raw_rows[1]["selected_work_unit_key"] == ""
    assert raw_rows[2]["trace_content_id"] == ""
    assert raw_rows[2]["writer_schema_version"] == (
        BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION
    )
    assert read_backfill_peak_fact_provenance_tsv(path).rows == batch.rows
    assert not (tmp_path / ".provenance.tsv.tmp").exists()
    assert extraction.request_identity.request_identity_id == stage.request_identity_id


def test_strict_reader_rejects_bom_decimal_float_and_noncanonical_json(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    stage = live.stage
    evaluation = live.evaluation
    batch = build_backfill_peak_fact_provenance_batch(
        (BackfillPeakFactObservation(stage, evaluation),),
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    path = write_backfill_peak_fact_provenance_tsv(tmp_path / "strict.tsv", batch)
    original = path.read_bytes()
    path.write_bytes(b"\xef\xbb\xbf" + original)
    with pytest.raises(ValueError, match="BOM"):
        read_backfill_peak_fact_provenance_tsv(path)

    path.write_bytes(original)
    lines = path.read_text(encoding="utf-8").splitlines()
    values = lines[2].split("\t")
    values[BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS.index("query_rt_min")] = "1.5"
    path.write_bytes(
        (lines[0] + "\n" + lines[1] + "\n" + "\t".join(values) + "\n").encode("utf-8")
    )
    with pytest.raises(ValueError, match="hexadecimal float"):
        read_backfill_peak_fact_provenance_tsv(path)

    path.write_bytes(original)
    lines = path.read_text(encoding="utf-8").splitlines()
    values = lines[2].split("\t")
    field = BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS.index("segment_candidate_ids")
    values[field] = values[field].replace("[", "[ ", 1)
    path.write_bytes(
        (lines[0] + "\n" + lines[1] + "\n" + "\t".join(values) + "\n").encode("utf-8")
    )
    with pytest.raises(ValueError, match="canonical compact JSON"):
        read_backfill_peak_fact_provenance_tsv(path)


def test_strict_reader_rejects_non_writer_bytes(tmp_path: Path) -> None:
    batch = build_backfill_peak_fact_provenance_batch(
        (),
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    path = write_backfill_peak_fact_provenance_tsv(
        tmp_path / "canonical.tsv",
        batch,
    )
    original = path.read_bytes()

    path.write_bytes(original.removesuffix(b"\n"))
    with pytest.raises(ValueError, match="LF-terminated"):
        read_backfill_peak_fact_provenance_tsv(path)

    lines = original.decode("utf-8").splitlines()
    values = lines[1].split("\t")
    method_index = BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS.index(
        "signal_processing_method"
    )
    values[method_index] = '"gaussian"'
    path.write_bytes((lines[0] + "\n" + "\t".join(values) + "\n").encode("utf-8"))
    with pytest.raises(ValueError, match="not canonical writer output"):
        read_backfill_peak_fact_provenance_tsv(path)


def test_standalone_writer_cleans_its_temp_on_verification_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "standalone.tsv"
    temporary = tmp_path / ".standalone.tsv.tmp"
    original_open = Path.open

    def fail_temp_verification(candidate: Path, *args: object, **kwargs: object):
        # The writer streams the artifact out, then reopens the temp file to
        # verify what landed.  Fail that read-back leg only.
        mode = args[0] if args else kwargs.get("mode", "r")
        if candidate == temporary and "r" in str(mode):
            raise OSError("injected temp verification failure")
        return original_open(candidate, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", fail_temp_verification)

    with pytest.raises(OSError, match="injected temp verification failure"):
        write_backfill_peak_fact_provenance_tsv(
            path,
            build_backfill_peak_fact_provenance_batch(
                (),
                profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
            ),
        )

    assert not path.exists()
    assert not temporary.exists()


def test_run_profile_is_mandatory_even_without_questions(tmp_path: Path) -> None:
    batch = build_backfill_peak_fact_provenance_batch(
        (),
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    assert len(batch.rows) == 1
    assert batch.rows[0].row_kind == "run_profile"
    path = write_backfill_peak_fact_provenance_tsv(tmp_path / "empty.tsv", batch)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_header_is_rejected_and_requires_rerun(tmp_path: Path) -> None:
    path, _live = _write_three_kind_provenance(tmp_path)
    header, rows = _read_projection_rows(path)
    removed = {
        "consolidation_state",
        "consolidation_winner_group_hypothesis_id",
        "consolidation_source_group_hypothesis_id",
    }
    legacy_header = [name for name in header if name not in removed]
    legacy_rows = [
        [value for name, value in zip(header, row, strict=True) if name not in removed]
        for row in rows
    ]
    _write_projection_rows(path, legacy_header, legacy_rows)

    with pytest.raises(ValueError, match="provenance.*rerun alignment"):
        read_backfill_peak_fact_provenance_tsv(path)


def test_moved_cell_lineage_uses_source_group_not_area(tmp_path: Path) -> None:
    live = _live_available_inputs(tmp_path)
    stage = live.stage
    source = live.observation.cell
    moved = replace(
        source,
        cluster_id="WINNER",
        group_hypothesis_id="WINNER",
        public_family_id="WINNER",
        area=(source.area or 0.0) + 999999.0,
        consolidation_state="moved_to_primary_winner",
        consolidation_winner_group_hypothesis_id="WINNER",
        consolidation_source_group_hypothesis_id=stage.group_hypothesis_id,
    )
    assert backfill_cell_lineage_matches(stage, moved) is True
    backfill_authority_cell_projection(
        stage=stage,
        cell=moved,
        profile_id=DEFAULT_SIGNAL_PROCESSING_PROFILE.profile_id,
        selected_fact_content_id=live.evaluation.facts.fact_content_id,
        physical_answer_id=live.evaluation.answer.physical_answer_id,
        physical_segment_id=live.evaluation.facts.physical_segment_id,
    )

    crossed = replace(
        moved,
        consolidation_source_group_hypothesis_id="OTHER-SOURCE",
    )
    assert backfill_cell_lineage_matches(stage, crossed) is False
    with pytest.raises(ValueError, match="cell lineage"):
        backfill_authority_cell_projection(
            stage=stage,
            cell=crossed,
            profile_id=DEFAULT_SIGNAL_PROCESSING_PROFILE.profile_id,
            selected_fact_content_id=live.evaluation.facts.fact_content_id,
            physical_answer_id=live.evaluation.answer.physical_answer_id,
            physical_segment_id=live.evaluation.facts.physical_segment_id,
        )


def test_primary_winner_requires_complete_winner_and_source_lineage(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    stage = live.stage
    primary = replace(
        live.observation.cell,
        consolidation_state="primary_winner",
        consolidation_winner_group_hypothesis_id=stage.group_hypothesis_id,
        consolidation_source_group_hypothesis_id=stage.group_hypothesis_id,
    )
    assert backfill_cell_lineage_matches(stage, primary) is True
    authority = backfill_authority_cell_projection(
        stage=stage,
        cell=primary,
        profile_id=DEFAULT_SIGNAL_PROCESSING_PROFILE.profile_id,
        selected_fact_content_id=live.evaluation.facts.fact_content_id,
        physical_answer_id=live.evaluation.answer.physical_answer_id,
        physical_segment_id=live.evaluation.facts.physical_segment_id,
    )
    production = production_cell_decision_projection(
        ProductionCellDecision(
            feature_family_id=primary.cluster_id,
            sample_stem=primary.sample_stem,
            raw_status=primary.status,
            production_status="accepted_backfill",
            backfill_tier="accepted_backfill",
            write_matrix_value=True,
            matrix_value=primary.matrix_area,
            blank_reason="",
        ),
        cell=primary,
        config=AlignmentConfig(),
        owner_backfill_cell_content_id=authority.owner_backfill_cell_content_id,
    )
    valid_projection = backfill_cell_projection(
        authority_cell=authority,
        production_decision=production,
    )

    with pytest.raises(ValueError, match="invalid primary lineage"):
        build_backfill_peak_fact_provenance_batch(
            (
                BackfillPeakFactObservation(
                    stage,
                    live.evaluation,
                ),
            ),
            profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
            cell_projections=(
                replace(
                    valid_projection,
                    consolidation_source_group_hypothesis_id="",
                ),
            ),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        (
            "profile_schema_version",
            "unsupported_signal_processing_profile",
            "schema_version",
        ),
        ("signal_processing_method", "savgol", "must be 'gaussian'"),
        ("signal_processing_window_points", "16", "odd integer"),
        ("physical_signal_formula", "arbitrary", "physical_signal_formula"),
        ("profile_hash", "0" * 64, "profile_hash"),
        ("profile_id", "signal-profile:" + "0" * 64, "profile_id"),
    ),
)
def test_run_profile_requires_exact_owner_reconstruction(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    path = write_backfill_peak_fact_provenance_tsv(
        tmp_path / f"tampered-{field}.tsv",
        build_backfill_peak_fact_provenance_batch(
            (),
            profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        ),
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    values = lines[1].split("\t")
    values[BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS.index(field)] = value
    _reseal_projection_content_id(values)
    path.write_bytes((lines[0] + "\n" + "\t".join(values) + "\n").encode("utf-8"))

    with pytest.raises((TypeError, ValueError), match=message):
        read_backfill_peak_fact_provenance_tsv(path)


@pytest.mark.parametrize(
    ("attack", "message"),
    (
        ("cross_key", "lacks its selected physical fact"),
        ("stale_profile", "crosses the run profile"),
        ("partial", "acquisition_source_id must be non-empty"),
        ("reordered", "row kinds are out of order"),
        ("duplicate", "duplicate work_unit_key"),
    ),
)
def test_reader_rejects_owner_bearing_persisted_attack_classes(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    path, _live = _write_three_kind_provenance(tmp_path)
    header, rows = _read_projection_rows(path)
    physical = rows[1]
    cell = rows[2]

    if attack == "cross_key":
        cell[_column("selected_work_unit_key")] = "missing-work-unit"
        _recompute_cell_projection_id(cell)
        _reseal_projection_content_id(cell)
    elif attack == "stale_profile":
        stale = SignalProcessingProfile(window_points=17)
        physical[_column("profile_schema_version")] = stale.schema_version
        physical[_column("profile_id")] = stale.profile_id
        physical[_column("profile_hash")] = stale.profile_hash
        physical[_column("signal_processing_method")] = stale.method
        physical[_column("signal_processing_window_points")] = str(stale.window_points)
        physical[_column("physical_signal_formula")] = stale.physical_signal_formula
        _reseal_projection_content_id(physical)
    elif attack == "partial":
        physical[_column("acquisition_source_id")] = ""
        _reseal_projection_content_id(physical)
    elif attack == "reordered":
        rows = [rows[0], cell, physical]
    elif attack == "duplicate":
        rows = [rows[0], physical, physical.copy(), cell]
    else:  # pragma: no cover - closed parameter set
        raise AssertionError(attack)
    _write_projection_rows(path, header, rows)

    with pytest.raises(ValueError, match=message):
        read_backfill_peak_fact_provenance_tsv(path)


def test_resealed_owner_field_tamper_stays_untrusted_until_live_replay(
    tmp_path: Path,
) -> None:
    path, live = _write_three_kind_provenance(tmp_path)
    header, rows = _read_projection_rows(path)
    physical = rows[1]
    physical[_column("selection_subject_id")] = "owner-backfill-subject:" + "0" * 64
    _reseal_projection_content_id(physical)
    _write_projection_rows(path, header, rows)

    read_result = read_backfill_peak_fact_provenance_tsv(path)
    assert read_result.projection_integrity == "verified"
    with pytest.raises(ValueError, match="crosses persisted work"):
        compose_backfill_peak_fact_provenance(
            read_result,
            live_observation_by_work={live.stage.work_unit_key: live.observation},
        )


@pytest.mark.parametrize(
    "case",
    _OWNER_BEARING_MUTATION_CASES,
    ids=lambda case: case.owner_class,
)
def test_resealed_owner_bearing_class_requires_live_owner_replay(
    tmp_path: Path,
    case: _OwnerBearingMutationCase,
) -> None:
    assert _V4_OWNER_BEARING_CLASS_CENSUS == (
        "trace",
        "prepared_projection",
        "universe",
        "question",
        "answer_facts",
        "alignment_stage_binding",
        "selected_cell_content_authority",
        "production_decision",
        "writer_final_cell_projection",
    )
    assert len(_V4_OWNER_BEARING_CLASS_CENSUS) == 9
    assert (
        tuple(item.owner_class for item in _OWNER_BEARING_MUTATION_CASES)
        == _V4_OWNER_BEARING_CLASS_CENSUS
    )

    path, live = _write_three_kind_provenance(tmp_path)
    header, rows = _read_projection_rows(path)
    _mutate_owner_bearing_projection(rows, owner_class=case.owner_class)
    _write_projection_rows(path, header, rows)

    read_result = read_backfill_peak_fact_provenance_tsv(path)
    assert read_result.projection_integrity == "verified"
    matrix, config, decisions = _live_final_owner_inputs(live)
    with pytest.raises(ValueError, match=case.expected_replay_error):
        compose_backfill_peak_fact_provenance(
            read_result,
            live_observation_by_work={
                live.stage.work_unit_key: live.observation,
            },
            live_extraction_by_work={
                live.stage.work_unit_key: live.extraction,
            },
            profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
            live_matrix=matrix,
            alignment_config=config,
            production_decisions=decisions,
        )


@pytest.mark.parametrize(
    ("attack", "message"),
    (
        ("integer", "canonical non-negative integer"),
        ("float", "canonical finite hexadecimal float"),
        ("sequence_duplicate", "must not contain duplicates"),
        ("sequence_type", "JSON array of strings"),
        ("sequence_count", "does not match"),
    ),
)
def test_strict_codec_rejects_scalar_and_sequence_attack_classes(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    path, _live = _write_three_kind_provenance(tmp_path)
    header, rows = _read_projection_rows(path)
    physical = rows[1]

    if attack == "integer":
        physical[_column("candidate_index")] = "01"
    elif attack == "float":
        physical[_column("query_rt_min")] = physical[_column("query_rt_min")].upper()
    elif attack == "sequence_duplicate":
        ids = json.loads(physical[_column("segment_candidate_ids")])
        assert ids
        physical[_column("segment_candidate_ids")] = json.dumps(
            [ids[0], ids[0]],
            separators=(",", ":"),
        )
    elif attack == "sequence_type":
        physical[_column("segment_candidate_ids")] = "[1]"
    elif attack == "sequence_count":
        physical[_column("segment_candidate_count")] = "999"
    else:  # pragma: no cover - closed parameter set
        raise AssertionError(attack)
    _reseal_projection_content_id(physical)
    _write_projection_rows(path, header, rows)

    with pytest.raises(ValueError, match=message):
        read_backfill_peak_fact_provenance_tsv(path)


def test_physical_claim_requires_live_alignment_and_acquisition_owner_replay(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    stage = live.stage
    extraction = live.extraction
    evaluation = live.evaluation
    path = write_backfill_peak_fact_provenance_tsv(
        tmp_path / "composition.tsv",
        build_backfill_peak_fact_provenance_batch(
            (BackfillPeakFactObservation(stage, evaluation),),
            profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        ),
    )
    read_result = read_backfill_peak_fact_provenance_tsv(path)

    unavailable = compose_backfill_peak_fact_provenance(read_result)
    assert unavailable.physical_claims[0].stage_claim == (
        "unavailable_without_live_owner"
    )
    assert unavailable.physical_claims[0].physical_claim == (
        "unavailable_without_live_owner"
    )
    verified = compose_backfill_peak_fact_provenance(
        read_result,
        live_observation_by_work={stage.work_unit_key: live.observation},
        live_extraction_by_work={stage.work_unit_key: extraction},
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    assert verified.physical_claims[0].stage_claim == "verified"
    assert verified.physical_claims[0].physical_claim == "verified"

    with pytest.raises(TypeError, match="must be minted from a live"):
        replace(stage, query_rt_min=stage.query_rt_min + 0.01)

    crossed = _live_available_inputs(tmp_path / "crossed", feature_id="FAM000002")
    with pytest.raises(ValueError, match="crosses persisted work"):
        compose_backfill_peak_fact_provenance(
            read_result,
            live_observation_by_work={
                stage.work_unit_key: crossed.observation,
            },
        )


def test_zero_point_replay_uses_live_request_bounds(tmp_path: Path) -> None:
    live = _live_available_inputs(tmp_path, empty=True)
    stage = live.stage
    path = write_backfill_peak_fact_provenance_tsv(
        tmp_path / "zero.tsv",
        build_backfill_peak_fact_provenance_batch(
            (BackfillPeakFactObservation(stage, live.evaluation),),
            profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        ),
    )

    composed = compose_backfill_peak_fact_provenance(
        read_backfill_peak_fact_provenance_tsv(path),
        live_observation_by_work={stage.work_unit_key: live.observation},
        live_extraction_by_work={stage.work_unit_key: live.extraction},
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )

    assert composed.physical_claims[0].physical_claim == "verified"


def test_numeric_cell_claim_lifts_only_after_all_three_owners_replay(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    stage = live.stage
    extraction = live.extraction
    evaluation = live.evaluation
    facts = evaluation.facts
    assert facts is not None
    cell = live.observation.cell
    matrix = AlignmentMatrix(
        clusters=(_production_feature(stage),),
        cells=(
            cell,
            _detected_cell("detected-1", evaluation.integration, stage=stage),
            _detected_cell("detected-2", evaluation.integration, stage=stage),
        ),
        sample_order=(stage.sample_stem, "detected-1", "detected-2"),
    )
    config = AlignmentConfig()
    decisions = build_production_decisions(matrix, config)
    selected_decision = decisions.cell(cell.cluster_id, cell.sample_stem)
    assert selected_decision.production_status == "accepted_backfill"
    authority = backfill_authority_cell_projection(
        stage=stage,
        cell=cell,
        profile_id=DEFAULT_SIGNAL_PROCESSING_PROFILE.profile_id,
        selected_fact_content_id=facts.fact_content_id,
        physical_answer_id=facts.physical_answer_id,
        physical_segment_id=facts.physical_segment_id,
    )
    production = production_cell_decision_projection(
        selected_decision,
        cell=cell,
        config=config,
        owner_backfill_cell_content_id=authority.owner_backfill_cell_content_id,
    )
    path = write_backfill_peak_fact_provenance_tsv(
        tmp_path / "three-owner.tsv",
        build_backfill_peak_fact_provenance_batch(
            (BackfillPeakFactObservation(stage, evaluation),),
            profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
            cell_projections=(
                backfill_cell_projection(
                    authority_cell=authority,
                    production_decision=production,
                ),
            ),
        ),
    )
    read_result = read_backfill_peak_fact_provenance_tsv(path)
    physical_inputs = {
        "live_observation_by_work": {stage.work_unit_key: live.observation},
        "live_extraction_by_work": {stage.work_unit_key: extraction},
        "profile": DEFAULT_SIGNAL_PROCESSING_PROFILE,
    }
    complete = compose_backfill_peak_fact_provenance(
        read_result,
        **physical_inputs,
        live_matrix=matrix,
        alignment_config=config,
        production_decisions=decisions,
    )
    assert complete.physical_claims[0].physical_claim == "verified"
    assert complete.cell_claims[0].cell_projection_claim == "verified"

    final_inputs = {
        "live_matrix": matrix,
        "alignment_config": config,
        "production_decisions": decisions,
    }
    for missing in physical_inputs:
        incomplete = compose_backfill_peak_fact_provenance(
            read_result,
            **{key: value for key, value in physical_inputs.items() if key != missing},
            **final_inputs,
        )
        assert incomplete.physical_claims[0].physical_claim == (
            "unavailable_without_live_owner"
        )
        assert incomplete.cell_claims[0].cell_projection_claim == (
            "unavailable_without_live_owner"
        )
    for missing in final_inputs:
        incomplete = compose_backfill_peak_fact_provenance(
            read_result,
            **physical_inputs,
            **{key: value for key, value in final_inputs.items() if key != missing},
        )
        assert incomplete.physical_claims[0].physical_claim == "verified"
        assert incomplete.cell_claims[0].cell_projection_claim == (
            "unavailable_without_live_owner"
        )

    crossed_extraction = _crossed_extraction(extraction)
    with pytest.raises(ValueError, match="crosses Alignment stage identity"):
        compose_backfill_peak_fact_provenance(
            read_result,
            live_observation_by_work={stage.work_unit_key: live.observation},
            live_extraction_by_work={stage.work_unit_key: crossed_extraction},
            profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        )
    crossed_decisions = ProductionDecisionSet(
        cells={
            **decisions.cells,
            (cell.cluster_id, cell.sample_stem): replace(
                selected_decision,
                blank_reason="crossed",
            ),
        },
        rows=decisions.rows,
    )
    with pytest.raises(ValueError, match="differ from live owner replay"):
        compose_backfill_peak_fact_provenance(
            read_result,
            **physical_inputs,
            live_matrix=matrix,
            alignment_config=config,
            production_decisions=crossed_decisions,
        )


def test_all_authority_projection_fields_change_their_owner_id(
    tmp_path: Path,
) -> None:
    live = _live_available_inputs(tmp_path)
    stage = live.stage
    evaluation = live.evaluation
    facts = evaluation.facts
    assert facts is not None
    integration = selected_integration_authority_projection(evaluation.integration)
    assert len(SELECTED_INTEGRATION_AUTHORITY_FIELDS) == 18
    for name in SELECTED_INTEGRATION_AUTHORITY_FIELDS:
        changed = _changed_value(getattr(integration, name))
        assert (
            replace(
                integration,
                **{name: changed},
            ).selected_integration_authority_content_id
            != integration.selected_integration_authority_content_id
        )

    authority = backfill_authority_cell_projection(
        stage=stage,
        cell=live.observation.cell,
        profile_id=DEFAULT_SIGNAL_PROCESSING_PROFILE.profile_id,
        selected_fact_content_id=facts.fact_content_id,
        physical_answer_id=facts.physical_answer_id,
        physical_segment_id=facts.physical_segment_id,
    )
    assert len(BACKFILL_AUTHORITY_CELL_FIELDS) == 48
    for name in BACKFILL_AUTHORITY_CELL_FIELDS:
        changed = _changed_value(getattr(authority, name))
        assert (
            replace(authority, **{name: changed}).owner_backfill_cell_content_id
            != authority.owner_backfill_cell_content_id
        )


@dataclass(frozen=True, slots=True)
class _LiveInputs:
    feature: AlignmentRowLike
    observation: LiveBackfillPeakFactObservation
    stage: BackfillPeakFactStageProjection
    extraction: BackfillExtractionResult
    evaluation: object


class _CanonicalOwnerBoundSource:
    def __init__(self, acquisition: object, *, empty: bool) -> None:
        self.acquisition_source = acquisition
        self.empty = empty
        self.last_trace = XICTrace.empty()

    def extract_xic(
        self,
        _mz: float,
        rt_min: float,
        rt_max: float,
        _ppm_tol: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        if self.empty:
            self.last_trace = XICTrace.empty()
        else:
            rt = np.linspace(rt_min, rt_max, 61)
            center = (rt_min + rt_max) / 2.0
            raw = 10.0 + 1000.0 * np.exp(-(((rt - center) / 0.15) ** 2))
            self.last_trace = XICTrace.from_arrays(rt, raw)
        return self.last_trace.rt, self.last_trace.intensity


def _live_available_inputs(
    tmp_path: Path,
    *,
    empty: bool = False,
    feature_id: str = "FAM000001",
) -> _LiveInputs:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_path = tmp_path / "sample-b.raw"
    source_path.write_bytes(f"no-raw-unit-fixture:{feature_id}".encode("utf-8"))
    acquisition = build_acquisition_source_identity(
        source_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    source = _CanonicalOwnerBoundSource(acquisition, empty=empty)
    feature = _feature(feature_family_id=feature_id, mz=500.0, rt=8.5)
    result = build_backfill_result(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        expected_acquisition_sources={"sample-b": acquisition},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=replace(_peak_config(), resolver_min_scans=3),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    assert len(result.peak_fact_observations) == 1
    observation = result.peak_fact_observations[0]
    evaluation = observation.physical_evaluation
    assert evaluation is not None
    assert evaluation.state == ("unavailable" if empty else "available")
    stage = backfill_peak_fact_stage_projection_from_observation(observation)
    extraction = BackfillExtractionResult(
        request_identity=observation.request_identity,
        trace=source.last_trace,
    )
    return _LiveInputs(
        feature=cast(AlignmentRowLike, feature),
        observation=observation,
        stage=stage,
        extraction=extraction,
        evaluation=evaluation,
    )


def _write_three_kind_provenance(tmp_path: Path) -> tuple[Path, _LiveInputs]:
    live = _live_available_inputs(tmp_path)
    evaluation = live.evaluation
    facts = evaluation.facts
    assert facts is not None
    cell = live.observation.cell
    authority = backfill_authority_cell_projection(
        stage=live.stage,
        cell=cell,
        profile_id=DEFAULT_SIGNAL_PROCESSING_PROFILE.profile_id,
        selected_fact_content_id=facts.fact_content_id,
        physical_answer_id=facts.physical_answer_id,
        physical_segment_id=facts.physical_segment_id,
    )
    decision = production_cell_decision_projection(
        ProductionCellDecision(
            feature_family_id=cell.cluster_id,
            sample_stem=cell.sample_stem,
            raw_status=cell.status,
            production_status="accepted_backfill",
            backfill_tier="accepted_backfill",
            write_matrix_value=True,
            matrix_value=cell.matrix_area,
            blank_reason="",
        ),
        cell=cell,
        config=AlignmentConfig(),
        owner_backfill_cell_content_id=authority.owner_backfill_cell_content_id,
    )
    batch = build_backfill_peak_fact_provenance_batch(
        (BackfillPeakFactObservation(live.stage, evaluation),),
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        cell_projections=(
            backfill_cell_projection(
                authority_cell=authority,
                production_decision=decision,
            ),
        ),
    )
    return (
        write_backfill_peak_fact_provenance_tsv(
            tmp_path / "three-kind.tsv",
            batch,
        ),
        live,
    )


def _live_final_owner_inputs(
    live: _LiveInputs,
) -> tuple[AlignmentMatrix, AlignmentConfig, ProductionDecisionSet]:
    evaluation = live.evaluation
    matrix = AlignmentMatrix(
        clusters=(_production_feature(live.stage),),
        cells=(
            live.observation.cell,
            _detected_cell("detected-1", evaluation.integration, stage=live.stage),
            _detected_cell("detected-2", evaluation.integration, stage=live.stage),
        ),
        sample_order=(live.stage.sample_stem, "detected-1", "detected-2"),
    )
    config = AlignmentConfig()
    decisions = build_production_decisions(matrix, config)
    assert (
        decisions.cell(
            live.stage.public_family_id, live.stage.sample_stem
        ).production_status
        == "accepted_backfill"
    )
    return matrix, config, decisions


def _detected_cell(
    sample_stem: str,
    integration: object,
    *,
    stage: BackfillPeakFactStageProjection,
) -> AlignedCell:
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=stage.public_family_id,
        status="detected",
        area=integration.area_ms1_morphology,
        apex_rt=integration.rt_apex_min,
        height=integration.height_smoothed,
        peak_start_rt=integration.rt_left_min,
        peak_end_rt=integration.rt_right_min,
        rt_delta_sec=0.0,
        trace_quality="clean",
        scan_support_score=0.9,
        source_candidate_id=f"{sample_stem}-candidate",
        reason="detected",
        selected_integration=integration,
        group_hypothesis_id=stage.group_hypothesis_id,
        public_family_id=stage.public_family_id,
    )


def _production_feature(
    stage: BackfillPeakFactStageProjection,
) -> AlignmentRowLike:
    return cast(
        AlignmentRowLike,
        SimpleNamespace(
            feature_family_id=stage.public_family_id,
            neutral_loss_tag="DNA_dR",
            family_center_mz=500.0,
            family_center_rt=stage.query_rt_min,
            family_product_mz=384.0,
            family_observed_neutral_loss_da=116.0,
            has_anchor=True,
            event_cluster_ids=("owner-1", "owner-2"),
            event_member_count=2,
            evidence="owner_complete_link;owner_count=2",
            review_only=False,
        ),
    )


def _reseal_projection_content_id(values: list[str]) -> None:
    digest = hashlib.sha256(
        json.dumps(
            values[:-1],
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    values[-1] = f"owner-backfill-provenance-row:{digest}"


def _column(name: str) -> int:
    return BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS.index(name)


def _mutate_owner_bearing_projection(
    rows: list[list[str]],
    *,
    owner_class: str,
) -> None:
    physical = rows[1]
    cell = rows[2]
    if owner_class == "trace":
        physical[_column("trace_content_id")] = _cross_typed_id(
            physical[_column("trace_content_id")]
        )
    elif owner_class == "prepared_projection":
        physical[_column("prepared_signal_id")] = _cross_typed_id(
            physical[_column("prepared_signal_id")]
        )
    elif owner_class == "universe":
        physical[_column("segment_universe_id")] = _cross_typed_id(
            physical[_column("segment_universe_id")]
        )
    elif owner_class == "question":
        physical[_column("physical_question_id")] = _cross_typed_id(
            physical[_column("physical_question_id")]
        )
    elif owner_class == "answer_facts":
        physical[_column("physical_answer_id")] = _cross_typed_id(
            physical[_column("physical_answer_id")]
        )
        area_column = _column("area_counts_seconds")
        physical[area_column] = (float.fromhex(physical[area_column]) + 1.0).hex()
    elif owner_class == "alignment_stage_binding":
        physical[_column("request_identity_id")] = _cross_typed_id(
            physical[_column("request_identity_id")]
        )
    elif owner_class == "selected_cell_content_authority":
        cell[_column("owner_backfill_cell_content_id")] = _cross_typed_id(
            cell[_column("owner_backfill_cell_content_id")]
        )
        _recompute_cell_projection_id(cell)
        _reseal_projection_content_id(cell)
        return
    elif owner_class == "production_decision":
        cell[_column("production_cell_decision_id")] = _cross_typed_id(
            cell[_column("production_cell_decision_id")]
        )
        _recompute_cell_projection_id(cell)
        _reseal_projection_content_id(cell)
        return
    elif owner_class == "writer_final_cell_projection":
        value_column = _column("matrix_value_counts_seconds")
        cell[value_column] = (float.fromhex(cell[value_column]) + 1.0).hex()
        _recompute_cell_projection_id(cell)
        _reseal_projection_content_id(cell)
        return
    else:  # pragma: no cover - closed by the pinned census
        raise AssertionError(owner_class)
    _reseal_projection_content_id(physical)


def _cross_typed_id(value: str) -> str:
    prefix, digest = value.rsplit(":", maxsplit=1)
    assert len(digest) == 64
    crossed_first = "1" if digest[0] == "0" else "0"
    return f"{prefix}:{crossed_first}{digest[1:]}"


def _recompute_cell_projection_id(values: list[str]) -> None:
    projection = BackfillCellProjection(
        group_hypothesis_id=values[_column("group_hypothesis_id")],
        public_family_id=values[_column("public_family_id")],
        sample_stem=values[_column("sample_stem")],
        profile_id=values[_column("profile_id")],
        selected_work_unit_key=values[_column("selected_work_unit_key")],
        selected_fact_content_id=values[_column("selected_fact_content_id")],
        owner_backfill_cell_content_id=values[
            _column("owner_backfill_cell_content_id")
        ],
        production_cell_decision_id=values[_column("production_cell_decision_id")],
        cell_status=values[_column("cell_status")],
        consolidation_state=values[_column("consolidation_state")],
        consolidation_winner_group_hypothesis_id=values[
            _column("consolidation_winner_group_hypothesis_id")
        ],
        consolidation_source_group_hypothesis_id=values[
            _column("consolidation_source_group_hypothesis_id")
        ],
        matrix_value_counts_seconds=float.fromhex(
            values[_column("matrix_value_counts_seconds")]
        ),
        matrix_value_source=values[_column("matrix_value_source")],
        writer_schema_version=values[_column("writer_schema_version")],
    )
    values[_column("cell_projection_id")] = projection.cell_projection_id


def _write_projection_rows(
    path: Path,
    header: list[str],
    rows: list[list[str]],
) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    path.write_bytes(buffer.getvalue().encode("utf-8"))


def _read_projection_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(
        io.StringIO(path.read_text(encoding="utf-8"), newline=""),
        delimiter="\t",
    )
    header = next(reader)
    return header, list(reader)


def _crossed_extraction(
    extraction: BackfillExtractionResult,
) -> BackfillExtractionResult:
    identity = extraction.request_identity
    request = identity.request
    crossed_identity = mint_backfill_request_identity(
        sample_stem=identity.sample_stem,
        acquisition_source=identity.acquisition_source,
        request=XICRequest(
            request.mz,
            request.rt_min,
            request.rt_max + 0.1,
            request.ppm_tol,
        ),
        backend=identity.backend,
        window_strategy=identity.window_strategy,
        superwindow_span_factor=identity.superwindow_span_factor,
        validation_mode=identity.validation_mode,
        result_semantics=identity.result_semantics,
    )
    return BackfillExtractionResult(
        request_identity=crossed_identity,
        trace=extraction.trace,
    )


def _changed_value(value: object) -> object:
    if value is None:
        return "changed"
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return f"{value}-changed"
    if type(value) is int:
        return value + 1
    if isinstance(value, float):
        return value + 1.0
    if isinstance(value, tuple):
        if value and type(value[0]) is int:
            return (*value, 1)
        return (*value, "changed")
    raise AssertionError(type(value))


def test_provenance_writer_streams_bytes_identical_to_single_shot_render(
    tmp_path: Path,
) -> None:
    """The streaming writer must produce exactly render_...()'s bytes.

    One provenance row per Backfill request means an 85-sample full-audit batch
    renders to several GB, so the writer streams instead of holding the whole
    artifact.  This pins the streamed bytes to the single-shot rendering across
    several chunk boundaries.
    """

    live = _live_available_inputs(tmp_path)
    base = build_backfill_peak_fact_provenance_batch(
        (BackfillPeakFactObservation(live.stage, live.evaluation),),
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    profile_row, physical_row = base.rows[0], base.rows[1]
    row_count = _PROVENANCE_ROWS_PER_CHUNK * 2 + 7
    batch = BackfillPeakFactProvenanceBatch(
        rows=(
            profile_row,
            *(
                replace(
                    physical_row,
                    work_unit_key=f"{physical_row.work_unit_key}:{index}",
                )
                for index in range(row_count)
            ),
        )
    )

    expected = render_backfill_peak_fact_provenance_tsv(batch)
    chunks = list(_iter_backfill_peak_fact_provenance_chunks(batch))

    assert len(chunks) > 2, "batch must cross several chunk boundaries"
    assert b"".join(chunks) == expected
    assert max(len(chunk) for chunk in chunks) < len(expected)

    path = write_backfill_peak_fact_provenance_tsv(tmp_path / "big.tsv", batch)

    assert path.read_bytes() == expected
    # header + the run_profile row + every physical_question row
    assert path.read_bytes().count(b"\n") == row_count + 2
    assert not (tmp_path / ".big.tsv.tmp").exists()


def test_provenance_writer_detects_a_temp_file_that_does_not_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read-back leg must actually compare, not just re-read.

    Nothing else pins the comparison itself: the temp-cleanup test injects an
    OSError, so it stays green even if the verification branch is removed.
    """

    live = _live_available_inputs(tmp_path)
    batch = build_backfill_peak_fact_provenance_batch(
        (BackfillPeakFactObservation(live.stage, live.evaluation),),
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    path = tmp_path / "corrupt.tsv"
    temporary = tmp_path / ".corrupt.tsv.tmp"
    original_open = Path.open

    def corrupting_open(candidate: Path, *args: object, **kwargs: object):
        mode = args[0] if args else kwargs.get("mode", "r")
        handle = original_open(candidate, *args, **kwargs)  # type: ignore[arg-type]
        if candidate == temporary and "r" in str(mode):
            with handle:
                landed = handle.read()
            return io.BytesIO(landed[:-1])
        return handle

    monkeypatch.setattr(Path, "open", corrupting_open)

    with pytest.raises(OSError, match="verification failed"):
        write_backfill_peak_fact_provenance_tsv(path, batch)

    assert not path.exists()
    assert not temporary.exists()


def test_provenance_writer_rejects_a_batch_of_the_wrong_type(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="typed provenance batch"):
        write_backfill_peak_fact_provenance_tsv(
            tmp_path / "bad.tsv",
            cast(BackfillPeakFactProvenanceBatch, object()),
        )
