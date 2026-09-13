"""Query and attention strategy filters for XIC Inspector."""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from xic_inspector.selection.canonical import CanonicalItem, Witness


def witness_matches_criteria(
    witness: Witness,
    *,
    sample_ids: set[str] | None = None,
    candidate_ids: set[str] | None = None,
    group_ids: set[str] | None = None,
    mz_min: float | None = None,
    mz_max: float | None = None,
    rt_min: float | None = None,
    rt_max: float | None = None,
) -> bool:
    """Check whether a single witness chain satisfies all query criteria."""
    # Sample check
    if (
        sample_ids is not None
        and witness.canonical_sample_identity_id not in sample_ids
    ):
        return False

    # Candidate check
    if candidate_ids is not None and witness.candidate_id not in candidate_ids:
        return False

    # Group check
    if group_ids is not None:
        if (
            witness.group_hypothesis_id is None
            or witness.group_hypothesis_id not in group_ids
        ):
            return False

    # m/z check (inclusive bounds)
    if mz_min is not None:
        if witness.precursor_mz is None or witness.precursor_mz < mz_min:
            return False
    if mz_max is not None:
        if witness.precursor_mz is None or witness.precursor_mz > mz_max:
            return False

    # RT check (inclusive bounds; requires actual apex in minutes)
    if rt_min is not None:
        if witness.rt_apex_min is None or witness.rt_apex_min < rt_min:
            return False
    if rt_max is not None:
        if witness.rt_apex_min is None or witness.rt_apex_min > rt_max:
            return False

    return True


def witness_matches_non_rt_criteria(
    witness: Witness,
    *,
    sample_ids: set[str] | None = None,
    candidate_ids: set[str] | None = None,
    group_ids: set[str] | None = None,
    mz_min: float | None = None,
    mz_max: float | None = None,
) -> bool:
    """Check whether a witness satisfies non-RT criteria."""
    if (
        sample_ids is not None
        and witness.canonical_sample_identity_id not in sample_ids
    ):
        return False
    if candidate_ids is not None and witness.candidate_id not in candidate_ids:
        return False
    if group_ids is not None:
        if (
            witness.group_hypothesis_id is None
            or witness.group_hypothesis_id not in group_ids
        ):
            return False
    if mz_min is not None:
        if witness.precursor_mz is None or witness.precursor_mz < mz_min:
            return False
    if mz_max is not None:
        if witness.precursor_mz is None or witness.precursor_mz > mz_max:
            return False
    return True


def filter_by_query(
    items: Sequence[CanonicalItem],
    *,
    sample_ids: tuple[str, ...] = (),
    candidate_ids: tuple[str, ...] = (),
    group_ids: tuple[str, ...] = (),
    mz_min: float | None = None,
    mz_max: float | None = None,
    rt_min: float | None = None,
    rt_max: float | None = None,
    offset: int = 0,
    limit: int = 30,
) -> tuple[list[CanonicalItem], int, int]:
    """Execute query strategy over eligible items.

    Returns:
        (selected_items, matched_count, missing_rt_count)
    """
    sample_set = set(sample_ids) if sample_ids else None
    candidate_set = set(candidate_ids) if candidate_ids else None
    group_set = set(group_ids) if group_ids else None

    has_rt_filter = rt_min is not None or rt_max is not None

    matched_items: list[CanonicalItem] = []
    missing_rt_count = 0

    for item in items:
        # Check full criteria
        matched_witnesses = [
            w
            for w in item.witnesses
            if witness_matches_criteria(
                w,
                sample_ids=sample_set,
                candidate_ids=candidate_set,
                group_ids=group_set,
                mz_min=mz_min,
                mz_max=mz_max,
                rt_min=rt_min,
                rt_max=rt_max,
            )
        ]
        if matched_witnesses:
            matched_items.append(
                replace(item, matched_witnesses=tuple(matched_witnesses))
            )
        elif has_rt_filter:
            # Passed non-RT criteria but had no RT apex across witnesses
            candidate_non_rt_witnesses = [
                w
                for w in item.witnesses
                if witness_matches_non_rt_criteria(
                    w,
                    sample_ids=sample_set,
                    candidate_ids=candidate_set,
                    group_ids=group_set,
                    mz_min=mz_min,
                    mz_max=mz_max,
                )
            ]
            if candidate_non_rt_witnesses and all(
                w.rt_apex_min is None for w in candidate_non_rt_witnesses
            ):
                missing_rt_count += 1

    # Sorted by canonical item ref UTF-8 bytes
    sorted_matched = sorted(matched_items, key=lambda it: it.sort_key)
    matched_count = len(sorted_matched)

    # Apply offset and limit
    selected = sorted_matched[offset : offset + limit]
    return selected, matched_count, missing_rt_count


def filter_by_attention(
    items: Sequence[CanonicalItem],
    *,
    limit: int = 30,
) -> tuple[list[CanonicalItem], int]:
    """Execute attention strategy: select items matching scoped attention predicate."""
    matched = [it for it in items if it.is_attention or it.stratum == "attention"]
    sorted_matched = sorted(matched, key=lambda it: it.sort_key)
    matched_count = len(sorted_matched)
    selected = sorted_matched[:limit]
    return selected, matched_count
