"""Select completed review evidence before assembling the selected witnesses.

SQL filters coordinates over the report's review views; Inspector owns ranking
and presentation. Only selected items are expanded into witnesses.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from xic_extractor.alignment_explanation.storage import (
    open_readonly,
)


@dataclass(frozen=True)
class IndexedItem:
    """One selected row/group/candidate and its scoped recorded coordinates."""

    kind: Literal["row", "group", "candidate"]
    identifier: str
    group_id: str | None
    sample_ids: tuple[str, ...]
    candidate_sample_id: str | None
    witnesses: tuple[tuple[str, str, float | None, float | None, str | None, str], ...]


@dataclass(frozen=True)
class _Item:
    """Only the identity and group disposition needed to order an item."""

    kind: Literal["row", "group", "candidate"]
    identifier: str
    group_id: str | None
    sample_id: str | None
    sort_key: bytes


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
        self._items: dict[int, _Item] = {}
        for group, feature in db.execute(
            "SELECT id,feature_id FROM groups ORDER BY feature_id IS NULL,id"
        ):
            kind: Literal["row", "group", "candidate"] = (
                "row" if feature is not None else "group"
            )
            identifier = feature if feature is not None else str(group)
            self._items[group] = _Item(
                kind,
                identifier,
                identifier,
                None,
                json.dumps(
                    [kind, identifier], ensure_ascii=False, separators=(",", ":")
                ).encode(),
            )
        for item, cid, sid in db.execute("""SELECT ic.item,c.candidate_id,s.sample_key
            FROM review_item_candidates ic JOIN candidates c ON c.id=ic.candidate
            JOIN samples s ON s.id=c.sample WHERE ic.item<0 ORDER BY c.id"""):
            self._items[item] = _Item(
                "candidate",
                cid,
                None,
                sid,
                json.dumps(
                    ["candidate", sid, cid], ensure_ascii=False, separators=(",", ":")
                ).encode(),
            )

    def item_headers(self) -> list[tuple[int, str, str]]:
        return [(key, item.kind, item.identifier) for key, item in self._items.items()]

    def _scope(self, scope: Sequence[str]) -> str:
        return json.dumps([self._samples[sid] for sid in scope if sid in self._samples])

    def _eligible(self, scope: Sequence[str], within=None) -> int:
        query = (
            "SELECT COUNT(DISTINCT item) FROM review_samples "
            "WHERE sample IN (SELECT value FROM json_each(?))"
        )
        parameters = [self._scope(scope)]
        if within is not None:
            query += " AND item IN (SELECT value FROM json_each(?))"
            parameters.append(json.dumps(list(within)))
        return self._db.execute(query, parameters).fetchone()[0]

    def page(
        self,
        scope,
        *,
        candidate_ids=(),
        group_ids=(),
        mz_min=None,
        mz_max=None,
        rt_min=None,
        rt_max=None,
        offset=0,
        limit=30,
        ordered_item_ids=None,
    ):
        """Filter each witness as one row, returning only matching item handles."""
        where = ["sample IN (SELECT value FROM json_each(?))"]
        parameters: list[Any] = [self._scope(scope)]
        priority = (
            None
            if ordered_item_ids is None
            else {item: index for index, item in enumerate(ordered_item_ids)}
        )
        allowed = None if priority is None else set(priority)
        if group_ids:
            groups = {
                key for key, item in self._items.items() if item.group_id in group_ids
            }
            allowed = groups if allowed is None else allowed & groups
        if allowed is not None:
            where.append("item IN (SELECT value FROM json_each(?))")
            parameters.append(json.dumps(sorted(allowed)))
        if candidate_ids:
            where.append("candidate_id IN (SELECT value FROM json_each(?))")
            parameters.append(json.dumps(list(candidate_ids)))
        for bound, operator in ((mz_min, ">="), (mz_max, "<=")):
            if bound is not None:
                where.append(f"mz {operator} ?")
                parameters.append(bound)
        matched, missing = [], 0
        predicate = " AND ".join(where)
        if rt_min is not None or rt_max is not None:
            rows = self._db.execute(
                "SELECT item,MAX(rt IS NOT NULL),"
                "MAX(rt IS NOT NULL AND (? IS NULL OR rt>=?) "
                "AND (? IS NULL OR rt<=?)) "
                f"FROM review_witnesses WHERE {predicate} GROUP BY item",
                [rt_min, rt_min, rt_max, rt_max, *parameters],
            )
            for key, has_rt, rt_match in rows:
                missing += not has_rt
                if rt_match:
                    matched.append(key)
        else:
            matched = [
                row[0]
                for row in self._db.execute(
                    f"SELECT DISTINCT item FROM review_witnesses WHERE {predicate}",
                    parameters,
                )
            ]
        matched.sort(
            key=lambda key: (
                priority[key] if priority is not None else 0,
                self._items[key].sort_key,
            )
        )
        page = matched[offset:] if limit < 0 else matched[offset : offset + limit]
        return page, len(self._items), self._eligible(scope), len(matched), missing

    def nearest_page(
        self, scope, *, mz, offset, limit, ordered_item_ids=None, **criteria
    ):
        """Rank the first scoped witness, as display does, not the nearest one."""
        ids, run_total, eligible, _, missing_rt = self.page(
            scope, **criteria, ordered_item_ids=ordered_item_ids, limit=-1
        )
        rows = self._db.execute(
            """SELECT item,mz FROM (
            SELECT item,mz,ROW_NUMBER() OVER (
                PARTITION BY item ORDER BY candidate_id,sample_key) AS witness_order
            FROM review_witnesses WHERE sample IN (SELECT value FROM json_each(?))
              AND item IN (SELECT value FROM json_each(?))) WHERE witness_order=1""",
            (self._scope(scope), json.dumps(ids)),
        )
        ranked = [
            (abs(value - mz), self._items[key].sort_key, key)
            for key, value in rows
            if value is not None and math.isfinite(value) and value > 0
        ]
        population = len(ids)
        if criteria.get("rt_min") is None and criteria.get("rt_max") is None:
            population = self._eligible(scope, ordered_item_ids)
        ranked.sort()
        distances = {
            key: distance for distance, _, key in ranked[offset : offset + limit]
        }
        return (
            list(distances),
            run_total,
            eligible,
            len(ranked),
            missing_rt,
            population - len(ranked),
            distances,
        )

    def item(self, item_id: int, scope: Sequence[str]) -> IndexedItem:
        item = self._items[item_id]
        samples = self._db.execute(
            """SELECT s.sample_key FROM review_samples r
            JOIN samples s ON s.id=r.sample WHERE item=?
              AND sample IN (SELECT value FROM json_each(?)) ORDER BY s.sample_key""",
            (item_id, self._scope(scope)),
        )
        witnesses = self._db.execute(
            """SELECT sample_key,candidate_id,mz,rt,peak,sample_stem
            FROM review_witnesses WHERE item=?
              AND sample IN (SELECT value FROM json_each(?))
            ORDER BY candidate_id,sample_key""",
            (item_id, self._scope(scope)),
        )
        return IndexedItem(
            item.kind,
            item.identifier,
            item.group_id,
            tuple(row[0] for row in samples),
            item.sample_id,
            tuple(witnesses),
        )
