"""Measured alternatives for an accepted member or Backfill cell."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
    from xic_extractor.alignment.sample_rt_calibration import RtEstimate

OwnerFacts = tuple[str, float, float]
OwnerGroup = tuple[OwnerFacts, ...]
OwnerPartition = tuple[OwnerGroup, ...]


def measured_members(owners: Iterable[SampleLocalMS1Owner]) -> OwnerGroup:
    return tuple(
        sorted(
            (owner.peak.sample_key, owner.peak.mz, owner.peak.rt_apex_min)
            for owner in owners
        )
    )


@dataclass(frozen=True, slots=True)
class AcceptedMembershipRisk:
    status: str
    phase: str
    selected_owners: OwnerGroup
    alternative_partition: OwnerPartition | None
    equivalent_alternatives: tuple[OwnerPartition, ...]
    rt_basis: str
    evidence_basis: str
    soft_contradictions: tuple[str, ...]
    risk_flags: tuple[str, ...]
    rt_estimate: RtEstimate | None = None
