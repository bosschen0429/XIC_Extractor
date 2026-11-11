"""Select indexed evidence with Inspector policy, without knowing SQLite tables."""

from __future__ import annotations

import json
from dataclasses import replace

from xic_extractor.alignment_explanation.query import (
    ScopedReviewFacts,
    open_review_query,
)
from xic_extractor.alignment_explanation.reader import read_review_evidence
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
from xic_inspector.sources.cid_nl import CidNlSource, build_item_evidence
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
    """Select in SQL, then read and render only the selected items."""

    def __init__(self, path, summary):
        self.path = path
        self._bind(summary)
        self.missing_mz_count = 0
        self.mz_distances = {}

    def load_render_items(self, items, *, capability, check_cancel):
        items = list(items)
        check_cancel()
        evidence = read_review_evidence(
            self.path,
            candidate_keys=sorted(
                {
                    (w.candidate_id, w.sample_key)
                    for item in items
                    for w in item.witnesses
                }
            ),
            row_ids=sorted({item.identifier for item in items if item.kind == "row"}),
            summary=self.summary,
        )
        scope = {sid for item in items for sid in item.sample_ids}
        views = []
        for item in items:
            if item.kind == "candidate":
                key = (item.identifier, json.loads(item.serialized_ref)[2])
                view = build_item_evidence(
                    evidence,
                    self.attempt_id,
                    item.kind,
                    item.identifier,
                    None,
                    (key,),
                    scope,
                    evidence.candidates[key].fields,
                )
            else:
                group = evidence.groups[item.identifier]
                view = build_item_evidence(
                    evidence,
                    self.attempt_id,
                    item.kind,
                    item.identifier,
                    item.identifier,
                    group.candidates,
                    scope,
                    group.row if group.row is not None else group.record,
                )
            views.append(
                replace(
                    view,
                    item=replace(view.item, matched_witnesses=item.matched_witnesses),
                )
            )
        return self._render(views, capability=capability, check_cancel=check_cancel)

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

            def item(item_id):
                stored = query.item(item_id, scope)
                _, ref = build_canonical_ref(
                    stored.kind,
                    self.attempt_id,
                    stored.identifier,
                    sample_id=stored.candidate_sample_id,
                )
                return CanonicalItem(
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
                )

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
            return (
                selected
                if request.review_scope != "all"
                else sorted(selected, key=lambda item: item.sort_key),
                run_total,
                eligible,
                matched,
                missing,
            )
