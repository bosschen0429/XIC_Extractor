from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
    OwnerAssignment,
    SampleLocalMS1Owner,
)


@dataclass(frozen=True)
class OwnershipBuildResult:
    owners: tuple[SampleLocalMS1Owner, ...]
    assignments: tuple[OwnerAssignment, ...]
    ambiguous_records: tuple[AmbiguousOwnerRecord, ...]
