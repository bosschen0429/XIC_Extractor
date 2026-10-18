"""Stable cross-sample membership shared by Backfill and finalization."""

from __future__ import annotations

from dataclasses import dataclass, field

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSamplePartitionTransition,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    typed_identity_digest,
)


class CrossSampleGroupingContractError(ValueError):
    """Raised when a grouping result crosses its processing request."""


@dataclass(frozen=True, slots=True)
class CrossSampleGroupingResult:
    """One immutable membership result, including explicit recovery admissions.

    The partition owner has already closed membership and lineage. This owner
    binds that answer to the processing request once, without rebuilding its
    trajectory. Recovery may extend a missing sample with exact lineage while
    preserving every original group and member.
    """

    processing_owners: ProcessingOwnerReferences
    partition_transition: CrossSamplePartitionTransition
    rt_frame: SampleRtFrame = field(compare=False)
    receipt_id: str = field(init=False)
    basis_bytes: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        partition = self.partition_transition
        basis = canonical_identity_basis_bytes(
            {
                "schema_version": "cross_sample_grouping_result",
                "processing_request_id": self.processing_request_id,
                "initial_group_ids": [
                    group.group_hypothesis_id for group in partition.predecessors
                ],
                "final_group_ids": [
                    group.group_hypothesis_id for group in partition.successors
                ],
                "lineage_ids": [edge.lineage_edge_id for edge in partition.lineage],
            }
        )
        object.__setattr__(self, "basis_bytes", basis)
        object.__setattr__(
            self,
            "receipt_id",
            typed_identity_digest("xic.cross-sample-grouping-result:", basis),
        )

    @property
    def processing_request_id(self) -> str:
        return self.processing_owners.request.processing_request_id

    @property
    def initial_population(self) -> tuple[CrossSampleGroupIdentity, ...]:
        return self.partition_transition.predecessors

    @property
    def final_population(self) -> tuple[CrossSampleGroupIdentity, ...]:
        return self.partition_transition.successors
