"""Translate retained CID-NL evidence into shared Inspector presentation data.

The scientific reader owns payload validation. This adapter owns record kinds,
exact link projection, scoped status interpretation and curve presentation.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from xic_extractor.alignment.drift_evidence import interpolate_calibrated_rt_min
from xic_extractor.diagnostics.cid_nl_explanation import (
    ExplanationError,
    read_cid_nl_curves,
    read_cid_nl_explanation,
)
from xic_inspector.selection.canonical import (
    CanonicalItem,
    Witness,
    build_canonical_ref,
)
from xic_inspector.selection.strata import classify_scoped_stratum

_RECORD_TITLES = {
    "rows": "結果列與輸出去向",
    "final_groups": "最終分組紀錄",
    "cell_decisions": "樣本定量決策",
    "terminal_subjects": "終端觀察紀錄",
    "method_subjects": "方法條件",
    "method_assessments": "方法評估紀錄",
    "initial_edges": "初始關聯紀錄",
    "exclusions": "候選排除紀錄",
    "capture_evidence": "曲線擷取關係",
}

_FIELD_LABELS = {
    "quantitative_value": "正式定量值",
    "value": "正式定量值",
    "variant": "結果類型",
    "source": "定量來源",
    "reason": "來源記錄理由",
    "disposition": "記錄狀態",
    "destination": "輸出去向",
    "method_result": "方法結果",
    "reason_codes": "方法判定理由",
    "technical_state": "技術狀態",
    "decision": "初始判定",
    "failure_reason": "未建立關聯理由",
    "score": "初始關聯分數",
    "neutral_loss_da": "中性丟失 (Da)",
    "precursor_mz": "前驅離子 m/z",
    "rt_apex_min": "峰頂 RT (min)",
    "discovery_candidate_state": "候選狀態",
    "reason_code": "排除理由",
    "kind": "擷取階段",
}

_TECHNICAL_LABELS = {
    "record_id": "所指紀錄 ID",
    "payload_id": "曲線 evidence ref",
    "sample_id": "樣本 ID",
    "canonical_sample_identity_id": "樣本 ID",
    "candidate_id": "候選 ID",
    "row_key_id": "結果列 ID",
    "group_hypothesis_id": "分組 ID",
    "peak_observation_content_id": "觀察 ID",
    "processing_request_id": "處理 request ID",
    "processing_attempt_id": "處理 attempt ID",
    "profile_id": "訊號處理 profile ID",
    "prepared_signal_id": "處理訊號 ID",
    "trace_content_id": "訊號內容 ID",
    "acquisition_source_id": "擷取來源 ID",
    "observation_artifact_id": "觀察 artifact ID",
    "method_subject_id": "方法對象 ID",
    "full_trace_sha256": "完整訊號 SHA-256",
    "selection_trace_sha256": "選取訊號 SHA-256",
    "matrix_path": "Matrix 路徑",
}

_STAGE_LABELS = {
    "discovery_physical": "探索階段曲線",
    "initial_capture": "首次擷取曲線",
    "reacquired_capture": "再次擷取曲線",
    "recovery_observation": "方法補取曲線",
}

_REASON_LABELS = {
    "insufficient_method_evidence": "方法證據不足",
    "no_unique_qualifying_relation": "沒有唯一合格關係",
    "unique_support_within_declared_candidate_universe": (
        "在已宣告候選範圍內具有唯一支持"
    ),
    "product_mz_out_of_tolerance": "產品離子 m/z 超出容許範圍",
    "discovery_candidate_not_search_eligible": "Discovery 候選未取得後續搜尋資格",
}

_RESULT_VARIANT_LABELS = {
    "not_searched": "本次未搜尋",
    "rejected_observation": "觀察未被採用",
    "unavailable": "沒有可用觀察",
    "unresolved": "決策尚未解決",
}


def read_summary(path: str | Path) -> dict[str, Any]:
    """Read compact metadata without retrieving curve payloads."""
    return read_cid_nl_explanation(path)


class CidNlSource:
    """One report's metadata projection and selected evidence retrieval."""

    def __init__(self, path: Path, request_id: str, attempt_id: str) -> None:
        self.path = path
        self.request_id = request_id
        self.attempt_id = attempt_id
        self.records: dict[str, dict[str, dict[str, Any]]] = {}
        self.candidates: dict[tuple[str, str], dict[str, Any]] = {}
        self.links: dict[tuple[str, str], set[tuple[str, str]]] = {}
        self.record_links: dict[tuple[str, str], set[tuple[str, str]]] = {}
        self.initial_edge_refs_by_member: dict[str, set[str]] = {}
        self.sample_labels: dict[str, str] = {}
        self.row_cell_decisions: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self.related_items: dict[str, list[tuple[CanonicalItem, tuple[str, ...]]]] = {}
        self.calibration_by_sample_stem: dict[str, tuple[tuple[float, float], ...]] = {}

    def load_items(self, sample_ids: set[str]) -> list[CanonicalItem]:
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro", uri=True
        )
        try:
            connection.execute("PRAGMA query_only = ON")
            summary_row = connection.execute(
                "SELECT payload FROM metadata WHERE key='summary'"
            ).fetchone()
            if summary_row is not None:
                summary = json.loads(summary_row[0])
                for sample in summary.get("samples", []):
                    sid = sample.get("canonical_sample_identity_id")
                    sample_stem = sample.get("sample_stem")
                    if isinstance(sid, str) and isinstance(sample_stem, str):
                        self.sample_labels[sid] = sample_stem
                self.calibration_by_sample_stem = self._read_calibration(summary)
            for cid, sid, payload in connection.execute(
                "SELECT candidate_id, sample_id, payload FROM candidates"
            ):
                self.candidates[cid, sid] = json.loads(payload)
                sample_stem = self.candidates[cid, sid].get("sample_stem")
                if isinstance(sample_stem, str) and sample_stem:
                    self.sample_labels.setdefault(sid, sample_stem)
            for kind, rid, payload in connection.execute(
                "SELECT kind, record_id, payload FROM records"
            ):
                self.records.setdefault(kind, {})[rid] = json.loads(payload)
            for edge_ref, edge in self.records.get("initial_edges", {}).items():
                for field in ("left_member_subject_id", "right_member_subject_id"):
                    member_id = edge.get(field)
                    if isinstance(member_id, str):
                        refs = self.initial_edge_refs_by_member.setdefault(
                            member_id, set()
                        )
                        refs.add(edge_ref)
            for rid, fact in self.records.get("cell_decisions", {}).items():
                group_id = fact.get("group_hypothesis_id")
                row_key_id = fact.get("row_key_id")
                if isinstance(group_id, str) and isinstance(row_key_id, str):
                    self.row_cell_decisions.setdefault((group_id, row_key_id), {})[
                        rid
                    ] = fact
            for cid, sid, kind, rid in connection.execute(
                "SELECT candidate_id, sample_id, kind, record_id FROM links"
            ):
                key = cid, sid
                record = kind, rid
                self.links.setdefault(key, set()).add(record)
                self.record_links.setdefault(record, set()).add(key)
        except (sqlite3.Error, json.JSONDecodeError, TypeError) as exc:
            raise ExplanationError(f"Invalid CID-NL metadata: {exc}") from exc
        finally:
            connection.close()

        items = []
        item_keys: dict[str, set[tuple[str, str]]] = {}
        row_groups = set()
        grouped = set()
        # Construct the run population before applying sample scope.
        for rid, payload in self.records.get("rows", {}).items():
            gid = payload.get("group_hypothesis_id")
            row_groups.add(gid)
            keys = self._linked("rows", rid)
            grouped.update(keys)
            item = self._item("row", rid, gid, keys, sample_ids, payload)
            items.append(item)
            item_keys[item.serialized_ref] = keys
        for gid, payload in self.records.get("final_groups", {}).items():
            keys = self._linked("final_groups", gid)
            grouped.update(keys)
            if gid not in row_groups:
                item = self._item("group", gid, gid, keys, sample_ids, payload)
                items.append(item)
                item_keys[item.serialized_ref] = keys
        for key, payload in self.candidates.items():
            if key not in grouped:
                item = self._item("candidate", key[0], None, {key}, sample_ids, payload)
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
        expected = {
            "schema_version": "known_istd_calibration_v1",
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
            if any(a[0] >= b[0] or a[1] > b[1] for a, b in zip(knots, knots[1:])):
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

    def _item(self, kind, identifier, gid, keys, scope, payload) -> CanonicalItem:
        scoped = sorted(key for key in keys if key[1] in scope)
        facts: dict[tuple[str, str], dict[str, Any]] = {}
        fact_samples: dict[tuple[str, str], set[str]] = {}
        cell_bound_samples: set[str] = set()
        witnesses = []
        for cid, sid in scoped:
            candidate = self.candidates[cid, sid]
            linked = self.links.get((cid, sid), set())
            for record_kind, rid in linked:
                fact = self.records.get(record_kind, {}).get(rid, {})
                fact_sid = fact.get(
                    "canonical_sample_identity_id", fact.get("sample_id")
                )
                if fact_sid is not None and fact_sid != sid:
                    continue
                if fact.get("group_hypothesis_id") not in (None, gid):
                    continue
                if kind == "row" and fact.get("row_key_id") not in (None, identifier):
                    continue
                facts[record_kind, rid] = fact
                fact_samples.setdefault((record_kind, rid), set()).add(sid)
            terminals = [
                self.records[k][r] for k, r in linked if k == "terminal_subjects"
            ]
            observations = [
                o for terminal in terminals for o in terminal.get("observations", [])
            ]
            group = self.records.get("final_groups", {}).get(gid, {})
            member_observations = {
                oid
                for member in group.get("member_subjects", [])
                if member.get("canonical_sample_identity_id") == sid
                for oid in member.get("physical_observation_ids", [])
            }
            if member_observations:
                observations = [
                    o
                    for o in observations
                    if o.get("peak_observation_content_id") in member_observations
                ]
            if not observations:
                observations = [{"rt_apex_min": candidate.get("rt_apex_min")}]
            for observation in observations:
                witnesses.append(
                    Witness(
                        canonical_sample_identity_id=sid,
                        candidate_id=cid,
                        group_hypothesis_id=gid,
                        row_key_id=identifier if kind == "row" else None,
                        precursor_mz=candidate.get("precursor_mz"),
                        rt_apex_min=observation.get("rt_apex_min"),
                        observation_id=observation.get("peak_observation_content_id"),
                        sample_stem=candidate.get("sample_stem"),
                    )
                )
        if kind == "row" and isinstance(gid, str):
            indexed_cells = self.row_cell_decisions.get((gid, identifier), {})
            for rid, fact in sorted(indexed_cells.items()):
                sid = fact.get("canonical_sample_identity_id", fact.get("sample_id"))
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
                    "canonical_sample_identity_id", observation.get("sample_id")
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
            reason = None
            if record_kind == "cell_decisions":
                value = fact.get("value", fact.get("quantitative_value"))
                cells.append({**fact, "quantitative_value": value})
                if fact.get("variant") == "unresolved" and value is None:
                    reason = "unresolved"
            elif (
                record_kind == "method_assessments"
                and fact.get("method_result") == "HOLD"
            ):
                reason = "HOLD"
            elif record_kind == "initial_edges":
                evidence = fact.get("initial_evidence", {})
                failure = evidence.get("failure", evidence.get("reason"))
                if failure in ("ambiguous_owner", "identity_conflict"):
                    reason = failure
                elif fact.get("disposition") == "review_only":
                    reason = "review_only"
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
                c
                for c in cells
                if c.get("canonical_sample_identity_id", c.get("sample_id")) == sid
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
            if witness.canonical_sample_identity_id == sample_id
            and witness.observation_id
        }
        observation_refs.update(
            observation_id
            for member in group.get("member_subjects", [])
            if member.get("canonical_sample_identity_id") == sample_id
            for observation_id in member.get("physical_observation_ids", [])
            if isinstance(observation_id, str)
        )
        for (record_kind, _), fact in facts.items():
            if record_kind != "cell_observations":
                continue
            for predecessor in fact.get("predecessor_cells", []):
                content = predecessor.get("content", {})
                if (
                    content.get("canonical_sample_identity_id") == sample_id
                    and content.get("group_hypothesis_id") == group_id
                    and isinstance(content.get("peak_observation_content_id"), str)
                ):
                    observation_refs.add(content["peak_observation_content_id"])
        return sorted(observation_refs)

    @staticmethod
    def _support_origin(sample_id, observation_refs, facts):
        observation_set = set(observation_refs)
        captures = []
        methods = []
        for (record_kind, ref), fact in facts.items():
            fact_sid = fact.get("canonical_sample_identity_id", fact.get("sample_id"))
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
            "recovery_observation" in capture_kinds
            and "untargeted-cid-nl-backfill-recovery:v1" in invocations
        ):
            kind, label = "recovery_support", "方法補取支持"
        elif "reacquired_capture" in capture_kinds and methods:
            kind, label = "reacquired_support", "重新擷取支持"
        elif "initial_capture" in capture_kinds and any(
            isinstance(value, str) and value.startswith("untargeted-cid-nl-real-data:")
            for value in invocations
        ):
            kind, label = "discovery_support", "初始探索支持"
        else:
            kind, label = "unavailable", "來源未提供"
        return {
            "kind": kind,
            "label": label,
            "method_refs": sorted(ref for ref, _ in methods),
            "capture_refs": sorted(ref for ref, _ in captures),
            "observation_refs": observation_refs,
        }

    def load_render_items(
        self, items, *, capability: str, check_cancel: Callable[[], None]
    ):
        primary_items = list(items)
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
                refs = refs_by_sample.setdefault(
                    witness.canonical_sample_identity_id, set()
                )
                if witness.observation_id:
                    refs.add(witness.observation_id)
                elif item.kind == "candidate":
                    refs.add(witness.candidate_id)
        raw_curves = []
        if capability == "retained_curves_v1":
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
                (w.canonical_sample_identity_id, w.observation_id)
                for item in items
                for w in item.witnesses
                if w.observation_id
            }
            missing = promised - present
            if missing:
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
            samples = []
            for sid in item.sample_ids:
                cells = [
                    c
                    for c in item.details["cells"]
                    if c.get("canonical_sample_identity_id", c.get("sample_id")) == sid
                ]
                values = [c["quantitative_value"] for c in cells]
                samples.append(
                    {
                        "sample_id": sid,
                        "sample_stem": next(
                            (
                                w.sample_stem
                                for w in item.witnesses
                                if w.canonical_sample_identity_id == sid
                                and w.sample_stem
                            ),
                            self.sample_labels.get(sid, sid),
                        ),
                        "status_kind": item.details["sample_status"][sid],
                        "support_origin": item.details["support_origins"][sid],
                        "status_label": {
                            "attention": "需留意",
                            "excluded": "已排除",
                            "null": "空值",
                            "zero": "真實零值",
                            "rest": "有定量值" if len(values) == 1 else "有結果紀錄",
                        }.get(item.details["sample_status"][sid], "未提供定量決策"),
                        **(
                            {"quantitative_value": values[0]}
                            if len(values) == 1
                            else {}
                        ),
                        "decision_reason": self._decision_reason(cells),
                        "decision_label": self._decision_label(cells),
                        "decision_refs": self._decision_refs(item, sid),
                        "evidence_status": "available"
                        if any(
                            curve.get("sample_id") == sid and curve.get("raw_rts")
                            for curve in projected_curves
                        )
                        else "unavailable",
                        "refs": cells,
                    }
                )
            source = item.details["source"]
            route = source.get("route", {})
            reason = route.get(
                "reason", source.get("reason", source.get("disposition", "unavailable"))
            )
            internal_pair_support = self._internal_pair_support(item)
            rendered.append(
                {
                    "id": item.serialized_ref,
                    "kind": item.kind,
                    "stratum": item.stratum,
                    "badge": item.stratum,
                    "precursor_mz": item.witnesses[0].precursor_mz
                    if item.witnesses
                    else None,
                    "rt_apex_min": item.witnesses[0].rt_apex_min
                    if item.witnesses
                    else None,
                    "reason": reason,
                    "route": route,
                    **self._item_labels(item, samples, route),
                    "samples": samples,
                    "witnesses": [
                        {**asdict(w), "matched": w in item.matched_witnesses}
                        for w in item.witnesses
                    ],
                    "matched_refs": [asdict(w) for w in item.matched_witnesses],
                    "details": {
                        **item.details,
                        "readable_records": self._readable_records(
                            item, projected_curves
                        ),
                    },
                    "review_summary": item.details["review_summary"],
                    **(
                        {"internal_pair_support": internal_pair_support}
                        if internal_pair_support
                        else {}
                    ),
                    "related_groups": [
                        self._related_group(
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
                    "curves": projected_curves,
                }
            )
        return rendered

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
                    "rt_raw_delta_sec": evidence.get("rt_raw_delta_sec"),
                    "rt_drift_corrected_delta_sec": evidence.get(
                        "rt_drift_corrected_delta_sec"
                    ),
                }
            )
        return projected

    def _member_sample_label(self, member_id: str, member: dict[str, Any]) -> str:
        sample_id = member.get("canonical_sample_identity_id")
        if isinstance(sample_id, str) and sample_id in self.sample_labels:
            return self.sample_labels[sample_id]
        for field in ("sample_label", "sample_stem", "label"):
            label = member.get(field)
            if isinstance(label, str) and label:
                return label
        return sample_id if isinstance(sample_id, str) and sample_id else member_id

    def _project_curves(self, item, raw_curves, captures):
        keys = {
            (w.candidate_id, w.canonical_sample_identity_id) for w in item.witnesses
        }
        observation_keys = {
            (w.observation_id, w.canonical_sample_identity_id)
            for w in item.witnesses
            if w.observation_id
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
                            and witness.canonical_sample_identity_id == sid
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

    @staticmethod
    def _related_group(item, edge_refs, curves):
        source = item.details["source"]
        group_id = source.get("group_hypothesis_id")
        row_key_id = source.get("row_key_id") if item.kind == "row" else None
        route = source.get("route", {})
        reason = route.get(
            "reason", source.get("reason", source.get("disposition", "unavailable"))
        )
        return {
            "id": item.serialized_ref,
            "label": f"相關分組 {group_id or item.identifier}",
            "group_id": group_id or item.identifier,
            "row_key_id": row_key_id,
            "edge_refs": list(edge_refs),
            "reason": reason,
            "sample_ids": list(item.sample_ids),
            "witnesses": [asdict(witness) for witness in item.witnesses],
            "curves": curves,
        }

    def _decision_refs(self, item: CanonicalItem, sample_id: str) -> list[str]:
        item_keys = {
            (witness.candidate_id, witness.canonical_sample_identity_id)
            for witness in item.witnesses
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
            recorded_sample = facts.get(
                "canonical_sample_identity_id", facts.get("sample_id")
            )
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

    @staticmethod
    def _decision_reason(cells: list[dict[str, Any]]) -> str:
        if not cells:
            return "來源未記錄此樣本的定量決策。"
        if len(cells) != 1:
            return f"來源保留 {len(cells)} 筆定量決策，未記錄單一理由。"
        cell = cells[0]
        reason = cell.get("decision_reason", cell.get("reason"))
        if reason not in (None, ""):
            return f"來源記錄理由：{reason}"
        source = cell.get("source")
        if source == "trapezoid_counts_seconds_on_canonical_signal_v1":
            return "來源記錄定量方式為當次採用訊號的梯形積分；未另記理由。"
        if source not in (None, ""):
            return f"來源記錄定量方式為「{source}」；未另記理由。"
        variant = cell.get("variant")
        if isinstance(variant, str) and variant:
            label = _RESULT_VARIANT_LABELS.get(variant)
            if label:
                return f"來源記錄結果類型為「{label}」（{variant}）；定量值為空。"
            return f"來源記錄結果類型為「{variant}」；未另記理由。"
        return "來源未記錄定量理由。"

    def _readable_records(
        self, item: CanonicalItem, curves: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        available_refs = {record["ref"] for record in item.details["records"]} | {
            curve["evidence_ref"]
            for curve in curves
            if curve.get("evidence_ref") is not None
        }
        readable = [
            self._readable_record(item, record, available_refs)
            for record in item.details["records"]
        ]
        positions = {record["ref"]: index for index, record in enumerate(readable)}
        for curve in curves:
            curve_record = self._readable_curve(item, curve, available_refs)
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
            curve_record["fields"] = self._dedupe_entries(
                existing["fields"] + curve_record["fields"]
            )
            curve_record["links"] = self._dedupe_entries(
                existing["links"] + curve_record["links"]
            )
            curve_record["technical_refs"] = self._dedupe_entries(
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
        for presentation in readable:
            ref = presentation["ref"]
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
                label = (
                    "查看相關方法判定（不代表正式定量採用）"
                    if target["kind"] == "method_assessments"
                    else "查看相關初始配對（不代表正式定量採用）"
                )
                related.append({"ref": target["ref"], "label": label})
            presentation["links"] = self._dedupe_entries(
                presentation["links"] + related
            )

    def _readable_record(
        self,
        item: CanonicalItem,
        record: dict[str, Any],
        available_refs: set[str],
    ) -> dict[str, Any]:
        kind = record["kind"]
        facts = record["facts"]
        sample_ids, candidate_ids = self._record_scope(item, record)
        title = _RECORD_TITLES.get(kind, f"{kind} 來源紀錄")
        return {
            "ref": record["ref"],
            "title": title,
            "summary": self._record_summary(
                item, kind, facts, sample_ids, candidate_ids, title
            ),
            "sample_ids": sample_ids,
            "candidate_ids": candidate_ids,
            "fields": self._record_fields(facts),
            "links": self._record_links(facts, record["ref"], available_refs),
            "technical_refs": self._technical_refs(
                facts,
                first={"label": "來源紀錄 ID", "value": record["ref"]},
            ),
        }

    def _record_scope(
        self, item: CanonicalItem, record: dict[str, Any]
    ) -> tuple[list[str], list[str]]:
        item_keys = self._item_keys(item)
        linked_keys = self._record_link_keys(item, record)
        facts = record["facts"]
        recorded_sample = facts.get(
            "canonical_sample_identity_id", facts.get("sample_id")
        )
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
            (witness.candidate_id, witness.canonical_sample_identity_id)
            for witness in item.witnesses
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
            "canonical_sample_identity_id", record["facts"].get("sample_id")
        )
        if recorded_sample is not None:
            linked_keys = {key for key in linked_keys if key[1] == recorded_sample}
        return linked_keys

    @staticmethod
    def _sample_name(item: CanonicalItem, sample_id: str) -> str:
        return next(
            (
                witness.sample_stem
                for witness in item.witnesses
                if witness.canonical_sample_identity_id == sample_id
                and witness.sample_stem
            ),
            sample_id,
        )

    def _record_summary(
        self,
        item: CanonicalItem,
        kind: str,
        facts: dict[str, Any],
        sample_ids: list[str],
        candidate_ids: list[str],
        title: str,
    ) -> str:
        sample = (
            self._sample_name(item, sample_ids[0]) if len(sample_ids) == 1 else None
        )
        if kind == "cell_decisions":
            value = facts.get("value", facts.get("quantitative_value"))
            owner = f"{sample} 的" if sample else "這筆"
            if value is None:
                return f"{owner}正式定量決策記錄為空值；空值不等於零。"
            if value == 0:
                return f"{owner}正式定量決策記錄為零。"
            return f"{owner}正式定量決策記錄數值 {self._format_measurement(value)}。"
        if kind == "rows":
            destination_value = (facts.get("route") or {}).get("destination")
            destination = (
                destination_value if isinstance(destination_value, str) else None
            )
            if destination == "matrix":
                return "此結果列的實錄去向為定量矩陣。"
            if destination == "archive":
                return "此結果列留在封存紀錄，未列入定量矩陣。"
            return "此結果列未記錄輸出去向。"
        if kind == "final_groups":
            state = facts.get("disposition")
            return (
                f"來源保留的最終分組紀錄，記錄狀態為「{state}」。"
                if state not in (None, "")
                else "來源保留最終分組紀錄，但未記錄狀態。"
            )
        if kind == "capture_evidence":
            stage_value = facts.get("kind")
            stage = (
                _STAGE_LABELS.get(stage_value, "保留曲線")
                if isinstance(stage_value, str)
                else "保留曲線"
            )
            owner = f"{sample} 的" if sample else "這筆"
            return f"{owner}{stage}與其精確曲線 evidence ref 的關係。"
        if kind == "terminal_subjects":
            count = len(facts.get("observations") or [])
            return f"來源保留 {count} 筆終端觀察。"
        if kind == "method_subjects" and facts.get("neutral_loss_da") is not None:
            return f"此方法紀錄的中性丟失為 {facts['neutral_loss_da']} Da。"
        if kind == "method_assessments":
            result = facts.get("method_result")
            reasons = facts.get("reason_codes")
            reason_values = (
                [reason for reason in reasons if isinstance(reason, str)]
                if isinstance(reasons, list)
                else []
            )
            readable_reasons = (
                "、".join(
                    _REASON_LABELS.get(reason, reason) for reason in reason_values
                )
                if reason_values
                else None
            )
            result_text = (
                f"方法結果為「{result}」"
                if result not in (None, "")
                else "來源未記錄方法結果"
            )
            reason_text = (
                f"；理由：{readable_reasons}" if readable_reasons else "；未記錄理由"
            )
            return f"{result_text}{reason_text}。此相關方法判定不代表正式定量採用。"
        if kind == "initial_edges":
            evidence = facts.get("initial_evidence")
            evidence = evidence if isinstance(evidence, dict) else {}
            disposition = facts.get("disposition")
            decision = evidence.get("decision")
            failure = evidence.get(
                "failure_reason", evidence.get("failure", evidence.get("reason"))
            )
            score = evidence.get("score")
            parts = []
            if disposition not in (None, ""):
                parts.append(f"記錄狀態為「{disposition}」")
            if decision not in (None, ""):
                parts.append(f"初始判定為「{decision}」")
            if failure not in (None, ""):
                readable_failure = (
                    _REASON_LABELS.get(failure, failure)
                    if isinstance(failure, str)
                    else str(failure)
                )
                parts.append(f"理由：{readable_failure}")
            if score is not None:
                parts.append(f"score {self._format_measurement(score)}")
            detail = "；".join(parts) if parts else "來源未記錄初始配對判定"
            return f"{detail}。此相關初始配對不代表正式定量採用。"
        if kind == "exclusions":
            state = facts.get("discovery_candidate_state")
            reason = facts.get("reason_code")
            parts = []
            if state not in (None, ""):
                parts.append(f"候選狀態為「{state}」")
            if reason not in (None, ""):
                readable_reason = (
                    _REASON_LABELS.get(reason, reason)
                    if isinstance(reason, str)
                    else str(reason)
                )
                parts.append(f"排除理由：{readable_reason}")
            detail = "；".join(parts) if parts else "來源未記錄候選排除理由"
            return f"{detail}。此排除紀錄不是正式定量採用決策。"
        scope = []
        if sample_ids:
            scope.append(f"{len(sample_ids)} 個樣本")
        if candidate_ids:
            scope.append(f"{len(candidate_ids)} 個候選")
        suffix = f"，明示關聯到{'、'.join(scope)}" if scope else ""
        return f"來源保留的{title}{suffix}。"

    @classmethod
    def _record_fields(cls, facts: dict[str, Any]) -> list[dict[str, Any]]:
        fields = []

        def add(key: str, value: Any) -> None:
            if value is None or isinstance(value, (dict, list, tuple)):
                return
            if isinstance(value, str) and len(value) > 160:
                return
            fields.append(
                {
                    "label": _FIELD_LABELS.get(key, key),
                    "value": cls._format_record_value(key, value),
                }
            )

        if "quantitative_value" in facts or "value" in facts:
            value = facts.get("value", facts.get("quantitative_value"))
            fields.append(
                {
                    "label": "正式定量值",
                    "value": (
                        "空值（非零）"
                        if value is None
                        else cls._format_measurement(value)
                    ),
                }
            )
        route = facts.get("route")
        if isinstance(route, dict):
            add("destination", route.get("destination"))
            add("reason", route.get("reason"))
        for key in (
            "variant",
            "source",
            "reason",
            "disposition",
            "method_result",
            "technical_state",
            "neutral_loss_da",
            "precursor_mz",
            "rt_apex_min",
            "discovery_candidate_state",
            "reason_code",
            "kind",
        ):
            add(key, facts.get(key))
        reason_codes = facts.get("reason_codes")
        if isinstance(reason_codes, list) and reason_codes:
            reason_values = [
                reason for reason in reason_codes if isinstance(reason, str)
            ]
            fields.append(
                {
                    "label": _FIELD_LABELS["reason_codes"],
                    "value": "、".join(
                        _REASON_LABELS.get(reason, reason) for reason in reason_values
                    ),
                }
            )
        observations = facts.get("observations")
        if isinstance(observations, list):
            fields.append({"label": "終端觀察數", "value": len(observations)})
            for index, observation in enumerate(observations, 1):
                if isinstance(observation, dict):
                    apex = observation.get("rt_apex_min")
                    if isinstance(apex, (int, float)):
                        fields.append(
                            {
                                "label": f"觀察 {index} 峰頂 RT (min)",
                                "value": cls._format_fixed(apex, 3),
                            }
                        )
        initial = facts.get("initial_evidence")
        if isinstance(initial, dict):
            add("decision", initial.get("decision"))
            failure = initial.get(
                "failure_reason", initial.get("failure", initial.get("reason"))
            )
            if failure not in (None, ""):
                readable_failure = (
                    _REASON_LABELS.get(failure, failure)
                    if isinstance(failure, str)
                    else str(failure)
                )
                fields.append(
                    {
                        "label": _FIELD_LABELS["failure_reason"],
                        "value": readable_failure,
                    }
                )
            add("score", initial.get("score"))
        return cls._dedupe_entries(fields)

    @classmethod
    def _format_record_value(cls, key: str, value: Any) -> Any:
        if key == "precursor_mz":
            return cls._format_fixed(value, 4)
        if key == "neutral_loss_da":
            return cls._format_fixed(value, 4)
        if key == "rt_apex_min":
            return cls._format_fixed(value, 3)
        if key == "reason_code" and isinstance(value, str):
            label = _REASON_LABELS.get(value)
            return f"{label}（{value}）" if label else value
        if key == "variant" and isinstance(value, str):
            label = _RESULT_VARIANT_LABELS.get(value)
            return f"{label}（{value}）" if label else value
        return value

    @staticmethod
    def _format_fixed(value: Any, digits: int) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        return f"{value:.{digits}f}"

    @staticmethod
    def _format_measurement(value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        if value == 0:
            return "0"
        if abs(value) < 0.00005:
            return f"{value:.4g}"
        return f"{value:,.4f}".rstrip("0").rstrip(".")

    @staticmethod
    def _record_links(
        facts: dict[str, Any], current_ref: str, available_refs: set[str]
    ) -> list[dict[str, str]]:
        links = []
        for key, label in (
            ("row_key_id", "查看結果列"),
            ("group_hypothesis_id", "查看分組紀錄"),
            ("payload_id", "查看曲線來源"),
            ("record_id", "查看所指紀錄"),
        ):
            value = facts.get(key)
            if (
                isinstance(value, str)
                and value in available_refs
                and value != current_ref
            ):
                links.append({"ref": value, "label": label})
        for key, value in facts.items():
            if not (key.endswith("_ref") or key.endswith("_refs")):
                continue
            values = value if isinstance(value, list) else [value]
            for ref in values:
                if (
                    isinstance(ref, str)
                    and ref in available_refs
                    and ref != current_ref
                ):
                    links.append({"ref": ref, "label": "查看相關紀錄"})
        return CidNlSource._dedupe_entries(links)

    @classmethod
    def _technical_refs(
        cls,
        payload: dict[str, Any],
        *,
        first: dict[str, Any],
    ) -> list[dict[str, Any]]:
        refs = [first]

        def visit(key: str, value: Any) -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    visit(child_key, child_value)
                return
            if isinstance(value, (list, tuple)):
                for child in value:
                    if isinstance(child, dict):
                        for child_key, child_value in child.items():
                            visit(child_key, child_value)
                    elif cls._is_technical_key(key):
                        visit(key, child)
                return
            if value is None or not cls._is_technical_key(key):
                return
            refs.append(
                {
                    "label": _TECHNICAL_LABELS.get(key, key),
                    "value": value,
                }
            )

        for field, value in payload.items():
            visit(field, value)
        return cls._dedupe_entries(refs)

    @staticmethod
    def _is_technical_key(key: str) -> bool:
        return (
            key == "id"
            or key.endswith("_id")
            or key.endswith("_ids")
            or key.endswith("_ref")
            or key.endswith("_refs")
            or key.endswith("_sha256")
            or key.endswith("_path")
        )

    @staticmethod
    def _dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for entry in entries:
            marker = tuple(entry.items())
            if marker in seen:
                continue
            seen.add(marker)
            result.append(entry)
        return result

    def _readable_curve(
        self,
        item: CanonicalItem,
        curve: dict[str, Any],
        available_refs: set[str],
    ) -> dict[str, Any]:
        ref = curve["evidence_ref"]
        sample_ids = [curve["sample_id"]] if curve.get("sample_id") else []
        candidate_ids = sorted(
            set(curve.get("candidate_ids") or [])
            | ({curve["candidate_id"]} if curve.get("candidate_id") else set())
        )
        fields = [
            {"label": "擷取階段", "value": curve["stage_label"]},
            {"label": "時間軸", "value": "原始保留時間 (RT)"},
        ]
        signal_labels = ["原始訊號"]
        signal_names = {
            "baseline": "基線",
            "smoothed": "平滑訊號",
            "positive": "處理後正值訊號",
        }
        signal_labels.extend(
            signal_names.get(signal["kind"], signal["kind"])
            for signal in curve.get("processed_curves", [])
        )
        fields.append({"label": "可用訊號", "value": "、".join(signal_labels)})
        left = curve.get("bound_left_rt")
        right = curve.get("bound_right_rt")
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            fields.append(
                {"label": "積分範圍 (min)", "value": f"{left:.3f}–{right:.3f}"}
            )
        if curve.get("apex_rt") is not None:
            fields.append(
                {
                    "label": "積分峰頂 RT (min)",
                    "value": self._format_fixed(curve["apex_rt"], 3),
                }
            )
        integration = curve.get("integration") or {}
        if integration.get("raw_apex_rt_min") is not None:
            fields.append(
                {
                    "label": "原始訊號峰頂 RT (min)",
                    "value": self._format_fixed(integration["raw_apex_rt_min"], 3),
                }
            )
        if curve.get("area") is not None:
            fields.append(
                {
                    "label": "當次原始計數面積 (counts·s)",
                    "value": self._format_measurement(curve["area"]),
                }
            )
        for key, label in (
            ("integration_method", "積分方法"),
            ("boundary_source", "峰界來源"),
        ):
            if integration.get(key) is not None:
                fields.append({"label": label, "value": integration[key]})
        facts = {
            "sample_id": curve.get("sample_id"),
            "candidate_id": curve.get("candidate_id"),
            "peak_observation_content_id": curve.get("observation_id"),
            "provenance": curve.get("provenance") or {},
        }
        sample = self._sample_name(item, sample_ids[0]) if sample_ids else "這個樣本"
        integration_note = (
            "，並包含積分範圍與面積紀錄" if integration else "；來源未提供積分紀錄"
        )
        return {
            "ref": ref,
            "title": curve["stage_label"],
            "summary": f"{sample} 的{curve['stage_label']}{integration_note}。",
            "sample_ids": sample_ids,
            "candidate_ids": candidate_ids,
            "fields": self._dedupe_entries(fields),
            "links": self._record_links(facts, ref, available_refs),
            "technical_refs": self._technical_refs(
                facts,
                first={"label": "曲線 evidence ref", "value": ref},
            ),
        }

    @staticmethod
    def _decision_label(cells):
        if not cells:
            return "沒有這個樣本的定量決策紀錄。"
        if len(cells) != 1:
            return f"保留 {len(cells)} 筆定量決策，沒有合併成單一數值。"
        cell = cells[0]
        value = cell["quantitative_value"]
        if value is None:
            variant = cell.get("variant")
            variant_label = {
                "not_searched": "本次未搜尋，定量結果保留為空值。",
                "rejected_observation": "本次觀察未被採用，定量結果保留為空值。",
                "unavailable": "本次沒有可用觀察，定量結果保留為空值。",
                "unresolved": "當次決策尚未解決，定量結果保留為空值。",
            }.get(variant)
            if variant_label:
                return variant_label
            return "當次定量決策記錄為空值，並非零。"
        if value == 0:
            return "當次定量決策記錄的數值為零。"
        if cell.get("source") == "trapezoid_counts_seconds_on_canonical_signal_v1":
            return "依當次採用的訊號，以梯形積分面積定量。"
        return "當次定量決策已保留數值。"

    def _item_labels(self, item, samples, route):
        """Describe recorded outcomes without promoting them to scientific claims."""
        group = next(
            (
                r["facts"]
                for r in item.details["records"]
                if r["kind"] == "final_groups"
            ),
            {},
        )
        state = group.get("disposition", route.get("reason"))
        title, outcome = {
            "established": ("已建立分組", "本次已建立分組"),
            "provisional": ("暫定分組", "本次分組仍為暫定"),
            "unresolved": ("分組尚待釐清", "本次分組判定尚未解決"),
        }.get(state, ("分組紀錄", "保留本次分組紀錄"))
        if item.kind == "candidate":
            title, outcome = (
                ("已排除候選", "本次候選已被排除，沒有最終分組")
                if item.stratum == "excluded"
                else ("未分組候選", "本次候選沒有最終分組紀錄")
            )
        count = len(samples)
        states = {"numeric": 0, "zero": 0, "null": 0, "multiple": 0, "missing": 0}
        for sample in samples:
            decision_count = len(sample["refs"])
            if decision_count == 0:
                states["missing"] += 1
            elif decision_count > 1:
                states["multiple"] += 1
            elif sample.get("quantitative_value") is None:
                states["null"] += 1
            elif sample["quantitative_value"] == 0:
                states["zero"] += 1
            else:
                states["numeric"] += 1
        if count == 1:
            population = {
                "numeric": "此樣本有定量數值",
                "zero": "此樣本定量值為零",
                "null": "此樣本定量值為空值",
                "multiple": "此樣本有多筆定量決策，未形成單一結果",
                "missing": "此樣本沒有定量決策紀錄",
            }[next(state for state, state_count in states.items() if state_count)]
        else:
            labels = (
                ("numeric", "有定量數值"),
                ("null", "空值"),
                ("zero", "零值"),
                ("multiple", "有多筆決策且無單一結果"),
                ("missing", "沒有定量決策紀錄"),
            )
            population = "所選 {} 個樣本：{}".format(
                count,
                "、".join(
                    f"{states[state]} 個{label}"
                    for state, label in labels
                    if states[state]
                ),
            )
        curve_missing = sum(
            sample["evidence_status"] == "unavailable" for sample in samples
        )
        if curve_missing:
            population += f"；{curve_missing} 個未保存曲線"
        destination = route.get("destination")
        route_label = {
            "matrix": "去向：定量矩陣",
            "archive": "去向：封存紀錄（未列入定量矩陣）",
        }.get(destination, "未記錄輸出去向")
        route_outcome = {
            "matrix": "結果去向為定量矩陣",
            "archive": "結果留存於封存紀錄，未列入定量矩陣",
        }.get(destination, "未記錄輸出去向")
        losses = sorted(
            {
                record["facts"]["neutral_loss_da"]
                for record in item.details["records"]
                if record["kind"] == "method_subjects"
                and isinstance(record["facts"].get("neutral_loss_da"), (int, float))
            }
        )
        reason_label = f"{outcome}，{population}；{route_outcome}。"
        attention = self._attention_reason_label(item, samples)
        if attention:
            reason_label += f" {attention}"
        labels = {
            "title": title,
            "badge_label": {
                "attention": "需留意",
                "excluded": "已排除",
                "null": "含空值",
                "zero": "含零值",
            }.get(item.stratum, "已記錄"),
            "reason_label": reason_label,
            "route_label": route_label,
        }
        if losses:
            labels["method_label"] = "NL " + "、".join(f"{loss:.4f}" for loss in losses)
        return labels

    @staticmethod
    def _attention_reason_label(item, samples) -> str:
        sample_labels = {
            sample["sample_id"]: sample.get("sample_stem", sample["sample_id"])
            for sample in samples
        }
        entries = []
        for trigger in item.details.get("attention_triggers", []):
            reason_codes = trigger.get("reason_codes") or [trigger.get("reason")]
            reasons = []
            for code in reason_codes:
                if not isinstance(code, str) or not code:
                    continue
                label = _REASON_LABELS.get(code)
                reasons.append(f"{label}（{code}）" if label else code)
            if not reasons:
                continue
            affected = [
                sample_labels.get(sample_id, sample_id)
                for sample_id in trigger.get("sample_ids", [])
                if isinstance(sample_id, str)
            ]
            subject = "、".join(affected) if affected else "本項目"
            prefix = "相關方法評估為 HOLD：" if trigger.get("reason") == "HOLD" else ""
            entries.append(f"{subject}：{prefix}{'、'.join(reasons)}")
        if not entries:
            return ""
        return (
            "需留意原因（不代表正式定量採用）："
            + "；".join(dict.fromkeys(entries))
            + "。"
        )

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
            "stage_label": _STAGE_LABELS.get(payload.get("kind"), "保留曲線"),
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
        knots = self.calibration_by_sample_stem.get(sample_stem)
        if knots:
            aligned_rts = [interpolate_calibrated_rt_min(value, knots) for value in rts]
            aligned_left = (
                interpolate_calibrated_rt_min(integration.get("rt_left_min"), knots)
                if isinstance(integration.get("rt_left_min"), (int, float))
                else None
            )
            aligned_right = (
                interpolate_calibrated_rt_min(integration.get("rt_right_min"), knots)
                if isinstance(integration.get("rt_right_min"), (int, float))
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
