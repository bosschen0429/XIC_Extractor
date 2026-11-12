"""Stateless CID-NL labels and presentation for already-selected evidence.

The source owns loading, sample scope and evidence cards. These functions
consume its cards, explicit scope and projected curves without accessing the
source, reading payloads or validating scientific evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xic_inspector.sources.cid_nl import Card, CardRef, ItemEvidence

_STAGE_LABELS = {
    "initial_capture": "detect 曲線",
    "backfill_observation": "backfill 曲線",
}
_SUPPORT_ORIGIN_LABELS = {
    "backfill_support": "backfill",
    "discovery_support": "detect",
    "unavailable": "來源未提供",
}
_REASON_LABELS = {
    "insufficient_method_evidence": "找過，沒有可用的 MS1 峰或符合條件的 MS2 支持",
    "no_unique_qualifying_relation": "沒有可唯一確認的 MS2 與峰的對應",
    "discovery_candidate_not_search_eligible": "初始候選未符合後續搜尋條件",
    "product_mz_out_of_tolerance": "碎片離子 m/z 超出容許範圍",
}


def blank_reason(cell: dict[str, Any]) -> str:
    """Describe only the recorded empty decision, not absence of raw signal."""
    if cell.get("value", cell.get("quantitative_value")) is not None:
        return "不是空值。"
    variant = cell.get("variant")
    states = set(cell.get("evidence_states", ()))
    reasons = set(cell.get("reason_codes", ()))
    if variant == "not_searched":
        return "本次未在這個群組位置搜尋。"
    if variant == "unresolved" or "unresolved" in states:
        return "有可能的峰，但無法唯一決定採用哪一個。"
    if variant == "searched_no_observation" or "searched_no_observation" in states:
        return "在搜尋範圍內找過，沒有可用的峰。"
    if "insufficient_method_evidence" in reasons:
        # _method_result emits this only after evaluation, with no candidate
        # peaks and no qualifying CID-NL relation. It does not mean zero signal.
        return _REASON_LABELS["insufficient_method_evidence"] + "。"
    if variant == "not_quantifiable":
        return "有峰，但這次積分無法提供可定量的值。"
    if variant == "rejected_observation":
        return "找到的觀察未符合採用條件。"
    return "沒有可用的觀察；保存的資料未提供更具體原因。"


def cell_risks(cell: dict[str, Any]) -> list[str]:
    """Recorded interpretation risks; ordinary MS1-only backfill is not one."""
    flags = {
        flag for risk in cell.get("membership_risks", ()) for flag in risk["risk_flags"]
    }
    labels = []
    if flags & {"low_rt_support", "backfill_low_localization_confidence"}:
        labels.append("RT 支持較弱")
    for flag, label in (
        ("accepted_contradiction", "採用的配對仍有矛盾證據"),
        ("weak_identity", "身分證據較弱"),
        ("direction_disagreement", "不同方向的配對結果不一致"),
    ):
        if flag in flags:
            labels.append(label)
    if any(
        q.get("low_signal_to_noise") is True for q in cell.get("signal_quality", ())
    ):
        labels.append("低 SNR（非 LOQ 判定）")
    return labels


def competition_groups(cell: dict[str, Any]) -> list[list[list[Any]]]:
    """Other groups considered with this sample's peak, by measured locations.

    These are the compared alternatives, not necessarily published features.
    Never invent a final feature ID for an alternative that was not selected.
    """
    groups = []
    sid = cell["sample_key"]
    for risk in cell.get("membership_risks", ()):
        if not {"accepted_competition", "backfill_competition"}.intersection(
            risk["risk_flags"]
        ):
            continue
        selected = risk["selected_owners"]
        for partition in [
            risk.get("alternative_partition") or [],
            *risk.get("equivalent_alternatives", []),
        ]:
            for group in partition:
                if any(owner[0] == sid for owner in group) and group != selected:
                    peers = [owner for owner in group if owner[0] != sid]
                    if peers and peers not in groups:
                        groups.append(peers)
        # A split alternative leaves this peak alone: the selected peers are
        # still the competing group whose association was accepted.
        if not groups:
            peers = [owner for owner in selected if owner[0] != sid]
            if peers:
                groups.append(peers)
    return groups


def _decision_reason(cells: list[dict[str, Any]]) -> str:
    if len(cells) != 1:
        return "沒有單一正式定量決策。"
    return blank_reason(cells[0])


def readable_record(
    card: Card,
    *,
    scope: tuple[list[str], list[str]],
    sample_labels: dict[str, str],
) -> dict[str, Any]:
    """The three user questions, with no internal-record drill-down."""
    facts = card.facts
    sample_ids, candidate_ids = scope
    sid = sample_ids[0] if sample_ids else ""
    value = facts.get("value")
    label = sample_labels.get(sid, sid)
    if card.kind == "cell_decisions":
        origin = facts.get("support_origin", "unavailable")
        risks = cell_risks(facts)
        return {
            "ref": card.ref,
            "title": f"{label} · "
            + ("空值" if value is None else _format_measurement(value)),
            "sample_ids": sample_ids,
            "candidate_ids": candidate_ids,
            "cell_review": True,
            "value": value,
            "fields": [
                {
                    "label": "值從哪裡來",
                    "value": support_origin_label(origin)
                    if value is not None
                    else "空值，沒有提供矩陣值的峰。",
                },
                {"label": "空值為什麼是空的", "value": blank_reason(facts)},
                {
                    "label": "需不需要擔心",
                    "value": "；".join(risks) if risks else "未記錄需留意的風險。",
                },
            ],
            "competition_groups": competition_groups(facts),
        }
    reason = facts.get("reason_code", "")
    return {
        "ref": card.ref,
        "title": f"{label} · 已排除候選",
        "compact_review": True,
        "sample_ids": sample_ids,
        "candidate_ids": candidate_ids,
        "fields": [
            {
                "label": "為什麼未採用",
                "value": _REASON_LABELS.get(reason, reason or "未記錄排除原因"),
            }
        ],
    }


def _format_measurement(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if value == 0:
        return "0"
    if abs(value) < 0.00005:
        return f"{value:.4g}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _decision_label(cells):
    if not cells:
        return "沒有定量決策紀錄"
    if len(cells) != 1:
        return f"{len(cells)} 筆定量決策，未合併"
    cell = cells[0]
    value = cell["quantitative_value"]
    if value is None:
        return blank_reason(cell)
    if value == 0:
        return "記錄為零"
    if cell.get("source") == "trapezoid_counts_seconds_on_canonical_signal":
        return "梯形積分面積"
    return "已保留數值"


def _item_labels(view: ItemEvidence, samples, route):
    title = {
        "established": "已建立分組",
        "provisional": "暫定分組",
        "unresolved": "分組尚待釐清",
    }.get(view.state, "分組紀錄")
    if view.item.kind == "candidate":
        title = "已排除候選" if view.item.stratum == "excluded" else "未分組候選"
    counts = {
        "discovery_support": 0,
        "backfill_support": 0,
        "unavailable": 0,
        "empty": 0,
    }
    for sample in samples:
        key = (
            "empty"
            if sample.get("quantitative_value") is None
            else sample["support_origin"]["kind"]
        )
        counts[key] += 1
    summary = (
        f"{len(samples)} 個樣本：{counts['discovery_support']} detect · "
        f"{counts['backfill_support']} backfill · {counts['empty']} 空值"
    )
    if counts["unavailable"]:
        summary += f" · {counts['unavailable']} 有值但來源未提供"
    labels = {
        "title": title,
        "badge_label": "需留意"
        if any(s["risk_labels"] or s["competition_groups"] for s in samples)
        else "已排除"
        if view.item.stratum == "excluded"
        else "已記錄",
        "reason_label": summary,
        "route_label": {
            "matrix": "去向：定量矩陣",
            "archive": "去向：封存紀錄（未列入定量矩陣）",
        }.get(route.get("destination"), "未記錄輸出去向"),
    }
    if view.losses:
        labels["method_label"] = "NL " + "、".join(
            f"{loss:.4f}" for loss in view.losses
        )
    if view.item.kind != "row":
        labels["reason_label"] = f"{title}；沒有矩陣格子的正式定量決策。"
    return labels


def build_sample(
    view: ItemEvidence,
    sid: str,
    *,
    fallback_sample_label: str,
    decision_refs: list[CardRef],
    projected_curves: list[dict[str, Any]],
) -> dict[str, Any]:
    """Present one already-scoped sample and its exact decision references."""
    cells = [c for c in view.cells if c["sample_key"] == sid]
    values = [c["quantitative_value"] for c in cells]
    return {
        "sample_id": sid,
        "sample_stem": fallback_sample_label,
        "risk_labels": [label for cell in cells for label in cell_risks(cell)],
        "competition_groups": [
            group for cell in cells for group in competition_groups(cell)
        ],
        "status_kind": view.sample_status[sid],
        "support_origin": view.support_origins[sid],
        "status_label": {
            "attention": "沒有正式定量決策"
            if len(values) != 1
            else "有定量值"
            if values[0] is not None
            else "空值",
            "excluded": "已排除",
            "null": "空值",
            "zero": "真實零值",
            "rest": "有定量值" if len(values) == 1 else "有結果紀錄",
        }.get(view.sample_status[sid], "未提供定量決策"),
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
    view: ItemEvidence,
    *,
    samples: list[dict[str, Any]],
    projected_curves: list[dict[str, Any]],
    readable_records: list[dict[str, Any]],
    measured_mz_by_candidate: Mapping[tuple[str, str], float | None],
    relationship_members: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble presentation from selected evidence; never select or reload it."""
    item = view.item
    route = view.route
    reason = view.reason
    return {
        "id": item.serialized_ref,
        "kind": item.kind,
        "stratum": item.stratum,
        "badge": item.stratum,
        "precursor_mz": item.witnesses[0].precursor_mz if item.witnesses else None,
        "rt_apex_min": item.witnesses[0].rt_apex_min if item.witnesses else None,
        "reason": reason,
        "route": route,
        **_item_labels(view, samples, route),
        "samples": samples,
        "witnesses": [
            {
                **asdict(w),
                "matched": w in item.matched_witnesses,
                "ms1_apex_mz": measured_mz_by_candidate.get(
                    (w.candidate_id, w.sample_key)
                ),
            }
            for w in item.witnesses
        ],
        "matched_refs": [asdict(w) for w in item.matched_witnesses],
        "details": {
            "attention_triggers": item.details["attention_triggers"],
            "readable_records": readable_records,
        },
        "review_summary": view.review_summary,
        "relationship_members": relationship_members,
        "curves": projected_curves,
    }


def curve_stage_label(kind: Any) -> str:
    return _STAGE_LABELS.get(kind, "保留曲線")


def support_origin_label(kind: str) -> str:
    return _SUPPORT_ORIGIN_LABELS[kind]
