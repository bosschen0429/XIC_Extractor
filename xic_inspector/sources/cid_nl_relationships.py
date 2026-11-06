"""Derive CID-NL Inspector relationships from already-loaded evidence.

Indexes are prepared once by the source and passed explicitly to stateless
queries. Results preserve candidate/sample identity, member/edge identity and
witness coordinates; loading and presentation remain with the source adapter.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from xic_extractor.alignment_explanation.query import recorded_observations
from xic_extractor.alignment_explanation.storage import RecordKey
from xic_inspector.selection.canonical import CanonicalItem, Witness

CandidateKey = tuple[str, str]
Records = dict[str, dict[RecordKey, dict[str, Any]]]
CandidateLinks = dict[CandidateKey, set[tuple[str, RecordKey]]]
RecordLinks = dict[tuple[str, RecordKey], set[CandidateKey]]


def index_record_links(
    links: Iterable[tuple[str, str, str, RecordKey]],
) -> tuple[CandidateLinks, RecordLinks]:
    """Index the loaded candidate/record links in both lookup directions."""
    candidate_links: CandidateLinks = {}
    record_links: RecordLinks = {}
    for cid, sid, kind, rid in links:
        key = cid, sid
        record = kind, rid
        candidate_links.setdefault(key, set()).add(record)
        record_links.setdefault(record, set()).add(key)
    return candidate_links, record_links


def index_member_edges(records: Records) -> dict[str, set[str]]:
    """Index initial edges by their recorded subject IDs, not candidate IDs."""
    edge_refs_by_member: dict[str, set[str]] = {}
    for edge_ref, edge in records.get("initial_edges", {}).items():
        for field in ("left_subject_id", "right_subject_id"):
            member_id = edge.get(field)
            if isinstance(member_id, str):
                edge_refs_by_member.setdefault(member_id, set()).add(
                    cast(str, edge_ref)
                )
    return edge_refs_by_member


def _edge_priority(records: Records, edge_ref: str) -> int:
    edge = records.get("initial_edges", {}).get(edge_ref, {})
    evidence = edge.get("initial_evidence", {})
    decision = evidence.get("decision")
    return 0 if decision in {"blocked_edge", "split_edge", "split"} else 1


def linked_candidates(
    record_links: RecordLinks, kind: str, rid: RecordKey
) -> set[CandidateKey]:
    return set(record_links.get((kind, rid), ()))


def witnesses(
    records: Records,
    candidates: dict[CandidateKey, dict[str, Any]],
    links: CandidateLinks,
    kind: str,
    identifier: str,
    gid: Any,
    scoped: list[CandidateKey],
) -> list[Witness]:
    """Candidate/observation coordinates shared by selection and presentation."""
    group = records.get("final_groups", {}).get(gid, {})
    members_by_sample: dict[str, set[str]] = {}
    for member in group.get("member_subjects", []):
        members_by_sample.setdefault(member.get("sample_key"), set()).update(
            member.get("physical_peak_keys", [])
        )
    witnesses: list[Witness] = []
    for cid, sid in scoped:
        candidate = candidates[cid, sid]
        observations = recorded_observations(
            candidate,
            links.get((cid, sid), ()),
            records,
            members_by_sample.get(sid),
        )
        witnesses.extend(
            Witness(
                sample_key=sid,
                candidate_id=cid,
                group_hypothesis_id=gid,
                row_key_id=identifier if kind == "row" else None,
                precursor_mz=candidate.get("precursor_mz"),
                rt_apex_min=observation.get("rt_apex_min"),
                observation_id=observation.get("physical_peak_key"),
                sample_stem=candidate.get("sample_stem"),
            )
            for observation in observations
        )
    return witnesses


def related_items(
    records: Records,
    record_links: RecordLinks,
    items: list[CanonicalItem],
    item_keys: dict[str, set[CandidateKey]],
) -> dict[str, list[tuple[CanonicalItem, tuple[str, ...]]]]:
    """Return exact peers ranked by recorded edge priority and stable references."""
    related: dict[str, list[tuple[CanonicalItem, tuple[str, ...]]]] = {}
    item_by_ref = {item.serialized_ref: item for item in items}
    edge_links = {
        edge_ref: record_links.get(("initial_edges", edge_ref), set())
        for edge_ref in records.get("initial_edges", {})
    }
    item_refs_by_key: dict[tuple[str, str], set[str]] = {}
    for item_ref, keys in item_keys.items():
        for key in keys:
            item_refs_by_key.setdefault(key, set()).add(item_ref)
    peer_edges: dict[str, dict[str, set[str]]] = {
        item_ref: {} for item_ref in item_keys
    }
    for edge_ref, linked_keys in edge_links.items():
        participating_refs = {
            item_ref
            for key in linked_keys
            for item_ref in item_refs_by_key.get(key, ())
        }
        for item_ref in participating_refs:
            peer_refs = {
                peer_ref
                for key in linked_keys - item_keys[item_ref]
                for peer_ref in item_refs_by_key.get(key, ())
                if peer_ref != item_ref
            }
            for peer_ref in peer_refs:
                peer_edges[item_ref].setdefault(peer_ref, set()).add(
                    cast(str, edge_ref)
                )
    for item in items:
        peers = peer_edges[item.serialized_ref]
        ranked = sorted(
            peers.items(),
            key=lambda entry: (
                min(_edge_priority(records, ref) for ref in entry[1]),
                min(entry[1]),
                entry[0],
            ),
        )
        related[item.serialized_ref] = [
            (item_by_ref[peer_ref], tuple(sorted(refs))) for peer_ref, refs in ranked
        ]
    return related


def relationship_members(
    records: Records, sample_labels: dict[str, str], item: CanonicalItem
) -> list[dict[str, Any]]:
    group_id = item.details["source"].get("feature_id")
    group = records.get("final_groups", {}).get(group_id, {})
    return [
        {
            "subject_id": member["subject_id"],
            "sample_id": member.get("sample_key"),
            "sample_label": _member_sample_label(
                sample_labels, member["subject_id"], member
            ),
        }
        for member in group.get("member_subjects", [])
        if isinstance(member, dict) and isinstance(member.get("subject_id"), str)
    ]


def internal_pair_support(
    records: Records,
    initial_edge_refs_by_member: dict[str, set[str]],
    sample_labels: dict[str, str],
    item: CanonicalItem,
) -> list[dict[str, Any]]:
    source = item.details["source"]
    group_id = source.get("feature_id")
    group = records.get("final_groups", {}).get(group_id, {})
    members = group.get("member_subjects")
    if not isinstance(members, list):
        return []
    members_by_id = {
        member["subject_id"]: member
        for member in members
        if isinstance(member, dict) and isinstance(member.get("subject_id"), str)
    }
    edge_refs = {
        edge_ref
        for member_id in members_by_id
        for edge_ref in initial_edge_refs_by_member.get(member_id, ())
    }
    projected = []
    for edge_ref in sorted(edge_refs):
        edge = records.get("initial_edges", {}).get(edge_ref, {})
        left_id = edge.get("left_subject_id")
        right_id = edge.get("right_subject_id")
        if left_id not in members_by_id or right_id not in members_by_id:
            continue
        # Membership above admits only the string IDs collected from members.
        left_id = cast(str, left_id)
        right_id = cast(str, right_id)
        disposition = edge.get("disposition")
        if disposition not in {"eligible", "review_only"}:
            continue
        evidence = edge.get("initial_evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        projected.append(
            {
                "edge_ref": edge_ref,
                "left_subject_id": left_id,
                "right_subject_id": right_id,
                "left_sample_label": _member_sample_label(
                    sample_labels, left_id, members_by_id[left_id]
                ),
                "right_sample_label": _member_sample_label(
                    sample_labels, right_id, members_by_id[right_id]
                ),
                "support_kind": (
                    "direct" if disposition == "eligible" else "review_only"
                ),
                "disposition": disposition,
                "decision": evidence.get("decision"),
                "reason": evidence.get("reason"),
                "score": evidence.get("score"),
                "rt_basis": evidence.get("rt_basis"),
                "rt_raw_delta_sec": evidence.get("rt_raw_delta_sec"),
                "rt_drift_corrected_delta_sec": evidence.get(
                    "rt_drift_corrected_delta_sec"
                ),
            }
        )
    return projected


def _member_sample_label(
    sample_labels: dict[str, str], member_id: str, member: dict[str, Any]
) -> str:
    sample_id = member.get("sample_key")
    if isinstance(sample_id, str) and sample_id in sample_labels:
        return sample_labels[sample_id]
    for field in ("sample_label", "sample_stem", "label"):
        label = member.get(field)
        if isinstance(label, str) and label:
            return label
    return sample_id if isinstance(sample_id, str) and sample_id else member_id


def record_scope(
    links: CandidateLinks, item: CanonicalItem, record: dict[str, Any]
) -> tuple[list[str], list[str]]:
    item_keys = _item_keys(item)
    linked_keys = record_link_keys(links, item, record)
    facts = record["facts"]
    recorded_sample = facts.get("sample_key", facts.get("sample_id"))
    if recorded_sample is not None:
        sample_ids = [recorded_sample] if recorded_sample in item.sample_ids else []
        linked_keys = {key for key in linked_keys if key[1] == recorded_sample}
    else:
        sample_ids = sorted({sample_id for _, sample_id in linked_keys})
    recorded_candidate = facts.get("candidate_id")
    candidate_ids = {candidate_id for candidate_id, _ in linked_keys}
    if recorded_candidate is not None and any(
        candidate_id == recorded_candidate
        and (recorded_sample is None or sample_id == recorded_sample)
        for candidate_id, sample_id in item_keys
    ):
        candidate_ids.add(recorded_candidate)
    return sample_ids, sorted(candidate_ids)


def _item_keys(item: CanonicalItem) -> set[CandidateKey]:
    return {(witness.candidate_id, witness.sample_key) for witness in item.witnesses}


def record_link_keys(
    links: CandidateLinks, item: CanonicalItem, record: dict[str, Any]
) -> set[CandidateKey]:
    linked_keys = {
        key
        for key in _item_keys(item)
        if (record["kind"], record["ref"]) in links.get(key, set())
    }
    recorded_sample = record["facts"].get(
        "sample_key", record["facts"].get("sample_id")
    )
    if recorded_sample is not None:
        linked_keys = {key for key in linked_keys if key[1] == recorded_sample}
    return linked_keys


def related_record_refs(
    links: CandidateLinks,
    item: CanonicalItem,
    curves: list[dict[str, Any]],
    readable_refs: Iterable[RecordKey],
) -> dict[RecordKey, list[tuple[str, str]]]:
    """Link curves/cell decisions to assessments and edges sharing exact keys."""
    records_by_ref = {record["ref"]: record for record in item.details["records"]}
    targets = [
        (record, record_link_keys(links, item, record))
        for record in item.details["records"]
        if record["kind"] in {"method_assessments", "initial_edges"}
    ]
    curves_by_ref = {
        curve["evidence_ref"]: curve
        for curve in curves
        if curve.get("evidence_ref") is not None
    }
    related_by_ref: dict[RecordKey, list[tuple[str, str]]] = {}
    for ref in readable_refs:
        record = records_by_ref.get(ref)
        if ref in curves_by_ref:
            curve = curves_by_ref[ref]
            sample_id = curve.get("sample_id")
            source_keys = {
                (candidate_id, sample_id)
                for candidate_id in curve.get("candidate_ids", [])
                if sample_id is not None
            }
        elif record is not None and record["kind"] == "cell_decisions":
            source_keys = record_link_keys(links, item, record)
        else:
            continue
        related = []
        for target, target_keys in targets:
            if not source_keys.intersection(target_keys):
                continue
            related.append((target["kind"], target["ref"]))
        related_by_ref[ref] = related
    return related_by_ref
