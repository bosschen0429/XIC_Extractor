from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class CandidateLike(Protocol):
    sample_stem: str
    neutral_loss_tag: str
    precursor_mz: float
    product_mz: float
    observed_neutral_loss_da: float
    ms1_apex_rt: float | None
    best_seed_rt: float | None


@dataclass(frozen=True)
class AlignmentCluster:
    cluster_id: str
    neutral_loss_tag: str
    cluster_center_mz: float
    cluster_center_rt: float
    cluster_product_mz: float
    cluster_observed_neutral_loss_da: float
    has_anchor: bool
    members: tuple[CandidateLike, ...]
    anchor_members: tuple[CandidateLike, ...] = ()
    folded_cluster_ids: tuple[str, ...] = ()
    folded_member_count: int = 0
    folded_sample_fill_count: int = 0
    fold_evidence: str = ""
