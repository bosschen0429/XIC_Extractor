"""Read an indexed query page without loading the review population."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace

from xic_extractor.diagnostics.alignment_explanation import ExplanationMetadata
from xic_extractor.diagnostics.explanation_query import query_page
from xic_extractor.diagnostics.explanation_review import ORIGIN_FLAGS, SELECTION_FLAGS
from xic_inspector.selection.canonical import (
    CanonicalItem,
    Witness,
    build_canonical_ref,
    serialize_canonical_ref,
)
from xic_inspector.selection.query import filter_by_query
from xic_inspector.selection.sampler import (
    review_case,
    review_case_order,
    sampling_rank,
)
from xic_inspector.selection.strata import classify_scoped_stratum
from xic_inspector.sources.cid_nl import CidNlSource


class IndexedCidNlSource(CidNlSource):
    """Use stored selection coordinates and the existing bounded renderer."""

    def __init__(self, path, summary):
        super().__init__(path, metadata=ExplanationMetadata(summary, {}, {}, ()))
        self._compact_selection = True

    def _review_page(self, db, scope, request):
        """Evaluate compact scope masks, then select IDs before reading details."""
        run_total = db.execute("SELECT COUNT(*) FROM query_items").fetchone()[0]
        if not scope:
            return [], run_total, 0, 0, 0
        scope_mask = sum(
            1 << i
            for i, sample in enumerate(self.summary["samples"])
            if sample["canonical_sample_identity_id"] in scope
        )

        def case_index(kind, packed):
            width, remainder = divmod(len(packed), len(SELECTION_FLAGS) + 1)
            if remainder or not width:
                raise ValueError("malformed review selection facts")
            masks = [
                int.from_bytes(packed[i : i + width], "little") & scope_mask
                for i in range(0, len(packed), width)
            ]
            present, observed, attention, excluded, nulls, zeros, *origins = masks
            if not present:
                return -1
            stratum = classify_scoped_stratum(
                is_attention=bool(attention),
                is_excluded=bool(excluded),
                has_null_cell=bool(nulls),
                has_zero_cell=bool(zeros),
            )
            case = review_case(
                kind,
                stratum,
                observed.bit_count(),
                present.bit_count(),
                [name for name, mask in zip(ORIGIN_FLAGS, origins) if mask],
            )
            coverage, origin, status = review_case_order(case)
            return coverage * 20 + origin * 5 + status

        db.create_function("review_case", 2, case_index, deterministic=True)
        db.execute(
            "CREATE TEMP TABLE selection_cases AS "
            "SELECT i.id AS item,i.sort_key,"
            "review_case(i.kind,r.sample_facts) AS case_id "
            "FROM query_items i JOIN query_review r ON r.item=i.id"
        )
        eligible = db.execute(
            "SELECT COUNT(*) FROM selection_cases WHERE case_id>=0"
        ).fetchone()[0]
        if request.strategy == "attention":
            matched = db.execute(
                "SELECT COUNT(*) FROM selection_cases WHERE case_id>=0 AND case_id%5=0"
            ).fetchone()[0]
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT item FROM selection_cases WHERE case_id>=0 AND case_id%5=0 "
                    "ORDER BY sort_key LIMIT ?",
                    (request.limit,),
                )
            ]
        else:

            def rank(sort_key):
                ref = json.loads(sort_key)
                full = serialize_canonical_ref(
                    [ref[0], self.request_id, self.attempt_id, *ref[1:]]
                )
                return sampling_rank(full, request.seed)

            db.create_function("sample_rank", 1, rank, deterministic=True)
            ids = [
                row[0]
                for row in db.execute(
                    "WITH ranked AS (SELECT item,case_id,ROW_NUMBER() OVER "
                    "(PARTITION BY case_id ORDER BY sample_rank(sort_key),sort_key) "
                    "AS slot "
                    "FROM selection_cases WHERE case_id>=0) "
                    "SELECT item FROM ranked "
                    "ORDER BY (case_id>=60),slot,case_id LIMIT ?",
                    (request.limit,),
                )
            ]
            matched = eligible
        return ids, run_total, eligible, matched, 0

    def select_items(self, scope, request):
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
            if request.strategy == "query":
                ids, run_total, eligible, matched, missing = query_page(
                    db,
                    tuple(sorted(scope)),
                    **criteria,
                    offset=request.offset,
                    limit=request.limit,
                )
            else:
                ids, run_total, eligible, matched, missing = self._review_page(
                    db, scope, request
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
                if request.strategy == "query":
                    with_matches, _, _ = filter_by_query([primary], **criteria, limit=1)
                    primary = replace(
                        primary, matched_witnesses=with_matches[0].matched_witnesses
                    )
                selected.append(primary)
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
            return (
                sorted(selected, key=lambda item: item.sort_key),
                run_total,
                eligible,
                matched,
                missing,
            )
