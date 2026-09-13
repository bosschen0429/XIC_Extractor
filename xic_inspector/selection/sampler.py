"""Stratified SHA-256 sampling algorithm (stratified_sha256_v1) for XIC Inspector."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Sequence

from xic_inspector.selection.canonical import CanonicalItem
from xic_inspector.selection.strata import STRATA_ORDER, StratumKey


def compute_hash_rank(seed: int, tag: str, key: str) -> str:
    """Compute SHA-256 hex digest for [1, str(seed), tag, key] as canonical JSON."""
    payload = json.dumps(
        [1, str(seed), tag, key], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sample_stratified_sha256_v1(
    items: Sequence[CanonicalItem],
    *,
    limit: int = 30,
    seed: int = 0,
) -> list[CanonicalItem]:
    """Sample up to `limit` items using the stratified_sha256_v1 algorithm.

    1. Group items into non-empty strata (attention, excluded, null, zero, rest).
    2. If limit < len(non_empty_strata):
       Select top `limit` strata by (stratum_rank, stratum_name).
       Pick 1 best item from each selected stratum by (pick_rank, item_ref).
    3. If limit >= len(non_empty_strata):
       Pick 1 best item from each non-empty stratum by (pick_rank, item_ref).
       Fill remaining slots from all remaining items by (fill_rank, item_ref).
    4. Return selected items sorted by canonical item ref UTF-8 bytes.
    """
    if limit <= 0 or not items:
        return []

    # Group by stratum
    strata_map: dict[str, list[CanonicalItem]] = defaultdict(list)
    for item in items:
        strata_map[item.stratum].append(item)

    non_empty_strata: list[StratumKey] = [
        s for s in STRATA_ORDER if s in strata_map and strata_map[s]
    ]

    if not non_empty_strata:
        return []

    # Rank strata using tag='stratum'
    ranked_strata = sorted(
        non_empty_strata,
        key=lambda s: (compute_hash_rank(seed, "stratum", s), s),
    )

    selected: list[CanonicalItem] = []
    selected_refs: set[str] = set()

    if limit < len(non_empty_strata):
        # Pick 1 item from the top `limit` strata
        chosen_strata = ranked_strata[:limit]
        for stratum in chosen_strata:
            stratum_items = strata_map[stratum]
            best_item = min(
                stratum_items,
                key=lambda it: (
                    compute_hash_rank(seed, "pick", it.serialized_ref),
                    it.serialized_ref,
                ),
            )
            selected.append(best_item)
            selected_refs.add(best_item.serialized_ref)
    else:
        # Pick 1 item from every non-empty stratum
        for stratum in non_empty_strata:
            stratum_items = strata_map[stratum]
            best_item = min(
                stratum_items,
                key=lambda it: (
                    compute_hash_rank(seed, "pick", it.serialized_ref),
                    it.serialized_ref,
                ),
            )
            selected.append(best_item)
            selected_refs.add(best_item.serialized_ref)

        # Fill remaining slots up to limit
        remaining_needed = limit - len(selected)
        if remaining_needed > 0:
            remaining_pool = [
                it for it in items if it.serialized_ref not in selected_refs
            ]
            ranked_remaining = sorted(
                remaining_pool,
                key=lambda it: (
                    compute_hash_rank(seed, "fill", it.serialized_ref),
                    it.serialized_ref,
                ),
            )
            selected.extend(ranked_remaining[:remaining_needed])

    # Final sort by canonical item ref UTF-8 bytes lexicographical order
    return sorted(selected, key=lambda it: it.sort_key)
