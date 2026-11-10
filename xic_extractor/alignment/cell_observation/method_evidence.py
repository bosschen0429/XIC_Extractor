"""Evidence states consumed by cell closure and conservative recovery."""

from __future__ import annotations

from typing import Literal, TypeAlias

FinalCellEvidenceState: TypeAlias = Literal[
    "direct_observation",
    "ms1_only_backfill_observation",
    "unresolved",
    "rejected_observation",
    "searched_no_observation",
    "unavailable",
    "not_searched",
]
