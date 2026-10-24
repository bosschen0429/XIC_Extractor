"""Browse saved review facts, without scoring peaks or changing decisions."""

from __future__ import annotations

import math

REVIEW_FILTERS = {
    "all": "全部結果",
    "attention": "有已記錄待查訊號",
    "unflagged": "未記錄待查訊號",
    "unavailable": "未提供定量決策",
    "unresolved": "定量尚未解決",
    "method_hold": "方法判讀待確認",
    "conflict": "配對或身分衝突",
    "group_unresolved": "分組待釐清",
    "review_only": "配對僅供檢閱",
    "target_review": "Targeted 檢閱註記",
    "minority_counted": "少數樣本計入",
    "minority_not_counted": "少數樣本未計入",
}
VALUE_FILTERS = {"all", "complete", "missing", "zero"}
REVIEW_ORDERS = {"source", "priority", "mz", "rt", "coverage"}


def finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def reason_category(reason, *, group=False):
    if group and reason in {"provisional", "unresolved"}:
        return "group_unresolved"
    return {
        "unresolved": "unresolved",
        "HOLD": "method_hold",
        "ambiguous_owner": "conflict",
        "identity_conflict": "conflict",
        "review_only": "review_only",
    }.get(reason)


def item_review_facts(item, sample_count):
    """Translate an already-scoped source item, preserving sample attribution."""
    details = item.details
    if "review" in details:
        return details["review"]
    reasons = set()
    affected: set[str] = set()
    if item.kind == "target":
        records = details["records"]
        values = {r["SampleName"]: r.get("Area") for r in records}
        queue = details.get("review_queue", [])
        if queue:
            reasons.add("target_review")
            affected.update(r["Sample"] for r in queue)
        focus = details.get("focus", {})
        if focus.get("samples"):
            affected.update(focus["samples"])
            if focus.get("kind") in REVIEW_FILTERS:
                reasons.add(focus["kind"])
        decision_count = len(records)
    else:
        cells = details.get("cells", [])
        values = {
            c.get("canonical_sample_identity_id", c.get("sample_id")): c.get(
                "quantitative_value", c.get("value")
            )
            for c in cells
        }
        decision_count = len(values)
        for trigger in details.get("attention_triggers", []):
            category = reason_category(
                trigger.get("reason"), group=trigger.get("scope") == "group"
            )
            if category:
                reasons.add(category)
            affected.update(trigger.get("sample_ids", item.sample_ids))
    return {
        "sample_count": sample_count,
        "numeric_count": sum(finite_number(value) for value in values.values()),
        "zero_count": sum(
            finite_number(value) and value == 0 for value in values.values()
        ),
        "decision_count": decision_count,
        "attention_count": len(affected),
        "reasons": sorted(reasons),
    }


def matches_review(facts, kind):
    if kind == "all":
        return True
    if kind == "attention":
        return facts["attention_count"] > 0
    if kind == "unflagged":
        return facts["decision_count"] > 0 and not facts["attention_count"]
    if kind == "unavailable":
        return facts["decision_count"] == 0
    return kind in facts["reasons"]


def matches_values(facts, kind):
    return {
        "all": True,
        "complete": facts["sample_count"] > 0
        and facts["numeric_count"] == facts["sample_count"],
        "missing": facts["numeric_count"] < facts["sample_count"],
        "zero": facts["zero_count"] > 0,
    }[kind]


def select_review_entries(entries, request):
    """Filter and order the entire eligible population, then take one page."""
    entries = [
        entry
        for entry in entries
        if matches_values(entry["review"], request.value_filter)
    ]
    missing_mz = 0
    if request.nearest_mz is not None:
        valid = []
        for entry in entries:
            if finite_number(entry["mz"]) and entry["mz"] > 0:
                entry["mz_distance"] = abs(entry["mz"] - request.nearest_mz)
                valid.append(entry)
            elif matches_review(entry["review"], request.review_filter):
                missing_mz += 1
        entries = valid
    counts = {
        kind: sum(matches_review(entry["review"], kind) for entry in entries)
        for kind in REVIEW_FILTERS
    }
    entries = [
        entry
        for entry in entries
        if matches_review(entry["review"], request.review_filter)
    ]

    def key(entry):
        if request.nearest_mz is not None:
            return entry["mz_distance"], entry["sort_key"]
        if request.order_by in {"mz", "rt"}:
            value = entry[request.order_by]
            return value if finite_number(value) else math.inf, entry["sort_key"]
        if request.order_by == "coverage":
            return -entry["review"]["numeric_count"], entry["sort_key"]
        if request.order_by == "priority":
            return (
                -entry["review"]["attention_count"],
                -entry["review"]["numeric_count"],
                entry["sort_key"],
            )
        return entry["sort_key"]

    entries.sort(key=key)
    return (
        entries[request.offset : request.offset + request.limit],
        len(entries),
        counts,
        missing_mz,
    )


def browse_canonical_items(source, items, request, sample_count):
    entries = []
    for item in items:
        mz = (
            item.details["target"]["m/z"]
            if item.kind == "target"
            else item.witnesses[0].precursor_mz
            if item.witnesses
            else None
        )
        rt = (
            next(
                (
                    r["RT"]
                    for r in item.details["records"]
                    if finite_number(r.get("RT"))
                ),
                None,
            )
            if item.kind == "target"
            else item.witnesses[0].rt_apex_min
            if item.witnesses
            else None
        )
        entries.append(
            {
                "item": item,
                "sort_key": item.sort_key,
                "mz": mz,
                "rt": rt,
                "review": item_review_facts(item, sample_count),
            }
        )
    page, matched, source.review_counts, source.missing_mz_count = (
        select_review_entries(entries, request)
    )
    source.review_facts = {
        entry["item"].serialized_ref: entry["review"] for entry in page
    }
    source.mz_distances = {
        entry["item"].serialized_ref: entry["mz_distance"]
        for entry in page
        if "mz_distance" in entry
    }
    return [entry["item"] for entry in page], matched
