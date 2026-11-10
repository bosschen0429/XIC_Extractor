"""Select completed review evidence before assembling the selected witnesses.

SQL filters coordinates and reduces scoped sample facts; Inspector owns ranking
and presentation. Only selected items are expanded into witnesses. Derived
relations live on the request's read-only connection, never in the report.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from xic_extractor.alignment_explanation.relations import relation
from xic_extractor.alignment_explanation.review import (
    ORIGIN_FLAGS,
    attention_reason,
    cell_attention,
    group_attention,
)
from xic_extractor.alignment_explanation.storage import (
    decode_json,
    open_readonly,
    present_tables,
)


def recorded_observations(candidate, linked_records, records, member_ids):
    """Resolve the recorded observations used by both selection and display."""
    observations = [
        observation
        for kind, rid in linked_records
        if kind == "terminal_subjects"
        for observation in records[kind][rid].get("observations", [])
        if not member_ids or observation.get("physical_peak_key") in member_ids
    ]
    return observations or [{"rt_apex_min": candidate.get("rt_apex_min")}]


@dataclass(frozen=True)
class ScopedReviewFacts:
    """Recorded sample counts and flags, independent of their packed encoding."""

    present: int
    observed: int
    attention: bool
    excluded: bool
    nulls: bool
    zeros: bool
    origins: tuple[str, ...]


@dataclass(frozen=True)
class IndexedItem:
    """One selected row/group/candidate and its scoped recorded coordinates."""

    kind: Literal["row", "group", "candidate"]
    identifier: str
    group_id: str | None
    sample_ids: tuple[str, ...]
    candidate_sample_id: str | None
    # sample, candidate, precursor m/z, apex RT, observation, sample label
    witnesses: tuple[tuple[str, str, float | None, float | None, str | None, str], ...]


@dataclass(frozen=True)
class _Item:
    """Only the identity and group disposition needed to order an item."""

    kind: Literal["row", "group", "candidate"]
    identifier: str
    group_id: str | None
    sample_id: str | None
    sort_key: bytes
    attention: bool


def _query_views(db: sqlite3.Connection, *, edges: bool) -> None:
    groups = relation(db, "group_candidates")
    # Positive handles are group row IDs; negative handles are ungrouped
    # candidate row IDs. Neither is displayed or persisted as an item ref.
    db.execute(f"""CREATE TEMP VIEW review_item_candidates AS
        SELECT group_id AS item, candidate FROM {groups}
        UNION ALL
        SELECT -c.id, c.id FROM main.candidates c
        WHERE NOT EXISTS (SELECT 1 FROM {groups} g WHERE g.candidate=c.id)""")
    db.execute("""CREATE TEMP VIEW review_members AS
        SELECT m.final_group AS item, m.sample, j.value AS peak
        FROM main.members m, json_each(m.peaks) j
        WHERE m.final_group IS NOT NULL""")
    db.execute("""CREATE TEMP VIEW review_samples AS
        SELECT ic.item,c.sample FROM review_item_candidates ic
        JOIN main.candidates c ON c.id=ic.candidate
        UNION SELECT group_id,sample FROM main.cells""")
    observed = """(NOT EXISTS (
        SELECT 1 FROM review_members m WHERE m.item=ic.item AND m.sample=c.sample)
        OR EXISTS (SELECT 1 FROM review_members m
                   WHERE m.item=ic.item AND m.sample=c.sample
                     AND m.peak=c.terminal_peak))"""
    db.execute(f"""CREATE TEMP VIEW review_witnesses AS
        SELECT ic.item, c.sample, s.sample_key, c.candidate_id, c.precursor_mz AS mz,
            CASE WHEN {observed} THEN p.rt_apex_min END AS rt,
            CASE WHEN {observed} THEN p.physical_peak_key END AS peak,
            s.sample_stem
        FROM review_item_candidates ic
        JOIN main.candidates c ON c.id=ic.candidate
        JOIN main.samples s ON s.id=c.sample
        LEFT JOIN main.peaks p ON p.id=c.terminal_peak""")
    db.create_function(
        "cell_attention",
        3,
        lambda variant, value, risky: (
            cell_attention(variant, value, bool(risky)) is not None
        ),
        deterministic=True,
    )
    db.create_function(
        "assessment_attention",
        1,
        lambda result: (
            attention_reason("method_assessments", {"method_result": result})
            is not None
        ),
        deterministic=True,
    )
    db.create_function(
        "risks_flagged",
        1,
        lambda risks: any(risk.get("risk_flags") for risk in decode_json(risks) or ()),
        deterministic=True,
    )
    if edges:
        relation(db, "edge_candidates")
        db.create_function(
            "edge_attention",
            2,
            lambda disposition, evidence: (
                attention_reason(
                    "initial_edges",
                    {
                        "disposition": disposition,
                        "initial_evidence": decode_json(evidence) or {},
                    },
                )
                is not None
            ),
            deterministic=True,
        )


@contextmanager
def open_review_query(path: Path, summary: Mapping[str, Any]) -> Iterator[ReviewQuery]:
    """One request-local selection session, with no cross-request cache."""
    with open_readonly(path) as db:
        yield ReviewQuery(db, summary)


class ReviewQuery:
    """Query stored facts; expand coordinates only for the selected page."""

    def __init__(self, db: sqlite3.Connection, summary: Mapping[str, Any]):
        self._db = db
        self._edges = "initial_edges" in present_tables(db)
        self._samples = {
            sample["sample_key"]: index
            for index, sample in enumerate(summary["samples"])
        }
        _query_views(db, edges=self._edges)
        self._items: dict[int, _Item] = {}
        for group, feature, disposition in db.execute(
            "SELECT id,feature_id,disposition FROM groups "
            "ORDER BY feature_id IS NULL,id"
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
                group_attention(disposition),
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
                False,
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
            # Missing RT is counted after non-RT filters, before the range.
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
            # No RT predicate needs the peak/member joins used to resolve RT.
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

    def review_page(
        self,
        scope: Sequence[str],
        *,
        classify: Callable[[str, ScopedReviewFacts], tuple[int, bool, bool]],
        rank: Callable[[bytes], str],
        attention_only: bool,
        limit: int,
    ):
        """Rank scoped per-item summaries; no witness or display payload is read."""
        sample_fields = "MAX(attention) AS attention"
        item_fields = "MAX(attention)"
        if not attention_only:
            sample_fields += (
                ",MAX(observed) AS observed,MAX(excluded) AS excluded,"
                "MAX(nulls) AS nulls,MAX(zeros) AS zeros,MAX(origin) AS origin"
            )
            origins = ",".join(f"MAX(origin='{origin}')" for origin in ORIGIN_FLAGS)
            item_fields = (
                "SUM(observed),MAX(attention),MAX(excluded),MAX(nulls),MAX(zeros),"
                + origins
            )
        edge_attention = (
            """EXISTS (SELECT 1 FROM edge_candidates ec
                JOIN initial_edges e ON e.id=ec.edge WHERE ec.candidate=c.id
                AND edge_attention(e.disposition,e.initial_evidence))"""
            if self._edges
            else "0"
        )
        # Scope before aggregation: an outer predicate on an aggregate view does
        # not necessarily stop SQLite from evaluating every sample's rules.
        rows = self._db.execute(
            f"""WITH member_samples AS MATERIALIZED (
                SELECT final_group AS item,sample,MAX(peaks!='[]') AS observed,
                    MAX(risk_flags IS NOT NULL AND risk_flags!='[]') AS risky
                FROM members WHERE final_group IS NOT NULL
                    AND sample IN (SELECT value FROM json_each(:scope))
                GROUP BY final_group,sample),
            held AS MATERIALIZED (
                SELECT method_subject,sample FROM method_assessments
                WHERE sample IN (SELECT value FROM json_each(:scope))
                    AND assessment_attention(method_result)
                GROUP BY method_subject,sample),
            facts AS (
                SELECT ic.item,c.sample,
                    c.terminal_peak IS NOT NULL OR COALESCE(m.observed,0) AS observed,
                    h.sample IS NOT NULL OR {edge_attention} AS attention,
                    e.candidate IS NOT NULL AS excluded,
                    0 AS nulls,0 AS zeros,'' AS origin
                FROM review_item_candidates ic JOIN candidates c ON c.id=ic.candidate
                LEFT JOIN member_samples m ON m.item=ic.item AND m.sample=c.sample
                LEFT JOIN held h
                    ON h.method_subject=c.method_subject AND h.sample=c.sample
                LEFT JOIN exclusions e ON e.candidate=c.id
                WHERE c.sample IN (SELECT value FROM json_each(:scope))
                UNION ALL
                SELECT c.group_id,c.sample,c.peaks!='[]' OR COALESCE(m.observed,0),
                    cell_attention(c.variant,c.value,COALESCE(m.risky,0)
                        OR CASE WHEN c.risks IS NOT NULL THEN risks_flagged(c.risks)
                           ELSE 0 END),
                    0,c.value IS NULL,c.value=0,c.support_origin
                FROM cells c LEFT JOIN member_samples m
                    ON m.item=c.group_id AND m.sample=c.sample
                WHERE c.sample IN (SELECT value FROM json_each(:scope))),
            sample_facts AS (
                SELECT item,sample,{sample_fields} FROM facts GROUP BY item,sample)
            SELECT item,COUNT(*),{item_fields}
            FROM sample_facts GROUP BY item""",
            {"scope": self._scope(scope)},
        )
        if attention_only:
            # Attention has no coverage/origin ranking. Select by the same
            # recorded attention rules without calculating sampling strata.
            eligible = 0
            chosen = []
            for key, _, attention in rows:
                eligible += 1
                if self._items[key].attention or attention:
                    chosen.append(key)
            chosen.sort(key=lambda key: self._items[key].sort_key)
            return chosen[:limit], len(self._items), eligible, len(chosen), 0
        cases = []
        for (
            key,
            present,
            observed,
            attention,
            excluded,
            nulls,
            zeros,
            *origin_flags,
        ) in rows:
            item = self._items[key]
            facts = ScopedReviewFacts(
                present,
                observed,
                item.attention or bool(attention),
                bool(excluded),
                bool(nulls),
                bool(zeros),
                tuple(
                    name
                    for name, flag in zip(ORIGIN_FLAGS, origin_flags, strict=True)
                    if flag
                ),
            )
            case_id, attention, deferred = classify(item.kind, facts)
            cases.append((key, item.sort_key, case_id, attention, deferred))
        by_case: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
        for case in cases:
            by_case[case[2]].append(case)
        ordered: list[tuple[bool, int, int, int]] = []
        for case_id, members in by_case.items():
            members.sort(key=lambda case: (rank(case[1]), case[1]))
            ordered.extend(
                (bool(case[4]), slot, case_id, case[0])
                for slot, case in enumerate(members, start=1)
            )
        ordered.sort()
        return (
            [entry[3] for entry in ordered[:limit]],
            len(self._items),
            len(cases),
            len(cases),
            0,
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

    def related_item(self, item_id: int) -> tuple[int, tuple[str, ...]] | None:
        if not self._edges:
            return None
        rows = self._db.execute(
            """SELECT DISTINCT peer.item,e.left_subject_id,
                e.right_subject_id,e.initial_evidence
            FROM review_item_candidates own
            JOIN edge_candidates ec ON ec.candidate=own.candidate
            JOIN initial_edges e ON e.id=ec.edge
            JOIN edge_candidates other ON other.edge=ec.edge
            JOIN review_item_candidates peer ON peer.candidate=other.candidate
            WHERE own.item=? AND peer.item!=own.item AND NOT EXISTS (
                SELECT 1 FROM review_item_candidates mine
                WHERE mine.item=own.item AND mine.candidate=peer.candidate)""",
            (item_id,),
        )
        best: dict[int, tuple[int, str]] = {}
        refs: dict[int, set[str]] = defaultdict(set)
        for peer, left, right, evidence in rows:
            priority = (
                0
                if (decode_json(evidence) or {}).get("decision")
                in {"blocked_edge", "split_edge", "split"}
                else 1
            )
            ref = json.dumps((left, right), separators=(",", ":"))
            refs[peer].add(ref)
            current = best.get(peer)
            best[peer] = (
                (priority, ref)
                if current is None
                else (min(current[0], priority), min(current[1], ref))
            )
        if not best:
            return None
        peer = min(best, key=lambda key: (*best[key], self._items[key].sort_key))
        return peer, tuple(sorted(refs[peer]))
