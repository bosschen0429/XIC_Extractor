"""Solve sparse per-sample feature competition with private unassigned options."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

import numpy as np
from scipy.optimize import linear_sum_assignment

from xic_extractor.alignment.edge_scoring import RTEvidenceBasis
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner

_UNASSIGNED_COST = 1.0


_INFEASIBLE_COST = 1_000_000.0


_AMBIGUITY_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class _Candidate:
    feature_key: str
    cost: float | None
    state: Literal["supported", "evaluated_undecided", "proven_incompatible"]
    reason: str
    witness_owner_ids: tuple[str, ...]
    selected_witness_owner_id: str | None = None
    rt_basis: RTEvidenceBasis | None = None
    rt_raw_delta_sec: float | None = None
    rt_drift_corrected_delta_sec: float | None = None


def _solve_sample_assignment(
    owners: Sequence[SampleLocalMS1Owner],
    candidates_by_owner: Mapping[str, tuple[_Candidate, ...]],
    counts: Counter[str],
    *,
    ambiguity_margin: float = _AMBIGUITY_EPSILON,
    owner_order: Mapping[str, tuple[object, ...]] | None = None,
    assess_ambiguity: bool = True,
) -> tuple[dict[str, str], frozenset[str]]:
    arcs = {
        owner.owner_id: {
            candidate.feature_key: cast(float, candidate.cost)
            for candidate in candidates_by_owner[owner.owner_id]
            if candidate.state == "supported"
        }
        for owner in owners
    }
    counts["supported_candidate_arcs"] += sum(len(options) for options in arcs.values())
    reverse: dict[str, set[str]] = defaultdict(set)
    for owner_id, options in arcs.items():
        for feature_key in options:
            reverse[feature_key].add(owner_id)
    pending = {owner_id for owner_id, options in arcs.items() if options}
    assignments: dict[str, str] = {}
    ambiguous: set[str] = set()

    while pending:
        queue = [min(pending)]
        row_ids: set[str] = set()
        feature_keys: set[str] = set()
        while queue:
            owner_id = queue.pop()
            if owner_id in row_ids:
                continue
            row_ids.add(owner_id)
            for feature_key in arcs[owner_id]:
                if feature_key not in feature_keys:
                    feature_keys.add(feature_key)
                    queue.extend(reverse[feature_key] - row_ids)
        pending.difference_update(row_ids)
        rows = sorted(row_ids, key=owner_order.__getitem__ if owner_order else None)
        columns = sorted(feature_keys)
        counts["largest_component_owners"] = max(
            counts["largest_component_owners"], len(rows)
        )
        counts["largest_component_features"] = max(
            counts["largest_component_features"], len(columns)
        )
        counts["largest_component_arcs"] = max(
            counts["largest_component_arcs"], sum(len(arcs[key]) for key in rows)
        )
        if len(rows) == len(columns) == 1:
            assignments[rows[0]] = columns[0]
            if assess_ambiguity and (
                _UNASSIGNED_COST <= arcs[rows[0]][columns[0]] + ambiguity_margin
            ):
                ambiguous.add(rows[0])
            continue
        counts["competition_components"] += 1
        if len(rows) == 1:
            owner_id = rows[0]
            feature_key = min(columns, key=lambda key: (arcs[owner_id][key], key))
            assignments[owner_id] = feature_key
            counts["trivial_assignment_components"] += 1
            if not assess_ambiguity:
                continue
            alternative = min(
                _UNASSIGNED_COST,
                min(arcs[owner_id][key] for key in columns if key != feature_key),
            )
            if alternative <= arcs[owner_id][feature_key] + ambiguity_margin:
                ambiguous.add(owner_id)
            continue
        if len(columns) == 1:
            feature_key = columns[0]
            owner_id = min(
                rows,
                key=lambda key: (
                    arcs[key][feature_key],
                    owner_order[key] if owner_order else key,
                ),
            )
            assignments[owner_id] = feature_key
            counts["trivial_assignment_components"] += 1
            if not assess_ambiguity:
                continue
            best = arcs[owner_id][feature_key]
            alternative = min(
                _UNASSIGNED_COST,
                min(arcs[key][feature_key] for key in rows if key != owner_id),
            )
            # The stable ID breaks only the diagnostic representation tie.
            # Equal/near-equal optima remain ambiguous and cannot add membership.
            if alternative <= best + ambiguity_margin:
                ambiguous.add(owner_id)
            continue
        matrix = _assignment_matrix(rows, columns, arcs)
        row_indexes, column_indexes = linear_sum_assignment(matrix)
        counts["assignment_solver_calls"] += 1
        column_by_index = dict(enumerate(columns))
        selected = {
            rows[row_index]: column_by_index[column_index]
            for row_index, column_index in zip(row_indexes, column_indexes, strict=True)
            if column_index < len(columns)
        }
        assignments.update(selected)
        if not assess_ambiguity:
            continue
        optimum = float(matrix[row_indexes, column_indexes].sum())
        row_index_by_id = {owner_id: index for index, owner_id in enumerate(rows)}
        column_index_by_key = {key: index for index, key in enumerate(columns)}
        for owner_id, feature_key in selected.items():
            row_index = row_index_by_id[owner_id]
            column_index = column_index_by_key[feature_key]
            saved = matrix[row_index, column_index]
            matrix[row_index, column_index] = _INFEASIBLE_COST
            alternative_rows, alternative_columns = linear_sum_assignment(matrix)
            counts["alternative_solver_calls"] += 1
            alternative = float(matrix[alternative_rows, alternative_columns].sum())
            matrix[row_index, column_index] = saved
            if alternative <= optimum + ambiguity_margin:
                ambiguous.add(owner_id)
    counts["ambiguous_assignments"] += len(ambiguous)
    return assignments, frozenset(ambiguous)


def _assignment_matrix(
    rows: Sequence[str],
    columns: Sequence[str],
    arcs: Mapping[str, Mapping[str, float]],
) -> np.ndarray:
    matrix = np.full((len(rows), len(columns) + len(rows)), _INFEASIBLE_COST)
    column_index = {feature_key: index for index, feature_key in enumerate(columns)}
    for row_index, owner_id in enumerate(rows):
        for feature_key, cost in arcs[owner_id].items():
            matrix[row_index, column_index[feature_key]] = cost
        matrix[row_index, len(columns) + row_index] = _UNASSIGNED_COST
    return matrix
