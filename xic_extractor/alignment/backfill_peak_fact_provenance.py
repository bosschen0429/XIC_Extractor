"""Strict durable provenance for canonical Backfill physical peak facts.

Persisted rows prove projection integrity only.  Alignment, physical, and final
cell claims are lifted only by replay through their original live owners.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xic_extractor.alignment.backfill_peak_fact_codec import (
    _PROVENANCE_ROWS_PER_CHUNK,
    BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS,
    BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION,
    BACKFILL_PROVENANCE_ROW_ID_PREFIX,
    BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION,
    EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH,
    BackfillPeakFactProvenanceBatch,
    BackfillPeakFactProvenanceReadResult,
    BackfillPeakFactProvenanceRow,
    ClaimState,
    RowKind,
    _cell_projection_row,
    _iter_backfill_peak_fact_provenance_chunks,
    _run_profile_row,
    build_backfill_peak_fact_provenance_batch_from_rows,
    read_backfill_peak_fact_provenance_tsv,
    render_backfill_peak_fact_provenance_tsv,
    write_backfill_peak_fact_provenance_tsv,
)
from xic_extractor.alignment.backfill_peak_fact_projection import (
    _STAGE_PROJECTION_MINT_TOKEN,
    BACKFILL_AUTHORITY_CELL_FIELDS,
    BACKFILL_AUTHORITY_CELL_SCHEMA_VERSION,
    BACKFILL_CELL_PROJECTION_ID_PREFIX,
    BACKFILL_CELL_PROJECTION_SCHEMA_VERSION,
    SELECTED_INTEGRATION_AUTHORITY_FIELDS,
    SELECTED_INTEGRATION_AUTHORITY_ID_PREFIX,
    SELECTED_INTEGRATION_AUTHORITY_SCHEMA_VERSION,
    BackfillAuthorityCellProjection,
    BackfillCellProjection,
    BackfillPeakFactObservation,
    BackfillPeakFactStageProjection,
    SelectedIntegrationAuthorityProjection,
    backfill_authority_cell_projection,
    backfill_cell_lineage_matches,
    backfill_cell_projection,
    backfill_peak_fact_stage_projection_from_observation,
    selected_integration_authority_projection,
)
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.production_decisions import (
    ProductionDecisionSet,
    build_production_decisions,
    production_cell_decision_projection,
)
from xic_extractor.peak_detection.physical_peak_codec import (
    rehydrate_physical_peak_evaluation,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
    validate_bounded_physical_peak_evaluation,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.backfill_peak_fact import (
        BackfillExtractionResult,
    )
    from xic_extractor.alignment.backfill_peak_fact import (
        BackfillPeakFactObservation as LiveBackfillPeakFactObservation,
    )
    from xic_extractor.alignment.config import AlignmentConfig


@dataclass(frozen=True, slots=True)
class PreparedBackfillPeakFact:
    feature_family_id: str
    stage: BackfillPeakFactStageProjection
    physical_row: BackfillPeakFactProvenanceRow
    selected_integration_authority: SelectedIntegrationAuthorityProjection | None
    selected_integration_authority_content_id: str | None

    def __post_init__(self) -> None:
        self.validate_binding()

    def validate_binding(self) -> None:
        if not self.feature_family_id:
            raise ValueError("prepared Backfill peak fact requires feature identity")
        if self.feature_family_id != self.stage.feature_family_id:
            raise ValueError("prepared Backfill peak fact crosses feature identity")
        if self.physical_row.row_kind != "physical_question":
            raise ValueError("prepared Backfill peak fact requires a physical row")
        try:
            _compare_stage_to_row(self.stage, self.physical_row)
        except ValueError as exc:
            raise ValueError(
                "prepared Backfill peak fact crosses stage identity"
            ) from exc
        integration = self.selected_integration_authority
        if integration is None:
            if (
                self.selected_integration_authority_content_id is not None
                or self.stage.selected_integration_authority_content_id is not None
            ):
                raise ValueError(
                    "prepared Backfill peak fact crosses selected integration"
                )
            return
        if (
            self.selected_integration_authority_content_id
            != integration.selected_integration_authority_content_id
        ):
            raise ValueError("prepared Backfill peak fact crosses selected integration")
        if (
            self.stage.selected_integration_authority_content_id
            != integration.selected_integration_authority_content_id
        ):
            raise ValueError("prepared Backfill peak fact crosses selected integration")
        if (
            integration.rt_left_min,
            integration.rt_apex_min,
            integration.rt_right_min,
            integration.area_ms1_morphology,
        ) != (
            self.physical_row.left_rt_min,
            self.physical_row.apex_rt_min,
            self.physical_row.right_rt_min,
            self.physical_row.area_counts_seconds,
        ):
            raise ValueError(
                "prepared Backfill peak fact crosses selected integration facts"
            )


@dataclass(frozen=True, slots=True)
class BackfillPhysicalClaim:
    work_unit_key: str
    stage_claim: ClaimState
    physical_claim: ClaimState


@dataclass(frozen=True, slots=True)
class BackfillCellClaim:
    group_hypothesis_id: str
    sample_stem: str
    cell_projection_claim: ClaimState


@dataclass(frozen=True, slots=True)
class BackfillPeakFactCompositionResult:
    physical_claims: tuple[BackfillPhysicalClaim, ...]
    cell_claims: tuple[BackfillCellClaim, ...]


def row_from_terminal_observation(
    stage: BackfillPeakFactStageProjection,
    physical_evaluation: PhysicalPeakEvaluation,
    *,
    profile: SignalProcessingProfile,
) -> BackfillPeakFactProvenanceRow:
    validate_bounded_physical_peak_evaluation(physical_evaluation)
    prepared = physical_evaluation.prepared
    universe = physical_evaluation.segment_universe
    question = physical_evaluation.question
    answer = physical_evaluation.answer
    facts_value = physical_evaluation.facts
    if prepared.profile_id != profile.profile_id:
        raise ValueError("physical evaluation crosses signal-processing profile")
    if (stage.selection_subject_id, stage.query_rt_min) != (
        question.selection_subject_id,
        question.query_rt_min,
    ):
        raise ValueError("stage crosses the owner physical question")
    if prepared.trace_point_count:
        expected_range = (question.context_rt_start_min, question.context_rt_end_min)
        if (stage.trace_rt_start_min, stage.trace_rt_end_min) != expected_range:
            raise ValueError("stage trace range differs from owner-derived context")
    elif stage.trace_rt_start_min is not None or stage.trace_rt_end_min is not None:
        raise ValueError("zero-point trace must not assert observed RT endpoints")
    return BackfillPeakFactProvenanceRow(
        row_kind="physical_question",
        work_unit_key=stage.work_unit_key,
        group_hypothesis_id=stage.group_hypothesis_id,
        public_family_id=stage.public_family_id,
        sample_stem=stage.sample_stem,
        candidate_index=stage.candidate_index,
        candidate_phase=stage.candidate_phase,
        request_identity_id=stage.request_identity_id,
        acquisition_source_id=stage.acquisition_source_id,
        trace_content_id=prepared.trace_content_id,
        trace_point_count=prepared.trace_point_count,
        trace_rt_start_min=stage.trace_rt_start_min,
        trace_rt_end_min=stage.trace_rt_end_min,
        profile_schema_version=profile.schema_version,
        profile_id=profile.profile_id,
        profile_hash=profile.profile_hash,
        signal_processing_method=profile.method,
        signal_processing_window_points=profile.window_points,
        physical_signal_formula=profile.physical_signal_formula,
        prepared_signal_id=prepared.prepared_signal_id,
        segment_universe_id=universe.segment_universe_id,
        segment_candidate_ids=universe.segment_ids,
        physical_question_schema_version=question.schema_version,
        physical_question_id=question.physical_question_id,
        selection_subject_id=question.selection_subject_id,
        query_rt_min=question.query_rt_min,
        context_rt_start_min=question.context_rt_start_min,
        context_rt_end_min=question.context_rt_end_min,
        admissible_segment_ids=question.admissible_segment_ids,
        selection_policy_version=question.selection_policy_version,
        state=physical_evaluation.state,
        reason=physical_evaluation.reason,
        physical_answer_id=answer.physical_answer_id,
        physical_segment_id=facts_value.physical_segment_id if facts_value else "",
        apex_rt_min=facts_value.apex_rt_min if facts_value else None,
        left_rt_min=facts_value.left_rt_min if facts_value else None,
        right_rt_min=facts_value.right_rt_min if facts_value else None,
        height=facts_value.height if facts_value else None,
        scan_support_count=facts_value.scan_support_count if facts_value else None,
        area_counts_seconds=facts_value.area_counts_seconds if facts_value else None,
        area_source=facts_value.area_source if facts_value else "",
        relative_shape_schema_version=(
            facts_value.relative_shape_schema_version if facts_value else ""
        ),
        relative_shape_point_count=(
            facts_value.relative_shape_point_count if facts_value else None
        ),
        relative_shape_content_hash=(
            facts_value.relative_shape_content_hash if facts_value else ""
        ),
        fact_content_id=facts_value.fact_content_id if facts_value else "",
    )


def prepare_backfill_peak_fact(
    observation: LiveBackfillPeakFactObservation,
    *,
    feature_order_index: int,
    sample_order_index: int,
    profile: SignalProcessingProfile,
) -> PreparedBackfillPeakFact:
    stage = backfill_peak_fact_stage_projection_from_observation(
        observation,
        feature_order_index=feature_order_index,
        sample_order_index=sample_order_index,
    )
    evaluation = observation.physical_evaluation
    if evaluation is None:
        raise ValueError(
            "covered Untargeted provenance requires canonical physical evaluation"
        )
    selected_integration = None
    if observation.selected_for_output and evaluation.facts is not None:
        if observation.facts is None:
            raise ValueError("selected physical fact lacks bounded owner receipt")
        selected_integration = selected_integration_authority_projection(
            observation.facts.integration
        )
    return PreparedBackfillPeakFact(
        feature_family_id=observation.cell.cluster_id,
        stage=stage,
        physical_row=row_from_terminal_observation(
            stage,
            evaluation,
            profile=profile,
        ),
        selected_integration_authority=selected_integration,
        selected_integration_authority_content_id=(
            selected_integration.selected_integration_authority_content_id
            if selected_integration is not None
            else None
        ),
    )


def build_backfill_peak_fact_provenance_batch(
    observations: Sequence[BackfillPeakFactObservation],
    *,
    profile: SignalProcessingProfile,
    cell_projections: Sequence[BackfillCellProjection] = (),
) -> BackfillPeakFactProvenanceBatch:
    ordered_physical = sorted(
        observations,
        key=lambda item: (
            item.stage.feature_order_index,
            item.stage.sample_order_index,
            item.stage.candidate_index,
            item.stage.candidate_phase,
        ),
    )
    return BackfillPeakFactProvenanceBatch(
        rows=(
            _run_profile_row(profile),
            *(
                row_from_terminal_observation(
                    item.stage,
                    item.physical_evaluation,
                    profile=profile,
                )
                for item in ordered_physical
            ),
            *(_cell_projection_row(item, profile) for item in cell_projections),
        )
    )


def rehydrate_backfill_peak_fact_provenance_row(
    row: BackfillPeakFactProvenanceRow,
    extraction: BackfillExtractionResult,
    *,
    stage: BackfillPeakFactStageProjection,
    profile: SignalProcessingProfile,
) -> PhysicalPeakEvaluation:
    """Replay one physical row from live Alignment and acquisition-owner inputs."""

    if row.row_kind != "physical_question":
        raise ValueError("only physical_question rows can be rehydrated")
    _compare_stage_to_row(stage, row)
    _validate_live_extraction(stage, extraction)
    trace = extraction.trace
    prepared = prepare_physical_signal(
        trace.rt,
        trace.intensity,
        trace_content_id=row.trace_content_id,
        profile=profile,
    )
    # The physical owner deliberately receives live stage/query and live request
    # bounds.  Persisted context/admissible IDs never become replay inputs.
    request = extraction.request_identity.request
    actual = evaluate_physical_peak(
        prepared,
        selection_subject_id=stage.selection_subject_id,
        query_rt_min=stage.query_rt_min,
        request_context_rt_start_min=request.rt_min,
        request_context_rt_end_min=request.rt_max,
    )
    actual = rehydrate_physical_peak_evaluation(
        trace.rt,
        trace.intensity,
        profile=profile,
        expected=actual,
        request_context_rt_start_min=request.rt_min,
        request_context_rt_end_min=request.rt_max,
    )
    live_row = row_from_terminal_observation(stage, actual, profile=profile)
    if live_row != row:
        raise ValueError("persisted physical row differs from live owner replay")
    return actual


def compose_backfill_peak_fact_provenance(
    read_result: BackfillPeakFactProvenanceReadResult,
    *,
    live_observation_by_work: Mapping[str, LiveBackfillPeakFactObservation]
    | None = None,
    live_extraction_by_work: Mapping[str, BackfillExtractionResult] | None = None,
    profile: SignalProcessingProfile | None = None,
    live_matrix: AlignmentMatrix | None = None,
    alignment_config: AlignmentConfig | None = None,
    production_decisions: ProductionDecisionSet | None = None,
) -> BackfillPeakFactCompositionResult:
    if not isinstance(read_result, BackfillPeakFactProvenanceReadResult):
        raise TypeError("read_result must come from the strict provenance reader")
    observations = live_observation_by_work or {}
    extractions = live_extraction_by_work or {}
    run_profile = read_result.rows[0]
    if profile is not None:
        live_profile_row = _run_profile_row(profile)
        if live_profile_row != run_profile:
            raise ValueError("live profile crosses persisted run profile")
    physical_rows = tuple(
        row for row in read_result.rows if row.row_kind == "physical_question"
    )
    verified_physical: dict[str, BackfillPeakFactProvenanceRow] = {}
    verified_stages: dict[str, BackfillPeakFactStageProjection] = {}
    physical_claims: list[BackfillPhysicalClaim] = []
    for row in physical_rows:
        observation = observations.get(row.work_unit_key)
        stage = (
            backfill_peak_fact_stage_projection_from_observation(observation)
            if observation is not None
            else None
        )
        stage_claim: ClaimState = "unavailable_without_live_owner"
        physical_claim: ClaimState = "unavailable_without_live_owner"
        if stage is not None:
            _compare_stage_to_row(stage, row)
            stage_claim = "verified"
            verified_stages[row.work_unit_key] = stage
        extraction = extractions.get(row.work_unit_key)
        if stage is not None and extraction is not None and profile is not None:
            rehydrate_backfill_peak_fact_provenance_row(
                row,
                extraction,
                stage=stage,
                profile=profile,
            )
            physical_claim = "verified"
            verified_physical[row.work_unit_key] = row
        physical_claims.append(
            BackfillPhysicalClaim(
                work_unit_key=row.work_unit_key,
                stage_claim=stage_claim,
                physical_claim=physical_claim,
            )
        )

    final_inputs_available = all(
        value is not None
        for value in (profile, live_matrix, alignment_config, production_decisions)
    )
    recomputed_decisions: ProductionDecisionSet | None = None
    if final_inputs_available:
        assert live_matrix is not None
        assert alignment_config is not None
        assert production_decisions is not None
        recomputed_decisions = build_production_decisions(live_matrix, alignment_config)
        if recomputed_decisions != production_decisions:
            raise ValueError(
                "supplied production decisions differ from live owner replay"
            )

    cell_claims: list[BackfillCellClaim] = []
    for row in (
        item for item in read_result.rows if item.row_kind == "cell_projection"
    ):
        claim: ClaimState = "unavailable_without_live_owner"
        selected = verified_physical.get(row.selected_work_unit_key)
        stage = verified_stages.get(row.selected_work_unit_key)
        if (
            selected is not None
            and stage is not None
            and recomputed_decisions is not None
        ):
            assert profile is not None
            assert live_matrix is not None
            assert alignment_config is not None
            cell = _live_cell_for_projection(live_matrix, row)
            authority = backfill_authority_cell_projection(
                stage=stage,
                cell=cell,
                profile_id=profile.profile_id,
                selected_fact_content_id=row.selected_fact_content_id,
                physical_answer_id=selected.physical_answer_id,
                physical_segment_id=selected.physical_segment_id,
            )
            decision = recomputed_decisions.cell(cell.cluster_id, cell.sample_stem)
            production_projection = production_cell_decision_projection(
                decision,
                cell=cell,
                config=alignment_config,
                owner_backfill_cell_content_id=authority.owner_backfill_cell_content_id,
            )
            expected = _cell_projection_row(
                backfill_cell_projection(
                    authority_cell=authority,
                    production_decision=production_projection,
                ),
                profile,
            )
            if expected != row:
                raise ValueError(
                    "persisted cell projection differs from live owner replay"
                )
            claim = "verified"
        cell_claims.append(
            BackfillCellClaim(
                group_hypothesis_id=row.group_hypothesis_id,
                sample_stem=row.sample_stem,
                cell_projection_claim=claim,
            )
        )
    return BackfillPeakFactCompositionResult(
        physical_claims=tuple(physical_claims),
        cell_claims=tuple(cell_claims),
    )


def _compare_stage_to_row(
    stage: BackfillPeakFactStageProjection,
    row: BackfillPeakFactProvenanceRow,
) -> None:
    observed = (
        stage.work_unit_key,
        stage.group_hypothesis_id,
        stage.public_family_id,
        stage.sample_stem,
        stage.candidate_index,
        stage.candidate_phase,
        stage.request_identity_id,
        stage.acquisition_source_id,
        stage.selection_subject_id,
        stage.query_rt_min,
        stage.trace_rt_start_min,
        stage.trace_rt_end_min,
    )
    expected = (
        row.work_unit_key,
        row.group_hypothesis_id,
        row.public_family_id,
        row.sample_stem,
        row.candidate_index,
        row.candidate_phase,
        row.request_identity_id,
        row.acquisition_source_id,
        row.selection_subject_id,
        row.query_rt_min,
        row.trace_rt_start_min,
        row.trace_rt_end_min,
    )
    if observed != expected:
        raise ValueError("live Alignment stage projection crosses persisted work")


def _validate_live_extraction(
    stage: BackfillPeakFactStageProjection,
    extraction: BackfillExtractionResult,
) -> None:
    from xic_extractor.alignment.backfill_peak_fact import BackfillExtractionResult

    if not isinstance(extraction, BackfillExtractionResult):
        raise TypeError("extraction must be owner-minted")
    identity = extraction.request_identity
    if (
        identity.request_identity_id,
        identity.acquisition_source.acquisition_source_id,
        identity.sample_stem,
    ) != (
        stage.request_identity_id,
        stage.acquisition_source_id,
        stage.sample_stem,
    ):
        raise ValueError("live extraction crosses Alignment stage identity")
    trace = extraction.trace
    if len(trace.rt) != len(trace.intensity):
        raise ValueError("live extraction trace cardinality mismatch")
    actual_range = (
        (float(trace.rt[0]), float(trace.rt[-1])) if len(trace.rt) else (None, None)
    )
    if actual_range != (stage.trace_rt_start_min, stage.trace_rt_end_min):
        raise ValueError("live extraction trace range crosses Alignment stage")


def _live_cell_for_projection(
    matrix: AlignmentMatrix,
    row: BackfillPeakFactProvenanceRow,
) -> AlignedCell:
    matches = tuple(
        cell
        for cell in matrix.cells
        if cell.sample_stem == row.sample_stem
        and cell.group_hypothesis_id == row.group_hypothesis_id
        and cell.public_family_id == row.public_family_id
    )
    if len(matches) != 1:
        raise ValueError("final cell replay requires one exact live Matrix cell")
    return matches[0]


__all__ = [
    "EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH",
    "BACKFILL_AUTHORITY_CELL_FIELDS",
    "BACKFILL_AUTHORITY_CELL_SCHEMA_VERSION",
    "BACKFILL_CELL_PROJECTION_ID_PREFIX",
    "BACKFILL_CELL_PROJECTION_SCHEMA_VERSION",
    "BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS",
    "BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION",
    "BACKFILL_PROVENANCE_ROW_ID_PREFIX",
    "BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION",
    "SELECTED_INTEGRATION_AUTHORITY_FIELDS",
    "SELECTED_INTEGRATION_AUTHORITY_ID_PREFIX",
    "SELECTED_INTEGRATION_AUTHORITY_SCHEMA_VERSION",
    "ClaimState",
    "BackfillAuthorityCellProjection",
    "BackfillCellClaim",
    "BackfillCellProjection",
    "BackfillPeakFactCompositionResult",
    "BackfillPeakFactObservation",
    "BackfillPeakFactProvenanceBatch",
    "BackfillPeakFactProvenanceReadResult",
    "BackfillPeakFactProvenanceRow",
    "BackfillPeakFactStageProjection",
    "BackfillPhysicalClaim",
    "PreparedBackfillPeakFact",
    "RowKind",
    "SelectedIntegrationAuthorityProjection",
    "_PROVENANCE_ROWS_PER_CHUNK",
    "_STAGE_PROJECTION_MINT_TOKEN",
    "_cell_projection_row",
    "_iter_backfill_peak_fact_provenance_chunks",
    "_run_profile_row",
    "build_backfill_peak_fact_provenance_batch",
    "build_backfill_peak_fact_provenance_batch_from_rows",
    "compose_backfill_peak_fact_provenance",
    "backfill_authority_cell_projection",
    "backfill_cell_lineage_matches",
    "backfill_cell_projection",
    "backfill_peak_fact_stage_projection_from_observation",
    "prepare_backfill_peak_fact",
    "read_backfill_peak_fact_provenance_tsv",
    "render_backfill_peak_fact_provenance_tsv",
    "rehydrate_backfill_peak_fact_provenance_row",
    "row_from_terminal_observation",
    "selected_integration_authority_projection",
    "write_backfill_peak_fact_provenance_tsv",
]
