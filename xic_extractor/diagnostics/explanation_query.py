"""Persisted coordinates for indexed selection of completed review evidence.

This projection contains identities and recorded coordinates, never new scientific
decisions. Publication builds it once; readers fetch only a requested page.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from xic_extractor.diagnostics.explanation_review import (
    SELECTION_FLAGS,
    selection_sample_facts,
)


def recorded_observations(candidate, linked_records, records, member_ids):
    """Resolve the recorded observations used by both selection and display."""
    observations = [
        observation
        for kind, rid in linked_records
        if kind == "terminal_subjects"
        for observation in records[kind][rid].get("observations", [])
        if not member_ids
        or observation.get("peak_observation_content_id") in member_ids
    ]
    return observations or [{"rt_apex_min": candidate.get("rt_apex_min")}]


def build_query_index(db: sqlite3.Connection, summary: dict[str, Any]) -> None:
    """Build the review query projection inside the publication transaction."""
    from xic_extractor.diagnostics.alignment_explanation import (
        _object,
        _read_review_cells,
    )

    db.executescript("""
        CREATE TABLE query_samples (id INTEGER PRIMARY KEY, sample_id TEXT UNIQUE);
        CREATE TABLE query_candidates (
            id INTEGER PRIMARY KEY, candidate_id TEXT, sample INTEGER,
            mz REAL, sample_stem TEXT, UNIQUE(candidate_id, sample));
        CREATE TABLE query_items (
            id INTEGER PRIMARY KEY, kind TEXT, identifier TEXT, group_id TEXT,
            sort_key BLOB UNIQUE);
        CREATE TABLE query_members (
            item INTEGER, candidate INTEGER, PRIMARY KEY(item,candidate)
        ) WITHOUT ROWID;
        CREATE INDEX query_members_candidate ON query_members(candidate,item);
        CREATE TABLE query_scope (
            sample INTEGER, item INTEGER, PRIMARY KEY(sample,item)
        ) WITHOUT ROWID;
        CREATE INDEX query_scope_item ON query_scope(item,sample);
        CREATE TABLE query_witnesses (
            item INTEGER, ordinal INTEGER, candidate INTEGER, rt REAL,
            observation_id TEXT, PRIMARY KEY(item,ordinal)
        ) WITHOUT ROWID;
        CREATE INDEX query_candidates_mz ON query_candidates(mz,id);
        CREATE INDEX query_witnesses_candidate ON query_witnesses(candidate,item);
        CREATE INDEX query_witnesses_rt ON query_witnesses(rt,item);
        CREATE INDEX query_items_group ON query_items(group_id,id);
        CREATE TABLE query_peers (
            item INTEGER, peer INTEGER, priority INTEGER, edge_ref TEXT,
            PRIMARY KEY(item,peer,edge_ref)
        ) WITHOUT ROWID;
        CREATE TABLE query_review (item INTEGER PRIMARY KEY, sample_facts BLOB);
    """)
    samples = {
        sample["canonical_sample_identity_id"]: i
        for i, sample in enumerate(summary["samples"])
    }
    db.executemany(
        "INSERT INTO query_samples VALUES (?,?)", ((i, s) for s, i in samples.items())
    )
    candidates = {
        (cid, sid): _object(payload)
        for cid, sid, payload in db.execute("SELECT * FROM candidates")
    }
    candidate_ids = {key: i for i, key in enumerate(sorted(candidates))}
    db.executemany(
        "INSERT INTO query_candidates VALUES (?,?,?,?,?)",
        (
            (
                candidate_ids[key],
                key[0],
                samples[key[1]],
                value.get("precursor_mz"),
                value.get("sample_stem"),
            )
            for key, value in candidates.items()
        ),
    )
    records: dict[str, dict[str, Any]] = {}
    kinds = (
        "('rows','final_groups','terminal_subjects','initial_edges',"
        "'method_assessments','exclusions')"
    )
    for kind, rid, payload in db.execute(
        "SELECT * FROM records WHERE kind IN " + kinds
    ):
        records.setdefault(kind, {})[rid] = _object(payload)
    links = defaultdict(set)
    record_keys = defaultdict(set)
    for cid, sid, kind, rid in db.execute("SELECT * FROM links WHERE kind IN " + kinds):
        links[cid, sid].add((kind, rid))
        record_keys[kind, rid].add((cid, sid))
    definitions = []
    grouped = set()
    row_groups = set()
    for rid, row in records.get("rows", {}).items():
        gid = row.get("group_hypothesis_id")
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
            "INSERT INTO query_items VALUES (?,?,?,?,?)",
            (item_id, kind, identifier, gid, sort_key),
        )
        item_keys[item_id] = keys
        db.executemany(
            "INSERT INTO query_members VALUES (?,?)",
            ((item_id, candidate_ids[key]) for key in keys),
        )
        members = defaultdict(set)
        for member in (
            records.get("final_groups", {}).get(gid, {}).get("member_subjects", [])
        ):
            members[member.get("canonical_sample_identity_id")].update(
                member.get("physical_observation_ids", [])
            )
        ordinal = 0
        observed = set()
        for key in sorted(keys):
            items_by_key[key].add(item_id)
            for observation in recorded_observations(
                candidates[key], links[key], records, members[key[1]]
            ):
                if observation.get("peak_observation_content_id"):
                    observed.add(key[1])
                db.execute(
                    "INSERT INTO query_witnesses VALUES (?,?,?,?,?)",
                    (
                        item_id,
                        ordinal,
                        candidate_ids[key],
                        observation.get("rt_apex_min"),
                        observation.get("peak_observation_content_id"),
                    ),
                )
                ordinal += 1
        cells = (
            _read_review_cells(db, summary, (identifier,)).values()
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
            "INSERT INTO query_review VALUES (?,?)",
            (
                item_id,
                b"".join(mask.to_bytes(mask_width, "little") for mask in masks),
            ),
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
        ("s.sample_id", scope),
        ("c.candidate_id", candidate_ids),
        ("i.group_id", group_ids),
    ):
        if values or column == "s.sample_id":
            predicates.append(f"{column} IN ({','.join('?' for _ in values)})")
            params.extend(values)
    for column, operator, value in (("c.mz", ">=", mz_min), ("c.mz", "<=", mz_max)):
        if value is not None:
            predicates.append(f"{column}{operator}?")
            params.append(value)
    base = (
        " FROM query_witnesses w JOIN query_candidates c ON c.id=w.candidate "
        "JOIN query_samples s ON s.id=c.sample JOIN query_items i ON i.id=w.item WHERE "
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
        "query_samples s ON s.id=q.sample "
        f"WHERE s.sample_id IN ({','.join('?' for _ in scope)})",
        tuple(scope),
    ).fetchone()[0]
    return page, run_total, eligible, matched, missing
