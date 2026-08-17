from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillCandidateAuditRow,
    OwnerBackfillRequestReuseObservation,
)
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    PreparedOwnerBackfillPeakFact,
    prepare_owner_backfill_peak_fact,
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
        self._feature_owner_identity = {
            feature.feature_family_id: (
                feature.group_hypothesis_id,
                feature.public_family_id,
            )
            for feature in self._features
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
        prepared_peak_facts = result.prepared_peak_facts or tuple(
            prepare_owner_backfill_peak_fact(
                observation,
                feature_order_index=self._feature_rank.get(
                    observation.cell.cluster_id,
                    len(self._feature_rank),
                ),
                sample_order_index=self._sample_rank.get(
                    observation.cell.sample_stem,
                    len(self._sample_rank),
                ),
                profile=self._profile,
            )
            for observation in result.peak_fact_observations
        )
        self._validate_prepared_peak_facts(result, prepared_peak_facts)
        self._builder.consume_sample_rescued_cells(result.sample_stem, result.cells)
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

    def _validate_prepared_peak_facts(
        self,
        result: OwnerBackfillSampleResult,
        prepared_peak_facts: tuple[PreparedOwnerBackfillPeakFact, ...],
    ) -> None:
        for prepared in prepared_peak_facts:
            prepared.validate_binding()
            expected_feature_index = self._feature_rank.get(
                prepared.feature_family_id
            )
            expected_sample_index = self._sample_rank.get(result.sample_stem)
            if expected_feature_index is None or (
                prepared.stage.feature_order_index != expected_feature_index
            ):
                raise ValueError("prepared Backfill peak fact crosses feature order")
            if self._feature_owner_identity.get(prepared.feature_family_id) != (
                prepared.stage.group_hypothesis_id,
                prepared.stage.public_family_id,
            ):
                raise ValueError("prepared Backfill peak fact crosses feature identity")
            if expected_sample_index is None or (
                prepared.stage.sample_order_index != expected_sample_index
            ):
                raise ValueError("prepared Backfill peak fact crosses sample order")
            if (
                prepared.stage.sample_stem,
                prepared.physical_row.sample_stem,
            ) != (result.sample_stem, result.sample_stem):
                raise ValueError("prepared Backfill peak fact crosses sample identity")

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
            *(fact.stage.sample_stem for fact in result.prepared_peak_facts),
            *(fact.physical_row.sample_stem for fact in result.prepared_peak_facts),
        )
        if any(item != sample_stem for item in attachment_samples):
            raise ValueError("Backfill result attachments cross sample identity")
