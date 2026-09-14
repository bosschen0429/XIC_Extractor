"""Deterministic, balanced sampling of actual cross-sample review items."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Sequence

from xic_inspector.selection.canonical import CanonicalItem
from xic_inspector.selection.strata import STRATA_ORDER

SAMPLING_ALGORITHM = "cross_sample_sha256_v2"
_COVERAGE_ORDER = ("broad", "partial", "limited", "candidate")
_ORIGIN_ORDER = (
    "recovery_support", "reacquired_support", "discovery_support", "unavailable"
)


def review_case_key(item: CanonicalItem) -> tuple[str, str, str]:
    """Use scoped observation facts, never cell presence as coverage."""
    if item.kind == "candidate":
        return "candidate", "unavailable", item.stratum
    summary = item.details.get("review_summary", {})
    observed = summary.get("observed_sample_count", len({
        witness.canonical_sample_identity_id for witness in item.witnesses
        if witness.observation_id is not None
    }))
    scope = summary.get("scope_sample_count", len(item.sample_ids))
    coverage = (
        "broad" if observed >= max(2, scope - 1)
        else "partial" if observed >= 2 else "limited"
    )
    origins = summary.get("origin_kinds", ())
    origin = next((kind for kind in _ORIGIN_ORDER if kind in origins), "unavailable")
    return coverage, origin if coverage != "limited" else "unavailable", item.stratum


def sample_cross_sample_sha256_v2(
    items: Sequence[CanonicalItem], *, limit: int = 30, seed: int = 0,
) -> list[CanonicalItem]:
    """Cycle through family cases; candidates only fill spare slots."""
    if limit <= 0 or not items:
        return []
    buckets: dict[tuple[str, str, str], list[CanonicalItem]] = defaultdict(list)
    for item in items:
        buckets[review_case_key(item)].append(item)

    def rank(item: CanonicalItem) -> tuple[str, bytes]:
        payload = json.dumps(
            [2, str(seed), "pick", item.serialized_ref],
            ensure_ascii=False, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest(), item.sort_key

    keys = sorted(buckets, key=lambda key: (
        _COVERAGE_ORDER.index(key[0]), _ORIGIN_ORDER.index(key[1]),
        STRATA_ORDER.index(key[2]),
    ))
    queues = {key: deque(sorted(buckets[key], key=rank)) for key in keys}
    selected = []
    for candidate_phase in (False, True):
        active = [key for key in keys if (key[0] == "candidate") == candidate_phase]
        while active and len(selected) < limit:
            for key in active:
                selected.append(queues[key].popleft())
                if len(selected) == limit:
                    break
            active = [key for key in active if queues[key]]
    return sorted(selected, key=lambda item: item.sort_key)
