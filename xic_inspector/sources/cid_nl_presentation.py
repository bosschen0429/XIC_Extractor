"""Stateless CID-NL labels and presentation for already-selected evidence.

The source owns loading, sample scope and evidence relationships. These functions
consume its records, explicit scope and projected curves without accessing the
source, reading payloads or validating scientific evidence.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from xic_inspector.selection.canonical import CanonicalItem

if TYPE_CHECKING:
    from xic_extractor.diagnostics.alignment_explanation import RecordKey

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
    "sample_key": "樣本 ID",
    "candidate_id": "候選 ID",
    "feature_id": "分組 ID",
    "physical_peak_key": "觀察 ID",
    "processing_attempt_id": "處理 attempt ID",
    "acquisition_source_id": "擷取來源 ID",
    "method_subject_id": "方法對象 ID",
    "matrix_path": "Matrix 路徑",
}


_STAGE_LABELS = {
    "discovery_physical": "探索階段曲線",
    "initial_capture": "首次擷取曲線",
    "reacquired_capture": "再次擷取曲線",
    "backfill_observation": "Backfill（回填）曲線",
}

_SUPPORT_ORIGIN_LABELS = {
    "backfill_support": "Backfill（回填）支持",
    "reacquired_support": "重新擷取支持",
    "discovery_support": "初始探索支持",
    "unavailable": "來源未提供",
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


def related_group(
    item: CanonicalItem,
    edge_refs: tuple[str, ...],
    curves: list[dict[str, Any]],
) -> dict[str, Any]:
    source = item.details["source"]
    group_id = source.get("feature_id")
    route = source.get("route", {})
    reason = route.get(
        "reason", source.get("reason", source.get("disposition", "unavailable"))
    )
    return {
        "id": item.serialized_ref,
        "label": f"相關分組 {group_id or item.identifier}",
        "group_id": group_id or item.identifier,
        "edge_refs": list(edge_refs),
        "reason": reason,
        "sample_ids": list(item.sample_ids),
        "witnesses": [asdict(witness) for witness in item.witnesses],
        "curves": curves,
    }


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
    if source == "trapezoid_counts_seconds_on_canonical_signal":
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


def readable_record(
    item: CanonicalItem,
    record: dict[str, Any],
    available_refs: set[RecordKey],
    *,
    scope: tuple[list[str], list[str]],
) -> dict[str, Any]:
    kind = record["kind"]
    facts = record["facts"]
    sample_ids, candidate_ids = scope
    title = _RECORD_TITLES.get(kind, f"{kind} 來源紀錄")
    return {
        "ref": record["ref"],
        "title": title,
        "summary": _record_summary(item, kind, facts, sample_ids, candidate_ids, title),
        "sample_ids": sample_ids,
        "candidate_ids": candidate_ids,
        "fields": _record_fields(facts),
        "links": _record_links(facts, record["ref"], available_refs),
        "technical_refs": _technical_refs(
            facts,
            first={"label": "來源紀錄 ID", "value": record["ref"]},
        ),
    }


def _sample_name(item: CanonicalItem, sample_id: str) -> str:
    return next(
        (
            witness.sample_stem
            for witness in item.witnesses
            if witness.sample_key == sample_id and witness.sample_stem
        ),
        sample_id,
    )


def _record_summary(
    item: CanonicalItem,
    kind: str,
    facts: dict[str, Any],
    sample_ids: list[str],
    candidate_ids: list[str],
    title: str,
) -> str:
    sample = _sample_name(item, sample_ids[0]) if len(sample_ids) == 1 else None
    if kind == "cell_decisions":
        value = facts.get("value", facts.get("quantitative_value"))
        owner = f"{sample} 的" if sample else "這筆"
        if value is None:
            return f"{owner}正式定量決策記錄為空值；空值不等於零。"
        if "ms1_only_backfill_observation" in facts.get("evidence_states", []):
            return "MS1 Backfill（非直接 CID-NL 確認）"
        if value == 0:
            return f"{owner}正式定量決策記錄為零。"
        return f"{owner}正式定量決策記錄數值 {_format_measurement(value)}。"
    if kind == "rows":
        destination_value = (facts.get("route") or {}).get("destination")
        destination = destination_value if isinstance(destination_value, str) else None
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
            "、".join(_REASON_LABELS.get(reason, reason) for reason in reason_values)
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
            parts.append(f"score {_format_measurement(score)}")
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


def _record_fields(facts: dict[str, Any]) -> list[dict[str, Any]]:
    fields = []

    def add(key: str, value: Any) -> None:
        if value is None or isinstance(value, (dict, list, tuple)):
            return
        if isinstance(value, str) and len(value) > 160:
            return
        fields.append(
            {
                "label": _FIELD_LABELS.get(key, key),
                "value": _format_record_value(key, value),
            }
        )

    if "quantitative_value" in facts or "value" in facts:
        value = facts.get("value", facts.get("quantitative_value"))
        fields.append(
            {
                "label": "正式定量值",
                "value": (
                    "空值（非零）" if value is None else _format_measurement(value)
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
        reason_values = [reason for reason in reason_codes if isinstance(reason, str)]
        fields.append(
            {
                "label": _FIELD_LABELS["reason_codes"],
                "value": "、".join(
                    _REASON_LABELS.get(reason, reason) for reason in reason_values
                ),
            }
        )
    risks = facts.get("membership_risks")
    if "variant" in facts or risks is not None:
        if not risks:
            fields.append({"label": "配對風險", "value": "未記錄"})
        for risk in risks or ():
            selected = "已配對" if len(risk["selected_owners"]) > 1 else "保持分開"
            fields.append(
                {
                    "label": "配對風險",
                    "value": (
                        f"{selected}，有其他合理候選"
                        if {
                            "accepted_competition",
                            "backfill_competition",
                        }.intersection(risk.get("risk_flags", []))
                        else f"{selected}，含矛盾或低支援證據"
                        if risk.get("risk_flags")
                        else "已評估，未標記風險"
                    ),
                }
            )
            for label, key in (
                ("決策階段", "phase"),
                ("選擇的成員", "selected_owners"),
                ("替代評估狀態", "status"),
                ("風險標記", "risk_flags"),
                ("接受的矛盾", "soft_contradictions"),
                ("比較座標", "rt_basis"),
                ("替代配置", "alternative_partition"),
                ("成本差", "cost_gap"),
            ):
                value = risk.get(key)
                fields.append(
                    {
                        "label": label,
                        "value": str(value) if value is not None else "無可行替代",
                    }
                )
            if risk.get("selected_peer_fit") is not None:
                fields.append(
                    {
                        "label": "同分決勝：平均配合成本（越低越好）",
                        "value": str(risk["selected_peer_fit"]),
                    }
                )
                if risk.get("alternative_peer_fit") is not None:
                    fields.append(
                        {
                            "label": "替代的平均配合成本",
                            "value": str(risk["alternative_peer_fit"]),
                        }
                    )
            else:
                fields.append({"label": "同分決勝資訊", "value": "未記錄"})
            estimate = risk.get("rt_estimate")
            if isinstance(estimate, dict):
                methods = {
                    "loess": "LOESS 範圍內校正",
                    "median_shift": "中位數平移（較弱估計）",
                    "boundary_shift": "邊界位移（較弱估計）",
                    "retained_map": "既有座標（品質未記錄）",
                    "diagnostic_override": "診斷指定座標",
                }
                fields.append(
                    {
                        "label": "RT 估計方法",
                        "value": methods.get(estimate["method"], estimate["method"]),
                    }
                )
                for label, key in (
                    ("估計 RT（分鐘）", "rt_min"),
                    ("合格對照位置數", "anchor_count"),
                    ("對照範圍（原始分鐘）", "anchor_range_min"),
                    ("殘差幅度（分鐘）", "residual_spread_min"),
                    ("最近對照距離（分鐘）", "nearest_anchor_distance_min"),
                    ("超出支援範圍（分鐘）", "outside_anchor_range_min"),
                    ("強模型狀態", "fit_status"),
                ):
                    value = estimate.get(key)
                    fields.append(
                        {
                            "label": label,
                            "value": str(value) if value is not None else "未記錄",
                        }
                    )
            else:
                fields.append(
                    {"label": "RT 估計品質", "value": "未記錄或未用於本次比較"}
                )
    qualities = facts.get("signal_quality", [] if "variant" in facts else None)
    if isinstance(qualities, list):
        if not qualities:
            fields.append({"label": "訊號品質", "value": "未記錄"})
        for index, quality in enumerate(qualities, 1):
            if not isinstance(quality, dict):
                continue
            ratio = quality.get("signal_to_noise")
            label = f"觀察 {index} SNR"
            if isinstance(ratio, (int, float)):
                fields.append({"label": label, "value": _format_measurement(ratio)})
                if quality.get("low_signal_to_noise") is True:
                    fields.append(
                        {
                            "label": "訊號品質提示",
                            "value": (
                                "低 SNR（僅供 review，不改變 Matrix 值；非 LOQ 判定）"
                            ),
                        }
                    )
            else:
                fields.append(
                    {
                        "label": label,
                        "value": "未記錄"
                        if quality.get("status") == "not_recorded"
                        else "未知",
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
                            "value": _format_fixed(apex, 3),
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
    return dedupe_entries(fields)


def _format_record_value(key: str, value: Any) -> Any:
    if key == "precursor_mz":
        return _format_fixed(value, 4)
    if key == "neutral_loss_da":
        return _format_fixed(value, 4)
    if key == "rt_apex_min":
        return _format_fixed(value, 3)
    if key == "reason_code" and isinstance(value, str):
        label = _REASON_LABELS.get(value)
        return f"{label}（{value}）" if label else value
    if key == "variant" and isinstance(value, str):
        label = _RESULT_VARIANT_LABELS.get(value)
        return f"{label}（{value}）" if label else value
    return value


def _format_fixed(value: Any, digits: int) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return f"{value:.{digits}f}"


def _format_measurement(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if value == 0:
        return "0"
    if abs(value) < 0.00005:
        return f"{value:.4g}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _record_links(
    facts: dict[str, Any], current_ref: RecordKey, available_refs: set[RecordKey]
) -> list[dict[str, str]]:
    links = []
    for key, label in (
        ("feature_id", "查看分組紀錄"),
        ("payload_id", "查看曲線來源"),
        ("record_id", "查看所指紀錄"),
    ):
        value = facts.get(key)
        if isinstance(value, str) and value in available_refs and value != current_ref:
            links.append({"ref": value, "label": label})
    for key, value in facts.items():
        if not (key.endswith("_ref") or key.endswith("_refs")):
            continue
        values = value if isinstance(value, list) else [value]
        for ref in values:
            if isinstance(ref, str) and ref in available_refs and ref != current_ref:
                links.append({"ref": ref, "label": "查看相關紀錄"})
    return dedupe_entries(links)


def _technical_refs(
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
                elif _is_technical_key(key):
                    visit(key, child)
            return
        if value is None or not _is_technical_key(key):
            return
        refs.append(
            {
                "label": _TECHNICAL_LABELS.get(key, key),
                "value": value,
            }
        )

    for field, value in payload.items():
        visit(field, value)
    return dedupe_entries(refs)


def _is_technical_key(key: str) -> bool:
    return (
        key == "id"
        or key.endswith("_id")
        or key.endswith("_ids")
        or key.endswith("_key")
        or key.endswith("_keys")
        or key.endswith("_ref")
        or key.endswith("_refs")
        or key.endswith("_sha256")
        or key.endswith("_path")
    )


def dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for entry in entries:
        marker = tuple(entry.items())
        if marker in seen:
            continue
        seen.add(marker)
        result.append(entry)
    return result


def readable_curve(
    item: CanonicalItem,
    curve: dict[str, Any],
    available_refs: set[RecordKey],
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
        fields.append({"label": "積分範圍 (min)", "value": f"{left:.3f}–{right:.3f}"})
    if curve.get("apex_rt") is not None:
        fields.append(
            {
                "label": "積分峰頂 RT (min)",
                "value": _format_fixed(curve["apex_rt"], 3),
            }
        )
    integration = curve.get("integration") or {}
    if integration.get("raw_apex_rt_min") is not None:
        fields.append(
            {
                "label": "原始訊號峰頂 RT (min)",
                "value": _format_fixed(integration["raw_apex_rt_min"], 3),
            }
        )
    if curve.get("area") is not None:
        fields.append(
            {
                "label": "當次原始計數面積 (counts·s)",
                "value": _format_measurement(curve["area"]),
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
        "physical_peak_key": curve.get("observation_id"),
        "provenance": curve.get("provenance") or {},
    }
    sample = _sample_name(item, sample_ids[0]) if sample_ids else "這個樣本"
    integration_note = (
        "，並包含積分範圍與面積紀錄" if integration else "；來源未提供積分紀錄"
    )
    return {
        "ref": ref,
        "title": curve["stage_label"],
        "summary": f"{sample} 的{curve['stage_label']}{integration_note}。",
        "sample_ids": sample_ids,
        "candidate_ids": candidate_ids,
        "fields": dedupe_entries(fields),
        "links": _record_links(facts, ref, available_refs),
        "technical_refs": _technical_refs(
            facts,
            first={"label": "曲線 evidence ref", "value": ref},
        ),
    }


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
    if cell.get("source") == "trapezoid_counts_seconds_on_canonical_signal":
        return "依當次採用的訊號，以梯形積分面積定量。"
    return "當次定量決策已保留數值。"


def _item_labels(item, samples, route):
    """Describe recorded outcomes without promoting them to scientific claims."""
    group: dict[str, Any] = next(
        (r["facts"] for r in item.details["records"] if r["kind"] == "final_groups"),
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
        population_labels = (
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
                for state, label in population_labels
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
    attention = _attention_reason_label(item, samples)
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
        "需留意原因（不代表正式定量採用）：" + "；".join(dict.fromkeys(entries)) + "。"
    )


def build_sample(
    item: CanonicalItem,
    sid: str,
    *,
    fallback_sample_label: str,
    decision_refs: list[RecordKey],
    projected_curves: list[dict[str, Any]],
) -> dict[str, Any]:
    """Present one already-scoped sample and its exact decision references."""
    cells = [
        c
        for c in item.details["cells"]
        if c.get("sample_key", c.get("sample_id")) == sid
    ]
    values = [c["quantitative_value"] for c in cells]
    return {
        "sample_id": sid,
        "sample_stem": next(
            (
                w.sample_stem
                for w in item.witnesses
                if w.sample_key == sid and w.sample_stem
            ),
            fallback_sample_label,
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
        **({"quantitative_value": values[0]} if len(values) == 1 else {}),
        "decision_reason": _decision_reason(cells),
        "signal_quality": [
            quality for cell in cells for quality in cell.get("signal_quality", [])
        ],
        "membership_risks": [
            risk for cell in cells for risk in cell.get("membership_risks", [])
        ],
        "decision_label": _decision_label(cells),
        "decision_refs": decision_refs,
        "evidence_status": "available"
        if any(
            curve.get("sample_id") == sid and curve.get("raw_rts")
            for curve in projected_curves
        )
        else "unavailable",
        "refs": cells,
    }


def build_render_item(
    item: CanonicalItem,
    *,
    samples: list[dict[str, Any]],
    projected_curves: list[dict[str, Any]],
    readable_records: list[dict[str, Any]],
    relationship_members: list[dict[str, Any]],
    internal_pair_support: list[dict[str, Any]],
    relationship_availability: str,
    related_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble presentation from selected evidence; never select or reload it."""
    source = item.details["source"]
    route = source.get("route", {})
    reason = route.get(
        "reason", source.get("reason", source.get("disposition", "unavailable"))
    )
    return {
        "id": item.serialized_ref,
        "kind": item.kind,
        "stratum": item.stratum,
        "badge": item.stratum,
        "precursor_mz": item.witnesses[0].precursor_mz if item.witnesses else None,
        "rt_apex_min": item.witnesses[0].rt_apex_min if item.witnesses else None,
        "reason": reason,
        "route": route,
        **_item_labels(item, samples, route),
        "samples": samples,
        "witnesses": [
            {**asdict(w), "matched": w in item.matched_witnesses}
            for w in item.witnesses
        ],
        "matched_refs": [asdict(w) for w in item.matched_witnesses],
        "details": {
            **item.details,
            "readable_records": readable_records,
        },
        "review_summary": item.details["review_summary"],
        "relationship_members": relationship_members,
        "relationship_availability": relationship_availability,
        **(
            {"internal_pair_support": internal_pair_support}
            if internal_pair_support
            else {}
        ),
        "related_groups": related_groups,
        "curves": projected_curves,
    }


def related_record_link(kind: str, ref: str) -> dict[str, str]:
    label = (
        "查看相關方法判定（不代表正式定量採用）"
        if kind == "method_assessments"
        else "查看相關初始配對（不代表正式定量採用）"
    )
    return {"ref": ref, "label": label}


def curve_stage_label(kind: Any) -> str:
    return _STAGE_LABELS.get(kind, "保留曲線")


def support_origin_label(kind: str) -> str:
    return _SUPPORT_ORIGIN_LABELS[kind]
