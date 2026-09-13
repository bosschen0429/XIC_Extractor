"""Review-mode predicates for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Sequence

from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
)


def _is_cid_nl_successor_review_group(group: ReconciliationGroup) -> bool:
    return group.seed_group_basis in {
        "cid_nl_successor_authority",
        "cid_nl_feature_inclusion_review",
    }


def _is_cid_nl_differential_review_group(group: ReconciliationGroup) -> bool:
    return (
        _is_cid_nl_successor_review_group(group)
        and group.seed_group_id.startswith("cid_nl_differential::")
    )


def _cid_nl_transition_label(group: ReconciliationGroup) -> str:
    if not _is_cid_nl_differential_review_group(group):
        return group.feature_family_id
    return group.seed_group_id.removeprefix("cid_nl_differential::")


def _is_cid_nl_successor_review_index(
    groups: Sequence[ReconciliationGroup],
) -> bool:
    return bool(groups) and all(
        _is_cid_nl_successor_review_group(group) for group in groups
    )
