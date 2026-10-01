"""Read an indexed query page without loading the review population."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace

from xic_extractor.diagnostics.alignment_explanation import ExplanationMetadata
from xic_extractor.diagnostics.explanation_query import query_page
from xic_inspector.selection.canonical import (
    CanonicalItem,
    Witness,
    build_canonical_ref,
)
from xic_inspector.selection.query import filter_by_query
from xic_inspector.sources.cid_nl import CidNlSource


class IndexedCidNlSource(CidNlSource):
    """Use stored selection coordinates and the existing bounded renderer."""

    def __init__(self, path, summary):
        super().__init__(path, metadata=ExplanationMetadata(summary, {}, {}, ()))
        self._compact_selection = True

    def select_query(self, scope, request):
        criteria = dict(
            candidate_ids=request.candidate_ids,
            group_ids=request.group_ids,
            mz_min=request.mz_min,
            mz_max=request.mz_max,
            rt_min=request.rt_min,
            rt_max=request.rt_max,
        )
        with closing(
            sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            ids, run_total, eligible, matched, missing = query_page(
                db,
                tuple(sorted(scope)),
                **criteria,
                offset=request.offset,
                limit=request.limit,
            )
            cache = {}

            def item(item_id):
                if item_id in cache:
                    return cache[item_id]
                kind, identifier, gid = db.execute(
                    "SELECT kind,identifier,group_id FROM query_items WHERE id=?",
                    (item_id,),
                ).fetchone()
                witnesses = tuple(
                    Witness(
                        sid,
                        cid,
                        gid,
                        identifier if kind == "row" else None,
                        mz,
                        rt,
                        oid,
                        stem,
                    )
                    for sid, cid, mz, rt, oid, stem in db.execute(
                        "SELECT s.sample_id,c.candidate_id,c.mz,w.rt,"
                        "w.observation_id,c.sample_stem "
                        "FROM query_witnesses w JOIN query_candidates c ON "
                        "c.id=w.candidate "
                        "JOIN query_samples s ON s.id=c.sample "
                        "WHERE w.item=? ORDER BY w.ordinal",
                        (item_id,),
                    )
                    if sid in scope
                )
                sample_ids = tuple(
                    sorted(
                        sid
                        for (sid,) in db.execute(
                            "SELECT s.sample_id FROM query_scope q "
                            "JOIN query_samples s ON s.id=q.sample WHERE q.item=?",
                            (item_id,),
                        )
                        if sid in scope
                    )
                )
                # Candidate identity is independent of the requested sample scope.
                sid = db.execute(
                    "SELECT s.sample_id FROM query_members m JOIN "
                    "query_candidates c ON c.id=m.candidate "
                    "JOIN query_samples s ON s.id=c.sample WHERE m.item=? LIMIT 1",
                    (item_id,),
                ).fetchone()
                _, ref = build_canonical_ref(
                    kind,
                    self.request_id,
                    self.attempt_id,
                    identifier,
                    sample_id=sid[0] if sid else None,
                )
                result = CanonicalItem(
                    kind,
                    self.request_id,
                    self.attempt_id,
                    identifier,
                    ref,
                    sample_ids,
                    witnesses,
                    "unclassified",
                    details={"review_summary": {}},
                )
                cache[item_id] = result
                return result

            selected = []
            for item_id in ids:
                primary = item(item_id)
                # SQL owns filtering/counting/pagination. Apply the same predicate
                # only to the page's witnesses for the displayed match markers.
                with_matches, _, _ = filter_by_query([primary], **criteria, limit=1)
                selected.append(
                    replace(
                        primary, matched_witnesses=with_matches[0].matched_witnesses
                    )
                )
                peer = db.execute(
                    "SELECT p.peer FROM query_peers p JOIN query_items i "
                    "ON i.id=p.peer "
                    "WHERE p.item=? GROUP BY p.peer "
                    "ORDER BY MIN(p.priority),MIN(p.edge_ref),i.sort_key LIMIT 1",
                    (item_id,),
                ).fetchone()
                if peer:
                    refs = tuple(
                        row[0]
                        for row in db.execute(
                            "SELECT edge_ref FROM query_peers "
                            "WHERE item=? AND peer=? ORDER BY edge_ref",
                            (item_id, peer[0]),
                        )
                    )
                    self.related_items[primary.serialized_ref] = [(item(peer[0]), refs)]
            return selected, run_total, eligible, matched, missing
