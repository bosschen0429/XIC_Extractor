"""Persisted coordinates for indexed selection of completed review evidence.

This projection contains identities and recorded coordinates, never new scientific
decisions. Publication builds it once; readers fetch only a requested page.
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

from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.alignment_explanation.reader import read_review_cells
from xic_extractor.alignment_explanation.review import (
    ORIGIN_FLAGS,
    SELECTION_FLAGS,
    selection_sample_facts,
)
from xic_extractor.alignment_explanation.storage import (
    CANDIDATE_SELECT,
    decode_candidate,
    decode_object,
    open_readonly,
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


def build_query_index(db: sqlite3.Connection, summary: dict[str, Any]) -> None:
    """Build the review query projection inside the publication transaction."""
    samples = {sample["sample_key"]: i for i, sample in enumerate(summary["samples"])}
    stored_candidates = [decode_candidate(row) for row in db.execute(CANDIDATE_SELECT)]
    candidates = {(c["candidate_id"], c["sample_key"]): c for _, c in stored_candidates}
    candidate_ids = {
        (c["candidate_id"], c["sample_key"]): cid for cid, c in stored_candidates
    }
    records: dict[str, dict[str, Any]] = {}
    kinds = (
        "('rows','final_groups','terminal_subjects','initial_edges',"
        "'method_assessments','exclusions')"
    )
    for kind, rid, payload in db.execute(
        "SELECT kind, record_id, payload FROM records WHERE kind IN " + kinds
    ):
        records.setdefault(kind, {})[rid] = decode_object(payload)
    links = defaultdict(set)
    record_keys = defaultdict(set)
    for cid, sid, kind, rid in db.execute("SELECT * FROM links WHERE kind IN " + kinds):
        links[cid, sid].add((kind, rid))
        record_keys[kind, rid].add((cid, sid))
    definitions = []
    grouped = set()
    row_groups = set()
    for rid, row in records.get("rows", {}).items():
        gid = row.get("feature_id")
        row_groups.add(gid)
        keys = record_keys["rows", rid]
        grouped.update(keys)
        definitions.append(("row", rid, gid, keys))
    for gid in records.get("final_groups", {}):
        keys = record_keys["final_groups", gid]
        grouped.update(keys)
        if gid not in row_groups:
            definitions.append(("group", gid, gid, keys))
    definitions.extend(
        ("candidate", key[0], None, {key}) for key in candidates if key not in grouped
    )
    mask_width = max(1, (len(samples) + 7) // 8)
    items_by_key = defaultdict(set)
    item_keys = {}
    for item_id, (kind, identifier, gid, keys) in enumerate(definitions):
        # The request and attempt are constant within this report. Omitting them
        # preserves the canonical JSON UTF-8 order, including escaped IDs.
        ref = (
            [kind, next(iter(keys))[1], identifier]
            if kind == "candidate"
            else [kind, identifier]
        )
        sort_key = json.dumps(ref, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        db.execute(
            "INSERT INTO query_items VALUES (?,?,?,?,?,?)",
            (item_id, kind, identifier, gid, sort_key, b""),
        )
        item_keys[item_id] = keys
        members = defaultdict(set)
        for member in (
            records.get("final_groups", {}).get(gid, {}).get("member_subjects", [])
        ):
            members[member.get("sample_key")].update(
                member.get("physical_peak_keys", [])
            )
        ordinal = 0
        observed = set()
        for key in sorted(keys):
            items_by_key[key].add(item_id)
            for observation in recorded_observations(
                candidates[key], links[key], records, members[key[1]]
            ):
                if observation.get("physical_peak_key"):
                    observed.add(key[1])
                db.execute(
                    "INSERT INTO query_witnesses VALUES (?,?,?,?,?)",
                    (
                        item_id,
                        ordinal,
                        candidate_ids[key],
                        observation.get("rt_apex_min"),
                        observation.get("physical_peak_key"),
                    ),
                )
                ordinal += 1
        cells = (
            read_review_cells(db, summary, (identifier,)).values()
            if kind == "row"
            else ()
        )
        flags = selection_sample_facts(
            kind,
            identifier,
            gid,
            keys,
            candidates,
            records,
            links,
            cells,
            observed,
        )
        db.executemany(
            "INSERT INTO query_scope VALUES (?,?)",
            ((samples[sid], item_id) for sid in flags),
        )
        # One bit per sample for presence and each scalar fact; no cell payload
        # needs decoding again when a caller changes its selected sample scope.
        masks = [sum(1 << samples[sid] for sid in flags)]
        masks.extend(
            sum(1 << samples[sid] for sid, value in flags.items() if value & bit)
            for bit in SELECTION_FLAGS
        )
        db.execute(
            "UPDATE query_items SET sample_facts=? WHERE id=?",
            (b"".join(mask.to_bytes(mask_width, "little") for mask in masks), item_id),
        )
    for edge_ref, edge in records.get("initial_edges", {}).items():
        keys = record_keys["initial_edges", edge_ref]
        participants = {item for key in keys for item in items_by_key[key]}
        priority = (
            0
            if edge.get("initial_evidence", {}).get("decision")
            in {"blocked_edge", "split_edge", "split"}
            else 1
        )
        for item in participants:
            peers = {
                peer
                for key in keys - item_keys[item]
                for peer in items_by_key[key]
                if peer != item
            }
            db.executemany(
                "INSERT INTO query_peers VALUES (?,?,?,?)",
                ((item, peer, priority, edge_ref) for peer in peers),
            )
    db.execute("ANALYZE")


def query_page(
    db,
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
    """Return population counts and item IDs, filtering each witness as one row."""
    params: list[Any] = []
    predicates = []
    order = "i.sort_key"
    if ordered_item_ids is not None:
        db.execute(
            "CREATE TEMP TABLE review_order "
            "(item INTEGER PRIMARY KEY, priority INTEGER)"
        )
        db.executemany(
            "INSERT INTO review_order VALUES (?,?)",
            ((item, priority) for priority, item in enumerate(ordered_item_ids)),
        )
        predicates.append("i.id IN (SELECT item FROM review_order)")
        order = "(SELECT priority FROM review_order WHERE item=i.id),i.sort_key"
    for column, values in (
        ("s.sample_key", scope),
        ("c.candidate_id", candidate_ids),
        ("i.group_id", group_ids),
    ):
        if values or column == "s.sample_key":
            predicates.append(f"{column} IN ({','.join('?' for _ in values)})")
            params.extend(values)
    for column, operator, value in (
        ("c.precursor_mz", ">=", mz_min),
        ("c.precursor_mz", "<=", mz_max),
    ):
        if value is not None:
            predicates.append(f"{column}{operator}?")
            params.append(value)
    base = (
        " FROM query_witnesses w JOIN candidates c ON c.id=w.candidate "
        "JOIN samples s ON s.id=c.sample JOIN query_items i ON i.id=w.item WHERE "
        + " AND ".join(predicates)
    )
    missing = 0
    if rt_min is not None or rt_max is not None:
        missing = db.execute(
            "SELECT COUNT(*) FROM (SELECT w.item"
            + base
            + " GROUP BY w.item HAVING COUNT(w.rt)=0)",
            params,
        ).fetchone()[0]
    for operator, value in ((">=", rt_min), ("<=", rt_max)):
        if value is not None:
            base += f" AND w.rt{operator}?"
            params.append(value)
    matches = "SELECT DISTINCT w.item" + base
    matched = db.execute("SELECT COUNT(*) FROM (" + matches + ")", params).fetchone()[0]
    page = [
        row[0]
        for row in db.execute(
            matches + f" ORDER BY {order} LIMIT ? OFFSET ?", [*params, limit, offset]
        )
    ]
    run_total = db.execute("SELECT COUNT(*) FROM query_items").fetchone()[0]
    eligible = db.execute(
        "SELECT COUNT(DISTINCT q.item) FROM query_scope q JOIN "
        "samples s ON s.id=q.sample "
        f"WHERE s.sample_key IN ({','.join('?' for _ in scope)})",
        tuple(scope),
    ).fetchone()[0]
    return page, run_total, eligible, matched, missing


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


@contextmanager
def open_review_query(path: Path, summary: Mapping[str, Any]) -> Iterator[ReviewQuery]:
    """One request-local index session; the caller supplies an admitted summary."""
    with open_readonly(path) as db:
        yield ReviewQuery(db, summary)


class ReviewQuery:
    """Own SQL and packed coordinates; presentation owns review ordering policy."""

    def __init__(self, db: sqlite3.Connection, summary: Mapping[str, Any]):
        self._db = db
        self._summary = summary

    def item_headers(self) -> list[tuple[int, str, str]]:
        return list(self._db.execute("SELECT id,kind,identifier FROM query_items"))

    def page(self, scope, **criteria):
        return query_page(self._db, tuple(sorted(scope)), **criteria)

    def nearest_page(
        self, scope, *, mz, offset, limit, ordered_item_ids=None, **criteria
    ):
        """Rank the same first scoped witness the display uses, not any nearest one."""
        db = self._db
        scope = tuple(sorted(scope))
        ids, run_total, eligible, _, missing_rt = self.page(
            scope, **criteria, ordered_item_ids=ordered_item_ids, limit=-1
        )
        db.execute("CREATE TEMP TABLE nearest_eligible (item INTEGER PRIMARY KEY)")
        db.executemany(
            "INSERT INTO nearest_eligible VALUES (?)", ((item,) for item in ids)
        )
        ranked = []
        for item_id, sort_key, witness_mz in db.execute(
            "SELECT item,sort_key,mz FROM ("
            "SELECT w.item,i.sort_key,c.precursor_mz AS mz,ROW_NUMBER() OVER "
            "(PARTITION BY w.item ORDER BY w.ordinal) AS position "
            "FROM query_witnesses w JOIN nearest_eligible e ON e.item=w.item "
            "JOIN query_items i ON i.id=w.item "
            "JOIN candidates c ON c.id=w.candidate "
            "JOIN samples s ON s.id=c.sample "
            f"WHERE s.sample_key IN ({','.join('?' for _ in scope)})) WHERE position=1",
            scope,
        ):
            if (
                isinstance(witness_mz, (int, float))
                and math.isfinite(witness_mz)
                and witness_mz > 0
            ):
                ranked.append((abs(witness_mz - mz), sort_key, item_id))
        population_count = len(ids)
        if criteria.get("rt_min") is None and criteria.get("rt_max") is None:
            # Scalar-only cells remain in the missing-coordinate disclosure.
            order_filter = (
                "JOIN review_order r ON r.item=q.item "
                if ordered_item_ids is not None
                else ""
            )
            population_count = db.execute(
                "SELECT COUNT(DISTINCT q.item) FROM query_scope q "
                "JOIN samples s ON s.id=q.sample "
                + order_filter
                + f"WHERE s.sample_key IN ({','.join('?' for _ in scope)})",
                scope,
            ).fetchone()[0]
        missing_mz = population_count - len(ranked)
        ranked.sort()
        distances = {
            item_id: distance
            for distance, _, item_id in ranked[offset : offset + limit]
        }
        return (
            list(distances),
            run_total,
            eligible,
            len(ranked),
            missing_rt,
            missing_mz,
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
        """Apply caller (bucket, attention, deferred) policy before hydration."""
        db = self._db
        run_total = db.execute("SELECT COUNT(*) FROM query_items").fetchone()[0]
        if not scope:
            return [], run_total, 0, 0, 0
        scope_mask = sum(
            1 << i
            for i, sample in enumerate(self._summary["samples"])
            if sample["sample_key"] in scope
        )

        def classify_packed(kind, packed):
            width, remainder = divmod(len(packed), len(SELECTION_FLAGS) + 1)
            if remainder or not width:
                raise ExplanationError("malformed review selection facts")
            masks = [
                int.from_bytes(packed[i : i + width], "little") & scope_mask
                for i in range(0, len(packed), width)
            ]
            present, observed, attention, excluded, nulls, zeros, *origins = masks
            if not present:
                return None
            return classify(
                kind,
                ScopedReviewFacts(
                    present.bit_count(),
                    observed.bit_count(),
                    bool(attention),
                    bool(excluded),
                    bool(nulls),
                    bool(zeros),
                    tuple(
                        name
                        for name, mask in zip(ORIGIN_FLAGS, origins, strict=True)
                        if mask
                    ),
                ),
            )

        db.execute(
            "CREATE TEMP TABLE selection_cases "
            "(item INTEGER PRIMARY KEY,sort_key BLOB,case_id INTEGER,"
            "attention INTEGER,deferred INTEGER)"
        )
        db.executemany(
            "INSERT INTO selection_cases VALUES (?,?,?,?,?)",
            (
                (item, sort_key, *classification)
                for item, sort_key, kind, packed in db.execute(
                    "SELECT i.id,i.sort_key,i.kind,i.sample_facts FROM query_items i"
                )
                if (classification := classify_packed(kind, packed)) is not None
            ),
        )
        eligible = db.execute("SELECT COUNT(*) FROM selection_cases").fetchone()[0]
        if attention_only:
            matched = db.execute(
                "SELECT COUNT(*) FROM selection_cases WHERE attention=1"
            ).fetchone()[0]
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT item FROM selection_cases WHERE attention=1 "
                    "ORDER BY sort_key LIMIT ?",
                    (limit,),
                )
            ]
        else:
            db.create_function("sample_rank", 1, rank, deterministic=True)
            ids = [
                row[0]
                for row in db.execute(
                    "WITH ranked AS (SELECT item,case_id,deferred,ROW_NUMBER() OVER "
                    "(PARTITION BY case_id ORDER BY sample_rank(sort_key),sort_key) "
                    "AS slot FROM selection_cases) "
                    "SELECT item FROM ranked "
                    "ORDER BY deferred,slot,case_id LIMIT ?",
                    (limit,),
                )
            ]
            matched = eligible
        return ids, run_total, eligible, matched, 0

    def item(self, item_id: int, scope: Sequence[str]) -> IndexedItem:
        db = self._db
        kind, identifier, gid = db.execute(
            "SELECT kind,identifier,group_id FROM query_items WHERE id=?", (item_id,)
        ).fetchone()
        witnesses = tuple(
            row
            for row in db.execute(
                "SELECT s.sample_key,c.candidate_id,c.precursor_mz,w.rt,"
                "w.observation_id,s.sample_stem "
                "FROM query_witnesses w JOIN candidates c ON c.id=w.candidate "
                "JOIN samples s ON s.id=c.sample "
                "WHERE w.item=? ORDER BY w.ordinal",
                (item_id,),
            )
            if row[0] in scope
        )
        samples = tuple(
            sorted(
                sid
                for (sid,) in db.execute(
                    "SELECT s.sample_key FROM query_scope q "
                    "JOIN samples s ON s.id=q.sample WHERE q.item=?",
                    (item_id,),
                )
                if sid in scope
            )
        )
        # Candidate identity is independent of the requested sample scope.
        sid = db.execute(
            "SELECT s.sample_key FROM query_witnesses w JOIN "
            "candidates c ON c.id=w.candidate "
            "JOIN samples s ON s.id=c.sample WHERE w.item=? ORDER BY w.ordinal LIMIT 1",
            (item_id,),
        ).fetchone()
        return IndexedItem(
            kind, identifier, gid, samples, sid[0] if sid else None, witnesses
        )

    def related_item(self, item_id: int) -> tuple[int, tuple[str, ...]] | None:
        db = self._db
        peer = db.execute(
            "SELECT p.peer FROM query_peers p JOIN query_items i ON i.id=p.peer "
            "WHERE p.item=? GROUP BY p.peer "
            "ORDER BY MIN(p.priority),MIN(p.edge_ref),i.sort_key LIMIT 1",
            (item_id,),
        ).fetchone()
        if peer is None:
            return None
        refs = tuple(
            row[0]
            for row in db.execute(
                "SELECT edge_ref FROM query_peers "
                "WHERE item=? AND peer=? ORDER BY edge_ref",
                (item_id, peer[0]),
            )
        )
        return peer[0], refs
