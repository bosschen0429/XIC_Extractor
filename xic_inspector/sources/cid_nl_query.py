"""Select indexed evidence with Inspector policy, without knowing SQLite tables."""

from __future__ import annotations

import json
from dataclasses import replace

from xic_extractor.alignment_explanation.query import (
    ScopedReviewFacts,
    open_review_query,
)
from xic_extractor.alignment_explanation.reader import ExplanationMetadata
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
from xic_inspector.sources.untargeted_overview import (
    ordered_review_items,
    review_overview,
)


def _review_order(kind: str, facts: ScopedReviewFacts) -> tuple[int, bool, bool]:
    stratum = classify_scoped_stratum(
        is_attention=facts.attention,
        is_excluded=facts.excluded,
        has_null_cell=facts.nulls,
        has_zero_cell=facts.zeros,
    )
    case = review_case(kind, stratum, facts.observed, facts.present, facts.origins)
    coverage, origin, status = review_case_order(case)
    return coverage * 20 + origin * 5 + status, status == 0, kind == "candidate"


class IndexedCidNlSource(CidNlSource):
    """Use stored selection coordinates and the existing bounded renderer."""

    def __init__(self, path, summary):
        super().__init__(path, metadata=ExplanationMetadata(summary, {}, {}, ()))
        self._compact_selection = True
        self.missing_mz_count = 0
        self.mz_distances = {}

    def select_items(self, scope, request):
        criteria = dict(
            candidate_ids=request.candidate_ids,
            group_ids=request.group_ids,
            mz_min=request.mz_min,
            mz_max=request.mz_max,
            rt_min=request.rt_min,
            rt_max=request.rt_max,
        )
        with open_review_query(self.path, self.summary) as query:
            if request.strategy == "query":
                ordered = None
                if request.review_scope != "all":
                    overview, rows = review_overview(self.path, self.summary)
                    headers = query.item_headers()
                    overview["candidate_count"] = sum(
                        kind == "candidate" for _, kind, _ in headers
                    )
                    self.summary["overview"] = overview
                    ordered = ordered_review_items(headers, rows, request.review_scope)
                if request.nearest_mz is not None:
                    (
                        ids,
                        run_total,
                        eligible,
                        matched,
                        missing,
                        self.missing_mz_count,
                        distances,
                    ) = query.nearest_page(
                        scope,
                        mz=request.nearest_mz,
                        offset=request.offset,
                        limit=request.limit,
                        ordered_item_ids=ordered,
                        **criteria,
                    )
                else:
                    ids, run_total, eligible, matched, missing = query.page(
                        scope,
                        **criteria,
                        offset=request.offset,
                        limit=request.limit,
                        ordered_item_ids=ordered,
                    )
            else:

                def rank(sort_key):
                    ref = json.loads(sort_key)
                    full = serialize_canonical_ref([ref[0], self.attempt_id, *ref[1:]])
                    return sampling_rank(full, request.seed)

                ids, run_total, eligible, matched, missing = query.review_page(
                    scope,
                    classify=_review_order,
                    rank=rank,
                    attention_only=request.strategy == "attention",
                    limit=request.limit,
                )
            cache: dict[int, CanonicalItem] = {}

            def item(item_id):
                if item_id in cache:
                    return cache[item_id]
                stored = query.item(item_id, scope)
                _, ref = build_canonical_ref(
                    stored.kind,
                    self.attempt_id,
                    stored.identifier,
                    sample_id=stored.candidate_sample_id,
                )
                result = CanonicalItem(
                    stored.kind,
                    self.attempt_id,
                    stored.identifier,
                    ref,
                    stored.sample_ids,
                    tuple(
                        Witness(
                            sid,
                            cid,
                            stored.group_id,
                            stored.identifier if stored.kind == "row" else None,
                            mz,
                            rt,
                            oid,
                            stem,
                        )
                        for sid, cid, mz, rt, oid, stem in stored.witnesses
                    ),
                    "unclassified",
                    details={"review_summary": {}},
                )
                cache[item_id] = result
                return result

            selected = []
            for item_id in ids:
                primary = item(item_id)
                if request.strategy == "query":
                    # Filtering is complete; only mark the page's witnesses.
                    with_matches, _, _ = filter_by_query([primary], **criteria, limit=1)
                    primary = replace(
                        primary, matched_witnesses=with_matches[0].matched_witnesses
                    )
                selected.append(primary)
                if request.nearest_mz is not None:
                    self.mz_distances[primary.serialized_ref] = distances[item_id]
                peer = query.related_item(item_id)
                if peer:
                    peer_id, refs = peer
                    self.related_items[primary.serialized_ref] = [(item(peer_id), refs)]
            return (
                selected
                if request.review_scope != "all"
                else sorted(selected, key=lambda item: item.sort_key),
                run_total,
                eligible,
                matched,
                missing,
            )
