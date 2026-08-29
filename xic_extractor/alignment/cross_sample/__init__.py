"""CrossSample-owned grouping, identity, and lineage handoffs."""

from xic_extractor.alignment.cross_sample.association import (
    CrossSampleAssociationContractError,
    CrossSampleAssociationLineageHandoff,
    CrossSampleCoveragePositionAssociation,
    CrossSamplePredecessorAssociation,
    build_cross_sample_association_lineage_handoff,
    validate_cross_sample_association_lineage_handoff,
)

__all__ = [
    "CrossSampleAssociationContractError",
    "CrossSampleAssociationLineageHandoff",
    "CrossSampleCoveragePositionAssociation",
    "CrossSamplePredecessorAssociation",
    "build_cross_sample_association_lineage_handoff",
    "validate_cross_sample_association_lineage_handoff",
]
