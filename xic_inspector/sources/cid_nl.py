"""Load and scope retained CID-NL evidence for the shared Inspector.

The scientific reader owns payload validation. This adapter owns record kinds,
exact link projection, scoped status interpretation and curve projection;
cid_nl_presentation assembles the selected data's labels and readable records.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from xic_extractor.alignment.drift_evidence import interpolate_calibrated_rt_min
from xic_extractor.diagnostics.alignment_explanation import (
    ExplanationError,
    ExplanationMetadata,
    open_review_cells,
    read_cid_nl_curves,
    read_cid_nl_explanation,
    read_explanation_metadata,
    read_explanation_selection,
    read_review_row_samples,
)
from xic_extractor.diagnostics.explanation_query import recorded_observations
from xic_extractor.diagnostics.explanation_review import (
    ATTENTION,
    EXCLUDED,
    NULL_CELL,
    OBSERVED,
    ORIGIN_FLAGS,
    REVIEW_SCHEMA_VERSION,
    ZERO_CELL,
    selection_sample_facts,
)
from xic_extractor.diagnostics.explanation_review import (
    attention_reason as _attention_reason,
)
from xic_inspector.selection.canonical import (
    CanonicalItem,
    Witness,
    build_canonical_ref,
)
from xic_inspector.selection.strata import classify_scoped_stratum
from xic_inspector.sources import cid_nl_presentation as presentation


def read_summary(path: str | Path) -> dict[str, Any]:
    """Read compact metadata without retrieving curve payloads."""
    return read_cid_nl_explanation(path)


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
            not from_selection
            and self.summary["schema_version"] == REVIEW_SCHEMA_VERSION
        )
        self._query_only = self._compact_selection and query_only
        self._row_samples = (
            read_review_row_samples(path, self.summary) if self._query_only else {}
        )
        self._selection_cells: Callable[[str], dict[str, dict[str, Any]]] | None = None
        self.request_id = self.summary.get("processing_request_id", "")
        self.attempt_id = self.summary.get("processing_attempt_id", "")
        self.records = metadata.records
        self.candidates = metadata.candidates
        self.links: dict[tuple[str, str], set[tuple[str, str]]] = {}
        self.record_links: dict[tuple[str, str], set[tuple[str, str]]] = {}
        self.initial_edge_refs_by_member: dict[str, set[str]] = {}
        self.sample_labels: dict[str, str] = {}
        self.row_cell_decisions: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self.related_items: dict[str, list[tuple[CanonicalItem, tuple[str, ...]]]] = {}
        self.calibration_by_sample_stem: dict[str, tuple[tuple[float, float], ...]] = {}

        for sample in self.summary.get("samples", []):
            sid = sample.get("sample_key")
            sample_stem = sample.get("sample_stem")
            if isinstance(sid, str) and isinstance(sample_stem, str):
                self.sample_labels[sid] = sample_stem
        self.calibration_by_sample_stem = self._read_calibration(self.summary)
        for (_, sid), candidate in self.candidates.items():
            sample_stem = candidate.get("sample_stem")
            if isinstance(sample_stem, str) and sample_stem:
                self.sample_labels.setdefault(sid, sample_stem)
        for edge_ref, edge in self.records.get("initial_edges", {}).items():
            for field in ("left_member_subject_id", "right_member_subject_id"):
                member_id = edge.get(field)
                if isinstance(member_id, str):
                    self.initial_edge_refs_by_member.setdefault(member_id, set()).add(
                        edge_ref
                    )
        for rid, fact in self.records.get("cell_decisions", {}).items():
            group_id = fact.get("group_hypothesis_id")
            row_key_id = fact.get("row_key_id")
            if isinstance(group_id, str) and isinstance(row_key_id, str):
                self.row_cell_decisions.setdefault((group_id, row_key_id), {})[rid] = (
                    fact
                )
        for cid, sid, kind, rid in metadata.links:
            key = cid, sid
            record = kind, rid
            self.links.setdefault(key, set()).add(record)
            self.record_links.setdefault(record, set()).add(key)

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
            gid = payload.get("group_hypothesis_id")
            row_groups.add(gid)
            keys = self._linked("rows", rid)
            grouped.update(keys)
            item = build_item("row", rid, gid, keys, sample_ids, payload)
            items.append(item)
            item_keys[item.serialized_ref] = keys
        for gid, payload in self.records.get("final_groups", {}).items():
            keys = self._linked("final_groups", gid)
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
        item_by_ref = {item.serialized_ref: item for item in items}
        edge_links = {
            edge_ref: self.record_links.get(("initial_edges", edge_ref), set())
            for edge_ref in self.records.get("initial_edges", {})
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
                    peer_edges[item_ref].setdefault(peer_ref, set()).add(edge_ref)
        for item in items:
            peers = peer_edges[item.serialized_ref]
            ranked = sorted(
                peers.items(),
                key=lambda entry: (
                    min(self._edge_priority(ref) for ref in entry[1]),
                    min(entry[1]),
                    entry[0],
                ),
            )
            self.related_items[item.serialized_ref] = [
                (item_by_ref[peer_ref], tuple(sorted(refs)))
                for peer_ref, refs in ranked
            ]
            item.details["review_summary"]["related_group_count"] = len(ranked)
        return sorted(items, key=lambda item: item.sort_key)

    @staticmethod
    def _read_calibration(summary) -> dict[str, tuple[tuple[float, float], ...]]:
        reference = summary.get("known_istd_reference")
        if reference is None:
            return {}
        if not isinstance(reference, dict):
            raise ExplanationError("known_istd_reference must be an object")
        calibration = reference.get("calibration")
        if calibration is None:
            return {}
        if not isinstance(calibration, dict):
            raise ExplanationError("known_istd_reference.calibration must be an object")
        if calibration.get("schema_version") not in {
            "known_istd_calibration",
            "known_istd_calibration_duration_search",
        }:
            raise ExplanationError("Invalid known ISTD calibration schema_version")
        expected = {
            "rt_unit": "minute",
            "interpolation": "piecewise_linear",
            "extrapolation": "unavailable",
        }
        for field, value in expected.items():
            if calibration.get(field) != value:
                actual = calibration.get(field)
                raise ExplanationError(
                    f"Invalid known ISTD calibration {field}: {actual!r}"
                )
        samples = calibration.get("samples")
        if not isinstance(samples, list):
            raise ExplanationError("known ISTD calibration samples must be a list")
        result = {}
        for sample in samples:
            if not isinstance(sample, dict):
                raise ExplanationError(
                    "known ISTD calibration sample must be an object"
                )
            stem = sample.get("sample_stem")
            status = sample.get("status")
            if not isinstance(stem, str) or not stem or stem in result:
                raise ExplanationError("known ISTD calibration sample_stem is invalid")
            if status == "unavailable":
                continue
            if status != "available":
                raise ExplanationError(
                    "known ISTD calibration sample status is invalid"
                )
            raw_knots = sample.get("knots")
            domain = sample.get("domain_rt_min")
            if not isinstance(raw_knots, list) or len(raw_knots) < 2:
                raise ExplanationError(
                    "available known ISTD calibration needs two knots"
                )
            knots = []
            for knot in raw_knots:
                if not isinstance(knot, list) or len(knot) != 2:
                    raise ExplanationError(
                        "known ISTD calibration knot must be an RT pair"
                    )
                x, y = knot
                if (
                    isinstance(x, bool)
                    or isinstance(y, bool)
                    or not isinstance(x, (int, float))
                    or not isinstance(y, (int, float))
                    or not math.isfinite(x)
                    or not math.isfinite(y)
                ):
                    raise ExplanationError(
                        "known ISTD calibration knots must be finite"
                    )
                knots.append((float(x), float(y)))
            if any(
                a[0] >= b[0] or a[1] > b[1]
                for a, b in zip(knots, knots[1:], strict=False)
            ):
                raise ExplanationError("known ISTD calibration knots must be monotone")
            if domain != [knots[0][0], knots[-1][0]]:
                raise ExplanationError(
                    "known ISTD calibration domain does not match knots"
                )
            result[stem] = tuple(knots)
        return result

    def _edge_priority(self, edge_ref: str) -> int:
        edge = self.records.get("initial_edges", {}).get(edge_ref, {})
        evidence = edge.get("initial_evidence", {})
        decision = evidence.get("decision")
        return 0 if decision in {"blocked_edge", "split_edge", "split"} else 1

    def _linked(self, kind: str, rid: str) -> set[tuple[str, str]]:
        return set(self.record_links.get((kind, rid), ()))

    def _witnesses(self, kind, identifier, gid, scoped) -> list[Witness]:
        """Candidate/observation coordinates shared by selection and presentation."""
        group = self.records.get("final_groups", {}).get(gid, {})
        members_by_sample: dict[str, set[str]] = {}
        for member in group.get("member_subjects", []):
            members_by_sample.setdefault(member.get("sample_key"), set()).update(
                member.get("physical_observation_ids", [])
            )
        witnesses: list[Witness] = []
        for cid, sid in scoped:
            candidate = self.candidates[cid, sid]
            observations = recorded_observations(
                candidate,
                self.links.get((cid, sid), ()),
                self.records,
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
                    observation_id=observation.get("peak_observation_content_id"),
                    sample_stem=candidate.get("sample_stem"),
                )
                for observation in observations
            )
        return witnesses

    def _selection_item(
        self, kind, identifier, gid, keys, scope, payload
    ) -> CanonicalItem:
        """Reduce scoped facts once; never construct per-sample presentation."""
        scoped = sorted(key for key in keys if key[1] in scope)
        sample_ids = {sid for _, sid in scoped}
        witnesses = self._witnesses(kind, identifier, gid, scoped)
        if self._query_only:
            sample_ids.update(self._row_samples.get(identifier, set()) & scope)
            _, ref = build_canonical_ref(
                kind,
                self.request_id,
                self.attempt_id,
                identifier,
                sample_id=next(iter(keys))[1] if kind == "candidate" else None,
            )
            return CanonicalItem(
                kind=kind,
                request_id=self.request_id,
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
            self.request_id,
            self.attempt_id,
            identifier,
            sample_id=next(iter(keys))[1] if kind == "candidate" else None,
        )
        return CanonicalItem(
            kind=kind,
            request_id=self.request_id,
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
        facts: dict[tuple[str, str], dict[str, Any]] = {}
        fact_samples: dict[tuple[str, str], set[str]] = {}
        cell_bound_samples: set[str] = set()
        witnesses = self._witnesses(kind, identifier, gid, scoped)
        for cid, sid in scoped:
            linked = self.links.get((cid, sid), set())
            for record_kind, rid in linked:
                fact = self.records.get(record_kind, {}).get(rid, {})
                fact_sid = fact.get("sample_key", fact.get("sample_id"))
                if fact_sid is not None and fact_sid != sid:
                    continue
                if fact.get("group_hypothesis_id") not in (None, gid):
                    continue
                if kind == "row" and fact.get("row_key_id") not in (None, identifier):
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
                    observation.get("group_hypothesis_id") != gid
                    or observation_sid != sid
                    or observation.get("row_key_id") not in (None, identifier)
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
        excluded = any(
            self.candidates[key].get("discovery_candidate_state") == "excluded"
            for key in scoped
        )
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
                self.candidates[key].get("discovery_candidate_state") == "excluded"
                for key in scoped
                if key[1] == sid
            ) or any(
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
            self.request_id,
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
            member.get("member_subject_id")
            for initial_ref, fact in self.records.get("initial_groups", {}).items()
            if any(
                ("initial_groups", initial_ref) in self.links.get(key, set())
                for key in scoped
            )
            for member in fact.get("member_subjects", [])
            if isinstance(member, dict)
            and isinstance(member.get("member_subject_id"), str)
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
            request_id=self.request_id,
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
            for observation_id in member.get("physical_observation_ids", [])
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
                    and content.get("group_hypothesis_id") == group_id
                    and isinstance(content.get("peak_observation_content_id"), str)
                ):
                    observation_refs.add(content["peak_observation_content_id"])
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
                        "reacquired_support",
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
                and fact.get("peak_observation_content_id") in observation_set
            ):
                captures.append((ref, fact))
            elif (
                record_kind == "method_assessments"
                and fact.get("method_result") == "SUPPORT"
            ):
                peak_ids = fact.get("candidate_peak_content_ids", [])
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
        elif "reacquired_capture" in capture_kinds and methods:
            kind = "reacquired_support"
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
                    read_cid_nl_curves(
                        self.path, sample_ids=(sid,), record_ids=tuple(sorted(refs))
                    )
                )
            present = {
                (curve.get("sample_id"), curve.get("peak_observation_content_id"))
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
                for name, expected in (
                    ("processing_request_id", self.request_id),
                    ("processing_attempt_id", self.attempt_id),
                ):
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
            pair_support = self._internal_pair_support(item)
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
                    relationship_members=self._relationship_members(item),
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

    def _relationship_members(self, item: CanonicalItem) -> list[dict[str, Any]]:
        group_id = item.details["source"].get("group_hypothesis_id")
        group = self.records.get("final_groups", {}).get(group_id, {})
        return [
            {
                "member_subject_id": member["member_subject_id"],
                "sample_id": member.get("sample_key"),
                "sample_label": self._member_sample_label(
                    member["member_subject_id"], member
                ),
            }
            for member in group.get("member_subjects", [])
            if isinstance(member, dict)
            and isinstance(member.get("member_subject_id"), str)
        ]

    def _internal_pair_support(self, item: CanonicalItem) -> list[dict[str, Any]]:
        source = item.details["source"]
        group_id = source.get("group_hypothesis_id")
        group = self.records.get("final_groups", {}).get(group_id, {})
        members = group.get("member_subjects")
        if not isinstance(members, list):
            return []
        members_by_id = {
            member["member_subject_id"]: member
            for member in members
            if isinstance(member, dict)
            and isinstance(member.get("member_subject_id"), str)
        }
        edge_refs = {
            edge_ref
            for member_id in members_by_id
            for edge_ref in self.initial_edge_refs_by_member.get(member_id, ())
        }
        projected = []
        for edge_ref in sorted(edge_refs):
            edge = self.records.get("initial_edges", {}).get(edge_ref, {})
            left_id = edge.get("left_member_subject_id")
            right_id = edge.get("right_member_subject_id")
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
                    "left_member_subject_id": left_id,
                    "right_member_subject_id": right_id,
                    "left_sample_label": self._member_sample_label(
                        left_id, members_by_id[left_id]
                    ),
                    "right_sample_label": self._member_sample_label(
                        right_id, members_by_id[right_id]
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

    def _member_sample_label(self, member_id: str, member: dict[str, Any]) -> str:
        sample_id = member.get("sample_key")
        if isinstance(sample_id, str) and sample_id in self.sample_labels:
            return self.sample_labels[sample_id]
        for field in ("sample_label", "sample_stem", "label"):
            label = member.get(field)
            if isinstance(label, str) and label:
                return label
        return sample_id if isinstance(sample_id, str) and sample_id else member_id

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
                (curve.get("peak_observation_content_id"), curve.get("sample_id"))
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
                            and witness.observation_id
                            == curve.get("peak_observation_content_id")
                            for witness in item.witnesses
                            if witness.observation_id
                        )
                    )
                }
            )
            projected_curves.append(projected)
        return projected_curves

    def _decision_refs(self, item: CanonicalItem, sample_id: str) -> list[str]:
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
                    for _candidate_id, sid in self._record_link_keys(item, record)
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
                item, record, available_refs, scope=self._record_scope(item, record)
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
        self._add_related_links(item, curves, readable)
        return readable

    def _add_related_links(
        self,
        item: CanonicalItem,
        curves: list[dict[str, Any]],
        readable: list[dict[str, Any]],
    ) -> None:
        records_by_ref = {record["ref"]: record for record in item.details["records"]}
        targets = [
            (record, self._record_link_keys(item, record))
            for record in item.details["records"]
            if record["kind"] in {"method_assessments", "initial_edges"}
        ]
        curves_by_ref = {
            curve["evidence_ref"]: curve
            for curve in curves
            if curve.get("evidence_ref") is not None
        }
        for readable_record in readable:
            ref = readable_record["ref"]
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
                source_keys = self._record_link_keys(item, record)
            else:
                continue
            related = []
            for target, target_keys in targets:
                if not source_keys.intersection(target_keys):
                    continue
                related.append(
                    presentation.related_record_link(target["kind"], target["ref"])
                )
            readable_record["links"] = presentation.dedupe_entries(
                readable_record["links"] + related
            )

    def _record_scope(
        self, item: CanonicalItem, record: dict[str, Any]
    ) -> tuple[list[str], list[str]]:
        item_keys = self._item_keys(item)
        linked_keys = self._record_link_keys(item, record)
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

    @staticmethod
    def _item_keys(item: CanonicalItem) -> set[tuple[str, str]]:
        return {
            (witness.candidate_id, witness.sample_key) for witness in item.witnesses
        }

    def _record_link_keys(
        self, item: CanonicalItem, record: dict[str, Any]
    ) -> set[tuple[str, str]]:
        linked_keys = {
            key
            for key in self._item_keys(item)
            if (record["kind"], record["ref"]) in self.links.get(key, set())
        }
        recorded_sample = record["facts"].get(
            "sample_key", record["facts"].get("sample_id")
        )
        if recorded_sample is not None:
            linked_keys = {key for key in linked_keys if key[1] == recorded_sample}
        return linked_keys

    def _curve(self, payload):
        curves = payload.get("curves", {})
        integration = payload.get("integration") or {}
        rts = curves.get("rt", [])
        result = {
            "sample_id": payload.get("sample_id"),
            "candidate_id": payload.get("candidate_id"),
            "evidence_ref": payload.get(
                "payload_id", payload.get("peak_observation_content_id")
            ),
            "observation_id": payload.get("peak_observation_content_id"),
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
        sample_stem = self.sample_labels.get(payload.get("sample_id"))
        knots = (
            self.calibration_by_sample_stem.get(sample_stem)
            if sample_stem is not None
            else None
        )
        if knots:
            aligned_rts = [interpolate_calibrated_rt_min(value, knots) for value in rts]
            left = integration.get("rt_left_min")
            right = integration.get("rt_right_min")
            aligned_left = (
                interpolate_calibrated_rt_min(left, knots)
                if isinstance(left, (int, float))
                else None
            )
            aligned_right = (
                interpolate_calibrated_rt_min(right, knots)
                if isinstance(right, (int, float))
                else None
            )
            apex = integration.get("rt_apex_min", integration.get("apex_rt_min"))
            aligned_apex = (
                interpolate_calibrated_rt_min(apex, knots)
                if isinstance(apex, (int, float))
                else None
            )
            if (
                aligned_rts
                and all(value is not None for value in aligned_rts)
                and all(
                    value is not None
                    for value in (aligned_left, aligned_right, aligned_apex)
                )
            ):
                result.update(
                    aligned_rts=aligned_rts,
                    aligned_bound_left_rt=aligned_left,
                    aligned_bound_right_rt=aligned_right,
                    aligned_apex_rt=aligned_apex,
                )
        return result
