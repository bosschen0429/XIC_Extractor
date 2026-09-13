"""Translate retained CID-NL evidence into shared Inspector presentation data.

The scientific reader owns payload validation. This adapter owns record kinds,
exact link projection, scoped status interpretation and curve presentation.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

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

    def load_items(self, sample_ids: set[str]) -> list[CanonicalItem]:
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro", uri=True
        )
        try:
            connection.execute("PRAGMA query_only = ON")
            for cid, sid, payload in connection.execute(
                "SELECT candidate_id, sample_id, payload FROM candidates"
            ):
                self.candidates[cid, sid] = json.loads(payload)
            for kind, rid, payload in connection.execute(
                "SELECT kind, record_id, payload FROM records"
            ):
                self.records.setdefault(kind, {})[rid] = json.loads(payload)
            for cid, sid, kind, rid in connection.execute(
                "SELECT candidate_id, sample_id, kind, record_id FROM links"
            ):
                self.links.setdefault((cid, sid), set()).add((kind, rid))
        except (sqlite3.Error, json.JSONDecodeError, TypeError) as exc:
            raise ExplanationError(f"Invalid CID-NL metadata: {exc}") from exc
        finally:
            connection.close()

        items = []
        row_groups = set()
        grouped = set()
        # Construct the run population before applying sample scope.
        for rid, payload in self.records.get("rows", {}).items():
            gid = payload.get("group_hypothesis_id")
            row_groups.add(gid)
            keys = self._linked("rows", rid)
            grouped.update(keys)
            items.append(self._item("row", rid, gid, keys, sample_ids, payload))
        for gid, payload in self.records.get("final_groups", {}).items():
            keys = self._linked("final_groups", gid)
            grouped.update(keys)
            if gid not in row_groups:
                items.append(self._item("group", gid, gid, keys, sample_ids, payload))
        for key, payload in self.candidates.items():
            if key not in grouped:
                items.append(
                    self._item("candidate", key[0], None, {key}, sample_ids, payload)
                )
        return sorted(items, key=lambda item: item.sort_key)

    def _linked(self, kind: str, rid: str) -> set[tuple[str, str]]:
        return {key for key, links in self.links.items() if (kind, rid) in links}

    def _item(self, kind, identifier, gid, keys, scope, payload) -> CanonicalItem:
        scoped = sorted(key for key in keys if key[1] in scope)
        facts: dict[tuple[str, str], dict[str, Any]] = {}
        fact_samples: dict[tuple[str, str], set[str]] = {}
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
                triggers.append(
                    {
                        "scope": "sample",
                        "ref": rid,
                        "reason": reason,
                        "sample_ids": sorted(fact_samples[record_kind, rid]),
                    }
                )
        stratum = classify_scoped_stratum(
            is_attention=bool(triggers),
            is_excluded=excluded,
            has_null_cell=any(c["quantitative_value"] is None for c in cells),
            has_zero_cell=any(c["quantitative_value"] == 0 for c in cells),
        )
        sample_status = {}
        for sid in sorted({sid for _, sid in scoped}):
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
        return CanonicalItem(
            kind=kind,
            request_id=self.request_id,
            attempt_id=self.attempt_id,
            identifier=identifier,
            serialized_ref=ref,
            sample_ids=tuple(sorted({sid for _, sid in scoped})),
            witnesses=tuple(witnesses),
            stratum=stratum,
            is_attention=bool(triggers),
            details={
                "source": payload,
                "cells": cells,
                "attention_triggers": triggers,
                "sample_status": sample_status,
                "records": [
                    {"kind": k, "ref": r, "facts": f}
                    for (k, r), f in sorted(facts.items())
                ],
            },
        )

    def load_render_items(
        self, items, *, capability: str, check_cancel: Callable[[], None]
    ):
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
        for item in items:
            check_cancel()
            keys = {
                (w.candidate_id, w.canonical_sample_identity_id) for w in item.witnesses
            }
            observation_keys = {
                (w.observation_id, w.canonical_sample_identity_id)
                for w in item.witnesses
                if w.observation_id
            }
            capture_keys = {
                (capture["sample_id"], capture["payload_id"])
                for capture in captures_by_item[item.serialized_ref]
            }
            curves = [
                c
                for c in raw_curves
                if (c.get("sample_id"), c.get("payload_id")) in capture_keys
                or (
                    (c.get("peak_observation_content_id"), c.get("sample_id"))
                    in observation_keys
                    if observation_keys
                    else (c.get("candidate_id"), c.get("sample_id")) in keys
                )
            ]
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
                            sid,
                        ),
                        "status_kind": item.details["sample_status"][sid],
                        "status_label": {
                            "attention": "需留意",
                            "excluded": "已排除",
                            "rest": "其他結果",
                        }.get(item.details["sample_status"][sid], "未提供"),
                        **(
                            {"quantitative_value": values[0]}
                            if len(values) == 1
                            else {}
                        ),
                        "decision_reason": "; ".join(
                            str(c.get("source", c.get("variant", "unavailable")))
                            for c in cells
                        )
                        or "unavailable",
                        "decision_label": (
                            "當次結果為空值"
                            if len(values) == 1 and values[0] is None
                            else "當次定量結果為零"
                            if len(values) == 1 and values[0] == 0
                            else "沿用當次定量結果"
                            if len(values) == 1
                            else "未提供單一樣本定量值"
                        ),
                        "evidence_status": "available"
                        if any(
                            c.get("sample_id") == sid and c["curves"]["rt"]
                            for c in curves
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
            rendered.append(
                {
                    "id": item.serialized_ref,
                    "kind": item.kind,
                    "title": f"{item.kind.capitalize()} {item.identifier}",
                    "stratum": item.stratum,
                    "badge": item.stratum,
                    "precursor_mz": item.witnesses[0].precursor_mz
                    if item.witnesses
                    else None,
                    "rt_apex_min": item.witnesses[0].rt_apex_min
                    if item.witnesses
                    else None,
                    "reason": reason,
                    "reason_label": {
                        "established": "已建立分組",
                        "provisional": "分組仍為暫定狀態",
                        "unresolved": "當次判定尚未解決",
                        "unavailable": "未提供當次判定說明",
                    }.get(reason, "當次判定已保留，可展開查看原始理由"),
                    "route": route,
                    "route_label": {
                        "matrix": "結果去向：定量矩陣（Matrix）",
                        "archive": "結果去向：留存紀錄（Archive）",
                    }.get(route.get("destination"), "未提供結果去向"),
                    "samples": samples,
                    "witnesses": [
                        {**asdict(w), "matched": w in item.matched_witnesses}
                        for w in item.witnesses
                    ],
                    "matched_refs": [asdict(w) for w in item.matched_witnesses],
                    "details": item.details,
                    "curves": [
                        {
                            **self._curve(c),
                            "candidate_ids": sorted(
                                {
                                    cid
                                    for cid, sid in keys
                                    if sid == c.get("sample_id")
                                    and (
                                        ("capture_evidence", c.get("payload_id"))
                                        in self.links.get((cid, sid), set())
                                        or any(
                                            w.candidate_id == cid
                                            and w.canonical_sample_identity_id == sid
                                            and w.observation_id
                                            == c.get("peak_observation_content_id")
                                            for w in item.witnesses
                                            if w.observation_id
                                        )
                                    )
                                }
                            ),
                        }
                        for c in curves
                    ],
                }
            )
        return rendered

    @staticmethod
    def _curve(payload):
        curves = payload.get("curves", {})
        integration = payload.get("integration") or {}
        rts = curves.get("rt", [])
        return {
            "sample_id": payload.get("sample_id"),
            "candidate_id": payload.get("candidate_id"),
            "evidence_ref": payload.get(
                "payload_id", payload.get("peak_observation_content_id")
            ),
            "observation_id": payload.get("peak_observation_content_id"),
            "kind": payload.get("kind"),
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
