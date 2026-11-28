"""Select completed review evidence before assembling the selected witnesses.

SQL scopes, filters, orders and pages the report's review items; only the
selected page is expanded into witnesses. Inspector owns presentation.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from xic_extractor.alignment_explanation.storage import (
    open_readonly,
)

ReviewScope = Literal[
    "all", "matrix", "unresolved", "archive", "curation_removed", "candidates"
]

_SCOPES: dict[str, str] = {
    "all": "1",
    "candidates": "kind = 'candidate'",
    "matrix": "destination = 'matrix'",
    "archive": "destination = 'archive'",
    "curation_removed": "destination = 'curation_removed'",
    "unresolved": "destination = 'matrix' AND unresolved_count > 0",
}
_SCOPED = """scoped AS (
    SELECT item, kind, identifier, sort_key, numeric_count, unresolved_count,
        CASE WHEN destination = 'matrix'
                AND identifier IN (SELECT value FROM json_each(:removed))
            THEN 'curation_removed' ELSE destination END AS destination
    FROM review_items)"""
_PRIORITY = (
    "unresolved_count DESC, numeric_count DESC, "
    "kind = 'candidate', kind = 'group', abs(item)"
)
_IN_SAMPLES = "sample IN (SELECT value FROM json_each(:samples))"


@dataclass(frozen=True)
class IndexedItem:
    """One selected row/group/candidate and its scoped recorded coordinates."""

    kind: Literal["row", "group", "candidate"]
    identifier: str
    group_id: str | None
    sample_ids: tuple[str, ...]
    candidate_sample_id: str | None
    witnesses: tuple[tuple[str, str, float | None, float | None, str | None, str], ...]


@contextmanager
def open_review_query(path: Path, summary: Mapping[str, Any]) -> Iterator[ReviewQuery]:
    """One request-local selection session, with no cross-request cache."""
    with open_readonly(path) as db:
        yield ReviewQuery(db, summary)


class ReviewQuery:
    """Query stored facts; expand coordinates only for the selected page."""

    def __init__(self, db: sqlite3.Connection, summary: Mapping[str, Any]):
        self._db = db
        self._samples = {
            sample["sample_key"]: index
            for index, sample in enumerate(summary["samples"])
        }

    def _scope(self, scope: Sequence[str]) -> str:
        return json.dumps([self._samples[sid] for sid in scope if sid in self._samples])

    def overview(self, removed: Sequence[str] = ()) -> dict[str, Any]:
        """Counts of the published rows by destination and of their values."""
        parameters = {"removed": json.dumps(list(removed))}
        rows = f"WITH {_SCOPED} "
        matrix, unresolved, complete, archive, curation_removed = self._db.execute(
            rows + "SELECT COUNT(*) FILTER (WHERE destination = 'matrix'),"
            "COUNT(*) FILTER (WHERE destination = 'matrix' AND unresolved_count > 0),"
            "COUNT(*) FILTER (WHERE destination = 'matrix' "
            "AND numeric_count = (SELECT COUNT(*) FROM samples)),"
            "COUNT(*) FILTER (WHERE destination = 'archive'),"
            "COUNT(*) FILTER (WHERE destination = 'curation_removed') "
            "FROM scoped WHERE kind = 'row'",
            parameters,
        ).fetchone()
        return {
            "kind": "untargeted",
            "matrix_count": matrix,
            "unresolved_count": unresolved,
            "complete_count": complete,
            "archive_count": archive,
            "curation_removed_count": curation_removed,
            "samples": [
                {"id": key, "label": stem, "numeric_count": numeric}
                for key, stem, numeric in self._db.execute(
                    rows + "SELECT s.sample_key, s.sample_stem, ("
                    "SELECT COUNT(*) FROM cells c JOIN scoped r ON r.item = c.group_id "
                    "WHERE c.sample = s.id AND c.variant = 'numeric' "
                    "AND r.kind = 'row' AND r.destination = 'matrix') "
                    "FROM samples s ORDER BY s.id",
                    parameters,
                )
            ],
        }

    def candidate_count(self) -> int:
        """Candidates that no final group explains."""
        return self._db.execute(
            "SELECT COUNT(*) FROM review_items WHERE kind = 'candidate'"
        ).fetchone()[0]

    def _eligible(self, parameters: Mapping[str, Any], within: str = "") -> int:
        return self._db.execute(
            f"WITH {_SCOPED} SELECT COUNT(DISTINCT item) FROM review_samples "
            f"WHERE {_IN_SAMPLES}{within}",
            parameters,
        ).fetchone()[0]

    def _matched(
        self,
        scope: Sequence[str],
        *,
        review_scope: ReviewScope,
        removed: Sequence[str],
        candidate_ids: Sequence[str],
        group_ids: Sequence[str],
        mz_min: float | None,
        mz_max: float | None,
        rt_min: float | None,
        rt_max: float | None,
    ) -> tuple[str, dict[str, Any]]:
        """The scoped items with a witness that passes every filter."""
        parameters: dict[str, Any] = {
            "samples": self._scope(scope),
            "removed": json.dumps(list(removed)),
            "rt_min": rt_min,
            "rt_max": rt_max,
        }
        selected = _SCOPES[review_scope]
        if group_ids:
            selected += (
                " AND kind != 'candidate' "
                "AND identifier IN (SELECT value FROM json_each(:groups))"
            )
            parameters["groups"] = json.dumps(list(group_ids))
        where = [_IN_SAMPLES, "item IN (SELECT item FROM selected)"]
        if candidate_ids:
            where.append("candidate_id IN (SELECT value FROM json_each(:candidates))")
            parameters["candidates"] = json.dumps(list(candidate_ids))
        for name, bound, operator in (
            ("mz_min", mz_min, ">="),
            ("mz_max", mz_max, "<="),
        ):
            if bound is not None:
                where.append(f"mz {operator} :{name}")
                parameters[name] = bound
        return (
            f"WITH {_SCOPED}, "
            f"selected AS (SELECT * FROM scoped WHERE {selected}), "
            "matched AS (SELECT item, MAX(rt IS NOT NULL) AS has_rt, "
            "(:rt_min IS NULL AND :rt_max IS NULL) OR MAX(rt IS NOT NULL "
            "AND (:rt_min IS NULL OR rt >= :rt_min) "
            "AND (:rt_max IS NULL OR rt <= :rt_max)) AS in_rt "
            f"FROM review_witnesses WHERE {' AND '.join(where)} GROUP BY item)",
            parameters,
        )

    def page(
        self,
        scope: Sequence[str],
        *,
        review_scope: ReviewScope = "all",
        removed: Sequence[str] = (),
        candidate_ids: Sequence[str] = (),
        group_ids: Sequence[str] = (),
        mz_min: float | None = None,
        mz_max: float | None = None,
        rt_min: float | None = None,
        rt_max: float | None = None,
        offset: int = 0,
        limit: int = 30,
    ) -> tuple[list[int], int, int, int, int]:
        """One page of matching item handles, with the run, eligible and matched
        counts and how many filtered items record no RT.

        A reviewed scope leads with unresolved, then well-covered rows; the whole
        report is listed by identity. Rows a curation removed leave the Matrix
        scope without changing the report.
        """
        matched, parameters = self._matched(
            scope,
            review_scope=review_scope,
            removed=removed,
            candidate_ids=candidate_ids,
            group_ids=group_ids,
            mz_min=mz_min,
            mz_max=mz_max,
            rt_min=rt_min,
            rt_max=rt_max,
        )
        order = "sort_key" if review_scope == "all" else _PRIORITY
        page = [
            row[0]
            for row in self._db.execute(
                f"{matched} SELECT item FROM matched JOIN selected USING (item) "
                f"WHERE in_rt ORDER BY {order} LIMIT :limit OFFSET :offset",
                {**parameters, "limit": limit, "offset": offset},
            )
        ]
        count, without_rt = self._db.execute(
            f"{matched} SELECT COUNT(*) FILTER (WHERE in_rt), "
            "COUNT(*) FILTER (WHERE NOT has_rt) FROM matched",
            parameters,
        ).fetchone()
        filtered = rt_min is not None or rt_max is not None
        return (
            page,
            self._db.execute("SELECT COUNT(*) FROM review_items").fetchone()[0],
            self._eligible(parameters),
            count,
            without_rt if filtered else 0,
        )

    def nearest_page(
        self,
        scope: Sequence[str],
        *,
        mz: float,
        offset: int,
        limit: int,
        review_scope: ReviewScope = "all",
        removed: Sequence[str] = (),
        **criteria: Any,
    ) -> tuple[list[int], int, int, int, int, int, dict[int, float]]:
        """Rank the first scoped witness, as display does, not the nearest one."""
        matched, parameters = self._matched(
            scope, review_scope=review_scope, removed=removed, **criteria
        )
        _, run_total, eligible, population, missing_rt = self.page(
            scope, review_scope=review_scope, removed=removed, **criteria, limit=0
        )
        ranked = (
            f"{matched}, first AS (SELECT item, mz FROM ("
            "SELECT item, mz, ROW_NUMBER() OVER ("
            "PARTITION BY item ORDER BY candidate_id, sample_key) AS witness_order "
            f"FROM review_witnesses WHERE {_IN_SAMPLES} "
            "AND item IN (SELECT item FROM matched WHERE in_rt)) "
            "WHERE witness_order = 1), "
            "ranked AS (SELECT item, abs(mz - :mz) AS distance, sort_key "
            "FROM first JOIN selected USING (item) WHERE mz > 0 AND mz < 9e999)"
        )
        parameters = {**parameters, "mz": mz}
        distances = dict(
            self._db.execute(
                f"{ranked} SELECT item, distance FROM ranked "
                "ORDER BY distance, sort_key, item LIMIT :limit OFFSET :offset",
                {**parameters, "limit": limit, "offset": offset},
            )
        )
        (ranked_count,) = self._db.execute(
            f"{ranked} SELECT COUNT(*) FROM ranked", parameters
        ).fetchone()
        if criteria.get("rt_min") is None and criteria.get("rt_max") is None:
            population = self._eligible(
                parameters,
                f" AND item IN (SELECT item FROM scoped WHERE {_SCOPES[review_scope]})",
            )
        return (
            list(distances),
            run_total,
            eligible,
            ranked_count,
            missing_rt,
            population - ranked_count,
            distances,
        )

    def item(self, item_id: int, scope: Sequence[str]) -> IndexedItem:
        kind, identifier, sample_key = self._db.execute(
            "SELECT kind, identifier, sample_key FROM review_items WHERE item = ?",
            (item_id,),
        ).fetchone()
        parameters = {"item": item_id, "samples": self._scope(scope)}
        samples = self._db.execute(
            "SELECT s.sample_key FROM review_samples r "
            f"JOIN samples s ON s.id = r.sample WHERE item = :item AND {_IN_SAMPLES} "
            "ORDER BY s.sample_key",
            parameters,
        )
        witnesses = self._db.execute(
            "SELECT sample_key, candidate_id, mz, rt, peak, sample_stem "
            f"FROM review_witnesses WHERE item = :item AND {_IN_SAMPLES} "
            "ORDER BY candidate_id, sample_key",
            parameters,
        )
        return IndexedItem(
            kind,
            identifier,
            None if kind == "candidate" else identifier,
            tuple(row[0] for row in samples),
            sample_key,
            tuple(witnesses),
        )
