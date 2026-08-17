from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillCandidateAuditRow,
    OwnerBackfillRequestReuseObservation,
)
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    OwnerBackfillPeakFactProvenanceRow,
    OwnerBackfillPeakFactStageProjection,
    owner_backfill_peak_fact_stage_projection_from_observation,
    row_from_terminal_observation,
    selected_integration_authority_projection,
)
from xic_extractor.alignment.owner_group_delivery import OwnerGroupDeliveryFeatures
from xic_extractor.alignment.owner_matrix import OwnerAlignmentMatrixBuilder
from xic_extractor.alignment.process_backend import (
    OwnerBackfillSampleResult,
    OwnerBackfillTimingStats,
)
from xic_extractor.execution_runtime_owner import RuntimePeakFactCostMetrics
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)


@dataclass(frozen=True, slots=True)
class PreparedOwnerBackfillPeakFact:
    stage: OwnerBackfillPeakFactStageProjection
    physical_row: OwnerBackfillPeakFactProvenanceRow
    selected_integration_authority_content_id: str | None


@dataclass(frozen=True, slots=True)
class OwnerBackfillSampleDeliveryReceipt:
    sample_index: int
    sample_stem: str
    cell_count: int
    candidate_audit_row_count: int
    peak_fact_count: int
    timing_stats: tuple[OwnerBackfillTimingStats, ...]
    request_reuse_observations: tuple[
        OwnerBackfillRequestReuseObservation, ...
    ]


@dataclass(frozen=True, slots=True)
class OwnerBackfillTransactionOutput:
    matrix: AlignmentMatrix
    candidate_audit_rows: tuple[OwnerBackfillCandidateAuditRow, ...]
    timing_stats: tuple[OwnerBackfillTimingStats, ...]
    request_reuse_observations: tuple[
        OwnerBackfillRequestReuseObservation, ...
    ]
    prepared_peak_facts: tuple[PreparedOwnerBackfillPeakFact, ...]
    peak_fact_cost_receipts: tuple[RuntimePeakFactCostMetrics, ...]


@dataclass(frozen=True, slots=True)
class _PreparedSample:
    candidate_audit_rows: tuple[OwnerBackfillCandidateAuditRow, ...]
    timing_stats: tuple[OwnerBackfillTimingStats, ...]
    request_reuse_observations: tuple[
        OwnerBackfillRequestReuseObservation, ...
    ]
    peak_facts: tuple[PreparedOwnerBackfillPeakFact, ...]
    peak_fact_cost: RuntimePeakFactCostMetrics | None


class OwnerBackfillSampleTransaction:
    """Consume complete sample results into one unpublished Alignment state."""

    def __init__(
        self,
        features: OwnerGroupDeliveryFeatures,
        *,
        matrix_sample_order: tuple[str, ...],
        expected_samples: tuple[tuple[int, str], ...],
        signal_processing_profile: SignalProcessingProfile,
    ) -> None:
        self._features = tuple(features)
        self._matrix_sample_order = matrix_sample_order
        self._expected_samples = expected_samples
        self._profile = signal_processing_profile
        self._feature_rank = {
            feature.feature_family_id: index
            for index, feature in enumerate(self._features)
        }
        self._sample_rank = {
            sample: index for index, sample in enumerate(matrix_sample_order)
        }
        self._audit_sample_rank = {
            sample_stem: sample_index
            for sample_index, sample_stem in expected_samples
        }
        self._builder = OwnerAlignmentMatrixBuilder(
            self._features,
            sample_order=matrix_sample_order,
            ambiguous_by_sample={},
        )
        self._prepared_by_ordinal: dict[int, _PreparedSample] = {}

    def deliver(
        self,
        ordinal: int,
        result: OwnerBackfillSampleResult,
    ) -> OwnerBackfillSampleDeliveryReceipt:
        if not isinstance(result, OwnerBackfillSampleResult):
            raise TypeError("Backfill transaction requires a successful sample result")
        if ordinal < 0 or ordinal >= len(self._expected_samples):
            raise ValueError("Backfill result ordinal falls outside expected work")
        if ordinal in self._prepared_by_ordinal:
            raise ValueError("Backfill sample result was delivered more than once")
        expected = self._expected_samples[ordinal]
        if (result.sample_index, result.sample_stem) != expected:
            raise ValueError("Backfill sample result crosses expected sample identity")
        self._validate_sample_attachments(result)
        prepared_peak_facts = tuple(
            self._prepare_peak_fact(observation)
            for observation in result.peak_fact_observations
        )
        self._builder.consume_rescued_cells(result.cells)
        self._prepared_by_ordinal[ordinal] = _PreparedSample(
            candidate_audit_rows=result.candidate_audit_rows,
            timing_stats=result.timing_stats,
            request_reuse_observations=result.request_reuse_observations,
            peak_facts=prepared_peak_facts,
            peak_fact_cost=result.peak_fact_cost,
        )
        return OwnerBackfillSampleDeliveryReceipt(
            sample_index=result.sample_index,
            sample_stem=result.sample_stem,
            cell_count=len(result.cells),
            candidate_audit_row_count=len(result.candidate_audit_rows),
            peak_fact_count=len(prepared_peak_facts),
            timing_stats=result.timing_stats,
            request_reuse_observations=result.request_reuse_observations,
        )

    def finish(self) -> OwnerBackfillTransactionOutput:
        if len(self._prepared_by_ordinal) != len(self._expected_samples):
            raise ValueError("Backfill transaction cannot publish incomplete samples")
        ordered = tuple(
            self._prepared_by_ordinal[ordinal]
            for ordinal in range(len(self._expected_samples))
        )
        candidate_rows = tuple(
            sorted(
                (
                    row
                    for prepared in ordered
                    for row in prepared.candidate_audit_rows
                ),
                key=lambda row: (
                    self._feature_rank.get(
                        row.feature_family_id, len(self._feature_rank)
                    ),
                    self._audit_sample_rank.get(
                        row.sample_stem, len(self._audit_sample_rank)
                    ),
                    row.candidate_index,
                ),
            )
        )
        return OwnerBackfillTransactionOutput(
            matrix=self._builder.finish(),
            candidate_audit_rows=candidate_rows,
            timing_stats=tuple(
                stat for prepared in ordered for stat in prepared.timing_stats
            ),
            request_reuse_observations=tuple(
                observation
                for prepared in ordered
                for observation in prepared.request_reuse_observations
            ),
            prepared_peak_facts=tuple(
                peak_fact for prepared in ordered for peak_fact in prepared.peak_facts
            ),
            peak_fact_cost_receipts=tuple(
                prepared.peak_fact_cost
                for prepared in ordered
                if prepared.peak_fact_cost is not None
            ),
        )

    def _prepare_peak_fact(self, observation) -> PreparedOwnerBackfillPeakFact:
        feature_index = self._feature_rank.get(
            observation.cell.cluster_id,
            len(self._feature_rank),
        )
        sample_index = self._sample_rank.get(
            observation.cell.sample_stem,
            len(self._sample_rank),
        )
        stage = owner_backfill_peak_fact_stage_projection_from_observation(
            observation,
            feature_order_index=feature_index,
            sample_order_index=sample_index,
        )
        evaluation = observation.physical_evaluation
        if evaluation is None:
            raise ValueError(
                "covered Untargeted provenance requires canonical physical evaluation"
            )
        physical_row = row_from_terminal_observation(
            stage,
            evaluation,
            profile=self._profile,
        )
        selected_integration_id = None
        if observation.selected_for_output and evaluation.facts is not None:
            if observation.facts is None:
                raise ValueError("selected physical fact lacks bounded owner receipt")
            selected_integration_id = selected_integration_authority_projection(
                observation.facts.integration
            ).selected_integration_authority_content_id
        return PreparedOwnerBackfillPeakFact(
            stage=stage,
            physical_row=physical_row,
            selected_integration_authority_content_id=selected_integration_id,
        )

    @staticmethod
    def _validate_sample_attachments(result: OwnerBackfillSampleResult) -> None:
        sample_stem = result.sample_stem
        attachment_samples = (
            *(cell.sample_stem for cell in result.cells),
            *(row.sample_stem for row in result.candidate_audit_rows),
            *(stat.sample_stem for stat in result.timing_stats),
            *(
                observation.sample_stem
                for observation in result.request_reuse_observations
            ),
            *(
                observation.cell.sample_stem
                for observation in result.peak_fact_observations
            ),
        )
        if any(item != sample_stem for item in attachment_samples):
            raise ValueError("Backfill result attachments cross sample identity")
