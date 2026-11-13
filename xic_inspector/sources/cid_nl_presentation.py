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
    "unavailable": "來源未記錄",
}
_REASON_LABELS = {
    "insufficient_method_evidence": "找過，沒有 MS1 峰也沒有 MS2 支持",
    "no_unique_qualifying_relation": "沒有可唯一確認的 MS2 與峰的對應",
    "discovery_candidate_not_search_eligible": "初始候選未符合後續搜尋條件",
    "product_mz_out_of_tolerance": "碎片離子 m/z 超出容許範圍",
}


def blank_reason(cell: dict[str, Any]) -> str | None:
    """Why a recorded cell is empty; None when it has a value.

    This names the recorded decision, not absence of raw signal.
    """
    if cell.get("value", cell.get("quantitative_value")) is not None:
        return None
    variant = cell.get("variant")
    states = set(cell.get("evidence_states", ()))
    reasons = set(cell.get("reason_codes", ()))
    if variant == "not_searched":
        return "未在此位置搜尋"
    if variant == "unresolved" or "unresolved" in states:
        return "多峰留空"
    if variant == "searched_no_observation" or "searched_no_observation" in states:
        return "找過，沒有可用的峰"
    if "insufficient_method_evidence" in reasons:
        # _method_result emits this only after evaluation, with no candidate
        # peaks and no qualifying CID-NL relation. It does not mean zero signal.
        return _REASON_LABELS["insufficient_method_evidence"]
    if variant == "not_quantifiable":
        return "有峰，但無法定量"
    if variant == "rejected_observation":
        return "找到的峰未符合採用條件"
    return "未記錄原因"


def cell_risks(cell: dict[str, Any]) -> list[str]:
    """Recorded interpretation risks; ordinary MS1-only backfill is not one."""
    flags = {
        flag for risk in cell.get("membership_risks", ()) for flag in risk["risk_flags"]
    }
    labels = []
    if flags & {"low_rt_support", "backfill_low_localization_confidence"}:
        labels.append("RT 支持弱")
    for flag, label in (
        ("accepted_contradiction", "配對有矛盾證據"),
        ("weak_identity", "身分證據弱"),
        ("direction_disagreement", "配對方向不一致"),
    ):
        if flag in flags:
            labels.append(label)
    if any(
        q.get("low_signal_to_noise") is True for q in cell.get("signal_quality", ())
    ):
        labels.append("低 SNR")
    return labels


def competition_groups(cell: dict[str, Any]) -> list[list[list[Any]]]:
    """The peers each recorded alternative would give this cell's peak.

    The peak is its full owner key (sample, measured m/z, native apex RT), so
    another peak of the same sample is not mistaken for it. An empty entry
    means the alternative leaves the peak out of this group. Alternatives are
    compared configurations, not published features: never invent a feature
    ID for one that was not selected.
    """
    groups: list[list[list[Any]]] = []
    sid = cell["sample_key"]
    for risk in cell.get("membership_risks", ()):
        if not {"accepted_competition", "backfill_competition"}.intersection(
            risk["risk_flags"]
        ):
            continue
        selected = risk["selected_owners"]
        own = [owner for owner in selected if owner[0] == sid]
        if len(own) != 1:
            continue
        peak = own[0]
        alternative = risk.get("alternative_partition")
        for partition in [
            *([alternative] if alternative is not None else []),
            *risk.get("equivalent_alternatives", []),
        ]:
            group = next((group for group in partition if peak in group), [peak])
            if sorted(group) == sorted(selected):
                continue
            peers = [owner for owner in group if owner != peak]
            if peers not in groups:
                groups.append(peers)
    return groups


def competition_label(peers: list[list[Any]], sample_labels: Mapping[str, str]) -> str:
    """Name what one recorded alternative would do with this cell's peak."""
    if not peers:
        return "可能不併入本組"
    names = "、".join(
        f"{sample_labels.get(owner[0], owner[0])} {owner[2]:.2f} min" for owner in peers
    )
    return f"可能改與 {names} 同組"


def exclusion_record(
    card: Card,
    *,
    scope: tuple[list[str], list[str]],
    sample_labels: Mapping[str, str],
) -> dict[str, Any]:
    """Why an excluded candidate was not used."""
    sample_ids, candidate_ids = scope
    sid = sample_ids[0] if sample_ids else ""
    reason = card.facts.get("reason_code", "")
    return {
        "ref": card.ref,
        "title": f"{sample_labels.get(sid, sid)} · 已排除候選",
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


def _item_labels(view: ItemEvidence, samples, route):
    title = {
        "established": "已建立分組",
        "provisional": "暫定分組",
        "unresolved": "分組尚待釐清",
    }.get(view.state, "分組紀錄")
    if view.item.kind == "candidate":
        title = "已排除候選" if view.item.stratum == "excluded" else "未分組候選"
    labels: dict[str, Any] = {
        "title": title,
        "badge_label": "需留意"
        if any(s["risk_labels"] for s in samples)
        else "已排除"
        if view.item.stratum == "excluded"
        else "已記錄",
        "route_label": {
            "matrix": "去向：定量矩陣",
            "archive": "去向：封存紀錄（未列入定量矩陣）",
        }.get(route.get("destination"), "未記錄輸出去向"),
    }
    if view.losses:
        labels["method_label"] = "NL " + "、".join(
            f"{loss:.4f}" for loss in view.losses
        )
    if view.item.kind == "row":
        labels["group_counts"] = {
            **_group_counts(samples),
            # Left-empty with several possible peaks: context for the overview.
            "unresolved": len(
                {
                    c["sample_key"]
                    for c in view.cells
                    if c.get("variant") == "unresolved"
                }
            ),
        }
    return labels


def _group_counts(samples: list[dict[str, Any]]) -> dict[str, int]:
    """Count samples by the extraction stage of their value, or why there is none."""
    counts = dict.fromkeys(("detect", "backfill", "unrecorded", "empty", "missing"), 0)
    stage = {"discovery_support": "detect", "backfill_support": "backfill"}
    for sample in samples:
        if "quantitative_value" not in sample:
            counts["missing"] += 1
        elif sample["quantitative_value"] is None:
            counts["empty"] += 1
        else:
            counts[stage.get(sample["support_origin"]["kind"], "unrecorded")] += 1
    return counts


def build_sample(
    view: ItemEvidence,
    sid: str,
    *,
    sample_labels: Mapping[str, str],
    decision_refs: list[CardRef],
) -> dict[str, Any]:
    """One sample's saved value and what to read beside it."""
    cells = [c for c in view.cells if c["sample_key"] == sid]
    values = [c["quantitative_value"] for c in cells]
    note = None
    if view.item.kind == "row":
        note = "沒有單一定量結果" if len(cells) != 1 else blank_reason(cells[0])
    return {
        "sample_id": sid,
        "sample_stem": sample_labels.get(sid, sid),
        "support_origin": view.support_origins[sid],
        **({"quantitative_value": values[0]} if len(values) == 1 else {}),
        **({"note": note} if note else {}),
        "risk_labels": [
            *(label for cell in cells for label in cell_risks(cell)),
            *(
                competition_label(peers, sample_labels)
                for cell in cells
                for peers in competition_groups(cell)
            ),
        ],
        "decision_refs": decision_refs,
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
