"""Load and scope retained CID-NL evidence for the shared Inspector.

The scientific reader owns payload validation and reads each concept once. This
adapter scopes an item to its candidates, builds the item's evidence cards from
the concepts those candidates relate to;
cid_nl_presentation assembles labels and readable cards.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.query import (
    ScopedReviewFacts,
    open_review_query,
)
from xic_extractor.alignment_explanation.reader import (
    CandidateKey,
    GroupEvidence,
    ReviewEvidence,
    read_alignment_explanation,
    read_review_evidence,
)
from xic_extractor.alignment_explanation.review import (
    assessment_attention,
    cell_attention,
    group_attention,
)
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
from xic_inspector.sources import cid_nl_presentation as presentation
from xic_inspector.sources.untargeted_overview import (
    ordered_review_items,
    review_overview,
)

CardRef = str | tuple[str, str]


def read_summary(path: str | Path) -> dict[str, Any]:
    """Read compact metadata without retrieving curve payloads."""
    return {
        **read_alignment_explanation(path),
        "plot_evidence_capability": "metadata_only",
    }


@dataclass(frozen=True, slots=True)
class Card:
    """One evidence card as presented: a recorded fact, and which of the item's
    samples and candidates it concerns."""

    kind: str
    ref: CardRef
    facts: dict[str, Any]
    keys: frozenset[CandidateKey]
    samples: frozenset[str]


@dataclass(frozen=True, slots=True)
class ItemEvidence:
    """One scoped review item: its state, computed from the evidence, and the
    cards it is presented with."""

    item: CanonicalItem
    cards: tuple[Card, ...]
    route: dict[str, Any]
    reason: Any
    state: Any
    losses: tuple[float, ...]
    cells: tuple[dict[str, Any], ...]
    support_origins: dict[str, dict[str, Any]]
    review_summary: dict[str, Any]
    members: tuple[dict[str, Any], ...]
    measured_mz: dict[CandidateKey, float | None]


def _candidate_ref(key: CandidateKey) -> str:
    return json.dumps(key, separators=(",", ":"))


Linked = dict[str, tuple[dict[str, Any], set[CandidateKey]]]


def build_item_evidence(
    evidence: ReviewEvidence,
    attempt_id: str,
    kind: str,
    identifier: str,
    gid: str | None,
    keys: Iterable[CandidateKey],
    scope: set[str],
) -> ItemEvidence:
    """Scope one item to its candidates, compute its state from their facts and
    the group's cells, then build its cards for presentation."""
    keys = set(keys)
    scoped = sorted(key for key in keys if key[1] in scope)
    group = evidence.groups.get(gid) if gid is not None else None
    subjects: Linked = {}
    assessments: Linked = {}
    initial: Linked = {}
    groups: dict[str, set[CandidateKey]] = {}

    def link(found: Linked, ref: str, facts: dict[str, Any], key) -> None:
        found.setdefault(ref, (facts, set()))[1].add(key)

    for key in scoped:
        candidate = evidence.candidates[key]
        link(subjects, candidate.method_subject_id, candidate.method_subject, key)
        for ref, assessment in candidate.assessments:
            link(assessments, ref, assessment, key)
        for ref in candidate.groups:
            if evidence.groups[ref].feature_id in (None, gid):
                groups.setdefault(ref, set()).add(key)
        for ref in candidate.initial_groups:
            link(initial, ref, evidence.initial_groups[ref], key)
    excluded_keys = [key for key in scoped if evidence.candidates[key].exclusion]
    cells = [
        {**cell, "quantitative_value": cell["value"]}
        for sid, cell in sorted(group.cells.items() if kind == "row" and group else ())
        if sid in scope
    ]
    cells_by_sample = {cell["sample_key"]: cell for cell in cells}
    witnesses = _witnesses(evidence, kind, identifier, gid, group, scoped)

    triggers: list[dict[str, Any]] = []
    if group is not None and group_attention(group.disposition):
        triggers.append({"scope": "group", "ref": gid, "reason": group.disposition})
    for sid, cell in cells_by_sample.items():
        reason = cell_attention(
            cell["variant"],
            cell["value"],
            any(risk.get("risk_flags") for risk in cell["membership_risks"]),
        )
        if reason:
            triggers.append(
                _trigger((identifier, sid), reason, {sid}, cell["reason_codes"])
            )
    for ref, (assessment, linked) in sorted(assessments.items()):
        reason = assessment_attention(assessment["method_result"])
        if reason:
            triggers.append(
                _trigger(
                    ref,
                    reason,
                    {sid for _, sid in linked},
                    assessment["reason_codes"],
                )
            )
    excluded = {sid for _, sid in excluded_keys}
    stratum = classify_scoped_stratum(
        is_attention=bool(triggers),
        is_excluded=bool(excluded),
        has_null_cell=any(c["quantitative_value"] is None for c in cells),
        has_zero_cell=any(c["quantitative_value"] == 0 for c in cells),
    )
    sample_ids = sorted({sid for _, sid in scoped} | set(cells_by_sample))
    _, ref = build_canonical_ref(
        kind,  # type: ignore[arg-type]
        attempt_id,
        identifier,
        sample_id=next(iter(keys))[1] if kind == "candidate" else None,
    )
    members = tuple(group.members) if group is not None else ()
    support_observations = {
        sid: _support_observation_refs(
            sid, members, witnesses, cells_by_sample.get(sid)
        )
        for sid in sample_ids
    }
    origins = {
        sid: _support_origin(
            sid,
            support_observations[sid],
            cells_by_sample.get(sid),
            assessments,
        )
        for sid in sample_ids
    }
    initial_member_ids = {
        member["subject_id"]
        for record, _ in initial.values()
        for member in record.get("member_subjects", [])
    }
    review_summary = {
        "observed_sample_count": len(
            {sid for sid, refs in support_observations.items() if refs}
        ),
        "scope_sample_count": len(sample_ids),
        "origin_kinds": sorted({origin["kind"] for origin in origins.values()}),
        "initial_member_count": (
            len(initial_member_ids) if initial_member_ids else len(keys)
        ),
        "final_member_count": len(members) if group is not None else len(keys),
    }
    item = CanonicalItem(
        kind=kind,  # type: ignore[arg-type]
        attempt_id=attempt_id,
        identifier=identifier,
        serialized_ref=ref,
        sample_ids=tuple(sample_ids),
        witnesses=tuple(witnesses),
        stratum=stratum,
        is_attention=bool(triggers),
        details={"attention_triggers": triggers, "review_summary": review_summary},
    )
    row = group if kind == "row" else None
    route = {"destination": row.destination, "reason": row.disposition} if row else {}
    cards = tuple(
        Card(
            "exclusions",
            _candidate_ref(key),
            facts,
            frozenset((key,)),
            frozenset((key[1],)),
        )
        for key in excluded_keys
        if (facts := evidence.candidates[key].exclusion) is not None
    )
    return ItemEvidence(
        item=item,
        cards=cards,
        route=route,
        reason=group.disposition if group is not None else "unavailable",
        state=evidence.groups[min(groups)].disposition
        if groups
        else route.get("reason"),
        losses=tuple(
            sorted(
                {
                    facts["neutral_loss_da"]
                    for facts, _ in subjects.values()
                    if isinstance(facts["neutral_loss_da"], (int, float))
                }
            )
        ),
        cells=tuple(cells),
        support_origins=origins,
        review_summary=review_summary,
        members=members if kind == "row" else (),
        measured_mz={
            key: evidence.candidates[key].fields["ms1_apex_mz"] for key in scoped
        },
    )


def _samples(keys: Iterable[CandidateKey]) -> frozenset[str]:
    return frozenset(sid for _, sid in keys)


def _trigger(ref, reason, samples, reason_codes) -> dict[str, Any]:
    return {
        "scope": "sample",
        "ref": ref,
        "reason": reason,
        "sample_ids": sorted(samples),
        "reason_codes": [code for code in reason_codes if isinstance(code, str)],
    }


def _witnesses(
    evidence: ReviewEvidence,
    kind: str,
    identifier: str,
    gid: str | None,
    group: GroupEvidence | None,
    scoped: list[CandidateKey],
) -> list[Witness]:
    """Each scoped candidate's terminal observation that the group kept."""
    members_by_sample: dict[str, set[str]] = {}
    for member in group.members if group is not None else ():
        members_by_sample.setdefault(member["sample_key"], set()).update(
            member["physical_peak_keys"]
        )
    witnesses: list[Witness] = []
    for cid, sid in scoped:
        candidate = evidence.candidates[cid, sid]
        member_peaks = members_by_sample.get(sid)
        observations = [
            observation
            for observation in (candidate.terminal or {}).get("observations", [])
            if not member_peaks or observation["physical_peak_key"] in member_peaks
        ] or [{}]
        witnesses.extend(
            Witness(
                sample_key=sid,
                candidate_id=cid,
                group_hypothesis_id=gid,
                row_key_id=identifier if kind == "row" else None,
                precursor_mz=candidate.fields["precursor_mz"],
                rt_apex_min=observation.get("rt_apex_min"),
                observation_id=observation.get("physical_peak_key"),
                sample_stem=candidate.fields["sample_stem"],
            )
            for observation in observations
        )
    return witnesses


def _support_observation_refs(sample_id, members, witnesses, cell):
    refs = {
        witness.observation_id
        for witness in witnesses
        if witness.sample_key == sample_id and witness.observation_id
    }
    refs.update(
        peak
        for member in members
        if member["sample_key"] == sample_id
        for peak in member["physical_peak_keys"]
    )
    refs.update(cell["observation_ids"] if cell is not None else ())
    return sorted(refs)


def _support_origin(sample_id, observation_refs, cell, assessments):
    """Read the cell owner; without a cell, show evidence but no origin."""
    if cell is not None:
        kind = cell["support_origin"]
        return {
            "kind": kind if kind in ("backfilled", "detected") else "unavailable",
            "label": presentation.support_origin_label(
                kind if kind in ("backfilled", "detected") else "unavailable"
            ),
            "method_refs": [],
            "observation_refs": observation_refs,
        }
    observation_set = set(observation_refs)
    methods = [
        (ref, facts)
        for ref, (facts, _) in assessments.items()
        if facts["sample_key"] == sample_id
        and facts["method_result"] == "SUPPORT"
        and observation_set.intersection(facts["candidate_physical_peak_keys"])
    ]
    kind = "unavailable"
    return {
        "kind": kind,
        "label": presentation.support_origin_label(kind),
        "method_refs": sorted(ref for ref, _ in methods),
        "observation_refs": observation_refs,
    }


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


class CidNlSource:
    """Select in SQL, then read and render only the selected items."""

    def __init__(self, path: Path) -> None:
        self.path = path
        summary = read_summary(path)
        self.summary = summary
        self.attempt_id = summary.get("processing_attempt_id", "")
        self.sample_labels = {
            sample["sample_key"]: sample["sample_stem"]
            for sample in summary.get("samples", [])
        }
        self.missing_mz_count = 0
        self.mz_distances: dict[str, float] = {}

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
                )
            views.append(
                replace(
                    view,
                    item=replace(view.item, matched_witnesses=item.matched_witnesses),
                )
            )
        return self._render(views, check_cancel=check_cancel)

    def select_items(self, scope, request):
        criteria = dict(
            candidate_ids=request.candidate_ids,
            group_ids=request.group_ids,
            mz_min=request.mz_min,
            mz_max=request.mz_max,
            rt_min=request.rt_min,
            rt_max=request.rt_max,
        )
        review_scope = request.review_scope
        with open_review_query(self.path, self.summary) as query:
            if request.strategy == "query":
                ordered = None
                if review_scope != "all":
                    overview, rows = review_overview(
                        self.path, self.summary, request.curation_removed_ids
                    )
                    headers = query.item_headers()
                    overview["candidate_count"] = sum(
                        kind == "candidate" for _, kind, _ in headers
                    )
                    self.summary["overview"] = overview
                    ordered = ordered_review_items(headers, rows, review_scope)
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
                    with_matches, _, _ = filter_by_query([primary], **criteria, limit=1)
                    primary = replace(
                        primary, matched_witnesses=with_matches[0].matched_witnesses
                    )
                selected.append(primary)
                if request.nearest_mz is not None:
                    self.mz_distances[primary.serialized_ref] = distances[item_id]
            return (
                selected
                if review_scope != "all"
                else sorted(selected, key=lambda item: item.sort_key),
                run_total,
                eligible,
                matched,
                missing,
            )

    def _render(self, views: list[ItemEvidence], *, check_cancel):
        rendered = []
        for view in views:
            check_cancel()
            item = view.item
            samples = [
                presentation.build_sample(
                    view,
                    sid,
                    sample_labels=self.sample_labels,
                    decision_refs=_decision_refs(view, sid),
                )
                for sid in item.sample_ids
            ]
            rendered.append(
                presentation.build_render_item(
                    view,
                    samples=samples,
                    readable_records=self._readable_records(view),
                    measured_mz_by_candidate=view.measured_mz,
                    relationship_members=[
                        {
                            "subject_id": member["subject_id"],
                            "sample_id": member["sample_key"],
                            "sample_label": self.sample_labels.get(
                                member["sample_key"], member["sample_key"]
                            ),
                        }
                        for member in view.members
                    ],
                )
            )
        return rendered

    def _readable_records(self, view: ItemEvidence) -> list[dict[str, Any]]:
        item = view.item
        return [
            presentation.exclusion_record(
                card,
                scope=_card_scope(item, card),
                sample_labels=self.sample_labels,
            )
            for card in view.cards
            if card.kind == "exclusions"
        ]


def _decision_refs(view: ItemEvidence, sample_id: str) -> list[CardRef]:
    """The exclusion records of an excluded candidate's sample."""
    item = view.item
    if item.kind != "candidate" or item.stratum != "excluded":
        return []
    return sorted(
        {
            card.ref
            for card in view.cards
            if card.kind == "exclusions" and sample_id in card.samples
        }
    )


def _card_scope(item: CanonicalItem, card: Card) -> tuple[list[str], list[str]]:
    """The item samples and candidates a card shows it concerns."""
    recorded_sample = card.facts.get("sample_key", card.facts.get("sample_id"))
    if recorded_sample is not None:
        sample_ids = [recorded_sample] if recorded_sample in item.sample_ids else []
    else:
        sample_ids = sorted(card.samples)
    candidate_ids = {cid for cid, _ in card.keys}
    recorded_candidate = card.facts.get("candidate_id")
    if recorded_candidate is not None and any(
        witness.candidate_id == recorded_candidate
        and (recorded_sample is None or witness.sample_key == recorded_sample)
        for witness in item.witnesses
    ):
        candidate_ids.add(recorded_candidate)
    return sample_ids, sorted(candidate_ids)
