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
    "neutral_loss_da": "中性丟失 (Da)",
    "precursor_mz": "前驅離子 m/z",
    "rt_apex_min": "峰頂 RT (min)",
    "discovery_candidate_state": "候選狀態",
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
                            curve.get("sample_id") == sid
                            and curve.get("raw_rts")
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
                    "curves": projected_curves,
                }
            )
        return rendered

    def _decision_refs(self, item: CanonicalItem, sample_id: str) -> list[str]:
        item_keys = {
            (witness.candidate_id, witness.canonical_sample_identity_id)
            for witness in item.witnesses
        }
        refs = []
        for record in item.details["records"]:
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
        if variant not in (None, ""):
            return f"來源記錄結果類型為「{variant}」；未另記理由。"
        return "來源未記錄定量理由。"

    def _readable_records(
        self, item: CanonicalItem, curves: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        available_refs = {
            record["ref"] for record in item.details["records"]
        } | {
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
        return readable

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
        item_keys = {
            (witness.candidate_id, witness.canonical_sample_identity_id)
            for witness in item.witnesses
        }
        linked_keys = {
            key
            for key in item_keys
            if (record["kind"], record["ref"])
            in self.links.get(key, set())
        }
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
            self._sample_name(item, sample_ids[0])
            if len(sample_ids) == 1
            else None
        )
        if kind == "cell_decisions":
            value = facts.get("value", facts.get("quantitative_value"))
            owner = f"{sample} 的" if sample else "這筆"
            if value is None:
                return f"{owner}正式定量決策記錄為空值；空值不等於零。"
            if value == 0:
                return f"{owner}正式定量決策記錄為零。"
            return (
                f"{owner}正式定量決策記錄數值 "
                f"{self._format_measurement(value)}。"
            )
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
            "neutral_loss_da",
            "precursor_mz",
            "rt_apex_min",
            "discovery_candidate_state",
            "kind",
        ):
            add(key, facts.get(key))
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
            add("reason", initial.get("failure", initial.get("reason")))
        return cls._dedupe_entries(fields)

    @classmethod
    def _format_record_value(cls, key: str, value: Any) -> Any:
        if key == "precursor_mz":
            return cls._format_fixed(value, 4)
        if key == "neutral_loss_da":
            return cls._format_fixed(value, 4)
        if key == "rt_apex_min":
            return cls._format_fixed(value, 3)
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
                    "value": self._format_fixed(
                        integration["raw_apex_rt_min"], 3
                    ),
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
        sample = (
            self._sample_name(item, sample_ids[0]) if sample_ids else "這個樣本"
        )
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
            if cell.get("variant") == "unresolved":
                return "當次決策尚未解決，定量結果保留為空值。"
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
        labels = {
            "title": title,
            "badge_label": {
                "attention": "需留意",
                "excluded": "已排除",
                "null": "含空值",
                "zero": "含零值",
            }.get(item.stratum, "已記錄"),
            "reason_label": f"{outcome}，{population}；{route_outcome}。",
            "route_label": route_label,
        }
        if losses:
            labels["method_label"] = (
                "NL " + "、".join(f"{loss:.4f}" for loss in losses)
            )
        return labels

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
