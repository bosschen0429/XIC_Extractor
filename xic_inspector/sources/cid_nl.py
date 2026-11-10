"""Load and scope retained CID-NL evidence for the shared Inspector.

The scientific reader owns payload validation. This adapter owns record kinds,
scoped status interpretation and curve projection; cid_nl_relationships derives
evidence relationships, while cid_nl_presentation assembles labels and readable
records.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.alignment_explanation.reader import (
    ExplanationMetadata,
    open_review_cells,
    read_alignment_curves,
    read_alignment_explanation,
    read_explanation_metadata,
    read_explanation_selection,
    read_review_row_samples,
)
from xic_extractor.alignment_explanation.review import (
    ATTENTION,
    EXCLUDED,
    NULL_CELL,
    OBSERVED,
    ORIGIN_FLAGS,
    ZERO_CELL,
    selection_sample_facts,
)
from xic_extractor.alignment_explanation.review import (
    attention_reason as _attention_reason,
)
from xic_extractor.alignment_explanation.storage import RecordKey
from xic_inspector.selection.canonical import (
    CanonicalItem,
    build_canonical_ref,
)
from xic_inspector.selection.strata import classify_scoped_stratum
from xic_inspector.sources import cid_nl_presentation as presentation
from xic_inspector.sources import cid_nl_relationships as relationships
from xic_inspector.sources.rt_replay import align_curve, read_calibration


def read_summary(path: str | Path) -> dict[str, Any]:
    """Read compact metadata without retrieving curve payloads."""
    return read_alignment_explanation(path)


class CidNlSource:
    """One report's metadata projection and selected evidence retrieval."""

    def __init__(
        self,
        path: Path,
        *,
        metadata: ExplanationMetadata | None = None,
        query_only: bool = False,
    ) -> None:
        self.path = path
        from_selection = metadata is not None
        metadata = metadata or read_explanation_metadata(
            path, selection_only=True, query_only=query_only
        )
        self.summary = metadata.summary
        self._compact_selection = (
            not from_selection and self.summary["retention"] == "review"
        )
        self._query_only = self._compact_selection and query_only
        self._row_samples = read_review_row_samples(path) if self._query_only else {}
        self._selection_cells: (
            Callable[[str], dict[tuple[str, str], dict[str, Any]]] | None
        ) = None
        self.attempt_id = self.summary.get("processing_attempt_id", "")
        self.records = metadata.records
        self.candidates = metadata.candidates
        self.links, self.record_links = relationships.index_record_links(metadata.links)
        self.initial_edge_refs_by_member = relationships.index_member_edges(
            self.records
        )
        self.sample_labels: dict[str, str] = {}
        self.row_cell_decisions: dict[
            tuple[str, str], dict[RecordKey, dict[str, Any]]
        ] = {}
        self.related_items: dict[str, list[tuple[CanonicalItem, tuple[str, ...]]]] = {}

        for sample in self.summary.get("samples", []):
            sid = sample.get("sample_key")
            sample_stem = sample.get("sample_stem")
            if isinstance(sid, str) and isinstance(sample_stem, str):
                self.sample_labels[sid] = sample_stem
        self.rt_calibration = read_calibration(self.summary["sample_rt_calibration"])
        for (_, sid), candidate in self.candidates.items():
            sample_stem = candidate.get("sample_stem")
            if isinstance(sample_stem, str) and sample_stem:
                self.sample_labels.setdefault(sid, sample_stem)
        for rid, fact in self.records.get("cell_decisions", {}).items():
            group_id = fact.get("feature_id")
            row_key_id = fact.get("feature_id")
            if isinstance(group_id, str) and isinstance(row_key_id, str):
                self.row_cell_decisions.setdefault((group_id, row_key_id), {})[rid] = (
                    fact
                )

    def load_items(self, sample_ids: set[str]) -> list[CanonicalItem]:
        if self._compact_selection and not self._query_only:
            with open_review_cells(self.path, self.summary) as read_cells:
                self._selection_cells = read_cells
                try:
                    return self._load_items(sample_ids)
                finally:
                    self._selection_cells = None
        return self._load_items(sample_ids)

    def _load_items(self, sample_ids: set[str]) -> list[CanonicalItem]:
        items = []
        build_item = (
            self._selection_item if self._compact_selection else self._presentation_item
        )
        item_keys: dict[str, set[tuple[str, str]]] = {}
        row_groups = set()
        grouped = set()
        # Construct the run population before applying sample scope.
        for rid, payload in self.records.get("rows", {}).items():
            gid = payload.get("feature_id")
            row_groups.add(gid)
            keys = relationships.linked_candidates(self.record_links, "rows", rid)
            grouped.update(keys)
            item = build_item("row", rid, gid, keys, sample_ids, payload)
            items.append(item)
            item_keys[item.serialized_ref] = keys
        for gid, payload in self.records.get("final_groups", {}).items():
            keys = relationships.linked_candidates(
                self.record_links, "final_groups", gid
            )
            grouped.update(keys)
            if gid not in row_groups:
                item = build_item("group", gid, gid, keys, sample_ids, payload)
                items.append(item)
                item_keys[item.serialized_ref] = keys
        for key, payload in self.candidates.items():
            if key not in grouped:
                item = build_item("candidate", key[0], None, {key}, sample_ids, payload)
                items.append(item)
                item_keys[item.serialized_ref] = {key}
        self.related_items.update(
            relationships.related_items(
                self.records, self.record_links, items, item_keys
            )
        )
        for item in items:
            item.details["review_summary"]["related_group_count"] = len(
                self.related_items[item.serialized_ref]
            )
        return sorted(items, key=lambda item: item.sort_key)

    def _selection_item(
        self, kind, identifier, gid, keys, scope, payload
    ) -> CanonicalItem:
        """Reduce scoped facts once; never construct per-sample presentation."""
        scoped = sorted(key for key in keys if key[1] in scope)
        sample_ids = {sid for _, sid in scoped}
        witnesses = relationships.witnesses(
            self.records, self.candidates, self.links, kind, identifier, gid, scoped
        )
        if self._query_only:
            sample_ids.update(self._row_samples.get(identifier, set()) & scope)
            _, ref = build_canonical_ref(
                kind,
                self.attempt_id,
                identifier,
                sample_id=next(iter(keys))[1] if kind == "candidate" else None,
            )
            return CanonicalItem(
                kind=kind,
                attempt_id=self.attempt_id,
                identifier=identifier,
                serialized_ref=ref,
                sample_ids=tuple(sorted(sample_ids)),
                witnesses=tuple(witnesses),
                stratum="unclassified",
                details={"review_summary": {}},
            )
        cells: tuple[dict[str, Any], ...] = ()
        if kind == "row":
            assert self._selection_cells is not None
            cells = tuple(
                cell
                for cell in self._selection_cells(identifier).values()
                if cell["sample_key"] in scope
            )
        flags = selection_sample_facts(
            kind,
            identifier,
            gid,
            scoped,
            self.candidates,
            self.records,
            self.links,
            cells,
            {w.sample_key for w in witnesses if w.observation_id},
        )
        sample_ids = set(flags)
        observed = {sid for sid, flag in flags.items() if flag & OBSERVED}
        group = self.records.get("final_groups", {}).get(gid, {})
        attention = group.get("disposition") in ("provisional", "unresolved") or any(
            flag & ATTENTION for flag in flags.values()
        )
        excluded = any(flag & EXCLUDED for flag in flags.values())
        has_null = any(flag & NULL_CELL for flag in flags.values())
        has_zero = any(flag & ZERO_CELL for flag in flags.values())
        origins = {
            sid: next(
                (origin for origin, bit in ORIGIN_FLAGS.items() if flag & bit),
                "unavailable",
            )
            for sid, flag in flags.items()
        }
        members = group.get("member_subjects", [])
        _, ref = build_canonical_ref(
            kind,
            self.attempt_id,
            identifier,
            sample_id=next(iter(keys))[1] if kind == "candidate" else None,
        )
        return CanonicalItem(
            kind=kind,
            attempt_id=self.attempt_id,
            identifier=identifier,
            serialized_ref=ref,
            sample_ids=tuple(sorted(sample_ids)),
            witnesses=tuple(witnesses),
            stratum=classify_scoped_stratum(
                is_attention=attention,
                is_excluded=excluded,
                has_null_cell=has_null,
                has_zero_cell=has_zero,
            ),
            is_attention=attention,
            details={
                "review_summary": {
                    "observed_sample_count": len(observed & sample_ids),
                    "scope_sample_count": len(sample_ids),
                    "origin_kinds": sorted(
                        {origins.get(sid, "unavailable") for sid in sample_ids}
                    ),
                    "initial_member_count": len(keys),
                    "final_member_count": len(members)
                    if "member_subjects" in group
                    else len(keys),
                    "related_group_count": 0,
                }
            },
        )

    def _presentation_item(
        self, kind, identifier, gid, keys, scope, payload
    ) -> CanonicalItem:
        scoped = sorted(key for key in keys if key[1] in scope)
        facts: dict[tuple[str, RecordKey], dict[str, Any]] = {}
        fact_samples: dict[tuple[str, RecordKey], set[str]] = {}
        cell_bound_samples: set[str] = set()
        rid: RecordKey
        witnesses = relationships.witnesses(
            self.records, self.candidates, self.links, kind, identifier, gid, scoped
        )
        for cid, sid in scoped:
            linked = self.links.get((cid, sid), set())
            for record_kind, rid in linked:
                fact = self.records.get(record_kind, {}).get(rid, {})
                fact_sid = fact.get("sample_key", fact.get("sample_id"))
                if fact_sid is not None and fact_sid != sid:
                    continue
                if fact.get("feature_id") not in (None, gid):
                    continue
                if kind == "row" and fact.get("feature_id") not in (None, identifier):
                    continue
                facts[record_kind, rid] = fact
                fact_samples.setdefault((record_kind, rid), set()).add(sid)
        if kind == "row" and isinstance(gid, str):
            indexed_cells = self.row_cell_decisions.get((gid, identifier), {})
            for rid, fact in sorted(indexed_cells.items()):
                sid = fact.get("sample_key", fact.get("sample_id"))
                if not isinstance(sid, str) or sid not in scope:
                    continue
                facts["cell_decisions", rid] = fact
                fact_samples.setdefault(("cell_decisions", rid), set()).add(sid)
                cell_bound_samples.add(sid)
                observation_id = fact.get("cell_observation_id")
                if not isinstance(observation_id, str):
                    continue
                observation = self.records.get("cell_observations", {}).get(
                    observation_id
                )
                if not isinstance(observation, dict):
                    continue
                observation_sid = observation.get(
                    "sample_key", observation.get("sample_id")
                )
                if (
                    observation.get("feature_id") != gid
                    or observation_sid != sid
                    or observation.get("feature_id") not in (None, identifier)
                ):
                    continue
                facts["cell_observations", observation_id] = observation
                fact_samples.setdefault(
                    ("cell_observations", observation_id), set()
                ).add(sid)
        triggers = []
        group = self.records.get("final_groups", {}).get(gid, {})
        if group.get("disposition") in ("provisional", "unresolved"):
            triggers.append(
                {"scope": "group", "ref": gid, "reason": group["disposition"]}
            )
        cells = []
        excluded = False
        for (record_kind, rid), fact in sorted(facts.items()):
            reason = _attention_reason(record_kind, fact)
            if record_kind == "cell_decisions":
                value = fact.get("value", fact.get("quantitative_value"))
                cells.append({**fact, "quantitative_value": value})
            elif record_kind == "exclusions":
                excluded = True
            if reason:
                trigger = {
                    "scope": "sample",
                    "ref": rid,
                    "reason": reason,
                    "sample_ids": sorted(fact_samples[record_kind, rid]),
                }
                reason_codes = fact.get("reason_codes")
                if isinstance(reason_codes, list):
                    trigger["reason_codes"] = [
                        code for code in reason_codes if isinstance(code, str)
                    ]
                triggers.append(trigger)
        stratum = classify_scoped_stratum(
            is_attention=bool(triggers),
            is_excluded=excluded,
            has_null_cell=any(c["quantitative_value"] is None for c in cells),
            has_zero_cell=any(c["quantitative_value"] == 0 for c in cells),
        )
        sample_status = {}
        item_sample_ids = {sid for _, sid in scoped} | cell_bound_samples
        for sid in sorted(item_sample_ids):
            local_cells = [
                c for c in cells if c.get("sample_key", c.get("sample_id")) == sid
            ]
            local_attention = any(
                sid in trigger.get("sample_ids", ()) for trigger in triggers
            )
            local_excluded = any(
                kind == "exclusions" and sid in fact_samples[kind, rid]
                for kind, rid in facts
            )
            sample_status[sid] = (
                classify_scoped_stratum(
                    is_attention=local_attention,
                    is_excluded=local_excluded,
                    has_null_cell=any(
                        c["quantitative_value"] is None for c in local_cells
                    ),
                    has_zero_cell=any(
                        c["quantitative_value"] == 0 for c in local_cells
                    ),
                )
                if local_cells or local_attention or local_excluded
                else "unavailable"
            )
        _, ref = build_canonical_ref(
            kind,
            self.attempt_id,
            identifier,
            sample_id=next(iter(keys))[1] if kind == "candidate" else None,
        )
        support_observations = {
            sid: self._support_observation_refs(sid, gid, group, witnesses, facts)
            for sid in sorted(item_sample_ids)
        }
        origins = {
            sid: self._support_origin(sid, support_observations[sid], facts)
            for sid in sorted(item_sample_ids)
        }
        initial_member_ids = {
            member.get("subject_id")
            for initial_ref, fact in self.records.get("initial_groups", {}).items()
            if any(
                ("initial_groups", initial_ref) in self.links.get(key, set())
                for key in scoped
            )
            for member in fact.get("member_subjects", [])
            if isinstance(member, dict) and isinstance(member.get("subject_id"), str)
        }
        final_members = group.get("member_subjects")
        review_summary = {
            "observed_sample_count": len(
                {sid for sid, refs in support_observations.items() if refs}
            ),
            "scope_sample_count": len(item_sample_ids),
            "origin_kinds": sorted({origin["kind"] for origin in origins.values()}),
            "initial_member_count": (
                len(initial_member_ids) if initial_member_ids else len(keys)
            ),
            "final_member_count": len(final_members)
            if isinstance(final_members, list)
            else len(keys),
            "related_group_count": 0,
        }
        return CanonicalItem(
            kind=kind,
            attempt_id=self.attempt_id,
            identifier=identifier,
            serialized_ref=ref,
            sample_ids=tuple(sorted(item_sample_ids)),
            witnesses=tuple(witnesses),
            stratum=stratum,
            is_attention=bool(triggers),
            details={
                "source": payload,
                "cells": cells,
                "attention_triggers": triggers,
                "sample_status": sample_status,
                "support_origins": origins,
                "review_summary": review_summary,
                "initial_edge_coverage": self.summary["initial_edge_coverage"],
                "records": [
                    {"kind": k, "ref": r, "facts": f}
                    for (k, r), f in sorted(facts.items())
                ],
            },
        )

    @staticmethod
    def _support_observation_refs(sample_id, group_id, group, witnesses, facts):
        observation_refs = {
            witness.observation_id
            for witness in witnesses
            if witness.sample_key == sample_id and witness.observation_id
        }
        observation_refs.update(
            observation_id
            for member in group.get("member_subjects", [])
            if member.get("sample_key") == sample_id
            for observation_id in member.get("physical_peak_keys", [])
            if isinstance(observation_id, str)
        )
        for (record_kind, _), fact in facts.items():
            if record_kind == "cell_decisions" and fact.get("sample_key") == sample_id:
                observation_refs.update(fact.get("observation_ids", ()))
            if record_kind != "cell_observations":
                continue
            for predecessor in fact.get("predecessor_cells", []):
                content = predecessor.get("content", {})
                if (
                    content.get("sample_key") == sample_id
                    and content.get("feature_id") == group_id
                    and isinstance(content.get("physical_peak_key"), str)
                ):
                    observation_refs.add(content["physical_peak_key"])
        return sorted(observation_refs)

    @staticmethod
    def _support_origin(sample_id, observation_refs, facts):
        recorded = {
            fact["support_origin"]
            for (kind, _), fact in facts.items()
            if kind == "cell_decisions"
            and fact.get("sample_key") == sample_id
            and "support_origin" in fact
        }
        if recorded:
            kind = next(
                (
                    value
                    for value in (
                        "backfill_support",
                        "discovery_support",
                    )
                    if value in recorded
                ),
                "unavailable",
            )
            return {
                "kind": kind,
                "label": presentation.support_origin_label(kind),
                "method_refs": [],
                "capture_refs": [],
                "observation_refs": observation_refs,
            }
        observation_set = set(observation_refs)
        captures = []
        methods = []
        for (record_kind, ref), fact in facts.items():
            fact_sid = fact.get("sample_key", fact.get("sample_id"))
            if fact_sid != sample_id:
                continue
            if (
                record_kind == "capture_evidence"
                and fact.get("physical_peak_key") in observation_set
            ):
                captures.append((ref, fact))
            elif (
                record_kind == "method_assessments"
                and fact.get("method_result") == "SUPPORT"
            ):
                peak_ids = fact.get("candidate_physical_peak_keys", [])
                if isinstance(peak_ids, list) and observation_set.intersection(
                    peak_ids
                ):
                    methods.append((ref, fact))

        capture_kinds = {fact.get("kind") for _, fact in captures}
        invocations = {fact.get("detector_invocation_id") for _, fact in methods}
        if (
            "backfill_observation" in capture_kinds
            and "untargeted-cid-nl-backfill" in invocations
        ):
            kind = "backfill_support"
        elif "initial_capture" in capture_kinds and any(
            isinstance(value, str) and value.startswith("untargeted-cid-nl-real-data:")
            for value in invocations
        ):
            kind = "discovery_support"
        else:
            kind = "unavailable"
        return {
            "kind": kind,
            "label": presentation.support_origin_label(kind),
            "method_refs": sorted(ref for ref, _ in methods),
            "capture_refs": sorted(ref for ref, _ in captures),
            "observation_refs": observation_refs,
        }

    def load_render_items(
        self, items, *, capability: str, check_cancel: Callable[[], None]
    ):
        primary_items = list(items)
        if self._compact_selection:
            expanded = [
                *primary_items,
                *(
                    peer
                    for item in primary_items
                    for peer, _ in self.related_items.get(item.serialized_ref, [])[:1]
                ),
            ]
            check_cancel()
            metadata = read_explanation_selection(
                self.path,
                candidate_keys=tuple(
                    sorted(
                        {
                            (w.candidate_id, w.sample_key)
                            for item in expanded
                            for w in item.witnesses
                        }
                    )
                ),
                row_ids=tuple(
                    sorted({item.identifier for item in expanded if item.kind == "row"})
                ),
                summary=self.summary,
            )
            selected_source = CidNlSource(self.path, metadata=metadata)
            restored = {
                item.serialized_ref: item
                for item in selected_source.load_items(
                    {sid for item in expanded for sid in item.sample_ids}
                )
            }
            selected = [
                replace(
                    restored[item.serialized_ref],
                    matched_witnesses=item.matched_witnesses,
                )
                for item in primary_items
            ]
            return selected_source.load_render_items(
                selected, capability=capability, check_cancel=check_cancel
            )
        chosen_peers = {
            item.serialized_ref: self.related_items.get(item.serialized_ref, [])[:1]
            for item in primary_items
        }
        expanded = list(primary_items)
        expanded_refs = {item.serialized_ref for item in expanded}
        for peers in chosen_peers.values():
            for peer, _edge_refs in peers:
                if peer.serialized_ref not in expanded_refs:
                    expanded.append(peer)
                    expanded_refs.add(peer.serialized_ref)
        items = expanded
        # Read each sample's selected exact references once. This avoids the
        # candidate-ID/sample cross product when IDs are shared across samples.
        refs_by_sample: dict[str, set[str]] = {}
        captures_by_item = {
            item.serialized_ref: [
                record["facts"]
                for record in item.details["records"]
                if record["kind"] == "capture_evidence"
            ]
            for item in items
        }
        for item in items:
            for capture in captures_by_item[item.serialized_ref]:
                refs_by_sample.setdefault(capture["sample_id"], set()).add(
                    capture["record_id"]
                )
            for witness in item.witnesses:
                refs = refs_by_sample.setdefault(witness.sample_key, set())
                if witness.observation_id:
                    refs.add(witness.observation_id)
                elif item.kind == "candidate":
                    refs.add(witness.candidate_id)
        raw_curves = []
        if capability == "retained_curves":
            for sid, refs in sorted(refs_by_sample.items()):
                if not refs:
                    continue
                check_cancel()
                raw_curves.extend(
                    read_alignment_curves(
                        self.path, sample_ids=(sid,), record_ids=tuple(sorted(refs))
                    )
                )
            present = {
                (curve.get("sample_id"), curve.get("physical_peak_key"))
                for curve in raw_curves
            }
            promised = {
                (w.sample_key, w.observation_id)
                for item in items
                for w in item.witnesses
                if w.observation_id
            }
            missing = promised - present
            if missing and self.summary.get("curve_retention") != "selected":
                raise ExplanationError(
                    f"Missing promised selected observation payloads: {sorted(missing)}"
                )
            promised_captures = {
                (capture["sample_id"], capture["payload_id"])
                for captures in captures_by_item.values()
                for capture in captures
            }
            present_captures = {
                (curve.get("sample_id"), curve.get("payload_id"))
                for curve in raw_curves
            }
            missing_captures = promised_captures - present_captures
            if missing_captures:
                raise ExplanationError(
                    "Missing promised selected capture payloads: "
                    f"{sorted(missing_captures)}"
                )
            for curve in raw_curves:
                provenance = curve.get("provenance", {})
                for name, expected in (("processing_attempt_id", self.attempt_id),):
                    if name in provenance and provenance[name] != expected:
                        raise ExplanationError(f"Selected curve crosses {name} binding")
        rendered = []
        for item in primary_items:
            check_cancel()
            projected_curves = self._project_curves(
                item, raw_curves, captures_by_item[item.serialized_ref]
            )
            samples = [
                presentation.build_sample(
                    item,
                    sid,
                    fallback_sample_label=self.sample_labels.get(sid, sid),
                    decision_refs=self._decision_refs(item, sid),
                    projected_curves=projected_curves,
                )
                for sid in item.sample_ids
            ]
            pair_support = relationships.internal_pair_support(
                self.records, self.initial_edge_refs_by_member, self.sample_labels, item
            )
            relationship_availability = (
                "recorded"
                if pair_support
                else self.summary.get("audit_availability", {}).get(
                    "initial_edges", "unavailable"
                )
            )
            rendered.append(
                presentation.build_render_item(
                    item,
                    samples=samples,
                    projected_curves=projected_curves,
                    readable_records=self._readable_records(item, projected_curves),
                    measured_mz_by_candidate={
                        (w.candidate_id, w.sample_key): self.candidates[
                            w.candidate_id, w.sample_key
                        ].get("ms1_apex_mz")
                        for w in item.witnesses
                    },
                    relationship_members=relationships.relationship_members(
                        self.records, self.sample_labels, item
                    ),
                    internal_pair_support=pair_support,
                    relationship_availability=relationship_availability,
                    related_groups=[
                        presentation.related_group(
                            peer,
                            edge_refs,
                            self._project_curves(
                                peer,
                                raw_curves,
                                captures_by_item[peer.serialized_ref],
                            )[:1],
                        )
                        for peer, edge_refs in chosen_peers[item.serialized_ref]
                    ],
                )
            )
        return rendered

    def _project_curves(self, item, raw_curves, captures):
        keys = {(w.candidate_id, w.sample_key) for w in item.witnesses}
        observation_keys = {
            (w.observation_id, w.sample_key) for w in item.witnesses if w.observation_id
        }
        capture_keys = {
            (capture["sample_id"], capture["payload_id"]) for capture in captures
        }
        curves = [
            curve
            for curve in raw_curves
            if (curve.get("sample_id"), curve.get("payload_id")) in capture_keys
            or (
                (curve.get("physical_peak_key"), curve.get("sample_id"))
                in observation_keys
                if observation_keys
                else (curve.get("candidate_id"), curve.get("sample_id")) in keys
            )
        ]
        projected_curves = []
        for curve in curves:
            projected = self._curve(curve)
            projected["candidate_ids"] = sorted(
                {
                    cid
                    for cid, sid in keys
                    if sid == curve.get("sample_id")
                    and (
                        ("capture_evidence", curve.get("payload_id"))
                        in self.links.get((cid, sid), set())
                        or any(
                            witness.candidate_id == cid
                            and witness.sample_key == sid
                            and witness.observation_id == curve.get("physical_peak_key")
                            for witness in item.witnesses
                            if witness.observation_id
                        )
                    )
                }
            )
            projected_curves.append(projected)
        return projected_curves

    def _decision_refs(self, item: CanonicalItem, sample_id: str) -> list[RecordKey]:
        item_keys = {
            (witness.candidate_id, witness.sample_key) for witness in item.witnesses
        }
        refs = []
        for record in item.details["records"]:
            if record["kind"] == "exclusions":
                if item.kind != "candidate" or item.stratum != "excluded":
                    continue
                applies = any(
                    sid == sample_id
                    for _candidate_id, sid in relationships.record_link_keys(
                        self.links, item, record
                    )
                )
                if applies:
                    refs.append(record["ref"])
                continue
            if record["kind"] != "cell_decisions":
                continue
            facts = record["facts"]
            recorded_sample = facts.get("sample_key", facts.get("sample_id"))
            if recorded_sample is not None:
                applies = recorded_sample == sample_id
            else:
                applies = any(
                    sid == sample_id
                    and (cid, sid) in item_keys
                    and (record["kind"], record["ref"])
                    in self.links.get((cid, sid), set())
                    for cid, sid in item_keys
                )
            if applies:
                refs.append(record["ref"])
        return sorted(set(refs))

    def _readable_records(
        self, item: CanonicalItem, curves: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        available_refs = {record["ref"] for record in item.details["records"]} | {
            curve["evidence_ref"]
            for curve in curves
            if curve.get("evidence_ref") is not None
        }
        readable = [
            presentation.readable_record(
                item,
                record,
                available_refs,
                scope=relationships.record_scope(self.links, item, record),
                sample_labels=self.sample_labels,
            )
            for record in item.details["records"]
        ]
        positions = {record["ref"]: index for index, record in enumerate(readable)}
        for curve in curves:
            curve_record = presentation.readable_curve(item, curve, available_refs)
            ref = curve_record["ref"]
            if ref not in positions:
                positions[ref] = len(readable)
                readable.append(curve_record)
                continue
            index = positions[ref]
            existing = readable[index]
            curve_record["sample_ids"] = sorted(
                set(existing["sample_ids"]) | set(curve_record["sample_ids"])
            )
            curve_record["candidate_ids"] = sorted(
                set(existing["candidate_ids"]) | set(curve_record["candidate_ids"])
            )
            curve_record["fields"] = presentation.dedupe_entries(
                existing["fields"] + curve_record["fields"]
            )
            curve_record["links"] = presentation.dedupe_entries(
                existing["links"] + curve_record["links"]
            )
            curve_record["technical_refs"] = presentation.dedupe_entries(
                existing["technical_refs"] + curve_record["technical_refs"]
            )
            readable[index] = curve_record
        related_refs = relationships.related_record_refs(
            self.links, item, curves, (record["ref"] for record in readable)
        )
        for record in readable:
            if record["ref"] in related_refs:
                record["links"] = presentation.dedupe_entries(
                    record["links"]
                    + [
                        presentation.related_record_link(kind, ref)
                        for kind, ref in related_refs[record["ref"]]
                    ]
                )
        return readable

    def _curve(self, payload):
        curves = payload.get("curves", {})
        integration = payload.get("integration") or {}
        rts = curves.get("rt", [])
        result = {
            "sample_id": payload.get("sample_id"),
            "candidate_id": payload.get("candidate_id"),
            "evidence_ref": payload.get("payload_id", payload.get("physical_peak_key")),
            "observation_id": payload.get("physical_peak_key"),
            "kind": payload.get("kind"),
            "stage_label": presentation.curve_stage_label(payload.get("kind")),
            "raw_rts": rts,
            "raw_intensities": curves.get("raw", []),
            "processed_curves": [
                {"kind": kind, "rts": rts, "intensities": values}
                for kind, values in curves.items()
                if kind in ("baseline", "smoothed", "positive") and values is not None
            ],
            "bound_left_rt": integration.get("rt_left_min"),
            "bound_right_rt": integration.get("rt_right_min"),
            "apex_rt": integration.get("rt_apex_min", integration.get("apex_rt_min")),
            "area": integration.get("area_raw_counts_seconds"),
            "integration": integration,
            "provenance": payload.get("provenance"),
        }
        align_curve(
            result,
            self.sample_labels.get(payload.get("sample_id")),
            self.rt_calibration,
        )
        return result
