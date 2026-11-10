"""Indexed selection of completed review evidence, computed when a report opens.

The review population (rows, groups without a row, ungrouped candidates), each
item's witnesses and its per-sample review facts are derived from the stored
groups, candidates, peaks, cells and method evidence. Nothing here is stored;
readers fetch only a requested page.
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

from xic_extractor.alignment_explanation.review import (
    ATTENTION,
    EXCLUDED,
    NULL_CELL,
    OBSERVED,
    ORIGIN_FLAGS,
    ZERO_CELL,
    attention_reason,
    cell_attention,
)
from xic_extractor.alignment_explanation.storage import (
    decode_json,
    open_readonly,
    present_tables,
)

CandidateKey = tuple[str, str]


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
    kind: Literal["row", "group", "candidate"]
    identifier: str
    group_id: str | None
    sort_key: bytes
    # sample, candidate, precursor m/z, apex RT, observation, sample label
    witnesses: tuple[tuple[str, str, float | None, float | None, str | None, str], ...]
    flags: dict[str, int]
    edge_keys: frozenset[CandidateKey]


def _review_items(
    db: sqlite3.Connection, summary: Mapping[str, Any]
) -> tuple[list[_Item], dict[int, list[tuple[int, int, str]]]]:
    """Derive the review population once, in the stored group and candidate order."""
    samples = [sample["sample_key"] for sample in summary["samples"]]
    stems = [sample["sample_stem"] for sample in summary["samples"]]
    peaks = {
        peak_id: (key, rt)
        for peak_id, key, rt in db.execute(
            "SELECT id,physical_peak_key,rt_apex_min FROM peaks"
        )
    }
    candidates: dict[int, tuple[Any, ...]] = {}
    key_of: dict[int, CandidateKey] = {}
    for row in db.execute(
        "SELECT id,candidate_id,sample,precursor_mz,method_subject,terminal_peak,"
        "rt_apex_min FROM candidates ORDER BY id"
    ):
        candidates[row[0]] = row
        key_of[row[0]] = (row[1], samples[row[2]])
    held: dict[tuple[int, int], int] = defaultdict(int)
    for subject, sample, result in db.execute(
        "SELECT method_subject,sample,method_result FROM method_assessments"
    ):
        held[subject, sample] |= ATTENTION if result == "HOLD" else 0
    excluded = {row[0] for row in db.execute("SELECT candidate FROM exclusions")}
    edge_attention: dict[int, int] = defaultdict(int)
    edge_sets: list[tuple[frozenset[int], int, str]] = []
    if "initial_edges" in present_tables(db):
        for left, right, disposition, evidence, keys in db.execute(
            "SELECT left_subject_id,right_subject_id,disposition,initial_evidence,"
            "candidates FROM initial_edges ORDER BY id"
        ):
            evidence = decode_json(evidence) or {}
            fact = {"disposition": disposition, "initial_evidence": evidence}
            edge_rows = frozenset(json.loads(keys))
            reason = attention_reason("initial_edges", fact)
            for candidate in edge_rows:
                edge_attention[candidate] |= ATTENTION if reason else 0
            priority = (
                0
                if evidence.get("decision") in {"blocked_edge", "split_edge", "split"}
                else 1
            )
            edge_sets.append(
                (edge_rows, priority, json.dumps((left, right), separators=(",", ":")))
            )
    members: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for group, sample, key in db.execute(
        "SELECT m.final_group,m.sample,p.physical_peak_key "
        "FROM members m, json_each(m.peaks) j JOIN peaks p ON p.id=j.value "
        "WHERE m.final_group IS NOT NULL"
    ):
        members[group][samples[sample]].add(key)
    # Cells needing attention through an accepted member risk; Backfill risks,
    # stored with the cell, are decoded only where present.
    flagged = set(
        db.execute(
            "SELECT final_group,sample FROM members WHERE final_group IS NOT NULL "
            "AND risk_flags IS NOT NULL AND risk_flags!='[]'"
        )
    )
    # group -> (sample key, cell flag) in sample order
    cells: dict[int, list[tuple[str, int]]] = defaultdict(list)
    origin_mask = sum(ORIGIN_FLAGS.values())
    for group, sample, variant, value, origin, observed, risks in db.execute(
        "SELECT group_id,sample,variant,value,support_origin,peaks!='[]',risks "
        "FROM cells ORDER BY group_id,sample"
    ):
        risky = (group, sample) in flagged or any(
            risk.get("risk_flags") for risk in decode_json(risks) or ()
        )
        flag = NULL_CELL if value is None else ZERO_CELL if value == 0 else 0
        if cell_attention(variant, value, risky) is not None:
            flag |= ATTENTION
        if observed:
            flag |= OBSERVED
        cells[group].append((samples[sample], flag | ORIGIN_FLAGS.get(origin, 0)))

    definitions: list[
        tuple[
            Literal["row", "group", "candidate"],
            str,
            str | None,
            list[int],
            int | None,
            str,
        ]
    ] = []
    grouped: set[int] = set()
    groups = list(
        db.execute(
            "SELECT id,feature_id,disposition,candidates FROM groups ORDER BY id"
        )
    )
    # Rows first, then groups without a row, in stored order.
    for with_row in (True, False):
        for group_id, feature_id, disposition, keys in groups:
            if (feature_id is not None) != with_row:
                continue
            linked_rows: list[int] = json.loads(keys)
            grouped.update(linked_rows)
            if with_row:
                definitions.append(
                    ("row", feature_id, feature_id, linked_rows, group_id, disposition)
                )
            else:
                rid = str(group_id)
                definitions.append(
                    ("group", rid, rid, linked_rows, group_id, disposition)
                )
    definitions.extend(
        ("candidate", key_of[row_id][0], None, [row_id], None, "")
        for row_id in candidates
        if row_id not in grouped
    )

    items = []
    kind: Literal["row", "group", "candidate"]
    for kind, identifier, gid, linked, group_id, disposition in definitions:
        ordered = sorted(linked, key=lambda row_id: key_of[row_id])
        ref = (
            [kind, key_of[ordered[0]][1], identifier]
            if kind == "candidate"
            else [kind, identifier]
        )
        sort_key = json.dumps(ref, ensure_ascii=False, separators=(",", ":")).encode()
        initial = ATTENTION if disposition in ("provisional", "unresolved") else 0
        group_members: Mapping[str, set[str]] = (
            members.get(group_id, {}) if group_id is not None else {}
        )
        flags = {key_of[row_id][1]: initial for row_id in ordered}
        witnesses = []
        observed = set()
        for row_id in ordered:
            cid, sid = key_of[row_id]
            _, _, sample, mz, subject, terminal, own_rt = candidates[row_id]
            member_ids = group_members.get(sid)
            observation: tuple[str | None, float | None] = (None, own_rt)
            if terminal is not None:
                key, rt = peaks[terminal]
                if not member_ids or key in member_ids:
                    observation = (key, rt)
            if observation[0]:
                observed.add(sid)
            witnesses.append(
                (sid, cid, mz, observation[1], observation[0], stems[sample])
            )
            if subject is not None:
                flags[sid] |= held.get((subject, sample), 0)
            flags[sid] |= edge_attention.get(row_id, 0)
            if row_id in excluded:
                flags[sid] |= EXCLUDED
        for sid, cell_flag in cells.get(group_id, ()) if group_id is not None else ():
            flags[sid] = (flags.get(sid, initial) & ~origin_mask) | cell_flag
        observed.update(sid for sid, keys in group_members.items() if keys)
        for sid in observed & flags.keys():
            flags[sid] |= OBSERVED
        items.append(
            _Item(
                kind,
                identifier,
                gid,
                sort_key,
                tuple(witnesses),
                flags,
                frozenset(key_of[row_id] for row_id in ordered),
            )
        )
    return items, _peers(items, edge_sets, key_of)


def _peers(items, edge_sets, key_of) -> dict[int, list[tuple[int, int, str]]]:
    """Items sharing a recorded initial edge, with the edge priority and ref."""
    peers: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    if not edge_sets:
        return peers
    by_key: dict[CandidateKey, set[int]] = defaultdict(set)
    for index, item in enumerate(items):
        for key in item.edge_keys:
            by_key[key].add(index)
    for linked, priority, edge_ref in edge_sets:
        keys = {key_of[row_id] for row_id in linked}
        participants = {index for key in keys for index in by_key[key]}
        for index in participants:
            for peer in {
                peer
                for key in keys - items[index].edge_keys
                for peer in by_key[key]
                if peer != index
            }:
                peers[index].append((peer, priority, edge_ref))
    return peers


@contextmanager
def open_review_query(path: Path, summary: Mapping[str, Any]) -> Iterator[ReviewQuery]:
    """One request-local selection session; the caller supplies an admitted summary."""
    with open_readonly(path) as db:
        items, peers = _review_items(db, summary)
    yield ReviewQuery(items, summary, peers)


class ReviewQuery:
    """Own selection over derived coordinates; presentation owns review policy."""

    def __init__(
        self,
        items: Sequence[_Item],
        summary: Mapping[str, Any],
        peers: Mapping[int, list[tuple[int, int, str]]],
    ):
        self._items = items
        self._summary = summary
        self._peers = peers

    def item_headers(self) -> list[tuple[int, str, str]]:
        return [
            (index, item.kind, item.identifier)
            for index, item in enumerate(self._items)
        ]

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
        """Return population counts and item IDs, filtering each witness as one row."""
        scope = set(scope)
        priority = (
            None
            if ordered_item_ids is None
            else {item: index for index, item in enumerate(ordered_item_ids)}
        )
        candidate_ids = set(candidate_ids)
        group_ids = set(group_ids)

        def base(index: int, witness: tuple[Any, ...]) -> bool:
            sid, cid, mz = witness[:3]
            item = self._items[index]
            return (
                (priority is None or index in priority)
                and sid in scope
                and (not candidate_ids or cid in candidate_ids)
                and (not group_ids or item.group_id in group_ids)
                and (mz_min is None or (mz is not None and mz >= mz_min))
                and (mz_max is None or (mz is not None and mz <= mz_max))
            )

        matched_items = []
        missing = 0
        for index, item in enumerate(self._items):
            rows = [w for w in item.witnesses if base(index, w)]
            if not rows:
                continue
            if rt_min is not None or rt_max is not None:
                if all(w[3] is None for w in rows):
                    missing += 1
                rows = [
                    w
                    for w in rows
                    if w[3] is not None
                    and (rt_min is None or w[3] >= rt_min)
                    and (rt_max is None or w[3] <= rt_max)
                ]
            if rows:
                matched_items.append(index)
        if priority is None:
            matched_items.sort(key=lambda index: self._items[index].sort_key)
        else:
            matched_items.sort(
                key=lambda index: (priority[index], self._items[index].sort_key)
            )
        page = (
            matched_items[offset:]
            if limit < 0
            else matched_items[offset : offset + limit]
        )
        return (
            page,
            len(self._items),
            self._eligible(scope),
            len(matched_items),
            missing,
        )

    def _eligible(self, scope: set[str], within=None) -> int:
        return sum(
            1
            for index, item in enumerate(self._items)
            if (within is None or index in within) and scope.intersection(item.flags)
        )

    def nearest_page(
        self, scope, *, mz, offset, limit, ordered_item_ids=None, **criteria
    ):
        """Rank the same first scoped witness the display uses, not any nearest one."""
        scope = tuple(sorted(scope))
        ids, run_total, eligible, _, missing_rt = self.page(
            scope, **criteria, ordered_item_ids=ordered_item_ids, limit=-1
        )
        ranked = []
        for item_id in ids:
            item = self._items[item_id]
            first = next((w for w in item.witnesses if w[0] in scope), None)
            witness_mz = None if first is None else first[2]
            if (
                isinstance(witness_mz, (int, float))
                and math.isfinite(witness_mz)
                and witness_mz > 0
            ):
                ranked.append((abs(witness_mz - mz), item.sort_key, item_id))
        population_count = len(ids)
        if criteria.get("rt_min") is None and criteria.get("rt_max") is None:
            # Scalar-only cells remain in the missing-coordinate disclosure.
            population_count = self._eligible(
                set(scope),
                None if ordered_item_ids is None else set(ordered_item_ids),
            )
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
        run_total = len(self._items)
        if not scope:
            return [], run_total, 0, 0, 0
        scoped_samples = set(scope)
        cases = []
        for index, item in enumerate(self._items):
            scoped = {
                sid: flag for sid, flag in item.flags.items() if sid in scoped_samples
            }
            if not scoped:
                continue
            values = list(scoped.values())
            facts = ScopedReviewFacts(
                len(values),
                sum(1 for flag in values if flag & OBSERVED),
                any(flag & ATTENTION for flag in values),
                any(flag & EXCLUDED for flag in values),
                any(flag & NULL_CELL for flag in values),
                any(flag & ZERO_CELL for flag in values),
                tuple(
                    name
                    for name, bit in ORIGIN_FLAGS.items()
                    if any(flag & bit for flag in values)
                ),
            )
            case_id, attention, deferred = classify(item.kind, facts)
            cases.append((index, item.sort_key, case_id, attention, deferred))
        eligible = len(cases)
        if attention_only:
            chosen = sorted((c for c in cases if c[3]), key=lambda c: c[1])
            return [c[0] for c in chosen[:limit]], run_total, eligible, len(chosen), 0
        by_case: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
        for case in cases:
            by_case[case[2]].append(case)
        ordered: list[tuple[bool, int, int, int]] = []
        for case_id, members in by_case.items():
            members.sort(key=lambda c: (rank(c[1]), c[1]))
            ordered.extend(
                (bool(c[4]), slot, case_id, c[0])
                for slot, c in enumerate(members, start=1)
            )
        ordered.sort()
        return [entry[3] for entry in ordered[:limit]], run_total, eligible, eligible, 0

    def item(self, item_id: int, scope: Sequence[str]) -> IndexedItem:
        item = self._items[item_id]
        scoped_samples = set(scope)
        return IndexedItem(
            item.kind,
            item.identifier,
            item.group_id,
            tuple(sorted(sid for sid in item.flags if sid in scoped_samples)),
            item.witnesses[0][0] if item.witnesses else None,
            tuple(w for w in item.witnesses if w[0] in scoped_samples),
        )

    def related_item(self, item_id: int) -> tuple[int, tuple[str, ...]] | None:
        peers = self._peers.get(item_id)
        if not peers:
            return None
        best: dict[int, tuple[int, str]] = {}
        refs: dict[int, set[str]] = defaultdict(set)
        for peer, priority, edge_ref in peers:
            refs[peer].add(edge_ref)
            current = best.get(peer)
            best[peer] = (
                (priority, edge_ref)
                if current is None
                else (min(current[0], priority), min(current[1], edge_ref))
            )
        peer = min(
            best, key=lambda p: (best[p][0], best[p][1], self._items[p].sort_key)
        )
        return peer, tuple(sorted(refs[peer]))
